"""Generador de voz con OpenRouter (misma clave): elige el modelo más pro, la voz que encaja y devuelve WAV."""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
import imageio_ffmpeg

os.environ.setdefault("IMAGE_ENGINE", "mock")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="shelton_tts_"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import voice  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import init_db  # noqa: E402

ELEVEN = ["george", "sarah", "adam", "alice", "bella", "bill", "brian", "callum", "charlie", "chris", "daniel", "eric",
          "harry", "jessica", "laura", "liam", "lily", "matilda", "river", "roger", "will"]
MODELS = {"data": [
    {"id": "openai/gpt-4o-mini-tts-2025-12-15", "architecture": {"output_modalities": ["speech"]}, "supported_voices": ["alloy", "nova"]},
    {"id": "elevenlabs/eleven-v4-turbo", "architecture": {"output_modalities": ["speech"]}, "supported_voices": ELEVEN},
    {"id": "elevenlabs/eleven-v4", "architecture": {"output_modalities": ["speech"]}, "supported_voices": ELEVEN},
    {"id": "microsoft/mai-voice-2", "architecture": {"output_modalities": ["speech"]},
     "supported_voices": ["en-US-Harper:MAI-Voice-2", "en-US-Andrew:MAI-Voice-2"]},
]}


def _mp3() -> bytes:
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "v.mp3"
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "sine=frequency=220:duration=1.5", "-c:a", "libmp3lame", str(out)], check=True)
        return out.read_bytes()


MP3 = _mp3()


def _setup(handler):
    voice.OpenRouterTTS.transport = httpx.MockTransport(handler)
    voice.OpenRouterTTS._models = None
    voice.OpenRouterTTS.last_model = None


def test_best_model_voice_and_wav(monkeypatch):
    init_db()
    sent = []

    def handler(request: httpx.Request):
        if request.method == "GET":
            assert request.url.params["output_modalities"] == "speech"
            return httpx.Response(200, json=MODELS)
        sent.append(json.loads(request.content))
        return httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})
    _setup(handler)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "OPENROUTER_TTS_MODEL", "auto")
    try:
        eng = voice.OpenRouterTTS({})
        assert asyncio.run(eng.resolve())["id"] == "elevenlabs/eleven-v4"     # el más pro, no la versión turbo
        wav = asyncio.run(eng.synthesize("Hola, esto es Shelton.", "es",
                                         {"request": "voz femenina sensual", "speed": 0.85, "pitch_semitones": 0}))
        assert wav[:4] == b"RIFF" and len(wav) > 10000
        b = sent[-1]
        assert b["model"] == "elevenlabs/eleven-v4" and b["response_format"] == "mp3" and b["input"].startswith("Hola")
        assert b["voice"] in voice.FEMALE and "speed" not in b          # Eleven v4 no acepta velocidad
        asyncio.run(eng.synthesize("Hola", "es", {"request": "un señor anciano, voz grave"}))
        assert sent[-1]["voice"] in ("bill", "george", "roger")
        asyncio.run(eng.synthesize("Hola", "es", {"base_voice": "Liam"}))
        assert sent[-1]["voice"] == "liam"                                # la que eligió el Locutor
        # Modelo fijado a mano (otro proveedor) y velocidad
        eng2 = voice.OpenRouterTTS({"model": "microsoft/mai-voice-2"})
        asyncio.run(eng2.synthesize("Hola", "es", {"request": "hombre joven", "speed": 1.2}))
        assert sent[-1]["voice"] == "en-US-Andrew:MAI-Voice-2" and sent[-1]["speed"] == 1.2
    finally:
        voice.OpenRouterTTS.transport = None
        voice.OpenRouterTTS._models = None


def test_retry_without_speed_and_errors(monkeypatch):
    calls = []

    def handler(request: httpx.Request):
        if request.method == "GET":
            return httpx.Response(200, json=MODELS)
        b = json.loads(request.content)
        calls.append(b)
        if "speed" in b:
            return httpx.Response(400, json={"error": {"message": "speed not supported"}})
        return httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})
    _setup(handler)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")
    try:
        wav = asyncio.run(voice.OpenRouterTTS({"model": "openai/gpt-4o-mini-tts-2025-12-15"}).synthesize(
            "Hola", "es", {"speed": 1.3}))
        assert wav[:4] == b"RIFF" and "speed" in calls[0] and "speed" not in calls[1]
        _setup(lambda r: httpx.Response(200, json=MODELS) if r.method == "GET" else httpx.Response(402, text="no credits"))
        try:
            asyncio.run(voice.OpenRouterTTS({}).synthesize("Hola", "es", {}))
        except RuntimeError as e:
            assert "402" in str(e) and "eleven-v4" in str(e)
        else:
            raise AssertionError("debía fallar")
    finally:
        voice.OpenRouterTTS.transport = None
        voice.OpenRouterTTS._models = None


def test_route_follows_switch(monkeypatch):
    init_db()
    from app import engines
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "IMAGE_ENGINE", "openrouter")
    monkeypatch.setattr(engines, "infra_mode", lambda: "auto")
    assert voice.voice_route()[0] == "openrouter_tts" and "OpenRouter" in voice.voice_label()
    monkeypatch.setattr(engines, "infra_mode", lambda: "cloud")
    assert voice.voice_route()[0] == "openrouter_tts"
    monkeypatch.setattr(engines, "infra_mode", lambda: "local")
    assert voice.voice_route()[0] == "local_tts"
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(engines, "infra_mode", lambda: "cloud")
    from app import llm
    monkeypatch.setattr(llm, "openrouter_config", lambda: ("", ""))
    kind, _, label = voice.voice_route()
    assert kind == "" and "Falta" in label
