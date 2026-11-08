"""Video vía OpenRouter: lista de modelos, elección automática, envío, consulta de estado y descarga (simulados)."""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import httpx

os.environ.setdefault("IMAGE_ENGINE", "mock")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="shelton_orv_"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db import init_db  # noqa: E402
from app.engines.cloud import OpenRouterVideoEngine  # noqa: E402

MODELS = {"data": [
    {"id": "alibaba/wan-2.7", "supported_durations": [5, 10], "supported_aspect_ratios": ["16:9", "9:16"]},
    {"id": "google/veo-3.1", "supported_durations": [4, 6, 8], "supported_resolutions": ["720p", "1080p"],
     "supported_aspect_ratios": ["16:9", "9:16"]},
    {"id": "bytedance/seedance-2.0", "supported_durations": [5, 10]},
]}


def test_openrouter_video_flow():
    init_db()
    settings.ANTHROPIC_API_KEY = ""
    settings.OPENROUTER_API_KEY = "sk-or-test"
    OpenRouterVideoEngine._models_cache = None
    calls = []

    def handler(request: httpx.Request):
        calls.append((request.method, request.url.path))
        assert request.headers["authorization"] == "Bearer sk-or-test"
        if request.url.path.endswith("/videos/models"):
            return httpx.Response(200, json=MODELS)
        if request.method == "POST" and request.url.path.endswith("/videos"):
            body = json.loads(request.content)
            assert body["model"] == "google/veo-3.1"                       # auto elige el mejor
            assert body["duration"] == 6 and body["resolution"] == "1080p" and body["aspect_ratio"] == "9:16"
            fi = body["frame_images"][0]
            assert fi["frame_type"] == "first_frame" and fi["image_url"]["url"].startswith("data:image/png;base64,")
            return httpx.Response(200, json={"id": "job1", "status": "pending",
                                             "polling_url": "https://openrouter.ai/api/v1/videos/job1"})
        if request.url.path.endswith("/videos/job1"):
            n = sum(1 for m, p in calls if p.endswith("/videos/job1"))
            if n < 2:
                return httpx.Response(200, json={"id": "job1", "status": "in_progress"})
            return httpx.Response(200, json={"id": "job1", "status": "completed",
                                             "unsigned_urls": ["https://openrouter.ai/api/v1/videos/job1/content?index=0"]})
        if request.url.path.endswith("/videos/job1/content"):
            return httpx.Response(200, content=b"\x00\x00\x00\x18ftypmp42FAKEVIDEO")
        raise AssertionError(request.url)

    OpenRouterVideoEngine.transport = httpx.MockTransport(handler)
    import app.engines.cloud as cloud
    real_sleep = cloud.asyncio.sleep

    async def fast_sleep(_):
        await real_sleep(0)
    cloud.asyncio.sleep = fast_sleep
    try:
        eng = OpenRouterVideoEngine({})
        data, fps = asyncio.run(eng.video(b"PNGDATA", "slow push-in", "blur", 1080, 1920, 1))
        assert data[4:8] == b"ftyp" and fps == 30
        # Modelo elegido a mano
        OpenRouterVideoEngine._models_cache = None
        calls.clear()
        eng2 = OpenRouterVideoEngine({"model": "bytedance/seedance-2.0"})
        assert asyncio.run(eng2._pick())["id"] == "bytedance/seedance-2.0"
        # Etiquetas del catálogo
        from app.api import _video_info
        assert "Mejor calidad" in _video_info({"id": "google/veo-3.1"})["tags"]
        assert "Personajes consistentes" in _video_info({"id": "bytedance/seedance-2.0"})["tags"]
        # Con IMAGE_ENGINE=openrouter el rol video va a OpenRouter
        from app.engines import get_engine, reset_cache
        settings.IMAGE_ENGINE = "openrouter"
        reset_cache()
        assert get_engine("video").name == "openrouter_video"
    finally:
        cloud.asyncio.sleep = real_sleep
        OpenRouterVideoEngine.transport = None
        OpenRouterVideoEngine._models_cache = None
        settings.IMAGE_ENGINE = "mock"
        settings.OPENROUTER_API_KEY = ""
