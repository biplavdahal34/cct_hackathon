import os
import re
import time
import hashlib
import secrets
from datetime import datetime, timezone
from urllib.parse import urlparse

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

@app.template_filter("fmt")
def fmt_number(v):
    """Format numbers with thousands separators: 51000 → '51,000'. Non-numbers pass through."""
    try:
        return f"{int(v):,}"
        # 51,000-style formatting for prices/stats in templates
    except (TypeError, ValueError):
        return v

# ── Flask-Mail (hardcoded for testing — ROTATE this app password after the demo,
#    it was pasted into chat. Google Account → Security → App passwords) ──────
app.config.update(
    MAIL_SERVER="smtp.gmail.com",
    MAIL_PORT=587,
    MAIL_USE_TLS=True,
    MAIL_USE_SSL=False,
    MAIL_USERNAME="ok.2346756@gmail.com",
    MAIL_PASSWORD="mmty kmyv ymop gkwz",
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

OTP_TTL_SECONDS = 600        
OTP_MAX_ATTEMPTS = 5         
OTP_RESEND_COOLDOWN = 30     

def _generate_otp() -> str:
    """6-digit code, zero-padded."""
    return f"{secrets.randbelow(1000000):06d}"

def _hash_otp(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()

def _mask_email(email: str) -> str:
    """name@example.com → d***@example.com (for display on the OTP page)."""
    try:
        name, domain = email.split("@", 1)
        return f"{name[0]}{'*' * max(len(name) - 1, 1)}@{domain}"
    except ValueError:
        return email

def send_otp_email(to_email: str, code: str):
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
        Regexp(r"^\d{6}$", message="Enter the 6-digit code from your email."),
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
            flash("Could not send the verification email — check your connection and try again.", "error")
            return render_template("register.html", form=form)

        # pending registration lives in the signed session cookie.
        # password stored as a hash — never plaintext.
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
    pending = session.get("pending_registration")
    if not pending:
        return redirect(url_for("register"))

    form = OTPForm()
    if form.validate_on_submit():
        pending = session["pending_registration"]

        if time.time() > pending["expires_at"]:
            session.pop("pending_registration", None)
            flash("That code expired. Please register again.", "error")
            return redirect(url_for("register"))

        if pending["attempts_left"] <= 0:
            session.pop("pending_registration", None)
            flash("Too many wrong attempts. Please register again.", "error")
            return redirect(url_for("register"))

        if _hash_otp(form.code.data.strip()) != pending["otp_hash"]:
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

        # ✓ correct code — create the real account
        user = User(username=pending["username"], email=pending["email"])
        user.password_hash = pending["password_hash"]   # already hashed at registration
        db.session.add(user)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
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
    pending = session.get("pending_registration")
    if not pending:
        return redirect(url_for("register"))
    if time.time() - pending.get("last_sent", 0) < OTP_RESEND_COOLDOWN:
        flash("Please wait a few seconds before requesting a new code.", "error")
        return redirect(url_for("verify_otp"))

    code = _generate_otp()
    try:
        send_otp_email(pending["email"], code)
    except Exception:
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
            next_page = request.args.get("next")
            if not next_page or urlparse(next_page).netloc != "":
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

# ------------------------------------------------------------------ analyzer helpers (unchanged)
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


def price_vm(ps: dict, pa: dict) -> dict:
    p = ps.get("asking_price") or {}
    rng = pa.get("estimated_fair_range_npr") or [None, None]
    tones = {"good_deal": "ok", "fair": None, "overpriced": "warn", "suspiciously_cheap": "bad"}
    return {
        "main": f"{p.get('currency', '')} {p.get('amount', 0):,}" if p else None,
        "tone": tones.get(pa.get("verdict")),
        "verdict_text": (pa.get("verdict") or "unavailable").replace("_", " "),
        "range": f"{rng[0]:,}–{rng[1]:,}" if rng[0] and rng[1] else "—",
        "confidence": pa.get("confidence", "low"),
    }

def normalize_report(report: dict) -> dict:
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

def facts_rows(ps: dict, claims: dict) -> list:
    bh = claims.get("battery_health_percent")
    price = ps.get("asking_price") or {}
    match = ps.get("matches_stated_condition")
    storage = str(ps.get("storage") or "").replace("GB", "").strip()
    rows = [
        ("Model", ps.get("model"), None),
        ("Storage", f"{storage} GB" if storage else None, None),
        ("Listed condition", ps.get("stated_condition"), None),
        ("Assessed condition", ps.get("assessed_condition"), None),
        ("Matches listing tag", match,
         "bad" if match == "no" else ("ok" if match == "yes" else None)),
        ("Price", (f"{price.get('currency', '')} {price.get('amount', 0):,} · "
                   f"{'negotiable' if price.get('negotiable') else 'fixed'}") if price else None, None),
        ("Battery health",
         (f"{bh}% — below 80% threshold" if bh < 80 else f"{bh}%") if bh is not None else None,
         "warn" if (bh is not None and bh < 80) else ("ok" if bh is not None else None)),
        ("Box included", "Yes" if claims.get("box_included") else "No", None),
        ("Face ID / True Tone",
         f"{'Claimed working' if claims.get('face_id') == 'claimed_working' else 'Not mentioned'} / "
         f"{'claimed working' if claims.get('true_tone') == 'claimed_working' else 'not mentioned'}", None),
        ("Reason for selling", claims.get("reason_for_selling"), None),
        ("Location", ps.get("location"), None),
        ("Delivery", "Available" if ps.get("delivery_available") else "Not available", None),
    ]
    return [{"label": l, "value": v if v not in (None, "") else "unknown", "tone": t}
            for l, v, t in rows]

def price_vm(ps: dict, pa: dict) -> dict:
    p = ps.get("asking_price") or {}
    rng = pa.get("estimated_fair_range_npr") or [0, 0]
    tones = {"good_deal": "ok", "fair": None, "overpriced": "warn", "suspiciously_cheap": "bad"}
    return {
        "main": f"{p.get('currency', '')} {p.get('amount', 0):,}" if p else None,
        "tone": tones.get(pa.get("verdict")),
        "verdict_text": (pa.get("verdict") or "").replace("_", " "),
        "range": f"{rng[0]:,}–{rng[1]:,}",
        "confidence": pa.get("confidence", "low"),
    }


SEARCH_API = "https://hamrobazaar.com/api/products/search"

def _search_api(product_name: str, exclude_url: str = None, limit: int = 10) -> list:
    payload = {
        "keyword": product_name,     # ← match the real key names from the Request tab
        "latitude": 0,
        "longitude": 0,
    }
    r = requests.post(
        SEARCH_API,
        json=payload,
        headers={
            "User-Agent": _UA["User-Agent"],
            "Origin": "https://hamrobazaar.com",
            "Referer": "https://hamrobazaar.com/search/product",
        },
        proxies=proxies,
        timeout=30,
    )
    r.raise_for_status()

    out = []
    _listings_from_json(r.json(), out)          # tolerant walker: any key naming works

    # dedupe, junk-filter, exclude the subject listing, cap at limit
    seen, results = set(), []
    for item in out:
        u = item["url"].split("?")[0]
        if u in seen or (exclude_url and u.rstrip("/") == exclude_url.rstrip("/")):
            continue
        if _JUNK_PATTERNS.search(item["title"]):
            continue
        seen.add(u)
        results.append({**item, "url": u})
        if len(results) >= limit:
            break
    return results
    
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
        return render_template("bbeat.html", report=None,
                               error=f"Analysis failed: {e}", url=url, facts=[], price=None)

    ps = report.get("product_summary") or {}
    claims = (report.get("description_analysis") or {}).get("extracted_claims") or {}

    # ── live market pricing: search comps for the model, value with Gemini #2 ──
    model_name = ps.get("model") or ps.get("title") or ""
    asking = (ps.get("asking_price") or {}).get("amount")
    market = None
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
        report["price_analysis"] = {
            "verdict": None, "estimated_fair_range_npr": None,
            "reasoning": f"Live market analysis unavailable: {e}",
            "confidence": "low",
        }
        report["market_data"] = None

    return render_template(
        "bbeat.html",                      # ← your actual template name
        report={"listing": listing, "A": report},
        error=None,
        url=url,
        facts=facts_rows(ps, claims),
        price=price_vm(ps, report.get("price_analysis") or {}),
    )

if __name__ == "__main__":
    app.run(debug=True)