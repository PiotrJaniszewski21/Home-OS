from datetime import datetime, timezone

from home_os.extensions import db


class Share(db.Model):
    __tablename__ = "shares"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    path = db.Column(db.String(512), nullable=False)
    read_only = db.Column(db.Boolean, nullable=False, default=False)
    guest_access = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "read_only": self.read_only,
            "guest_access": self.guest_access,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat(),
        }
