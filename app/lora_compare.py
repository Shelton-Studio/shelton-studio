"""Comparador de versiones de la LoRA: las mismas escenas y semillas con cada versión (y cada fuerza), lado a lado,
para elegir con los ojos cuál sale mejor. No borra ni cambia nada hasta que alguien elige.

Usa la GPU local (ComfyUI) y espera su turno: si hay un entrenamiento corriendo, empieza cuando termine.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import events
from app.config import settings
from app.db import Subject, session

log = logging.getLogger("shelton.lora_compare")
router = APIRouter(prefix="/api")

SCENES = {
    "person": [
        "close-up portrait photo of {t}, soft window light, neutral background, looking at the camera, natural skin",
        "full body photo of {t} walking in a city street at golden hour, casual outfit",
        "photo of {t} sitting in a cozy cafe, smiling, candid, shallow depth of field",
        "photo of {t} at the beach in a summer dress, bright midday sun, wide shot",
    ],
    "other": [
        "studio photo of {t}, clean background, soft light",
        "photo of {t} outdoors at golden hour",
        "close-up detail photo of {t}",
        "wide shot photo of {t} in a natural setting",
    ],
}
SEEDS = [1101, 2202, 3303, 4404]
SIZE = (896, 1152)
MAX_VERSIONS = 6
_tasks: dict[str, asyncio.Task] = {}


def _work(sid: str) -> Path:
    return settings.DATA_DIR / "lora" / sid


def _dir(sid: str) -> Path:
    d = _work(sid) / "comparaciones"
    d.mkdir(parents=True, exist_ok=True)
    return d


def candidates(sid: str) -> list[dict]:
    """Versiones guardadas + puntos intermedios del entrenamiento (uno por cantidad de pasos)."""
    from app import lora
    work = _work(sid)
    found: dict[int, dict] = {}
    with session() as s:
        info = dict(s.get(Subject, sid).lora_info or {})
    for f in sorted((work / "versiones").glob("*.safetensors")) if (work / "versiones").exists() else []:
        st = lora._read_saved_step(f)
        if st:
            found[st] = {"key": f"v:{f.name}", "step": st, "kind": "respaldo", "active": info.get("active_version") == f.name}
    out_dir = work / "output"
    for d in out_dir.iterdir() if out_dir.exists() else []:
        if not d.is_dir():
            continue
        for f in d.glob("*.safetensors"):
            st = lora._read_saved_step(f)
            if st and st not in found:
                found[st] = {"key": f"c:{d.name}/{f.name}", "step": st,
                             "kind": "archivado" if "_anterior_" in d.name else "punto intermedio", "active": False}
    return sorted(found.values(), key=lambda x: -x["step"])


def _path_for(sid: str, key: str) -> Path:
    work = _work(sid).resolve()
    kind, _, rest = key.partition(":")
    p = (work / ("versiones" if kind == "v" else "output") / rest).resolve()
    if kind not in ("v", "c") or work not in p.parents or not p.exists() or p.suffix != ".safetensors":
        raise ValueError("Esa versión no existe")
    return p


def _comfy_name(sid: str, step: int) -> str:
    """Copia de la versión en la carpeta de LoRAs de ComfyUI con un nombre propio (no pisa la activa)."""
    from app import lora
    return f"{lora.job_for(sid)}__cmp_{step}.safetensors"


def _save(job: dict) -> None:
    (_dir(job["subject_id"]) / f"{job['id']}.json").write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")


def load(sid: str, job_id: str) -> dict:
    f = _dir(sid) / f"{Path(job_id).name}.json"
    if not f.exists():
        raise ValueError("Esa comparación no existe")
    return json.loads(f.read_text(encoding="utf-8"))


def latest(sid: str) -> dict | None:
    files = sorted(_dir(sid).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return json.loads(files[0].read_text(encoding="utf-8")) if files else None


def start(sid: str, keys: list[str], strengths: list[float] | None = None) -> dict:
    from app import lora
    with session() as s:
        sub = s.get(Subject, sid)
        if not sub:
            raise ValueError("Ese modelo no existe")
        if not sub.lora_trigger:
            raise ValueError("Este modelo todavía no tiene LoRA entrenada")
        trigger, kind, name = sub.lora_trigger, sub.kind, sub.name
        base_strength = lora.strength_of(sub)
    keys = list(dict.fromkeys(keys))[:MAX_VERSIONS]
    if not keys:
        raise ValueError("Elegí al menos una versión")
    strengths = [round(float(x), 2) for x in (strengths or [base_strength]) if 0 < float(x) <= 1.5][:4] or [base_strength]
    versions = []
    for k in keys:
        p = _path_for(sid, k)
        versions.append({"key": k, "step": lora._read_saved_step(p)})
    scenes = [sc.format(t=trigger) for sc in SCENES["person" if kind == "person" else "other"]]
    cells = [{"key": v["key"], "step": v["step"], "strength": st, "scene": i, "image": "", "status": "pending"}
             for v in versions for st in strengths for i in range(len(scenes))]
    job = {"id": uuid.uuid4().hex[:10], "subject_id": sid, "name": name, "created": datetime.now().isoformat(timespec="seconds"),
           "status": "queued", "versions": versions, "strengths": strengths, "scenes": scenes, "cells": cells, "error": ""}
    _save(job)
    _tasks[job["id"]] = asyncio.get_event_loop().create_task(_run(job))
    return job


async def _run(job: dict) -> None:
    from app.engines import get_engine
    from app.media import save_bytes, url_for
    sid = job["subject_id"]
    try:
        engine = get_engine("generate", "local")
        if getattr(engine, "name", "") != "comfyui":
            engine = get_engine("generate")
        if getattr(engine, "name", "") != "comfyui":
            raise RuntimeError("El comparador usa la GPU local (ComfyUI) y no está configurada")
        lora_dir = Path(settings.COMFY_LORA_DIR).expanduser()
        lora_dir.mkdir(parents=True, exist_ok=True)
        names = {}
        for v in job["versions"]:
            dest = lora_dir / _comfy_name(sid, v["step"])
            if not dest.exists():
                await asyncio.to_thread(shutil.copy2, _path_for(sid, v["key"]), dest)
            names[v["key"]] = dest.name
        job["status"] = "running"
        _save(job)
        for i, cell in enumerate(job["cells"]):
            cell["status"] = "running"
            events.publish({"type": "lora_compare", "subject_id": sid, "id": job["id"], "done": i, "total": len(job["cells"])})
            try:
                raw = await engine.generate_with_loras([(names[cell["key"]], cell["strength"])], job["scenes"][cell["scene"]],
                                                       SIZE[0], SIZE[1], SEEDS[cell["scene"] % len(SEEDS)], None, quality=False)
                path = save_bytes(raw, f"cmp_{job['id']}_{i}.png")
                cell["image"], cell["status"] = url_for(path), "done"
            except Exception as e:  # noqa: BLE001
                cell["status"], cell["error"] = "failed", str(e)[:200]
            _save(job)
        job["status"] = "done"
    except Exception as e:  # noqa: BLE001
        log.exception("comparación falló")
        job["status"], job["error"] = "failed", str(e)[:300]
    _save(job)
    events.publish({"type": "lora_compare", "subject_id": sid, "id": job["id"], "status": job["status"]})


def choose(sid: str, key: str, strength: float | None = None) -> dict:
    """Deja esa versión como la activa (y desde la que se sigue entrenando) con esa fuerza. No borra nada."""
    from app import lora
    p = _path_for(sid, key)
    if key.startswith("c:"):     # punto intermedio: se guarda primero como respaldo
        vdir = _work(sid) / "versiones"
        vdir.mkdir(parents=True, exist_ok=True)
        step = lora._read_saved_step(p)
        same = next((f for f in vdir.glob("*.safetensors") if lora._read_saved_step(f) == step), None)
        if same is None:
            same = vdir / f"{lora.job_for(sid)}_{step}pasos_{datetime.now():%Y%m%d_%H%M}_elegida.safetensors"
            shutil.copy2(p, same)
        p = same
    r = lora.use_version(sid, p.name)
    if strength:
        lora._update(sid, info={"strength": max(0.1, min(float(strength), 1.5))})
    return {**r, "strength": strength}


class CompareIn(BaseModel):
    keys: list[str]
    strengths: list[float] | None = None


class ChooseIn(BaseModel):
    key: str
    strength: float | None = None


@router.get("/subjects/{sid}/lora/compare")
async def compare_info(sid: str):
    try:
        return {"candidates": candidates(sid), "latest": latest(sid)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(404, str(e))


@router.post("/subjects/{sid}/lora/compare")
async def compare_start(sid: str, body: CompareIn):
    try:
        return start(sid, body.keys, body.strengths)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/subjects/{sid}/lora/compare/{job_id}")
async def compare_status(sid: str, job_id: str):
    try:
        return load(sid, job_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/subjects/{sid}/lora/choose")
async def compare_choose(sid: str, body: ChooseIn):
    try:
        return choose(sid, body.key, body.strength)
    except ValueError as e:
        raise HTTPException(400, str(e))
