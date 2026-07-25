import math
from calendar import monthrange
from datetime import date, timedelta

from flask import jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import extract

from home_os.extensions import db
from home_os.models.bill_payment import BillPayment
from home_os.models.calendar import CalendarEvent
from home_os.models.user import User
from home_os.modules.budget import budget_bp


@budget_bp.route("/budget")
@login_required
def budget_view():
    return render_template("budget/budget.html")


def _month_bounds(year, month):
    last_day = monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _monthly_equivalent(bill):
    amount = bill.amount or 0
    if bill.recurrence == "daily":
        return amount * 365.25 / 12
    if bill.recurrence == "weekly":
        return amount * 52 / 12
    if bill.recurrence == "yearly":
        return amount / 12
    return amount


def _occurrences_in_month(bill, year, month):
    month_start, month_end = _month_bounds(year, month)
    if bill.date > month_end:
        return []

    recurrence = bill.recurrence or "monthly"
    if recurrence == "daily":
        first = max(bill.date, month_start)
        return [first + timedelta(days=offset) for offset in range((month_end - first).days + 1)]

    if recurrence == "weekly":
        first = bill.date
        if first < month_start:
            weeks = max(0, (month_start - first).days // 7)
            first += timedelta(weeks=weeks)
            while first < month_start:
                first += timedelta(days=7)
        occurrences = []
        while first <= month_end:
            occurrences.append(first)
            first += timedelta(days=7)
        return occurrences

    if recurrence == "yearly":
        if bill.date.month != month:
            return []
        day = min(bill.date.day, month_end.day)
        occurrence = date(year, month, day)
        return [occurrence] if occurrence >= bill.date else []

    day = min(bill.date.day, month_end.day)
    occurrence = date(year, month, day)
    return [occurrence] if occurrence >= bill.date else []


def _financial_events_for_month(event_type, year, month):
    return CalendarEvent.query.filter_by(event_type=event_type).filter(
        extract("year", CalendarEvent.date) == year,
        extract("month", CalendarEvent.date) == month,
    ).all()


@budget_bp.route("/api/budget/summary")
@login_required
def budget_summary():
    """Return an accurate household forecast for one calendar month."""
    today = date.today()
    year = request.args.get("year", default=today.year, type=int)
    month = request.args.get("month", default=today.month, type=int)
    if not 1 <= month <= 12 or not 2000 <= year <= 2200:
        return jsonify({"ok": False, "error": "Invalid year or month"}), 400

    _, month_end = _month_bounds(year, month)
    bills = CalendarEvent.query.filter_by(event_type="bill").filter(
        CalendarEvent.date <= month_end
    ).order_by(CalendarEvent.amount.desc(), CalendarEvent.title).all()
    payments = _financial_events_for_month("payment", year, month)
    income_events = _financial_events_for_month("income", year, month)
    users = User.query.filter_by(is_active=True).order_by(User.username).all()

    bill_ids = [bill.id for bill in bills]
    paid_dates = set()
    if bill_ids:
        month_start, month_end = _month_bounds(year, month)
        bill_payments = BillPayment.query.filter(
            BillPayment.event_id.in_(bill_ids),
            BillPayment.period_date >= month_start,
            BillPayment.period_date <= month_end,
        ).all()
        paid_dates = {(payment.event_id, payment.period_date) for payment in bill_payments}

    bill_list = []
    monthly_bills = 0.0
    due_this_month = 0.0
    paid_bills_total = 0.0
    for bill in bills:
        amount = bill.amount or 0
        monthly_equivalent = _monthly_equivalent(bill)
        occurrences = _occurrences_in_month(bill, year, month)
        paid_count = sum((bill.id, occurrence) in paid_dates for occurrence in occurrences)
        expected_count = len(occurrences)
        outstanding_count = max(expected_count - paid_count, 0)
        due_total = amount * expected_count
        outstanding_total = amount * outstanding_count

        bill_list.append({
            "id": bill.id,
            "title": bill.title,
            "amount": amount,
            "currency": bill.currency or "GBP",
            "recurrence": bill.recurrence or "monthly",
            "monthly_equivalent": round(monthly_equivalent, 2),
            "is_paid": expected_count > 0 and paid_count >= expected_count,
            "is_due": expected_count > 0,
            "paid_count": paid_count,
            "expected_count": expected_count,
            "outstanding_total": round(outstanding_total, 2),
            "next_date": occurrences[0].isoformat() if occurrences else None,
            "created_by": bill.creator.username if bill.creator else None,
        })
        monthly_bills += monthly_equivalent
        due_this_month += due_total
        paid_bills_total += amount * paid_count

    payment_list = [{
        "id": event.id,
        "title": event.title,
        "amount": event.amount or 0,
        "currency": event.currency or "GBP",
        "date": event.date.isoformat(),
        "is_paid": event.is_paid,
        "created_by": event.creator.username if event.creator else None,
    } for event in payments]
    income_event_list = [{
        "id": event.id,
        "title": event.title,
        "amount": event.amount or 0,
        "currency": event.currency or "GBP",
        "date": event.date.isoformat(),
        "is_paid": event.is_paid,
        "created_by": event.creator.username if event.creator else None,
    } for event in income_events]

    incomes = [{"username": user.username, "monthly_income": user.monthly_income or 0} for user in users]
    total_monthly_income = sum(item["monthly_income"] for item in incomes)
    total_income_events = sum(item["amount"] for item in income_event_list)
    total_payments = sum(item["amount"] for item in payment_list)
    committed_outgoings = monthly_bills + total_payments
    total_income = total_monthly_income + total_income_events
    monthly_remaining = total_income - committed_outgoings
    savings_rate = monthly_remaining / total_income * 100 if total_income > 0 else 0
    coverage_percent = total_income / committed_outgoings * 100 if committed_outgoings > 0 else 100

    return jsonify({
        "ok": True,
        "data": {
            "year": year,
            "month": month,
            "bills": bill_list,
            "payments": payment_list,
            "income_events": income_event_list,
            "incomes": incomes,
            "monthly_bills": round(monthly_bills, 2),
            "bills_due_this_month": round(due_this_month, 2),
            "paid_bills_total": round(paid_bills_total, 2),
            "weekly_bills": round(monthly_bills * 12 / 52, 2),
            "yearly_bills": round(monthly_bills * 12, 2),
            "upcoming_payments_total": round(sum(item["amount"] for item in payment_list if not item["is_paid"]), 2),
            "upcoming_income_total": round(sum(item["amount"] for item in income_event_list if not item["is_paid"]), 2),
            "total_monthly_income": round(total_monthly_income, 2),
            "total_income_events": round(total_income_events, 2),
            "total_payments": round(total_payments, 2),
            "committed_outgoings": round(committed_outgoings, 2),
            "monthly_remaining": round(monthly_remaining, 2),
            "savings_rate": round(savings_rate, 1),
            "coverage_percent": round(coverage_percent, 1),
        },
    })


@budget_bp.route("/api/budget/yearly")
@login_required
def budget_yearly():
    """Return an optimized month-by-month forecast for a calendar year."""
    year = request.args.get("year", default=date.today().year, type=int)
    if not 2000 <= year <= 2200:
        return jsonify({"ok": False, "error": "Invalid year"}), 400

    users = User.query.filter_by(is_active=True).all()
    total_monthly_income = sum(user.monthly_income or 0 for user in users)
    year_end = date(year, 12, 31)
    bills = CalendarEvent.query.filter_by(event_type="bill").filter(CalendarEvent.date <= year_end).all()
    financial_events = CalendarEvent.query.filter(
        CalendarEvent.event_type.in_(("payment", "income")),
        extract("year", CalendarEvent.date) == year,
    ).all()

    payment_totals = {month: 0.0 for month in range(1, 13)}
    income_totals = {month: 0.0 for month in range(1, 13)}
    for event in financial_events:
        target = payment_totals if event.event_type == "payment" else income_totals
        target[event.date.month] += event.amount or 0

    months = []
    for month in range(1, 13):
        _, month_end = _month_bounds(year, month)
        monthly_bills = sum(_monthly_equivalent(bill) for bill in bills if bill.date <= month_end)
        payment_total = payment_totals[month]
        income_total = income_totals[month]
        net = total_monthly_income + income_total - monthly_bills - payment_total
        months.append({
            "month": month,
            "income": round(total_monthly_income, 2),
            "bills": round(monthly_bills, 2),
            "payments": round(payment_total, 2),
            "income_events": round(income_total, 2),
            "net": round(net, 2),
        })

    yearly_income = total_monthly_income * 12
    yearly_bills = sum(month["bills"] for month in months)
    yearly_payments = sum(payment_totals.values())
    yearly_income_events = sum(income_totals.values())
    yearly_net = yearly_income + yearly_income_events - yearly_bills - yearly_payments

    return jsonify({
        "ok": True,
        "data": {
            "year": year,
            "months": months,
            "yearly_income": round(yearly_income, 2),
            "yearly_bills": round(yearly_bills, 2),
            "yearly_payments": round(yearly_payments, 2),
            "yearly_income_events": round(yearly_income_events, 2),
            "yearly_net": round(yearly_net, 2),
        },
    })


@budget_bp.route("/api/budget/income", methods=["POST"])
@login_required
def set_income():
    """Set a household member's non-negative monthly income."""
    data = request.get_json(silent=True) or {}
    username = data.get("username")
    try:
        income = float(data.get("monthly_income", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Monthly income must be a number"}), 400

    if not math.isfinite(income) or income < 0 or income > 100_000_000:
        return jsonify({"ok": False, "error": "Monthly income is outside the allowed range"}), 400

    if username and username != current_user.username and not current_user.is_admin:
        return jsonify({"ok": False, "error": "Access denied"}), 403

    user = User.query.filter_by(username=username).first() if username else current_user
    if not user or not user.is_active:
        return jsonify({"ok": False, "error": "Active user not found"}), 404

    user.monthly_income = round(income, 2)
    db.session.commit()
    return jsonify({"ok": True, "data": {"username": user.username, "monthly_income": user.monthly_income}})


@budget_bp.route("/api/budget/bills/<int:event_id>/toggle-paid", methods=["POST"])
@login_required
def toggle_monthly_bill_paid(event_id):
    """Toggle every occurrence of one recurring bill in the selected month."""
    data = request.get_json(silent=True) or {}
    try:
        year = int(data.get("year"))
        month = int(data.get("month"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Invalid year or month"}), 400
    if not 1 <= month <= 12 or not 2000 <= year <= 2200:
        return jsonify({"ok": False, "error": "Invalid year or month"}), 400

    bill = CalendarEvent.query.filter_by(id=event_id, event_type="bill").first_or_404()
    occurrences = _occurrences_in_month(bill, year, month)
    if not occurrences:
        return jsonify({"ok": False, "error": "This bill is not due in the selected month"}), 400

    existing = BillPayment.query.filter(
        BillPayment.event_id == bill.id,
        BillPayment.period_date.in_(occurrences),
    ).all()
    existing_by_date = {payment.period_date: payment for payment in existing}
    mark_paid = len(existing_by_date) < len(occurrences)

    if mark_paid:
        for occurrence in occurrences:
            if occurrence not in existing_by_date:
                db.session.add(BillPayment(event_id=bill.id, period_date=occurrence, paid_by=current_user.id))
    else:
        for payment in existing:
            db.session.delete(payment)

    db.session.commit()
    return jsonify({
        "ok": True,
        "data": {
            "event_id": bill.id,
            "is_paid": mark_paid,
            "paid_count": len(occurrences) if mark_paid else 0,
            "expected_count": len(occurrences),
        },
    })
