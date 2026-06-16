from datetime import date

from flask import jsonify, render_template, request
from flask_login import current_user, login_required

from home_os.extensions import db
from home_os.models.calendar import CalendarEvent
from home_os.models.user import User
from home_os.modules.budget import budget_bp


@budget_bp.route("/budget")
@login_required
def budget_view():
    return render_template("budget/budget.html")


@budget_bp.route("/api/budget/summary")
@login_required
def budget_summary():
    """Calculate totals from bill + payment data, including household income."""
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)

    bills = CalendarEvent.query.filter_by(event_type="bill").all()

    # Filter one-off payments and income by month if specified
    payments_query = CalendarEvent.query.filter_by(event_type="payment")
    income_query = CalendarEvent.query.filter_by(event_type="income")

    if year and month:
        from sqlalchemy import extract
        payments_query = payments_query.filter(
            extract("year", CalendarEvent.date) == year,
            extract("month", CalendarEvent.date) == month,
        )
        income_query = income_query.filter(
            extract("year", CalendarEvent.date) == year,
            extract("month", CalendarEvent.date) == month,
        )

    payments = payments_query.all()
    one_off_income = income_query.all()
    users = User.query.filter_by(is_active=True).all()

    # Calculate monthly bill total
    monthly_bills = 0.0
    bill_list = []

    for bill in bills:
        amount = bill.amount or 0
        if bill.recurrence == "weekly":
            monthly_equiv = amount * 4.33
        elif bill.recurrence == "yearly":
            monthly_equiv = amount / 12
        else:
            monthly_equiv = amount

        bill_list.append({
            "id": bill.id,
            "title": bill.title,
            "amount": amount,
            "currency": bill.currency or "GBP",
            "recurrence": bill.recurrence or "monthly",
            "monthly_equivalent": round(monthly_equiv, 2),
            "is_paid": bill.is_paid,
            "next_date": bill.date.isoformat(),
            "created_by": bill.creator.username if bill.creator else None,
        })
        monthly_bills += monthly_equiv

    # One-off payments (upcoming, not paid)
    today = date.today()
    payment_list = []
    for p in payments:
        payment_list.append({
            "id": p.id,
            "title": p.title,
            "amount": p.amount or 0,
            "currency": p.currency or "GBP",
            "date": p.date.isoformat(),
            "is_paid": p.is_paid,
            "created_by": p.creator.username if p.creator else None,
        })

    upcoming_payments_total = sum(p["amount"] for p in payment_list if not p["is_paid"])

    # One-off income
    income_event_list = []
    for inc in one_off_income:
        income_event_list.append({
            "id": inc.id,
            "title": inc.title,
            "amount": inc.amount or 0,
            "currency": inc.currency or "GBP",
            "date": inc.date.isoformat(),
            "is_paid": inc.is_paid,
            "created_by": inc.creator.username if inc.creator else None,
        })

    upcoming_income_total = sum(i["amount"] for i in income_event_list if not i["is_paid"])

    # Household income
    incomes = []
    total_monthly_income = 0.0
    for user in users:
        income = user.monthly_income or 0
        incomes.append({
            "username": user.username,
            "monthly_income": income,
        })
        total_monthly_income += income

    monthly_remaining = total_monthly_income - monthly_bills

    return jsonify({
        "ok": True,
        "data": {
            "bills": bill_list,
            "payments": payment_list,
            "income_events": income_event_list,
            "incomes": incomes,
            "monthly_bills": round(monthly_bills, 2),
            "weekly_bills": round(monthly_bills / 4.33, 2),
            "yearly_bills": round(monthly_bills * 12, 2),
            "upcoming_payments_total": round(upcoming_payments_total, 2),
            "upcoming_income_total": round(upcoming_income_total, 2),
            "total_monthly_income": round(total_monthly_income, 2),
            "monthly_remaining": round(monthly_remaining, 2),
            "yearly_remaining": round(monthly_remaining * 12, 2),
        }
    })


@budget_bp.route("/api/budget/yearly")
@login_required
def budget_yearly():
    """Year overview — monthly breakdown for a given year."""
    from sqlalchemy import extract

    year = request.args.get("year", type=int) or date.today().year
    users = User.query.filter_by(is_active=True).all()
    total_monthly_income = sum(u.monthly_income or 0 for u in users)

    bills = CalendarEvent.query.filter_by(event_type="bill").all()
    monthly_bills = 0.0
    for bill in bills:
        amount = bill.amount or 0
        if bill.recurrence == "weekly":
            monthly_bills += amount * 4.33
        elif bill.recurrence == "yearly":
            monthly_bills += amount / 12
        else:
            monthly_bills += amount

    months = []
    year_payments_total = 0.0
    year_income_events_total = 0.0

    for m in range(1, 13):
        payments = CalendarEvent.query.filter_by(event_type="payment").filter(
            extract("year", CalendarEvent.date) == year,
            extract("month", CalendarEvent.date) == m,
        ).all()
        payment_total = sum(p.amount or 0 for p in payments)

        income_events = CalendarEvent.query.filter_by(event_type="income").filter(
            extract("year", CalendarEvent.date) == year,
            extract("month", CalendarEvent.date) == m,
        ).all()
        income_total = sum(i.amount or 0 for i in income_events)

        net = total_monthly_income - monthly_bills - payment_total + income_total
        months.append({
            "month": m,
            "income": round(total_monthly_income, 2),
            "bills": round(monthly_bills, 2),
            "payments": round(payment_total, 2),
            "income_events": round(income_total, 2),
            "net": round(net, 2),
        })

        year_payments_total += payment_total
        year_income_events_total += income_total

    yearly_income = total_monthly_income * 12
    yearly_bills = monthly_bills * 12
    yearly_net = yearly_income - yearly_bills - year_payments_total + year_income_events_total

    return jsonify({
        "ok": True,
        "data": {
            "year": year,
            "months": months,
            "yearly_income": round(yearly_income, 2),
            "yearly_bills": round(yearly_bills, 2),
            "yearly_payments": round(year_payments_total, 2),
            "yearly_income_events": round(year_income_events_total, 2),
            "yearly_net": round(yearly_net, 2),
        }
    })


@budget_bp.route("/api/budget/income", methods=["POST"])
@login_required
def set_income():
    """Set monthly income for a user."""
    data = request.get_json()
    username = data.get("username")
    income = data.get("monthly_income", 0)

    if username:
        user = User.query.filter_by(username=username).first()
    else:
        user = current_user

    if not user:
        return jsonify({"ok": False, "error": "User not found"}), 404

    user.monthly_income = float(income)
    db.session.commit()
    return jsonify({"ok": True, "data": {"username": user.username, "monthly_income": user.monthly_income}})
