"""Descarga los modelos locales de Shelton en ComfyUI/models/ (para la RTX 3090).

Uso (en la PC con la GPU):
    python scripts/download_models.py --comfy ~/ComfyUI                 # todo
    python scripts/download_models.py --comfy ~/ComfyUI imagen edicion  # solo algunos grupos

Grupos: imagen (Z-Image Turbo), calidad (Qwen-Image), edicion (Qwen-Image-Edit, banco de modelos),
video (Wan 2.2 14B), video_rapido (Wan 2.2 5B), sonido (Stable Audio Open).
Busca cada archivo POR NOMBRE dentro de su repositorio de Hugging Face (no depende de la carpeta interna) y lo deja
en la carpeta de ComfyUI que corresponde. Si ya está, lo salta. Vuelve a correrlo si se corta.
Stable Audio Open pide aceptar la licencia en Hugging Face y un token: `hf auth login` (o HF_TOKEN).
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

# (grupo, archivo, carpeta de ComfyUI, repositorio de Hugging Face, GB aprox.)
MODELS = [
    ("imagen", "z_image_turbo_bf16.safetensors", "diffusion_models", "Comfy-Org/z_image_turbo", 12.3),
    ("imagen", "qwen_3_4b.safetensors", "text_encoders", "Comfy-Org/z_image_turbo", 8.0),
    ("imagen", "ae.safetensors", "vae", "Comfy-Org/z_image_turbo", 0.3),
    ("calidad", "qwen_image_fp8_e4m3fn.safetensors", "diffusion_models", "Comfy-Org/Qwen-Image_ComfyUI", 20.4),
    ("calidad", "Qwen-Image-Lightning-8steps-V1.0.safetensors", "loras", "lightx2v/Qwen-Image-Lightning", 1.7),
    ("calidad", "qwen_2.5_vl_7b_fp8_scaled.safetensors", "text_encoders", "Comfy-Org/Qwen-Image_ComfyUI", 9.4),
    ("calidad", "qwen_image_vae.safetensors", "vae", "Comfy-Org/Qwen-Image_ComfyUI", 0.3),
    ("edicion", "qwen_image_edit_2509_fp8_e4m3fn.safetensors", "diffusion_models", "Comfy-Org/Qwen-Image-Edit_ComfyUI", 20.4),
    ("edicion", "qwen_2.5_vl_7b_fp8_scaled.safetensors", "text_encoders", "Comfy-Org/Qwen-Image_ComfyUI", 9.4),
    ("edicion", "qwen_image_vae.safetensors", "vae", "Comfy-Org/Qwen-Image_ComfyUI", 0.3),
    ("edicion", "Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors", "loras", "lightx2v/Qwen-Image-Lightning", 1.7),
    ("video", "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors", "diffusion_models", "Comfy-Org/Wan_2.2_ComfyUI_Repackaged", 14.3),
    ("video", "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors", "diffusion_models", "Comfy-Org/Wan_2.2_ComfyUI_Repackaged", 14.3),
    ("video", "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors", "loras", "Comfy-Org/Wan_2.2_ComfyUI_Repackaged", 1.2),
    ("video", "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors", "loras", "Comfy-Org/Wan_2.2_ComfyUI_Repackaged", 1.2),
    ("video", "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "text_encoders", "Comfy-Org/Wan_2.1_ComfyUI_repackaged", 6.7),
    ("video", "wan_2.1_vae.safetensors", "vae", "Comfy-Org/Wan_2.2_ComfyUI_Repackaged", 0.3),
    ("video_rapido", "wan2.2_ti2v_5B_fp16.safetensors", "diffusion_models", "Comfy-Org/Wan_2.2_ComfyUI_Repackaged", 10.0),
    ("video_rapido", "wan2.2_vae.safetensors", "vae", "Comfy-Org/Wan_2.2_ComfyUI_Repackaged", 1.4),
    ("video_rapido", "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "text_encoders", "Comfy-Org/Wan_2.1_ComfyUI_repackaged", 6.7),
    ("sonido", "stable_audio_open_1.0.safetensors", "checkpoints", "stabilityai/stable-audio-open-1.0", 4.9),
    ("sonido", "t5_base.safetensors", "text_encoders", "Comfy-Org/stable-audio-open-1.0_repackaged", 0.9),
]
GROUPS = ["imagen", "calidad", "edicion", "video", "video_rapido", "sonido"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("groups", nargs="*", help=f"Grupos a bajar ({', '.join(GROUPS)}). Vacío = todos.")
    ap.add_argument("--comfy", default=str(Path.home() / "ComfyUI"), help="Carpeta de ComfyUI")
    ap.add_argument("--list", action="store_true", help="Solo mostrar qué falta")
    a = ap.parse_args()
    groups = a.groups or GROUPS
    bad = [g for g in groups if g not in GROUPS]
    if bad:
        print(f"Grupo desconocido: {', '.join(bad)}. Opciones: {', '.join(GROUPS)}")
        return 2
    models_dir = Path(a.comfy).expanduser() / "models"
    if not models_dir.exists():
        print(f"No encuentro {models_dir}. Instala ComfyUI primero (scripts/gpu_setup.sh) o indica --comfy")
        return 2
    todo, seen = [], set()
    for g, name, folder, repo, gb in MODELS:
        if g in groups and (name, folder) not in seen:
            seen.add((name, folder))
            dest = models_dir / folder / name
            if not dest.exists():
                todo.append((g, name, folder, repo, gb, dest))
    need = sum(t[4] for t in todo)
    free = shutil.disk_usage(models_dir).free / 1e9
    print(f"Faltan {len(todo)} archivos (~{need:.0f} GB). Libre en disco: {free:.0f} GB.")
    for g, name, folder, repo, gb, _ in todo:
        print(f"  [{g}] {folder}/{name}  (~{gb} GB, {repo})")
    if a.list or not todo:
        return 0
    if need > free - 5:
        print("⚠ No alcanza el disco. Libera espacio o baja menos grupos (ej: imagen edicion).")
        return 1
    try:
        from huggingface_hub import list_repo_files, hf_hub_download
    except ImportError:
        print("Falta huggingface_hub: pip install -U huggingface_hub")
        return 2
    fails = 0
    for g, name, folder, repo, gb, dest in todo:
        print(f"\n▶ {name} ({repo})")
        try:
            match = [f for f in list_repo_files(repo) if f.endswith("/" + name) or f == name]
            if not match:
                print(f"  ✖ No está en {repo} con ese nombre. Búscalo en https://huggingface.co/{repo} y déjalo en "
                      f"{dest.parent}/ (si tiene otro nombre, ajusta el nombre en el .env de Shelton).")
                fails += 1
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=dest.parent) as td:
                got = Path(hf_hub_download(repo, match[0], local_dir=td))
                shutil.move(str(got), dest)
            print(f"  ✔ {dest}")
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            hint = (" (este modelo pide aceptar su licencia en Hugging Face y entrar con `hf auth login`)"
                    if "401" in msg or "403" in msg or "gated" in msg.lower() else "")
            print(f"  ✖ {msg[:300]}{hint}")
            fails += 1
    print("\nListo." if not fails else f"\nTerminó con {fails} archivo(s) pendientes: vuelve a correrlo.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
