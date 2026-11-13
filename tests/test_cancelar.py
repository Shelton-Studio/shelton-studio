"""⏹ Cancelar una foto en cola o haciéndose: libera el lugar y no tumba al trabajador."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ["IMAGE_ENGINE"] = "mock"
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="shelton_cancel_"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_chain  # noqa: E402,F401

from app import pipeline  # noqa: E402
from app.db import Job, Post, init_db, session  # noqa: E402


def test_cancel_running_and_pending_photos(monkeypatch):
    init_db()
    started = asyncio.Event()

    async def slow(target, payload):
        started.set()
        await asyncio.sleep(30)                      # «esperando la GPU» para siempre

    monkeypatch.setitem(pipeline.__dict__, "_create_post", slow)

    with session() as s:
        a, b = Post(idea="a", status="generating"), Post(idea="b", status="queued")
        s.add_all([a, b])
        s.flush()
        ja, jb = Job(kind="create", post_id=a.id, status="pending", payload={}), Job(kind="create", post_id=b.id, status="pending", payload={})
        s.add_all([ja, jb])
        s.flush()
        ida, idb, jida, jidb = a.id, b.id, ja.id, jb.id

    async def go():
        run = asyncio.create_task(pipeline._run_job(jida))
        await asyncio.wait_for(started.wait(), 5)
        assert pipeline.cancel_post(ida) and pipeline.cancel_post(idb)
        await asyncio.wait_for(run, 5)               # termina enseguida, sin tumbar al trabajador
        await pipeline._run_job(jidb)                # la que estaba en cola ni arranca
    asyncio.run(go())
    with session() as s:
        assert s.get(Job, jida).status == "canceled" and s.get(Job, jidb).status == "canceled"
        assert s.get(Post, ida).status == "failed" and "Cancelado" in s.get(Post, ida).error
        assert s.get(Post, idb).status == "failed"
        for pid in (ida, idb):                       # no dejar posts de prueba a las otras pruebas
            s.delete(s.get(Post, pid))
        for jid in (jida, jidb):
            s.delete(s.get(Job, jid))
