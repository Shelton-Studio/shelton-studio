"""LoRA propia de cada modelo: preparar fotos, entrenar (con un entrenador simulado), dejarla en ComfyUI y usarla."""
import asyncio
import io
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

os.environ["IMAGE_ENGINE"] = "mock"
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="shelton_lora_"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_chain  # noqa: E402,F401  (agentes simulados)
from PIL import Image  # noqa: E402

from app import lora, pipeline  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import Post, Subject, init_db, session  # noqa: E402
from app.engines.comfy import inject_loras  # noqa: E402
from app.comfyui.client import load_workflow  # noqa: E402

FAKE_TRAINER = r'''#!/usr/bin/env python3
# Entrenador simulado que se comporta como ai-toolkit: retoma desde el paso guardado y la barra cuenta el TOTAL.
import json, sys, time, pathlib
cfg = json.load(open(next(a for a in sys.argv[1:] if a.endswith(".json"))))
p = cfg["config"]["process"][0]
name, steps = cfg["config"]["name"], p["train"]["steps"]
assert p["model"]["arch"] == "zimage:turbo" and p["model"]["assistant_lora_path"].endswith(".safetensors")
imgs = list(pathlib.Path(p["datasets"][0]["folder_path"]).glob("*.jpg"))
assert len(imgs) >= 8 and all(i.with_suffix(".txt").read_text().startswith("a photo of " + p["trigger_word"]) for i in imgs)
out = pathlib.Path(p["training_folder"]) / name
out.mkdir(parents=True, exist_ok=True)

def read_step(f):
    data = f.read_bytes(); n = int.from_bytes(data[:8], "little")
    meta = json.loads(data[8:8 + n]).get("__metadata__", {})
    return json.loads(meta.get("training_info", "{}")).get("step", 0)

start = max([read_step(f) for f in out.glob("*.safetensors")] or [0])
print(f"resuming from step {start}" if start else "starting fresh", flush=True)
for s in range(start, steps + 1, max(1, (steps - start) // 20)):
    print(f"{name}:  {100*s//steps}%|####| {s}/{steps} [00:01<00:01, 1.0s/it, loss: 0.1]", flush=True)
    time.sleep(0.01)
print(f"{name}:  100%|####| {steps}/{steps} [00:01<00:00, 1.0s/it, loss: 0.1]", flush=True)
header = json.dumps({"__metadata__": {"training_info": json.dumps({"step": steps})}}).encode()
(out / f"{name}.safetensors").write_bytes(len(header).to_bytes(8, "little") + header + b"PESOS")
'''


def _png(color) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (96, 128), color).save(b, "PNG")
    return b.getvalue()


def _setup(monkeypatch, tmp_path):
    aitk = tmp_path / "ai-toolkit"
    (aitk / "venv" / "bin").mkdir(parents=True)
    (aitk / "run.py").write_text("# ai-toolkit")
    py = aitk / "venv" / "bin" / "python"
    py.write_text(FAKE_TRAINER)
    py.chmod(py.stat().st_mode | stat.S_IEXEC)
    loras = tmp_path / "loras"
    loras.mkdir()
    monkeypatch.setattr(settings, "AITK_DIR", str(aitk))
    monkeypatch.setattr(settings, "COMFY_LORA_DIR", str(loras))
    return loras


def _subject(n_photos: int) -> str:
    refs = []
    for i in range(n_photos):
        p = settings.media_dir / f"lora_ref_{i}_{os.getpid()}.png"
        p.write_bytes(_png((i * 20 % 255, 80, 120)))
        refs.append(str(p))
    with session() as s:
        sub = Subject(name="Camila Test", kind="person", description="mujer de 21 años", references=refs)
        s.add(sub)
        s.flush()
        return sub.id


def test_inject_loras_rewires_model_chain():
    wf = load_workflow("z_image_turbo.json")
    inject_loras(wf, [("camila.safetensors", 0.9)])
    assert wf["shl_lora_0"]["inputs"] == {"model": ["1", 0], "lora_name": "camila.safetensors", "strength_model": 0.9}
    assert wf["4"]["inputs"]["model"] == ["shl_lora_0", 0]       # ModelSamplingAuraFlow ahora usa la LoRA
    assert not any(isinstance(v, dict) and v.get("inputs", {}).get("model") == ["1", 0]
                   for k, v in wf.items() if k != "shl_lora_0")


def test_train_needs_trainer_and_photos(monkeypatch, tmp_path):
    init_db()
    monkeypatch.setattr(settings, "AITK_DIR", str(tmp_path / "nada"))
    sid = _subject(3)
    ok, why = lora.available()
    assert not ok and "lora_setup.sh" in why
    _setup(monkeypatch, tmp_path)
    try:
        lora.start(sid)
    except ValueError as e:
        assert "al menos 8" in str(e)
    else:
        raise AssertionError("debía pedir más fotos")


