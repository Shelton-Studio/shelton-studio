"""Higgsfield como motor de imagen y video: enviar, esperar el resultado y traerlo (API simulada)."""
import asyncio
import base64
import io
import json
import os
import sys
import tempfile
from pathlib import Path

import httpx
from PIL import Image

os.environ.setdefault("IMAGE_ENGINE", "mock")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="shelton_hf_"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engines.cloud import CLOUD_ENGINES, HiggsfieldEngine  # noqa: E402


def _png() -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(b, "PNG")
    return b.getvalue()


PNG_URI = "data:image/png;base64," + base64.b64encode(_png()).decode()
VID_URI = "data:video/mp4;base64," + base64.b64encode(b"MP4DATA").decode()


def _engine(handler, **cfg):
    e = HiggsfieldEngine({"api_key": "KID", "api_secret": "SECRET", **cfg})
    e.transport = httpx.MockTransport(handler)
    e.POLL = 0.01
    return e


def test_generate_submits_and_polls_until_completed():
    seen = []

    def handler(req: httpx.Request):
        seen.append((req.method, req.url.path, req.headers.get("authorization"),
                     json.loads(req.content) if req.content else None))
        if req.method == "POST" and req.url.path == "/higgsfield-ai/soul/v2/standard":
            return httpx.Response(200, json={"status": "queued", "request_id": "r1",
                                             "status_url": "https://api.higgsfield.ai/requests/r1/status"})
        if req.url.path == "/requests/r1/status":
            n = sum(1 for s in seen if s[1] == "/requests/r1/status")
            if n < 2:
                return httpx.Response(200, json={"status": "in_progress", "request_id": "r1"})
            return httpx.Response(200, json={"status": "completed", "request_id": "r1", "images": [{"url": PNG_URI}]})
        return httpx.Response(404)
    e = _engine(handler, extra='{"resolution": "2K"}')
    out = asyncio.run(e.generate("Valentina en un café", 1080, 1350, 7))
    assert out == _png()
    post = seen[0]
    assert post[2] == "Key KID:SECRET"                                  # cabecera oficial: Key ID:SECRET
    assert post[3] == {"prompt": "Valentina en un café", "aspect_ratio": "4:5", "resolution": "2K"}   # lo de la conexión manda
    assert e.last_model == "higgsfield-ai/soul/v2/standard"


def test_edit_uploads_images_and_video_works():
    uploads = []

    def handler(req: httpx.Request):
        if req.url.path == "/files/generate-upload-url":
            return httpx.Response(200, json={"upload_url": "https://up.example/put/1", "public_url": "https://cdn.example/1.png"})
        if req.method == "PUT":
            uploads.append(req.content)
            return httpx.Response(200)
        if req.url.path == "/alibaba/qwen-image-3/edit":
            body = json.loads(req.content)
            assert body["image_urls"] == ["https://cdn.example/1.png"]
            return httpx.Response(200, json={"status": "completed", "images": [{"url": PNG_URI}]})
        if req.url.path == "/wan/v2.7/image-to-video":
            body = json.loads(req.content)
            assert body["image_url"] == "https://cdn.example/1.png" and "Avoid: blur" in body["prompt"]
            return httpx.Response(200, json={"request_id": "v1"})
        if req.url.path == "/requests/v1/status":
            return httpx.Response(200, json={"status": "completed", "video": {"url": VID_URI}})
        return httpx.Response(404)
    e = _engine(handler)
    assert asyncio.run(e.edit(_png(), "ponle fondo azul", 1)) == _png()
    data, fps = asyncio.run(e.video(_png(), "camina", "blur", 720, 1280, 1))
    assert data == b"MP4DATA" and fps == 30 and len(uploads) == 2


def test_clear_errors_in_spanish():
    def bad_key(req):
        return httpx.Response(401, json={"detail": "unauthorized"})
    try:
        asyncio.run(_engine(bad_key).generate("x", 1, 1, 1))
    except RuntimeError as e:
        assert "clave completa" in str(e)
    else:
        raise AssertionError

    def nsfw(req):
        if req.method == "POST":
            return httpx.Response(200, json={"request_id": "n1"})
        return httpx.Response(200, json={"status": "nsfw"})
    try:
        asyncio.run(_engine(nsfw).generate("x", 1, 1, 1))
    except RuntimeError as e:
        assert "nsfw" in str(e)
    else:
        raise AssertionError
    assert CLOUD_ENGINES["higgsfield"] is HiggsfieldEngine


def test_full_key_pasted_in_one_box():
    seen = []

    def handler(req):
        seen.append(req.headers.get("authorization"))
        return httpx.Response(200, json={"status": "completed", "images": [{"url": PNG_URI}]})
    e = HiggsfieldEngine({"api_key": "KID:SECRET"})
    e.transport = httpx.MockTransport(handler)
    asyncio.run(e.generate("x", 1, 1, 1))
    assert seen[0] == "Key KID:SECRET"


def test_best_quality_defaults_and_safe_retry():
    bodies = []

    def handler(req):
        b = json.loads(req.content)
        bodies.append(b)
        if "resolution" in b:
            return httpx.Response(422, json={"detail": "unknown field resolution"})
        return httpx.Response(200, json={"status": "completed", "images": [{"url": PNG_URI}]})
    e = HiggsfieldEngine({"api_key": "KID:SECRET"})
    e.transport = httpx.MockTransport(handler)
    assert asyncio.run(e.generate("x", 1080, 1350, 1)) == _png()
    assert bodies[0]["resolution"] == "1080p" and "resolution" not in bodies[1]     # pide lo mejor; si no se puede, sigue igual
