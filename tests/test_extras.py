"""Audios (WAV/MP3), videos de referencia (copiar un TikTok con tu modelo), exportar todo, memoria por modelo,
Shelton pasando lecciones al equipo, capacidad, Shelton mini y dictado por voz — de punta a punta en modo demo."""
import asyncio
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import httpx
import imageio_ffmpeg
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from test_api import chat, final, srv  # noqa: E402,F401  (servidor de demo compartido)

FF = imageio_ffmpeg.get_ffmpeg_exe()


def _media(args: list[str], suffix: str) -> bytes:
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / f"x{suffix}"
        subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", *args, str(out)], check=True)
        return out.read_bytes()


def tone(seconds=3, suffix=".wav", volume=0.6) -> bytes:
    return _media(["-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}", "-af", f"volume={volume}"], suffix)


def vertical_video(seconds=6) -> bytes:
    return _media(["-f", "lavfi", "-i", f"testsrc2=size=360x640:rate=24:duration={seconds}",
                   "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"], ".mp4")


def wait_post(c, pid, timeout=180):
    for _ in range(timeout * 2):
        p = c.get(f"/api/posts/{pid}").json()
        if p["status"] in ("ready", "failed", "approved"):
            return p
        time.sleep(0.5)
    raise AssertionError(f"timeout {pid}")


def test_audio_library_and_track(srv):
    c = srv
    a = c.post("/api/audio", files={"file": ("cancion.wav", tone(4), "audio/wav")}).json()
    assert a["duration"] > 3.5 and a["url"].endswith(".wav") and a["name"] == "cancion"
    m = c.post("/api/audio", files={"file": ("voz.mp3", tone(2, ".mp3"), "audio/mpeg")}).json()
    assert m["url"].endswith(".mp3")
    bad = c.post("/api/audio", files={"file": ("nada.wav", b"RIFF0000WAVEnope", "audio/wav")})
    assert bad.status_code == 400
    assert {x["id"] for x in c.get("/api/audio").json()} >= {a["id"], m["id"]}
    assert c.patch(f"/api/audio/{a['id']}", json={"name": "Tema verano"}).json()["name"] == "Tema verano"
    # Ponerle el audio a una foto -> video con ese audio (la foto original se conserva)
    pid = c.post("/api/posts", json={"idea": "atardecer en la playa", "platform": "tiktok"}).json()["ids"][0]
    wait_post(c, pid)
    r = c.post(f"/api/posts/{pid}/audio-track", json={"audio_id": a["id"]}).json()
    v = wait_post(c, r["id"])
    assert v["status"] == "ready" and v["video_url"] and v["audio_url"], v
    assert v["made_with"].get("audio") == "Tema verano"
    # Guardar el audio de un video en la biblioteca
    saved = c.post(f"/api/posts/{r['id']}/save-audio", json={"name": "Audio guardado"}).json()
    assert saved["duration"] > 2 and saved["source"] == "post"
    assert c.post(f"/api/posts/{pid}/save-audio", json={}).status_code == 400   # una foto no tiene audio
    # Descarga directa del archivo
    d = c.get(f"/api/posts/{r['id']}/download")
    assert d.status_code == 200 and d.content[4:8] == b"ftyp"
    assert c.delete(f"/api/audio/{m['id']}").json()["ok"]


def test_reference_video_recreated_with_bank_model(srv):
    c = srv
    sub = c.post("/api/subjects/from-photos", files=[("files", ("a.png", _png("red"), "image/png")),
                                                     ("files", ("b.png", _png("blue"), "image/png"))],
                 data={"name": "Lucia Ref", "kind": "person"}).json()
    ref = c.post("/api/references/upload", files={"file": ("baile.mp4", vertical_video(6), "video/mp4")}).json()
    assert ref["platform"] == "tiktok" and len(ref["frames"]) == 2 and ref["audio_id"], ref
    assert ref["analysis"]["scenes"] and ref["analysis"]["hook"]
    assert c.get(ref["video_url"]).status_code == 200
    # Los cuadros quedan como posts (para verlos o editarlos)
    assert all(c.get(f"/api/posts/{p}").json()["status"] == "ready" for p in ref["frame_post_ids"])
    # El audio quedó en la biblioteca
    assert any(a["id"] == ref["audio_id"] for a in c.get("/api/audio").json())
    r = c.post(f"/api/references/{ref['id']}/recreate", json={"subjects": ["Lucia Ref"], "mode": "same",
                                                              "changes": "de noche", "keep_audio": True}).json()
    assert r["kind"] == "reel" and len(r["scene_ids"]) == 2 and r["audio"] and not r["subjects_not_found"]
    reel = wait_post(c, r["target_id"], 300)
    assert reel["status"] == "ready" and reel["media_type"] == "reel", reel
    scene = c.get(f"/api/posts/{r['scene_ids'][0]}").json()
    assert sub["id"] in scene["subject_ids"] and scene["reel_id"] == r["target_id"]
    # Luego se crea una versión con el audio original
    for _ in range(240):
        kids = [p for p in c.get(f"/api/posts/{r['target_id']}/versions").json() if p.get("parent_id") == r["target_id"]]
        if kids and kids[0]["status"] == "ready":
            break
        time.sleep(0.5)
    assert kids and kids[0]["audio_url"], kids
    assert c.get("/api/references").json()[0]["id"] == ref["id"]


