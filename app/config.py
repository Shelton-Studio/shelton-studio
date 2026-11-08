"""Configuración central. Todo se lee de .env (ver .env.example)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "si", "sí")


class Settings:
    # --- Claude (cerebro de Shelton) ---
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
    # Plan B: OpenRouter (se usa solo si no hay clave de Anthropic)
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4.5")

    # --- Motor de imágenes ---
    # "comfyui" = ComfyUI real (GPU). "mock" = imágenes de prueba sin GPU.
    IMAGE_ENGINE = os.getenv("IMAGE_ENGINE", "comfyui").lower()
    COMFYUI_URL = os.getenv("COMFYUI_URL", os.getenv("COMFYUI_LOCAL_URL", "http://127.0.0.1:8188")).rstrip("/")
    COMFYUI_TOKEN = os.getenv("COMFYUI_TOKEN", "")
    COMFYUI_TIMEOUT = int(os.getenv("COMFYUI_TIMEOUT", "600"))

    # Plantillas de workflow (archivos en app/comfyui/workflows o ruta absoluta)
    WORKFLOW_GENERATE = os.getenv("WORKFLOW_GENERATE", "z_image_turbo.json")
    WORKFLOW_EDIT = os.getenv("WORKFLOW_EDIT", "qwen_image_edit.json")
    WORKFLOW_SUBJECT = os.getenv("WORKFLOW_SUBJECT", "qwen_subject.json")  # posts con modelos del banco

    # Perfil de generación: fast (Z-Image Turbo) | quality (Qwen-Image 20B) | lite (Z-Image GGUF, 6-8 GB) | custom
    GEN_PRESET = os.getenv("GEN_PRESET", "fast").lower()

    # Qwen-Image (calidad máxima, mejor texto dentro de la imagen). Comparte text encoder y VAE con el editor.
    QWEN_IMAGE_UNET = os.getenv("QWEN_IMAGE_UNET", "qwen_image_fp8_e4m3fn.safetensors")
    QWEN_IMAGE_LORA = os.getenv("QWEN_IMAGE_LORA", "Qwen-Image-Lightning-8steps-V1.0.safetensors")
    QWEN_IMAGE_STEPS = int(os.getenv("QWEN_IMAGE_STEPS", "8"))

    # Modelos de Z-Image Turbo (generación)
    GEN_UNET = os.getenv("GEN_UNET", "z_image_turbo_bf16.safetensors")
    GEN_CLIP = os.getenv("GEN_CLIP", "qwen_3_4b.safetensors")
    GEN_VAE = os.getenv("GEN_VAE", "ae.safetensors")
    GEN_STEPS = int(os.getenv("GEN_STEPS", "9"))
    # Escala de resolución: 1.0 en la 3090; 0.75 en GPUs de 6-8 GB
    GEN_SCALE = float(os.getenv("GEN_SCALE", "1.0"))

    # Modelos de Qwen-Image-Edit (edición)
    EDIT_UNET = os.getenv("EDIT_UNET", "qwen_image_edit_2509_fp8_e4m3fn.safetensors")
    EDIT_CLIP = os.getenv("EDIT_CLIP", "qwen_2.5_vl_7b_fp8_scaled.safetensors")
    EDIT_VAE = os.getenv("EDIT_VAE", "qwen_image_vae.safetensors")
    EDIT_LORA = os.getenv("EDIT_LORA", "Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors")
    EDIT_STEPS = int(os.getenv("EDIT_STEPS", "4"))

    # --- Video (Wan 2.2) ---
    # cinema = I2V 14B 720p + interpolación a 30 fps (lo mejor en local) | fast = TI2V 5B 720p 24 fps
    VIDEO_PRESET = os.getenv("VIDEO_PRESET", "cinema").lower()
    VIDEO_CLIP = os.getenv("VIDEO_CLIP", "umt5_xxl_fp8_e4m3fn_scaled.safetensors")
    VIDEO_FAST_UNET = os.getenv("VIDEO_FAST_UNET", "wan2.2_ti2v_5B_fp16.safetensors")
    VIDEO_FAST_VAE = os.getenv("VIDEO_FAST_VAE", "wan2.2_vae.safetensors")
    VIDEO_HIGH_UNET = os.getenv("VIDEO_HIGH_UNET", "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors")
    VIDEO_LOW_UNET = os.getenv("VIDEO_LOW_UNET", "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors")
    VIDEO_HIGH_LORA = os.getenv("VIDEO_HIGH_LORA", "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors")
    VIDEO_LOW_LORA = os.getenv("VIDEO_LOW_LORA", "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors")
    VIDEO_VAE = os.getenv("VIDEO_VAE", "wan_2.1_vae.safetensors")
    VIDEO_SECONDS = float(os.getenv("VIDEO_SECONDS", "5"))
    VIDEO_OUTPUT_FPS = int(os.getenv("VIDEO_OUTPUT_FPS", "30"))

    # --- Audio (local) ---
    TTS_URL = os.getenv("TTS_URL", "http://127.0.0.1:8189")          # tts_server/server.py (Chatterbox)
    WORKFLOW_SFX = os.getenv("WORKFLOW_SFX", "stable_audio_sfx.json")
    SFX_CKPT = os.getenv("SFX_CKPT", "stable_audio_open_1.0.safetensors")
    SFX_CLIP = os.getenv("SFX_CLIP", "t5_base.safetensors")
    SFX_STEPS = int(os.getenv("SFX_STEPS", "50"))

    # --- Cadena de producción ---
    QA_MIN_SCORE = int(os.getenv("QA_MIN_SCORE", "70"))      # por debajo se regenera
    QA_MAX_RETRIES = int(os.getenv("QA_MAX_RETRIES", "2"))   # reintentos por imagen
    WORKERS = int(os.getenv("WORKERS", "3"))                 # posts en paralelo (la GPU va de a uno)
    CONTENT_LANGUAGE = os.getenv("CONTENT_LANGUAGE", "es")

    # --- Publicación ---
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

    # --- App ---
    DATA_DIR = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
    DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'shelton.db').as_posix()}")
    APP_HOST = os.getenv("APP_HOST", "127.0.0.1")
    APP_PORT = int(os.getenv("APP_PORT", "8000"))

    @property
    def media_dir(self) -> Path:
        return self.DATA_DIR / "media"


settings = Settings()
settings.media_dir.mkdir(parents=True, exist_ok=True)
