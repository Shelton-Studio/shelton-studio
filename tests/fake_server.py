"""Servidor en MODO DEMO: motor de imagen simulado + Claude simulado. Sin GPU ni API key.

Sirve para probar la interfaz y para las pruebas de punta a punta:
    python tests/fake_server.py 8011
Incluye /test-hook, que guarda lo que le envían los webhooks (para verificar integraciones).
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="shelton_demo_"))

import test_chain  # noqa: E402  (configura motor mock + subagentes simulados)
from fastapi import Request  # noqa: E402

from app import llm  # noqa: E402
from app.agents import shelton  # noqa: E402

shelton.create = test_chain.FakeChief()
llm.create = test_chain.fake_create
llm.json_call = test_chain._logged_fake

from app.main import app  # noqa: E402

HOOK: list = []


@app.post("/test-hook")
async def test_hook(req: Request):
    HOOK.append(await req.json())
    return {"ok": True}


@app.get("/test-hook")
async def test_hook_get():
    return HOOK


@app.get("/test-hook/{_rest:path}")
async def test_hook_discord_like(_rest: str):
    return {"channel_id": "demo"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8011, log_level="warning")
