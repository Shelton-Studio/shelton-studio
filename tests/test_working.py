"""Panel En vivo: quién trabaja ahora y registro (agentes y motores)."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("IMAGE_ENGINE", "mock")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="shelton_live_"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import working  # noqa: E402
from app.db import AgentRun, init_db, session  # noqa: E402


def test_track_shows_active_and_logs():
    init_db()
    seen = []

    async def job():
        with working.track("motor_video", "Valentina en la playa", "google/veo-3.1", "p1", log=True):
            seen.append([dict(x) for x in working.snapshot()])
            await asyncio.sleep(0)
    asyncio.run(job())
    assert seen[0][0]["agent"] == "motor_video" and seen[0][0]["model"] == "google/veo-3.1"
    assert working.snapshot() == []                                   # al terminar, ya no aparece
    with session() as s:
        r = s.query(AgentRun).filter(AgentRun.agent == "motor_video").order_by(AgentRun.id.desc()).first()
        assert r and r.ok and r.model == "google/veo-3.1" and "Valentina" in r.summary


def test_track_logs_failures():
    init_db()
    try:
        with working.track("motor_imagen", "prueba que falla", "x", None, log=True):
            raise RuntimeError("sin GPU")
    except RuntimeError:
        pass
    assert working.snapshot() == []
    with session() as s:
        r = s.query(AgentRun).filter(AgentRun.agent == "motor_imagen").order_by(AgentRun.id.desc()).first()
        assert r and not r.ok and "sin GPU" in r.error
