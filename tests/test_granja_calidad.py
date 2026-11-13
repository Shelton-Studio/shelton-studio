"""Calidad máxima, comparador de versiones y granja de GPUs (tarjetas alquiladas con el trabajador de Shelton)."""
import asyncio
import os
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

os.environ["IMAGE_ENGINE"] = "mock"
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="shelton_cg_"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_chain  # noqa: E402,F401
from test_lora import FAKE_TRAINER, _png, _setup, _subject  # noqa: E402

from app import calidad, granja, lora, lora_compare, pipeline  # noqa: E402
from app.agents import specialists as sp  # noqa: E402
from app.comfyui.client import load_workflow  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import Connection, Post, Subject, UsageEvent, init_db, session  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ calidad máxima
def test_enhance_adds_refine_and_face_and_rewires_save():
    wf = load_workflow("z_image_turbo.json")
    done = calidad.enhance(wf, {"refine": True, "refine_scale": 1.5, "refine_denoise": 0.35, "face": True},
                           {"FaceDetailer", "UltralyticsDetectorProvider"})
    assert done == ["detalle", "rostro"]
    assert wf["shl_up"]["inputs"]["samples"] == ["8", 0] and wf["shl_refine"]["inputs"]["denoise"] == 0.35
    assert wf["shl_face"]["inputs"]["image"] == ["shl_decode", 0]
    assert wf["10"]["inputs"]["images"] == ["shl_face", 0]                 # se guarda la foto mejorada
    # Sin el Impact Pack: solo la segunda pasada, sin error
    wf2 = load_workflow("z_image_turbo.json")
    assert calidad.enhance(wf2, {"refine": True, "face": True}, set()) == ["detalle"]
    assert wf2["10"]["inputs"]["images"] == ["shl_decode", 0]
    # Se adapta a la versión instalada del nodo (agrega lo que pide, quita lo que no conoce)
    info = {"FaceDetailer": {"input": {"required": {"image": ["IMAGE"], "guide_size": ["FLOAT", {"default": 512}],
                                                    "nuevo_campo": ["INT", {"default": 7}]}, "optional": {}}}}
    calidad.fill_required(wf, info)
    assert wf["shl_face"]["inputs"]["nuevo_campo"] == 7 and "wildcard" not in wf["shl_face"]["inputs"]


def test_best_of_n_lets_the_judge_pick(monkeypatch, tmp_path):
    init_db()
    _setup(monkeypatch, tmp_path)
    sid = _subject(9)

    async def train():
        lora.start(sid, 300)
        await lora._worker
    asyncio.run(train())
    calidad.save(max_quality=True, candidates=3)
    scores = iter([55, 91, 70])
    seeds = []

    class FakeComfy:
        name = "comfyui"

        async def generate_with_loras(self, loras, prompt, w, h, seed, step=None):
            seeds.append(seed)
            return _png((len(seeds) * 60 % 255, 10, 10))

    async def fake_judge(path, *a, **k):
        return {"score": next(scores), "issues": [], "summary": "", "fix_hint": ""}
    monkeypatch.setattr(pipeline, "get_engine", lambda role="generate", choice=None: FakeComfy())
    monkeypatch.setattr(sp, "judge", fake_judge)
    with session() as s:
        p = Post(idea="Camila en la playa", platform="instagram_feed", subject_ids=[sid], status="queued")
        s.add(p)
        s.flush()
        pid = p.id
    try:
        asyncio.run(pipeline._create_post(pid, {}))
    finally:
        calidad.save(max_quality=False)
    assert len(seeds) == 3 and len(set(seeds)) == 3
    with session() as s:
        post = s.get(Post, pid)
        assert post.qa_score == 91 and len(post.alternatives) == 2      # se quedó con la mejor, las otras se guardan


