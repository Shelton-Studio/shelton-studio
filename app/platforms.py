"""Formatos de cada red social: tamaño final de publicación y tamaño de generación."""
from dataclasses import dataclass



@dataclass(frozen=True)
class Platform:
    key: str
    name: str
    aspect: str          # "4:5", "9:16"...
    width: int           # tamaño final publicado
    height: int
    caption_style: str   # guía para el redactor


PLATFORMS: dict[str, Platform] = {p.key: p for p in [
    Platform("instagram_feed", "Instagram Feed", "4:5", 1080, 1350,
             "Caption de Instagram: gancho en la primera línea, 2-4 líneas de valor, llamada a la acción, 8-15 hashtags relevantes al final."),
    Platform("instagram_story", "Instagram Story / Reels", "9:16", 1080, 1920,
             "Texto muy corto para story: 1-2 líneas, directo, con emoji. Máximo 5 hashtags."),
    Platform("tiktok", "TikTok", "9:16", 1080, 1920,
             "Descripción de TikTok: una frase con gancho y tono cercano, 3-6 hashtags (incluye tendencias del nicho)."),
    Platform("telegram", "Telegram", "4:5", 1080, 1350,
             "Post de canal de Telegram: título en negrita con emoji, 2-5 líneas informativas, sin hashtags o como mucho 2."),
    Platform("facebook", "Facebook", "4:5", 1080, 1350,
             "Post de Facebook: tono conversacional, 2-4 líneas, una pregunta para generar comentarios, 0-3 hashtags."),
    Platform("x", "X / Twitter", "16:9", 1600, 900,
             "Tweet: máximo 260 caracteres, 1-2 hashtags."),
    Platform("pinterest", "Pinterest", "2:3", 1000, 1500,
             "Pin de Pinterest: título descriptivo con palabras clave + descripción de 1-2 frases, 3-5 hashtags."),
    Platform("youtube_thumb", "Miniatura YouTube", "16:9", 1280, 720,
             "Título de video corto y llamativo (máx 70 caracteres). Sin hashtags."),
    Platform("square", "Cuadrado 1:1", "1:1", 1080, 1080,
             "Caption genérico para redes: gancho, 2-3 líneas, 5-10 hashtags."),
    Platform("original", "Original (sin recortar)", "orig", 0, 0,
             "Caption genérico para redes: gancho, 2-3 líneas, 5-10 hashtags."),
]}

DEFAULT_PLATFORM = "instagram_feed"

# Resolución nativa de generación (~1.3 MP, múltiplos de 16) por proporción.
_GEN_SIZES = {
    "1:1": (1152, 1152),
    "4:5": (1024, 1280),
    "9:16": (864, 1536),
    "16:9": (1536, 864),
    "2:3": (960, 1440),
}


def get_platform(key: str | None) -> Platform:
    return PLATFORMS.get((key or "").lower(), PLATFORMS[DEFAULT_PLATFORM])


def generation_size(aspect: str) -> tuple[int, int]:
    """Tamaño nativo; cada motor lo escala según su GPU (gen_scale)."""
    return _GEN_SIZES.get(aspect, _GEN_SIZES["1:1"])


def platform_list() -> list[dict]:
    return [{"key": p.key, "name": p.name, "aspect": p.aspect, "width": p.width, "height": p.height}
            for p in PLATFORMS.values()]
