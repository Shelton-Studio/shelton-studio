"""Consumo: de dónde salen los costos y cuánto se gasta.

- Nube (OpenRouter, fal.ai, Replicate, OpenAI, Gemini, ElevenLabs…): cada llamada queda registrada. Si el proveedor
  informa el costo (OpenRouter lo hace), se guarda el costo REAL; si no, se estima con una tabla de precios y se marca
  como «estimado».
- GPU local (ComfyUI, voz, entrenamiento LoRA): no cobra por uso, pero gasta luz. Se mide la GPU cada 30 s con
  nvidia-smi (vatios, % de uso, memoria y qué programa la ocupa) y se calcula el costo con el precio del kWh.
- Cuenta de OpenRouter: gasto real de hoy, la semana y el mes, tope y saldo, leído directo de OpenRouter.
Nada se borra: el historial queda en la base (tablas usage_events y gpu_samples).
"""
from __future__ import annotations

import asyncio
import contextvars
import csv
import io
import json
import logging
import shutil
import time
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import httpx
from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func

from app.config import settings
from app.db import GpuSample, UsageEvent, now, session

log = logging.getLogger("shelton.consumo")
router = APIRouter(prefix="/api")

KINDS = {"cerebro": "Cerebro (texto)", "imagen": "Imágenes", "video": "Video", "voz": "Voz",
         "transcripcion": "Dictado por voz", "sonido": "Efectos de sonido", "lora": "Entrenamiento LoRA"}
PROVIDERS = {"openrouter": "OpenRouter", "openrouter_image": "OpenRouter", "openrouter_video": "OpenRouter",
             "openrouter_tts": "OpenRouter", "anthropic": "Anthropic", "fal": "fal.ai", "replicate": "Replicate",
             "openai_images": "OpenAI", "gemini_image": "Google Gemini", "http_engine": "API propia",
             "elevenlabs": "ElevenLabs", "http_tts": "API propia", "comfyui": "GPU local · ComfyUI",
             "local_tts": "GPU local · voz", "whisper": "Local · Whisper", "ai-toolkit": "GPU local · ai-toolkit",
             "mock": "Simulado (demo)", "cloud": "Nube"}
LOCAL = {"comfyui", "local_tts", "whisper", "ai-toolkit", "mock"}

# Precio por millón de tokens (entrada, salida) cuando el proveedor no informa el costo
TOKEN_PRICES = [("opus", (5, 25)), ("sonnet", (3, 15)), ("haiku", (1, 5)), ("gemini-2.5-pro", (1.25, 10)),
                ("gemini-2.5-flash", (0.3, 2.5)), ("gpt-4o-mini", (0.15, 0.6)), ("gpt-4o", (2.5, 10))]
DEFAULT_TOKEN_PRICE = (3, 15)
# Precio estimado por unidad (imagen, video, audio) cuando el proveedor no lo informa
UNIT_PRICES = {("fal", "imagen"): 0.03, ("fal", "video"): 0.40, ("replicate", "imagen"): 0.03,
               ("replicate", "video"): 0.40, ("openai_images", "imagen"): 0.04, ("gemini_image", "imagen"): 0.039,
               ("openrouter_image", "imagen"): 0.039, ("openrouter_video", "video"): 0.50,
               ("elevenlabs", "voz"): 0.03, ("openrouter_tts", "voz"): 0.01, ("openrouter", "transcripcion"): 0.002}
DEFAULT_GPU_WATTS = 300.0
SAMPLE_EVERY = 30


# ------------------------------------------------------------------ ajustes (precio de la luz)
def _settings_file() -> Path:
    return settings.DATA_DIR / "consumo_ajustes.json"


