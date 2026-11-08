"""API del panel: métricas, agentes, tareas, aprobaciones, conexiones, flujos, motores, scout y marca blanca."""
import json
import re
import uuid
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func

from app import events, pipeline, publishers
from app.agents import registry
from app.comfyui.client import WORKFLOW_DIR, placeholders, user_workflow_dir
from app.config import settings
from app.connections import ENGINE_ROLES, TYPES, merge_config, public
from app.db import AgentRun, Approval, Connection, Finding, Job, Post, get_or_create_brand, now, session
from app.engines import PRESET_INFO, VIDEO_INFO, engine_choices, reset_cache
from app.media import save_bytes, url_for

router = APIRouter(prefix="/api")

# Precio por millón de tokens (entrada, salida) para estimar costos.
PRICES = {"claude-opus-5-5": (4, 20), "claude-sonnet-5-5": (2, 10), "claude-haiku-5-5": (0.1, 0.5),
          "claude-fable-5-1": (10, 50), "claude-opus-5": (5, 25), "claude-opus-4-8": (5, 25)}


def _cost(model: str, tin: int, tout: int) -> float:
    pin, pout = PRICES.get(model, (4, 20))
    return (tin * pin + tout * pout) / 1_000_000


# ------------------------------------------------------------------ Métricas
@router.get("/metrics")
async def metrics(days: int = 7):
    days = max(1, min(days, 365))
    end = now()
    start = end.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    prev = start - timedelta(days=days)
    with session() as s:
        def run_stats(a, b):
            q = s.query(AgentRun).filter(AgentRun.created_at >= a, AgentRun.created_at < b)
            ok = q.filter(AgentRun.ok.is_(True)).count()
            failed = q.filter(AgentRun.ok.is_(False)).count()
            tokens = cost = 0
            for model, tin, tout in q.with_entities(AgentRun.model, func.sum(AgentRun.input_tokens),
                                                    func.sum(AgentRun.output_tokens)).group_by(AgentRun.model):
                tokens += (tin or 0) + (tout or 0)
                cost += _cost(model, tin or 0, tout or 0)
            return ok, failed, tokens, cost

        ok, failed, tokens, cost = run_stats(start, end)
        pok, pfailed, ptokens, pcost = run_stats(prev, start)

        posts_q = s.query(Post).filter(Post.created_at >= start)
        made = posts_q.filter(Post.status.in_(["ready", "approved", "published"])).count()
        pmade = s.query(Post).filter(Post.created_at >= prev, Post.created_at < start,
                                     Post.status.in_(["ready", "approved", "published"])).count()
        by_status = dict(posts_q.with_entities(Post.status, func.count()).group_by(Post.status).all())
        by_media = dict(posts_q.with_entities(Post.media_type, func.count()).group_by(Post.media_type).all())
        by_platform = dict(posts_q.with_entities(Post.platform, func.count()).group_by(Post.platform).all())
        avg_qa = posts_q.filter(Post.qa_score > 0).with_entities(func.avg(Post.qa_score)).scalar() or 0
        qa_pass = posts_q.filter(Post.qa_score > 0, Post.qa_passed.is_(True)).count()
        qa_total = posts_q.filter(Post.qa_score > 0).count()
        approved = posts_q.filter(Post.status.in_(["approved", "published"])).count()
        reviewed = posts_q.filter(Post.status.in_(["approved", "published", "rejected"])).count()

        # Serie diaria
        series = []
        today = end.replace(hour=0, minute=0, second=0, microsecond=0)
        for i in range(days):
            a = today - timedelta(days=days - 1 - i)
            b = a + timedelta(days=1)
            rq = s.query(AgentRun).filter(AgentRun.created_at >= a, AgentRun.created_at < b)
            series.append({
                "day": a.strftime("%Y-%m-%d"),
                "runs": rq.filter(AgentRun.ok.is_(True)).count(),
                "failed": rq.filter(AgentRun.ok.is_(False)).count(),
                "tokens": int(rq.with_entities(func.sum(AgentRun.input_tokens + AgentRun.output_tokens)).scalar() or 0),
                "posts": s.query(Post).filter(Post.created_at >= a, Post.created_at < b,
                                              Post.status.in_(["ready", "approved", "published"])).count(),
            })

        # Por agente
        agents = []
        for key in registry.AGENTS:
            q = s.query(AgentRun).filter(AgentRun.agent == key, AgentRun.created_at >= start)
            tin = q.with_entities(func.sum(AgentRun.input_tokens)).scalar() or 0
            tout = q.with_entities(func.sum(AgentRun.output_tokens)).scalar() or 0
            last = s.query(func.max(AgentRun.created_at)).filter(AgentRun.agent == key).scalar()
            agents.append({"key": key, "runs": q.count(), "failed": q.filter(AgentRun.ok.is_(False)).count(),
                           "tokens": tin + tout, "cost": round(_cost(registry.model(key), tin, tout), 4),
                           "avg_ms": int(q.with_entities(func.avg(AgentRun.duration_ms)).scalar() or 0),
                           "last": last.isoformat() + "Z" if last else None})

        # Tiempo medio de producción por post
        done_jobs = s.query(Job).filter(Job.status == "done", Job.created_at >= start, Job.kind == "create").all()
        avg_job = (sum((j.updated_at - j.created_at).total_seconds() for j in done_jobs) / len(done_jobs)) if done_jobs else 0

        pending_approvals = s.query(Approval).filter(Approval.status == "pending").count()
        to_review = s.query(Post).filter(Post.status == "ready").count()
        new_findings = s.query(Finding).filter(Finding.status == "new").count()

    return {
        "days": days,
        "kpis": {"runs": ok, "runs_prev": pok, "failed": failed, "failed_prev": pfailed, "tokens": tokens,
                 "tokens_prev": ptokens, "cost": round(cost, 2), "cost_prev": round(pcost, 2), "posts": made,
                 "posts_prev": pmade, "pending_approvals": pending_approvals, "to_review": to_review,
                 "avg_qa": round(avg_qa, 1), "qa_pass_rate": round(100 * qa_pass / qa_total) if qa_total else 0,
                 "approval_rate": round(100 * approved / reviewed) if reviewed else 0,
                 "avg_seconds_per_post": round(avg_job), "new_findings": new_findings,
                 "per_day": round(made / days, 1)},
        "series": series, "agents": agents, "by_status": by_status, "by_media": by_media, "by_platform": by_platform,
        "queue": pipeline.queue_stats(),
    }


