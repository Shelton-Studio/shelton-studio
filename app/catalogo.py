"""Catálogo de modelos de cada ecosistema en la nube (Higgsfield, fal.ai): elegir con un clic cuál usa cada tarea.

Trae una lista curada con los mejores (⭐ recomendados) y, en fal, también su catálogo en vivo. Lo elegido se guarda
en la conexión (model_generate / model_edit / model_video), así funciona en todo Shelton sin tocar nada más.
"""
from __future__ import annotations

import logging
import re
import time

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import Connection, session

log = logging.getLogger("shelton.catalogo")
router = APIRouter(prefix="/api/catalogo")
ROLES = {"generate": "Generar imágenes", "edit": "Editar / con referencias (modelos del banco)", "video": "Video"}
FIELD = {"generate": "model_generate", "edit": "model_edit", "video": "model_video"}


def _m(id_, name, good, best=False, cfg=None):
    return {"id": id_, "name": name, "good": good, "best": best, **({"cfg": cfg} if cfg else {})}


REF = {"image_field": "image_urls"}           # video que parte de una o más referencias (lista de imágenes)
FIRST = {"image_field": "first_frame_url"}    # video que parte de la primera imagen (Kling O3 / Omni)

# Catálogo oficial de la API de Higgsfield (docs.higgsfield.ai): todos los modelos que sirven para las tareas de
# Shelton. Los que necesitan un video de entrada (editar/extender video, control de movimiento) no aplican acá.
CATALOG = {
    "higgsfield": {
        "generate": [
            _m("higgsfield-ai/soul/v2/standard", "SOUL V2", "El modelo propio de Higgsfield: foto real con estética de redes. El de la app.", True),
            _m("higgsfield-ai/soul/cinema", "SOUL Cinema", "Look de cine: luz y color de película."),
            _m("higgsfield-ai/soul/standard", "SOUL", "Versión anterior de SOUL."),
            _m("xai/grok-imagine-image-2.0", "Grok Image 2.0 (xAI)", "Muy fiel al pedido; también edita con hasta 10 referencias."),
            _m("alibaba/qwen-image-3/text-to-image", "Qwen Image 3", "Excelente con texto dentro de la imagen y escenas complejas."),
            _m("z-image/turbo", "Z-Image Turbo", "Rápido y barato, muy realista."),
            _m("ideogram/v4.0", "Ideogram 4.0", "El mejor para letras, logos y carteles."),
            _m("marketing-studio/image/flare", "Marketing Studio 2.5 Flare", "Fotos de producto y publicidad."),
            _m("marketing-studio/image/sunburst", "Marketing Studio 2.5 Sunburst", "Publicidad con luz cálida y brillante."),
            _m("marketing-studio/image", "Marketing Studio 2.0 Alpha", "Versión anterior de Marketing Studio."),
            _m("recraft/v4.1/pro/text-to-image", "Recraft V4.1 Pro", "Diseño, ilustración y estilo gráfico de alta calidad."),
            _m("recraft/v4.1/text-to-image", "Recraft V4.1", "Diseño e ilustración."),
            _m("recraft/v4.1/utility/pro/text-to-image", "Recraft V4.1 Utility Pro", "Piezas gráficas utilitarias (iconos, fondos)."),
            _m("recraft/v4.1/utility/text-to-image", "Recraft V4.1 Utility", "Piezas gráficas utilitarias, más barato."),
        ],
        "edit": [
            _m("alibaba/qwen-image-3/edit", "Qwen Image 3 Edit", "Edita con 1–3 referencias manteniendo la composición y la cara.", True),
            _m("xai/grok-imagine-image-2.0", "Grok Image 2.0 (xAI)", "Edita combinando hasta 10 referencias."),
            _m("marketing-studio/image/flare", "Marketing Studio 2.5 Flare", "Producto + modelo: publicidad con hasta 16 referencias."),
            _m("marketing-studio/image/sunburst", "Marketing Studio 2.5 Sunburst", "Publicidad con referencias, luz cálida."),
            _m("marketing-studio/image", "Marketing Studio 2.0 Alpha", "Versión anterior, con referencias."),
        ],
        "video": [
            _m("kling-video/v3.0/pro/image-to-video", "Kling 3.0 Pro", "El Kling más nuevo: personas y movimiento muy reales.", True),
            _m("kling-video/v3.0/4k/image-to-video", "Kling 3.0 4K", "Kling 3.0 en 4K: máxima nitidez."),
            _m("kling-video/v3.0/std/image-to-video", "Kling 3.0 Standard", "Kling 3.0 más barato."),
            _m("kling-video/v3.0-turbo/image-to-video", "Kling 3.0 Turbo", "Kling 3.0 rápido."),
            _m("kling-video/v2.6/pro/image-to-video", "Kling 2.6 Pro", "Muy buen movimiento, generación anterior."),
            _m("kling-video/v2.5-turbo/pro/image-to-video", "Kling 2.5 Turbo Pro", "Rápido y bueno."),
            _m("kling-video/v2.5-turbo/standard/image-to-video", "Kling 2.5 Turbo Standard", "El Kling más económico."),
            _m("kling-video/o3/image-reference", "Kling O3 · Referencias", "Video a partir de varias referencias (personaje + lugar).", cfg=REF),
            _m("kling-video/o3/first-last-frame", "Kling O3 · Primer cuadro", "Arranca exactamente desde tu imagen.", cfg=FIRST),
            _m("kling-video/omni/image-reference", "Kling Omni · Referencias", "Video desde referencias.", cfg=REF),
            _m("kling-video/omni/first-last-frame", "Kling Omni · Primer cuadro", "Arranca desde tu imagen.", cfg=FIRST),
            _m("bytedance/seedance-2.5/image-to-video", "Seedance 2.5", "Cámara cinematográfica, muy coherente."),
            _m("bytedance/seedance-2.5/reference-to-video", "Seedance 2.5 · Referencias", "Mantiene al personaje desde referencias.", cfg=REF),
            _m("bytedance/seedance-2.0/image-to-video", "Seedance 2.0", "Generación anterior de Seedance."),
            _m("bytedance/seedance-2.0/reference-to-video", "Seedance 2.0 · Referencias", "Personaje desde referencias.", cfg=REF),
            _m("alibaba/wan-3.0-prime/image-to-video", "Wan 3.0 Prime", "Lo mejor de Wan: gran calidad."),
            _m("alibaba/wan-3.0-prime/reference-to-video", "Wan 3.0 Prime · Referencias", "Personaje desde referencias.", cfg=REF),
            _m("alibaba/wan-3.0/image-to-video", "Wan 3.0", "Muy buena relación calidad/precio."),
            _m("alibaba/wan-3.0/reference-to-video", "Wan 3.0 · Referencias", "Desde referencias.", cfg=REF),
            _m("wan/v2.7/image-to-video", "Wan 2.7", "Rápido y económico."),
            _m("wan/v2.7/reference-to-video", "Wan 2.7 · Referencias", "Desde referencias.", cfg=REF),
            _m("wan/v2.6/image-to-video", "Wan 2.6", "Generación anterior de Wan."),
            _m("minimax/h3/image-to-video", "MiniMax H3", "Movimiento expresivo, buenas caras."),
            _m("minimax/h3/reference-to-video", "MiniMax H3 · Referencias", "Desde referencias.", cfg=REF),
            _m("minimax/hailuo-2.3/standard/image-to-video", "Hailuo 2.3", "Buen equilibrio calidad/precio."),
            _m("pixverse/v6/image-to-video", "PixVerse V6", "Estilo dinámico para redes."),
            _m("lightricks/ltx-2.5/image-to-video/pro", "LTX-2.5 Pro", "Rápido, buena calidad."),
            _m("lightricks/ltx-2.5/image-to-video/fast", "LTX-2.5 Fast", "El más rápido."),
            _m("alibaba/happy-horse/v1.1/image-to-video", "Happy Horse 1.1", "Video divertido y fluido."),
            _m("alibaba/happy-horse/v1.1/reference-to-video", "Happy Horse 1.1 · Referencias", "Desde referencias.", cfg=REF),
            _m("alibaba/happy-horse/image-to-video", "Happy Horse 1.0", "Versión anterior."),
            _m("xai/grok-imagine-video/v1.5/reference-to-video", "Grok Imagine Video 1.5", "Video desde referencias (xAI).", cfg=REF),
        ],
    },
    "fal": {   # ids verificados en el catálogo público de fal (fal.ai/api/models); el resto llega en vivo
        "generate": [
            _m("fal-ai/nano-banana-pro", "Nano Banana Pro (Google)", "Lo más avanzado de Google: realismo y fidelidad altísimos.", True),
            _m("bytedance/seedream/v5.5/text-to-image", "Seedream 5.5", "Lo último de ByteDance: fotos muy reales y detalladas."),
            _m("bytedance/seedream/v5/pro/text-to-image", "Seedream 5.0 Pro", "El Seedream que usa la app de Higgsfield: realismo muy alto."),
            _m("fal-ai/flux-2-max", "FLUX 2 Max", "Máxima calidad de FLUX."),
            _m("fal-ai/flux-2-pro", "FLUX 2 Pro", "Muy buena calidad, barato por megapíxel."),
            _m("openai/gpt-image-2", "GPT Image 2 (OpenAI)", "Muy fiel a pedidos largos y con texto."),
            _m("fal-ai/bytedance/seedream/v4.5/text-to-image", "Seedream 4.5", "Muy buen realismo, económico."),
            _m("fal-ai/z-image/turbo", "Z-Image Turbo", "Rápido y barato."),
        ],
        "edit": [
            _m("fal-ai/nano-banana-pro/edit", "Nano Banana Pro Edit (Google)", "El mejor manteniendo la misma cara al editar y combinar referencias.", True),
            _m("fal-ai/nano-banana-2/edit", "Nano Banana 2 Edit (Google)", "Muy bueno con caras, más barato que Pro."),
            _m("bytedance/seedream/v5/pro/edit", "Seedream 5.0 Pro Edit", "Combina varias referencias con calidad altísima (el de la app de Higgsfield)."),
            _m("bytedance/seedream/v5.5/edit", "Seedream 5.5 Edit", "La versión más nueva de Seedream para editar con referencias."),
            _m("fal-ai/flux-2-pro/edit", "FLUX 2 Pro Edit", "Ediciones precisas y económicas."),
            _m("openai/gpt-image-2/edit", "GPT Image 2 Edit (OpenAI)", "Sigue instrucciones complejas al editar."),
            _m("fal-ai/flux-pro/kontext/max", "FLUX Kontext Max", "Cambios precisos (ropa, fondo) sin tocar el resto."),
            _m("fal-ai/qwen-image-edit-2511", "Qwen Image Edit 2511", "Barato; bueno con varias referencias."),
        ],
        "video": [
            _m("kling/v4/image-to-video", "Kling 4.0", "Lo último de Kling: personas y movimiento muy reales.", True),
            _m("fal-ai/kling-video/v3/pro/image-to-video", "Kling 3 Pro", "Excelente calidad, algo más barato."),
            _m("fal-ai/kling-video/v3/4k/image-to-video", "Kling 3 4K", "Máxima nitidez."),
            _m("bytedance/seedance-2.5/us/image-to-video", "Seedance 2.5", "Cámara cinematográfica, muy coherente."),
            _m("alibaba/wan-3.0-prime/image-to-video", "Wan 3.0 Prime", "Gran calidad a buen precio."),
            _m("fal-ai/minimax/hailuo-2.3/pro/image-to-video", "Hailuo 2.3 Pro", "Movimiento expresivo."),
            _m("fal-ai/kling-video/v2.5-turbo/pro/image-to-video", "Kling 2.5 Turbo Pro", "Rápido y económico."),
        ],
    },
}
FAL_CATEGORY = {"generate": "text-to-image", "edit": "image-to-image", "video": "image-to-video"}
_live: dict[str, tuple[float, list]] = {}
LIVE_TTL = 6 * 3600


