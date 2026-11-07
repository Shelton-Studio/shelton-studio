"""Publicación y exportación de posts.

Telegram publica directo con un bot. Instagram, TikTok y Facebook necesitan cuentas de negocio
y una URL pública para la imagen; mientras corre en local se exporta un paquete (ZIP + CSV)
listo para subir o programar en Meta Business Suite, Metricool, Buffer, etc.
"""
import csv
import io
import zipfile
from pathlib import Path

import httpx

from app import events
from app.config import settings
from app.db import Post, now, session
from app.platforms import get_platform
from app.serialize import post_dict


def full_text(p: Post) -> str:
    tags = " ".join(p.hashtags or [])
    return f"{p.caption}\n\n{tags}".strip() if tags else p.caption


async def publish(post_id: str, target: str) -> dict:
    with session() as s:
        p = s.get(Post, post_id)
        if not p or not p.image_path:
            raise ValueError("El post no existe o no tiene imagen")
        if p.status not in ("approved", "published", "ready"):
            raise ValueError(f"El post está en estado '{p.status}'; apruébalo antes de publicar")
        image, text = p.image_path, full_text(p)
    if target == "telegram":
        result = await _telegram(image, text)
    else:
        raise ValueError(f"Publicación directa en '{target}' aún no disponible; usa la exportación")
    with session() as s:
        p = s.get(Post, post_id)
        p.status = "published"
        p.publish_log = [*(p.publish_log or []), {"target": target, "at": now().isoformat() + "Z", **result}]
        events.publish({"type": "post", "post": post_dict(p)})
    return {"ok": True, "target": target, **result}


async def _telegram(image_path: str, text: str) -> dict:
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        raise ValueError("Configura TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en .env")
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}"
    async with httpx.AsyncClient(timeout=60) as c:
        with open(image_path, "rb") as f:
            # El caption de una foto admite 1024 caracteres; si es más largo va en un mensaje aparte.
            r = await c.post(f"{url}/sendPhoto", data={"chat_id": settings.TELEGRAM_CHAT_ID,
                                                       "caption": text[:1024] if len(text) <= 1024 else ""},
                             files={"photo": (Path(image_path).name, f, "image/jpeg")})
        j = r.json()
        if not j.get("ok"):
            raise ValueError(f"Telegram: {j.get('description')}")
        if len(text) > 1024:
            await c.post(f"{url}/sendMessage", data={"chat_id": settings.TELEGRAM_CHAT_ID, "text": text[:4096]})
    return {"message_id": j["result"]["message_id"]}


def export_zip(post_ids: list[str]) -> bytes:
    """ZIP con imágenes + un .txt por post + posts.csv (para programadores de contenido)."""
    buf = io.BytesIO()
    rows = []
    with session() as s, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i, pid in enumerate(post_ids, 1):
            p = s.get(Post, pid)
            if not p or not p.image_path or not Path(p.image_path).exists():
                continue
            plat = get_platform(p.platform)
            base = f"{i:03d}_{plat.key}_{p.id}"
            z.write(p.image_path, f"{plat.key}/{base}.jpg")
            z.writestr(f"{plat.key}/{base}.txt", full_text(p))
            rows.append({"file": f"{plat.key}/{base}.jpg", "platform": plat.name, "caption": p.caption,
                         "hashtags": " ".join(p.hashtags or []), "alt_text": p.alt_text,
                         "qa_score": p.qa_score, "idea": p.idea})
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=["file", "platform", "caption", "hashtags", "alt_text", "qa_score", "idea"])
        w.writeheader()
        w.writerows(rows)
        z.writestr("posts.csv", "﻿" + out.getvalue())
    return buf.getvalue()
