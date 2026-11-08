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



async def fake_json_call(system, content, schema, effort="low", max_tokens=8000, **kw):
    keys = set(schema["properties"])
    if "script" in keys and "spec" in keys:
        return {"script": "Hola, esto es una prueba de voz.", "spec": {"pitch_semitones": -3, "speed": 0.95,
                "exaggeration": 0.6, "cfg_weight": 0.5, "warmth": True, "breathy": False, "tremolo": 0.0,
                "robot": "robot" in str(content).lower(), "reverb": 0.2, "base_voice": ""}}
    if keys == {"prompt", "negative"}:
        return {"prompt": "aggressive V8 engine revving", "negative": "music"}
    if keys == {"summary", "platforms", "models", "sources"}:   # mejor hora para publicar
        names = [x.split(" (")[0] for x in str(content).split("Models: ")[-1].split(", ") if x and x != "none"]
        return {"summary": "Tarde-noche entre semana.", "sources": ["https://example.com/horarios"],
                "platforms": [{"platform": "tiktok", "best": "mar-jue 19:00-21:00", "why": "más actividad"}],
                "models": [{"model": n, "platform": "tiktok", "best": "jue 20:00-22:00", "why": "se aprueba más"} for n in names[:5]]}
    if "updates" in keys:   # Shelton pasa la lección al equipo
        hit = "LOGO" in str(content)
        return {"updates": [{"agent": "judge", "title": "Revisar logos", "content": "Reprueba si se ve el logo."}] if hit else []}
    if "scenes" in keys and "hook" in keys:   # analista de videos de referencia
        n = max(1, str(content).count("Frame "))
        return {"summary": "Chica bailando en una terraza al atardecer", "hook": "Giro en el primer segundo",
                "style": "cálido, cámara en mano", "audio": "canción en tendencia",
                "scenes": [{"what": f"woman dancing on a rooftop at sunset, shot {i + 1}", "motion": "spins, handheld"}
                           for i in range(n)]}
    if keys == {"text", "clear", "issue"}:   # transcripción de voz
        return {"text": "hazme tres fotos de Valentina en la playa", "clear": True, "issue": ""}
    if "contradicts" in keys:
        hit = "CONTRADICE" in str(content).split("NEW:")[-1]
        old = str(content).split("[")[1].split("]")[0] if hit and "[" in str(content) else ""
        return {"contradicts": hit, "conflicting_id": old, "explanation": "Una dice 3 hashtags y otra 10."}
    if keys == {"issues", "summary"}:
        return {"summary": "Hay 1 problema.", "issues": [{"agents": ["copywriter", "planner"], "severity": "media",
                "problem": "Hashtags", "explanation": "x", "suggestion": "y"}]}
    if "lessons" in keys:
        return {"lessons": [{"agent": "copywriter", "title": "Captions más cortos", "content": "Máximo 2 líneas."}]}
    if "motion_prompt" in keys:
        return {"motion_prompt": "slow push-in, hair moving in the wind", "negative": "blur"}
    if "findings" in keys:
        return {"findings": [{"title": "Trend: retratos con flash", "summary": "s", "url": "https://example.com/t",
                              "why": "encaja", "how": "replicar", "ideas": ["Idea trend 1", "Idea trend 2"],
                              "platforms": ["tiktok"]}]}
    if "prompt" in keys:
        fixed = "FIXED " if "Problems found in previous attempts" in content else ""
        return {"prompt": fixed + "A photorealistic scene, golden hour, 35mm", "concept": "escena"}
    if "score" in keys:
        # El juez reprueba siempre el primer intento (para probar reintentos y alternativas guardadas).
        retried = any(k in str(content) for k in ("FIXED", "Avoid:", "Edit requested", "frames", "push-in"))
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
    if keys == {"description", "kind", "best_photo"}:
        n = str(content).count("Photo ")
        return {"description": f"red sports coupe seen in {n} photos", "kind": "car", "best_photo": 2 if n > 1 else 1}
    if keys == {"description"}:
        return {"description": "red sports car"}
    raise AssertionError(keys)


_real_json_call = specialists.json_call


async def _logged_fake(system, content, schema, effort="low", max_tokens=8000, **kw):
    """Igual que el falso, pero registra la ejecución como si fuera real (para métricas y actividad)."""
    import time
    from app import llm
    out = await fake_json_call(system, content, schema, effort, max_tokens, **kw)
    usage = SimpleNamespace(input_tokens=1200, output_tokens=300, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    llm._log_run(kw.get("agent", "shelton"), True, time.monotonic() - 1.5, "claude-opus-5-5",
                 SimpleNamespace(usage=usage), summary=kw.get("summary", ""))
    return out


specialists.json_call = _logged_fake


async def fake_create(**kw):
    """Simula la llamada de los scouts con búsqueda web."""
    return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="Informe con tendencias")])


def block(**kw):
    ns = SimpleNamespace(**kw)
    ns.model_dump = lambda **_: kw
    return ns


class FakeChief:
    """Simula a Shelton. Por defecto: crea a Valentina y 2 posts con ella.
    Si el mensaje del usuario empieza con 'TOOL nombre {json}', llama exactamente esa herramienta y termina
    (sirve para probar cada herramienta desde el chat)."""
    def __init__(self):
        self.calls = 0
        self.scripted = False

    async def __call__(self, **kw):
        import json as _json
        last = kw["messages"][-1]
        content = last["content"] if isinstance(last["content"], list) else []
        if self.scripted and content and content[0].get("type") == "tool_result":
            self.scripted = False
            res = content[0]
            body = res["content"] if isinstance(res["content"], str) else res["content"][0].get("text", "")
            text = ("ERROR: " if res.get("is_error") else "OK: ") + body[:2000]
            if res.get("tool_use_id", "").startswith("d"):
                text = ("Listo (modo demo): el equipo hizo 2 posts con tu idea. Ábrelos para ver la nota del juez, "
                        "editar el texto, animarlos o ponerles voz. Con tu GPU y tu clave de Claude salen reales.")
            return SimpleNamespace(stop_reason="end_turn", content=[block(type="text", text=text)])
        txt = next((b["text"] for b in reversed(content) if b.get("type") == "text"), "")
        if txt.startswith("TOOL "):
            self.scripted = True
            name, _, args = txt[5:].partition(" ")
            return SimpleNamespace(stop_reason="tool_use", content=[block(type="tool_use", id=f"s{id(txt)}",
                                   name=name, input=_json.loads(args or "{}"))])
        if txt and not txt.startswith("crea a Valentina"):
            tools = {t.get("name") for t in kw.get("tools", [])}
            if "create_posts" not in tools:  # chat directo con un subagente
                return SimpleNamespace(stop_reason="end_turn", content=[block(type="text",
                                       text="(Modo demo) Con tu clave de Claude te respondería de verdad.")])
            # Modo demo: cualquier pedido arranca la cadena con 2 posts sobre lo que escribiste.
            self.scripted = True
            platform = "tiktok" if "tiktok" in txt.lower() else "instagram_feed"
            return SimpleNamespace(stop_reason="tool_use", content=[
                block(type="thinking", thinking="Modo demo: encargo 2 posts al equipo", signature="x"),
                block(type="tool_use", id=f"d{id(txt)}", name="create_posts", input={"posts": [
                    {"idea": txt[:300], "platform": platform}, {"idea": txt[:300] + " (variante)", "platform": platform}]})])
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
