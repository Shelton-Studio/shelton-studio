"""🪪 Cara por foto (propuesta de Love): 1 a 3 fotos de la cara de una modelo del banco y Shelton genera fotos nuevas
con esa cara en segundos, sin entrenar nada (InstantID sobre SDXL + retoque final de la cara).

No reemplaza a la LoRA: sirve para probar una cara nueva al instante. La LoRA sigue siendo lo que mantiene la cara
y el cuerpo iguales de una foto a otra. Vive en el Shelton normal (Banco), no en Shelton Hot.
"""
from __future__ import annotations

import io
import json
import random
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.config import settings
from app.db import Subject, session

MAX_REFS = 3
WEIGHT = 0.8          # parecido de la cara (más = más parecida, menos libertad de estilo)
POSE = 0.5            # cuánto copia la pose de la foto (0,8 la copia; 0,3 la deja libre)
SIZES = {"vertical": (832, 1216), "cuadrada": (1024, 1024), "horizontal": (1216, 832)}
MAX_TRIES = 4

router = APIRouter(prefix="/api")


def _dir(sid: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", sid or ""):
        raise ValueError("Modelo inválido")
    return settings.DATA_DIR / "identidad" / sid


def _meta_file(sid: str) -> Path:
    return _dir(sid) / "cara.json"


def _meta(sid: str) -> dict:
    try:
        d = json.loads(_meta_file(sid).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        d = {}
    d.setdefault("refs", [])
    d["refs"] = [f for f in d["refs"] if (_dir(sid) / f).exists()]
    d.setdefault("weight", WEIGHT)
    d.setdefault("pose", POSE)
    return d


def _save_meta(sid: str, d: dict) -> None:
    _dir(sid).mkdir(parents=True, exist_ok=True)
    _meta_file(sid).write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def _subject(sid: str) -> str:
    """Nombre de la modelo; solo modelos del Banco (las de Shelton Hot no usan esto)."""
    from app.zona import adult_only
    with session() as s:
        sub = s.get(Subject, sid)
        if not sub:
            raise LookupError("Ese modelo no existe")
        if adult_only(sub):
            raise ValueError("La cara por foto es del Banco de Shelton, no de Shelton Hot")
        return sub.name


def _url(sid: str, file: str) -> str:
    return f"/api/subjects/{sid}/identidad/archivo/{file}"


def info(sid: str) -> dict:
    name = _subject(sid)
    d = _meta(sid)
    tests = sorted((_dir(sid) / "pruebas").glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)[:12] \
        if (_dir(sid) / "pruebas").exists() else []
    return {"id": sid, "name": name, "refs": [{"file": f, "url": _url(sid, f)} for f in d["refs"]],
            "weight": d["weight"], "pose": d["pose"], "max_refs": MAX_REFS, "ready": bool(d["refs"]),
            "tests": [{"file": f"pruebas/{p.name}", "url": _url(sid, f"pruebas/{p.name}")} for p in tests]}


def _clean_image(data: bytes) -> bytes:
    from PIL import Image, ImageOps
    try:
        with Image.open(io.BytesIO(data)) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((1536, 1536))
            out = io.BytesIO()
            im.save(out, "JPEG", quality=92)
            return out.getvalue()
    except Exception as e:  # noqa: BLE001
        raise ValueError("Eso no es una imagen que pueda leer (usa JPG, PNG o WebP)") from e


async def add_ref(sid: str, data: bytes) -> dict:
    """Guarda una foto de la cara (hasta 3). Antes la revisa igual que el cambio de cara: nunca menores."""
    _subject(sid)
    d = _meta(sid)
    if len(d["refs"]) >= MAX_REFS:
        raise ValueError(f"Ya tiene {MAX_REFS} fotos de la cara: borra una para subir otra")
    jpg = _clean_image(data)
    _dir(sid).mkdir(parents=True, exist_ok=True)
    file = f"cara_{uuid.uuid4().hex[:10]}.jpg"
    path = _dir(sid) / file
    path.write_bytes(jpg)
    try:
        from app.pipeline import _swap_check
        await _swap_check(str(path))
    except Exception:
        path.unlink(missing_ok=True)
        raise
    d["refs"].append(file)
    _save_meta(sid, d)
    return info(sid)


def remove_ref(sid: str, file: str) -> dict:
    _subject(sid)
    d = _meta(sid)
    if file not in d["refs"]:
        raise LookupError("Esa foto ya no está")
    d["refs"].remove(file)
    (_dir(sid) / file).unlink(missing_ok=True)
    _save_meta(sid, d)
    return info(sid)


def set_options(sid: str, weight: float | None = None, pose: float | None = None) -> dict:
    _subject(sid)
    d = _meta(sid)
    if weight is not None:
        d["weight"] = round(min(1.5, max(0.3, float(weight))), 2)
    if pose is not None:
        d["pose"] = round(min(1.0, max(0.0, float(pose))), 2)
    _save_meta(sid, d)
    return info(sid)


def _engine():
    """El ComfyUI de la placa con el motor SDXL (RealVisXL)."""
    from app.connections import list_enabled
    from app.engines import _build
    conns = [c for c in list_enabled("engine") if c.type == "comfyui"]
    pick = next((c for c in conns if (c.config or {}).get("preset") == "sdxl"), None) or (conns[0] if conns else None)
    cfg = {**({k: v for k, v in (pick.config or {}).items() if k in ("url", "token")} if pick else {}), "preset": "sdxl"}
    return _build("comfyui", cfg)


async def generate(sid: str, prompt: str, count: int = 1, size: str = "vertical") -> dict:
    """Fotos nuevas con su cara. El texto puede ir en español: se traduce como en Shelton Hot."""
    from app import traductor
    name = _subject(sid)
    d = _meta(sid)
    if not d["refs"]:
        raise ValueError(f"Primero sube 1 a {MAX_REFS} fotos claras de la cara de {name}")
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("Escribe qué foto quieres")
    # el nombre no se traduce ni ayuda: la cara la pone la foto
    text = re.sub(rf"\b{re.escape(name)}\b", "una mujer", prompt, flags=re.IGNORECASE)
    en, _how = await traductor.to_english(text)
    refs = [(_dir(sid) / f).read_bytes() for f in d["refs"]]
    w, h = SIZES.get(size, SIZES["vertical"])
    engine = _engine()
    if not hasattr(engine, "generate_identity"):
        raise ValueError("La cara por foto necesita el ComfyUI de la placa (modo Local)")
    out_dir = _dir(sid) / "pruebas"
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []
    for _ in range(max(1, min(int(count or 1), MAX_TRIES))):
        png = await engine.generate_identity(refs, en or prompt, w, h, random.randint(1, 2**31 - 1),
                                             d["weight"], d["pose"])
        f = out_dir / f"{uuid.uuid4().hex[:12]}.png"
        f.write_bytes(png)
        made.append({"file": f"pruebas/{f.name}", "url": _url(sid, f"pruebas/{f.name}")})
    return {"images": made, "prompt_en": en, "info": info(sid)}


# ------------------------------------------------------------------ API
def _http(e: Exception) -> HTTPException:
    if isinstance(e, LookupError):
        return HTTPException(404, str(e).strip("'\""))
    return HTTPException(400, str(e))


class OptionsIn(BaseModel):
    weight: float | None = None
    pose: float | None = None


class GenIn(BaseModel):
    prompt: str
    count: int = 1
    size: str = "vertical"


@router.get("/subjects/{sid}/identidad")
async def api_info(sid: str):
    try:
        return info(sid)
    except (LookupError, ValueError) as e:
        raise _http(e)


@router.post("/subjects/{sid}/identidad")
async def api_add(sid: str, file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(400, "La foto pesa demasiado (máximo 25 MB)")
    try:
        return await add_ref(sid, data)
    except (LookupError, ValueError) as e:
        raise _http(e)


@router.delete("/subjects/{sid}/identidad/{file}")
async def api_remove(sid: str, file: str):
    try:
        return remove_ref(sid, file)
    except (LookupError, ValueError) as e:
        raise _http(e)


@router.put("/subjects/{sid}/identidad")
async def api_options(sid: str, body: OptionsIn):
    try:
        return set_options(sid, body.weight, body.pose)
    except (LookupError, ValueError) as e:
        raise _http(e)


@router.post("/subjects/{sid}/identidad/generar")
async def api_generate(sid: str, body: GenIn):
    try:
        return await generate(sid, body.prompt, body.count, body.size)
    except (LookupError, ValueError) as e:
        raise _http(e)
    except RuntimeError as e:
        raise HTTPException(503, str(e))


@router.get("/subjects/{sid}/identidad/archivo/{file:path}")
async def api_file(sid: str, file: str):
    from fastapi.responses import FileResponse
    try:
        _subject(sid)
        base = _dir(sid).resolve()
    except (LookupError, ValueError) as e:
        raise _http(e)
    p = (base / file).resolve()
    if base not in p.parents or not p.is_file():
        raise HTTPException(404, "No está")
    return FileResponse(p)