# ------------------------------------------------------------------ Agentes
@router.get("/agents")
async def agents():
    with session() as s:
        out = []
        for key in registry.AGENTS:
            total = s.query(AgentRun).filter(AgentRun.agent == key).count()
            last = s.query(func.max(AgentRun.created_at)).filter(AgentRun.agent == key).scalar()
            out.append({**registry.info(key), "runs": total, "last": last.isoformat() + "Z" if last else None})
    return out


class AgentIn(BaseModel):
    prompt: str | None = None
    model: str | None = None
    effort: str | None = None
    enabled: bool | None = None


@router.put("/agents/{key}")
async def save_agent(key: str, body: AgentIn):
    try:
        registry.save(key, **body.model_dump())
    except ValueError as e:
        raise HTTPException(404, str(e))
    return registry.info(key)


@router.post("/agents/{key}/reset")
async def reset_agent(key: str):
    registry.save(key, prompt="")
    return registry.info(key)


@router.get("/runs")
async def runs(agent: str | None = None, limit: int = 100, failed: bool = False):
    with session() as s:
        q = s.query(AgentRun).order_by(AgentRun.created_at.desc())
        if agent:
            q = q.filter(AgentRun.agent == agent)
        if failed:
            q = q.filter(AgentRun.ok.is_(False))
        from app.working import ENGINE_NAMES
        return [{"id": r.id, "agent": r.agent,
                 "agent_name": registry.AGENTS.get(r.agent, {}).get("name") or ENGINE_NAMES.get(r.agent, r.agent),
                 "ok": r.ok, "tokens": r.input_tokens + r.output_tokens, "cost": round(_cost(r.model, r.input_tokens, r.output_tokens), 5),
                 "ms": r.duration_ms, "model": r.model, "summary": r.summary, "error": r.error,
                 "created_at": r.created_at.isoformat() + "Z"} for r in q.limit(min(limit, 500))]


# ------------------------------------------------------------------ Tareas (tablero)
KIND_AGENT = {"create": "art_director", "edit": "edit_director", "subject": "subject_designer",
              "video": "video_producer", "reel": "video_producer", "audio": "voice_director"}
KIND_LABEL = {"create": "Crear post", "edit": "Editar", "subject": "Banco de modelos", "video": "Video", "reel": "Reel",
              "audio": "Voz y sonido"}


@router.get("/tasks")
async def tasks(limit: int = 60):
    cols = {"queued": [], "running": [], "waiting": [], "done": [], "failed": []}
    with session() as s:
        for j in s.query(Job).order_by(Job.created_at.desc()).limit(600):
            p = s.get(Post, j.post_id) if j.kind != "subject" else None
            title = (p.idea if p else (j.payload or {}).get("request") or "Modelo del banco")[:140]
            card = {"id": j.id, "kind": j.kind, "label": KIND_LABEL.get(j.kind, j.kind),
                    "agent": KIND_AGENT.get(j.kind, "shelton"),
                    "agent_name": registry.AGENTS[KIND_AGENT.get(j.kind, "shelton")]["name"],
                    "title": title, "post_id": p.id if p else None, "image_url": url_for(p.image_path) if p else "",
                    "error": j.error, "at": (j.updated_at or j.created_at).isoformat() + "Z"}
            if j.status == "pending":
                col = "queued"
            elif j.status == "running":
                col = "running"
            elif j.status == "failed":
                col = "failed"
            elif p and p.status == "ready":
                col = "waiting"
            else:
                col = "done"
            if len(cols[col]) < limit:
                cols[col].append(card)
        for a in s.query(Approval).filter(Approval.status == "pending").order_by(Approval.created_at.desc()):
            cols["waiting"].insert(0, {"id": a.id, "kind": "approval", "label": "Aprobación", "agent": a.agent,
                                       "agent_name": registry.AGENTS.get(a.agent, {}).get("name", a.agent),
                                       "title": a.title, "post_id": a.payload.get("post_id"),
                                       "image_url": a.payload.get("image_url", ""), "at": a.created_at.isoformat() + "Z"})
    counts = {k: len(v) for k, v in cols.items()}
    return {"columns": cols, "counts": counts}