# ------------------------------------------------------------------ comparador de versiones
def test_compare_versions_and_choose(monkeypatch, tmp_path):
    init_db()
    loras_dir = _setup(monkeypatch, tmp_path)
    sid = _subject(9)

    async def run(**kw):
        lora.start(sid, **kw)
        await lora._worker
    asyncio.run(run(steps=300))
    asyncio.run(run(steps=200, mode="continue"))
    cands = lora_compare.candidates(sid)
    assert [c["step"] for c in cands][:2] == [500, 300]
    calls = []

    class FakeComfy:
        name = "comfyui"

        async def generate_with_loras(self, loras, prompt, w, h, seed, step=None, quality=None):
            calls.append((loras[0], seed, quality))
            assert (loras_dir / loras[0][0]).exists()
            return _png((20, 20, 20))
    monkeypatch.setattr("app.engines.get_engine", lambda role="generate", choice=None: FakeComfy())

    async def go():
        job = lora_compare.start(sid, [c["key"] for c in cands[:2]], [0.8, 1.0])
        await lora_compare._tasks[job["id"]]
        return lora_compare.load(sid, job["id"])
    job = asyncio.run(go())
    assert job["status"] == "done" and len(job["cells"]) == 2 * 2 * 4 and all(c["image"] for c in job["cells"])
    assert {c[2] for c in calls} == {False}                       # comparación rápida y pareja (sin calidad máxima)
    assert len({c[1] for c in calls}) == 4                        # mismas 4 semillas para todas las versiones
    key300 = next(c["key"] for c in cands if c["step"] == 300)
    lora_compare.choose(sid, key300, 0.8)
    with session() as s:
        sub = s.get(Subject, sid)
        assert lora.strength_of(sub) == 0.8 and lora._read_saved_step(loras_dir / sub.lora_name) == 300
        assert lora.loras_for([sub]) and lora.strengths_for([sub]) == [0.8]
    assert lora.saved_step(sid) == 300                             # seguir entrenando, desde la elegida


# ------------------------------------------------------------------ granja: tarjeta alquilada con el trabajador
def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_remote_node_trains_two_models_at_once(monkeypatch, tmp_path):
    init_db()
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    loras_dir = tmp_path / "loras"
    loras_dir.mkdir()
    monkeypatch.setattr(settings, "COMFY_LORA_DIR", str(loras_dir))
    monkeypatch.setattr(settings, "AITK_DIR", str(tmp_path / "sin-entrenador-en-la-placa"))   # la placa no entrena
    # «Tarjeta alquilada»: el trabajador real con un entrenador simulado
    aitk = tmp_path / "node_aitk"
    (aitk / "venv" / "bin").mkdir(parents=True)
    (aitk / "run.py").write_text("# ai-toolkit")
    py = aitk / "venv" / "bin" / "python"
    py.write_text(FAKE_TRAINER)
    py.chmod(py.stat().st_mode | stat.S_IEXEC)
    node_loras = tmp_path / "node_loras"
    port = _free_port()
    env = {**os.environ, "WORKER_TOKEN": "secreto", "WORKER_PORT": str(port), "WORKER_HOST": "127.0.0.1",
           "AITK_DIR": str(aitk), "WORKER_DIR": str(tmp_path / "jobs"), "WORKER_GPUS": "MIG-a,MIG-b",
           "COMFY_LORA_DIR": str(node_loras)}
    proc = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "shelton_worker.py")], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        url = f"http://127.0.0.1:{port}"
        for _ in range(50):
            try:
                if httpx.get(f"{url}/health", headers={"Authorization": "Bearer secreto"}).status_code == 200:
                    break
            except Exception:  # noqa: BLE001
                time.sleep(0.1)
        assert httpx.get(f"{url}/health").status_code == 401          # sin token no entra nadie
        with session() as s:
            s.add(Connection(type="gpu_node", name="PRO 6000 alquilada", enabled=True,
                             config={"url": url, "token": "secreto", "slots": "2", "profile": "pro",
                                     "cost_hour": "2", "comfy_url": "http://x"}))
        a, b = _subject(9), _subject(9)
        monkeypatch.setattr(granja, "POLL", 0.2)
        captions = []

        async def fake_captions(sid, trigger):
            captions.append(sid)
            return {}
        monkeypatch.setattr(lora, "caption_photos", fake_captions)

        async def main():
            ra, rb = lora.start(a, 300), lora.start(b, 300)
            assert ra["started"] and rb["started"]
            seen_both = False
            while not lora._worker.done():
                with session() as s:
                    st = {s.get(Subject, a).lora_status, s.get(Subject, b).lora_status}
                seen_both = seen_both or st == {"training"}
                await asyncio.sleep(0.05)
            return seen_both
        both_at_once = asyncio.run(main())
        assert both_at_once                                            # dos entrenamientos a la vez en la misma tarjeta
        assert set(captions) == {a, b}                                 # perfil pro: describe cada foto antes
        for sid in (a, b):
            with session() as s:
                sub = s.get(Subject, sid)
                assert sub.lora_status == "ready", sub.lora_info
                assert (loras_dir / sub.lora_name).read_bytes().endswith(b"PESOS")    # la LoRA vuelve a la placa
                assert sub.lora_info["where"] == "PRO 6000 alquilada"
                assert (node_loras / sub.lora_name).exists()           # y se copia al ComfyUI del nodo
            assert lora.versions(sid)
        assert not any((tmp_path / "jobs").iterdir())                  # las fotos no quedan en la tarjeta alquilada
        with session() as s:
            ev = s.query(UsageEvent).filter(UsageEvent.kind == "lora", UsageEvent.provider == "gpu_node").all()
            assert len(ev) == 2 and all(e.cost_source == "real" for e in ev)
    finally:
        proc.terminate()
        proc.wait(5)
        with session() as s:
            s.query(Connection).filter(Connection.type == "gpu_node").delete()
        granja._health.clear()


