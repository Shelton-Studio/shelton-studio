"""Subagentes que se encienden y apagan a gusto: Shelton sigue trabajando igual (cada paso se hace simple sin ellos)."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ["IMAGE_ENGINE"] = "mock"
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="shelton_sub_"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_chain  # noqa: E402,F401
from test_lora import _png  # noqa: E402

from app import consumo, pipeline  # noqa: E402
from app.agents import registry  # noqa: E402
from app.agents import specialists as sp  # noqa: E402
from app.db import Post, init_db, session  # noqa: E402


def _off(*keys):
    for k in keys:
        registry.save(k, enabled=False)


def _on(*keys):
    for k in keys:
        registry.save(k, enabled=True)


def test_shelton_cannot_be_turned_off():
    init_db()
    try:
        registry.save("shelton", enabled=False)
    except ValueError as e:
        assert "jefe" in str(e)
    else:
        raise AssertionError("Shelton no se debe poder apagar")
    assert registry.is_on("shelton")


def test_post_still_made_with_judge_copywriter_and_art_director_off(monkeypatch):
    """Con el Juez, el Copywriter y el Director creativo apagados, la foto se hace igual y llega a Aprobaciones."""
    init_db()
    _off("judge", "copywriter", "art_director")
    called = []

    async def must_not_call(*a, **k):
        called.append(k.get("agent"))
        raise AssertionError("no debía llamar a un agente apagado")
    monkeypatch.setattr(sp, "json_call", must_not_call)
    prompts = []

    class Eng:
        name = "comfyui"

        async def generate(self, prompt, w, h, seed, step=None):
            prompts.append(prompt)
            return _png("blue")
    monkeypatch.setattr(pipeline, "get_engine", lambda role="generate", choice=None: Eng())
    with session() as s:
        p = Post(idea="Un café con medialunas al sol", platform="instagram_feed", subject_ids=[], status="queued")
        s.add(p)
        s.flush()
        pid = p.id
    try:
        asyncio.run(pipeline._create_post(pid, {}))
    finally:
        _on("judge", "copywriter", "art_director")
    assert not called and prompts and prompts[0].startswith("Un café con medialunas al sol")
    with session() as s:
        post = s.get(Post, pid)
        assert post.status == "ready" and post.caption == "Un café con medialunas al sol"
        assert "Juez apagado" in post.qa_notes


def test_other_agents_off_have_simple_fallbacks():
    init_db()
    _off("trend_scout", "sound_designer", "voice_director", "video_producer", "edit_director", "planner")
    try:
        assert asyncio.run(sp.scout("trend", None, [], [])) == []                       # no sale a buscar ni gasta
        assert asyncio.run(sp.design_sound("moto pasando"))["prompt"] == "moto pasando"
        assert asyncio.run(sp.direct_voice("idea", "", 5, [], script="Hola"))["script"] == "Hola"
        assert asyncio.run(sp.direct_video("x.png", "idea", "gira la cámara"))["motion_prompt"] == "gira la cámara"
        assert asyncio.run(sp.edit_instruction("ponle fondo azul", "p"))["instruction"] == "ponle fondo azul"
        try:
            asyncio.run(sp.plan_ideas("brief", 3, ["instagram_feed"], None))
        except ValueError as e:
            assert "Planificador" in str(e)
        else:
            raise AssertionError("el planificador apagado debe avisar")
        assert "Scout de tendencias" in registry.paused()
    finally:
        _on("trend_scout", "sound_designer", "voice_director", "video_producer", "edit_director", "planner")
    assert registry.paused() == []


def test_turn_on_all_savers(monkeypatch):
    init_db()
    monkeypatch.setattr("app.llm.provider", lambda: "openrouter")
    monkeypatch.delenv("NETWORK_REVIEW_HOURS", raising=False)
    consumo.save_settings(prompt_cache=False, network_hours=2, night_pause=False)
    done = consumo.aplicar("all_on")["done"]
    cfg = consumo.get_settings()
    assert cfg["prompt_cache"] and cfg["night_pause"] and consumo.network_hours() == 6
    assert all(registry.model(k) == consumo.CHEAP_MODEL for k in consumo.SIMPLE_AGENTS)
    assert len(done) == 3
    for k in consumo.SIMPLE_AGENTS:                       # se puede deshacer uno por uno
        consumo.aplicar(f"model_restore:{k}")
    assert registry.model("copywriter") != consumo.CHEAP_MODEL


def test_campaign_with_planner_off_does_not_lie_and_turns_it_on_and_off(monkeypatch):
    """Planificador apagado: Shelton NO dice que las fotos están en la galería; con permiso lo enciende,
    planifica, y lo vuelve a apagar apenas termina."""
    import json
    from app.agents import shelton
    init_db()
    _off("planner")
    try:
        async def fake_plan(brief, count, platforms, brand, already=None, subjects=None):
            assert registry.is_on("planner")                    # encendido mientras planifica
            return [{"idea": f"idea {len(already or []) + i}", "platform": platforms[0]} for i in range(count)]
        monkeypatch.setattr(sp, "plan_ideas", fake_plan)
        monkeypatch.setattr(pipeline, "new_post", lambda idea, plat, **k: _fake_post(k["campaign_id"]))
        ctx = {"conv_id": None, "engine": None}

        async def emit(ev):
            pass

        async def go(args):
            out = await shelton._run_tool("create_campaign", args, ctx, emit)
            for _ in range(50):
                await asyncio.sleep(0.02)
            return json.loads(out)
        r = asyncio.run(go({"brief": "semana", "count": 3}))
        assert r["planner_off"] and not r["launched"] and not registry.is_on("planner")
        r = asyncio.run(go({"brief": "semana", "count": 3, "turn_on_planner": True}))
        assert r["ok"] and r["planner_turned_on_temporarily"]
        assert not registry.is_on("planner")                    # se apagó solo al terminar
    finally:
        _on("planner")
        with session() as s:
            for p in s.query(Post).filter(Post.idea == "x", Post.campaign_id.isnot(None)):
                s.delete(p)


def _fake_post(campaign_id):
    with session() as s:
        p = Post(idea="x", platform="instagram_feed", campaign_id=campaign_id, status="queued")
        s.add(p)
        s.flush()
        return p.id


def test_banner_when_a_needed_agent_is_off():
    """Si un resultado dice que un subagente está apagado, el chat muestra el banner para encenderlo."""
    from app.agents import shelton
    _off("planner")
    try:
        assert shelton.agent_off_in("NO se lanzó la campaña: el Planificador está apagado.") == "planner"
        assert shelton.agent_off_in("Todo bien, 3 posts listos") is None
        evs = []

        async def emit(ev):
            evs.append(ev)
        asyncio.run(shelton._need_agent("El Planificador está apagado: enciéndelo en Agentes", emit))
        assert evs and evs[0]["type"] == "need_agent" and evs[0]["key"] == "planner"
    finally:
        _on("planner")
    assert shelton.agent_off_in("El Planificador está apagado") is None      # ya encendido: no molesta


def test_big_request_is_not_split_in_batches_when_planner_is_off(monkeypatch):
    """«Hazme 30 fotos» con el Planificador apagado: no las parte en tandas de 6; se frena y pregunta."""
    from app.agents import shelton
    assert shelton.asked_count("hazme 30 fotos de Valentina en la cama") == 30
    assert shelton.asked_count("una foto de Valentina") == 0
    _off("planner")
    try:
        made = []
        monkeypatch.setattr(pipeline, "new_post", lambda *a, **k: made.append(1) or "x")
        posts = [{"idea": f"escena {i}"} for i in range(6)]

        async def emit(ev):
            pass
        out = asyncio.run(shelton._run_tool("create_posts", {"posts": posts}, {"conv_id": None, "engine": None,
                                                                             "asked": 30}, emit))
        assert "NO se crearon" in out and shelton.agent_off_in(out) == "planner" and not made
        ctx = {"conv_id": None, "engine": None, "asked": 0, "turn_posts": 6}       # segunda tanda en el mismo turno
        out = asyncio.run(shelton._run_tool("create_posts", {"posts": posts[:2]}, ctx, emit))
        assert "NO se crearon" in out and not made
    finally:
        _on("planner")


def test_banner_for_every_needed_agent_that_is_off_and_silence_when_on():
    """Cualquier subagente que la tarea usa y está apagado → banner. Si están encendidos, no dice nada."""
    from app.agents import shelton
    evs = []

    async def emit(ev):
        evs.append(ev)
    asyncio.run(shelton._warn_off_agents("create_posts", emit))
    assert evs == []                                               # todos encendidos: silencio
    _off("judge", "copywriter")
    try:
        asyncio.run(shelton._warn_off_agents("create_posts", emit))
        assert {e["key"] for e in evs} == {"judge", "copywriter"} and all(e["optional"] for e in evs)
    finally:
        _on("judge", "copywriter")


def test_swap_face_copies_the_photo_and_only_changes_the_face(monkeypatch):
    """🔁 Foto de referencia → la misma foto con la cara de la modelo entrenada. Nunca con menores ni desnudos."""
    import io
    from PIL import Image
    from app import llm, privado
    from app.db import Subject
    from app.media import save_bytes
    init_db()
    b = io.BytesIO()
    Image.new("RGB", (64, 80), "blue").save(b, "PNG")
    ref = save_bytes(b.getvalue(), "ref_swap.png")
    with session() as s:
        sub = Subject(name="Cara Swap", kind="person", lora_status="ready", lora_name="cara.safetensors",
                      lora_trigger="cara_tk")
        parent = Post(idea="foto de tiktok", platform="original", image_path=str(ref), raw_path=str(ref), status="ready")
        s.add_all([sub, parent])
        s.flush()
        sid, pid = sub.id, parent.id
    seen = {}

    class FakeEngine:
        async def face_pass(self, image, loras, prompt, seed, denoise=0.45):
            seen.update(loras=loras, prompt=prompt, denoise=denoise)
            out = io.BytesIO()
            Image.new("RGB", (64, 80), "red").save(out, "PNG")
            return out.getvalue()
    monkeypatch.setattr(privado, "_engine", lambda p, c: ({}, FakeEngine()))
    verdict = {"people": True, "minor": False, "nudity": False}

    async def fake_check(system, content, schema, **k):
        return dict(verdict)
    monkeypatch.setattr(llm, "json_call", fake_check)

    def run():
        new = pipeline.new_swap(pid, sid)
        asyncio.run(pipeline._swap_face(new, {"subject_id": sid}))
        with session() as s:
            return s.get(Post, new)
    p = run()
    assert p.status == "ready" and seen["loras"][0][0] == "cara.safetensors" and "cara_tk" in seen["prompt"]
    assert (p.width, p.height) == (64, 80)                     # mismo tamaño que la original
    verdict["minor"] = True
    try:
        run()
    except ValueError as e:
        assert "no se puede usar" in str(e)
    else:
        raise AssertionError("con un posible menor no debía hacerlo")


def test_putting_a_bank_models_face_or_body_always_uses_her_lora(monkeypatch):
    """«Ponle la cara y el cuerpo de Valentina» nunca va a la edición normal (que inventa caras): va con su LoRA.
    Con cuerpo, primero se redibuja toda la foto con su LoRA y después se le pone su cara."""
    import io
    from PIL import Image
    from app import llm, privado
    from app.agents import shelton
    from app.comfyui.client import load_workflow
    from app.db import Subject
    from app.engines.comfy import img2img_workflow
    from app.media import save_bytes
    init_db()
    wf = load_workflow("z_image_turbo.json")
    assert img2img_workflow(wf, "ref.png", 0.55)
    ks = next(v for v in wf.values() if isinstance(v, dict) and v.get("class_type") == "KSampler")
    assert ks["inputs"]["latent_image"] == ["shl_enc", 0] and ks["inputs"]["denoise"] == 0.55
    assert not any(isinstance(v, dict) and v.get("class_type") == "EmptySD3LatentImage" for v in wf.values())
    assert wf["shl_scale"]["class_type"] == "ImageScale"           # nodo que existe en todas las versiones
    from app.engines.comfy import _i2i_size
    b0 = io.BytesIO()
    Image.new("RGB", (638, 643)).save(b0, "PNG")
    w, h = _i2i_size(b0.getvalue())
    assert w % 16 == 0 and h % 16 == 0 and 900 * 900 < w * h <= 1024 * 1024
    with session() as s:
        sub = Subject(name="Valeria Lora", kind="person", lora_status="ready", lora_name="val.safetensors",
                      lora_trigger="val_tk")
        s.add(sub)
        s.flush()
        sid = sub.id
    hit = shelton._edit_is_a_swap("quiero que le pongas la cara y el cuerpo de Valeria Lora")
    assert hit == (sid, "Valeria Lora", True)
    assert shelton._edit_is_a_swap("ponle la cara de Valeria Lora")[2] is False
    assert shelton._edit_is_a_swap("cambia el fondo a una playa") is None
    b = io.BytesIO()
    Image.new("RGB", (64, 80), "blue").save(b, "PNG")
    ref = save_bytes(b.getvalue(), "ref_body.png")
    with session() as s:
        parent = Post(idea="selfie", platform="original", image_path=str(ref), raw_path=str(ref), status="ready")
        s.add(parent)
        s.flush()
        pid = parent.id
    calls = []

    class FakeEngine:
        async def img2img(self, image, loras, prompt, seed, denoise=0.5):
            calls.append(("cuerpo", loras[0][0], denoise))
            return image

        async def face_pass(self, image, loras, prompt, seed, denoise=0.45):
            calls.append(("cara", loras[0][0], denoise))
            return image
    monkeypatch.setattr(privado, "_engine", lambda p, c: ({}, FakeEngine()))

    async def ok_check(system, content, schema, **k):
        return {"people": True, "minor": False, "nudity": False}
    monkeypatch.setattr(llm, "json_call", ok_check)
    new = pipeline.new_swap(pid, sid, body=True)
    asyncio.run(pipeline._swap_face(new, {"subject_id": sid, "body": True}))
    assert [c[0] for c in calls] == ["cuerpo", "cara"] and all(c[1] == "val.safetensors" for c in calls)


def test_swap_turns_on_body_when_the_user_asked_for_her_body(monkeypatch):
    """Si en el mensaje dijo «cuerpo», el modo cuerpo se activa aunque Shelton no lo haya marcado."""
    from app.agents import shelton
    from app.db import Subject
    init_db()
    with session() as s:
        sub = Subject(name="Cuerpo Lora", kind="person", lora_status="ready", lora_name="c.safetensors")
        parent = Post(idea="x", platform="original", image_path="/tmp/x.png", status="ready")
        s.add_all([sub, parent])
        s.flush()
        pid = parent.id
    got = {}
    monkeypatch.setattr(pipeline, "new_swap", lambda p, sid, conv, body=False: got.update(body=body) or "np")

    async def fake_wait(ids, emit, text):
        return "OK"
    monkeypatch.setattr(shelton, "_wait_and_report", fake_wait)

    async def emit(ev):
        pass
    ctx = {"conv_id": None, "engine": None, "user_text": "ponele la cara y el cuerpo de Cuerpo Lora"}
    asyncio.run(shelton._run_tool("swap_face", {"post_id": pid, "subject": "Cuerpo Lora"}, ctx, emit))
    assert got["body"] is True
