"""Motores de imagen. Todos exponen la misma interfaz para que la cadena no dependa del backend.

Elección del motor (de más a menos específica):
  1. la que elijas en el chat / en el pedido ("fast", "quality", "lite" o "conn:<id>")
  2. la conexión que tenga asignado el rol (generate / edit / subject) en Conexiones
  3. lo que diga .env (IMAGE_ENGINE + GEN_PRESET), por defecto ComfyUI local
"""
import asyncio
from typing import Awaitable, Callable, Protocol

from app.config import settings

Progress = Callable[[str], Awaitable[None]] | None


class ImageEngine(Protocol):
    name: str

    async def generate(self, prompt: str, width: int, height: int, seed: int, on_progress: Progress = None) -> bytes: ...
    async def edit(self, image: bytes, instruction: str, seed: int, on_progress: Progress = None) -> bytes: ...
    async def generate_with_refs(self, refs: list[bytes], prompt: str, width: int, height: int, seed: int,
                                 on_progress: Progress = None) -> bytes: ...
    async def health(self) -> dict: ...


# Cada GPU (cada ComfyUI) procesa de a un trabajo; con varias GPUs se reparten entre ellas.
_locks: dict[str, asyncio.Semaphore] = {}
_busy: dict[str, int] = {}


class gpu_slot:
    """`async with gpu_slot(url):` reserva la GPU de ese servidor y lleva la cuenta de trabajos en espera."""
    def __init__(self, key: str):
        self.key = key

    async def __aenter__(self):
        _busy[self.key] = _busy.get(self.key, 0) + 1
        await _locks.setdefault(self.key, asyncio.Semaphore(1)).acquire()

    async def __aexit__(self, *exc):
        _locks[self.key].release()
        _busy[self.key] -= 1


def busy(key: str) -> int:
    return _busy.get(key, 0)


GPU_LOCK = gpu_slot("local")  # compatibilidad: motor de prueba

PRESET_INFO = {
    "fast": ("Rápido · Z-Image Turbo", "Fotorrealista, ~5-10 s por imagen en una 3090. Ideal para volumen diario."),
    "quality": ("Calidad máxima · Qwen-Image 20B", "Más detalle y el mejor texto dentro de la imagen (carteles, frases). ~2-3x más lento."),
    "lite": ("Lite · Z-Image GGUF", "Para GPUs de 6-8 GB como la 3050. Algo menos de detalle."),
}
VIDEO_INFO = {
    "cinema": ("Video cine · Wan 2.2 14B", "Lo mejor en local: 720p, movimiento realista, interpolado a 30 fps. ~2-4 min por clip en una 3090."),
    "fast": ("Video rápido · Wan 2.2 5B", "720p 24 fps, ~1 min por clip. Para volumen."),
}

_cache: dict[str, ImageEngine] = {}


def _build(kind: str, cfg: dict) -> ImageEngine:
    if kind == "mock":
        from app.engines.mock import MockEngine
        return MockEngine()
    if kind == "comfyui":
        from app.engines.comfy import ComfyEngine
        return ComfyEngine(cfg)
    from app.engines.cloud import CLOUD_ENGINES
    if kind not in CLOUD_ENGINES:
        raise ValueError(f"Tipo de motor desconocido: {kind}")
    return CLOUD_ENGINES[kind](cfg)


MODES = {"auto": "Auto", "local": "Local", "cloud": "Nube"}


def infra_mode() -> str:
    """Switch global: local (solo tus máquinas), cloud (solo APIs en la nube) o auto (lo que haya, local primero)."""
    from app.db import get_or_create_brand, session
    with session() as s:
        m = (get_or_create_brand(s).infra_mode or "auto").lower()
    return m if m in MODES else "auto"


def _routed(role: str, mode: str):
    """Elige el motor para un rol según el switch. Devuelve (clave_cache, tipo, config)."""
    from app.connections import list_enabled
    from app.llm import openrouter_config
    conns = [c for c in list_enabled("engine") if role in (c.roles or [])]
    if mode == "local":
        local = [c for c in conns if c.type == "comfyui"]
        if local:
            c = min(local, key=lambda c: busy(c.config.get("url", c.id)))
            return f"conn:{c.id}:{c.config}", "comfyui", c.config
        if settings.IMAGE_ENGINE == "mock":
            return "mock", "mock", {}
        return "default", "comfyui", {}
    # nube
    cloud = [c for c in conns if c.type != "comfyui"]
    if cloud:
        c = cloud[0]
        return f"conn:{c.id}:{c.config}", c.type, c.config
    if role in ("generate", "edit", "subject") and openrouter_config()[0]:
        return "openrouter", "openrouter_image", {}
    if role == "video" and openrouter_config()[0]:
        return "openrouter_video", "openrouter_video", {}
    if settings.IMAGE_ENGINE == "mock":
        return "mock", "mock", {}
    need = {"video": "un motor de video en la nube (fal.ai o Replicate) con el rol «Video»",
            "sound": "un motor de sonido en la nube (o cambia a Local para usar Stable Audio en tu GPU)"}.get(
        role, "una clave de OpenRouter o una API de imagen")
    raise ValueError(f"Modo Nube: falta {need}. Agrégalo en el botón APIs.")


