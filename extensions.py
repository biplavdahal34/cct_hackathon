"""Shared Flask extension instances.
Created here — NOT bound to the app — so models/forms/otp can import them
without circular imports. flask_app.py calls .init_app(app) on each."""
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail
from flask_login import LoginManager
from flask_wtf import CSRFProtect

csrf = CSRFProtect()

db = SQLAlchemy()
mail = Mail()
login_manager = LoginManager()