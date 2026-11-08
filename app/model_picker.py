"""Shelton elige el modelo de cada pedido.

Shelton ve el catálogo completo de OpenRouter (imagen y video) con etiquetas de para qué sirve cada uno,
y en cada orden puede pedir un modelo concreto. La elección viaja con el trabajo como un "choice" del motor:

    or:image=google/gemini-3-pro-image-preview;video=google/veo-3.1

Si no elige nada, todo sigue igual que antes (automático = el más realista disponible).
"""
from __future__ import annotations

IMAGE_TAGS = [
    # (palabra clave en el id, etiquetas, para qué sirve)
    ("gemini-3-pro-image", ["Máximo realismo", "Texto en la imagen", "Personajes consistentes"],
     "Nano Banana Pro: el más fotorrealista; ideal para personas, productos, autos y escenas con texto."),
    ("nano-banana-pro", ["Máximo realismo", "Texto en la imagen"], "Nano Banana Pro: el más fotorrealista."),
    ("gemini-3.1", ["Realismo", "Rápido"], "Nano Banana 2: muy realista y más rápido/barato que Pro."),
    ("nano-banana", ["Realismo", "Rápido"], "Familia Nano Banana: realista y rápida."),
    ("gpt-image", ["Sigue instrucciones", "Texto en la imagen", "Ilustración"],
     "GPT Image: obedece prompts largos y composiciones complejas; bueno para gráficos y estilos."),
    ("riverflow", ["Diseño", "Estilos"], "Bueno para diseño gráfico y estilos artísticos."),
    ("flux", ["Fotografía", "Artístico"], "FLUX: fotografía y arte de alta calidad."),
    ("seedream", ["Realismo", "Buen precio"], "Seedream: realista y económico."),
    ("gemini-2.5-flash-image", ["Rápido", "Económico", "Ediciones"], "Nano Banana original: rápido y barato, bueno para editar."),
]


def image_info(m: dict) -> dict:
    mid = str(m.get("id", ""))
    low = mid.lower()
    tags, use = ["Imagen"], "Generación de imágenes."
    for key, t, u in IMAGE_TAGS:
        if key in low:
            tags, use = t, u
            break
    return {"id": mid, "name": m.get("name") or mid, "tags": tags, "use": use,
            "price": m.get("pricing") or {}, "description": (m.get("description") or "")[:200]}


def encode(image: str | None = None, video: str | None = None) -> str | None:
    parts = [f"{k}={v.strip()}" for k, v in (("image", image), ("video", video)) if v and v.strip()]
    return "or:" + ";".join(parts) if parts else None


def parse(choice: str | None) -> dict:
    if not choice or not choice.startswith("or:"):
        return {}
    out = {}
    for part in choice[3:].split(";"):
        k, _, v = part.partition("=")
        if k in ("image", "video") and v:
            out[k] = v
    return out


def available() -> bool:
    """Shelton solo elige modelos de la nube si hay clave de OpenRouter y el switch no está en Local."""
    from app.engines import infra_mode
    from app.llm import openrouter_config
    return bool(openrouter_config()[0]) and infra_mode() != "local"


async def catalog() -> dict:
    """Modelos de imagen y de video disponibles, del mejor al más simple, con etiquetas y cuál usa el automático."""
    from app.api import _video_info
    from app.engines.cloud import OpenRouterImageEngine, OpenRouterVideoEngine
    out = {"available": available(), "image": [], "video": [], "auto": {}}
    if not out["available"]:
        out["note"] = "Sin clave de OpenRouter o switch en Local: se usan los motores locales/asignados."
        return out

    def ranked(models, prefs):
        def order(m):
            low = str(m.get("id", "")).lower()
            return min([i for i, k in enumerate(prefs) if k in low] or [99])
        return sorted(models, key=order)

    try:
        img = OpenRouterImageEngine({})
        out["image"] = [image_info(m) for m in ranked(await img.list_models(), OpenRouterImageEngine.PREFERENCE)][:15]
        out["auto"]["image"] = await img.resolve_model()
    except Exception as e:  # noqa: BLE001
        out["image_error"] = str(e)[:200]
    try:
        vid = OpenRouterVideoEngine({})
        out["video"] = [_video_info(m) for m in ranked(await vid.list_models(), OpenRouterVideoEngine.PREFERENCE)][:15]
        out["auto"]["video"] = (await vid._pick())["id"]
    except Exception as e:  # noqa: BLE001
        out["video_error"] = str(e)[:200]
    return out


async def choice_for(image_model: str | None, video_model: str | None, current: str | None) -> tuple[str | None, str]:
    """Convierte la elección de Shelton en un choice del motor. Respeta el motor que el usuario fijó en el chat.

    Devuelve (choice, aviso). Los modelos que no existan en el catálogo se ignoran (queda el automático)."""
    if current or not (image_model or video_model) or not available():
        return current, ""
    cat = await catalog()
    ok_img = {m["id"] for m in cat.get("image", [])}
    ok_vid = {m["id"] for m in cat.get("video", [])}
    notes = []
    if image_model and image_model not in ok_img:
        notes.append(f"modelo de imagen «{image_model}» no disponible, uso el automático")
        image_model = None
    if video_model and video_model not in ok_vid:
        notes.append(f"modelo de video «{video_model}» no disponible, uso el automático")
        video_model = None
    return encode(image_model, video_model), "; ".join(notes)
