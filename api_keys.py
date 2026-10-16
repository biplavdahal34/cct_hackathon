"""Bring-your-own API keys: validate, encrypt, store, load, audit.

Plaintext keys exist only (a) in the request that saves them and (b) inside
one analysis request after decryption. They are never logged, never put in
the session, and never rendered back to the browser."""
from datetime import datetime, timezone

import requests
from google import genai

import crypto
from extensions import db
from models import UserApiKey, KeyEvent

PROVIDERS = {
    "gemini": {
        "label": "Gemini",
        "help": "Free key from Google AI Studio. Used to read the photos and write the report.",
        "where": "https://aistudio.google.com/app/apikey",
        "min_len": 20,
    },
    "apify": {
        "label": "Apify",
        "help": "Free token from Apify. Only needed for Facebook listings.",
        "where": "https://console.apify.com/account/integrations",
        "min_len": 20,
    },
}


class KeyInvalid(ValueError):
    """The provider rejected the key, or we couldn't verify it."""


# ── validation (one cheap call per provider) ───────────────────────────────
def _validate_gemini(key: str):
    try:
        client = genai.Client(api_key=key, http_options={"timeout": 15_000})
        next(iter(client.models.list(config={"page_size": 1})), None)
    except Exception as e:                       # SDK raises on 400 "API key not valid"
        msg = str(e)
        if "API key" in msg or "401" in msg or "403" in msg or "400" in msg or "PERMISSION" in msg.upper():
            raise KeyInvalid("Google rejected that key. Check it was copied completely.") from None
        raise KeyInvalid("Couldn't reach Google to verify the key — try again in a moment.") from None


def _validate_apify(token: str):
    try:
        r = requests.get("https://api.apify.com/v2/users/me",
                         headers={"Authorization": f"Bearer {token}"}, timeout=15)
    except requests.RequestException:
        raise KeyInvalid("Couldn't reach Apify to verify the token — try again in a moment.") from None
    if r.status_code in (401, 403):
        raise KeyInvalid("Apify rejected that token. Check it was copied completely.")
    if r.status_code != 200:
        raise KeyInvalid(f"Apify answered with an unexpected status ({r.status_code}).")


_VALIDATORS = {"gemini": _validate_gemini, "apify": _validate_apify}


def _clean(provider: str, raw: str) -> str:
    key = (raw or "").strip()
    spec = PROVIDERS[provider]
    if len(key) < spec["min_len"] or len(key) > 512 or any(c.isspace() for c in key):
        raise KeyInvalid("That doesn't look like a complete key.")
    return key


def validate(provider: str, raw_key: str) -> str:
    """Clean + verify a key with its provider without storing it (per-check keys).
    Returns the cleaned key; raises KeyInvalid."""
    if provider not in PROVIDERS:
        raise KeyInvalid("Unknown provider.")
    key = _clean(provider, raw_key)
    _VALIDATORS[provider](key)
    return key


# ── storage ───────────────────────────────────────────────────────────────
def _event(user_id, provider, event, ip=None, analysis_id=None):
    db.session.add(KeyEvent(user_id=user_id, provider=provider, event=event,
                            ip=ip, analysis_id=analysis_id))


def save_key(user_id: int, provider: str, raw_key: str, ip: str = None):
    """Validate with the provider, encrypt, upsert. Raises KeyInvalid."""
    if provider not in PROVIDERS:
        raise KeyInvalid("Unknown provider.")
    if not crypto.is_configured():
        raise KeyInvalid("Key storage isn't enabled on this server.")
    key = _clean(provider, raw_key)
    _VALIDATORS[provider](key)

    row = UserApiKey.query.filter_by(user_id=user_id, provider=provider).first()
    event = "replaced" if row else "added"
    if not row:
        row = UserApiKey(user_id=user_id, provider=provider)
        db.session.add(row)
    row.ciphertext = crypto.encrypt(key)
    row.last4 = key[-4:]
    row.created_at = datetime.now(timezone.utc)
    row.last_used_at = None
    _event(user_id, provider, event, ip)
    db.session.commit()


def remove_key(user_id: int, provider: str, ip: str = None) -> bool:
    row = UserApiKey.query.filter_by(user_id=user_id, provider=provider).first()
    if not row:
        return False
    db.session.delete(row)
    _event(user_id, provider, "removed", ip)
    db.session.commit()
    return True


def load_keys(user_id: int) -> dict:
    """{provider: plaintext} for this user. Rows that can't be decrypted
    (master key rotated away) are skipped and audited, never raised."""
    if not crypto.is_configured():
        return {}
    out = {}
    for row in UserApiKey.query.filter_by(user_id=user_id).all():
        try:
            out[row.provider] = crypto.decrypt(row.ciphertext)
        except crypto.InvalidToken:
            _event(user_id, row.provider, "undecryptable")
            db.session.commit()
    return out


def mark_used(user_id: int, providers, analysis_id: int = None):
    now = datetime.now(timezone.utc)
    for p in providers:
        row = UserApiKey.query.filter_by(user_id=user_id, provider=p).first()
        if row:
            row.last_used_at = now
            _event(user_id, p, "used", analysis_id=analysis_id)
    db.session.commit()


def status(user_id: int) -> dict:
    """Display data for the account page — no key material."""
    rows = {r.provider: r for r in UserApiKey.query.filter_by(user_id=user_id).all()}
    return {p: {"label": spec["label"], "help": spec["help"], "where": spec["where"],
                "saved": p in rows,
                "last4": rows[p].last4 if p in rows else None,
                "added_at": rows[p].created_at if p in rows else None,
                "last_used_at": rows[p].last_used_at if p in rows else None}
            for p, spec in PROVIDERS.items()}
