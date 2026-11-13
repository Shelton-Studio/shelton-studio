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


def _ratio(width: int, height: int, allowed: tuple | None = None) -> str:
    options = {"1:1": 1, "4:5": 0.8, "3:4": 0.75, "2:3": 2 / 3, "9:16": 0.5625, "16:9": 16 / 9, "3:2": 1.5, "4:3": 4 / 3, "5:4": 1.25}
    if allowed:
        options = {k: v for k, v in options.items() if k in allowed}
    r = width / height
    return min(options, key=lambda k: abs(options[k] - r))


# Proporciones que aceptan todos los modelos de Higgsfield (4:5 y 5:4 no): se usa la más parecida
HF_RATIOS = ("1:1", "3:4", "4:3", "2:3", "3:2", "9:16", "16:9")


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
    """fal.ai: cualquier modelo de su catálogo. Cada modelo pide campos un poco distintos: si uno rechaza el pedido
    (400/422), se reintenta con lo mínimo que todos entienden (prompt, imagen, proporción)."""
    name = "fal"

    @staticmethod
    def _aspect(width, height) -> str:
        if not (width and height):
            return "1:1"
        r = width / height
        return min({"9:16": 9 / 16, "3:4": 3 / 4, "1:1": 1, "4:3": 4 / 3, "16:9": 16 / 9}.items(),
                   key=lambda kv: abs(kv[1] - r))[0]

    async def _post(self, model: str, payloads: list[dict], timeout: float) -> dict:
        last = None
        async with httpx.AsyncClient(timeout=timeout) as c:
            for payload in payloads:
                r = await c.post(f"https://fal.run/{model.strip('/')}", json=payload,
                                 headers={"Authorization": f"Key {self.cfg['api_key']}"})
                if r.status_code in (400, 422):
                    last = r
                    continue
                if r.status_code >= 400:
                    raise RuntimeError(f"fal.ai {r.status_code} ({model}): {r.text[:400]}")
                return r.json()
        raise RuntimeError(f"fal.ai {last.status_code} ({model}): {last.text[:400]}")

    async def _run(self, model: str, payload: dict, minimal: dict | None = None) -> bytes:
        j = await self._post(model, [payload] + ([minimal] if minimal else []), 300)
        img = (j.get("images") or [None])[0] or j.get("image") or {}
        url = img.get("url") if isinstance(img, dict) else img
        if not url:
            raise RuntimeError(f"fal.ai ({model}) no devolvió imagen: {str(j)[:300]}")
        return await _fetch(url)

    def _extra(self) -> dict:
        import json as _json
        try:
            return _json.loads(self.cfg.get("extra") or "{}")
        except ValueError:
            return {}

    async def generate(self, prompt, width, height, seed, on_progress: Progress = None) -> bytes:
        model = self.cfg.get("model_generate") or "fal-ai/nano-banana-pro"
        return await self._run(model, {
            "prompt": prompt, "image_size": {"width": width, "height": height}, "seed": seed,
            "num_images": 1, "enable_safety_checker": True, **self._extra()},
            {"prompt": prompt, "aspect_ratio": self._aspect(width, height), "num_images": 1})

    async def _edit_many(self, images, prompt, width, height, seed) -> bytes:
        uris = [_data_uri(i) for i in images[:3]]
        payload = {"prompt": prompt, "image_urls": uris, "seed": seed, "num_images": 1, **self._extra()}
        if width and height:
            payload["image_size"] = {"width": width, "height": height}
        model = self.cfg.get("model_edit") or "fal-ai/nano-banana-pro/edit"
        j_payloads = [payload, {"prompt": prompt, "image_urls": uris, "num_images": 1},
                      {"prompt": prompt, "image_url": uris[0]}]        # modelos de una sola imagen (Kontext)
        j = await self._post(model, j_payloads, 300)
        img = (j.get("images") or [None])[0] or j.get("image") or {}
        url = img.get("url") if isinstance(img, dict) else img
        if not url:
            raise RuntimeError(f"fal.ai ({model}) no devolvió imagen: {str(j)[:300]}")
        return await _fetch(url)

    async def video(self, image, prompt, negative, width, height, seed, preset=None, on_progress: Progress = None):
        model = self.cfg.get("model_video") or "kling/v4/image-to-video"
        uri = _data_uri(image)
        j = await self._post(model, [
            {"prompt": prompt, "image_url": uri, "duration": "5", "negative_prompt": negative},
            {"prompt": prompt, "image_url": uri}], 900)
        vid = j.get("video") or {}
        url = vid.get("url") if isinstance(vid, dict) else vid
        if not url:
            raise RuntimeError(f"fal.ai ({model}) no devolvió video: {str(j)[:300]}")
        return await _fetch(url), 30

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


