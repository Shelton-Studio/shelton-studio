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
    return out


def _studied_file(subject_id: str) -> Path:
    return settings.DATA_DIR / "lora" / subject_id / "fotos_estudiadas.json"


def studied_counts(subject_id: str) -> dict:
    """Cuántas veces estudió la LoRA cada foto (para que cada sesión aprenda de fotos nuevas)."""
    f = _studied_file(subject_id)
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except Exception:  # noqa: BLE001
        return {}


def session_images(subject_id: str) -> list[str]:
    """Las fotos de esta sesión: primero las que la LoRA todavía no estudió (o estudió menos veces).
    Así la misma LoRA va sumando material nuevo cada vez, en vez de repetir siempre las mismas 30."""
    allp = dataset_images(subject_id)
    seen = studied_counts(subject_id)
    order = sorted(range(len(allp)), key=lambda i: (seen.get(allp[i], 0), i))
    return [allp[i] for i in order[:MAX_IMAGES]]


def _mark_studied(subject_id: str, paths: list[str]) -> None:
    seen = studied_counts(subject_id)
    for p in paths:
        seen[p] = seen.get(p, 0) + 1
    f = _studied_file(subject_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(seen, ensure_ascii=False, indent=1), encoding="utf-8")


def status(subject_id: str) -> dict:
    ok, why = available()
    imgs = dataset_images(subject_id)
    with session() as s:
        sub = s.get(Subject, subject_id)
        data = subject_dict(sub)["lora"]
    seen = studied_counts(subject_id)
    return {**data, "trainer_ready": ok, "trainer_msg": why, "images": len(imgs), "min_images": MIN_IMAGES,
            "images_studied": sum(1 for p in imgs if seen.get(p)), "images_new": sum(1 for p in imgs if not seen.get(p)),
            "max_images": MAX_IMAGES, "default_steps": settings.LORA_STEPS, "saved_step": saved_step(subject_id),
            "sec_per_step": round(sec_per_step(subject_id), 2), "max_session_steps": MAX_SESSION_STEPS,
            "versions": versions(subject_id)}


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
    used = []
    for i, src in enumerate(session_images(subject_id)):
        try:
            img = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
        except Exception:  # noqa: BLE001
            continue
        img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        img.save(ds / f"{i:03d}.jpg", "JPEG", quality=95)
        (ds / f"{i:03d}.txt").write_text(f"a photo of {trigger}, a {kind}", encoding="utf-8")
        used.append(src)
        n += 1
    (work / "fotos_sesion.json").write_text(json.dumps(used, ensure_ascii=False), encoding="utf-8")
    return n


