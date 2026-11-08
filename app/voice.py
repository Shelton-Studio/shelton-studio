"""Voz: motores de texto a voz + efectos de voz con ffmpeg + mezcla con video.

Motores:
  local      -> servidor TTS local (tts_server/, Chatterbox Multilingual: español, clonación con 5-10 s de muestra)
  elevenlabs -> nube (conexión opcional)
  http_tts   -> cualquier API de voz (conexión genérica)
  mock       -> tono de prueba (modo demo)

El "diseño" de la voz (grave, aguda, femenina, sensual, adulta, anciana, robot...) se traduce a:
  - parámetros del motor (expresividad, ritmo)
  - efectos: tono en semitonos, velocidad, calidez, temblor, robot, eco
  - y, si existe, una muestra de voz de referencia (banco de voces o la voz del modelo del banco)
"""
import base64
import io
import json
import math
import struct
import subprocess
import tempfile
import wave
from pathlib import Path

import httpx

from app.config import settings
from app.video import FFMPEG, _ENCODE, _run, duration


def voices_dir() -> Path:
    d = settings.DATA_DIR / "voices"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_voices() -> list[dict]:
    """Banco de voces: muestras .wav/.mp3 que subes (5-15 s, una sola persona hablando, sin música)."""
    out = []
    for p in sorted(voices_dir().iterdir()):
        if p.suffix.lower() in (".wav", ".mp3", ".m4a", ".ogg", ".flac"):
            meta = p.with_suffix(".json")
            info = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}
            out.append({"name": p.stem, "file": p.name, "description": info.get("description", ""),
                        "url": f"/voices/{p.name}"})
    return out


# ------------------------------------------------------------------ Motores
def voice_label() -> str:
    """Qué voz se usaría ahora (para la etiqueta «Modo audio»)."""
    from app.connections import list_enabled
    from app.engines import infra_mode
    mode = infra_mode()
    voices = list_enabled("voice")
    if mode == "local":
        voices = [c for c in voices if c.type == "local_tts"]
    elif mode == "cloud":
        voices = [c for c in voices if c.type != "local_tts"]
    if voices:
        return voices[0].name
    return "Simulado (demo)" if settings.IMAGE_ENGINE == "mock" else "Chatterbox (local)"


async def synthesize(text: str, language: str, ref_audio: bytes | None, spec: dict) -> bytes:
    """Devuelve WAV. Elige el motor según Conexiones (rol 'voice') o .env."""
    from app.connections import list_enabled
    from app.engines import infra_mode
    mode = infra_mode()
    voices = list_enabled("voice")
    if mode == "local":
        voices = [c for c in voices if c.type == "local_tts"]
    elif mode == "cloud":
        voices = [c for c in voices if c.type != "local_tts"]
        if not voices and settings.IMAGE_ENGINE != "mock":
            raise RuntimeError("Modo Nube: falta una voz en la nube (ElevenLabs u otra API de voz). Agrégala en el botón APIs.")
    conn = voices[0] if voices else None
    kind = conn.type if conn else ("mock" if settings.IMAGE_ENGINE == "mock" else "local_tts")
    cfg = dict(conn.config) if conn else {"url": settings.TTS_URL}
    if kind == "mock":
        return _mock_wav(text)
    if kind == "local_tts":
        return await _local(cfg, text, language, ref_audio, spec)
    if kind == "elevenlabs":
        return await _elevenlabs(cfg, text, spec)
    if kind == "http_tts":
        return await _http(cfg, text, language, ref_audio, spec)
    raise ValueError(f"Motor de voz desconocido: {kind}")


async def _local(cfg, text, language, ref_audio, spec) -> bytes:
    body = {"text": text, "language": language, "exaggeration": spec.get("exaggeration", 0.5),
            "cfg_weight": spec.get("cfg_weight", 0.5)}
    if ref_audio:
        body["ref_audio_b64"] = base64.standard_b64encode(ref_audio).decode()
    url = (cfg.get("url") or settings.TTS_URL).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=600) as c:
            r = await c.post(f"{url}/tts", json=body)
    except httpx.ConnectError:
        raise RuntimeError(f"No conecta con el servidor de voz en {url}. Arráncalo con: python tts_server/server.py")
    if r.status_code >= 400:
        raise RuntimeError(f"Servidor de voz {r.status_code}: {r.text[:300]}")
    return r.content


async def _elevenlabs(cfg, text, spec) -> bytes:
    voice = spec.get("elevenlabs_voice_id") or cfg.get("voice_id") or "21m00Tcm4TlvDq8ikWAM"
    async with httpx.AsyncClient(timeout=300) as c:
        r = await c.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=pcm_44100",
                         headers={"xi-api-key": cfg["api_key"]},
                         json={"text": text, "model_id": cfg.get("model") or "eleven_multilingual_v2",
                               "voice_settings": {"stability": max(0.0, 1 - spec.get("exaggeration", 0.5)),
                                                  "similarity_boost": 0.8,
                                                  "style": min(1.0, spec.get("exaggeration", 0.5))}})
    if r.status_code >= 400:
        raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:300]}")
    return _pcm_to_wav(r.content, 44100)


