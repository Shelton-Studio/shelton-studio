"""Servidor de Shelton: API + interfaz web. Arranque: python -m app.main"""
import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field

from app import events, pipeline, publishers
from app.agents import shelton
from app.api import router as panel_router
from app.config import ROOT, settings
from app.db import Brand, Campaign, Conversation, Post, Subject, get_or_create_brand, init_db, session
from app.engines import get_engine
from app.media import fit_to_platform, save_bytes
from app.platforms import get_platform, platform_list
from app.serialize import brand_dict, campaign_dict, post_dict, subject_dict

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("shelton")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    await pipeline.start_workers()
    autopilot = asyncio.create_task(pipeline.autopilot_loop())
    scout = asyncio.create_task(pipeline.scout_loop())
    learning = asyncio.create_task(pipeline.learning_loop())
    network = asyncio.create_task(pipeline.network_loop())
    from app import consumo
    consumo.apply_savers_once()
    from app import privado
    privado.recover_after_restart()
    from app import traductor
    translator_setup = asyncio.create_task(traductor.prepare())     # baja el traductor la primera vez
    gpu_meter = asyncio.create_task(consumo.sample_loop())

    async def _engine_check():
        # en segundo plano: si la placa está ocupada (entrenando) o ComfyUI tarda, Shelton abre igual al instante
        try:
            health = await asyncio.wait_for(get_engine().health(), 120)
        except Exception as e:  # noqa: BLE001
            health = {"ok": False, "error": str(e) or "ComfyUI no respondió a tiempo"}
        log.info("Motor de imagen: %s", json.dumps(health, ensure_ascii=False))
    engine_check = asyncio.create_task(_engine_check())
    from app.llm import provider
    if not settings.ANTHROPIC_API_KEY and not settings.OPENROUTER_API_KEY:
        log.warning("Falta ANTHROPIC_API_KEY u OPENROUTER_API_KEY en .env (o en Conexiones): Shelton no podrá pensar")
    else:
        log.info("Cerebro: %s", provider())
    yield
    autopilot.cancel()
    scout.cancel()
    learning.cancel()
    network.cancel()
    gpu_meter.cancel()
    translator_setup.cancel()
    engine_check.cancel()
    await pipeline.stop_workers()


app = FastAPI(title="Shelton", lifespan=lifespan)
from app.auth_routes import guard as _auth_guard, router as auth_router  # noqa: E402
app.middleware("http")(_auth_guard)
app.include_router(auth_router)
app.include_router(panel_router)
from app.api_extra import router as extra_router  # noqa: E402
app.include_router(extra_router)
from app.consumo import router as consumo_router  # noqa: E402
app.include_router(consumo_router)
from app.calidad import router as calidad_router  # noqa: E402
from app.granja import router as granja_router  # noqa: E402
from app.lora_compare import router as compare_router  # noqa: E402
app.include_router(calidad_router)
app.include_router(granja_router)
app.include_router(compare_router)
from app.privado import router as privado_router  # noqa: E402
app.include_router(privado_router)
from app.catalogo import router as catalogo_router  # noqa: E402
app.include_router(catalogo_router)
app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")
app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")
(settings.DATA_DIR / "voices").mkdir(parents=True, exist_ok=True)
app.mount("/voices", StaticFiles(directory=settings.DATA_DIR / "voices"), name="voices")


def sse(ev: dict) -> str:
    return f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"


def _web_version() -> str:
    """Huella de la versión de la web: cambia cuando se actualiza cualquier archivo de web/."""
    web = ROOT / "web"
    return str(int(max((f.stat().st_mtime for f in web.iterdir() if f.is_file()), default=0)))


@app.get("/")
async def index():
    """La página con los .js/.css marcados con la versión: después de actualizar, el navegador baja los nuevos
    (antes podía quedarse con los viejos guardados y no mostrar lo nuevo)."""
    import re as _re
    from fastapi.responses import HTMLResponse
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    v = _web_version()
    html = _re.sub(r'(/static/[\w.-]+\.(?:js|css))"', rf'\1?v={v}"', html)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.middleware("http")
