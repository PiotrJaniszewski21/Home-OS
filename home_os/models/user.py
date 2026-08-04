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
    auth_version = db.Column(db.Integer, nullable=False, default=1)
    api_token_hash = db.Column(db.String(64), nullable=True)
    monthly_income = db.Column(db.Float, nullable=True, default=0)
    default_page = db.Column(db.String(30), nullable=False, default="dashboard")
    permissions = db.Column(db.Text, nullable=False, default="dashboard,files,storage,media,ai,calendar,budget")
    dock_tabs = db.Column(db.Text, nullable=True)
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

    def get_id(self):
        return f"{self.id}:{self.auth_version}"

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def allowed_pages(self):
        if self.is_admin:
            return ["dashboard", "files", "storage", "media", "ai", "calendar", "budget", "network", "sharing", "users", "settings"]
        return [
            page
            for page in (p.strip() for p in (self.permissions or "").split(","))
            if page and page != "terminal"
        ]

    def has_permission(self, page):
        if self.is_admin:
            return True
        return page in self.allowed_pages

    @property
    def visible_dock_tabs(self):
        """Tabs the user wants in their mobile dock (subset of allowed_pages)."""
        if self.dock_tabs:
            chosen = [t.strip() for t in self.dock_tabs.split(",") if t.strip()]
            return [t for t in chosen if self.has_permission(t)]
        return self.allowed_pages

    def in_dock(self, page):
        """Check if a page should show in this user's mobile dock."""
        return page in self.visible_dock_tabs

    def __repr__(self):
        return f"<User {self.username}>"


class APIToken(db.Model):
    __tablename__ = "api_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False, default="native-app")
    user_agent = db.Column(db.String(255), nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    last_used_at = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True)
    revoked_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship(
        "User",
        backref=db.backref("api_tokens", cascade="all, delete-orphan", lazy="dynamic"),
    )
