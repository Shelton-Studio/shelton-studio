"""Guardado de imágenes y adaptación al formato exacto de cada red."""
import base64
import io
from pathlib import Path

from PIL import Image, ImageOps

from app.config import settings


def media_path(name: str) -> Path:
    return settings.media_dir / name


def url_for(path: str) -> str:
    return f"/media/{Path(path).name}" if path else ""


def save_bytes(data: bytes, name: str) -> str:
    p = media_path(name)
    p.write_bytes(data)
    return str(p)


def fit_to_platform(raw_path: str, width: int, height: int, out_name: str) -> str:
    """Recorte centrado + redimensión Lanczos al tamaño exacto. JPEG alta calidad (lo que piden las redes)."""
    img = Image.open(raw_path)
    img = ImageOps.exif_transpose(img).convert("RGB")
    img = ImageOps.fit(img, (width, height), method=Image.Resampling.LANCZOS, centering=(0.5, 0.45))
    out = media_path(out_name)
    img.save(out, "JPEG", quality=94, optimize=True, progressive=True, subsampling=0)
    return str(out)


def preview_b64(path: str, max_side: int = 768) -> str:
    """Versión reducida en base64 para que Claude 'vea' la imagen sin gastar de más."""
    img = Image.open(path).convert("RGB")
    img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return base64.standard_b64encode(buf.getvalue()).decode()


def image_block(path: str, max_side: int = 768) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": preview_b64(path, max_side)}}
