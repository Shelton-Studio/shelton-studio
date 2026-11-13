"""Catálogo de modelos de cada ecosistema en la nube (Higgsfield, fal.ai): elegir con un clic cuál usa cada tarea.

Trae una lista curada con los mejores (⭐ recomendados) y, en fal, también su catálogo en vivo. Lo elegido se guarda
en la conexión (model_generate / model_edit / model_video), así funciona en todo Shelton sin tocar nada más.
"""
from __future__ import annotations

import logging
import time

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import Connection, session

log = logging.getLogger("shelton.catalogo")
router = APIRouter(prefix="/api/catalogo")
ROLES = {"generate": "Generar imágenes", "edit": "Editar / con referencias (modelos del banco)", "video": "Video"}
FIELD = {"generate": "model_generate", "edit": "model_edit", "video": "model_video"}


def _m(id_, name, good, best=False):
    return {"id": id_, "name": name, "good": good, "best": best}


CATALOG = {
    "higgsfield": {
        "generate": [
            _m("higgsfield-ai/soul/v2/standard", "Soul 2 (Higgsfield)", "Su modelo propio: look de foto real, estética de redes. El de la app.", True),
            _m("higgsfield-ai/soul/standard", "Soul", "Versión anterior de Soul."),
            _m("bytedance/seedream/v4/text-to-image", "Seedream 4", "Muy buena calidad general y texto en la imagen."),
        ],
        "edit": [
            _m("bytedance/seedream/v4/edit", "Seedream 4 Edit", "Edita y combina referencias manteniendo la cara: ideal con modelos del banco.", True),
        ],
        "video": [
            _m("kling-video/v2.1/pro/image-to-video", "Kling 2.1 Pro", "Movimiento muy natural, personas realistas.", True),
            _m("bytedance/seedance/v1/pro/image-to-video", "Seedance Pro", "Cámara cinematográfica y buena coherencia."),
            _m("wan/v2.7/image-to-video", "Wan 2.7", "Rápido y económico."),
        ],
    },
    "fal": {
        "generate": [
            _m("fal-ai/bytedance/seedream/v4/text-to-image", "Seedream 4", "Realismo y detalle altísimos; buen precio.", True),
            _m("fal-ai/flux-pro/v1.1-ultra", "FLUX 1.1 Pro Ultra", "Fotos de alta resolución muy realistas."),
            _m("fal-ai/imagen4/preview", "Imagen 4 (Google)", "Muy fiel al pedido, buena piel y luz."),
            _m("fal-ai/qwen-image", "Qwen-Image", "Barato; muy bueno con texto dentro de la imagen."),
            _m("fal-ai/flux/dev", "FLUX.1 Dev", "Económico y versátil."),
        ],
        "edit": [
            _m("fal-ai/nano-banana/edit", "Nano Banana Edit (Google)", "El mejor manteniendo la misma cara al editar.", True),
            _m("fal-ai/bytedance/seedream/v4/edit", "Seedream 4 Edit", "Combina varias referencias con muy buena calidad."),
            _m("fal-ai/flux-pro/kontext", "FLUX Kontext Pro", "Cambios precisos (ropa, fondo) sin tocar el resto."),
            _m("fal-ai/qwen-image-edit-plus", "Qwen-Image Edit Plus", "Barato; bueno con varias referencias."),
        ],
        "video": [
            _m("fal-ai/kling-video/v2.1/master/image-to-video", "Kling 2.1 Master", "La mejor calidad de Kling: personas muy reales.", True),
            _m("fal-ai/kling-video/v2.1/standard/image-to-video", "Kling 2.1 Standard", "Más barato, muy buen movimiento."),
            _m("fal-ai/veo3/fast/image-to-video", "Veo 3 Fast (Google)", "Video con sonido, muy realista."),
            _m("fal-ai/bytedance/seedance/v1/pro/image-to-video", "Seedance Pro", "Cámara cinematográfica."),
            _m("fal-ai/minimax/hailuo-02/standard/image-to-video", "Hailuo 02", "Buen equilibrio calidad/precio."),
        ],
    },
}
FAL_CATEGORY = {"generate": "text-to-image", "edit": "image-to-image", "video": "image-to-video"}
_live: dict[tuple, tuple[float, list]] = {}
LIVE_TTL = 6 * 3600


