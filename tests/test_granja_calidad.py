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


def test_sdxl_and_flux_workflows_with_lora_and_quality():
    from app.comfyui.client import fill_workflow, placeholders
    from app.engines.comfy import ComfyEngine, inject_loras
    for preset, wfname in (("sdxl", "sdxl.json"), ("flux", "flux_dev.json")):
        e = ComfyEngine({"preset": preset})
        assert e.arch == preset and e.workflows["generate"] == wfname
        wf = e._wf("generate", {"prompt": "foto", "width": 896, "height": 1152, "seed": 3})
        assert not placeholders(wf)
        inject_loras(wf, [("x.safetensors", 0.8)])
        ks = next(v for v in wf.values() if v["class_type"] == "KSampler")
        assert ks["inputs"]["model"] == ["shl_lora_0", 0]              # la LoRA se enchufa al checkpoint
        assert any(v["inputs"].get("clip") == ["1", 1] for v in wf.values() if v["class_type"] == "CLIPTextEncode")
        done = calidad.enhance(wf, {"refine": True, "face": True}, {"FaceDetailer", "UltralyticsDetectorProvider"})
        assert done == ["detalle", "rostro"] and wf["shl_face"]["inputs"]["clip"] == ["1", 1]


def test_variant_for_flux_trains_its_own_lora_and_never_mixes(monkeypatch, tmp_path):
    init_db()
    _setup(monkeypatch, tmp_path)
    sid = _subject(9)
    v = lora.make_variant(sid, "flux")
    assert v["name"].endswith("· Flux") and lora.base_of(v["id"]) == "flux" and lora.base_of(sid) == "zimage"
    cfg = lora.config_dict("j", "/w", "t", 100, "pro", "flux")["config"]["process"][0]
    assert cfg["model"]["is_flux"] and cfg["train"]["noise_scheduler"] == "flowmatch"
    cfg = lora.config_dict("j", "/w", "t", 100, "estandar", "sdxl")["config"]["process"][0]
    assert cfg["model"]["is_xl"] and cfg["model"]["name_or_path"].endswith("RealVisXL_V5.0_fp16.safetensors")
    try:
        lora.make_variant(sid, "flux")
    except ValueError as e:
        assert "Ya existe" in str(e)
    else:
        raise AssertionError

    class Eng:
        def __init__(self, arch):
            self.arch, self.name = arch, "comfyui"
    with session() as s:
        z, f = s.get(Subject, sid), s.get(Subject, v["id"])
        for x in (z, f):
            x.lora_status, x.lora_name, x.lora_trigger = "ready", f"{x.id}.safetensors", "trig"
    with session() as s:
        z, f = s.get(Subject, sid), s.get(Subject, v["id"])
        assert lora.loras_for_engine([z], Eng("zimage")) and not lora.loras_for_engine([z], Eng("flux"))
        assert lora.loras_for_engine([f], Eng("flux")) and not lora.loras_for_engine([f], Eng("zimage"))


def test_qwen_professional_lora_trains_only_on_big_cards(monkeypatch):
    """La versión «Qwen» (Profesional) se entrena solo en tarjetas «pro» (RTX PRO 6000) y genera con Calidad máxima."""
    import asyncio
    from app import granja, lora
    big = lora.config_dict("v", "/tmp/x", "vlntn", 1000, profile="pro", base="qwen")["config"]["process"][0]
    assert big["model"]["arch"] == "qwen_image" and big["model"]["quantize"] is False
    assert big["train"]["cache_text_embeddings"] is True
    small = lora.config_dict("v", "/tmp/x", "vlntn", 1000, profile="estandar", base="qwen")["config"]["process"][0]
    assert small["model"]["quantize"] is True and "uint3" in small["model"]["qtype"]
    monkeypatch.setattr(granja, "local_slots", lambda: [{"id": "local:0", "slots": 1, "profile": "estandar"}])
    monkeypatch.setattr(granja, "remote_nodes", lambda: [{"id": "n1", "slots": 1, "profile": "pro", "label": "RTX PRO 6000"}])

    async def ok(n, fresh=False):
        return {"ok": True, "free": 1}
    monkeypatch.setattr(granja, "health", ok)
    assert asyncio.run(granja.free_slot({}))["id"] == "n1"                  # la tarjeta alquilada prendida va primero
    assert asyncio.run(granja.free_slot({"n1": 1}))["id"] == "local:0"      # llena: recién ahí la placa
    assert asyncio.run(granja.free_slot({}, big=True))["id"] == "n1"         # Qwen: solo la tarjeta grande
    assert asyncio.run(granja.free_slot({"n1": 1}, big=True)) is None        # si está ocupada, espera (no usa la 3090)


