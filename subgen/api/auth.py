from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import __version__
from ..config import get_settings
from ..db import SessionLocal
from ..security import (
    PENDING_SECONDS, authenticate, client_ip, login_succeeded, new_totp_secret, pending_token, pending_valid,
    require_auth, reserve_login_attempt, revoke_all_sessions, secure_cookies, session_seconds, session_token,
    store_totp_secret, totp_enabled, totp_managed_by_env, totp_secret, totp_uri, verify_totp,
)
from .common import audit

router = APIRouter()
PENDING_COOKIE = "subgen_pending"
PENDING_PATH = "/api/login"


def _refuse_cross_site(request: Request) -> None:
    # Login is exempt from CSRF tokens; refuse cross-site form posts so other sites can't lock you out.
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Cross-site sign-in is not allowed")


def _reserve(request: Request) -> str:
    key = reserve_login_attempt(request)
    if key is None:
        raise HTTPException(429, "Too many login attempts; try again later")
    return key


def _audit(event: str, username: str, request: Request, detail: str = "") -> None:
    with SessionLocal() as db:
        audit(db, event, username[:100], client_ip(request) + (f" {detail}" if detail else ""))
        db.commit()


def _start_session(request: Request, response: Response) -> None:
    token, csrf = session_token()
    secure, max_age = secure_cookies(request), session_seconds()
    for name, value, http_only in (("subgen_session", token, True), ("subgen_csrf", csrf, False)):
        response.set_cookie(
            name, value, httponly=http_only, secure=secure, samesite="strict", max_age=max_age,
        )
    response.delete_cookie(PENDING_COOKIE, path=PENDING_PATH)


@router.post("/api/login")
def login(
    request: Request, response: Response, username: str = Form(max_length=200),
    password: str = Form(max_length=1000), totp_code: str = Form("", max_length=10),
):
    """First sign-in step. With two-factor sign-in enabled, it answers ``totp_required`` and the
    authenticator code is sent to ``/api/login/totp`` (or here as ``totp_code``, for scripts)."""
    _refuse_cross_site(request)
    key = _reserve(request)
    if not authenticate(username, password):
        _audit("auth.failed", username, request)
        raise HTTPException(401, "Invalid username or password")
    if totp_enabled():
        if not totp_code.strip():
            response.set_cookie(
                PENDING_COOKIE, pending_token(), httponly=True, secure=secure_cookies(request),
                samesite="strict", max_age=PENDING_SECONDS, path=PENDING_PATH,
            )
            return {"ok": False, "totp_required": True}
        if not verify_totp(totp_code):
            _audit("auth.failed", username, request, "(authenticator code)")
            raise HTTPException(401, "Invalid authenticator code")
    login_succeeded(key)
    _audit("auth.login", username, request)
    _start_session(request, response)
    return {"ok": True}


@router.post("/api/login/totp")
def login_totp(request: Request, response: Response, totp_code: str = Form(max_length=10)):
    """Second sign-in step: the six-digit code, after the password was accepted."""
    _refuse_cross_site(request)
    key = _reserve(request)
    username = get_settings().admin_username
    if not pending_valid(request.cookies.get(PENDING_COOKIE, "")):
        raise HTTPException(401, "Sign-in expired. Enter your password again.")
    if not verify_totp(totp_code):
        _audit("auth.failed", username, request, "(authenticator code)")
        raise HTTPException(401, "Invalid authenticator code")
    login_succeeded(key)
    _audit("auth.login", username, request)
    _start_session(request, response)
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
    return {"user": user, "version": __version__, "totp_enabled": totp_enabled()}


class TotpEnableInput(BaseModel):
    secret: str = Field(min_length=16, max_length=64, pattern=r"^[A-Z2-7]+$")
    code: str = Field(max_length=10)


class TotpCodeInput(BaseModel):
    code: str = Field(max_length=10)


@router.get("/api/totp")
def totp_status(_: str = Depends(require_auth)):
    return {"enabled": totp_enabled(), "managed_by_env": totp_managed_by_env()}


@router.post("/api/totp/setup")
def totp_setup(_: str = Depends(require_auth)):
    """A fresh secret to add to an authenticator app. Nothing is stored until /enable confirms it."""
    if totp_enabled():
        raise HTTPException(409, "Two-factor sign-in is already enabled")
    secret = new_totp_secret()
    return {"secret": secret, "uri": totp_uri(secret)}


@router.post("/api/totp/enable")
def totp_enable(payload: TotpEnableInput, request: Request, user: str = Depends(require_auth)):
    if totp_enabled():
        raise HTTPException(409, "Two-factor sign-in is already enabled")
    if not verify_totp(payload.code, payload.secret):
        raise HTTPException(422, "That code does not match. Check the time on your phone and try the newest code.")
    store_totp_secret(payload.secret)
    _audit("auth.totp_enabled", user, request)
    return {"enabled": True, "managed_by_env": False}


@router.post("/api/totp/disable")
def totp_disable(payload: TotpCodeInput, request: Request, user: str = Depends(require_auth)):
    if totp_managed_by_env():
        raise HTTPException(409, "Two-factor sign-in is set by SUBGEN_TOTP_SECRET; remove it from the environment")
    secret = totp_secret()
    if secret is None:
        return {"enabled": False, "managed_by_env": False}
    if not verify_totp(payload.code, secret):
        raise HTTPException(422, "Invalid authenticator code")
    store_totp_secret(None)
    _audit("auth.totp_disabled", user, request)
    return {"enabled": False, "managed_by_env": False}


@router.get("/health")
def health():
    return {"status": "ok"}