def _price(text: str) -> str:
    """«Your request will cost **$0.15** per image…» → «$0.15 por imagen» (aproximado: fal manda la letra chica)."""
    t = (text or "").lower()
    m = re.search(r"\$\s?([\d.]+)", t)
    if not m:
        return ""
    if "token" in t:
        return "según tokens"
    unit = ("segundo" if re.search(r"per second|/sec|each second|every second|per \*\*second", t) else
            "megapíxel" if "megapixel" in t else
            "imagen" if re.search(r"per (output )?image|each image|per generated image|/image", t) else
            "video" if re.search(r"per video|\d+ ?s(econd)? video|per \*\*\d", t) else "")
    return f"${m.group(1).rstrip('.')}" + (f" por {unit}" if unit else "")


def _fal_item(it: dict, role: str) -> dict | None:
    if it.get("deprecated") or it.get("removed"):
        return None
    tags = set((it.get("discovery") or {}).get("tags") or [])
    if role == "edit" and "task:edit-image" not in tags:
        return None                     # en «imagen a imagen» también hay mejoradores, quitafondos… solo editores
    mid = it.get("id") or it.get("endpoint_id")
    if not mid:
        return None
    return {"id": mid, "name": it.get("title") or mid, "good": (it.get("shortDescription") or "")[:160],
            "price": _price(it.get("pricingInfoOverride") or ""), "best": False,
            "date": (it.get("publishedAt") or it.get("date") or "")[:10]}