def test_reference_from_chat_and_tools(srv):
    c = srv
    ref = c.get("/api/references").json()[0]
    ev, _ = chat(c, f'TOOL analyze_reference {{"reference_id": "{ref["id"]}"}}')
    assert ref["id"] in final(ev)
    ev, _ = chat(c, "mira este video", attachments=[{"kind": "reference", "id": ref["id"]}])
    assert any(e["type"] == "conversation" for e in ev)
    ev, _ = chat(c, "TOOL list_audio {}")
    assert "OK:" in final(ev)
    ev, _ = chat(c, "TOOL studio_capacity {}")
    assert "parallel_lanes" in final(ev)


def test_subject_memory_and_team_propagation(srv):
    c = srv
    sub = c.get("/api/subjects").json()[0]
    key = f"subject_{sub['id']}"
    e = c.post(f"/api/training/{key}", json={"title": "Lunar", "content": "Siempre con lunar en la mejilla izquierda"}).json()
    assert e["status"] == "active"
    assert c.get(f"/api/training/{key}").json()[0]["title"] == "Lunar"
    assert c.get("/api/training/subject_noexiste").status_code == 404
    # Shelton pasa una lección del director creativo al juez
    c.post("/api/training/art_director", json={"title": "Sin logos", "content": "Nunca mostrar el LOGO del auto"})
    for _ in range(40):
        if any("logo" in x["title"].lower() for x in c.get("/api/training/judge").json()):
            break
        time.sleep(0.25)
    judge = c.get("/api/training/judge").json()
    hit = next(x for x in judge if "logo" in x["title"].lower())
    assert hit["source"] == "equipo:art_director" and "Pasada por Shelton" in hit["content"]
    # Exportar un modelo con su memoria
    z = zipfile.ZipFile(io.BytesIO(c.get(f"/api/subjects/{sub['id']}/export").content))
    names = z.namelist()
    assert "ficha.json" in names and any(n.startswith("memoria/") for n in names) and any(n.startswith("fotos/") for n in names)
    # Lección de un modelo desde el chat
    ev, _ = chat(c, f'TOOL remember {{"title": "Pelo", "content": "Pelo siempre recogido", "subject": "{sub["name"]}"}}')
    assert "aplicada" in final(ev).lower()
    assert any(x["title"] == "Pelo" for x in c.get(f"/api/training/{key}").json())


def test_export_everything(srv):
    c = srv
    r = c.get("/api/export/all")
    assert r.status_code == 200 and "shelton_todo_" in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    for prefix in ("contenido/", "banco/", "audios/", "memoria/", "referencias/", "respaldo/data/shelton.db", "LEEME.txt"):
        assert any(n.startswith(prefix) for n in names), prefix
    assert not any(n.endswith(".env") for n in names)
    assert "contenido/posts.csv" in names


def test_capacity_and_buddy(srv):
    c = srv
    cap = c.get("/api/capacity").json()
    assert cap["parallel_lanes"] >= 1 and "imagen" in cap["seconds_per_job"] and "imagen" in cap["measured"]
    b = c.get("/api/buddy").json()
    assert b["subjects"] and b["tips"] and all("text" in t for t in b["tips"])
    # Varias cosas a la vez: mientras trabaja, Shelton mini propone avanzar en paralelo
    c.post("/api/posts", json={"idea": "foto 1", "platform": "tiktok", "count": 2})
    b = c.get("/api/buddy").json()
    assert any(t["kind"] == "parallel" for t in b["tips"])