async def _fresh_static(request, call_next):
    """Los archivos de la web se revalidan siempre (si no cambiaron, el navegador usa su copia sin bajarla)."""
    resp = await call_next(request)
    if request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# ------------------------------------------------------------------ Estado
@app.get("/api/health")
async def health():
    from app.connections import list_enabled
    from app.llm import api_key, openrouter_config, provider
    engines = []
    try:
        engines.append({"name": "Motor por defecto", **(await get_engine().health())})
    except Exception as e:
        engines.append({"name": "Motor por defecto", "ok": False, "error": str(e)})
    for c in list_enabled("engine"):
        if c.type == "comfyui":
            try:
                engines.append({"name": c.name, **(await get_engine(choice=f"conn:{c.id}").health())})
            except Exception as e:
                engines.append({"name": c.name, "ok": False, "error": str(e)})
    from app import video as vid
    prov = provider()
    brain_ok = bool(api_key()) or bool(openrouter_config()[0])
    model = settings.CLAUDE_MODEL if prov == "anthropic" else openrouter_config()[1]
    return {"claude": brain_ok, "provider": prov, "model": model, "engine": engines[0], "engines": engines,
            "ffmpeg": bool(vid.FFMPEG), "queue": pipeline.queue_stats(), "local_first": settings.IMAGE_ENGINE != "mock"}


@app.get("/api/platforms")
async def platforms():
    return platform_list()


@app.get("/api/queue")
async def queue():
    return pipeline.queue_stats()


@app.get("/api/events")
async def event_stream():
    q = events.subscribe()

    async def gen():
        try:
            yield sse({"type": "hello"})
            while True:
                try:
                    yield sse(await asyncio.wait_for(q.get(), 20))
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            events.unsubscribe(q)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


# ------------------------------------------------------------------ Chat con Shelton
class ChatIn(BaseModel):
    message: str = Field(min_length=1)
    conversation_id: str | None = None
    upload_ids: list[str] = []
    agent: str = "shelton"
    engine: str | None = None
    attachments: list[dict] = []     # [{"kind": "audio"|"reference", "id": ...}]


@app.post("/api/chat")
async def chat(body: ChatIn):
    async def gen():
        async for ev in shelton.chat(body.conversation_id, body.message, body.upload_ids, body.agent, body.engine,
                                     body.attachments):
            yield sse(ev)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/api/conversations")
async def conversations(agent: str | None = None, q: str | None = None):
    with session() as s:
        query = s.query(Conversation).order_by(Conversation.updated_at.desc())
        if agent:
            query = query.filter(Conversation.agent == agent)
        if q:
            query = query.filter(Conversation.title.ilike(f"%{q}%"))
        return [{"id": c.id, "title": c.title, "agent": c.agent or "shelton", "updated_at": c.updated_at.isoformat() + "Z"}
                for c in query.limit(100)]


@app.get("/api/conversations/{cid}")
async def conversation(cid: str):
    with session() as s:
        c = s.get(Conversation, cid) or _404("Conversación")
        return {"id": c.id, "title": c.title, "agent": c.agent or "shelton", "engine": c.engine or "",
                "messages": c.display}


@app.delete("/api/conversations/{cid}")
async def delete_conversation(cid: str):
    with session() as s:
        if c := s.get(Conversation, cid):
            s.delete(c)
    return {"ok": True}


# ------------------------------------------------------------------ Posts
class PostIn(BaseModel):
    idea: str
    platform: str = "instagram_feed"
    subjects: list[str] = []
    count: int = Field(1, ge=1, le=12)
    format: str = "image"
    engine: str | None = None


@app.get("/api/posts")
async def list_posts(status: str | None = None, campaign_id: str | None = None,
                     limit: int = Query(60, le=500), offset: int = 0):
    with session() as s:
        q = s.query(Post).order_by(Post.created_at.desc())
        if status:
            q = q.filter(Post.status.in_(status.split(",")))
        if campaign_id:
            q = q.filter(Post.campaign_id == campaign_id)
        total = q.count()
        return {"total": total, "items": [post_dict(p) for p in q.offset(offset).limit(limit)]}


