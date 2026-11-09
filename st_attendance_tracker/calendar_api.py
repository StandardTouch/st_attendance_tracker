"""Calendar view of an employee's own work: tasks per day, scheduling, attendance.

Thin whitelisted layer for the Daily Check-in calendar. Check-in, status
changes (autosave_eod_progress), ad-hoc tasks, checkout and move-to-backlog
keep using the existing endpoints in api.py; this module only adds the range
read and the schedule write the calendar needs.
"""
import json
import re

import frappe
from frappe.utils import add_days, getdate, today

from st_attendance_tracker.api import (
    _format_hours,
    _get_employee,
    _get_employees_on_leave,
    _assign_task,
    _get_team_members,
    _get_work_log,
    _save_work_log,
    _to_hhmm,
    move_task_to_backlog,
)

MAX_RANGE_DAYS = 62
MAX_TEAM_RANGE_DAYS = 14
EXTRA_FIELDS = ["name", "employee", "work_date", "login_time", "logout_time", "project_name", "hours_spent", "status", "description"]
HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _validated_range(from_date, to_date, max_days=MAX_RANGE_DAYS):
    start, end = getdate(from_date), getdate(to_date)
    if end < start:
        frappe.throw("End date must not be before start date.", frappe.ValidationError)
    if (end - start).days > max_days:
        frappe.throw(f"Date range is limited to {max_days} days.", frappe.ValidationError)
    return str(start), str(end)


@frappe.whitelist()
def get_calendar_range(from_date, to_date):
    """Own Daily Work Logs, task rows and Additional Work entries between two dates."""
    employee = _get_employee()
    start, end = _validated_range(from_date, to_date)

    logs = frappe.get_all(
        "Daily Work Log",
        filters={"employee": employee.name, "date": ["between", [start, end]]},
        fields=[
            "name", "date", "login_time", "logout_time", "lunch_from", "lunch_to",
            "net_hours", "work_location", "half_day_session", "is_late",
            "morning_submitted", "eod_submitted",
        ],
        order_by="date asc",
    )
    date_by_log = {log.name: str(log.date) for log in logs}
    for log in logs:
        log["date"] = str(log.date)
        for key in ("login_time", "logout_time", "lunch_from", "lunch_to"):
            log[key] = _to_hhmm(log.get(key))

    tasks = []
    if logs:
        rows = frappe.get_all(
            "Task Entry",
            filters={"parent": ["in", list(date_by_log)], "parenttype": "Daily Work Log"},
            fields=[
                "name", "parent", "description", "status", "task_type", "project_name",
                "remarks", "estimated_time", "actual_time", "start_time", "sequence",
                "origin_date", "series_id", "repeat_next_day",
            ],
            order_by="parent asc, sequence asc, idx asc",
        )
        for row in rows:
            row["date"] = date_by_log[row.pop("parent")]
            row["start_time"] = _to_hhmm(row.get("start_time")) or None
            row["is_carried"] = bool(row.get("origin_date")) and str(row["origin_date"]) != row["date"]
            row["origin_date"] = str(row["origin_date"]) if row.get("origin_date") else None
            tasks.append(row)

    additional_work = frappe.get_all(
        "Additional Work",
        filters={"employee": employee.name, "work_date": ["between", [start, end]]},
        fields=[
            "name", "work_date", "login_time", "logout_time", "project_name",
            "hours_spent", "status", "description", "remarks",
        ],
        order_by="work_date asc, login_time asc",
    )
    for entry in additional_work:
        entry["work_date"] = str(entry.work_date)
        entry["login_time"] = _to_hhmm(entry.get("login_time")) or None
        entry["logout_time"] = _to_hhmm(entry.get("logout_time")) or None

    scheduled_backlog = frappe.get_all(
        "Task Backlog Item",
        filters={"employee": employee.name, "scheduled_for": ["between", [start, end]]},
        fields=["name", "description", "project_name", "estimated_time", "scheduled_for", "start_time"],
        order_by="scheduled_for asc, start_time asc",
    )
    for item in scheduled_backlog:
        item["scheduled_for"] = str(item.scheduled_for)
        item["start_time"] = _to_hhmm(item.get("start_time")) or None

    return {
        "today": today(),
        "from_date": start,
        "to_date": end,
        "logs": logs,
        "tasks": tasks,
        "additional_work": additional_work,
        "scheduled_backlog": scheduled_backlog,
    }