def test_full_training_cycle_and_use_in_generation(monkeypatch, tmp_path):
    init_db()
    loras_dir = _setup(monkeypatch, tmp_path)
    sid = _subject(10)
    st = lora.status(sid)
    assert st["trainer_ready"] and st["images"] == 10

    async def main():
        r = lora.start(sid, 400)
        assert r["images"] == 10 and r["steps"] == 400
        await lora._worker
    asyncio.run(main())
    with session() as s:
        sub = s.get(Subject, sid)
        assert sub.lora_status == "ready", sub.lora_info
        assert sub.lora_progress == 100 and sub.lora_trigger.startswith("shlcamilatest")
        assert (loras_dir / sub.lora_name).read_bytes().endswith(b"PESOS")
        name, trig = sub.lora_name, sub.lora_trigger

    # En una foto local, el modelo usa su LoRA (y la palabra clave va en el prompt)
    calls = {}

    class FakeComfy:
        name = "comfyui"
        url = "http://fake"

        async def generate_with_loras(self, loras, prompt, w, h, seed, step=None):
            calls["loras"], calls["prompt"] = loras, prompt
            return _png("red")

        async def generate_with_refs(self, *a, **k):
            raise AssertionError("con LoRA lista no debe usar referencias")
    monkeypatch.setattr(pipeline, "get_engine", lambda role="generate", choice=None: FakeComfy())
    with session() as s:
        p = Post(idea="Camila en un café", platform="instagram_feed", subject_ids=[sid], status="queued")
        s.add(p)
        s.flush()
        pid = p.id
    asyncio.run(pipeline._create_post(pid, {}))
    assert calls["loras"] == [(name, settings.LORA_STRENGTH)] and trig in calls["prompt"]
    with session() as s:
        post = s.get(Post, pid)
        assert post.status == "ready" and "LoRA Camila Test" in post.made_with["image"]

    # Si la apagan, vuelve a usar las fotos de referencia
    with session() as s:
        s.get(Subject, sid).lora_enabled = False
    with session() as s:
        assert lora.loras_for([s.get(Subject, sid)]) == []


def test_restart_marks_interrupted():
    init_db()
    with session() as s:
        sub = Subject(name="Otro", kind="person", lora_status="training")
        s.add(sub)
        s.flush()
        sid = sub.id
    lora.recover_after_restart()
    with session() as s:
        assert s.get(Subject, sid).lora_status == "failed"


def test_queue_trains_one_after_another(monkeypatch, tmp_path):
    """Varios modelos en fila: se entrenan solos, uno detrás de otro."""
    init_db()
    _setup(monkeypatch, tmp_path)
    a, b, c = _subject(9), _subject(9), _subject(9)

    async def main():
        ra = lora.start(a, 300)
        rb = lora.start(b, 300)
        rc = lora.start(c, 300)
        assert not ra["queued"] and rb["queued"] and rc["queued"] and rc["position"] == 3
        try:
            lora.start(b, 300)
        except ValueError as e:
            assert "fila" in str(e)
        else:
            raise AssertionError("no debía aceptar el mismo modelo dos veces")
        lora.cancel(c)                       # sacar uno de la fila
        await lora._worker
    asyncio.run(main())
    with session() as s:
        assert s.get(Subject, a).lora_status == "ready"
        assert s.get(Subject, b).lora_status == "ready"
        assert s.get(Subject, c).lora_status == "canceled"


def test_continue_adds_steps_and_restart_keeps_old(monkeypatch, tmp_path):
    """«Entrena 2 horas más» suma a lo aprendido (no repite ni inventa pasos); «de cero» guarda lo viejo aparte."""
    init_db()
    _setup(monkeypatch, tmp_path)
    sid = _subject(9)

    async def run(**kw):
        r = lora.start(sid, **kw)
        await lora._worker
        return r
    r1 = asyncio.run(run(steps=400))
    assert r1["mode"] == "restart" and r1["total_steps"] == 400
    assert lora.saved_step(sid) == 400
    spp = lora.sec_per_step(sid)
    assert spp > 0

    # Seguir entrenando +200 pasos: sigue desde 400 y llega a 600
    p = lora.plan(sid, "continue", steps=200)
    assert p == {**p, "mode": "continue", "from_step": 400, "add_steps": 200, "total_steps": 600}
    r2 = asyncio.run(run(steps=200, mode="continue"))
    assert r2["from_step"] == 400 and r2["total_steps"] == 600
    assert lora.saved_step(sid) == 600
    with session() as s:
        info = s.get(Subject, sid).lora_info
        assert info["trained_steps"] == 600 and info["step"] == 600 and s.get(Subject, sid).lora_status == "ready"
    log = (settings.DATA_DIR / "lora" / sid / "train.log").read_text()
    assert "resuming from step 400" in log

    # Pedido en tiempo: se convierte con la velocidad real medida (no se inventa)
    pm = lora.plan(sid, "continue", minutes=1)
    expected = max(100, min(int(60 / lora.sec_per_step(sid)), lora.MAX_SESSION_STEPS))
    assert pm["add_steps"] == expected and pm["from_step"] == 600 and pm["total_steps"] == 600 + expected

    # Empezar de cero: lo viejo se guarda aparte (no se borra) y arranca desde 0
    r3 = asyncio.run(run(steps=300, mode="restart"))
    assert r3["from_step"] == 0 and r3["total_steps"] == 300
    out = settings.DATA_DIR / "lora" / sid / "output"
    assert any("_anterior_" in d.name for d in out.iterdir())
    assert lora.saved_step(sid) == 300


def test_every_finished_training_is_kept_as_a_version(monkeypatch, tmp_path):
    """Lo aprendido nunca se pierde: cada entrenamiento terminado queda como versión y se puede volver a ella."""
    init_db()
    loras_dir = _setup(monkeypatch, tmp_path)
    sid = _subject(9)

    async def run(**kw):
        lora.start(sid, **kw)
        await lora._worker
    asyncio.run(run(steps=300))
    asyncio.run(run(steps=200, mode="continue"))
    vs = lora.versions(sid)
    assert [v["steps"] for v in vs] == [500, 300]          # las dos versiones, la más nueva primero
    st = lora.status(sid)
    assert len(st["versions"]) == 2 and st["saved_step"] == 500
    r = lora.use_version(sid, vs[1]["file"])                 # volver a la de 300 pasos
    with session() as s:
        sub = s.get(Subject, sid)
        assert sub.lora_status == "ready" and sub.lora_info["active_version"] == vs[1]["file"]
        assert lora._read_saved_step(loras_dir / sub.lora_name) == 300
    assert len(lora.versions(sid)) == 2                      # no se borró ninguna
