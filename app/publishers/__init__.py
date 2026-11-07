"""Publicación, aprobaciones y exportación.

- Una persona publica directo desde la web (botón Publicar) a cualquier conexión de tipo "publisher".
- Si un agente quiere publicar, se crea una Aprobación: no sale nada hasta que una persona la apruebe.
- La exportación ZIP/CSV sirve para cualquier programador de contenido.
"""
import asyncio
import base64
import csv
import io
import json
import zipfile
from pathlib import Path

import httpx

from app import events
from app.config import settings
from app.connections import TYPES, list_enabled
from app.db import Approval, Connection, Post, now, session
from app.media import url_for
from app.platforms import get_platform
from app.serialize import post_dict


def full_text(p: Post) -> str:
    tags = " ".join(p.hashtags or [])
    return f"{p.caption}\n\n{tags}".strip() if tags else p.caption


def targets() -> list[dict]:
    """Destinos disponibles: conexiones de publicación (+ Telegram de .env si está configurado)."""
    out = [{"id": c.id, "name": c.name or TYPES[c.type]["name"], "type": c.type} for c in list_enabled("publisher")]
    if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID and not any(t["type"] == "telegram" for t in out):
        out.append({"id": "env:telegram", "name": "Telegram (.env)", "type": "telegram"})
    return out


def _resolve(target: str) -> tuple[str, dict, str]:
    if target in ("env:telegram", "telegram") and settings.TELEGRAM_BOT_TOKEN:
        if target == "telegram":
            conn = next((c for c in list_enabled("publisher") if c.type == "telegram"), None)
            if conn:
                return conn.type, conn.config, conn.name or "Telegram"
        return "telegram", {"bot_token": settings.TELEGRAM_BOT_TOKEN, "chat_id": settings.TELEGRAM_CHAT_ID}, "Telegram"
    with session() as s:
        c = s.get(Connection, target) or next((x for x in list_enabled("publisher") if x.type == target), None)
        if not c or TYPES.get(c.type, {}).get("category") != "publisher":
            raise ValueError("Destino de publicación no encontrado: configúralo en Conexiones")
        return c.type, dict(c.config or {}), c.name or TYPES[c.type]["name"]


async def publish(post_id: str, target: str) -> dict:
    """Publica YA. Solo debe llamarse por decisión de una persona (botón o aprobación)."""
    with session() as s:
        p = s.get(Post, post_id)
        if not p or not p.image_path:
            raise ValueError("El post no existe o no tiene imagen")
        data = post_dict(p)
        image, text = (p.video_path or p.image_path), full_text(p)
    kind, cfg, name = _resolve(target)
    sender = {"telegram": _telegram, "instagram": _instagram, "facebook": _facebook, "discord": _discord,
              "webhook_publisher": _webhook}[kind]
    result = await sender(cfg, image, text, data)
    with session() as s:
        p = s.get(Post, post_id)
        p.status = "published"
        p.publish_log = [*(p.publish_log or []), {"target": name, "type": kind, "at": now().isoformat() + "Z", **result}]
        events.publish({"type": "post", "post": post_dict(p)})
    return {"ok": True, "target": name, **result}


# ------------------------------------------------------------------ Aprobaciones
def request_publish(post_id: str, target: str, agent: str = "shelton") -> dict:
    kind, _, name = _resolve(target)
    with session() as s:
        p = s.get(Post, post_id)
        if not p:
            raise ValueError("No existe ese post")
        a = Approval(kind="publish", agent=agent, title=f"Publicar en {name}: {p.idea[:90]}",
                     payload={"post_id": post_id, "target": target, "target_name": name, "image_url": url_for(p.image_path),
                              "caption": full_text(p)})
        s.add(a)
        s.flush()
        out = approval_dict(a)
    events.publish({"type": "approval", "approval": out})
    return out


async def decide(approval_id: str, approve: bool) -> dict:
    with session() as s:
        a = s.get(Approval, approval_id)
        if not a or a.status != "pending":
            raise ValueError("La aprobación no existe o ya fue decidida")
        payload, kind = dict(a.payload), a.kind
    status, result = "rejected", {}
    if approve:
        try:
            if kind == "publish":
                result = await publish(payload["post_id"], payload["target"])
            status = "approved"
        except Exception as e:
            status, result = "failed", {"error": str(e)}
    with session() as s:
        a = s.get(Approval, approval_id)
        a.status, a.result, a.decided_at = status, result, now()
        out = approval_dict(a)
    events.publish({"type": "approval", "approval": out})
    return out


def approval_dict(a: Approval) -> dict:
    return {"id": a.id, "kind": a.kind, "title": a.title, "agent": a.agent, "payload": a.payload, "status": a.status,
            "result": a.result, "created_at": a.created_at.isoformat() + "Z" if a.created_at else None,
            "decided_at": a.decided_at.isoformat() + "Z" if a.decided_at else None}


# ------------------------------------------------------------------ Destinos
def _is_video(path: str) -> bool:
    return path.lower().endswith((".mp4", ".mov", ".webm"))


def _mime(path: str) -> str:
    return "video/mp4" if _is_video(path) else "image/jpeg"


def _headers(cfg: dict) -> dict:
    h = cfg.get("headers") or {}
    return json.loads(h) if isinstance(h, str) and h.strip() else (h if isinstance(h, dict) else {})


