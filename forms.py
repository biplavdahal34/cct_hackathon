from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import (DataRequired, Email, EqualTo, Length,
                                Regexp, ValidationError)

from models import User

DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com",
    "temp-mail.org", "yopmail.com", "trashmail.com", "sharklasers.com",
    "getnada.com", "dispostable.com", "throwawaymail.com",
}


class RegisterForm(FlaskForm):
    username = StringField("Username", validators=[
        DataRequired(), Length(3, 64),
        Regexp(r'^[A-Za-z0-9_.-]+$', message="Letters, numbers, dots, dashes and underscores only."),
    ])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=120)])
    password = PasswordField("Password", validators=[
        DataRequired(), Length(8, 128, message="At least 8 characters."),
        # NOTE: WTForms' Regexp uses re.match (anchored at string start).
        # The .* prefix gives it search semantics — without it, "hello123"
        # would fail the digit check because position 0 isn't a digit.
        Regexp(r".*[A-Za-z]", message="Must contain at least one letter."),
        Regexp(r".*\d", message="Must contain at least one digit."),
    ])
    confirm = PasswordField("Confirm password", validators=[
        DataRequired(), EqualTo("password", message="Passwords do not match."),
    ])
    submit = SubmitField("Create account")

    def validate_username(self, field):
        if User.query.filter(User.username == field.data.strip().lower()).first():
            raise ValidationError("That username is taken.")

    def validate_email(self, field):
        email = field.data.strip().lower()
        domain = email.rsplit("@", 1)[-1]
        if domain in DISPOSABLE_DOMAINS:
            raise ValidationError("Disposable email addresses are not allowed.")
        if User.query.filter(User.email == email).first():
            raise ValidationError("That email is already registered.")

    def validate_password(self, field):
        username = (self.username.data or "").strip().lower()
        if username and username in field.data.lower():
            raise ValidationError("Password must not contain your username.")


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