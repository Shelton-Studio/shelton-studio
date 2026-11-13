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
import os
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
LOG_POLL = 2.0                  # cada cuántos segundos se lee el avance del entrenador
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
    try:   # fotos privadas (Adult Zone), solo si la modelo está confirmada y eligieron usarlas al entrenar
        from app import privado
        paths += privado.private_paths(subject_id)
    except Exception:  # noqa: BLE001
        log.warning("No pude leer las fotos privadas", exc_info=True)
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


def _prepare(subject_id: str, work: Path, trigger: str, captions: dict | None = None, max_side: int = 1024) -> int:
    """Copia las fotos (recortadas a max_side px) con su texto: el simple de siempre o, si hay, una descripción
    detallada de la escena (pose, ropa, luz, fondo) para que la LoRA aprenda a la persona y no la ropa ni el fondo."""
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
        img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        img.save(ds / f"{i:03d}.jpg", "JPEG", quality=95)
        extra = (captions or {}).get(src, "").strip()
        (ds / f"{i:03d}.txt").write_text(f"a photo of {trigger}, a {kind}" + (f", {extra}" if extra else ""),
                                         encoding="utf-8")
        used.append(src)
        n += 1
    (work / "fotos_sesion.json").write_text(json.dumps(used, ensure_ascii=False), encoding="utf-8")
    return n


PROFILES = {
    # 3090 (24 GB): comprimido y en modo de poca memoria, como siempre
    "estandar": {"rank": 16, "resolution": [512, 768, 1024], "quantize": True, "low_vram": True, "max_side": 1024,
                 "keep": 4, "label": "Estándar (24 GB)"},
    # Tarjetas grandes (32 GB o más): LoRA más detallada, más resolución y el modelo sin comprimir
    "pro": {"rank": 32, "resolution": [768, 1024, 1280], "quantize": False, "low_vram": False, "max_side": 1536,
            "keep": 6, "label": "Pro (32 GB o más)"},
}


BASES = {"zimage": "Z-Image Turbo", "sdxl": "SDXL", "flux": "Flux.1 Dev", "qwen": "Qwen-Image (Profesional)"}
BIG_BASES = {"qwen"}          # se entrenan solo en tarjetas grandes (perfil «pro», p. ej. la RTX PRO 6000)


def base_of(subject_id: str) -> str:
    """Modelo base de la LoRA de este modelo del banco (zimage por defecto; las «versiones» SDXL/Flux lo cambian)."""
    with session() as s:
        sub = s.get(Subject, subject_id)
        b = ((sub.lora_info or {}).get("base") if sub else None) or "zimage"
    return b if b in BASES else "zimage"


def _arch_parts(base: str, pf: dict, trigger: str) -> tuple[dict, dict, dict]:
    """(modelo, ajustes de entrenamiento, muestras) de ai-toolkit para cada modelo base."""
    if base == "sdxl":
        ckpt = os.getenv("SDXL_TRAIN_MODEL") or str(Path(settings.COMFY_LORA_DIR).expanduser().parent / "checkpoints"
                                                     / os.getenv("SDXL_CKPT", "RealVisXL_V5.0_fp16.safetensors"))
        return ({"name_or_path": ckpt, "is_xl": True, "quantize": False},
                {"noise_scheduler": "ddpm", "lr": 1e-4},
                {"sampler": "ddpm", "sample_every": 100000, "width": 1024, "height": 1024,
                 "prompts": [f"a photo of {trigger}"], "guidance_scale": 5, "sample_steps": 25})
    if base == "qwen":
        # Qwen-Image 20B (licencia Apache 2.0: uso comercial). En la tarjeta grande va sin comprimir el modelo;
        # en una de 24 GB usaría la versión comprimida con su adaptador de precisión.
        big = not pf["quantize"]
        model = {"name_or_path": os.getenv("QWEN_TRAIN_MODEL", "Qwen/Qwen-Image"), "arch": "qwen_image",
                 "quantize": not big, "quantize_te": True, "qtype_te": "qfloat8", "low_vram": not big}
        if not big:
            model["qtype"] = "uint3|ostris/accuracy_recovery_adapters/qwen_image_torchao_uint3.safetensors"
        return (model,
                {"noise_scheduler": "flowmatch", "lr": 1e-4, "cache_text_embeddings": True},
                {"sampler": "flowmatch", "sample_every": 100000, "width": 1024, "height": 1024,
                 "prompts": [f"a photo of {trigger}"], "guidance_scale": 3, "sample_steps": 25})
    if base == "flux":
        return ({"name_or_path": os.getenv("FLUX_TRAIN_MODEL", "black-forest-labs/FLUX.1-dev"), "is_flux": True,
                 "quantize": pf["quantize"] or pf["low_vram"], "low_vram": pf["low_vram"]},
                {"noise_scheduler": "flowmatch", "lr": 1e-4},
                {"sampler": "flowmatch", "sample_every": 100000, "width": 1024, "height": 1024,
                 "prompts": [f"a photo of {trigger}"], "guidance_scale": 4, "sample_steps": 20})
    return ({"name_or_path": "Tongyi-MAI/Z-Image-Turbo", "arch": "zimage:turbo", "quantize": pf["quantize"],
             "qtype": "qfloat8", "quantize_te": pf["quantize"], "low_vram": pf["low_vram"],
             "assistant_lora_path": "ostris/zimage_turbo_training_adapter/zimage_turbo_training_adapter_v2.safetensors"},
            {"noise_scheduler": "flowmatch", "timestep_type": "weighted", "lr": 1e-4},
            {"sampler": "flowmatch", "sample_every": 100000, "width": 1024, "height": 1024,
             "prompts": [f"a photo of {trigger}"], "guidance_scale": 1, "sample_steps": 9})


