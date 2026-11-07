"""Motor real: Z-Image Turbo (generar) y Qwen-Image-Edit (editar) en ComfyUI."""
import uuid

from app.comfyui.client import ComfyUIClient, ComfyUIError, fill_workflow, load_workflow
from app.config import settings
from app.engines import GPU_LOCK, Progress

# Nodos que necesita cada workflow por defecto y el archivo de modelo esperado en cada cargador.
REQUIRED_MODELS = {
    "UNETLoader": ("unet_name", [settings.GEN_UNET, settings.EDIT_UNET]),
    "CLIPLoader": ("clip_name", [settings.GEN_CLIP, settings.EDIT_CLIP]),
    "VAELoader": ("vae_name", [settings.GEN_VAE, settings.EDIT_VAE]),
    "LoraLoaderModelOnly": ("lora_name", [settings.EDIT_LORA]),
}


class ComfyEngine:
    name = "comfyui"

    def __init__(self):
        self.client = ComfyUIClient()

    async def generate(self, prompt, width, height, seed, on_progress: Progress = None) -> bytes:
        wf = fill_workflow(load_workflow(settings.WORKFLOW_GENERATE), {
            "prompt": prompt, "width": width, "height": height, "seed": seed, "steps": settings.GEN_STEPS,
            "unet": settings.GEN_UNET, "clip": settings.GEN_CLIP, "vae": settings.GEN_VAE,
        })
        async with GPU_LOCK:
            images = await self.client.run(wf, on_progress)
        return images[0]

    async def edit(self, image, instruction, seed, on_progress: Progress = None) -> bytes:
        async with GPU_LOCK:
            name = await self.client.upload_image(image, f"shelton_{uuid.uuid4().hex[:10]}.png")
            wf = fill_workflow(load_workflow(settings.WORKFLOW_EDIT), {
                "image": name, "instruction": instruction, "seed": seed, "steps": settings.EDIT_STEPS,
                "unet": settings.EDIT_UNET, "clip": settings.EDIT_CLIP, "vae": settings.EDIT_VAE,
                "lora": settings.EDIT_LORA,
            })
            images = await self.client.run(wf, on_progress)
        return images[0]

    async def generate_with_refs(self, refs, prompt, width, height, seed, on_progress: Progress = None) -> bytes:
        """Escena nueva manteniendo la identidad de hasta 3 referencias (personaje, auto, lugar...)."""
        async with GPU_LOCK:
            names = [await self.client.upload_image(r, f"shelton_ref_{uuid.uuid4().hex[:10]}.png") for r in refs[:3]]
            wf = fill_workflow(load_workflow(settings.WORKFLOW_SUBJECT), {
                "image": names[0], "prompt": prompt, "width": width, "height": height, "seed": seed,
                "steps": settings.EDIT_STEPS, "unet": settings.EDIT_UNET, "clip": settings.EDIT_CLIP,
                "vae": settings.EDIT_VAE, "lora": settings.EDIT_LORA,
            })
            _attach_extra_refs(wf, names[1:])
            images = await self.client.run(wf, on_progress)
        return images[0]

    async def health(self) -> dict:
        """Comprueba conexión, GPU y que los modelos configurados estén descargados."""
        try:
            stats = await self.client.system_stats()
        except Exception as e:
            return {"ok": False, "engine": self.name, "error": f"No conecta con ComfyUI en {settings.COMFYUI_URL}: {e}"}
        devices = [{"name": d.get("name"), "vram_gb": round(d.get("vram_total", 0) / 1024**3, 1)}
                   for d in stats.get("devices", [])]
        missing = []
        try:
            for node, (field, wanted) in REQUIRED_MODELS.items():
                info = (await self.client.object_info(node)).get(node, {})
                options = info.get("input", {}).get("required", {}).get(field, [[]])[0]
                if isinstance(options, str):  # formato nuevo: ["COMBO", {"options": [...]}]
                    options = info["input"]["required"][field][1].get("options", [])
                missing += [w for w in wanted if w and w not in options]
        except (ComfyUIError, Exception) as e:
            missing.append(f"(no se pudo revisar modelos: {e})")
        workflow_problems = await self.check_workflows()
        return {"ok": not missing and not workflow_problems, "engine": self.name, "url": settings.COMFYUI_URL,
                "devices": devices, "missing_models": sorted(set(missing)), "workflow_problems": workflow_problems}

    async def check_workflows(self) -> list[str]:
        """Compara cada plantilla con los nodos instalados en este ComfyUI (nombres y entradas obligatorias)."""
        try:
            info = await self.client.object_info()
        except Exception as e:
            return [f"No se pudo leer /object_info: {e}"]
        problems = []
        for wf_name in (settings.WORKFLOW_GENERATE, settings.WORKFLOW_EDIT, settings.WORKFLOW_SUBJECT):
            try:
                wf = load_workflow(wf_name)
            except ComfyUIError as e:
                problems.append(str(e))
                continue
            for node_id, node in wf.items():
                cls = node.get("class_type")
                if cls not in info:
                    problems.append(f"{wf_name}: falta el nodo '{cls}' (actualiza ComfyUI o instala el nodo)")
                    continue
                required = info[cls].get("input", {}).get("required", {})
                for field in required:
                    if field not in node.get("inputs", {}):
                        problems.append(f"{wf_name}: al nodo {node_id} ({cls}) le falta la entrada '{field}'")
        return problems


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
                        "inputs": {**(wf[scale]["inputs"] if scale else {"upscale_method": "lanczos", "megapixels": 1.0}),
                                   "image": [load_id, 0]}}
        for enc in encoders:
            wf[enc]["inputs"][f"image{i}"] = [scale_id, 0]
