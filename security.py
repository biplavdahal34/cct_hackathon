"""Rate limiting, client-IP resolution, manual CSRF, and response headers."""
import os
import time
import threading

from flask import request
from flask_wtf.csrf import validate_csrf


class RateLimiter:
    """In-process sliding-window limiter (per gunicorn worker).
    For multi-host scale swap the dict for Redis — semantics stay identical."""
    def __init__(self):
        self._hits = {}
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window: int) -> bool:
        """Record a hit; return False when the caller exceeded the limit."""
        now = time.time()
        with self._lock:
            start, count = self._hits.get(key, (now, 0))
            if now - start >= window:
                start, count = now, 0
            count += 1
            self._hits[key] = (start, count)
            if len(self._hits) > 10000:                     # prune old entries
                cutoff = now - 3600
                self._hits = {k: v for k, v in self._hits.items() if v[0] > cutoff}
            return count <= limit


limiter = RateLimiter()


def client_ip() -> str:
    """Behind our nginx: X-Real-IP is set by the proxy (can't be spoofed).
    Falls back to remote_addr for direct/local access."""
    return (request.headers.get("X-Real-IP")
            or (request.remote_addr or "?"))


def is_https() -> bool:
    """True when the client's connection to nginx was TLS (nginx forwards the
    scheme) or Flask itself sees HTTPS. Used to refuse secrets over plain HTTP."""
    return (request.headers.get("X-Forwarded-Proto", "").lower() == "https"
            or request.is_secure)


def production() -> bool:
    """SESSION_COOKIE_SECURE=1 doubles as 'this deployment is behind HTTPS'."""
    return os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"


def csrf_ok() -> bool:
    """Manual CSRF validation for the plain (non-WTF) analyze form."""
    try:
        validate_csrf(request.form.get("csrf_token", ""))
        return True
    except Exception:
        return False


def register_security_headers(app):
    """Attach security headers to every response."""
    @app.after_request
    def _set_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Permissions-Policy", "geolocation=(), camera=(), microphone=()")
        if production():
            # browsers pin HTTPS for a year; pages carrying account data are never cached
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if "text/html" in (resp.headers.get("Content-Type") or "") and request.cookies.get("session"):
            resp.headers.setdefault("Cache-Control", "no-store")
        # inline script/style are required by the current templates (theme toggle +
        # loading overlay / inline CSS). Hardening path: external files + nonces.
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        return resp