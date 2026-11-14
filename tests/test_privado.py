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



def test_private_photos_in_the_bank_train_locally_and_never_go_to_the_cloud(monkeypatch):
    """Las fotos adultas de una modelo se guardan en su ficha (privadas), se pueden usar para entrenar su LoRA y
    nunca se mandan a la IA de la nube (ni para describirlas)."""
    import io
    from app import lora
    from PIL import Image
    init_db()
    sid = _subject("Iris")
    try:
        privado.add_ref_upload(sid, _png("red"))
    except ValueError as e:
        assert "confirmar" in str(e)
    else:
        raise AssertionError("sin confirmar no se suben fotos privadas")
    privado.confirm(sid, "admin", True, True)
    b = io.BytesIO()
    Image.new("RGB", (64, 64), "blue").save(b, "JPEG")
    r = privado.add_ref_upload(sid, b.getvalue())
    assert r["count"] == 1
    f = settings.DATA_DIR / "privado" / r["file"]
    assert f.exists() and not (settings.media_dir / r["file"]).exists()
    # desde lo generado en la Adult Zone
    privado._append({"id": "g-0", "batch": "g", "at": "2099", "status": "done", "file": "g-0.png", "subject_id": sid})
    (settings.DATA_DIR / "privado" / "g-0.png").write_bytes(_png("green"))
    privado.add_ref_from_entry(sid, "g-0")
    assert len(privado.model_private(sid)["refs"]) == 2
    # al entrenar: solo si eligieron usarlas
    assert not any(privado.is_private_path(p) for p in lora.dataset_images(sid))
    lora._update(sid, info={"train_private": True})
    priv = [p for p in lora.dataset_images(sid) if privado.is_private_path(p)]
    assert len(priv) == 2
    # describir fotos para la LoRA usa la nube: lo privado se salta siempre
    sent = []

    async def fake_json_call(system, content, schema, **k):
        sent.extend(c for c in content if c.get("type") == "image")
        return {"captions": []}
    monkeypatch.setattr("app.llm.json_call", fake_json_call)
    monkeypatch.setattr(lora, "session_images", lambda sid_: priv)
    caps = asyncio.run(lora.caption_photos(sid, "trig"))
    assert sent == [] and all(v == "" for v in caps.values())
    privado.remove_ref(sid, r["file"])
    assert len(privado.model_private(sid)["refs"]) == 1


def test_pending_images_resume_by_themselves_after_restart_and_retry_on_cuts(monkeypatch):
    """Si Shelton se reinicia o se corta la conexión, las fotos de la Adult Zone no se pierden: se retoman solas."""
    init_db()
    sid = _subject("Aura")
    privado.confirm(sid, "admin", True, True)
    monkeypatch.setattr(privado, "RETRY_WAIT", 0.01)
    fails = {"n": 1}

    class Eng:
        name = "comfyui"
        values = {"generate": {}}

        async def generate(self, prompt, w, h, seed, on_progress=None):
            if fails["n"]:
                fails["n"] -= 1
                raise ConnectionError("se cayó internet")
            return _png("red")
    monkeypatch.setattr(privado, "_engine", lambda preset, ckpt: ({"preset": preset}, Eng()))

    async def go():
        r = await privado.generate("admin", "retrato en un loft", sid, "sdxl", count=1)
        for _ in range(200):
            await asyncio.sleep(0.02)
            if all(e["status"] == "done" for e in privado.entries() if e["batch"] == r["batch"]):
                break
        return r
    r = asyncio.run(go())
    assert [e["status"] for e in privado.entries() if e["batch"] == r["batch"]] == ["done"]   # reintentó solo

    # una foto que quedó a medias cuando se reinició Shelton
    privado._append({"id": "corte-0", "batch": "corte", "at": "2099", "status": "waiting", "subject_id": sid,
                     "preset": "sdxl", "ckpt": "", "job": {"full": "adult, x", "neg": "", "w": 64, "h": 64, "loras": []}})

    async def restart():
        assert privado.recover_after_restart() == 1
        for _ in range(200):
            await asyncio.sleep(0.02)
            if next(x for x in privado.entries() if x["id"] == "corte-0")["status"] == "done":
                break
    asyncio.run(restart())
    e = next(x for x in privado.entries() if x["id"] == "corte-0")
    assert e["status"] == "done" and (settings.DATA_DIR / "privado" / e["file"]).exists()


def test_uses_her_bank_lora_engine_by_default():
    """«Hazme una foto de Valentina…» usa el motor de SU LoRA (Z-Image, aprendida de sus fotos del Banco),
    también mientras sigue aprendiendo (con la última versión lista)."""
    init_db()
    with session() as s:
        sub = Subject(name="Valeria", kind="person", description="mujer inventada de 28 años",
                      lora_status="training", lora_name="valeria.safetensors", lora_trigger="vlr")
        s.add(sub)
        s.flush()
        sid = sub.id
    privado.confirm(sid, "admin", True, True)
    p = privado.parse_request("hazme una foto de Valeria en un loft")
    assert p["preset"] == "zimage" and p["subject"]["lora_ready"]
    assert privado.parse_request("foto de Valeria con sdxl")["preset"] == "sdxl"
    cfg, _ = privado._engine("zimage", "")
    assert cfg["preset"] == "fast"


