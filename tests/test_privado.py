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
    # desde lo generado en Shelton Hot
    privado._append({"id": "g-0", "batch": "g", "at": "2099", "status": "done", "file": "g-0.png", "subject_id": sid})
    (settings.DATA_DIR / "privado" / "g-0.png").write_bytes(_png("green"))
    privado.add_ref_from_entry(sid, "g-0")
    assert len(privado.model_private(sid)["refs"]) == 2
    # al entrenar: su LoRA normal NUNCA las ve; solo su 🔞 LoRA adulta (Shelton Hot)
    lora._update(sid, info={"train_private": True})
    assert not any(privado.is_private_path(p) for p in lora.dataset_images(sid))
    priv = [p for p in lora.dataset_images(privado.adult_twin_id(sid)) if privado.is_private_path(p)]
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
    """Si Shelton se reinicia o se corta la conexión, las fotos de Shelton Hot no se pierden: se retoman solas."""
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
    """Aprobar / rechazar en Shelton Hot: queda marcado, no publica nada."""
    privado._append({"id": "rev-0", "batch": "rev", "at": "2099", "status": "done", "file": "rev-0.png"})
    assert privado.review("rev-0", True, "admin")["review"] == "approved"
    assert next(x for x in privado.entries() if x["id"] == "rev-0")["review"] == "approved"
    privado.review("rev-0", False)
    assert next(x for x in privado.entries() if x["id"] == "rev-0")["review"] == "rejected"
    privado.review("rev-0", None)
    assert next(x for x in privado.entries() if x["id"] == "rev-0")["review"] == ""


def test_adult_lora_stays_only_in_the_adult_zone(monkeypatch):
    """La 🔞 LoRA adulta: aprende de sus fotos privadas, no aparece en el Banco ni en el Shelton normal,
    y sus LoRAs normales (incluida «· SDXL») nunca ven lo privado. Nombrarla usa la 🔞 sola."""
    import asyncio
    from app import lora, main
    init_db()
    sid = _subject("Brisa")
    privado.confirm(sid, "admin", True, True)
    privado._append({"id": "br-0", "batch": "br", "at": "2099", "status": "done", "file": "br-0.png", "subject_id": sid})
    (settings.DATA_DIR / "privado" / "br-0.png").write_bytes(_png("green"))
    privado.add_ref_from_entry(sid, "br-0")
    lora._update(sid, info={"train_private": True})
    var = lora.make_variant(sid, "sdxl")
    assert privado.private_paths(var["id"]) == [] and privado.private_paths(sid) == []
    tw = privado.adult_twin_id(sid)
    assert tw == privado.adult_twin_id(sid)                       # una sola
    assert len(privado.private_paths(tw)) == 1
    names = [x["name"] for x in asyncio.run(main.subjects())]
    assert "Brisa" in names and not any("🔞" in n for n in names)
    started = []
    monkeypatch.setattr(lora, "available", lambda: (True, ""))
    monkeypatch.setattr(lora, "start", lambda i, *a, **k: started.append(i) or {"ok": True})
    r = privado.train_adult(sid)                                  # sin RTX prendida: en la placa
    assert started == [tw] and r["where"] == "la placa"
    m = next(x for x in privado.models() if x["id"] == sid)
    assert m["adult"]["id"] == tw and not m["adult"]["ready"]
    lora._update(tw, lora_status="ready", lora_name="brisa_adulta.safetensors")
    p = privado.parse_request("hazme una foto de Brisa en la playa")
    assert p["subject"]["id"] == tw and p["preset"] == "sdxl" and "Brisa" not in p["prompt"]

def test_adult_zone_style_lora_learns_from_approved(monkeypatch):
    """🎓 La LoRA propia de Shelton Hot aprende de las fotos aprobadas, no aparece en el Banco y se suma en SDXL."""
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
    """Subir fotos propias para la LoRA de Shelton Hot: solo con la confirmación (IA, inventadas, +18)."""
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


