"""Dictado por voz: el audio que grabas en la web -> texto para el chat.

- Mide el volumen: si llegó muy bajito, avisa «habla un poco más alto» en vez de inventar.
- Transcribe con faster-whisper si está instalado (local), si no con un modelo de audio de OpenRouter.
- Si no se entendió bien, lo dice («no te entendí bien, ¿lo repites?») y no manda nada.
"""
from __future__ import annotations

import base64
import re
import subprocess
import tempfile
from pathlib import Path

import httpx

from app.config import settings

QUIET_DB = -42.0       # pico por debajo de esto = casi no se oye
transport = None       # las pruebas inyectan un transporte simulado
_whisper = None


def _to_mp3(data: bytes, filename: str) -> tuple[str, float, float]:
    """Convierte lo grabado a MP3 mono 16 kHz. Devuelve (ruta, pico dB, duración s)."""
    from app import video as vid
    ext = Path(filename or "voz.webm").suffix or ".webm"
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(data)
    out = tmp.name + ".mp3"
    try:
        vid._run(["-i", tmp.name, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "64k", out])
    finally:
        Path(tmp.name).unlink(missing_ok=True)
    p = subprocess.run([vid.FFMPEG, "-hide_banner", "-i", out, "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True)
    err = p.stderr.decode(errors="ignore")
    m = re.search(r"max_volume:\s*(-?[\d.]+) dB", err)
    peak = float(m.group(1)) if m else 0.0
    return out, peak, vid.duration(out)


def _local(path: str, language: str) -> dict | None:
    global _whisper
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    if _whisper is None:
        _whisper = WhisperModel(settings.WHISPER_MODEL, device="auto", compute_type="auto")
    segments, info = _whisper.transcribe(path, language=language[:2] or None, vad_filter=True)
    segs = list(segments)
    text = " ".join(s.text.strip() for s in segs).strip()
    conf = min([s.avg_logprob for s in segs] or [-9])
    return {"text": text, "clear": bool(text) and conf > -1.1, "issue": "" if text else "unclear", "engine": "whisper local"}


async def _cloud(path: str, language: str) -> dict:
    from app.llm import openrouter_config
    from app.openrouter import extract_json
    key = openrouter_config()[0]
    if not key:
        raise RuntimeError("Para dictar por voz hace falta la clave de OpenRouter (o instalar faster-whisper en el servidor)")
    b64 = base64.b64encode(Path(path).read_bytes()).decode()
    prompt = (f"Transcribe exactly what the person says (language: {language}). The person is giving orders to a "
              "content studio assistant. Answer ONLY JSON: {\"text\": transcription in the original language, "
              "\"clear\": true if you understood it well, false if it was mumbled, cut, noisy or you are unsure, "
              "\"issue\": \"\" or one of \"quiet\", \"noise\", \"unclear\", \"empty\"}. Never invent words.")
    body = {"model": settings.OPENROUTER_AUDIO_MODEL, "temperature": 0,
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt},
                                                      {"type": "input_audio", "input_audio": {"data": b64, "format": "mp3"}}]}]}
    body["usage"] = {"include": True}
    from app.consumo import measure, report_cost
    with measure("transcripcion", "openrouter", settings.OPENROUTER_AUDIO_MODEL):
        async with httpx.AsyncClient(timeout=90, transport=transport) as c:
            r = await c.post("https://openrouter.ai/api/v1/chat/completions", json=body,
                             headers={"Authorization": f"Bearer {key}", "X-Title": "Shelton Studio"})
        if r.status_code < 400:
            report_cost((r.json().get("usage") or {}).get("cost"))
    if r.status_code >= 400:
        raise RuntimeError(f"No pude transcribir (OpenRouter {r.status_code}): {r.text[:200]}")
    msg = r.json()["choices"][0]["message"]["content"]
    if isinstance(msg, list):
        msg = " ".join(p.get("text", "") for p in msg if isinstance(p, dict))
    try:
        data = extract_json(msg) or {}
    except Exception:  # noqa: BLE001
        data = {"text": str(msg or "").strip(), "clear": bool(str(msg or "").strip())}
    text = str(data.get("text") or "").strip()
    return {"text": text, "clear": bool(data.get("clear", bool(text))) and bool(text),
            "issue": data.get("issue") or ("" if text else "empty"), "engine": settings.OPENROUTER_AUDIO_MODEL}


MESSAGES = {
    "quiet": "Casi no te escuché 🙉 Habla un poco más alto o acércate al micrófono y repítelo.",
    "noise": "Hay mucho ruido de fondo y no te entendí bien. ¿Lo repites?",
    "unclear": "No te entendí muy bien. ¿Lo repites, un poco más despacio?",
    "empty": "No escuché nada. Mantén el botón del micrófono y habla.",
    "short": "Fue muy cortito y no alcancé a entender. ¿Lo dices otra vez?",
}


async def transcribe(data: bytes, filename: str = "voz.webm", language: str = "") -> dict:
    import asyncio
    language = language or settings.CONTENT_LANGUAGE or "es"
    path, peak, seconds = await asyncio.to_thread(_to_mp3, data, filename)
    try:
        if seconds < 0.4:
            return {"text": "", "clear": False, "issue": "short", "message": MESSAGES["short"], "peak_db": peak}
        if peak < QUIET_DB:
            return {"text": "", "clear": False, "issue": "quiet", "message": MESSAGES["quiet"], "peak_db": peak}
        res = await asyncio.to_thread(_local, path, language) if settings.WHISPER_LOCAL else None
        if res is None:
            res = await _cloud(path, language)
        if not res["clear"] and not res.get("issue"):
            res["issue"] = "unclear"
        res["message"] = "" if res["clear"] else MESSAGES.get(res["issue"], MESSAGES["unclear"])
        res["peak_db"] = peak
        return res
    finally:
        Path(path).unlink(missing_ok=True)
