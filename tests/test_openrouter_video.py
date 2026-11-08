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


def test_auto_prefers_full_quality_models():
    """En automático elige la versión completa (Veo 3.1) antes que la 'fast', y Nano Banana Pro para imagen."""
    from app.engines.cloud import OpenRouterImageEngine
    vids = [{"id": "google/veo-3.1-fast"}, {"id": "google/veo-3.1"}, {"id": "openai/sora-2"}]
    imgs = [{"id": "google/gemini-2.5-flash-image", "architecture": {"output_modalities": ["image"]}},
            {"id": "openai/gpt-image-1", "architecture": {"output_modalities": ["image"]}},
            {"id": "google/gemini-3-pro-image-preview", "architecture": {"output_modalities": ["image", "text"]}}]

    def handler(request: httpx.Request):
        return httpx.Response(200, json={"data": imgs if "output_modalities" in str(request.url) else vids})
    old_v, old_i = settings.OPENROUTER_VIDEO_MODEL, settings.OPENROUTER_IMAGE_MODEL
    settings.OPENROUTER_VIDEO_MODEL = settings.OPENROUTER_IMAGE_MODEL = "auto"
    OpenRouterVideoEngine.transport = OpenRouterImageEngine.transport = httpx.MockTransport(handler)
    OpenRouterVideoEngine._models_cache = OpenRouterImageEngine._models_cache = None
    try:
        v = OpenRouterVideoEngine({"api_key": "k"})
        if hasattr(v, "_models_cache"):
            OpenRouterVideoEngine._models_cache = vids
        assert asyncio.run(v._pick())["id"] == "google/veo-3.1"
        i = OpenRouterImageEngine({"api_key": "k"})
        assert asyncio.run(i.resolve_model()) == "google/gemini-3-pro-image-preview"
        # Si el usuario fija un modelo, se respeta
        assert asyncio.run(OpenRouterImageEngine({"model": "openai/gpt-image-1"}).resolve_model()) == "openai/gpt-image-1"
    finally:
        settings.OPENROUTER_VIDEO_MODEL, settings.OPENROUTER_IMAGE_MODEL = old_v, old_i
        OpenRouterVideoEngine.transport = OpenRouterImageEngine.transport = None
        OpenRouterVideoEngine._models_cache = OpenRouterImageEngine._models_cache = None


def test_shelton_picks_model_per_request():
    """Shelton puede elegir un modelo concreto para un pedido; si no existe en el catálogo, queda el automático."""
    from app import model_picker
    from app.engines import engine_label, get_engine, reset_cache
    from app.engines.cloud import OpenRouterImageEngine
    assert model_picker.parse(model_picker.encode("a/img", "b/vid")) == {"image": "a/img", "video": "b/vid"}
    assert model_picker.encode(None, "") is None
    reset_cache()
    e = get_engine("video", "or:video=google/veo-3.1")
    assert e.name == "openrouter_video" and engine_label(e, "video") == "google/veo-3.1"
    e = get_engine("generate", "or:image=openai/gpt-image-1;video=google/veo-3.1")
    assert e.name == "openrouter_image" and asyncio.run(e.resolve_model()) == "openai/gpt-image-1"
    # sin modelo para ese rol: sigue la ruta normal
    assert get_engine("generate", "or:video=google/veo-3.1").name == get_engine("generate", None).name

    vids = [{"id": "google/veo-3.1"}, {"id": "openai/sora-2"}]
    imgs = [{"id": "google/gemini-3-pro-image-preview", "architecture": {"output_modalities": ["image"]}}]
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"data": imgs if "output_modalities" in str(request.url) else vids})
    OpenRouterVideoEngine.transport = OpenRouterImageEngine.transport = httpx.MockTransport(handler)
    OpenRouterVideoEngine._models_cache = OpenRouterImageEngine._models_cache = None
    old, old_key = model_picker.available, settings.OPENROUTER_API_KEY
    model_picker.available = lambda: True
    settings.OPENROUTER_API_KEY = "sk-test"
    try:
        cat = asyncio.run(model_picker.catalog())
        assert cat["auto"] == {"image": "google/gemini-3-pro-image-preview", "video": "google/veo-3.1"}, cat
        assert "Máximo realismo" in cat["image"][0]["tags"]
        choice, note = asyncio.run(model_picker.choice_for("no/existe", "openai/sora-2", None))
        assert choice == "or:video=openai/sora-2" and "no disponible" in note
        # Si el usuario fijó un motor en el chat, se respeta
        assert asyncio.run(model_picker.choice_for(None, "openai/sora-2", "conn:x"))[0] == "conn:x"
    finally:
        model_picker.available, settings.OPENROUTER_API_KEY = old, old_key
        OpenRouterVideoEngine.transport = OpenRouterImageEngine.transport = None
        OpenRouterVideoEngine._models_cache = OpenRouterImageEngine._models_cache = None
        reset_cache()