def test_valentina_con_sdxl_uses_her_sdxl_version():
    """«Valentina con sdxl» usa «Valentina · SDXL» (su LoRA SDXL), que hereda la confirmación de la original."""
    from app import lora
    init_db()
    with session() as s:
        sub = Subject(name="Ámbar", kind="person", description="mujer inventada de 30 años",
                      lora_status="ready", lora_name="ambar.safetensors")
        s.add(sub)
        s.flush()
        sid = sub.id
    privado.confirm(sid, "admin", True, True)
    var = lora.make_variant(sid, "sdxl")
    lora._update(var["id"], lora_status="ready", lora_name="ambar_sdxl.safetensors")
    p = privado.parse_request("hazme una foto de Ámbar con sdxl en la playa")
    assert p["subject"]["name"] == "Ámbar · SDXL" and p["preset"] == "sdxl" and "Ámbar" not in p["prompt"]
    assert privado.parse_request("hazme una foto de Ámbar en la playa")["preset"] == "zimage"


def test_review_adult_images_privately():
    """Aprobar / rechazar en la Adult Zone: queda marcado, no publica nada."""
    privado._append({"id": "rev-0", "batch": "rev", "at": "2099", "status": "done", "file": "rev-0.png"})
    assert privado.review("rev-0", True, "admin")["review"] == "approved"
    assert next(x for x in privado.entries() if x["id"] == "rev-0")["review"] == "approved"
    privado.review("rev-0", False)
    assert next(x for x in privado.entries() if x["id"] == "rev-0")["review"] == "rejected"
    privado.review("rev-0", None)
    assert next(x for x in privado.entries() if x["id"] == "rev-0")["review"] == ""


def test_sdxl_version_trains_with_her_private_photos():
    """«Valentina · SDXL» aprende también de las fotos privadas de Valentina (si ahí eligieron usarlas)."""
    from app import lora
    init_db()
    sid = _subject("Brisa")
    privado.confirm(sid, "admin", True, True)
    privado._append({"id": "br-0", "batch": "br", "at": "2099", "status": "done", "file": "br-0.png", "subject_id": sid})
    (settings.DATA_DIR / "privado" / "br-0.png").write_bytes(_png("green"))
    privado.add_ref_from_entry(sid, "br-0")
    var = lora.make_variant(sid, "sdxl")
    assert privado.private_paths(var["id"]) == []                 # todavía no eligieron usarlas
    lora._update(sid, info={"train_private": True})
    assert len(privado.private_paths(var["id"])) == 1
    assert any(privado.is_private_path(p) for p in lora.dataset_images(var["id"]))



def test_adult_zone_style_lora_learns_from_approved(monkeypatch):
    """🎓 La LoRA propia de la Adult Zone aprende de las fotos aprobadas, no aparece en el Banco y se suma en SDXL."""
    import asyncio as _a
    from app import lora, main
    init_db()
    for i in range(9):
        eid = f"st-{i}"
        privado._append({"id": eid, "batch": "st", "at": "2099", "status": "done", "file": f"{eid}.png",
                         "prompt": "adult woman, loft" if i else "una colegiala", "review": "approved"})
        (settings.DATA_DIR / "privado" / f"{eid}.png").write_bytes(_png("red"))
    ds = privado.style_dataset()
    assert len([p for p in ds if "st-" in p]) == 8                      # la que menciona menores se descarta
    started = {}
    monkeypatch.setattr(lora, "start", lambda sid, **k: started.update(sid=sid) or {"queued": True})
    privado.style_train()
    sid = started["sid"]
    with session() as s:
        sub = s.get(Subject, sid)
        assert sub.name == privado.STYLE_NAME and len(sub.references) >= 8
        sub.lora_status, sub.lora_name, sub.lora_trigger = "ready", "estilo.safetensors", "azst"
    assert all(x["id"] != sid for x in _a.run(main.subjects()))         # no aparece en el Banco
    assert privado._style_lora("sdxl") == ("estilo.safetensors", "azst")
    assert privado._style_lora("flux") is None
    for i in range(9):
        privado.delete(f"st-{i}")
    with session() as s:                                                 # no dejarla activa para otras pruebas
        s.delete(s.get(Subject, sid))



def test_own_photos_for_the_adult_zone_lora_need_confirmation():
    """Subir fotos propias para la LoRA de la Adult Zone: solo con la confirmación (IA, inventadas, +18)."""
    import io
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (32, 32), "blue").save(b, "PNG")
    try:
        privado.style_upload(b.getvalue(), "admin", confirmed=False)
    except ValueError as e:
        assert "inventadas" in str(e)
    else:
        raise AssertionError("sin confirmar no debía dejar")
    r = privado.style_upload(b.getvalue(), "admin", confirmed=True)
    assert any(r["file"] in p for p in privado.style_dataset())
    assert privado.style_uploads()[-1]["by"] == "admin"
    privado.style_remove(r["file"])
    assert not any(r["file"] in p for p in privado.style_dataset())
