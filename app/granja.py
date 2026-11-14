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

from app.zona import adult_only  # noqa: E402

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
                    "cost_hour": cost, "comfy_url": (cfg.get("comfy_url") or "").rstrip("/"),
                    "runpod": bool(cfg.get("runpod_api_key") and cfg.get("runpod_pod_id")),
                    "resuming": resuming(c.id)})
    return out


def local_slots() -> list[dict]:
    from app import lora
    ok, _ = lora.available()
    if not ok:
        return []
    gpus = lora.local_gpus()
    return [{"id": f"local:{g}", "kind": "local", "gpu": g, "slots": 1,
             "label": "Placa" if len(gpus) == 1 else f"Placa · GPU {g}", "profile": lora.profile_for_gpu(g)} for g in gpus]


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


async def free_slot(used: dict[str, int], big: bool = False, only: str = "") -> dict | None:
    """El primer lugar libre. Si hay una tarjeta alquilada prendida (se paga por hora y es la más rápida), va
    PRIMERO a ella hasta llenarla; recién después usa las GPUs de la placa.
    big=True (modelos grandes como Qwen-Image): nunca en una GPU chica de la placa (solo placa «pro» o tarjetas alquiladas)."""
    for n in remote_nodes():      # las tarjetas alquiladas siempre son grandes: con perfil estándar, Qwen va comprimido
        if only and n["id"] != only:
            continue
        if used.get(n["id"], 0) >= n["slots"]:
            continue
        h = await health(n)
        if h.get("ok") and h.get("trainer", True) and int(h.get("free", n["slots"])) > 0:
            return n
    for s in local_slots():
        if only and s["id"] != only:
            continue
        if big and s.get("profile") != "pro":
            continue
        if used.get(s["id"], 0) < s["slots"]:
            return s
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
    cfg = config_dict(job, "__JOB__", trigger, steps, node["profile"], lora.base_of(subject_id))
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
    from app import lora as lora_mod
    from app.lora import STEP_RE, _finish, _update
    work, job = ctx["work"], ctx["job"]
    finished = False
    async with _client(120) as c:
        try:
            spp, first, fails = lora_mod.sec_per_step(subject_id) or None, None, 0
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
                    # Velocidad con las últimas lecturas reales (ventana de ~5 min). Al arrancar o retomar, la
                    # tarjeta puede informar de golpe muchos pasos: eso no es velocidad y daba «faltan ~0 min».
                    if first is None or done < first[-1][0]:
                        first = []
                    first.append((done, now_t))
                    first = [x for x in first if now_t - x[1] <= 300] or [(done, now_t)]
                    d_steps, d_t = first[-1][0] - first[0][0], first[-1][1] - first[0][1]
                    if d_steps >= 10 and d_t >= 30 and d_steps / d_t < 20:
                        spp = d_t / d_steps
                    start_at = int(ctx.get("base") or 0)
                    pct = 100.0 * max(0, done - start_at) / max(1, steps - start_at)
                    _update(subject_id, lora_progress=min(99.0, pct),
                            info={"phase": f"⚡ Entrenando en {node['label']}: paso {done} de {steps}", "step": done,
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


_sent_loras: dict[str, set] = {}


async def ensure_loras(node: dict, names: list[str]) -> None:
    """Antes de generar en la tarjeta alquilada: le copia las LoRA del Banco que todavía no tiene."""
    from app.config import settings
    sent = _sent_loras.setdefault(node["id"], set())
    for name in names:
        if name in sent:
            continue
        path = Path(settings.COMFY_LORA_DIR).expanduser() / name
        if not path.exists():
            continue
        try:
            async with _client(300) as c:
                r = await c.post(f"{node['url']}/loras", params={"name": path.name}, content=path.read_bytes(),
                                 headers=_headers(node))
            if r.status_code == 200:
                sent.add(name)
        except Exception:  # noqa: BLE001
            log.warning("No pude copiar %s a %s", name, node["label"])


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
        subs = [(x.id, "🔒 Privado (Shelton Hot)" if adult_only(x) else x.name, x.lora_status, x.lora_progress or 0,
                 dict(x.lora_info or {}))
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


# ------------------------------------------------------------------ ▶ prender / ⏸ pausar la tarjeta en RunPod
RUNPOD_GQL = "https://api.runpod.io/graphql"


async def runpod(cid: str, query: str) -> dict:
    from app.connections import list_enabled
    c = next((x for x in list_enabled("gpu") if x.id == cid), None)
    cfg = (c.config or {}) if c else {}
    key, pod = cfg.get("runpod_api_key"), cfg.get("runpod_pod_id")
    if not key or not pod:
        raise ValueError("Falta la clave de API de RunPod o el ID del pod: Conexiones → la tarjeta → Editar")
    async with _client(30) as cl:
        r = await cl.post(RUNPOD_GQL, params={"api_key": key}, json={"query": query.replace("POD_ID", pod)},
                          headers={"Authorization": f"Bearer {key}"})
    if r.status_code in (401, 403):
        raise ValueError("RunPod rechazó la clave de API: créala de nuevo en RunPod → Settings → API Keys")
    try:
        j = r.json()
    except ValueError:
        raise ValueError(f"RunPod respondió {r.status_code}")
    if j.get("errors"):
        msg = "; ".join(str(e.get("message", e)) for e in j["errors"])[:300]
        if "not enough" in msg.lower() or "no longer any instances" in msg.lower():
            msg = "RunPod no tiene esa tarjeta libre ahora mismo en su datacenter. Prueba en unos minutos. (" + msg + ")"
        raise ValueError(msg)
    return j.get("data") or {}


@router.get("/granja/rtx/{cid}")
async def rtx_status(cid: str):
    try:
        d = await runpod(cid, 'query { pod(input: {podId: "POD_ID"}) { id name desiredStatus costPerHr } }')
    except ValueError as e:
        raise HTTPException(400, str(e))
    p = d.get("pod") or {}
    return {"status": p.get("desiredStatus", ""), "name": p.get("name", ""), "cost_hour": p.get("costPerHr")}


RESUME_EVERY = 60        # segundos entre intentos cuando RunPod no tiene la tarjeta libre
RESUME_TRIES = 45        # ~45 minutos intentando solo
_resuming: dict[str, dict] = {}     # cid → {"try": n, "since": t, "error": ""}


def _no_free_gpu(msg: str) -> bool:
    return "no tiene esa tarjeta libre" in msg


async def _keep_resuming(cid: str) -> None:
    """RunPod no tenía la tarjeta libre: sigue intentando prenderla sola cada minuto (hasta ~45 min)."""
    q = 'mutation { podResume(input: {podId: "POD_ID", gpuCount: 1}) { id desiredStatus costPerHr } }'
    st = _resuming[cid]
    try:
        while st["try"] < RESUME_TRIES and not st.get("stop"):
            await asyncio.sleep(RESUME_EVERY)
            if st.get("stop"):
                break
            st["try"] += 1
            try:
                await runpod(cid, q)
                _health.pop(f"node:{cid}", None)
                log.info("RunPod: tarjeta %s prendida al intento %d", cid, st["try"])
                return
            except ValueError as e:
                st["error"] = str(e)
                if not _no_free_gpu(str(e)):
                    return
            except Exception as e:  # noqa: BLE001
                st["error"] = str(e)[:200]
    finally:
        _resuming.pop(cid, None)


def resuming(cid: str) -> dict | None:
    st = _resuming.get(cid)
    return {"try": st["try"], "max": RESUME_TRIES} if st else None


@router.post("/granja/rtx/{cid}/{action}")
async def rtx_power(cid: str, action: str):
    """▶ prender o ⏸ pausar la tarjeta alquilada. Al prender, se conecta sola en 2-4 minutos (arranque automático)."""
    if action == "dejar":                                  # ✕ dejar de intentar prenderla
        if cid in _resuming:
            _resuming[cid]["stop"] = True
            _resuming.pop(cid, None)
        return {"ok": True, "stopped": True}
    if action not in ("prender", "pausar"):
        raise HTTPException(400, "Acción desconocida")
    q = ('mutation { podResume(input: {podId: "POD_ID", gpuCount: 1}) { id desiredStatus costPerHr } }' if action == "prender"
         else 'mutation { podStop(input: {podId: "POD_ID"}) { id desiredStatus } }')
    if action == "pausar" and cid in _resuming:          # estaba esperando una libre: deja de intentar
        _resuming[cid]["stop"] = True
    if action == "prender" and cid in _resuming:
        return {"ok": True, "waiting": True, **resuming(cid)}
    try:
        d = await runpod(cid, q)
    except ValueError as e:
        if action == "prender" and _no_free_gpu(str(e)):
            _resuming[cid] = {"try": 0, "since": time.time(), "error": str(e)}
            asyncio.get_event_loop().create_task(_keep_resuming(cid))
            return {"ok": True, "waiting": True, **resuming(cid)}
        raise HTTPException(400, str(e))
    _health.pop(f"node:{cid}", None)
    p = d.get("podResume") or d.get("podStop") or {}
    return {"ok": True, "status": p.get("desiredStatus", ""), "cost_hour": p.get("costPerHr")}


@router.get("/granja")
async def granja_overview():
    return await overview()


@router.get("/granja/entrenando")
async def granja_training():
    """Lo que entrena ahora y lo que está en fila (para todos: sin datos técnicos)."""
    with session() as s:
        subs = [(x.id, "🔒 Privado (Shelton Hot)" if adult_only(x) else x.name, x.lora_status, x.lora_progress or 0,
                 dict(x.lora_info or {}))
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
