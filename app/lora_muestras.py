"""📸 Muestras del entrenamiento: cada 100 pasos la LoRA dibuja una foto de prueba y sigue entrenando.

Cada muestra queda numerada para siempre («Valentina #0042 · paso 20.700»). Con ese número se puede decir
«usa la foto 42» y Shelton deja activa la LoRA EXACTA de ese paso (se guarda un punto cada 100 pasos).

No cambia cómo se entrena: solo enciende las muestras de ai-toolkit y guarda un punto en cada muestra.
Se apaga con LORA_SAMPLE_EVERY=0.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from app import events
from app.config import settings
from app.db import Subject, session

log = logging.getLogger("shelton.lora_muestras")
router = APIRouter(prefix="/api")

KEEP_MIN = 12          # puntos guardados que se conservan (≈ los últimos 1.200 pasos) para poder elegir una muestra
EXTS = (".jpg", ".jpeg", ".png", ".webp")
_STEP = re.compile(r"__(\d{1,9})_\d+\.\w+$")          # ai-toolkit: 1712345678__000020700_0.jpg
_STEP_ALT = re.compile(r"_(\d{1,9})_\d+\.\w+$")
_lock = threading.Lock()


def every() -> int:
    try:
        return max(0, int(os.getenv("LORA_SAMPLE_EVERY", "100")))
    except ValueError:
        return 100


def apply_to_config(proc: dict) -> None:
    """Enciende las muestras de ai-toolkit (cada `every()` pasos) y un punto guardado en cada una."""
    n = every()
    if not n:
        return
    proc["sample"] = {**proc.get("sample", {}), "sample_every": n, "seed": 42, "walk_seed": False}
    proc["train"] = {**proc.get("train", {}), "disable_sampling": False, "skip_first_sample": True}
    save = proc.setdefault("save", {})
    save["save_every"] = min(int(save.get("save_every") or n), n)
    save["max_step_saves_to_keep"] = max(int(save.get("max_step_saves_to_keep") or 0), KEEP_MIN)


def _work(sid: str) -> Path:
    return settings.DATA_DIR / "lora" / sid


def _dir(sid: str) -> Path:
    return _work(sid) / "muestras"


def _index_file(sid: str) -> Path:
    return _dir(sid) / "muestras.json"


def index(sid: str) -> list[dict]:
    try:
        return json.loads(_index_file(sid).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


def step_of(name: str) -> int | None:
    m = _STEP.search(name) or _STEP_ALT.search(name)
    return int(m.group(1)) if m else None


def _name(sid: str) -> str:
    with session() as s:
        sub = s.get(Subject, sid)
        return sub.name if sub else ""


def collect(sid: str, extra_dirs: list[Path] | None = None) -> list[dict]:
    """Busca muestras nuevas en la carpeta del entrenamiento, les pone número y las guarda aparte (no se pierden
    aunque el entrenamiento se archive). Devuelve las nuevas."""
    out_dir = _work(sid) / "output"
    dirs = [d / "samples" for d in (out_dir.iterdir() if out_dir.exists() else []) if d.is_dir()] + (extra_dirs or [])
    files = [f for d in dirs if d.exists() for f in d.iterdir() if f.suffix.lower() in EXTS]
    if not files:
        return []
    with _lock:
        idx = index(sid)
        seen = {x["src"] for x in idx}
        fresh = sorted((f for f in files if f.name not in seen), key=lambda f: (step_of(f.name) or 0, f.name))
        if not fresh:
            return []
        _dir(sid).mkdir(parents=True, exist_ok=True)
        n = max((x["n"] for x in idx), default=0)
        new = []
        for f in fresh:
            st = step_of(f.name)
            if st is None:
                continue
            n += 1
            dest = f"{n:04d}_paso{st}{f.suffix.lower()}"
            shutil.copy2(f, _dir(sid) / dest)
            row = {"n": n, "step": st, "file": dest, "src": f.name, "at": datetime.now().isoformat(timespec="seconds")}
            idx.append(row)
            new.append(row)
        _index_file(sid).write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    if new:
        from app.zona import adult_only
        with session() as s:
            sub = s.get(Subject, sid)
            private = bool(sub and adult_only(sub))
        last = new[-1]
        events.publish({"type": "lora_sample", "subject_id": sid, "private": private, "n": last["n"],
                        "step": last["step"], "name": "" if private else _name(sid),
                        "url": "" if private else url(sid, last["file"])})
    return new


def url(sid: str, file: str) -> str:
    return f"/api/subjects/{sid}/lora/muestras/archivo/{file}"


def label(sid: str, row: dict) -> str:
    return f"{_name(sid)} #{row['n']:04d} · paso {row['step']:,}".replace(",", ".")


def find(sid: str, n: int) -> dict:
    row = next((x for x in index(sid) if x["n"] == int(n)), None)
    if not row:
        raise ValueError(f"No existe la foto #{int(n):04d} de {_name(sid)}")
    return row


def use(sid: str, n: int, strength: float | None = None) -> dict:
    """«Usa la foto 42»: deja activa la LoRA exacta del paso de esa muestra (y sigue entrenando desde ahí)."""
    from app import lora_compare
    row = find(sid, n)
    cands = lora_compare.candidates(sid)
    exact = next((c for c in cands if c["step"] == row["step"]), None)
    if not exact:
        near = min(cands, key=lambda c: abs(c["step"] - row["step"]), default=None)
        hint = (f" La más cercana guardada es la del paso {near['step']:,}".replace(",", ".") +
                " (puedes elegirla en 🔍 Comparar versiones)." if near else "")
        raise ValueError(f"La versión del paso {row['step']:,} ya no está guardada.".replace(",", ".") + hint)
    r = lora_compare.choose(sid, exact["key"], strength)
    from app.lora import _update
    _update(sid, info={"pinned_sample": row["n"], "pinned_step": row["step"]})
    return {**r, "n": row["n"], "step": row["step"], "label": label(sid, row)}


def listing(sid: str, limit: int = 300) -> dict:
    with session() as s:
        sub = s.get(Subject, sid)
        if not sub:
            raise ValueError("Ese modelo no existe")
        pinned = (sub.lora_info or {}).get("pinned_sample")
    rows = index(sid)[-limit:]
    return {"every": every(), "pinned": pinned, "name": _name(sid),
            "samples": [{**x, "url": url(sid, x["file"]), "label": label(sid, x)} for x in reversed(rows)]}


def last(sid: str) -> dict | None:
    idx = index(sid)
    if not idx:
        return None
    x = idx[-1]
    return {"n": x["n"], "step": x["step"], "url": url(sid, x["file"]), "label": label(sid, x)}


def overview(include_private: bool = False) -> list[dict]:
    """📸 Todas las modelos con muestras (la que entrena ahora primero, después la más reciente)."""
    from app.zona import adult_only
    out = []
    with session() as s:
        subs = [(x.id, x.name, x.lora_status, adult_only(x)) for x in s.query(Subject)]
    for sid, name, status, private in subs:
        if private and not include_private:
            continue
        rows = index(sid)
        if not rows:
            continue
        out.append({"id": sid, "name": "🔒 " + name if private else name, "training": status == "training",
                    "count": len(rows), "last_at": rows[-1].get("at", ""),
                    "samples": [{**x, "url": url(sid, x["file"]), "label": label(sid, x)} for x in reversed(rows[-60:])]})
    out.sort(key=lambda m: (not m["training"], m["last_at"]), reverse=False)
    out.sort(key=lambda m: m["training"], reverse=True)
    return out


# ------------------------------------------------------------------ API
@router.get("/muestras")
async def api_overview(request: Request):
    """Pantalla 📸 Muestras: cada foto del entrenamiento con su número y sus pasos."""
    admin = True
    try:
        from app.privado import _admin
        _admin(request)
    except HTTPException:
        admin = False
    return {"every": every(), "models": overview(include_private=admin)}


def _guard(sid: str, request: Request) -> None:
    """Las muestras de las 🔞 LoRAs de Shelton Hot son solo para administradores."""
    from app.zona import adult_only
    with session() as s:
        sub = s.get(Subject, sid)
        if not sub:
            raise HTTPException(404, "Ese modelo no existe")
        private = adult_only(sub)
    if private:
        from app.privado import _admin
        _admin(request)


@router.get("/subjects/{sid}/lora/muestras")
async def api_list(sid: str, request: Request):
    _guard(sid, request)
    try:
        return listing(sid)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/subjects/{sid}/lora/muestras/archivo/{file}")
async def api_file(sid: str, file: str, request: Request):
    from fastapi.responses import FileResponse
    _guard(sid, request)
    f = _dir(sid) / Path(file).name
    if not f.exists() or f.suffix.lower() not in EXTS:
        raise HTTPException(404, "No existe")
    return FileResponse(f, headers={"Cache-Control": "private, max-age=86400"})


@router.post("/subjects/{sid}/lora/muestras/{n}/usar")
async def api_use(sid: str, n: int, request: Request):
    _guard(sid, request)
    try:
        return use(sid, n)
    except ValueError as e:
        raise HTTPException(400, str(e))
