"""App creation, configuration, and wiring. Routes live in routes_*.py,
models in models.py, forms in forms.py, pipeline helpers in report_views.py."""
import os
from datetime import datetime, timezone
# Load .env BEFORE importing anything that reads env at import time
# (functions.py reads API keys; extensions read nothing yet).
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from flask import Flask

from extensions import db, mail, login_manager
import models  # noqa: F401  — registers the User model + login user_loader
from security import register_security_headers
from routes_auth import register_routes as register_auth_routes
from routes_main import register_routes as register_main_routes


app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///users.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# ── session & cookie hardening ───────────────────────────────────────────
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Set SESSION_COOKIE_SECURE=1 in production (HTTPS) — breaks login over plain http dev
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "0") == "1",
    PERMANENT_SESSION_LIFETIME=12 * 3600,
    MAX_CONTENT_LENGTH=1 * 1024 * 1024,   # refuse absurdly large request bodies
)

app.config.update(
    MAIL_SERVER="smtp.gmail.com",
    MAIL_PORT=465,
    MAIL_USE_TLS=True,
    MAIL_USE_SSL=False,
    MAIL_USERNAME=os.environ.get("MAIL_USERNAME"),
    MAIL_PASSWORD=os.environ.get("MAIL_PASSWORD"),
)
from extensions import csrf
csrf.init_app(app)

# ── extensions ───────────────────────────────────────────────────────────
db.init_app(app)
mail.init_app(app)
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to analyze listings."
login_manager.login_message_category = "error"
login_manager.session_protection = "strong"   # stolen/tampered sessions get logged out


@app.template_filter("fmt")
def fmt_number(v):
    """Format numbers with thousands separators: 51000 -> '51,000'. Non-numbers pass through."""
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return v

@app.template_filter("timesince")
def timesince(dt):
    if not dt:
        return ""
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    s = int((now - dt).total_seconds())
    if s < 60:
        return "just now"
    m = s // 60
    if m < 60:
        return f"{m}m ago"
    h = m // 60
    if h < 24:
        return f"{h}h ago"
    d = h // 24
    if d < 30:
        return f"{d}d ago"
    return dt.strftime("%b %d, %Y")


register_security_headers(app)

with app.app_context():
    db.create_all()
    # Columns added after a table already exists aren't created by create_all;
    # add them in place (SQLite and Postgres both support ADD COLUMN) and backfill.
    from sqlalchemy import inspect, text
    if "url_key" not in {c["name"] for c in inspect(db.engine).get_columns("analysis")}:
        from report_views import canonical_url
        db.session.execute(text("ALTER TABLE analysis ADD COLUMN url_key VARCHAR(2048)"))
        db.session.execute(text("CREATE INDEX IF NOT EXISTS ix_analysis_url_key ON analysis (url_key)"))
        for row in models.Analysis.query.all():
            row.url_key = canonical_url(row.url)
        db.session.commit()
        app.logger.info("migrated: analysis.url_key added and backfilled")

register_auth_routes(app)
register_main_routes(app)


if __name__ == "__main__":
    # debug mode only when explicitly requested — never in deployment
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")

# ── operator commands:  venv/bin/flask --app flask_app <command> ─────────
import click  # noqa: E402

@app.cli.command("gen-encryption-key")
def gen_encryption_key():
    """Print a new KEY_ENCRYPTION_KEY (keep it out of git and the image)."""
    import crypto
    click.echo(crypto.generate_key())


@app.cli.command("rotate-api-keys")
def rotate_api_keys():
    """Re-encrypt every stored API key under the newest KEY_ENCRYPTION_KEY."""
    import crypto
    from models import UserApiKey
    n = 0
    for row in UserApiKey.query.all():
        row.ciphertext = crypto.rotate(row.ciphertext)
        n += 1
    db.session.commit()
    click.echo(f"re-encrypted {n} key(s); old master keys can now be dropped from KEY_ENCRYPTION_KEY")


@app.cli.command("wipe-api-keys")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
def wipe_api_keys(yes):
    """Breach response: delete every stored user API key (audited as 'wiped')."""
    from models import UserApiKey, KeyEvent
    rows = UserApiKey.query.all()
    if not rows:
        click.echo("no stored keys"); return
    if not yes and not click.confirm(f"Delete {len(rows)} stored key(s)? Users will have to re-add them."):
        return
    for row in rows:
        db.session.add(KeyEvent(user_id=row.user_id, provider=row.provider, event="wiped"))
        db.session.delete(row)
    db.session.commit()
    click.echo(f"deleted {len(rows)} key(s) — now tell affected users to rotate at the provider")
