"""Conexiones: todo lo externo se enchufa aquí desde la web, sin tocar código.

Cada tipo declara sus campos (para que la web arme el formulario), su categoría y cómo se prueba.
Categorías:
  brain     -> clave de Claude
  engine    -> motores de imagen (ComfyUI local/remoto, fal.ai, Replicate, OpenAI, Gemini, HTTP genérico)
  voice     -> motores de voz (local Chatterbox, ElevenLabs, API genérica)
  publisher -> destinos de publicación (Telegram, Instagram, Facebook, Discord, webhook)
  webhook   -> avisos salientes de eventos (n8n, Make, Zapier, tu propio servidor)
  gpu       -> nodos de la granja de GPUs (tarjetas alquiladas u otras PCs con el trabajador)
"""
from app.db import Connection, session

F = lambda key, label, kind="text", **kw: {"key": key, "label": label, "kind": kind, **kw}  # noqa: E731

TYPES: dict[str, dict] = {
    # --- cerebro
    "anthropic": {"category": "brain", "name": "Claude (Anthropic)", "fields": [
        F("api_key", "API key", "secret", required=True)]},
    "openrouter": {"category": "brain", "name": "OpenRouter",
                   "help": "Plan B si no tienes clave de Anthropic: una sola clave para muchos modelos. Recomendado un modelo "
                           "de Claude (visión + herramientas). La búsqueda web de los scouts usa la de OpenRouter (cobro aparte).",
                   "fields": [F("api_key", "API key", "secret", required=True),
                              F("model", "Modelo", placeholder="anthropic/claude-sonnet-4.5")]},

    # --- motores de imagen
    "comfyui": {"category": "engine", "name": "ComfyUI", "help": "Tu GPU local o remota (3050, 3090, servidor). Usa los workflows de app/comfyui/workflows.",
                "fields": [F("url", "URL", placeholder="http://127.0.0.1:8188", required=True), F("token", "Token (opcional)", "secret"),
                           F("workflow_generate", "Workflow generar", placeholder="z_image_turbo.json"),
                           F("workflow_edit", "Workflow editar", placeholder="qwen_image_edit.json"),
                           F("workflow_subject", "Workflow con referencias", placeholder="qwen_subject.json"),
                           F("gen_unet", "Modelo de generación", placeholder="z_image_turbo_bf16.safetensors"),
                           F("preset", "Perfil de imagen (fast / quality / lite)", placeholder="fast"),
                           F("video_preset", "Perfil de video (cinema / fast)", placeholder="cinema"),
                           F("gen_unet", "Modelo de generación (opcional)", placeholder="z_image_turbo_bf16.safetensors"),
                           F("gen_scale", "Escala de resolución", placeholder="1.0"),
                           F("gpu", "GPU de la placa (si hay varias: 0, 1, 2)", placeholder="0")]},
    "openrouter_image": {"category": "engine", "name": "OpenRouter (imagen)",
                         "help": "Modelos de imagen de OpenRouter (Gemini Nano Banana, GPT Image...). Usa la clave del cerebro si no pones otra. "
                                 "Sirve para generar, editar y escenas con modelos del banco. No hace video.",
                         "fields": [F("model", "Modelo de imagen", placeholder="google/gemini-2.5-flash-image"),
                                    F("api_key", "API key (opcional)", "secret")]},
    "openrouter_video": {"category": "engine", "name": "OpenRouter (video)",
                         "help": "Video con Veo, Sora, Seedance, Kling, Wan... usando tu clave de OpenRouter. Modelo «auto» = el mejor disponible.",
                         "fields": [F("model", "Modelo de video", placeholder="auto"), F("api_key", "API key (opcional)", "secret")]},
    "wavespeed": {"category": "engine", "name": "WaveSpeed",
                  "help": "Más de 1.000 modelos con una sola clave (wavespeed.ai): Seedream 5.5 para fotos y edición "
                          "con hasta 10 referencias, Wan, Seedance, Kling… para video. Marca para qué la usas.",
                  "fields": [F("api_key", "API key", "secret", required=True),
                             F("model_generate", "Modelo para generar", placeholder="bytedance/seedream-v5.5"),
                             F("model_edit", "Modelo para editar / con referencias", placeholder="bytedance/seedream-v5.5/edit"),
                             F("model_video", "Modelo de video (imagen a video)", placeholder="wavespeed-ai/wan-2.2/i2v-720p"),
                             F("extra", "Parámetros extra (JSON, opcional)", placeholder='{"output_format": "jpeg"}')]},
    "higgsfield": {"category": "engine", "name": "Higgsfield",
                   "help": "Imágenes y video de Higgsfield (Soul, Seedream, Wan, Kling, Veo…). Clave doble de "
                           "console.higgsfield.ai: Key ID y Key Secret. Marca para qué la usas (generar, editar, video).",
                   "fields": [F("api_key", "Key ID", "secret", required=True),
                              F("api_secret", "Key Secret", "secret", required=True),
                              F("model_generate", "Modelo para generar", placeholder="higgsfield-ai/soul/v2/standard"),
                              F("model_edit", "Modelo para editar / con referencias", placeholder="bytedance/seedream/v4/edit"),
                              F("model_video", "Modelo de video (imagen a video)", placeholder="wan/v2.7/image-to-video"),
                              F("extra", "Parámetros extra (JSON, opcional)", placeholder='{"resolution": "2K"}')]},
    "fal": {"category": "engine", "name": "fal.ai", "help": "Modelos en la nube (FLUX, Qwen-Image, Seedream...). Pon el id del endpoint de fal.",
            "fields": [F("api_key", "API key", "secret", required=True),
                       F("model_generate", "Endpoint generar", placeholder="fal-ai/qwen-image"),
                       F("model_edit", "Endpoint editar / referencias", placeholder="fal-ai/qwen-image-edit-plus"),
                       F("model_video", "Endpoint video", placeholder="fal-ai/kling-video/v2.1/standard/image-to-video")]},
    "replicate": {"category": "engine", "name": "Replicate", "fields": [
        F("api_token", "API token", "secret", required=True),
        F("model_generate", "Modelo generar (owner/name)", placeholder="black-forest-labs/flux-1.1-pro"),
        F("model_edit", "Modelo editar (owner/name)", placeholder="qwen/qwen-image-edit")]},
    "openai_images": {"category": "engine", "name": "OpenAI Images", "fields": [
        F("api_key", "API key", "secret", required=True), F("model", "Modelo", placeholder="gpt-image-1"),
        F("quality", "Calidad", placeholder="high")]},
    "gemini_image": {"category": "engine", "name": "Google Gemini (imagen)", "fields": [
        F("api_key", "API key", "secret", required=True), F("model", "Modelo", placeholder="gemini-2.5-flash-image")]},
    "http_engine": {"category": "engine", "name": "API HTTP genérica",
                    "help": "Cualquier API de imágenes. Plantilla JSON con {{prompt}}, {{width}}, {{height}}, {{seed}}, {{image_b64}}. "
                            "La respuesta puede traer la imagen en base64 o una URL: indica la ruta (ej: data.0.url).",
                    "fields": [F("url", "URL (POST)", required=True), F("headers", "Cabeceras (JSON)", "json", placeholder='{"Authorization": "Bearer ..."}'),
                               F("body", "Cuerpo (JSON con marcadores)", "json", required=True, placeholder='{"prompt": "{{prompt}}", "width": "{{width}}"}'),
                               F("result_path", "Ruta a la imagen en la respuesta", required=True, placeholder="images.0.url")]},

    # --- voz
    "local_tts": {"category": "voice", "name": "Voz local (Chatterbox)",
                  "help": "Servidor de voz en tu GPU: python tts_server/server.py. Español y 22 idiomas más, clona voces con 5-10 s de muestra.",
                  "fields": [F("url", "URL", placeholder="http://127.0.0.1:8189", required=True)]},
    "openrouter_tts": {"category": "voice", "name": "OpenRouter (voz)",
                       "help": "Generador de voz con tu clave de OpenRouter (ElevenLabs v4, MAI-Voice, Gemini TTS...). "
                               "Modelo «auto» = el más pro disponible. Voz vacía = el Locutor elige la que encaja.",
                       "fields": [F("model", "Modelo de voz", placeholder="auto"), F("voice", "Voz fija (opcional)", placeholder="sarah, george…"),
                                  F("api_key", "API key (opcional)", "secret")]},
    "elevenlabs": {"category": "voice", "name": "ElevenLabs (nube)", "fields": [
        F("api_key", "API key", "secret", required=True), F("voice_id", "Voice id por defecto"),
        F("model", "Modelo", placeholder="eleven_multilingual_v2")]},
    "http_tts": {"category": "voice", "name": "API de voz genérica",
                 "help": "Cuerpo JSON con {{text}}, {{language}}, {{ref_audio_b64}}. Respuesta: audio directo o base64 en la ruta indicada.",
                 "fields": [F("url", "URL (POST)", required=True), F("headers", "Cabeceras (JSON)", "json"),
                            F("body", "Cuerpo (JSON)", "json", required=True), F("result_path", "Ruta al audio base64")]},

    # --- publicación
    "telegram": {"category": "publisher", "name": "Telegram", "fields": [
        F("bot_token", "Token del bot (@BotFather)", "secret", required=True), F("chat_id", "Chat o canal (@canal o id)", required=True)]},
    "instagram": {"category": "publisher", "name": "Instagram (Graph API)",
                  "help": "Cuenta profesional conectada a una página de Facebook. Instagram descarga la imagen desde una URL pública: "
                          "pon la dirección pública donde vive Shelton (o un túnel tipo Cloudflare/ngrok).",
                  "fields": [F("ig_user_id", "Instagram user id", required=True), F("access_token", "Access token", "secret", required=True),
                             F("public_base_url", "URL pública de Shelton", required=True, placeholder="https://shelton.midominio.com"),
                             F("api_version", "Versión Graph API", placeholder="v21.0")]},
    "facebook": {"category": "publisher", "name": "Página de Facebook", "fields": [
        F("page_id", "Page id", required=True), F("access_token", "Page access token", "secret", required=True),
        F("api_version", "Versión Graph API", placeholder="v21.0")]},
    "discord": {"category": "publisher", "name": "Discord (webhook)", "fields": [F("webhook_url", "Webhook URL", "secret", required=True)]},
    "webhook_publisher": {"category": "publisher", "name": "Webhook (n8n / Make / Zapier / tu API)",
                          "help": "Shelton envía un POST con el post completo (texto, hashtags, plataforma y la imagen en base64 o URL). "
                                  "Desde ahí lo mandas a TikTok, LinkedIn, un CMS o lo que quieras.",
                          "fields": [F("url", "URL", required=True), F("headers", "Cabeceras (JSON)", "json"),
                                     F("public_base_url", "URL pública de Shelton (para mandar link en vez de base64)")]},

    # --- avisos de eventos
    # --- granja de GPUs: tarjetas alquiladas u otras PCs con el trabajador de Shelton (scripts/node_setup.sh)
    "gpu_node": {"category": "gpu", "name": "Nodo de GPU (trabajador)",
                 "help": "Tarjeta alquilada (RunPod, Vast…) u otra PC con GPU, preparada con scripts/node_setup.sh. "
                         "Shelton le manda entrenamientos de LoRA y la usa cuando la placa está ocupada.",
                 "fields": [F("url", "URL del trabajador (Tailscale)", placeholder="http://100.x.x.x:8299", required=True),
                            F("token", "Token del trabajador", "secret", required=True),
                            F("slots", "Entrenamientos a la vez", placeholder="3"),
                            F("profile", "Perfil (pro / estandar)", placeholder="pro"),
                            F("cost_hour", "Costo por hora en US$ (para Consumo)", placeholder="2.0"),
                            F("comfy_url", "ComfyUI de ese nodo (opcional)", placeholder="http://100.x.x.x:8288")]},
    "event_webhook": {"category": "webhook", "name": "Webhook de eventos",
                      "help": "Recibe un POST cada vez que pasa algo (post listo, aprobado, publicado, campaña terminada...).",
                      "fields": [F("url", "URL", required=True), F("headers", "Cabeceras (JSON)", "json"),
                                 F("events", "Eventos (coma)", placeholder="post.ready, post.approved, post.published")]},
}

