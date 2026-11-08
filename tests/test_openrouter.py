"""Cerebro vía OpenRouter: se simulan las respuestas del servidor y se usa el código real de Shelton."""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import httpx

os.environ.setdefault("IMAGE_ENGINE", "mock")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="shelton_or_"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import llm, openrouter  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import init_db  # noqa: E402

REQUESTS: list[dict] = []


def use(handler):
    def wrapped(request: httpx.Request):
        body = json.loads(request.content)
        REQUESTS.append(body)
        return handler(body)
    openrouter._transport = httpx.MockTransport(wrapped)


def ok(message: dict, finish="stop"):
    return httpx.Response(200, json={"model": "anthropic/claude-sonnet-4.5", "choices": [
        {"message": message, "finish_reason": finish}], "usage": {"prompt_tokens": 100, "completion_tokens": 20}})


def setup_module():
    init_db()
    settings.ANTHROPIC_API_KEY = ""
    settings.OPENROUTER_API_KEY = "sk-or-test"
    REQUESTS.clear()


def teardown_module():
    openrouter._transport = None
    settings.OPENROUTER_API_KEY = ""


def test_provider_and_json_with_image():
    assert llm.provider() == "openrouter"
    use(lambda b: ok({"content": '```json\n{"score": 88, "notes": "bien"}\n```'}))
    img = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "AAAA"}}
    out = asyncio.run(llm.json_call("Juez", [img, {"type": "text", "text": "evalúa"}],
                                    llm.obj({"score": {"type": "integer"}, "notes": {"type": "string"}}), agent="judge"))
    assert out == {"score": 88, "notes": "bien"}
    req = REQUESTS[-1]
    assert req["model"] == "anthropic/claude-sonnet-4.5" and req["response_format"]["type"] == "json_schema"
    assert req["messages"][0] == {"role": "system", "content": "Juez"}
    parts = req["messages"][1]["content"]
    assert parts[0]["type"] == "image_url" and parts[0]["image_url"]["url"] == "data:image/jpeg;base64,AAAA"


def test_retry_without_structured_output():
    def handler(b):
        if "response_format" in b:
            return httpx.Response(400, json={"error": {"message": "response_format not supported"}})
        assert "Responde SOLO con un objeto JSON" in b["messages"][0]["content"]
        return ok({"content": 'Aquí está: {"a": 1}'})
    use(handler)
    out = asyncio.run(llm.json_call("Sistema", "hola", llm.obj({"a": {"type": "integer"}})))
    assert out == {"a": 1}


def test_chief_tool_loop():
    from app.agents import shelton
    shelton.create = llm.create  # por si otra prueba lo reemplazó

    def handler(b):
        last = b["messages"][-1]
        if last["role"] == "tool":
            assert last["tool_call_id"] == "call_1" and ("banco" in last["content"].lower() or '"kind"' in last["content"])
            assert any(m.get("tool_calls") for m in b["messages"] if m["role"] == "assistant")
            return ok({"content": "Tu banco está vacío. ¿Creo a Valentina?"})
        assert any(t["function"]["name"] == "create_posts" for t in b["tools"])
        return ok({"content": None, "tool_calls": [{"id": "call_1", "type": "function",
                                                     "function": {"name": "list_subjects", "arguments": "{}"}}]}, "tool_calls")
    use(handler)

    async def run():
        return [ev async for ev in shelton.chat(None, "¿qué modelos tengo?")]
    events = asyncio.run(run())
    assert not [e for e in events if e["type"] == "error"], events
    msg = next(e for e in events if e["type"] == "message")
    assert "Valentina" in msg["text"]


def test_web_search_uses_plugin():
    from app.agents import specialists
    use(lambda b: ok({"content": "Opciones: Pagani Utopia (fuente: https://example.com)"}))
    out = asyncio.run(specialists.research("autos exóticos"))
    assert "Pagani" in out
    assert REQUESTS[-1]["plugins"][0]["id"] == "web" and "tools" not in REQUESTS[-1]


def test_clear_errors():
    use(lambda b: httpx.Response(402, json={"error": {"message": "Insufficient credits"}}))
    try:
        asyncio.run(llm.json_call("x", "y", llm.obj({"a": {"type": "integer"}})))
    except llm.LLMError as e:
        assert "saldo" in str(e).lower()
    else:
        raise AssertionError("debía fallar")
