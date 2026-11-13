"""Traducción español → inglés en la propia placa (sin IA en la nube): SDXL y Flux entienden casi solo inglés.

Busca un Python que tenga `ctranslate2` y `sentencepiece` (el de Shelton o el de ComfyUI), baja una vez el modelo
abierto de traducción y lo usa en la CPU. Si todavía no está listo, usa un glosario básico para no frenar nada.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

from app.config import settings

log = logging.getLogger("shelton.traductor")
SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "traducir.py"
TIMEOUT = 90
_python: str | None = None
_checked = False
_preparing = False

SPANISH_RE = re.compile(
    r"[ñáéíóú¿¡]|\b(de|la|el|los|las|una?|con|en|sin|muy|mujer|chica|foto|cuerpo|pelo|piel|luz|cama|playa|"
    r"desnud[ao]s?|sentad[ao]|parad[ao]|acostad[ao]|mirando|fondo|ventana|habitaci[oó]n|largo|corto|rubi[ao]|"
    r"moren[ao]|ojos|labios|sonrisa|vestid[ao]|negr[ao]|blanc[ao]|roj[ao])\b", re.IGNORECASE)

# Respaldo mínimo mientras el traductor de verdad no está listo (palabras frecuentes en pedidos de fotos)
GLOSSARY = {
    "mujer": "woman", "chica": "young woman", "adulta": "adult", "desnuda": "nude", "desnudo": "nude",
    "cuerpo entero": "full body", "cuerpo": "body", "de pie": "standing", "sentada": "sitting",
    "acostada": "lying down", "recostada": "reclining", "de espaldas": "from behind", "mirando a la cámara":
    "looking at camera", "pelo": "hair", "cabello": "hair", "largo": "long", "corto": "short", "ondulado": "wavy",
    "rubia": "blonde", "morena": "brunette", "pelirroja": "redhead", "piel": "skin", "bronceada": "tanned",
    "ojos": "eyes", "labios": "lips", "sonrisa": "smile", "habitación": "bedroom", "dormitorio": "bedroom",
    "cama": "bed", "sábanas blancas": "white sheets", "playa": "beach", "ducha": "shower", "baño": "bathroom",
    "ventana": "window", "luz natural": "natural light", "luz suave": "soft light", "atardecer": "sunset",
    "fondo desenfocado": "blurred background", "curvilínea": "curvy", "delgada": "slim", "tonificada": "toned",
    "piernas": "legs", "espalda": "back", "lencería": "lingerie", "tatuajes": "tattoos", "ropa": "clothes",
    "realista": "realistic", "fotografía profesional": "professional photography", "en": "in", "con": "with",
    "y": "and", "de": "of", "una": "a", "un": "a", "la": "the", "el": "the",
}


def looks_spanish(text: str) -> bool:
    return len(SPANISH_RE.findall(text or "")) >= 2


def _candidates() -> list[str]:
    comfy = Path(os.getenv("COMFY_DIR", str(Path.home() / "ComfyUI"))) / "venv" / "bin" / "python"
    return [sys.executable, str(comfy)]


def find_python() -> str | None:
    """Un Python con ctranslate2 y sentencepiece (se revisa una vez)."""
    global _python, _checked
    if _checked:
        return _python
    for py in _candidates():
        if not Path(py).exists():
            continue
        try:
            r = subprocess.run([py, "-c", "import ctranslate2, sentencepiece"], capture_output=True, timeout=60)
        except Exception:  # noqa: BLE001
            continue
        if r.returncode == 0:
            _python = py
            break
    _checked = True
    return _python


def model_dir() -> Path:
    return settings.DATA_DIR / "traductor"


def model_ready() -> bool:
    return any(p.parent.joinpath("model", "model.bin").exists() for p in model_dir().rglob("sentencepiece.model"))


def status() -> dict:
    py = find_python()
    if not py:
        return {"ok": False, "why": "Falta instalar el traductor (corre el instalador de nuevo)"}
    if not model_ready():
        return {"ok": False, "why": "Bajando el modelo de traducción (una sola vez)…" if _preparing else
                "Falta el modelo de traducción: se baja solo al arrancar Shelton"}
    return {"ok": True, "why": ""}


async def prepare() -> None:
    """Al arrancar: si hay Python con las librerías y falta el modelo, lo baja (una vez, en segundo plano)."""
    global _preparing
    py = await asyncio.to_thread(find_python)
    if not py or model_ready():
        return
    _preparing = True
    try:
        proc = await asyncio.create_subprocess_exec(py, str(SCRIPT), "--setup", "--dir", str(model_dir()),
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), 900)
        log.info("Traductor: %s", (out or err).decode(errors="ignore").strip()[-300:])
    except Exception as e:  # noqa: BLE001
        log.warning("Traductor: no pude bajar el modelo: %s", e)
    finally:
        _preparing = False


def glossary(text: str) -> str:
    out = text
    for es in sorted(GLOSSARY, key=len, reverse=True):
        out = re.sub(rf"\b{re.escape(es)}\b", GLOSSARY[es], out, flags=re.IGNORECASE)
    return out


async def to_english(text: str) -> tuple[str, str]:
    """Devuelve (texto en inglés, cómo se tradujo: "" si no hacía falta, "placa" o "glosario")."""
    text = (text or "").strip()
    if not text or not looks_spanish(text):
        return text, ""
    py = await asyncio.to_thread(find_python)
    if py and model_ready():
        try:
            proc = await asyncio.create_subprocess_exec(
                py, str(SCRIPT), "--dir", str(model_dir()), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await asyncio.wait_for(proc.communicate(json.dumps({"texts": [text]}).encode()), TIMEOUT)
            if proc.returncode == 0:
                en = (json.loads(out.decode()).get("texts") or [""])[0].strip()
                if en:
                    return en, "placa"
            log.warning("Traductor falló: %s", err.decode(errors="ignore")[-300:])
        except Exception as e:  # noqa: BLE001
            log.warning("Traductor falló: %s", e)
    return glossary(text), "glosario"
