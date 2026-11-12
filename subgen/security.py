import base64
import hashlib
import hmac
import ipaddress
import secrets
import struct
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pwdlib import PasswordHash

from .config import get_settings

settings = get_settings()
hasher = PasswordHash.recommended()
serializer = URLSafeTimedSerializer(settings.secret_key.get_secret_value(), salt="subgen-session")
admin_password_hash = hasher.hash(settings.admin_password.get_secret_value())
login_attempts: dict[str, deque[float]] = defaultdict(deque)
session_generation = secrets.token_hex(16)


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    trusted = {item.strip() for item in settings.trusted_proxies.split(",") if item.strip()}
    if peer in trusted:
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if forwarded:
            try:
                return str(ipaddress.ip_address(forwarded))
            except ValueError:
                pass
    return peer


def login_allowed(request: Request) -> bool:
    now = time.monotonic()
    attempts = login_attempts[client_ip(request)]
    while attempts and now - attempts[0] > settings.login_window_seconds:
        attempts.popleft()
    return len(attempts) < settings.login_attempts


def record_login(request: Request, successful: bool) -> None:
    key = client_ip(request)
    if successful:
        login_attempts.pop(key, None)
    else:
        login_attempts[key].append(time.monotonic())


def _totp(secret: str, timestamp: int | None = None) -> str:
    normalized = secret.upper().replace(" ", "")
    normalized += "=" * ((8 - len(normalized) % 8) % 8)
    key = base64.b32decode(normalized)
    counter = int((timestamp or int(time.time())) / 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    value = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{value:06d}"


def verify_totp(code: str) -> bool:
    if settings.totp_secret is None:
        return True
    secret = settings.totp_secret.get_secret_value()
    now = int(time.time())
    try:
        return any(hmac.compare_digest(code, _totp(secret, now + offset)) for offset in (-30, 0, 30))
    except (ValueError, TypeError):
        return False


def authenticate(username: str, password: str, totp_code: str = "") -> bool:
    username_ok = hmac.compare_digest(username, settings.admin_username)
    password_ok = hasher.verify(password, admin_password_hash)
    return username_ok and password_ok and verify_totp(totp_code)


def session_token() -> tuple[str, str]:
    csrf = secrets.token_urlsafe(24)
    token = serializer.dumps({"user": settings.admin_username, "csrf": csrf, "generation": session_generation})
    return token, csrf


def session_payload(request: Request) -> dict[str, str]:
    token = request.cookies.get("subgen_session")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    try:
        payload = serializer.loads(token, max_age=settings.session_hours * 3600)
    except (BadSignature, SignatureExpired) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED) from exc
    if payload.get("generation") != session_generation:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session revoked")
    return payload


def require_auth(request: Request) -> str:
    return str(session_payload(request)["user"])


def require_csrf(request: Request) -> None:
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"} or request.url.path == "/api/login":
        return
    payload = session_payload(request)
    header = request.headers.get("x-csrf-token", "")
    cookie = request.cookies.get("subgen_csrf", "")
    expected = str(payload.get("csrf", ""))
    if not expected or not hmac.compare_digest(header, expected) or not hmac.compare_digest(cookie, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")


def revoke_all_sessions() -> None:
    global session_generation
    session_generation = secrets.token_hex(16)
