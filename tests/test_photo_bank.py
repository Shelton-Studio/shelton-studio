"""Banco de fotos -> modelo del banco -> contenido consistente (varias referencias)."""
import io
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent


def _png(color) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (300, 200), color).save(b, "PNG")
    return b.getvalue()


@pytest.fixture(scope="module")
def srv():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen([sys.executable, str(ROOT / "tests" / "fake_server.py"), str(port)], cwd=ROOT,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            httpx.get(base + "/api/health", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    yield httpx.Client(base_url=base, timeout=120)
    proc.terminate()
    proc.wait(10)


def test_bank_from_photos_and_consistent_content(srv):
    files = [("files", (f"mustang{i}.png", _png(c), "image/png")) for i, c in enumerate(["red", "darkred", "crimson"])]
    sub = srv.post("/api/subjects/from-photos", files=files, data={"name": "Mustang Rojo", "kind": "auto"}).json()
    assert sub["kind"] == "car" and len(sub["references"]) == 3 and "3 photos" in sub["description"]
    # Volver a subir con el mismo nombre suma fotos al mismo modelo
    more = srv.post("/api/subjects/from-photos", files=[("files", ("m4.png", _png("maroon"), "image/png"))],
                    data={"name": "Mustang Rojo"}).json()
    assert more["id"] == sub["id"] and len(more["references"]) == 4
    # Contenido con el modelo: la cadena termina y queda vinculado al modelo
    r = srv.post("/api/posts", json={"idea": "Mustang Rojo en una carretera de montaña", "subjects": ["Mustang Rojo"]}).json()
    pid = r["ids"][0]
    for _ in range(120):
        p = srv.get(f"/api/posts/{pid}").json()
        if p["status"] in ("ready", "failed"):
            break
        time.sleep(0.3)
    assert p["status"] == "ready" and p["subject_ids"] == [sub["id"]]


def test_chat_creates_model_from_pasted_photos(srv):
    ups = [srv.post("/api/upload", files={"file": (f"rosa{i}.png", _png(c), "image/png")}).json()["id"]
           for i, c in enumerate(["pink", "hotpink"])]
    events = []
    msg = "TOOL create_subject_from_photos " + json.dumps({"name": "Rosa del jardín", "post_ids": ups})
    with srv.stream("POST", "/api/chat", json={"message": msg, "upload_ids": ups}, timeout=120) as resp:
        for line in resp.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    final = next(e for e in events if e["type"] == "message")["text"]
    assert final.startswith("OK") and '"photos": 2' in final, final
    assert any(s["name"] == "Rosa del jardín" for s in srv.get("/api/subjects").json())


def test_reference_images_order():
    sys.path.insert(0, str(ROOT))
    from types import SimpleNamespace

    from app.agents.specialists import reference_map
    a = SimpleNamespace(references=["a1", "a2", "a3", "a4"])
    b = SimpleNamespace(references=["b1"])
    assert reference_map([a]) == [[1, 2, 3]]          # un modelo: sus 3 mejores fotos
    assert reference_map([a, b]) == [[1, 3], [2]]     # dos modelos: la principal de cada uno primero
