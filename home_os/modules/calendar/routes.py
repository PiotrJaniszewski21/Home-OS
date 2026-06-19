from datetime import date, datetime, timedelta

from flask import jsonify, render_template, request
from flask_login import current_user, login_required

from home_os.extensions import db
from home_os.models.bill_payment import BillPayment
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

    # Non-recurring events filtered by date range
    # Include events that overlap the range (for multi-day events like holidays)
    from sqlalchemy import or_, and_
    query = CalendarEvent.query.filter_by(is_recurring=False)
    if start and end:
        query = query.filter(or_(
            # Single-day events within range
            and_(CalendarEvent.end_date.is_(None), CalendarEvent.date >= start, CalendarEvent.date <= end),
            # Multi-day events that overlap the range (starts before range ends AND ends after range starts)
            and_(CalendarEvent.end_date.isnot(None), CalendarEvent.date <= end, CalendarEvent.end_date >= start),
        ))
    elif start:
        query = query.filter(or_(CalendarEvent.date >= start, CalendarEvent.end_date >= start))
    elif end:
        query = query.filter(CalendarEvent.date <= end)

    result = [e.to_dict() for e in query.order_by(CalendarEvent.date).all()]

    # Recurring events: fetch all and expand instances within range
    if start and end:
        recurring = CalendarEvent.query.filter_by(is_recurring=True).all()
        paid_set = _get_paid_set(start, end)
        for event in recurring:
            result.extend(_expand_recurring(event, start, end, paid_set))

    return jsonify({"ok": True, "data": result})


def _get_paid_set(start_str, end_str):
    """Get set of (event_id, period_date) that are paid in this range."""
    payments = BillPayment.query.filter(
        BillPayment.period_date >= start_str,
        BillPayment.period_date <= end_str,
    ).all()
    return {(p.event_id, p.period_date) for p in payments}


def _expand_recurring(event, start_str, end_str, paid_set=None):
    """Generate all recurring instances that fall within a date range."""
    start_date = date.fromisoformat(start_str)
    end_date = date.fromisoformat(end_str)
    instances = []
    current = event.date

    # Include the original occurrence if it falls within range
    if start_date <= current <= end_date:
        instance = event.to_dict()
        if paid_set is not None:
            instance["is_paid"] = (event.id, current) in paid_set
        instances.append(instance)

    max_iterations = (end_date - start_date).days + (start_date - event.date).days + 1
    if event.recurrence == "weekly":
        max_iterations = max_iterations // 7 + 2
    elif event.recurrence == "monthly":
        max_iterations = max_iterations // 28 + 2
    elif event.recurrence == "yearly":
        max_iterations = max_iterations // 365 + 2
    max_iterations = min(max_iterations, 3650)

    for _ in range(max_iterations):
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
            if paid_set is not None:
                instance["is_paid"] = (event.id, current) in paid_set
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
    BillPayment.query.filter_by(event_id=event_id).delete()
    db.session.delete(event)
    db.session.commit()
    return jsonify({"ok": True})


@calendar_bp.route("/api/calendar/bills")
@login_required
def get_bills():
    """Get bills, expanded for a date range if provided."""
    start = request.args.get("start")
    end = request.args.get("end")
    query = CalendarEvent.query.filter_by(event_type="bill")
    bills = query.order_by(CalendarEvent.date).all()

    if start and end:
        paid_set = _get_paid_set(start, end)
        result = []
        for bill in bills:
            if bill.is_recurring:
                result.extend(_expand_recurring(bill, start, end, paid_set))
            else:
                bill_date = bill.date
                if date.fromisoformat(start) <= bill_date <= date.fromisoformat(end):
                    result.append(bill.to_dict())
        return jsonify({"ok": True, "data": result})

    return jsonify({"ok": True, "data": [b.to_dict() for b in bills]})


@calendar_bp.route("/api/calendar/events/<event_id>/toggle-paid", methods=["POST"])
@login_required
def toggle_paid(event_id):
    """Toggle paid status. For recurring bills, uses BillPayment table per-instance."""
    data = request.get_json(silent=True) or {}

    # Check if this is a recurring instance (ID like "5_r_2026-07-15")
    if isinstance(event_id, str) and "_r_" in event_id:
        parts = event_id.split("_r_", 1)
        try:
            real_id = int(parts[0])
            period_date = date.fromisoformat(parts[1])
        except (ValueError, TypeError, IndexError):
            return jsonify({"ok": False, "error": "Invalid event ID"}), 400
        event = CalendarEvent.query.get_or_404(real_id)
    else:
        try:
            real_id = int(event_id)
        except (ValueError, TypeError):
            return jsonify({"ok": False, "error": "Invalid event ID"}), 400
        event = CalendarEvent.query.get_or_404(real_id)
        # For recurring bills called from budget view, derive period from year/month
        if event.is_recurring and data.get("year") and data.get("month"):
            from calendar import monthrange
            try:
                y, m = int(data["year"]), int(data["month"])
            except (ValueError, TypeError):
                return jsonify({"ok": False, "error": "Invalid year/month"}), 400
            if not (1 <= m <= 12):
                return jsonify({"ok": False, "error": "Invalid month"}), 400
            _, last_day = monthrange(y, m)
            day = min(event.date.day, last_day)
            period_date = date(y, m, day)
        else:
            period_date = event.date

    # For recurring events, use per-instance tracking
    if event.is_recurring:
        existing = BillPayment.query.filter_by(event_id=real_id, period_date=period_date).first()
        if existing:
            db.session.delete(existing)
            is_paid = False
        else:
            payment = BillPayment(
                event_id=real_id,
                period_date=period_date,
                paid_by=current_user.id,
            )
            db.session.add(payment)
            is_paid = True
        db.session.commit()
        result = event.to_dict()
        result["is_paid"] = is_paid
        result["date"] = period_date.isoformat()
        return jsonify({"ok": True, "data": result})

    # For non-recurring events, toggle the boolean directly
    event.is_paid = not event.is_paid
    db.session.commit()
    return jsonify({"ok": True, "data": event.to_dict()})