def get_settings() -> dict:
    base = {"kwh_price": 0.15, "currency": "USD"}
    try:
        f = _settings_file()
        if f.exists():
            base.update(json.loads(f.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        pass
    return base


def save_settings(kwh_price: float) -> dict:
    data = {**get_settings(), "kwh_price": max(0.0, float(kwh_price))}
    _settings_file().parent.mkdir(parents=True, exist_ok=True)
    _settings_file().write_text(json.dumps(data), encoding="utf-8")
    return data


# ------------------------------------------------------------------ registrar
_box: contextvars.ContextVar[dict | None] = contextvars.ContextVar("consumo_box", default=None)


def report_cost(usd) -> None:
    """Un motor avisa el costo real que informó el proveedor (se suma a la medición en curso)."""
    b = _box.get()
    if b is None or usd is None:
        return
    try:
        b["usd"] = b.get("usd", 0.0) + float(usd)
        b["real"] = True
    except (TypeError, ValueError):
        pass


def token_cost(model: str, tin: int, tout: int) -> float:
    m = (model or "").lower()
    try:
        from app.api import PRICES
        if model in PRICES:
            pin, pout = PRICES[model]
            return (tin * pin + tout * pout) / 1_000_000
    except Exception:  # noqa: BLE001
        pass
    pin, pout = next((p for k, p in TOKEN_PRICES if k in m), DEFAULT_TOKEN_PRICE)
    return (tin * pin + tout * pout) / 1_000_000


_last_power: dict = {"w": None, "t": 0.0}


def _gpu_watts() -> float:
    w = _last_power.get("w")
    return float(w) if w and time.time() - _last_power["t"] < 300 else DEFAULT_GPU_WATTS


def record(kind: str, provider: str, model: str = "", *, seconds: float = 0, cost_usd: float | None = None,
           tokens_in: int = 0, tokens_out: int = 0, agent: str = "", post_id: str = "", ok: bool = True,
           note: str = "", watts: float | None = None) -> None:
    """Guarda un consumo. Sin costo real: local → luz de la GPU; nube → tabla de precios (marcado «estimado»)."""
    energy = 0.0
    if cost_usd is not None:
        source, cost = "real", float(cost_usd)
    elif provider in LOCAL:
        source = "local"
        if watts is None and seconds > 120:          # trabajos largos: el promedio medido durante ese rato
            try:
                with session() as s:
                    avg = s.query(func.avg(GpuSample.power_w)).filter(
                        GpuSample.created_at >= now() - timedelta(seconds=seconds)).scalar()
                watts = float(avg) if avg else None
            except Exception:  # noqa: BLE001
                watts = None
        energy = seconds * (watts if watts is not None else _gpu_watts()) / 3600 if provider != "mock" else 0.0
        cost = energy / 1000 * get_settings()["kwh_price"]
    else:
        source = "estimado"
        if not ok:
            cost = 0.0                      # las llamadas que fallan normalmente no se cobran
        elif tokens_in or tokens_out:
            cost = token_cost(model, tokens_in, tokens_out)
        else:
            cost = UNIT_PRICES.get((provider, kind), 0.0)
    try:
        with session() as s:
            s.add(UsageEvent(kind=kind, provider=provider or "", model=(model or "")[:200], agent=agent or "",
                             post_id=post_id or "", tokens_in=int(tokens_in or 0), tokens_out=int(tokens_out or 0),
                             seconds=round(float(seconds or 0), 2), cost_usd=round(cost, 6), cost_source=source,
                             energy_wh=round(energy, 3), ok=ok, note=(note or "")[:500]))
    except Exception:  # noqa: BLE001
        log.exception("no se pudo registrar el consumo")


@contextmanager
def measure(kind: str, provider: str, model: str = "", **kw):
    """Mide una llamada (tiempo + costo real si el motor lo informa con report_cost) y la registra al final."""
    box = {"usd": 0.0, "real": False, "model": model}
    tok = _box.set(box)
    t0, ok = time.monotonic(), True
    try:
        yield box
    except BaseException:
        ok = False
        raise
    finally:
        _box.reset(tok)
        record(kind, provider, box.get("model") or model, seconds=time.monotonic() - t0,
               cost_usd=box["usd"] if box["real"] else None, ok=ok, **kw)


def record_llm(agent: str, provider: str, model: str, usage, ok: bool, seconds: float) -> None:
    tin = (getattr(usage, "input_tokens", 0) or 0) + (getattr(usage, "cache_read_input_tokens", 0) or 0) \
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0) if usage else 0
    tout = (getattr(usage, "output_tokens", 0) or 0) if usage else 0
    real = getattr(usage, "cost", None) if usage else None
    record("cerebro", provider, model, seconds=seconds, cost_usd=real, tokens_in=tin, tokens_out=tout,
           agent=agent, ok=ok)


# ------------------------------------------------------------------ motores medidos
METERED = {"generate": "imagen", "generate_with_refs": "imagen", "generate_with_loras": "imagen", "edit": "imagen",
           "video": "video", "sound": "sonido"}


class Metered:
    """Envuelve un motor de imagen/video/sonido: cada llamada queda registrada con su tiempo y costo."""

    def __init__(self, inner):
        object.__setattr__(self, "_inner", inner)

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        kind = METERED.get(name)
        if not kind or not callable(attr):
            return attr
        inner = self._inner

        async def run(*args, **kwargs):
            model = getattr(inner, "last_model", "") or (getattr(inner, "cfg", None) or {}).get("model", "") \
                or (getattr(inner, "cfg", None) or {}).get("preset", "")
            with measure(kind, getattr(inner, "name", "cloud"), model) as box:
                result = await attr(*args, **kwargs)
                box["model"] = getattr(inner, "last_model", "") or box.get("model")
                return result
        return run

    def __setattr__(self, name, value):
        setattr(self._inner, name, value)


def metered(engine):
    return engine if isinstance(engine, Metered) else Metered(engine)


# ------------------------------------------------------------------ GPU
def _who(pid: int) -> str:
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="ignore").lower()
    except Exception:  # noqa: BLE001
        cmd = ""
    if "ai-toolkit" in cmd or ("run.py" in cmd and "shelton" in cmd):
        return "Entrenador LoRA (Shelton)"
    if "comfyui" in cmd or "main.py --listen" in cmd:
        return "ComfyUI de Shelton" if "shelton" in cmd else "ComfyUI de otro programa"
    if "tts_server" in cmd:
        return "Voz local (Shelton)"
    if "app.main" in cmd or "shelton" in cmd:
        return "Shelton"
    try:
        name = Path(f"/proc/{pid}/comm").read_text().strip()
    except Exception:  # noqa: BLE001
        name = ""
    return f"Otro programa ({name})" if name else "Otro programa"


