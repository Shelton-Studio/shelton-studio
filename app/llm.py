"""Acceso a Claude. Un solo cliente asíncrono; cada llamada se registra a nombre de su agente."""
import json
import logging
import time

import anthropic
import httpx

from app.config import settings

log = logging.getLogger("shelton.llm")

# Si un filtro de seguridad rechaza la petición, la API la reintenta sola en el modelo recomendado.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

_client: anthropic.AsyncAnthropic | None = None
_client_key: str | None = None


class LLMError(RuntimeError):
    pass


def api_key() -> str:
    """Clave de Conexiones (si la configuraste en la web) o la de .env."""
    from app.connections import first_config
    cfg = first_config("anthropic")
    return (cfg or {}).get("api_key") or settings.ANTHROPIC_API_KEY


def openrouter_config() -> tuple[str, str]:
    """(clave, modelo) de OpenRouter: Conexiones o .env."""
    from app.connections import first_config
    cfg = first_config("openrouter") or {}
    return (cfg.get("api_key") or settings.OPENROUTER_API_KEY,
            cfg.get("model") or settings.OPENROUTER_MODEL)


def provider() -> str:
    """Anthropic directo si hay clave; si no, OpenRouter si está configurado."""
    if api_key():
        return "anthropic"
    return "openrouter" if openrouter_config()[0] else "anthropic"


def client() -> anthropic.AsyncAnthropic:
    global _client, _client_key
    key = api_key()
    if _client is None or key != _client_key:
        _client = anthropic.AsyncAnthropic(api_key=key or None, max_retries=4)
        _client_key = key
    return _client


def _log_run(agent: str, ok: bool, start: float, model: str, resp=None, error: str = "", summary: str = ""):
    from app.db import AgentRun, session
    usage = getattr(resp, "usage", None)
    tokens_in = (getattr(usage, "input_tokens", 0) or 0) + (getattr(usage, "cache_read_input_tokens", 0) or 0) \
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0) if usage else 0
    try:
        with session() as s:
            s.add(AgentRun(agent=agent, ok=ok, model=model, input_tokens=tokens_in,
                           output_tokens=(getattr(usage, "output_tokens", 0) or 0) if usage else 0,
                           duration_ms=int((time.monotonic() - start) * 1000), error=error[:1000], summary=summary[:300]))
    except Exception:
        log.exception("no se pudo registrar la ejecución")
    from app import memoria
    memoria.log_work(agent, summary, ok, model, error)
    from app import events
    events.publish({"type": "run", "agent": agent, "ok": ok})


async def create(agent: str = "shelton", betas_extra: list[str] | None = None, summary: str = "", **kwargs):
    """Llamada a un agente. Mientras dura, el agente aparece como «trabajando» en la web (panel En vivo)."""
    from app import working
    with working.track(agent, summary or "pensando"):
        return await _create_inner(agent, betas_extra, summary, **kwargs)


async def _create_inner(agent: str = "shelton", betas_extra: list[str] | None = None, summary: str = "", **kwargs):
    """messages.create con la ficha del agente, fallback por defecto, control de rechazos y registro."""
    from app.agents import registry
    kwargs.setdefault("model", registry.model(agent))
    kwargs.setdefault("max_tokens", 16000)
    oc = kwargs.get("output_config")
    if oc and "effort" in oc:
        kwargs["output_config"] = {**oc, "effort": registry.effort(agent, oc["effort"])}
    start = time.monotonic()
    if provider() == "openrouter":
        return await _create_openrouter(agent, summary, start, kwargs)
    try:
        resp = await client().beta.messages.create(betas=[FALLBACK_BETA, *(betas_extra or [])],
                                                   fallbacks="default", **kwargs)
    except anthropic.AuthenticationError as e:
        err = LLMError("Clave de Anthropic inválida o ausente (ponla en Conexiones o en .env)")
    except anthropic.RateLimitError:
        err = LLMError("Límite de uso de Claude alcanzado; reintenta en un momento")
    except anthropic.APIStatusError as e:
        err = LLMError(f"Error de la API de Claude ({e.status_code}): {e.message}")
    except anthropic.APIConnectionError:
        err = LLMError("Sin conexión con la API de Claude")
    else:
        if resp.stop_reason == "refusal":
            reason = getattr(resp.stop_details, "explanation", None) if resp.stop_details else None
            msg = f"Claude rechazó la petición{': ' + reason if reason else ''}"
            _log_run(agent, False, start, kwargs["model"], resp, msg, summary)
            raise LLMError(msg)
        _log_run(agent, True, start, kwargs["model"], resp, summary=summary)
        return resp
    _log_run(agent, False, start, kwargs["model"], None, str(err), summary)
    raise err


async def _create_openrouter(agent: str, summary: str, start: float, kwargs: dict):
    from app import openrouter
    key, default_model = openrouter_config()
    # Si la ficha del agente tiene un modelo de OpenRouter ("proveedor/modelo"), se respeta.
    model = kwargs["model"] if "/" in str(kwargs.get("model", "")) else default_model
    kwargs["model"] = model
    try:
        resp = await openrouter.create(api_key=key, **kwargs)
    except openrouter.ORError as e:
        msg = {401: "Clave de OpenRouter inválida (revisa Conexiones o .env)",
               402: "Sin saldo en OpenRouter: recarga créditos",
               429: "Límite de OpenRouter alcanzado; reintenta en un momento"}.get(e.status, f"OpenRouter ({e.status}): {e}")
        _log_run(agent, False, start, model, None, msg, summary)
        raise LLMError(msg) from e
    except httpx.HTTPError as e:
        _log_run(agent, False, start, model, None, str(e), summary)
        raise LLMError(f"Sin conexión con OpenRouter: {e}") from e
    _log_run(agent, True, start, model, resp, summary=summary)
    return resp


def text_of(resp) -> str:
    return "".join(b.text for b in resp.content if b.type == "text").strip()


async def json_call(system: str, content, schema: dict, effort: str = "low", max_tokens: int = 8000,
                    agent: str = "shelton", summary: str = "") -> dict:
    """Llamada con salida JSON garantizada por esquema."""
    resp = await create(
        agent=agent, summary=summary,
        system=system,
        max_tokens=max_tokens,
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": content}],
    )
    if resp.stop_reason == "max_tokens":
        raise LLMError("Respuesta de Claude cortada (max_tokens)")
    try:
        return json.loads(text_of(resp))
    except json.JSONDecodeError:
        pass
    from app.openrouter import extract_json
    try:
        return extract_json(text_of(resp))
    except json.JSONDecodeError:
        pass
    # Un reintento pidiendo explícitamente el formato (solo hace falta con algunos modelos vía OpenRouter).
    retry = await create(agent=agent, summary=summary + " (reintento JSON)", system=system + "\n\nResponde SOLO con JSON válido.",
                         max_tokens=max_tokens, output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
                         messages=[{"role": "user", "content": content}])
    try:
        return extract_json(text_of(retry))
    except json.JSONDecodeError as e:
        raise LLMError(f"El modelo devolvió JSON inválido: {e}") from e


def obj(properties: dict, required: list[str] | None = None) -> dict:
    """Atajo para esquemas JSON estrictos."""
    return {"type": "object", "properties": properties,
            "required": required if required is not None else list(properties), "additionalProperties": False}
