"""WaveSpeed como motor (Seedream 5.5 para fotos y edición, Wan para video): API simulada."""
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
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="shelton_ws_"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engines.cloud import CLOUD_ENGINES, WaveSpeedEngine  # noqa: E402


def _png() -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(b, "PNG")
    return b.getvalue()


PNG_URI = "data:image/png;base64," + base64.b64encode(_png()).decode()


def _engine(handler, **cfg):
    e = WaveSpeedEngine({"api_key": "WS-KEY", **cfg})
    e.transport = httpx.MockTransport(handler)
    e.POLL = 0.01
    return e


def test_seedream_generate_and_poll():
    calls = []

    def handler(req: httpx.Request):
        calls.append(req)
        if req.method == "POST" and req.url.path == "/api/v3/bytedance/seedream-v5.5":
            assert req.headers["authorization"] == "Bearer WS-KEY"
            body = json.loads(req.content)
            assert body["prompt"] == "Valentina en la playa" and body["size"] == "1632*2048"
            return httpx.Response(200, json={"code": 200, "data": {"id": "p1", "status": "created",
                                                                  "urls": {"get": "https://api.wavespeed.ai/api/v3/predictions/p1/result"}}})
        if req.url.path == "/api/v3/predictions/p1/result":
            n = sum(1 for c in calls if c.url.path.endswith("/p1/result"))
            if n < 2:
                return httpx.Response(200, json={"code": 200, "data": {"id": "p1", "status": "processing", "outputs": []}})
            return httpx.Response(200, json={"code": 200, "data": {"id": "p1", "status": "completed", "outputs": [PNG_URI]}})
        return httpx.Response(404)
    e = _engine(handler)
    assert asyncio.run(e.generate("Valentina en la playa", 1080, 1350, 1)) == _png()
    assert e.last_model == "bytedance/seedream-v5.5"


def test_edit_with_references_and_video():
    def handler(req: httpx.Request):
        if req.url.path == "/api/v3/media/upload/binary":
            return httpx.Response(200, json={"code": 200, "data": {"download_url": "https://cdn.ws/ref.png"}})
        if req.url.path == "/api/v3/bytedance/seedream-v5.5/edit":
            assert json.loads(req.content)["images"] == ["https://cdn.ws/ref.png"] * 3
            return httpx.Response(200, json={"data": {"id": "e1", "status": "completed", "outputs": [PNG_URI]}})
        if req.url.path == "/api/v3/wavespeed-ai/wan-2.2/i2v-720p":
            body = json.loads(req.content)
            assert body["image"] == "https://cdn.ws/ref.png" and body["negative_prompt"] == "blur"
            return httpx.Response(200, json={"data": {"id": "v1", "status": "completed",
                                                      "outputs": ["data:video/mp4;base64," + base64.b64encode(b"MP4").decode()]}})
        return httpx.Response(404)
    e = _engine(handler)
    assert asyncio.run(e.generate_with_refs([_png(), _png(), _png()], "misma cara", 1080, 1350, 1)) == _png()
    data, fps = asyncio.run(e.video(_png(), "camina", "blur", 720, 1280, 1))
    assert data == b"MP4" and fps == 30


def test_errors_are_clear():
    def no_money(req):
        return httpx.Response(402, json={"message": "insufficient balance"})
    try:
        asyncio.run(_engine(no_money).generate("x", 1, 1, 1))
    except RuntimeError as e:
        assert "sin saldo" in str(e)
    else:
        raise AssertionError

    def failed(req):
        return httpx.Response(200, json={"data": {"id": "f", "status": "failed", "error": "bad prompt"}})
    try:
        asyncio.run(_engine(failed).generate("x", 1, 1, 1))
    except RuntimeError as e:
        assert "failed" in str(e) and "bad prompt" in str(e)
    else:
        raise AssertionError
    assert CLOUD_ENGINES["wavespeed"] is WaveSpeedEngine