def test_transcribe_quiet_and_short(srv):
    c = srv
    r = c.post("/api/transcribe", files={"file": ("voz.webm", tone(2, ".webm", 0.002), "audio/webm")}).json()
    assert not r["clear"] and r["issue"] == "quiet" and "más alto" in r["message"]
    r = c.post("/api/transcribe", files={"file": ("voz.webm", tone(0.2, ".webm"), "audio/webm")}).json()
    assert not r["clear"] and r["issue"] == "short"


def test_transcribe_cloud(monkeypatch):
    os.environ.setdefault("IMAGE_ENGINE", "mock")
    os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="shelton_tr_"))
    sys.path.insert(0, str(ROOT))
    from app import transcribe
    from app.config import settings
    from app.db import init_db
    init_db()
    seen = {}

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        seen["part"] = body["messages"][0]["content"][1]
        text = "hazme tres fotos de Valentina" if "ok" not in seen else "mmm"
        seen["ok"] = True
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(
            {"text": text, "clear": text != "mmm", "issue": "" if text != "mmm" else "unclear"})}}]})
    monkeypatch.setattr(transcribe, "transport", httpx.MockTransport(handler))
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "WHISPER_LOCAL", False)
    r = asyncio.run(transcribe.transcribe(tone(2, ".webm"), "voz.webm"))
    assert r["clear"] and r["text"] == "hazme tres fotos de Valentina" and seen["part"]["type"] == "input_audio"
    r = asyncio.run(transcribe.transcribe(tone(2, ".webm"), "voz.webm"))
    assert not r["clear"] and "No te entendí" in r["message"]


def _png(color: str) -> bytes:
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(b, "PNG")
    return b.getvalue()


def test_network_review_best_times_and_live(srv):
    c = srv
    assert c.post("/api/network-review").json()["running"]
    for _ in range(120):
        b = c.get("/api/buddy").json()
        if b["review"].get("at"):
            break
        time.sleep(0.5)
    assert b["review"]["at"] and b["review"]["timing"], b["review"]
    t = c.get("/api/timing").json()
    assert t["platforms"][0]["best"] and t["models"] and t["sources"] and "subjects" in t["inside"]
    assert b["timing"]["models"] and b["timing"]["timezone"]
    # Métricas en vivo para Shelton mini
    assert len(b["activity"]) == 12 and "jobs_done" in b["last_hour"] and isinstance(b["jobs"], list)
    ev, _ = chat(c, 'TOOL best_times {"model": "Lucia Ref"}')
    assert "Lucia Ref" in final(ev)
    ev, _ = chat(c, "TOOL live_status {}")
    assert "working_now" in final(ev) and "last_network_review" in final(ev)


def test_video_links_in_chat_are_detected_and_reused():
    os.environ.setdefault("IMAGE_ENGINE", "mock")
    os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="shelton_links_"))
    sys.path.insert(0, str(ROOT))
    from app import reference
    from app.agents import shelton
    from app.db import init_db
    init_db()
    text = ("mira https://www.tiktok.com/@ana/video/7312?is_from_webapp=1 y https://youtu.be/abc, "
            "también https://www.instagram.com/reel/XYZ/ y esto no https://www.google.com/search?q=x")
    links = reference.video_links(text)
    assert [s for _, s in links] == ["TikTok", "YouTube", "Instagram"]
    assert reference.video_links("https://cdn.site.com/clip.mp4?x=1")[0][1] == "video"
    notes = shelton._link_notes(text)
    assert len(notes) == 3 and "analyze_reference" in notes[0]
    # Un link ya analizado se reutiliza (no se vuelve a bajar)
    video = Path(reference.settings.media_dir) / "ref_test_src.mp4"
    video.write_bytes(b"x")
    rid = "linktest01"
    (reference._dir() / f"{rid}.json").write_text(json.dumps({
        "id": rid, "url": "https://www.tiktok.com/@ana/video/7312", "title": "t", "duration": 5.0,
        "video_path": str(video), "frames": ["a", "b"], "frame_post_ids": [], "audio_id": ""}), encoding="utf-8")
    assert reference.find_by_url("https://www.tiktok.com/@ana/video/7312/?lang=es")["id"] == rid
    assert asyncio.run(reference.from_url("https://www.tiktok.com/@ana/video/7312"))["id"] == rid
    assert f"reference_id={rid}" in shelton._link_notes(text)[0]
