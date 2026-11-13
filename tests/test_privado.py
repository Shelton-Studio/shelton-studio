"""Estudio privado: solo modelos confirmadas (inventadas y +18), bloqueo de menores, todo local y fuera de /media."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ["IMAGE_ENGINE"] = "mock"
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="shelton_priv_"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_chain  # noqa: E402,F401
from test_lora import _png  # noqa: E402

from app import privado  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import Post, Subject, init_db, session  # noqa: E402


def _subject(name="Luna", desc="mujer inventada de 27 años") -> str:
    with session() as s:
        sub = Subject(name=name, kind="person", description=desc)
        s.add(sub)
        s.flush()
        return sub.id


def test_minor_mentions_are_always_blocked():
    for bad in ("una colegiala en la playa", "teen girl", "chica de 16 años", "schoolgirl outfit", "loli", "niña"):
        assert privado.minor_problem(bad), bad
    for ok in ("mujer de 25 años en la playa", "adult woman, beach, golden hour", "lencería negra, luz suave"):
        assert not privado.minor_problem(ok), ok


def test_only_confirmed_adult_invented_models(monkeypatch):
    init_db()
    sid = _subject()
    try:
        asyncio.run(privado.generate("admin", "foto en la playa", sid))
    except ValueError as e:
        assert "confirmar" in str(e)
    else:
        raise AssertionError("sin confirmar no debía dejar")
    try:
        privado.confirm(sid, "admin", inventada=True, mayor=False)
    except ValueError:
        pass
    else:
        raise AssertionError("hay que confirmar las dos cosas")
    young = _subject("Mía", "chica de 17 años")
    try:
        privado.confirm(young, "admin", True, True)
    except ValueError as e:
        assert "edad menor de 18" in str(e)
    else:
        raise AssertionError("no debía confirmar una ficha con menor de edad")
    try:
        asyncio.run(privado.generate("admin", "una adolescente posando"))
    except ValueError as e:
        assert "Bloqueado" in str(e)
    else:
        raise AssertionError


def test_generates_locally_private_and_never_in_posts(monkeypatch):
    init_db()
    sid = _subject("Nova")
    privado.confirm(sid, "admin", True, True)
    calls = []

    class Eng:
        name = "comfyui"
        values = {"generate": {}}

        async def generate(self, prompt, w, h, seed, on_progress=None):
            calls.append((prompt, self.values["generate"]["negative"]))
            return _png("red")
    monkeypatch.setattr(privado, "_engine", lambda preset, ckpt: ({"preset": preset}, Eng()))
    with session() as s:
        posts_before = s.query(Post).count()

    async def go():
        r = await privado.generate("admin", "retrato en un loft, luz de ventana", sid, "sdxl", count=2)
        for _ in range(100):
            await asyncio.sleep(0.02)
            if all(e["status"] == "done" for e in privado.entries() if e["batch"] == r["batch"]):
                break
        return r
    r = asyncio.run(go())
    assert r["count"] == 2 and "todavía no tiene LoRA" in r["note"]
    assert len(calls) == 2 and calls[0][0].startswith("adult, ") and "underage" in calls[0][1]
    done = [e for e in privado.entries() if e["batch"] == r["batch"]]
    assert len(done) == 2 and all(e["status"] == "done" for e in done)
    for e in done:
        assert (settings.DATA_DIR / "privado" / e["file"]).exists()
        assert not (settings.media_dir / e["file"]).exists()          # nunca en /media (que es público)
    with session() as s:
        assert s.query(Post).count() == posts_before                   # no pasa por la galería ni Aprobaciones
    privado.delete(done[0]["id"])
    assert len([e for e in privado.entries() if e["batch"] == r["batch"]]) == 1


def test_only_admins(monkeypatch):
    from fastapi import HTTPException

    class Req:
        class state:
            user = {"id": "u", "username": "operador", "role": "editor"}
        cookies = {}
    try:
        privado._admin(Req())
    except HTTPException as e:
        assert e.status_code == 403
    else:
        raise AssertionError("un editor no debía entrar")


def test_chat_understands_requests_like_shelton(monkeypatch):
    init_db()
    sid = _subject("Selene")
    privado.confirm(sid, "admin", True, True)
    p = privado.parse_request("Hazme 3 fotos de Selene en un loft con luz de ventana, formato historia, con flux")
    assert p["subject"]["name"] == "Selene" and p["count"] == 3 and p["size"] == "historia" and p["preset"] == "flux"
    assert "Selene" not in p["prompt"] and "loft" in p["prompt"]
    p = privado.parse_request("dos fotos de una mujer en la playa")
    assert p["subject"] is None and p["count"] == 2 and p["preset"] == "sdxl"
    try:
        asyncio.run(privado.chat("admin", "fotos de una colegiala"))
    except ValueError as e:
        assert "Bloqueado" in str(e)
    else:
        raise AssertionError


def test_best_defaults_and_waiting_reason(monkeypatch):
    assert privado.best_checkpoint(["RealVisXL_V5.0_fp16.safetensors", "cyberrealisticXL_v5.safetensors"]).startswith("cyber")
    assert privado.best_checkpoint(["ponyDiffusionV6XL.safetensors"]).startswith("pony")
    assert privado.enrich("adult, x", "sdxl", "ponyDiffusionV6XL.safetensors").startswith("score_9")
    assert "photorealistic" in privado.enrich("adult, x", "sdxl", "RealVisXL.safetensors")
    from app.engines import _busy
    init_db()
    with session() as s:
        s.add(Subject(name="Zara", kind="person", lora_status="training"))
    _busy["http://gpu"] = 1
    try:
        assert "entrenando la LoRA de Zara" in privado._gpu_reason("http://gpu")
    finally:
        _busy["http://gpu"] = 0
    assert privado._gpu_reason("http://gpu") == ""


def test_stuck_entries_are_cleared_after_restart():
    privado._append({"id": "stuck-0", "batch": "stuck", "at": "2099", "status": "waiting"})
    privado.recover_after_restart()
    e = next(x for x in privado.entries() if x["id"] == "stuck-0")
    assert e["status"] == "failed" and "reinició" in e["error"]
