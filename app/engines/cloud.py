"""Motores de imagen en la nube y genérico HTTP. Misma interfaz que ComfyUI.

El recorte final al formato de cada red lo hace Shelton, así que aquí basta con pedir la
proporción más cercana que acepte cada servicio.
"""
import asyncio
import base64
import json
import re

import httpx

from app.engines import Progress


def _b64(data: bytes) -> str:
    return base64.standard_b64encode(data).decode()


def _data_uri(data: bytes) -> str:
    return "data:image/png;base64," + _b64(data)


def _ratio(width: int, height: int) -> str:
    options = {"1:1": 1, "4:5": 0.8, "3:4": 0.75, "2:3": 2 / 3, "9:16": 0.5625, "16:9": 16 / 9, "3:2": 1.5, "4:3": 4 / 3, "5:4": 1.25}
    r = width / height
    return min(options, key=lambda k: abs(options[k] - r))


async def _fetch(url_or_data: str) -> bytes:
    if url_or_data.startswith("data:"):
        return base64.b64decode(url_or_data.split(",", 1)[1])
    if re.fullmatch(r"[A-Za-z0-9+/=\s]{200,}", url_or_data):
        return base64.b64decode(url_or_data)
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
        r = await c.get(url_or_data)
        r.raise_for_status()
        return r.content


def _dig(data, path: str):
    for part in [p for p in path.split(".") if p]:
        data = data[int(part)] if isinstance(data, list) else data[part]
    return data


class _Base:
    name = "cloud"

    def __init__(self, cfg: dict):
        self.cfg = cfg or {}

    async def generate_with_refs(self, refs, prompt, width, height, seed, on_progress: Progress = None) -> bytes:
        return await self._edit_many(refs, prompt, width, height, seed)

    async def edit(self, image, instruction, seed, on_progress: Progress = None) -> bytes:
        return await self._edit_many([image], instruction, 0, 0, seed)

    async def _edit_many(self, images, prompt, width, height, seed) -> bytes:
        raise RuntimeError(f"{self.name} no admite edición; asigna otro motor al rol 'editar'")

    async def video(self, *a, **kw):
        raise RuntimeError(f"{self.name} no genera video; asigna ComfyUI (Wan 2.2) o fal.ai al rol 'video'")

    async def health(self) -> dict:
        return {"ok": True, "engine": self.name}


# ------------------------------------------------------------------ fal.ai
class FalEngine(_Base):
    name = "fal"

    async def _run(self, model: str, payload: dict) -> bytes:
        async with httpx.AsyncClient(timeout=300) as c:
            r = await c.post(f"https://fal.run/{model}", json=payload,
                             headers={"Authorization": f"Key {self.cfg['api_key']}"})
            if r.status_code >= 400:
                raise RuntimeError(f"fal.ai {r.status_code}: {r.text[:400]}")
            j = r.json()
        return await _fetch(j["images"][0]["url"])

    async def generate(self, prompt, width, height, seed, on_progress: Progress = None) -> bytes:
        return await self._run(self.cfg.get("model_generate") or "fal-ai/qwen-image", {
            "prompt": prompt, "image_size": {"width": width, "height": height}, "seed": seed,
            "num_images": 1, "enable_safety_checker": True})

    async def _edit_many(self, images, prompt, width, height, seed) -> bytes:
        payload = {"prompt": prompt, "image_urls": [_data_uri(i) for i in images[:3]], "seed": seed, "num_images": 1}
        if width and height:
            payload["image_size"] = {"width": width, "height": height}
        return await self._run(self.cfg.get("model_edit") or "fal-ai/qwen-image-edit-plus", payload)

    async def video(self, image, prompt, negative, width, height, seed, preset=None, on_progress: Progress = None):
        model = self.cfg.get("model_video") or "fal-ai/kling-video/v2.1/standard/image-to-video"
        async with httpx.AsyncClient(timeout=900) as c:
            r = await c.post(f"https://fal.run/{model}", headers={"Authorization": f"Key {self.cfg['api_key']}"},
                             json={"prompt": prompt, "image_url": _data_uri(image), "duration": "5",
                                   "negative_prompt": negative})
            if r.status_code >= 400:
                raise RuntimeError(f"fal.ai {r.status_code}: {r.text[:400]}")
            j = r.json()
        return await _fetch(j["video"]["url"]), 30

    async def health(self) -> dict:
        return {"ok": bool(self.cfg.get("api_key")), "engine": self.name}


