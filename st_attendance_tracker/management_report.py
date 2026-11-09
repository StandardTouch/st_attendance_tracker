"""Management report: per-employee hours, overtime, late checkouts and login-time trend
for a date range. HR Manager and Management only."""
import frappe
from frappe.utils import add_days, date_diff, getdate, today

from st_attendance_tracker.api import (
    _format_hours, _get_attendance_settings, _parse_time_to_hours, _to_ampm, _to_hhmm,
)
from st_attendance_tracker.time_bank import balances_for, get_ledger
from st_attendance_tracker.time_utils import time_to_minutes

MAX_RANGE_DAYS = 366
REPORT_ROLES = {"HR Manager", "Management"}


def _assert_report_access():
    if not (REPORT_ROLES & set(frappe.get_roles(frappe.session.user))):
        frappe.throw("Access denied. HR Manager or Management role required.", frappe.PermissionError)


def _resolve_range(from_date, to_date):
    """Default: the last 30 days up to today. Reject reversed or oversized ranges."""
    end = getdate(to_date) if to_date else getdate(today())
    start = getdate(from_date) if from_date else add_days(end, -29)
    if start > end:
        frappe.throw("From date must be on or before To date.")
    if date_diff(end, start) + 1 > MAX_RANGE_DAYS:
        frappe.throw(f"Choose a range of at most {MAX_RANGE_DAYS} days.")
    return start, end


def _half_day_pairs(start, end):
    """{(employee, date string)} of days with an approved half-day leave."""
    rows = frappe.get_all(
        "Leave Application",
        filters={"status": "Approved", "docstatus": 1, "half_day": 1, "half_day_date": ["between", [start, end]]},
        fields=["employee", "half_day_date"],
    )
    return {(r.employee, str(r.half_day_date)) for r in rows}


