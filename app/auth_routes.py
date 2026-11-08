"""Rutas de login/usuarios y el filtro que protege la web."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel

from app import auth
from app.config import ROOT

router = APIRouter()


async def guard(request: Request, call_next):
    """Todo pide sesión (si hay usuarios creados), salvo el login, la salud y los archivos públicos."""
    path = request.url.path
    if auth.is_public(path) or not auth.auth_enabled():
        return await call_next(request)
    user = auth.read_token(request.cookies.get(auth.COOKIE))
    if not user:
        if path.startswith("/api/") or path.startswith("/voices/"):
            return JSONResponse({"detail": "Inicia sesión"}, status_code=401)
        nxt = path + (("?" + request.url.query) if request.url.query else "")
        return RedirectResponse("/login" + (f"?next={nxt}" if path not in ("/", "") else ""), status_code=302)
    request.state.user = user
    resp = await call_next(request)
    if user.get("renew") and path in ("/", "/api/auth/me"):   # sesión que no vence mientras la uses
        _set_cookie(resp, request, user_token(user["id"]), True)
    return resp


def user_token(user_id: str) -> str:
    from app.db import User, session
    with session() as s:
        return auth.make_token(s.get(User, user_id), auth.DAYS, True)


def _set_cookie(resp, request: Request, token: str, remember: bool) -> None:
    https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    resp.set_cookie(auth.COOKIE, token, max_age=auth.DAYS * 86400 if remember else None,
                    httponly=True, samesite="lax", secure=https, path="/")


def current(request: Request) -> dict:
    u = getattr(request.state, "user", None) or auth.read_token(request.cookies.get(auth.COOKIE))
    if u:
        return u
    if not auth.auth_enabled():
        return {"id": "", "username": "admin", "role": "admin", "open": True}
    raise HTTPException(401, "Inicia sesión")


def _admin(request: Request) -> dict:
    u = current(request)
    if u["role"] != "admin":
        raise HTTPException(403, "Solo un admin puede hacer esto")
    return u


@router.get("/login")
async def login_page(request: Request):
    if auth.auth_enabled() and auth.read_token(request.cookies.get(auth.COOKIE)):
        return RedirectResponse("/", status_code=302)
    return FileResponse(ROOT / "web" / "login.html", headers={"Cache-Control": "no-store"})


@router.get("/api/auth/brand")
async def login_brand():
    """Lo mínimo para pintar el login con tu marca blanca (sin datos privados)."""
    from app.api import whitelabel_data
    w = whitelabel_data()
    return {k: w.get(k, "") for k in ("product_name", "tagline", "logo_url", "accent", "accent2", "chief_name")} | {
        "auth": auth.auth_enabled()}


class LoginIn(BaseModel):
    username: str
    password: str
    remember: bool = True


@router.post("/api/auth/login")
async def login(body: LoginIn, request: Request):
    ip = request.client.host if request.client else "?"
    key = f"{ip}:{body.username.strip().lower()}"
    wait = auth.too_many_fails(key, 5) or auth.too_many_fails(ip, 20)   # 5 por usuario; 20 por conexión
    if wait:
        raise HTTPException(429, f"Demasiados intentos. Espera {wait} s y vuelve a intentar.")
    user = auth.authenticate(body.username, body.password)
    if not user:
        auth.note_fail(key)
        auth.note_fail(ip)
        raise HTTPException(401, "Usuario o contraseña incorrectos")
    auth.clear_fails(key)
    resp = JSONResponse({"ok": True, "username": user.username, "role": user.role})
    _set_cookie(resp, request, auth.make_token(user, auth.DAYS if body.remember else 1, body.remember), body.remember)
    return resp


@router.post("/api/auth/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


@router.get("/api/auth/me")
async def me(request: Request):
    u = auth.read_token(request.cookies.get(auth.COOKIE))
    return {"auth": auth.auth_enabled(), "user": {k: u[k] for k in ("id", "username", "role")} if u else None}


class PasswordIn(BaseModel):
    current: str
    new: str


@router.post("/api/auth/password")
async def change_password(body: PasswordIn, request: Request):
    u = current(request)
    if u.get("open"):
        raise HTTPException(400, "Primero crea un usuario")
    if not auth.authenticate(u["username"], body.current):
        raise HTTPException(400, "La contraseña actual no es correcta")
    try:
        auth.set_password(u["username"], body.new)
    except ValueError as e:
        raise HTTPException(400, str(e))
    user = auth.authenticate(u["username"], body.new)
    resp = JSONResponse({"ok": True})
    _set_cookie(resp, request, auth.make_token(user, auth.DAYS, u.get("remember", True)), u.get("remember", True))
    return resp


# ------------------------------------------------------------------ Usuarios (solo admin)
@router.get("/api/users")
async def users(request: Request):
    _admin(request)
    return auth.list_users()


class UserIn(BaseModel):
    username: str
    password: str
    role: str = "editor"


@router.post("/api/users")
async def add_user(body: UserIn, request: Request):
    _admin(request)
    try:
        return auth.create_user(body.username, body.password, body.role)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/api/users/{uid}")
async def remove_user(uid: str, request: Request):
    me_ = _admin(request)
    if uid == me_.get("id"):
        raise HTTPException(400, "No puedes borrarte a ti mismo")
    try:
        auth.delete_user(uid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return Response(status_code=204)