async def _run(*cmd: str) -> str:
    p = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await asyncio.wait_for(p.communicate(), 10)
    return out.decode(errors="ignore")


def _num(x) -> float:
    try:
        return float(str(x).strip())
    except ValueError:
        return 0.0


async def gpu_now() -> dict:
    """Estado de la(s) GPU en este momento: uso, memoria, vatios, temperatura y quién la ocupa."""
    gpus = []
    if shutil.which("nvidia-smi"):
        try:
            out = await _run("nvidia-smi", "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,"
                             "power.draw,power.limit,temperature.gpu,pci.bus_id", "--format=csv,noheader,nounits")
            for line in out.strip().splitlines():
                c = [x.strip() for x in line.split(",")]
                if len(c) < 9:
                    continue
                gpus.append({"index": int(_num(c[0])), "name": c[1], "util": _num(c[2]), "mem_used_mb": _num(c[3]),
                             "mem_total_mb": _num(c[4]), "power_w": _num(c[5]), "power_limit_w": _num(c[6]),
                             "temp_c": _num(c[7]), "bus": c[8].lower(), "who": {}})
            apps = await _run("nvidia-smi", "--query-compute-apps=pid,used_memory,gpu_bus_id",
                              "--format=csv,noheader,nounits")
            for line in apps.strip().splitlines():
                c = [x.strip() for x in line.split(",")]
                if len(c) < 3:
                    continue
                g = next((g for g in gpus if g["bus"].endswith(c[2].lower()[-12:])), gpus[0] if gpus else None)
                if g is not None:
                    w = _who(int(_num(c[0])))
                    g["who"][w] = g["who"].get(w, 0) + _num(c[1])
        except Exception:  # noqa: BLE001
            log.debug("nvidia-smi falló", exc_info=True)
    if not gpus:                                    # GPU en otra máquina: lo que informe ComfyUI
        from app.lora import _comfy_urls
        async with httpx.AsyncClient(timeout=4) as c:
            for i, url in enumerate(_comfy_urls()):
                try:
                    d = (await c.get(f"{url}/system_stats")).json()
                except Exception:  # noqa: BLE001
                    continue
                for dev in d.get("devices", []):
                    tot, free = dev.get("vram_total", 0) / 2**20, dev.get("vram_free", 0) / 2**20
                    gpus.append({"index": i, "name": dev.get("name", "GPU"), "util": None, "mem_used_mb": tot - free,
                                 "mem_total_mb": tot, "power_w": None, "power_limit_w": None, "temp_c": None,
                                 "who": {}, "remote": url})
    from app import working
    try:
        names = _agent_names()
        busy = [{"agent": names.get(t.get("agent"), t.get("agent")), "what": t.get("task") or "", "model": t.get("model") or ""}
                for t in working.snapshot()]
    except Exception:  # noqa: BLE001
        busy = []
    total_w = sum(g["power_w"] or 0 for g in gpus)
    if any(g["power_w"] for g in gpus):
        _last_power.update(w=total_w, t=time.time())
    return {"gpus": gpus, "source": "nvidia-smi" if gpus and "remote" not in gpus[0] else ("comfyui" if gpus else "none"),
            "kwh_price": get_settings()["kwh_price"], "working": busy}