async def _http(cfg, text, language, ref_audio, spec) -> bytes:
    tpl = cfg.get("body") or '{"text": "{{text}}"}'
    tpl = tpl if isinstance(tpl, str) else json.dumps(tpl)
    values = {"text": text, "language": language, "ref_audio_b64": base64.standard_b64encode(ref_audio).decode() if ref_audio else ""}
    body = json.loads(tpl.replace("{{text}}", json.dumps(text)[1:-1]).replace("{{language}}", language)
                      .replace("{{ref_audio_b64}}", values["ref_audio_b64"]))
    headers = cfg.get("headers") or {}
    headers = json.loads(headers) if isinstance(headers, str) and headers.strip() else (headers or {})
    async with httpx.AsyncClient(timeout=600) as c:
        r = await c.post(cfg["url"], json=body, headers=headers)
    if r.status_code >= 400:
        raise RuntimeError(f"API de voz {r.status_code}: {r.text[:300]}")
    if r.headers.get("content-type", "").startswith("audio/"):
        return r.content
    data = r.json()
    for part in [p for p in (cfg.get("result_path") or "audio").split(".") if p]:
        data = data[int(part)] if isinstance(data, list) else data[part]
    return base64.b64decode(data)


def _pcm_to_wav(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _mock_wav(text: str) -> bytes:
    """Modo demo: un tono suave con la duración aproximada de la locución (~14 caracteres por segundo)."""
    rate, secs = 22050, max(1.5, min(20.0, len(text) / 14))
    frames = b"".join(struct.pack("<h", int(3000 * math.sin(2 * math.pi * 220 * i / rate) * (0.5 + 0.5 * math.sin(i / 900))))
                      for i in range(int(rate * secs)))
    return _pcm_to_wav(frames, rate)


# ------------------------------------------------------------------ Efectos
def _atempo_chain(factor: float) -> list[str]:
    out = []
    while factor > 2.0:
        out.append("atempo=2.0")
        factor /= 2.0
    while factor < 0.5:
        out.append("atempo=0.5")
        factor /= 0.5
    out.append(f"atempo={factor:.4f}")
    return out


def fx_chain(spec: dict) -> str:
    """Traduce el diseño de voz a filtros de ffmpeg."""
    f = []
    semis = float(spec.get("pitch_semitones", 0) or 0)
    speed = float(spec.get("speed", 1.0) or 1.0)
    if abs(semis) > 0.05:
        ratio = 2 ** (semis / 12)
        f.append(f"asetrate=44100*{ratio:.5f}")
        f.append("aresample=44100")
        f += _atempo_chain(speed / ratio)
    elif abs(speed - 1) > 0.01:
        f += _atempo_chain(speed)
    if spec.get("warmth"):
        f += ["equalizer=f=180:t=q:w=1.2:g=4", "lowpass=f=9000"]
    if spec.get("breathy"):
        f += ["highshelf=f=6000:g=2", "acompressor=threshold=-20dB:ratio=3"]
    if float(spec.get("tremolo", 0) or 0) > 0:
        f.append(f"tremolo=f=5.5:d={min(0.6, float(spec['tremolo'])):.2f}")
    if spec.get("robot"):
        f.append("afftfilt=real='hypot(re,im)*sin(0)':imag='hypot(re,im)*cos(0)':win_size=512:overlap=0.75")
        f.append("aecho=0.8:0.6:12:0.4")
    if float(spec.get("reverb", 0) or 0) > 0:
        r = min(0.9, float(spec["reverb"]))
        f.append(f"aecho=0.8:{0.4 + r / 3:.2f}:60|120:{r / 2:.2f}|{r / 3:.2f}")
    f += ["loudnorm=I=-16:TP=-1.5:LRA=11"]
    return ",".join(f)


def apply_fx(wav: bytes, spec: dict, out_path: str) -> str:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(wav)
    try:
        _run(["-i", tmp.name, "-af", f"aresample=44100,{fx_chain(spec)}", "-ar", "44100", "-ac", "1", out_path])
    finally:
        Path(tmp.name).unlink(missing_ok=True)
    return out_path


# ------------------------------------------------------------------ Mezcla con imagen / video
def still_to_video(image_path: str, audio_path: str, out_path: str, width: int, height: int, fps: int = 30) -> str:
    """Imagen + voz -> video con movimiento lento de cámara (Ken Burns) del largo de la locución."""
    secs = duration(audio_path) + 0.6
    frames = int(secs * fps)
    zoom = (f"zoompan=z='min(zoom+0.0007,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:"
            f"s={width}x{height}:fps={fps}")
    _run(["-loop", "1", "-i", image_path, "-i", audio_path,
          "-filter_complex", f"[0:v]scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
                             f"crop={width * 2}:{height * 2},{zoom},format=yuv420p[v]",
          "-map", "[v]", "-map", "1:a", "-t", f"{secs:.2f}",
          *[x for x in _ENCODE if x != "-an"], "-c:a", "aac", "-b:a", "192k", out_path])
    return out_path


def mux(video_path: str, audio_path: str, out_path: str, keep_original_audio: bool = False) -> str:
    """Pone la voz sobre el video. Si la voz dura más, congela el último cuadro hasta que termine."""
    vd, ad = duration(video_path), duration(audio_path)
    pad = max(0.0, ad - vd + 0.3)
    vf = f"[0:v]tpad=stop_mode=clone:stop_duration={pad:.2f}[v]" if pad > 0 else "[0:v]null[v]"
    _run(["-i", video_path, "-i", audio_path, "-filter_complex", vf, "-map", "[v]", "-map", "1:a",
          *[x for x in _ENCODE if x != "-an"], "-c:a", "aac", "-b:a", "192k", "-shortest" if pad == 0 else "-y", out_path])
    return out_path


def has_audio(path: str) -> bool:
    p = subprocess.run([FFMPEG, "-hide_banner", "-i", path], capture_output=True)
    return "Audio:" in p.stderr.decode(errors="ignore")