@router.post("/tasks/{job_id}/retry")
async def retry_task(job_id: str):
    with session() as s:
        j = s.get(Job, job_id)
        if not j or j.status != "failed":
            raise HTTPException(400, "Solo se pueden reintentar tareas fallidas")
        kind, target, payload = j.kind, j.post_id, dict(j.payload or {})
        if kind != "subject" and (p := s.get(Post, target)):
            p.status, p.error = "queued", ""
    return {"id": pipeline.enqueue(kind, target, payload)}


# ------------------------------------------------------------------ Aprobaciones
@router.get("/approvals")
async def approvals(status: str = "pending"):
    with session() as s:
        return [publishers.approval_dict(a) for a in
                s.query(Approval).filter(Approval.status.in_(status.split(","))).order_by(Approval.created_at.desc()).limit(200)]


class DecisionIn(BaseModel):
    approve: bool


@router.post("/approvals/{aid}")
async def decide(aid: str, body: DecisionIn):
    try:
        return await publishers.decide(aid, body.approve)
    except ValueError as e:
        raise HTTPException(400, str(e))


class PublishRequest(BaseModel):
    target: str


@router.get("/targets")
async def targets():
    return publishers.targets()


# ------------------------------------------------------------------ Conexiones
@router.get("/connection-types")
async def connection_types():
    return {"types": TYPES, "engine_roles": ENGINE_ROLES}


@router.get("/connections")
async def connections():
    with session() as s:
        return [public(c) for c in s.query(Connection).order_by(Connection.created_at)]


class ConnectionIn(BaseModel):
    type: str
    name: str = ""
    config: dict = {}
    roles: list[str] = []
    enabled: bool = True


def _after_change():
    reset_cache()
    events.invalidate_hooks()


@router.post("/connections")
async def create_connection(body: ConnectionIn):
    if body.type not in TYPES:
        raise HTTPException(400, "Tipo de conexión desconocido")
    with session() as s:
        c = Connection(type=body.type, name=body.name or TYPES[body.type]["name"], config=body.config,
                       roles=[r for r in body.roles if r in ENGINE_ROLES], enabled=body.enabled)
        s.add(c)
        s.flush()
        out = public(c)
    _after_change()
    return out


@router.put("/connections/{cid}")
async def update_connection(cid: str, body: ConnectionIn):
    with session() as s:
        c = s.get(Connection, cid) or _404()
        c.name, c.enabled = body.name or c.name, body.enabled
        c.config = merge_config(c.config, body.config)
        c.roles = [r for r in body.roles if r in ENGINE_ROLES]
        s.flush()
        out = public(c)
    _after_change()
    return out


@router.delete("/connections/{cid}")
async def delete_connection(cid: str):
    with session() as s:
        if c := s.get(Connection, cid):
            s.delete(c)
    _after_change()
    return {"ok": True}


@router.post("/connections/{cid}/test")
async def test_connection(cid: str):
    from app.conn_tests import test_connection as run_test
    with session() as s:
        c = s.get(Connection, cid) or _404()
        type_, cfg = c.type, dict(c.config or {})
    result = await run_test(type_, cfg)
    result["at"] = now().isoformat() + "Z"
    with session() as s:
        s.get(Connection, cid).last_test = result
    return result


# ------------------------------------------------------------------ Motores
@router.get("/engines")
async def engines():
    return {"image": engine_choices(), "video": [{"id": k, "label": v[0], "description": v[1]} for k, v in VIDEO_INFO.items()],
            "default_image": settings.GEN_PRESET, "default_video": settings.VIDEO_PRESET,
            "presets": {k: {"label": v[0], "description": v[1]} for k, v in PRESET_INFO.items()}}


# ------------------------------------------------------------------ Flujos (workflows de ComfyUI)
def _wf_info(path: Path, builtin: bool) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        nodes = {k: v for k, v in data.items() if not k.startswith("_")}
        return {"name": path.name, "builtin": builtin, "doc": data.get("_doc", ""), "nodes": len(nodes),
                "classes": sorted({n.get("class_type", "?") for n in nodes.values()}),
                "placeholders": sorted(placeholders(nodes))}
    except Exception as e:
        return {"name": path.name, "builtin": builtin, "error": str(e)}


@router.get("/workflows")
async def workflows():
    user = sorted(user_workflow_dir().glob("*.json"))
    builtin = [p for p in sorted(WORKFLOW_DIR.glob("*.json")) if not (user_workflow_dir() / p.name).exists()]
    return [_wf_info(p, False) for p in user] + [_wf_info(p, True) for p in builtin]


@router.post("/workflows")
async def upload_workflow(file: UploadFile = File(...)):
    name = re.sub(r"[^\w.\-]", "_", Path(file.filename or "workflow.json").name)
    if not name.endswith(".json"):
        name += ".json"
    try:
        data = json.loads((await file.read()).decode("utf-8"))
    except Exception:
        raise HTTPException(400, "No es un JSON válido")
    nodes = {k: v for k, v in data.items() if not k.startswith("_")} if isinstance(data, dict) else {}
    if not nodes or not all(isinstance(v, dict) and "class_type" in v for v in nodes.values()):
        raise HTTPException(400, "Exporta el workflow en ComfyUI con 'Save (API Format)': este archivo no tiene ese formato")
    (user_workflow_dir() / name).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    reset_cache()
    return _wf_info(user_workflow_dir() / name, False)