@frappe.whitelist(methods=["POST"])
def set_task_schedule(name, start_time=None, estimated_hours=None):
    """Move (start_time 'HH:MM') and/or resize (estimated_hours) one of my tasks.

    Same guards as the other task mutations: only the task's own employee, and
    nothing changes after that day's checkout is submitted.
    """
    employee = _get_employee()
    parent = frappe.db.get_value("Task Entry", name, "parent")
    if not parent:
        frappe.throw("Not authorised to edit this task.", frappe.PermissionError)

    work_log = frappe.get_doc("Daily Work Log", parent)
    if work_log.employee != employee.name:
        frappe.throw("Not authorised to edit this task.", frappe.PermissionError)
    if work_log.eod_submitted:
        frappe.throw("Cannot change tasks after checkout is submitted.", frappe.ValidationError)

    row = next((r for r in work_log.tasks if r.name == name), None)
    if not row:
        frappe.throw("Not authorised to edit this task.", frappe.PermissionError)

    if start_time:
        match = HHMM.match(str(start_time).strip())
        if not match:
            frappe.throw("Start time must be HH:MM.", frappe.ValidationError)
        row.start_time = f"{int(match.group(1)):02d}:{match.group(2)}:00"

    if estimated_hours not in (None, ""):
        try:
            hours = float(estimated_hours)
        except (TypeError, ValueError):
            frappe.throw("Estimated hours must be a number.", frappe.ValidationError)
        if not 0.25 <= hours <= 24:
            frappe.throw("Estimated hours must be between 0.25 and 24.", frappe.ValidationError)
        # Text, not a float: Daily Work Log parses estimated_time once on save and
        # treats a bare number as minutes.
        row.estimated_time = _format_hours(hours)

    _save_work_log(work_log)
    frappe.db.commit()

    saved = next(r for r in work_log.tasks if r.name == name)
    return {
        "name": saved.name,
        "start_time": _to_hhmm(saved.start_time) or None,
        "estimated_time": saved.estimated_time,
    }


# ── Team Leader and HR / Management views ──────────────────────────────────────

def _extra_work_by_day(names, start, end):
    """{(employee, date): [entries]} of Additional Work for these employees. Callers authorise first."""
    by_key = {}
    for entry in frappe.get_all(
        "Additional Work",
        filters={"employee": ["in", names], "work_date": ["between", [start, end]]},
        fields=EXTRA_FIELDS,
        order_by="work_date asc, login_time asc",
    ):
        entry["work_date"] = str(entry.work_date)
        entry["login_time"] = _to_hhmm(entry.get("login_time")) or None
        entry["logout_time"] = _to_hhmm(entry.get("logout_time")) or None
        by_key.setdefault((entry.employee, entry.work_date), []).append(entry)
    return by_key


def _day_status(log, on_leave):
    """Same precedence as api._build_team_data: real attendance beats a leave record."""
    if log and log.eod_submitted:
        return "eod_done"
    if log and log.morning_submitted:
        return "late" if log.is_late else "checked_in"
    return "leave" if on_leave else "missing"


def _employees_week(employees, start, end):
    """Per employee, per date: attendance status, times and task rows. Callers authorise first."""
    names = [e.name for e in employees]
    if not names:
        return []

    logs = frappe.get_all(
        "Daily Work Log",
        filters={"employee": ["in", names], "date": ["between", [start, end]]},
        fields=["name", "employee", "date", "login_time", "logout_time", "net_hours",
                "is_late", "morning_submitted", "eod_submitted"],
    )
    log_by_key = {(log.employee, str(log.date)): log for log in logs}
    tasks_by_log = {}
    if logs:
        for row in frappe.get_all(
            "Task Entry",
            filters={"parent": ["in", [log.name for log in logs]], "parenttype": "Daily Work Log"},
            fields=["name", "parent", "description", "status", "project_name", "task_type",
                    "estimated_time", "actual_time"],
            order_by="parent asc, sequence asc, idx asc",
        ):
            tasks_by_log.setdefault(row.pop("parent"), []).append(row)

    extra_by_key = _extra_work_by_day(names, start, end)

    dates = []
    day = getdate(start)
    while day <= getdate(end):
        dates.append(str(day))
        day = add_days(day, 1)
    today_str = today()
    leave_by_date = {d: set(_get_employees_on_leave(names, d)) for d in dates if d <= today_str}

    result = []
    for emp in employees:
        days = {}
        for date in dates:
            if date > today_str:
                days[date] = {"status": None, "tasks": [], "extra_hours": 0, "extra_work": []}
                continue
            log = log_by_key.get((emp.name, date))
            extra = extra_by_key.get((emp.name, date), [])
            days[date] = {
                "extra_hours": round(sum(e.hours_spent or 0 for e in extra), 2),
                "extra_work": extra,
                "status": _day_status(log, emp.name in leave_by_date[date]),
                "login_time": _to_hhmm(log.login_time) if log and log.morning_submitted else "",
                "logout_time": _to_hhmm(log.logout_time) if log and log.eod_submitted else "",
                "net_hours": (log.net_hours or "") if log and log.eod_submitted else "",
                "is_late": bool(log and log.is_late and log.morning_submitted),
                "tasks": tasks_by_log.get(log.name, []) if log else [],
            }
        result.append({
            "name": emp.name,
            "employee_name": emp.employee_name,
            "department": emp.department,
            "designation": emp.get("designation") or "",
            "days": days,
        })
    return result


