"""Granja de GPUs: todas las tarjetas trabajan para Shelton (las de la placa y las alquiladas).

- Placa: cada GPU de LORA_GPUS (por defecto la 0) entrena una LoRA a la vez, como siempre.
- Tarjetas alquiladas (RunPod, Vast…) u otras PCs: se conectan con el «trabajador de Shelton»
  (scripts/shelton_worker.py) y se agregan en Conexiones → «Nodo de GPU (trabajador)». Cada nodo dice cuántos
  entrenamientos hace a la vez (una RTX PRO 6000 dividida en 3-4) y con qué perfil (pro = LoRA más detallada).
- La fila reparte sola: primero la placa, después los nodos alquilados. Las fotos se borran del nodo al terminar.
- Cuando una LoRA termina (donde sea), se copia a todas las tarjetas que generan, para usarla en cualquiera.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException

from app.config import settings
from app.db import Subject, session

log = logging.getLogger("shelton.granja")
router = APIRouter(prefix="/api")

_health: dict[str, tuple[float, dict]] = {}
_remote_jobs: dict[str, tuple[dict, str]] = {}     # subject_id -> (nodo, job_id en el nodo)
_transport = None                                  # las pruebas inyectan un transporte simulado
POLL = 5                                           # cada cuántos segundos pregunta el avance al nodo
LOST_AFTER = 180                                   # segundos sin respuesta para darlo por caído


def remote_nodes() -> list[dict]:
    from app.connections import list_enabled
    out = []
    for c in list_enabled("gpu"):
        cfg = c.config or {}
        if not cfg.get("url"):
            continue
        try:
            slots = max(1, min(int(cfg.get("slots") or 1), 8))
        except (TypeError, ValueError):
            slots = 1
        try:
            cost = float(cfg.get("cost_hour") or 0)
        except (TypeError, ValueError):
            cost = 0.0
        out.append({"id": f"node:{c.id}", "kind": "remote", "conn_id": c.id, "label": c.name or cfg["url"],
                    "url": cfg["url"].rstrip("/"), "token": cfg.get("token", ""), "slots": slots,
                    "profile": "pro" if str(cfg.get("profile", "pro")).lower().startswith("pro") else "estandar",
                    "cost_hour": cost, "comfy_url": (cfg.get("comfy_url") or "").rstrip("/")})
    return out


def local_slots() -> list[dict]:
    from app import lora
    ok, _ = lora.available()
    if not ok:
        return []
    gpus = lora.local_gpus()
    return [{"id": f"local:{g}", "kind": "local", "gpu": g, "slots": 1,
             "label": "Placa" if len(gpus) == 1 else f"Placa · GPU {g}", "profile": lora._local_profile()} for g in gpus]


def _headers(node: dict) -> dict:
    return {"Authorization": f"Bearer {node['token']}"} if node.get("token") else {}


def _client(timeout: float = 30) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, transport=_transport)


async def health(node: dict, fresh: bool = False) -> dict:
    hit = _health.get(node["id"])
    if hit and not fresh and time.time() - hit[0] < 30:
        return hit[1]
    try:
        async with _client(8) as c:
            r = await c.get(f"{node['url']}/health", headers=_headers(node))
            data = r.json() if r.status_code == 200 else {"ok": False, "error": f"HTTP {r.status_code}"}
    except Exception as e:  # noqa: BLE001
        data = {"ok": False, "error": f"No responde: {str(e)[:120]}"}
    _health[node["id"]] = (time.time(), data)
    return data


async def free_slot(used: dict[str, int]) -> dict | None:
    """El primer lugar libre: GPUs de la placa primero, después los nodos (si están prendidos y con lugar)."""
    for s in local_slots():
        if used.get(s["id"], 0) < s["slots"]:
            return s
    for n in remote_nodes():
        if used.get(n["id"], 0) >= n["slots"]:
            continue
        h = await health(n)
        if h.get("ok") and h.get("trainer", True) and int(h.get("free", n["slots"])) > 0:
            return n
    return None


# ------------------------------------------------------------------ entrenar en un nodo remoto
async def run_remote(subject_id: str, steps: int, base: int, mode: str, node: dict) -> None:
    """Entrena en una tarjeta alquilada: sube fotos y punto de partida, sigue el avance y trae la LoRA."""
    from app import lora
    from app.lora import STEP_RE, _finish, _pre, _update, config_dict
    # 1) Primero se reserva el lugar en el nodo: si no responde, vuelve a la fila sin tocar nada
    try:
        async with _client(30) as c0:
            r = await c0.post(f"{node['url']}/jobs", json={"name": lora.job_for(subject_id)}, headers=_headers(node))
    except httpx.HTTPError as e:
        _health.pop(node["id"], None)
        _health[node["id"]] = (time.time(), {"ok": False, "error": f"No responde: {str(e)[:120]}"})
        raise NodeUnavailable(node["label"]) from e
    if r.status_code != 200:
        raise NodeUnavailable(node["label"])
    jid = r.json()["id"]
    ctx = await _pre(subject_id, mode, base, node["profile"])
    if not ctx:
        await _drop(node, jid)
        return
    work, job, trigger = ctx["work"], ctx["job"], ctx["trigger"]
    t0 = time.time()
    _update(subject_id, lora_status="training", lora_progress=0, lora_trigger=trigger,
            info={"images": ctx["n"], "steps": steps, "from_step": ctx["base"], "mode": mode, "where": node["label"],
                  "requeued": False,
                  "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "phase": f"Subiendo fotos a {node['label']}",
                  "error": "", "eta_min": None})
    cfg = config_dict(job, "__JOB__", trigger, steps, node["profile"])
    async with _client(120) as c:
        _remote_jobs[subject_id] = (node, jid)
        try:
            files = sorted((work / "dataset").iterdir())
            seed_dir = work / "output" / job
            seeds = [f for f in seed_dir.glob("*.safetensors")] if (mode == "continue" and seed_dir.exists()) else []
            if seeds:      # el punto más avanzado, para que siga sumando allá
                seeds = [max(seeds, key=lora._read_saved_step)]
            for f in files:
                await _upload(c, node, jid, f"dataset/{f.name}", f.read_bytes())
            for f in seeds:
                await _upload(c, node, jid, f"output/{job}/{job}.safetensors", f.read_bytes())
            r = await c.post(f"{node['url']}/jobs/{jid}/start", json={"config": cfg}, headers=_headers(node))
            if r.status_code != 200:
                raise RuntimeError(f"{node['label']} no pudo arrancar: {r.text[:200]}")
        except BaseException:
            _remote_jobs.pop(subject_id, None)
            await _drop(node, jid)
            raise
    # Se guarda dónde corre: si Shelton se reinicia, al volver se reconecta a este mismo trabajo
    _update(subject_id, info={"remote_job": {"conn_id": node.get("conn_id"), "jid": jid, "t0": t0, "steps": steps,
                                             "ctx": {k: ctx[k] for k in ("name", "trigger", "job", "base", "n")}}})
    await _follow_remote(subject_id, node, jid, ctx, steps, t0)


async def resume_remote(subject_id: str, node: dict, jid: str, info: dict) -> None:
    """Shelton se reinició mientras entrenaba en una tarjeta alquilada: se reconecta al mismo trabajo."""
    from app.lora import _work
    rj = info.get("remote_job") or {}
    ctx = {**rj.get("ctx", {}), "work": _work(subject_id)}
    _remote_jobs[subject_id] = (node, jid)
    await _follow_remote(subject_id, node, jid, ctx, int(rj.get("steps") or info.get("steps") or 0),
                         float(rj.get("t0") or time.time()))


class NodeLost(RuntimeError):
    """La tarjeta alquilada dejó de responder (o perdió el trabajo) a mitad del entrenamiento."""


async def _follow_remote(subject_id: str, node: dict, jid: str, ctx: dict, steps: int, t0: float) -> None:
    from app.lora import STEP_RE, _finish, _update
    work, job = ctx["work"], ctx["job"]
    finished = False
    async with _client(120) as c:
        try:
            spp, first, fails = None, None, 0
            while True:
                await asyncio.sleep(POLL)
                with session() as s:
                    if s.get(Subject, subject_id).lora_status == "canceled":
                        await c.post(f"{node['url']}/jobs/{jid}/cancel", headers=_headers(node))
                        finished = True
                        return
                try:
                    r = await c.get(f"{node['url']}/jobs/{jid}", headers=_headers(node))
                except httpx.HTTPError:
                    fails += 1
                    if fails * POLL >= LOST_AFTER:
                        raise NodeLost(f"{node['label']} dejó de responder")
                    continue
                if r.status_code == 404:          # el nodo se reinició y perdió el trabajo
                    raise NodeLost(f"{node['label']} perdió el trabajo (se reinició)")
                fails = 0
                st = r.json()
                done = int(st.get("step") or 0)
                if not done:
                    for line in reversed(st.get("tail") or []):
                        m = STEP_RE.search(line)
                        if m:
                            done = int(m.group(1))
                            break
                if done:
                    now_t = time.time()
                    if first is None or done < first[0]:
                        first = (done, now_t)
                    did = done - first[0]
                    if did >= 10:
                        spp = (now_t - first[1]) / did
                    start_at = int(ctx.get("base") or 0)
                    pct = 100.0 * max(0, done - start_at) / max(1, steps - start_at)
                    _update(subject_id, lora_progress=min(99.0, pct),
                            info={"phase": f"Entrenando en {node['label']}: paso {done} de {steps}", "step": done,
                                  "eta_min": round(spp * (steps - done) / 60) if spp else None})
                elif st.get("phase"):
                    _update(subject_id, info={"phase": f"{node['label']}: {st['phase']}"})
                if st.get("status") == "done":
                    break
                if st.get("status") in ("failed", "canceled"):
                    finished = True
                    raise RuntimeError(st.get("error") or "El entrenamiento falló en el nodo")
            _update(subject_id, info={"phase": f"Trayendo la LoRA desde {node['label']}"})
            res = await c.get(f"{node['url']}/jobs/{jid}/result", headers=_headers(node), timeout=600)
            if res.status_code != 200:
                raise RuntimeError(f"No pude traer la LoRA: {res.text[:200]}")
            out_dir = work / "output" / job
            out_dir.mkdir(parents=True, exist_ok=True)
            final = out_dir / f"{job}.safetensors"
            final.write_bytes(res.content)
            finished = True
            hours = (time.time() - t0) / 3600
            _update(subject_id, info={"remote_job": None})
            await _finish(subject_id, ctx, steps, final, t0, spp, provider="gpu_node",
                          cost_usd=round(hours * node["cost_hour"], 4) if node["cost_hour"] else None,
                          where=node["label"])
        finally:
            _remote_jobs.pop(subject_id, None)
            if finished:
                try:   # las fotos de la modelo no se quedan en la tarjeta alquilada
                    await c.delete(f"{node['url']}/jobs/{jid}", headers=_headers(node))
                except Exception:  # noqa: BLE001
                    log.warning("No pude borrar el trabajo %s en %s", jid, node["label"])


class NodeUnavailable(RuntimeError):
    """El nodo no respondió al reservar lugar: el modelo vuelve a la fila (nada se tocó)."""


async def _drop(node: dict, jid: str) -> None:
    try:
        async with _client(15) as c:
            await c.delete(f"{node['url']}/jobs/{jid}", headers=_headers(node))
    except Exception:  # noqa: BLE001
        pass


async def _upload(c: httpx.AsyncClient, node: dict, jid: str, path: str, data: bytes) -> None:
    r = await c.post(f"{node['url']}/jobs/{jid}/file", params={"path": path}, content=data, headers=_headers(node))
    if r.status_code != 200:
        raise RuntimeError(f"No pude subir {path} a {node['label']}: {r.text[:150]}")


def cancel_remote(subject_id: str) -> None:
    hit = _remote_jobs.get(subject_id)
    if not hit:
        return
    node, jid = hit

    async def _go():
        try:
            async with _client(10) as c:
                await c.post(f"{node['url']}/jobs/{jid}/cancel", headers=_headers(node))
        except Exception:  # noqa: BLE001
            pass
    try:
        asyncio.get_event_loop().create_task(_go())
    except RuntimeError:
        pass


async def push_lora(path: Path) -> list[str]:
    """Copia una LoRA a todos los nodos que generan con su propio ComfyUI."""
    sent = []
    for n in remote_nodes():
        if not n["comfy_url"]:
            continue
        try:
            async with _client(300) as c:
                r = await c.post(f"{n['url']}/loras", params={"name": path.name}, content=path.read_bytes(),
                                 headers=_headers(n))
            if r.status_code == 200:
                sent.append(n["label"])
        except Exception:  # noqa: BLE001
            log.warning("No pude copiar %s a %s", path.name, n["label"])
    return sent


# ------------------------------------------------------------------ pantalla Granja de GPUs (solo admin)
async def overview() -> dict:
    from app import consumo, lora
    with session() as s:
        subs = [(x.id, x.name, x.lora_status, x.lora_progress or 0, dict(x.lora_info or {}))
                for x in s.query(Subject).filter(Subject.lora_status.in_(["training", "queued"]))]
    training = [{"id": i, "name": n, "status": st, "progress": round(p), "where": inf.get("where", ""),
                 "eta_min": inf.get("eta_min"), "phase": inf.get("phase", ""), "position": inf.get("position"),
                 "queued_at": inf.get("queued_at", "")} for i, n, st, p, inf in subs]
    training.sort(key=lambda x: (x["status"] != "training", x["queued_at"]))
    local = await consumo.gpu_now()
    nodes = []
    for n in remote_nodes():
        h = await health(n, fresh=True)
        nodes.append({**{k: v for k, v in n.items() if k != "token"}, "health": h})
    ok, why = lora.available()
    return {"training": training, "local": {"gpus": local.get("gpus", []), "trainer_ready": ok, "trainer_msg": why,
                                            "slots": local_slots(), "profile": lora._local_profile()},
            "nodes": nodes, "profiles": {k: v["label"] for k, v in lora.PROFILES.items()}}


@router.get("/granja")
async def granja_overview():
    return await overview()


@router.get("/granja/entrenando")
async def granja_training():
    """Lo que entrena ahora y lo que está en fila (para todos: sin datos técnicos)."""
    with session() as s:
        subs = [(x.id, x.name, x.lora_status, x.lora_progress or 0, dict(x.lora_info or {}))
                for x in s.query(Subject).filter(Subject.lora_status.in_(["training", "queued"]))]
    out = [{"id": i, "name": n, "status": st, "progress": round(p), "eta_min": inf.get("eta_min"),
            "position": inf.get("position"), "queued_at": inf.get("queued_at", "")} for i, n, st, p, inf in subs]
    out.sort(key=lambda x: (x["status"] != "training", x["queued_at"]))
    return out


@router.post("/granja/nodos/{conn_id}/probar")
async def granja_test(conn_id: str):
    node = next((n for n in remote_nodes() if n["conn_id"] == conn_id), None)
    if not node:
        raise HTTPException(404, "Ese nodo no existe o está apagado en Conexiones")
    return await health(node, fresh=True)
