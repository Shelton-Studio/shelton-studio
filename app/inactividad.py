"""🌙 Entrenar sola cuando no hay nadie: si pasan 2 horas (ajustable) sin que nadie use Shelton y la GPU está libre,
Shelton pone a entrenar a las modelos, una por vez, sumando sobre lo que ya saben.

«Nadie lo usa» = ni clics ni teclas en la web, ni pedidos, ni fotos generándose, ni videos, ni Shelton Hot trabajando,
ni otro entrenamiento en curso. Apenas alguien vuelve, no arranca ninguno nuevo (el que está en curso termina
su sesión y queda guardado, como siempre).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from app import events
from app.config import settings
from app.db import Subject, session

log = logging.getLogger("shelton.inactividad")
router = APIRouter(prefix="/api")

DEFAULTS = {"on": True, "hours": 2.0, "steps": 1000, "rest_min": 30}
CHECK_EVERY = 300          # segundos entre revisiones
_last = {"t": time.time()}  # última actividad de una persona (al arrancar cuenta como actividad)
_busy_seen = {"t": time.time()}   # última vez que vimos a la placa o la RTX trabajando (fotos, videos, entrenamientos)
_state = {"last_auto": "", "last_name": "", "last_at": ""}


def _file():
    return settings.DATA_DIR / "inactividad.json"


def get() -> dict:
    try:
        return {**DEFAULTS, **json.loads(_file().read_text(encoding="utf-8"))}
    except Exception:  # noqa: BLE001
        return dict(DEFAULTS)


def save(**kw) -> dict:
    cur = get()
    for k, v in kw.items():
        if v is not None and k in DEFAULTS:
            cur[k] = v
    cur["hours"] = max(0.5, min(float(cur["hours"]), 24.0))
    cur["steps"] = max(100, min(int(cur["steps"]), 5000))
    cur["rest_min"] = max(10, min(int(cur.get("rest_min") or 30), 240))
    _file().write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
    return cur


def touch() -> None:
    """Alguien está usando Shelton (clic, tecla, pedido)."""
    _last["t"] = time.time()


def rested_minutes() -> float:
    """Minutos de calma total: desde lo último que se vio trabajando o el último entrenamiento que terminó."""
    last = _busy_seen["t"]
    with session() as s:
        for x in s.query(Subject).filter(Subject.lora_status == "ready"):
            fin = (x.lora_info or {}).get("finished")
            if fin:
                try:
                    last = max(last, datetime.fromisoformat(fin).timestamp())
                except ValueError:
                    pass
    return (time.time() - last) / 60


def idle_minutes() -> float:
    return (time.time() - _last["t"]) / 60


def busy_reason() -> str:
    """Por qué no se puede entrenar ahora (vacío = todo libre)."""
    from app import lora, pipeline
    from app.engines import _busy
    q = pipeline.queue_stats()
    if q["pending"] or q["running"]:
        return "hay fotos o videos en cola"
    if any(v > 0 for v in list(_busy.values())):
        return "la GPU está generando algo"
    with session() as s:
        if s.query(Subject).filter(Subject.lora_status == "training").count() or lora.queue_ids():
            return "ya hay un entrenamiento en curso"
    try:
        from app import privado
        if any(e.get("status") in ("queued", "running", "waiting") for e in privado.entries()[-40:]):
            return "Shelton Hot está generando"
    except Exception:  # noqa: BLE001
        pass
    return ""


def candidates() -> list[str]:
    """Modelos para entrenar solas: las que ya tienen LoRA y están listas. Primero las que tienen fotos nuevas
    sin estudiar; después las que hace más tiempo no entrenan."""
    from app import lora
    rows = []
    with session() as s:
        for x in s.query(Subject).filter(Subject.lora_status == "ready"):
            info = x.lora_info or {}
            if not x.lora_name or info.get("adult_style") or x.lora_enabled is False:
                continue
            rows.append((x.id, info.get("finished") or ""))
    out = []
    for sid, finished in rows:
        try:
            st = lora.status(sid)
        except Exception:  # noqa: BLE001
            continue
        if st.get("images", 0) < lora.MIN_IMAGES or lora.saved_step(sid) <= 0:
            continue          # solo suma sobre lo aprendido: nunca empieza una de cero por su cuenta
        out.append((0 if st.get("images_new") else 1, finished, sid))
    return [sid for _, _, sid in sorted(out)]


def tick() -> dict:
    """Una revisión: si hay inactividad larga y todo está libre, pone a entrenar a la siguiente modelo."""
    from app import lora
    cfg = get()
    if not cfg["on"]:
        return {"started": False, "why": "apagado"}
    if idle_minutes() < cfg["hours"] * 60:
        return {"started": False, "why": "hay actividad"}
    why = busy_reason()
    if why:
        _busy_seen["t"] = time.time()
        return {"started": False, "why": why}
    rest = rested_minutes()
    if rest < float(cfg.get("rest_min") or 30):
        # Recién terminó algo (por ejemplo un entrenamiento): espera al menos 30 min de calma antes de seguir
        return {"started": False, "why": f"descansando: hace {round(rest)} min que terminó lo último"}
    for sid in candidates():
        try:
            r = lora.start(sid, steps=int(cfg["steps"]), mode="continue")
        except ValueError as e:
            log.info("Inactividad: no pude entrenar %s: %s", sid, e)
            continue
        with session() as s:
            name = s.get(Subject, sid).name
        from app.zona import adult_only
        with session() as s:
            private = adult_only(s.get(Subject, sid))
        shown = "🔒 Privado (Shelton Hot)" if private else name
        _state.update(last_auto=sid, last_name=shown, last_at=datetime.now().isoformat(timespec="minutes"))
        lora._update(sid, info={"auto_idle": True})
        events.publish({"type": "idle_training", "name": shown})
        log.info("Inactividad: %.0f min sin uso, entreno a %s", idle_minutes(), name)
        return {"started": True, "subject_id": sid, "name": shown, **r}
    return {"started": False, "why": "no hay modelos para entrenar"}


async def loop() -> None:
    while True:
        await asyncio.sleep(CHECK_EVERY)
        try:
            await asyncio.to_thread(tick)
        except Exception:  # noqa: BLE001
            log.exception("inactividad")


# ------------------------------------------------------------------ API
class IdleIn(BaseModel):
    rest_min: int | None = None
    on: bool | None = None
    hours: float | None = None
    steps: int | None = None


@router.post("/actividad")
async def api_touch():
    touch()
    return {"ok": True}


@router.get("/inactividad")
async def api_get():
    return {**get(), "idle_min": round(idle_minutes()), "busy": busy_reason(), "rest_min_now": round(rested_minutes()),
            "last_name": _state["last_name"],
            "last_at": _state["last_at"]}


@router.put("/inactividad")
async def api_put(body: IdleIn):
    return save(**body.model_dump())
