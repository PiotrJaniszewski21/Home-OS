from datetime import datetime, timezone

from home_os.extensions import db


class CalendarEvent(db.Model):
    __tablename__ = "calendar_events"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    event_type = db.Column(db.String(20), nullable=False)  # bill, payment, income, event, work, holiday
    date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=True)
    time = db.Column(db.String(5), nullable=True)  # HH:MM
    all_day = db.Column(db.Boolean, nullable=False, default=True)

    # Bill-specific
    amount = db.Column(db.Float, nullable=True)
    currency = db.Column(db.String(3), nullable=True, default="GBP")
    is_paid = db.Column(db.Boolean, nullable=False, default=False)

    # Recurrence
    is_recurring = db.Column(db.Boolean, nullable=False, default=False)
    recurrence = db.Column(db.String(20), nullable=True)  # daily, weekly, monthly, yearly

    # Work hours
    hours = db.Column(db.Float, nullable=True)

    # Metadata
    color = db.Column(db.String(7), nullable=True)  # hex color
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    creator = db.relationship("User", backref="calendar_events")

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "event_type": self.event_type,
            "date": self.date.isoformat(),
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "time": self.time,
            "all_day": self.all_day,
            "amount": self.amount,
            "currency": self.currency,
            "is_paid": self.is_paid,
            "is_recurring": self.is_recurring,
            "recurrence": self.recurrence,
            "hours": self.hours,
            "color": self.color,
            "created_by": self.creator.username if self.creator else None,
            "created_at": self.created_at.isoformat(),
        }