# ------------------------------------------------------------------ OpenRouter (imagen)
class OpenRouterImageEngine(_Base):
    """Modelos de imagen de OpenRouter (Gemini "Nano Banana", GPT Image, etc.) con la misma clave del cerebro.
    Genera, edita y crea escenas con referencias (manda las fotos junto al pedido)."""
    name = "openrouter_image"
    URL = "https://openrouter.ai/api/v1/chat/completions"
    transport = None  # las pruebas inyectan un transporte simulado

    def _key(self) -> str:
        if self.cfg.get("api_key"):
            return self.cfg["api_key"]
        from app.llm import openrouter_config
        key = openrouter_config()[0]
        if not key:
            raise RuntimeError("Falta la clave de OpenRouter (Conexiones → Cerebro o .env)")
        return key

    # Fotorrealismo primero: Nano Banana Pro (Gemini 3 Pro Image) > Nano Banana 2.x > GPT Image > Gemini 2.5 Flash Image
    PREFERENCE = ("gemini-3-pro-image", "nano-banana-pro", "gemini-3.1-pro-image", "nano-banana-2.1", "nano-banana-2",
                  "gemini-3.1-flash-image", "gemini-3-flash-image", "gpt-image-2", "gpt-image", "riverflow",
                  "gemini-2.5-flash-image")
    _models_cache: list | None = None

    async def list_models(self) -> list[dict]:
        """Modelos de OpenRouter que generan imágenes (lista pública)."""
        if OpenRouterImageEngine._models_cache is None:
            async with httpx.AsyncClient(timeout=30, transport=self.transport) as c:
                r = await c.get("https://openrouter.ai/api/v1/models", params={"output_modalities": "image"})
            data = r.json().get("data", []) if r.status_code < 400 else []
            OpenRouterImageEngine._models_cache = [
                m for m in data if "image" in ((m.get("architecture") or {}).get("output_modalities") or [])]
        return OpenRouterImageEngine._models_cache

    async def resolve_model(self) -> str:
        from app.config import settings
        wanted = self.cfg.get("model") or settings.OPENROUTER_IMAGE_MODEL
        if wanted and wanted != "auto":
            return wanted
        try:
            models = await self.list_models()
        except Exception:
            models = []
        ids = [str(m.get("id", "")) for m in models]
        for pref in self.PREFERENCE:
            hits = [i for i in ids if pref in i.lower()]
            if hits:
                return sorted(hits, key=lambda i: ("preview" in i, len(i)))[0]
        return "google/gemini-2.5-flash-image"

    async def _run(self, prompt: str, images: list[bytes], width: int, height: int) -> bytes:
        model = await self.resolve_model()
        self.last_model = model
        parts = [{"type": "image_url", "image_url": {"url": _data_uri(i)}} for i in images[:3]]
        ratio = _ratio(width, height) if width and height else None
        text = prompt + (f"\n\nAspect ratio {ratio}, high detail, no watermark." if ratio else "")
        body = {"model": model, "modalities": ["image", "text"], "usage": {"include": True},
                "messages": [{"role": "user", "content": [{"type": "text", "text": text}, *parts]}]}
        if ratio:
            body["image_config"] = {"aspect_ratio": ratio}
        async with httpx.AsyncClient(timeout=300, transport=self.transport) as c:
            r = await c.post(self.URL, json=body, headers={"Authorization": f"Bearer {self._key()}",
                                                           "X-Title": "Shelton Studio"})
        if r.status_code >= 400:
            raise RuntimeError(f"OpenRouter imagen {r.status_code}: {r.text[:300]}")
        from app.consumo import report_cost
        report_cost((r.json().get("usage") or {}).get("cost"))
        msg = r.json()["choices"][0]["message"]
        urls = [im.get("image_url", {}).get("url") for im in msg.get("images") or []]
        if not urls and isinstance(msg.get("content"), list):
            urls = [p.get("image_url", {}).get("url") for p in msg["content"] if p.get("type") == "image_url"]
        urls = [u for u in urls if u]
        if not urls:
            raise RuntimeError(f"El modelo {model} no devolvió imagen: {str(msg.get('content'))[:200]}")
        return await _fetch(urls[0])

    STYLE_WORDS = ("painting", "oil", "watercolor", "illustration", "anime", "cartoon", "3d", "render", "sketch",
                   "drawing", "pixel", "comic", "vector", "pintura", "óleo", "acuarela", "ilustración", "dibujo")
    REALISM = (" Ultra-realistic photograph indistinguishable from a real photo: natural skin texture with pores and "
               "small imperfections, real hair strands, true-to-life light and shadows, subtle film grain, real camera "
               "lens and depth of field. No plastic skin, no AI look, no oversaturation, no text or watermark.")

    def _realistic(self, prompt: str) -> str:
        p = prompt.lower()
        return prompt if any(w in p for w in self.STYLE_WORDS) else prompt + self.REALISM

    async def generate(self, prompt, width, height, seed, on_progress: Progress = None) -> bytes:
        return await self._run(self._realistic(prompt), [], width, height)

    async def _edit_many(self, images, prompt, width, height, seed) -> bytes:
        prompt = self._realistic(prompt) + " Keep everything not mentioned exactly as in the original."
        prefix = ("Edit the attached image: " if len(images) == 1 and not width else
                  "Create a new image keeping the identity of the subjects in the attached references: ")
        return await self._run(prefix + prompt, images, width, height)