@frappe.whitelist()
def get_team_week(from_date, to_date):
    """Direct reports (same rule as get_team_dashboard) with a week of attendance and tasks."""
    employee = _get_employee()
    team_names = _get_team_members(employee.name)
    if not team_names:
        frappe.throw("Access denied. You are not a Team Leader.", frappe.PermissionError)
    start, end = _validated_range(from_date, to_date, MAX_TEAM_RANGE_DAYS)
    members = frappe.get_all(
        "Employee",
        filters={"name": ["in", team_names]},
        fields=["name", "employee_name", "department", "designation"],
        order_by="employee_name asc",
    )
    return {"today": today(), "from_date": start, "to_date": end, "employees": _employees_week(members, start, end)}


@frappe.whitelist()
def get_company_week(from_date, to_date):
    """Daily attendance counts for the whole company. HR Manager or Management only."""
    if not ({"HR Manager", "Management"} & set(frappe.get_roles(frappe.session.user))):
        frappe.throw("Access denied. HR Manager or Management role required.", frappe.PermissionError)
    start, end = _validated_range(from_date, to_date, MAX_TEAM_RANGE_DAYS)

    total = frappe.db.count("Employee", {"status": "Active"})
    rows = frappe.get_all(
        "Daily Work Log",
        filters={"date": ["between", [start, end]], "morning_submitted": 1},
        fields=["date", "count(name) as present", "sum(is_late) as late", "sum(eod_submitted) as eod_done"],
        group_by="date",
    )
    by_date = {str(r.date): r for r in rows}
    days = []
    day = getdate(start)
    while day <= getdate(end):
        key = str(day)
        row = by_date.get(key)
        days.append({
            "date": key,
            "present": int(row.present) if row else 0,
            "late": int(row.late or 0) if row else 0,
            "eod_done": int(row.eod_done or 0) if row else 0,
        })
        day = add_days(day, 1)
    return {"today": today(), "total": total, "days": days}


@frappe.whitelist()
def get_day_context(date):
    """My check-in window and Additional Work entries for one date, for the Additional Work form.

    Extra work is stored on its own (employee + work_date) and matched to the day's Daily Work Log by
    that pair, so it also works for a day with no check-in.
    """
    employee = _get_employee()
    day = str(getdate(date))
    log = frappe.db.get_value(
        "Daily Work Log",
        {"employee": employee.name, "date": day},
        ["login_time", "logout_time", "lunch_from", "lunch_to", "net_hours", "morning_submitted", "eod_submitted"],
        as_dict=True,
    )
    if log:
        for key in ("login_time", "logout_time", "lunch_from", "lunch_to"):
            log[key] = _to_hhmm(log.get(key)) or None
    entries = _extra_work_by_day([employee.name], day, day).get((employee.name, day), [])
    from st_attendance_tracker.st_attendance_tracker.doctype.additional_work.additional_work import (
        BLOCKED_MESSAGE, additional_work_allowed,
    )

    can_log, reason = additional_work_allowed(employee.name, day)
    return {"date": day, "today": today(), "log": log, "entries": entries,
            "can_log": can_log, "can_log_reason": reason, "blocked_message": "" if can_log else BLOCKED_MESSAGE}