def config_dict(name: str, root: str, trigger: str, steps: int, profile: str = "estandar", base: str = "zimage") -> dict:
    """Configuración de ai-toolkit. root = carpeta de trabajo (local o la del nodo remoto).
    base: zimage (por defecto, la de siempre), sdxl o flux (las «versiones» de un modelo para esos motores)."""
    pf = PROFILES.get(profile, PROFILES["estandar"])
    model, train_extra, sample = _arch_parts(base, pf, trigger)
    return {"job": "extension", "config": {"name": name, "process": [{
        "type": "sd_trainer", "training_folder": f"{root}/output", "device": "cuda:0", "trigger_word": trigger,
        "network": {"type": "lora", "linear": pf["rank"], "linear_alpha": pf["rank"]},
        "save": {"dtype": "float16", "save_every": 500, "max_step_saves_to_keep": pf["keep"]},
        "datasets": [{"folder_path": f"{root}/dataset", "caption_ext": "txt", "caption_dropout_rate": 0.05,
                      "shuffle_tokens": False, "cache_latents_to_disk": True, "resolution": pf["resolution"]}],
        "train": {"batch_size": 1, "steps": int(steps), "gradient_accumulation": 1, "train_unet": True,
                  "train_text_encoder": False, "gradient_checkpointing": True, "optimizer": "adamw8bit",
                  "dtype": "bf16", "unload_text_encoder": False, "disable_sampling": True, **train_extra},
        "model": model,
        "sample": sample,
    }]}, "meta": {"name": name, "version": "1.0", "base": base}}


def _config(name: str, work: Path, trigger: str, steps: int, profile: str = "estandar", base: str = "zimage") -> Path:
    cfg = config_dict(name, str(work), trigger, steps, profile, base)
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


async def _pre(subject_id: str, mode: str, base: int, profile: str = "estandar") -> dict | None:
    """Todo lo que se hace antes de entrenar (igual en la placa que en una tarjeta alquilada)."""
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
    captions = None
    if profile == "pro" or _detailed_captions_on():
        try:
            _update(subject_id, info={"phase": "Describiendo cada foto (pose, ropa, luz, fondo)"})
            captions = await caption_photos(subject_id, trigger)
        except Exception:  # noqa: BLE001
            log.warning("No pude describir las fotos: sigo con el texto simple", exc_info=True)
    pf = PROFILES.get(profile, PROFILES["estandar"])
    n = await asyncio.to_thread(_prepare, subject_id, work, trigger, captions, pf["max_side"])
    if n < MIN_IMAGES:
        _update(subject_id, lora_status="failed",
                info={"error": f"Hay {n} fotos y hacen falta al menos {MIN_IMAGES}. Agrega fotos a la ficha o aprueba posts con este modelo."})
        return None
    return {"name": name_human, "trigger": trigger, "work": work, "job": job_name, "base": base, "n": n}


async def _run(subject_id: str, steps: int, base: int = 0, mode: str = "restart", gpu: str | None = None) -> None:
    ctx = await _pre(subject_id, mode, base, profile_for_gpu(gpu))
    if not ctx:
        return
    cfg = _config(ctx["job"], ctx["work"], ctx["trigger"], steps, profile_for_gpu(gpu), base_of(subject_id))
    t0 = time.time()
    _update(subject_id, lora_status="training", lora_progress=0, lora_trigger=ctx["trigger"],
            info={"images": ctx["n"], "steps": steps, "from_step": ctx["base"], "mode": mode, "requeued": False,
                  "started": datetime.now().isoformat(timespec="seconds"),
                  "phase": "Esperando la GPU", "error": "", "eta_min": None})
    await _train_local(subject_id, ctx, steps, t0, gpu, cfg=cfg)


