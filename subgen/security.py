import hmac

from fastapi import HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pwdlib import PasswordHash

from .config import get_settings

settings = get_settings()
hasher = PasswordHash.recommended()
serializer = URLSafeTimedSerializer(settings.secret_key.get_secret_value(), salt="subgen-session")
admin_password_hash = hasher.hash(settings.admin_password.get_secret_value())


def authenticate(username: str, password: str) -> bool:
    username_ok = hmac.compare_digest(username, settings.admin_username)
    password_ok = hasher.verify(password, admin_password_hash)
    return username_ok and password_ok


def session_token() -> str:
    return serializer.dumps({"user": settings.admin_username})


def require_auth(request: Request) -> str:
    token = request.cookies.get("subgen_session")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    try:
        payload = serializer.loads(token, max_age=86400)
    except (BadSignature, SignatureExpired) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED) from exc
    return str(payload["user"])

