"""Videos de referencia: pegas el link de un TikTok, Reel, Short (o subes el video) y el equipo lo recrea
con tus propios modelos del banco: mismo fondo, mismo encuadre, mismo movimiento, otra cara.

1. Se descarga (yt-dlp) o se recibe el archivo.
2. Se sacan cuadros clave (quedan como posts para editarlos o usarlos) y el audio va a la biblioteca.
3. El Analista de videos lo desarma escena por escena (qué pasa, fondo, luz, cámara, movimiento, gancho).
4. Recrear: cada cuadro pasa por la cadena normal (imagen con tu modelo -> juez -> video -> juez) y se monta
   el reel. Si se pide, se le pone el audio original.

Ficha de cada referencia: data/references/<id>.json
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path

from app import events
from app.config import settings
from app.db import Post, get_or_create_brand, session
from app.serialize import post_dict

log = logging.getLogger("shelton.reference")
MAX_SECONDS = 180
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)


def _dir() -> Path:
    d = settings.DATA_DIR / "references"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save(ref: dict) -> dict:
    (_dir() / f"{ref['id']}.json").write_text(json.dumps(ref, ensure_ascii=False, indent=2), encoding="utf-8")
    return public(ref)


def public(ref: dict) -> dict:
    out = dict(ref)
    out["video_url"] = f"/media/{Path(ref['video_path']).name}" if ref.get("video_path") else ""
    return out


def get(ref_id: str) -> dict:
    p = _dir() / f"{Path(ref_id).name}.json"
    if not p.exists():
        raise ValueError("Esa referencia no existe")
    return json.loads(p.read_text(encoding="utf-8"))


def list_refs(limit: int = 50) -> list[dict]:
    out = []
    for p in sorted(_dir().glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
        try:
            out.append(public(json.loads(p.read_text(encoding="utf-8"))))
        except Exception:  # noqa: BLE001
            continue
    return out


def find_urls(text: str) -> list[str]:
    return [u.rstrip(").,;") for u in URL_RE.findall(text or "")]


# Redes y sitios de video conocidos (yt-dlp soporta muchos más: cualquier link se intenta igual)
VIDEO_SITES = {"tiktok.com": "TikTok", "instagram.com": "Instagram", "youtube.com": "YouTube", "youtu.be": "YouTube",
               "facebook.com": "Facebook", "fb.watch": "Facebook", "x.com": "X", "twitter.com": "X",
               "pinterest.": "Pinterest", "pin.it": "Pinterest", "threads.net": "Threads", "threads.com": "Threads",
               "snapchat.com": "Snapchat", "vimeo.com": "Vimeo", "kwai": "Kwai", "likee": "Likee",
               "twitch.tv": "Twitch", "dailymotion.com": "Dailymotion", "reddit.com": "Reddit", "linkedin.com": "LinkedIn",
               "bilibili.com": "Bilibili", "douyin.com": "Douyin", "capcut.com": "CapCut", "tumblr.com": "Tumblr"}
MEDIA_EXT = (".mp4", ".mov", ".webm", ".m4v", ".mkv")


def video_links(text: str) -> list[tuple[str, str]]:
    """Links de video en un mensaje: [(url, red)]. Un link directo a .mp4 también cuenta."""
    out = []
    for u in find_urls(text):
        low = u.lower()
        site = next((name for dom, name in VIDEO_SITES.items() if dom in low), None)
        if site or low.split("?")[0].endswith(MEDIA_EXT):
            out.append((u, site or "video"))
    return out


def _norm(url: str) -> str:
    return url.split("?")[0].rstrip("/").lower()


def find_by_url(url: str) -> dict | None:
    """Si ese link ya se analizó, devuelve su ficha (no se vuelve a bajar)."""
    key = _norm(url)
    for p in sorted(_dir().glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if r.get("url") and _norm(r["url"]) == key and Path(r.get("video_path", "")).exists():
            return r
    return None


# ------------------------------------------------------------------ Descarga
def _download(url: str, out_base: Path) -> tuple[str, str]:
    """Descarga el video con yt-dlp (TikTok, Instagram, YouTube Shorts, X, Facebook...). Devuelve (ruta, título)."""
    try:
        import yt_dlp
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("Falta yt-dlp: vuelve a correr el instalador (o pip install yt-dlp)") from e
    opts = {"outtmpl": str(out_base) + ".%(ext)s", "format": "bv*[height<=1920][ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
            "merge_output_format": "mp4", "quiet": True, "no_warnings": True, "noplaylist": True,
            "max_filesize": 300 * 1024 * 1024, "socket_timeout": 30}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if (info.get("duration") or 0) > MAX_SECONDS:
            raise ValueError(f"El video dura {int(info['duration'])} s: el máximo es {MAX_SECONDS} s")
        info = ydl.extract_info(url, download=True)
        path = ydl.prepare_filename(info)
    p = Path(path)
    if not p.exists():
        cands = sorted(out_base.parent.glob(out_base.name + ".*"))
        if not cands:
            raise RuntimeError("No se pudo descargar el video")
        p = cands[0]
    return str(p), (info.get("title") or info.get("description") or "")[:200]


async def from_url(url: str, conversation_id: str | None = None, reuse: bool = True) -> dict:
    if reuse and (old := find_by_url(url)):
        return public(old)
    rid = uuid.uuid4().hex[:12]
    base = settings.media_dir / f"ref_{rid}"
    try:
        path, title = await asyncio.to_thread(_download, url, base)
    except ValueError:
        raise
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        low = msg.lower()
        log.warning("No se pudo bajar %s: %s", url, msg)
        upload = (" Solución segura: en la app de la red toca «Compartir → Guardar video», y súbelo aquí con 📎 "
                  "(en el chat o en Copiar videos): el resto (escenas, audio y recrear con tu modelo) sigue igual.")
        if "login" in low or "private" in low or "cookies" in low:
            raise ValueError("Esa red pide iniciar sesión para bajar este video." + upload) from e
        if "unavailable" in low or "not available" in low or "removed" in low or "404" in low:
            raise ValueError("Ese video no está disponible (borrado, privado o bloqueado en el país del servidor)."
                             + upload) from e
        if any(w in low for w in ("403", "blocked", "captcha", "unexpected response", "challenge", "impersonat")):
            raise ValueError("La red bloqueó la descarga desde el servidor (TikTok y otras suelen bloquear a los "
                             "servidores en la nube)." + upload + f" Detalle técnico: {msg[-200:]}") from e
        raise ValueError(f"No pude bajar el video de ese link." + upload + f" Detalle técnico: {msg[-300:]}") from e
    return await ingest(path, url, title, conversation_id, rid)


async def from_file(data: bytes, filename: str, conversation_id: str | None = None, token: str = "") -> dict:
    rid = uuid.uuid4().hex[:12]
    ext = Path(filename or "video.mp4").suffix.lower() or ".mp4"
    path = settings.media_dir / f"ref_{rid}{ext}"
    path.write_bytes(data)
    return await ingest(str(path), "", Path(filename or "video").stem, conversation_id, rid, token)


# ------------------------------------------------------------------ Análisis
async def ingest(path: str, url: str, title: str, conversation_id: str | None, rid: str | None = None,
                 token: str = "") -> dict:
    import time
    from app import audio_lib, video as vid, working
    from app.agents import specialists as sp
    from app.voice import has_audio
    rid = rid or uuid.uuid4().hex[:12]
    t0 = time.monotonic()

    def step(text: str) -> None:   # avance en vivo para el chip «Analizando video…»
        if token:
            events.publish({"type": "reference_progress", "token": token, "text": text})
    step("Preparando el video…")
    seconds = await asyncio.to_thread(vid.duration, path)
    if seconds <= 0:
        raise ValueError("No pude leer ese video")
    if seconds > MAX_SECONDS:
        raise ValueError(f"El video dura {int(seconds)} s: el máximo es {MAX_SECONDS} s")
    # MP4 limpio (para verlo en la web y para sacar cuadros)
    mp4 = str(settings.media_dir / f"ref_{rid}_src.mp4")
    copied = False
    if await asyncio.to_thread(vid.web_ready, path):   # TikTok/Reels ya vienen en H.264: se copia (casi instantáneo)
        try:
            await asyncio.to_thread(vid._run, ["-i", path, "-map", "0:v:0", "-map", "0:a?", "-c", "copy",
                                               "-movflags", "+faststart", mp4], None, True)
            copied = True
        except RuntimeError:
            copied = False
    if not copied:
        step("Convirtiendo el video a un formato compatible…")
        await asyncio.to_thread(vid._run, ["-i", path, "-map", "0:v:0", "-map", "0:a?", "-vf",
                                           "scale='min(1080,iw)':-2,format=yuv420p", *[x for x in vid._ENCODE if x != "-an"],
                                           "-c:a", "aac", "-b:a", "192k", mp4])
    if Path(path) != Path(mp4):
        Path(path).unlink(missing_ok=True)
    # Cuadros clave: uno cada ~3 s (entre 2 y 6)
    n = max(2, min(6, round(seconds / 3)))
    times = [seconds * (i + 0.5) / n for i in range(n)]
    step(f"Sacando {n} escenas…")
    frames = [(str(settings.media_dir / f"ref_{rid}_f{i}.jpg"), t) for i, t in enumerate(times)]
    await asyncio.gather(*(asyncio.to_thread(vid.frame_at, mp4, f, f"{t:.2f}", True) for f, t in frames))
    from app.media import size_of
    w, h = size_of(frames[0][0])
    platform = "tiktok" if h > w * 1.2 else "x" if w > h * 1.2 else "square"
    frame_posts = []
    with session() as s:
        brand = get_or_create_brand(s)
        for i, (f, t) in enumerate(frames):
            p = Post(idea=f"Cuadro {i + 1} de la referencia «{title or url or 'video'}» (t={t:.1f}s)", platform="original",
                     raw_path=f, image_path=f, width=w, height=h, status="ready", brand_id=brand.id,
                     prompt="(cuadro de video de referencia)", conversation_id=conversation_id)
            s.add(p)
            s.flush()
            frame_posts.append(p.id)
            events.publish({"type": "post", "post": post_dict(p)})
    audio = None
    step("La IA está mirando las escenas y guardo el audio…")

    async def save_audio():
        if not await asyncio.to_thread(has_audio, mp4):
            return None
        try:
            return await asyncio.to_thread(audio_lib.from_video, mp4, f"Audio de {title or 'referencia'}", "referencia", url)
        except ValueError:
            return None

    async def analyze():
        with working.track("video_analyst", f"Analizando referencia: {title or url or 'video subido'}"):
            return await sp.analyze_reference(frames, title, seconds)
    audio, analysis = await asyncio.gather(save_audio(), analyze())   # en paralelo
    log.info("Referencia %s lista en %.1f s (%s)", rid, time.monotonic() - t0, "copiada" if copied else "convertida")
    step("¡Listo!")
    ref = {"id": rid, "url": url, "title": title, "duration": round(seconds, 2), "video_path": mp4,
           "platform": platform, "frames": [f for f, _ in frames], "frame_times": [round(t, 2) for _, t in frames],
           "frame_post_ids": frame_posts, "audio_id": audio["id"] if audio else "", "analysis": analysis,
           "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    events.publish({"type": "reference", "reference": public(ref)})
    if audio:
        events.publish({"type": "audio", "audio": audio})
    return _save(ref)


# ------------------------------------------------------------------ Recrear con tus modelos
def recreate(ref_id: str, subject_ids: list[str], mode: str = "same", changes: str = "", keep_audio: bool = True,
             platform: str = "", scenes: list[int] | None = None, conversation_id: str | None = None,
             engine: str | None = None, video_engine: str | None = None) -> dict:
    """Cada cuadro -> imagen con tu modelo -> juez -> video -> juez; luego reel (y audio original si se pide)."""
    from app import pipeline
    ref = get(ref_id)
    an = ref.get("analysis") or {}
    shots = an.get("scenes") or []
    idx = [i for i in (scenes or range(len(ref["frames"]))) if 0 <= i < len(ref["frames"])]
    if not idx:
        raise ValueError("No hay escenas para recrear")
    plat = platform or ref.get("platform") or "tiktok"
    scene_ids = []
    for i in idx:
        shot = shots[i] if i < len(shots) else {}
        idea = (shot.get("what") or an.get("summary") or "escena de la referencia")
        scene_ids.append(pipeline.new_post(
            idea, plat, conversation_id=conversation_id, subject_ids=subject_ids, engine=engine, media="video",
            motion=shot.get("motion", ""), video_engine=video_engine, base_image=ref["frames"][i],
            base_mode=mode, changes=changes,
            caption_notes=f"Inspirado en un video de referencia. Gancho: {an.get('hook', '')}"))
    if len(scene_ids) > 1:
        target = pipeline.new_reel(scene_ids, an.get("summary") or ref.get("title", ""), conversation_id, plat, video_engine)
        with session() as s:
            for sid in scene_ids:
                if p := s.get(Post, sid):
                    p.reel_id = target
    else:
        target = scene_ids[0]
    if keep_audio and ref.get("audio_id"):
        asyncio.create_task(_add_audio_when_ready(target, ref["audio_id"], conversation_id))
    return {"target_id": target, "scene_ids": scene_ids, "audio": bool(keep_audio and ref.get("audio_id")),
            "kind": "reel" if len(scene_ids) > 1 else "video"}


async def _add_audio_when_ready(target: str, audio_id: str, conversation_id: str | None) -> None:
    from app import pipeline
    await pipeline.wait_for([target], timeout=7200)
    with session() as s:
        p = s.get(Post, target)
        ok = p and p.status == "ready" and p.video_path
    if ok:
        pid = pipeline.new_audio_track(target, audio_id, "replace", conversation_id=conversation_id)
        events.publish({"type": "progress", "id": target, "text": f"Poniéndole el audio original (post {pid})"})