@router.get("/workflows/{name}")
async def get_workflow(name: str):
    p = user_workflow_dir() / Path(name).name
    if not p.exists():
        p = WORKFLOW_DIR / Path(name).name
    if not p.exists():
        _404()
    return json.loads(p.read_text(encoding="utf-8"))


@router.delete("/workflows/{name}")
async def delete_workflow(name: str):
    p = user_workflow_dir() / Path(name).name
    if p.exists():
        p.unlink()
    reset_cache()
    return {"ok": True}


# ------------------------------------------------------------------ Scout y hallazgos
def _finding(f: Finding) -> dict:
    return {"id": f.id, "kind": f.kind, "title": f.title, "summary": f.summary, "url": f.url, "why": f.why,
            "how": f.how, "ideas": f.ideas or [], "platforms": f.platforms or [], "status": f.status,
            "created_at": f.created_at.isoformat() + "Z"}


@router.get("/findings")
async def findings(status: str = "new", kind: str | None = None):
    with session() as s:
        q = s.query(Finding).filter(Finding.status.in_(status.split(","))).order_by(Finding.created_at.desc())
        if kind:
            q = q.filter(Finding.kind == kind)
        return [_finding(f) for f in q.limit(200)]


@router.post("/scout/run")
async def scout_run():
    import asyncio
    asyncio.create_task(pipeline.run_scout())
    return {"ok": True, "message": "El scout está investigando; los hallazgos aparecerán en unos minutos."}


class UseFindingIn(BaseModel):
    ideas: list[str] = []
    platform: str = "instagram_feed"
    subjects: list[str] = []
    format: str = "image"


@router.post("/findings/{fid}/use")
async def use_finding(fid: str, body: UseFindingIn):
    """Una persona decide replicar una tendencia: se crean los posts con las ideas elegidas."""
    with session() as s:
        f = s.get(Finding, fid) or _404()
        ideas = body.ideas or f.ideas
        f.status = "used"
    sids, missing = pipeline.find_subjects(body.subjects)
    ids = [pipeline.new_post(i, body.platform, subject_ids=sids, media=body.format) for i in ideas[:12]]
    return {"ids": ids, "subjects_not_found": missing}


@router.post("/findings/{fid}/dismiss")
async def dismiss_finding(fid: str):
    with session() as s:
        if f := s.get(Finding, fid):
            f.status = "dismissed"
    return {"ok": True}


# ------------------------------------------------------------------ Marca blanca
WL_DEFAULTS = {"product_name": "Shelton Studio", "chief_name": "Shelton", "tagline": "Estudio de contenido multiagente",
               "accent": "#8b5cf6", "accent2": "#22d3ee", "logo_url": "", "support_url": ""}


def _wl_path() -> Path:
    return settings.DATA_DIR / "whitelabel.json"


def whitelabel_data() -> dict:
    try:
        return {**WL_DEFAULTS, **json.loads(_wl_path().read_text(encoding="utf-8"))}
    except Exception:
        return dict(WL_DEFAULTS)


@router.get("/whitelabel")
async def get_whitelabel():
    return whitelabel_data()


@router.put("/whitelabel")
async def put_whitelabel(body: dict):
    data = {**whitelabel_data(), **{k: v for k, v in body.items() if k in WL_DEFAULTS}}
    _wl_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


@router.post("/whitelabel/logo")
async def upload_logo(file: UploadFile = File(...)):
    data = await file.read()
    ext = (Path(file.filename or "").suffix or ".png").lower()
    if ext not in (".png", ".jpg", ".jpeg", ".svg", ".webp"):
        raise HTTPException(400, "Formato de logo no soportado")
    path = save_bytes(data, f"logo_{uuid.uuid4().hex[:6]}{ext}")
    wl = {**whitelabel_data(), "logo_url": url_for(path)}
    _wl_path().write_text(json.dumps(wl, ensure_ascii=False, indent=2), encoding="utf-8")
    return wl


# ------------------------------------------------------------------ Búsqueda global (⌘K)
@router.get("/search")
async def search(q: str):
    q = q.strip()
    if len(q) < 2:
        return []
    like = f"%{q}%"
    from app.db import Conversation, Subject
    out = []
    with session() as s:
        for p in s.query(Post).filter(Post.idea.ilike(like) | Post.caption.ilike(like)).order_by(Post.created_at.desc()).limit(8):
            out.append({"type": "post", "id": p.id, "title": p.idea[:90], "image_url": url_for(p.image_path)})
        for x in s.query(Subject).filter(Subject.name.ilike(like)).limit(5):
            out.append({"type": "subject", "id": x.id, "title": x.name, "image_url": url_for((x.references or [""])[0])})
        for c in s.query(Conversation).filter(Conversation.title.ilike(like)).order_by(Conversation.updated_at.desc()).limit(5):
            out.append({"type": "conversation", "id": c.id, "title": c.title})
    return out


def _404():
    raise HTTPException(404, "No encontrado")


# ------------------------------------------------------------------ Entrenamiento y memoria
from app.agents import training  # noqa: E402