@app.get("/api/posts/{pid}")
async def get_post(pid: str):
    with session() as s:
        return post_dict(s.get(Post, pid) or _404("Post"))


@app.get("/api/posts/{pid}/versions")
async def versions(pid: str):
    """Original + todas sus ediciones, en orden."""
    with session() as s:
        p = s.get(Post, pid) or _404("Post")
        while p.parent_id and (parent := s.get(Post, p.parent_id)):
            p = parent
        out, frontier = [p], [p.id]
        while frontier:
            kids = s.query(Post).filter(Post.parent_id.in_(frontier)).order_by(Post.created_at).all()
            out += kids
            frontier = [k.id for k in kids]
        return [post_dict(x) for x in out]


@app.post("/api/posts")
async def create_posts(body: PostIn):
    """Generación directa sin chat (botón rápido de la web y CLI)."""
    sids, missing = pipeline.find_subjects(body.subjects)
    ids = [pipeline.new_post(body.idea, body.platform, subject_ids=sids, media=body.format, engine=body.engine)
           for _ in range(body.count)]
    return {"ids": ids, "subjects_not_found": missing}


class EditIn(BaseModel):
    instruction: str
    rewrite_caption: bool = False


@app.post("/api/posts/{pid}/edit")
async def edit_post(pid: str, body: EditIn):
    try:
        return {"id": pipeline.new_edit(pid, body.instruction, body.rewrite_caption)}
    except ValueError as e:
        raise HTTPException(400, str(e))


class VideoIn(BaseModel):
    motion: str = ""
    engine: str | None = None


@app.post("/api/posts/{pid}/video")
async def make_video(pid: str, body: VideoIn):
    try:
        return {"id": pipeline.new_video(pid, body.motion, engine=body.engine)}
    except ValueError as e:
        raise HTTPException(400, str(e))


class ReelIn(BaseModel):
    post_ids: list[str]
    idea: str = ""
    platform: str = "tiktok"


