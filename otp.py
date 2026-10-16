"""OTP generation, keyed hashing, pending-registration state, and email delivery."""
import time
import hmac
import hashlib
import secrets

from flask import current_app, session
from flask_mail import Message
import os,requests

from extensions import mail

OTP_TTL_SECONDS = 600
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_COOLDOWN = 30

PENDING_KEYS = {"username", "email", "password_hash", "otp_hash",
                "expires_at", "attempts_left", "last_sent"}


def generate_otp() -> str:
    """6-digit code, zero-padded."""
    return f"{secrets.randbelow(1000000):06d}"


def hash_otp(code: str) -> str:
    """Keyed hash (HMAC with SECRET_KEY). The pending registration lives in a
    client-readable cookie, so a plain SHA-256 of a 6-digit code could be
    brute-forced offline by the user in about a second."""
    key = current_app.config["SECRET_KEY"].encode()
    return hmac.new(key, code.encode(), hashlib.sha256).hexdigest()


def mask_email(email: str) -> str:
    """name@example.com -> n***@example.com (for display on the OTP page)."""
    try:
        name, domain = email.split("@", 1)
        return f"{name[0]}{'*' * max(len(name) - 1, 1)}@{domain}"
    except (ValueError, IndexError):
        return email


def get_pending():
    """Return a well-formed pending registration from the session, else None."""
    pending = session.get("pending_registration")
    if not isinstance(pending, dict) or not PENDING_KEYS.issubset(pending):
        session.pop("pending_registration", None)
        return None
    return pending

import requests as _rq

def send_otp_email(to_email: str, code: str):
    """Send the OTP via Resend's HTTP API. Raises ValueError with a
    user-safe message; details go to the app log via the caller."""
    import os
    import requests

    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        raise ValueError("Email service is not configured (RESEND_API_KEY missing).")
    try:
        r = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": "BEAT <onboarding@resend.dev>",
                "to": [to_email],
                "subject": f"Your verification code: {code}",
                "html": (
                    "<div style='font-family:Arial,sans-serif;max-width:480px;"
                    "margin:0 auto;padding:24px;border:1px solid #e4e1da;"
                    "border-radius:12px;'>"
                    "<h2 style='color:#23211e;'>Verify your email</h2>"
                    "<p style='color:#6f6b64;'>Enter this code to finish creating "
                    "your account:</p>"
                    "<div style='font-size:34px;font-weight:700;letter-spacing:10px;"
                    "color:#0f766e;background:#f4f3ef;padding:16px;text-align:center;"
                    "border-radius:10px;'>" + code + "</div>"
                    "<p style='color:#6f6b64;font-size:13px;'>Expires in 10 minutes. "
                    "If you didn't request this, you can safely ignore this email.</p>"
                    "</div>"
                ),
            },
            timeout=15,
        )
        if r.status_code >= 400:
            try:
                detail = r.json().get("message", "")
            except Exception:
                detail = r.text[:200]
            raise ValueError(f"Email delivery failed ({r.status_code}): {detail}")
    except requests.RequestException:
        raise ValueError("Could not reach the email service — check your connection.") from None