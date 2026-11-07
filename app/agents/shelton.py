"""Shelton: el jefe supremo. Conversa con el usuario y dirige a todos los subagentes mediante herramientas."""
import json
import logging
from typing import AsyncIterator

from app import pipeline, publishers
from app.config import settings
from app.db import Brand, Campaign, Conversation, Post, Subject, get_or_create_brand, session
from app.llm import create, text_of
from app.media import image_block
from app.platforms import PLATFORMS
from app.serialize import post_dict, subject_dict

log = logging.getLogger("shelton.chief")

PLATFORM_KEYS = list(PLATFORMS)
UPDATES_BETA = "thinking-display-updates-2026-08-18"

SYSTEM = f"""Eres Shelton, el director general de un estudio de contenido para redes sociales (Instagram, TikTok,
Telegram, Facebook, X, Pinterest, YouTube). Eres el jefe de un equipo de subagentes que trabajan para ti:
- Director creativo: convierte una idea en el prompt perfecto para el modelo de imagen.
- Motor de imagen: Z-Image Turbo (fotorrealista y rápido), Qwen-Image-Edit (ediciones y escenas con modelos del banco).
- Juez de calidad: mira cada imagen, la puntúa y obliga a regenerarla si tiene defectos.
- Redactor: escribe caption, hashtags y texto alternativo para cada red.
- Planificador: diseña lotes grandes de ideas distintas (campañas de 10, 50 o más posts).
- Diseñador del banco de modelos: crea personajes, autos, lugares o productos recurrentes con su foto de referencia.
Tú decides qué hacer y a quién encargarlo usando tus herramientas. El usuario solo te dice lo que quiere.

Cómo trabajas:
- Habla en el idioma del usuario, breve y claro, como un director creativo con criterio. Nada de relleno.
- Si el pedido es claro, actúa enseguida; no pidas confirmación para cosas pequeñas. Si falta algo esencial
  (por ejemplo, de qué trata la marca para una campaña grande), pregunta una sola vez y propone opciones.
- Hasta ~6 posts: usa create_posts. Más que eso o "N diarios": usa create_campaign (se procesan en cola).
- Cada idea que envíes debe ser una escena concreta y visual, no un tema abstracto.
- Si el usuario nombra a un personaje, auto, lugar o producto, búscalo en el banco de modelos (list_subjects) y pásalo
  en `subjects`. Si no existe y lo quiere recurrente, créalo con create_subject.
- Para cambiar una imagen existente usa edit_post (crea una versión nueva; la original se conserva).
- Elige la plataforma según lo pedido: {', '.join(PLATFORM_KEYS)}. Si no dicen, usa las de la marca.
- Después de generar, resume en 1-3 líneas lo que salió (nota de calidad, qué mejorarías). No repitas el caption completo:
  el usuario lo ve en la galería.
- Nunca inventes resultados: solo informa lo que devuelven las herramientas.
- La decisión final es siempre de una persona. Tú y el juez solo recomiendan. No apruebes, rechaces ni publiques
  nada salvo que el usuario te lo pida explícitamente en este chat. Nada se borra: los intentos que el juez reprobó
  quedan guardados como alternativas para que el usuario los vea y elija si quiere.
- No crees imágenes de personas reales identificables ni contenido sexual, de odio o engañoso."""


def _tool(name, description, properties, required):
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": properties, "required": required}}


_IDEA = {"type": "object", "properties": {
    "idea": {"type": "string", "description": "Escena concreta y visual del post"},
    "platform": {"type": "string", "enum": PLATFORM_KEYS},
    "subjects": {"type": "array", "items": {"type": "string"}, "description": "Nombres del banco de modelos que aparecen"},
    "caption_notes": {"type": "string", "description": "Indicaciones para el texto (oferta, CTA, idioma...)"},
}, "required": ["idea", "platform"]}