async def _telegram(cfg, image_path, text, post) -> dict:
    url = f"https://api.telegram.org/bot{cfg['bot_token']}"
    async with httpx.AsyncClient(timeout=60) as c:
        method, field = ("sendVideo", "video") if _is_video(image_path) else ("sendPhoto", "photo")
        with open(image_path, "rb") as f:
            # El caption admite 1024 caracteres; si es más largo va en un mensaje aparte.
            data = {"chat_id": cfg["chat_id"], "caption": text if len(text) <= 1024 else ""}
            if field == "video":
                data["supports_streaming"] = "true"
            r = await c.post(f"{url}/{method}", data=data, files={field: (Path(image_path).name, f, _mime(image_path))})
        j = r.json()
        if not j.get("ok"):
            raise ValueError(f"Telegram: {j.get('description')}")
        if len(text) > 1024:
            await c.post(f"{url}/sendMessage", data={"chat_id": cfg["chat_id"], "text": text[:4096]})
    return {"message_id": j["result"]["message_id"]}


async def _instagram(cfg, image_path, text, post) -> dict:
    base = cfg["public_base_url"].rstrip("/")
    v = cfg.get("api_version") or "v21.0"
    graph = f"https://graph.facebook.com/{v}/{cfg['ig_user_id']}"
    async with httpx.AsyncClient(timeout=120) as c:
        media = ({"media_type": "REELS", "video_url": base + url_for(image_path)} if _is_video(image_path)
                 else {"image_url": base + url_for(image_path)})
        r = await c.post(f"{graph}/media", data={**media, "caption": text[:2200], "access_token": cfg["access_token"]})
        j = r.json()
        if "id" not in j:
            raise ValueError(f"Instagram: {j.get('error', {}).get('message', j)}")
        if _is_video(image_path):  # Instagram procesa el video antes de poder publicarlo
            for _ in range(60):
                st = (await c.get(f"https://graph.facebook.com/{v}/{j['id']}",
                                  params={"fields": "status_code", "access_token": cfg["access_token"]})).json()
                if st.get("status_code") == "FINISHED":
                    break
                if st.get("status_code") == "ERROR":
                    raise ValueError("Instagram no pudo procesar el video")
                await asyncio.sleep(5)
        r = await c.post(f"{graph}/media_publish", data={"creation_id": j["id"], "access_token": cfg["access_token"]})
        j = r.json()
        if "id" not in j:
            raise ValueError(f"Instagram: {j.get('error', {}).get('message', j)}")
    return {"media_id": j["id"]}


async def _facebook(cfg, image_path, text, post) -> dict:
    v = cfg.get("api_version") or "v21.0"
    async with httpx.AsyncClient(timeout=120) as c:
        video = _is_video(image_path)
        with open(image_path, "rb") as f:
            r = await c.post(f"https://graph.facebook.com/{v}/{cfg['page_id']}/{'videos' if video else 'photos'}",
                             data={("description" if video else "message"): text, "access_token": cfg["access_token"]},
                             files={"source": (Path(image_path).name, f, _mime(image_path))})
        j = r.json()
        if "id" not in j:
            raise ValueError(f"Facebook: {j.get('error', {}).get('message', j)}")
    return {"post_id": j.get("post_id") or j["id"]}


async def _discord(cfg, image_path, text, post) -> dict:
    async with httpx.AsyncClient(timeout=60) as c:
        with open(image_path, "rb") as f:
            r = await c.post(cfg["webhook_url"], data={"content": text[:2000]},
                             files={"file": (Path(image_path).name, f, _mime(image_path))})
        if r.status_code >= 400:
            raise ValueError(f"Discord {r.status_code}: {r.text[:200]}")
    return {}


async def _webhook(cfg, image_path, text, post) -> dict:
    body = {"event": "post.publish", "post": post, "text": text}
    if cfg.get("public_base_url"):
        body["media_url"] = cfg["public_base_url"].rstrip("/") + url_for(image_path)
    else:
        body["media_base64"] = base64.standard_b64encode(Path(image_path).read_bytes()).decode()
    body["media_type"] = "video" if _is_video(image_path) else "image"
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(cfg["url"], json=body, headers=_headers(cfg))
        if r.status_code >= 400:
            raise ValueError(f"Webhook {r.status_code}: {r.text[:200]}")
    return {"status_code": r.status_code}


# ------------------------------------------------------------------ Exportación
def export_zip(post_ids: list[str]) -> bytes:
    """ZIP con imágenes + un .txt por post + posts.csv (para programadores de contenido)."""
    buf = io.BytesIO()
    rows = []
    with session() as s, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i, pid in enumerate(post_ids, 1):
            p = s.get(Post, pid)
            media = (p.video_path or p.image_path) if p else ""
            if not media or not Path(media).exists():
                continue
            plat = get_platform(p.platform)
            base = f"{i:03d}_{plat.key}_{p.id}"
            ext = Path(media).suffix
            z.write(media, f"{plat.key}/{base}{ext}")
            z.writestr(f"{plat.key}/{base}.txt", full_text(p))
            rows.append({"file": f"{plat.key}/{base}{ext}", "platform": plat.name, "caption": p.caption,
                         "hashtags": " ".join(p.hashtags or []), "alt_text": p.alt_text,
                         "qa_score": p.qa_score, "idea": p.idea})
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=["file", "platform", "caption", "hashtags", "alt_text", "qa_score", "idea"])
        w.writeheader()
        w.writerows(rows)
        z.writestr("posts.csv", "﻿" + out.getvalue())
    return buf.getvalue()
