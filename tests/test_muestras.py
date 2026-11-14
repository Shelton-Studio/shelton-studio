"""📸 Muestras del entrenamiento: una foto numerada cada 100 pasos y «usa la foto 42» deja esa versión exacta."""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["IMAGE_ENGINE"] = "mock"
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="shelton_smp_"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_chain  # noqa: E402,F401

from app import lora, lora_muestras  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import Subject, init_db, session  # noqa: E402


def _ckpt(path: Path, step: int) -> None:
    header = json.dumps({"__metadata__": {"training_info": json.dumps({"step": step})}}).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(len(header).to_bytes(8, "little") + header + b"\0" * 16)


def _subject(name: str) -> str:
    init_db()
    with session() as s:
        sub = Subject(name=name, kind="person", description="mujer inventada de 28 años")
        s.add(sub)
        s.flush()
        return sub.id


def test_config_samples_every_100_steps_with_a_saved_point_each():
    cfg = lora.config_dict("job", "/w", "tok", 2000, "estandar", "zimage")
    p = cfg["config"]["process"][0]
    assert p["sample"]["sample_every"] == 100 and p["train"]["disable_sampling"] is False
    assert p["save"]["save_every"] == 100 and p["save"]["max_step_saves_to_keep"] >= 12
    # lo demás del entrenamiento no cambia
    assert p["train"]["steps"] == 2000 and p["model"]["arch"] == "zimage:turbo"


def test_samples_are_numbered_and_a_number_pins_that_exact_version(monkeypatch):
    sid = _subject("Mora")
    job = lora.job_for(sid)
    out = settings.DATA_DIR / "lora" / sid / "output" / job
    for step in (100, 200, 300):
        (out / "samples").mkdir(parents=True, exist_ok=True)
        (out / "samples" / f"17000000{step}__{step:09d}_0.jpg").write_bytes(b"jpg")
        _ckpt(out / f"{job}_{step:09d}.safetensors", step)
    new = lora_muestras.collect(sid)
    assert [(x["n"], x["step"]) for x in new] == [(1, 100), (2, 200), (3, 300)]
    assert lora_muestras.collect(sid) == []                       # no se repiten
    # siguen numerándose aunque empiece otra sesión
    (out / "samples" / f"1800000400__{400:09d}_0.jpg").write_bytes(b"jpg")
    assert lora_muestras.collect(sid)[0]["n"] == 4
    d = lora_muestras.listing(sid)
    assert d["samples"][0]["label"] == "Mora #0004 · paso 400"
    # «usa la foto 2» → la LoRA exacta del paso 200 queda activa
    Path(settings.COMFY_LORA_DIR).expanduser().mkdir(parents=True, exist_ok=True)
    r = lora_muestras.use(sid, 2)
    assert r["step"] == 200 and lora._read_saved_step(settings.DATA_DIR / "lora" / sid / "versiones" / r["active_version"]) == 200
    assert lora_muestras.listing(sid)["pinned"] == 2
    # la del paso 400 ya no tiene punto guardado: lo dice y propone la más cercana
    try:
        lora_muestras.use(sid, 4)
    except ValueError as e:
        assert "paso 400" in str(e) and "300" in str(e)
    else:
        raise AssertionError("debía avisar que esa versión ya no está")


def test_shelton_can_list_and_use_samples_by_number():
    import asyncio
    from app.agents import shelton
    sid = _subject("Lirio")
    job = lora.job_for(sid)
    out = settings.DATA_DIR / "lora" / sid / "output" / job
    (out / "samples").mkdir(parents=True, exist_ok=True)
    (out / "samples" / f"1700000100__{100:09d}_0.jpg").write_bytes(b"jpg")
    _ckpt(out / f"{job}_{100:09d}.safetensors", 100)
    lora_muestras.collect(sid)
    Path(settings.COMFY_LORA_DIR).expanduser().mkdir(parents=True, exist_ok=True)

    async def emit(ev):
        pass
    ctx = {"conv_id": None, "engine": None}
    got = json.loads(asyncio.run(shelton._run_tool("training_samples", {"subject": "Lirio"}, ctx, emit)))
    assert got["samples"][0]["number"] == 1 and got["samples"][0]["step"] == 100
    got = json.loads(asyncio.run(shelton._run_tool("use_training_sample", {"subject": "Lirio", "number": 1}, ctx, emit)))
    assert got["ok"] and "#0001" in got["now_active"]


