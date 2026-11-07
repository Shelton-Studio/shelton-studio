"""Batería de punta a punta por HTTP contra el servidor en modo demo.

Recorre todo lo que usa la interfaz: chat y herramientas de Shelton, posts, ediciones, alternativas del juez,
video, reels, campañas, banco de modelos, aprobaciones, conexiones, webhooks, flujos, scout, métricas,
tareas, marca blanca, exportación y búsqueda.
"""
import io
import json
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def srv():
    port = _free_port()
    proc = subprocess.Popen([sys.executable, str(ROOT / "tests" / "fake_server.py"), str(port)], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            if httpx.get(base + "/api/health", timeout=2).status_code == 200:
                break
        except Exception:
            time.sleep(0.5)
    else:
        proc.kill()
        raise RuntimeError(proc.stdout.read().decode())
    client = httpx.Client(base_url=base, timeout=120)
    yield client
    proc.terminate()
    proc.wait(10)


def chat(c, message, conv=None, **extra) -> tuple[list[dict], str]:
    events = []
    with c.stream("POST", "/api/chat", json={"message": message, "conversation_id": conv, **extra}, timeout=600) as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    conv_id = next(e["id"] for e in events if e["type"] == "conversation")
    return events, conv_id


def final(events) -> str:
    return next(e["text"] for e in events if e["type"] == "message")


def wait_post(c, pid, statuses=("ready",), timeout=180) -> dict:
    t = time.time()
    while time.time() - t < timeout:
        p = c.get(f"/api/posts/{pid}").json()
        if p["status"] in statuses or p["status"] == "failed":
            return p
        time.sleep(0.4)
    raise AssertionError(f"timeout esperando {pid}: {p['status']}")


def tool(c, conv, name, args=None) -> str:
    events, _ = chat(c, f"TOOL {name} {json.dumps(args or {})}", conv)
    assert not [e for e in events if e["type"] == "error"], events
    return final(events)


# ------------------------------------------------------------------ pruebas (en orden)
def test_basics(srv):
    h = srv.get("/api/health").json()
    assert h["engine"]["ok"] and h["ffmpeg"]
    assert len(srv.get("/api/platforms").json()) >= 9
    eng = srv.get("/api/engines").json()
    assert {"fast", "quality", "lite"} <= {e["id"] for e in eng["image"]} and len(eng["video"]) == 2
    agents = srv.get("/api/agents").json()
    keys = {a["key"] for a in agents}
    assert {"shelton", "judge", "copywriter", "video_producer", "trend_scout", "tool_scout"} <= keys
    assert all(a["prompt"] for a in agents)
    assert srv.get("/").status_code == 200 and "Shelton" in srv.get("/").text


def test_chat_chain_with_model_bank(srv):
    events, conv = chat(srv, "crea a Valentina y haz 2 posts con ella")
    types = [e["type"] for e in events]
    assert "error" not in types and "status" in types and "posts" in types
    msg = next(e for e in events if e["type"] == "message")
    assert len(msg["post_ids"]) == 2
    for pid in msg["post_ids"]:
        p = wait_post(srv, pid)
        assert p["status"] == "ready" and p["caption"] and p["hashtags"] and p["image_url"]
        assert srv.get(p["image_url"]).status_code == 200
        assert p["alternatives"], "el intento reprobado por el juez debe conservarse"
    subs = srv.get("/api/subjects").json()
    assert subs[0]["name"] == "Valentina" and subs[0]["references"] and subs[0]["alternatives"]
    conv_data = srv.get(f"/api/conversations/{conv}").json()
    assert len(conv_data["messages"]) == 2
    pytest.conv = conv
    pytest.posts = msg["post_ids"]


def test_human_decides(srv):
    pid = pytest.posts[0]
    p = srv.get(f"/api/posts/{pid}").json()
    before = p["image_url"]
    p2 = srv.post(f"/api/posts/{pid}/use-alternative", json={"index": 0}).json()
    assert p2["image_url"] != before and before in [a["image_url"] for a in p2["alternatives"]]
    p3 = srv.patch(f"/api/posts/{pid}", json={"caption": "Texto editado", "status": "approved"}).json()
    assert p3["caption"] == "Texto editado" and p3["status"] == "approved"


def test_edit_and_versions(srv):
    r = srv.post(f"/api/posts/{pytest.posts[0]}/edit", json={"instruction": "fondo de playa"}).json()
    e = wait_post(srv, r["id"])
    assert e["status"] == "ready" and e["parent_id"] == pytest.posts[0]
    versions = srv.get(f"/api/posts/{r['id']}/versions").json()
    assert [v["id"] for v in versions][:2] == [pytest.posts[0], r["id"]]


def test_video_and_reel(srv):
    v = srv.post(f"/api/posts/{pytest.posts[1]}/video", json={"motion": "zoom lento"}).json()
    vp = wait_post(srv, v["id"], timeout=240)
    assert vp["status"] == "ready" and vp["media_type"] == "video" and vp["duration"] > 0.5
    r = srv.get(vp["video_url"])
    assert r.status_code == 200 and r.content[4:8] == b"ftyp", "debe ser un MP4 real"
    # Reel: un post de imagen (se anima solo) + el video
    reel = srv.post("/api/reels", json={"post_ids": [pytest.posts[0], v["id"]], "idea": "reel de prueba"}).json()
    rp = wait_post(srv, reel["id"], timeout=300)
    assert rp["status"] == "ready" and rp["media_type"] == "reel" and rp["duration"] > vp["duration"]
    pytest.video = v["id"]


def test_reel_in_one_order_via_chat(srv):
    out = tool(srv, pytest.conv, "create_reel", {"idea": "un día en el parque", "platform": "tiktok",
                                                 "scenes": ["niños jugando en el parque", "una familia brindando"]})
    assert out.startswith("OK"), out
    data = json.loads(out[4:])
    assert data[0]["media_type"] == "reel" and data[0]["status"] == "ready", data


def test_all_chief_tools(srv):
    conv = pytest.conv
    assert tool(srv, conv, "list_posts", {"limit": 5}).startswith("OK")
    assert tool(srv, conv, "view_post", {"post_id": pytest.posts[0]}).startswith("OK")
    assert tool(srv, conv, "list_subjects").startswith("OK")
    assert tool(srv, conv, "image_engines").startswith("OK")
    assert "Calidad" in tool(srv, conv, "set_image_engine", {"engine_id": "quality"})
    assert srv.get(f"/api/conversations/{conv}").json()["engine"] == "quality"
    assert tool(srv, conv, "set_image_engine", {"engine_id": "no-existe"}).startswith("ERROR")
    assert tool(srv, conv, "update_brand", {"name": "Café Luna", "tone": "cálido"}).startswith("OK")
    assert srv.get("/api/brand").json()["name"] == "Café Luna"
    assert tool(srv, conv, "studio_status").startswith("OK")
    out = tool(srv, conv, "create_posts", {"posts": [{"idea": "nave espacial sobre Marte, óleo", "platform": "x"},
                                                     {"idea": "auto clásico, clip", "platform": "tiktok", "format": "video"}]})
    data = json.loads(out[4:].split("\n")[0]) if out.startswith("OK: [") else None
    assert data and data[0]["status"] == "ready" and data[1]["media_type"] == "video", out
    assert tool(srv, conv, "add_subject_reference", {"subject_name": "Mustang Rojo", "post_id": data[0]["id"],
                                                     "kind": "car"}).startswith("OK")
    assert any(s["name"] == "Mustang Rojo" for s in srv.get("/api/subjects").json())
    assert tool(srv, conv, "edit_post", {"post_id": data[0]["id"], "instruction": "más dramático"}).startswith("OK")
    assert tool(srv, conv, "make_video", {"post_id": data[0]["id"]}).startswith("OK")
    out = tool(srv, conv, "create_campaign", {"brief": "semana", "count": 8, "platforms": ["tiktok", "telegram"]})
    assert out.startswith("OK"), out
    pytest.campaign = json.loads(out[4:])["campaign_id"]


def test_campaign_finishes(srv):
    t = time.time()
    while time.time() - t < 300:
        camp = next(c for c in srv.get("/api/campaigns").json() if c["id"] == pytest.campaign)
        if camp["counts"].get("ready", 0) >= 8:
            break
        time.sleep(1)
    assert camp["counts"].get("ready", 0) == 8, camp


def test_connections_publish_and_webhooks(srv):
    types = srv.get("/api/connection-types").json()
    assert {"comfyui", "fal", "replicate", "openai_images", "gemini_image", "http_engine", "telegram", "instagram",
            "facebook", "discord", "webhook_publisher", "event_webhook", "anthropic"} <= set(types["types"])
    base = str(srv.base_url)
    hook = srv.post("/api/connections", json={"type": "webhook_publisher", "name": "Mi n8n",
                                              "config": {"url": base + "/test-hook"}}).json()
    evh = srv.post("/api/connections", json={"type": "event_webhook", "name": "Eventos",
                                             "config": {"url": base + "/test-hook", "events": "post.*, approval.*"}}).json()
    assert srv.post(f"/api/connections/{hook['id']}/test").json()["ok"]
    # ComfyUI inexistente: la prueba debe fallar con un mensaje claro
    bad = srv.post("/api/connections", json={"type": "comfyui", "name": "3090", "config": {"url": "http://127.0.0.1:9"},
                                             "roles": ["generate"]}).json()
    t = srv.post(f"/api/connections/{bad['id']}/test").json()
    assert not t["ok"] and "No conecta" in t["message"]
    srv.delete(f"/api/connections/{bad['id']}")
    # secretos enmascarados y conservados al editar
    tg = srv.post("/api/connections", json={"type": "telegram", "config": {"bot_token": "123456:SECRET", "chat_id": "@x"}}).json()
    assert tg["config"]["bot_token"].startswith("••••")
    tg2 = srv.put(f"/api/connections/{tg['id']}", json={"type": "telegram", "config": tg["config"]}).json()
    assert tg2["config"]["bot_token"] == tg["config"]["bot_token"]
    srv.delete(f"/api/connections/{tg['id']}")
    # Publicación directa (decisión humana) con video
    r = srv.post(f"/api/posts/{pytest.video}/publish", json={"target": hook["id"]})
    assert r.status_code == 200, r.text
    time.sleep(1)
    received = srv.get("/test-hook").json()
    pub = [h for h in received if h.get("event") == "post.publish"]
    assert pub and pub[-1]["media_type"] == "video" and len(pub[-1]["media_base64"]) > 1000
    assert any(h.get("event") == "post.published" for h in received), "el webhook de eventos debe avisar"
    # Shelton NO publica: crea una aprobación
    out = tool(srv, pytest.conv, "publish_post", {"post_id": pytest.posts[1], "target": hook["id"]})
    assert "Aprobaciones" in out
    pend = srv.get("/api/approvals").json()
    assert len(pend) == 1 and srv.get(f"/api/posts/{pytest.posts[1]}").json()["status"] != "published"
    res = srv.post(f"/api/approvals/{pend[0]['id']}", json={"approve": True}).json()
    assert res["status"] == "approved"
    assert srv.get(f"/api/posts/{pytest.posts[1]}").json()["status"] == "published"
    assert srv.post(f"/api/approvals/{pend[0]['id']}", json={"approve": True}).status_code == 400
    srv.delete(f"/api/connections/{evh['id']}")


def test_failed_task_and_retry(srv):
    r = srv.post("/api/posts", json={"idea": "x", "engine": "conn:noexiste"}).json()
    p = wait_post(srv, r["ids"][0])
    assert p["status"] == "failed" and p["error"]
    board = srv.get("/api/tasks").json()
    failed = [c for c in board["columns"]["failed"] if c["post_id"] == p["id"]]
    assert failed
    assert srv.post(f"/api/tasks/{failed[0]['id']}/retry").status_code == 200


def test_workflows(srv):
    wfs = srv.get("/api/workflows").json()
    names = {w["name"] for w in wfs}
    assert {"z_image_turbo.json", "qwen_image.json", "qwen_image_edit.json", "qwen_subject.json",
            "wan22_i2v_14b.json", "wan22_ti2v_5b.json"} <= names
    assert all(not w.get("error") for w in wfs)
    bad = srv.post("/api/workflows", files={"file": ("ui.json", b'{"nodes": [], "links": []}', "application/json")})
    assert bad.status_code == 400 and "API" in bad.json()["detail"]
    good = {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{prompt}}"}}}
    r = srv.post("/api/workflows", files={"file": ("mi flujo.json", json.dumps(good).encode(), "application/json")})
    assert r.status_code == 200 and r.json()["placeholders"] == ["prompt"]
    assert srv.delete(f"/api/workflows/{r.json()['name']}").json()["ok"]


def test_scout_findings(srv):
    srv.post("/api/scout/run")
    for _ in range(40):
        f = srv.get("/api/findings").json()
        if f:
            break
        time.sleep(0.5)
    assert f and f[0]["ideas"]
    used = srv.post(f"/api/findings/{f[0]['id']}/use", json={"ideas": f[0]["ideas"][:1], "platform": "tiktok",
                                                             "subjects": ["Valentina"]}).json()
    p = wait_post(srv, used["ids"][0])
    assert p["status"] == "ready" and p["subject_ids"]
    assert srv.get("/api/findings").json() == [] or all(x["id"] != f[0]["id"] for x in srv.get("/api/findings").json())


def test_agents_fichas(srv):
    a = srv.put("/api/agents/copywriter", json={"prompt": "Escribe como pirata.", "effort": "medium"}).json()
    assert a["custom_prompt"] and a["version"] == 2 and a["effort"] == "medium"
    a = srv.post("/api/agents/copywriter/reset").json()
    assert not a["custom_prompt"] and a["version"] == 3
    events, conv = chat(srv, "hola", agent="copywriter")
    assert final(events) and srv.get(f"/api/conversations/{conv}").json()["agent"] == "copywriter"


def test_metrics_runs_tasks(srv):
    m = srv.get("/api/metrics?days=7").json()
    k = m["kpis"]
    assert k["runs"] > 10 and k["tokens"] > 0 and k["cost"] > 0 and k["posts"] >= 10
    assert len(m["series"]) == 7 and sum(d["runs"] for d in m["series"]) == k["runs"]
    assert any(a["runs"] for a in m["agents"]) and m["by_media"].get("video")
    assert srv.get("/api/metrics?days=30").json()["days"] == 30
    assert srv.get("/api/runs?limit=5").json()
    board = srv.get("/api/tasks").json()
    assert set(board["columns"]) == {"queued", "running", "waiting", "done", "failed"}
    assert board["counts"]["waiting"] + board["counts"]["done"] > 5


def test_whitelabel_export_search(srv):
    wl = srv.put("/api/whitelabel", json={"product_name": "MiMarca AI", "chief_name": "Max", "accent": "#ff0066"}).json()
    assert wl["product_name"] == "MiMarca AI" and srv.get("/api/whitelabel").json()["chief_name"] == "Max"
    png = io.BytesIO()
    from PIL import Image
    Image.new("RGB", (64, 64), "red").save(png, "PNG")
    logo = srv.post("/api/whitelabel/logo", files={"file": ("logo.png", png.getvalue(), "image/png")}).json()
    assert srv.get(logo["logo_url"]).status_code == 200
    srv.put("/api/whitelabel", json={"product_name": "Shelton Studio", "chief_name": "Shelton", "accent": "#8b5cf6"})
    z = zipfile.ZipFile(io.BytesIO(srv.get("/api/export?status=ready,approved,published").content))
    names = z.namelist()
    assert "posts.csv" in names and any(n.endswith(".mp4") for n in names) and any(n.endswith(".jpg") for n in names)
    assert srv.get("/api/search?q=Valentina").json()
    up = srv.post("/api/upload", files={"file": ("foto.png", png.getvalue(), "image/png")}).json()
    assert up["status"] == "ready" and up["width"] == 64 and up["platform"] == "original"
    assert srv.post("/api/upload", files={"file": ("x.txt", b"hola", "text/plain")}).status_code == 400
    assert srv.delete(f"/api/posts/{up['id']}").json()["ok"]


def test_voice_sound_and_uploaded_edit(srv):
    # Voz sobre un video, con efecto de sonido de fondo
    r = srv.post(f"/api/posts/{pytest.video}/audio", json={"voice_request": "grave y robotizada",
                                                           "sound_request": "motor V8 rugiendo"}).json()
    p = wait_post(srv, r["id"], timeout=240)
    assert p["status"] == "ready" and p["voiceover"] and p["audio_url"], p
    mp4 = srv.get(p["video_url"]).content
    assert mp4[4:8] == b"ftyp"
    # Voz sobre una imagen -> video con movimiento lento
    r = srv.post(f"/api/posts/{pytest.posts[0]}/audio", json={"voice_request": "femenina y cálida"}).json()
    p = wait_post(srv, r["id"], timeout=240)
    assert p["status"] == "ready" and p["media_type"] == "video" and p["duration"] > 1
    # Solo sonido, desde el chat
    out = tool(srv, pytest.conv, "add_sound", {"post_id": pytest.video, "sound_request": "moto pasando rápido"})
    assert out.startswith("OK"), out
    # Banco de voces + prueba de voz
    wav = srv.post("/api/voices/preview", json={"text": "hola", "request": "voz de anciano"}).json()
    assert srv.get(wav["url"]).status_code == 200
    up = srv.post("/api/voices", files={"file": ("lola.wav", srv.get(wav["url"]).content, "audio/wav")},
                  params={"name": "Lola", "description": "cálida"}).json()
    assert any(v["name"] == "Lola" for v in srv.get("/api/voices").json())
    assert srv.delete(f"/api/voices/{up['name']}").json()["ok"]
    # Foto subida: se edita sin recortar y conserva su tamaño
    import io as _io
    from PIL import Image
    buf = _io.BytesIO()
    Image.new("RGB", (900, 600), "blue").save(buf, "PNG")
    upl = srv.post("/api/upload", files={"file": ("mia.png", buf.getvalue(), "image/png")}).json()
    assert (upl["width"], upl["height"]) == (900, 600) and upl["platform"] == "original"
    e = wait_post(srv, srv.post(f"/api/posts/{upl['id']}/edit", json={"instruction": "cielo al atardecer"}).json()["id"])
    assert e["status"] == "ready" and (e["width"], e["height"]) == (900, 600), e


def test_training_memory_contradictions(srv):
    a = srv.post("/api/training/copywriter", json={"title": "Hashtags", "content": "Usa 10 hashtags siempre",
                                                    "author": "Ana", "role": "admin"}).json()
    assert a["status"] == "active" and Path(a["file"]).exists()
    b = srv.post("/api/training/copywriter", json={"title": "Pocos hashtags", "content": "CONTRADICE: solo 3 hashtags",
                                                    "author": "Luis", "role": "operador"}).json()
    assert b["status"] == "conflict" and "Contradice" in b["conflict_reason"] and b["conflict_with"] == a["id"]
    feed = srv.get("/api/training").json()
    assert {a["id"], b["id"]} <= {e["id"] for e in feed}
    r = srv.post(f"/api/training/copywriter/{b['id']}/resolve", json={"action": "replace", "decided_by": "Ana"}).json()
    assert r["status"] == "active"
    les = {e["id"]: e for e in srv.get("/api/training/copywriter").json()}
    assert les[a["id"]]["status"] == "disabled"
    # Enseñar en el chat directo con un agente
    events, conv = chat(srv, "TOOL remember " + json.dumps({"title": "Emojis", "content": "Máximo 2 emojis"}),
                        agent="copywriter")
    learned = [e for e in events if e["type"] == "learned"]
    assert learned and learned[0]["applied"] and learned[0]["agent"] == "copywriter"
    # Shelton lee la memoria de un subagente y revisa al equipo
    assert "Emojis" in tool(srv, pytest.conv, "read_agent_memory", {"agent": "copywriter"})
    assert "Hashtags" in tool(srv, pytest.conv, "review_team")
    assert srv.get("/api/audits").json()
    # Aprende de las decisiones humanas
    st = srv.get("/api/learning/status").json()
    assert st["total_decisions"] >= 3
    res = srv.post("/api/learning/run").json()
    assert res["lessons"] and res["lessons"][0]["agent"] == "copywriter"
    assert srv.get("/api/learning/status").json()["pending_decisions"] == 0


def test_research_tool(srv):
    out = tool(srv, pytest.conv, "research_web", {"question": "autos exóticos 2026"})
    assert out.startswith("OK") and "Informe" in out