def _ot_minutes(log, minutes, standard_minutes, half_pairs):
    """Task minutes above a full day, or above half a day when a half-day leave or session applies."""
    half = bool(log.get("half_day_session")) or (log.employee, str(log.date)) in half_pairs
    return max(0, minutes - (standard_minutes // 2 if half else standard_minutes))


def _task_stats(log_names):
    """({log: actual minutes}, status counts, {log: [done, total]}) over every task of these logs, one query."""
    counts = {"total": 0, "completed": 0, "pending": 0, "rolled_over": 0, "dropped": 0}
    totals, per_log = {}, {}
    if not log_names:
        return {}, counts, per_log
    for row in frappe.get_all("Task Entry", filters={"parent": ["in", log_names]}, fields=["parent", "actual_time", "status"]):
        totals[row.parent] = totals.get(row.parent, 0) + (row.actual_time or 0)
        counts["total"] += 1
        counts[TASK_BUCKETS.get(row.status, "pending")] += 1
        mine = per_log.setdefault(row.parent, [0, 0])
        mine[0] += 1 if row.status == "Done" else 0
        mine[1] += 1
    return {name: int(round(hours * 60)) for name, hours in totals.items()}, counts, per_log


def _period_summary(start, end, department, standard_minutes):
    """Headline numbers for a period (used for the previous-period comparison)."""
    filters = {"date": ["between", [start, end]], "morning_submitted": 1}
    if department:
        filters["department"] = department
    logs = frappe.get_all("Daily Work Log", filters=filters, fields=["name", "employee", "date", "login_time", "half_day_session"])
    minutes, _counts, _per_log = _task_stats([log.name for log in logs])
    half_pairs = _half_day_pairs(start, end)
    logins = [time_to_minutes(_to_hhmm(log.login_time)) for log in logs if log.login_time]
    return {
        "days_worked": len(logs),
        "ot_minutes": sum(_ot_minutes(log, minutes.get(log.name, 0), standard_minutes, half_pairs) for log in logs),
        "avg_login_minutes": int(sum(logins) / len(logins)) if logins else None,
    }


def _late_checkout_status(log):
    if log.eod_submitted and log.checkout_unlocked:
        return "Approved, checked out"
    if log.checkout_unlocked:
        return "Approved, not checked out yet"
    return "Pending approval"


@frappe.whitelist()
def get_management_report(from_date=None, to_date=None, department=None):
    _assert_report_access()
    start, end = _resolve_range(from_date, to_date)
    standard_minutes = int((_get_attendance_settings().get("standard_workday_hours") or 8) * 60)

    log_filters = {"date": ["between", [start, end]], "morning_submitted": 1}
    if department:
        log_filters["department"] = department
    logs = frappe.get_all(
        "Daily Work Log", filters=log_filters,
        fields=["name", "employee", "employee_name", "department", "date", "login_time", "is_late",
                "eod_submitted", "net_hours", "checkout_unlocked", "half_day_session"],
        order_by="date asc",
    )
    task_minutes, task_status, task_counts = _task_stats([log.name for log in logs])
    half_pairs = _half_day_pairs(start, end)

    employee_filters = {"status": "Active"}
    if department:
        employee_filters["department"] = department
    people = {
        e.name: {
            "employee": e.name, "employee_name": e.employee_name, "department": e.department,
            "days_worked": 0, "days_checked_out": 0, "late_checkins": 0, "late_checkouts": 0,
            "task_minutes": 0, "net_minutes": 0, "ot_minutes": 0, "tasks_done": 0, "tasks_total": 0,
        }
        for e in frappe.get_all("Employee", filters=employee_filters, fields=["name", "employee_name", "department"])
    }

    overtime, login_series, trend_days = [], {}, {}
    for log in logs:
        person = people.setdefault(log.employee, {
            "employee": log.employee, "employee_name": log.employee_name, "department": log.department,
            "days_worked": 0, "days_checked_out": 0, "late_checkins": 0, "late_checkouts": 0,
            "task_minutes": 0, "net_minutes": 0, "ot_minutes": 0, "tasks_done": 0, "tasks_total": 0,
        })
        minutes = task_minutes.get(log.name, 0)
        person["days_worked"] += 1
        person["late_checkins"] += 1 if log.is_late else 0
        person["task_minutes"] += minutes
        done, total = task_counts.get(log.name, (0, 0))
        person["tasks_done"] += done
        person["tasks_total"] += total
        if log.eod_submitted:
            person["days_checked_out"] += 1
            person["net_minutes"] += int(round(_parse_time_to_hours(log.net_hours) * 60))
        ot = _ot_minutes(log, minutes, standard_minutes, half_pairs)
        if ot:
            person["ot_minutes"] += ot
            overtime.append({
                "employee": log.employee, "employee_name": log.employee_name, "department": log.department,
                "date": str(log.date), "task_hours": _format_hours(minutes / 60), "ot_hours": _format_hours(ot / 60),
                "task_minutes": minutes, "ot_minutes": ot,
            })
        if log.login_time:
            by_day = trend_days.setdefault(str(log.date), [])
            by_day.append(time_to_minutes(_to_hhmm(log.login_time)))
            login_series.setdefault(log.employee, []).append({
                "date": str(log.date), "minutes": time_to_minutes(_to_hhmm(log.login_time)),
                "label": _to_ampm(log.login_time), "late": bool(log.is_late),
            })

    late_filters = {"date": ["between", [start, end]]}
    if department:
        late_filters["department"] = department
    late_rows = frappe.get_all(
        "Daily Work Log",
        filters=late_filters,
        or_filters=[["unlock_requested_at", "is", "set"], ["checkout_unlocked", "=", 1]],
        fields=["employee", "employee_name", "department", "date", "eod_submitted", "checkout_unlocked",
                "unlock_requested_at", "unlock_request_reason", "unlocked_by", "unlocked_at", "unlock_reason"],
        order_by="date desc",
    )
    late_checkouts = []
    for row in late_rows:
        if row.employee in people:
            people[row.employee]["late_checkouts"] += 1
        late_checkouts.append({
            "employee": row.employee, "employee_name": row.employee_name, "department": row.department,
            "date": str(row.date), "status": _late_checkout_status(row),
            "requested_at": str(row.unlock_requested_at or ""), "reason": row.unlock_request_reason or "",
            "approved_by": row.unlocked_by or "", "approved_at": str(row.unlocked_at or ""),
            "approval_note": row.unlock_reason or "",
        })

    bank = balances_for(list(people))
    employees = []
    for person in people.values():
        employees.append({
            **person,
            "bank_hours": bank.get(person["employee"], 0.0),
            "bank_label": _format_hours(bank.get(person["employee"], 0.0)) or "0h",
            "task_hours": _format_hours(person["task_minutes"] / 60) or "0m",
            "net_hours": _format_hours(person["net_minutes"] / 60) or "0m",
            "ot_hours": _format_hours(person["ot_minutes"] / 60) or "0m",
        })
    employees.sort(key=lambda p: (p["department"] or "", p["employee_name"] or ""))
    overtime.sort(key=lambda r: (r["date"], r["employee_name"] or ""), reverse=True)

    ot_by_department = {}
    for p in people.values():
        if p["ot_minutes"]:
            key = p["department"] or "No department"
            ot_by_department[key] = ot_by_department.get(key, 0) + p["ot_minutes"]
    all_logins = [m for mins in trend_days.values() for m in mins]
    length = date_diff(end, start) + 1
    previous_end = add_days(start, -1)
    previous = _period_summary(add_days(previous_end, -(length - 1)), previous_end, department, standard_minutes)

    return {
        "from_date": str(start), "to_date": str(end), "department": department or "",
        "standard_workday_minutes": standard_minutes,
        "departments": sorted(d for d in frappe.get_all("Employee", filters={"status": "Active", "department": ["is", "set"]}, pluck="department", distinct=True)),
        "employees": employees,
        "late_checkouts": late_checkouts,
        "overtime": overtime,
        "ot_by_department": [{"department": k, "ot_minutes": v, "ot_hours": _format_hours(v / 60)}
                             for k, v in sorted(ot_by_department.items(), key=lambda kv: kv[1], reverse=True)],
        "trend": [{"date": d, "avg_login_minutes": int(sum(m) / len(m)), "count": len(m)} for d, m in sorted(trend_days.items())],
        "avg_login_minutes": int(sum(all_logins) / len(all_logins)) if all_logins else None,
        "task_status": task_status,
        "previous": {**previous, "from_date": str(add_days(previous_end, -(length - 1))), "to_date": str(previous_end)},
        "login_series": login_series,
    }


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    return int(ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2)


def _day_calendar(employee, start, end, worked):
    """One entry per calendar day of the range: present, late, leave, off (weekly off or holiday),
    missing or future. Days the employee checked in count as present even on a holiday."""
    leave_days = set()
    for row in frappe.get_all(
        "Leave Application",
        filters={"employee": employee, "status": "Approved", "docstatus": 1, "from_date": ["<=", end], "to_date": [">=", start]},
        fields=["from_date", "to_date"],
    ):
        for offset in range(date_diff(row.to_date, row.from_date) + 1):
            leave_days.add(str(add_days(row.from_date, offset)))
    try:
        from hrms.hr.utils import get_holidays_for_employee

        off_days = {str(h.holiday_date) for h in get_holidays_for_employee(employee, start, end, raise_exception=False)}
    except Exception:
        off_days = set()
    today_ = getdate(today())
    days = []
    for offset in range(date_diff(end, start) + 1):
        day = add_days(start, offset)
        key = str(day)
        if key in worked:
            status = "late" if worked[key]["late_checkin"] else "present"
        elif key in leave_days:
            status = "leave"
        elif key in off_days or getdate(day).weekday() == 6:
            status = "off"
        elif getdate(day) > today_:
            status = "future"
        else:
            status = "missing"
        days.append({"date": key, "status": status})
    return days


TASK_BUCKETS = {"Done": "completed", "Pending": "pending", "In Progress": "pending",
                "Dropped": "dropped", "Rolled Over": "rolled_over"}


@frappe.whitelist()
def get_employee_report(employee, from_date=None, to_date=None):
    """One employee's day-by-day check-in/check-out times, late checkouts and every task in the range."""
    _assert_report_access()
    if not employee or not frappe.db.exists("Employee", employee):
        frappe.throw("Employee not found.", frappe.DoesNotExistError)
    start, end = _resolve_range(from_date, to_date)

    logs = frappe.get_all(
        "Daily Work Log",
        filters={"employee": employee, "date": ["between", [start, end]], "morning_submitted": 1},
        fields=["name", "date", "login_time", "logout_time", "is_late", "eod_submitted", "net_hours",
                "checkout_unlocked", "unlock_requested_at", "unlock_request_reason", "unlocked_by", "unlock_reason"],
        order_by="date asc",
    )
    log_by_name = {log.name: log for log in logs}
    task_rows = frappe.get_all(
        "Task Entry", filters={"parent": ["in", list(log_by_name)]} if log_by_name else {"name": ["is", "not set"]},
        fields=["parent", "description", "project_name", "task_type", "status", "estimated_time",
                "actual_time", "remarks", "origin_date", "assigned_by_name", "sequence"],
        order_by="sequence asc",
    ) if log_by_name else []

    counts = {"total": 0, "completed": 0, "pending": 0, "dropped": 0, "rolled_over": 0}
    tasks, per_log = [], {}
    for row in task_rows:
        counts["total"] += 1
        counts[TASK_BUCKETS.get(row.status, "pending")] += 1
        agg = per_log.setdefault(row.parent, {"total": 0, "done": 0, "minutes": 0})
        agg["total"] += 1
        agg["done"] += 1 if row.status == "Done" else 0
        agg["minutes"] += int(round((row.actual_time or 0) * 60))
        tasks.append({
            "date": str(log_by_name[row.parent].date), "project": row.project_name or "",
            "description": row.description or "", "task_type": row.task_type or "", "status": row.status or "",
            "estimated": _format_hours(row.estimated_time) or "-", "actual": _format_hours(row.actual_time) or "-",
            "remarks": row.remarks or "", "carried_from": str(row.origin_date) if row.origin_date and str(row.origin_date) != str(log_by_name[row.parent].date) else "",
            "assigned_by": row.assigned_by_name or "",
        })
    tasks.sort(key=lambda t: t["date"], reverse=True)

    days, late_checkouts = [], []
    for log in logs:
        agg = per_log.get(log.name, {"total": 0, "done": 0, "minutes": 0})
        late_out = bool(log.unlock_requested_at or log.checkout_unlocked)
        login_min = time_to_minutes(_to_hhmm(log.login_time)) if log.login_time else None
        logout_min = time_to_minutes(_to_hhmm(log.logout_time)) if log.eod_submitted and log.logout_time else None
        # A checkout earlier than the check-in belongs to the next calendar day (worked past midnight):
        # plot and average it as 24 hours later so it does not drag the averages across the clock.
        next_day = logout_min is not None and login_min is not None and logout_min < login_min
        if next_day:
            logout_min += 24 * 60
        days.append({
            "date": str(log.date),
            "login_minutes": login_min, "login_label": _to_ampm(log.login_time) if log.login_time else "",
            "logout_minutes": logout_min,
            "logout_label": (_to_ampm(log.logout_time) + (" (next day)" if next_day else "")) if logout_min is not None else "",
            "logout_next_day": next_day,
            "late_checkin": bool(log.is_late), "late_checkout": late_out, "checked_out": bool(log.eod_submitted),
            "task_hours": _format_hours(agg["minutes"] / 60) or "0m", "net_hours": log.net_hours or "",
            "tasks_total": agg["total"], "tasks_done": agg["done"],
        })
        if late_out:
            late_checkouts.append({
                "date": str(log.date), "status": _late_checkout_status(log), "reason": log.unlock_request_reason or "",
                "approved_by": log.unlocked_by or "", "approval_note": log.unlock_reason or "",
            })

    minutes_in = [d["login_minutes"] for d in days if d["login_minutes"] is not None]
    minutes_out = [d["logout_minutes"] for d in days if d["logout_minutes"] is not None]
    calendar = _day_calendar(employee, start, end, {d["date"]: d for d in days})
    name, department = frappe.db.get_value("Employee", employee, ["employee_name", "department"])
    return {
        "employee": employee, "employee_name": name, "department": department,
        "from_date": str(start), "to_date": str(end), "range_days": date_diff(end, start) + 1,
        "days": days, "days_worked": len(days), "late_checkouts": late_checkouts,
        "avg_login_minutes": int(sum(minutes_in) / len(minutes_in)) if minutes_in else None,
        "avg_logout_minutes": int(sum(minutes_out) / len(minutes_out)) if minutes_out else None,
        "median_login_minutes": _median(minutes_in), "median_logout_minutes": _median(minutes_out),
        "calendar": calendar,
        "task_counts": counts, "tasks": tasks,
        "time_bank": {
            "balance": balances_for([employee])[employee],
            "balance_label": _format_hours(balances_for([employee])[employee]) or "0h",
            "entries": get_ledger(employee, start, end),
        },
    }
