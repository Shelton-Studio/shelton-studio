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
import json, sys, time, pathlib
cfg = json.load(open(next(a for a in sys.argv[1:] if a.endswith(".json"))))
p = cfg["config"]["process"][0]
name, steps = cfg["config"]["name"], p["train"]["steps"]
assert p["model"]["arch"] == "zimage:turbo" and p["model"]["assistant_lora_path"].endswith(".safetensors")
imgs = list(pathlib.Path(p["datasets"][0]["folder_path"]).glob("*.jpg"))
assert len(imgs) >= 8 and all(i.with_suffix(".txt").read_text().startswith("a photo of " + p["trigger_word"]) for i in imgs)
for s in (0, steps // 2, steps):
    print(f"{name}:  {100*s//steps}%|####| {s}/{steps} [00:01<00:01, 1.0s/it, loss: 0.1]", flush=True)
out = pathlib.Path(p["training_folder"]) / name
out.mkdir(parents=True, exist_ok=True)
(out / f"{name}.safetensors").write_bytes(b"LORA")
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
        await lora._tasks[sid]
    asyncio.run(main())
    with session() as s:
        sub = s.get(Subject, sid)
        assert sub.lora_status == "ready", sub.lora_info
        assert sub.lora_progress == 100 and sub.lora_trigger.startswith("shlcamilatest")
        assert (loras_dir / sub.lora_name).read_bytes() == b"LORA"
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
