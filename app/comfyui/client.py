"""Cliente de la API de ComfyUI: encola workflows, espera el resultado y descarga las imágenes."""
import asyncio
import copy
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from app.config import settings

WORKFLOW_DIR = Path(__file__).parent / "workflows"
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


class ComfyUIError(RuntimeError):
    pass


def load_workflow(name: str) -> dict:
    p = Path(name)
    if not p.is_absolute():
        # Primero los que subiste desde la web (data/workflows), luego los incluidos.
        p = user_workflow_dir() / name if (user_workflow_dir() / name).exists() else WORKFLOW_DIR / name
    if not p.exists():
        raise ComfyUIError(f"No existe la plantilla de workflow: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}  # quita comentarios "_doc"


def user_workflow_dir() -> Path:
    d = settings.DATA_DIR / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    return d


def placeholders(workflow: dict) -> set[str]:
    return set(_PLACEHOLDER.findall(json.dumps(workflow)))


def fill_workflow(workflow: dict, values: dict[str, Any]) -> dict:
    """Reemplaza {{clave}}. Si el valor completo es un marcador, conserva el tipo (int, float...)."""
    def sub(v):
        if isinstance(v, str):
            m = _PLACEHOLDER.fullmatch(v)
            if m and m.group(1) in values:
                return values[m.group(1)]
            return _PLACEHOLDER.sub(lambda mm: str(values.get(mm.group(1), mm.group(0))), v)
        if isinstance(v, dict):
            return {k: sub(x) for k, x in v.items()}
        if isinstance(v, list):
            return [sub(x) for x in v]
        return v
    filled = sub(copy.deepcopy(workflow))
    missing = sorted(set(_PLACEHOLDER.findall(json.dumps(filled))))
    if missing:
        raise ComfyUIError(f"Faltan valores para el workflow: {missing}")
    return filled


class ComfyUIClient:
    def __init__(self, base_url: str | None = None, token: str | None = None):
        self.base = (base_url or settings.COMFYUI_URL).rstrip("/")
        self.headers = {"Authorization": f"Bearer {token or settings.COMFYUI_TOKEN}"} if (token or settings.COMFYUI_TOKEN) else {}
        self.client_id = uuid.uuid4().hex

    def _http(self, timeout: float = 30) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base, headers=self.headers, timeout=timeout)

    async def system_stats(self) -> dict:
        async with self._http(5) as c:
            r = await c.get("/system_stats")
            r.raise_for_status()
            return r.json()

    async def object_info(self, node: str | None = None) -> dict:
        async with self._http(60) as c:
            r = await c.get(f"/object_info/{node}" if node else "/object_info")
            r.raise_for_status()
            return r.json()

    async def upload_image(self, data: bytes, filename: str) -> str:
        async with self._http(60) as c:
            r = await c.post("/upload/image", files={"image": (filename, data, "image/png")},
                             data={"overwrite": "true", "type": "input"})
            r.raise_for_status()
            j = r.json()
            return f"{j['subfolder']}/{j['name']}" if j.get("subfolder") else j["name"]

    async def run(self, workflow: dict, on_progress=None) -> list[bytes]:
        """Ejecuta el workflow y devuelve los bytes de todas las imágenes de salida."""
        async with self._http(30) as c:
            r = await c.post("/prompt", json={"prompt": workflow, "client_id": self.client_id})
            if r.status_code != 200:
                raise ComfyUIError(_explain_prompt_error(r))
            prompt_id = r.json()["prompt_id"]

            start = time.monotonic()
            while time.monotonic() - start < settings.COMFYUI_TIMEOUT:
                await asyncio.sleep(0.75)
                h = await c.get(f"/history/{prompt_id}")
                if h.status_code != 200:
                    continue
                entry = h.json().get(prompt_id)
                if not entry:
                    if on_progress:
                        await on_progress(f"en GPU ({int(time.monotonic() - start)}s)")
                    continue
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    raise ComfyUIError(_explain_exec_error(status))
                if not status.get("completed", True):
                    continue
                images = []
                for node_out in entry.get("outputs", {}).values():
                    files = [f for key in ("images", "gifs", "videos", "audio") for f in node_out.get(key, [])
                             if isinstance(f, dict) and f.get("filename")]
                    for img in files:
                        if img.get("type") != "output":
                            continue
                        v = await c.get("/view", params={"filename": img["filename"],
                                                         "subfolder": img.get("subfolder", ""),
                                                         "type": img["type"]}, timeout=120)
                        v.raise_for_status()
                        images.append(v.content)
                if not images:
                    raise ComfyUIError("ComfyUI terminó pero no devolvió archivos (¿falta un nodo Save...?)")
                return images
            raise ComfyUIError(f"Tiempo de espera agotado ({settings.COMFYUI_TIMEOUT}s)")


def _explain_prompt_error(r: httpx.Response) -> str:
    try:
        j = r.json()
    except Exception:
        return f"ComfyUI rechazó el workflow ({r.status_code}): {r.text[:500]}"
    parts = [j.get("error", {}).get("message", "error")]
    for node_id, info in (j.get("node_errors") or {}).items():
        for e in info.get("errors", []):
            parts.append(f"nodo {node_id} ({info.get('class_type')}): {e.get('message')} {e.get('details', '')}".strip())
    return "ComfyUI rechazó el workflow: " + " | ".join(parts)


def _explain_exec_error(status: dict) -> str:
    for kind, data in status.get("messages", []):
        if kind == "execution_error":
            return f"Error en ComfyUI ({data.get('node_type')}): {data.get('exception_message', '').strip()[:400]}"
    return "Error de ejecución en ComfyUI"