async def fal_live(api_key: str, role: str) -> list[dict]:
    """TODO el catálogo de fal para esa tarea (con precio), desde su catálogo público. Se guarda 6 horas."""
    hit = _live.get(role)
    if hit and time.time() - hit[0] < LIVE_TTL:
        return hit[1]
    out: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            page, pages = 1, 1
            while page <= min(pages, 15):
                r = await c.get("https://fal.ai/api/models", params={"categories": FAL_CATEGORY[role], "page": page})
                if r.status_code >= 400:
                    break
                j = r.json()
                pages = int(j.get("pages") or 1)
                out += [x for x in (_fal_item(it, role) for it in j.get("items") or []) if x]
                page += 1
    except Exception as e:  # noqa: BLE001
        log.info("Catálogo público de fal no disponible: %s", e)
    if not out and api_key:                      # respaldo: la API de plataforma de fal (con la clave)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                cursor = None
                for _ in range(10):
                    params = {"category": FAL_CATEGORY[role], "limit": 100, "status": "active"}
                    if cursor:
                        params["cursor"] = cursor
                    r = await c.get("https://api.fal.ai/v1/models", params=params,
                                    headers={"Authorization": f"Key {api_key}"})
                    if r.status_code >= 400:
                        break
                    j = r.json()
                    for m in j.get("models") or []:
                        meta = m.get("metadata") or {}
                        if m.get("endpoint_id"):
                            out.append({"id": m["endpoint_id"], "name": meta.get("display_name") or m["endpoint_id"],
                                        "good": (meta.get("description") or "")[:160], "price": "", "best": False})
                    cursor = j.get("next_cursor")
                    if not cursor or not j.get("has_more"):
                        break
        except Exception as e:  # noqa: BLE001
            log.info("API de modelos de fal no disponible: %s", e)
    seen, uniq = set(), []
    for m in out:
        if m["id"] not in seen:
            seen.add(m["id"])
            uniq.append(m)
    if uniq:
        _live[role] = (time.time(), uniq)
    return uniq


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
        if c.type == "fal":
            everything = await fal_live(cfg.get("api_key", ""), role)
            info = {m["id"]: m for m in everything}
            for m in curated:                                  # precio y fecha de fal para los recomendados también
                if m["id"] in info:
                    m["price"] = info[m["id"]].get("price", "")
            live = sorted((m for m in everything if m["id"] not in seen),       # lo más nuevo primero
                          key=lambda m: m.get("date") or "", reverse=True)
        current = cfg.get(FIELD[role]) or next((m["id"] for m in curated if m["best"]), "")
        roles[role] = {"label": ROLES[role], "current": current, "custom": bool(cfg.get(FIELD[role])),
                       "models": curated + live, "live_count": len(live)}
    return {"id": c.id, "name": c.name, "type": c.type, "roles": roles}


