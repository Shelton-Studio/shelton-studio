"""Shelton: el jefe supremo. Conversa con el usuario y dirige a todos los subagentes mediante herramientas.

También permite chatear directo con cualquier subagente (sin herramientas) usando su ficha.
"""
import asyncio
import json
import logging
from typing import AsyncIterator

from app import events, pipeline, publishers
from app.agents import registry, training
from app.db import Brand, Campaign, Conversation, Post, Subject, get_or_create_brand, session
from app.engines import engine_choices
from app.llm import create, text_of
from app.media import image_block
from app.platforms import PLATFORMS
from app.serialize import post_dict, subject_dict

log = logging.getLogger("shelton.chief")

PLATFORM_KEYS = list(PLATFORMS)
SUBJECT_KINDS = ["person", "car", "vehicle", "place", "product", "pet", "creature", "object", "style", "other"]
UPDATES_BETA = "thinking-display-updates-2026-08-18"

SYSTEM = registry.register_default("shelton", f"""Eres Shelton, el director general de un estudio de contenido para redes
sociales (Instagram, TikTok, Telegram, Facebook, X, Pinterest, YouTube). Eres el jefe de un equipo de subagentes:
- Director creativo: convierte una idea en el prompt perfecto para el modelo de imagen.
- Motor de imagen (local por defecto): Z-Image Turbo (rápido), Qwen-Image (calidad máxima y texto en la imagen),
  Qwen-Image-Edit (ediciones y escenas con modelos del banco). Puede hacer cualquier cosa: fotos, pinturas,
  ilustración, 3D, naves espaciales, criaturas, familias, niños jugando, productos, paisajes...
- Productor de video: anima posts con Wan 2.2 (clips de ~5 s) y une clips en reels verticales.
- Juez de calidad: puntúa cada imagen y pide reintentos. Solo opina: nunca borra ni aprueba.
- Copywriter: caption, hashtags y texto alternativo para cada red.
- Planificador: diseña lotes grandes de ideas distintas (campañas de 10, 50 o más posts).
- Diseñador del banco de modelos: personajes, autos, naves, lugares, productos, criaturas o estilos recurrentes.
Tú decides qué hacer y a quién encargarlo usando tus herramientas. El usuario solo te dice lo que quiere.

Cómo trabajas:
- Habla en el idioma del usuario, breve y claro, como un director creativo con criterio. Nada de relleno.
- Si el pedido es claro, actúa enseguida. Si falta algo esencial (por ejemplo, de qué trata la marca para una
  campaña grande), pregunta una sola vez y propone opciones.
- Hasta ~6 posts: create_posts. Más que eso o "N diarios": create_campaign (se procesan en cola).
- Cada idea que envíes debe ser una escena concreta y visual, con el estilo pedido (foto, óleo, anime, 3D...).
- Videos y reels, en cadena y con una sola orden: para un reel de varias escenas usa create_reel (imagen -> juez ->
  video -> juez -> montaje -> texto). Para clips sueltos usa create_posts con format="video". Para animar posts que ya
  existen: make_video; para unir posts existentes: make_reel. Formato vertical (tiktok / instagram_story) salvo que pidan otro.
- Si el usuario nombra a un personaje, auto, lugar, producto o estilo, búscalo en el banco (list_subjects) y pásalo
  en `subjects`. Si no existe y lo quiere recurrente, créalo con create_subject.
- Para cambiar una imagen existente usa edit_post (crea una versión nueva; la original se conserva).
- Motor de imagen: si el usuario pide cambiar de modelo/motor o pregunta cuáles hay, usa image_engines y
  set_image_engine, y explícale en 1-2 líneas la diferencia (velocidad, calidad, costo) y que también puede
  cambiarlo con el selector "Motor" arriba del chat.
- Elegir modelo por pedido: tienes todos los modelos disponibles (models_catalog). Si el usuario no fijó un motor en
  el chat, escoge en create_posts / create_reel / make_video / create_campaign el que más convenga (image_model,
  video_model): máximo realismo para personas, autos y productos; texto en la imagen para afiches; video con audio
  (Veo) para escenas con sonido; económico para pruebas o lotes grandes. Usa lo que tengas en tu memoria sobre qué
  modelo funcionó mejor para cada tipo de pedido. Si dudas, déjalo vacío (automático = el más realista). Di en una
  línea qué modelo elegiste y por qué. Cuando el usuario apruebe o critique un resultado, guarda con remember qué
  modelo funcionó (o no) para ese tipo de pedido, para elegir mejor la próxima vez.
- Banco de fotos: si el usuario sube o pega varias fotos de lo mismo (un carro, una persona, una flor, un paisaje)
  y pide crear el modelo, usa create_subject_from_photos con TODAS las fotos. Si no dio nombre, pregúntalo una vez.
  Después ofrece hacer contenido con ese modelo: se usarán sus fotos como referencia para mantener la identidad.
- Voz y sonido: add_voice pone locución (el usuario puede pedir grave, aguda, femenina, sensual, adulta, anciana,
  robotizada, épica...) y add_sound efectos (motor rugiendo, moto, olas...). Para un reel con voz: create_reel y luego
  add_voice sobre el reel. Si un modelo del banco tiene voz propia, se usa sola.
- Investigación: research_web para opciones actuales con fuentes (autos exóticos, lugares, estilos). Presenta las
  opciones y deja que el usuario elija antes de crear modelos del banco.
- Memoria: si alguien te enseña algo ("aprende que...", "de ahora en adelante..."), guárdalo con remember en el agente
  que corresponda (copywriter para textos, judge para criterios de calidad, art_director para estilo visual...).
- Eres el núcleo: puedes leer la memoria de cualquier subagente (read_agent_memory) y revisar al equipo completo
  (review_team). Si detectas un problema o contradicción entre agentes, explícalo claro (quién, qué dice cada uno, por
  qué choca) y sugiere la solución; la decisión de cambiar lecciones es de las personas.
- Plataformas: {', '.join(PLATFORM_KEYS)}. Si no dicen, usa las de la marca.
- Después de generar, resume en 1-3 líneas lo que salió (nota del juez, qué mejorarías). No repitas el caption.
- Nunca inventes resultados: solo informa lo que devuelven las herramientas.
- La decisión final es siempre de una persona. No apruebes ni rechaces nada salvo que el usuario lo pida
  explícitamente. Publicar siempre pasa por la bandeja de Aprobaciones. Nada se borra: los intentos que el juez
  reprobó quedan guardados para que el usuario elija.
- No crees imágenes de personas reales identificables, ni contenido sexual, de odio o engañoso. Nunca sexualices
  a menores; escenas con niños deben ser totalmente inocentes (familia, juego, escuela).""")


