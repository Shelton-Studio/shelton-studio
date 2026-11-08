"""Si Shelton se reinicia con un reel a medio montar, al arrancar lo retoma y lo termina."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ["IMAGE_ENGINE"] = "mock"
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="shelton_resume_"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_chain  # noqa: E402,F401  (subagentes simulados)
from app import pipeline  # noqa: E402
from app.db import Job, Post, init_db, session  # noqa: E402


def test_interrupted_reel_is_resumed():
    async def main():
        # Otras pruebas corren en otro event loop: cola y candados nuevos para este (en producción hay un solo loop)
        from app import engines
        pipeline._queue = asyncio.Queue()
        engines._locks.clear()
        init_db()
        await pipeline.start_workers()
        rid = pipeline.new_reel_from_scenes(["escena uno", "escena dos"], "reel de prueba")
        await pipeline.wait_for([rid], timeout=120)
        with session() as s:
            assert s.get(Post, rid).status == "ready", s.get(Post, rid).error
            # Simula el corte: el reel quedó "montándose" y su trabajo de montaje se perdió
            s.get(Post, rid).status = "generating"
            s.get(Post, rid).video_path = ""
            for j in s.query(Job).filter(Job.post_id == rid, Job.kind == "reel"):
                s.delete(j)
            scenes = s.query(Post).filter(Post.reel_id == rid).count()
        assert scenes == 2
        pipeline._done.pop(rid, None)
        pipeline._reels_in_progress.discard(rid)   # un reinicio real arranca con la memoria vacía
        await pipeline.stop_workers()
        await pipeline.start_workers()          # "reinicio"
        for _ in range(200):
            await asyncio.sleep(0.1)
            with session() as s:
                p = s.get(Post, rid)
                if p.status == "ready" and p.video_path:
                    break
        assert p.status == "ready" and Path(p.video_path).exists(), (p.status, p.error)
        await pipeline.stop_workers()
    asyncio.run(main())
