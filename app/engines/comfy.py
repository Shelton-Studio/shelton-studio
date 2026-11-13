"""Motor local: ComfyUI con Z-Image Turbo / Qwen-Image (generar) y Qwen-Image-Edit (editar y referencias).

Se configura desde .env o desde una conexión "ComfyUI" en la web (puedes tener varias: 3050, 3090...).
"""
import os
import uuid

from app.comfyui.client import ComfyUIClient, ComfyUIError, fill_workflow, load_workflow, placeholders
from app.config import settings
from app.engines import Progress, gpu_slot

LOADER_FIELDS = {"UNETLoader": "unet_name", "CLIPLoader": "clip_name", "VAELoader": "vae_name",
                 "CheckpointLoaderSimple": "ckpt_name",
                 "LoraLoaderModelOnly": "lora_name", "UnetLoaderGGUF": "unet_name"}

# Perfiles de generación listos: el workflow y sus modelos.
PRESETS = {
    "fast": {"workflow_generate": "z_image_turbo.json", "gen_unet": settings.GEN_UNET, "gen_clip": settings.GEN_CLIP,
             "gen_vae": settings.GEN_VAE, "gen_steps": settings.GEN_STEPS},
    "quality": {"workflow_generate": "qwen_image.json", "gen_unet": settings.QWEN_IMAGE_UNET, "gen_clip": settings.EDIT_CLIP,
                "gen_vae": settings.EDIT_VAE, "gen_lora": settings.QWEN_IMAGE_LORA, "gen_steps": settings.QWEN_IMAGE_STEPS},
    "lite": {"workflow_generate": "z_image_turbo_gguf.json", "gen_unet": "z_image_turbo-Q4_K_M.gguf",
             "gen_clip": settings.GEN_CLIP, "gen_vae": settings.GEN_VAE, "gen_steps": settings.GEN_STEPS},
    # SDXL (RealVisXL u otro checkpoint: Pony, CyberRealistic… en models/checkpoints)
    "sdxl": {"workflow_generate": "sdxl.json", "gen_ckpt": os.getenv("SDXL_CKPT", "RealVisXL_V5.0_fp16.safetensors"),
             "gen_steps": int(os.getenv("SDXL_STEPS", "30")), "gen_cfg": float(os.getenv("SDXL_CFG", "5.0"))},
    # Flux.1 Dev (checkpoint fp8 todo en uno de Comfy-Org)
    "flux": {"workflow_generate": "flux_dev.json", "gen_ckpt": os.getenv("FLUX_CKPT", "flux1-dev-fp8.safetensors"),
             "gen_steps": int(os.getenv("FLUX_STEPS", "20")), "gen_guidance": float(os.getenv("FLUX_GUIDANCE", "3.5"))},
}
# Arquitectura de cada perfil: una LoRA solo sirve en el mismo modelo base con el que se entrenó
PRESET_ARCH = {"fast": "zimage", "lite": "zimage", "quality": "qwen", "sdxl": "sdxl", "flux": "flux"}
SDXL_NEGATIVE = ("lowres, blurry, bad anatomy, bad hands, extra fingers, deformed, disfigured, watermark, text, "
                 "logo, cartoon, 3d render, plastic skin, oversaturated")


VIDEO_PRESETS = {
    "cinema": {"workflow": "wan22_i2v_14b.json", "fps": 16, "size": (720, 1280),
               "values": {"unet_high": settings.VIDEO_HIGH_UNET, "unet_low": settings.VIDEO_LOW_UNET,
                          "lora_high": settings.VIDEO_HIGH_LORA, "lora_low": settings.VIDEO_LOW_LORA,
                          "clip": settings.VIDEO_CLIP, "vae": settings.VIDEO_VAE, "steps": 4}},
    "fast": {"workflow": "wan22_ti2v_5b.json", "fps": 24, "size": (704, 1280),
             "values": {"unet": settings.VIDEO_FAST_UNET, "clip": settings.VIDEO_CLIP, "vae": settings.VIDEO_FAST_VAE,
                        "steps": 20}},
}