TOOLS = [
    _tool("create_posts", "Crea de 1 a 6 posts (imagen + texto) y espera el resultado con la nota del juez.",
          {"posts": {"type": "array", "items": _IDEA, "minItems": 1, "maxItems": 6}}, ["posts"]),
    _tool("create_campaign", "Planifica y encola un lote grande de posts distintos (7 a 200). Devuelve al instante; "
          "los posts aparecen en la galería a medida que se terminan.",
          {"brief": {"type": "string", "description": "Objetivo, temas, tono, fechas, ofertas..."},
           "count": {"type": "integer", "minimum": 1, "maximum": 200},
           "platforms": {"type": "array", "items": {"type": "string", "enum": PLATFORM_KEYS}},
           "subjects": {"type": "array", "items": {"type": "string"}}}, ["brief", "count"]),
    _tool("edit_post", "Edita un post existente: cambiar algo de la imagen (fondo, ropa, luz, color, añadir/quitar "
          "objetos, texto en la imagen) o solo reescribir su texto. Crea una versión nueva y espera el resultado.",
          {"post_id": {"type": "string"}, "instruction": {"type": "string"},
           "rewrite_caption": {"type": "boolean", "description": "Reescribir también el caption"}}, ["post_id", "instruction"]),
    _tool("list_posts", "Lista los posts más recientes (opcionalmente filtrados por estado).",
          {"status": {"type": "string", "enum": ["queued", "generating", "ready", "approved", "published", "failed", "rejected"]},
           "limit": {"type": "integer", "minimum": 1, "maximum": 30}}, []),
    _tool("view_post", "Mira la imagen y los datos de un post para opinar o decidir una edición.",
          {"post_id": {"type": "string"}}, ["post_id"]),
    _tool("set_status", "Aprueba o rechaza posts. Úsala SOLO si el usuario lo pide explícitamente.",
          {"post_ids": {"type": "array", "items": {"type": "string"}},
           "status": {"type": "string", "enum": ["approved", "rejected", "ready"]}}, ["post_ids", "status"]),
    _tool("publish_post", "Publica un post en una red conectada (por ahora: telegram). SOLO si el usuario lo pide explícitamente.",
          {"post_id": {"type": "string"}, "target": {"type": "string", "enum": ["telegram"]}}, ["post_id", "target"]),
    _tool("create_subject", "Crea un modelo en el banco (personaje, auto, lugar, producto, mascota) con nombre propio. "
          "Genera su foto de referencia y su ficha visual para usarlo en posts futuros. Tarda ~1 minuto.",
          {"name": {"type": "string"}, "kind": {"type": "string", "enum": ["person", "car", "place", "product", "pet", "other"]},
           "request": {"type": "string", "description": "Cómo es: rasgos, estilo, edad, color, detalles, personalidad"}},
          ["name", "kind", "request"]),
    _tool("list_subjects", "Lista el banco de modelos (nombre, tipo, descripción).", {}, []),
    _tool("add_subject_reference", "Usa la imagen de un post (o una foto subida por el usuario) como referencia de un "
          "modelo del banco. Si el modelo no existe, lo crea a partir de esa imagen.",
          {"subject_name": {"type": "string"}, "post_id": {"type": "string"},
           "kind": {"type": "string", "enum": ["person", "car", "place", "product", "pet", "other"]}},
          ["subject_name", "post_id"]),
    _tool("update_brand", "Guarda el perfil de la marca para que todo el contenido sea coherente. "
          "También configura el piloto automático (posts diarios).",
          {"name": {"type": "string"}, "description": {"type": "string"}, "audience": {"type": "string"},
           "tone": {"type": "string"}, "visual_style": {"type": "string"},
           "pillars": {"type": "array", "items": {"type": "string"}},
           "base_hashtags": {"type": "array", "items": {"type": "string"}},
           "platforms": {"type": "array", "items": {"type": "string", "enum": PLATFORM_KEYS}},
           "autopilot_daily": {"type": "integer", "minimum": 0, "maximum": 200},
           "autopilot_hour": {"type": "integer", "minimum": 0, "maximum": 23}}, []),
    _tool("studio_status", "Estado de la cola, posts listos hoy y campañas en curso.", {}, []),
]


def studio_context() -> str:
    with session() as s:
        b = get_or_create_brand(s)
        subs = s.query(Subject).order_by(Subject.created_at).all()
        brand = (f"Marca: {b.name} | {b.description or 'sin descripción'} | público: {b.audience or '-'} | "
                 f"tono: {b.tone} | estilo visual: {b.visual_style} | plataformas: {', '.join(b.platforms or [])} | "
                 f"piloto automático: {b.autopilot_daily}/día")
        bank = ", ".join(f"{x.name} ({x.kind})" for x in subs) or "vacío"
    return f"[Estado del estudio — {brand}. Banco de modelos: {bank}.]"


# ------------------------------------------------------------------ Ejecución de herramientas
def _brief(p: dict) -> dict:
    out = {k: p[k] for k in ("id", "status", "platform", "idea", "qa_score", "qa_passed", "qa_notes", "caption", "hashtags", "error")}
    out["rejected_attempts_kept"] = len(p.get("alternatives", []))
    return out


