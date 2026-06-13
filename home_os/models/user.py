from datetime import datetime, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from flask_login import UserMixin

from home_os.extensions import db

ph = PasswordHasher()


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120))
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="user")
    quota_bytes = db.Column(db.BigInteger, nullable=True)
    home_directory = db.Column(db.String(512), nullable=False, default="")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    api_token_hash = db.Column(db.String(64), nullable=True)
    monthly_income = db.Column(db.Float, nullable=True, default=0)
    default_page = db.Column(db.String(30), nullable=False, default="dashboard")
    permissions = db.Column(db.Text, nullable=False, default="dashboard,files,storage,ai,calendar,budget")
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    last_login = db.Column(db.DateTime, nullable=True)

    def set_password(self, password):
        self.password_hash = ph.hash(password)

    def check_password(self, password):
        try:
            return ph.verify(self.password_hash, password)
        except VerifyMismatchError:
            return False

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def allowed_pages(self):
        if self.is_admin:
            return ["dashboard", "files", "storage", "ai", "calendar", "budget", "network", "sharing", "users", "terminal"]
        return [p.strip() for p in (self.permissions or "").split(",") if p.strip()]

    def has_permission(self, page):
        if self.is_admin:
            return True
        return page in self.allowed_pages

    def __repr__(self):
        return f"<User {self.username}>"