async def fal_live(api_key: str, role: str) -> list[dict]:
    """Catálogo en vivo de fal (si su API lo da); si falla, vacío y queda la lista curada."""
    key = (api_key[-6:], role)
    hit = _live.get(key)
    if hit and time.time() - hit[0] < LIVE_TTL:
        return hit[1]
    out: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            cursor, pages = None, 0
            while pages < 5:
                params = {"category": FAL_CATEGORY[role], "limit": 100, "status": "active"}
                if cursor:
                    params["cursor"] = cursor
                r = await c.get("https://api.fal.ai/v1/models", params=params,
                                headers={"Authorization": f"Key {api_key}"})
                if r.status_code >= 400:
                    break
                j = r.json()
                items = j.get("models") or j.get("items") or j.get("data") or (j if isinstance(j, list) else [])
                for m in items:
                    meta = m.get("metadata") or m
                    mid = m.get("endpoint_id") or m.get("id") or meta.get("endpoint_id")
                    if not mid:
                        continue
                    out.append({"id": mid, "name": meta.get("display_name") or meta.get("title") or mid,
                                "good": (meta.get("description") or "")[:140], "best": False})
                cursor = j.get("next_cursor") if isinstance(j, dict) else None
                pages += 1
                if not cursor or not (isinstance(j, dict) and j.get("has_more", True)):
                    break
    except Exception as e:  # noqa: BLE001
        log.info("Catálogo en vivo de fal no disponible: %s", e)
    _live[key] = (time.time(), out)
    return out


def _conn(cid: str) -> Connection:
    with session() as s:
        c = s.get(Connection, cid)
        if not c or c.type not in CATALOG:
            raise HTTPException(404, "Esa conexión no tiene catálogo de modelos")
        s.expunge(c)
        return c


async def catalog_for(cid: str) -> dict:
    c = _conn(cid)
    cfg = c.config or {}
    roles = {}
    for role in ROLES:
        curated = [dict(m) for m in CATALOG[c.type][role]]
        seen = {m["id"] for m in curated}
        live = []
        if c.type == "fal" and cfg.get("api_key"):
            live = [m for m in await fal_live(cfg["api_key"], role) if m["id"] not in seen]
        current = cfg.get(FIELD[role]) or next((m["id"] for m in curated if m["best"]), "")
        roles[role] = {"label": ROLES[role], "current": current, "custom": bool(cfg.get(FIELD[role])),
                       "models": curated + live, "live_count": len(live)}
    return {"id": c.id, "name": c.name, "type": c.type, "roles": roles}


class PickIn(BaseModel):
    role: str
    model: str = ""            # vacío = volver al recomendado


@router.get("/conexiones")
async def api_cloud_connections():
    from app.connections import list_enabled
    return [{"id": c.id, "name": c.name, "type": c.type} for c in list_enabled("engine") if c.type in CATALOG]


@router.get("/{cid}")
async def api_catalog(cid: str):
    return await catalog_for(cid)


@router.post("/{cid}")
async def api_pick(cid: str, body: PickIn):
    if body.role not in FIELD:
        raise HTTPException(400, "Tarea desconocida")
    model = body.model.strip().strip("/")
    with session() as s:
        c = s.get(Connection, cid)
        if not c or c.type not in CATALOG:
            raise HTTPException(404, "Esa conexión no tiene catálogo de modelos")
        cfg = dict(c.config or {})
        if model:
            cfg[FIELD[body.role]] = model
        else:
            cfg.pop(FIELD[body.role], None)
        c.config = cfg
        s.flush()
    from app.engines import reset_cache
    reset_cache()
    return {"ok": True, "role": body.role, "model": model}
