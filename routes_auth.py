"""Authentication routes: register → OTP → login → logout."""
import time
import hmac
from urllib.parse import urlparse

from flask import (render_template, request, redirect, url_for, flash, session)
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash

from extensions import db
from security import limiter, client_ip
from forms import RegisterForm, LoginForm, OTPForm
from models import User
from otp import (OTP_TTL_SECONDS, OTP_MAX_ATTEMPTS, generate_otp, hash_otp,
                 get_pending, send_otp_email, mask_email)


def register_routes(app):

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if current_user.is_authenticated:
            return redirect(url_for("home"))
        form = RegisterForm()
        if form.validate_on_submit():
            ip = client_ip()
            if not limiter.hit(f"reg:{ip}", limit=30, window=3600):
                flash("Too many registration attempts from this network. Try again later.", "error")
                return render_template("register.html", form=form)

            username = form.username.data.strip().lower()
            email = form.email.data.strip().lower()
            code = generate_otp()
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
                "otp_hash": hash_otp(code),
                "expires_at": time.time() + OTP_TTL_SECONDS,
                "attempts_left": OTP_MAX_ATTEMPTS,
                "last_sent": time.time(),
            }
            return redirect(url_for("verify_otp"))
        return render_template("register.html", form=form)

    @app.route("/verify-otp", methods=["GET", "POST"])
    def verify_otp():
        pending = get_pending()
        if not pending:
            return redirect(url_for("register"))

        form = OTPForm()
        if form.validate_on_submit():
            ip = client_ip()
            if not limiter.hit(f"otp:{ip}", limit=20, window=300):
                flash("Too many attempts. Please wait a few minutes.", "error")
                return render_template("verify_otp.html", form=form,
                                       masked_email=mask_email(pending["email"]))

            if time.time() > pending["expires_at"]:
                session.pop("pending_registration", None)
                flash("That code expired. Please register again.", "error")
                return redirect(url_for("register"))

            if pending["attempts_left"] <= 0:
                session.pop("pending_registration", None)
                flash("Too many wrong attempts. Please register again.", "error")
                return redirect(url_for("register"))

            submitted = hash_otp(form.code.data.strip())
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
                                       masked_email=mask_email(pending["email"]))

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

            session.clear()      # rotate session on privilege change (fixation defense)
            login_user(user)
            session.pop("pending_registration", None)
            return redirect(url_for("home"))

        return render_template("verify_otp.html", form=form,
                               masked_email=mask_email(pending["email"]))

    @app.post("/resend-otp")
    def resend_otp():
        pending = get_pending()
        if not pending:
            return redirect(url_for("register"))
        if time.time() - pending.get("last_sent", 0) < 30:
            flash("Please wait a few seconds before requesting a new code.", "error")
            return redirect(url_for("verify_otp"))
        if not limiter.hit(f"resend:{client_ip()}", limit=5, window=3600):
            flash("Too many resend requests. Try again later.", "error")
            return redirect(url_for("verify_otp"))

        code = generate_otp()
        try:
            send_otp_email(pending["email"], code)
        except Exception:
            app.logger.exception("Could not resend OTP email")
            flash("Could not send the email — try again in a moment.", "error")
            return redirect(url_for("verify_otp"))

        pending.update({
            "otp_hash": hash_otp(code),
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
            ip = client_ip()
            ident = form.username.data.strip().lower()
            # brute-force lockout: per network AND per targeted account
            if (not limiter.hit(f"login-ip:{ip}", limit=10, window=300)
                    or not limiter.hit(f"login-id:{ident}", limit=5, window=300)):
                flash("Too many login attempts. Please wait a few minutes.", "error")
                return render_template("login.html", form=form)

            user = User.query.filter(
                (User.username == ident) | (User.email == ident)
            ).first()
            if user and user.check_password(form.password.data):
                session.clear()  # rotate session on privilege change (fixation defense)
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
        session.clear()
        flash("You have been logged out.", "danger")
        return redirect(url_for("login"))