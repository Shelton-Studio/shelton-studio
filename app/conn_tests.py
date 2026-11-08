"""Botón "Probar" de cada conexión: verifica credenciales y acceso sin gastar (o gastando lo mínimo)."""
import json

import httpx


async def test_connection(type_: str, cfg: dict) -> dict:
    try:
        msg = await _TESTS.get(type_, _fields_only)(cfg)
        return {"ok": True, "message": msg}
    except Exception as e:
        return {"ok": False, "message": str(e)[:400]}


async def _fields_only(cfg: dict) -> str:
    return "Configuración guardada (este tipo se prueba al usarlo)."


async def _anthropic(cfg):
    import anthropic
    c = anthropic.AsyncAnthropic(api_key=cfg["api_key"])
    m = await c.models.retrieve("claude-opus-5-5")
    return f"Clave válida. Modelo disponible: {m.display_name}"


async def _comfyui(cfg):
    from app.engines.comfy import ComfyEngine
    h = await ComfyEngine(cfg).health()
    if h.get("error"):
        raise RuntimeError(h["error"])
    gpus = ", ".join(f"{d['name']} ({d['vram_gb']} GB)" for d in h.get("devices", [])) or "sin GPU detectada"
    extra = ""
    if h.get("missing_models"):
        extra += f" Faltan modelos: {', '.join(h['missing_models'])}."
    if h.get("workflow_problems"):
        extra += f" Problemas: {'; '.join(h['workflow_problems'][:3])}."
    if extra:
        raise RuntimeError(f"Conecta ({gpus}) pero:{extra}")
    return f"Conectado. GPU: {gpus}. Modelos y workflows OK."


async def _get(url, **kw):
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"{r.status_code}: {r.text[:200]}")
    return r


async def _telegram(cfg):
    r = (await _get(f"https://api.telegram.org/bot{cfg['bot_token']}/getMe")).json()
    chat = (await _get(f"https://api.telegram.org/bot{cfg['bot_token']}/getChat", params={"chat_id": cfg["chat_id"]})).json()
    return f"Bot @{r['result']['username']} listo para publicar en {chat['result'].get('title') or chat['result'].get('username')}"


async def _graph(cfg, node_key):
    v = cfg.get("api_version") or "v21.0"
    r = (await _get(f"https://graph.facebook.com/{v}/{cfg[node_key]}",
                    params={"fields": "id,name,username", "access_token": cfg["access_token"]})).json()
    return f"Cuenta conectada: {r.get('username') or r.get('name') or r['id']}"


async def _instagram(cfg):
    msg = await _graph(cfg, "ig_user_id")
    try:
        await _get(cfg["public_base_url"].rstrip("/") + "/api/health")
    except Exception:
        return msg + " ⚠ La URL pública no responde: Instagram no podrá descargar las imágenes."
    return msg


async def _facebook(cfg):
    return await _graph(cfg, "page_id")


async def _discord(cfg):
    r = (await _get(cfg["webhook_url"])).json()
    return f"Webhook de Discord en el canal {r.get('channel_id')}"


async def _webhook(cfg):
    headers = cfg.get("headers") or {}
    if isinstance(headers, str):
        headers = json.loads(headers or "{}")
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(cfg["url"], json={"event": "test", "data": {"message": "Prueba de conexión"}}, headers=headers)
    if r.status_code >= 400:
        raise RuntimeError(f"{r.status_code}: {r.text[:200]}")
    return f"El destino respondió {r.status_code}"


async def _fal(cfg):
    if not cfg.get("api_key"):
        raise RuntimeError("Falta la API key")
    return "Clave guardada. fal.ai se valida en la primera generación (cada imagen tiene costo)."


async def _replicate(cfg):
    r = (await _get("https://api.replicate.com/v1/account", headers={"Authorization": f"Bearer {cfg['api_token']}"})).json()
    return f"Cuenta de Replicate: {r.get('username')}"


async def _openai(cfg):
    await _get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {cfg['api_key']}"})
    return "Clave de OpenAI válida"


async def _gemini(cfg):
    await _get("https://generativelanguage.googleapis.com/v1beta/models", headers={"x-goog-api-key": cfg["api_key"]})
    return "Clave de Gemini válida"


async def _openrouter(cfg):
    r = (await _get("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {cfg['api_key']}"})).json()
    d = r.get("data", {})
    limit = d.get("limit_remaining")
    return (f"Clave válida ({d.get('label') or 'OpenRouter'}). Modelo: {cfg.get('model') or 'anthropic/claude-sonnet-4.5'}"
            + (f". Saldo restante: ${limit:.2f}" if isinstance(limit, (int, float)) else ""))


async def _openrouter_image(cfg):
    from app.config import settings
    from app.llm import openrouter_config
    key = cfg.get("api_key") or openrouter_config()[0]
    if not key:
        raise RuntimeError("Sin clave: ponla aquí o en Conexiones → Cerebro (OpenRouter)")
    msg = await _openrouter({"api_key": key, "model": cfg.get("model") or settings.OPENROUTER_IMAGE_MODEL})
    return msg.replace("Modelo:", "Modelo de imagen:")


_TESTS = {"anthropic": _anthropic, "openrouter": _openrouter, "openrouter_image": _openrouter_image, "comfyui": _comfyui, "telegram": _telegram, "instagram": _instagram,
          "facebook": _facebook, "discord": _discord, "webhook_publisher": _webhook, "event_webhook": _webhook,
          "fal": _fal, "replicate": _replicate, "openai_images": _openai, "gemini_image": _gemini}
