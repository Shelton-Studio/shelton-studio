import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    # Anthropic
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

    # ComfyUI
    COMFYUI_LOCAL_URL = os.getenv("COMFYUI_LOCAL_URL", "http://localhost:8188")
    COMFYUI_CLOUD_URL = os.getenv("COMFYUI_CLOUD_URL", "")
    COMFYUI_CLOUD_TOKEN = os.getenv("COMFYUI_CLOUD_TOKEN", "")

    # Database
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./shelton.db")

    # App
    APP_TITLE = os.getenv("APP_TITLE", "Shelton - Content Generation Platform")
    APP_PORT = int(os.getenv("APP_PORT", "8000"))
    DEBUG = os.getenv("DEBUG", "True").lower() == "true"

settings = Settings()
