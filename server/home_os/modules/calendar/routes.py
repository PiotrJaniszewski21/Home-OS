import math
import re
from calendar import monthrange
from datetime import date, timedelta

from flask import abort, jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import and_, or_

from home_os.extensions import db
from home_os.models.bill_payment import BillPayment
from home_os.models.calendar import CalendarEvent
from home_os.modules.calendar import calendar_bp


EVENT_TYPES = {"event", "bill", "payment", "income", "work", "holiday"}
RECURRENCES = {"daily", "weekly", "monthly", "yearly"}
CURRENCIES = {"GBP", "USD", "EUR"}
TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")


def _event_for_write(event_id):
    event = CalendarEvent.query.get_or_404(event_id)
    if event.created_by != current_user.id and not current_user.is_admin:
        abort(403)
    return event


@calendar_bp.route("/calendar")
@login_required
def calendar_view():
    today = date.today()
    year = request.args.get("year", today.year, type=int)
    month = request.args.get("month", today.month, type=int)
    if not 1 <= month <= 12 or not 2000 <= year <= 2200:
        year, month = today.year, today.month
    return render_template("calendar/calendar.html", year=year, month=month, today=today)


def _parse_date(value, field_name, required=False):
    if not value:
        if required:
            raise ValueError(f"{field_name} is required")
        return None
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be a valid date") from None


def _parse_range():
    start = _parse_date(request.args.get("start"), "Start date")
    end = _parse_date(request.args.get("end"), "End date")
    if start and end and end < start:
        raise ValueError("End date must not be before start date")
    if start and end and (end - start).days > 370:
        raise ValueError("Date range is too large")
    return start, end


def _month_occurrence(anchor, year, month):
    return date(year, month, min(anchor.day, monthrange(year, month)[1]))


