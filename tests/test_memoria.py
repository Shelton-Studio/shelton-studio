"""Archivos de memoria: bitácoras, conversaciones en .md, nada se borra (se archiva), ZIP y contexto al empezar sesión."""
import io
import json
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_api import chat, final, srv  # noqa: E402,F401


def test_files_conversations_and_logs(srv):
    c = srv
    ev, conv = chat(c, "hola, prueba de memoria")
    tree = c.get("/api/memory/files").json()
    groups = {f["group"] for f in tree}
    assert "Conversaciones" in groups
    convs = next(f for f in tree if f["key"] == "_conversaciones")
    assert any(conv in x["path"] for x in convs["files"])
    path = next(x["path"] for x in convs["files"] if conv in x["path"])
    text = c.get("/api/memory/file", params={"path": path}).text
    assert "prueba de memoria" in text and "**Persona**" in text
    # Bitácora diaria de los agentes (cada trabajo deja una línea)
    c.post("/api/posts", json={"idea": "foto para la bitácora", "platform": "tiktok"})
    for _ in range(60):
        tree = c.get("/api/memory/files").json()
        if any(x["kind"] == "bitácora" for f in tree for x in f["files"]):
            break
        time.sleep(0.5)
    assert any(x["kind"] == "bitácora" for f in tree for x in f["files"])


def test_nothing_is_deleted_lessons_are_archived(srv):
    c = srv
    e = c.post("/api/training/copywriter", json={"title": "Emojis", "content": "Usar 2 emojis por caption"}).json()
    assert c.delete(f"/api/training/copywriter/{e['id']}").status_code == 200
    assert not any(x["id"] == e["id"] for x in c.get("/api/training/copywriter").json())   # ya no se aplica
    folder = next(f for f in c.get("/api/memory/files").json() if f["key"] == "copywriter")
    arch = next(x for x in folder["files"] if x["kind"] == "archivada" and e["id"] in x["path"])  # pero sigue guardada
    assert "Usar 2 emojis" in c.get("/api/memory/file", params={"path": arch["path"]}).text
    r = c.post(f"/api/training/copywriter/{e['id']}/restore").json()
    assert r["enabled"] and any(x["id"] == e["id"] for x in c.get("/api/training/copywriter").json())


def test_zip_and_safety(srv):
    c = srv
    z = zipfile.ZipFile(io.BytesIO(c.get("/api/memory/zip").content))
    names = z.namelist()
    assert "LEEME.md" in names and any(n.startswith("_conversaciones/") for n in names)
    zc = zipfile.ZipFile(io.BytesIO(c.get("/api/memory/zip", params={"folder": "copywriter"}).content))
    assert all(n.startswith("copywriter/") or n.startswith("_bitacora/copywriter/") or n == "LEEME.md" for n in zc.namelist())
    assert c.get("/api/memory/file", params={"path": "../shelton.db"}).status_code == 404
    assert c.get("/api/memory/file", params={"path": "../../.env"}).status_code == 404


def test_shelton_can_search_and_read_archive(srv):
    c = srv
    ev, _ = chat(c, 'TOOL memory_archive {"action": "search", "query": "prueba de memoria"}')
    assert "_conversaciones/" in final(ev)
    ev, _ = chat(c, 'TOOL memory_archive {"action": "list"}')
    assert "Conversaciones" in final(ev)