# ------------------------------------------------------------------ entrenador independiente (sobrevive reinicios)
# El entrenador corre por su cuenta (en su propia unidad de systemd si existe), escribe su avance en train.log y su
# código de salida en un archivo. Si Shelton se reinicia (por ejemplo al actualizar), al volver lo encuentra y se
# reconecta: el entrenamiento NO se corta.
def _state_file(subject_id: str) -> Path:
    return _work(subject_id) / "entrenando.json"


def _read_state(subject_id: str) -> dict | None:
    f = _state_file(subject_id)
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
    except Exception:  # noqa: BLE001
        return None


def _use_systemd() -> bool:
    return (os.getenv("LORA_DETACH", "auto") != "process" and Path("/run/systemd/system").exists()
            and shutil.which("systemd-run") is not None and hasattr(os, "geteuid") and os.geteuid() == 0)


def _launch(subject_id: str, ctx: dict, cfg: Path, steps: int, gpu: str | None, t0: float) -> dict:
    """Arranca el entrenador separado de Shelton y guarda cómo encontrarlo."""
    import shlex
    import subprocess
    work = ctx["work"]
    logf, rcf = work / "train.log", work / "train.rc"
    rcf.unlink(missing_ok=True)
    logf.write_text("[Shelton] Entrenador iniciado. Primero baja Z-Image (~20 GB) la primera vez…\n", encoding="utf-8")
    offset = logf.stat().st_size
    env = {"PYTHONUNBUFFERED": "1"}
    if gpu is not None and len(local_gpus()) > 1:
        env["CUDA_VISIBLE_DEVICES"] = str(gpu)          # varias GPU en la placa: cada entrenamiento en la suya
    for k in ("HF_HOME", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HOME", "PATH", "CUDA_VISIBLE_DEVICES"):
        if k in os.environ and k not in env:
            env[k] = os.environ[k]
    aitk = str(Path(settings.AITK_DIR).expanduser())
    script = (f"{shlex.quote(str(trainer_python()))} -u run.py {shlex.quote(str(cfg))} >> {shlex.quote(str(logf))} 2>&1; "
              f"echo $? > {shlex.quote(str(rcf))}")
    state = {"job": ctx["job"], "steps": steps, "base": ctx["base"], "n": ctx["n"], "name": ctx["name"],
             "trigger": ctx["trigger"], "gpu": gpu, "t0": t0, "log": str(logf), "rc": str(rcf), "offset": offset}
    if _use_systemd():
        unit = f"shelton-lora-{subject_id[:8]}-{int(t0)}"
        cmd = ["systemd-run", f"--unit={unit}", "--collect", "--quiet", f"--working-directory={aitk}",
               *[f"--setenv={k}={v}" for k, v in env.items()], "/bin/sh", "-c", script]
        subprocess.run(cmd, check=True, timeout=30)
        state["unit"] = unit
    else:
        p = subprocess.Popen(["/bin/sh", "-c", script], cwd=aitk, env={**os.environ, **env}, start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        state["pid"] = p.pid
    _state_file(subject_id).write_text(json.dumps(state), encoding="utf-8")
    return state


def _alive(state: dict) -> bool:
    import subprocess
    if state.get("unit"):
        r = subprocess.run(["systemctl", "is-active", "--quiet", state["unit"]], timeout=10)
        return r.returncode == 0
    pid = state.get("pid")
    if not pid:
        return False
    try:
        os.waitpid(pid, os.WNOHANG)      # si es hijo nuestro y ya terminó, que no quede «zombi»
    except ChildProcessError:
        pass
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill(state: dict) -> None:
    import signal
    import subprocess
    if state.get("unit"):
        subprocess.run(["systemctl", "stop", state["unit"]], timeout=30)
    elif state.get("pid"):
        try:
            os.killpg(state["pid"], signal.SIGTERM)
        except OSError:
            pass


async def _train_local(subject_id: str, ctx: dict, steps: int, t0: float, gpu: str | None,
                       cfg: Path | None = None, state: dict | None = None) -> None:
    """Entrena (o se reconecta a un entrenamiento que ya corría) y lo cierra al terminar."""
    from app import working
    from app.engines import gpu_slot
    name_human, n, base = ctx["name"], ctx["n"], ctx["base"]
    async with AsyncExitStack() as stack:
        for url in _comfy_urls_for_gpu(gpu):           # nuestras fotos locales esperan mientras entrena (en esa GPU)
            await stack.enter_async_context(gpu_slot(url))
        if state is None:
            await _free_comfy_vram()
            _update(subject_id, info={"phase": "Preparando (la primera vez baja Z-Image para entrenar, ~20 GB)"})
            state = await asyncio.to_thread(_launch, subject_id, ctx, cfg, steps, gpu, t0)
        what = (f"siguiendo desde el paso {base} hasta el {steps}" if base else f"{steps} pasos")
        with working.track("lora_trainer", f"Entrenando la LoRA de {name_human} ({n} fotos, {what})", "ai-toolkit · Z-Image Turbo"):
            rc, tail, spp_now = await _follow(subject_id, state, steps)
    _state_file(subject_id).unlink(missing_ok=True)
    with session() as s:
        canceled = (s.get(Subject, subject_id).lora_status == "canceled")
    if canceled:
        return
    if rc is None and auto_resume(subject_id, "el entrenador se detuvo sin terminar"):
        return
    if rc != 0:
        if rc is None:
            err = "El entrenador se cortó varias veces seguidas. Revisa la placa y vuelve a entrenar: sigue desde lo último guardado."
        else:
            err = next((ln for ln in reversed(tail) if "Error" in ln or "error" in ln), tail[-1] if tail else "sin detalle")
            if "out of memory" in " ".join(tail).lower():
                err = ("La GPU se quedó sin memoria (otros programas la están usando). Prueba cuando esté más libre o "
                       "con menos pasos.")
        _update(subject_id, lora_status="failed", info={"error": err[-400:], "phase": "", "log": state["log"]})
        return
    job_name, work = ctx["job"], ctx["work"]
    outs = sorted((work / "output" / job_name).glob(f"{job_name}*.safetensors"), key=lambda p: p.stat().st_mtime)
    final = next((p for p in outs if p.stem == job_name), outs[-1] if outs else None)
    await _finish(subject_id, ctx, steps, final, t0, spp_now)


async def _follow(subject_id: str, state: dict, steps: int) -> tuple[int | None, list[str], float | None]:
    """Lee el avance del archivo de log mientras el entrenador trabaja. Devuelve (código de salida, últimas líneas,
    segundos por paso). Si Shelton se detiene a mitad, el entrenador sigue igual."""
    logf, rcf = Path(state["log"]), Path(state["rc"])
    pos = int(state.get("offset") or 0)
    last_pub, tail, phase = 0.0, [], ""
    first_seen: tuple[int, float] | None = None   # (paso, hora) del primer paso visto en esta conexión
    spp_now = None
    buf = b""
    dead_checks = 0
    catching_up = True       # la primera lectura puede traer horas de log (al reconectarse): no sirve para medir tiempo
    spp_hint = sec_per_step(subject_id)     # velocidad ya medida en esta placa, para el tiempo restante mientras tanto
    while True:
        chunk = b""
        try:
            with open(logf, "rb") as f:
                f.seek(pos)
                chunk = f.read()
        except OSError:
            pass
        pos += len(chunk)
        buf += chunk
        parts = re.split(rb"[\r\n]", buf)
        buf = parts.pop()
        for raw in parts:
            line = raw.decode(errors="ignore").strip()
            if not line:
                continue
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
                if not catching_up and (first_seen is None or done < first_seen[0]):
                    first_seen = (done, now_t)
                start_at = int(state.get("base") or 0) or (first_seen[0] if first_seen else done)
                session_total = max(1, steps - start_at)
                pct = 100.0 * max(0, done - start_at) / session_total   # avance de ESTA sesión
                did = done - first_seen[0] if first_seen else 0
                if did >= 10:
                    spp_now = (now_t - first_seen[1]) / did
                if now_t - last_pub > 5 or catching_up:
                    rate = spp_now or spp_hint
                    eta = round(rate * (steps - done) / 60) if rate else None
                    label = (f"Entrenando: paso {done} de {steps} (esta sesión: {done - start_at} de {session_total})"
                             if start_at else f"Entrenando: paso {done} de {steps}")
                    _update(subject_id, lora_progress=min(99.9, pct),
                            info={"phase": label, "eta_min": eta, "step": done,
                                  **({"sec_per_step": round(spp_now, 3)} if spp_now else {})})
                    last_pub = now_t
        catching_up = False
        if rcf.exists():
            try:
                return int(rcf.read_text().strip() or 1), tail, spp_now
            except ValueError:
                return 1, tail, spp_now
        if not chunk:
            if await asyncio.to_thread(_alive, state):
                dead_checks = 0
            else:
                dead_checks += 1
                if dead_checks >= 3 and not rcf.exists():   # se cortó sin dejar código (apagón, lo mataron)
                    return None, tail, spp_now
        await asyncio.sleep(LOG_POLL)


async def _finish(subject_id: str, ctx: dict, steps: int, final: Path | None, t0: float, spp_now: float | None,
                  provider: str = "ai-toolkit", cost_usd: float | None = None, where: str = "") -> None:
    """Todo lo que se hace al terminar (igual en la placa que en una tarjeta alquilada): dejarla activa, respaldo,
    fotos estudiadas, consumo y aviso. Además la copia a las demás tarjetas que generan."""
    work, job_name, name_human, base, n = ctx["work"], ctx["job"], ctx["name"], ctx["base"], ctx["n"]
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
        record("lora", provider, BASES.get(base_of(subject_id), "Z-Image Turbo"), seconds=time.time() - t0,
               agent="lora_trainer", cost_usd=cost_usd,
               note=f"LoRA de {name_human}: {base} → {steps} pasos ({n} fotos){' · ' + where if where else ''}")
    except Exception:  # noqa: BLE001
        log.warning("No pude registrar el consumo del entrenamiento", exc_info=True)
    _update(subject_id, lora_status="ready", lora_progress=100, lora_name=dest_name, lora_enabled=True,
            info={"phase": "", "finished": datetime.now().isoformat(timespec="seconds"), "minutes": mins, "eta_min": None,
                  "auto_retries": 0, "requeued": False,
                  "trained_steps": steps, "step": steps, **({"sec_per_step": round(spp_now, 3)} if spp_now else {})})
    events.publish({"type": "lora_ready", "subject_id": subject_id, "name": name_human, "minutes": mins})
    try:
        from app import granja
        await granja.push_lora(dest)        # que todas las tarjetas que generan la tengan
    except Exception:  # noqa: BLE001
        log.warning("No pude copiar la LoRA a las otras tarjetas", exc_info=True)


# ------------------------------------------------------------------ varias GPU (placa y alquiladas)
def local_gpus() -> list[str]:
    """GPU de la placa que pueden entrenar (LORA_GPUS=0,1,2). Por defecto la primera."""
    raw = os.getenv("LORA_GPUS", "0").strip()
    return [g.strip() for g in raw.split(",") if g.strip()] or ["0"]


def profile_for_gpu(gpu: str | None) -> str:
    """Perfil de entrenamiento de cada GPU de la placa. Con tarjetas distintas (ej. 3090 + RTX PRO 6000):
    LORA_GPU_PROFILES=0:estandar,1:pro. Si no se indica, el perfil general (LORA_LOCAL_PROFILE)."""
    raw = os.getenv("LORA_GPU_PROFILES", "")
    for part in raw.split(","):
        k, _, v = part.partition(":")
        if gpu is not None and k.strip() == str(gpu) and v.strip().lower() in PROFILES:
            return v.strip().lower()
    return _local_profile()


def _local_profile() -> str:
    p = os.getenv("LORA_LOCAL_PROFILE", "estandar").strip().lower()
    return p if p in PROFILES else "estandar"


def _detailed_captions_on() -> bool:
    from app import calidad
    return bool(calidad.get().get("detailed_captions"))


def _comfy_urls_for_gpu(gpu: str | None) -> list[str]:
    """ComfyUI que hay que pausar mientras se entrena en esa GPU. Con una sola GPU, todos los de la placa (como antes);
    con varias, solo el que corre en esa misma GPU (campo «gpu» de la conexión ComfyUI)."""
    if gpu is None or len(local_gpus()) <= 1:
        return _comfy_urls()
    from app.connections import list_enabled
    same = [c.config["url"].rstrip("/") for c in list_enabled("engine")
            if c.type == "comfyui" and c.config.get("url") and str(c.config.get("gpu", "0")) == str(gpu)]
    if str(gpu) == "0" and settings.COMFYUI_URL not in same:
        same.append(settings.COMFYUI_URL)
    return sorted(set(same))


CAPTION_PROMPT = ("You write training captions for a LoRA of ONE recurring subject. For each photo, in order, write ONE "
                  "short English caption (max 30 words) describing ONLY what changes between photos: pose, framing "
                  "(close-up, half body, full body), clothing, accessories, expression, background/place, lighting and "
                  "camera angle. NEVER describe the subject's identity traits (face shape, eye color, skin tone, hair "
                  "color, body type, age): those must be learned from the trigger word. No names. "
                  "Return JSON {\"captions\": [..]} with exactly one caption per photo.")


async def caption_photos(subject_id: str, trigger: str) -> dict:
    """Descripción detallada de cada foto del dataset (se guarda y no se repite si la foto no cambió)."""
    from app import llm
    from app.media import image_block
    from app.agents import registry
    if not registry.is_on("subject_analyst"):
        return {}
    cache_f = _work(subject_id) / "descripciones.json"
    try:
        cache = json.loads(cache_f.read_text(encoding="utf-8")) if cache_f.exists() else {}
    except Exception:  # noqa: BLE001
        cache = {}
    paths = session_images(subject_id)
    from app import privado
    todo = [p for p in paths if p not in cache and not privado.is_private_path(p)]   # lo privado NUNCA va a la nube
    for i in range(0, len(todo), 5):
        batch = todo[i:i + 5]
        content = []
        for p in batch:
            content.append(image_block(p, 768))
        content.append({"type": "text", "text": f"{len(batch)} photos. Trigger word: {trigger}."})
        out = await llm.json_call(CAPTION_PROMPT, content, llm.obj({"captions": {"type": "array", "items": {"type": "string"}}}),
                                  agent="subject_analyst", max_tokens=2000)
        caps = [str(c).strip().strip(".") for c in (out.get("captions") or [])]
        for p, c in zip(batch, caps):
            cache[p] = c[:300]
        cache_f.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return {p: cache.get(p, "") for p in paths}


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


async def _one(sid: str, slot: dict) -> None:
    """Entrena un modelo en el lugar asignado (GPU de la placa o tarjeta alquilada)."""
    from app import granja
    with session() as s:
        inf = s.get(Subject, sid).lora_info or {}
        steps = int(inf.get("steps") or settings.LORA_STEPS)
        base, mode = int(inf.get("from_step") or 0), inf.get("mode") or "restart"
    _update(sid, info={"where": slot.get("label", "")})
    try:
        if slot["kind"] == "local":
            await _run(sid, steps, base, mode, slot.get("gpu"))
        else:
            await granja.run_remote(sid, steps, base, mode, slot)
    except granja.NodeLost as e:          # la tarjeta alquilada se cayó a mitad: retoma solo desde lo guardado
        if not auto_resume(sid, str(e)):
            _update(sid, lora_status="failed", info={"error": str(e), "phase": ""})
        return
    except granja.NodeUnavailable as e:   # la tarjeta alquilada no respondió: vuelve a la fila, sin perder nada
        _update(sid, lora_status="queued", info={"where": ""})
        _publish_queue()
        _update(sid, info={"phase": f"En fila: {e} no respondió, espera otro lugar"})
        return
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        log.exception("LoRA %s", sid)
        _update(sid, lora_status="failed", info={"error": str(e)[:400], "phase": ""})
    with session() as s:   # si algo lo dejó en «queued» por error, no volver a tomarlo en bucle
        sub = s.get(Subject, sid)
        requeued = bool((sub.lora_info or {}).get("requeued")) if sub else False
        if sub and (sub.lora_status == "training" or (sub.lora_status == "queued" and not requeued)):
            sub.lora_status = "failed"
    _publish_queue()


async def _drain() -> None:
    """Reparte la fila entre todos los lugares libres (cada GPU entrena uno a la vez). Con una sola GPU y sin
    tarjetas alquiladas, es exactamente como antes: uno detrás de otro."""
    from app import granja
    running: dict[str, tuple[asyncio.Task, str]] = {}
    for sid, item in list(_reattach.items()):
        _reattach.pop(sid, None)
        slot_id = (f"local:{item['state'].get('gpu') or local_gpus()[0]}" if item["kind"] == "local"
                   else item["node"]["id"])
        task = asyncio.create_task(_resume(sid, item))
        _tasks[sid] = task
        running[sid] = (task, slot_id)
    while True:
        used: dict[str, int] = {}
        for _t, slot_id in running.values():
            used[slot_id] = used.get(slot_id, 0) + 1
        for sid in queue_ids():
            if sid in running:
                continue
            slot = await granja.free_slot(used, big=base_of(sid) in BIG_BASES, only=_prefer(sid))
            if not slot:
                break
            used[slot["id"]] = used.get(slot["id"], 0) + 1
            task = asyncio.create_task(_one(sid, slot))
            _tasks[sid] = task
            running[sid] = (task, slot["id"])
        if not running:
            if not queue_ids():
                return
            await asyncio.sleep(30)     # nadie libre (p. ej. la tarjeta alquilada todavía no prendió): reintenta
            continue
        done, _ = await asyncio.wait([t for t, _ in running.values()], timeout=15,
                                     return_when=asyncio.FIRST_COMPLETED)
        for sid in [k for k, (t, _) in running.items() if t in done]:
            running.pop(sid)


def _prefer(subject_id: str) -> str:
    """Dónde pidieron entrenarla («⚡ Entrenar en la RTX»): solo ahí. Vacío = donde haya lugar."""
    with session() as s:
        sub = s.get(Subject, subject_id)
        return ((sub.lora_info or {}).get("prefer_slot") or "") if sub else ""


def start_on(subject_id: str, slot_id: str, **kw) -> dict:
    """Igual que start(), pero en una tarjeta elegida (ej. la RTX PRO 6000). El diálogo de entrenar no cambia."""
    _update(subject_id, info={"prefer_slot": slot_id})
    try:
        return start(subject_id, _on=True, **kw)
    except Exception:
        _update(subject_id, info={"prefer_slot": ""})
        raise


def _publish_queue() -> None:
    """Actualiza en la web la posición de cada modelo en la fila."""
    for pos, sid in enumerate(queue_ids(), 1):
        _update(sid, info={"position": pos, "phase": f"En fila: #{pos}"})


def start(subject_id: str, steps: int | None = None, mode: str = "auto", minutes: float | None = None,
          _on: bool = False) -> dict:
    """Pone el modelo en la fila de entrenamiento (la GPU entrena uno a la vez; los demás esperan su turno).
    mode: continue = sigue desde lo ya aprendido y SUMA pasos · restart = empieza de cero (lo viejo se guarda aparte).
    steps/minutes = cuánto MÁS entrenar en esta sesión (minutes se convierte con la velocidad real medida)."""
    ok, why = available()
    if not ok:
        from app import granja
        if not granja.remote_nodes():
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
    if not _on and _prefer(subject_id):
        _update(subject_id, info={"prefer_slot": ""})      # con el botón de siempre: donde haya lugar
    p = plan(subject_id, mode, steps, minutes)
    busy = any(x != subject_id for x in queue_ids()) or (_worker is not None and not _worker.done())
    _update(subject_id, lora_status="queued", lora_progress=0,
            info={"steps": p["total_steps"], "from_step": p["from_step"], "mode": p["mode"], "images": n,
                  "auto_retries": 0, "requeued": False, "remote_job": None,
                  "queued_at": datetime.now().isoformat(timespec="seconds"), "error": "", "eta_min": p["eta_min"]})
    _publish_queue()
    ensure_worker()
    pos = queue_ids().index(subject_id) + 1 if subject_id in queue_ids() else 1
    return {"started": True, "queued": busy, "position": pos, "images": n, "steps": p["add_steps"], **p}


def cancel(subject_id: str) -> None:
    _update(subject_id, lora_status="canceled", info={"phase": "", "error": "Cancelado por una persona"})
    state = _read_state(subject_id)
    if state:
        _kill(state)
    from app import granja
    granja.cancel_remote(subject_id)
    _publish_queue()


MAX_AUTO_RESUME = 3


def auto_resume(sid: str, reason: str) -> bool:
    """Si un entrenamiento se cortó (apagón, reinicio de la PC, tarjeta alquilada caída), vuelve solo a la fila y
    sigue desde el último punto guardado, sin que nadie apriete nada. Hasta 3 veces seguidas."""
    with session() as s:
        sub = s.get(Subject, sid)
        if not sub or sub.lora_status == "canceled":
            return False
        info = dict(sub.lora_info or {})
    tries = int(info.get("auto_retries") or 0)
    if tries >= MAX_AUTO_RESUME:
        return False
    done = saved_step(sid)
    target = max(int(info.get("steps") or settings.LORA_STEPS), done + 100)
    _state_file(sid).unlink(missing_ok=True)
    _update(sid, lora_status="queued", lora_progress=0,
            info={"mode": "continue", "from_step": done, "steps": target, "requeued": True, "remote_job": None,
                  "auto_retries": tries + 1, "queued_at": "0-" + datetime.now().isoformat(timespec="seconds"),
                  "error": "", "phase": f"Se cortó ({reason}): retoma solo desde el paso {done}"})
    log.warning("LoRA %s: %s; retoma sola desde el paso %s (intento %s)", sid, reason, done, tries + 1)
    _publish_queue()
    _update(sid, info={"phase": f"Se cortó ({reason}): retoma solo desde el paso {done}"})
    return True


_reattach: dict[str, dict] = {}     # entrenamientos que seguían corriendo al reiniciar Shelton: sid -> lugar


def recover_after_restart() -> None:
    """Al arrancar Shelton: los entrenamientos que siguen corriendo (en la placa o en una tarjeta alquilada) se
    reconectan solos. Solo los que de verdad se cortaron quedan como interrumpidos (se relanzan y siguen desde lo
    último guardado)."""
    from app import granja
    with session() as s:
        rows = [(x.id, dict(x.lora_info or {})) for x in s.query(Subject).filter(Subject.lora_status == "training")]
    for sid, info in rows:
        st = _read_state(sid)
        if st and (Path(st["rc"]).exists() or _alive(st)):
            _reattach[sid] = {"kind": "local", "state": st}
            continue
        rj = info.get("remote_job") or {}
        node = next((n for n in granja.remote_nodes() if n["conn_id"] == rj.get("conn_id")), None) if rj else None
        if node and rj.get("jid"):
            _reattach[sid] = {"kind": "remote", "node": node, "jid": rj["jid"]}
            continue
        if auto_resume(sid, "se apagó o reinició la placa"):
            continue
        _state_file(sid).unlink(missing_ok=True)
        _update(sid, lora_status="failed", info={"error": "Se interrumpió varias veces. Vuelve a entrenar: "
                                                          "sigue desde lo último guardado.", "phase": ""})
    if _reattach:
        ensure_worker()
    # La fila sigue: los que esperaban su turno se entrenan solos al volver a arrancar


async def _resume(sid: str, item: dict) -> None:
    """Se reconecta a un entrenamiento que siguió corriendo mientras Shelton se reiniciaba."""
    from app import granja
    with session() as s:
        info = dict(s.get(Subject, sid).lora_info or {})
    _update(sid, info={"phase": "Reconectado: el entrenamiento siguió mientras Shelton se reiniciaba"})
    try:
        if item["kind"] == "local":
            st = item["state"]
            ctx = {"name": st["name"], "trigger": st["trigger"], "work": _work(sid), "job": st["job"],
                   "base": st["base"], "n": st["n"]}
            await _train_local(sid, ctx, int(st["steps"]), float(st["t0"]), st.get("gpu"), state=st)
        else:
            await granja.resume_remote(sid, item["node"], item["jid"], info)
    except granja.NodeLost as e:
        if not auto_resume(sid, str(e)):
            _update(sid, lora_status="failed", info={"error": str(e), "phase": ""})
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        log.exception("LoRA %s (reconexión)", sid)
        _update(sid, lora_status="failed", info={"error": str(e)[:400], "phase": ""})
    _publish_queue()


def make_variant(subject_id: str, base: str) -> dict:
    """Crea «<nombre> · SDXL» o «<nombre> · Flux»: mismas fotos y descripción, con su propia LoRA para ese motor.
    El modelo original y su LoRA de Z-Image no se tocan."""
    if base not in ("sdxl", "flux", "qwen"):
        raise ValueError("Elige sdxl, flux o qwen")
    from app.serialize import subject_dict
    with session() as s:
        src = s.get(Subject, subject_id)
        if not src:
            raise ValueError("Ese modelo no existe")
        name = f"{src.name} · {({'flux': 'Flux', 'qwen': 'Qwen'}).get(base, 'SDXL')}"
        if s.query(Subject).filter(Subject.name == name).first():
            raise ValueError(f"Ya existe «{name}»")
        new = Subject(name=name, kind=src.kind, description=src.description, notes=src.notes,
                      references=list(src.references or []), lora_info={"base": base, "variant_of": src.id})
        for f in ("voice_path", "voice_style"):
            if hasattr(src, f) and hasattr(new, f):
                setattr(new, f, getattr(src, f))
        s.add(new)
        s.flush()
        return subject_dict(new)


def engine_arch(engine) -> str:
    return getattr(engine, "arch", "zimage") or "zimage"


def strength_of(subject: Subject) -> float:
    try:
        v = float((subject.lora_info or {}).get("strength") or 0)
    except (TypeError, ValueError):
        v = 0
    return v if 0 < v <= 1.5 else settings.LORA_STRENGTH


def strengths_for(subjects: list[Subject]) -> list[float]:
    return [strength_of(s) for s in subjects]


def loras_for_engine(subjects: list[Subject], engine) -> list[tuple[str, str]]:
    """Como loras_for, pero solo si cada LoRA es del mismo modelo base que el motor (nunca se mezclan)."""
    arch = engine_arch(engine)
    if any(((s.lora_info or {}).get("base") or "zimage") != arch for s in subjects):
        return []
    return loras_for(subjects)


def loras_for(subjects: list[Subject]) -> list[tuple[str, str]]:
    """LoRAs listas y activas de los modelos de un post: [(archivo, palabra clave)]. Vacío si alguno no tiene."""
    out = []
    for s in subjects:
        if s.lora_status == "ready" and s.lora_name and s.lora_enabled is not False:
            out.append((s.lora_name, s.lora_trigger))
        else:
            return []
    return out
