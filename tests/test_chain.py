"""Prueba de la cadena completa sin GPU ni API: motor 'mock' + Claude simulado.

Ejecutar:  python -m pytest -q
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ["IMAGE_ENGINE"] = "mock"
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="shelton_test_")
os.environ.pop("DATABASE_URL", None)
os.environ["QA_MAX_RETRIES"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import pipeline  # noqa: E402
from app.agents import shelton, specialists  # noqa: E402
from app.db import Post, Subject, init_db, session  # noqa: E402



async def fake_json_call(system, content, schema, effort="low", max_tokens=8000):
    keys = set(schema["properties"])
    if "prompt" in keys:
        fixed = "FIXED " if "Problems found in previous attempts" in content else ""
        return {"prompt": fixed + "A photorealistic scene, golden hour, 35mm", "concept": "escena"}
    if "score" in keys:
        # El juez reprueba siempre el primer intento (para probar reintentos y alternativas guardadas).
        retried = "FIXED" in str(content) or "Avoid:" in str(content) or "Edit requested" in str(content)
        return {"score": 90 if retried else 55, "issues": ["mano deformada"], "fix_hint": "manos fuera de plano", "summary": "ok"}
    if "caption" in keys:
        return {"caption": "Texto de prueba", "hashtags": ["prueba", "#shelton"], "alt_text": "alt"}
    if "ideas" in keys:
        n = int(content.split("Number of ideas: ")[1].split("\n")[0])
        item = schema["properties"]["ideas"]["items"]["properties"]
        names = item.get("subjects", {}).get("items", {}).get("enum", [])
        return {"ideas": [{"idea": f"Idea {i}", "platform": "tiktok", "pillar": "p",
                           **({"subjects": names[:1]} if names else {})} for i in range(n)]}
    if "instruction" in keys:
        return {"instruction": "Make the background a beach", "image_change": True}
    if "reference_prompt" in keys:
        return {"description": "young woman, curly hair", "reference_prompt": "portrait", "notes": "influencer"}
    if keys == {"description"}:
        return {"description": "red sports car"}
    raise AssertionError(keys)


specialists.json_call = fake_json_call


def block(**kw):
    ns = SimpleNamespace(**kw)
    ns.model_dump = lambda **_: kw
    return ns


class FakeChief:
    """Simula a Shelton: pide crear la modelo Valentina, luego 2 posts con ella, luego responde."""
    def __init__(self):
        self.calls = 0

    async def __call__(self, **kw):
        self.calls += 1
        if self.calls == 1:
            return SimpleNamespace(stop_reason="tool_use", content=[
                block(type="thinking", thinking="Primero creo a Valentina", signature="x"),
                block(type="tool_use", id="t1", name="create_subject",
                      input={"name": "Valentina", "kind": "person", "request": "25 años, rizos"})])
        if self.calls == 2:
            return SimpleNamespace(stop_reason="tool_use", content=[block(
                type="tool_use", id="t2", name="create_posts", input={"posts": [
                    {"idea": "Valentina en la playa", "platform": "instagram_feed", "subjects": ["valentina"]},
                    {"idea": "Valentina tomando café", "platform": "tiktok", "subjects": ["Valentina"]}]})])
        last = kw["messages"][-1]["content"]
        assert last[0]["type"] == "tool_result" and not last[0].get("is_error"), last
        return SimpleNamespace(stop_reason="end_turn", content=[block(type="text", text="Listo: 2 posts de Valentina.")])


def test_full_chain():
    async def main():
        init_db()
        await pipeline.start_workers()
        shelton.create = FakeChief()
        evs = [ev async for ev in shelton.chat(None, "crea a Valentina y haz 2 posts con ella")]
        types = [e["type"] for e in evs]
        assert "error" not in types, evs
        msg = next(e for e in evs if e["type"] == "message")
        assert len(msg["post_ids"]) == 2

        with session() as s:
            val = s.query(Subject).one()
            assert val.references and Path(val.references[0]).exists()
            posts = [s.get(Post, i) for i in msg["post_ids"]]
            for p in posts:
                assert p.status == "ready", (p.status, p.error)
                assert p.subject_ids == [val.id]
                assert Path(p.image_path).exists() and p.caption and "#prueba" in p.hashtags
            assert posts[1].width == 1080 and posts[1].height == 1920   # TikTok 9:16

        # El juez nunca borra: los intentos reprobados quedan como alternativas y decide una persona.
        with session() as s:
            all_posts = s.query(Post).all()
            assert all(p.status == "ready" for p in all_posts)          # nada se aprueba solo
            val = s.query(Subject).one()
            assert val.alternatives and Path(val.alternatives[0]).exists()  # intento 55 de la referencia guardado
        with_alts = [p for p in all_posts if p.alternatives]
        for p in with_alts:
            assert all(Path(a["final"]).exists() for a in p.alternatives)
        assert with_alts
        if with_alts:
            p = with_alts[0]
            old_main = p.image_path
            data = pipeline.use_alternative(p.id, 0)
            assert data["image_url"].endswith(Path(p.alternatives[0]["final"]).name)
            with session() as s:
                assert s.get(Post, p.id).alternatives[0]["final"] == old_main  # el anterior tampoco se pierde

        # Edición -> versión nueva enlazada a la original
        eid = pipeline.new_edit(posts[0].id, "ponle fondo de playa")
        await pipeline.wait_for([eid], timeout=30)
        with session() as s:
            e = s.get(Post, eid)
            assert e.status == "ready" and e.parent_id == posts[0].id, e.error

        # Campaña de 30 (planificada en tandas) con la modelo
        cid = pipeline.new_campaign("lanzamiento", 30, ["tiktok"], [val.id])
        for _ in range(1500):
            await asyncio.sleep(0.1)
            with session() as s:
                done = s.query(Post).filter(Post.campaign_id == cid, Post.status == "ready").count()
            if done == 30:
                break
        assert done == 30
        await pipeline.stop_workers()
    asyncio.run(main())