@frappe.whitelist()
def get_company_extra_hours(date):
    """{employee: extra hours} for one date. HR Manager or Management only."""
    if not ({"HR Manager", "Management"} & set(frappe.get_roles(frappe.session.user))):
        frappe.throw("Access denied. HR Manager or Management role required.", frappe.PermissionError)
    day = str(getdate(date))
    rows = frappe.get_all(
        "Additional Work",
        filters={"work_date": day},
        fields=["employee", "sum(hours_spent) as hours"],
        group_by="employee",
    )
    return {r.employee: round(r.hours or 0, 2) for r in rows}


MAX_BULK = 100


@frappe.whitelist(methods=["POST"])
def bulk_move_to_backlog(names):
    """Move several of my tasks to the backlog. Each one goes through move_task_to_backlog, so the same
    rules apply (own task, checkout not submitted, not Recurring, not Done); a task that breaks a rule is
    skipped with its reason and the rest still move."""
    _get_employee()
    names = json.loads(names) if isinstance(names, str) else (names or [])
    if not isinstance(names, list) or not names:
        frappe.throw("Choose at least one task.", frappe.ValidationError)
    if len(names) > MAX_BULK:
        frappe.throw(f"Move at most {MAX_BULK} tasks at a time.", frappe.ValidationError)

    moved, skipped = [], []
    for name in dict.fromkeys(str(n) for n in names):
        try:
            move_task_to_backlog(name)
            moved.append(name)
        except (frappe.ValidationError, frappe.PermissionError) as exc:
            frappe.db.rollback()
            skipped.append({"name": name, "reason": str(exc).strip() or "Not allowed."})
    return {"moved": moved, "skipped": skipped}


@frappe.whitelist(methods=["POST"])
def set_task_repeat(name, repeat):
    """Turn "Repeat next day" on or off for one of my ordinary tasks (before checkout)."""
    from st_attendance_tracker.task_repeat import set_task_repeat as _set_task_repeat

    employee = _get_employee()
    on = str(repeat).strip().lower() in ("1", "true", "yes")
    result = _set_task_repeat(employee.name, name, on)
    frappe.db.commit()
    return result


@frappe.whitelist(methods=["POST"])
def update_task(name, description=None, project_name=None, remarks=None, estimated_hours=None, start_time=None):
    """Edit one of my tasks before checkout. Only the fields passed are changed.

    A task made by a Recurring Task Template keeps its description and project, because the template
    re-syncs them; change those on the Recurring Tasks page.
    """
    employee = _get_employee()
    parent = frappe.db.get_value("Task Entry", name, "parent")
    if not parent:
        frappe.throw("Not authorised to edit this task.", frappe.PermissionError)

    work_log = frappe.get_doc("Daily Work Log", parent)
    if work_log.employee != employee.name:
        frappe.throw("Not authorised to edit this task.", frappe.PermissionError)
    if work_log.eod_submitted:
        frappe.throw("Cannot change tasks after checkout is submitted.", frappe.ValidationError)
    row = next((r for r in work_log.tasks if r.name == name), None)
    if not row:
        frappe.throw("Not authorised to edit this task.", frappe.PermissionError)

    if description is not None:
        description = description.strip()
        if not description:
            frappe.throw("Task description cannot be empty.", frappe.ValidationError)
    if row.task_type == "Recurring":
        changes_text = (description is not None and description != (row.description or "")) or (
            project_name is not None and project_name.strip() != (row.project_name or ""))
        if changes_text:
            frappe.throw(
                "This task comes from a recurring template. Change its description or project on the Recurring Tasks page.",
                frappe.ValidationError,
            )

    if description is not None:
        row.description = description
    if project_name is not None:
        row.project_name = project_name.strip()
    if remarks is not None:
        row.remarks = remarks.strip()
    if start_time:
        match = HHMM.match(str(start_time).strip())
        if not match:
            frappe.throw("Start time must be HH:MM.", frappe.ValidationError)
        row.start_time = f"{int(match.group(1)):02d}:{match.group(2)}:00"
    if estimated_hours not in (None, ""):
        try:
            hours = float(estimated_hours)
        except (TypeError, ValueError):
            frappe.throw("Estimated hours must be a number.", frappe.ValidationError)
        if not 0.25 <= hours <= 24:
            frappe.throw("Estimated hours must be between 0.25 and 24.", frappe.ValidationError)
        row.estimated_time = _format_hours(hours)

    _save_work_log(work_log)
    frappe.db.commit()
    saved = next(r for r in work_log.tasks if r.name == name)
    return {
        "name": saved.name, "description": saved.description, "project_name": saved.project_name,
        "remarks": saved.remarks, "estimated_time": saved.estimated_time, "start_time": _to_hhmm(saved.start_time) or None,
    }


