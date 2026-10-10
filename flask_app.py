import time
import hmac
import hashlib
import secrets
from datetime import datetime, timezone
from urllib.parse import urlparse
import os
# Load .env BEFORE importing functions.py (it may read API keys at import time).
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import requests
from flask import (Flask, Response, render_template, request,
                   redirect, url_for, flash, session)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (LoginManager, UserMixin, login_user, logout_user,
                         login_required, current_user)
from flask_mail import Mail, Message
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import (DataRequired, Email, EqualTo, Length,
                                Regexp, ValidationError)
from werkzeug.security import generate_password_hash, check_password_hash

from functions import analyze, scrape_url, market_analysis

# ------------------------------------------------------------------ app setup
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///users.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] == os.environ.get("SECRET_KEY")


@app.template_filter("fmt")
def fmt_number(v):
    """Format numbers with thousands separators: 51000 -> '51,000'. Non-numbers pass through."""
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return v

app.config.update(
    MAIL_SERVER="smtp.gmail.com",
    MAIL_PORT=587,
    MAIL_USE_TLS=True,
    MAIL_USE_SSL=False,
    MAIL_USERNAME=os.environ.get("MAIL_USERNAME"),
    MAIL_PASSWORD=os.environ.get("MAIL_PASSWORD"),
)
mail = Mail(app)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to analyze listings."
login_manager.login_message_category = "error"


# ------------------------------------------------------------------ model
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


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


with app.app_context():
    db.create_all()

# ------------------------------------------------------------------ OTP helpers
OTP_TTL_SECONDS = 600
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_COOLDOWN = 30

PENDING_KEYS = {"username", "email", "password_hash", "otp_hash",
                "expires_at", "attempts_left", "last_sent"}


def _generate_otp() -> str:
    """6-digit code, zero-padded."""
    return f"{secrets.randbelow(1000000):06d}"


def _hash_otp(code: str) -> str:
    """Keyed hash (HMAC with SECRET_KEY). The pending registration lives in a
    client-readable cookie, so a plain SHA-256 of a 6-digit code could be
    brute-forced offline by the user in about a second."""
    key = app.config["SECRET_KEY"].encode()
    return hmac.new(key, code.encode(), hashlib.sha256).hexdigest()


def _mask_email(email: str) -> str:
    """name@example.com -> n***@example.com (for display on the OTP page)."""
    try:
        name, domain = email.split("@", 1)
        return f"{name[0]}{'*' * max(len(name) - 1, 1)}@{domain}"
    except (ValueError, IndexError):
        return email


def _get_pending():
    """Return a well-formed pending registration from the session, else None."""
    pending = session.get("pending_registration")
    if not isinstance(pending, dict) or not PENDING_KEYS.issubset(pending):
        session.pop("pending_registration", None)
        return None
    return pending


def send_otp_email(to_email: str, code: str):
    if not app.config.get("MAIL_USERNAME") or not app.config.get("MAIL_PASSWORD"):
        raise RuntimeError("MAIL_USERNAME / MAIL_PASSWORD are not set in the environment.")
    msg = Message(
        subject="Your verification code — Hamrobazaar Buyer Helper",
        sender=("Hamrobazaar Buyer Helper", app.config["MAIL_USERNAME"]),
        recipients=[to_email],
        body=(
            f"Your verification code is: {code}\n\n"
            f"It expires in {OTP_TTL_SECONDS // 60} minutes.\n"
            f"If you didn't request this, you can ignore this email."
        ),
        html=f"""
        <div style="font-family:Arial,Helvetica,sans-serif;max-width:480px;margin:0 auto;
                    padding:24px;border:1px solid #e4e1da;border-radius:12px;">
          <h2 style="margin:0 0 8px;color:#23211e;">Verify your email</h2>
          <p style="color:#6f6b64;margin:0 0 18px;">
            Enter this code on the site to finish creating your account:
          </p>
          <div style="font-size:34px;font-weight:700;letter-spacing:10px;color:#0f766e;
                      background:#f4f3ef;padding:16px;text-align:center;border-radius:10px;">
            {code}
          </div>
          <p style="color:#6f6b64;font-size:13px;margin:16px 0 0;">
            Expires in {OTP_TTL_SECONDS // 60} minutes. If you didn't request this,
            you can safely ignore this email.
          </p>
        </div>""",
    )
    mail.send(msg)


