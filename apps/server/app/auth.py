"""Session login for the whole service.

The gate is enforced on the server, not in the browser. A login screen that
only hides the UI would leave `POST /api/jobs` open to anyone who knows the
URL, which on a public deployment is no gate at all — so the same check that
shows the login page also guards every API route and the WebSocket.

Credentials default to `admin` / `admin123` and are overridable with
`DHC_USERNAME` / `DHC_PASSWORD`. This is a single shared operator login, not a
user system: there are no accounts, roles or password resets.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request, Response
from starlette.types import ASGIApp, Receive, Scope, Send

COOKIE = "dhc_session"
SESSION_HOURS = 12

#: Reachable without a session: the login endpoints themselves, and the health
#: probe, which container orchestrators call with no cookie jar.
PUBLIC_PATHS = frozenset({
    "/api/health",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/me",
})


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str
    secret: bytes
    #: True when the signing key was generated at boot, so sessions do not
    #: survive a restart. Set DHC_SECRET to keep people logged in.
    ephemeral_secret: bool


def _load() -> Credentials:
    raw = os.environ.get("DHC_SECRET", "")
    return Credentials(
        username=os.environ.get("DHC_USERNAME", "admin"),
        password=os.environ.get("DHC_PASSWORD", "admin123"),
        secret=raw.encode() if raw else secrets.token_bytes(32),
        ephemeral_secret=not raw,
    )


_creds = _load()


def credentials() -> Credentials:
    return _creds


# --------------------------------------------------------------------------
# tokens
# --------------------------------------------------------------------------


def _sign(payload: str) -> str:
    mac = hmac.new(_creds.secret, payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def issue(username: str) -> str:
    expires = int(time.time()) + SESSION_HOURS * 3600
    payload = f"{base64.urlsafe_b64encode(username.encode()).decode().rstrip('=')}.{expires}"
    return f"{payload}.{_sign(payload)}"


def verify(token: str | None) -> str | None:
    """Return the username a token attests to, or None if it does not hold."""
    if not token or token.count(".") != 2:
        return None
    user_b64, expires, mac = token.rsplit(".", 2)
    payload = f"{user_b64}.{expires}"
    if not hmac.compare_digest(mac, _sign(payload)):
        return None
    try:
        if int(expires) < time.time():
            return None
    except ValueError:
        return None
    try:
        pad = "=" * (-len(user_b64) % 4)
        return base64.urlsafe_b64decode(user_b64 + pad).decode()
    except Exception:
        return None


def check_password(username: str, password: str) -> bool:
    # compare_digest on both halves so a wrong username costs the same as a
    # wrong password.
    ok_user = hmac.compare_digest(username or "", _creds.username)
    ok_pass = hmac.compare_digest(password or "", _creds.password)
    return ok_user and ok_pass


# --------------------------------------------------------------------------
# middleware
# --------------------------------------------------------------------------


def _cookie_from_scope(scope: Scope) -> str | None:
    for key, value in scope.get("headers", []):
        if key == b"cookie":
            for part in value.decode("latin-1").split(";"):
                name, _, val = part.strip().partition("=")
                if name == COOKIE:
                    return val
    return None


class RequireSession:
    """Gate every `/api` path except the public few, for HTTP and WebSocket.

    Written as raw ASGI rather than `@app.middleware("http")` because that
    decorator never sees WebSocket connections, and the live telemetry stream
    is a WebSocket.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope.get("type")
        if kind not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not path.startswith("/api") or path in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        if verify(_cookie_from_scope(scope)) is not None:
            await self.app(scope, receive, send)
            return

        if kind == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return

        body = b'{"detail":"Sign in to continue."}'
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

router = APIRouter(prefix="/api/auth")


def _set_cookie(response: Response, request: Request, token: str) -> None:
    # Mark Secure only on HTTPS: forcing it on plain-HTTP localhost would make
    # the browser drop the cookie and the login would silently never take.
    https = request.url.scheme == "https" or (
        request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"
    )
    response.set_cookie(
        COOKIE, token,
        max_age=SESSION_HOURS * 3600,
        httponly=True,
        samesite="lax",
        secure=https,
        path="/",
    )


@router.post("/login")
async def login(request: Request, response: Response) -> dict[str, object]:
    try:
        body = await request.json()
    except Exception:
        body = {}
    username = str(body.get("username", ""))
    password = str(body.get("password", ""))

    if not check_password(username, password):
        raise HTTPException(401, "That username and password do not match.")

    _set_cookie(response, request, issue(username))
    return {"user": username}


@router.post("/logout")
async def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
async def me(request: Request) -> dict[str, object]:
    user = verify(request.cookies.get(COOKIE))
    return {"user": user, "authenticated": user is not None}
