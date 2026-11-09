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
            "max_images": MAX_IMAGES, "default_steps": settings.LORA_STEPS}


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


async def _run(subject_id: str, steps: int) -> None:
    from app import working
    from app.engines import gpu_slot
    with session() as s:
        sub = s.get(Subject, subject_id)
        name_human, trigger = sub.name, sub.lora_trigger or trigger_for(sub)
    work = settings.DATA_DIR / "lora" / subject_id
    work.mkdir(parents=True, exist_ok=True)
    job_name = f"shelton_{_slug(name_human)}_{subject_id[:6]}"
    n = await asyncio.to_thread(_prepare, subject_id, work, trigger)
    if n < MIN_IMAGES:
        _update(subject_id, lora_status="failed",
                info={"error": f"Hay {n} fotos y hacen falta al menos {MIN_IMAGES}. Agrega fotos a la ficha o aprueba posts con este modelo."})
        return
    cfg = _config(job_name, work, trigger, steps)
    t0 = time.time()
    _update(subject_id, lora_status="training", lora_progress=0, lora_trigger=trigger,
            info={"images": n, "steps": steps, "started": datetime.now().isoformat(timespec="seconds"),
                  "phase": "Esperando la GPU", "error": "", "eta_min": None})
    logf = work / "train.log"
    async with AsyncExitStack() as stack:
        for url in _comfy_urls():                      # nuestras fotos locales esperan mientras entrena
            await stack.enter_async_context(gpu_slot(url))
        await _free_comfy_vram()
        _update(subject_id, info={"phase": "Preparando (la primera vez baja Z-Image para entrenar, ~20 GB)"})
        with working.track("lora_trainer", f"Entrenando la LoRA de {name_human} ({n} fotos, {steps} pasos)", "ai-toolkit · Z-Image Turbo"):
            proc = await asyncio.create_subprocess_exec(
                str(trainer_python()), "run.py", str(cfg), cwd=str(Path(settings.AITK_DIR).expanduser()),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            _procs[subject_id] = proc
            last_pub, tail = 0.0, []
            with open(logf, "w", encoding="utf-8") as lf:
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
                        tail = (tail + [line])[-40:]
                        m = STEP_RE.search(line)
                        if m and int(m.group(2)) == steps:
                            done = int(m.group(1))
                            pct = 100.0 * done / max(1, steps)
                            if time.time() - last_pub > 5:
                                el = time.time() - t0
                                eta = round(el / max(done, 1) * (steps - done) / 60) if done > 20 else None
                                _update(subject_id, lora_progress=pct, info={"phase": f"Entrenando: paso {done} de {steps}",
                                                                              "eta_min": eta})
                                last_pub = time.time()
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
            info={"phase": "", "finished": datetime.now().isoformat(timespec="seconds"), "minutes": mins, "eta_min": None})
    events.publish({"type": "lora_ready", "subject_id": subject_id, "name": name_human, "minutes": mins})


def start(subject_id: str, steps: int | None = None) -> dict:
    ok, why = available()
    if not ok:
        raise ValueError(why)
    if subject_id in _tasks and not _tasks[subject_id].done():
        raise ValueError("Ese modelo ya se está entrenando")
    if any(not t.done() for t in _tasks.values()):
        raise ValueError("Ya hay otro modelo entrenándose: la GPU entrena uno a la vez. Espera a que termine.")
    n = len(dataset_images(subject_id))
    if n < MIN_IMAGES:
        raise ValueError(f"Hay {n} fotos de este modelo y hacen falta al menos {MIN_IMAGES} (mejor 15-30). "
                         "Agrega fotos a su ficha o aprueba posts donde aparezca.")
    steps = max(300, min(int(steps or settings.LORA_STEPS), 6000))

    async def runner():
        try:
            await _run(subject_id, steps)
        except Exception as e:  # noqa: BLE001
            log.exception("LoRA %s", subject_id)
            _update(subject_id, lora_status="failed", info={"error": str(e)[:400], "phase": ""})
    _tasks[subject_id] = asyncio.create_task(runner())
    return {"started": True, "images": n, "steps": steps}


def cancel(subject_id: str) -> None:
    proc = _procs.get(subject_id)
    _update(subject_id, lora_status="canceled", info={"phase": "", "error": "Cancelado por una persona"})
    if proc and proc.returncode is None:
        proc.terminate()


def recover_after_restart() -> None:
    """Si Shelton se reinició en medio de un entrenamiento, lo marca como interrumpido (se puede relanzar)."""
    with session() as s:
        for sub in s.query(Subject).filter(Subject.lora_status == "training"):
            sub.lora_status = "failed"
            sub.lora_info = {**(sub.lora_info or {}), "error": "Se interrumpió (Shelton se reinició). Vuelve a entrenar.",
                             "phase": ""}


def loras_for(subjects: list[Subject]) -> list[tuple[str, str]]:
    """LoRAs listas y activas de los modelos de un post: [(archivo, palabra clave)]. Vacío si alguno no tiene."""
    out = []
    for s in subjects:
        if s.lora_status == "ready" and s.lora_name and s.lora_enabled is not False:
            out.append((s.lora_name, s.lora_trigger))
        else:
            return []
    return out
