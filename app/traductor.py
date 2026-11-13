"""Traducción español → inglés en la propia placa (sin IA en la nube): SDXL y Flux entienden casi solo inglés.

Usa el modelo abierto opus-mt-es-en con el Python de ComfyUI (que ya trae transformers y torch), en la CPU. Lo baja
una vez y lo deja cargado en un proceso aparte para traducir al instante mientras se escribe. Si todavía no está
listo, usa un glosario básico para no frenar nada.
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
TIMEOUT = 60
START_TIMEOUT = 240
_caps: dict[str, set] | None = None
_preparing = False
_cache: dict[str, str] = {}          # lo ya traducido (al escribir en vivo se repite mucho)

SPANISH_RE = re.compile(
    r"[ñáéíóú¿¡]|\b(de|la|el|los|las|una?|con|en|sin|muy|mujer|chica|foto|cuerpo|pelo|piel|luz|cama|playa|"
    r"desnud[ao]s?|sentad[ao]|parad[ao]|acostad[ao]|mirando|fondo|ventana|habitaci[oó]n|largo|corto|rubi[ao]|"
    r"moren[ao]|ojos|labios|sonrisa|vestid[ao]|negr[ao]|blanc[ao]|roj[ao]|quiero|necesito|que|est[ae]|y|por|"
    r"para|hazme|haz|genera|dame|hermosa|bonita|grande|pequeñ[ao]|alta|baja|delgada|tetas|senos|nalgas|cola|"
    r"pechos|piernas|brazos|manos|cara|rostro)\b", re.IGNORECASE)

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
    return bool(SPANISH_RE.search(text or ""))


def _candidates() -> list[str]:
    comfy = Path(os.getenv("COMFY_DIR", str(Path.home() / "ComfyUI"))) / "venv" / "bin" / "python"
    return [str(comfy), sys.executable]


def _capabilities() -> dict[str, set]:
    """Qué puede usar cada Python: «marian» (transformers+torch) o «argos» (ctranslate2). Se revisa una vez."""
    global _caps
    if _caps is not None:
        return _caps
    caps: dict[str, set] = {}
    for py in _candidates():
        if not Path(py).exists() or py in caps:
            continue
        caps[py] = set()
        for kind, mods in (("marian", "transformers, torch, sentencepiece"), ("argos", "ctranslate2, sentencepiece")):
            try:
                if subprocess.run([py, "-c", f"import {mods}"], capture_output=True, timeout=120).returncode == 0:
                    caps[py].add(kind)
            except Exception:  # noqa: BLE001
                pass
    _caps = caps
    return caps


def _python_for(kind: str) -> str | None:
    return next((py for py, k in _capabilities().items() if kind in k), None)


def find_python() -> str | None:
    """El Python con que se traduce (o se bajaría el modelo)."""
    return _python_for("marian") or _python_for("argos")


def model_dir() -> Path:
    return settings.DATA_DIR / "traductor"


def _script():
    import importlib.util
    spec = importlib.util.spec_from_file_location("shelton_traducir", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def engine() -> tuple[str, str] | None:
    """(motor, python) listo para traducir, o None."""
    try:
        kind = _script().ready(model_dir())
    except Exception:  # noqa: BLE001
        return None
    py = _python_for(kind) if kind else None
    return (kind, py) if py else None


def model_ready() -> bool:
    return engine() is not None


def files() -> list[str]:
    """Para diagnosticar: qué hay en la carpeta del traductor."""
    root = model_dir()
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())[:40] if root.exists() else []


def status() -> dict:
    if not find_python():
        return {"ok": False, "why": "Falta el Python de ComfyUI con transformers (corre el instalador de la placa)"}
    if not model_ready():
        return {"ok": False, "why": "Bajando el modelo de traducción (una sola vez)…" if _preparing else
                "Falta el modelo de traducción: se baja solo al arrancar Shelton"}
    return {"ok": True, "why": ""}


async def prepare() -> None:
    """Al arrancar: si falta el modelo, lo baja (una vez, en segundo plano) y deja el traductor cargado."""
    global _preparing
    py = await asyncio.to_thread(find_python)
    if not py:
        return
    if not await asyncio.to_thread(model_ready) or (_python_for("marian") and not _script().marian_ready(model_dir())):
        _preparing = True
        try:
            proc = await asyncio.create_subprocess_exec(py, str(SCRIPT), "--setup", "--dir", str(model_dir()),
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await asyncio.wait_for(proc.communicate(), 1200)
            log.info("Traductor: %s", (out or err).decode(errors="ignore").strip()[-300:])
        except Exception as e:  # noqa: BLE001
            log.warning("Traductor: no pude bajar el modelo: %s", e)
        finally:
            _preparing = False
    try:
        await _server()                      # lo deja cargado para que la primera traducción ya sea rápida
    except Exception as e:  # noqa: BLE001
        log.warning("Traductor: no pude cargarlo: %s", e)


_proc: asyncio.subprocess.Process | None = None
_lock: asyncio.Lock | None = None


async def _server() -> asyncio.subprocess.Process | None:
    global _proc
    if _proc and _proc.returncode is None:
        return _proc
    eng = await asyncio.to_thread(engine)
    if not eng:
        return None
    _, py = eng
    proc = await asyncio.create_subprocess_exec(py, str(SCRIPT), "--serve", "--dir", str(model_dir()),
                                                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.DEVNULL, limit=1 << 20)
    line = await asyncio.wait_for(proc.stdout.readline(), START_TIMEOUT)
    if line.strip() != b"READY":
        proc.kill()
        raise RuntimeError("el traductor no arrancó")
    _proc = proc
    return proc


async def _ask(text: str) -> str:
    global _proc, _lock
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        proc = await _server()
        if not proc:
            return ""
        try:
            proc.stdin.write((json.dumps({"texts": [text]}, ensure_ascii=False) + "\n").encode())
            await proc.stdin.drain()
            line = await asyncio.wait_for(proc.stdout.readline(), TIMEOUT)
            data = json.loads(line.decode() or "{}")
        except Exception:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
            _proc = None
            raise
        if data.get("error"):
            raise RuntimeError(data["error"])
        return ((data.get("texts") or [""])[0] or "").strip()


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
    if text in _cache:
        return _cache[text], "placa"
    try:
        en = await _ask(text)
        if en:
            if len(_cache) > 300:
                _cache.clear()
            _cache[text] = en
            return en, "placa"
    except Exception as e:  # noqa: BLE001
        log.warning("Traductor falló: %s", e)
    return glossary(text), "glosario"
