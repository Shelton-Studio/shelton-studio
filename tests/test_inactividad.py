"""🌙 Entrenar sola cuando no hay nadie: solo con inactividad larga y todo libre; nunca empieza una de cero."""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["IMAGE_ENGINE"] = "mock"
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="shelton_idle_"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_chain  # noqa: E402,F401

from app import inactividad, lora  # noqa: E402
from app.db import Subject, init_db, session  # noqa: E402


def test_trains_only_after_long_inactivity_and_when_everything_is_free(monkeypatch):
    init_db()
    with session() as s:
        sub = Subject(name="Brisa Noche", kind="person", description="mujer inventada de 30 años",
                      lora_status="ready", lora_name="brisa.safetensors")
        s.add(sub)
        s.flush()
        sid = sub.id
    started = []
    monkeypatch.setattr(lora, "start", lambda i, **k: started.append((i, k)) or {"started": True})
    monkeypatch.setattr(lora, "status", lambda i: {"images": 20, "images_new": 3 if i == sid else 0})
    monkeypatch.setattr(lora, "saved_step", lambda i: 2000 if i == sid else 0)
    monkeypatch.setattr(inactividad, "busy_reason", lambda: "")
    inactividad.save(on=True, hours=2, steps=1000)
    inactividad.touch()
    assert inactividad.tick() == {"started": False, "why": "hay actividad"} and not started
    inactividad._last["t"] = time.time() - 3 * 3600                    # 3 h sin nadie
    monkeypatch.setattr(inactividad, "busy_reason", lambda: "hay fotos o videos en cola")
    assert not inactividad.tick()["started"]                          # algo trabajando: espera
    monkeypatch.setattr(inactividad, "busy_reason", lambda: "")
    assert "descansando" in inactividad.tick()["why"]                 # recién terminó algo: espera 30 min de calma
    inactividad._busy_seen["t"] = time.time() - 31 * 60
    r = inactividad.tick()
    assert r["started"] and started[0][0] == sid and started[0][1]["mode"] == "continue" and started[0][1]["steps"] == 1000
    # apagado: no hace nada
    inactividad.save(on=False)
    assert inactividad.tick()["why"] == "apagado"
    inactividad.save(on=True)


def test_never_starts_a_model_from_zero(monkeypatch):
    init_db()
    with session() as s:
        sub = Subject(name="Sin Punto", kind="person", description="mujer inventada", lora_status="ready",
                      lora_name="x.safetensors")
        s.add(sub)
        s.flush()
        sid = sub.id
    monkeypatch.setattr(lora, "status", lambda i: {"images": 20, "images_new": 0})
    monkeypatch.setattr(lora, "saved_step", lambda i: 0)
    assert sid not in inactividad.candidates()


def test_clicks_and_requests_count_as_activity():
    from fastapi.testclient import TestClient
    from app.main import app
    inactividad._last["t"] = 0
    with TestClient(app) as c:
        c.post("/api/actividad")
    assert inactividad.idle_minutes() < 1


def test_after_a_training_ends_it_rests_30_minutes_before_the_next(monkeypatch):
    from datetime import datetime, timedelta
    init_db()
    with session() as s:
        sub = Subject(name="Recien Terminada", kind="person", description="mujer inventada", lora_status="ready",
                      lora_name="r.safetensors",
                      lora_info={"finished": (datetime.now() - timedelta(minutes=10)).isoformat(timespec="seconds")})
        s.add(sub)
        s.flush()
        sid = sub.id
    monkeypatch.setattr(inactividad, "busy_reason", lambda: "")
    inactividad._busy_seen["t"] = time.time() - 5 * 3600
    inactividad._last["t"] = time.time() - 5 * 3600
    inactividad.save(on=True, hours=2)
    assert "descansando" in inactividad.tick()["why"]
    with session() as s:
        s.delete(s.get(Subject, sid))
