"""Acceso a Claude. Un solo cliente asíncrono para todos los agentes."""
import json
import logging

import anthropic

from app.config import settings

log = logging.getLogger("shelton.llm")

# Si un filtro de seguridad rechaza la petición, la API la reintenta sola en el modelo recomendado.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

_client: anthropic.AsyncAnthropic | None = None


class LLMError(RuntimeError):
    pass


def client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY or None, max_retries=4)
    return _client


async def create(betas_extra: list[str] | None = None, **kwargs):
    """messages.create con fallback por defecto y control de rechazos."""
    kwargs.setdefault("model", settings.CLAUDE_MODEL)
    kwargs.setdefault("max_tokens", 16000)
    try:
        resp = await client().beta.messages.create(betas=[FALLBACK_BETA, *(betas_extra or [])],
                                                   fallbacks="default", **kwargs)
    except anthropic.AuthenticationError as e:
        raise LLMError("ANTHROPIC_API_KEY inválida o ausente (revisa .env)") from e
    except anthropic.RateLimitError as e:
        raise LLMError("Límite de uso de Claude alcanzado; reintenta en un momento") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"Error de la API de Claude ({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError("Sin conexión con la API de Claude") from e
    if resp.stop_reason == "refusal":
        reason = getattr(resp.stop_details, "explanation", None) if resp.stop_details else None
        raise LLMError(f"Claude rechazó la petición{': ' + reason if reason else ''}")
    return resp


def text_of(resp) -> str:
    return "".join(b.text for b in resp.content if b.type == "text").strip()


async def json_call(system: str, content, schema: dict, effort: str = "low", max_tokens: int = 8000) -> dict:
    """Llamada con salida JSON garantizada por esquema."""
    resp = await create(
        system=system,
        max_tokens=max_tokens,
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": content}],
    )
    if resp.stop_reason == "max_tokens":
        raise LLMError("Respuesta de Claude cortada (max_tokens)")
    try:
        return json.loads(text_of(resp))
    except json.JSONDecodeError as e:
        raise LLMError(f"Claude devolvió JSON inválido: {e}") from e


def obj(properties: dict, required: list[str] | None = None) -> dict:
    """Atajo para esquemas JSON estrictos."""
    return {"type": "object", "properties": properties,
            "required": required if required is not None else list(properties), "additionalProperties": False}
