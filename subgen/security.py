import base64
import hashlib
import hmac
import ipaddress
import secrets
import struct
import threading
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
_login_lock = threading.Lock()
# Argon2 verification costs ~64 MiB and a full core; never run more than two at once.
_verify_slots = threading.BoundedSemaphore(2)
_last_totp_counter = -1


def _trusted_proxies() -> set[str]:
    return {item.strip() for item in settings.trusted_proxies.split(",") if item.strip()}


def client_ip(request: Request) -> str:
    """Client address; X-Forwarded-For is read right-to-left, skipping trusted proxies.

    Proxies append the address they received the request from, so only the rightmost entries are
    trustworthy. The leftmost entries are supplied by the client and are ignored.
    """
    peer = request.client.host if request.client else "unknown"
    trusted = _trusted_proxies()
    if peer not in trusted:
        return peer
    for hop in reversed(request.headers.get("x-forwarded-for", "").split(",")):
        hop = hop.strip()
        try:
            address = str(ipaddress.ip_address(hop))
        except ValueError:
            break
        if address not in trusted:
            return address
    return peer


def reserve_login_attempt(request: Request) -> str | None:
    """Atomically check the throttle and count this attempt; returns the key, or None when blocked."""
    now = time.monotonic()
    key = client_ip(request)
    with _login_lock:
        for stale in [k for k, v in login_attempts.items() if not v or now - v[-1] > settings.login_window_seconds]:
            login_attempts.pop(stale, None)
        attempts = login_attempts[key]
        while attempts and now - attempts[0] > settings.login_window_seconds:
            attempts.popleft()
        if len(attempts) >= settings.login_attempts:
            return None
        attempts.append(now)
        return key


def login_succeeded(key: str) -> None:
    with _login_lock:
        login_attempts.pop(key, None)


def login_allowed(request: Request) -> bool:
    now = time.monotonic()
    with _login_lock:
        attempts = login_attempts.get(client_ip(request), deque())
        return sum(1 for t in attempts if now - t <= settings.login_window_seconds) < settings.login_attempts


def record_login(request: Request, successful: bool) -> None:
    key = client_ip(request)
    with _login_lock:
        if successful:
            login_attempts.pop(key, None)
        else:
            login_attempts[key].append(time.monotonic())


def _equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _totp(secret: str, timestamp: int | None = None) -> str:
    return _totp_at(secret, int((timestamp or int(time.time())) / 30))


def _totp_at(secret: str, counter: int) -> str:
    normalized = secret.upper().replace(" ", "")
    normalized += "=" * ((8 - len(normalized) % 8) % 8)
    key = base64.b32decode(normalized)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    value = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{value:06d}"


def verify_totp(code: str) -> bool:
    """Accept the current, previous or next 30-second code, each at most once."""
    global _last_totp_counter
    if settings.totp_secret is None:
        return True
    secret = settings.totp_secret.get_secret_value()
    now = int(time.time()) // 30
    try:
        for counter in (now - 1, now, now + 1):
            if counter > _last_totp_counter and _equal(code, _totp_at(secret, counter)):
                _last_totp_counter = counter
                return True
    except (ValueError, TypeError):
        return False
    return False


def authenticate(username: str, password: str, totp_code: str = "") -> bool:
    if not _verify_slots.acquire(timeout=10):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Server busy; try again")
    try:
        username_ok = _equal(username, settings.admin_username)
        password_ok = hasher.verify(password, admin_password_hash)
    finally:
        _verify_slots.release()
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


def is_authenticated(request: Request) -> bool:
    try:
        session_payload(request)
    except HTTPException:
        return False
    return True


def require_csrf(request: Request) -> None:
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"} or request.url.path == "/api/login":
        return
    payload = session_payload(request)
    header = request.headers.get("x-csrf-token", "")
    cookie = request.cookies.get("subgen_csrf", "")
    expected = str(payload.get("csrf", ""))
    if not expected or not _equal(header, expected) or not _equal(cookie, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")


def revoke_all_sessions() -> None:
    global session_generation
    session_generation = secrets.token_hex(16)
