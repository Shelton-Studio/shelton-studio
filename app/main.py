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
    try:
        health = await get_engine().health()
    except Exception as e:
        health = {"ok": False, "error": str(e)}
    log.info("Motor de imagen: %s", json.dumps(health, ensure_ascii=False))
    from app.llm import provider
    if not settings.ANTHROPIC_API_KEY and not settings.OPENROUTER_API_KEY:
        log.warning("Falta ANTHROPIC_API_KEY u OPENROUTER_API_KEY en .env (o en Conexiones): Shelton no podrá pensar")
    else:
        log.info("Cerebro: %s", provider())
    yield
    autopilot.cancel()
    scout.cancel()
    learning.cancel()
    await pipeline.stop_workers()


app = FastAPI(title="Shelton", lifespan=lifespan)
app.include_router(panel_router)
app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")
app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")
(settings.DATA_DIR / "voices").mkdir(parents=True, exist_ok=True)
app.mount("/voices", StaticFiles(directory=settings.DATA_DIR / "voices"), name="voices")


def sse(ev: dict) -> str:
    return f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"


@app.get("/")
async def index():
    return FileResponse(ROOT / "web" / "index.html")


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


@app.post("/api/chat")
async def chat(body: ChatIn):
    async def gen():
        async for ev in shelton.chat(body.conversation_id, body.message, body.upload_ids, body.agent, body.engine):
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


@app.delete("/api/posts/{pid}")
async def delete_post(pid: str):
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
    for f in files[:20]:
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
    uvicorn.run("app.main:app", host=settings.APP_HOST, port=settings.APP_PORT)
