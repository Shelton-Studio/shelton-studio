"""Cerebro alternativo: OpenRouter (una sola clave para muchos modelos).

Traduce las llamadas de Shelton (formato de mensajes de Claude: texto, imágenes, herramientas, JSON, búsqueda web)
al formato de OpenRouter y devuelve una respuesta con la misma forma, para que el resto del código no cambie.
Si hay una clave de Anthropic configurada, se usa Anthropic directo; OpenRouter es el plan B.
"""
import json
import re
from types import SimpleNamespace

import httpx

URL = "https://openrouter.ai/api/v1/chat/completions"
_transport = None  # las pruebas inyectan aquí un transporte simulado


class ORError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ bloques con la forma de Anthropic
class _Block(SimpleNamespace):
    def model_dump(self, **_):
        return {k: v for k, v in self.__dict__.items() if v is not None}


def _text(t: str) -> _Block:
    return _Block(type="text", text=t)


def _tool_use(i: str, name: str, args: dict) -> _Block:
    return _Block(type="tool_use", id=i, name=name, input=args)


# ------------------------------------------------------------------ conversión de la petición
def _system_text(system) -> str:
    if not system:
        return ""
    if isinstance(system, str):
        return system
    return "\n\n".join(b.get("text", "") for b in system if isinstance(b, dict))


def _part(block: dict):
    t = block.get("type")
    if t == "text":
        return {"type": "text", "text": block["text"]}
    if t == "image":
        src = block.get("source", {})
        url = src.get("url") if src.get("type") == "url" else f"data:{src.get('media_type', 'image/jpeg')};base64,{src.get('data', '')}"
        return {"type": "image_url", "image_url": {"url": url}}
    return None


