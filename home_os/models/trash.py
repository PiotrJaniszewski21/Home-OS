from datetime import datetime, timezone

from home_os.extensions import db


class TrashEntry(db.Model):
    __tablename__ = "trash"

    id = db.Column(db.Integer, primary_key=True)
    original_path = db.Column(db.String(1024), nullable=False)
    trash_path = db.Column(db.String(1024), nullable=False)
    size_bytes = db.Column(db.BigInteger, nullable=False, default=0)
    restored = db.Column(db.Boolean, nullable=False, default=False)
    deleted_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    expires_at = db.Column(db.DateTime, nullable=False)