async def sample_loop() -> None:
    """Cada 30 s guarda una lectura de la GPU (para calcular la luz gastada y el uso por programa)."""
    while True:
        try:
            st = await gpu_now()
            if st["source"] == "nvidia-smi":
                with session() as s:
                    for g in st["gpus"]:
                        s.add(GpuSample(gpu=g["index"], util=g["util"] or 0, mem_used_mb=g["mem_used_mb"] or 0,
                                        mem_total_mb=g["mem_total_mb"] or 0, power_w=g["power_w"] or 0,
                                        temp_c=g["temp_c"] or 0, who=g["who"]))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.debug("muestra de GPU falló", exc_info=True)
        await asyncio.sleep(SAMPLE_EVERY)


# ------------------------------------------------------------------ OpenRouter (gasto real de la cuenta)
_or_cache: dict = {"t": 0.0, "data": None}


async def openrouter_account() -> dict | None:
    from app.llm import openrouter_config
    key = openrouter_config()[0]
    if not key:
        return None
    if _or_cache["data"] and time.time() - _or_cache["t"] < 60:
        return _or_cache["data"]
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"})
        d = (r.json() or {}).get("data") or {}
        if r.status_code >= 400:
            raise RuntimeError(r.text[:200])
        data = {"ok": True, "label": d.get("label") or "", "usage_total": d.get("usage"),
                "usage_today": d.get("usage_daily"), "usage_week": d.get("usage_weekly"),
                "usage_month": d.get("usage_monthly"), "limit": d.get("limit"),
                "limit_remaining": d.get("limit_remaining"), "limit_reset": d.get("limit_reset"),
                "free_tier": d.get("is_free_tier")}
    except Exception as e:  # noqa: BLE001
        data = {"ok": False, "error": str(e)[:200]}
    _or_cache.update(t=time.time(), data=data)
    return data