def _tool(name, description, properties, required):
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": properties, "required": required}}


_IDEA = {"type": "object", "properties": {
    "idea": {"type": "string", "description": "Escena concreta y visual del post, con su estilo"},
    "platform": {"type": "string", "enum": PLATFORM_KEYS},
    "subjects": {"type": "array", "items": {"type": "string"}, "description": "Nombres del banco de modelos que aparecen"},
    "caption_notes": {"type": "string", "description": "Indicaciones para el texto (oferta, CTA, idioma...)"},
    "format": {"type": "string", "enum": ["image", "video"], "description": "video = la cadena sigue hasta un clip animado"},
    "motion": {"type": "string", "description": "Solo video: qué se mueve y cómo (opcional)"},
}, "required": ["idea", "platform"]}

_MODEL_PICK = {
    "image_model": {"type": "string", "description": "Opcional: id exacto de un modelo de imagen de models_catalog que "
                    "convenga para ESTE pedido. Vacío = automático (el más realista disponible)."},
    "video_model": {"type": "string", "description": "Opcional: id exacto de un modelo de video de models_catalog que "
                    "convenga para ESTE pedido. Vacío = automático (la mejor calidad disponible)."},
}

TOOLS = [
    _tool("models_catalog", "Lista TODOS los modelos de imagen y video disponibles (OpenRouter) con etiquetas de para qué "
          "sirve cada uno y cuál elige el automático. Úsalo para escoger el modelo que más conviene a cada pedido.", {}, []),
    _tool("create_posts", "Crea de 1 a 6 posts (imagen + texto) y espera el resultado con la nota del juez.",
          {"posts": {"type": "array", "items": _IDEA, "minItems": 1, "maxItems": 6}, **_MODEL_PICK}, ["posts"]),
    _tool("create_campaign", "Planifica y encola un lote grande de posts distintos (7 a 200). Devuelve al instante; "
          "los posts aparecen en la galería a medida que se terminan.",
          {"brief": {"type": "string", "description": "Objetivo, temas, tono, estilos, fechas, ofertas..."},
           "count": {"type": "integer", "minimum": 1, "maximum": 200},
           "platforms": {"type": "array", "items": {"type": "string", "enum": PLATFORM_KEYS}},
           "subjects": {"type": "array", "items": {"type": "string"}}, **_MODEL_PICK}, ["brief", "count"]),
    _tool("edit_post", "Edita un post existente: cambiar algo de la imagen (fondo, ropa, luz, color, estilo, añadir/quitar "
          "objetos, texto en la imagen) o solo reescribir su texto. Crea una versión nueva y espera el resultado.",
          {"post_id": {"type": "string"}, "instruction": {"type": "string"},
           "rewrite_caption": {"type": "boolean", "description": "Reescribir también el caption"}}, ["post_id", "instruction"]),
    _tool("create_reel", "UNA SOLA ORDEN para un reel completo: cada escena pasa por imagen -> juez -> video -> juez y "
          "al final se monta el reel vertical con su texto. Espera el resultado (puede tardar varios minutos).",
          {"idea": {"type": "string", "description": "De qué trata el reel"},
           "scenes": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 8,
                      "description": "Escenas concretas y visuales, en orden (gancho primero)"},
           "platform": {"type": "string", "enum": ["tiktok", "instagram_story", "x", "square", "instagram_feed"]},
           "subjects": {"type": "array", "items": {"type": "string"}}, **_MODEL_PICK}, ["idea", "scenes"]),
    _tool("make_video", "Anima la imagen de un post y crea un clip de video (~5 s) para Reels/TikTok/Shorts. "
          "Crea un post nuevo de tipo video y espera el resultado.",
          {"post_id": {"type": "string"},
           "motion": {"type": "string", "description": "Qué se mueve y cómo (cámara, acción, ambiente). Vacío = lo decide el productor"},
           "video_model": _MODEL_PICK["video_model"]},
          ["post_id"]),
    _tool("make_reel", "Une varios posts de video en un solo reel vertical (en orden) con su caption.",
          {"post_ids": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 12},
           "idea": {"type": "string", "description": "De qué trata el reel (para el texto)"}}, ["post_ids"]),
    _tool("add_voice", "Le pone voz (y opcionalmente un efecto de sonido) a un post de imagen o video. El Locutor escribe "
          "el guion y diseña la voz según la descripción (grave, aguda, femenina, masculina, sensual, adulta, anciana, "
          "robotizada, épica...). Si el post tiene un modelo del banco con voz propia, la usa. Crea una versión nueva.",
          {"post_id": {"type": "string"},
           "voice_request": {"type": "string", "description": "Cómo debe sonar y qué tono"},
           "script": {"type": "string", "description": "Texto exacto a decir (vacío = lo escribe el Locutor)"},
           "sound_request": {"type": "string", "description": "Efecto de fondo opcional: 'motor V8 rugiendo', 'olas'..."},
           "seconds": {"type": "number"}}, ["post_id"]),
    _tool("add_sound", "Le pone solo un efecto de sonido o ambiente (sin voz) a un post: motor de auto, moto, olas, "
          "ciudad, viento, multitud... Crea una versión nueva.",
          {"post_id": {"type": "string"}, "sound_request": {"type": "string"}}, ["post_id", "sound_request"]),
    _tool("research_web", "Investiga en internet y devuelve opciones con fuentes (modelos de autos exóticos, lugares, "
          "estilos, referencias, datos). Úsala cuando el usuario pida investigar u opciones actuales.",
          {"question": {"type": "string"}}, ["question"]),
    training.REMEMBER_TOOL,
    _tool("read_agent_memory", "Lee el prompt y las lecciones de entrenamiento de un subagente (para entender cómo trabaja, "
          "detectar problemas o explicar por qué hizo algo).",
          {"agent": {"type": "string", "enum": list(registry.AGENTS)}}, ["agent"]),
    _tool("review_team", "Revisa la memoria y las instrucciones de TODO el equipo, detecta contradicciones o problemas entre "
          "subagentes y propone soluciones. Úsala si el usuario lo pide o si notas resultados incoherentes.", {}, []),
    _tool("list_posts", "Lista los posts más recientes (opcionalmente filtrados por estado).",
          {"status": {"type": "string", "enum": ["queued", "generating", "ready", "approved", "published", "failed", "rejected"]},
           "limit": {"type": "integer", "minimum": 1, "maximum": 30}}, []),
    _tool("view_post", "Mira la imagen y los datos de un post para opinar o decidir una edición.",
          {"post_id": {"type": "string"}}, ["post_id"]),
    _tool("set_status", "Aprueba o rechaza posts. Úsala SOLO si el usuario lo pide explícitamente.",
          {"post_ids": {"type": "array", "items": {"type": "string"}},
           "status": {"type": "string", "enum": ["approved", "rejected", "ready"]}}, ["post_ids", "status"]),
    _tool("publish_post", "Pide publicar un post. NO publica: crea una solicitud en la bandeja de Aprobaciones que una "
          "persona debe aprobar. Usa solo si el usuario lo pide.",
          {"post_id": {"type": "string"}, "target": {"type": "string", "description": "id del destino (ver list_targets) o 'telegram'"}},
          ["post_id", "target"]),
    _tool("list_targets", "Destinos de publicación conectados (Telegram, Instagram, Facebook, Discord, webhooks...).", {}, []),
    _tool("create_subject", "Crea un modelo en el banco (persona, auto, vehículo/nave, lugar, producto, mascota, criatura, "
          "objeto o estilo) con nombre propio. Genera su imagen de referencia y su ficha. Tarda ~1 minuto.",
          {"name": {"type": "string"}, "kind": {"type": "string", "enum": SUBJECT_KINDS},
           "request": {"type": "string", "description": "Cómo es: rasgos, estilo, edad, color, detalles, personalidad"}},
          ["name", "kind", "request"]),
    _tool("list_subjects", "Lista el banco de modelos (nombre, tipo, descripción).", {}, []),
    _tool("create_subject_from_photos", "Crea (o amplía) un modelo del banco a partir de VARIAS fotos del mismo carro, "
          "persona, flor, producto o paisaje que subió el usuario. Úsala cuando el usuario sube o pega fotos y pide "
          "'créame este modelo', 'guárdalo como modelo', etc. Pasa TODAS las fotos subidas (sus post_id).",
          {"name": {"type": "string", "description": "Nombre del modelo (pregúntalo si no lo dijo)"},
           "post_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
           "kind": {"type": "string", "enum": ["auto", *SUBJECT_KINDS], "description": "auto = detectarlo"},
           "notes": {"type": "string", "description": "Lo que el usuario contó del modelo"}}, ["name", "post_ids"]),
    _tool("add_subject_reference", "Usa la imagen de un post (o una foto subida por el usuario) como referencia de un "
          "modelo del banco. Si el modelo no existe, lo crea a partir de esa imagen.",
          {"subject_name": {"type": "string"}, "post_id": {"type": "string"},
           "kind": {"type": "string", "enum": SUBJECT_KINDS}}, ["subject_name", "post_id"]),
    _tool("image_engines", "Lista los motores de imagen disponibles (locales y conectados) y cuál usa esta conversación.", {}, []),
    _tool("set_image_engine", "Cambia el motor de imagen de esta conversación.",
          {"engine_id": {"type": "string", "description": "id de image_engines ('' = automático)"}}, ["engine_id"]),
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


