"""⚖️ Calificar al juez: tu nota y si acertó quedan guardadas y van al aprendizaje."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ["IMAGE_ENGINE"] = "mock"
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="shelton_juez_"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_chain  # noqa: E402,F401

from app import main  # noqa: E402
from app.db import Feedback, Post, init_db, session  # noqa: E402


def test_rate_the_judge():
    init_db()
    with session() as s:
        p = Post(idea="Valentina en un café", status="ready", qa_score=72, qa_notes="mano rara")
        s.add(p)
        s.flush()
        pid = p.id
    out = asyncio.run(main.rate_judge(pid, main.JudgeRatingIn(score=91, agree=False, note="la mano está bien")))
    assert out["human_score"] == 91 and out["judge_review"] == "disagree"
    with session() as s:
        f = s.query(Feedback).filter(Feedback.post_id == pid, Feedback.action == "judge_rating").one()
        assert f.detail["judge_score"] == 72 and f.detail["human_score"] == 91 and f.detail["agree"] is False
        s.delete(f)
        s.delete(s.get(Post, pid))
