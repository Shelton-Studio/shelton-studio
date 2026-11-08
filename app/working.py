"""Quién está trabajando ahora mismo (agentes y motores) y registro del trabajo de los motores.

Cada llamada de un agente (Claude/OpenRouter) y cada trabajo de un motor (imagen, video, voz, sonido) se marca
al empezar y al terminar: la web lo muestra en vivo y todo queda guardado en la Actividad.
"""
import itertools
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from app import events

_active: dict[int, dict] = {}
_ids = itertools.count(1)

# Pseudo-agentes para los motores (no son agentes de Claude, pero también trabajan)
ENGINE_NAMES = {"motor_imagen": "Motor de imagen", "motor_video": "Motor de video",
                "motor_voz": "Motor de voz", "motor_sonido": "Motor de sonido"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def start(agent: str, task: str = "", model: str = "", post_id: str | None = None) -> int:
    i = next(_ids)
    _active[i] = {"id": i, "agent": agent, "task": task[:200], "model": model, "post_id": post_id, "since": _now_iso()}
    events.publish({"type": "working", "active": snapshot()})
    return i


def end(i: int) -> None:
    if _active.pop(i, None) is not None:
        events.publish({"type": "working", "active": snapshot()})


def snapshot() -> list[dict]:
    return sorted(_active.values(), key=lambda x: x["id"])


@contextmanager
def track(agent: str, task: str = "", model: str = "", post_id: str | None = None, log: bool = False):
    """Marca trabajo en curso. Con log=True además lo guarda en la Actividad (para los motores)."""
    i = start(agent, task, model, post_id)
    t0 = time.monotonic()
    ok, err = True, ""
    try:
        yield
    except Exception as e:
        ok, err = False, str(e)[:500]
        raise
    finally:
        end(i)
        if log:
            try:
                from app.db import AgentRun, session
                with session() as s:
                    s.add(AgentRun(agent=agent, ok=ok, model=model, duration_ms=int((time.monotonic() - t0) * 1000),
                                   summary=task[:300], error=err))
                events.publish({"type": "run", "agent": agent, "ok": ok})
            except Exception:
                pass
