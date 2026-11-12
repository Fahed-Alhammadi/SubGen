from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response

from .. import __version__
from ..config import get_settings
from ..db import SessionLocal
from ..security import (
    authenticate, client_ip, login_succeeded, require_auth, reserve_login_attempt, revoke_all_sessions,
    session_token,
)
from .common import audit

router = APIRouter()


@router.post("/api/login")
def login(
    request: Request, response: Response, username: str = Form(max_length=200),
    password: str = Form(max_length=1000), totp_code: str = Form("", max_length=10),
):
    # Login is exempt from CSRF tokens; refuse cross-site form posts so other sites can't lock you out.
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Cross-site sign-in is not allowed")
    key = reserve_login_attempt(request)
    if key is None:
        raise HTTPException(429, "Too many login attempts; try again later")
    valid = authenticate(username, password, totp_code.strip())
    if valid:
        login_succeeded(key)
    with SessionLocal() as db:
        audit(db, "auth.login" if valid else "auth.failed", username[:100], client_ip(request))
        db.commit()
    if not valid:
        raise HTTPException(401, "Invalid username, password or authenticator code")
    settings = get_settings()
    token, csrf = session_token()
    for name, value, http_only in (("subgen_session", token, True), ("subgen_csrf", csrf, False)):
        response.set_cookie(
            name, value, httponly=http_only, secure=settings.secure_cookies, samesite="strict",
            max_age=settings.session_hours * 3600,
        )
    return {"ok": True}


@router.post("/api/logout")
def logout(response: Response, request: Request, user: str = Depends(require_auth)):
    with SessionLocal() as db:
        audit(db, "auth.logout", user, client_ip(request))
        db.commit()
    revoke_all_sessions()
    response.delete_cookie("subgen_session")
    response.delete_cookie("subgen_csrf")
    return {"ok": True}


@router.get("/api/session")
def session(user: str = Depends(require_auth)):
    return {"user": user, "version": __version__, "totp_enabled": get_settings().totp_secret is not None}


@router.get("/health")
def health():
    return {"status": "ok"}
