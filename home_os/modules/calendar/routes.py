from datetime import date, datetime, timedelta

from flask import jsonify, render_template, request
from flask_login import current_user, login_required

from home_os.extensions import db
from home_os.models.calendar import CalendarEvent
from home_os.modules.calendar import calendar_bp


@calendar_bp.route("/calendar")
@login_required
def calendar_view():
    today = date.today()
    year = request.args.get("year", today.year, type=int)
    month = request.args.get("month", today.month, type=int)
    return render_template("calendar/calendar.html", year=year, month=month, today=today)


@calendar_bp.route("/api/calendar/events")
@login_required
def get_events():
    start = request.args.get("start")
    end = request.args.get("end")

    query = CalendarEvent.query

    if start:
        query = query.filter(CalendarEvent.date >= start)
    if end:
        query = query.filter(CalendarEvent.date <= end)

    events = query.order_by(CalendarEvent.date).all()

    # Expand recurring events within the date range
    result = []
    for event in events:
        result.append(event.to_dict())
        if event.is_recurring and start and end:
            result.extend(_expand_recurring(event, start, end))

    return jsonify({"ok": True, "data": result})


def _expand_recurring(event, start_str, end_str):
    """Generate recurring instances within a date range."""
    start_date = date.fromisoformat(start_str)
    end_date = date.fromisoformat(end_str)
    instances = []
    current = event.date

    for _ in range(365):  # safety limit
        if event.recurrence == "daily":
            current = current + timedelta(days=1)
        elif event.recurrence == "weekly":
            current = current + timedelta(weeks=1)
        elif event.recurrence == "monthly":
            month = current.month + 1
            year = current.year
            if month > 12:
                month = 1
                year += 1
            try:
                current = current.replace(year=year, month=month)
            except ValueError:
                current = current.replace(year=year, month=month + 1, day=1) - timedelta(days=1)
        elif event.recurrence == "yearly":
            try:
                current = current.replace(year=current.year + 1)
            except ValueError:
                current = current.replace(year=current.year + 1, month=3, day=1) - timedelta(days=1)
        else:
            break

        if current > end_date:
            break
        if current >= start_date:
            instance = event.to_dict()
            instance["date"] = current.isoformat()
            instance["id"] = f"{event.id}_r_{current.isoformat()}"
            instances.append(instance)

    return instances


@calendar_bp.route("/api/calendar/events", methods=["POST"])
@login_required
def create_event():
    data = request.get_json()

    if not data.get("title") or not data.get("date"):
        return jsonify({"ok": False, "error": "Title and date required"}), 400

    # Input validation
    title = data["title"][:200]
    if data.get("description"):
        data["description"] = data["description"][:2000]
    if data.get("amount") is not None:
        try:
            data["amount"] = float(data["amount"])
            if data["amount"] < 0 or data["amount"] > 99999999:
                return jsonify({"ok": False, "error": "Invalid amount"}), 400
        except (ValueError, TypeError):
            return jsonify({"ok": False, "error": "Invalid amount"}), 400
    data["title"] = title

    event_type = data.get("event_type", "event")
    is_recurring = data.get("is_recurring", False)
    recurrence = data.get("recurrence")

    # Bills are always recurring
    if event_type == "bill":
        is_recurring = True
        recurrence = recurrence or "monthly"

    event = CalendarEvent(
        title=data["title"],
        description=data.get("description"),
        event_type=event_type,
        date=date.fromisoformat(data["date"]),
        end_date=date.fromisoformat(data["end_date"]) if data.get("end_date") else None,
        time=data.get("time"),
        all_day=data.get("all_day", True),
        amount=data.get("amount"),
        currency=data.get("currency", "GBP"),
        is_paid=data.get("is_paid", False),
        is_recurring=is_recurring,
        recurrence=recurrence,
        hours=data.get("hours"),
        color=data.get("color"),
        created_by=current_user.id,
    )

    db.session.add(event)
    db.session.commit()
    return jsonify({"ok": True, "data": event.to_dict()})


@calendar_bp.route("/api/calendar/events/<int:event_id>", methods=["PUT"])
@login_required
def update_event(event_id):
    event = CalendarEvent.query.get_or_404(event_id)
    data = request.get_json()

    if "title" in data:
        event.title = data["title"]
    if "description" in data:
        event.description = data["description"]
    if "event_type" in data:
        event.event_type = data["event_type"]
    if "date" in data:
        event.date = date.fromisoformat(data["date"])
    if "end_date" in data:
        event.end_date = date.fromisoformat(data["end_date"]) if data["end_date"] else None
    if "time" in data:
        event.time = data["time"]
    if "all_day" in data:
        event.all_day = data["all_day"]
    if "amount" in data:
        event.amount = data["amount"]
    if "currency" in data:
        event.currency = data["currency"]
    if "is_paid" in data:
        event.is_paid = data["is_paid"]
    if "is_recurring" in data:
        event.is_recurring = data["is_recurring"]
    if "recurrence" in data:
        event.recurrence = data["recurrence"]
    if "hours" in data:
        event.hours = data["hours"]
    if "color" in data:
        event.color = data["color"]

    db.session.commit()
    return jsonify({"ok": True, "data": event.to_dict()})


@calendar_bp.route("/api/calendar/events/<int:event_id>", methods=["DELETE"])
@login_required
def delete_event(event_id):
    event = CalendarEvent.query.get_or_404(event_id)
    db.session.delete(event)
    db.session.commit()
    return jsonify({"ok": True})


@calendar_bp.route("/api/calendar/bills")
@login_required
def get_bills():
    """Get all bills, optionally filtered by paid/unpaid."""
    paid = request.args.get("paid")
    query = CalendarEvent.query.filter_by(event_type="bill")

    if paid == "true":
        query = query.filter_by(is_paid=True)
    elif paid == "false":
        query = query.filter_by(is_paid=False)

    bills = query.order_by(CalendarEvent.date).all()
    return jsonify({"ok": True, "data": [b.to_dict() for b in bills]})


@calendar_bp.route("/api/calendar/events/<int:event_id>/toggle-paid", methods=["POST"])
@login_required
def toggle_paid(event_id):
    event = CalendarEvent.query.get_or_404(event_id)
    event.is_paid = not event.is_paid
    db.session.commit()
    return jsonify({"ok": True, "data": event.to_dict()})
