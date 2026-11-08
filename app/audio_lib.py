"""Biblioteca de audios: WAV/MP3 que sube el equipo o que se sacan de un video (TikTok, Reels, posts propios).

Cada audio queda como archivo en data/media/ (se escucha y se descarga desde /media/...) y su ficha en
data/audio/<id>.json. Shelton puede ponerle cualquiera de estos audios a un post de imagen o video.
"""
from __future__ import annotations

import json
import re
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from app.config import settings

AUDIO_EXT = (".wav", ".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac", ".webm")
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi")
MAX_BYTES = 80 * 1024 * 1024


def _dir() -> Path:
    d = settings.DATA_DIR / "audio"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _clean(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s.\-áéíóúñÁÉÍÓÚÑ]", "", name or "")).strip()[:80] or "audio"


def is_audio(data: bytes, filename: str = "") -> bool:
    ext = Path(filename or "").suffix.lower()
    if ext in AUDIO_EXT and ext != ".webm":
        return True
    head = data[:12]
    return head[:4] == b"RIFF" and head[8:12] == b"WAVE" or head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2") \
        or head[:4] in (b"fLaC", b"OggS")


def is_video(data: bytes, filename: str = "") -> bool:
    return Path(filename or "").suffix.lower() in VIDEO_EXT or data[4:8] == b"ftyp"


def _info(aid: str) -> dict:
    p = _dir() / f"{aid}.json"
    if not p.exists():
        raise ValueError("Ese audio no existe en la biblioteca")
    d = json.loads(p.read_text(encoding="utf-8"))
    d["url"] = f"/media/{Path(d['path']).name}"
    return d


def get(aid: str) -> dict:
    return _info(aid)


def list_audio() -> list[dict]:
    out = []
    for p in sorted(_dir().glob("*.json"), reverse=True):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if Path(d.get("path", "")).exists():
                d["url"] = f"/media/{Path(d['path']).name}"
                out.append(d)
        except Exception:  # noqa: BLE001
            continue
    return sorted(out, key=lambda d: d.get("created", ""), reverse=True)


def _save(src_path: str, name: str, source: str, origin: str = "", keep_format: bool = True) -> dict:
    """Guarda (y si hace falta convierte a MP3) un archivo de audio o el audio de un video."""
    from app import video as vid
    aid = uuid.uuid4().hex[:12]
    ext = Path(src_path).suffix.lower()
    if keep_format and ext in (".wav", ".mp3"):
        out = settings.media_dir / f"audio_{aid}{ext}"
        out.write_bytes(Path(src_path).read_bytes())
        try:
            vid._run(["-i", str(out), "-f", "null", "-"])  # valida que sea audio legible
        except RuntimeError as e:
            out.unlink(missing_ok=True)
            raise ValueError("Ese archivo no es un audio válido (WAV/MP3 dañado o de otro tipo)") from e
    else:
        from app.voice import has_audio
        if not has_audio(src_path):
            raise ValueError("Ese archivo no tiene audio")
        out = settings.media_dir / f"audio_{aid}.mp3"
        try:
            vid._run(["-i", src_path, "-vn", "-ac", "2", "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "192k", str(out)])
        except RuntimeError as e:
            out.unlink(missing_ok=True)
            raise ValueError(f"No pude leer el audio: {str(e)[-200:]}") from e
    seconds = vid.duration(str(out))
    if seconds <= 0:
        out.unlink(missing_ok=True)
        raise ValueError("Ese archivo no tiene audio")
    d = {"id": aid, "name": _clean(name), "path": str(out), "duration": round(seconds, 2), "source": source,
         "origin": origin, "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    (_dir() / f"{aid}.json").write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    d["url"] = f"/media/{out.name}"
    return d


def save_upload(data: bytes, filename: str, name: str = "") -> dict:
    """Sube un WAV/MP3 (u otro audio, o un video del que se saca el audio)."""
    if not data:
        raise ValueError("El archivo está vacío")
    if len(data) > MAX_BYTES:
        raise ValueError("El archivo es muy grande (máximo 80 MB)")
    ext = Path(filename or "").suffix.lower() or (".wav" if data[:4] == b"RIFF" else ".mp3")
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(data)
    try:
        return _save(tmp.name, name or Path(filename or "audio").stem, "subido", filename or "")
    finally:
        Path(tmp.name).unlink(missing_ok=True)


def from_video(video_path: str, name: str, source: str = "video", origin: str = "") -> dict:
    """Saca el audio de un video (post propio, TikTok, Reel...) y lo guarda en la biblioteca."""
    return _save(video_path, name, source, origin, keep_format=False)


def rename(aid: str, name: str) -> dict:
    d = _info(aid)
    d["name"] = _clean(name)
    d.pop("url", None)
    (_dir() / f"{aid}.json").write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    return _info(aid)


def delete(aid: str) -> None:
    p = _dir() / f"{aid}.json"
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
        Path(d.get("path", "")).unlink(missing_ok=True)
        p.unlink()