class LessonIn(BaseModel):
    title: str
    content: str
    author: str = ""
    role: str = "admin"


@router.get("/training")
async def memory_feed(status: str | None = None):
    """Toda la memoria del equipo (todas las lecciones de todos los agentes), más nueva primero."""
    out = []
    for key, meta in registry.AGENTS.items():
        for e in training.entries(key):
            if status and e["status"] not in status.split(","):
                continue
            out.append({**e, "agent": key, "agent_name": meta["name"], "color": meta["color"]})
    return sorted(out, key=lambda e: e["created"], reverse=True)


@router.get("/training/{agent}")
async def lessons(agent: str):
    if agent not in registry.AGENTS:
        _404()
    return training.entries(agent)


@router.post("/training/{agent}")
async def add_lesson(agent: str, body: LessonIn):
    if agent not in registry.AGENTS:
        _404()
    e = await training.add_checked(agent, body.title, body.content, body.author, body.role, "manual")
    ev = {"type": "learned", "agent": agent, "agent_name": registry.AGENTS[agent]["name"], "title": e["title"],
          "applied": e["status"] == "active", "status": e["status"], "reason": e["conflict_reason"], "id": e["id"]}
    events.publish(ev)
    return e


class LessonPatch(BaseModel):
    title: str | None = None
    content: str | None = None
    enabled: bool | None = None


@router.patch("/training/{agent}/{entry_id}")
async def patch_lesson(agent: str, entry_id: str, body: LessonPatch):
    try:
        return training.update(agent, entry_id, **body.model_dump())
    except ValueError as e:
        raise HTTPException(404, str(e))


class ResolveIn(BaseModel):
    action: str          # activate | replace | discard
    decided_by: str = ""


@router.post("/training/{agent}/{entry_id}/resolve")
async def resolve_lesson(agent: str, entry_id: str, body: ResolveIn):
    try:
        e = training.resolve(agent, entry_id, body.action, body.decided_by)
    except ValueError as err:
        raise HTTPException(400, str(err))
    events.publish({"type": "learned", "agent": agent, "agent_name": registry.AGENTS[agent]["name"],
                    "title": e["title"], "applied": e["status"] == "active", "status": e["status"], "id": e["id"]})
    return e


@router.delete("/training/{agent}/{entry_id}")
async def delete_lesson(agent: str, entry_id: str):
    training.delete(agent, entry_id)
    return {"ok": True}


@router.get("/audits")
async def list_audits():
    return training.audits()


@router.post("/audits")
async def run_audit():
    with session() as s:
        b = get_or_create_brand(s)
        brand_text = f"Marca: {b.name}. {b.description}. Tono: {b.tone}. Hashtags fijos: {' '.join(b.base_hashtags or [])}"
    audit = await training.team_audit(brand_text)
    events.publish({"type": "audit", "issues": len(audit["issues"]), "summary": audit["summary"]})
    return audit


@router.post("/learning/run")
async def learning_run():
    saved = await pipeline.run_learning(min_items=1)
    return {"lessons": saved}


@router.get("/learning/status")
async def learning_status():
    from app.db import Feedback
    with session() as s:
        b = get_or_create_brand(s)
        return {"mode": b.learning_mode or "auto",
                "pending_decisions": s.query(Feedback).filter(Feedback.processed.is_(False)).count(),
                "total_decisions": s.query(Feedback).count()}


# ------------------------------------------------------------------ Voces y audio
from app import voice as vc  # noqa: E402


@router.get("/voices")
async def voices():
    return vc.list_voices()


@router.post("/voices")
async def upload_voice(file: UploadFile = File(...), name: str = "", description: str = ""):
    ext = (Path(file.filename or "").suffix or ".wav").lower()
    if ext not in (".wav", ".mp3", ".m4a", ".ogg", ".flac"):
        raise HTTPException(400, "Sube un audio .wav, .mp3, .m4a, .ogg o .flac")
    stem = re.sub(r"[^\w-]", "_", name or Path(file.filename or "voz").stem)[:40] or "voz"
    path = vc.voices_dir() / f"{stem}{ext}"
    path.write_bytes(await file.read())
    path.with_suffix(".json").write_text(json.dumps({"description": description}, ensure_ascii=False), encoding="utf-8")
    return {"name": stem, "url": f"/voices/{path.name}"}


@router.delete("/voices/{name}")
async def delete_voice(name: str):
    for p in vc.voices_dir().glob(f"{Path(name).name}.*"):
        p.unlink()
    return {"ok": True}


class VoicePreviewIn(BaseModel):
    text: str
    request: str = ""
    subject: str = ""


