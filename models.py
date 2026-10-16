from datetime import datetime, timezone
import json

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from extensions import db, login_manager


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Analysis(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    url = db.Column(db.String(2048), nullable=False)
    url_key = db.Column(db.String(2048), index=True)      # canonical form, see report_views.canonical_url
    title = db.Column(db.String(300))
    source = db.Column(db.String(40))            # hamrobazaar / facebook_marketplace / facebook_post
    device_category = db.Column(db.String(20))
    rating = db.Column(db.String(40))
    badge_color = db.Column(db.String(10))
    score = db.Column(db.Integer)
    price_amount = db.Column(db.Integer, nullable=True)
    price_currency = db.Column(db.String(10))
    report_json = db.Column(db.Text, nullable=False)   # full AI report
    listing_json = db.Column(db.Text)                  # scraped listing (images, seller…)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    user = db.relationship("User", backref=db.backref("analyses", lazy="dynamic"))

    @property
    def report(self) -> dict:
        return json.loads(self.report_json)

    @property
    def listing(self) -> dict:
        return json.loads(self.listing_json or "{}")


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# ── bring-your-own API keys ──────────────────────────────────────────────
class UserApiKey(db.Model):
    """One encrypted key per (user, provider). The plaintext is never stored;
    `last4` is kept separately so the UI can show "····a1b2"."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    provider = db.Column(db.String(16), nullable=False)       # gemini / apify
    ciphertext = db.Column(db.Text, nullable=False)
    last4 = db.Column(db.String(4), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_used_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (db.UniqueConstraint("user_id", "provider", name="uq_user_provider"),)


class KeyEvent(db.Model):
    """Audit trail for key management — never contains key material."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    provider = db.Column(db.String(16), nullable=False)
    event = db.Column(db.String(16), nullable=False)          # added / replaced / removed / used / invalid / undecryptable
    analysis_id = db.Column(db.Integer, nullable=True)
    ip = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class UsageEvent(db.Model):
    """One row per analysis attempt that spent BEAT's shared keys. Drives the
    per-user and global caps in limits.py; rows older than two days are pruned."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    ip = db.Column(db.String(64), nullable=True)
    source = db.Column(db.String(16), nullable=False)         # hamrobazaar / facebook
    server_gemini = db.Column(db.Boolean, nullable=False, default=False)
    server_apify = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)