# ------------------------------------------------------------------ resumen
def _agent_names() -> dict:
    try:
        from app.agents.registry import AGENTS
        from app.working import ENGINE_NAMES
        return {**{k: v.get("name", k) for k, v in AGENTS.items()}, **ENGINE_NAMES}
    except Exception:  # noqa: BLE001
        return {}


def _start(days: int):
    end = now()
    return end.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1), end


def _group(rows, key) -> list[dict]:
    out: dict[str, dict] = {}
    for r in rows:
        k = key(r) or "—"
        g = out.setdefault(k, {"name": k, "cost": 0.0, "count": 0, "tokens": 0, "seconds": 0.0, "energy_wh": 0.0,
                               "real": 0.0, "estimated": 0.0, "local": 0.0, "failed": 0})
        g["cost"] += r.cost_usd
        g["count"] += 1
        g["tokens"] += r.tokens_in + r.tokens_out
        g["seconds"] += r.seconds
        g["energy_wh"] += r.energy_wh
        g["failed"] += 0 if r.ok else 1
        g[{"real": "real", "local": "local"}.get(r.cost_source, "estimated")] += r.cost_usd
    for g in out.values():
        for k in ("cost", "real", "estimated", "local"):
            g[k] = round(g[k], 4)
        g["seconds"] = round(g["seconds"])
        g["energy_wh"] = round(g["energy_wh"], 1)
    return sorted(out.values(), key=lambda g: (-g["cost"], -g["count"]))


def _event(r) -> dict:
    return {"at": r.created_at.isoformat(timespec="seconds"), "kind": r.kind, "kind_label": KINDS.get(r.kind, r.kind),
            "provider": PROVIDERS.get(r.provider, r.provider), "model": r.model, "agent": r.agent,
            "tokens": r.tokens_in + r.tokens_out, "seconds": round(r.seconds, 1), "cost": round(r.cost_usd, 5),
            "source": r.cost_source, "ok": r.ok, "post_id": r.post_id, "note": r.note}


