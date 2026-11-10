"""Consumo: cada llamada queda registrada con su costo (real, estimado o luz) y la GPU se lee en vivo."""
import asyncio
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

import httpx

os.environ.setdefault("IMAGE_ENGINE", "mock")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="shelton_consumo_"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import consumo, llm, openrouter  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import UsageEvent, init_db, session  # noqa: E402


def _events():
    with session() as s:
        return [(e.kind, e.provider, e.cost_source, round(e.cost_usd, 6), e.ok)
                for e in s.query(UsageEvent).order_by(UsageEvent.id)]


def _clear():
    with session() as s:
        s.query(UsageEvent).delete()


def test_real_estimated_and_local_costs():
    init_db()
    _clear()
    consumo.record("cerebro", "openrouter", "anthropic/claude-sonnet-4.5", cost_usd=0.0123, tokens_in=1000, tokens_out=100)
    consumo.record("cerebro", "anthropic", "claude-x-sonnet", tokens_in=1_000_000, tokens_out=0)   # estimado: 3 US$/M
    consumo.record("imagen", "fal", "flux")                                                         # tabla de precios
    consumo.record("imagen", "fal", "flux", ok=False)                                               # falló: no se cobra
    consumo.record("imagen", "comfyui", "z_image", seconds=3600, watts=300)                         # 0,3 kWh de luz
    ev = _events()
    assert ev[0] == ("cerebro", "openrouter", "real", 0.0123, True)
    assert ev[1][2:4] == ("estimado", 3.0)
    assert ev[2][2:4] == ("estimado", 0.03)
    assert ev[3][2:5] == ("estimado", 0.0, False)
    assert ev[4][2] == "local" and abs(ev[4][3] - 0.3 * consumo.get_settings()["kwh_price"]) < 1e-6

    s = consumo.summary(7)
    assert s["totals"]["events"] == 5 and s["totals"]["failed"] == 1
    assert abs(s["totals"]["cloud_real"] - 0.0123) < 1e-6
    assert {k["name"] for k in s["by_kind"]} == {"cerebro", "imagen"}
    assert any(p["name"] == "OpenRouter" for p in s["by_provider"])


def test_engines_are_metered_and_report_real_cost():
    init_db()
    _clear()

    class FakeCloud:
        name = "openrouter_image"
        last_model = ""

        async def generate(self, prompt, w, h, seed, on_progress=None):
            self.last_model = "google/gemini-2.5-flash-image"
            consumo.report_cost(0.039)
            return b"img"

        async def health(self):
            return {"ok": True}

    eng = consumo.metered(FakeCloud())
    assert eng.name == "openrouter_image" and consumo.metered(eng) is eng
    assert asyncio.run(eng.generate("x", 1, 1, 1)) == b"img"
    assert asyncio.run(eng.health()) == {"ok": True}               # lo demás pasa igual, sin registrar
    with session() as s:
        e = s.query(UsageEvent).one()
        assert (e.kind, e.cost_source, e.cost_usd, e.model) == ("imagen", "real", 0.039, "google/gemini-2.5-flash-image")


def test_openrouter_brain_saves_real_cost():
    init_db()
    _clear()

    def handler(request):
        body = json.loads(request.content)
        assert body["usage"] == {"include": True}
        return httpx.Response(200, json={"choices": [{"message": {"content": "hola"}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 120, "completion_tokens": 30, "cost": 0.00081}})
    openrouter._transport = httpx.MockTransport(handler)
    old = (settings.ANTHROPIC_API_KEY, settings.OPENROUTER_API_KEY)
    settings.ANTHROPIC_API_KEY, settings.OPENROUTER_API_KEY = "", "sk-or-test"
    try:
        asyncio.run(llm.create(agent="copywriter", messages=[{"role": "user", "content": "hola"}]))
    finally:
        openrouter._transport = None
        settings.ANTHROPIC_API_KEY, settings.OPENROUTER_API_KEY = old
    with session() as s:
        e = s.query(UsageEvent).one()
        assert (e.kind, e.provider, e.agent, e.cost_source, e.cost_usd, e.tokens_in, e.tokens_out) == \
            ("cerebro", "openrouter", "copywriter", "real", 0.00081, 120, 30)


def test_gpu_live_reads_nvidia_smi(monkeypatch, tmp_path):
    fake = tmp_path / "nvidia-smi"
    fake.write_text('#!/bin/bash\ncase "$*" in\n'
                    '  *query-gpu*) echo "0, NVIDIA GeForce RTX 3090, 97, 20000, 24576, 340.5, 350.00, 70, 00000000:01:00.0";;\n'
                    '  *query-compute-apps*) echo "999999, 18000, 00000000:01:00.0";;\nesac\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    g = asyncio.run(consumo.gpu_now())
    assert g["source"] == "nvidia-smi"
    x = g["gpus"][0]
    assert x["name"] == "NVIDIA GeForce RTX 3090" and x["util"] == 97 and x["power_w"] == 340.5
    assert sum(x["who"].values()) == 18000
