"""Cadena de producción que ejecutan los subagentes de Shelton.

crear post:  director creativo -> motor de imagen -> juez de calidad (reintenta si falla) -> formato final -> redactor
editar post: director de edición -> Qwen-Image-Edit -> juez -> (redactor si hace falta)
crear modelo del banco: diseñador -> imagen de referencia -> juez -> guardar ficha
"""
import asyncio
import json
import logging
import random
import shutil
import traceback
from pathlib import Path

from app import events
from app.agents import specialists as sp
from app.config import settings
from app.db import Brand, Campaign, Job, Post, Subject, get_or_create_brand, now, session
from app.engines import get_engine
from app.media import fit_to_platform, save_bytes
from app.platforms import generation_size, get_platform
from app.serialize import post_dict, subject_dict

log = logging.getLogger("shelton.pipeline")

_queue: asyncio.Queue[str] = asyncio.Queue()
_done: dict[str, asyncio.Event] = {}      # target_id -> evento al terminar
_workers: list[asyncio.Task] = []


# ============================================================ Cola
def enqueue(kind: str, target_id: str, payload: dict | None = None) -> str:
    with session() as s:
        job = Job(kind=kind, post_id=target_id, payload=payload or {})
        s.add(job)
        s.flush()
        job_id = job.id
    _done.setdefault(target_id, asyncio.Event())
    _queue.put_nowait(job_id)
    return job_id


async def wait_for(target_ids: list[str], timeout: float = 900) -> None:
    evs = [_done.setdefault(t, asyncio.Event()) for t in target_ids]
    try:
        await asyncio.wait_for(asyncio.gather(*(e.wait() for e in evs)), timeout)
    except asyncio.TimeoutError:
        pass


def queue_stats() -> dict:
    with session() as s:
        pending = s.query(Job).filter(Job.status == "pending").count()
        running = s.query(Job).filter(Job.status == "running").count()
        today = now().replace(hour=0, minute=0, second=0, microsecond=0)
        ready_today = s.query(Post).filter(Post.created_at >= today,
                                           Post.status.in_(["ready", "approved", "published"])).count()
    return {"pending": pending, "running": running, "ready_today": ready_today, "workers": len(_workers)}


async def start_workers() -> None:
    # Al arrancar, retoma lo que quedó a medias.
    with session() as s:
        for job in s.query(Job).filter(Job.status.in_(["pending", "running"])).order_by(Job.created_at):
            job.status = "pending"
            _done.setdefault(job.post_id, asyncio.Event())
            _queue.put_nowait(job.id)
    for i in range(max(1, settings.WORKERS)):
        _workers.append(asyncio.create_task(_worker(i)))


async def stop_workers() -> None:
    for t in _workers:
        t.cancel()
    _workers.clear()