def test_node_that_does_not_answer_sends_the_model_back_to_the_queue(monkeypatch, tmp_path):
    """Si la tarjeta alquilada se apagó justo cuando le tocaba, el modelo vuelve a la fila (no queda «fallido»)."""
    init_db()
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    _setup(monkeypatch, tmp_path)
    sid = _subject(9)
    lora._update(sid, lora_status="queued", info={"steps": 300, "mode": "restart", "queued_at": "x"})
    node = {"id": "node:x", "kind": "remote", "label": "PRO 6000 apagada", "url": f"http://127.0.0.1:{_free_port()}",
            "token": "t", "slots": 1, "profile": "pro", "cost_hour": 2.0, "comfy_url": ""}
    asyncio.run(lora._one(sid, node))
    with session() as s:
        sub = s.get(Subject, sid)
        assert sub.lora_status == "queued" and "no respondió" in sub.lora_info["phase"]
    assert granja._health["node:x"][1]["ok"] is False          # no se la vuelve a elegir enseguida
    granja._health.clear()
    lora._update(sid, lora_status="canceled")


def test_mixed_gpus_in_the_same_pc_have_their_own_profile(monkeypatch):
    """3090 + RTX PRO 6000 en la misma placa: cada GPU entrena con su perfil."""
    monkeypatch.setenv("LORA_GPUS", "0,1")
    monkeypatch.setenv("LORA_GPU_PROFILES", "0:estandar,1:pro")
    assert lora.local_gpus() == ["0", "1"]
    assert lora.profile_for_gpu("0") == "estandar" and lora.profile_for_gpu("1") == "pro"
    assert lora.profile_for_gpu(None) == "estandar"
    cfg = lora.config_dict("job", "/w", "trig", 100, lora.profile_for_gpu("1"))
    proc = cfg["config"]["process"][0]
    assert proc["network"]["linear"] == 32 and proc["model"]["quantize"] is False
