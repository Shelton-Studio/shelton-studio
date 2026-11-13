"""Shelton Studio · Adult Zone (solo administradores): contenido adulto con modelos INVENTADAS y MAYORES DE 18, 100% local.

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


def enrich(prompt: str, preset: str, ckpt: str) -> str:
    """Agrega solo las etiquetas de calidad que cada modelo necesita para rendir al máximo."""
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
        raise HTTPException(403, "La Adult Zone es solo para administradores")
    return u


def subject_ok(sub: Subject) -> tuple[bool, str]:
    info = sub.lora_info or {}
    if not info.get("adult_ok"):
        return False, "Falta confirmar en su ficha que es una modelo inventada y mayor de 18"
    why = minor_problem(" ".join([sub.name or "", sub.description or "", sub.notes or ""]))
    if why:
        return False, f"La ficha de {sub.name} {why}: no se puede usar en la Adult Zone"
    return True, ""


def models() -> list[dict]:
    from app.lora import base_of
    out = []
    with session() as s:
        for sub in s.query(Subject).filter(Subject.kind == "person").order_by(Subject.name):
            ok, why = subject_ok(sub)
            out.append({"id": sub.id, "name": sub.name, "confirmed": bool((sub.lora_info or {}).get("adult_ok")),
                        "usable": ok, "why": why, "base": base_of(sub.id),
                        "lora_ready": sub.lora_status == "ready" and bool(sub.lora_name)})
    return out


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


def _engine(preset: str, ckpt: str):
    """ComfyUI local con el perfil pedido (y la conexión ComfyUI de la placa que corresponda, si hay)."""
    from app.connections import list_enabled
    from app.engines import _build
    conns = [c for c in list_enabled("engine") if c.type == "comfyui"]
    pick = next((c for c in conns if (c.config or {}).get("preset") == preset), None) or (conns[0] if conns else None)
    cfg = {**({k: v for k, v in (pick.config or {}).items() if k in ("url", "token")} if pick else {}), "preset": preset}
    if ckpt:
        cfg["gen_ckpt"] = ckpt
    return cfg, _build("comfyui", cfg)


async def generate(by: str, prompt: str, subject_id: str = "", preset: str = "sdxl", ckpt: str = "",
                   negative: str = "", count: int = 1, size: str = "vertical", strength: float | None = None) -> dict:
    """Valida, encola la generación local y devuelve el lote (las imágenes aparecen a medida que salen)."""
    from app import lora
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("Escribe qué quieres generar")
    if preset not in ("sdxl", "flux"):
        raise ValueError("Elige el motor SDXL o Flux")
    for text in (prompt, negative or "", ckpt or ""):
        why = minor_problem(text)
        if why:
            log.warning("Estudio privado: pedido bloqueado (%s)", why)
            raise ValueError(f"Bloqueado: el pedido {why}. La Adult Zone es solo para adultos.")
    loras, trigger = [], ""
    if subject_id:
        with session() as s:
            sub = s.get(Subject, subject_id)
            if not sub:
                raise ValueError("Ese modelo no existe")
            ok, why = subject_ok(sub)
            if not ok:
                raise ValueError(why)
            if sub.lora_status == "ready" and sub.lora_name and lora.base_of(sub.id) == preset:
                loras = [(sub.lora_name, float(strength) if strength else lora.strength_of(sub))]
                trigger = sub.lora_trigger or ""
            name = sub.name
    count = max(1, min(int(count or 1), MAX_COUNT))
    w, h = SIZES.get(size, SIZES["vertical"])
    if preset == "sdxl" and not ckpt:
        ckpt = best_checkpoint(await checkpoints())
    from app.engines.comfy import SDXL_NEGATIVE
    neg = ", ".join(x for x in [negative.strip(), SDXL_NEGATIVE, SAFE_NEGATIVE] if x)
    cfg, engine = _engine(preset, ckpt)
    full = enrich(f"{trigger}, adult, {prompt}" if trigger else f"adult, {prompt}", preset, ckpt)
    batch = uuid.uuid4().hex[:10]
    for i in range(count):
        _append({"id": f"{batch}-{i}", "batch": batch, "at": datetime.now().isoformat(timespec="seconds"), "by": by,
                 "subject": subject_id and name or "", "subject_id": subject_id or "", "preset": preset,
                 "ckpt": ckpt, "prompt": prompt,
                 "negative": negative, "lora": bool(loras), "status": "queued", "file": ""})
    asyncio.get_event_loop().create_task(_run(batch, count, engine, cfg, full, neg, w, h, loras))
    return {"batch": batch, "count": count, "with_lora": bool(loras),
            "note": "" if loras or not subject_id else
            f"{name} todavía no tiene LoRA para {preset.upper()}: sale sin su cara. Créale la versión {preset.upper()} en el Banco y entrénala."}


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
    import random
    try:
        engine.values["generate"]["negative"] = negative      # motor nuevo para este lote: no afecta a otros
        url = getattr(engine, "url", "")
    except Exception as e:  # noqa: BLE001
        for i in range(count):
            _append({"id": f"{batch}-{i}", "status": "failed", "error": f"No pude preparar el motor: {e}"[:300]})
        return
    for i in range(count):
        eid = f"{batch}-{i}"
        if eid in _cancelled:
            continue
        reason = _gpu_reason(url)
        _append({"id": eid, "status": "waiting" if reason else "running", "note": reason})
        try:
            seed = random.randint(1, 2**31 - 1)
            if loras:
                raw = await engine.generate_with_loras(loras, prompt, w, h, seed)
            else:
                raw = await engine.generate(prompt, w, h, seed)
            if eid in _cancelled:
                continue
            f = _dir() / f"{eid}.png"
            f.write_bytes(raw)
            _append({"id": eid, "status": "done", "file": f.name, "seed": seed, "note": ""})
        except Exception as e:  # noqa: BLE001
            log.warning("Adult Zone: falló %s: %s", eid, e)
            _append({"id": eid, "status": "failed", "error": str(e)[:300], "note": ""})


def recover_after_restart() -> None:
    """Si Shelton se reinició con fotos a medio hacer, quedan como interrumpidas (no «generando» para siempre)."""
    for e in entries():
        if e.get("status") in ("queued", "running", "waiting"):
            _append({"id": e["id"], "status": "failed", "error": "Se interrumpió (Shelton se reinició). Pídela de nuevo.",
                     "note": ""})


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
        return sorted(opts)
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


class ConfirmIn(BaseModel):
    inventada: bool
    mayor: bool


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


@router.delete("/imagen/{entry_id}")
async def api_delete(entry_id: str, request: Request):
    _admin(request)
    try:
        delete(entry_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


# ------------------------------------------------------------------ chat de la Adult Zone (como hablarle a Shelton)
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
    preset = "flux" if "flux" in low else "sdxl"
    if subject and not re.search(r"\b(flux|sdxl)\b", low) and subject["lora_ready"] and subject["base"] in ("sdxl", "flux"):
        preset = subject["base"]                       # si su LoRA es de Flux, usa Flux sola
    prompt = t
    if subject:
        prompt = re.sub(re.escape(subject["name"]), "", prompt, flags=re.IGNORECASE)
    prompt = re.sub(r"\b(hazme|haz|genera|generame|créame|creame|crea|quiero|dame)\b", "", prompt, flags=re.IGNORECASE)
    prompt = re.sub(r"\b(\d|una|un|uno|dos|tres|cuatro)\s*(fotos?|im[aá]genes?|versiones?)\s*(de)?\b", "", prompt,
                    flags=re.IGNORECASE)
    prompt = re.sub(r"\b(con|en)\s+(flux|sdxl)\b|\bformato\s+\w+\b", "", prompt, flags=re.IGNORECASE)
    prompt = re.sub(r"\s{2,}", " ", prompt).strip(" ,.;:")
    return {"subject": subject, "count": max(1, min(count, MAX_COUNT)), "size": size, "preset": preset,
            "prompt": prompt or t}


async def chat(by: str, text: str) -> dict:
    p = parse_request(text)
    sub = p["subject"]
    r = await generate(by, p["prompt"], sub["id"] if sub else "", p["preset"], count=p["count"], size=p["size"])
    who = f" de {sub['name']}" if sub else ""
    msg = (f"Listo: genero {p['count']} {'foto' if p['count'] == 1 else 'fotos'}{who} en la placa con "
           f"{'Flux' if p['preset'] == 'flux' else 'SDXL'}{' y su LoRA' if r['with_lora'] else ''}, formato {p['size']}. "
           "Aparecen abajo en unos segundos.")
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
    """Rutas de sus fotos privadas, solo si la modelo está confirmada y eligieron usarlas al entrenar."""
    try:
        info = _sub_info(sid)
    except ValueError:
        return []
    if not (info.get("adult_ok") and info.get("train_private")):
        return []
    return [str(_dir() / Path(f).name) for f in private_refs(sid)]


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