def test_samples_screen_lists_models_with_numbers_and_steps_and_posts_record_lora_steps():
    from fastapi.testclient import TestClient
    from app.main import app
    sid = _subject("Pantalla")
    job = lora.job_for(sid)
    out = settings.DATA_DIR / "lora" / sid / "output" / job
    (out / "samples").mkdir(parents=True, exist_ok=True)
    for step in (100, 300):
        (out / "samples" / f"17{step}__{step:09d}_0.jpg").write_bytes(b"jpg")
    lora_muestras.collect(sid)
    with TestClient(app) as c:
        d = c.get("/api/muestras").json()
    m = next(x for x in d["models"] if x["id"] == sid)
    assert [(x["n"], x["step"]) for x in m["samples"]] == [(2, 300), (1, 100)]
    assert lora_muestras.last(sid)["label"] == "Pantalla #0002 · paso 300"
    # la LoRA que se usa para generar dice sus pasos
    lora_dir = Path(settings.COMFY_LORA_DIR).expanduser()
    _ckpt(lora_dir / "pantalla.safetensors", 2500)
    with session() as s:
        sub = s.get(Subject, sid)
        sub.lora_name = "pantalla.safetensors"
        s.flush()
        assert lora.used_step(sub) == 2500 and lora.fmt_steps(20700) == "20.700"


def test_training_instructions_shape_captions_and_samples(monkeypatch):
    """🎯 Indicaciones al entrenar: se guardan, guían la descripción de cada foto y arman las fotos de muestra."""
    import asyncio
    from fastapi.testclient import TestClient
    from app import llm
    from app.main import app
    sid = _subject("Indicada")
    with TestClient(app) as c:
        r = c.put(f"/api/subjects/{sid}/lora/indicaciones",
                  json={"brief": "que aprenda su lunar en la mejilla izquierda; no la ropa", "sample_prompt": "retrato de frente, luz de ventana"})
        assert r.status_code == 200 and r.json()["brief"].startswith("que aprenda")
        assert c.get(f"/api/subjects/{sid}/lora/indicaciones").json()["sample_prompt"] == "retrato de frente, luz de ventana"
    assert lora.brief_for(sid).startswith("que aprenda") and lora.sample_prompt_for(sid)
    cfg = lora.config_dict("job", "/w", "tok_x", 1000, "estandar", "zimage", lora.sample_prompt_for(sid))
    assert cfg["config"]["process"][0]["sample"]["prompts"] == ["tok_x, retrato de frente, luz de ventana"]
    # sin indicaciones, las muestras son las de siempre
    assert lora.config_dict("job", "/w", "tok_x", 1000)["config"]["process"][0]["sample"]["prompts"] == ["a photo of tok_x"]
    # la descripción de las fotos sigue las indicaciones
    seen = []

    async def fake_json_call(system, content, schema, **k):
        seen.append(system)
        return {"captions": ["a caption"] * (len(content) - 1)}
    monkeypatch.setattr(llm, "json_call", fake_json_call)
    img = settings.DATA_DIR / "ind.jpg"
    img.write_bytes(b"x")
    monkeypatch.setattr(lora, "session_images", lambda i: [str(img)])
    monkeypatch.setattr("app.media.image_block", lambda p, s=0: {"type": "image"})
    asyncio.run(lora.caption_photos(sid, "tok_x"))
    assert seen and "lunar en la mejilla izquierda" in seen[0]