def summary(days: int = 7) -> dict:
    days = max(1, min(int(days), 365))
    start, end = _start(days)
    kwh = get_settings()["kwh_price"]
    with session() as s:
        rows = s.query(UsageEvent).filter(UsageEvent.created_at >= start).order_by(UsageEvent.created_at).all()
        samples = s.query(GpuSample).filter(GpuSample.created_at >= start).order_by(GpuSample.created_at).all()
        first = s.query(func.min(UsageEvent.created_at)).scalar()
        for r in rows:
            s.expunge(r)
        for x in samples:
            s.expunge(x)
    names = _agent_names()
    cloud = [r for r in rows if r.provider not in LOCAL]
    # Por día y tipo (para el gráfico)
    daily = []
    for i in range(days):
        d0 = start + timedelta(days=i)
        d1 = d0 + timedelta(days=1)
        day = {"date": d0.date().isoformat(), "by_kind": {}}
        for r in rows:
            if d0 <= r.created_at < d1 and r.provider not in LOCAL:
                day["by_kind"][r.kind] = round(day["by_kind"].get(r.kind, 0) + r.cost_usd, 5)
        e_wh = sum(x.power_w * SAMPLE_EVERY / 3600 for x in samples if d0 <= x.created_at < d1)
        day["gpu_kwh"] = round(e_wh / 1000, 3)
        day["gpu_cost"] = round(e_wh / 1000 * kwh, 4)
        daily.append(day)
    # Luz de la GPU (medida) y quién la ocupó
    energy_wh = sum(x.power_w * SAMPLE_EVERY / 3600 for x in samples)
    who: dict[str, float] = {}
    for x in samples:
        for k, mb in (x.who or {}).items():
            who[k] = who.get(k, 0) + float(mb)
    tot_who = sum(who.values()) or 1
    busy = [x for x in samples if x.util >= 20]
    gpu = {"measured": bool(samples), "energy_kwh": round(energy_wh / 1000, 3),
           "cost": round(energy_wh / 1000 * kwh, 4), "kwh_price": kwh,
           "avg_power_w": round(sum(x.power_w for x in samples) / len(samples)) if samples else None,
           "max_power_w": round(max((x.power_w for x in samples), default=0)) if samples else None,
           "busy_hours": round(len(busy) * SAMPLE_EVERY / 3600, 2),
           "measured_hours": round(len(samples) * SAMPLE_EVERY / 3600, 2),
           "avg_util": round(sum(x.util for x in samples) / len(samples)) if samples else None,
           "max_temp_c": round(max((x.temp_c for x in samples), default=0)) if samples else None,
           "who": sorted([{"name": k, "share": round(v / tot_who * 100, 1)} for k, v in who.items()],
                         key=lambda w: -w["share"])}
    cloud_cost = sum(r.cost_usd for r in cloud)
    real_cost = sum(r.cost_usd for r in cloud if r.cost_source == "real")
    total = cloud_cost + gpu["cost"]
    elapsed_days = max((end - start).total_seconds() / 86400, 1 / 24)
    return {
        "days": days, "from": start.isoformat(timespec="seconds"), "tracking_since": first.isoformat() if first else None,
        "totals": {"total": round(total, 4), "cloud": round(cloud_cost, 4), "cloud_real": round(real_cost, 4),
                   "cloud_estimated": round(cloud_cost - real_cost, 4), "gpu_light": gpu["cost"],
                   "events": len(rows), "failed": sum(1 for r in rows if not r.ok),
                   "tokens": sum(r.tokens_in + r.tokens_out for r in rows),
                   "projection_month": round(total / elapsed_days * 30, 2)},
        "by_kind": [{**g, "label": KINDS.get(g["name"], g["name"])} for g in _group(rows, lambda r: r.kind)],
        "by_provider": _group(rows, lambda r: PROVIDERS.get(r.provider, r.provider)),
        "by_model": _group([r for r in rows if r.model], lambda r: r.model)[:25],
        "by_agent": [{**g, "label": names.get(g["name"], g["name"])}
                     for g in _group([r for r in rows if r.agent], lambda r: r.agent)],
        "daily": daily, "gpu": gpu,
        "top": [_event(r) for r in sorted(rows, key=lambda r: -r.cost_usd)[:12] if r.cost_usd > 0],
        "recent": [_event(r) for r in reversed(rows[-60:])],
        "kinds": KINDS,
    }


# ------------------------------------------------------------------ API
@router.get("/consumo")
async def consumo(days: int = 7):
    data = await asyncio.to_thread(summary, days)
    data["openrouter"] = await openrouter_account()
    return data


@router.get("/consumo/gpu")
async def consumo_gpu():
    return await gpu_now()


class AjustesIn(BaseModel):
    kwh_price: float


@router.get("/consumo/ajustes")
async def consumo_ajustes():
    return get_settings()


@router.post("/consumo/ajustes")
async def consumo_ajustes_save(body: AjustesIn):
    return save_settings(body.kwh_price)


@router.get("/consumo/csv")
async def consumo_csv(days: int = 30):
    start, _ = _start(max(1, min(int(days), 3650)))
    with session() as s:
        rows = s.query(UsageEvent).filter(UsageEvent.created_at >= start).order_by(UsageEvent.created_at).all()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["fecha", "tipo", "proveedor", "modelo", "agente", "tokens_entrada", "tokens_salida", "segundos",
                    "costo_usd", "origen_del_costo", "luz_wh", "ok", "post", "nota"])
        for r in rows:
            w.writerow([r.created_at.isoformat(timespec="seconds"), KINDS.get(r.kind, r.kind),
                        PROVIDERS.get(r.provider, r.provider), r.model, r.agent, r.tokens_in, r.tokens_out,
                        r.seconds, f"{r.cost_usd:.6f}", r.cost_source, r.energy_wh, "sí" if r.ok else "no",
                        r.post_id, r.note])
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="consumo_{days}dias.csv"'})