# ------------------------------------------------------------------ Replicate
class ReplicateEngine(_Base):
    name = "replicate"

    async def _run(self, model: str, inputs: dict) -> bytes:
        headers = {"Authorization": f"Bearer {self.cfg['api_token']}", "Prefer": "wait"}
        async with httpx.AsyncClient(timeout=300) as c:
            r = await c.post(f"https://api.replicate.com/v1/models/{model}/predictions", json={"input": inputs}, headers=headers)
            if r.status_code >= 400:
                raise RuntimeError(f"Replicate {r.status_code}: {r.text[:400]}")
            pred = r.json()
            while pred.get("status") in ("starting", "processing"):
                await asyncio.sleep(2)
                pred = (await c.get(pred["urls"]["get"], headers=headers)).json()
        if pred.get("status") != "succeeded":
            raise RuntimeError(f"Replicate: {pred.get('error') or pred.get('status')}")
        out = pred["output"]
        return await _fetch(out[0] if isinstance(out, list) else out)

    async def generate(self, prompt, width, height, seed, on_progress: Progress = None) -> bytes:
        return await self._run(self.cfg.get("model_generate") or "black-forest-labs/flux-1.1-pro",
                               {"prompt": prompt, "aspect_ratio": _ratio(width, height), "seed": seed, "output_format": "png"})

    async def _edit_many(self, images, prompt, width, height, seed) -> bytes:
        inputs = {"prompt": prompt, "image": _data_uri(images[0]), "seed": seed, "output_format": "png"}
        if width and height:
            inputs["aspect_ratio"] = _ratio(width, height)
        return await self._run(self.cfg.get("model_edit") or "qwen/qwen-image-edit", inputs)


# ------------------------------------------------------------------ OpenAI Images
class OpenAIImagesEngine(_Base):
    name = "openai_images"

    def _size(self, width, height) -> str:
        r = width / height if width and height else 1
        return "1024x1536" if r < 0.9 else "1536x1024" if r > 1.1 else "1024x1024"

    async def generate(self, prompt, width, height, seed, on_progress: Progress = None) -> bytes:
        async with httpx.AsyncClient(timeout=300) as c:
            r = await c.post("https://api.openai.com/v1/images/generations",
                             headers={"Authorization": f"Bearer {self.cfg['api_key']}"},
                             json={"model": self.cfg.get("model") or "gpt-image-1", "prompt": prompt,
                                   "size": self._size(width, height), "quality": self.cfg.get("quality") or "high", "n": 1})
            if r.status_code >= 400:
                raise RuntimeError(f"OpenAI {r.status_code}: {r.text[:400]}")
        return base64.b64decode(r.json()["data"][0]["b64_json"])

    async def _edit_many(self, images, prompt, width, height, seed) -> bytes:
        files = [("image[]", (f"ref{i}.png", img, "image/png")) for i, img in enumerate(images[:4])]
        data = {"model": self.cfg.get("model") or "gpt-image-1", "prompt": prompt,
                "size": self._size(width, height) if width else "auto", "quality": self.cfg.get("quality") or "high"}
        async with httpx.AsyncClient(timeout=300) as c:
            r = await c.post("https://api.openai.com/v1/images/edits", data=data, files=files,
                             headers={"Authorization": f"Bearer {self.cfg['api_key']}"})
            if r.status_code >= 400:
                raise RuntimeError(f"OpenAI {r.status_code}: {r.text[:400]}")
        return base64.b64decode(r.json()["data"][0]["b64_json"])


