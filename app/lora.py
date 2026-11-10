"""Entrenar una LoRA de un modelo del banco en la GPU local, para que la placa lo aprenda de verdad.

Flujo:
1. Junta las mejores fotos del modelo: las de su ficha y las de los posts aprobados o publicados donde aparece
   (pueden venir de cualquier IA: Nano Banana, la 3090...). Hacen falta al menos MIN_IMAGES.
2. Entrena con ai-toolkit (Z-Image Turbo + su adaptador oficial de entrenamiento) en la 3090. Mientras entrena, las
   fotos locales de Shelton esperan su turno y nuestro ComfyUI suelta la GPU.
3. Copia la LoRA a la carpeta de LoRAs de nuestro ComfyUI. Desde ahí, en modo Local, las fotos de ese modelo salen
   con Z-Image Turbo + su LoRA (más rápido y con su cara aprendida).

Se instala con scripts/lora_setup.sh (ai-toolkit en ~/ai-toolkit, con su propio Python 3.12).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import time
from contextlib import AsyncExitStack
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps

from app import events
from app.config import settings
from app.db import Post, Subject, session
from app.serialize import subject_dict

log = logging.getLogger("shelton.lora")
MIN_IMAGES = 8
MAX_IMAGES = 30
_procs: dict[str, asyncio.subprocess.Process] = {}
_tasks: dict[str, asyncio.Task] = {}
STEP_RE = re.compile(r"(\d+)\s*/\s*(\d+)\s*\[")      # barra de progreso de ai-toolkit: " 120/2000 ["


def trainer_python() -> Path:
    return Path(settings.AITK_DIR).expanduser() / "venv" / "bin" / "python"


def available() -> tuple[bool, str]:
    """¿Se puede entrenar en esta máquina?"""
    aitk = Path(settings.AITK_DIR).expanduser()
    if not (aitk / "run.py").exists() or not trainer_python().exists():
        return False, ("Falta el entrenador en la PC con la GPU. Instálalo una vez con: "
                       "bash ~/shelton-studio/scripts/lora_setup.sh")
    if not Path(settings.COMFY_LORA_DIR).expanduser().exists():
        return False, f"No encuentro la carpeta de LoRAs de ComfyUI ({settings.COMFY_LORA_DIR}). Ajusta COMFY_LORA_DIR."
    return True, ""


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return s[:24] or "modelo"


def trigger_for(sub: Subject) -> str:
    """Palabra clave única: así el modelo sabe cuándo dibujar a ESTE personaje."""
    return f"shl{_slug(sub.name).replace('_', '')}{sub.id[:4]}"


def dataset_images(subject_id: str) -> list[str]:
    """Fotos para entrenar: las de la ficha primero y después los posts aprobados/publicados donde aparece."""
    with session() as s:
        sub = s.get(Subject, subject_id)
        if not sub:
            raise ValueError("Ese modelo no existe")
        paths = [p for p in (sub.references or []) if Path(p).exists()]
        for p in (s.query(Post).filter(Post.status.in_(["approved", "published"]))
                  .order_by(Post.qa_score.desc(), Post.created_at.desc()).limit(400)):
            if subject_id in (p.subject_ids or []) and len(p.subject_ids or []) == 1 and p.image_path \
                    and Path(p.image_path).exists() and p.media_type == "image":
                paths.append(p.image_path)
    seen, out = set(), []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out[:MAX_IMAGES]


def status(subject_id: str) -> dict:
    ok, why = available()
    imgs = dataset_images(subject_id)
    with session() as s:
        sub = s.get(Subject, subject_id)
        data = subject_dict(sub)["lora"]
    return {**data, "trainer_ready": ok, "trainer_msg": why, "images": len(imgs), "min_images": MIN_IMAGES,
            "max_images": MAX_IMAGES, "default_steps": settings.LORA_STEPS, "saved_step": saved_step(subject_id),
            "sec_per_step": round(sec_per_step(subject_id), 2), "max_session_steps": MAX_SESSION_STEPS}


def _update(subject_id: str, **fields) -> None:
    with session() as s:
        sub = s.get(Subject, subject_id)
        if not sub:
            return
        info = dict(sub.lora_info or {})
        info.update(fields.pop("info", {}) or {})
        sub.lora_info = info
        for k, v in fields.items():
            setattr(sub, k, v)
        s.flush()
        data = subject_dict(sub)
    events.publish({"type": "subject", "subject": data})


def _prepare(subject_id: str, work: Path, trigger: str) -> int:
    """Copia las fotos (recortadas a 1024 px como máximo) con un texto simple por foto."""
    ds = work / "dataset"
    if ds.exists():
        shutil.rmtree(ds)
    ds.mkdir(parents=True)
    with session() as s:
        sub = s.get(Subject, subject_id)
        kind = {"person": "person", "car": "car", "vehicle": "vehicle", "place": "place", "product": "product",
                "pet": "pet", "creature": "creature"}.get(sub.kind, "subject")
    n = 0
    for i, src in enumerate(dataset_images(subject_id)):
        try:
            img = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
        except Exception:  # noqa: BLE001
            continue
        img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        img.save(ds / f"{i:03d}.jpg", "JPEG", quality=95)
        (ds / f"{i:03d}.txt").write_text(f"a photo of {trigger}, a {kind}", encoding="utf-8")
        n += 1
    return n


def _config(name: str, work: Path, trigger: str, steps: int) -> Path:
    """Configuración de ai-toolkit para Z-Image Turbo (la misma que usa su interfaz para este modelo)."""
    cfg = {"job": "extension", "config": {"name": name, "process": [{
        "type": "sd_trainer", "training_folder": str(work / "output"), "device": "cuda:0", "trigger_word": trigger,
        "network": {"type": "lora", "linear": 16, "linear_alpha": 16},
        "save": {"dtype": "float16", "save_every": 500, "max_step_saves_to_keep": 2},
        "datasets": [{"folder_path": str(work / "dataset"), "caption_ext": "txt", "caption_dropout_rate": 0.05,
                      "shuffle_tokens": False, "cache_latents_to_disk": True, "resolution": [512, 768, 1024]}],
        "train": {"batch_size": 1, "steps": int(steps), "gradient_accumulation": 1, "train_unet": True,
                  "train_text_encoder": False, "gradient_checkpointing": True, "noise_scheduler": "flowmatch",
                  "timestep_type": "weighted", "optimizer": "adamw8bit", "lr": 1e-4, "dtype": "bf16",
                  "unload_text_encoder": False, "disable_sampling": True},
        "model": {"name_or_path": "Tongyi-MAI/Z-Image-Turbo", "arch": "zimage:turbo", "quantize": True,
                  "qtype": "qfloat8", "quantize_te": True, "low_vram": True,
                  "assistant_lora_path": "ostris/zimage_turbo_training_adapter/zimage_turbo_training_adapter_v2.safetensors"},
        "sample": {"sampler": "flowmatch", "sample_every": 100000, "width": 1024, "height": 1024,
                   "prompts": [f"a photo of {trigger}"], "guidance_scale": 1, "sample_steps": 9},
    }]}, "meta": {"name": name, "version": "1.0"}}
    path = work / "config.json"
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return path


DEFAULT_SEC_PER_STEP = 2.5     # hasta medir la velocidad real de la placa
MAX_SESSION_STEPS = 15000       # tope de pasos por pedido (≈10 h en la 3090)


def job_name_for(subject_id: str, name: str) -> str:
    return f"shelton_{_slug(name)}_{subject_id[:6]}"


def _read_saved_step(path: Path) -> int:
    """Paso guardado dentro de un .safetensors de ai-toolkit (metadata training_info.step)."""
    try:
        with open(path, "rb") as f:
            n = int.from_bytes(f.read(8), "little")
            if n <= 0 or n > 50_000_000:
                return 0
            header = json.loads(f.read(n))
        info = (header.get("__metadata__") or {}).get("training_info")
        info = json.loads(info) if isinstance(info, str) else (info or {})
        return int(info.get("step") or 0)
    except Exception:  # noqa: BLE001
        return 0


def saved_step(subject_id: str) -> int:
    """Hasta qué paso llegó el entrenamiento guardado de este modelo (0 = nunca entrenó)."""
    with session() as s:
        sub = s.get(Subject, subject_id)
        if not sub:
            return 0
        name = sub.name
    folder = settings.DATA_DIR / "lora" / subject_id / "output" / job_name_for(subject_id, name)
    if not folder.exists():
        return 0
    files = [p for p in folder.glob("*.safetensors")]
    if not files:
        return 0
    return max(_read_saved_step(p) for p in files)


def sec_per_step(subject_id: str | None = None) -> float:
    """Velocidad real medida en esta placa (segundos por paso). Sirve para convertir «2 horas más» en pasos."""
    vals = []
    with session() as s:
        q = s.query(Subject)
        for sub in q:
            v = (sub.lora_info or {}).get("sec_per_step")
            if v:
                vals.append((sub.id == subject_id, float(v)))
    if not vals:
        return DEFAULT_SEC_PER_STEP
    mine = [v for is_me, v in vals if is_me]
    return mine[0] if mine else sorted(v for _, v in vals)[len(vals) // 2]


def plan(subject_id: str, mode: str = "auto", steps: int | None = None, minutes: float | None = None) -> dict:
    """Cuántos pasos hacer y cuánto tardará, antes de empezar. mode: continue | restart | auto."""
    done = saved_step(subject_id)
    mode = mode if mode in ("continue", "restart") else ("continue" if done > 0 else "restart")
    if mode == "continue" and done <= 0:
        mode = "restart"
    spp = sec_per_step(subject_id)
    if minutes:
        add = int(float(minutes) * 60 / spp)
    elif steps:
        add = int(steps)
    else:
        add = 1000 if mode == "continue" else settings.LORA_STEPS
    add = max(100, min(add, MAX_SESSION_STEPS))
    base = done if mode == "continue" else 0
    total = base + add
    return {"mode": mode, "from_step": base, "add_steps": add, "total_steps": total,
            "sec_per_step": round(spp, 2), "eta_min": round(add * spp / 60),
            "measured": spp != DEFAULT_SEC_PER_STEP}


def _archive_old_output(subject_id: str, job: str) -> None:
    """Empezar de cero sin borrar nada: el entrenamiento viejo se guarda aparte."""
    folder = settings.DATA_DIR / "lora" / subject_id / "output" / job
    if folder.exists() and any(folder.iterdir()):
        dest = folder.parent / f"{job}_anterior_{datetime.now():%Y%m%d_%H%M%S}"
        folder.rename(dest)


async def _free_comfy_vram() -> None:
    """Le pide a nuestro ComfyUI que suelte la GPU antes de entrenar."""
    import httpx
    for url in _comfy_urls():
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                await c.post(f"{url}/free", json={"unload_models": True, "free_memory": True})
        except Exception:  # noqa: BLE001
            pass


def _comfy_urls() -> list[str]:
    from app.connections import list_enabled
    urls = {settings.COMFYUI_URL}
    for c in list_enabled("engine"):
        if c.type == "comfyui" and c.config.get("url"):
            urls.add(c.config["url"].rstrip("/"))
    return sorted(urls)


async def _run(subject_id: str, steps: int, base: int = 0, mode: str = "restart") -> None:
    from app import working
    from app.engines import gpu_slot
    with session() as s:
        sub = s.get(Subject, subject_id)
        name_human, trigger = sub.name, sub.lora_trigger or trigger_for(sub)
    work = settings.DATA_DIR / "lora" / subject_id
    work.mkdir(parents=True, exist_ok=True)
    job_name = job_name_for(subject_id, name_human)
    if mode == "restart":
        await asyncio.to_thread(_archive_old_output, subject_id, job_name)
        base = 0
    n = await asyncio.to_thread(_prepare, subject_id, work, trigger)
    if n < MIN_IMAGES:
        _update(subject_id, lora_status="failed",
                info={"error": f"Hay {n} fotos y hacen falta al menos {MIN_IMAGES}. Agrega fotos a la ficha o aprueba posts con este modelo."})
        return
    cfg = _config(job_name, work, trigger, steps)
    t0 = time.time()
    _update(subject_id, lora_status="training", lora_progress=0, lora_trigger=trigger,
            info={"images": n, "steps": steps, "from_step": base, "mode": mode,
                  "started": datetime.now().isoformat(timespec="seconds"),
                  "phase": "Esperando la GPU", "error": "", "eta_min": None})
    logf = work / "train.log"
    async with AsyncExitStack() as stack:
        for url in _comfy_urls():                      # nuestras fotos locales esperan mientras entrena
            await stack.enter_async_context(gpu_slot(url))
        await _free_comfy_vram()
        _update(subject_id, info={"phase": "Preparando (la primera vez baja Z-Image para entrenar, ~20 GB)"})
        what = (f"siguiendo desde el paso {base} hasta el {steps}" if base else f"{steps} pasos")
        with working.track("lora_trainer", f"Entrenando la LoRA de {name_human} ({n} fotos, {what})", "ai-toolkit · Z-Image Turbo"):
            import os as _os
            env = {**_os.environ, "PYTHONUNBUFFERED": "1"}   # que escriba cada línea al momento (log y avance en vivo)
            proc = await asyncio.create_subprocess_exec(
                str(trainer_python()), "-u", "run.py", str(cfg), cwd=str(Path(settings.AITK_DIR).expanduser()),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env)
            logf.write_text(f"[Shelton] Entrenador iniciado (pid {proc.pid}). Primero baja Z-Image (~20 GB) la primera vez…\n",
                            encoding="utf-8")
            _procs[subject_id] = proc
            last_pub, tail = 0.0, []
            phase = ""
            first_seen: tuple[int, float] | None = None   # (paso, hora) del primer paso de ESTA sesión
            spp_now = None
            with open(logf, "a", encoding="utf-8") as lf:
                buf = b""
                while True:
                    chunk = await proc.stdout.read(4096)
                    if not chunk:
                        break
                    buf += chunk
                    parts = re.split(rb"[\r\n]", buf)
                    buf = parts.pop()
                    for raw in parts:
                        line = raw.decode(errors="ignore").strip()
                        if not line:
                            continue
                        lf.write(line + "\n")
                        lf.flush()
                        tail = (tail + [line])[-40:]
                        low = line.lower()
                        new_phase = ("Bajando Z-Image para entrenar (solo la primera vez)" if ("download" in low or "fetching" in low)
                                     else "Preparando las fotos" if "caching" in low or "latent" in low
                                     else "Cargando el modelo en la GPU" if ("loading" in low and "model" in low) or "quantiz" in low
                                     else "")
                        if new_phase and new_phase != phase and time.time() - last_pub > 3:
                            phase = new_phase
                            _update(subject_id, info={"phase": phase})
                            last_pub = time.time()
                        m = STEP_RE.search(line)
                        if m and int(m.group(2)) == steps:
                            done = int(m.group(1))
                            now_t = time.time()
                            if first_seen is None or done < first_seen[0]:
                                first_seen = (done, now_t)
                            start_at = first_seen[0]
                            session_total = max(1, steps - start_at)
                            pct = 100.0 * max(0, done - start_at) / session_total   # avance de ESTA sesión
                            did = done - start_at
                            if did >= 10:
                                spp_now = (now_t - first_seen[1]) / did
                            if now_t - last_pub > 5:
                                eta = round(spp_now * (steps - done) / 60) if spp_now else None
                                label = (f"Entrenando: paso {done} de {steps} (esta sesión: {did} de {session_total})"
                                         if start_at else f"Entrenando: paso {done} de {steps}")
                                _update(subject_id, lora_progress=pct,
                                        info={"phase": label, "eta_min": eta, "step": done,
                                              **({"sec_per_step": round(spp_now, 3)} if spp_now else {})})
                                last_pub = now_t
            rc = await proc.wait()
            _procs.pop(subject_id, None)
    if rc != 0:
        with session() as s:
            canceled = (s.get(Subject, subject_id).lora_status == "canceled")
        if canceled:
            return
        err = next((ln for ln in reversed(tail) if "Error" in ln or "error" in ln), tail[-1] if tail else "sin detalle")
        if "out of memory" in " ".join(tail).lower():
            err = ("La GPU se quedó sin memoria (otros programas la están usando). Prueba cuando esté más libre o "
                   "con menos pasos.")
        _update(subject_id, lora_status="failed", info={"error": err[-400:], "phase": "", "log": str(logf)})
        return
    outs = sorted((work / "output" / job_name).glob(f"{job_name}*.safetensors"), key=lambda p: p.stat().st_mtime)
    final = next((p for p in outs if p.stem == job_name), outs[-1] if outs else None)
    if not final:
        _update(subject_id, lora_status="failed", info={"error": "El entrenamiento terminó pero no encontré la LoRA", "phase": ""})
        return
    dest_name = f"{job_name}.safetensors"
    dest = Path(settings.COMFY_LORA_DIR).expanduser() / dest_name
    await asyncio.to_thread(shutil.copy2, final, dest)
    mins = round((time.time() - t0) / 60)
    _update(subject_id, lora_status="ready", lora_progress=100, lora_name=dest_name, lora_enabled=True,
            info={"phase": "", "finished": datetime.now().isoformat(timespec="seconds"), "minutes": mins, "eta_min": None,
                  "trained_steps": steps, "step": steps, **({"sec_per_step": round(spp_now, 3)} if spp_now else {})})
    events.publish({"type": "lora_ready", "subject_id": subject_id, "name": name_human, "minutes": mins})


_worker: asyncio.Task | None = None


def queue_ids() -> list[str]:
    """Modelos esperando su turno, en orden de llegada."""
    with session() as s:
        subs = s.query(Subject).filter(Subject.lora_status == "queued").all()
        subs.sort(key=lambda x: (x.lora_info or {}).get("queued_at", ""))
        return [x.id for x in subs]


def ensure_worker() -> None:
    """Arranca (si hace falta) el que entrena la fila: uno a la vez, uno detrás de otro."""
    global _worker
    if _worker is None or _worker.done():
        _worker = asyncio.create_task(_drain())


async def _drain() -> None:
    while True:
        ids = queue_ids()
        if not ids:
            return
        sid = ids[0]
        with session() as s:
            inf = s.get(Subject, sid).lora_info or {}
            steps = int(inf.get("steps") or settings.LORA_STEPS)
            base, mode = int(inf.get("from_step") or 0), inf.get("mode") or "restart"
        _tasks[sid] = asyncio.current_task()
        try:
            await _run(sid, steps, base, mode)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("LoRA %s", sid)
            _update(sid, lora_status="failed", info={"error": str(e)[:400], "phase": ""})
        with session() as s:   # si algo lo dejó en «queued» por error, no volver a tomarlo en bucle
            sub = s.get(Subject, sid)
            if sub and sub.lora_status in ("queued", "training"):
                sub.lora_status = "failed"
        _publish_queue()


def _publish_queue() -> None:
    """Actualiza en la web la posición de cada modelo en la fila."""
    for pos, sid in enumerate(queue_ids(), 1):
        _update(sid, info={"position": pos, "phase": f"En fila: #{pos}"})


def start(subject_id: str, steps: int | None = None, mode: str = "auto", minutes: float | None = None) -> dict:
    """Pone el modelo en la fila de entrenamiento (la GPU entrena uno a la vez; los demás esperan su turno).
    mode: continue = sigue desde lo ya aprendido y SUMA pasos · restart = empieza de cero (lo viejo se guarda aparte).
    steps/minutes = cuánto MÁS entrenar en esta sesión (minutes se convierte con la velocidad real medida)."""
    ok, why = available()
    if not ok:
        raise ValueError(why)
    with session() as s:
        sub = s.get(Subject, subject_id)
        if not sub:
            raise ValueError("Ese modelo no existe")
        if sub.lora_status in ("training", "queued"):
            raise ValueError("Ese modelo ya está entrenando o en la fila")
    n = len(dataset_images(subject_id))
    if n < MIN_IMAGES:
        raise ValueError(f"Hay {n} fotos de este modelo y hacen falta al menos {MIN_IMAGES} (mejor 15-30). "
                         "Agrega fotos a su ficha o aprueba posts donde aparezca.")
    p = plan(subject_id, mode, steps, minutes)
    busy = any(x != subject_id for x in queue_ids()) or (_worker is not None and not _worker.done())
    _update(subject_id, lora_status="queued", lora_progress=0,
            info={"steps": p["total_steps"], "from_step": p["from_step"], "mode": p["mode"], "images": n,
                  "queued_at": datetime.now().isoformat(timespec="seconds"), "error": "", "eta_min": p["eta_min"]})
    _publish_queue()
    ensure_worker()
    pos = queue_ids().index(subject_id) + 1 if subject_id in queue_ids() else 1
    return {"started": True, "queued": busy, "position": pos, "images": n, "steps": p["add_steps"], **p}


def cancel(subject_id: str) -> None:
    proc = _procs.get(subject_id)
    _update(subject_id, lora_status="canceled", info={"phase": "", "error": "Cancelado por una persona"})
    if proc and proc.returncode is None:
        proc.terminate()
    _publish_queue()


def recover_after_restart() -> None:
    """Si Shelton se reinició en medio de un entrenamiento, lo marca como interrumpido (se puede relanzar)."""
    with session() as s:
        for sub in s.query(Subject).filter(Subject.lora_status == "training"):
            sub.lora_status = "failed"
            sub.lora_info = {**(sub.lora_info or {}), "error": "Se interrumpió (Shelton se reinició). Vuelve a entrenar.",
                             "phase": ""}
    # La fila sigue: los que esperaban su turno se entrenan solos al volver a arrancar


def loras_for(subjects: list[Subject]) -> list[tuple[str, str]]:
    """LoRAs listas y activas de los modelos de un post: [(archivo, palabra clave)]. Vacío si alguno no tiene."""
    out = []
    for s in subjects:
        if s.lora_status == "ready" and s.lora_name and s.lora_enabled is not False:
            out.append((s.lora_name, s.lora_trigger))
        else:
            return []
    return out