ENGINE_ROLES = ["generate", "edit", "subject", "video", "sound"]


def public(c: Connection) -> dict:
    """Versión segura para la web: los secretos se muestran enmascarados."""
    t = TYPES.get(c.type, {})
    secret = {f["key"] for f in t.get("fields", []) if f["kind"] == "secret"}
    cfg = {k: ("••••" + str(v)[-4:] if k in secret and v else v) for k, v in (c.config or {}).items()}
    return {"id": c.id, "type": c.type, "type_name": t.get("name", c.type), "category": t.get("category"),
            "name": c.name, "config": cfg, "roles": c.roles or [], "enabled": c.enabled, "last_test": c.last_test or {}}


def merge_config(old: dict, new: dict) -> dict:
    """Si la web devuelve un secreto enmascarado, se conserva el valor guardado."""
    out = dict(old or {})
    for k, v in (new or {}).items():
        if isinstance(v, str) and v.startswith("••••"):
            continue
        out[k] = v
    return out


def first_config(type_: str) -> dict | None:
    with session() as s:
        c = s.query(Connection).filter(Connection.type == type_, Connection.enabled.is_(True)).first()
        return dict(c.config or {}) if c else None


def list_enabled(category: str | None = None) -> list[Connection]:
    with session() as s:
        rows = s.query(Connection).filter(Connection.enabled.is_(True)).order_by(Connection.created_at).all()
    return [c for c in rows if category is None or TYPES.get(c.type, {}).get("category") == category]


def engine_for_role(role: str) -> Connection | None:
    for c in list_enabled("engine"):
        if role in (c.roles or []):
            return c
    return None
