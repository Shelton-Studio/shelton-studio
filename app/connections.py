"""Conexiones: todo lo externo se enchufa aquí desde la web, sin tocar código.

Cada tipo declara sus campos (para que la web arme el formulario), su categoría y cómo se prueba.
Categorías:
  brain     -> clave de Claude
  engine    -> motores de imagen (ComfyUI local/remoto, fal.ai, Replicate, OpenAI, Gemini, HTTP genérico)
  publisher -> destinos de publicación (Telegram, Instagram, Facebook, Discord, webhook)
  webhook   -> avisos salientes de eventos (n8n, Make, Zapier, tu propio servidor)
"""
from app.db import Connection, session

F = lambda key, label, kind="text", **kw: {"key": key, "label": label, "kind": kind, **kw}  # noqa: E731

TYPES: dict[str, dict] = {
    # --- cerebro
    "anthropic": {"category": "brain", "name": "Claude (Anthropic)", "fields": [
        F("api_key", "API key", "secret", required=True)]},

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
                           F("gen_scale", "Escala de resolución", placeholder="1.0")]},
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
    "event_webhook": {"category": "webhook", "name": "Webhook de eventos",
                      "help": "Recibe un POST cada vez que pasa algo (post listo, aprobado, publicado, campaña terminada...).",
                      "fields": [F("url", "URL", required=True), F("headers", "Cabeceras (JSON)", "json"),
                                 F("events", "Eventos (coma)", placeholder="post.ready, post.approved, post.published")]},
}

ENGINE_ROLES = ["generate", "edit", "subject", "video"]


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