def test_adult_lora_keeps_learning_with_new_photos(monkeypatch):
    """Cada 5 fotos nuevas, su 🔞 LoRA adulta sigue aprendiendo sola (en la RTX si está prendida, si no la placa)."""
    from app import granja, lora
    init_db()
    sid = _subject("Nube")
    privado.confirm(sid, "admin", True, True)
    tw = privado.adult_twin_id(sid)
    started = []
    monkeypatch.setattr(lora, "available", lambda: (True, ""))
    monkeypatch.setattr(lora, "start", lambda i, *a, **k: started.append(i) or {"ok": True})
    for c in ("red", "blue", "green", "white"):
        privado.add_ref_upload(sid, _png(c))
    assert privado.auto_learn(sid) == ""                     # todavía no tiene LoRA adulta: no hace nada solo
    lora._update(tw, lora_status="ready", lora_name="nube_adulta.safetensors")
    assert privado.auto_learn(sid) == "" and privado.new_photos(sid) == 4     # 4 nuevas: espera a 5
    privado.add_ref_upload(sid, _png("black"))
    assert next(m for m in privado.models() if m["id"] == sid)["new_photos"] == 5
    assert privado.auto_learn(sid) and started == [tw]
    # con la RTX prendida va ahí, como en el Shelton normal
    lora._update(tw, lora_status="ready")
    on = []
    monkeypatch.setattr(granja, "remote_nodes", lambda: [{"id": "rtx", "label": "RTX", "slots": 1}])
    monkeypatch.setitem(granja._health, "rtx", (9e18, {"ok": True}))
    monkeypatch.setattr(lora, "start_on", lambda i, n, **k: on.append((i, n)) or {"ok": True})
    assert privado.train_adult(sid)["where"] == "RTX" and on == [(tw, "rtx")]


def test_adult_photos_get_her_zimage_face(monkeypatch):
    """Foto con su 🔞 LoRA adulta (SDXL) → segunda pasada automática con Z-Image + su LoRA de siempre: su cara tal cual."""
    import asyncio
    from app import lora
    from app.comfyui.client import load_workflow
    from app.engines.comfy import face_workflow
    init_db()
    # el workflow de Z-Image se convierte en «re-dibujar solo la cara de esta imagen»
    wf = load_workflow("z_image_turbo.json")
    assert face_workflow(wf, "foto.png", 0.45)
    save = next(v for v in wf.values() if isinstance(v, dict) and v.get("class_type") == "SaveImage")
    assert save["inputs"]["images"] == ["shl_face", 0] and wf["shl_src"]["inputs"]["image"] == "foto.png"
    # la modelo: LoRA normal de Z-Image (su cara) + su 🔞 LoRA adulta (SDXL)
    sid = _subject("Rocío")
    lora._update(sid, lora_status="ready", lora_name="rocio.safetensors", lora_trigger="rocio_tk")
    privado.confirm(sid, "admin", True, True)
    tw = privado.adult_twin_id(sid)
    lora._update(tw, lora_status="ready", lora_name="rocio_adulta.safetensors")
    with privado.session() as s:
        face = privado._face_lora(s.get(privado.Subject, tw))
    assert face["lora"] == "rocio.safetensors" and face["trigger"] == "rocio_tk"

    class Fake:
        url = "http://x"
        values = {"generate": {}}

        async def generate_with_loras(self, loras, prompt, w, h, seed):
            return b"sdxl"

    calls = []

    async def fake_face(raw, f, prompt, seed):
        calls.append((raw, f["lora"]))
        return b"con-su-cara"
    monkeypatch.setattr(privado, "_put_face", fake_face)
    asyncio.run(privado._run_ids(["fc-0"], Fake(), {}, "p", "n", 64, 64, [("rocio_adulta.safetensors", 1.0)], face))
    assert calls == [(b"sdxl", "rocio.safetensors")]
    assert (privado._dir() / "fc-0.png").read_bytes() == b"con-su-cara"


def test_choosing_the_model_in_the_form_uses_her_adult_lora_and_her_face(monkeypatch):
    """Elegirla en «Modelo» con motor SDXL: cuerpo con su 🔞 LoRA adulta + cara con su LoRA de Z-Image."""
    import asyncio
    from app import lora
    init_db()
    sid = _subject("Abril")
    lora._update(sid, lora_status="ready", lora_name="abril.safetensors")
    privado.confirm(sid, "admin", True, True)
    tw = privado.adult_twin_id(sid)
    lora._update(tw, lora_status="ready", lora_name="abril_adulta.safetensors")
    got = {}

    async def fake_run(batch, count, engine, cfg, full, neg, w, h, loras, face=None):
        got.update(loras=loras, face=face)
    monkeypatch.setattr(privado, "_run", fake_run)
    monkeypatch.setattr(privado, "_engine", lambda p, c: ({}, object()))
    monkeypatch.setattr(privado, "_style_lora", lambda p: None)

    async def go():
        r = await privado.generate("admin", "en la playa", subject_id=sid, preset="sdxl", ckpt="x.safetensors")
        await asyncio.sleep(0)
        return r
    asyncio.run(go())
    assert got["loras"][0][0] == "abril_adulta.safetensors" and got["face"]["lora"] == "abril.safetensors"


