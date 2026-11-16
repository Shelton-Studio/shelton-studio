"""🪪 Cara por foto: fotos de la cara de una modelo del Banco → fotos nuevas con esa cara (InstantID sobre SDXL)."""
import io
import os
import sys
import tempfile
from pathlib import Path

os.environ["IMAGE_ENGINE"] = "mock"
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="shelton_id_"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_chain  # noqa: E402,F401

from app import identidad  # noqa: E402
from app.comfyui.client import load_workflow  # noqa: E402
from app.db import Subject, init_db, session  # noqa: E402
from app.engines import comfy  # noqa: E402


def _png() -> bytes:
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (64, 64), "pink").save(b, "PNG")
    return b.getvalue()


def _subject(name: str, info: dict | None = None) -> str:
    init_db()
    with session() as s:
        sub = Subject(name=name, kind="person", description="mujer inventada de 28 años", lora_info=info or {})
        s.add(sub)
        s.flush()
        return sub.id


def test_workflow_puts_the_face_into_sdxl_and_retouches_it():
    wf = load_workflow("sdxl.json")
    assert comfy.identity_workflow(wf, ["a.png", "b.png"], weight=0.9, pose=0.3)
    ap = wf["shl_id_apply"]["inputs"]
    assert ap["ip_weight"] == 0.9 and ap["cn_strength"] == 0.3
    assert ap["image"] == ["shl_id_batch2", 0]                       # dos fotos: se promedian
    ks = next(v for v in wf.values() if v["class_type"] == "KSampler")["inputs"]
    assert ks["model"] == ["shl_id_apply", 0] and ks["positive"] == ["shl_id_apply", 1]
    assert comfy.identity_face_fix(wf)
    assert wf["shl_face"]["inputs"]["model"] == ["shl_id_apply", 0]
    save = next(v for v in wf.values() if v["class_type"] == "SaveImage")
    assert save["inputs"]["images"] == ["shl_face", 0]
    # con Z-Image (sin checkpoint SDXL) no se puede
    assert not comfy.identity_workflow(load_workflow("z_image_turbo.json"), ["a.png"])


def test_upload_check_generate_and_never_for_shelton_hot(monkeypatch):
    import asyncio
    sid = _subject("Cara Foto")
    checked = []

    async def ok_check(path):
        checked.append(path)
    monkeypatch.setattr("app.pipeline._swap_check", ok_check)
    d = asyncio.run(identidad.add_ref(sid, _png()))
    assert checked and len(d["refs"]) == 1 and d["ready"]

    async def bad_check(path):
        raise ValueError("Esta foto no se puede usar para cambiar la cara. Prueba con otra.")
    monkeypatch.setattr("app.pipeline._swap_check", bad_check)
    try:
        asyncio.run(identidad.add_ref(sid, _png()))
    except ValueError:
        pass
    else:
        raise AssertionError("una foto que no pasa la revisión no se guarda")
    assert len(identidad.info(sid)["refs"]) == 1

    seen = {}

    class Eng:
        async def generate_identity(self, refs, prompt, w, h, seed, weight, pose):
            seen.update(refs=len(refs), prompt=prompt, w=w, h=h, weight=weight, pose=pose)
            return _png()
    monkeypatch.setattr(identidad, "_engine", lambda: Eng())

    async def to_en(t):
        return t, ""
    monkeypatch.setattr("app.traductor.to_english", to_en)
    identidad.set_options(sid, weight=1.0, pose=0.2)
    r = asyncio.run(identidad.generate(sid, "Cara Foto en un café", count=2))
    assert len(r["images"]) == 2 and seen["refs"] == 1 and seen["weight"] == 1.0 and seen["pose"] == 0.2
    assert "Cara Foto" not in seen["prompt"]                          # el nombre no va al texto: la cara la pone la foto
    assert len(identidad.info(sid)["tests"]) == 2
    d = identidad.remove_ref(sid, r["info"]["refs"][0]["file"])
    assert d["refs"] == []
    # las modelos de Shelton Hot no usan esto
    hot = _subject("Cara Foto · 🔞", {"adult_twin": True})
    try:
        identidad.info(hot)
    except ValueError:
        pass
    else:
        raise AssertionError("Shelton Hot no tiene cara por foto")


def test_api_routes():
    from fastapi.testclient import TestClient
    from app.main import app
    sid = _subject("Cara Api")
    with TestClient(app) as c:
        r = c.get(f"/api/subjects/{sid}/identidad")
        assert r.status_code == 200 and r.json()["refs"] == []
        assert c.post(f"/api/subjects/{sid}/identidad/generar", json={"prompt": "x"}).status_code == 400
        assert c.get("/api/subjects/nope/identidad").status_code == 404
        assert c.get(f"/api/subjects/{sid}/identidad/archivo/../../secret").status_code == 404