class ComfyEngine:
    name = "comfyui"

    def __init__(self, cfg: dict | None = None):
        cfg = {k: v for k, v in (cfg or {}).items() if v not in (None, "")}
        pname = cfg.get("preset") or settings.GEN_PRESET
        preset = PRESETS.get(pname, {})
        self.arch = PRESET_ARCH.get(pname, "zimage") if preset else (cfg.get("arch") or "zimage")
        if not cfg.get("preset") and settings.GEN_PRESET == "custom":
            preset = {}
        cfg = {**preset, **cfg}
        self.url = cfg.get("url", settings.COMFYUI_URL)
        self.client = ComfyUIClient(self.url, cfg.get("token"))
        self.workflows = {
            "generate": cfg.get("workflow_generate", settings.WORKFLOW_GENERATE),
            "edit": cfg.get("workflow_edit", settings.WORKFLOW_EDIT),
            "subject": cfg.get("workflow_subject", settings.WORKFLOW_SUBJECT),
        }
        self.scale = float(cfg.get("gen_scale", settings.GEN_SCALE))
        self.video_preset = cfg.get("video_preset", settings.VIDEO_PRESET)
        self.values = {
            "generate": {"unet": cfg.get("gen_unet", settings.GEN_UNET), "clip": cfg.get("gen_clip", settings.GEN_CLIP),
                         "vae": cfg.get("gen_vae", settings.GEN_VAE), "steps": int(cfg.get("gen_steps", settings.GEN_STEPS)),
                         "lora": cfg.get("gen_lora", settings.QWEN_IMAGE_LORA),
                         "ckpt": cfg.get("gen_ckpt", ""), "cfg": float(cfg.get("gen_cfg", 5.0)),
                         "guidance": float(cfg.get("gen_guidance", 3.5)),
                         "negative": cfg.get("gen_negative", SDXL_NEGATIVE)},
            "edit": {"unet": settings.EDIT_UNET, "clip": settings.EDIT_CLIP, "vae": settings.EDIT_VAE,
                     "lora": settings.EDIT_LORA, "steps": settings.EDIT_STEPS},
        }
        self.values["subject"] = self.values["edit"]

    def _wf(self, role: str, extra: dict) -> dict:
        wf = load_workflow(self.workflows[role])
        values = {**self.values[role], **extra}
        return fill_workflow(wf, {k: v for k, v in values.items() if k in placeholders(wf)})

    def _size(self, width: int, height: int) -> tuple[int, int]:
        return max(256, int(width * self.scale) // 16 * 16), max(256, int(height * self.scale) // 16 * 16)

    async def generate(self, prompt, width, height, seed, on_progress: Progress = None) -> bytes:
        w, h = self._size(width, height)
        wf = self._wf("generate", {"prompt": prompt, "width": w, "height": h, "seed": seed})
        from app import calidad
        await calidad.apply(wf, self.client)
        async with gpu_slot(self.url):
            return (await self.client.run(wf, on_progress))[0]

    async def generate_with_loras(self, loras: list[tuple[str, float]], prompt, width, height, seed,
                                  on_progress: Progress = None, quality: bool | None = None) -> bytes:
        """Generar con LoRAs propias del banco (aprendidas en la GPU): se enchufan después del modelo base."""
        hook = getattr(self, "ensure_loras", None)
        if hook and loras:
            await hook([name for name, _ in loras])        # en una tarjeta alquilada: le copia las LoRA que falten
        w, h = self._size(width, height)
        wf = self._wf("generate", {"prompt": prompt, "width": w, "height": h, "seed": seed})
        inject_loras(wf, loras)
        if quality is not False:          # el comparador de versiones genera sin calidad máxima (más rápido y parejo)
            from app import calidad
            await calidad.apply(wf, self.client)
        async with gpu_slot(self.url):
            return (await self.client.run(wf, on_progress))[0]

    async def edit(self, image, instruction, seed, on_progress: Progress = None) -> bytes:
        async with gpu_slot(self.url):
            name = await self.client.upload_image(image, f"shelton_{uuid.uuid4().hex[:10]}.png")
            wf = self._wf("edit", {"image": name, "instruction": instruction, "prompt": instruction, "seed": seed})
            return (await self.client.run(wf, on_progress))[0]

    async def generate_with_refs(self, refs, prompt, width, height, seed, on_progress: Progress = None) -> bytes:
        """Escena nueva manteniendo la identidad de hasta 3 referencias (personaje, auto, lugar, estilo...)."""
        w, h = self._size(width, height)
        async with gpu_slot(self.url):
            names = [await self.client.upload_image(r, f"shelton_ref_{uuid.uuid4().hex[:10]}.png") for r in refs[:3]]
            wf = self._wf("subject", {"image": names[0], "prompt": prompt, "instruction": prompt,
                                      "width": w, "height": h, "seed": seed})
            _attach_extra_refs(wf, names[1:])
            return (await self.client.run(wf, on_progress))[0]

    async def video(self, image, prompt, negative, width, height, seed, preset: str | None = None,
                    on_progress: Progress = None) -> tuple[bytes, float]:
        """Imagen -> clip. Devuelve (WebP animado, fps)."""
        preset = preset if preset in VIDEO_PRESETS else self.video_preset
        vp = VIDEO_PRESETS.get(preset, VIDEO_PRESETS["cinema"])
        fps = vp["fps"]
        length = int(settings.VIDEO_SECONDS * fps) // 4 * 4 + 1  # Wan pide 4n+1 cuadros
        w, h = vp["size"] if height > width else vp["size"][::-1]
        async with gpu_slot(self.url):
            name = await self.client.upload_image(image, f"shelton_vid_{uuid.uuid4().hex[:10]}.png")
            wf = load_workflow(vp["workflow"])
            values = {**vp["values"], "image": name, "prompt": prompt, "negative": negative, "width": w, "height": h,
                      "length": length, "seed": seed, "fps": fps}
            wf = fill_workflow(wf, {k: v for k, v in values.items() if k in placeholders(wf)})
            return (await self.client.run(wf, on_progress))[0], fps

    async def sound(self, prompt: str, negative: str, seconds: float, seed: int, on_progress: Progress = None) -> bytes:
        """Efecto de sonido / ambiente con Stable Audio Open. Devuelve audio (FLAC)."""
        wf = load_workflow(settings.WORKFLOW_SFX)
        values = {"ckpt": settings.SFX_CKPT, "clip": settings.SFX_CLIP, "prompt": prompt, "negative": negative,
                  "seconds": float(max(1.0, min(seconds, 47.0))), "seed": seed, "steps": settings.SFX_STEPS}
        wf = fill_workflow(wf, {k: v for k, v in values.items() if k in placeholders(wf)})
        async with gpu_slot(self.url):
            return (await self.client.run(wf, on_progress))[0]

    async def health(self) -> dict:
        """Comprueba conexión, GPU, modelos descargados y que los workflows encajen con los nodos instalados."""
        try:
            stats = await self.client.system_stats()
        except Exception as e:
            return {"ok": False, "engine": self.name, "url": self.url, "error": f"No conecta con ComfyUI en {self.url}: {e}"}
        devices = [{"name": d.get("name"), "vram_gb": round(d.get("vram_total", 0) / 1024**3, 1)}
                   for d in stats.get("devices", [])]
        try:
            info = await self.client.object_info()
        except Exception as e:
            return {"ok": False, "engine": self.name, "url": self.url, "devices": devices, "error": f"/object_info: {e}"}
        missing, problems = set(), []
        checks = []
        for role in ("generate", "edit", "subject"):
            try:
                checks.append((self.workflows[role], self._wf(role, {"prompt": "x", "instruction": "x", "image": "x.png",
                                                                    "width": 1024, "height": 1024, "seed": 1})))
            except ComfyUIError as e:
                problems.append(f"{self.workflows[role]}: {e}")
        vp = VIDEO_PRESETS.get(settings.VIDEO_PRESET, VIDEO_PRESETS["cinema"])
        try:
            vwf = load_workflow(vp["workflow"])
            checks.append((vp["workflow"], fill_workflow(vwf, {k: v for k, v in {**vp["values"], "image": "x.png", "prompt": "x",
                           "negative": "", "width": 720, "height": 1280, "length": 81, "seed": 1, "fps": 16}.items()
                           if k in placeholders(vwf)})))
        except ComfyUIError as e:
            problems.append(f"video: {e}")
        try:
            swf = load_workflow(settings.WORKFLOW_SFX)
            checks.append((settings.WORKFLOW_SFX, fill_workflow(swf, {k: v for k, v in {
                "ckpt": settings.SFX_CKPT, "clip": settings.SFX_CLIP, "prompt": "x", "negative": "", "seconds": 5.0,
                "seed": 1, "steps": 10}.items() if k in placeholders(swf)})))
        except ComfyUIError as e:
            problems.append(f"sonido: {e}")
        for wf_name, wf in checks:
            role = wf_name
            for node_id, node in wf.items():
                cls = node.get("class_type")
                if cls not in info:
                    problems.append(f"{role}: falta el nodo '{cls}' (actualiza ComfyUI o instala el nodo)")
                    continue
                required = info[cls].get("input", {}).get("required", {})
                for field in required:
                    if field not in node.get("inputs", {}):
                        problems.append(f"{role}: al nodo {node_id} ({cls}) le falta la entrada '{field}'")
                field = LOADER_FIELDS.get(cls)
                if field and field in required:
                    options = required[field][0]
                    if isinstance(options, str):  # formato nuevo: ["COMBO", {"options": [...]}]
                        options = (required[field][1] or {}).get("options", [])
                    wanted = node["inputs"].get(field)
                    if wanted and wanted not in options:
                        missing.add(wanted)
        return {"ok": not missing and not problems, "engine": self.name, "url": self.url, "devices": devices,
                "missing_models": sorted(missing), "workflow_problems": problems, "workflows": self.workflows}


def _attach_extra_refs(wf: dict, names: list[str]) -> None:
    """Conecta referencias 2 y 3 a los nodos TextEncodeQwenImageEditPlus como image2/image3."""
    if not names:
        return
    encoders = [k for k, n in wf.items() if n.get("class_type") == "TextEncodeQwenImageEditPlus"]
    scale = next((k for k, n in wf.items() if n.get("class_type") == "ImageScaleToTotalPixels"), None)
    for i, name in enumerate(names, start=2):
        load_id, scale_id = f"ref{i}_load", f"ref{i}_scale"
        wf[load_id] = {"class_type": "LoadImage", "inputs": {"image": name}}
        wf[scale_id] = {"class_type": "ImageScaleToTotalPixels",
                        "inputs": {**(wf[scale]["inputs"] if scale else {"upscale_method": "lanczos", "megapixels": 1.0,
                                                                         "resolution_steps": 1}),
                                   "image": [load_id, 0]}}
        for enc in encoders:
            wf[enc]["inputs"][f"image{i}"] = [scale_id, 0]


def inject_loras(wf: dict, loras: list[tuple[str, float]]) -> dict:
    """Agrega nodos LoraLoaderModelOnly entre el modelo base (UNETLoader/UnetLoaderGGUF) y lo que lo usa."""
    base = next((k for k, v in wf.items() if isinstance(v, dict)
                 and v.get("class_type") in ("UNETLoader", "UnetLoaderGGUF", "CheckpointLoaderSimple")), None)
    if not base or not loras:
        return wf
    prev = base
    new_ids = []
    for i, (name, strength) in enumerate(loras):
        nid = f"shl_lora_{i}"
        wf[nid] = {"class_type": "LoraLoaderModelOnly",
                   "inputs": {"model": [prev, 0], "lora_name": name, "strength_model": float(strength)}}
        new_ids.append(nid)
        prev = nid
    for k, v in wf.items():
        if k in new_ids or not isinstance(v, dict):
            continue
        for key, val in (v.get("inputs") or {}).items():
            if isinstance(val, list) and len(val) == 2 and val[0] == base and val[1] == 0:
                v["inputs"][key] = [prev, 0]
    return wf
