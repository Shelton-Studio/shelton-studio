"""Shelton Studio · Shelton Hot (solo administradores): contenido adulto con modelos INVENTADAS y MAYORES DE 18, 100% local.

- El pedido lo escribe la persona y va directo a la GPU de la placa (SDXL / Flux). No pasa por ningún agente ni por
  la nube: el texto y las imágenes no salen de la placa.
- Las imágenes se guardan en data/privado/ (no en /media, que es público) y solo se ven con sesión de administrador.
  Nunca pasan por Aprobaciones ni se publican.
- Protecciones obligatorias:
  1) solo con modelos del banco marcadas por un administrador como «inventada (no es una persona real) y mayor de 18»;
  2) se bloquea cualquier pedido o ficha que mencione menores de edad.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.config import settings
from app.db import Subject, session

log = logging.getLogger("shelton.privado")
router = APIRouter(prefix="/api/privado")

# Menores: se bloquea siempre (pedido, ficha del modelo, checkpoint)
MINOR_RE = re.compile(
    r"\b(ni[ñn]as?|ni[ñn]os?|nenas?|nenes?|menor(es)?\s*de\s*edad|menor(es)?|adolescentes?|colegialas?|escolar(es)?|"
    r"infantil(es)?|beb[ée]s?|pubert|prepuber|lolis?|lolita|shota|teen(s|ager|agers)?|preteen|child(ren)?|kids?|"
    r"minors?|underage|young\s*girls?|little\s*girls?|schoolgirls?|school\s*girls?|juvenile|toddler|infant|"
    r"high\s*school|middle\s*school|secundaria|primaria)\b", re.IGNORECASE)
AGE_RE = re.compile(r"\b(\d{1,2})\s*(a[ñn]os|años|years?\s*old|yo|y/o)\b", re.IGNORECASE)
SAFE_NEGATIVE = "child, teen, underage, minor, young-looking, childlike, school uniform"
# Si no eligen checkpoint, se usa el mejor instalado para realismo adulto (en este orden)
CKPT_PRIORITY = ("cyberrealistic", "realvis", "juggernaut", "epicrealism", "pony")
QUALITY_SDXL = ("photorealistic, RAW photo, highly detailed skin texture, natural skin pores, sharp focus, "
                "professional photography, soft natural lighting")
QUALITY_PONY = "score_9, score_8_up, score_7_up, photo, realistic, "
QUALITY_FLUX = "realistic photo, natural skin texture, sharp focus, professional photography"
_cancelled: set[str] = set()


def best_checkpoint(installed: list[str]) -> str:
    low = [(c, c.lower()) for c in installed]
    for key in CKPT_PRIORITY:
        hit = next((c for c, lc in low if key in lc), None)
        if hit:
            return hit
    return ""


ENGINES = {"sdxl": "SDXL", "flux": "Flux", "zimage": "Z-Image"}
COMFY_PRESET = {"zimage": "fast"}          # Z-Image Turbo: el mismo motor con el que se entrenaron las LoRA del Banco


def enrich(prompt: str, preset: str, ckpt: str) -> str:
    """Agrega solo las etiquetas de calidad que cada modelo necesita para rendir al máximo."""
    if preset == "zimage":
        return f"{prompt}, photorealistic, natural skin texture, detailed, sharp focus, professional photography"
    if preset == "flux":
        return f"{prompt}, {QUALITY_FLUX}"
    if "pony" in (ckpt or "").lower():
        return QUALITY_PONY + prompt
    return f"{prompt}, {QUALITY_SDXL}"
MAX_COUNT = 4


def _dir() -> Path:
    d = settings.DATA_DIR / "privado"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _registry() -> Path:
    return _dir() / "registro.jsonl"


def minor_problem(text: str) -> str:
    """Devuelve el motivo si el texto menciona menores (o una edad menor de 18); si no, cadena vacía."""
    t = text or ""
    m = MINOR_RE.search(t)
    if m:
        return f"menciona «{m.group(0)}»"
    for a in AGE_RE.finditer(t):
        if int(a.group(1)) < 18:
            return f"menciona una edad menor de 18 («{a.group(0)}»)"
    return ""


def _admin(request: Request) -> dict:
    from app.auth_routes import current
    u = current(request)
    if u.get("role") != "admin":
        raise HTTPException(403, "Shelton Hot es solo para administradores")
    return u


def subject_ok(sub: Subject) -> tuple[bool, str]:
    info = sub.lora_info or {}
    if not info.get("adult_ok") and info.get("variant_of"):
        # «Valentina · SDXL» hereda la confirmación de Valentina (es la misma modelo para otro motor)
        with session() as s2:
            orig = s2.get(Subject, info["variant_of"])
            if orig and (orig.lora_info or {}).get("adult_ok"):
                info = {**info, "adult_ok": (orig.lora_info or {})["adult_ok"]}
    if not info.get("adult_ok"):
        return False, "Falta confirmar en su ficha que es una modelo inventada y mayor de 18"
    why = minor_problem(" ".join([sub.name or "", sub.description or "", sub.notes or ""]))
    if why:
        return False, f"La ficha de {sub.name} {why}: no se puede usar en Shelton Hot"
    return True, ""


def models() -> list[dict]:
    from app.lora import base_of
    out = []
    with session() as s:
        for sub in s.query(Subject).filter(Subject.kind == "person").order_by(Subject.name):
            ok, why = subject_ok(sub)
            out.append({"id": sub.id, "name": sub.name, "confirmed": bool((sub.lora_info or {}).get("adult_ok")),
                        "usable": ok, "why": why, "base": base_of(sub.id),
                        "lora_ready": _has_lora(sub), "variant_of": (sub.lora_info or {}).get("variant_of", ""),
                        "status": sub.lora_status or "", "progress": round(sub.lora_progress or 0),
                        "private_photos": len((sub.lora_info or {}).get("private_refs") or [])})
            out[-1]["adult_twin"] = bool((sub.lora_info or {}).get("adult_twin"))
    # cada modelo muestra el estado de su 🔞 LoRA adulta (privada, solo de Shelton Hot)
    for m in out:
        twin = next((x for x in out if x["variant_of"] == m["id"] and x["adult_twin"]), None)
        m["adult"] = ({"id": twin["id"], "ready": twin["lora_ready"], "status": twin["status"], "progress": twin["progress"]}
                      if twin else None)
    return out


ADULT_MARK = "🔞"


def adult_twin_id(sid: str) -> str:
    """La 🔞 LoRA adulta de una modelo: una gemela SDXL privada («Valentina · 🔞»). Vive solo en Shelton Hot:
    no aparece en el Banco, ni en el chat de Shelton, ni en el contenido normal. La crea si no existe."""
    with session() as s:
        for x in s.query(Subject).filter(Subject.kind == "person"):
            info = x.lora_info or {}
            if info.get("adult_twin") and info.get("variant_of") == sid:
                return x.id
        src = s.get(Subject, sid)
        if not src:
            raise ValueError("Esa modelo no existe")
        new = Subject(name=f"{src.name} · {ADULT_MARK}", kind=src.kind, description=src.description, notes=src.notes,
                      references=list(src.references or []),
                      lora_info={"base": "sdxl", "variant_of": src.id, "adult_twin": True})
        s.add(new)
        s.flush()
        return new.id


def train_adult(sid: str) -> dict:
    """🎓 Entrenar la 🔞 LoRA adulta de una modelo: con sus fotos del Banco + sus fotos privadas. Mismo entrenamiento
    de siempre, pero queda solo en Shelton Hot. Su LoRA normal no se toca."""
    from app import lora
    with session() as s:
        sub = s.get(Subject, sid)
        if not sub:
            raise ValueError("Esa modelo no existe")
        ok, why = subject_ok(sub)
        if not ok:
            raise ValueError(why)
        name = sub.name
    twin_id = adult_twin_id(sid)
    # se entrena SOLO en la placa: sus fotos privadas no salen de aquí (ni a la nube ni a tarjetas alquiladas)
    r = lora.start(twin_id)
    return {**r, "twin": twin_id, "name": f"{name} · {ADULT_MARK}", "where": "la placa"}


def confirm(subject_id: str, by: str, inventada: bool, mayor: bool) -> dict:
    if not (inventada and mayor):
        raise ValueError("Hay que confirmar las dos cosas: que es inventada y que es mayor de 18")
    from app.lora import _update
    with session() as s:
        sub = s.get(Subject, subject_id)
        if not sub:
            raise ValueError("Ese modelo no existe")
        why = minor_problem(" ".join([sub.name or "", sub.description or "", sub.notes or ""]))
    if why:
        raise ValueError(f"La ficha {why}: corrígela antes de confirmar")
    _update(subject_id, info={"adult_ok": {"by": by, "at": datetime.now().isoformat(timespec="seconds")}})
    return {"ok": True}


def _append(entry: dict) -> None:
    with open(_registry(), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def entries() -> list[dict]:
    if not _registry().exists():
        return []
    rows: dict[str, dict] = {}
    for line in _registry().read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("deleted"):
            rows.pop(e["id"], None)
        else:
            rows[e["id"]] = {**rows.get(e["id"], {}), **e}
    return sorted(rows.values(), key=lambda e: e.get("at", ""), reverse=True)


SIZES = {"vertical": (896, 1152), "cuadrado": (1024, 1024), "horizontal": (1152, 896), "historia": (768, 1344)}


def _has_lora(sub) -> bool:
    """Tiene una LoRA terminada para usar. Mientras sigue aprendiendo se usa la última versión ya lista."""
    return bool(sub.lora_name) and (sub.lora_status in ("ready", "training", "queued")
                                    or bool((sub.lora_info or {}).get("versions")))


def _engine(preset: str, ckpt: str):
    """ComfyUI local con el perfil pedido (y la conexión ComfyUI de la placa que corresponda, si hay)."""
    from app.connections import list_enabled
    from app.engines import _build
    conns = [c for c in list_enabled("engine") if c.type == "comfyui"]
    preset = COMFY_PRESET.get(preset, preset)
    pick = next((c for c in conns if (c.config or {}).get("preset") == preset), None) or (conns[0] if conns else None)
    cfg = {**({k: v for k, v in (pick.config or {}).items() if k in ("url", "token")} if pick else {}), "preset": preset}
    if ckpt and preset in ("sdxl", "flux"):
        cfg["gen_ckpt"] = ckpt
    return cfg, _build("comfyui", cfg)


async def generate(by: str, prompt: str, subject_id: str = "", preset: str = "sdxl", ckpt: str = "",
                   negative: str = "", count: int = 1, size: str = "vertical", strength: float | None = None) -> dict:
    """Valida, encola la generación local y devuelve el lote (las imágenes aparecen a medida que salen)."""
    from app import lora
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("Escribe qué quieres generar")
    if preset not in ENGINES:
        raise ValueError("Elige el motor SDXL, Flux o Z-Image")
    for text in (prompt, negative or "", ckpt or ""):
        why = minor_problem(text)
        if why:
            log.warning("Estudio privado: pedido bloqueado (%s)", why)
            raise ValueError(f"Bloqueado: el pedido {why}. Shelton Hot es solo para adultos.")
    loras, trigger = [], ""
    if subject_id:
        with session() as s:
            sub = s.get(Subject, subject_id)
            if not sub:
                raise ValueError("Ese modelo no existe")
            ok, why = subject_ok(sub)
            if not ok:
                raise ValueError(why)
            if _has_lora(sub) and lora.base_of(sub.id) == preset:
                loras = [(sub.lora_name, float(strength) if strength else lora.strength_of(sub))]
                trigger = sub.lora_trigger or ""
            name = sub.name
    st = _style_lora(preset)                  # 🎓 la LoRA propia de Shelton Hot (aprende de las aprobadas)
    if st:
        loras = [*loras, (st[0], STYLE_STRENGTH)]
        trigger = ", ".join(x for x in [trigger, st[1]] if x)
    count = max(1, min(int(count or 1), MAX_COUNT))
    w, h = SIZES.get(size, SIZES["vertical"])
    if preset == "sdxl" and not ckpt:
        ckpt = best_checkpoint(await checkpoints())
    from app.engines.comfy import SDXL_NEGATIVE
    neg = ", ".join(x for x in [negative.strip(), SDXL_NEGATIVE, SAFE_NEGATIVE] if x)
    # SDXL y Flux entienden casi solo inglés: se traduce en la placa (sin IA en la nube). Z-Image entiende español.
    prompt_en, how = prompt, ""
    if preset in ("sdxl", "flux"):
        from app import traductor
        prompt_en, how = await traductor.to_english(prompt)
        neg_en, _ = await traductor.to_english(negative or "")
        if negative and neg_en:
            neg = ", ".join(x for x in [neg_en, SDXL_NEGATIVE, SAFE_NEGATIVE] if x)
        why = minor_problem(prompt_en)
        if why:
            raise ValueError(f"Bloqueado: el pedido {why}. Shelton Hot es solo para adultos.")
    cfg, engine = _engine(preset, ckpt)
    full = enrich(f"{trigger}, adult, {prompt_en}" if trigger else f"adult, {prompt_en}", preset, ckpt)
    batch = uuid.uuid4().hex[:10]
    for i in range(count):
        _append({"id": f"{batch}-{i}", "batch": batch, "at": datetime.now().isoformat(timespec="seconds"), "by": by,
                 "subject": subject_id and name or "", "subject_id": subject_id or "", "preset": preset,
                 "ckpt": ckpt, "prompt": prompt, "prompt_en": prompt_en if how else "", "translated": how,
                 "negative": negative, "lora": bool(loras), "status": "queued", "file": "",
                 # todo lo necesario para rehacerla sola si Shelton se reinicia o se corta la conexión
                 "job": {"full": full, "neg": neg, "w": w, "h": h, "loras": [list(x) for x in loras]}})
    asyncio.get_event_loop().create_task(_run(batch, count, engine, cfg, full, neg, w, h, loras))
    return {"batch": batch, "count": count, "with_lora": bool(loras), "translated": how, "prompt_en": prompt_en,
            "note": "" if loras or not subject_id else
            f"{name} todavía no tiene LoRA para {ENGINES[preset]}: sale sin su cara. "
            f"Pídela sin elegir motor (uso el de su LoRA) o créale la versión {ENGINES[preset]} en el Banco."}


def _gpu_reason(url: str) -> str:
    """Por qué está ocupada la GPU (para decirlo en pantalla en vez de «Generando…» sin explicación)."""
    from app.engines import busy
    if not busy(url):
        return ""
    with session() as s:
        tr = [x.name for x in s.query(Subject).filter(Subject.lora_status == "training")]
    return (f"Esperando la GPU: está entrenando la LoRA de {', '.join(tr)}. Empieza sola cuando termine."
            if tr else "Esperando la GPU: está terminando otro trabajo. Empieza sola enseguida.")


async def _run(batch: str, count: int, engine, cfg: dict, prompt: str, negative: str, w: int, h: int, loras) -> None:
    await _run_ids([f"{batch}-{i}" for i in range(count)], engine, cfg, prompt, negative, w, h, loras)


async def _run_ids(ids: list[str], engine, cfg: dict, prompt: str, negative: str, w: int, h: int, loras) -> None:
    import random
    try:
        engine.values["generate"]["negative"] = negative      # motor nuevo para este lote: no afecta a otros
        url = getattr(engine, "url", "")
    except Exception as e:  # noqa: BLE001
        for eid in ids:
            _append({"id": eid, "status": "failed", "error": f"No pude preparar el motor: {e}"[:300]})
        return
    for eid in ids:
        if eid in _cancelled:
            continue
        seed = random.randint(1, 2**31 - 1)
        for attempt in range(1, ATTEMPTS + 1):
            if eid in _cancelled:
                break
            reason = _gpu_reason(url)
            _append({"id": eid, "status": "waiting" if reason else "running", "note": reason})
            try:
                if loras:
                    raw = await engine.generate_with_loras(loras, prompt, w, h, seed)
                else:
                    raw = await engine.generate(prompt, w, h, seed)
                if eid in _cancelled:
                    break
                f = _dir() / f"{eid}.png"
                f.write_bytes(raw)
                _append({"id": eid, "status": "done", "file": f.name, "seed": seed, "note": ""})
                break
            except Exception as e:  # noqa: BLE001
                log.warning("Shelton Hot: falló %s (intento %d/%d): %s", eid, attempt, ATTEMPTS, e)
                if attempt == ATTEMPTS:
                    _append({"id": eid, "status": "failed", "error": str(e)[:300], "note": ""})
                    break
                # se cortó la placa, internet o ComfyUI: espera y lo vuelve a intentar solo
                _append({"id": eid, "status": "waiting",
                         "note": f"Se cortó la conexión con la placa. Reintento solo ({attempt}/{ATTEMPTS - 1})…"})
                await asyncio.sleep(RETRY_WAIT * attempt)


ATTEMPTS = 4          # si se corta algo, reintenta sola antes de darla por fallida
RETRY_WAIT = 20.0     # segundos (crece en cada intento)


def recover_after_restart() -> int:
    """Si Shelton se reinició con fotos a medio hacer, las retoma solas (no se pierden).
    Solo las muy viejas, sin los datos para rehacerlas, quedan como interrumpidas."""
    pending: dict[tuple, list[str]] = {}
    for e in sorted(entries(), key=lambda x: x["id"]):
        if e.get("status") not in ("queued", "running", "waiting"):
            continue
        job = e.get("job")
        if not job:
            _append({"id": e["id"], "status": "failed", "error": "Se interrumpió (Shelton se reinició). Pídela de nuevo.",
                     "note": ""})
            continue
        _append({"id": e["id"], "status": "queued", "note": "Shelton se reinició: la retomo sola."})
        key = (e.get("preset") or "sdxl", e.get("ckpt") or "", json.dumps(job, sort_keys=True))
        pending.setdefault(key, []).append(e["id"])
    if not pending:
        return 0
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.get_event_loop()
    n = 0
    for (preset, ckpt, raw), ids in pending.items():
        job = json.loads(raw)
        loop.create_task(_resume(ids, preset, ckpt, job))
        n += len(ids)
    log.info("Shelton Hot: retomo %d foto(s) que quedaron a medias", n)
    return n


async def _resume(ids: list[str], preset: str, ckpt: str, job: dict) -> None:
    try:
        cfg, engine = _engine(preset, ckpt)
    except Exception as e:  # noqa: BLE001
        for eid in ids:
            _append({"id": eid, "status": "failed", "error": f"No pude preparar el motor: {e}"[:300], "note": ""})
        return
    loras = [tuple(x) for x in job.get("loras") or []]
    await _run_ids(ids, engine, cfg, job["full"], job["neg"], int(job["w"]), int(job["h"]), loras)


def review(entry_id: str, ok: bool | None, by: str = "") -> dict:
    """Aprobación interna de Shelton Hot (no publica nada: solo marca cuáles sirven y cuáles no)."""
    e = next((x for x in entries() if x["id"] == entry_id), None)
    if not e or e.get("status") != "done":
        raise ValueError("Esa imagen no existe o todavía no está lista")
    state = "approved" if ok else "rejected" if ok is False else ""
    _append({"id": entry_id, "review": state, "review_by": by, "review_at": datetime.now().isoformat(timespec="seconds")})
    return {"ok": True, "review": state}


# ------------------------------------------------------------------ 🎓 LoRA propia de Shelton Hot
STYLE_NAME = "Adult Zone · Estilo"
STYLE_STRENGTH = 0.6          # se suma a la LoRA de la modelo sin taparle la cara
STYLE_MIN = 8


def style_subject_id() -> str:
    """La «modelo» interna que guarda la LoRA de estilo de Shelton Hot (no aparece en el Banco)."""
    with session() as s:
        for x in s.query(Subject).filter(Subject.name == STYLE_NAME):
            return x.id
        sub = Subject(name=STYLE_NAME, kind="style", description="Estilo aprendido de las fotos aprobadas de Shelton Hot",
                      lora_info={"base": "sdxl", "adult_style": True,
                                 "adult_ok": {"by": "sistema", "at": datetime.now().isoformat(timespec="seconds")}})
        s.add(sub)
        s.flush()
        return sub.id


def _style_file() -> Path:
    return _dir() / "estilo_fotos.json"


def style_uploads() -> list[dict]:
    """Fotos propias subidas para la LoRA de Shelton Hot (con quién confirmó que son inventadas y +18)."""
    try:
        rows = json.loads(_style_file().read_text(encoding="utf-8")) if _style_file().exists() else []
    except ValueError:
        rows = []
    return [r for r in rows if (_dir() / Path(r["file"]).name).exists()]


def style_upload(data: bytes, by: str, confirmed: bool) -> dict:
    if not confirmed:
        raise ValueError("Confirma que son imágenes generadas con IA, de personas inventadas y mayores de 18")
    from io import BytesIO
    from PIL import Image, ImageOps
    try:
        img = ImageOps.exif_transpose(Image.open(BytesIO(data))).convert("RGB")
    except Exception:  # noqa: BLE001
        raise ValueError("Ese archivo no es una imagen")
    name = f"estilo_{uuid.uuid4().hex[:10]}.png"
    img.save(_dir() / name, "PNG")
    rows = style_uploads() + [{"file": name, "by": by, "at": datetime.now().isoformat(timespec="seconds"),
                               "confirmed": "IA · inventada · +18"}]
    _style_file().write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "file": name, "count": len(rows)}


def style_remove(file: str) -> dict:
    name = Path(file).name
    rows = [r for r in style_uploads() if r["file"] != name]
    _style_file().write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    (_dir() / name).unlink(missing_ok=True)
    return {"ok": True, "count": len(rows)}


def style_dataset() -> list[str]:
    """Fotos propias subidas + las aprobadas en Shelton Hot (solo las que pasan el control de menores)."""
    out = [str(_dir() / Path(r["file"]).name) for r in style_uploads()]
    for e in entries():
        if e.get("review") != "approved" or e.get("status") != "done" or not e.get("file"):
            continue
        if minor_problem(" ".join([e.get("prompt") or "", e.get("subject") or ""])):
            continue
        f = _dir() / Path(e["file"]).name
        if f.exists():
            out.append(str(f))
    return out


def _style_lora(preset: str) -> tuple[str, str] | None:
    with session() as s:
        sub = next(iter(s.query(Subject).filter(Subject.name == STYLE_NAME)), None)
        if not sub or not sub.lora_name or sub.lora_status not in ("ready", "training", "queued"):
            return None
        if ((sub.lora_info or {}).get("base") or "sdxl") != preset:
            return None
        return sub.lora_name, sub.lora_trigger or ""


def style_status() -> dict:
    with session() as s:
        sub = next(iter(s.query(Subject).filter(Subject.name == STYLE_NAME)), None)
        info = dict(sub.lora_info or {}) if sub else {}
        ups = style_uploads()
        return {"approved": len(style_dataset()), "uploads": [r["file"] for r in ups], "n_uploads": len(ups), "min": STYLE_MIN, "status": sub.lora_status if sub else "",
                "progress": round(sub.lora_progress or 0) if sub else 0, "phase": info.get("phase", ""),
                "eta_min": info.get("eta_min"), "ready": bool(sub and sub.lora_name), "trained_with": info.get("images")}


def style_train() -> dict:
    from app import lora
    paths = style_dataset()
    if len(paths) < STYLE_MIN:
        raise ValueError(f"Hay {len(paths)} fotos aprobadas y hacen falta al menos {STYLE_MIN}. "
                         "Aprueba con ✔ las mejores en la galería privada.")
    sid = style_subject_id()
    with session() as s:
        s.get(Subject, sid).references = paths          # aprende de las aprobadas (nunca van a la nube)
    return lora.start(sid)


def delete(entry_id: str) -> None:
    e = next((x for x in entries() if x["id"] == entry_id), None)
    if not e:
        raise ValueError("No existe")
    if e.get("status") in ("queued", "running", "waiting"):
        _cancelled.add(entry_id)                                # si estaba esperando, se cancela
    if e.get("file"):
        (_dir() / Path(e["file"]).name).unlink(missing_ok=True)
    _append({"id": entry_id, "deleted": True, "at": datetime.now().isoformat(timespec="seconds")})


async def checkpoints() -> list[str]:
    """Checkpoints instalados en el ComfyUI de la placa (para elegir Pony, CyberRealistic…)."""
    from app.comfyui.client import ComfyUIClient
    try:
        info = await ComfyUIClient().object_info("CheckpointLoaderSimple")
        req = info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"]
        opts = req[0] if isinstance(req[0], list) else (req[1] or {}).get("options", [])
        return sorted(o for o in opts if "flux" not in o.lower())   # la lista es de SDXL: Flux va aparte
    except Exception:  # noqa: BLE001
        return []


# ------------------------------------------------------------------ API (todo exige administrador)
class GenIn(BaseModel):
    prompt: str
    subject_id: str = ""
    preset: str = "sdxl"
    ckpt: str = ""
    negative: str = ""
    count: int = 1
    size: str = "vertical"
    strength: float | None = None


class TranslateIn(BaseModel):
    text: str = ""


@router.post("/traducir")
async def api_translate(body: TranslateIn, request: Request):
    """Traducción en vivo mientras se escribe (en la placa, sin nube), para ver qué recibe SDXL/Flux."""
    _admin(request)
    from app import traductor
    en, how = await traductor.to_english(body.text[:2000])
    return {"en": en, "how": how, "status": traductor.status()}


class ConfirmIn(BaseModel):
    inventada: bool
    mayor: bool


@router.get("/estilo")
async def api_style(request: Request):
    _admin(request)
    return style_status()


@router.post("/estilo/subir")
async def api_style_upload(request: Request):
    u = _admin(request)
    form = await request.form()
    confirmed = str(form.get("confirm") or "") == "1"
    out = None
    try:
        for f in form.getlist("files"):
            out = style_upload(await f.read(), (u or {}).get("username", ""), confirmed)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return out or {"ok": False}


class StyleRemoveIn(BaseModel):
    file: str


@router.post("/estilo/quitar")
async def api_style_remove(body: StyleRemoveIn, request: Request):
    _admin(request)
    return style_remove(body.file)


@router.post("/modelo/{sid}/lora-adulta")
async def api_train_adult(sid: str, request: Request):
    _admin(request)
    try:
        return train_adult(sid)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/estilo/entrenar")
async def api_style_train(request: Request):
    _admin(request)
    try:
        return style_train()
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/estado")
async def api_state(request: Request):
    _admin(request)
    return {"models": models(), "entries": entries()[:200], "checkpoints": await checkpoints()}


@router.post("/modelos/{sid}/confirmar")
async def api_confirm(sid: str, body: ConfirmIn, request: Request):
    u = _admin(request)
    try:
        return confirm(sid, u.get("username", ""), body.inventada, body.mayor)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/generar")
async def api_generate(body: GenIn, request: Request):
    u = _admin(request)
    try:
        return await generate(u.get("username", ""), body.prompt, body.subject_id, body.preset, body.ckpt,
                              body.negative, body.count, body.size, body.strength)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/imagen/{entry_id}")
async def api_image(entry_id: str, request: Request):
    _admin(request)
    e = next((x for x in entries() if x["id"] == entry_id and x.get("file")), None)
    if not e:
        raise HTTPException(404, "No existe")
    return FileResponse(_dir() / Path(e["file"]).name, headers={"Cache-Control": "private, no-store"})


class ReviewIn(BaseModel):
    ok: bool | None = None          # True aprobar, False rechazar, None volver a «por revisar»


@router.post("/imagen/{entry_id}/revisar")
async def api_review(entry_id: str, body: ReviewIn, request: Request):
    u = _admin(request)
    try:
        return review(entry_id, body.ok, u.get("username", "") if isinstance(u, dict) else "")
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.delete("/imagen/{entry_id}")
async def api_delete(entry_id: str, request: Request):
    _admin(request)
    try:
        delete(entry_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


# ------------------------------------------------------------------ chat de Shelton Hot (como hablarle a Shelton)
NUM_WORDS = {"una": 1, "un": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4}


def parse_request(text: str) -> dict:
    """Entiende un pedido escrito como a Shelton: «3 fotos de Luna en un loft, formato historia, con flux».
    No usa ninguna IA en la nube: reconoce la modelo por su nombre, la cantidad, el formato y el motor."""
    t = text.strip()
    low = t.lower()
    usable = [m for m in models() if m["usable"]]
    subject = next((m for m in sorted(usable, key=lambda m: -len(m["name"])) if m["name"].lower() in low), None)
    count = 1
    m = re.search(r"\b(\d)\s*(fotos?|im[aá]genes?|versiones?)\b", low) or \
        re.search(r"\b(una|un|uno|dos|tres|cuatro)\s*(fotos?|im[aá]genes?|versiones?)\b", low)
    if m:
        count = int(m.group(1)) if m.group(1).isdigit() else NUM_WORDS[m.group(1)]
    size = ("historia" if re.search(r"historia|9:16|story|reel", low) else "horizontal" if "horizontal" in low
            else "cuadrado" if "cuadrad" in low else "vertical")
    preset = "flux" if "flux" in low else "zimage" if re.search(r"z-?image", low) else "sdxl"
    if subject and subject.get("adult_twin"):            # escribieron «Valentina · 🔞»: es la misma Valentina
        subject = next((m for m in usable if m["id"] == subject["variant_of"]), subject)
    adult_twin = (next((m for m in usable if m["variant_of"] == subject["id"] and m["adult_twin"] and m["lora_ready"]),
                       None) if subject else None)
    if subject and adult_twin and not re.search(r"\b(flux|z-?image)\b", low):
        # Como en el Shelton normal: nombras a la modelo y listo. Si ya tiene su 🔞 LoRA adulta, se usa esa,
        # junto con la LoRA de Shelton Hot.
        preset = "sdxl"
        subject = adult_twin
    elif subject and not re.search(r"\b(flux|sdxl)\b|z-?image", low) and subject["lora_ready"] and subject["base"] in ENGINES:
        preset = subject["base"]          # usa el motor de SU LoRA (la que aprendió de sus fotos del Banco): sale con su cara
    named = subject
    if subject and subject["base"] != preset:
        # «Valentina con sdxl»: usa su versión SDXL (con su propia LoRA SDXL) si ya la tiene
        twin = next((m for m in usable if m["variant_of"] == subject["id"] and m["base"] == preset
                     and not m["adult_twin"]), None)
        if twin:
            subject = twin
    prompt = t
    for who in {x["name"] for x in (named, subject) if x} | ({named["name"].split(" · ")[0]} if named else set()):
        prompt = re.sub(re.escape(who), "", prompt, flags=re.IGNORECASE)
    prompt = re.sub(r"\b(hazme|haz|genera|generame|créame|creame|crea|quiero|dame)\b", "", prompt, flags=re.IGNORECASE)
    prompt = re.sub(r"\b(\d|una|un|uno|dos|tres|cuatro)\s*(fotos?|im[aá]genes?|versiones?)\s*(de)?\b", "", prompt,
                    flags=re.IGNORECASE)
    prompt = re.sub(r"\b(con|en)\s+(flux|sdxl|z-?image)\b|\bformato\s+\w+\b", "", prompt, flags=re.IGNORECASE)
    prompt = re.sub(r"\s{2,}", " ", prompt).strip(" ,.;:")
    return {"subject": subject, "count": max(1, min(count, MAX_COUNT)), "size": size, "preset": preset,
            "prompt": prompt or t}


async def chat(by: str, text: str) -> dict:
    p = parse_request(text)
    sub = p["subject"]
    r = await generate(by, p["prompt"], sub["id"] if sub else "", p["preset"], count=p["count"], size=p["size"])
    who = f" de {sub['name']}" if sub else ""
    msg = (f"Listo: genero {p['count']} {'foto' if p['count'] == 1 else 'fotos'}{who} en la placa con "
           f"{ENGINES[p['preset']]}{' y su LoRA (aprendida de sus fotos del Banco)' if r['with_lora'] else ''}, formato {p['size']}. "
           "Aparecen abajo en unos segundos.")
    if r.get("translated") == "placa":
        msg += f" Se lo pasé al inglés en la placa: «{r['prompt_en'][:300]}»."
    elif r.get("translated") == "glosario":
        msg += (" El traductor de la placa todavía no está listo: traduje solo las palabras básicas. "
                "Para lo más preciso, escribe la descripción en inglés por ahora.")
    if r.get("note"):
        msg += " " + r["note"]
    if not sub and usable_names():
        msg += f" (Si quieres a una modelo, nómbrala: {', '.join(usable_names()[:5])}.)"
    return {"reply": msg, "batch": r["batch"], "parsed": {k: (v["name"] if k == "subject" and v else v) for k, v in p.items()}}


def usable_names() -> list[str]:
    return [m["name"] for m in models() if m["usable"]]


class ChatIn(BaseModel):
    text: str


@router.post("/chat")
async def api_chat(body: ChatIn, request: Request):
    u = _admin(request)
    try:
        return await chat(u.get("username", ""), body.text)
    except ValueError as e:
        raise HTTPException(400, str(e))



# ------------------------------------------------------------------ fotos privadas en la ficha del modelo (Banco)
# Viven en data/privado/ (no en /media). Se ven solo con sesión de admin y se usan para entrenar su LoRA
# EN LA PLACA (o en tus tarjetas alquiladas). Nunca se mandan a ninguna IA de la nube.
def _sub_info(sid: str) -> dict:
    with session() as s:
        sub = s.get(Subject, sid)
        if not sub:
            raise ValueError("Ese modelo no existe")
        return dict(sub.lora_info or {})


def private_refs(sid: str) -> list[str]:
    return [f for f in (_sub_info(sid).get("private_refs") or []) if (_dir() / Path(f).name).exists()]


def private_paths(sid: str) -> list[str]:
    """Rutas de sus fotos privadas: solo para su 🔞 LoRA adulta (Shelton Hot), si la modelo está confirmada."""
    try:
        info = _sub_info(sid)
    except ValueError:
        return []
    if not (info.get("adult_twin") and info.get("variant_of")):
        return []        # sus LoRAs normales (las del Shelton normal) nunca aprenden de lo privado
    try:
        orig = _sub_info(info["variant_of"])
    except ValueError:
        return []
    if not orig.get("adult_ok"):
        return []
    return [str(_dir() / Path(f).name) for f in private_refs(info["variant_of"])]


def is_private_path(p: str) -> bool:
    try:
        return Path(p).resolve().parent == _dir().resolve()
    except Exception:  # noqa: BLE001
        return False


def _set_refs(sid: str, refs: list[str], **extra) -> None:
    from app.lora import _update
    _update(sid, info={"private_refs": list(dict.fromkeys(refs)), **extra})


def add_ref_from_entry(sid: str, entry_id: str) -> dict:
    with session() as s:
        ok, why = subject_ok(s.get(Subject, sid)) if s.get(Subject, sid) else (False, "Ese modelo no existe")
    if not ok:
        raise ValueError(why)
    e = next((x for x in entries() if x["id"] == entry_id and x.get("file")), None)
    if not e:
        raise ValueError("Esa imagen no existe")
    _set_refs(sid, private_refs(sid) + [e["file"]])
    return {"ok": True, "count": len(private_refs(sid))}


def add_ref_upload(sid: str, data: bytes) -> dict:
    from io import BytesIO
    from PIL import Image, ImageOps
    with session() as s:
        ok, why = subject_ok(s.get(Subject, sid)) if s.get(Subject, sid) else (False, "Ese modelo no existe")
    if not ok:
        raise ValueError(why)
    try:
        img = ImageOps.exif_transpose(Image.open(BytesIO(data))).convert("RGB")
    except Exception:  # noqa: BLE001
        raise ValueError("Ese archivo no es una imagen")
    name = f"ref_{sid[:8]}_{uuid.uuid4().hex[:8]}.png"
    img.save(_dir() / name, "PNG")
    _set_refs(sid, private_refs(sid) + [name])
    return {"ok": True, "file": name, "count": len(private_refs(sid))}


def remove_ref(sid: str, file: str) -> dict:
    _set_refs(sid, [f for f in private_refs(sid) if f != Path(file).name])
    return {"ok": True}


def model_private(sid: str) -> dict:
    info = _sub_info(sid)
    gen = [e for e in entries() if e.get("subject_id") == sid and e.get("file")]
    return {"refs": private_refs(sid), "generated": gen[:60], "train_private": bool(info.get("train_private")),
            "confirmed": bool(info.get("adult_ok"))}


class TrainPrivIn(BaseModel):
    on: bool


class RefIn(BaseModel):
    entry_id: str = ""
    file: str = ""


@router.get("/modelo/{sid}")
async def api_model(sid: str, request: Request):
    _admin(request)
    try:
        return model_private(sid)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/modelo/{sid}/fotos")
async def api_add_ref(sid: str, body: RefIn, request: Request):
    _admin(request)
    try:
        return add_ref_from_entry(sid, body.entry_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/modelo/{sid}/subir")
async def api_upload_ref(sid: str, request: Request):
    _admin(request)
    form = await request.form()
    out = None
    try:
        for f in form.getlist("files"):
            out = add_ref_upload(sid, await f.read())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return out or {"ok": False}


@router.post("/modelo/{sid}/quitar")
async def api_remove_ref(sid: str, body: RefIn, request: Request):
    _admin(request)
    return remove_ref(sid, body.file)


@router.post("/modelo/{sid}/entrenar-con-privadas")
async def api_train_private(sid: str, body: TrainPrivIn, request: Request):
    _admin(request)
    with session() as s:
        if not s.get(Subject, sid):
            raise HTTPException(404, "Ese modelo no existe")
    from app.lora import _update
    _update(sid, info={"train_private": bool(body.on)})
    return {"ok": True}


@router.get("/ref/{file}")
async def api_ref_file(file: str, request: Request):
    _admin(request)
    f = _dir() / Path(file).name
    if not f.exists():
        raise HTTPException(404, "No existe")
    return FileResponse(f, headers={"Cache-Control": "private, no-store"})
