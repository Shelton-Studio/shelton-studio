"""Rutas nuevas: biblioteca de audios, videos de referencia, exportar todo, capacidad, dictado por voz y el robot
asistente (optimizador de trabajo)."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from app import audio_lib, events, pipeline, reference
from app.config import settings
from app.db import Post, Subject, session

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------ Biblioteca de audios
@router.get("/audio")
async def audio_list():
    return audio_lib.list_audio()


@router.post("/audio")
async def audio_upload(file: UploadFile = File(...), name: str = Form("")):
    data = await file.read()
    try:
        a = await asyncio.to_thread(audio_lib.save_upload, data, file.filename or "", name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    events.publish({"type": "audio", "audio": a})
    return a


class AudioPatch(BaseModel):
    name: str


@router.patch("/audio/{aid}")
async def audio_rename(aid: str, body: AudioPatch):
    try:
        return audio_lib.rename(aid, body.name)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.delete("/audio/{aid}")
async def audio_delete(aid: str):
    audio_lib.delete(aid)
    return {"ok": True}


class SaveAudioIn(BaseModel):
    name: str = ""


@router.post("/posts/{pid}/save-audio")
async def post_save_audio(pid: str, body: SaveAudioIn):
    """Guarda en la biblioteca el audio de un post de video."""
    with session() as s:
        p = s.get(Post, pid)
        path, idea = (p.video_path, p.idea) if p else ("", "")
    if not path or not Path(path).exists():
        raise HTTPException(400, "Ese post no tiene video")
    try:
        a = await asyncio.to_thread(audio_lib.from_video, path, body.name or f"Audio de {idea[:50]}", "post", pid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    events.publish({"type": "audio", "audio": a})
    return a


class TrackIn(BaseModel):
    audio_id: str
    mode: str = "replace"
    start: float = 0
    volume: float = 1.0


@router.post("/posts/{pid}/audio-track")
async def post_audio_track(pid: str, body: TrackIn):
    try:
        return {"id": pipeline.new_audio_track(pid, body.audio_id, body.mode, body.start, body.volume)}
    except ValueError as e:
        raise HTTPException(400, str(e))


# ------------------------------------------------------------------ Videos de referencia
@router.get("/references")
async def refs_list():
    return reference.list_refs()


@router.get("/references/{rid}")
async def refs_get(rid: str):
    try:
        return reference.public(reference.get(rid))
    except ValueError as e:
        raise HTTPException(404, str(e))


class RefUrlIn(BaseModel):
    url: str


@router.post("/references")
async def refs_from_url(body: RefUrlIn):
    try:
        return await reference.from_url(body.url.strip())
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/references/upload")
async def refs_upload(file: UploadFile = File(...), token: str = Form("")):
    data = await file.read()
    if len(data) > 300 * 1024 * 1024:
        raise HTTPException(400, "El video es muy grande (máximo 300 MB)")
    try:
        return await reference.from_file(data, file.filename or "video.mp4", token=token[:40])
    except ValueError as e:
        raise HTTPException(400, str(e))


class RecreateIn(BaseModel):
    subjects: list[str] = []
    mode: str = "same"
    changes: str = ""
    keep_audio: bool = True
    platform: str = ""


@router.post("/references/{rid}/recreate")
async def refs_recreate(rid: str, body: RecreateIn):
    sids, missing = pipeline.find_subjects(body.subjects)
    try:
        r = reference.recreate(rid, sids, body.mode, body.changes, body.keep_audio, body.platform)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {**r, "subjects_not_found": missing}


# ------------------------------------------------------------------ Exportar TODO (copia completa)
def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in " -_." else "_" for c in (name or "x")).strip()[:60] or "x"


def build_full_export(out_path: str) -> str:
    """ZIP con todo el contenido, legible a mano y listo para restaurar:
    contenido/ (imágenes y videos + textos + posts.csv), banco/ (fotos y voz de cada modelo + ficha), audios/,
    voces/, memoria/ (lecciones de cada agente y de cada modelo), referencias/, y respaldo/ (base de datos +
    carpeta data completa para migrar). Las claves (.env) NO se incluyen."""
    from app.platforms import get_platform
    from app.publishers import full_text
    data_dir = settings.DATA_DIR
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        rows = []
        with session() as s:
            for i, p in enumerate(s.query(Post).order_by(Post.created_at), 1):
                media = p.video_path or p.image_path
                if not media or not Path(media).exists():
                    continue
                plat = get_platform(p.platform)
                base = f"contenido/{p.status}/{plat.key}/{i:04d}_{p.id}"
                z.write(media, base + Path(media).suffix)
                if p.audio_path and Path(p.audio_path).exists():
                    z.write(p.audio_path, base + "_audio" + Path(p.audio_path).suffix)
                z.writestr(base + ".txt", full_text(p))
                rows.append({"file": base + Path(media).suffix, "id": p.id, "status": p.status, "type": p.media_type,
                             "platform": plat.name, "idea": p.idea, "caption": p.caption,
                             "hashtags": " ".join(p.hashtags or []), "qa_score": p.qa_score,
                             "made_with": json.dumps(p.made_with or {}, ensure_ascii=False),
                             "created": p.created_at.isoformat() if p.created_at else ""})
            out = io.StringIO()
            w = csv.DictWriter(out, fieldnames=list(rows[0].keys()) if rows else ["file"])
            w.writeheader()
            w.writerows(rows)
            z.writestr("contenido/posts.csv", "﻿" + out.getvalue())
            for sub in s.query(Subject).order_by(Subject.created_at):
                folder = f"banco/{_safe(sub.name)}_{sub.id}"
                for j, ref in enumerate(sub.references or [], 1):
                    if Path(ref).exists():
                        z.write(ref, f"{folder}/foto_{j}{Path(ref).suffix}")
                if sub.voice_path and Path(sub.voice_path).exists():
                    z.write(sub.voice_path, f"{folder}/voz{Path(sub.voice_path).suffix}")
                z.writestr(f"{folder}/ficha.json", json.dumps({
                    "name": sub.name, "kind": sub.kind, "description": sub.description, "notes": sub.notes,
                    "voice_style": sub.voice_style}, ensure_ascii=False, indent=2))
        for a in audio_lib.list_audio():
            if Path(a["path"]).exists():
                z.write(a["path"], f"audios/{_safe(a['name'])}_{a['id']}{Path(a['path']).suffix}")
        for sub_dir, arc in (("voices", "voces"), ("training", "memoria"), ("references", "referencias")):
            d = data_dir / sub_dir
            if d.exists():
                for f in d.rglob("*"):
                    if f.is_file():
                        z.write(f, f"{arc}/{f.relative_to(d).as_posix()}")
        # Respaldo para restaurar en otra máquina: base de datos consistente + media
        db = data_dir / "shelton.db"
        if db.exists():
            with tempfile.TemporaryDirectory() as td:
                copy = Path(td) / "shelton.db"
                src = sqlite3.connect(str(db))
                dst = sqlite3.connect(str(copy))
                with dst:
                    src.backup(dst)
                src.close()
                dst.close()
                z.write(copy, "respaldo/data/shelton.db")
        media = settings.media_dir
        if media.exists():
            for f in media.iterdir():
                if f.is_file():
                    z.write(f, f"respaldo/data/media/{f.name}")
        z.writestr("LEEME.txt", (
            "Exportación completa de Shelton Studio — " + datetime.now().strftime("%Y-%m-%d %H:%M") + "\n\n"
            "contenido/   imágenes y videos con su texto (por estado y red) + posts.csv\n"
            "banco/       fotos, voz y ficha de cada modelo\n"
            "audios/      biblioteca de audios (WAV/MP3)\n"
            "voces/       banco de voces\n"
            "memoria/     entrenamiento de cada agente y de cada modelo (Markdown con fecha)\n"
            "referencias/ fichas de los videos de referencia\n"
            "respaldo/    para restaurar: copia respaldo/data/* dentro de la carpeta data/ de otra instalación\n\n"
            "Las claves (.env) no se incluyen por seguridad: cópialas a mano.\n"))
    return out_path


@router.get("/export/all")
async def export_all():
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()
    await asyncio.to_thread(build_full_export, tmp.name)
    name = f"shelton_todo_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.zip"
    return FileResponse(tmp.name, media_type="application/zip", filename=name,
                        background=BackgroundTask(lambda: Path(tmp.name).unlink(missing_ok=True)))


@router.get("/subjects/{sid}/export")
async def export_subject(sid: str):
    """Un modelo del banco con sus fotos, voz, ficha, memoria y todo el contenido que se hizo con él."""
    from app.agents import training
    with session() as s:
        sub = s.get(Subject, sid)
        if not sub:
            raise HTTPException(404, "Modelo no encontrado")
        refs, voice, name = list(sub.references or []), sub.voice_path, sub.name
        ficha = {"name": sub.name, "kind": sub.kind, "description": sub.description, "notes": sub.notes}
        posts = [(p.id, p.video_path or p.image_path, p.caption) for p in s.query(Post).all() if sid in (p.subject_ids or [])]
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()
    with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as z:
        for j, r in enumerate(refs, 1):
            if Path(r).exists():
                z.write(r, f"fotos/foto_{j}{Path(r).suffix}")
        if voice and Path(voice).exists():
            z.write(voice, "voz" + Path(voice).suffix)
        z.writestr("ficha.json", json.dumps(ficha, ensure_ascii=False, indent=2))
        for e in training.entries(training.subject_key(sid)):
            z.write(e["file"], f"memoria/{Path(e['file']).name}")
        for pid, media, caption in posts:
            if media and Path(media).exists():
                z.write(media, f"contenido/{pid}{Path(media).suffix}")
                z.writestr(f"contenido/{pid}.txt", caption or "")
    return FileResponse(tmp.name, media_type="application/zip", filename=f"modelo_{_safe(name)}.zip",
                        background=BackgroundTask(lambda: Path(tmp.name).unlink(missing_ok=True)))


@router.get("/posts/{pid}/download")
async def post_download(pid: str):
    with session() as s:
        p = s.get(Post, pid)
        media = (p.video_path or p.image_path) if p else ""
    if not media or not Path(media).exists():
        raise HTTPException(404, "Ese post no tiene archivo todavía")
    return FileResponse(media, filename=f"shelton_{pid}{Path(media).suffix}")


# ------------------------------------------------------------------ Capacidad
@router.get("/capacity")
async def capacity():
    return pipeline.capacity()


# ------------------------------------------------------------------ Dictado por voz
@router.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...), language: str = Form("")):
    from app import transcribe
    data = await file.read()
    if not data:
        return {"text": "", "clear": False, "issue": "empty", "message": transcribe.MESSAGES["empty"]}
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(400, "La grabación es muy larga")
    try:
        return await transcribe.transcribe(data, file.filename or "voz.webm", language)
    except RuntimeError as e:
        raise HTTPException(503, str(e))


# ------------------------------------------------------------------ Robot asistente (optimizador de trabajo)
@router.get("/buddy")
async def buddy():
    """Sugerencias para aprovechar el tiempo: mientras algo se procesa, qué más se puede ir haciendo."""
    from app import working
    cap = pipeline.capacity()
    with session() as s:
        subs = s.query(Subject).order_by(Subject.created_at.desc()).all()
        busy_posts = (s.query(Post).filter(Post.status.in_(["queued", "generating"]))
                      .order_by(Post.created_at.desc()).limit(20).all())
        to_review = s.query(Post).filter(Post.status == "ready").count()
        used = {}
        for p in s.query(Post).order_by(Post.created_at.desc()).limit(300):
            for sid in p.subject_ids or []:
                used.setdefault(sid, p.created_at)
        busy_subjects = {sid for p in busy_posts for sid in (p.subject_ids or [])}
        subjects = [{"id": x.id, "name": x.name, "kind": x.kind,
                     "photo": f"/media/{Path(x.references[0]).name}" if x.references else "",
                     "busy": x.id in busy_subjects, "last_used": used.get(x.id).isoformat() + "Z" if used.get(x.id) else None}
                    for x in subs]
        busy_ideas = [p.idea[:70] for p in busy_posts[:3]]
    tips = []
    names = {x["id"]: x["name"] for x in subjects}
    if busy_posts:
        mins = max(1, round(cap["queue_eta_seconds"] / 60))
        tips.append({"text": f"Estoy con {len(busy_posts)} trabajo(s) (~{mins} min). Mientras tanto, ¿abro otro modelo "
                             "u otro chat? Dime cuál, por escrito, tocando o con tu voz.", "kind": "parallel"})
        for sid in list(busy_subjects)[:2]:
            if sid in names:
                tips.append({"text": f"Mientras sale lo de {names[sid]}, ¿hago 3 fotos más de {names[sid]} en otro lugar?",
                             "message": f"Mientras termina lo que está en cola, haz 3 fotos nuevas de {names[sid]} en lugares distintos",
                             "kind": "more"})
    idle = [x for x in subjects if not x["busy"]]
    idle.sort(key=lambda x: x["last_used"] or "")
    for x in idle[:2]:
        tips.append({"text": f"{x['name']} lleva rato sin contenido nuevo. ¿Le hacemos un reel o fotos?",
                     "message": f"Haz un reel corto de {x['name']} con 3 escenas en tendencia", "kind": "model",
                     "subject": x["name"]})
    if not subjects:
        tips.append({"text": "Aún no tienes modelos en el banco. Pega 3-6 fotos de la misma persona, auto o lugar y "
                             "lo creo para que todo salga consistente.", "route": "banco", "kind": "bank"})
    refs = [r for r in reference.list_refs(5)]
    if refs:
        r = refs[0]
        tips.append({"text": f"¿Recreo el video de referencia «{(r.get('title') or 'último')[:40]}» con uno de tus modelos?",
                     "message": f"Recrea el video de referencia {r['id']} igual pero con mi modelo", "kind": "reference"})
    if to_review >= 5:
        tips.append({"text": f"Tienes {to_review} posts esperando tu revisión en la Galería.", "route": "galeria",
                     "kind": "review"})
    if not busy_posts and subjects:
        tips.append({"text": "Estoy libre ⚡ Puedo trabajar varias cosas a la vez: dime qué modelo abro y qué hacemos.",
                     "kind": "parallel"})
    return {"capacity": cap, "subjects": subjects[:12], "tips": tips[:6], "busy": busy_ideas,
            "working": working.snapshot(), **live_state(), "timing": _timing_brief(), "review": _review_brief()}


def live_state() -> dict:
    """Lo que está vivo ahora: trabajos en marcha (con su paso actual) y actividad de la última hora (cada 5 min)."""
    from datetime import timedelta
    from app.db import AgentRun, Job, now
    with session() as s:
        jobs = []
        for j in s.query(Job).filter(Job.status.in_(["running", "pending"])).order_by(Job.created_at).limit(30):
            p = s.get(Post, j.post_id)
            jobs.append({"kind": pipeline.JOB_NAMES.get(j.kind, j.kind), "status": j.status, "post_id": j.post_id,
                         "idea": (p.idea if p else "")[:80], "media": p.media_type if p else "",
                         "since": (j.started_at or j.created_at).isoformat() + "Z"})
        t0 = now() - timedelta(minutes=60)
        buckets = [0] * 12
        fails = 0
        for r in s.query(AgentRun).filter(AgentRun.created_at >= t0):
            i = min(11, int((r.created_at - t0).total_seconds() // 300))
            buckets[i] += 1
            fails += 0 if r.ok else 1
        done_h = s.query(Job).filter(Job.status == "done", Job.updated_at >= t0).count()
        failed_h = s.query(Job).filter(Job.status == "failed", Job.updated_at >= t0).count()
    return {"jobs": jobs, "activity": buckets, "last_hour": {"runs": sum(buckets), "run_errors": fails,
                                                             "jobs_done": done_h, "jobs_failed": failed_h}}


def _timing_brief() -> dict:
    from app import timing
    t = timing.load()
    return {"updated": t.get("updated"), "summary": t.get("summary", ""), "timezone": t.get("timezone") or timing.tz_name(),
            "platforms": t.get("platforms", [])[:6], "models": t.get("models", [])[:12], "sources": t.get("sources", [])[:6]}


def _review_brief() -> dict:
    r = pipeline.last_review()
    return {**r, "every_hours": settings.NETWORK_REVIEW_HOURS}


# ------------------------------------------------------------------ Revisión de la red y mejor hora para publicar
@router.get("/timing")
async def timing_get():
    from app import timing
    return {**timing.load(), "inside": timing.inside_stats()}


_review_task: asyncio.Task | None = None


@router.post("/network-review")
async def network_review_now():
    """Lanza ya la revisión de la red (scouts + mejor hora para publicar). Corre en segundo plano."""
    global _review_task
    if _review_task and not _review_task.done():
        return {"running": True}
    _review_task = asyncio.create_task(pipeline.run_network_review())
    return {"running": True, "started": True}


# ------------------------------------------------------------------ LoRA propia de cada modelo (entrenar en la GPU)
@router.get("/subjects/{sid}/lora")
async def lora_status(sid: str):
    from app import lora
    try:
        return lora.status(sid)
    except ValueError as e:
        raise HTTPException(404, str(e))


class LoraTrainIn(BaseModel):
    steps: int | None = None


@router.post("/subjects/{sid}/lora/train")
async def lora_train(sid: str, body: LoraTrainIn):
    from app import lora
    try:
        return lora.start(sid, body.steps)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/subjects/{sid}/lora/cancel")
async def lora_cancel(sid: str):
    from app import lora
    lora.cancel(sid)
    return {"ok": True}


class LoraToggleIn(BaseModel):
    enabled: bool


@router.post("/subjects/{sid}/lora/enabled")
async def lora_enabled(sid: str, body: LoraToggleIn):
    from app.serialize import subject_dict
    with session() as s:
        sub = s.get(Subject, sid)
        if not sub:
            raise HTTPException(404, "Modelo no encontrado")
        sub.lora_enabled = body.enabled
        s.flush()
        data = subject_dict(sub)
    events.publish({"type": "subject", "subject": data})
    return data


# ------------------------------------------------------------------ Archivos de memoria (todo en .md, nada se borra)
@router.get("/memory/files")
async def memory_files():
    from app import memoria
    return memoria.tree()


@router.get("/memory/file")
async def memory_file(path: str, download: bool = False):
    from fastapi.responses import FileResponse as _FR
    from app import memoria
    try:
        p = memoria.resolve(path)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return _FR(p, media_type="text/markdown; charset=utf-8", filename=p.name if download else None)


@router.get("/memory/zip")
async def memory_zip(folder: str = ""):
    from fastapi.responses import Response as _R
    from app import memoria
    data = await asyncio.to_thread(memoria.zip_bytes, folder or None)
    name = f"memoria_{folder or 'completa'}_{datetime.now():%Y-%m-%d}.zip"
    return _R(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/training/{agent}/{entry_id}/restore")
async def lesson_restore(agent: str, entry_id: str):
    from app.agents import training
    try:
        return training.restore(agent, entry_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