def test_one_big_card_trains_three_at_once_with_worker_share(monkeypatch, tmp_path):
    """WORKER_SHARE=3: la RTX PRO 6000 entrena 3 LoRA a la vez (3 modelos distintos) en la misma GPU."""
    import importlib.util
    monkeypatch.setenv("WORKER_GPUS", "0")
    monkeypatch.setenv("WORKER_SHARE", "3")
    monkeypatch.setenv("WORKER_DIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location("worker_share", Path(__file__).resolve().parent.parent / "scripts" / "shelton_worker.py")
    w = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(w)
    assert w.SLOTS == ["0/1", "0/2", "0/3"] and {w._device(s) for s in w.SLOTS} == {"0"}
    w._jobs.update({"a": {"status": "running", "slot": "0/1"}, "b": {"status": "running", "slot": "0/2"}})
    assert [s for s in w.SLOTS if s not in w._busy_slots()] == ["0/3"]          # queda 1 lugar para el tercero



def test_choose_the_rtx_to_train_and_to_generate(monkeypatch):
    """⚡ Entrenar en la RTX: la fila la manda solo ahí. Y en el chat se puede elegir generar en la RTX."""
    import asyncio
    from app import engines, granja
    node = {"id": "node:c1", "conn_id": "c1", "slots": 3, "profile": "estandar", "label": "RTX PRO 6000",
            "url": "http://100.1.1.1:8299", "token": "t", "comfy_url": "http://100.1.1.1:8288", "kind": "remote"}
    monkeypatch.setattr(granja, "local_slots", lambda: [{"id": "local:0", "slots": 1, "profile": "estandar"}])
    monkeypatch.setattr(granja, "remote_nodes", lambda: [node])

    async def ok(n, fresh=False):
        return {"ok": True, "free": 3}
    monkeypatch.setattr(engines, "reachable", lambda url, ttl=30.0: True)
    monkeypatch.setattr(granja, "health", ok)
    assert asyncio.run(granja.free_slot({}, only="node:c1"))["id"] == "node:c1"     # aunque la placa esté libre
    ids = [c["id"] for c in engines.engine_choices()]
    assert "rtx:c1:fast" in ids and "rtx:c1:quality" in ids
    eng = engines.get_engine("generate", "rtx:c1:quality")
    assert eng.url.startswith("http://100.1.1.1:8288") and hasattr(eng, "ensure_loras")
    engines.reset_cache()



def test_photos_go_to_the_rtx_when_the_placa_is_busy(monkeypatch):
    """Si la placa está entrenando (o dibujando) y la RTX está prendida, la foto va a la RTX: nunca queda trabada."""
    import time
    from app import engines, granja
    from app.config import settings
    node = {"id": "node:c9", "conn_id": "c9", "slots": 3, "profile": "estandar", "label": "RTX PRO 6000",
            "url": "http://100.9.9.9:8299", "token": "t", "comfy_url": "http://100.9.9.9:8288", "kind": "remote"}
    monkeypatch.setattr(granja, "remote_nodes", lambda: [node])
    monkeypatch.setitem(granja._health, "node:c9", (time.time(), {"ok": True}))
    monkeypatch.setattr(engines, "reachable", lambda url, ttl=30.0: True)
    monkeypatch.setattr(engines, "infra_mode", lambda: "local")
    engines.reset_cache()
    assert engines._rtx_when_placa_busy("generate", None) is None              # placa libre: sigue en la placa
    engines._busy[settings.COMFYUI_URL] = 1                                    # la placa está dibujando/entrenando
    try:
        e = engines._rtx_when_placa_busy("generate", None)
        assert e is not None and e.url.startswith("http://100.9.9.9:8288")
        assert engines._rtx_when_placa_busy("video", None) is None             # video no (otros motores)
        assert engines._rtx_when_placa_busy("generate", "sdxl") is None        # perfiles que la RTX no tiene: no
    finally:
        engines._busy[settings.COMFYUI_URL] = 0
        engines.reset_cache()
    assert settings.WORKERS >= 8


def test_rtx_off_falls_back_to_the_placa(monkeypatch):
    """Si la RTX está apagada, el Motor «⚡ RTX» no se ofrece y una foto pedida ahí se hace en la placa."""
    from app import engines, granja
    node = {"id": "node:c7", "conn_id": "c7", "slots": 3, "profile": "estandar", "label": "RTX PRO 6000",
            "url": "http://100.7.7.7:8299", "token": "t", "comfy_url": "http://100.7.7.7:8288", "kind": "remote"}
    monkeypatch.setattr(granja, "remote_nodes", lambda: [node])
    monkeypatch.setattr(engines, "reachable", lambda url, ttl=30.0: False)
    engines.reset_cache()
    assert not any(c["id"].startswith("rtx:") for c in engines.engine_choices())
    e = engines.get_engine("generate", "rtx:c7:fast")
    assert not getattr(e, "url", "").startswith("http://100.7.7.7")
    engines.reset_cache()



def test_power_the_rented_card_from_shelton(monkeypatch):
    """▶ prender / ⏸ pausar la RTX desde la Granja: Shelton le habla a RunPod con la clave guardada en la conexión."""
    import asyncio
    import json as _json
    import httpx
    from app import granja
    from app.db import Connection, init_db, session
    init_db()
    with session() as s:
        c = Connection(type="gpu_node", name="RTX", config={"url": "http://100.1.2.3:8299", "token": "t",
                       "runpod_api_key": "rk", "runpod_pod_id": "pod123"}, roles=[])
        s.add(c)
        s.flush()
        cid = c.id
    seen = []

    def handler(req):
        body = _json.loads(req.content)
        seen.append((req.url.host, req.headers.get("authorization"), body["query"]))
        if "podResume" in body["query"]:
            return httpx.Response(200, json={"data": {"podResume": {"id": "pod123", "desiredStatus": "RUNNING", "costPerHr": 2.69}}})
        if "podStop" in body["query"]:
            return httpx.Response(200, json={"data": {"podStop": {"id": "pod123", "desiredStatus": "EXITED"}}})
        return httpx.Response(200, json={"data": {"pod": {"id": "pod123", "name": "rtx", "desiredStatus": "EXITED", "costPerHr": 0}}})
    monkeypatch.setattr(granja, "_transport", httpx.MockTransport(handler))
    assert asyncio.run(granja.rtx_power(cid, "prender"))["status"] == "RUNNING"
    assert asyncio.run(granja.rtx_power(cid, "pausar"))["status"] == "EXITED"
    assert asyncio.run(granja.rtx_status(cid))["status"] == "EXITED"
    assert all(h == "api.runpod.io" and a == "Bearer rk" and "pod123" in q for h, a, q in seen)
    assert any(n["runpod"] for n in granja.remote_nodes() if n["conn_id"] == cid)
    with session() as s:
        s.delete(s.get(Connection, cid))



def test_photo_settings_override_the_workflow(monkeypatch, tmp_path):
    """⚙️ Ajustes de foto: pasos, semilla y fuerza de la LoRA fijados a mano llegan al workflow; vacío = automático."""
    from app import calidad
    from app.config import settings as st
    from app.engines.comfy import ComfyEngine
    monkeypatch.setattr(st, "DATA_DIR", tmp_path)
    eng = ComfyEngine({"url": "http://127.0.0.1:1", "preset": "fast"})
    wf_auto = eng._wf("generate", {"prompt": "x", "width": 512, "height": 512, "seed": 7})
    steps_auto = [n["inputs"]["steps"] for n in wf_auto.values() if isinstance(n, dict) and "steps" in n.get("inputs", {})]
    calidad.save(steps=17, seed=1234, lora_strength=0.6)
    assert calidad.overrides() == {"steps": 17, "seed": 1234, "lora_strength": 0.6}
    wf = eng._wf("generate", {"prompt": "x", "width": 512, "height": 512, "seed": 7})
    ks = [n["inputs"] for n in wf.values() if isinstance(n, dict) and "steps" in n.get("inputs", {})]
    assert ks and all(k["steps"] == 17 and k["seed"] == 1234 for k in ks) and steps_auto != [17]
    calidad.save(steps=0, seed=0, lora_strength=0)
    assert calidad.overrides() == {}
