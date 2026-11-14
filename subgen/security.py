import base64
import binascii
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

from .config import get_settings, load_secret_key
from .db import AppSetting, SessionLocal, utcnow
from .settings_store import current

settings = get_settings()
hasher = PasswordHash.recommended()
_signing_key = load_secret_key(settings)
serializer = URLSafeTimedSerializer(_signing_key, salt="subgen-session")
# Proves the password step passed; only the authenticator code is asked for next.
pending_serializer = URLSafeTimedSerializer(_signing_key, salt="subgen-totp-pending")
PENDING_SECONDS = 300
LOGIN_PATHS = {"/api/login", "/api/login/totp"}  # exempt from CSRF tokens (no session exists yet)
TOTP_KEY = "auth_totp_secret"  # app_settings row; not part of the pipeline settings schema
admin_password_hash = hasher.hash(settings.admin_password.get_secret_value())
login_attempts: dict[str, deque[float]] = defaultdict(deque)
session_generation = secrets.token_hex(16)
_login_lock = threading.Lock()
# Argon2 verification costs ~64 MiB and a full core; never run more than two at once.
_verify_slots = threading.BoundedSemaphore(2)
_last_totp_counter = -1


def _trusted(address: str) -> bool:
    """True when ``address`` is one of SUBGEN_TRUSTED_PROXIES (single addresses or ranges)."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in network for network in settings.proxy_networks)


def client_ip(request: Request) -> str:
    """Client address; X-Forwarded-For is read right-to-left, skipping trusted proxies.

    Proxies append the address they received the request from, so only the rightmost entries are
    trustworthy. The leftmost entries are supplied by the client and are ignored.
    """
    peer = request.client.host if request.client else "unknown"
    if not _trusted(peer):
        return peer
    for hop in reversed(request.headers.get("x-forwarded-for", "").split(",")):
        hop = hop.strip()
        try:
            address = str(ipaddress.ip_address(hop))
        except ValueError:
            break
        if not _trusted(address):
            return address
    return peer


def secure_cookies(request: Request) -> bool:
    """Whether to mark cookies Secure: SUBGEN_SECURE_COOKIES, or automatic from how the browser connected.

    Automatic means HTTPS to SubGen itself or to a reverse proxy that says so in X-Forwarded-Proto or
    Forwarded. Trusting those headers from anyone is safe here: a forged "https" only stops the forger's
    own browser from keeping the cookie, and a browser on HTTPS sends neither header itself.
    """
    if settings.secure_cookies is not None:
        return settings.secure_cookies
    if request.url.scheme == "https":
        return True
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    if any(part.strip().lower() == "https" for part in forwarded_proto.split(",")):
        return True
    forwarded = request.headers.get("forwarded", "").lower().replace(" ", "")
    return "proto=https" in forwarded


def reserve_login_attempt(request: Request) -> str | None:
    """Atomically check the throttle and count this attempt; returns the key, or None when blocked."""
    now = time.monotonic()
    key = client_ip(request)
    window, allowed = _login_limits()
    with _login_lock:
        for stale in [k for k, v in login_attempts.items() if not v or now - v[-1] > window]:
            login_attempts.pop(stale, None)
        attempts = login_attempts[key]
        while attempts and now - attempts[0] > window:
            attempts.popleft()
        if len(attempts) >= allowed:
            return None
        attempts.append(now)
        return key


def _login_limits() -> tuple[int, int]:
    """(window in seconds, failed attempts allowed in it) from Settings → Security."""
    values = current()
    return int(values["login_window_seconds"]), int(values["login_attempts"])


def session_seconds() -> int:
    return int(current()["session_hours"]) * 3600


def login_succeeded(key: str) -> None:
    with _login_lock:
        login_attempts.pop(key, None)


def login_allowed(request: Request) -> bool:
    now = time.monotonic()
    window, allowed = _login_limits()
    with _login_lock:
        attempts = login_attempts.get(client_ip(request), deque())
        return sum(1 for t in attempts if now - t <= window) < allowed


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


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def totp_uri(secret: str) -> str:
    from urllib.parse import quote

    label = quote(f"SubGen:{settings.admin_username}", safe=":@")
    return f"otpauth://totp/{label}?secret={secret}&issuer=SubGen&algorithm=SHA1&digits=6&period=30"


def totp_managed_by_env() -> bool:
    return settings.totp_secret is not None


def totp_secret() -> str | None:
    """The active secret: SUBGEN_TOTP_SECRET when set, otherwise the one enabled in Settings."""
    if settings.totp_secret is not None:
        return settings.totp_secret.get_secret_value()
    with SessionLocal() as db:
        row = db.get(AppSetting, TOTP_KEY)
        return row.value if row and row.value else None


def totp_enabled() -> bool:
    return totp_secret() is not None


def store_totp_secret(secret: str | None) -> None:
    with SessionLocal() as db:
        row = db.get(AppSetting, TOTP_KEY)
        if secret is None:
            if row:
                db.delete(row)
        elif row:
            row.value, row.updated_at = secret, utcnow()
        else:
            db.add(AppSetting(key=TOTP_KEY, value=secret))
        db.commit()


def verify_totp(code: str, secret: str | None = None) -> bool:
    """Accept the current, previous or next 30-second code, each at most once."""
    global _last_totp_counter
    secret = secret if secret is not None else totp_secret()
    if secret is None:
        return True
    code = code.strip()
    if len(code) != 6 or not code.isdigit():
        return False
    now = int(time.time()) // 30
    try:
        with _login_lock:
            for counter in (now - 1, now, now + 1):
                if counter > _last_totp_counter and _equal(code, _totp_at(secret, counter)):
                    _last_totp_counter = counter
                    return True
    except (ValueError, TypeError, binascii.Error):
        return False
    return False


def authenticate(username: str, password: str) -> bool:
    """Check the username and password (the first sign-in step)."""
    if not _verify_slots.acquire(timeout=10):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Server busy; try again")
    try:
        username_ok = _equal(username, settings.admin_username)
        password_ok = hasher.verify(password, admin_password_hash)
    finally:
        _verify_slots.release()
    return username_ok and password_ok


def pending_token() -> str:
    return pending_serializer.dumps({"user": settings.admin_username, "generation": session_generation})


def pending_valid(token: str) -> bool:
    try:
        payload = pending_serializer.loads(token, max_age=PENDING_SECONDS)
    except (BadSignature, SignatureExpired):
        return False
    return payload.get("user") == settings.admin_username and payload.get("generation") == session_generation


def session_token() -> tuple[str, str]:
    csrf = secrets.token_urlsafe(24)
    token = serializer.dumps({"user": settings.admin_username, "csrf": csrf, "generation": session_generation})
    return token, csrf


def session_payload(request: Request) -> dict[str, str]:
    token = request.cookies.get("subgen_session")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    try:
        payload = serializer.loads(token, max_age=session_seconds())
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
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"} or request.url.path in LOGIN_PATHS:
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
