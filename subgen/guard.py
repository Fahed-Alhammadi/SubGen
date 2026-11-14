"""ASGI guard that runs before routing: authentication gate and request body limits.

FastAPI reads and parses request bodies before route dependencies run, so without this guard an
anonymous client could make the server buffer arbitrarily large bodies before receiving a 401.
"""

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .security import is_authenticated
from .settings_store import current

PUBLIC_API = {"/api/login", "/api/login/totp"}
MIB = 1024 * 1024


def body_limit(method: str, path: str) -> int:
    if path == "/api/uploads":
        return int(current()["max_upload_mb"]) * MIB
    if path == "/api/restore":
        return 2048 * MIB
    if method == "PUT" and path.startswith("/api/subtitles/"):
        return 64 * MIB
    if path in PUBLIC_API:
        return 64 * 1024
    return MIB


class RequestGuard:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path, method = scope["path"], scope["method"]
        if path.startswith("/api/") and path not in PUBLIC_API and not is_authenticated(Request(scope)):
            await JSONResponse({"detail": "Not authenticated"}, status_code=401)(scope, receive, send)
            return
        limit = body_limit(method, path)
        declared = dict(scope.get("headers") or []).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > limit:
            response = JSONResponse({"detail": f"Request body exceeds {limit // MIB or 1} MB"}, status_code=413)
            await response(scope, receive, send)
            return
        if path == "/api/uploads":
            # The upload route streams to disk and enforces the same limit with a proper 413.
            await self.app(scope, receive, send)
            return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    return {"type": "http.disconnect"}  # stop reading; the route sees a disconnect
            return message

        await self.app(scope, limited_receive, send)