# ------------------------------------------------------------------ forms
class RegisterForm(FlaskForm):
    username = StringField("Username", validators=[
        DataRequired(), Length(3, 64),
        Regexp(r'^[A-Za-z0-9_.-]+$', message="Letters, numbers, dots, dashes and underscores only."),
    ])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=120)])
    password = PasswordField("Password", validators=[DataRequired(), Length(8, 128, message="At least 8 characters.")])
    confirm = PasswordField("Confirm password", validators=[
        DataRequired(), EqualTo("password", message="Passwords do not match."),
    ])
    submit = SubmitField("Create account")

    def validate_username(self, field):
        if User.query.filter(User.username == field.data.strip().lower()).first():
            raise ValidationError("That username is taken.")

    def validate_email(self, field):
        if User.query.filter(User.email == field.data.strip().lower()).first():
            raise ValidationError("That email is already registered.")


class LoginForm(FlaskForm):
    username = StringField("Username or email", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Log in")


class OTPForm(FlaskForm):
    code = StringField("Verification code", validators=[
        DataRequired(),
        Regexp(r"^[0-9]{6}$", message="Enter the 6-digit code from your email."),
    ])
    submit = SubmitField("Verify email")


# ------------------------------------------------------------------ auth routes
@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("home"))
    form = RegisterForm()
    if form.validate_on_submit():
        username = form.username.data.strip().lower()
        email = form.email.data.strip().lower()
        code = _generate_otp()
        try:
            send_otp_email(email, code)
        except Exception:
            app.logger.exception("Could not send OTP email")
            flash("Could not send the verification email. Please try again in a moment.", "error")
            return render_template("register.html", form=form)

        # Pending registration lives in the signed session cookie.
        # Password is stored as a hash - never plaintext.
        session["pending_registration"] = {
            "username": username,
            "email": email,
            "password_hash": generate_password_hash(form.password.data),
            "otp_hash": _hash_otp(code),
            "expires_at": time.time() + OTP_TTL_SECONDS,
            "attempts_left": OTP_MAX_ATTEMPTS,
            "last_sent": time.time(),
        }
        return redirect(url_for("verify_otp"))
    return render_template("register.html", form=form)


@app.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():
    pending = _get_pending()
    if not pending:
        return redirect(url_for("register"))

    form = OTPForm()
    if form.validate_on_submit():
        if time.time() > pending["expires_at"]:
            session.pop("pending_registration", None)
            flash("That code expired. Please register again.", "error")
            return redirect(url_for("register"))

        if pending["attempts_left"] <= 0:
            session.pop("pending_registration", None)
            flash("Too many wrong attempts. Please register again.", "error")
            return redirect(url_for("register"))

        submitted = _hash_otp(form.code.data.strip())
        if not hmac.compare_digest(submitted, pending["otp_hash"]):
            pending["attempts_left"] -= 1
            session["pending_registration"] = pending
            left = pending["attempts_left"]
            if left <= 0:
                session.pop("pending_registration", None)
                flash("Too many wrong attempts. Please register again.", "error")
                return redirect(url_for("register"))
            flash(f"Wrong code. {left} attempt{'s' if left != 1 else ''} left.", "error")
            return render_template("verify_otp.html", form=form,
                                   masked_email=_mask_email(pending["email"]))

        # Correct code - create the real account
        user = User(username=pending["username"], email=pending["email"])
        user.password_hash = pending["password_hash"]   # already hashed at registration
        db.session.add(user)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.exception("Could not create user after OTP verification")
            session.pop("pending_registration", None)
            flash("Could not create the account — please register again.", "error")
            return redirect(url_for("register"))

        login_user(user)
        session.pop("pending_registration", None)
        return redirect(url_for("home"))

    return render_template("verify_otp.html", form=form,
                           masked_email=_mask_email(pending["email"]))