@frappe.whitelist(methods=["POST"])
def schedule_backlog_item(name, date=None, start_time=None):
    """Give one of my backlog items a day (and optionally a time), or clear it with no date.

    On that day it is added to the check-in plan automatically (see api._pull_scheduled_backlog).
    A task cannot be scheduled for a day that has already passed.
    """
    employee = _get_employee()
    item = frappe.db.get_value("Task Backlog Item", name, ["employee", "name"], as_dict=True)
    if not item or item.employee != employee.name:
        frappe.throw("Not authorised to edit this task.", frappe.PermissionError)

    doc = frappe.get_doc("Task Backlog Item", name)
    if not date:
        doc.scheduled_for = None
        doc.start_time = None
    else:
        day = getdate(date)
        if day < getdate(today()):
            frappe.throw("A task cannot be scheduled for a day that has passed.", frappe.ValidationError)
        doc.scheduled_for = day
        if start_time:
            match = HHMM.match(str(start_time).strip())
            if not match:
                frappe.throw("Start time must be HH:MM.", frappe.ValidationError)
            doc.start_time = f"{int(match.group(1)):02d}:{match.group(2)}:00"
        else:
            doc.start_time = None
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"name": doc.name, "scheduled_for": str(doc.scheduled_for) if doc.scheduled_for else None,
            "start_time": _to_hhmm(doc.start_time) or None}


@frappe.whitelist(methods=["POST"])
def reassign_task(name, to_employee):
    """A Team Leader hands one of a direct report's untouched tasks for today to another direct report.

    Only today's Pending, non-recurring tasks without attachments move, and only while neither person
    has checked out. The task is created on the other person's day first and removed from the first
    person's day second, so a failure in between never loses it.
    """
    leader = _get_employee()
    team = _get_team_members(leader.name)
    parent = frappe.db.get_value("Task Entry", name, "parent")
    if not parent:
        frappe.throw("Task not found.", frappe.PermissionError)
    source = frappe.get_doc("Daily Work Log", parent)
    if source.employee not in team or to_employee not in team:
        frappe.throw("You can only reassign tasks between people on your team.", frappe.PermissionError)
    if source.employee == to_employee:
        frappe.throw("Choose a different person.", frappe.ValidationError)
    if str(source.date) != today():
        frappe.throw("Only today's tasks can be reassigned.", frappe.ValidationError)
    if source.eod_submitted:
        frappe.throw("That person has already checked out, so their tasks are locked.", frappe.ValidationError)
    destination = _get_work_log(to_employee, today())
    if destination and destination.eod_submitted:
        frappe.throw("The other person has already checked out today.", frappe.ValidationError)

    row = next((r for r in source.tasks if r.name == name), None)
    if not row:
        frappe.throw("Task not found.", frappe.PermissionError)
    if row.task_type == "Recurring":
        frappe.throw("Recurring tasks come from the person's own template and cannot be reassigned.", frappe.ValidationError)
    if row.status != "Pending":
        frappe.throw("Only a task that has not been started can be reassigned.", frappe.ValidationError)
    if frappe.db.exists("File", {"attached_to_doctype": "Task Entry", "attached_to_name": name}):
        frappe.throw("This task has attachments, so it cannot be reassigned.", frappe.ValidationError)

    _log, new_row = _assign_task(to_employee, leader.name, row.description, row.project_name, _format_hours(row.estimated_time))

    # Same clean-up as deleting a carried task: stop rollover from bringing the old copy back.
    frappe.db.sql(
        "UPDATE `tabTask Entry` SET status = 'Rolled Over' WHERE series_id = %s AND status IN ('Pending', 'In Progress') AND name != %s",
        (row.series_id, new_row.name),
    )
    source.tasks.remove(row)
    frappe.flags.in_task_assignment = True
    try:
        source.save(ignore_permissions=True)
    finally:
        frappe.flags.in_task_assignment = False
    frappe.db.commit()
    return {"task": new_row.name, "from_employee": source.employee, "to_employee": to_employee}