async def _worker(n: int) -> None:
    while True:
        job_id = await _queue.get()
        try:
            await _run_job(job_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("worker %s: fallo inesperado en %s", n, job_id)
        finally:
            _queue.task_done()


async def _run_job(job_id: str) -> None:
    with session() as s:
        job = s.get(Job, job_id)
        if not job or job.status not in ("pending", "running"):
            return
        job.status = "running"
        kind, target, payload = job.kind, job.post_id, dict(job.payload or {})

    handler = {"create": _create_post, "edit": _edit_post, "subject": _create_subject,
               "video": _video_post, "reel": _reel_post, "audio": _audio_post}[kind]
    try:
        await handler(target, payload)
        status, error = "done", ""
    except Exception as e:
        log.error("job %s (%s) falló: %s\n%s", job_id, kind, e, traceback.format_exc())
        status, error = "failed", str(e)[:1000]
        if kind == "subject":
            events.publish({"type": "subject_failed", "id": target, "error": error})
        else:
            _update_post(target, status="failed", error=error)
    with session() as s:
        job = s.get(Job, job_id)
        job.status, job.error = status, error
    _done.setdefault(target, asyncio.Event()).set()


# ============================================================ Utilidades
def _update_post(post_id: str, **fields) -> dict:
    with session() as s:
        p = s.get(Post, post_id)
        for k, v in fields.items():
            setattr(p, k, v)
        s.flush()
        data = post_dict(p)
    events.publish({"type": "post", "post": data})
    return data


def reference_images(subjects, limit: int = 3) -> list[bytes]:
    """Fotos de referencia para mantener la identidad: hasta 3 en total.
    Un solo modelo -> sus 3 mejores fotos (distintos ángulos); varios -> la principal de cada uno primero."""
    lists = [[r for r in (s.references or []) if Path(r).exists()] for s in subjects]
    lists = [lst for lst in lists if lst]
    picked: list[str] = []
    depth = 0
    while len(picked) < limit and any(depth < len(lst) for lst in lists):
        for lst in lists:
            if depth < len(lst) and len(picked) < limit:
                picked.append(lst[depth])
        depth += 1
    return [Path(p).read_bytes() for p in picked]


def _mark(post_id: str, kind: str, label: str) -> None:
    """Guarda qué modelo hizo cada parte del post (se muestra como etiqueta)."""
    with session() as s:
        p = s.get(Post, post_id)
        if p:
            p.made_with = {**(p.made_with or {}), kind: label}
    icon = {"image": "🖼️ Modo imagen", "edit": "✏️ Modo edición", "video": "🎬 Modo video", "voice": "🎙️ Modo audio",
            "sound": "🔊 Modo audio"}.get(kind, kind)
    events.publish({"type": "progress", "id": post_id, "text": f"{icon} · {label}"})


def _progress(post_id: str, text: str):
    async def cb(extra: str):
        events.publish({"type": "progress", "id": post_id, "text": f"{text} · {extra}"})
    return cb


def _load(post_id: str):
    with session() as s:
        p = s.get(Post, post_id)
        brand = s.get(Brand, p.brand_id) if p.brand_id else get_or_create_brand(s)
        subjects = [x for x in (s.get(Subject, i) for i in (p.subject_ids or [])) if x]
        return p, brand, subjects


# ============================================================ Crear post
async def _create_post(post_id: str, payload: dict) -> None:
    post, brand, subjects = _load(post_id)
    platform = get_platform(post.platform)
    width, height = generation_size(platform.aspect)
    choice = payload.get("engine") or None
    refs = reference_images(subjects)
    engine = get_engine("subject" if refs else "generate", choice)
    from app.engines import engine_label
    _mark(post_id, "image", engine_label(engine, "subject" if refs else "generate"))

    feedback: list[str] = []
    tries: list[dict] = []   # todos los intentos se conservan
    for attempt in range(1, 2 + max(0, settings.QA_MAX_RETRIES)):
        _update_post(post_id, status="generating", attempts=attempt, error="")
        events.publish({"type": "progress", "id": post_id, "text": f"Director creativo (intento {attempt})"})
        ad = await sp.art_direct(post.idea, platform, brand, feedback, subjects)
        seed = random.randint(1, 2**31 - 1)

        step = _progress(post_id, f"Generando imagen (intento {attempt})")
        from app import working
        from app.engines import engine_label as _el
        with working.track("motor_imagen", f"{post.idea[:120]} (intento {attempt})", _el(engine, "generate"), post_id, log=True):
            if refs:
                raw = await engine.generate_with_refs(refs, ad["prompt"], width, height, seed, step)
            else:
                raw = await engine.generate(ad["prompt"], width, height, seed, step)
        raw_path = save_bytes(raw, f"{post_id}_a{attempt}_raw.png")
        final_path = fit_to_platform(raw_path, platform.width, platform.height, f"{post_id}_a{attempt}.jpg")

        events.publish({"type": "progress", "id": post_id, "text": f"Juez de calidad revisando (intento {attempt})"})
        qa = await sp.judge(final_path, post.idea, ad["prompt"], brand, subjects)
        score = int(qa.get("score", 0))
        tries.append({"prompt": ad["prompt"], "seed": seed, "raw": raw_path, "final": final_path, "score": score,
                      "issues": qa.get("issues", []), "summary": qa.get("summary", ""), "fix_hint": qa.get("fix_hint", "")})
        best = max(tries, key=lambda t: t["score"])
        _update_post(post_id, prompt=best["prompt"], image_path=best["final"], raw_path=best["raw"],
                     qa_score=best["score"], qa_notes=_qa_text(best, best["score"] >= settings.QA_MIN_SCORE),
                     alternatives=[t for t in tries if t is not best])
        if score >= settings.QA_MIN_SCORE:
            break
        feedback += [*qa.get("issues", []), qa.get("fix_hint", "")]

    best = max(tries, key=lambda t: t["score"])
    events.publish({"type": "progress", "id": post_id, "text": "Redactor escribiendo el texto"})
    copy = await sp.write_post(best["final"], post.idea, platform, brand, payload.get("caption_notes", ""))
    passed = best["score"] >= settings.QA_MIN_SCORE
    hashtags = _merge_hashtags(copy.get("hashtags", []), brand.base_hashtags if brand else [])
    # Siempre termina en revisión humana: el juez solo opina.
    _update_post(post_id, prompt=best["prompt"], seed=best["seed"], raw_path=best["raw"], image_path=best["final"],
                 width=_dims(platform, best["final"])[0], height=_dims(platform, best["final"])[1],
                 caption=copy["caption"], hashtags=hashtags,
                 alt_text=copy.get("alt_text", ""), qa_score=best["score"], qa_passed=passed,
                 qa_notes=_qa_text(best, passed), alternatives=[t for t in tries if t is not best],
                 status="generating" if payload.get("media") == "video" else "ready")
    if payload.get("media") == "video":
        await _animate_into(post_id, best["final"], payload.get("motion", ""), payload.get("video_engine") or None,
                            rewrite_caption=True)


def _dims(platform, image_path: str | None) -> tuple[int, int]:
    """Tamaño final: el de la red, o el de la imagen de origen (pares, máx. 1920) si el formato es 'original'."""
    if platform.width and platform.height:
        return platform.width, platform.height
    from app.media import size_of
    w, h = size_of(image_path) if image_path else (1080, 1350)
    k = min(1.0, 1920 / max(w, h))
    return int(w * k) // 2 * 2, int(h * k) // 2 * 2


# ============================================================ Video y reels
async def _animate_into(post_id: str, source_image: str, motion: str, choice: str | None, rewrite_caption: bool) -> None:
    """Productor de video -> Wan 2.2 -> MP4 vertical interpolado -> juez revisa 3 cuadros -> (copywriter)."""
    from app import video as vid
    post, brand, subjects = _load(post_id)
    platform = get_platform(post.platform)
    _update_post(post_id, status="generating", media_type="video")
    events.publish({"type": "progress", "id": post_id, "text": "Productor de video dirigiendo el movimiento"})
    vp = await sp.direct_video(source_image, post.idea, motion)
    engine = get_engine("video", choice)
    step = _progress(post_id, "Animando con el motor de video")
    W, H = _dims(platform, source_image)
    from app.engines import describe_role, engine_label
    _mark(post_id, "video", await describe_role("video", choice))
    from app import working
    with working.track("motor_video", post.idea[:140], await describe_role("video", choice), post_id, log=True):
        clip, fps = await engine.video(Path(source_image).read_bytes(), vp["motion_prompt"], vp.get("negative", ""),
                                       W, H, random.randint(1, 2**31 - 1), None, step)
    _mark(post_id, "video", engine_label(engine, "video"))
    raw = save_bytes(clip, f"{post_id}_clip.{'webp' if vid.is_webp(clip) else 'mp4'}")
    events.publish({"type": "progress", "id": post_id, "text": "Post-producción: 30 fps y formato vertical"})
    mp4 = str(settings.media_dir / f"{post_id}.mp4")
    await asyncio.to_thread(vid.clip_to_mp4, clip, fps, mp4, W, H, settings.VIDEO_OUTPUT_FPS, True)
    poster = await asyncio.to_thread(vid.frame_at, mp4, str(settings.media_dir / f"{post_id}_poster.jpg"))
    sheet = await asyncio.to_thread(vid.contact_sheet, mp4, str(settings.media_dir / f"{post_id}_sheet.jpg"))
    seconds = await asyncio.to_thread(vid.duration, mp4)
    events.publish({"type": "progress", "id": post_id, "text": "Juez revisando el video"})
    qa = await sp.judge(sheet, f"{post.idea} (these are 3 frames — start, middle, end — of a short video clip: also "
                               "check temporal consistency, warping, identity drift and flicker)", vp["motion_prompt"],
                        brand, subjects)
    score = int(qa.get("score", 0))
    fields = dict(video_path=mp4, raw_path=raw, image_path=poster, duration=seconds, width=W, height=H, qa_score=score, qa_passed=score >= settings.QA_MIN_SCORE,
                  qa_notes=_qa_text(qa, score >= settings.QA_MIN_SCORE),
                  prompt=f"{post.prompt}\n[VIDEO] {vp['motion_prompt']}".strip())
    if rewrite_caption:
        events.publish({"type": "progress", "id": post_id, "text": "Copywriter escribiendo el texto del reel"})
        copy = await sp.write_post(poster, post.idea, platform, brand, "Es un video corto (Reel/TikTok): usa un gancho "
                                   "que invite a verlo entero.")
        fields.update(caption=copy["caption"], alt_text=copy.get("alt_text", ""),
                      hashtags=_merge_hashtags(copy.get("hashtags", []), brand.base_hashtags if brand else []))
    _update_post(post_id, status="ready", **fields)


async def _video_post(post_id: str, payload: dict) -> None:
    """Animar un post existente (crea un post nuevo de video; el original se conserva)."""
    with session() as s:
        parent = s.get(Post, s.get(Post, post_id).parent_id)
        src = parent.raw_path if parent.raw_path and not parent.raw_path.endswith((".webp", ".mp4")) else parent.image_path
        _update_post(post_id, caption=parent.caption, hashtags=parent.hashtags, alt_text=parent.alt_text,
                     prompt=parent.prompt)
    await _animate_into(post_id, src, payload.get("motion", ""), payload.get("engine") or None, rewrite_caption=True)


async def _reel_post(post_id: str, payload: dict) -> None:
    """Une los clips (en orden) en un reel y le escribe el texto."""
    from app import video as vid
    post, brand, _ = _load(post_id)
    platform = get_platform(post.platform)
    with session() as s:
        clips = [p.video_path for p in (s.get(Post, i) for i in payload["clip_ids"]) if p and p.video_path]
    if not clips:
        raise RuntimeError("Ninguna escena terminó con video; revisa el motor de video en Estado")
    _update_post(post_id, status="generating", media_type="reel")
    events.publish({"type": "progress", "id": post_id, "text": f"Montando el reel ({len(clips)} escenas)"})
    mp4 = str(settings.media_dir / f"{post_id}.mp4")
    W, H = _dims(platform, post.image_path or None) if platform.width else (1080, 1920)
    await asyncio.to_thread(vid.concat, clips, mp4, W, H, settings.VIDEO_OUTPUT_FPS)
    poster = await asyncio.to_thread(vid.frame_at, mp4, str(settings.media_dir / f"{post_id}_poster.jpg"), "0.5")
    seconds = await asyncio.to_thread(vid.duration, mp4)
    copy = await sp.write_post(poster, post.idea, platform, brand,
                               f"Es un reel de {len(clips)} escenas y {seconds:.0f} s: gancho fuerte en la primera línea.")
    with session() as s:
        scores = [p.qa_score for p in (s.get(Post, i) for i in payload["clip_ids"]) if p and p.qa_score]
    avg = round(sum(scores) / len(scores)) if scores else 0
    _update_post(post_id, status="ready", video_path=mp4, image_path=poster, duration=seconds, width=W,
                 height=H, caption=copy["caption"], alt_text=copy.get("alt_text", ""),
                 hashtags=_merge_hashtags(copy.get("hashtags", []), brand.base_hashtags if brand else []),
                 qa_score=avg, qa_passed=avg >= settings.QA_MIN_SCORE,
                 qa_notes=f"Nota media de las escenas según el juez: {avg}/100. Revisa cada escena en su post.")


def new_video(parent_id: str, motion: str = "", conversation_id=None, engine: str | None = None) -> str:
    with session() as s:
        parent = s.get(Post, parent_id)
        if not parent or not parent.image_path:
            raise ValueError(f"El post {parent_id} no existe o no tiene imagen todavía")
        p = Post(idea=parent.idea, platform=parent.platform, brand_id=parent.brand_id, parent_id=parent.id,
                 campaign_id=parent.campaign_id, conversation_id=conversation_id or parent.conversation_id,
                 subject_ids=parent.subject_ids, media_type="video", status="queued")
        s.add(p)
        s.flush()
        pid, data = p.id, post_dict(p)
    events.publish({"type": "post", "post": data})
    enqueue("video", pid, {"motion": motion, "engine": engine or ""})
    return pid


def new_reel(source_ids: list[str], idea: str = "", conversation_id=None, platform: str = "tiktok",
             engine: str | None = None) -> str:
    """Reel a partir de posts: los que son imagen se animan primero; luego se unen en orden."""
    with session() as s:
        sources = [s.get(Post, i) for i in source_ids]
        if not all(sources):
            raise ValueError("Algún post no existe")
        brand_id = sources[0].brand_id
        reel = Post(idea=idea or " / ".join(p.idea for p in sources)[:500], platform=get_platform(platform).key,
                    brand_id=brand_id, conversation_id=conversation_id, media_type="reel", status="queued",
                    subject_ids=sorted({x for p in sources for x in (p.subject_ids or [])}))
        s.add(reel)
        s.flush()
        rid, data = reel.id, post_dict(reel)
        needs = [(p.id, p.media_type, bool(p.video_path)) for p in sources]
    events.publish({"type": "post", "post": data})
    _done.setdefault(rid, asyncio.Event())

    async def orchestrate():
        try:
            clip_ids = []
            for pid, media, has_video in needs:
                if media in ("video", "reel") or has_video:
                    clip_ids.append(pid)
                else:
                    clip_ids.append(new_video(pid, "", conversation_id, engine))
            _update_post(rid, status="generating")
            await wait_for(clip_ids, timeout=7200)
            enqueue("reel", rid, {"clip_ids": clip_ids})
        except Exception as e:
            _update_post(rid, status="failed", error=str(e))
            _done[rid].set()
    asyncio.create_task(orchestrate())
    return rid


def new_reel_from_scenes(scenes: list[str], idea: str, platform: str = "tiktok", subject_ids=None,
                         conversation_id=None, engine: str | None = None, video_engine: str | None = None) -> str:
    """Una sola orden: cada escena pasa por imagen -> juez -> video -> juez, y al final se monta el reel."""
    scene_ids = [new_post(sc, platform, conversation_id=conversation_id, subject_ids=subject_ids, engine=engine,
                          media="video", video_engine=video_engine) for sc in scenes]
    return new_reel(scene_ids, idea, conversation_id, platform, video_engine)


# ============================================================ Voz y sonido
async def _audio_post(post_id: str, payload: dict) -> None:
    """Locutor (guion + diseño de voz) -> voz local -> efectos de voz -> efectos de sonido -> mezcla con video/imagen."""
    from app import video as vid
    from app import voice as vc
    post, brand, subjects = _load(post_id)
    with session() as s:
        parent = s.get(Post, post.parent_id)
        src_video, src_image = parent.video_path, parent.image_path
        parent_dur = parent.duration or 0
        caption, hashtags, alt = parent.caption, parent.hashtags, parent.alt_text
    platform = get_platform(post.platform)
    _update_post(post_id, status="generating", media_type="video", caption=caption, hashtags=hashtags, alt_text=alt)
    seconds = float(payload.get("seconds") or parent_dur or 8)
    tracks = []

    if payload.get("voice", True):
        events.publish({"type": "progress", "id": post_id, "text": "Locutor escribiendo el guion y diseñando la voz"})
        speaker = next((x for x in subjects if x.voice_path or x.kind in ("person", "creature")), None)
        voices = vc.list_voices()
        dv = await sp.direct_voice(post.idea, payload.get("voice_request", ""), seconds, voices,
                                   f"{speaker.name}: {speaker.voice_style or speaker.notes}" if speaker else "",
                                   payload.get("script", ""))
        spec = dv["spec"]
        ref = None
        if speaker and speaker.voice_path and Path(speaker.voice_path).exists():
            ref = Path(speaker.voice_path).read_bytes()
        elif spec.get("base_voice"):
            match = next((v for v in voices if v["name"].lower() == spec["base_voice"].lower()), None)
            if match:
                ref = (vc.voices_dir() / match["file"]).read_bytes()
        _mark(post_id, "voice", vc.voice_label())
        from app import working
        with working.track("motor_voz", dv["script"][:140], vc.voice_label(), post_id, log=True):
            wav = await vc.synthesize(dv["script"], settings.CONTENT_LANGUAGE, ref, spec)
        voice_path = str(settings.media_dir / f"{post_id}_voice.wav")
        await asyncio.to_thread(vc.apply_fx, wav, spec, voice_path)
        tracks.append(("voice", voice_path))
        _update_post(post_id, voiceover=dv["script"])

    if payload.get("sound_request"):
        events.publish({"type": "progress", "id": post_id, "text": "Diseñador de sonido creando el efecto"})
        sd = await sp.design_sound(payload["sound_request"], post.idea)
        engine = get_engine("sound", payload.get("engine") or None)
        _mark(post_id, "sound", "Stable Audio Open" if engine.name == "comfyui" else
              ("Simulado (demo)" if engine.name == "mock" else engine.name))
        sfx_len = max(seconds, vid.duration(tracks[0][1]) if tracks else seconds)
        from app import working
        with working.track("motor_sonido", payload["sound_request"][:140], engine.name, post_id, log=True):
            audio = await engine.sound(sd["prompt"], sd.get("negative", ""), sfx_len + 0.5, random.randint(1, 2**31 - 1),
                                       _progress(post_id, "Generando sonido"))
        raw = save_bytes(audio, f"{post_id}_sfx_raw.bin")
        sfx_path = str(settings.media_dir / f"{post_id}_sfx.wav")
        await asyncio.to_thread(vid._run, ["-i", raw, "-ar", "44100", "-ac", "2", sfx_path])
        tracks.append(("sfx", sfx_path))

    if not tracks:
        raise RuntimeError("Pide voz y/o un efecto de sonido")
    # Mezcla: la voz manda; el efecto queda de fondo (o solo, si no hay voz)
    mix = str(settings.media_dir / f"{post_id}_mix.wav")
    if len(tracks) == 2:
        await asyncio.to_thread(vid._run, ["-i", tracks[0][1], "-i", tracks[1][1], "-filter_complex",
                                           "[1:a]volume=0.35[b];[0:a][b]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14",
                                           "-ar", "44100", mix])
    else:
        await asyncio.to_thread(vid._run, ["-i", tracks[0][1], "-af", "loudnorm=I=-14", "-ar", "44100", mix])

    out = str(settings.media_dir / f"{post_id}.mp4")
    events.publish({"type": "progress", "id": post_id, "text": "Mezclando audio y video"})
    if src_video:
        await asyncio.to_thread(vc.mux, src_video, mix, out)
    else:
        await asyncio.to_thread(vc.still_to_video, src_image, mix, out, *_dims(platform, src_image))
    poster = await asyncio.to_thread(vid.frame_at, out, str(settings.media_dir / f"{post_id}_poster.jpg"), "0.3")
    _update_post(post_id, status="ready", video_path=out, audio_path=mix, image_path=poster,
                 duration=await asyncio.to_thread(vid.duration, out), width=_dims(platform, src_image)[0],
                 height=_dims(platform, src_image)[1],
                 qa_score=parent.qa_score, qa_passed=parent.qa_passed,
                 qa_notes="Audio agregado. Escúchalo antes de aprobar: el juez solo revisa la imagen.")


def new_audio(parent_id: str, voice_request: str = "", script: str = "", sound_request: str = "", voice: bool = True,
              conversation_id=None, seconds: float | None = None) -> str:
    with session() as s:
        parent = s.get(Post, parent_id)
        if not parent or not (parent.image_path or parent.video_path):
            raise ValueError(f"El post {parent_id} no existe o no tiene imagen/video todavía")
        p = Post(idea=parent.idea, platform=parent.platform, brand_id=parent.brand_id, parent_id=parent.id,
                 campaign_id=parent.campaign_id, conversation_id=conversation_id or parent.conversation_id,
                 subject_ids=parent.subject_ids, media_type="video", status="queued", prompt=parent.prompt)
        s.add(p)
        s.flush()
        pid, data = p.id, post_dict(p)
    events.publish({"type": "post", "post": data})
    enqueue("audio", pid, {"voice_request": voice_request, "script": script, "sound_request": sound_request,
                           "voice": voice, "seconds": seconds})
    return pid


def use_alternative(post_id: str, index: int) -> dict:
    """La persona elige un intento que el juez puntuó más bajo. El actual pasa a ser alternativa (nada se pierde)."""
    with session() as s:
        p = s.get(Post, post_id)
        alts = list(p.alternatives or [])
        if not 0 <= index < len(alts):
            raise ValueError("Esa alternativa no existe")
        chosen = alts.pop(index)
        current = {"prompt": p.prompt, "seed": p.seed, "raw": p.raw_path, "final": p.image_path, "score": p.qa_score,
                   "issues": [], "summary": p.qa_notes}
        p.alternatives = [current, *alts]
        p.prompt, p.seed, p.raw_path, p.image_path = chosen["prompt"], chosen.get("seed", 0), chosen["raw"], chosen["final"]
        p.qa_score, p.qa_passed = chosen.get("score", 0), chosen.get("score", 0) >= settings.QA_MIN_SCORE
        p.qa_notes = "Elegida por una persona. Opinión del juez: " + _qa_text(chosen, p.qa_passed)
        from app.db import Feedback
        s.add(Feedback(post_id=post_id, action="chose_lower_scored_attempt",
                       detail={"chosen_score": chosen.get("score"), "judge_preferred_score": current["score"],
                               "judge_issues_on_chosen": chosen.get("issues", []), "idea": p.idea}))
        s.flush()
        data = post_dict(p)
    events.publish({"type": "post", "post": data})
    return data


def _merge_hashtags(tags: list[str], base: list[str]) -> list[str]:
    out, seen = [], set()
    for t in [*tags, *(base or [])]:
        t = "#" + t.strip().lstrip("#").replace(" ", "")
        if len(t) > 1 and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def _qa_text(qa: dict, passed: bool) -> str:
    text = qa.get("summary", "")
    if qa.get("issues"):
        text += " | Detalles: " + "; ".join(qa["issues"])
    if not passed:
        text = "⚠ El juez la reprobó en todos los intentos. Revísala tú: tú decides si se publica. " + text
    return text


# ============================================================ Editar post
async def _edit_post(post_id: str, payload: dict) -> None:
    post, brand, subjects = _load(post_id)
    with session() as s:
        parent = s.get(Post, post.parent_id)
    platform = get_platform(post.platform)
    request = payload["instruction"]

    _update_post(post_id, status="generating", attempts=1)
    events.publish({"type": "progress", "id": post_id, "text": "Interpretando la edición"})
    ei = await sp.edit_instruction(request, parent.prompt)

    if not ei["image_change"]:
        # Solo cambia el texto: se reutiliza la imagen.
        final = shutil.copy(parent.image_path, settings.media_dir / f"{post_id}.jpg")
        raw = shutil.copy(parent.raw_path, settings.media_dir / f"{post_id}_raw.png") if parent.raw_path else str(final)
        qa_score, qa_notes, prompt = parent.qa_score, parent.qa_notes, parent.prompt
    else:
        step = _progress(post_id, "Editando con Qwen-Image-Edit")
        src = Path(parent.raw_path or parent.image_path).read_bytes()
        edit_engine = get_engine("edit", payload.get("engine") or None)
        from app.engines import engine_label
        _mark(post_id, "edit", engine_label(edit_engine, "edit"))
        from app import working
        with working.track("motor_imagen", f"Editar: {request[:120]}", engine_label(edit_engine, "edit"), post_id, log=True):
            out = await edit_engine.edit(src, ei["instruction"], random.randint(1, 2**31 - 1), step)
        raw = save_bytes(out, f"{post_id}_raw.png")
        if platform.key == "original":
            from app.media import resize_to, size_of
            final = resize_to(raw, size_of(parent.image_path), f"{post_id}.jpg")
        else:
            final = fit_to_platform(raw, platform.width, platform.height, f"{post_id}.jpg")
        prompt = f"{parent.prompt}\n[EDIT] {ei['instruction']}"
        events.publish({"type": "progress", "id": post_id, "text": "Juez de calidad revisando la edición"})
        qa = await sp.judge(str(final), f"{parent.idea}. Edit requested: {request}", prompt, brand, subjects,
                            original_path=parent.image_path)
        qa_score = int(qa.get("score", 0))
        qa_notes = _qa_text(qa, qa_score >= settings.QA_MIN_SCORE)

    caption, hashtags, alt = parent.caption, parent.hashtags, parent.alt_text
    if not ei["image_change"] or payload.get("rewrite_caption"):
        copy = await sp.write_post(str(final), parent.idea, platform, brand, request)
        caption, alt = copy["caption"], copy.get("alt_text", alt)
        hashtags = _merge_hashtags(copy.get("hashtags", []), brand.base_hashtags if brand else [])
    from app.media import size_of
    fw, fh = size_of(str(final))
    _update_post(post_id, prompt=prompt, raw_path=str(raw), image_path=str(final), width=fw,
                 height=fh, caption=caption, hashtags=hashtags, alt_text=alt,
                 qa_score=qa_score, qa_notes=qa_notes, qa_passed=qa_score >= settings.QA_MIN_SCORE, status="ready")


# ============================================================ Banco de modelos
async def _create_subject(subject_id: str, payload: dict) -> None:
    with session() as s:
        sub = s.get(Subject, subject_id)
        brand = get_or_create_brand(s)
        name, kind = sub.name, sub.kind
    events.publish({"type": "subject_progress", "id": subject_id, "text": "Diseñando la ficha"})
    design = await sp.design_subject(name, kind, payload.get("request", ""), brand)
    with session() as s:
        sub = s.get(Subject, subject_id)
        sub.description, sub.notes = design["description"], design["notes"]

    width, height = generation_size("4:5" if kind in ("person", "creature") else "1:1")
    feedback, tries = [], []
    for attempt in range(1, 2 + settings.QA_MAX_RETRIES):
        prompt = design["reference_prompt"] + (" Avoid: " + "; ".join(feedback) if feedback else "")
        events.publish({"type": "subject_progress", "id": subject_id, "text": f"Creando foto de referencia ({attempt})"})
        from app import working
        with working.track("motor_imagen", f"Referencia de {name} ({attempt})", "", None, log=True):
            raw = await get_engine("generate", payload.get("engine") or None).generate(prompt, width, height, random.randint(1, 2**31 - 1))
        path = save_bytes(raw, f"subject_{subject_id}_{attempt}.png")
        qa = await sp.judge(path, f"Reference picture of {name}: {design['description']}", prompt, brand)
        tries.append((path, int(qa.get("score", 0))))
        if tries[-1][1] >= settings.QA_MIN_SCORE:
            break
        feedback += qa.get("issues", [])
    best = max(tries, key=lambda t: t[1])
    with session() as s:
        sub = s.get(Subject, subject_id)
        sub.references = [best[0], *(sub.references or [])]
        sub.alternatives = [*(sub.alternatives or []), *(t[0] for t in tries if t is not best)]
        data = subject_dict(sub)
    events.publish({"type": "subject", "subject": data})


def use_subject_alternative(subject_id: str, index: int) -> dict:
    """La persona elige otra foto como referencia principal; la anterior queda como alternativa."""
    with session() as s:
        sub = s.get(Subject, subject_id)
        alts = list(sub.alternatives or [])
        if not 0 <= index < len(alts):
            raise ValueError("Esa alternativa no existe")
        chosen = alts.pop(index)
        refs = list(sub.references or [])
        sub.alternatives = ([refs[0]] if refs else []) + alts
        sub.references = [chosen, *refs[1:]]
        data = subject_dict(sub)
    events.publish({"type": "subject", "subject": data})
    return data


async def create_subject_from_photos(name: str, image_paths: list[str], kind: str = "auto", notes: str = "") -> dict:
    """Banco de fotos -> modelo. El Analista mira todas, escribe la ficha de identidad y detecta el tipo.
    Si ya existe un modelo con ese nombre, se le agregan las fotos y se rehace la ficha."""
    from app import working
    paths = [p for p in image_paths if p and Path(p).exists()]
    if not paths:
        raise ValueError("No llegó ninguna foto válida")
    found, _ = find_subjects([name])
    with session() as s:
        if found:
            sub = s.get(Subject, found[0])
            sub.references = [*(sub.references or []), *[p for p in paths if p not in (sub.references or [])]]
        else:
            sub = Subject(name=name.strip(), kind=kind if kind != "auto" else "other", notes=notes, references=paths)
            s.add(sub)
        s.flush()
        sid, all_refs = sub.id, list(sub.references)
    events.publish({"type": "subject_progress", "id": sid, "text": f"Analizando {len(all_refs)} fotos"})
    with working.track("subject_analyst", f"Creando el modelo {name} desde {len(paths)} fotos"):
        info = await sp.describe_subject_photos(all_refs, name, kind, notes)
    with session() as s:
        sub = s.get(Subject, sid)
        sub.description = info["description"]
        if kind == "auto" or not sub.kind or sub.kind == "other":
            sub.kind = info.get("kind") or sub.kind
        best = int(info.get("best_photo") or 1) - 1
        refs = list(sub.references or [])
        if 0 < best < len(refs):  # la mejor foto pasa a ser la principal
            refs.insert(0, refs.pop(best))
            sub.references = refs
        data = subject_dict(sub)
    events.publish({"type": "subject", "subject": data})
    return data


async def add_subject_reference(subject_id: str, image_path: str, describe: bool) -> dict:
    """Añade una foto (subida o de un post) a un modelo del banco; si es la primera, la analiza."""
    with session() as s:
        sub = s.get(Subject, subject_id)
        name, kind, notes, first = sub.name, sub.kind, sub.notes, not sub.references
    description = await sp.describe_subject(image_path, name, kind, notes) if (describe or first) else None
    with session() as s:
        sub = s.get(Subject, subject_id)
        sub.references = [*(sub.references or []), image_path] if not first else [image_path]
        if description:
            sub.description = description
        data = subject_dict(sub)
    events.publish({"type": "subject", "subject": data})
    return data


# ============================================================ Crear posts y campañas
def new_post(idea: str, platform: str, *, brand_id=None, campaign_id=None, conversation_id=None,
             subject_ids=None, caption_notes: str = "", engine: str | None = None, media: str = "image",
             motion: str = "", video_engine: str | None = None) -> str:
    with session() as s:
        brand_id = brand_id or get_or_create_brand(s).id
        p = Post(idea=idea, platform=get_platform(platform).key, brand_id=brand_id, campaign_id=campaign_id,
                 conversation_id=conversation_id, subject_ids=subject_ids or [], status="queued",
                 media_type="video" if media == "video" else "image")
        s.add(p)
        s.flush()
        pid, data = p.id, post_dict(p)
    events.publish({"type": "post", "post": data})
    enqueue("create", pid, {"caption_notes": caption_notes, "engine": engine or "", "media": media,
                            "motion": motion, "video_engine": video_engine or ""})
    return pid


def new_edit(parent_id: str, instruction: str, rewrite_caption: bool = False, conversation_id=None,
             engine: str | None = None) -> str:
    with session() as s:
        parent = s.get(Post, parent_id)
        if not parent or not parent.image_path:
            raise ValueError(f"El post {parent_id} no existe o no tiene imagen todavía")
        p = Post(idea=parent.idea, platform=parent.platform, brand_id=parent.brand_id, parent_id=parent.id,
                 campaign_id=parent.campaign_id, conversation_id=conversation_id or parent.conversation_id,
                 subject_ids=parent.subject_ids, status="queued")
        s.add(p)
        s.flush()
        pid, data = p.id, post_dict(p)
    events.publish({"type": "post", "post": data})
    enqueue("edit", pid, {"instruction": instruction, "rewrite_caption": rewrite_caption, "engine": engine or ""})
    from app.db import record_feedback
    record_feedback(parent_id, "requested_edit", instruction=instruction)
    return pid


def new_subject(name: str, kind: str, request: str, generate: bool = True, engine: str | None = None) -> str:
    with session() as s:
        sub = Subject(name=name.strip(), kind=kind or "person", notes=request if not generate else "")
        s.add(sub)
        s.flush()
        sid, data = sub.id, subject_dict(sub)
    events.publish({"type": "subject", "subject": data})
    if generate:
        enqueue("subject", sid, {"request": request, "engine": engine or ""})
    return sid


def find_subjects(names: list[str]) -> tuple[list[str], list[str]]:
    """Busca por nombre (sin distinguir mayúsculas). Devuelve (ids encontrados, nombres no encontrados)."""
    found, missing = [], []
    with session() as s:
        all_subs = s.query(Subject).all()
        for n in names or []:
            match = next((x for x in all_subs if x.name.lower() == n.strip().lower()), None) or \
                    next((x for x in all_subs if n.strip().lower() in x.name.lower()), None)
            (found.append(match.id) if match else missing.append(n))
    return found, missing


async def run_campaign(campaign_id: str, subject_ids: list[str] | None = None, engine: str | None = None) -> None:
    """Planifica N ideas (en tandas de 25 para variedad) y las pone todas en la cola."""
    with session() as s:
        c = s.get(Campaign, campaign_id)
        brand = get_or_create_brand(s)
        brief, count, platforms = c.brief, c.count, c.platforms or brand.platforms or ["instagram_feed"]
        subjects = [x for x in (s.get(Subject, i) for i in (subject_ids or [])) if x]
        by_name = {x.name: x.id for x in subjects}
        brand_id = brand.id
    try:
        planned: list[str] = []
        while len(planned) < count:
            batch = min(25, count - len(planned))
            events.publish({"type": "campaign", "id": campaign_id, "text": f"Planificando ideas {len(planned) + 1}-{len(planned) + batch}"})
            ideas = await sp.plan_ideas(brief, batch, platforms, brand, planned, subjects)
            if not ideas:
                break
            for it in ideas:
                planned.append(it["idea"])
                sids = [by_name[n] for n in it.get("subjects", []) if n in by_name] or (subject_ids if len(subjects) == 1 else [])
                new_post(it["idea"], it["platform"], brand_id=brand_id, campaign_id=campaign_id, subject_ids=sids,
                         engine=engine)
        status, error = "running", ""
    except Exception as e:
        log.exception("campaña %s", campaign_id)
        status, error = "failed", str(e)
    with session() as s:
        c = s.get(Campaign, campaign_id)
        c.status, c.error = status, error
    events.publish({"type": "campaign", "id": campaign_id, "text": "En cola" if not error else f"Error: {error}"})


def new_campaign(brief: str, count: int, platforms: list[str] | None = None, subject_ids=None,
                 engine: str | None = None) -> str:
    with session() as s:
        brand = get_or_create_brand(s)
        plats = [get_platform(p).key for p in (platforms or brand.platforms or ["instagram_feed"])]
        c = Campaign(brief=brief, count=max(1, min(int(count), 200)), platforms=plats, brand_id=brand.id)
        s.add(c)
        s.flush()
        cid = c.id
    asyncio.create_task(run_campaign(cid, subject_ids, engine))
    return cid


# ============================================================ Piloto automático diario
async def autopilot_loop() -> None:
    """Cada día, a la hora configurada, lanza un lote de N posts para la marca activa."""
    from datetime import datetime
    while True:
        try:
            with session() as s:
                brand = get_or_create_brand(s)
                local = datetime.now()
                today = local.strftime("%Y-%m-%d")
                due = brand.autopilot_daily > 0 and local.hour >= brand.autopilot_hour and brand.autopilot_last != today
                if due:
                    brand.autopilot_last = today
                    count = brand.autopilot_daily
            if due:
                log.info("Piloto automático: lanzando %s posts", count)
                new_campaign(f"Lote diario automático del {today}", count)
        except Exception:
            log.exception("autopilot")
        await asyncio.sleep(300)


# ============================================================ Scout 24/7
async def run_scout(kinds: tuple[str, ...] = ("trend", "tool")) -> int:
    """Investiga y guarda hallazgos. No ejecuta nada: una persona decide qué se usa."""
    from app.db import Finding
    with session() as s:
        brand = get_or_create_brand(s)
        subjects = s.query(Subject).all()
        known = [f.title for f in s.query(Finding).order_by(Finding.created_at.desc()).limit(80)]
    total = 0
    for kind in kinds:
        events.publish({"type": "scout", "text": f"Scout buscando {'tendencias' if kind == 'trend' else 'herramientas'}…"})
        try:
            found = await sp.scout(kind, brand, subjects, known)
        except Exception as e:
            log.warning("scout %s falló: %s", kind, e)
            events.publish({"type": "scout", "text": f"Scout ({kind}) falló: {e}"})
            continue
        with session() as s:
            for f in found:
                if f["title"] in known:
                    continue
                s.add(Finding(kind=kind, title=f["title"], summary=f["summary"], url=f["url"], why=f["why"],
                              how=f["how"], ideas=f["ideas"], platforms=f["platforms"]))
                total += 1
    events.publish({"type": "findings", "new": total})
    return total


async def scout_loop() -> None:
    while True:
        try:
            with session() as s:
                b = get_or_create_brand(s)
                every, last = b.scout_every_hours or 0, b.scout_last
                due = every > 0 and (last is None or (now() - last).total_seconds() >= every * 3600)
                if due:
                    b.scout_last = now()
            if due:
                await run_scout()
        except Exception:
            log.exception("scout loop")
        await asyncio.sleep(600)


# ============================================================ Aprendizaje continuo
LEARNER = """You are Shelton, chief of a content studio, reviewing what the human team decided recently, to improve your
team. From the decisions below, extract only clear, repeated or strongly signaled preferences and turn each into a
short, actionable lesson for the ONE agent it concerns:
- judge: when humans approve images it scored low or reject ones it scored high (calibrate criteria).
- art_director: visual preferences (style, light, framing, subjects) shown by approvals/rejections and edit requests.
- copywriter: how humans rewrite captions/hashtags (tone, length, emojis, CTA, hashtag count).
- video_producer / voice_director: preferences about motion or voice.
Do not invent; skip if there is not enough signal. Lessons in Spanish, with a concrete example when possible."""


async def run_learning(min_items: int = 3) -> list[dict]:
    """Revisa decisiones humanas no procesadas y guarda lecciones (revisando contradicciones)."""
    from app.agents import training
    from app.agents.registry import AGENTS
    from app.db import Feedback
    from app.llm import json_call, obj
    with session() as s:
        brand = get_or_create_brand(s)
        mode = brand.learning_mode or "auto"
        items = s.query(Feedback).filter(Feedback.processed.is_(False)).order_by(Feedback.created_at).limit(200).all()
        rows = [{"post": f.post_id, "action": f.action, "detail": f.detail, "at": f.created_at.isoformat()} for f in items]
        ids = [f.id for f in items]
    if mode == "off" or len(rows) < min_items:
        return []
    keys = [k for k in ("judge", "art_director", "copywriter", "video_producer", "voice_director", "planner") if k in AGENTS]
    data = await json_call(LEARNER, json.dumps(rows, ensure_ascii=False)[:60000], obj({"lessons": {"type": "array", "items": obj({
        "agent": {"type": "string", "enum": keys}, "title": {"type": "string"}, "content": {"type": "string"}})}}),
        effort="medium", agent="shelton", summary=f"aprender de {len(rows)} decisiones")
    saved = []
    for l in data["lessons"][:8]:
        e = await training.add_checked(l["agent"], l["title"], l["content"], author="Shelton (aprendizaje automático)",
                                       role="auto", source="auto", auto_apply=(mode == "auto"))
        ev = {"type": "learned", "agent": l["agent"], "agent_name": AGENTS[l["agent"]]["name"], "title": e["title"],
              "applied": e["status"] == "active", "status": e["status"], "reason": e["conflict_reason"], "id": e["id"]}
        events.publish(ev)
        saved.append(ev)
    with session() as s:
        for f in s.query(Feedback).filter(Feedback.id.in_(ids)):
            f.processed = True
    return saved


async def learning_loop() -> None:
    """Cada 30 min aprende de las decisiones humanas; una vez al día revisa al equipo completo."""
    from app.agents import training
    last_audit = None
    while True:
        await asyncio.sleep(1800)
        try:
            await run_learning()
            with session() as s:
                b = get_or_create_brand(s)
                mode = b.learning_mode
                brand_text = f"Marca: {b.name}. {b.description}. Tono: {b.tone}. Hashtags fijos: {' '.join(b.base_hashtags or [])}"
            today = now().date()
            if mode != "off" and last_audit != today:
                last_audit = today
                audit = await training.team_audit(brand_text)
                events.publish({"type": "audit", "issues": len(audit["issues"]), "summary": audit["summary"]})
        except Exception:
            log.exception("aprendizaje")