@app.post("/api/reels")
async def make_reel(body: ReelIn):
    if len(body.post_ids) < 1:
        raise HTTPException(400, "Elige al menos un post")
    try:
        return {"id": pipeline.new_reel(body.post_ids, body.idea, platform=body.platform)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/posts/{pid}/regenerate")
async def regenerate(pid: str):
    with session() as s:
        p = s.get(Post, pid) or _404("Post")
        idea, platform, subjects, camp, media = p.idea, p.platform, p.subject_ids, p.campaign_id, p.media_type
    return {"id": pipeline.new_post(idea, platform, subject_ids=subjects, campaign_id=camp,
                                    media="video" if media == "video" else "image")}


class AltIn(BaseModel):
    index: int


class JudgeRatingIn(BaseModel):
    score: float | None = None      # tu nota 0-100 para esta imagen
    agree: bool | None = None       # ¿el juez acertó?
    note: str = ""


@app.post("/api/posts/{pid}/juez")
async def rate_judge(pid: str, body: JudgeRatingIn):
    """⚖️ Calificar al juez: tu nota y si acertó. Queda como aprendizaje para el juez (Shelton revisa estas notas)."""
    from app.db import Feedback
    from app.serialize import post_dict
    with session() as s:
        p = s.get(Post, pid)
        if not p:
            raise HTTPException(404, "No existe")
        if body.score is not None:
            p.human_score = max(0.0, min(100.0, float(body.score)))
        if body.agree is not None:
            p.judge_review = "agree" if body.agree else "disagree"
        s.add(Feedback(post_id=pid, action="judge_rating",
                       detail={"judge_score": p.qa_score, "human_score": p.human_score, "agree": body.agree,
                               "note": body.note[:500], "judge_notes": (p.qa_notes or "")[:500], "idea": p.idea}))
        s.flush()
        data = post_dict(p)
    events.publish({"type": "post", "post": data})
    return data


@app.post("/api/posts/{pid}/use-alternative")
async def use_alternative(pid: str, body: AltIn):
    """Tú decides: usar un intento que el juez puntuó más bajo."""
    try:
        return pipeline.use_alternative(pid, body.index)
    except ValueError as e:
        raise HTTPException(400, str(e))


class PostPatch(BaseModel):
    caption: str | None = None
    hashtags: list[str] | None = None
    status: str | None = None


@app.patch("/api/posts/{pid}")
async def patch_post(pid: str, body: PostPatch):
    from app.db import Feedback
    with session() as s:
        p = s.get(Post, pid) or _404("Post")
        if body.status and body.status != p.status and body.status in ("approved", "rejected"):
            s.add(Feedback(post_id=pid, action=body.status, detail={"idea": p.idea, "judge_score": p.qa_score,
                                                                     "judge_passed": p.qa_passed, "platform": p.platform,
                                                                     "media": p.media_type, "prompt": p.prompt[:500]}))
        if body.caption is not None and body.caption.strip() != (p.caption or "").strip():
            s.add(Feedback(post_id=pid, action="human_rewrote_caption", detail={"platform": p.platform,
                                                                                "before": p.caption, "after": body.caption}))
        if body.hashtags is not None and body.hashtags != (p.hashtags or []):
            s.add(Feedback(post_id=pid, action="human_changed_hashtags", detail={"before": p.hashtags, "after": body.hashtags}))
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(p, k, v)
        s.flush()
        data = post_dict(p)
    events.publish({"type": "post", "post": data})
    return data


@app.post("/api/posts/{pid}/cancel")
async def cancel_post(pid: str):
    """⏹ Cancelar una foto en cola o haciéndose (libera el lugar y la GPU)."""
    return {"ok": True, "had_job": pipeline.cancel_post(pid)}


@app.delete("/api/posts/{pid}")
async def delete_post(pid: str):
    with session() as s:
        if (p := s.get(Post, pid)) and p.status in ("queued", "generating"):
            pipeline.cancel_post(pid)                  # si se estaba haciendo, primero se corta
    with session() as s:
        if p := s.get(Post, pid):
            s.delete(p)
    events.publish({"type": "post_deleted", "id": pid})
    return {"ok": True}


class PublishIn(BaseModel):
    target: str = "telegram"   # id de la conexión de publicación (o "telegram" de .env)


@app.post("/api/posts/{pid}/publish")
async def publish(pid: str, body: PublishIn):
    try:
        return await publishers.publish(pid, body.target)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/upload")
async def upload(file: UploadFile = File(...), platform: str = Form("original")):
    """Sube una foto propia: queda como post editable y puede ir al banco de modelos."""
    data = await file.read()
    pid = uuid.uuid4().hex[:12]
    raw = save_bytes(_normalize(data), f"{pid}_upload.png")
    plat = get_platform(platform)
    final = fit_to_platform(raw, plat.width, plat.height, f"{pid}.jpg")
    with session() as s:
        brand = get_or_create_brand(s)
        from app.media import size_of
        w, h = size_of(final)
        p = Post(id=pid, idea="Imagen subida por el usuario", platform=plat.key, raw_path=raw, image_path=final,
                 width=w, height=h, status="ready", brand_id=brand.id, prompt="(subida)")
        s.add(p)
        s.flush()
        out = post_dict(p)
    events.publish({"type": "post", "post": out})
    return out


@app.get("/api/export")
async def export(ids: str | None = None, status: str = "approved"):
    with session() as s:
        if ids:
            post_ids = ids.split(",")
        else:
            post_ids = [p.id for p in s.query(Post).filter(Post.status.in_(status.split(",")))
                        .order_by(Post.created_at)]
    if not post_ids:
        raise HTTPException(404, "No hay posts para exportar")
    return Response(publishers.export_zip(post_ids), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="shelton_posts.zip"'})


# ------------------------------------------------------------------ Campañas
class CampaignIn(BaseModel):
    brief: str
    count: int = Field(10, ge=1, le=200)
    platforms: list[str] = []
    subjects: list[str] = []


@app.post("/api/campaigns")
async def create_campaign(body: CampaignIn):
    sids, missing = pipeline.find_subjects(body.subjects)
    return {"id": pipeline.new_campaign(body.brief, body.count, body.platforms or None, sids),
            "subjects_not_found": missing}


@app.get("/api/campaigns")
async def campaigns():
    with session() as s:
        out = []
        for c in s.query(Campaign).order_by(Campaign.created_at.desc()).limit(30):
            counts: dict = {}
            for (st,) in s.query(Post.status).filter(Post.campaign_id == c.id):
                counts[st] = counts.get(st, 0) + 1
            out.append(campaign_dict(c, counts))
        return out


# ------------------------------------------------------------------ Marca
@app.get("/api/brand")
async def get_brand():
    with session() as s:
        return brand_dict(get_or_create_brand(s))


@app.put("/api/brand")
async def put_brand(body: dict):
    with session() as s:
        b = get_or_create_brand(s)
        for k, v in body.items():
            if k != "id" and hasattr(Brand, k):
                setattr(b, k, v)
        s.flush()
        return brand_dict(b)


# ------------------------------------------------------------------ Banco de modelos
class SubjectIn(BaseModel):
    name: str
    kind: str = "person"
    request: str = ""
    generate: bool = True


@app.get("/api/subjects")
async def subjects():
    with session() as s:
        return [subject_dict(x) for x in s.query(Subject).order_by(Subject.name)]


@app.post("/api/subjects")
async def create_subject(body: SubjectIn):
    return {"id": pipeline.new_subject(body.name, body.kind, body.request, body.generate)}


@app.post("/api/subjects/from-photos")
async def subject_from_photos(files: list[UploadFile] = File(...), name: str = Form(...), kind: str = Form("auto"),
                              notes: str = Form("")):
    """Banco de fotos del mismo carro, persona, flor o paisaje -> modelo listo para generar contenido consistente."""
    if not name.strip():
        raise HTTPException(400, "Ponle un nombre al modelo")
    paths = []
    for f in files[:40]:   # hasta 40: alcanzan para entrenar su LoRA (15-30 ideal)
        paths.append(save_bytes(_normalize(await f.read()), f"subject_up_{uuid.uuid4().hex[:8]}.png"))
    try:
        return await pipeline.create_subject_from_photos(name, paths, kind or "auto", notes)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/subjects/{sid}/photos")
async def subject_photo(sid: str, file: UploadFile = File(...), describe: bool = Form(False)):
    with session() as s:
        s.get(Subject, sid) or _404("Modelo")
    path = save_bytes(_normalize(await file.read()), f"subject_{sid}_{uuid.uuid4().hex[:6]}.png")
    return await pipeline.add_subject_reference(sid, path, describe)


class SubjectPatch(BaseModel):
    name: str | None = None
    kind: str | None = None
    description: str | None = None
    notes: str | None = None


@app.patch("/api/subjects/{sid}")
async def patch_subject(sid: str, body: SubjectPatch):
    with session() as s:
        x = s.get(Subject, sid) or _404("Modelo")
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(x, k, v)
        s.flush()
        return subject_dict(x)


@app.post("/api/subjects/{sid}/use-alternative")
async def subject_alternative(sid: str, body: AltIn):
    try:
        return pipeline.use_subject_alternative(sid, body.index)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/subjects/{sid}")
async def delete_subject(sid: str):
    with session() as s:
        if x := s.get(Subject, sid):
            s.delete(x)
    return {"ok": True}


# ------------------------------------------------------------------ util
def _404(what: str):
    raise HTTPException(404, f"{what} no encontrado")


def _normalize(data: bytes) -> bytes:
    import io
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        raise HTTPException(400, "El archivo no es una imagen válida")
    img = img.convert("RGB")
    img.thumbnail((2048, 2048))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


if __name__ == "__main__":
    import uvicorn
    # Al reiniciar (actualizar), no esperar a que se cierren los navegadores conectados al canal en vivo
    uvicorn.run("app.main:app", host=settings.APP_HOST, port=settings.APP_PORT, timeout_graceful_shutdown=5)