def _config(name: str, work: Path, trigger: str, steps: int) -> Path:
    """Configuración de ai-toolkit para Z-Image Turbo (la misma que usa su interfaz para este modelo)."""
    cfg = {"job": "extension", "config": {"name": name, "process": [{
        "type": "sd_trainer", "training_folder": str(work / "output"), "device": "cuda:0", "trigger_word": trigger,
        "network": {"type": "lora", "linear": 16, "linear_alpha": 16},
        "save": {"dtype": "float16", "save_every": 500, "max_step_saves_to_keep": 4},
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


def _work(subject_id: str) -> Path:
    return settings.DATA_DIR / "lora" / subject_id


def _live_folders(subject_id: str) -> list[tuple[int, Path]]:
    """Carpetas de entrenamiento en uso (no las archivadas al «empezar de cero»), con su paso más alto."""
    out = _work(subject_id) / "output"
    if not out.exists():
        return []
    res = []
    for d in out.iterdir():
        if d.is_dir() and "_anterior_" not in d.name:
            steps = [_read_saved_step(f) for f in d.glob("*.safetensors")]
            if steps:
                res.append((max(steps), d))
    return sorted(res, key=lambda x: -x[0])


def job_for(subject_id: str, name: str | None = None) -> str:
    """Nombre de la carpeta/archivo de la LoRA de este modelo. Se fija la primera vez y no cambia aunque
    le cambien el nombre al modelo (antes, renombrarlo hacía que empezara de cero en otra carpeta)."""
    with session() as s:
        sub = s.get(Subject, subject_id)
        info = dict(sub.lora_info or {}) if sub else {}
        name = name or (sub.name if sub else "")
    if info.get("job"):
        return info["job"]
    live = _live_folders(subject_id)
    if live:
        return live[0][1].name          # la que más aprendió
    return job_name_for(subject_id, name)


def best_checkpoint(subject_id: str) -> tuple[int, Path | None]:
    """Desde dónde sigue aprendiendo este modelo:
    1) la versión que eligieron con «Usar esta» (si eligieron una);
    2) si no, la carpeta en uso que más aprendió;
    3) si no hay ninguna carpeta en uso (y no pidieron empezar de cero), el respaldo más avanzado."""
    with session() as s:
        sub = s.get(Subject, subject_id)
        info = dict(sub.lora_info or {}) if sub else {}
    vdir = _work(subject_id) / "versiones"
    if info.get("resume_from"):
        f = vdir / Path(info["resume_from"]).name
        if f.exists():
            return _read_saved_step(f), f
    live = _live_folders(subject_id)
    if live:
        step, d = live[0]
        return step, max(d.glob("*.safetensors"), key=_read_saved_step)
    if info.get("mode") == "restart" or not vdir.exists():
        return 0, None
    best: tuple[int, Path | None] = (0, None)
    for f in vdir.glob("*.safetensors"):
        st = _read_saved_step(f)
        if st > best[0]:
            best = (st, f)
    return best


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
    # Lo que de verdad va a retomar el entrenador al «seguir» (ver best_checkpoint): nunca vuelve a cero solo
    return best_checkpoint(subject_id)[0]


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


def versions(subject_id: str) -> list[dict]:
    """Todas las versiones terminadas de la LoRA de un modelo (la más nueva primero)."""
    vdir = settings.DATA_DIR / "lora" / subject_id / "versiones"
    if not vdir.exists():
        return []
    out = []
    for f in sorted(vdir.glob("*.safetensors"), key=lambda p: p.stat().st_mtime, reverse=True):
        out.append({"file": f.name, "steps": _read_saved_step(f) or None,
                    "date": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="minutes")})
    return out


def use_version(subject_id: str, file: str) -> dict:
    """Vuelve a una versión anterior: la copia a ComfyUI como la LoRA activa (las demás siguen guardadas)."""
    vfile = settings.DATA_DIR / "lora" / subject_id / "versiones" / Path(file).name
    if not vfile.exists():
        raise ValueError("Esa versión no existe")
    with session() as s:
        sub = s.get(Subject, subject_id)
        sub_name = sub.name
    job = job_for(subject_id, sub_name)
    dest_name = f"{job}.safetensors"
    shutil.copy2(vfile, Path(settings.COMFY_LORA_DIR).expanduser() / dest_name)
    _update(subject_id, lora_status="ready", lora_name=dest_name, lora_enabled=True,
            info={"active_version": vfile.name, "resume_from": vfile.name, "job": job, "phase": "", "error": ""})
    return {"ok": True, "active_version": vfile.name, "resume_step": _read_saved_step(vfile)}


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


def _snapshot_before(subject_id: str, job: str) -> str | None:
    """Antes de entrenar más, guarda la LoRA que hay ahora como versión (si todavía no estaba guardada).
    Así, aunque la sesión nueva saliera peor, la que ya funcionaba nunca se pierde."""
    work = settings.DATA_DIR / "lora" / subject_id
    candidates = [Path(settings.COMFY_LORA_DIR).expanduser() / f"{job}.safetensors",
                  work / "output" / job / f"{job}.safetensors"]
    src = next((c for c in candidates if c.exists()), None)
    if not src:
        return None
    step = _read_saved_step(src)
    vdir = work / "versiones"
    vdir.mkdir(parents=True, exist_ok=True)
    if step and any(f"_{step}pasos_" in f.name for f in vdir.glob("*.safetensors")):
        return None                                  # esa ya está guardada
    name = f"{job}_{step or 'sin-dato'}pasos_{datetime.now():%Y%m%d_%H%M}_antes.safetensors"
    shutil.copy2(src, vdir / name)
    return name


def _seed_from_best(subject_id: str, job: str) -> None:
    """Deja en la carpeta de entrenamiento el punto desde el que hay que seguir (ver best_checkpoint).
    Si eligieron una versión, lo que había en la carpeta se archiva aparte (no se borra) y se sigue desde esa."""
    step, src = best_checkpoint(subject_id)
    folder = _work(subject_id) / "output" / job
    if not src or src.parent == folder:
        return
    with session() as s:
        chosen = bool((s.get(Subject, subject_id).lora_info or {}).get("resume_from"))
    here = max((_read_saved_step(f) for f in folder.glob("*.safetensors")), default=0) if folder.exists() else 0
    if chosen and here:
        _archive_old_output(subject_id, job)
    elif here >= step:
        return
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, folder / f"{job}.safetensors")
    _update(subject_id, info={"resume_from": ""})
    log.info("LoRA %s: sigo desde el paso %s (%s)", job, step, src)


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
    job_name = job_for(subject_id, name_human)
    _update(subject_id, info={"job": job_name})
    if mode == "continue":
        await asyncio.to_thread(_seed_from_best, subject_id, job_name)
    try:
        await asyncio.to_thread(_snapshot_before, subject_id, job_name)
    except Exception:  # noqa: BLE001
        log.warning("No pude guardar la LoRA anterior como versión", exc_info=True)
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
    # Cada entrenamiento terminado queda guardado como versión: lo aprendido nunca se pierde
    vdir = work / "versiones"
    vdir.mkdir(exist_ok=True)
    vname = f"{job_name}_{steps}pasos_{datetime.now():%Y%m%d_%H%M}.safetensors"
    await asyncio.to_thread(shutil.copy2, final, vdir / vname)
    # Las fotos de esta sesión quedan marcadas como estudiadas: la próxima vez aprende de las que faltan
    try:
        _mark_studied(subject_id, json.loads((work / "fotos_sesion.json").read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        pass
    mins = round((time.time() - t0) / 60)
    try:
        from app.consumo import record
        record("lora", "ai-toolkit", "Z-Image Turbo", seconds=time.time() - t0, agent="lora_trainer",
               note=f"LoRA de {name_human}: {base} → {steps} pasos ({n} fotos)")
    except Exception:  # noqa: BLE001
        log.warning("No pude registrar el consumo del entrenamiento", exc_info=True)
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
