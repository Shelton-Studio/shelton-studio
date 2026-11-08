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


def get_engine(role: str = "generate", choice: str | None = None) -> ImageEngine:
    from app.connections import engine_for_role, list_enabled

    if choice == "openrouter" or (settings.IMAGE_ENGINE == "openrouter" and not choice and not engine_for_role(role)):
        # Imágenes con OpenRouter usando la clave del cerebro (sin GPU).
        if role in ("video", "sound"):
            raise ValueError("OpenRouter no genera video ni sonido: usa la GPU (ComfyUI) o una conexión fal.ai")
        key, kind, cfg = "openrouter", "openrouter_image", {}
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