def studio_context(engine: str) -> str:
    with session() as s:
        b = get_or_create_brand(s)
        subs = s.query(Subject).order_by(Subject.created_at).all()
        brand = (f"Marca: {b.name} | {b.description or 'sin descripción'} | público: {b.audience or '-'} | "
                 f"tono: {b.tone} | estilo visual: {b.visual_style} | plataformas: {', '.join(b.platforms or [])} | "
                 f"piloto automático: {b.autopilot_daily}/día")
        bank = ", ".join(f"{x.name} ({x.kind})" for x in subs) or "vacío"
    label = next((c["label"] for c in engine_choices() if c["id"] == engine), "Automático")
    from app.engines import MODES, infra_mode
    mode = MODES[infra_mode()]
    return (f"[Estado del estudio — {brand}. Banco de modelos: {bank}. Motor de imagen de esta conversación: {label}. "
            f"Switch de herramientas: {mode} (Local = solo máquinas propias; Nube = solo APIs en la nube). "
            f"Si una herramienta falta en este modo, explícale al usuario que la conecte en el botón APIs o cambie el switch.]")


# ------------------------------------------------------------------ Ejecución de herramientas
def _brief(p: dict) -> dict:
    out = {k: p[k] for k in ("id", "status", "media_type", "platform", "idea", "qa_score", "qa_passed", "qa_notes",
                             "caption", "hashtags", "error")}
    out["rejected_attempts_kept"] = len(p.get("alternatives", []))
    return out