def _recommended(kind: str, role: str) -> str:
    return next((m["id"] for m in CATALOG[kind][role] if m["best"]), "")


class PickIn(BaseModel):
    role: str
    model: str = ""            # vacío = volver al recomendado


@router.get("/conexiones")
async def api_cloud_connections():
    from app.connections import list_enabled
    return [{"id": c.id, "name": c.name, "type": c.type} for c in list_enabled("engine") if c.type in CATALOG]


def _name(kind: str, role: str, model_id: str) -> str:
    """Nombre lindo de un modelo («bytedance/seedream/v5/pro/text-to-image» → «Seedream 5.0 Pro»)."""
    for m in CATALOG.get(kind, {}).get(role, []):
        if m["id"] == model_id:
            return m["name"].replace(" ⭐", "")
    hit = _live.get(role)
    for m in (hit[1] if hit else []):
        if m["id"] == model_id:
            return m["name"]
    parts = [x for x in model_id.split("/") if x not in ("fal-ai", "text-to-image", "image-to-video", "edit")]
    return " ".join(parts[-2:]) or model_id


def active(choice: str | None) -> dict:
    """Qué modelo usa hoy cada tarea con el Motor elegido (para mostrarlo arriba del chat)."""
    from app.config import settings
    from app.connections import TYPES, engine_for_role, list_enabled
    from app.engines import PRESET_INFO, VIDEO_INFO
    choice = choice or ""
    video_default = VIDEO_INFO.get(settings.VIDEO_PRESET, ("Wan 2.2", ""))[0]

    def conn_models(c) -> dict:
        cfg = c.config or {}
        if c.type in CATALOG:
            out = {}
            for role in ROLES:
                mid = cfg.get(FIELD[role]) or _recommended(c.type, role)
                out[role] = f"{_name(c.type, role, mid)} · {c.name or TYPES[c.type]['name']}"
            return out
        name = c.name or TYPES.get(c.type, {}).get("name", c.type)
        return {"generate": name, "edit": name, "video": cfg.get("model_video") or video_default}

    if choice.startswith("conn:"):
        c = next((x for x in list_enabled("engine") if x.id == choice[5:]), None)
        if c:
            return {"label": c.name, **conn_models(c)}
    if choice.startswith("rtx:"):
        from app import granja
        _, cid, preset = (choice.split(":", 2) + ["", ""])[:3]
        node = next((n for n in granja.remote_nodes() if n["conn_id"] == cid), None)
        from app.engines import reachable
        if not node or not node["comfy_url"] or not reachable(node["comfy_url"]):
            fallback = active(preset if preset in PRESET_INFO else "")
            return {**fallback, "label": "RTX apagada → placa",
                    "generate": fallback["generate"] + " (la RTX está apagada)"}
        where = node["label"] if node else "RTX"
        img = "Qwen-Image 20B" if preset == "quality" else "Z-Image Turbo + LoRA del Banco"
        edit_m = "Qwen-Image-Edit 2509"
        return {"label": f"⚡ {where}", "generate": f"{img} · ⚡ {where}", "edit": f"{edit_m} · ⚡ {where}",
                "video": video_default}
    if choice == "openrouter":
        return {"label": "OpenRouter", "generate": f"OpenRouter · {settings.OPENROUTER_IMAGE_MODEL}",
                "edit": f"OpenRouter · {settings.OPENROUTER_IMAGE_MODEL}", "video": "OpenRouter (video)"}
    if choice in PRESET_INFO:
        label = PRESET_INFO[choice][0]
        return {"label": label, "generate": f"{label} · placa", "edit": "Qwen-Image-Edit · placa", "video": video_default}
    # Automático: lo que tenga asignado cada tarea en Conexiones, o la placa
    out = {"label": "Automático"}
    for role in ROLES:
        c = engine_for_role(role)
        if c:
            out[role] = conn_models(c)[role]
        elif role == "video":
            out[role] = f"{video_default} · placa"
        elif role == "edit":
            out[role] = "Qwen-Image-Edit · placa"
        else:
            out[role] = f"{PRESET_INFO.get(settings.GEN_PRESET, ('Z-Image Turbo', ''))[0]} · placa"
    return out


@router.get("/activo/ahora")
async def api_active(choice: str = ""):
    return active(choice)


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
        if body.role == "video":
            item = next((m for m in CATALOG[c.type]["video"] if m["id"] == (model or _recommended(c.type, "video"))), {})
            if item.get("cfg", {}).get("image_field"):
                cfg["image_field"] = item["cfg"]["image_field"]
            else:
                cfg.pop("image_field", None)
        c.config = cfg
        s.flush()
    from app.engines import reset_cache
    reset_cache()
    return {"ok": True, "role": body.role, "model": model}
