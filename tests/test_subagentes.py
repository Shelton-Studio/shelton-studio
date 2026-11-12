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