# ------------------------------------------------------------------ OpenRouter (video)
class OpenRouterVideoEngine(_Base):
    """Video con OpenRouter (Veo, Sora, Seedance, Wan, ...), misma clave. Flujo asíncrono:
    POST /api/v1/videos -> consultar estado -> descargar. Imagen -> video usando la imagen como primer cuadro."""
    name = "openrouter_video"
    BASE = "https://openrouter.ai/api/v1"
    PREFERENCE = ("veo-3.1", "veo-3", "sora-2-pro", "sora-2", "seedance-2", "kling", "wan-2.7", "seedance", "wan")
    transport = None
    _models_cache: list | None = None

    def _key(self) -> str:
        return OpenRouterImageEngine(self.cfg)._key()

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._key()}", "X-Title": "Shelton Studio"}

    async def list_models(self) -> list[dict]:
        if OpenRouterVideoEngine._models_cache is None:
            async with httpx.AsyncClient(timeout=30, transport=self.transport) as c:
                r = await c.get(f"{self.BASE}/videos/models", headers=self._headers())
            if r.status_code >= 400:
                raise RuntimeError(f"OpenRouter video {r.status_code}: {r.text[:300]}")
            j = r.json()
            OpenRouterVideoEngine._models_cache = j.get("data", j) if isinstance(j, dict) else j
        return OpenRouterVideoEngine._models_cache

    async def _pick(self) -> dict:
        from app.config import settings
        wanted = self.cfg.get("model") or settings.OPENROUTER_VIDEO_MODEL
        models = await self.list_models()
        if wanted and wanted != "auto":
            return next((m for m in models if m.get("id") == wanted), {"id": wanted})
        light = ("fast", "lite", "turbo", "mini")
        for pref in self.PREFERENCE:
            hits = [m for m in models if pref in str(m.get("id", "")).lower()]
            if hits:
                # Calidad primero: la versión completa antes que "fast"/"lite"
                return sorted(hits, key=lambda m: any(w in str(m.get("id", "")).lower() for w in light))[0]
        if not models:
            raise RuntimeError("OpenRouter no devolvió modelos de video")
        return models[0]

    @staticmethod
    def _options(m: dict, *names) -> list:
        for n in names:
            v = m.get(n)
            if isinstance(v, list) and v:
                return v
        return []

    async def video(self, image, prompt, negative, width, height, seed, preset=None, on_progress: Progress = None):
        m = await self._pick()
        self.last_model = m["id"]
        ratio = _ratio(width, height) if width and height else "9:16"
        body = {"model": m["id"], "prompt": prompt + (f"\nAvoid: {negative}" if negative else ""),
                "frame_images": [{"type": "image_url", "image_url": {"url": _data_uri(image)}, "frame_type": "first_frame"}]}
        durations = self._options(m, "supported_durations", "durations")
        if durations:
            nums = [d for d in durations if isinstance(d, (int, float))]
            if nums:
                body["duration"] = min(nums, key=lambda d: (abs(d - 5), -d))  # ~5 s; ante empate, el más largo
        else:
            body["duration"] = 5
        ratios = self._options(m, "supported_aspect_ratios", "aspect_ratios")
        if not ratios or ratio in ratios:
            body["aspect_ratio"] = ratio
        elif "9:16" in ratios and height > width:
            body["aspect_ratio"] = "9:16"
        res = self._options(m, "supported_resolutions", "resolutions")
        for r in ("1080p", "720p"):
            if not res or r in res:
                body["resolution"] = r
                break
        async with httpx.AsyncClient(timeout=120, transport=self.transport) as c:
            r = await c.post(f"{self.BASE}/videos", json=body, headers=self._headers())
            if r.status_code >= 400:
                raise RuntimeError(f"OpenRouter video ({m['id']}) {r.status_code}: {r.text[:300]}")
            job = r.json()
            job_id = job.get("id") or job.get("job_id")
            poll = job.get("polling_url") or f"{self.BASE}/videos/{job_id}"
            for i in range(120):  # hasta ~20 minutos
                if job.get("status") == "completed":
                    break
                if job.get("status") in ("failed", "cancelled", "expired"):
                    raise RuntimeError(f"OpenRouter video {job.get('status')}: {job.get('error') or ''}")
                if on_progress:
                    await on_progress(f"{m['id']} · {job.get('status', 'en cola')} ({i * 10}s)")
                await asyncio.sleep(10 if i else 5)
                pr = await c.get(poll, headers=self._headers())
                if pr.status_code >= 400:
                    raise RuntimeError(f"OpenRouter video {pr.status_code}: {pr.text[:300]}")
                job = pr.json()
            else:
                raise RuntimeError("OpenRouter video: tiempo de espera agotado")
            from app.consumo import report_cost
            report_cost((job.get("usage") or {}).get("cost") or job.get("cost"))
            urls = job.get("unsigned_urls") or job.get("urls") or []
            url = urls[0] if urls else f"{self.BASE}/videos/{job_id}/content?index=0"
            v = await c.get(url, headers=self._headers(), follow_redirects=True, timeout=300)
            if v.status_code >= 400:
                raise RuntimeError(f"OpenRouter video: no se pudo descargar ({v.status_code})")
        return v.content, 30