def _convert_messages(messages: list[dict], system: str) -> list[dict]:
    out = [{"role": "system", "content": system}] if system else []
    for m in messages:
        role, content = m["role"], m["content"]
        if role == "system":
            out.append({"role": "system", "content": content if isinstance(content, str) else _system_text(content)})
            continue
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if role == "assistant":
            text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
            calls = [{"id": b["id"], "type": "function",
                      "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False)}}
                     for b in content if b.get("type") == "tool_use"]
            msg = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
            continue
        # usuario: primero los resultados de herramientas (deben ir justo después de la llamada), luego el resto
        extra_parts = []
        for b in content:
            if b.get("type") == "tool_result":
                rc = b.get("content")
                if isinstance(rc, str):
                    text = rc
                else:
                    text = "\n".join(x.get("text", "") for x in (rc or []) if x.get("type") == "text")
                    extra_parts += [p for p in (_part(x) for x in (rc or []) if x.get("type") == "image") if p]
                out.append({"role": "tool", "tool_call_id": b["tool_use_id"],
                            "content": ("ERROR: " if b.get("is_error") else "") + (text or "(sin texto)")})
            else:
                p = _part(b)
                if p:
                    extra_parts.append(p)
        if extra_parts:
            if not any(p["type"] == "text" for p in extra_parts):
                extra_parts.insert(0, {"type": "text", "text": "Imágenes del resultado anterior:"})
            out.append({"role": "user", "content": extra_parts})
    return out


def add_cache_points(msgs: list[dict]) -> list[dict]:
    """Caché de instrucciones (Claude): las instrucciones y la conversación ya enviadas se guardan unos minutos y,
    al reenviarlas, cuestan ~10% en vez del 100%. La respuesta es idéntica: solo cambia el precio."""
    def mark(m: dict) -> None:
        c = m.get("content")
        if isinstance(c, str) and c:
            m["content"] = [{"type": "text", "text": c, "cache_control": {"type": "ephemeral"}}]
        elif isinstance(c, list):
            for part in reversed(c):
                if part.get("type") == "text":
                    part["cache_control"] = {"type": "ephemeral"}
                    break
    systems = [m for m in msgs if m["role"] == "system"]
    if systems:
        mark(systems[0])
    users = [m for m in msgs if m["role"] == "user"]
    if users:
        mark(users[-1])
    return msgs


def _convert_tools(tools: list[dict] | None) -> tuple[list[dict], list[dict]]:
    fns, plugins = [], []
    for t in tools or []:
        if str(t.get("type", "")).startswith("web_search"):
            plugins.append({"id": "web", "max_results": min(int(t.get("max_uses", 5)), 10)})
        elif "input_schema" in t:
            fns.append({"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                         "parameters": t["input_schema"]}})
    return fns, plugins


# ------------------------------------------------------------------ llamada
async def create(api_key: str, model: str, system=None, messages=None, tools=None, max_tokens: int = 8000,
                 output_config: dict | None = None, cache: bool = False, **_ignored) -> SimpleNamespace:
    """Misma firma útil que messages.create; ignora lo específico de Anthropic (thinking, betas, caché...)."""
    fns, plugins = _convert_tools(tools)
    fmt = (output_config or {}).get("format")
    sys_text = _system_text(system)
    body = {"model": model, "messages": _convert_messages(messages or [], sys_text), "max_tokens": min(max_tokens, 32000),
            "usage": {"include": True}}
    if cache and str(model).startswith("anthropic/"):
        add_cache_points(body["messages"])
    if fns:
        body["tools"] = fns
    if plugins:
        body["plugins"] = plugins
    if fmt and fmt.get("type") == "json_schema":
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "respuesta", "strict": True,
                                                                          "schema": fmt["schema"]}}
    headers = {"Authorization": f"Bearer {api_key}", "X-Title": "Shelton Studio", "HTTP-Referer": "https://localhost"}
    async with httpx.AsyncClient(timeout=600, transport=_transport) as c:
        r = await c.post(URL, json=body, headers=headers)
        if r.status_code == 400 and "response_format" in body:
            # Modelo sin salida estructurada: se pide el JSON por instrucciones y se valida al leerlo.
            body.pop("response_format")
            hint = ("Responde SOLO con un objeto JSON válido (sin texto alrededor ni ```), que cumpla este esquema:\n"
                    + json.dumps(fmt["schema"], ensure_ascii=False))
            if body["messages"] and body["messages"][0]["role"] == "system":
                sysm = body["messages"][0]
                sysm["content"] = _system_text(sysm["content"]) + "\n\n" + hint   # vuelve a texto simple (con o sin caché)
            else:
                body["messages"].insert(0, {"role": "system", "content": hint})
            r = await c.post(URL, json=body, headers=headers)
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("message", r.text)
        except Exception:
            msg = r.text
        raise ORError(r.status_code, str(msg)[:500])
    data = r.json()
    if data.get("error"):
        raise ORError(502, str(data["error"].get("message", data["error"]))[:500])
    choice = data["choices"][0]
    msg = choice.get("message", {})
    blocks = []
    text = msg.get("content") or ""
    if isinstance(text, list):
        text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
    if text.strip():
        blocks.append(_text(text))
    for tc in msg.get("tool_calls") or []:
        try:
            args = json.loads(tc["function"].get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        blocks.append(_tool_use(tc.get("id") or f"call_{len(blocks)}", tc["function"]["name"], args))
    finish = choice.get("finish_reason") or choice.get("native_finish_reason") or "stop"
    stop = "tool_use" if any(b.type == "tool_use" for b in blocks) else ("max_tokens" if finish == "length" else "end_turn")
    u = data.get("usage") or {}
    usage = SimpleNamespace(input_tokens=u.get("prompt_tokens", 0), output_tokens=u.get("completion_tokens", 0),
                            cache_read_input_tokens=0, cache_creation_input_tokens=0, cost=u.get("cost"),
                            cached_tokens=(u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    return SimpleNamespace(content=blocks, stop_reason=stop, stop_details=None, usage=usage,
                           model=data.get("model", model))


def extract_json(text: str) -> dict:
    """Lee JSON aunque venga dentro de ``` o con texto alrededor."""
    t = text.strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.S)
    if m:
        return json.loads(m.group(1))
    a, b = t.find("{"), t.rfind("}")
    if a >= 0 and b > a:
        return json.loads(t[a:b + 1])
    raise json.JSONDecodeError("sin JSON", t, 0)