@app.post("/resend-otp")
def resend_otp():
    pending = _get_pending()
    if not pending:
        return redirect(url_for("register"))
    if time.time() - pending.get("last_sent", 0) < OTP_RESEND_COOLDOWN:
        flash("Please wait a few seconds before requesting a new code.", "error")
        return redirect(url_for("verify_otp"))

    code = _generate_otp()
    try:
        send_otp_email(pending["email"], code)
    except Exception:
        app.logger.exception("Could not resend OTP email")
        flash("Could not send the email — try again in a moment.", "error")
        return redirect(url_for("verify_otp"))

    pending.update({
        "otp_hash": _hash_otp(code),
        "expires_at": time.time() + OTP_TTL_SECONDS,
        "attempts_left": OTP_MAX_ATTEMPTS,
        "last_sent": time.time(),
    })
    session["pending_registration"] = pending
    flash("A new code has been sent to your email.", "ok")
    return redirect(url_for("verify_otp"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("home"))
    form = LoginForm()
    if form.validate_on_submit():
        ident = form.username.data.strip().lower()
        user = User.query.filter(
            (User.username == ident) | (User.email == ident)
        ).first()
        if user and user.check_password(form.password.data):
            login_user(user)
            next_page = request.args.get("next", "")
            parsed = urlparse(next_page)
            if (not next_page or parsed.netloc or parsed.scheme
                    or not next_page.startswith("/")
                    or next_page.startswith("//") or "\\" in next_page):
                next_page = url_for("home")
            return redirect(next_page)
        flash("Wrong username or password.", "error")
    return render_template("login.html", form=form)


@app.post("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "danger")
    return redirect(url_for("login"))


# ------------------------------------------------------------------ analyzer helpers
RATING_SCORE = {
    "strong_buy": 90, "buy_worthy": 75, "consider_with_caution": 65,
    "caution": 40, "avoid": 10,
}
RATING_COLOR = {
    "strong_buy": "green", "buy_worthy": "green", "consider_with_caution": "yellow",
    "caution": "yellow", "avoid": "red",
}


def normalize_listing(listing: dict, url: str) -> dict:
    listing = dict(listing)
    imgs = listing.get("images") or listing.get("product_img") or []
    if not isinstance(imgs, list):
        imgs = list(imgs)
    listing["images"] = imgs
    listing["url"] = url
    return listing


def _to_num(v):
    """Best-effort number from model-generated JSON (51000, 51000.0, '51,000', '85%').
    Returns None when the value is not usable."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        try:
            f = float(v.replace(",", "").replace("%", "").strip())
        except ValueError:
            return None
        return int(f) if f.is_integer() else f
    return None


def _fmt(v, default="—"):
    """Thousands-separated number, or `default` if v is not numeric."""
    n = _to_num(v)
    if n is None:
        return default
    return f"{int(n):,}" if float(n).is_integer() else f"{n:,.2f}"


def price_vm(ps: dict, pa: dict) -> dict:
    p = ps.get("asking_price") or {}
    rng = pa.get("estimated_fair_range_npr")
    lo = hi = None
    if isinstance(rng, (list, tuple)) and len(rng) == 2:
        lo, hi = _to_num(rng[0]), _to_num(rng[1])
    verdict = pa.get("verdict")
    verdict = verdict if isinstance(verdict, str) else None
    tones = {"good_deal": "ok", "fair": None, "overpriced": "warn", "suspiciously_cheap": "bad"}
    return {
        "main": f"{p.get('currency', '')} {_fmt(p.get('amount'))}".strip() if p else None,
        "tone": tones.get(verdict),
        "verdict_text": (verdict or "unavailable").replace("_", " "),
        "range": f"{_fmt(lo)}–{_fmt(hi)}" if lo and hi else "—",
        "confidence": pa.get("confidence", "low"),
    }


def normalize_report(report: dict) -> dict:
    if not isinstance(report, dict):
        raise ValueError("Model returned no usable report — try again")
    v = report.get("verdict") or {}
    rating = v.get("rating")
    if rating not in RATING_SCORE:
        rating = "caution"
        v["rating"] = rating
    score = v.get("score_0_100")
    if not isinstance(score, (int, float)) or not 0 <= score <= 100:
        v["score_0_100"] = RATING_SCORE[rating]
    v["badge_color"] = {"green": "green", "yellow": "yellow", "red": "red"}.get(
        v.get("badge_color"), RATING_COLOR[rating])
    report["verdict"] = v
    return report


def facts_rows(ps: dict, claims: dict, category: str = "phone") -> list:
    bh = claims.get("battery_health_percent")
    price = ps.get("asking_price") or {}
    match = ps.get("matches_stated_condition")
    storage_raw = str(ps.get("storage") or "").strip()
    storage_disp = f"{storage_raw} GB" if storage_raw.isdigit() else (storage_raw or None)

    rows = [
        ("Model", ps.get("model"), None),
        ("Storage", storage_disp, None),
        ("RAM", ps.get("ram"), None),          # new field from the generalized prompt
        ("Listed condition", ps.get("stated_condition"), None),
        ("Assessed condition", ps.get("assessed_condition"), None),
        ("Matches listing tag", match,
         "bad" if match == "no" else ("ok" if match == "yes" else None)),
        ("Price", (f"{price.get('currency', '')} {price.get('amount', 0):,} · "
                   f"{'negotiable' if price.get('negotiable') else 'fixed'}") if price else None, None),
    ]

    # battery row only for devices that actually have one, and only when claimed
    if category in ("phone", "laptop") and bh is not None:
        rows.append(("Battery health",
                     f"{bh}% — below 80% threshold" if bh < 80 else f"{bh}%",
                     "warn" if bh < 80 else "ok"))

    rows.append(("Box included", "Yes" if claims.get("box_included") else "No", None))

    # category-specific claims → dynamic rows (biometrics, locks, GPU, hinges, OS…)
    for k, val in (claims.get("category_specific_claims") or {}).items():
        if isinstance(val, str) and val.strip():
            rows.append((k.replace("_", " ").title(), val.replace("_", " "), None))

    rows += [
        ("Reason for selling", claims.get("reason_for_selling"), None),
        ("Location", ps.get("location"), None),
        ("Delivery", "Available" if ps.get("delivery_available") else "Not available", None),
    ]
    return [{"label": l, "value": v if v not in (None, "") else "unknown", "tone": t}
            for l, v, t in rows]


# ------------------------------------------------------------------ image proxy
IMG_HOSTS = {
    "hamrobazaar.blr1.digitaloceanspaces.com",
    "hamrobazaar.blr1.cdn.digitaloceanspaces.com",
}
_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124 Safari/537.36"}
_PLACEHOLDER = (b'<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600">'
                b'<rect width="100%" height="100%" fill="#ecebe7"/>'
                b'<text x="50%" y="50%" text-anchor="middle" fill="#8a8a86" '
                b'font-family="sans-serif" font-size="20">Photo unavailable</text></svg>')


@app.get("/img")
def img_proxy():
    src = request.args.get("u", "")
    if not src.startswith("https://") or urlparse(src).netloc not in IMG_HOSTS:
        return Response(_PLACEHOLDER, mimetype="image/svg+xml")
    try:
        r = requests.get(src, headers=_UA, timeout=25)
        ct = r.headers.get("content-type", "").split(";")[0]
        if r.status_code != 200 or not ct.startswith("image/"):
            return Response(_PLACEHOLDER, mimetype="image/svg+xml")
        return Response(r.content, mimetype=ct,
                        headers={"Cache-Control": "public, max-age=86400"})
    except Exception:
        return Response(_PLACEHOLDER, mimetype="image/svg+xml")


# ------------------------------------------------------------------ main routes
@app.get("/")
def home():
    return render_template("bbeat.html", report=None, error=None, url="",
                           facts=[], price=None)


@app.post("/")
def analyze_url():
    url = (request.form.get("url") or "").strip()
    if not url.startswith("http"):
        return render_template("bbeat.html", report=None,
                               error="Please paste a valid URL.", url=url, facts=[], price=None)
    if "hamrobazaar.com/detail/" not in url:
        return render_template("bbeat.html", report=None,
                               error="That doesn't look like a Hamrobazaar listing URL "
                                     "(expected hamrobazaar.com/detail/…).",
                               url=url, facts=[], price=None)
    try:
        listing = scrape_url(url)
        listing = normalize_listing(listing, url)
        report = normalize_report(analyze(listing))
    except Exception as e:
        app.logger.exception("Analysis failed for %s", url)
        return render_template("bbeat.html", report=None,
                               error=f"Analysis failed: {e}", url=url, facts=[], price=None)

    ps = report.get("product_summary") or {}
    claims = (report.get("description_analysis") or {}).get("extracted_claims") or {}

    # Live market pricing: search comps for the model, value with the second model call
    model_name = ps.get("model") or ps.get("title") or ""
    asking = (ps.get("asking_price") or {}).get("amount")
    try:
        market = market_analysis(model_name, asking, exclude_url=url)
        report["price_analysis"] = market["price_analysis"]      # replaces the old guess
        report["market_data"] = {
            "listings_used": market.get("listings_used", len(market.get("listings", []))),
            "market_stats": market.get("market_stats") or {},
            "listings": market.get("listings") or [],
            "excluded": market.get("excluded_listings") or [],
        }
    except Exception as e:
        app.logger.exception("Market analysis failed for %s", url)
        report["price_analysis"] = {
            "verdict": None, "estimated_fair_range_npr": None,
            "reasoning": f"Live market analysis unavailable: {e}",
            "confidence": "low",
        }
        report["market_data"] = None

    return render_template(
    "bbeat.html",
    report={"listing": listing, "A": report},
    error=None,
    url=url,
    facts=facts_rows(ps, claims, report.get("device_category") or "phone"),
    price=price_vm(ps, report.get("price_analysis") or {}),
    )


if __name__ == "__main__":
    app.run(debug=True)