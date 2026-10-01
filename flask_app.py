import os
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from flask import (Flask, Response, render_template, request,
                   redirect, url_for, flash)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (LoginManager, UserMixin, login_user, logout_user,
                         login_required, current_user)
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import (DataRequired, Email, EqualTo, Length,
                                Regexp, ValidationError)
from werkzeug.security import generate_password_hash, check_password_hash

from functions import analyze, scrape_url

# ------------------------------------------------------------------ app setup
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///users.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

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

# ------------------------------------------------------------------ auth routes
@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("home"))
    form = RegisterForm()
    if form.validate_on_submit():
        user = User(
            username=form.username.data.strip().lower(),
            email=form.email.data.strip().lower(),
        )
        user.set_password(form.password.data)
        db.session.add(user)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash("Could not create the account — try a different username/email.", "error")
            return render_template("register.html", form=form)
        login_user(user)
        return redirect(url_for("home"))
    return render_template("register.html", form=form)

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
    flash("You have been logged out.", "ok")
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

# ------------------------------------------------------------------ routes
@app.get("/")
def home():
    # public landing: logged-out visitors see Log in / Register in the topbar
    return render_template("bbeatt.html", report=None, error=None, url="",
                           facts=[], price=None)

@app.post("/")
@login_required
def analyze_url():
    url = (request.form.get("url") or "").strip()
    if not url.startswith("http"):
        return render_template("bbeatt.html", report=None,
                               error="Please paste a valid URL.", url=url, facts=[], price=None)
    if "hamrobazaar.com/detail/" not in url:
        return render_template("bbeatt.html", report=None,
                               error="That doesn't look like a Hamrobazaar listing URL "
                                     "(expected hamrobazaar.com/detail/…).",
                               url=url, facts=[], price=None)
    try:
        listing = scrape_url(url)
        listing = normalize_listing(listing, url)
        report = normalize_report(analyze(listing))
    except Exception as e:
        return render_template("bbeatt.html", report=None,
                               error=f"Analysis failed: {e}", url=url, facts=[], price=None)

    ps = report.get("product_summary") or {}
    claims = (report.get("description_analysis") or {}).get("extracted_claims") or {}
    return render_template(
        "bbeatt.html",
        report={"listing": listing, "A": report},
        error=None,
        url=url,
        facts=facts_rows(ps, claims),
        price=price_vm(ps, report.get("price_analysis") or {}),
    )

if __name__ == "__main__":
    app.run(debug=True)