def get_engine(role: str = "generate", choice: str | None = None) -> ImageEngine:
    from app.connections import engine_for_role, list_enabled

    mode = infra_mode() if not choice else "auto"
    if mode in ("local", "cloud"):
        key, kind, cfg = _routed(role, mode)
        if key not in _cache:
            _cache[key] = _build(kind, cfg)
        return _cache[key]

    if choice == "openrouter" or (settings.IMAGE_ENGINE == "openrouter" and not choice and not engine_for_role(role)):
        # Imagen y video con OpenRouter usando la clave del cerebro (sin GPU).
        if role == "sound":
            raise ValueError("OpenRouter no genera efectos de sonido: usa la GPU (Stable Audio) o cambia el switch a Local")
        key, kind, cfg = ("openrouter_video", "openrouter_video", {}) if role == "video" else ("openrouter", "openrouter_image", {})
    elif settings.IMAGE_ENGINE == "mock" and not (choice or "").startswith("conn:") and not engine_for_role(role):
        key, kind, cfg = "mock", "mock", {}
    elif choice and choice.startswith("conn:"):
        conn = next((c for c in list_enabled("engine") if c.id == choice[5:]), None)
        if not conn:
            raise ValueError("La conexión elegida ya no existe o está desactivada")
        key, kind, cfg = f"{choice}:{conn.config}", conn.type, conn.config
    else:
        conn = _least_busy(role) or engine_for_role(role)
        if choice in PRESET_INFO:
            # Perfil local: usa el ComfyUI asignado (si hay) con ese perfil.
            base = conn.config if conn and conn.type == "comfyui" else {}
            key, kind, cfg = f"preset:{choice}:{base}", "comfyui", {**base, "preset": choice}
        elif conn:
            key, kind, cfg = f"conn:{conn.id}:{conn.config}", conn.type, conn.config
        else:
            key, kind, cfg = "default", "comfyui", {}
    if key not in _cache:
        _cache[key] = _build(kind, cfg)
    return _cache[key]


def _least_busy(role: str):
    """Con varias conexiones para el mismo rol (varias GPUs), elige la que tenga menos trabajos en espera."""
    from app.connections import list_enabled
    cands = [c for c in list_enabled("engine") if role in (c.roles or [])]
    if len(cands) < 2:
        return None
    return min(cands, key=lambda c: busy(c.config.get("url", c.id)))


def engine_choices() -> list[dict]:
    """Opciones para el selector del chat."""
    from app.connections import TYPES, list_enabled
    out = [{"id": "", "label": "Automático", "description": "Lo que tengas asignado en Conexiones o en .env.", "local": True}]
    out += [{"id": k, "label": v[0], "description": v[1], "local": True} for k, v in PRESET_INFO.items()]
    from app.llm import openrouter_config
    if openrouter_config()[0]:
        out.append({"id": "openrouter", "label": f"OpenRouter · {settings.OPENROUTER_IMAGE_MODEL}",
                    "description": "Imágenes en la nube con tu clave de OpenRouter (sin GPU). Cobra por imagen. "
                                   "Cambia el modelo en .env (OPENROUTER_IMAGE_MODEL) o agrega otra conexión «OpenRouter (imagen)».",
                    "local": False})
    for c in list_enabled("engine"):
        local = c.type == "comfyui"
        out.append({"id": f"conn:{c.id}", "label": f"{c.name or TYPES[c.type]['name']}",
                    "description": ("ComfyUI en " + c.config.get("url", "")) if local else f"{TYPES[c.type]['name']} (nube, tiene costo por imagen)",
                    "local": local})
    return out


def reset_cache() -> None:
    _cache.clear()


def engine_label(engine, role: str = "generate") -> str:
    """Nombre legible del modelo que usa un motor (para las etiquetas de cada post)."""
    from app.config import settings
    n = getattr(engine, "name", "?")
    cfg = getattr(engine, "cfg", {}) or {}
    if n == "mock":
        return "Simulado (demo)"
    if n == "openrouter_image":
        return cfg.get("model") or settings.OPENROUTER_IMAGE_MODEL
    if n == "openrouter_video":
        return getattr(engine, "last_model", None) or cfg.get("model") or settings.OPENROUTER_VIDEO_MODEL
    if n == "comfyui":
        if role == "video":
            return VIDEO_INFO.get(getattr(engine, "video_preset", ""), ("Wan 2.2",))[0]
        wf = getattr(engine, "workflows", {}).get("edit" if role == "edit" else "subject" if role == "subject" else "generate", "")
        return {"z_image_turbo.json": "Z-Image Turbo", "qwen_image.json": "Qwen-Image", "z_image_turbo_gguf.json": "Z-Image GGUF",
                "qwen_image_edit.json": "Qwen-Image-Edit", "qwen_subject.json": "Qwen-Image-Edit (referencias)"}.get(wf, wf or "ComfyUI")
    if n == "fal":
        return "fal.ai · " + (cfg.get("model_video") if role == "video" else cfg.get("model_generate") or "por defecto")
    return n


async def describe_role(role: str, choice: str | None = None) -> str:
    """Qué modelo se usaría ahora para un rol (en 'auto' de OpenRouter, consulta cuál es el mejor disponible)."""
    try:
        e = get_engine(role, choice)
        if hasattr(e, "_pick"):
            return (await e._pick())["id"]
        return engine_label(e, role)
    except Exception as err:
        return f"sin motor ({err})"