def test_shelton_hot_is_fast_cached_registry_thumbnails_and_checkpoints(monkeypatch):
    """Shelton Hot rápido: el registro se relee solo si cambió, la grilla usa miniaturas y la lista de checkpoints
    no consulta a ComfyUI en cada clic."""
    import asyncio
    privado._append({"id": "fast-0", "batch": "fast", "at": "2099-01-01", "status": "done", "file": "fast-0.png"})
    (settings.DATA_DIR / "privado" / "fast-0.png").write_bytes(_png("red"))
    assert any(e["id"] == "fast-0" for e in privado.entries())
    privado._append({"id": "fast-0", "review": "approved"})
    assert next(e for e in privado.entries() if e["id"] == "fast-0")["review"] == "approved"   # ve el cambio
    t = privado.thumb("fast-0.png")
    assert t and t.suffix == ".jpg" and t.parent.name == "miniaturas"
    calls = []

    class FakeClient:
        async def object_info(self, node):
            calls.append(node)
            return {"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["a.safetensors", "flux1.safetensors"]]}}}}
    monkeypatch.setattr("app.comfyui.client.ComfyUIClient", FakeClient)
    privado._ckpt_cache.update(t=0.0, list=[])
    assert asyncio.run(privado.checkpoints()) == ["a.safetensors"]
    assert asyncio.run(privado.checkpoints()) == ["a.safetensors"] and len(calls) == 1      # guardada
    privado.delete("fast-0")
    assert not t.exists()


def test_upload_ai_image_and_remake_with_a_model(monkeypatch):
    """⬆ Subir una imagen IA (solo con la confirmación) y 🔁 rehacerla con la cara, o la cara y el cuerpo, de la modelo."""
    import asyncio
    from app import lora
    try:
        privado.upload_ai_image(_png("red"), "admin", confirmed=False)
    except ValueError as e:
        assert "IA" in str(e)
    else:
        raise AssertionError("sin confirmar no debía dejar")
    up = privado.upload_ai_image(_png("red"), "admin", confirmed=True)
    e = next(x for x in privado.entries() if x["id"] == up["id"])
    assert e["status"] == "done" and e["confirmed"] == "IA · inventada · +18"
    sid = _subject("Remake")
    lora._update(sid, lora_status="ready", lora_name="remake.safetensors", lora_trigger="rmk")
    privado.confirm(sid, "admin", True, True)
    calls = []

    class Eng:
        async def img2img(self, image, loras, prompt, seed, denoise=0.5):
            calls.append(("cuerpo", loras[0][0], denoise))
            return image

        async def face_pass(self, image, loras, prompt, seed, denoise=0.45):
            calls.append(("cara", loras[0][0], denoise))
            return image
    monkeypatch.setattr(privado, "_engine", lambda p, c: ({}, Eng()))

    async def go(mode):
        r = privado.remake(up["id"], sid, mode, "suave", "admin")
        for _ in range(30):
            await asyncio.sleep(0.01)
        return next(x for x in privado.entries() if x["id"] == r["id"])
    out = asyncio.run(go("cara"))
    assert out["status"] == "done" and calls == [("cara", "remake.safetensors", privado.REMAKE_FACE)]
    calls.clear()
    out = asyncio.run(go("cuerpo"))
    assert [c[0] for c in calls] == ["cuerpo", "cara"] and calls[0][2] == privado.REMAKE_LEVELS["suave"]


def test_hot_chat_understands_put_her_face_on_the_uploaded_image(monkeypatch):
    from app import lora
    sid = _subject("Chatcara")
    lora._update(sid, lora_status="ready", lora_name="chatcara.safetensors")
    privado.confirm(sid, "admin", True, True)
    up = privado.upload_ai_image(_png("red"), "admin", confirmed=True)
    hit = privado._remake_intent("ponle el cuerpo y la cara de Chatcara, medio", up["id"])
    assert hit["mode"] == "cuerpo" and hit["level"] == "medio" and hit["subject"]["id"] == sid
    assert privado._remake_intent("ponle la cara de Chatcara")["mode"] == "cara"          # usa la última subida
    assert privado._remake_intent("3 fotos de Chatcara en la playa, cara sonriente") is None   # pedido normal


def test_choosing_a_checkpoint_in_hot_chat_forces_sdxl_with_her_face(monkeypatch):
    """Si eligió un checkpoint en el chat, va con SDXL aunque la modelo solo tenga su LoRA de Z-Image; la cara se le
    pone después con su LoRA de siempre."""
    import asyncio
    from app import lora
    sid = _subject("Chkpt")
    lora._update(sid, lora_status="ready", lora_name="chkpt.safetensors")
    privado.confirm(sid, "admin", True, True)
    got = {}

    async def fake_generate(by, prompt, subject_id="", preset="sdxl", ckpt="", **k):
        got.update(preset=preset, ckpt=ckpt, subject_id=subject_id)
        return {"batch": "b", "with_lora": False, "translated": "", "prompt_en": prompt}
    monkeypatch.setattr(privado, "generate", fake_generate)
    asyncio.run(privado.chat("admin", "una foto de Chkpt en la cama", "", "cyberrealistic.safetensors"))
    assert got == {"preset": "sdxl", "ckpt": "cyberrealistic.safetensors", "subject_id": sid}
    asyncio.run(privado.chat("admin", "una foto de Chkpt en la cama"))
    assert got["preset"] == "zimage"                               # sin checkpoint elegido, como siempre


def test_hot_chat_keeps_several_conversations_and_can_delete_them():
    """💬 Shelton Hot: varias conversaciones guardadas en la placa; se puede empezar de cero y borrar."""
    a = privado.new_thread("admin")
    b = privado.new_thread("admin")
    privado.add_message(a["id"], "me", "3 fotos de Luna en un loft")
    privado.add_message(a["id"], "bot", "Listo")
    t = next(x for x in privado.threads() if x["id"] == a["id"])
    assert t["title"] == "3 fotos de Luna en un loft" and len(t["messages"]) == 2
    assert privado.threads()[0]["id"] == a["id"]                  # la más reciente primero
    privado.delete_thread(a["id"])
    assert all(x["id"] != a["id"] for x in privado.threads()) and any(x["id"] == b["id"] for x in privado.threads())
    assert (privado._threads_file()).parent == privado._dir()     # privadas, junto a lo de Shelton Hot


def test_model_names_are_not_translated():
    """«hazme a Valentina en la playa» → no se traduce «Valentina» (salía «valentine»): su LoRA ya la pone."""
    _subject("Valentinax")
    out = privado.clean_for_translation("hazme a Valentinax en la playa, luz de ventana")
    assert "valentinax" not in out.lower() and not out.lower().startswith("hazme") and "playa" in out


def test_adult_lora_can_choose_where_to_train_and_be_canceled(monkeypatch):
    """🎓 LoRA adulta: elegir placa o la RTX (como en el otro Shelton) y ✕ cancelar."""
    from app import granja, lora
    sid = _subject("Donde")
    privado.confirm(sid, "admin", True, True)
    node = {"id": "node:rtx", "label": "RTX PRO 6000", "slots": 3}
    monkeypatch.setattr(granja, "remote_nodes", lambda: [node])
    monkeypatch.setitem(granja._health, "node:rtx", (9e18, {"ok": True}))
    calls, canceled = [], []
    monkeypatch.setattr(lora, "start", lambda i, *a, **k: calls.append(("placa", i)) or {"ok": True})
    monkeypatch.setattr(lora, "start_on", lambda i, n, **k: calls.append((n, i)) or {"ok": True})
    monkeypatch.setattr(lora, "cancel", lambda i: canceled.append(i))
    assert privado.train_adult(sid, "placa")["where"] == "la placa"
    assert privado.train_adult(sid, "node:rtx")["where"] == "RTX PRO 6000"
    assert privado.train_adult(sid)["where"] == "RTX PRO 6000"                 # automático: la RTX si está prendida
    tw = privado.adult_twin_id(sid)
    assert [c[0] for c in calls] == ["placa", "node:rtx", "node:rtx"] and all(c[1] == tw for c in calls)
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        assert c.post(f"/api/privado/modelo/{sid}/lora-adulta/cancelar").json()["ok"]
    assert canceled == [tw]