async def _run_tool(name: str, args: dict, conv_id: str, emit) -> list | str:
    if name == "create_posts":
        ids = []
        for it in args["posts"][:6]:
            sids, missing = pipeline.find_subjects(it.get("subjects", []))
            if missing:
                await emit({"type": "status", "text": f"No encontré en el banco: {', '.join(missing)}"})
            ids.append(pipeline.new_post(it["idea"], it.get("platform", ""), conversation_id=conv_id,
                                         subject_ids=sids, caption_notes=it.get("caption_notes", "")))
        await emit({"type": "status", "text": f"Equipo trabajando en {len(ids)} post(s)…"})
        await pipeline.wait_for(ids)
        return _posts_result(ids)

    if name == "create_campaign":
        sids, missing = pipeline.find_subjects(args.get("subjects", []))
        cid = pipeline.new_campaign(args["brief"], args["count"], args.get("platforms"), sids)
        await emit({"type": "status", "text": f"Campaña de {args['count']} posts planificándose…"})
        return json.dumps({"campaign_id": cid, "count": args["count"], "subjects_not_found": missing,
                           "note": "Los posts se generan en cola y aparecen en la galería."}, ensure_ascii=False)

    if name == "edit_post":
        pid = pipeline.new_edit(args["post_id"], args["instruction"], bool(args.get("rewrite_caption")), conv_id)
        await emit({"type": "status", "text": "Editando…"})
        await pipeline.wait_for([pid])
        return _posts_result([pid])

    if name == "list_posts":
        with session() as s:
            q = s.query(Post).order_by(Post.created_at.desc())
            if args.get("status"):
                q = q.filter(Post.status == args["status"])
            rows = [_brief(post_dict(p)) for p in q.limit(args.get("limit", 10))]
        return json.dumps(rows, ensure_ascii=False)

    if name == "view_post":
        with session() as s:
            p = s.get(Post, args["post_id"])
            if not p:
                raise ValueError("No existe ese post")
            data = _brief(post_dict(p))
        blocks = [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}]
        if p.image_path:
            blocks.append(image_block(p.image_path, 1024))
        return blocks

    if name == "set_status":
        with session() as s:
            for pid in args["post_ids"]:
                if p := s.get(Post, pid):
                    p.status = args["status"]
        for pid in args["post_ids"]:
            with session() as s:
                if p := s.get(Post, pid):
                    from app import events
                    events.publish({"type": "post", "post": post_dict(p)})
        return f"{len(args['post_ids'])} post(s) → {args['status']}"

    if name == "publish_post":
        return json.dumps(await publishers.publish(args["post_id"], args["target"]), ensure_ascii=False)

    if name == "create_subject":
        sid = pipeline.new_subject(args["name"], args["kind"], args["request"])
        await emit({"type": "status", "text": f"Creando a {args['name']} en el banco de modelos…"})
        await pipeline.wait_for([sid])
        with session() as s:
            sub = s.get(Subject, sid)
            data = subject_dict(sub)
        out = [{"type": "text", "text": json.dumps({k: data[k] for k in ("id", "name", "kind", "description", "notes")},
                                                   ensure_ascii=False)}]
        if sub.references:
            out.append(image_block(sub.references[0], 768))
        else:
            out[0]["text"] += "\n(No se pudo generar la foto de referencia; revisa el motor de imagen.)"
        return out

    if name == "list_subjects":
        with session() as s:
            rows = [{k: d[k] for k in ("id", "name", "kind", "description", "notes")} | {"photos": len(d["references"])}
                    for d in (subject_dict(x) for x in s.query(Subject).order_by(Subject.name))]
        return json.dumps(rows, ensure_ascii=False) if rows else "El banco de modelos está vacío."

    if name == "add_subject_reference":
        with session() as s:
            p = s.get(Post, args["post_id"])
            if not p or not (p.raw_path or p.image_path):
                raise ValueError("Ese post no tiene imagen")
            path = p.raw_path or p.image_path
        sids, _ = pipeline.find_subjects([args["subject_name"]])
        sid = sids[0] if sids else pipeline.new_subject(args["subject_name"], args.get("kind", "person"), "", generate=False)
        data = await pipeline.add_subject_reference(sid, path, describe=not sids)
        return json.dumps({k: data[k] for k in ("id", "name", "kind", "description")}, ensure_ascii=False)

    if name == "update_brand":
        with session() as s:
            b = get_or_create_brand(s)
            for k, v in args.items():
                if hasattr(Brand, k):
                    setattr(b, k, v)
        return "Perfil de marca actualizado. " + studio_context()

    if name == "studio_status":
        with session() as s:
            camps = [{"id": c.id, "brief": c.brief[:80], "count": c.count, "status": c.status}
                     for c in s.query(Campaign).order_by(Campaign.created_at.desc()).limit(5)]
        return json.dumps({"queue": pipeline.queue_stats(), "campaigns": camps}, ensure_ascii=False)

    raise ValueError(f"Herramienta desconocida: {name}")