def _recurring_dates(event, start_date, end_date):
    """Generate stable occurrences without monthly end-of-month drift."""
    if event.date > end_date:
        return []

    recurrence = event.recurrence
    occurrences = []
    if recurrence == "daily":
        current = max(event.date, start_date)
        return [current + timedelta(days=offset) for offset in range((end_date - current).days + 1)]

    if recurrence == "weekly":
        current = event.date
        if current < start_date:
            weeks = max(0, (start_date - current).days // 7)
            current += timedelta(weeks=weeks)
            while current < start_date:
                current += timedelta(weeks=1)
        while current <= end_date:
            occurrences.append(current)
            current += timedelta(weeks=1)
        return occurrences

    if recurrence == "monthly":
        month_index = max(0, (start_date.year - event.date.year) * 12 + start_date.month - event.date.month)
        while True:
            absolute_month = event.date.month - 1 + month_index
            year = event.date.year + absolute_month // 12
            month = absolute_month % 12 + 1
            current = _month_occurrence(event.date, year, month)
            if current > end_date:
                break
            if current >= start_date and current >= event.date:
                occurrences.append(current)
            month_index += 1
        return occurrences

    if recurrence == "yearly":
        for year in range(max(start_date.year, event.date.year), end_date.year + 1):
            current = date(year, event.date.month, min(event.date.day, monthrange(year, event.date.month)[1]))
            if start_date <= current <= end_date and current >= event.date:
                occurrences.append(current)
        return occurrences

    return [event.date] if start_date <= event.date <= end_date else []


def _paid_dates(start_date, end_date):
    payments = BillPayment.query.filter(
        BillPayment.period_date >= start_date,
        BillPayment.period_date <= end_date,
    ).all()
    return {(payment.event_id, payment.period_date) for payment in payments}


def _expanded_event(event, occurrence, paid_dates):
    item = event.to_dict()
    item["series_id"] = event.id
    item["series_date"] = event.date.isoformat()
    item["date"] = occurrence.isoformat()
    item["id"] = f"{event.id}_r_{occurrence.isoformat()}"
    item["is_instance"] = True
    if event.event_type == "bill":
        item["is_paid"] = (event.id, occurrence) in paid_dates
    return item


@calendar_bp.route("/api/calendar/events")
@login_required
def get_events():
    try:
        start_date, end_date = _parse_range()
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400

    query = CalendarEvent.query.filter_by(is_recurring=False)
    if start_date and end_date:
        query = query.filter(or_(
            and_(CalendarEvent.end_date.is_(None), CalendarEvent.date >= start_date, CalendarEvent.date <= end_date),
            and_(CalendarEvent.end_date.isnot(None), CalendarEvent.date <= end_date, CalendarEvent.end_date >= start_date),
        ))
    elif start_date:
        query = query.filter(or_(CalendarEvent.date >= start_date, CalendarEvent.end_date >= start_date))
    elif end_date:
        query = query.filter(CalendarEvent.date <= end_date)

    result = [event.to_dict() for event in query.order_by(CalendarEvent.date, CalendarEvent.time).all()]
    if start_date and end_date:
        paid_dates = _paid_dates(start_date, end_date)
        recurring = CalendarEvent.query.filter_by(is_recurring=True).filter(CalendarEvent.date <= end_date).all()
        for event in recurring:
            for occurrence in _recurring_dates(event, start_date, end_date):
                result.append(_expanded_event(event, occurrence, paid_dates))

    result.sort(key=lambda event: (event["date"], event.get("time") or "", event["title"].casefold()))
    return jsonify({"ok": True, "data": result})


def _validated_event_data(payload, existing=None):
    data = payload or {}
    title = str(data.get("title", existing.title if existing else "")).strip()[:200]
    if not title:
        raise ValueError("Title is required")

    event_type = data.get("event_type", existing.event_type if existing else "event")
    if event_type not in EVENT_TYPES:
        raise ValueError("Invalid event type")

    event_date = _parse_date(data.get("date", existing.date.isoformat() if existing else None), "Date", required=True)
    end_date = _parse_date(data.get("end_date", existing.end_date.isoformat() if existing and existing.end_date else None), "End date")
    if end_date and end_date < event_date:
        raise ValueError("End date must not be before the start date")
    if event_type != "holiday":
        end_date = None

    time = data.get("time", existing.time if existing else None) or None
    if time and not TIME_PATTERN.match(str(time)):
        raise ValueError("Time must use HH:MM format")
    if event_type == "holiday":
        time = None

    description = data.get("description", existing.description if existing else None)
    description = str(description).strip()[:2000] if description else None

    amount = data.get("amount", existing.amount if existing else None)
    if amount in (None, ""):
        amount = None
    else:
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            raise ValueError("Amount must be a number") from None
        if not math.isfinite(amount) or amount < 0 or amount > 99_999_999:
            raise ValueError("Amount is outside the allowed range")
    if event_type in {"bill", "payment", "income"} and amount is None:
        raise ValueError("Amount is required for financial events")
    if event_type not in {"bill", "payment", "income"}:
        amount = None

    currency = str(data.get("currency", existing.currency if existing else "GBP")).upper()
    if currency not in CURRENCIES:
        raise ValueError("Invalid currency")

    hours = data.get("hours", existing.hours if existing else None)
    if hours in (None, ""):
        hours = None
    else:
        try:
            hours = float(hours)
        except (TypeError, ValueError):
            raise ValueError("Hours must be a number") from None
        if not math.isfinite(hours) or hours <= 0 or hours > 24:
            raise ValueError("Hours must be between 0 and 24")
    if event_type == "work" and hours is None:
        raise ValueError("Hours are required for work entries")
    if event_type != "work":
        hours = None

    is_recurring = bool(data.get("is_recurring", existing.is_recurring if existing else False))
    recurrence = data.get("recurrence", existing.recurrence if existing else None)
    if event_type == "bill":
        is_recurring = True
        recurrence = recurrence or "monthly"
    elif event_type in {"payment", "income", "holiday"}:
        is_recurring = False
        recurrence = None
    elif not is_recurring:
        recurrence = None
    if is_recurring and recurrence not in RECURRENCES:
        raise ValueError("Invalid recurrence")

    color = data.get("color", existing.color if existing else None) or None
    if color and not COLOR_PATTERN.match(str(color)):
        raise ValueError("Invalid color")

    return {
        "title": title,
        "description": description,
        "event_type": event_type,
        "date": event_date,
        "end_date": end_date,
        "time": time,
        "all_day": not bool(time),
        "amount": amount,
        "currency": currency,
        "is_recurring": is_recurring,
        "recurrence": recurrence,
        "hours": hours,
        "color": color,
    }


@calendar_bp.route("/api/calendar/events", methods=["POST"])
@login_required
def create_event():
    try:
        values = _validated_event_data(request.get_json(silent=True))
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400

    event = CalendarEvent(**values, is_paid=False, created_by=current_user.id)
    db.session.add(event)
    db.session.commit()
    return jsonify({"ok": True, "data": event.to_dict()}), 201


@calendar_bp.route("/api/calendar/events/<int:event_id>", methods=["PUT"])
@login_required
def update_event(event_id):
    event = _event_for_write(event_id)
    try:
        values = _validated_event_data(request.get_json(silent=True), existing=event)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400

    recurrence_changed = event.date != values["date"] or event.recurrence != values["recurrence"] or not values["is_recurring"]
    for field, value in values.items():
        setattr(event, field, value)
    if recurrence_changed:
        BillPayment.query.filter_by(event_id=event.id).delete()
    db.session.commit()
    return jsonify({"ok": True, "data": event.to_dict()})


@calendar_bp.route("/api/calendar/events/<int:event_id>", methods=["DELETE"])
@login_required
def delete_event(event_id):
    event = _event_for_write(event_id)
    BillPayment.query.filter_by(event_id=event_id).delete()
    db.session.delete(event)
    db.session.commit()
    return jsonify({"ok": True})


@calendar_bp.route("/api/calendar/bills")
@login_required
def get_bills():
    try:
        start_date, end_date = _parse_range()
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400

    bills = CalendarEvent.query.filter_by(event_type="bill").order_by(CalendarEvent.date).all()
    if not start_date or not end_date:
        return jsonify({"ok": True, "data": [bill.to_dict() for bill in bills]})

    paid_dates = _paid_dates(start_date, end_date)
    result = []
    for bill in bills:
        for occurrence in _recurring_dates(bill, start_date, end_date):
            result.append(_expanded_event(bill, occurrence, paid_dates))
    result.sort(key=lambda item: (item["date"], item["title"].casefold()))
    return jsonify({"ok": True, "data": result})


@calendar_bp.route("/api/calendar/events/<event_id>/toggle-paid", methods=["POST"])
@login_required
def toggle_paid(event_id):
    if "_r_" in event_id:
        raw_id, raw_date = event_id.split("_r_", 1)
        try:
            real_id = int(raw_id)
            period_date = date.fromisoformat(raw_date)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Invalid event ID"}), 400
    else:
        try:
            real_id = int(event_id)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Invalid event ID"}), 400
        period_date = None

    event = CalendarEvent.query.get_or_404(real_id)
    if event.event_type != "bill" and event.created_by != current_user.id and not current_user.is_admin:
        abort(403)
    if event.event_type not in {"bill", "payment", "income"}:
        return jsonify({"ok": False, "error": "This event does not have a payment status"}), 400

    if event.is_recurring:
        if period_date is None:
            return jsonify({"ok": False, "error": "A recurring occurrence date is required"}), 400
        if period_date not in _recurring_dates(event, period_date, period_date):
            return jsonify({"ok": False, "error": "Invalid recurring occurrence"}), 400
        existing = BillPayment.query.filter_by(event_id=real_id, period_date=period_date).first()
        if existing:
            db.session.delete(existing)
            is_paid = False
        else:
            db.session.add(BillPayment(event_id=real_id, period_date=period_date, paid_by=current_user.id))
            is_paid = True
        db.session.commit()
        result = _expanded_event(event, period_date, {(real_id, period_date)} if is_paid else set())
        return jsonify({"ok": True, "data": result})

    event.is_paid = not event.is_paid
    db.session.commit()
    return jsonify({"ok": True, "data": event.to_dict()})
