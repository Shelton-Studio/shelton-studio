"""🎨 Modelos: elegir el modelo de cada ecosistema (Higgsfield, fal) y que fal acepte cualquier modelo."""
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
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="shelton_cat_"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import catalogo  # noqa: E402
from app.db import Connection, init_db, session  # noqa: E402
from app.engines import cloud  # noqa: E402


def _png() -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(b, "PNG")
    return b.getvalue()


PNG_URI = "data:image/png;base64," + base64.b64encode(_png()).decode()


def _mock_httpx(monkeypatch, handler):
    real = httpx.AsyncClient

    def factory(*a, **k):
        k["transport"] = httpx.MockTransport(handler)
        return real(*a, **k)
    monkeypatch.setattr(httpx, "AsyncClient", factory)


def test_pick_models_per_ecosystem(monkeypatch):
    init_db()
    with session() as s:
        hf = Connection(type="higgsfield", name="Higgsfield", config={"api_key": "a:b"}, roles=["generate"])
        fal = Connection(type="fal", name="fal.ai", config={"api_key": "k"}, roles=["generate"])
        s.add_all([hf, fal])
        s.flush()
        hid, fid = hf.id, fal.id

    def handler(req):
        assert req.url.host == "fal.ai" and req.url.path == "/api/models"
        cat = req.url.params["categories"]
        items = {"text-to-image": [
            {"id": "fal-ai/flux-3/max", "title": "FLUX 3 Max", "shortDescription": "nuevo",
             "pricingInfoOverride": "Your request will cost **$0.08** per image.", "discovery": {"tags": []}},
            {"id": "fal-ai/nano-banana-pro", "title": "dup", "pricingInfoOverride": "cost **$0.15** per image"},
            {"id": "fal-ai/viejo", "title": "viejo", "deprecated": True}],
            "image-to-image": [
            {"id": "fal-ai/upscaler", "title": "Upscaler", "discovery": {"tags": ["task:upscale-image"]}},
            {"id": "fal-ai/x/edit", "title": "X Edit", "discovery": {"tags": ["task:edit-image"]}}]}.get(cat, [])
        return httpx.Response(200, json={"items": items, "pages": 1, "total": len(items)})
    _mock_httpx(monkeypatch, handler)
    catalogo._live.clear()
    d = asyncio.run(catalogo.catalog_for(fid))
    gen = d["roles"]["generate"]
    assert gen["current"] == "fal-ai/nano-banana-pro" and not gen["custom"]
    ids = [m["id"] for m in gen["models"]]
    assert "fal-ai/flux-3/max" in ids and ids.count("fal-ai/nano-banana-pro") == 1 and "fal-ai/viejo" not in ids
    assert next(m for m in gen["models"] if m["id"] == "fal-ai/flux-3/max")["price"] == "$0.08 por imagen"
    assert gen["models"][0]["price"] == "$0.15 por imagen"                 # el recomendado también muestra precio
    edit_ids = [m["id"] for m in d["roles"]["edit"]["models"]]
    assert "fal-ai/x/edit" in edit_ids and "fal-ai/upscaler" not in edit_ids  # solo editores, no mejoradores
    asyncio.run(catalogo.api_pick(fid, catalogo.PickIn(role="generate", model="fal-ai/flux-3/max")))
    with session() as s:
        assert s.get(Connection, fid).config["model_generate"] == "fal-ai/flux-3/max"
        assert s.get(Connection, fid).config["api_key"] == "k"                 # la clave no se toca
    asyncio.run(catalogo.api_pick(fid, catalogo.PickIn(role="generate", model="")))
    with session() as s:
        assert "model_generate" not in s.get(Connection, fid).config            # vuelve al recomendado
    h = asyncio.run(catalogo.catalog_for(hid))
    assert h["roles"]["generate"]["current"] == "higgsfield-ai/soul/v2/standard"
    assert len(h["roles"]["video"]["models"]) >= 30                     # catálogo oficial completo
    asyncio.run(catalogo.api_pick(hid, catalogo.PickIn(role="video", model="kling-video/o3/image-reference")))
    with session() as s:
        assert s.get(Connection, hid).config["image_field"] == "image_urls"   # ese modelo pide lista de referencias
    asyncio.run(catalogo.api_pick(hid, catalogo.PickIn(role="video", model="kling-video/v3.0/pro/image-to-video")))
    with session() as s:
        assert "image_field" not in s.get(Connection, hid).config
    conns = asyncio.run(catalogo.api_cloud_connections())
    assert {c["type"] for c in conns} >= {"higgsfield", "fal"}
    with session() as s:
        s.delete(s.get(Connection, hid))
        s.delete(s.get(Connection, fid))


def test_fal_retries_with_minimal_payload_for_other_models(monkeypatch):
    calls = []

    def handler(req):
        if req.url.host == "fal.run":
            body = json.loads(req.content)
            calls.append(body)
            if "image_size" in body:                                    # este modelo pide aspect_ratio
                return httpx.Response(422, json={"detail": "image_size not allowed"})
            return httpx.Response(200, json={"image": {"url": PNG_URI}})
        raise AssertionError(req.url)
    _mock_httpx(monkeypatch, handler)
    e = cloud.FalEngine({"api_key": "k", "model_generate": "fal-ai/imagen4/preview"})
    out = asyncio.run(e.generate("a woman on the beach", 896, 1152, 1))
    assert out == _png() and calls[-1]["aspect_ratio"] == "3:4"


def test_fal_edit_falls_back_to_single_image_models(monkeypatch):
    def handler(req):
        body = json.loads(req.content)
        if "image_urls" in body:
            return httpx.Response(422, json={"detail": "use image_url"})
        assert body["image_url"].startswith("data:image")
        return httpx.Response(200, json={"images": [{"url": PNG_URI}]})
    _mock_httpx(monkeypatch, handler)
    e = cloud.FalEngine({"api_key": "k", "model_edit": "fal-ai/flux-pro/kontext"})
    assert asyncio.run(e._edit_many([_png()], "same face, red dress", 0, 0, 1)) == _png()



def test_active_model_shown_above_chat():
    """Arriba del chat se ve qué modelo usa cada tarea con el Motor elegido (ej. «Seedream 5.0 Pro · fal.ai»)."""
    init_db()
    with session() as s:
        fal = Connection(type="fal", name="fal.ai", roles=["generate"],
                         config={"api_key": "k", "model_generate": "bytedance/seedream/v5/pro/text-to-image"})
        s.add(fal)
        s.flush()
        fid = fal.id
    a = catalogo.active(f"conn:{fid}")
    assert a["generate"] == "Seedream 5.0 Pro · fal.ai"
    assert a["video"].endswith("· fal.ai") and "Kling 4.0" in a["video"]          # el recomendado si no eligieron
    assert "placa" in catalogo.active("fast")["generate"]
    assert catalogo.active("")["label"] == "Automático"
    with session() as s:
        s.delete(s.get(Connection, fid))
