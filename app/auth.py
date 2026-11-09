"""Login de la web: usuarios con contraseña (hash scrypt), sesión en cookie firmada y protección de rutas.

- Si no hay ningún usuario creado, la web queda abierta (modo demo y pruebas). Apenas existe uno, todo pide login.
- Crear o cambiar el admin desde la terminal del servidor:
      venv/bin/python -m app.auth create-admin            (pregunta usuario y contraseña sin mostrarla)
      venv/bin/python -m app.auth reset-password admin
- La contraseña nunca se guarda en texto plano ni se escribe en .env.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sys
import time
from datetime import datetime

from app.config import settings
from app.db import User, session

COOKIE = "shelton_session"
MIN_PASSWORD = 5    # largo mínimo de la contraseña (sin otras reglas)
DAYS = 365          # «mantener sesión iniciada»: se renueva sola cada vez que entras
PUBLIC_PREFIXES = ("/login", "/api/auth/", "/static/", "/media/", "/favicon")
PUBLIC_EXACT = {"/api/health"}
_users_cache: dict = {"t": 0.0, "any": None}
_fails: dict[str, list[float]] = {}


# ------------------------------------------------------------------ Contraseñas
def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(h).decode()


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, h = stored.split("$")
        got = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(got, base64.b64decode(h))
    except Exception:  # noqa: BLE001
        return False


def check_strength(pw: str) -> str:
    """'' si está bien; si no, por qué."""
    if len(pw) < MIN_PASSWORD:
        return f"La contraseña debe tener al menos {MIN_PASSWORD} caracteres"
    return ""


def generate_password() -> str:
    words = ["sol", "luna", "rio", "nube", "faro", "cobre", "jade", "lince", "puma", "ceiba", "brisa", "cometa"]
    return "-".join(secrets.choice(words) for _ in range(3)) + "-" + str(secrets.randbelow(9000) + 1000)


# ------------------------------------------------------------------ Sesión firmada
def _secret() -> bytes:
    p = settings.DATA_DIR / "secret.key"
    if not p.exists():
        p.write_bytes(secrets.token_bytes(32))
        try:
            p.chmod(0o600)
        except OSError:
            pass
    return p.read_bytes()


def _stamp(password_hash: str) -> str:
    """Cambia si cambia la contraseña: así se cierran las sesiones viejas."""
    return hashlib.sha256(password_hash.encode()).hexdigest()[:12]


def make_token(user: User, days: int = DAYS, remember: bool = True) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(
        {"u": user.id, "e": int(time.time()) + days * 86400, "s": _stamp(user.password_hash),
         "r": bool(remember)}).encode()).decode()
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def read_token(token: str | None) -> dict | None:
    """Devuelve {id, username, role} si la sesión es válida."""
    if not token or "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
    except Exception:  # noqa: BLE001
        return None
    if data.get("e", 0) < time.time():
        return None
    with session() as s:
        u = s.get(User, data.get("u"))
        if not u or _stamp(u.password_hash) != data.get("s"):
            return None
        return {"id": u.id, "username": u.username, "role": u.role, "remember": data.get("r", True),
                "renew": data.get("r", True) and data["e"] - time.time() < (DAYS - 1) * 86400}


def auth_enabled() -> bool:
    """El login se activa solo cuando existe al menos un usuario (cache de 5 s)."""
    now = time.monotonic()
    if _users_cache["any"] is None or now - _users_cache["t"] > 5:
        try:
            with session() as s:
                _users_cache["any"] = s.query(User).first() is not None
        except Exception:  # noqa: BLE001  (base aún sin crear)
            _users_cache["any"] = False
        _users_cache["t"] = now
    return bool(_users_cache["any"])


def invalidate_cache() -> None:
    _users_cache["any"] = None


def is_public(path: str) -> bool:
    return path in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIXES)


# ------------------------------------------------------------------ Intentos fallidos (anti fuerza bruta)
def too_many_fails(key: str, limit: int = 5) -> int:
    """Segundos a esperar si hubo `limit` fallos en 5 minutos (0 = puede intentar)."""
    now = time.time()
    lst = [t for t in _fails.get(key, []) if now - t < 300]
    _fails[key] = lst
    return int(300 - (now - lst[-limit])) + 1 if len(lst) >= limit else 0


def note_fail(key: str) -> None:
    _fails.setdefault(key, []).append(time.time())


def clear_fails(key: str) -> None:
    _fails.pop(key, None)


# ------------------------------------------------------------------ Usuarios
def create_user(username: str, password: str, role: str = "admin") -> dict:
    username = username.strip().lower()
    if not username or len(username) > 40 or not all(c.isalnum() or c in "._-@" for c in username):
        raise ValueError("Usuario inválido: usa letras, números, punto, guion o @")
    if role not in ("admin", "editor"):
        raise ValueError("Rol inválido")
    if err := check_strength(password):
        raise ValueError(err)
    with session() as s:
        if s.query(User).filter(User.username == username).first():
            raise ValueError("Ese usuario ya existe")
        u = User(username=username, password_hash=hash_password(password), role=role)
        s.add(u)
        s.flush()
        out = {"id": u.id, "username": u.username, "role": u.role}
    invalidate_cache()
    return out


def set_password(username: str, password: str) -> None:
    if err := check_strength(password):
        raise ValueError(err)
    with session() as s:
        u = s.query(User).filter(User.username == username.strip().lower()).first()
        if not u:
            raise ValueError("Ese usuario no existe")
        u.password_hash = hash_password(password)


def authenticate(username: str, password: str) -> User | None:
    with session() as s:
        u = s.query(User).filter(User.username == (username or "").strip().lower()).first()
        if u and verify_password(password or "", u.password_hash):
            u.last_login = datetime.utcnow()
            s.flush()
            s.expunge(u)
            return u
    verify_password("x", "scrypt$16384$8$1$AAAAAAAAAAAAAAAAAAAAAA==$AAAA")   # mismo tiempo exista o no
    return None


def list_users() -> list[dict]:
    with session() as s:
        return [{"id": u.id, "username": u.username, "role": u.role,
                 "created_at": u.created_at.isoformat() + "Z" if u.created_at else None,
                 "last_login": u.last_login.isoformat() + "Z" if u.last_login else None}
                for u in s.query(User).order_by(User.created_at)]


def delete_user(user_id: str) -> None:
    with session() as s:
        u = s.get(User, user_id)
        if not u:
            return
        admins = s.query(User).filter(User.role == "admin").count()
        if u.role == "admin" and admins <= 1:
            raise ValueError("No puedes borrar al único admin")
        s.delete(u)
    invalidate_cache()


# ------------------------------------------------------------------ Terminal
def _cli() -> None:
    import getpass
    import os
    from app.db import init_db
    init_db()
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "ensure-admin":
        # Para el instalador: crea el admin solo si no hay usuarios. Usuario/contraseña por variables de entorno;
        # sin contraseña, genera una segura y la muestra UNA vez.
        with session() as s:
            if s.query(User).first():
                print("EXISTS")
                return
        user = os.environ.get("SHELTON_ADMIN_USER") or "admin"
        pw = os.environ.get("SHELTON_ADMIN_PASSWORD") or generate_password()
        u = create_user(user, pw, "admin")
        print(f"CREATED {u['username']} {pw if not os.environ.get('SHELTON_ADMIN_PASSWORD') else ''}".strip())
    elif cmd == "create-admin":
        user = input("Usuario admin [admin]: ").strip() or "admin"
        pw = getpass.getpass("Contraseña (mín. 5 caracteres; Enter = generar una segura): ")
        if not pw:
            pw = generate_password()
            print(f"Contraseña generada: {pw}")
        elif getpass.getpass("Repítela: ") != pw:
            sys.exit("No coinciden")
        print("Creado:", create_user(user, pw, "admin")["username"])
    elif cmd == "reset-password":
        user = sys.argv[2] if len(sys.argv) > 2 else (input("Usuario: ").strip() or "admin")
        pw = getpass.getpass("Nueva contraseña (Enter = generar una segura): ")
        if not pw:
            pw = generate_password()
            print(f"Contraseña generada: {pw}")
        elif getpass.getpass("Repítela: ") != pw:
            sys.exit("No coinciden")
        set_password(user, pw)
        print("Contraseña cambiada. Las sesiones abiertas de ese usuario se cerraron.")
    elif cmd == "list":
        for u in list_users():
            print(f"{u['username']:20} {u['role']:7} último ingreso: {u['last_login'] or '-'}")
    else:
        print(__doc__)


if __name__ == "__main__":
    try:
        _cli()
    except ValueError as e:
        sys.exit(f"Error: {e}")