def _posts_result(ids: list[str]) -> list:
    blocks, previews = [], 0
    with session() as s:
        rows = [s.get(Post, i) for i in ids]
        summary = [_brief(post_dict(p)) for p in rows]
    blocks.append({"type": "text", "text": json.dumps(summary, ensure_ascii=False)})
    for p in rows:
        if p.image_path and p.status in ("ready", "approved") and previews < 4:
            blocks += [{"type": "text", "text": f"Imagen del post {p.id}:"}, image_block(p.image_path, 640)]
            previews += 1
    return blocks


# ------------------------------------------------------------------ Bucle del agente
async def chat(conv_id: str | None, user_text: str, upload_post_ids: list[str] | None = None) -> AsyncIterator[dict]:
    """Procesa un mensaje del usuario y va emitiendo eventos (estado, posts, respuesta final)."""
    import asyncio

    queue: asyncio.Queue = asyncio.Queue()

    async def emit(ev: dict):
        await queue.put(ev)

    async def run():
        try:
            await _turn(conv_id, user_text, upload_post_ids or [], emit)
        except Exception as e:
            log.exception("chat")
            await emit({"type": "error", "text": str(e)})
        finally:
            await emit({"type": "done"})

    task = asyncio.create_task(run())
    while True:
        ev = await queue.get()
        yield ev
        if ev["type"] == "done":
            break
    await task


async def _turn(conv_id, user_text, upload_post_ids, emit):
    with session() as s:
        conv = s.get(Conversation, conv_id) if conv_id else None
        if not conv:
            conv = Conversation(title=user_text[:60] or "Nueva conversación")
            s.add(conv)
            s.flush()
        conv_id, messages, display = conv.id, list(conv.api_messages or []), list(conv.display or [])
    await emit({"type": "conversation", "id": conv_id})

    content: list = [{"type": "text", "text": studio_context()}]
    for pid in upload_post_ids:
        with session() as s:
            p = s.get(Post, pid)
            if p and p.image_path:
                content += [{"type": "text", "text": f"El usuario subió esta imagen (post_id={pid}):"}, image_block(p.image_path, 1024)]
    content.append({"type": "text", "text": user_text})
    messages.append({"role": "user", "content": content})
    display.append({"role": "user", "text": user_text, "post_ids": upload_post_ids})

    created_posts: list[str] = []
    final_text = ""
    for _ in range(25):
        resp = await create(
            system=SYSTEM, tools=TOOLS, messages=messages, max_tokens=16000,
            thinking={"type": "adaptive", "display": "updates"},
            output_config={"effort": "medium"},
            cache_control={"type": "ephemeral"},
            betas_extra=[UPDATES_BETA],
        )
        messages.append({"role": "assistant", "content": [b.model_dump(mode="json", exclude_none=True) for b in resp.content]})
        for b in resp.content:
            if b.type == "thinking" and getattr(b, "thinking", ""):
                await emit({"type": "status", "text": b.thinking})
        text = text_of(resp)
        if text:
            final_text = text
        if resp.stop_reason == "pause_turn":
            continue
        uses = [b for b in resp.content if b.type == "tool_use"]
        if resp.stop_reason != "tool_use" or not uses:
            break
        if text:
            await emit({"type": "status", "text": text})
        results = []
        for u in uses:
            try:
                out = await _run_tool(u.name, dict(u.input or {}), conv_id, emit)
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": out})
                if u.name in ("create_posts", "edit_post"):
                    for blk in out:
                        if blk.get("type") == "text" and blk["text"].startswith("["):
                            ids = [r["id"] for r in json.loads(blk["text"])]
                            created_posts += ids
                            await emit({"type": "posts", "ids": ids})
                if u.name in ("create_subject", "add_subject_reference"):
                    await emit({"type": "subjects_changed"})
            except Exception as e:
                log.warning("tool %s falló: %s", u.name, e)
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": f"Error: {e}", "is_error": True})
        messages.append({"role": "user", "content": results})

    display.append({"role": "assistant", "text": final_text, "post_ids": created_posts})
    with session() as s:
        conv = s.get(Conversation, conv_id)
        conv.api_messages, conv.display = messages, display
    await emit({"type": "message", "text": final_text, "post_ids": created_posts})