@router.post("/voices/preview")
async def voice_preview(body: VoicePreviewIn):
    """Prueba rápida: el Locutor diseña la voz pedida y se escucha el texto."""
    from app.agents import specialists as sp
    from app.db import Subject
    ref, notes = None, ""
    if body.subject:
        with session() as s:
            sub = s.query(Subject).filter(Subject.name.ilike(body.subject)).first()
            if sub:
                notes = f"{sub.name}: {sub.voice_style or sub.notes}"
                if sub.voice_path and Path(sub.voice_path).exists():
                    ref = Path(sub.voice_path).read_bytes()
    voices_ = vc.list_voices()
    dv = await sp.direct_voice("Prueba de voz", body.request, max(3, len(body.text) / 14), voices_, notes, body.text)
    spec = dv["spec"]
    if ref is None and spec.get("base_voice"):
        m = next((v for v in voices_ if v["name"].lower() == spec["base_voice"].lower()), None)
        if m:
            ref = (vc.voices_dir() / m["file"]).read_bytes()
    try:
        wav = await vc.synthesize(body.text, settings.CONTENT_LANGUAGE, ref, spec)
    except Exception as e:
        raise HTTPException(503, str(e))
    out = settings.media_dir / f"preview_{uuid.uuid4().hex[:8]}.wav"
    vc.apply_fx(wav, spec, str(out))
    return {"url": url_for(str(out)), "spec": spec}


@router.post("/subjects/{sid}/voice")
async def subject_voice(sid: str, file: UploadFile | None = File(None), style: str = ""):
    from app.db import Subject
    with session() as s:
        sub = s.get(Subject, sid) or _404()
        if file is not None:
            ext = (Path(file.filename or "").suffix or ".wav").lower()
            sub.voice_path = save_bytes(await file.read(), f"voice_{sid}{ext}")
        if style:
            sub.voice_style = style
        from app.serialize import subject_dict
        return subject_dict(sub)


class AudioIn(BaseModel):
    voice_request: str = ""
    script: str = ""
    sound_request: str = ""
    voice: bool = True


