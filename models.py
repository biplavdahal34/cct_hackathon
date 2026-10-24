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
    report_ne_json = db.Column(db.Text, nullable=True) # Nepali translation of report_json, made on request
    seller_key = db.Column(db.String(120), nullable=True, index=True)   # see snapshots.seller_key
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    user = db.relationship("User", backref=db.backref("analyses", lazy="dynamic"))

    @property
    def report(self) -> dict:
        return json.loads(self.report_json)

    @property
    def listing(self) -> dict:
        return json.loads(self.listing_json or "{}")

    @property
    def report_ne(self):
        return json.loads(self.report_ne_json) if self.report_ne_json else None


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


# ── listing history: one row every time BEAT fetches a listing ───────────
class ListingSnapshot(db.Model):
    """What a listing said at one moment. Diffs between rows show price cuts,
    condition-tag changes and photo swaps; seller_key groups a seller's listings."""
    id = db.Column(db.Integer, primary_key=True)
    url_key = db.Column(db.String(2048), nullable=False, index=True)
    source = db.Column(db.String(40))
    external_id = db.Column(db.String(64), nullable=True, index=True)   # the site's own listing id
    title = db.Column(db.String(300))
    price_amount = db.Column(db.Integer, nullable=True)
    price_currency = db.Column(db.String(10))
    condition = db.Column(db.String(40))
    description_hash = db.Column(db.String(64))
    description_len = db.Column(db.Integer)
    image_count = db.Column(db.Integer)
    seller_key = db.Column(db.String(120), nullable=True, index=True)
    seller_name = db.Column(db.String(100))
    seller_ad_count = db.Column(db.Integer, nullable=True)
    seller_badge = db.Column(db.String(40))
    posted_at = db.Column(db.DateTime, nullable=True)
    views = db.Column(db.Integer, nullable=True)
    analysis_id = db.Column(db.Integer, nullable=True)
    captured_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class ImageHash(db.Model):
    """Perceptual hash (dHash, 64 bits as 16 hex chars) of one listing photo.
    The same photo in two listings is found by exact match; a re-encoded copy
    by Hamming distance over recent rows."""
    id = db.Column(db.Integer, primary_key=True)
    snapshot_id = db.Column(db.Integer, db.ForeignKey("listing_snapshot.id"), nullable=False, index=True)
    url_key = db.Column(db.String(2048), nullable=False, index=True)
    hash = db.Column(db.String(16), nullable=False, index=True)
    image_url = db.Column(db.String(2048))
    captured_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)


# ── price index: one row per listing per day from the crawler ────────────
class PriceObservation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    model_key = db.Column(db.String(80), nullable=False, index=True)      # prices.model_key("iPhone 13 Pro") → "iphone-13-pro"
    search_query = db.Column(db.String(120))                              # not "query": that name is the ORM's query property
    external_id = db.Column(db.String(64), nullable=False)
    title = db.Column(db.String(300))
    price = db.Column(db.Integer, nullable=False)
    condition = db.Column(db.String(20))                                  # brand_new / like_new / used / unknown
    category = db.Column(db.String(80))
    seller_key = db.Column(db.String(120), nullable=True)
    posted_at = db.Column(db.DateTime, nullable=True)
    observed_on = db.Column(db.Date, nullable=False, index=True)
    source_url = db.Column(db.String(2048))

    __table_args__ = (db.UniqueConstraint("external_id", "observed_on", name="uq_price_obs_day"),)


# ── report chat ───────────────────────────────────────────────────────────
class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    analysis_id = db.Column(db.Integer, db.ForeignKey("analysis.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    role = db.Column(db.String(10), nullable=False)                       # user / assistant
    content = db.Column(db.Text, nullable=False)
    metered = db.Column(db.Boolean, nullable=False, default=False)        # spent the shared key → counts toward the cap
    in_scope = db.Column(db.Boolean, nullable=True)                       # assistant rows: did it answer or decline
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)


# ── proof round: seller screenshots checked against the claims ───────────
class Followup(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    analysis_id = db.Column(db.Integer, db.ForeignKey("analysis.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    image_count = db.Column(db.Integer, nullable=False, default=0)
    result_json = db.Column(db.Text, nullable=False)                     # images are not kept, only the findings
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    @property
    def result(self) -> dict:
        return json.loads(self.result_json)


# ── outcome feedback: did the verdict hold up? ────────────────────────────
class Outcome(db.Model):
    OUTCOMES = ("bought_as_described", "bought_with_issues", "did_not_buy", "scam")
    id = db.Column(db.Integer, primary_key=True)
    analysis_id = db.Column(db.Integer, db.ForeignKey("analysis.id"), nullable=False, unique=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    url_key = db.Column(db.String(2048), index=True)
    rating_at_time = db.Column(db.String(40))
    score_at_time = db.Column(db.Integer)
    outcome = db.Column(db.String(32), nullable=False)
    note = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)