# ------------------------------------------------------------------ Gemini
class GeminiImageEngine(_Base):
    name = "gemini_image"

    async def _run(self, parts: list, width: int, height: int) -> bytes:
        model = self.cfg.get("model") or "gemini-2.5-flash-image"
        body = {"contents": [{"parts": parts}], "generationConfig": {"responseModalities": ["IMAGE"]}}
        if width and height:
            body["generationConfig"]["imageConfig"] = {"aspectRatio": _ratio(width, height)}
        async with httpx.AsyncClient(timeout=300) as c:
            r = await c.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                             headers={"x-goog-api-key": self.cfg["api_key"]}, json=body)
            if r.status_code >= 400:
                raise RuntimeError(f"Gemini {r.status_code}: {r.text[:400]}")
            j = r.json()
        for part in j.get("candidates", [{}])[0].get("content", {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline:
                return base64.b64decode(inline["data"])
        raise RuntimeError("Gemini no devolvió imagen (posible bloqueo de seguridad)")

    async def generate(self, prompt, width, height, seed, on_progress: Progress = None) -> bytes:
        return await self._run([{"text": prompt}], width, height)

    async def _edit_many(self, images, prompt, width, height, seed) -> bytes:
        parts = [{"inlineData": {"mimeType": "image/png", "data": _b64(i)}} for i in images[:3]] + [{"text": prompt}]
        return await self._run(parts, width, height)


# ------------------------------------------------------------------ HTTP genérico
class HttpEngine(_Base):
    """Cualquier API: plantilla JSON con marcadores y ruta a la imagen en la respuesta."""
    name = "http_engine"

    def _render(self, values: dict) -> dict:
        tpl = self.cfg.get("body") or {}
        if isinstance(tpl, str):
            tpl = json.loads(tpl)

        def sub(v):
            if isinstance(v, str):
                m = re.fullmatch(r"\{\{(\w+)\}\}", v)
                if m and m.group(1) in values:
                    return values[m.group(1)]
                return re.sub(r"\{\{(\w+)\}\}", lambda mm: str(values.get(mm.group(1), "")), v)
            if isinstance(v, dict):
                return {k: sub(x) for k, x in v.items()}
            if isinstance(v, list):
                return [sub(x) for x in v]
            return v
        return sub(tpl)

    async def _call(self, values: dict) -> bytes:
        headers = self.cfg.get("headers") or {}
        if isinstance(headers, str):
            headers = json.loads(headers or "{}")
        async with httpx.AsyncClient(timeout=300) as c:
            r = await c.post(self.cfg["url"], json=self._render(values), headers=headers)
            if r.status_code >= 400:
                raise RuntimeError(f"API {r.status_code}: {r.text[:400]}")
        if r.headers.get("content-type", "").startswith("image/"):
            return r.content
        return await _fetch(str(_dig(r.json(), self.cfg.get("result_path", ""))))

    async def generate(self, prompt, width, height, seed, on_progress: Progress = None) -> bytes:
        return await self._call({"prompt": prompt, "width": width, "height": height, "seed": seed,
                                 "aspect_ratio": _ratio(width, height)})

    async def _edit_many(self, images, prompt, width, height, seed) -> bytes:
        return await self._call({"prompt": prompt, "width": width, "height": height, "seed": seed,
                                 "image_b64": _b64(images[0]), "image_data_uri": _data_uri(images[0]),
                                 "aspect_ratio": _ratio(width, height) if width else "1:1"})


CLOUD_ENGINES = {"fal": FalEngine, "replicate": ReplicateEngine, "openai_images": OpenAIImagesEngine,
                 "gemini_image": GeminiImageEngine, "http_engine": HttpEngine}