# ------------------------------------------------------------------ Higgsfield
class HiggsfieldEngine(_Base):
    """Higgsfield (api.higgsfield.ai): se envía el pedido al modelo y se consulta hasta que termina.
    Clave: Key ID + Key Secret de console.higgsfield.ai (cabecera «Authorization: Key ID:SECRET»).
    Los modelos se eligen en la conexión (ej. «higgsfield-ai/soul/v2/standard», «bytedance/seedream/v4/edit»,
    «wan/v2.7/image-to-video»). Campos extra de cada modelo: JSON en «Parámetros extra»."""
    name = "higgsfield"
    BASE = "https://api.higgsfield.ai"
    transport = None
    POLL = 3.0

    def _headers(self) -> dict:
        key, secret = (self.cfg.get("api_key") or "").strip(), (self.cfg.get("api_secret") or "").strip()
        cred = key if ":" in key or not secret else f"{key}:{secret}"   # la clave completa ya trae «ID:SECRET»
        return {"Authorization": f"Key {cred}", "Content-Type": "application/json"}

    def _extra(self, kind: str) -> dict:
        raw = self.cfg.get(f"extra_{kind}") or self.cfg.get("extra") or ""
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except ValueError:
            raise RuntimeError("Higgsfield: «Parámetros extra» no es un JSON válido (ej: {\"resolution\": \"2K\"})")

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self.transport, follow_redirects=True)

    # Ajustes por defecto para sacar la máxima calidad (como en la web de Higgsfield). Si algún modelo no los acepta,
    # se reintenta solo sin ellos: nunca rompe el pedido.
    BEST = {"generate": {"resolution": "1080p"}, "edit": {"resolution": "2k"}, "video": {"duration": 5}}

    def _best(self, kind: str, model: str) -> dict:
        best = dict(self.BEST[kind])
        if "resolution" in best:
            best["resolution"] = "1080p" if model.startswith("higgsfield-ai/soul") else "2k"
        return best

    async def _run(self, model: str, body: dict, limit_s: float, on_progress: Progress = None,
                   optional: tuple = ()) -> dict:
        model = model.strip("/")
        async with self._client(120) as c:
            r = await c.post(f"{self.BASE}/{model}", json=body, headers=self._headers())
            if r.status_code in (400, 422) and any(k in body for k in optional):
                body = {k: v for k, v in body.items() if k not in optional}
                r = await c.post(f"{self.BASE}/{model}", json=body, headers=self._headers())
            if r.status_code in (401, 403):
                raise RuntimeError("Higgsfield rechazó la clave: revisa que pegaste la clave completa en Conexiones")
            if r.status_code >= 400:
                raise RuntimeError(f"Higgsfield {r.status_code} ({model}): {r.text[:400]}")
            j = r.json()
            from app.consumo import report_cost
            if self._outputs(j):
                return j
            rid = j.get("request_id") or j.get("id")
            url = j.get("status_url") or f"{self.BASE}/requests/{rid}/status"
            start = asyncio.get_event_loop().time()
            while asyncio.get_event_loop().time() - start < limit_s:
                await asyncio.sleep(self.POLL)
                s = await c.get(url, headers=self._headers())
                if s.status_code >= 400:
                    raise RuntimeError(f"Higgsfield (estado) {s.status_code}: {s.text[:300]}")
                j = s.json()
                st = str(j.get("status") or "").lower()
                if st == "completed" or (not st and self._outputs(j)):
                    report_cost(j.get("cost") or (j.get("usage") or {}).get("cost"))
                    return j
                if st in ("failed", "canceled", "cancelled"):
                    raise RuntimeError(f"Higgsfield: el pedido terminó como «{st}». {j.get('error') or ''}".strip())
                if st == "nsfw":
                    raise RuntimeError("Higgsfield bloqueó el resultado por contenido sensible (nsfw). Cambia la idea o el prompt.")
                if on_progress:
                    await on_progress(f"Higgsfield · {st or 'en cola'} ({int(asyncio.get_event_loop().time() - start)}s)")
            try:
                await c.post(f"{self.BASE}/requests/{rid}/cancel", headers=self._headers())
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError("Higgsfield: tiempo de espera agotado")

    @staticmethod
    def _outputs(j: dict):
        out = j.get("output") if isinstance(j.get("output"), dict) else j
        return out.get("images") or out.get("video") or out.get("videos")

    @staticmethod
    def _image_url(j: dict) -> str:
        out = j.get("output") if isinstance(j.get("output"), dict) else j
        imgs = out.get("images") or []
        first = imgs[0] if imgs else None
        url = first.get("url") if isinstance(first, dict) else first
        if not url:
            raise RuntimeError(f"Higgsfield terminó pero no devolvió imagen: {str(j)[:200]}")
        return url

    @staticmethod
    def _video_url(j: dict) -> str:
        out = j.get("output") if isinstance(j.get("output"), dict) else j
        v = out.get("video") or (out.get("videos") or [None])[0]
        url = v.get("url") if isinstance(v, dict) else v
        if not url:
            raise RuntimeError(f"Higgsfield terminó pero no devolvió video: {str(j)[:200]}")
        return url

    async def _upload(self, data: bytes) -> str:
        """Sube una imagen propia a Higgsfield (URL firmada) y devuelve la URL para usarla en el pedido."""
        if (self.cfg.get("upload") or "").lower() == "data":
            return _data_uri(data)
        async with self._client(120) as c:
            r = await c.post(f"{self.BASE}/files/generate-upload-url", json={"content_type": "image/png"},
                             headers=self._headers())
            if r.status_code >= 400:
                raise RuntimeError(f"Higgsfield (subir imagen) {r.status_code}: {r.text[:300]}")
            j = r.json()
            up = j.get("upload_url") or j.get("url")
            pub = j.get("public_url") or j.get("file_url") or j.get("download_url") or j.get("url")
            if not up or not pub:
                raise RuntimeError(f"Higgsfield (subir imagen): respuesta inesperada {str(j)[:200]}")
            p = await c.put(up, content=data, headers={"Content-Type": "image/png"})
            if p.status_code >= 400:
                raise RuntimeError(f"Higgsfield (subir imagen) {p.status_code}")
        return pub

    async def generate(self, prompt, width, height, seed, on_progress: Progress = None) -> bytes:
        model = self.cfg.get("model_generate") or "higgsfield-ai/soul/v2/standard"
        self.last_model = model
        extra = self._extra("generate")
        best = {k: v for k, v in self._best("generate", model).items() if k not in extra}
        body = {"prompt": prompt, "aspect_ratio": _ratio(width, height, HF_RATIOS) if width and height else "3:4", **best, **extra}
        j = await self._run(model, body, 600, on_progress, optional=tuple(best))
        return await _fetch(self._image_url(j))

    async def _edit_many(self, images, prompt, width, height, seed) -> bytes:
        model = self.cfg.get("model_edit") or "alibaba/qwen-image-3/edit"
        self.last_model = model
        urls = [await self._upload(i) for i in images[:4]]
        extra = self._extra("edit")
        best = {k: v for k, v in self._best("edit", model).items() if k not in extra}
        body = {"prompt": prompt, self.cfg.get("images_field") or "image_urls": urls, **best, **extra}
        if width and height:
            body.setdefault("aspect_ratio", _ratio(width, height, HF_RATIOS))
        j = await self._run(model, body, 600, optional=tuple(best))
        return await _fetch(self._image_url(j))

    async def video(self, image, prompt, negative, width, height, seed, preset=None, on_progress: Progress = None):
        model = self.cfg.get("model_video") or "wan/v2.7/image-to-video"
        self.last_model = model
        url = await self._upload(image)
        extra = self._extra("video")
        best = {k: v for k, v in self.BEST["video"].items() if k not in extra}
        field = self.cfg.get("image_field") or "image_url"
        body = {"prompt": prompt + (f"\nAvoid: {negative}" if negative else ""),
                field: [url] if field.endswith("s") else url, **best, **extra}     # «image_urls» lleva lista
        j = await self._run(model, body, 1500, on_progress, optional=tuple(best))
        return await _fetch(self._video_url(j)), 30

    async def health(self) -> dict:
        ok = bool(self.cfg.get("api_key"))
        return {"ok": ok, "engine": self.name, **({} if ok else {"error": "Falta la clave de Higgsfield"})}


