"""Cadena de producción que ejecutan los subagentes de Shelton.

crear post:  director creativo -> motor de imagen -> juez de calidad (reintenta si falla) -> formato final -> redactor
editar post: director de edición -> Qwen-Image-Edit -> juez -> (redactor si hace falta)
crear modelo del banco: diseñador -> imagen de referencia -> juez -> guardar ficha
"""
import asyncio
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

    handler = {"create": _create_post, "edit": _edit_post, "subject": _create_subject}[kind]
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
    engine = get_engine()
    refs = [Path(s.references[0]).read_bytes() for s in subjects if s.references]

    feedback: list[str] = []
    tries: list[dict] = []   # todos los intentos se conservan
    for attempt in range(1, 2 + max(0, settings.QA_MAX_RETRIES)):
        _update_post(post_id, status="generating", attempts=attempt, error="")
        events.publish({"type": "progress", "id": post_id, "text": f"Director creativo (intento {attempt})"})
        ad = await sp.art_direct(post.idea, platform, brand, feedback, subjects)
        seed = random.randint(1, 2**31 - 1)

        step = _progress(post_id, f"Generando imagen (intento {attempt})")
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
                 width=platform.width, height=platform.height, caption=copy["caption"], hashtags=hashtags,
                 alt_text=copy.get("alt_text", ""), qa_score=best["score"], qa_passed=passed,
                 qa_notes=_qa_text(best, passed), alternatives=[t for t in tries if t is not best], status="ready")


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
        out = await get_engine().edit(src, ei["instruction"], random.randint(1, 2**31 - 1), step)
        raw = save_bytes(out, f"{post_id}_raw.png")
        final = fit_to_platform(raw, platform.width, platform.height, f"{post_id}.jpg")
        prompt = f"{parent.prompt}\n[EDIT] {ei['instruction']}"
        events.publish({"type": "progress", "id": post_id, "text": "Juez de calidad revisando la edición"})
        qa = await sp.judge(str(final), f"{parent.idea}. Edit requested: {request}", prompt, brand, subjects)
        qa_score = int(qa.get("score", 0))
        qa_notes = _qa_text(qa, qa_score >= settings.QA_MIN_SCORE)

    caption, hashtags, alt = parent.caption, parent.hashtags, parent.alt_text
    if not ei["image_change"] or payload.get("rewrite_caption"):
        copy = await sp.write_post(str(final), parent.idea, platform, brand, request)
        caption, alt = copy["caption"], copy.get("alt_text", alt)
        hashtags = _merge_hashtags(copy.get("hashtags", []), brand.base_hashtags if brand else [])
    _update_post(post_id, prompt=prompt, raw_path=str(raw), image_path=str(final), width=platform.width,
                 height=platform.height, caption=caption, hashtags=hashtags, alt_text=alt,
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

    width, height = generation_size("4:5" if kind == "person" else "1:1")
    feedback, tries = [], []
    for attempt in range(1, 2 + settings.QA_MAX_RETRIES):
        prompt = design["reference_prompt"] + (" Avoid: " + "; ".join(feedback) if feedback else "")
        events.publish({"type": "subject_progress", "id": subject_id, "text": f"Creando foto de referencia ({attempt})"})
        raw = await get_engine().generate(prompt, width, height, random.randint(1, 2**31 - 1))
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
             subject_ids=None, caption_notes: str = "") -> str:
    with session() as s:
        brand_id = brand_id or get_or_create_brand(s).id
        p = Post(idea=idea, platform=get_platform(platform).key, brand_id=brand_id, campaign_id=campaign_id,
                 conversation_id=conversation_id, subject_ids=subject_ids or [], status="queued")
        s.add(p)
        s.flush()
        pid, data = p.id, post_dict(p)
    events.publish({"type": "post", "post": data})
    enqueue("create", pid, {"caption_notes": caption_notes})
    return pid


def new_edit(parent_id: str, instruction: str, rewrite_caption: bool = False, conversation_id=None) -> str:
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
    enqueue("edit", pid, {"instruction": instruction, "rewrite_caption": rewrite_caption})
    return pid


def new_subject(name: str, kind: str, request: str, generate: bool = True) -> str:
    with session() as s:
        sub = Subject(name=name.strip(), kind=kind or "person", notes=request if not generate else "")
        s.add(sub)
        s.flush()
        sid, data = sub.id, subject_dict(sub)
    events.publish({"type": "subject", "subject": data})
    if generate:
        enqueue("subject", sid, {"request": request})
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


async def run_campaign(campaign_id: str, subject_ids: list[str] | None = None) -> None:
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
                new_post(it["idea"], it["platform"], brand_id=brand_id, campaign_id=campaign_id, subject_ids=sids)
        status, error = "running", ""
    except Exception as e:
        log.exception("campaña %s", campaign_id)
        status, error = "failed", str(e)
    with session() as s:
        c = s.get(Campaign, campaign_id)
        c.status, c.error = status, error
    events.publish({"type": "campaign", "id": campaign_id, "text": "En cola" if not error else f"Error: {error}"})


def new_campaign(brief: str, count: int, platforms: list[str] | None = None, subject_ids=None) -> str:
    with session() as s:
        brand = get_or_create_brand(s)
        plats = [get_platform(p).key for p in (platforms or brand.platforms or ["instagram_feed"])]
        c = Campaign(brief=brief, count=max(1, min(int(count), 200)), platforms=plats, brand_id=brand.id)
        s.add(c)
        s.flush()
        cid = c.id
    asyncio.create_task(run_campaign(cid, subject_ids))
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
