"""Login: con un admin creado, toda la web pide sesión; la sesión queda guardada; salir; contraseñas; usuarios."""
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def site():
    data = tempfile.mkdtemp(prefix="shelton_auth_")
    env = {**os.environ, "DATA_DIR": data, "SHELTON_KEEP_DATA_DIR": "1", "SHELTON_ADMIN_USER": "Admin", "SHELTON_ADMIN_PASSWORD": "luna-faro-cometa-2026"}
    out = subprocess.run([sys.executable, "-m", "app.auth", "ensure-admin"], cwd=ROOT, env=env, capture_output=True, text=True)
    assert out.stdout.startswith("CREATED admin"), out.stderr
    port = _free_port()
    proc = subprocess.Popen([sys.executable, str(ROOT / "tests" / "fake_server.py"), str(port)], cwd=ROOT, env=env,
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
    yield base
    proc.terminate()
    proc.wait(10)


def login(base, user="admin", pw="luna-faro-cometa-2026", remember=True) -> httpx.Client:
    c = httpx.Client(base_url=base, timeout=30)
    r = c.post("/api/auth/login", json={"username": user, "password": pw, "remember": remember})
    assert r.status_code == 200, r.text
    return c


def test_everything_is_locked_without_session(site):
    c = httpx.Client(base_url=site, timeout=30)
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/login"
    assert c.get("/api/posts").status_code == 401
    assert c.get("/api/events", timeout=5).status_code == 401
    assert c.get("/api/health").status_code == 200                 # el instalador lo usa
    assert c.get("/login").status_code == 200 and "Mantener la sesión iniciada" in c.get("/login").text
    assert c.get("/static/app.js").status_code == 200
    b = c.get("/api/auth/brand").json()
    assert b["auth"] is True and "product_name" in b and "openrouter" not in str(b).lower()
    r = c.get("/galeria?x=1", follow_redirects=False)
    assert r.headers["location"].startswith("/login?next=/galeria")


def test_login_session_kept_and_logout(site):
    bad = httpx.post(site + "/api/auth/login", json={"username": "admin", "password": "otra-cosa-mala"})
    assert bad.status_code == 401 and "incorrectos" in bad.json()["detail"]
    c = login(site, user="  ADMIN ")                                 # sin distinguir mayúsculas ni espacios
    ck = c.cookies.jar
    cookie = next(x for x in ck if x.name == "shelton_session")
    assert cookie.expires and cookie.expires - time.time() > 300 * 86400   # sesión guardada (~1 año)
    assert c.get("/api/posts").status_code == 200
    assert c.get("/", follow_redirects=False).status_code == 200
    me = c.get("/api/auth/me").json()
    assert me["user"]["username"] == "admin" and me["user"]["role"] == "admin"
    assert c.get("/login", follow_redirects=False).status_code == 302   # ya dentro: al login no
    c.post("/api/auth/logout")
    assert c.get("/api/posts").status_code == 401


def test_session_only_when_not_remembered(site):
    c = login(site, remember=False)
    cookie = next(x for x in c.cookies.jar if x.name == "shelton_session")
    assert cookie.expires is None                                        # se borra al cerrar el navegador
    assert c.get("/api/posts").status_code == 200


def test_brute_force_is_slowed(site):
    for _ in range(5):
        httpx.post(site + "/api/auth/login", json={"username": "nadie", "password": "x" * 12})
    r = httpx.post(site + "/api/auth/login", json={"username": "nadie", "password": "x" * 12})
    assert r.status_code == 429 and "Espera" in r.json()["detail"]


def test_users_and_password_change(site):
    admin = login(site)
    assert admin.post("/api/users", json={"username": "maria", "password": "abcd", "role": "editor"}).status_code == 400   # menos de 5
    u = admin.post("/api/users", json={"username": "maria", "password": "estrella-del-sur-9", "role": "editor"}).json()
    assert u["role"] == "editor"
    assert admin.post("/api/users", json={"username": "maria", "password": "estrella-del-sur-9"}).status_code == 400
    ed = login(site, "maria", "estrella-del-sur-9")
    assert ed.get("/api/posts").status_code == 200
    assert ed.get("/api/users").status_code == 403                      # un editor no administra cuentas
    # Cambiar contraseña cierra las otras sesiones de ese usuario
    other = login(site, "maria", "estrella-del-sur-9")
    assert ed.post("/api/auth/password", json={"current": "mal", "new": "nueva-clave-segura-1"}).status_code == 400
    assert ed.post("/api/auth/password", json={"current": "estrella-del-sur-9", "new": "nueva-clave-segura-1"}).status_code == 200
    assert ed.get("/api/posts").status_code == 200                      # la propia sigue
    assert other.get("/api/posts").status_code == 401                   # la otra se cerró
    login(site, "maria", "nueva-clave-segura-1")
    me = admin.get("/api/auth/me").json()["user"]
    assert admin.delete(f"/api/users/{me['id']}").status_code == 400   # no te borras a ti mismo
    assert admin.delete(f"/api/users/{u['id']}").status_code == 204
    assert ed.get("/api/posts").status_code == 401                      # usuario borrado: fuera