def _set_conv_engine(conv_id: str, engine: str) -> None:
    with session() as s:
        if c := s.get(Conversation, conv_id):
            c.engine = engine


async def _wait_and_report(ids: list[str], emit, text: str) -> list:
    await emit({"type": "posts", "ids": ids})
    await emit({"type": "status", "text": text})
    await pipeline.wait_for(ids, timeout=1800)
    return _posts_result(ids)


async def _announce(emit, role: str, engine: str | None = None) -> None:
    """Antes de producir, avisa en qué modo trabaja y con qué modelo (elegido automáticamente si es «auto»)."""
    from app.engines import describe_role
    from app.voice import voice_label
    if role == "voice":
        label = f"🎙️ Modo audio · {voice_label()}"
    else:
        label = {"video": "🎬 Modo video", "generate": "🖼️ Modo imagen"}[role] + " · " + await describe_role(role, engine)
    await emit({"type": "status", "text": label})
    await emit({"type": "mode_tag", "text": label})


async def _run_tool(name: str, args: dict, ctx: dict, emit) -> list | str:
    conv_id, engine = ctx["conv_id"], ctx["engine"] or None
    if name in ("create_posts", "create_campaign", "create_reel", "make_video") and (
            args.get("image_model") or args.get("video_model")):
        from app.model_picker import choice_for
        engine, note = await choice_for(args.get("image_model"), args.get("video_model"), engine)
        if note:
            await emit({"type": "status", "text": note})
    picked = engine if (engine or "").startswith("or:") else None   # el video también usa el modelo que eligió Shelton
    if name in ("create_reel", "make_video", "make_reel") or (
            name == "create_posts" and any(p.get("format") == "video" for p in args.get("posts", []))):
        await _announce(emit, "video", engine if (engine or "").startswith("or:") else None)
    elif name in ("add_voice", "add_sound"):
        await _announce(emit, "voice")

    if name == "create_posts":
        ids = []
        for it in args["posts"][:6]:
            sids, missing = pipeline.find_subjects(it.get("subjects", []))
            if missing:
                await emit({"type": "status", "text": f"No encontré en el banco: {', '.join(missing)}"})
            ids.append(pipeline.new_post(it["idea"], it.get("platform", ""), conversation_id=conv_id, subject_ids=sids,
                                         caption_notes=it.get("caption_notes", ""), engine=engine,
                                         media=it.get("format", "image"), motion=it.get("motion", ""),
                                         video_engine=picked))
        return await _wait_and_report(ids, emit, f"Equipo trabajando en {len(ids)} post(s)…")

    if name == "create_campaign":
        sids, missing = pipeline.find_subjects(args.get("subjects", []))
        cid = pipeline.new_campaign(args["brief"], args["count"], args.get("platforms"), sids, engine)
        await emit({"type": "status", "text": f"Campaña de {args['count']} posts planificándose…"})
        return json.dumps({"campaign_id": cid, "count": args["count"], "subjects_not_found": missing,
                           "note": "Los posts se generan en cola y aparecen en la galería."}, ensure_ascii=False)

    if name == "edit_post":
        pid = pipeline.new_edit(args["post_id"], args["instruction"], bool(args.get("rewrite_caption")), conv_id, engine)
        return await _wait_and_report([pid], emit, "Editando…")

    if name == "create_reel":
        sids, missing = pipeline.find_subjects(args.get("subjects", []))
        if missing:
            await emit({"type": "status", "text": f"No encontré en el banco: {', '.join(missing)}"})
        rid = pipeline.new_reel_from_scenes(args["scenes"], args["idea"], args.get("platform", "tiktok"), sids, conv_id, engine,
                                            video_engine=picked)
        with session() as s:
            scene_ids = [p.id for p in s.query(Post).filter(Post.reel_id == rid).order_by(Post.created_at)]
        await emit({"type": "posts", "ids": [rid]})
        if scene_ids:
            await emit({"type": "status", "text": f"Escenas del reel ({len(scene_ids)}): cada una se puede ver apenas termina 👇"})
            await emit({"type": "posts", "ids": scene_ids})
        return await _wait_and_report([rid], emit, f"Cadena de reel en marcha: {len(args['scenes'])} escenas…")

    if name == "make_video":
        pid = pipeline.new_video(args["post_id"], args.get("motion", ""), conv_id, engine=engine)
        return await _wait_and_report([pid], emit, "Productor de video animando la escena…")

    if name == "make_reel":
        pid = pipeline.new_reel(args["post_ids"], args.get("idea", ""), conv_id)
        return await _wait_and_report([pid], emit, "Montando el reel…")

    if name == "add_voice":
        pid = pipeline.new_audio(args["post_id"], args.get("voice_request", ""), args.get("script", ""),
                                 args.get("sound_request", ""), True, conv_id, args.get("seconds"))
        return await _wait_and_report([pid], emit, "Locutor trabajando en la voz…")

    if name == "add_sound":
        pid = pipeline.new_audio(args["post_id"], sound_request=args["sound_request"], voice=False, conversation_id=conv_id)
        return await _wait_and_report([pid], emit, "Diseñador de sonido trabajando…")

    if name == "research_web":
        from app.agents import specialists
        await emit({"type": "status", "text": "Investigador buscando en internet…"})
        return await specialists.research(args["question"])

    if name == "remember":
        target = args.get("agent") or ctx.get("agent", "shelton")
        if target not in registry.AGENTS:
            target = ctx.get("agent", "shelton")
        e = await training.add_checked(target, args["title"], args["content"], author="Chat", role="usuario", source="chat")
        applied = e["status"] == "active"
        ev = {"type": "learned", "agent": target, "agent_name": registry.AGENTS[target]["name"], "title": e["title"],
              "applied": applied, "status": e["status"], "reason": e["conflict_reason"], "id": e["id"]}
        await emit(ev)
        events.publish(ev)
        if not applied:
            return (f"NO aplicada: hay una contradicción. {e['conflict_reason']} Quedó en espera en Memoria; "
                    "explícale al usuario la contradicción y que decida: activarla igual (bajo su responsabilidad), "
                    "reemplazar la anterior o descartarla.")
        return f"Lección guardada y aplicada en {registry.AGENTS[target]['name']} ({e['created']})."

    if name == "read_agent_memory":
        key = args["agent"]
        lessons = training.entries(key)
        return json.dumps({"agent": key, "name": registry.AGENTS[key]["name"], "prompt": registry.base_prompt(key)[:3000],
                           "lessons": [{k: e[k] for k in ("id", "title", "created", "author", "status", "content",
                                                          "conflict_reason")} for e in lessons[:40]]}, ensure_ascii=False)

    if name == "review_team":
        await emit({"type": "status", "text": "Shelton revisando la memoria de todo el equipo…"})
        audit = await training.team_audit(studio_context(ctx["engine"]))
        events.publish({"type": "audit", "issues": len(audit["issues"]), "summary": audit["summary"]})
        return json.dumps(audit, ensure_ascii=False)

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
            data, path = _brief(post_dict(p)), p.image_path
        blocks = [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}]
        if path:
            blocks.append(image_block(path, 1024))
        return blocks

    if name == "set_status":
        for pid in args["post_ids"]:
            with session() as s:
                if p := s.get(Post, pid):
                    p.status = args["status"]
                    s.flush()
                    events.publish({"type": "post", "post": post_dict(p)})
        return f"{len(args['post_ids'])} post(s) → {args['status']}"

    if name == "publish_post":
        a = publishers.request_publish(args["post_id"], args["target"], "shelton")
        return f"Solicitud creada en Aprobaciones ({a['title']}). Se publicará cuando una persona la apruebe."

    if name == "list_targets":
        return json.dumps(publishers.targets(), ensure_ascii=False) or "No hay destinos conectados."

    if name == "create_subject":
        sid = pipeline.new_subject(args["name"], args["kind"], args["request"], engine=engine)
        await emit({"type": "status", "text": f"Creando a {args['name']} en el banco de modelos…"})
        await pipeline.wait_for([sid])
        with session() as s:
            sub = s.get(Subject, sid)
            data, ref = subject_dict(sub), (sub.references or [None])[0]
        out = [{"type": "text", "text": json.dumps({k: data[k] for k in ("id", "name", "kind", "description", "notes")},
                                                   ensure_ascii=False)}]
        if ref:
            out.append(image_block(ref, 768))
        else:
            out[0]["text"] += "\n(No se pudo generar la imagen de referencia; revisa el motor de imagen.)"
        return out

    if name == "list_subjects":
        with session() as s:
            rows = [{k: d[k] for k in ("id", "name", "kind", "description", "notes")} | {"photos": len(d["references"])}
                    for d in (subject_dict(x) for x in s.query(Subject).order_by(Subject.name))]
        return json.dumps(rows, ensure_ascii=False) if rows else "El banco de modelos está vacío."

    if name == "create_subject_from_photos":
        paths = []
        with session() as s:
            for pid in args["post_ids"]:
                p = s.get(Post, pid)
                if p and (p.raw_path or p.image_path):
                    paths.append(p.raw_path or p.image_path)
        if not paths:
            raise ValueError("No encontré las fotos subidas; pide al usuario que las adjunte con el clip 📎")
        await emit({"type": "status", "text": f"Analista revisando {len(paths)} fotos para crear a {args['name']}…"})
        data = await pipeline.create_subject_from_photos(args["name"], paths, args.get("kind", "auto"), args.get("notes", ""))
        await emit({"type": "subjects_changed"})
        out = [{"type": "text", "text": json.dumps({k: data[k] for k in ("id", "name", "kind", "description")} |
                                                   {"photos": len(data["references"])}, ensure_ascii=False)}]
        return out

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

    if name == "models_catalog":
        from app.model_picker import catalog
        return json.dumps(await catalog(), ensure_ascii=False)[:12000]

    if name == "image_engines":
        return json.dumps({"current": ctx["engine"], "options": engine_choices()}, ensure_ascii=False)

    if name == "set_image_engine":
        eid = args.get("engine_id", "")
        if eid and eid not in {c["id"] for c in engine_choices()}:
            raise ValueError("Ese motor no existe; usa image_engines para ver las opciones")
        ctx["engine"] = eid
        _set_conv_engine(conv_id, eid)
        await emit({"type": "engine", "id": eid})
        return f"Motor cambiado a: {next(c['label'] for c in engine_choices() if c['id'] == eid)}"

    if name == "update_brand":
        with session() as s:
            b = get_or_create_brand(s)
            for k, v in args.items():
                if hasattr(Brand, k):
                    setattr(b, k, v)
        return "Perfil de marca actualizado. " + studio_context(ctx["engine"])

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
        paths = [(p.id, p.image_path, p.status) for p in rows]
    blocks.append({"type": "text", "text": json.dumps(summary, ensure_ascii=False)})
    for pid, path, status in paths:
        if path and status in ("ready", "approved") and previews < 4:
            blocks += [{"type": "text", "text": f"Imagen (o primer fotograma) del post {pid}:"}, image_block(path, 640)]
            previews += 1
    return blocks


