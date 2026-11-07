"""Motores de imagen. Todos exponen la misma interfaz para que la cadena no dependa del backend."""
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


# Una sola GPU: los trabajos de imagen pasan de a uno; lo demás (Claude) corre en paralelo.
GPU_LOCK = asyncio.Semaphore(1)

_engine: ImageEngine | None = None


def get_engine() -> ImageEngine:
    global _engine
    if _engine is None:
        if settings.IMAGE_ENGINE == "mock":
            from app.engines.mock import MockEngine
            _engine = MockEngine()
        else:
            from app.engines.comfy import ComfyEngine
            _engine = ComfyEngine()
    return _engine