# ------------------------------------------------------------------ WaveSpeed
class WaveSpeedEngine(_Base):
    """WaveSpeed (api.wavespeed.ai): Seedream 5.5, Wan, Seedance, Kling, Flux… con una sola clave.
    Se envía el pedido al modelo (POST /api/v3/<modelo>) y se consulta el resultado hasta que termina."""
    name = "wavespeed"
    BASE = "https://api.wavespeed.ai/api/v3"
    transport = None
    POLL = 2.0

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {(self.cfg.get('api_key') or '').strip()}", "Content-Type": "application/json"}

    def _extra(self) -> dict:
        raw = self.cfg.get("extra") or ""
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except ValueError:
            raise RuntimeError("WaveSpeed: «Parámetros extra» no es un JSON válido (ej: {\"output_format\": \"jpeg\"})")

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self.transport, follow_redirects=True)

    @staticmethod
    def _data(j: dict) -> dict:
        return j.get("data") if isinstance(j.get("data"), dict) else j

    async def _run(self, model: str, body: dict, limit_s: float, on_progress: Progress = None) -> str:
        model = model.strip("/")
        async with self._client(120) as c:
            r = await c.post(f"{self.BASE}/{model}", json=body, headers=self._headers())
            if r.status_code in (401, 403):
                raise RuntimeError("WaveSpeed rechazó la clave: revísala en Conexiones")
            if r.status_code == 402:
                raise RuntimeError("WaveSpeed: sin saldo. Recarga créditos en wavespeed.ai")
            if r.status_code >= 400:
                raise RuntimeError(f"WaveSpeed {r.status_code} ({model}): {r.text[:400]}")
            d = self._data(r.json())
            start = asyncio.get_event_loop().time()
            url = (d.get("urls") or {}).get("get") or f"{self.BASE}/predictions/{d.get('id')}/result"
            while True:
                st = str(d.get("status") or "").lower()
                if st == "completed" and d.get("outputs"):
                    out = d["outputs"][0]
                    return out.get("url") if isinstance(out, dict) else out
                if st in ("failed", "cancelled", "canceled", "timeout", "deleted"):
                    raise RuntimeError(f"WaveSpeed: el pedido terminó como «{st}». {d.get('error') or ''}".strip())
                if asyncio.get_event_loop().time() - start > limit_s:
                    raise RuntimeError("WaveSpeed: tiempo de espera agotado")
                if on_progress:
                    await on_progress(f"WaveSpeed · {st or 'en cola'} ({int(asyncio.get_event_loop().time() - start)}s)")
                await asyncio.sleep(self.POLL)
                s = await c.get(url, headers=self._headers())
                if s.status_code >= 400:
                    raise RuntimeError(f"WaveSpeed (estado) {s.status_code}: {s.text[:300]}")
                d = self._data(s.json())

    async def _upload(self, data: bytes) -> str:
        """Sube una imagen propia y devuelve su URL. Si la subida no está disponible, va como data URI."""
        if (self.cfg.get("upload") or "").lower() == "data":
            return _data_uri(data)
        try:
            async with self._client(120) as c:
                r = await c.post(f"{self.BASE}/media/upload/binary", files={"file": ("imagen.png", data, "image/png")},
                                 headers={"Authorization": self._headers()["Authorization"]})
            if r.status_code < 400:
                d = self._data(r.json())
                url = d.get("download_url") or d.get("url")
                if url:
                    return url
        except httpx.HTTPError:
            pass
        return _data_uri(data)

    @staticmethod
    def _size(width: int, height: int, long_side: int = 2048) -> str:
        if not (width and height):
            return f"{long_side}*{long_side}"
        k = long_side / max(width, height)
        return f"{int(width * k) // 16 * 16}*{int(height * k) // 16 * 16}"

    async def generate(self, prompt, width, height, seed, on_progress: Progress = None) -> bytes:
        model = self.cfg.get("model_generate") or "bytedance/seedream-v5.5"
        self.last_model = model
        body = {"prompt": prompt, "size": self._size(width, height), "enable_sync_mode": False, **self._extra()}
        return await _fetch(await self._run(model, body, 600, on_progress))

    async def _edit_many(self, images, prompt, width, height, seed) -> bytes:
        model = self.cfg.get("model_edit") or "bytedance/seedream-v5.5/edit"
        self.last_model = model
        urls = [await self._upload(i) for i in images[:10]]
        body = {"prompt": prompt, "images": urls, "enable_sync_mode": False, **self._extra()}
        if width and height:
            body.setdefault("size", self._size(width, height))
        return await _fetch(await self._run(model, body, 600))

    async def video(self, image, prompt, negative, width, height, seed, preset=None, on_progress: Progress = None):
        model = self.cfg.get("model_video") or "wavespeed-ai/wan-2.2/i2v-720p"
        self.last_model = model
        body = {"prompt": prompt, "image": await self._upload(image), "duration": 5, **self._extra()}
        if negative:
            body.setdefault("negative_prompt", negative)
        return await _fetch(await self._run(model, body, 1500, on_progress)), 30

    async def health(self) -> dict:
        ok = bool(self.cfg.get("api_key"))
        return {"ok": ok, "engine": self.name, **({} if ok else {"error": "Falta la clave de WaveSpeed"})}

CLOUD_ENGINES = {"wavespeed": WaveSpeedEngine, "higgsfield": HiggsfieldEngine, "openrouter_video": OpenRouterVideoEngine, "openrouter_image": OpenRouterImageEngine, "fal": FalEngine, "replicate": ReplicateEngine, "openai_images": OpenAIImagesEngine,
                 "gemini_image": GeminiImageEngine, "http_engine": HttpEngine}
