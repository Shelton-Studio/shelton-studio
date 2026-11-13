"""Calidad máxima de imagen (en la GPU local, sin cambiar de modelo).

Cuando está activada, cada foto local pasa por:
1. Segunda pasada de detalle: se agranda la imagen en el espacio latente (1,5x) y se vuelve a afinar suave (denoise
   bajo) con el mismo modelo + LoRA: piel, pelo y telas salen más nítidos. Solo usa nodos de ComfyUI de fábrica.
2. Mejora de rostro (si está instalado el Impact Pack en ComfyUI): detecta la cara, la vuelve a generar en alta
   resolución con la LoRA y la pega. Si el nodo no está, se salta sin error.
3. Mejor de N: se generan varias fotos con semillas distintas y el Juez de calidad elige la mejor.
La foto final se ajusta al tamaño de cada red; generar más grande y achicar después la deja más nítida.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from app.config import settings

DEFAULTS = {"max_quality": False, "refine": True, "refine_scale": 1.5, "refine_denoise": 0.35,
            "face": True, "candidates": 3, "detailed_captions": False}
FACE_NODES = ("FaceDetailer", "UltralyticsDetectorProvider")
FACE_MODEL = "bbox/face_yolov8m.pt"


def _file() -> Path:
    return settings.DATA_DIR / "calidad.json"


def get() -> dict:
    data = dict(DEFAULTS)
    try:
        if _file().exists():
            data.update(json.loads(_file().read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        pass
    return data


def save(**changes) -> dict:
    data = get()
    for k, v in changes.items():
        if k in DEFAULTS and v is not None:
            data[k] = type(DEFAULTS[k])(v)
    data["candidates"] = max(1, min(int(data["candidates"]), 6))
    data["refine_scale"] = max(1.0, min(float(data["refine_scale"]), 2.0))
    data["refine_denoise"] = max(0.1, min(float(data["refine_denoise"]), 0.6))
    _file().parent.mkdir(parents=True, exist_ok=True)
    _file().write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


def candidates() -> int:
    """Cuántas fotos se generan por intento para que el Juez elija (1 = como siempre)."""
    q = get()
    return int(q["candidates"]) if q["max_quality"] else 1


# ------------------------------------------------------------------ nodos disponibles en cada ComfyUI (caché 10 min)
_nodes_cache: dict[str, tuple[float, dict]] = {}


async def node_info(client) -> dict:
    """object_info de ese ComfyUI (qué nodos tiene y qué entradas piden)."""
    hit = _nodes_cache.get(client.base)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    try:
        info = await client.object_info()
    except Exception:  # noqa: BLE001
        info = {}
    _nodes_cache[client.base] = (time.time(), info)
    return info


async def apply(wf: dict, client) -> list[str]:
    """Si la calidad máxima está activada, agrega los pasos de calidad al workflow. Devuelve cuáles se agregaron."""
    q = get()
    if not q["max_quality"]:
        return []
    info = await node_info(client) if q.get("face", True) else {}
    done = enhance(wf, q, set(info))
    fill_required(wf, info)
    return done


async def status(client) -> dict:
    info = await node_info(client)
    return {**get(), "face_available": all(n in info for n in FACE_NODES), "comfy_online": bool(info)}


def _find(wf: dict, cls: str) -> list[str]:
    return [k for k, v in wf.items() if isinstance(v, dict) and v.get("class_type") == cls]


def enhance(wf: dict, opts: dict, nodes: set) -> list[str]:
    """Agrega al workflow la segunda pasada y la mejora de rostro. Devuelve qué pasos se agregaron."""
    done: list[str] = []
    ks = _find(wf, "KSampler")
    dec = _find(wf, "VAEDecode")
    saves = _find(wf, "SaveImage")
    if len(ks) != 1 or not dec or not saves:
        return done
    k = wf[ks[0]]["inputs"]
    decode = next((d for d in dec if wf[d]["inputs"].get("samples") == [ks[0], 0]), None)
    if not decode:
        return done
    vae = wf[decode]["inputs"]["vae"]
    image = [decode, 0]
    if opts.get("refine", True):
        wf["shl_up"] = {"class_type": "LatentUpscaleBy",
                        "inputs": {"samples": [ks[0], 0], "upscale_method": "nearest-exact",
                                   "scale_by": float(opts.get("refine_scale", 1.5))}}
        wf["shl_refine"] = {"class_type": "KSampler", "inputs": {
            **k, "latent_image": ["shl_up", 0], "denoise": float(opts.get("refine_denoise", 0.35))}}
        wf["shl_decode"] = {"class_type": "VAEDecode", "inputs": {"samples": ["shl_refine", 0], "vae": vae}}
        image = ["shl_decode", 0]
        done.append("detalle")
    clip_loader = next(iter(_find(wf, "CLIPLoader") + _find(wf, "DualCLIPLoader")), None)
    clip_ref = [clip_loader, 0] if clip_loader else None
    if not clip_ref and _find(wf, "CheckpointLoaderSimple"):
        clip_ref = [_find(wf, "CheckpointLoaderSimple")[0], 1]
    if opts.get("face", True) and clip_ref and all(n in nodes for n in FACE_NODES):
        wf["shl_facedet"] = {"class_type": "UltralyticsDetectorProvider", "inputs": {"model_name": FACE_MODEL}}
        wf["shl_face"] = {"class_type": "FaceDetailer", "inputs": {
            "image": image, "model": k["model"], "clip": clip_ref, "vae": vae,
            "positive": k["positive"], "negative": k["negative"], "bbox_detector": ["shl_facedet", 0],
            "guide_size": 768, "guide_size_for": True, "max_size": 1536, "seed": k["seed"], "steps": k["steps"],
            "cfg": k["cfg"], "sampler_name": k["sampler_name"], "scheduler": k["scheduler"], "denoise": 0.4,
            "feather": 5, "noise_mask": True, "force_inpaint": True, "bbox_threshold": 0.5, "bbox_dilation": 10,
            "bbox_crop_factor": 3.0, "sam_detection_hint": "center-1", "sam_dilation": 0, "sam_threshold": 0.93,
            "sam_bbox_expansion": 0, "sam_mask_hint_threshold": 0.7, "sam_mask_hint_use_negative": "False",
            "drop_size": 10, "wildcard": "", "cycle": 1, "inpaint_model": False, "noise_mask_feather": 20,
            "tiled_encode": False, "tiled_decode": False}}
        image = ["shl_face", 0]
        done.append("rostro")
    for s in saves:
        wf[s]["inputs"]["images"] = image
    return done


def fill_required(wf: dict, info: dict) -> None:
    """Completa entradas obligatorias que pida la versión instalada de un nodo (con su valor por defecto) y quita
    las que esa versión no conoce. Así la mejora de rostro funciona con distintas versiones del Impact Pack."""
    for nid in ("shl_face",):
        node = wf.get(nid)
        spec = (info or {}).get(node["class_type"]) if node else None
        if not spec:
            continue
        req = spec.get("input", {}).get("required", {})
        opt = spec.get("input", {}).get("optional", {})
        known = set(req) | set(opt)
        for key in list(node["inputs"]):
            if key not in known:
                node["inputs"].pop(key)
        for key, val in req.items():
            if key in node["inputs"]:
                continue
            cfg = val[1] if isinstance(val, list) and len(val) > 1 and isinstance(val[1], dict) else {}
            if "default" in cfg:
                node["inputs"][key] = cfg["default"]
            elif isinstance(val, list) and val and isinstance(val[0], list) and val[0]:
                node["inputs"][key] = val[0][0]


# ------------------------------------------------------------------ API
from fastapi import APIRouter  # noqa: E402
from pydantic import BaseModel  # noqa: E402

router = APIRouter(prefix="/api")


class CalidadIn(BaseModel):
    max_quality: bool | None = None
    refine: bool | None = None
    face: bool | None = None
    candidates: int | None = None
    detailed_captions: bool | None = None


@router.get("/calidad")
async def calidad_get():
    from app.comfyui.client import ComfyUIClient
    try:
        return await status(ComfyUIClient())
    except Exception:  # noqa: BLE001
        return {**get(), "face_available": False, "comfy_online": False}


@router.post("/calidad")
async def calidad_set(body: CalidadIn):
    return save(**body.model_dump(exclude_none=True))