# ------------------------------------------------------------------ Bucle del agente
async def chat(conv_id: str | None, user_text: str, upload_post_ids: list[str] | None = None,
               agent: str = "shelton", engine: str | None = None) -> AsyncIterator[dict]:
    """Procesa un mensaje del usuario y va emitiendo eventos (estado, posts, respuesta final)."""
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(ev: dict):
        await queue.put(ev)

    async def run():
        try:
            await _turn(conv_id, user_text, upload_post_ids or [], emit, agent, engine)
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


async def _turn(conv_id, user_text, upload_post_ids, emit, agent, engine):
    if agent not in registry.AGENTS:
        raise ValueError(f"Agente desconocido: {agent}")
    with session() as s:
        conv = s.get(Conversation, conv_id) if conv_id else None
        if not conv:
            conv = Conversation(title=user_text[:60] or "Nueva conversación", agent=agent, engine=engine or "")
            s.add(conv)
            s.flush()
        elif engine is not None:
            conv.engine = engine
        conv_id, agent = conv.id, conv.agent or "shelton"
        messages, display = list(conv.api_messages or []), list(conv.display or [])
        ctx = {"conv_id": conv_id, "engine": conv.engine or "", "agent": conv.agent or "shelton"}
    await emit({"type": "conversation", "id": conv_id, "agent": agent})

    content: list = [{"type": "text", "text": studio_context(ctx["engine"])}]
    for pid in upload_post_ids:
        with session() as s:
            p = s.get(Post, pid)
            path = p.image_path if p else None
        if path:
            content += [{"type": "text", "text": f"El usuario subió esta imagen (post_id={pid}):"}, image_block(path, 1024)]
    content.append({"type": "text", "text": user_text})
    messages.append({"role": "user", "content": content})
    display.append({"role": "user", "text": user_text, "post_ids": upload_post_ids})

    is_chief = agent == "shelton"
    system = registry.prompt(agent) if is_chief else (
        registry.prompt(agent) + "\n\nAhora estás conversando directamente con un miembro del equipo. Responde en su "
        "idioma, como el especialista que eres. Si te enseña algo para el futuro, guárdalo con la herramienta remember.")
    created_posts: list[str] = []
    final_text = ""
    for _ in range(25):
        kwargs = dict(system=system, messages=messages, max_tokens=16000,
                      thinking={"type": "adaptive", "display": "updates"},
                      output_config={"effort": registry.AGENTS[agent]["effort"]},
                      cache_control={"type": "ephemeral"}, betas_extra=[UPDATES_BETA])
        kwargs["tools"] = TOOLS if is_chief else [training.REMEMBER_TOOL]
        resp = await create(agent=agent, summary=user_text[:200], **kwargs)
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
                out = await _run_tool(u.name, dict(u.input or {}), ctx, emit)
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": out})
                if u.name in ("create_posts", "edit_post", "make_video", "make_reel", "create_reel", "add_voice", "add_sound"):
                    created_posts += [r["id"] for r in json.loads(out[0]["text"])]
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
