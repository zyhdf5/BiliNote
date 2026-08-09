import base64
import hmac
import secrets
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware

CSRF_COOKIE = "bilinote_csrf"


class OptionalBasicAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, config_manager):
        super().__init__(app)
        self.config_manager = config_manager

    async def dispatch(self, request: Request, call_next):
        if request.url.path in {"/healthz", "/readyz"}:
            return await call_next(request)
        cfg = self.config_manager.get().security
        if not cfg.web_username or not cfg.web_password:
            return await call_next(request)
        header = request.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                decoded = base64.b64decode(header[6:]).decode("utf-8")
                username, password = decoded.split(":", 1)
                if hmac.compare_digest(username, cfg.web_username) and hmac.compare_digest(password, cfg.web_password):
                    return await call_next(request)
            except Exception:
                pass
        return PlainTextResponse(
            "Authentication required",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="BiliNote Summary"'},
        )


class CSRFCookieMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        token = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(32)
        request.state.csrf_token = token
        response = await call_next(request)
        if not request.cookies.get(CSRF_COOKIE):
            response.set_cookie(
                CSRF_COOKIE,
                token,
                secure=request.url.scheme == "https",
                httponly=True,
                samesite="strict",
                path="/",
            )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' https: data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        )
        return response


def verify_csrf_form(request: Request, submitted: str) -> None:
    cookie = request.cookies.get(CSRF_COOKIE, "")
    if not cookie or not submitted or not hmac.compare_digest(cookie, submitted):
        raise HTTPException(status_code=403, detail="invalid CSRF token")


def enforce_same_origin_if_browser(request: Request) -> None:
    """Reject cross-site browser writes without breaking normal API clients.

    CLI/server clients normally omit Origin/Referer and remain supported. Browsers
    sending a cross-site form/fetch expose Origin, Referer, or Sec-Fetch-Site.
    """
    host = request.headers.get("host", "").lower()
    origin = request.headers.get("origin", "")
    referer = request.headers.get("referer", "")
    sec_fetch_site = request.headers.get("sec-fetch-site", "").lower()

    candidate = origin or referer
    if candidate:
        parsed = urlsplit(candidate)
        if not parsed.netloc or parsed.netloc.lower() != host:
            raise HTTPException(status_code=403, detail="cross-origin write rejected")
    elif sec_fetch_site == "cross-site":
        raise HTTPException(status_code=403, detail="cross-site write rejected")


def verify_browser_api_write(request: Request) -> None:
    enforce_same_origin_if_browser(request)
    browser_signals = bool(
        request.headers.get("origin")
        or request.headers.get("referer")
        or request.headers.get("sec-fetch-site")
    )
    if not browser_signals:
        return
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get("X-CSRF-Token", "")
    if not cookie or not header or not hmac.compare_digest(cookie, header):
        raise HTTPException(status_code=403, detail="invalid CSRF token")