@router.post("/posts/{pid}/audio")
async def add_audio(pid: str, body: AudioIn):
    try:
        return {"id": pipeline.new_audio(pid, body.voice_request, body.script, body.sound_request, body.voice)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/openrouter/models")
async def openrouter_models(kind: str = "brain"):
    """Modelos de OpenRouter: kind=brain (ven imágenes y usan herramientas) o kind=image (generan imágenes)."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            data = (await c.get("https://openrouter.ai/api/v1/models")).json()["data"]
    except Exception as e:
        raise HTTPException(503, f"No se pudo consultar OpenRouter: {e}")
    out = []
    for m in data:
        params = m.get("supported_parameters") or []
        inputs = (m.get("architecture") or {}).get("input_modalities") or []
        outputs = (m.get("architecture") or {}).get("output_modalities") or []
        if (kind == "image" and "image" in outputs) or (kind != "image" and "tools" in params and "image" in inputs):
            p = m.get("pricing") or {}
            out.append({"id": m["id"], "name": m.get("name", m["id"]), "structured": "structured_outputs" in params,
                        "price_in": float(p.get("prompt") or 0) * 1e6, "price_out": float(p.get("completion") or 0) * 1e6})
    return sorted(out, key=lambda x: (not x["id"].startswith("anthropic/"), x["id"]))


# ------------------------------------------------------------------ Resumen de APIs (botón "APIs")
def _mask(v: str) -> str:
    return ("••••" + v[-4:]) if v else ""


@router.get("/apis")
async def apis_summary():
    """Todas las APIs en uso: las de la web (Conexiones) y las del .env, con la clave enmascarada.
    Lo que se guarda en la web tiene prioridad sobre el .env."""
    from app.llm import provider
    with session() as s:
        conns = [public(c) for c in s.query(Connection).order_by(Connection.created_at)]
    items = []
    # Cerebro
    web_brain = {c["type"]: c for c in conns if c["category"] == "brain" and c["enabled"]}
    active = provider()
    for t, key, model, name in (("anthropic", settings.ANTHROPIC_API_KEY, settings.CLAUDE_MODEL, "Claude (Anthropic)"),
                                ("openrouter", settings.OPENROUTER_API_KEY, settings.OPENROUTER_MODEL, "OpenRouter")):
        if t in web_brain:
            c = web_brain[t]
            items.append({"group": "Cerebro", "name": c["name"], "type": t, "source": "web", "id": c["id"],
                          "key": c["config"].get("api_key", ""), "model": c["config"].get("model") or model,
                          "active": active == t, "last_test": c["last_test"]})
        elif key:
            items.append({"group": "Cerebro", "name": name, "type": t, "source": ".env", "key": _mask(key),
                          "model": model, "active": active == t})
    # Motor de imagen y video
    engine_conns = [c for c in conns if c["category"] == "engine"]
    for c in engine_conns:
        items.append({"group": "Imagen y video", "name": c["name"], "type": c["type"], "source": "web", "id": c["id"],
                      "key": c["config"].get("api_key", "") or c["config"].get("url", ""),
                      "model": c["config"].get("model", ""), "roles": c["roles"], "active": c["enabled"],
                      "last_test": c["last_test"]})
    eng = settings.IMAGE_ENGINE
    desc = {"openrouter": "OpenRouter · " + ("auto (el más realista disponible)" if settings.OPENROUTER_IMAGE_MODEL == "auto" else settings.OPENROUTER_IMAGE_MODEL), "mock": "Simulado (modo demo)",
            "comfyui": f"ComfyUI en {settings.COMFYUI_URL}"}.get(eng, eng)
    items.append({"group": "Imagen y video", "name": "Motor por defecto", "type": "openrouter_image" if eng == "openrouter" else eng,
                  "source": ".env", "model": desc, "active": True,
                  "note": "Se usa cuando ninguna conexión de la web tiene asignado el rol."})
    # Voz, publicación y avisos
    for c in conns:
        if c["category"] in ("voice", "publisher", "webhook"):
            group = {"voice": "Voz", "publisher": "Publicación", "webhook": "Avisos (webhooks)"}[c["category"]]
            items.append({"group": group, "name": c["name"], "type": c["type"], "source": "web", "id": c["id"],
                          "key": c["config"].get("api_key") or c["config"].get("bot_token") or c["config"].get("url", ""),
                          "model": c["config"].get("chat_id") or c["config"].get("model", ""), "active": c["enabled"],
                          "last_test": c["last_test"]})
    if not any(c["category"] == "voice" for c in conns):
        items.append({"group": "Voz", "name": "Voz local (Chatterbox)", "type": "local_tts", "source": ".env",
                      "model": settings.TTS_URL, "active": True})
    if settings.TELEGRAM_BOT_TOKEN and not any(c["type"] == "telegram" for c in conns):
        items.append({"group": "Publicación", "name": "Telegram", "type": "telegram", "source": ".env",
                      "key": _mask(settings.TELEGRAM_BOT_TOKEN), "model": settings.TELEGRAM_CHAT_ID, "active": True})
    return items


class EnvOverrideIn(BaseModel):
    type: str
    config: dict
    roles: list[str] = []


@router.post("/apis/override")
async def override_env(body: EnvOverrideIn):
    """Cambiar desde la web algo que venía del .env: crea una conexión que tiene prioridad (el .env no se toca)."""
    if body.type not in TYPES:
        raise HTTPException(400, "Tipo desconocido")
    cfg = {k: v for k, v in body.config.items() if isinstance(v, str) and v.strip() and not v.startswith("••••")}
    with session() as s:
        existing = s.query(Connection).filter(Connection.type == body.type).first()
        if existing:
            existing.config = merge_config(existing.config, cfg)
            existing.enabled = True
            if body.roles:
                existing.roles = body.roles
            c = existing
        else:
            c = Connection(type=body.type, name=TYPES[body.type]["name"], config=cfg,
                           roles=[r for r in body.roles if r in ENGINE_ROLES])
            s.add(c)
        s.flush()
        out = public(c)
    reset_cache()
    events.invalidate_hooks()
    return out


# ------------------------------------------------------------------ Switch Local / Auto / Nube
def _describe(kind: str, cfg: dict) -> str:
    from app.engines.comfy import VIDEO_PRESETS
    if kind == "mock":
        return "Simulado (demo)"
    if kind == "comfyui":
        return f"ComfyUI · {cfg.get('url') or settings.COMFYUI_URL}"
    if kind == "openrouter_image":
        return f"OpenRouter · {cfg.get('model') or settings.OPENROUTER_IMAGE_MODEL}"
    if kind == "fal":
        return f"fal.ai · {cfg.get('model_video') or cfg.get('model_generate') or 'por defecto'}"
    if kind == "openrouter_video":
        m = cfg.get("model") or settings.OPENROUTER_VIDEO_MODEL
        return f"OpenRouter · {'el mejor disponible (auto)' if m == 'auto' else m}"
    return TYPES.get(kind, {}).get("name", kind)


def mode_routes(mode: str) -> list[dict]:
    """Qué herramienta usaría cada parte con este modo (sin generar nada)."""
    from app.connections import engine_for_role, list_enabled
    from app.engines import _routed
    from app.llm import openrouter_config, provider
    out = []
    labels = {"generate": "Imagen", "edit": "Edición", "video": "Video", "sound": "Sonido"}
    for role, label in labels.items():
        try:
            if mode in ("local", "cloud"):
                _, kind, cfg = _routed(role, mode)
            else:
                conn = engine_for_role(role)
                if conn:
                    kind, cfg = conn.type, conn.config
                elif settings.IMAGE_ENGINE == "openrouter" and role not in ("video", "sound") and openrouter_config()[0]:
                    kind, cfg = "openrouter_image", {}
                elif settings.IMAGE_ENGINE == "openrouter" and role == "video" and openrouter_config()[0]:
                    kind, cfg = "openrouter_video", {}
                elif settings.IMAGE_ENGINE == "openrouter" and role == "sound":
                    raise ValueError("Falta: efectos de sonido necesitan la GPU (Stable Audio). Cambia a Local cuando la conectes")
                else:
                    kind, cfg = ("mock" if settings.IMAGE_ENGINE == "mock" else "comfyui"), {}
            local = kind in ("comfyui", "mock")
            out.append({"role": role, "label": label, "ok": True, "tool": _describe(kind, cfg), "local": local})
        except ValueError as e:
            out.append({"role": role, "label": label, "ok": False, "tool": str(e), "local": mode == "local"})
    voices = list_enabled("voice")
    if mode == "local":
        voices = [c for c in voices if c.type == "local_tts"]
    elif mode == "cloud":
        voices = [c for c in voices if c.type != "local_tts"]
    if voices:
        out.append({"role": "voice", "label": "Voz", "ok": True, "tool": voices[0].name, "local": voices[0].type == "local_tts"})
    elif mode == "cloud" and settings.IMAGE_ENGINE != "mock":
        out.append({"role": "voice", "label": "Voz", "ok": False, "tool": "Falta: conecta ElevenLabs u otra voz en el botón APIs", "local": False})
    else:
        out.append({"role": "voice", "label": "Voz", "ok": True, "tool": f"Voz local · {settings.TTS_URL}", "local": True})
    brain = "OpenRouter" if provider() == "openrouter" else "Claude (Anthropic)"
    out.insert(0, {"role": "brain", "label": "Cerebro", "ok": True, "tool": brain, "local": False})
    return out


@router.get("/mode")
async def get_mode():
    from app.engines import MODES, infra_mode
    mode = infra_mode()
    return {"mode": mode, "label": MODES[mode], "routes": mode_routes(mode)}


class ModeIn(BaseModel):
    mode: str


@router.put("/mode")
async def set_mode(body: ModeIn):
    from app.engines import MODES
    if body.mode not in MODES:
        raise HTTPException(400, "Modo inválido (auto, local o cloud)")
    with session() as s:
        get_or_create_brand(s).infra_mode = body.mode
    reset_cache()
    events.publish({"type": "mode", "mode": body.mode})
    return await get_mode()


# ------------------------------------------------------------------ Catálogo de modelos de video (OpenRouter)
VIDEO_TAGS = [
    # (palabra clave en el id, etiquetas, para qué sirve)
    ("veo-3", ["Mejor calidad", "Con audio", "Cine"], "Realismo de cine con sonido nativo (voces, ambiente). La opción más completa."),
    ("sora-2-pro", ["Física realista", "Con audio", "Premium"], "Escenas complejas, movimiento y física muy creíbles. Caro."),
    ("sora-2", ["Física realista", "Con audio"], "Buen realismo y sonido a menor precio que Sora 2 Pro."),
    ("seedance", ["Personajes consistentes", "Movimiento fluido", "Buen precio"], "Ideal para reels con personas: mantiene la cara y el estilo."),
    ("kling", ["Personas", "Cámara dinámica", "Reels"], "Muy usado en redes: gente, baile, moda, movimientos de cámara."),
    ("wan", ["Económico", "Imagen a video"], "Barato y estable para animar fotos; base open-source."),
    ("omni", ["Rápido", "Versátil"], "Rápido para pruebas y variaciones; texto o imagen a video."),
    ("heygen", ["Avatar que habla", "Presentador"], "Una persona hablando a cámara (tipo presentador o UGC)."),
    ("hailuo", ["Movimiento expresivo"], "Acción y expresiones marcadas."),
    ("runway", ["Creativo", "Cine"], "Estética cinematográfica y estilos creativos."),
    ("luma", ["Creativo"], "Movimientos suaves y estéticos."),
]


def _video_info(m: dict) -> dict:
    mid = str(m.get("id", ""))
    low = mid.lower()
    tags, use = ["Video"], "Generación de video."
    for key, t, u in VIDEO_TAGS:
        if key in low:
            tags, use = t, u
            break
    price = m.get("pricing") or {}
    return {"id": mid, "name": m.get("name") or mid, "tags": tags, "use": use,
            "durations": m.get("supported_durations") or m.get("durations") or [],
            "resolutions": m.get("supported_resolutions") or m.get("resolutions") or [],
            "aspect_ratios": m.get("supported_aspect_ratios") or m.get("aspect_ratios") or [],
            "price": price, "description": (m.get("description") or "")[:240]}


@router.get("/video-models")
async def video_models():
    """Modelos de video de OpenRouter, ordenados del mejor al más simple, con etiquetas de uso."""
    from app.engines.cloud import OpenRouterVideoEngine
    eng = OpenRouterVideoEngine({})
    try:
        models = await eng.list_models()
    except Exception as e:
        raise HTTPException(503, f"No se pudo consultar OpenRouter: {e}")
    rank = {k: i for i, k in enumerate(OpenRouterVideoEngine.PREFERENCE)}
    def order(m):
        low = str(m.get("id", "")).lower()
        return min([rank[k] for k in rank if k in low] or [99])
    current = None
    with session() as s:
        c = s.query(Connection).filter(Connection.type == "openrouter_video", Connection.enabled.is_(True)).first()
        current = (c.config or {}).get("model") if c else None
    auto = (await eng._pick())["id"] if models else None
    return {"current": current or settings.OPENROUTER_VIDEO_MODEL, "auto_pick": auto,
            "models": [_video_info(m) for m in sorted(models, key=order)]}


# ------------------------------------------------------------------ En vivo: quién trabaja ahora
@router.get("/working")
async def working_now():
    """Agentes y motores trabajando en este momento, más la cola y los últimos registros."""
    from app import working
    from app.working import ENGINE_NAMES
    active = working.snapshot()
    team = [{"key": k, **{x: v[x] for x in ("name", "role", "color")}} for k, v in registry.AGENTS.items()]
    team += [{"key": k, "name": n, "role": "Genera con el modelo elegido (local o nube)", "color": "#64748b"}
             for k, n in ENGINE_NAMES.items()]
    return {"active": active, "count": len(active), "agents_busy": len({a["agent"] for a in active}),
            "team": team, "queue": pipeline.queue_stats()}


@router.get("/models-catalog")
async def models_catalog():
    """Todos los modelos de imagen y video que Shelton puede elegir, con etiquetas y el que usa el automático."""
    from app.model_picker import catalog
    return await catalog()
