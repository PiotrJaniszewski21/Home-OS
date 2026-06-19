from datetime import datetime, timezone

from home_os.extensions import db


class BillPayment(db.Model):
    """Tracks per-instance payment status for recurring bills."""

    __tablename__ = "bill_payments"

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("calendar_events.id", ondelete="CASCADE"), nullable=False)
    period_date = db.Column(db.Date, nullable=False)
    paid_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    paid_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    event = db.relationship("CalendarEvent", backref="payments")
    user = db.relationship("User")

    __table_args__ = (
        db.UniqueConstraint("event_id", "period_date", name="uq_bill_period"),
    )
