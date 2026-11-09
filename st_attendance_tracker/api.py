"""
ST Attendance Tracker v2 — API
All whitelisted methods consumed by web pages.

Fixes:
  - Notifications sent to HR Manager role users + Team Leader(s)
  - No manual recipient configuration needed
  - Team Leader(s) resolved via Employee Department Assignment child table,
    falling back to reports_to for employees with no assignment rows
  - Root task guard prevents duplicate rollover
  - Safety rollover on morning page load
  - Checkin auto-heal if HR deletes record
  - Lunch from/to replacing single lunch_time
  - Login time editable by employee
  - Working hours auto-calculated
  - Carried tasks fully editable before check-in
  - Submit allowed with only carried tasks
  - [FIX] _to_hhmm() normalises timedelta/string before get_datetime() call —
    str(timedelta(seconds=34200)) = '9:30:00' and '9:30:00'[:5] = '9:30:'
    (trailing colon) which dateutil cannot parse, causing net_hours = ''.
  - [FIX] lunch_from/lunch_to display in right panel after EOD submit
    (moved from JS-only to server-rendered in get_context / template)
"""

import frappe
import datetime
import html
import os
import re
from html import escape as html_escape
import json
from frappe.utils import today, now_datetime, getdate, add_days, cint, formatdate
from frappe.desk.form.load import get_attachments
from frappe.rate_limiter import rate_limit
from frappe.utils.user import get_users_with_role
from st_attendance_tracker.time_utils import is_checkout_locked, parse_duration_to_hours, time_to_minutes


# ── Employee helper ────────────────────────────────────────────────────────────

def _get_employee():
    emp = frappe.db.get_value(
        "Employee",
        {"user_id": frappe.session.user, "status": "Active"},
        ["name", "employee_name", "department", "reports_to", "designation"],
        as_dict=True,
    )
    if not emp:
        frappe.throw(
            "No Employee record is linked to your user account. "
            "Please contact HR.",
            frappe.PermissionError,
        )
    return emp





def _task_owner_employee(task_name):
    """Resolve a Task Entry child row's owning employee via its parent
    Daily Work Log — child rows don't carry `employee` directly."""
    parent = frappe.db.get_value("Task Entry", task_name, "parent")
    return frappe.db.get_value("Daily Work Log", parent, "employee") if parent else None


def _assert_task_owner(task_name, employee_name):
    """Block cross-employee task mutation via db.set_value (BOLA guard)."""
    task_employee = _task_owner_employee(task_name)
    if task_employee != employee_name:
        frappe.throw("Not authorised to edit this task.", frappe.PermissionError)


def _assert_task_visible(task_name):
    """Allow the task's own employee, HR Manager, or that employee's Team Leader.

    Checks the HR Manager role first, same order as get_employee_task_detail —
    an HR Manager user need not have their own Employee record, so
    _get_employee() must not run unless the role check falls through.
    """
    if "HR Manager" in frappe.get_roles(frappe.session.user):
        return
    task_employee = _task_owner_employee(task_name)
    viewer_employee_name = _get_employee().name
    if task_employee == viewer_employee_name:
        return
    if task_employee in _get_team_members(viewer_employee_name):
        return
    frappe.throw("Not authorised to view this task.", frappe.PermissionError)


def _attach_task_files(tasks):
    """Batch-fetch File attachments for a list of task dicts, setting task['attachments']."""
    if not tasks:
        return
    files_by_task = {}
    for f in frappe.get_all("File",
        filters={"attached_to_doctype": "Task Entry", "attached_to_name": ["in", [t.name for t in tasks]]},
        fields=["name", "file_name", "file_url", "attached_to_name"],
    ):
        files_by_task.setdefault(f.attached_to_name, []).append(f)
    for t in tasks:
        t["attachments"] = files_by_task.get(t.name, [])


def _reparent_attachments(file_names, target_name, target_doctype="Task Entry"):
    """Point orphan-uploaded Files (no doctype/docname yet) at the now-saved task or document.

    `file_names` is client-supplied. Only reparent a File that is (a) still
    genuinely unattached and (b) owned by the calling user — otherwise any
    employee could pass another employee's (or another doctype's) File
    docname here and silently steal that attachment onto their own task,
    since `frappe.db.set_value` bypasses the framework's own permission
    checks. A name that fails either check is just skipped, not thrown on —
    a stale/tampered id in one row shouldn't fail the whole submission.
    """
    if not file_names:
        return
    files = frappe.get_all("File",
        filters={"name": ["in", file_names]},
        fields=["name", "attached_to_doctype", "owner"],
    )
    for f in files:
        if f.attached_to_doctype or f.owner != frappe.session.user:
            continue
        frappe.db.set_value("File", f.name, {
            "attached_to_doctype": target_doctype,
            "attached_to_name": target_name,
        })


# ── Daily Work Log helpers ──────────────────────────────────────────────────────
# One `Daily Work Log` document per (employee, date), holding check-in/checkout
# fields plus the day's `Task Entry` child rows — replaces the old
# `Daily Task Log` (submit/cancel, one doc per log_type) + standalone
# `Daily Task` (one doc per task) pair. See specs/daily-work-log-refactor.md.

def _get_work_log(employee, date):
    """Fetch the Daily Work Log doc for (employee, date), or None."""
    name = frappe.db.exists("Daily Work Log", {"employee": employee, "date": date})
    return frappe.get_doc("Daily Work Log", name) if name else None


def _get_or_new_work_log(employee, date):
    """Fetch the Daily Work Log for (employee, date), or an unsaved in-memory
    one if none exists yet — caller is responsible for saving it."""
    log = _get_work_log(employee, date)
    if log:
        return log
    log = frappe.new_doc("Daily Work Log")
    log.employee = employee
    log.date = date
    return log


def _save_work_log(log):
    # Permission-checked, not bypassed — has_permission_daily_work_log (hooks.py)
    # gates this to the doc's own employee (or HR/System Manager/the
    # task-assignment agent), so a caller acting on another employee's log
    # without one of those roles is stopped here rather than relying solely
    # on each controller method's own ownership checks.
    if log.is_new():
        log.insert()
    else:
        log.save()


def _next_sequence(work_log):
    """Next entry-order value for a new Task Entry row on this work log."""
    return max([row.sequence or 0 for row in work_log.tasks], default=0)


def _task_entry_dict(row, parent_date):
    """Flat dict view of a Task Entry child row (ORM row or plain dict from
    frappe.get_all), shaped like the old standalone Daily Task doctype so
    downstream code (dashboards, emails, my-history) needs no changes.
    `rolled_over_from` is synthesised as a truthy marker — every existing
    caller only ever checks it for truthiness, never reads the value. Real
    lineage is tracked via `series_id`/`origin_date` now (spec Section 4).
    """
    get = row.get if hasattr(row, "get") else (lambda k, d=None: getattr(row, k, d))
    origin = get("origin_date")
    is_carried = bool(origin) and str(origin) != str(parent_date)
    return frappe._dict({
        "name": get("name"),
        "description": get("description"),
        "status": get("status"),
        "task_type": get("task_type"),
        "origin_date": origin,
        "rolled_over_from": "carried" if is_carried else None,
        "is_carried": is_carried,
        "remarks": get("remarks"),
        "estimated_time": get("estimated_time"),
        "actual_time": get("actual_time"),
        "project_name": get("project_name"),
        "series_id": get("series_id"),
        "repeat_next_day": cint(get("repeat_next_day")),
        "assigned_by": get("assigned_by"),
        "assigned_by_name": get("assigned_by_name"),
        "start_time": (_to_hhmm(get("start_time")) or None) if get("start_time") else None,
    })


def _resolve_task_row(rows_by_name, task_name, work_log, employee_name, context):
    """Look up a Task Entry row the client is trying to edit, or fail with a
    refresh-and-retry error instead of a misleading "not authorised" one.

    The client only ever submits row names it rendered from an earlier
    server response, so a miss here isn't a permission violation — the row
    was deleted, rolled over into a new copy, or otherwise changed server-side
    between page load and submit (e.g. a stale tab held open across a
    same-day edit made from another tab/device, or a Team Leader/HR editing
    the same Daily Work Log via Desk). Logged so a real occurrence is
    diagnosable instead of a one-off support report with no trail.
    """
    row = rows_by_name.get(task_name)
    if not row:
        frappe.log_error(
            f"{context}: task '{task_name}' not found on {employee_name}'s "
            f"{work_log.date} Daily Work Log ({work_log.name}). "
            f"Rows currently on it: {[r.name for r in work_log.tasks]}",
            "ST Attendance Tracker — stale task reference"
        )
        frappe.throw(
            "This task could not be found — it may have been updated from "
            "another tab or device. Please refresh the page and try again.",
            frappe.ValidationError
        )
    return row


def _is_series_done(series_id):
    """True if any copy of this task's lineage has ever been marked Done."""
    return bool(series_id) and bool(frappe.db.exists("Task Entry", {
        "series_id": series_id, "status": "Done",
    }))


def _cascade_series_done(series_id, as_of_date):
    """Mark every past/current copy of a task's lineage Done, and delete every
    future carried-forward copy that hasn't been touched yet."""
    if not series_id:
        return

    te = frappe.qb.DocType("Task Entry")
    dwl = frappe.qb.DocType("Daily Work Log")

    query = (
        frappe.qb.from_(te)
        .inner_join(dwl).on(te.parent == dwl.name)
        .select(te.name, te.status, dwl.date)
        .where(te.series_id == series_id)
    ).run(as_dict=True)

    for row in query:
        if getdate(row.date) <= getdate(as_of_date):
            frappe.db.set_value("Task Entry", row.name, "status", "Done")
        elif row.status in ('Pending', 'In Progress', 'Rolled Over'):
            frappe.db.delete("Task Entry", {"name": row.name})


def _is_half_day_leave_today(employee_name, date):
    """True if employee has an approved half-day Leave Application covering `date`."""
    dt = getdate(date)
    exact = frappe.db.exists("Leave Application", {
        "employee": employee_name, "status": "Approved", "docstatus": 1,
        "half_day": 1, "half_day_date": dt,
    })
    if exact:
        return True
    return bool(frappe.db.exists("Leave Application", {
        "employee": employee_name, "status": "Approved", "docstatus": 1,
        "half_day": 1, "from_date": ["<=", dt], "to_date": [">=", dt],
    }))


def _get_employees_on_leave(employee_names, date):
    """Employee names (from `employee_names`) considered on leave for `date`:
    an approved, submitted Leave Application covering the date, OR an
    Attendance record already marked "On Leave" for that exact date — some
    leave is recorded directly on Attendance rather than through a Leave
    Application, and both should count for reminder/report exclusion.
    Half-day leave/attendance is deliberately NOT included here — that
    employee is still expected to check in/out for their working half.
    """
    if not employee_names:
        return set()
    on_leave = set(frappe.get_all("Leave Application", filters={
        "employee": ["in", employee_names],
        "from_date": ["<=", date],
        "to_date": [">=", date],
        "status": "Approved",
        "docstatus": 1,
    }, pluck="employee"))
    on_leave.update(frappe.get_all("Attendance", filters={
        "employee": ["in", employee_names],
        "attendance_date": date,
        "status": "On Leave",
        "docstatus": 1,
    }, pluck="employee"))
    return on_leave


def _resolve_half_day_session(employee_name, date, raw_value):
    """Validate + persist a half-day session choice. Informational only —
    never blocks check-in either way, just refuses to store nonsense."""
    value = (raw_value or "").strip()
    if value not in ("First Half", "Second Half"):
        return ""
    if not _is_half_day_leave_today(employee_name, date):
        return ""
    return value


# ── Team leader check ──────────────────────────────────────────────────────────

def _get_team_members(employee_name):
    """
    All active employees this person leads — via reports_to OR any Employee
    Department Assignment row naming them Team Leader.
    """
    reports_to_names = frappe.get_all("Employee", filters={
        "reports_to": employee_name, "status": "Active",
    }, pluck="name")

    eda_parents = frappe.get_all("Employee Department Assignment",
        filters={"parenttype": "Employee", "team_leader": employee_name},
        pluck="parent")
    eda_names = frappe.get_all("Employee", filters={
        "name": ["in", eda_parents], "status": "Active",
    }, pluck="name") if eda_parents else []

    return list(set(reports_to_names) | set(eda_names))


def _is_team_leader(employee_name):
    """True if this person leads at least one active employee (reports_to or EDA)."""
    return bool(_get_team_members(employee_name))


# ── ST Attendance Settings cache ────────────────────────────────────────────────

def _get_attendance_settings():
    """All ST Attendance Settings fields, cached — read on nearly every
    check-in/checkout/dashboard load but change only a handful of times
    a year. Invalidated explicitly on save (see clear_attendance_settings_cache),
    not a TTL guess, so HR sees changes immediately."""
    cache_key = "st_att:settings"
    # expires=True: without it, RedisWrapper.get_value() on a miss caches the
    # `None` into frappe.local's per-request memory cache (redis_wrapper.py),
    # and since set_value() with a TTL never clears that local entry, every
    # later get_value() call *in the same request* would keep returning the
    # stale None from local memory without ever checking Redis again — even
    # though Redis now holds the real value written just below. Only matters
    # right after a cache-cold miss; a warm cache is unaffected either way.
    cached = frappe.cache().get_value(cache_key, expires=True)
    if cached is not None:
        return cached
    settings = frappe.get_single("ST Attendance Settings").as_dict()
    frappe.cache().set_value(cache_key, settings, expires_in_sec=3600)
    return settings


def clear_attendance_settings_cache(doc=None, method=None):
    frappe.cache().delete_value("st_att:settings")


# ── HR Manager emails ──────────────────────────────────────────────────────────

def _get_hr_manager_emails():
    """Emails of all enabled users with HR Manager role (User.name is the
    email for standard Frappe Users, so get_users_with_role's names double
    as emails here). Excludes Administrator: this was already this
    function's own behavior before it was extracted for reuse, and
    tasks.py's scheduled HR reports now go through it too — previously
    tasks.py had its own inline copy that didn't exclude Administrator,
    an inconsistency between the two call sites rather than an
    intentional difference."""
    return get_users_with_role("HR Manager")


# ── Checkin helper ─────────────────────────────────────────────────────────────

CHECKIN_DEVICE_ID = "ST Daily Checkin"
LUNCH_DEVICE_ID = "ST Daily Checkin Lunch"


def _make_lunch_checkins(employee, lunch_from, lunch_to, on_date):
    """Record lunch as an OUT at lunch start and an IN at lunch end, so HRMS's
    "Every Valid Check-in and Check-out" working-hours calculation leaves lunch out.
    No-op without a valid lunch window or when this day's lunch rows already exist
    (checkout can be re-run after HR reopens a log)."""
    if not (lunch_from and lunch_to) or time_to_minutes(lunch_to) <= time_to_minutes(lunch_from):
        return
    day = getdate(on_date)
    if frappe.db.exists("Employee Checkin", {
        "employee": employee,
        "device_id": LUNCH_DEVICE_ID,
        "time": ["between", [f"{day} 00:00:00", f"{day} 23:59:59"]],
    }):
        return
    _make_checkin(employee, "OUT", lunch_from, on_date=on_date, device_id=LUNCH_DEVICE_ID)
    _make_checkin(employee, "IN", lunch_to, on_date=on_date, device_id=LUNCH_DEVICE_ID)


def _make_checkin(employee, log_type, time_value=None, on_date=None, device_id=CHECKIN_DEVICE_ID):
    """on_date: the work date the log belongs to. A late checkout is recorded days after
    the fact, so its OUT must land on the work date, not on today."""
    try:
        checkin = frappe.new_doc("Employee Checkin")
        checkin.employee = employee
        checkin.log_type = log_type
        if time_value:
            if isinstance(time_value, str):
                parts = (time_value + ":00").split(":")[:3]
                t = datetime.time(int(parts[0]), int(parts[1]), int(parts[2]))
            else:
                t = time_value
            checkin.time = datetime.datetime.combine(getdate(on_date or today()), t)
        else:
            checkin.time = now_datetime()
        checkin.device_id = device_id

        # Integrate with active HRMS Shift Assignment
        try:
            from hrms.hr.doctype.shift_assignment.shift_assignment import get_employee_shift
            shift = get_employee_shift(employee, checkin.time, consider_default_shift=True)
            if shift:
                checkin.shift = shift.shift_type.name
        except Exception:
            frappe.log_error(title="ST Attendance Tracker — checkin shift lookup failed", message=frappe.get_traceback())

        checkin.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception as e:
        # log_error(title, message): a swapped call put the long text in the 140-char title,
        # so the handler itself raised and aborted check-in/checkout.
        frappe.log_error(
            title="ST Attendance Tracker — Employee Checkin failed",
            message=f"Checkin failed for {employee} ({log_type}): {e}",
        )


def _format_hours(val):
    if not val:
        return ""
    try:
        val_float = float(val)
        if val_float <= 0:
            return ""
        total_minutes = int(round(val_float * 60))
        h, m = divmod(total_minutes, 60)
        if h > 0 and m > 0:
            return f"{h}h {m}m"
        elif h > 0:
            return f"{h}h"
        elif m > 0:
            return f"{m}m"
        return ""
    except Exception:
        return str(val)


def _parse_time_to_hours(s):
    return parse_duration_to_hours(s)


def _to_hhmm(t):
    """
    Safely convert any time value to 'HH:MM' string.

    Handles:
      • datetime.timedelta  — returned by Frappe DB for Time fields.
        str(timedelta(seconds=34200)) = '9:30:00'.
        '9:30:00'[:5] = '9:30:' (trailing colon) which
        frappe.utils.get_datetime / dateutil CANNOT parse → exception →
        _calc_net_hours silently returns '' → net_hours stored as empty.
      • 'H:MM:SS' / 'HH:MM:SS' — strings with or without leading zero
      • 'HH:MM' — already-clean strings from JavaScript input[type=time]
      • AM/PM formats like '05:19 pm' or '5:19 PM'
      • None / '' — returns ''
    """
    if isinstance(t, datetime.timedelta):
        secs = int(t.total_seconds())
        return f"{secs // 3600:02d}:{(secs % 3600) // 60:02d}"
    s = str(t or "").strip().lower()
    if not s:
        return ""
    is_pm = "pm" in s
    is_am = "am" in s
    s = s.replace("pm", "").replace("am", "").strip()
    parts = s.split(":")
    if len(parts) >= 2:
        try:
            h = int(parts[0])
            m = int(parts[1])
            if is_pm and h < 12:
                h += 12
            elif is_am and h == 12:
                h = 0
            return f"{h:02d}:{m:02d}"
        except Exception:
            pass
    return ""


def _to_ampm(t):
    """
    Converts a time/timedelta/string to 'HH:MM AM/PM' format.
    """
    hhmm = _to_hhmm(t)
    if not hhmm:
        return ""
    try:
        parts = hhmm.split(":")
        h = int(parts[0])
        m = int(parts[1])
        ampm = "PM" if h >= 12 else "AM"
        h = h % 12
        if h == 0:
            h = 12
        return f"{h:02d}:{m:02d} {ampm}"
    except Exception:
        return hhmm


def _format_action_timestamp(dt):
    """
    Full 'DD Mon YYYY, HH:MM AM/PM' timestamp of the actual moment an
    action happened (server clock, captured when the request hit the
    server) — independent of the Login/Logout Time fields, which the
    employee can edit. Lets management reading the email later know
    exactly when check-in/check-out was really pressed.
    """
    return dt.strftime("%d %b %Y, %I:%M %p")


def _format_email_date(date):
    """dd-mm-yyyy for the 'Date'/'Shift Date' row in email detail cards —
    matches the format already used in every email subject line."""
    return frappe.utils.getdate(date).strftime("%d-%m-%Y")


# ── Notification helper ────────────────────────────────────────────────────────

# Shared, single-tone styling — every email uses the same font/width/accent color.
EMAIL_ACCENT_COLOR = "#1e3a5f"
EMAIL_WRAPPER_STYLE = (
    "font-family:'Segoe UI', Arial, sans-serif; max-width: 680px; "
    "margin: 0 auto; padding: 16px; color: #1f2937;"
)
EMAIL_HEADING_STYLE = (
    f"font-size: 18px; font-weight: bold; color: {EMAIL_ACCENT_COLOR}; margin-bottom: 12px;"
)
EMAIL_SUBHEADING_STYLE = (
    f"font-size: 16px; font-weight: bold; color: {EMAIL_ACCENT_COLOR}; "
    "margin: 20px 0 12px;"
)


def _render_detail_table(rows):
    """
    Render a clean label/value detail card. rows: list of (label, value)
    tuples; any row with a falsy value is skipped. Single consistent
    style for every row — no per-field colors.
    """
    visible_rows = [(label, value) for label, value in rows if value]
    if not visible_rows:
        return ""

    body = "".join(
        f"""
        <tr>
          <td style="padding:7px 12px;font-size:13px;color:#64748b;width:40%;
                     border-bottom:1px solid #e2e8f0;vertical-align:top">{html.escape(str(label))}</td>
          <td style="padding:7px 12px;font-size:13px;font-weight:600;color:#1f2937;
                     border-bottom:1px solid #e2e8f0;vertical-align:top">{html.escape(str(value))}</td>
        </tr>
        """
        for label, value in visible_rows
    )
    return f"""
    <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;
           border:1px solid #e2e8f0;border-radius:6px;overflow:hidden;margin-bottom:8px;
           font-family:'Segoe UI', Arial, sans-serif">
      <tbody>{body}</tbody>
    </table>
    """


# ── Employee self-notification emails ─────────────────────────────────────────

def _send_employee_checkin_email(employee_name, employee_display_name, login_time,
                                  checkin_action_time, work_location, half_day_session,
                                  is_late, tasks, date, department=None):
    """
    Send check-in confirmation email to the employee themselves.
    Includes a detail card (login time, work location, ...) and
    today's planned task list grouped by project.
    """
    try:
        emp_email = _get_employee_email(employee_name)
        if not emp_email:
            return

        login_hm = _to_ampm(login_time)
        detail_table_html = _render_detail_table([
            ("Employee", employee_display_name),
            ("Date", _format_email_date(date)),
            ("Login Time", login_hm),
            ("Checked-In At", _format_action_timestamp(checkin_action_time)),
            ("Work Location", work_location),
            ("Half-Day Session", half_day_session),
            ("Status", "Late Check-in" if is_late else None),
        ])
        task_table_html = _get_task_summary_html(tasks, department, is_checkout=False)

        html = f"""
        <div style="{EMAIL_WRAPPER_STYLE}">
          <div style="{EMAIL_HEADING_STYLE}">Check-In Confirmation</div>
          {detail_table_html}
          <div style="{EMAIL_SUBHEADING_STYLE}">Today's Work To Do</div>
          <div>
            {task_table_html}
          </div>
        </div>
        """

        sender, reply_to = _employee_mail_identity(employee_name, employee_display_name)
        frappe.sendmail(
            recipients=[emp_email],
            subject=f"{employee_display_name} - Check-in - {frappe.utils.getdate(date).strftime('%d-%m-%Y')}",
            message=html,
            now=False,
            sender=sender,
            reply_to=reply_to,
        )
    except Exception as e:
        frappe.log_error(
            f"Check-in email failed for {employee_name}: {e}",
            "ST Attendance Tracker"
        )


def _send_employee_eod_email(employee_name, employee_display_name, logout_time,
                              checkout_action_time, net_hours, work_location,
                              half_day_session, tasks, date,
                              is_late_checkout=False, submission_date=None, department=None):
    """
    Send EOD confirmation email to the employee themselves.
    Includes a detail card (hours, work location, ...), done tasks,
    and carried-forward tasks.
    """
    try:
        emp_email = _get_employee_email(employee_name)
        if not emp_email:
            return

        done_tasks = [t for t in tasks if t.get("status") == "Done"]

        # Fetch lunch and check-in times from the day's Daily Work Log
        work_log = _get_work_log(employee_name, date)
        lunch_from = work_log.lunch_from if work_log else None
        lunch_to = work_log.lunch_to if work_log else None
        login_time = work_log.login_time if work_log else None

        login_hm = _to_ampm(login_time) if login_time else None
        logout_hm = _to_ampm(logout_time)
        lunch_from_hm = _to_ampm(lunch_from) if lunch_from else None
        lunch_to_hm = _to_ampm(lunch_to) if lunch_to else None
        lunch_break = f"{lunch_from_hm} - {lunch_to_hm}" if lunch_from_hm and lunch_to_hm else None

        task_table_html = _get_task_summary_html(
            tasks, department, is_checkout=True, lunch_from=lunch_from_hm, lunch_to=lunch_to_hm
        )

        total_actual_hours = sum(float(t.get("actual_time") or 0.0) for t in tasks)
        working_hours_str = _format_hours(total_actual_hours) or "0h"

        detail_table_html = _render_detail_table([
            ("Employee", employee_display_name),
            ("Shift Date", _format_email_date(date)),
            ("Login Time", login_hm),
            ("Logout Time", logout_hm),
            ("Checked-Out At", _format_action_timestamp(checkout_action_time)),
            ("Lunch Break", lunch_break),
            ("Work Location", work_location),
            ("Net Working Hours", net_hours),
            ("Total Task Hours", working_hours_str),
            ("Half-Day Session", half_day_session),
            ("Tasks Completed", f"{len(done_tasks)}/{len(tasks)}"),
        ])

        late_note_html = ""
        work_summary_heading = "Today's Work Summary"
        if is_late_checkout:
            late_note_html = f"""<div style="background:#fffbeb;border:1px solid #fde68a;border-radius:6px;padding:10px 14px;margin-bottom:14px;font-size:13px;color:#92400e;">
              Note: this checkout is for your shift on <strong>{date}</strong>, submitted on {submission_date} because checkout was missed at the time.
            </div>"""
            work_summary_heading = f"Work Summary for {frappe.utils.getdate(date).strftime('%d %b %Y')}"

        html = f"""
        <div style="{EMAIL_WRAPPER_STYLE}">
          <div style="{EMAIL_HEADING_STYLE}">Checkout Summary</div>
          {late_note_html}
          {detail_table_html}
          <div style="{EMAIL_SUBHEADING_STYLE}">{work_summary_heading}</div>
          <div>
            {task_table_html}
          </div>
        </div>"""

        sender, reply_to = _employee_mail_identity(employee_name, employee_display_name)
        frappe.sendmail(
            recipients=[emp_email],
            subject=f"{employee_display_name} - Check-out - {frappe.utils.getdate(date).strftime('%d-%m-%Y')}",
            message=html,
            now=False,
            sender=sender,
            reply_to=reply_to,
            attachments=_task_attachment_specs(tasks),
            inline_images=_task_inline_images(tasks),
        )
    except Exception as e:
        frappe.log_error(
            f"EOD email failed for {employee_name}: {e}",
            "ST Attendance Tracker"
        )


def _get_employee_email(employee_name):
    """Get employee's best available email address."""
    emp = frappe.db.get_value("Employee", employee_name,
        ["prefered_email", "company_email", "personal_email", "user_id"],
        as_dict=True)
    if not emp:
        return None
    email = (
        emp.get("prefered_email") or
        emp.get("company_email") or
        emp.get("personal_email")
    )
    if not email and emp.get("user_id"):
        candidate = frappe.db.get_value("User", emp["user_id"], "email") or emp["user_id"]
        email = candidate if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", candidate or "") else None
    return email


def _employee_mail_identity(employee_name, employee_display_name):
    """(sender, reply_to) so a notification about this employee's check-in/
    checkout looks like it's from them, while still relaying through the
    site's one working outgoing account (avoids From/SPF-DKIM mismatch).
    Falls back to (None, None) if no default outgoing account is configured,
    so frappe.sendmail just uses its normal default sender."""
    account_email = frappe.db.get_value("Email Account", {"default_outgoing": 1}, "email_id")
    if not account_email:
        return None, None
    return frappe.utils.formataddr((employee_display_name, account_email)), _get_employee_email(employee_name)


EMAIL_INLINE_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def _inline_image_key(file_row):
    """Unique `embed=` key for an image file, or None when it is not an image.
    Keyed by File name so two tasks attaching 'image.png' don't collide."""
    ext = os.path.splitext(file_row.get("file_name") or "")[1].lower()
    return f"{file_row['name']}{ext}" if ext in EMAIL_INLINE_IMAGE_EXTENSIONS else None


def _task_attachment_specs(tasks):
    """frappe.sendmail(attachments=...) entries for the non-image files across
    these tasks. Images are shown inline instead (see _task_inline_images)."""
    return [
        {"fid": f["name"]}
        for t in (tasks or []) for f in (t.get("attachments") or [])
        if not _inline_image_key(f)
    ]


def _task_inline_images(tasks):
    """frappe.sendmail(inline_images=...) entries for every image across these tasks."""
    images = []
    for t in tasks or []:
        for f in t.get("attachments") or []:
            key = _inline_image_key(f)
            if not key:
                continue
            content = frappe.get_doc("File", f["name"]).get_content()
            if content:
                images.append({"filename": key, "filecontent": content})
    return images


def _task_images_html(task):
    """<img embed=...> tags that frappe.sendmail swaps for cid: inline images."""
    tags = [
        f'<img embed="{_inline_image_key(f)}" alt="{html.escape(f["file_name"])}" '
        'style="display:block;max-width:100%;height:auto;margin-top:8px;border:1px solid #e2e8f0;border-radius:4px">'
        for f in (task.get("attachments") or []) if _inline_image_key(f)
    ]
    return "".join(tags)


def _attachment_names_html(task, is_checkout=False):
    """Small '📎 filename, filename' line for files the reader gets as real
    attachments. At checkout, images are shown inline instead and are left out."""
    names = [
        html.escape(f["file_name"]) for f in (task.get("attachments") or [])
        if not (is_checkout and _inline_image_key(f))
    ]
    if not names:
        return ""
    return f'<div style="font-size:11.5px;color:#64748b;margin-top:4px">📎 {", ".join(names)}</div>'


def _render_screenshot_task_table(tasks, is_checkout=False, lunch_from=None, lunch_to=None):
    if not tasks:
        return '<p style="font-size:13px;color:#9ca3af;font-style:italic;margin:6px 0">No tasks added yet.</p>'

    # Group tasks by project, preserving the order projects were first entered in
    groups = {}
    for t in tasks:
        pname = (t.get("project_name") or "").strip()
        if not pname:
            pname = "General"
        groups.setdefault(pname, []).append(t)

    sorted_group_names = list(groups.keys())

    # Single accent color for all project names; alternating neutral shade
    # per group is the only visual separator — no rainbow.
    palettes = [
        {"bg": "#ffffff", "border": "#e2e8f0", "text": EMAIL_ACCENT_COLOR},
        {"bg": "#f8fafc", "border": "#e2e8f0", "text": EMAIL_ACCENT_COLOR},
    ]

    html_rows = []

    header_html = f"""
    <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin-top:16px;font-family:'Segoe UI', Arial, sans-serif;border:1px solid #e2e8f0;table-layout:fixed">
      <thead>
        <tr style="background-color:{EMAIL_ACCENT_COLOR};color:#ffffff;font-size:12.5px;font-weight:bold">
          <th style="padding:12px 10px;text-align:left;border:1px solid #e2e8f0;width:20%">Client</th>
          <th style="padding:12px 10px;text-align:left;border:1px solid #e2e8f0;width:50%">Task / Work</th>
          <th style="padding:12px 10px;text-align:center;border:1px solid #e2e8f0;width:15%">Time</th>
          <th style="padding:12px 10px;text-align:center;border:1px solid #e2e8f0;width:15%">Status</th>
        </tr>
      </thead>
      <tbody>
    """
    html_rows.append(header_html)

    for group_idx, pname in enumerate(sorted_group_names):
        palette = palettes[group_idx % len(palettes)]
        ptasks = groups[pname]

        if group_idx > 0:
            sep_html = """
            <tr style="height:12px;line-height:12px">
              <td colspan="4" style="border:none;background-color:transparent;height:12px;padding:0;margin:0"></td>
            </tr>
            """
            html_rows.append(sep_html)

        for task_idx, t in enumerate(ptasks):
            client_text = html.escape(pname) if task_idx == 0 else ""
            client_style = f"padding:10px 12px;font-size:13px;font-weight:bold;color:{palette['text']};background-color:{palette['bg']};border:1px solid {palette['border']};vertical-align:top;word-wrap:break-word;overflow:hidden;text-overflow:ellipsis" if task_idx == 0 else f"padding:10px 12px;background-color:{palette['bg']};border:1px solid {palette['border']};vertical-align:top"

            badges = []
            if t.get("rolled_over_from"):
                badges.append('<span style="background-color:#fffbeb;color:#b45309;font-size:10px;font-weight:600;padding:2px 6px;border-radius:4px;border:1px solid #fde68a;margin-left:6px;display:inline-block">Carried</span>')
            if t.get("task_type") == "Ad-hoc":
                badges.append('<span style="background-color:#faf5ff;color:#7c3aed;font-size:10px;font-weight:600;padding:2px 6px;border-radius:4px;border:1px solid #e9d5ff;margin-left:6px;display:inline-block">Additional</span>')
            
            badges_str = "".join(badges)
            task_style = f"padding:10px 12px;font-size:13px;color:#334155;background-color:{palette['bg']};border:1px solid {palette['border']};vertical-align:top;word-wrap:break-word"

            if is_checkout:
                raw_time = t.get("actual_time") or t.get("estimated_time") or ""
            else:
                raw_time = t.get("estimated_time") or ""
            time_val = _format_hours(raw_time)
            time_style = f"padding:10px 12px;font-size:12px;color:#475569;text-align:center;background-color:{palette['bg']};border:1px solid {palette['border']};vertical-align:middle"

            status_val = ""
            status_style_color = "#475569"
            if is_checkout:
                if t.get("status") == "Done":
                    status_val = "✔ Done"
                    status_style_color = "#047857"
                else:
                    status_val = "Pending"
                    status_style_color = "#b45309"
            
            status_style = f"padding:10px 12px;font-size:12px;font-weight:bold;text-align:center;color:{status_style_color};background-color:#fef8e7;border:1px solid {palette['border']};vertical-align:middle"

            remark_html = ""
            if is_checkout and t.get("remarks"):
                formatted_remark = html.escape(t.get("remarks")).replace('\n', '<br>')
                remark_html = f'<div style="font-size:11.5px;color:#64748b;font-style:italic;margin-top:4px">Remark: {formatted_remark}</div>'
            remark_html += _attachment_names_html(t, is_checkout)
            if is_checkout:
                remark_html += _task_images_html(t)

            row_html = f"""
            <tr>
              <td style="{client_style}">{client_text}</td>
              <td style="{task_style}">{html.escape(t.get("description") or "")}{badges_str}{remark_html}</td>
              <td style="{time_style}">{time_val}</td>
              <td style="{status_style}">{status_val}</td>
            </tr>
            """
            html_rows.append(row_html)

    html_rows.append("  </tbody>\n</table>")
    return "".join(html_rows)


def _render_grouped_task_summary(tasks, is_checkout=False, lunch_from=None, lunch_to=None):
    """Task list grouped by project, rendered as clean plain-text-style HTML.

    Designed for readability on mobile email clients: no absolute positioning,
    no colored badge spans, no tables. Projects are bold headings; tasks are
    dash-prefixed lines with metadata on a subtle second line.

    Department-level alternative to the tabular layout — selected via the
    "Task Summary Email Format = Grouped List" setting on Department.
    """
    if not tasks:
        return (
            '<p style="font-size:14px;color:#9ca3af;font-style:italic;'
            'margin:8px 0;font-family:\'Segoe UI\',Arial,sans-serif">'
            'No tasks added yet.</p>'
        )

    # ── Group tasks by project, preserving insertion order ──────────────
    groups = {}
    for t in tasks:
        pname = (t.get("project_name") or "").strip() or "General"
        groups.setdefault(pname, []).append(t)

    sorted_group_names = list(groups.keys())

    # Common inline styles — defined once, reused everywhere.
    FONT = "font-family:'Segoe UI',Arial,sans-serif"
    # Outer wrapper: generous line-height, fills parent.
    WRAP = f"{FONT};line-height:1.6;color:#1f2937"
    # Project heading: bold, slightly larger, thin bottom rule.
    PROJ_HEAD = (
        f"{FONT};font-size:15px;font-weight:700;color:#1e293b;"
        "padding:0 0 5px 0;margin:0 0 8px 0;"
        "border-bottom:1px solid #e2e8f0"
    )
    # Task description line (pre-wrap preserves spaces and newlines).
    TASK_LINE = f"{FONT};font-size:14px;color:#334155;margin:0;padding:0;white-space:pre-wrap"
    # Subtle metadata line (time, status, labels, remarks).
    META_LINE = f"{FONT};font-size:12px;color:#64748b;margin:2px 0 0 18px;padding:0"

    # ── Render each project group ──────────────────────────────────────
    group_blocks = []
    for group_idx, pname in enumerate(sorted_group_names):
        lines = []
        for task_idx, t in enumerate(groups[pname]):
            # Replace \n with <br> to ensure email clients render newlines correctly.
            # white-space: pre-wrap will also handle any consecutive spaces.
            desc = html.escape(t.get("description") or "").replace('\n', '<br>')

            # ── Inline labels (plain text, parenthesised) ──────────────
            labels = []
            if t.get("task_type") == "Recurring":
                labels.append("Recurring")
            if t.get("rolled_over_from"):
                labels.append("Carried")
            if t.get("task_type") == "Ad-hoc":
                labels.append("Additional")
            label_text = f' ({", ".join(labels)})' if labels else ""

            # ── Status / time info ─────────────────────────────────────
            meta_parts = []
            if is_checkout:
                if t.get("status") == "Done":
                    meta_parts.append("✔ Done")
                else:
                    meta_parts.append("○ Pending")

                time_val = _format_hours(t.get("actual_time") or "")
                if time_val:
                    meta_parts.append(f"Actual: {time_val}")
                else:
                    est_val = _format_hours(t.get("estimated_time") or "")
                    if est_val:
                        meta_parts.append(f"Est: {est_val}")
            else:
                est_val = _format_hours(t.get("estimated_time") or "")
                if est_val:
                    meta_parts.append(f"Est: {est_val}")

            # ── Remark ─────────────────────────────────────────────────
            if is_checkout and t.get("remarks"):
                formatted_remark = html.escape(t.get('remarks')).replace('\n', '<br>')
                meta_parts.append(f"Remark: {formatted_remark}")

            # ── Attachments ────────────────────────────────────────────
            att_names = [
                html.escape(f["file_name"]) for f in (t.get("attachments") or [])
                if not (is_checkout and _inline_image_key(f))
            ]
            if att_names:
                meta_parts.append(f'📎 {", ".join(att_names)}')

            # ── Assemble the task block ────────────────────────────────
            # Task number + description on the primary line
            num = task_idx + 1
            task_html = (
                f'<p style="{TASK_LINE}">'
                f'&nbsp;&nbsp;{num}. {desc}{html.escape(label_text)}'
                f'</p>'
            )

            # Metadata on a second, lighter line (if any)
            if meta_parts:
                task_html += (
                    f'<p style="{META_LINE}">'
                    f'{" · ".join(meta_parts)}'
                    f'</p>'
                )

            if is_checkout:
                task_html += f'<div style="margin:2px 0 0 18px">{_task_images_html(t)}</div>'

            lines.append(task_html)

        # ── Assemble the project block ─────────────────────────────────
        task_count = len(groups[pname])
        count_note = f' — {task_count} task{"s" if task_count != 1 else ""}'
        project_block = (
            f'<div style="margin:0 0 18px 0">'
            f'<p style="{PROJ_HEAD}">'
            f'{html.escape(pname)}'
            f'<span style="font-weight:400;font-size:12px;color:#94a3b8">'
            f'{count_note}</span></p>'
            f'{"".join(lines)}'
            f'</div>'
        )
        group_blocks.append(project_block)

    return f'<div style="{WRAP}">{"".join(group_blocks)}</div>'


def _get_task_summary_html(tasks, department, is_checkout=False, lunch_from=None, lunch_to=None):
    """Pick the task-summary renderer for this department, defaulting to
    the tabular layout when unset (covers departments that never opted in
    and any department without the custom field populated yet)."""
    fmt = frappe.db.get_value("Department", department, "task_summary_email_format") if department else None
    if fmt == "Grouped List":
        return _render_grouped_task_summary(tasks, is_checkout=is_checkout, lunch_from=lunch_from, lunch_to=lunch_to)
    return _render_screenshot_task_table(tasks, is_checkout=is_checkout, lunch_from=lunch_from, lunch_to=lunch_to)


def _get_team_leader_emails(employee_name):
    """
    Team Leader(s) to notify for this employee. Employees working across
    multiple departments (Employee Department Assignment child table) get
    every listed department's Team Leader notified. Employees with no
    assignment rows yet fall back to the legacy single reports_to manager.
    """
    team_leaders = frappe.get_all("Employee Department Assignment",
        filters={"parenttype": "Employee", "parent": employee_name},
        pluck="team_leader")

    if not team_leaders:
        reports_to = frappe.db.get_value("Employee", employee_name, "reports_to")
        team_leaders = [reports_to] if reports_to else []

    emails = []
    for tl in set(team_leaders):
        tl_user = frappe.db.get_value("Employee", tl, "user_id")
        if tl_user and tl_user not in emails:
            emails.append(tl_user)
    return emails


def _notify_hr_and_team_leader(employee_name, employee_display_name, event, detail_rows=None, tasks=None, department=None):
    """
    Send email to:
    1. All users with HR Manager role (auto-fetched)
    2. The employee's Team Leader(s) — see _get_team_leader_emails

    detail_rows: list of (label, value) tuples — same structured detail
                 card shown to the employee, so HR/TL see identical facts.
    tasks: optional list of Daily Task dicts for the relevant date.
    """
    try:
        hr_emails = _get_hr_manager_emails()
        team_leaders = _get_team_leader_emails(employee_name)
        emp_email = _get_employee_email(employee_name)

        to_recipients = list(dict.fromkeys(([emp_email] if emp_email else []) + team_leaders))
        cc_recipients = [hr for hr in hr_emails if hr not in to_recipients]

        if not to_recipients and not cc_recipients:
            return

        recipients = to_recipients if to_recipients else cc_recipients
        cc = cc_recipients if to_recipients else []

        email_date = tasks[0].get("task_date") if (tasks and tasks[0].get("task_date")) else frappe.utils.today()
        email_date_label = frappe.utils.getdate(email_date).strftime('%d-%m-%Y')
        subject_map = {
            "checkin":  f"{employee_display_name} - Check-in - {email_date_label}",
            "checkout": f"{employee_display_name} - Check-out - {email_date_label}",
        }
        subject = subject_map.get(event, f"{employee_display_name} - Update - {email_date_label}")

        lunch_from_hm = None
        lunch_to_hm = None
        if event == "checkout":
            date = email_date
            work_log = _get_work_log(employee_name, date)
            if work_log:
                lunch_from_hm = _to_ampm(work_log.lunch_from) if work_log.lunch_from else None
                lunch_to_hm = _to_ampm(work_log.lunch_to) if work_log.lunch_to else None

        task_html = _get_task_summary_html(
            tasks or [],
            department,
            is_checkout=(event == "checkout"),
            lunch_from=lunch_from_hm,
            lunch_to=lunch_to_hm
        )
        task_label = "Today's Tasks" if event == "checkin" else "Task Summary"
        detail_table_html = _render_detail_table(detail_rows or [])

        # Checkout only, since that's the only flow with an attach control.
        attachment_specs = _task_attachment_specs(tasks) if event == "checkout" else []

        html = f"""
        <div style="{EMAIL_WRAPPER_STYLE}">
          <div style="{EMAIL_HEADING_STYLE}">{html_escape(subject)}</div>
          {detail_table_html}
          <div style="{EMAIL_SUBHEADING_STYLE}">{task_label}</div>
          <div>
            {task_html}
          </div>
        </div>
        """

        sender, reply_to = _employee_mail_identity(employee_name, employee_display_name)
        frappe.sendmail(
            recipients=recipients,
            cc=cc,
            subject=subject,
            message=html,
            now=False,
            sender=sender,
            reply_to=reply_to,
            attachments=attachment_specs,
            inline_images=_task_inline_images(tasks) if event == "checkout" else [],
        )
    except Exception as e:
        frappe.log_error(
            f"Notification failed for {employee_name} ({event}): {e}",
            "ST Attendance Tracker"
        )


# ── Next working date ──────────────────────────────────────────────────────────

def _employee_holidays(employee):
    """Every date in the Holiday List assigned to this employee in the Employee master,
    weekly offs and public holidays alike. No Company default and no weekday is assumed:
    an employee without a list has no days off."""
    holiday_list = frappe.db.get_value("Employee", employee, "holiday_list")
    if not holiday_list:
        return set()
    rows = frappe.get_all("Holiday", filters={"parent": holiday_list}, pluck="holiday_date")
    return {getdate(h) for h in rows}


def _get_next_working_date(employee, from_date):
    holidays = _employee_holidays(employee)

    candidate = getdate(add_days(from_date, 1))
    while candidate in holidays:
        candidate = getdate(add_days(candidate, 1))
    return candidate


def _get_previous_working_date(employee, from_date):
    """Last working day before `from_date`: skips every date in the employee's Holiday List
    (weekly offs and public holidays), same rule as _get_next_working_date."""
    holidays = _employee_holidays(employee)

    candidate = getdate(add_days(from_date, -1))
    while candidate in holidays:
        candidate = getdate(add_days(candidate, -1))
    return candidate


def _get_unclosed_previous_day(employee_name, on_date):
    """The employee's last working day before `on_date` if they checked in then and never
    checked out, else None. Drives the "no new check-in until yesterday is closed" rule.
    Everything is read from ST Attendance Settings (switch + start date); days before the
    start date are never held against anyone."""
    settings = _get_attendance_settings()
    effective_from = settings.get("checkin_block_effective_from")
    if not cint(settings.get("block_checkin_until_checkout_done")) or not effective_from:
        return None
    previous_day = _get_previous_working_date(employee_name, on_date)
    if previous_day < getdate(effective_from):
        return None
    return frappe.db.get_value(
        "Daily Work Log",
        {"employee": employee_name, "date": previous_day, "morning_submitted": 1, "eod_submitted": 0},
        ["name", "date", "checkout_unlocked", "unlock_requested_at"],
        as_dict=True,
    )


def _resolve_late_logout_time(raw_value, work_log):
    """Logout time the employee typed for a day whose checkout was approved late: it must be
    a real clock time on that work date, after their login."""
    import re

    value = (raw_value or "").strip()
    if not re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", value):
        frappe.throw("Enter the time you actually logged out on {0}.".format(formatdate(work_log.date)))
    hours, minutes = (int(part) for part in value.split(":")[:2])
    if hours > 23 or minutes > 59:
        frappe.throw("Logout time is not a valid time.")
    login_minutes = time_to_minutes(work_log.login_time) if work_log.login_time else 0
    if hours * 60 + minutes <= login_minutes:
        frappe.throw("Logout time must be after your login time.")
    return "%02d:%02d:00" % (hours, minutes)


def _assert_checkout_allowed(work_log):
    """Early, friendly gate for the whitelisted checkout paths. The Daily
    Work Log controller enforces the same rule on every save (REST included)."""
    if is_checkout_locked(work_log.date, work_log.checkout_unlocked):
        frappe.throw(
            "Checkout window is closed. Get late checkout approval from HR.",
            frappe.PermissionError,
        )


def _resolve_active_checkin_date(employee_name):
    """Which date's check-in is still 'open' (checked in, no EOD submitted
    yet) — the date the checkin/checkout page and EOD submission should act
    on. Only looks back to yesterday: an unresolved check-in older than
    that is abandoned, not something to silently resume into. Without this
    bound, a months-old forgotten checkout (or resetting today's check-in
    and thereby un-masking one) would surface as if it were the active
    shift, hiding today's own tasks behind a stale date — this is the same
    "don't fabricate a real span past one legitimate day" rule already
    applied in resolve_zero_diff_minutes (time_utils.py).
    """
    # An HR-approved late checkout (any past date) takes priority: the
    # employee finishes that EOD before continuing with today.
    unlocked_stale = frappe.db.get_value("Daily Work Log", {
        "employee": employee_name,
        "morning_submitted": 1,
        "eod_submitted": 0,
        "checkout_unlocked": 1,
        "date": ["<", today()],
    }, "date", order_by="date asc")
    if unlocked_stale:
        return unlocked_stale

    latest = frappe.db.get_value("Daily Work Log", {
        "employee": employee_name,
        "morning_submitted": 1,
        "date": [">=", add_days(today(), -1)],
    }, ["date", "eod_submitted", "checkout_unlocked"], order_by="date desc", as_dict=True)

    if not latest or latest.eod_submitted:
        return today()
    # A past-date log whose checkout window closed without HR approval must
    # not hijack the page: the employee checks in for today instead.
    if str(latest.date) != str(today()) and is_checkout_locked(latest.date, latest.checkout_unlocked):
        return today()
    return latest.date


# ── Rollover on EOD ────────────────────────────────────────────────────────────

def _repeat_copies(source_log, target_log, target_date, only_done=True):
    """Fresh next-day copies of the tasks the employee marked "Repeat next day".

    A copy gets its own series_id on purpose: sharing the source's would make _is_series_done /
    _cascade_series_done treat the new copy as already finished. The flag is copied, so the task keeps
    coming back until the employee turns it off. Idempotent: skips a copy that is already on the target
    day (same text and project, created for that day).
    Recurring-type tasks are excluded; they have their own schedule.
    """
    made = 0
    for row in source_log.tasks:
        if not cint(row.get("repeat_next_day")) or row.task_type == "Recurring" or row.status == "Dropped":
            continue
        if only_done and row.status != "Done":
            continue
        already = any(
            cint(t.get("repeat_next_day")) and t.description == row.description
            and (t.project_name or "") == (row.project_name or "") and str(t.origin_date) == str(target_date)
            for t in target_log.tasks
        )
        if already:
            continue
        new_row = target_log.append("tasks", {})
        new_row.series_id = frappe.generate_hash(length=32)
        new_row.origin_date = target_date
        new_row.description = row.description
        new_row.task_type = "Planned"
        new_row.status = "Pending"
        new_row.estimated_time = row.estimated_time or ""
        new_row.project_name = row.project_name or ""
        new_row.repeat_next_day = 1
        new_row.remarks = f"[Repeated from {source_log.date}]"
        new_row.sequence = _next_sequence(target_log) + 1
        made += 1
    return made


def _rollover_pending_tasks(employee, date):
    # Lock the employee record to serialize EOD rollovers and prevent duplicate entries
    frappe.db.sql("select name from `tabEmployee` where name = %s for update", (employee,))

    next_date = _get_next_working_date(employee, date)

    work_log = _get_work_log(employee, date)
    if not work_log:
        return 0

    pending = [row for row in work_log.tasks
               if row.status in ("Pending", "In Progress") and row.task_type != "Recurring"]
    has_repeaters = any(cint(row.get("repeat_next_day")) and row.status == "Done" and row.task_type != "Recurring"
                        for row in work_log.tasks)
    if not pending and not has_repeaters:
        return 0

    next_log = _get_or_new_work_log(employee, next_date)
    existing_series = {row.series_id for row in next_log.tasks}
    repeated = _repeat_copies(work_log, next_log, next_date)

    # Source-row status changes below go through frappe.db.set_value, not
    # work_log.save() — this source day's Daily Work Log may already be
    # eod_submitted=1 (that's exactly what triggered this rollover call),
    # and re-saving a locked parent through the ORM would hit its own lock
    # guard. Task Entry has no controller validation of its own to skip, so
    # a direct field write is safe here — the same pattern the old
    # standalone-Daily-Task model used for this exact "housekeeping, not a
    # user edit" case.
    rolled = 0
    for row in pending:
        if _is_series_done(row.series_id):
            if row.status != "Done":
                frappe.db.set_value("Task Entry", row.name, "status", "Done")
            continue

        if row.series_id in existing_series or str(row.origin_date) == str(next_date):
            continue

        new_row = next_log.append("tasks", {})
        new_row.series_id = row.series_id
        new_row.origin_date = row.origin_date or date
        new_row.description = row.description
        new_row.task_type = "Planned"
        new_row.status = "Pending"
        new_row.estimated_time = row.estimated_time or ""
        new_row.project_name = row.project_name or ""
        new_row.remarks = f"[Carried from {row.origin_date or date}]"
        new_row.repeat_next_day = cint(row.get("repeat_next_day"))
        new_row.sequence = _next_sequence(next_log) + 1
        existing_series.add(row.series_id)
        frappe.db.set_value("Task Entry", row.name, "status", "Rolled Over")
        rolled += 1

    if rolled or repeated:
        _save_work_log(next_log)
        frappe.db.commit()
    return rolled


# ── Safety rollover on page load ───────────────────────────────────────────────

def _safety_rollover(employee, today_date):
    """
    Catches tasks missed because employee didn't submit EOD.
    Runs silently on morning page load before check-in.
    """
    # Lock the employee record to serialize page-load safety rollovers and prevent duplicate entries
    frappe.db.sql("select name from `tabEmployee` where name = %s for update", (employee,))

    stale_log_names = frappe.get_all("Daily Work Log", filters={
        "employee": employee,
        "date": ["<", today_date],
    }, pluck="name")
    if not stale_log_names:
        return

    today_log = _get_or_new_work_log(employee, today_date)
    existing_series = {row.series_id for row in today_log.tasks}
    appended = False

    # Source-row status changes go through frappe.db.set_value, not
    # source.save() — a stale day can itself already be eod_submitted=1,
    # and re-saving a locked parent through the ORM would hit its own lock
    # guard. See the matching comment in _rollover_pending_tasks.
    for log_name in stale_log_names:
        source = frappe.get_doc("Daily Work Log", log_name)
        for row in source.tasks:
            if row.status not in ("Pending", "In Progress") or row.task_type == "Recurring":
                continue

            if _is_series_done(row.series_id):
                if row.status != "Done":
                    frappe.db.set_value("Task Entry", row.name, "status", "Done")
                continue

            if row.series_id in existing_series or str(row.origin_date) == str(today_date):
                continue

            new_row = today_log.append("tasks", {})
            new_row.series_id = row.series_id
            new_row.origin_date = row.origin_date or source.date
            new_row.description = row.description
            new_row.task_type = "Planned"
            new_row.status = "Pending"
            new_row.estimated_time = row.estimated_time or ""
            new_row.project_name = row.project_name or ""
            new_row.remarks = f"[Auto-carried from {row.origin_date or source.date}]"
            new_row.repeat_next_day = cint(row.get("repeat_next_day"))
            new_row.sequence = _next_sequence(today_log) + 1
            existing_series.add(row.series_id)
            frappe.db.set_value("Task Entry", row.name, "status", "Rolled Over")
            appended = True

    # "Repeat next day" tasks finished on the last day worked: bring them to today if checkout
    # (which normally does this) never ran. Only the latest day, so an old chain is not replayed.
    latest_name = frappe.db.get_value("Daily Work Log", {"employee": employee, "date": ["<", today_date]},
                                      "name", order_by="date desc")
    if latest_name and _repeat_copies(frappe.get_doc("Daily Work Log", latest_name), today_log, today_date):
        appended = True

    if appended:
        _save_work_log(today_log)

    frappe.db.commit()


# ── Recurring tasks (e.g. daily scrum) ─────────────────────────────────────────

def _parse_recurring_days(raw):
    """Empty/blank -> every day (empty list, caller treats as no restriction).
    Mirrors ST Attendance Settings.hybrid_office_days' storage convention:
    Small Text, one day name per line, Monday..Saturday, no Sunday."""
    day_map = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5}
    if not raw:
        return []
    return [day_map[d] for d in (line.strip().lower() for line in raw.strip().split("\n")) if d in day_map]


def _ensure_recurring_tasks(employee, date):
    """
    For every active Recurring Task Template belonging to this employee,
    make sure a Daily Task for today already exists; insert one if not.
    Idempotent — safe to call on every page load / check-in attempt.
    Recurring-type tasks never enter the carry-forward chain (see the
    task_type exclusion in _rollover_pending_tasks) — a fresh one appears
    here again tomorrow regardless of whether today's was completed.
    A template only fires on the weekdays in its recurring_days; blank
    recurring_days means every day (backward compatible with templates
    created before this field existed).
    """
    templates = frappe.get_all("Recurring Task Template",
        filters={"employee": employee, "is_active": 1},
        fields=["name", "description", "project_name", "estimated_time", "recurring_days"])

    weekday_num = getdate(date).weekday()
    due = []
    for tpl in templates:
        days = _parse_recurring_days(tpl.recurring_days)
        if days and weekday_num not in days:
            continue
        due.append(tpl)

    work_log = _get_or_new_work_log(employee, date)
    existing_by_template = {row.recurring_template: row for row in work_log.tasks
                             if row.task_type == "Recurring" and row.recurring_template}

    changed = False
    for tpl in due:
        row = existing_by_template.get(tpl.name)
        if row:
            # Template may have been edited since this instance was created —
            # keep an untouched (still Pending) instance in sync rather than
            # leaving a stale copy of the old description/project/estimate.
            if row.status == "Pending" and (
                row.description != tpl.description
                or row.project_name != (tpl.project_name or "")
                or row.estimated_time != (tpl.estimated_time or "")
            ):
                row.description = tpl.description
                row.project_name = tpl.project_name or ""
                row.estimated_time = tpl.estimated_time or ""
                changed = True
            continue

        row = work_log.append("tasks", {})
        row.series_id = frappe.generate_hash(length=32)
        row.origin_date = date
        row.description = tpl.description
        row.task_type = "Recurring"
        row.status = "Pending"
        row.estimated_time = tpl.estimated_time or ""
        row.project_name = tpl.project_name or ""
        row.recurring_template = tpl.name
        row.sequence = _next_sequence(work_log) + 1
        changed = True

    # A template that's since been deleted, deactivated, or edited to no
    # longer recur on this weekday shouldn't leave a stale instance behind —
    # but only touch it if the employee hasn't started it yet.
    due_names = {tpl.name for tpl in due}
    for template_name, row in existing_by_template.items():
        if template_name not in due_names and row.status == "Pending":
            work_log.tasks.remove(row)
            changed = True

    if changed:
        _save_work_log(work_log)
        frappe.db.commit()


def _pull_scheduled_backlog(employee, date):
    """Backlog items scheduled for `date` (or an earlier day that was missed) become Pending tasks on
    that day's log, so the check-in plan already has them. Only used before morning check-in, next to
    _ensure_recurring_tasks; idempotent because each pulled item is deleted."""
    # "is set" matters: Frappe compares a Date as ifnull(col, '0001-01-01'), so a plain <= would match every
    # backlog item that has no day at all.
    due = frappe.get_all("Task Backlog Item",
        filters=[["employee", "=", employee], ["scheduled_for", "is", "set"], ["scheduled_for", "<=", date]],
        fields=["name", "description", "project_name", "estimated_time", "remarks", "assigned_by", "start_time"],
        order_by="scheduled_for asc, start_time asc, creation asc")
    if not due:
        return
    work_log = _get_or_new_work_log(employee, date)
    if work_log.morning_submitted or work_log.eod_submitted:
        return
    for item in due:
        row = work_log.append("tasks", {})
        row.series_id = frappe.generate_hash(length=32)
        row.origin_date = date
        row.description = item.description
        row.task_type = "Ad-hoc"
        row.status = "Pending"
        row.project_name = item.project_name or ""
        row.estimated_time = item.estimated_time or ""
        row.remarks = item.remarks or ""
        row.assigned_by = item.assigned_by
        if item.start_time:
            row.start_time = item.start_time
        row.sequence = _next_sequence(work_log) + 1
    _save_work_log(work_log)
    # Delete only after the tasks are saved, so a failed save loses nothing.
    for item in due:
        frappe.delete_doc("Task Backlog Item", item.name, ignore_permissions=True)
    frappe.db.commit()


# ── Recurring task self-service (employee-facing CRUD) ─────────────────────────
# Ownership/validation is enforced by the doctype's own validate()/on_trash()
# (RecurringTaskTemplate._check_ownership) — these wrappers don't re-check it,
# and deliberately don't pass ignore_permissions so Frappe's own if_owner
# doctype permission (already granted to the Employee role) applies too.

@frappe.whitelist()
def get_recurring_tasks():
    """List the current employee's Recurring Task Templates for self-service management."""
    employee = _get_employee()
    rows = frappe.get_all("Recurring Task Template",
        filters={"employee": employee.name},
        fields=["name", "description", "project_name", "estimated_time", "is_active", "recurring_days"],
        order_by="description asc")
    for r in rows:
        raw = (r.recurring_days or "").strip()
        r["days"] = [d.strip() for d in raw.split("\n") if d.strip()] if raw else []
    return rows


@frappe.whitelist(methods=["POST"])
def save_recurring_task(name=None, description="", project_name="", estimated_time="", recurring_days=None, is_active=1):
    """Create or update (upsert by `name`) a self-service Recurring Task Template.
    `recurring_days` arrives as a JSON-encoded list of day names (or a plain list)."""
    employee = _get_employee()
    if not (description or "").strip():
        frappe.throw("Task description cannot be empty.")

    days = json.loads(recurring_days) if isinstance(recurring_days, str) else (recurring_days or [])

    if name:
        doc = frappe.get_doc("Recurring Task Template", name)
        if doc.employee != employee.name:
            frappe.throw("Not authorised to edit this task.", frappe.PermissionError)
    else:
        doc = frappe.new_doc("Recurring Task Template")
        doc.employee = employee.name

    doc.description = description.strip()
    doc.project_name = (project_name or "").strip()
    doc.estimated_time = estimated_time or ""
    doc.recurring_days = "\n".join(days)
    doc.is_active = 1 if str(is_active) in ("1", "true", "True") else 0
    doc.save()
    # Sync today's not-yet-started instance immediately — covers a
    # description/estimate edit, a day-change that drops today, or
    # deactivation — rather than waiting for the employee's next
    # /daily-checkin page load.
    _ensure_recurring_tasks(employee.name, today())
    return {"success": True, "name": doc.name}


@frappe.whitelist(methods=["POST"])
def delete_recurring_task(name):
    employee = _get_employee()
    task_employee = frappe.db.get_value("Recurring Task Template", name, "employee")
    if task_employee != employee.name:
        frappe.throw("Not authorised to delete this task.", frappe.PermissionError)
    # force=True: historical Task Entry rows keep a soft `recurring_template`
    # reference for provenance (spec: sync, not a hard dependency) — without
    # this, Frappe's link-check would block deleting any template that's
    # ever produced a task.
    frappe.delete_doc("Recurring Task Template", name, force=True)
    # Sync today's not-yet-started instance immediately rather than waiting
    # for the employee's next /daily-checkin page load.
    _ensure_recurring_tasks(employee.name, today())
    return {"success": True}


# ── Task Backlog (employee-facing CRUD + Team Leader push + pull-to-today) ─────
# Tasks with no target day. Nothing here is a Task Entry / doesn't count toward
# any day's pending total — it only becomes one (and starts counting) the
# moment it's pulled into today via pull_backlog_item_to_today. All mutating
# calls below pass ignore_permissions=True and rely on
# TaskBacklogItem._check_ownership() (keyed on the `employee` field) as the
# real authorization check — the framework's own if_owner doctype permission
# can't express "Team-Leader-created, employee-owned" items correctly, since
# `owner` on a TL-pushed item is the TL's user, not the employee's.

BACKLOG_STALE_DAYS = 14


@frappe.whitelist()
def get_task_backlog():
    """Current employee's own backlog, oldest first. Each row carries how
    many days it's been sitting there so the UI can flag stale ones —
    anti-rot nudge instead of a forced due date."""
    employee = _get_employee()
    rows = frappe.get_all("Task Backlog Item",
        filters={"employee": employee.name},
        fields=["name", "description", "project_name", "estimated_time", "remarks",
                "assigned_by", "assigned_by_name", "creation", "scheduled_for", "start_time"],
        order_by="creation asc")
    for r in rows:
        r["scheduled_for"] = str(r.scheduled_for) if r.get("scheduled_for") else None
        r["start_time"] = _to_hhmm(r.get("start_time")) or None
        age_days = (getdate(today()) - getdate(r.creation)).days
        r["age_days"] = age_days
        r["is_stale"] = age_days > BACKLOG_STALE_DAYS
    return rows


@frappe.whitelist(methods=["POST"])
def save_backlog_item(name=None, description="", project_name="", estimated_time="", remarks=""):
    """Create or update (upsert by `name`) a self-service backlog item."""
    employee = _get_employee()
    if not (description or "").strip():
        frappe.throw("Task description cannot be empty.")

    if name:
        doc = frappe.get_doc("Task Backlog Item", name)
        if doc.employee != employee.name:
            frappe.throw("Not authorised to edit this task.", frappe.PermissionError)
    else:
        doc = frappe.new_doc("Task Backlog Item")
        doc.employee = employee.name

    doc.description = description.strip()
    doc.project_name = (project_name or "").strip()
    doc.estimated_time = estimated_time or ""
    doc.remarks = remarks or ""
    doc.save(ignore_permissions=True)
    return {"success": True, "name": doc.name}


@frappe.whitelist(methods=["POST"])
def save_backlog_items(items):
    """Persist every not-yet-saved Project/Task row from the /task-backlog
    footer's Project/Task buttons in one batch — same "type several rows,
    Save once" pattern as add_adhoc_tasks on /daily-checkin, so adding a
    dozen backlog items doesn't mean a modal round-trip per item.

    items: JSON list of {client_id, description, project_name, estimated_time}.
    `client_id` is whatever the caller used to identify the row client-side
    and is echoed back so that row can be marked saved.
    """
    items = json.loads(items) if isinstance(items, str) else (items or [])
    if not items:
        return {"success": True, "created": []}

    employee = _get_employee()
    created = []
    for it in items:
        description = (it.get("description") or "").strip()
        if not description:
            continue
        doc = frappe.new_doc("Task Backlog Item")
        doc.employee = employee.name
        doc.description = description
        doc.project_name = (it.get("project_name") or "").strip()
        doc.estimated_time = it.get("estimated_time") or ""
        doc.insert(ignore_permissions=True)
        created.append({"client_id": it.get("client_id"), "name": doc.name})

    return {"success": True, "created": created}


@frappe.whitelist(methods=["POST"])
def delete_backlog_item(name):
    employee = _get_employee()
    item_employee = frappe.db.get_value("Task Backlog Item", name, "employee")
    if item_employee != employee.name:
        frappe.throw("Not authorised to delete this task.", frappe.PermissionError)
    frappe.delete_doc("Task Backlog Item", name, ignore_permissions=True)
    return {"success": True}


@frappe.whitelist(methods=["POST"])
def push_task_to_backlog(assignee_employee, description, project_name=None, estimated_time=None):
    """A Team Leader captures a future task for a team member's backlog —
    the no-date counterpart to assign_task_to_employee. Re-verifies team
    membership server-side exactly like that function does."""
    current_emp = _get_employee()
    team_members = _get_team_members(current_emp.name)
    if assignee_employee not in team_members:
        frappe.throw(f"{assignee_employee} is not on your team.", frappe.PermissionError)

    description = (description or "").strip()
    if not description:
        frappe.throw("Task description cannot be empty.", frappe.ValidationError)

    doc = frappe.new_doc("Task Backlog Item")
    doc.employee = assignee_employee
    doc.description = description
    doc.project_name = project_name
    doc.estimated_time = estimated_time or ""
    doc.assigned_by = current_emp.name

    frappe.flags.in_task_backlog_push = True
    try:
        doc.insert(ignore_permissions=True)
    finally:
        frappe.flags.in_task_backlog_push = False

    return {
        "success": True,
        "name": doc.name,
        "employee_name": frappe.db.get_value("Employee", assignee_employee, "employee_name"),
    }


@frappe.whitelist(methods=["POST"])
def pull_backlog_item_to_today(name):
    """"Add to today" / drag-onto-Today — turns a backlog item into a real
    Task Entry on today's Daily Work Log, then removes the backlog row.
    Only the owning employee can pull their own item (even a Team-Leader-
    pushed one now lives in *their* backlog). Preserves assigned_by so the
    "Assigned by {name}" tag still shows once it lands on /daily-checkin."""
    employee = _get_employee()
    item = frappe.get_doc("Task Backlog Item", name)
    if item.employee != employee.name:
        frappe.throw("Not authorised to pull this task.", frappe.PermissionError)

    date = today()
    log = _get_or_new_work_log(employee.name, date)
    row = log.append("tasks", {})
    row.task_type = "Ad-hoc"
    row.status = "Pending"
    row.description = item.description
    row.project_name = item.project_name
    # Passed through as raw text — Daily Work Log's own validate()/
    # _prepare_tasks() parses estimated_time exactly once on save (same
    # convention as every other ad-hoc/recurring insert in this file).
    row.estimated_time = item.estimated_time or ""
    row.remarks = item.remarks or ""
    row.series_id = frappe.generate_hash(length=32)
    row.origin_date = date
    row.assigned_by = item.assigned_by
    if item.start_time:
        row.start_time = item.start_time
    row.sequence = _next_sequence(log) + 1

    _save_work_log(log)
    frappe.db.commit()

    # Only remove the backlog item once the task has actually landed —
    # a save failure above leaves the backlog item intact instead of
    # silently losing the task.
    frappe.delete_doc("Task Backlog Item", name, ignore_permissions=True)
    frappe.db.commit()

    return {"success": True, "task_name": row.name, "series_id": row.series_id}


@frappe.whitelist()
def get_today_task_summary():
    """Lean read-only preview of today's already-committed tasks, for the
    Task Backlog page's "Today" column. Deliberately lighter than
    get_page_state — no rollover/shift/checkin side effects, this is just a
    glance, not the check-in flow itself."""
    employee = _get_employee()
    work_log = _get_work_log(employee.name, today())
    if not work_log:
        return {"is_checked_in": False, "tasks": []}
    tasks = [_task_entry_dict(row, today()) for row in work_log.tasks]
    return {
        "is_checked_in": bool(work_log.morning_submitted),
        "tasks": [{"description": t["description"], "status": t["status"],
                   "project_name": t.get("project_name"), "assigned_by_name": t.get("assigned_by_name")}
                  for t in tasks],
    }


# ── Additional work self-service (employee-facing CRUD) ────────────────────────
# Independent of Daily Task Log — never reopens or recalculates a submitted
# EOD log's net_hours/working_hours. Ownership/validation is enforced by the
# doctype's own validate()/on_trash() (AdditionalWork._check_ownership) —
# these wrappers don't re-check it, and deliberately don't pass
# ignore_permissions so Frappe's own if_owner doctype permission applies too.

ADDITIONAL_WORK_PAGE_SIZE = 15


@frappe.whitelist()
def get_additional_work(page=0):
    """Paginated list of the current employee's Additional Work entries,
    newest work_date first."""
    employee = _get_employee()
    page = int(page or 0)
    rows = frappe.get_all("Additional Work",
        filters={"employee": employee.name},
        fields=["name", "work_date", "project_name", "hours_spent", "description", "remarks", "login_time", "logout_time", "status"],
        order_by="work_date desc, creation desc",
        start=page * ADDITIONAL_WORK_PAGE_SIZE,
        page_length=ADDITIONAL_WORK_PAGE_SIZE + 1)

    has_more = len(rows) > ADDITIONAL_WORK_PAGE_SIZE
    rows = rows[:ADDITIONAL_WORK_PAGE_SIZE]
    total_hours = sum(r.hours_spent or 0 for r in rows)
    return {"entries": rows, "total_hours": total_hours, "has_more": has_more}





# ── WFH validation ─────────────────────────────────────────────────────────────

def _get_hybrid_office_days():
    """Reads hybrid office days from ST Attendance Settings. Fully dynamic."""
    day_map = {
        "monday": 0, "tuesday": 1, "wednesday": 2,
        "thursday": 3, "friday": 4, "saturday": 5,
    }
    try:
        raw = _get_attendance_settings().get("hybrid_office_days") or "Tuesday\nThursday"
        days = []
        for line in raw.strip().split("\n"):
            d = line.strip().lower()
            if d in day_map:
                days.append(day_map[d])
        return days if days else [1, 3]
    except Exception:
        frappe.log_error(frappe.get_traceback(), "ST Attendance Tracker — hybrid office days lookup failed")
        return [1, 3]


def _wfh_request_exists(employee_name, date):
    """True if a draft/submitted Attendance Request (reason=WFH) covers this date."""
    return bool(frappe.db.exists("Attendance Request", {
        "employee": employee_name,
        "from_date": ["<=", date],
        "to_date":   [">=", date],
        "reason":    "Work From Home",
        "docstatus": ["in", [0, 1]],
    }))


@frappe.whitelist()
def validate_wfh_request():
    """
    Check if logged-in employee has a saved/submitted Attendance Request
    with reason = Work From Home for today.
    """
    employee = frappe.db.get_value(
        "Employee", {"user_id": frappe.session.user, "status": "Active"}, "name"
    )
    if not employee:
        return {"valid": False, "message": "Employee record not found."}

    date = today()
    weekday_num = getdate(date).weekday()

    if weekday_num == 5:
        return {"valid": True, "message": "Saturday — WFH approved for all."}

    work_type = frappe.db.get_value("Employee", employee, "work_type") or "Office"
    if work_type == "Hybrid":
        hybrid_office_days = _get_hybrid_office_days()
        if weekday_num not in hybrid_office_days:
            return {"valid": True, "message": "Hybrid routine WFH day — no Attendance Request needed."}

    exists = _wfh_request_exists(employee, date)

    if exists:
        return {"valid": True, "message": "WFH request found.", "doc": exists}

    link = (
        f"/app/attendance-request/new-attendance-request-1?"
        f"employee={employee}&from_date={date}&to_date={date}&reason=Work From Home"
    )

    return {
        "valid": False,
        "message": (
            "You have not applied for Work From Home today. "
            "Please submit an Attendance Request with reason 'Work From Home' "
            "before checking in as WFH."
        ),
        "link": link,
    }


# ── Page state ─────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_page_state():
    employee = _get_employee()
    date = today()

    work_log = _get_work_log(employee.name, date)
    morning_done = bool(work_log and work_log.morning_submitted)

    # Safety rollover — only before check-in
    if not morning_done:
        _safety_rollover(employee.name, date)
        _ensure_recurring_tasks(employee.name, date)
        _pull_scheduled_backlog(employee.name, date)
        work_log = _get_work_log(employee.name, date)
        morning_done = bool(work_log and work_log.morning_submitted)

    # Auto-heal checkin if HR deleted it
    if morning_done:
        checkin_today = frappe.db.exists("Employee Checkin", {
            "employee": employee.name,
            "log_type": "IN",
            "device_id": "ST Daily Checkin",
            "time": ["between", [
                frappe.utils.get_datetime(date + " 00:00:00"),
                frappe.utils.get_datetime(date + " 23:59:59"),
            ]],
        })
        if not checkin_today:
            _make_checkin(employee.name, "IN", work_log.login_time)

    eod_done = bool(work_log and work_log.eod_submitted)

    tasks = []
    if work_log:
        tasks = [_task_entry_dict(row, date) for row in work_log.tasks]
        for t in tasks:
            t["days_pending"] = (
                (getdate(date) - getdate(t["origin_date"])).days if t["is_carried"] else 0
            )

    login_time_val = work_log.login_time if morning_done else ""

    # Fetch active employee shift & leave status
    shift_info = None
    try:
        from hrms.hr.doctype.shift_assignment.shift_assignment import get_employee_shift
        shift_details = get_employee_shift(employee.name, consider_default_shift=True)
        if shift_details:
            shift_info = {
                "name": shift_details.shift_type.name,
                "start_time": _to_hhmm(shift_details.shift_type.start_time),
                "end_time": _to_hhmm(shift_details.shift_type.end_time),
                "start_time_ampm": _to_ampm(shift_details.shift_type.start_time),
                "end_time_ampm": _to_ampm(shift_details.shift_type.end_time),
            }
    except Exception:
        frappe.log_error(frappe.get_traceback(), "ST Attendance Tracker — shift lookup failed")

    leave_today = None
    try:
        leave_today = frappe.db.get_value("Leave Application", {
            "employee": employee.name,
            "from_date": ["<=", date],
            "to_date": [">=", date],
            "status": "Approved",
            "docstatus": 1
        }, "leave_type")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "ST Attendance Tracker — leave lookup failed")

    has_reset_today = bool(work_log and work_log.was_reset_today)

    return {
        "employee":       employee,
        "date":           date,
        "morning_done":   morning_done,
        "eod_done":       eod_done,
        "tasks":          tasks,
        "current_time":   now_datetime().strftime("%H:%M"),
        # FIX: use _to_ampm so single-digit-hour timedeltas (e.g. 9:30:00)
        # don't produce a trailing colon after [:5] slicing
        "login_time":     _to_ampm(login_time_val),
        "is_team_leader": _is_team_leader(employee.name),
        "shift_info":     shift_info,
        "leave_today":    leave_today,
        "has_reset_today": has_reset_today,
        "morning_draft":  None if morning_done else _get_morning_draft(),
    }


# ── Notification jobs (run off the request path) ────────────────────────────────
# Building these emails means base64-encoding every attached image/zip/PDF into
# an Email Queue record — with a multi-megabyte checkout attachment that alone
# can take several seconds, which used to happen twice (HR/TL email + employee
# email) inside the same synchronous check-in/checkout HTTP request. Both
# submit_morning_log and submit_eod_log now enqueue these instead of calling
# them directly, so the employee's checkout finishes as soon as their data is
# saved — the emails still go out, just from a background worker a moment
# later. enqueue_after_commit=True guarantees the worker never reads the
# just-saved Daily Work Log before this request's transaction is durable.

def _send_checkin_notifications(employee_name, date, checkin_action_time):
    employee = frappe.db.get_value("Employee", employee_name,
        ["name", "employee_name", "department"], as_dict=True)
    work_log = _get_work_log(employee_name, date)
    if not employee or not work_log:
        return

    all_tasks = [_task_entry_dict(row, date) for row in work_log.tasks]
    detail_rows = [
        ("Employee", employee.employee_name),
        ("Date", _format_email_date(date)),
        ("Login Time", _to_ampm(work_log.login_time)),
        ("Checked-In At", _format_action_timestamp(checkin_action_time)),
        ("Work Location", work_log.work_location),
        ("Half-Day Session", work_log.half_day_session),
        ("Status", "Late Check-in" if work_log.is_late else None),
    ]
    _notify_hr_and_team_leader(
        employee.name, employee.employee_name, "checkin",
        detail_rows, tasks=all_tasks, department=employee.department,
    )


def _send_eod_notifications(employee_name, date, checkout_action_time, is_late_checkout):
    employee = frappe.db.get_value("Employee", employee_name,
        ["name", "employee_name", "department"], as_dict=True)
    work_log = _get_work_log(employee_name, date)
    if not employee or not work_log:
        return

    all_tasks = [_task_entry_dict(row, date) for row in work_log.tasks]
    _attach_task_files(all_tasks)

    done_count = sum(1 for t in all_tasks if t.status == "Done")
    total_count = len(all_tasks)
    total_actual_hours = sum(float(t.get("actual_time") or 0.0) for t in all_tasks)
    working_hours_str = _format_hours(total_actual_hours) or "0h"

    lunch_from_hm = _to_ampm(work_log.lunch_from) if work_log.lunch_from else None
    lunch_to_hm = _to_ampm(work_log.lunch_to) if work_log.lunch_to else None
    lunch_break = f"{lunch_from_hm} – {lunch_to_hm}" if lunch_from_hm and lunch_to_hm else None

    detail_rows = [
        ("Employee", employee.employee_name),
        ("Date", _format_email_date(date)),
        ("Login Time", _to_ampm(work_log.login_time) if work_log.login_time else None),
        ("Logout Time", _to_ampm(work_log.logout_time)),
        ("Checked-Out At", _format_action_timestamp(checkout_action_time)),
        ("Lunch Break", lunch_break),
        ("Work Location", work_log.work_location),
        ("Net Working Hours", work_log.net_hours),
        ("Total Task Hours", working_hours_str),
        ("Half-Day Session", work_log.half_day_session),
        ("Tasks Completed", f"{done_count}/{total_count}"),
    ]
    _notify_hr_and_team_leader(
        employee.name, employee.employee_name, "checkout",
        detail_rows, tasks=all_tasks, department=employee.department,
    )

# ── Check-ins made outside the web page (Hermes Telegram bot) ──────────────────
# The bot creates a bare Employee Checkin as its own agent user instead of
# calling submit_morning_log / submit_eod_log, so none of the emails the web
# page sends would fire. This hook sends the same HR / Team Leader / employee
# emails for those rows. Rows from the web page carry device_id "ST Daily
# Checkin" and already email through their own path.

BOT_AGENT_ROLE = "ST Task Assignment Agent"
_BOT_NOTIFY_TTL = 3 * 24 * 60 * 60


def notify_external_checkin(doc, method=None):
    """Employee Checkin after_insert: email for check-ins made by the bot."""
    if frappe.flags.in_test or frappe.flags.in_import or frappe.flags.in_migrate:
        return
    if doc.device_id in (CHECKIN_DEVICE_ID, LUNCH_DEVICE_ID) or doc.log_type not in ("IN", "OUT"):
        return
    if BOT_AGENT_ROLE not in frappe.get_roles(doc.owner or frappe.session.user):
        return

    day = frappe.utils.getdate(doc.time)
    # Once per employee, day and direction, so a repeated bot tap or retry cannot double-send.
    marker = f"st_bot_checkin_mail:{doc.employee}:{day}:{doc.log_type}"
    # Atomic SET NX: a get-then-set pair misses in Frappe's request-local cache and races across workers.
    cache = frappe.cache()
    if not cache.set(cache.make_key(marker), 1, nx=True, ex=_BOT_NOTIFY_TTL):
        return

    frappe.enqueue(
        _send_external_checkin_notifications,
        queue="short",
        enqueue_after_commit=True,
        employee_name=doc.employee,
        log_type=doc.log_type,
        action_time=str(doc.time),
    )


def _send_external_checkin_notifications(employee_name, log_type, action_time):
    employee = frappe.db.get_value(
        "Employee", employee_name, ["name", "employee_name", "department"], as_dict=True
    )
    if not employee:
        return
    when = frappe.utils.get_datetime(action_time)
    date = str(when.date())
    time_of_day = when.time().replace(microsecond=0)
    work_log = _get_work_log(employee_name, date)

    if log_type == "IN":
        # A web check-in the same day has already emailed everyone.
        if work_log and work_log.morning_submitted:
            return
        detail_rows = [
            ("Employee", employee.employee_name),
            ("Date", _format_email_date(date)),
            ("Login Time", _to_ampm(time_of_day)),
            ("Checked-In At", _format_action_timestamp(when)),
            ("Checked in via", "Telegram bot"),
        ]
        _notify_hr_and_team_leader(
            employee.name, employee.employee_name, "checkin",
            detail_rows, tasks=[], department=employee.department,
        )
    else:
        if work_log and work_log.eod_submitted:
            return
        detail_rows = [
            ("Employee", employee.employee_name),
            ("Date", _format_email_date(date)),
            ("Logout Time", _to_ampm(time_of_day)),
            ("Checked-Out At", _format_action_timestamp(when)),
            ("Checked out via", "Telegram bot"),
        ]
        _notify_hr_and_team_leader(
            employee.name, employee.employee_name, "checkout",
            detail_rows, tasks=[], department=employee.department,
        )



# ── Morning plan draft ─────────────────────────────────────────────────────────
# Tasks typed into the check-in form exist only in that browser until "Check in" is pressed, so a
# second device (or a closed laptop) shows nothing. The page saves its unsent plan here instead.
# Stored per user in Frappe's user defaults, like the design preference (ui_shell.py): durable, no
# schema change. The client owns the shape of `data`; the server only bounds and scopes it.

MORNING_DRAFT_KEY = "st_morning_draft"
MORNING_DRAFT_KINDS = ("calendar", "classic")
MORNING_DRAFT_MAX_CHARS = 20000


def _get_morning_draft(user=None):
    """Today's saved plan draft as {"kind", "data"}, or None (also None for a draft from another day)."""
    raw = frappe.defaults.get_user_default(MORNING_DRAFT_KEY, user or frappe.session.user)
    if not raw:
        return None
    try:
        draft = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(draft, dict) or draft.get("date") != today():
        return None
    return {"kind": draft.get("kind"), "data": draft.get("data")}


def _clear_morning_draft(user=None):
    frappe.defaults.clear_default(MORNING_DRAFT_KEY, parent=user or frappe.session.user)


@frappe.whitelist(methods=["POST"])
def save_morning_draft(kind, data=None):
    """Save (or, with empty `data`, clear) the caller's unsent check-in plan for today.

    kind: "calendar" or "classic", which page wrote it; a page only restores its own kind.
    data: JSON text the page can read back. Ignored once the employee has checked in.
    """
    employee = _get_employee()
    if kind not in MORNING_DRAFT_KINDS:
        frappe.throw("Unknown draft type.", frappe.ValidationError)

    work_log = _get_work_log(employee.name, today())
    if work_log and work_log.morning_submitted:
        return {"saved": False}

    if data in (None, "", "null"):
        _clear_morning_draft()
        frappe.db.commit()
        return {"saved": False}

    text = data if isinstance(data, str) else json.dumps(data)
    if len(text) > MORNING_DRAFT_MAX_CHARS:
        frappe.throw("That plan is too long to save as a draft.", frappe.ValidationError)
    try:
        parsed = json.loads(text)
    except ValueError:
        frappe.throw("Draft is not valid JSON.", frappe.ValidationError)
    if not isinstance(parsed, (dict, list)):
        frappe.throw("Draft is not valid JSON.", frappe.ValidationError)

    frappe.defaults.set_user_default(
        MORNING_DRAFT_KEY, json.dumps({"date": today(), "kind": kind, "data": parsed}), frappe.session.user
    )
    frappe.db.commit()
    return {"saved": True}


# ── Morning submit ─────────────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def submit_morning_log(new_tasks, login_time=None, carried_updates=None, work_location=None, half_day_session=None):
    """
    new_tasks:        JSON list of {description, estimated_time}
    login_time:       optional HH:MM override
    carried_updates:  JSON list of {name, description, estimated_time} edits
    half_day_session: "First Half"/"Second Half", only kept if employee is
                      actually on approved half-day leave today
    """
    checkin_action_time = now_datetime()
    employee = _get_employee()
    # Lock employee record to serialize check-in processing
    frappe.db.sql("select name from `tabEmployee` where name = %s for update", (employee.name,))

    date = today()

    # Safety rollover to catch and carry forward any pending tasks from previous days
    _safety_rollover(employee.name, date)
    _ensure_recurring_tasks(employee.name, date)
    _pull_scheduled_backlog(employee.name, date)

    work_log = _get_or_new_work_log(employee.name, date)
    if work_log.morning_submitted:
        frappe.throw("You have already checked in today.")

    unclosed_day = _get_unclosed_previous_day(employee.name, date)
    if unclosed_day:
        frappe.throw(
            "You did not check out on {0}. Complete that checkout before you check in today. "
            "If the checkout window has closed, request HR approval from the Today page.".format(
                formatdate(unclosed_day.date)
            ),
            title="Checkout pending",
        )

    # ── WFH Validation ──────────────────────────────────────────────────
    work_location = (work_location or "Office").strip()
    weekday_num = getdate(date).weekday()
    is_saturday = weekday_num == 5

    work_type = frappe.db.get_value("Employee", employee.name, "work_type") or "Office"
    is_hybrid_routine_day = False
    if work_type == "Hybrid" and not is_saturday:
        hybrid_office_days = _get_hybrid_office_days()
        is_hybrid_routine_day = weekday_num not in hybrid_office_days

    if work_location == "WFH" and not is_saturday and not is_hybrid_routine_day:
        wfh_exists = frappe.db.exists("Attendance Request", {
            "employee": employee.name,
            "from_date": ["<=", date],
            "to_date":   [">=", date],
            "reason":    "Work From Home",
            "docstatus": ["in", [0, 1]],
        })
        if not wfh_exists:
            frappe.throw(
                "You have not applied for Work From Home today. "
                "Please submit an Attendance Request before checking in as WFH.",
                title="WFH Not Applied"
            )

    tasks   = json.loads(new_tasks)       if isinstance(new_tasks, str)       else (new_tasks or [])
    carried = json.loads(carried_updates) if isinstance(carried_updates, str) else (carried_updates or [])

    # Count total tasks (new + already in DB from rollover)
    carried_count = len(work_log.tasks)

    if not tasks and carried_count == 0:
        frappe.throw("Please add at least one planned task before checking in.")

    # Update carried task descriptions/estimates if employee edited them
    rows_by_name = {row.name: row for row in work_log.tasks if row.name}
    for c in carried:
        task_name = c.get("name")
        if not task_name:
            continue
        row = _resolve_task_row(rows_by_name, task_name, work_log, employee.name, "submit_morning_log")
        if (c.get("description") or "").strip():
            row.description = c["description"].strip()
        if c.get("estimated_time") is not None:
            # Raw text ("2h 15m") is passed through as-is — Daily Work Log's
            # own validate()/_prepare_tasks() parses every row's
            # estimated_time/actual_time exactly once on save. Parsing here
            # too would feed that second pass an already-numeric value,
            # which parse_duration_to_hours treats as bare minutes (its
            # documented convention for unit-less input) and divides by 60.
            row.estimated_time = c.get("estimated_time", "")
        if c.get("project_name") is not None:
            row.project_name = c.get("project_name", "").strip()

    # Insert new tasks, preserving the order the employee entered them in
    sequence_base = _next_sequence(work_log)
    new_rows_with_attachments = []
    for i, t in enumerate(tasks):
        desc = (t.get("description") or "").strip()
        if not desc:
            continue
        row = work_log.append("tasks", {})
        row.series_id = frappe.generate_hash(length=32)
        row.origin_date = date
        row.description = desc
        row.task_type = "Planned"
        row.status = "Pending"
        row.estimated_time = t.get("estimated_time", "")
        row.project_name = (t.get("project_name") or "").strip()
        row.sequence = sequence_base + i + 1
        new_rows_with_attachments.append((row, t.get("attachment_names")))

    actual_login = login_time or now_datetime().strftime("%H:%M:%S")
    if re.fullmatch(r"\d{2}:\d{2}", str(actual_login)):
        actual_login = str(actual_login) + ":00"

    work_log.login_time = actual_login
    work_log.work_location = work_location
    work_log.half_day_session = _resolve_half_day_session(employee.name, date, half_day_session)
    work_log.morning_submitted = 1
    _save_work_log(work_log)

    for row, attachment_names in new_rows_with_attachments:
        _reparent_attachments(attachment_names, row.name)

    _make_checkin(employee.name, "IN", actual_login)
    frappe.db.commit()

    frappe.enqueue(
        _send_checkin_notifications,
        queue="short",
        enqueue_after_commit=True,
        employee_name=employee.name,
        date=date,
        checkin_action_time=checkin_action_time,
    )

    _clear_morning_draft()
    frappe.db.commit()

    return {"success": True, "login_time": _to_ampm(actual_login)}


# ── EOD submit ─────────────────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def submit_eod_log(lunch_from, lunch_to, logout_time, task_updates, adhoc_tasks):
    checkout_action_time = now_datetime()
    employee = _get_employee()
    # Lock employee record to serialize checkout/EOD processing
    frappe.db.sql("select name from `tabEmployee` where name = %s for update", (employee.name,))

    date = _resolve_active_checkin_date(employee.name)
    is_late_checkout = str(date) != str(today())

    if not isinstance(date, str):
        date = frappe.utils.getdate(date).strftime("%Y-%m-%d")

    work_log = _get_or_new_work_log(employee.name, date)
    if work_log.eod_submitted:
        frappe.throw(f"You have already checked out for {date}.")
    _assert_checkout_allowed(work_log)

    if is_late_checkout:
        # Approved late checkout: "now" is days after the fact, so the employee states the real time.
        logout_time = _resolve_late_logout_time(logout_time, work_log)
    elif not frappe.flags.in_test:
        logout_time = now_datetime().strftime("%H:%M:%S")
    elif not logout_time:
        frappe.throw("Logout time is required.")

    updates = json.loads(task_updates) if isinstance(task_updates, str) else task_updates
    adhocs  = json.loads(adhoc_tasks)  if isinstance(adhoc_tasks, str)  else adhoc_tasks

    # Update existing task statuses + actual time
    rows_by_name = {row.name: row for row in work_log.tasks if row.name}
    newly_done_series = []
    for t in updates:
        name = t.get("name")
        if not name:
            continue
        row = _resolve_task_row(rows_by_name, name, work_log, employee.name, "submit_eod_log")
        status = t.get("status", "Pending")
        actual_time = t.get("actual_time", "")
        if status == "Done" and not _parse_time_to_hours(actual_time):
            frappe.throw(f"Please provide time taken for task completion for the completed task: '{t.get('description') or name}'")
        # Entering time taken without touching the status dropdown means
        # they've started it — reflect that instead of leaving it Pending.
        if status == "Pending" and _parse_time_to_hours(actual_time):
            status = "In Progress"
        # Employee explicitly declined to carry an unfinished task forward —
        # drop it instead of letting _rollover_pending_tasks pick it up.
        if status in ("Pending", "In Progress") and not t.get("carry_forward", True):
            status = "Dropped"

        row.status = status
        # "Repeat next day" is only for finished tasks: take the checkout's choice for a Done task,
        # and never leave the flag on one that is not Done. Recurring tasks have their own schedule.
        if row.task_type == "Recurring" or status != "Done":
            row.repeat_next_day = 0
        elif "repeat_next_day" in t:
            row.repeat_next_day = 1 if cint(t.get("repeat_next_day")) else 0
        # Raw text passed through as-is — see the matching comment above on
        # the carried-task estimated_time assignment: Daily Work Log's own
        # validate() parses every row's actual_time exactly once on save,
        # and pre-parsing here too would double it down to bare minutes.
        row.actual_time = actual_time
        row.remarks = t.get("remarks", "")
        # Allow description edit during EOD
        if (t.get("description") or "").strip():
            row.description = t["description"].strip()

        if status == "Done":
            newly_done_series.append(row.series_id)

    # Insert ad-hoc tasks, preserving the order the employee entered them in.
    # A task already persisted mid-day via add_adhoc_tasks() (the footer
    # "Save" button) carries a series_id that already exists on this work
    # log — update that row in place instead of appending a duplicate.
    existing_by_series = {row.series_id: row for row in work_log.tasks if row.series_id}
    sequence_base = _next_sequence(work_log)
    new_rows_with_attachments = []
    appended = 0
    for t in adhocs:
        desc = (t.get("description") or "").strip()
        if not desc:
            continue
        row = existing_by_series.get(t.get("series_id"))
        if not row:
            row = work_log.append("tasks", {})
            row.series_id = frappe.generate_hash(length=32)
            row.origin_date = date
            appended += 1
            row.sequence = sequence_base + appended
            new_rows_with_attachments.append((row, t.get("attachment_names")))
        row.description = desc
        row.task_type = "Ad-hoc"
        row.status = t.get("status", "Done")
        row.estimated_time = t.get("estimated_time", "")
        row.actual_time = t.get("actual_time", "")
        row.remarks = t.get("remarks", "")
        row.project_name = (t.get("project_name") or "").strip()
        # "Repeat next day" for a task added after check-in: same rule as the task updates above
        # (Done only; a missing key leaves a flag set earlier, e.g. from the calendar page).
        if row.status != "Done":
            row.repeat_next_day = 0
        elif "repeat_next_day" in t:
            row.repeat_next_day = 1 if cint(t.get("repeat_next_day")) else 0

    login_time_raw = work_log.login_time or ""
    half_day_session = work_log.half_day_session or ""
    work_location = work_log.work_location or ""

    # net_hours is (re)computed by DailyWorkLog.validate() on save — no need
    # to pre-compute it here (the old two-doc model did, but its result was
    # always immediately overwritten by the log doc's own validate() anyway).
    work_log.lunch_from = lunch_from or ""
    work_log.lunch_to = lunch_to or ""
    work_log.logout_time = logout_time
    work_log.eod_submitted = 1

    _save_work_log(work_log)
    net_hours = work_log.net_hours or ""

    for row, attachment_names in new_rows_with_attachments:
        _reparent_attachments(attachment_names, row.name)

    frappe.db.commit()

    pending_count = sum(1 for r in work_log.tasks if r.status in ("Pending", "In Progress"))

    return {
        "success":       True,
        "pending_count": pending_count,
        "net_hours":     net_hours,
        "logout_time":   _to_ampm(logout_time),
    }


@frappe.whitelist(methods=["POST"])
def autosave_eod_progress(task_updates=None, lunch_from=None, lunch_to=None):
    """Periodic draft save of in-progress checkout edits (status, actual
    time, remarks, lunch) so they survive a refresh or a switch to another
    device even before the employee clicks "Check out & submit". Never
    flips eod_submitted and never runs any of submit_eod_log's once-per-day
    finalization side effects (checkin record, rollover, notifications) —
    this is a draft save, not a submission.

    task_updates: JSON list of {name, status, actual_time, remarks}. Entries
    with no `name` are skipped — a not-yet-persisted ad-hoc row has nothing
    to attach to until add_adhoc_tasks()/saveAllAdhocTasks() gives it one.
    """
    employee = _get_employee()
    # Lock employee record to serialize with check-in/checkout processing
    frappe.db.sql("select name from `tabEmployee` where name = %s for update", (employee.name,))

    date = _resolve_active_checkin_date(employee.name)
    if not isinstance(date, str):
        date = frappe.utils.getdate(date).strftime("%Y-%m-%d")

    work_log = _get_or_new_work_log(employee.name, date)
    if not work_log.morning_submitted:
        frappe.throw("Please check in before saving progress.")
    if work_log.eod_submitted:
        frappe.throw(f"You have already checked out for {date}.")
    _assert_checkout_allowed(work_log)

    updates = json.loads(task_updates) if isinstance(task_updates, str) else (task_updates or [])
    rows_by_name = {row.name: row for row in work_log.tasks if row.name}
    for t in updates:
        name = t.get("name")
        if not name:
            continue
        row = _resolve_task_row(rows_by_name, name, work_log, employee.name, "autosave_eod_progress")
        status = t.get("status")
        if status:
            row.status = status
        # Raw text passed through as-is, same as submit_eod_log — parsed
        # exactly once by DailyWorkLog._prepare_tasks() on save.
        row.actual_time = t.get("actual_time", row.actual_time)
        row.remarks = t.get("remarks", row.remarks)

    if lunch_from is not None:
        work_log.lunch_from = lunch_from or ""
    if lunch_to is not None:
        work_log.lunch_to = lunch_to or ""

    frappe.flags.in_eod_draft = True
    try:
        _save_work_log(work_log)
    finally:
        frappe.flags.in_eod_draft = False

    frappe.db.commit()

    return {"success": True, "server_time": now_datetime().isoformat()}


@frappe.whitelist(methods=["POST"])
def add_adhoc_tasks(tasks):
    """Persist every not-yet-saved mid-day ad-hoc task/project in one batch
    (the single "Save" button in the /daily-checkin footer), instead of
    waiting for checkout. One button for the whole page rather than one per
    row — with 20-25 tasks added in a day, a per-row Save would mean
    clicking Save that many times. Lets the employee's Team Leader see them
    on /team-dashboard in real time via publish_realtime, rather than only
    after EOD. submit_eod_log() later updates these same rows (matched by
    series_id) instead of appending duplicates.

    tasks: JSON list of {client_id, description, project_name,
           estimated_time, remarks, attachment_names}. `client_id` is
           whatever the caller used to identify the row (e.g. its DOM id)
           and is echoed back in the result so it can mark that row saved.
    """
    tasks = json.loads(tasks) if isinstance(tasks, str) else (tasks or [])
    if not tasks:
        return {"success": True, "created": []}

    employee = _get_employee()
    # Lock employee record to serialize with check-in/checkout processing
    frappe.db.sql("select name from `tabEmployee` where name = %s for update", (employee.name,))

    date = _resolve_active_checkin_date(employee.name)
    work_log = _get_or_new_work_log(employee.name, date)
    if not work_log.morning_submitted:
        frappe.throw("Please check in before adding tasks.")
    if work_log.eod_submitted:
        frappe.throw(f"You have already checked out for {date}.")

    sequence_base = _next_sequence(work_log)
    created = []
    for i, t in enumerate(tasks):
        description = (t.get("description") or "").strip()
        if not description:
            continue
        attachment_names = t.get("attachment_names")
        if isinstance(attachment_names, str):
            attachment_names = json.loads(attachment_names) if attachment_names else []

        row = work_log.append("tasks", {})
        row.series_id = frappe.generate_hash(length=32)
        row.origin_date = date
        row.description = description
        row.task_type = "Ad-hoc"
        row.status = "Pending"
        row.estimated_time = t.get("estimated_time") or ""
        row.remarks = t.get("remarks") or ""
        row.project_name = (t.get("project_name") or "").strip()
        row.sequence = sequence_base + i + 1
        created.append({"client_id": t.get("client_id"), "row": row,
                         "attachment_names": attachment_names, "description": description})

    if not created:
        return {"success": True, "created": []}

    _save_work_log(work_log)

    result = []
    for c in created:
        _reparent_attachments(c["attachment_names"], c["row"].name)
        result.append({"client_id": c["client_id"], "series_id": c["row"].series_id,
                        "task_name": c["row"].name, "description": c["description"]})

    frappe.db.commit()

    try:
        for tl_user in _get_team_leader_emails(employee.name):
            frappe.publish_realtime(
                "st_task_added",
                {
                    "employee": employee.name,
                    "employee_name": employee.employee_name,
                    "count": len(result),
                    "descriptions": [c["description"] for c in result],
                },
                user=tl_user,
            )
    except Exception:
        frappe.log_error(title="st_task_added realtime publish failed")

    return {"success": True, "created": result}


# ── Team dashboard ─────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_team_dashboard(date=None):
    """Team Leader sees only their direct reports (reports_to = current employee)."""
    employee = _get_employee()
    date = date or today()

    team_names = _get_team_members(employee.name)
    if not team_names:
        frappe.throw("Access denied. You are not a Team Leader.", frappe.PermissionError)

    team = frappe.get_all("Employee", filters={
        "name": ["in", team_names],
    }, fields=["name", "employee_name", "department", "designation", "user_id"])

    return _build_team_data(team, date)


# ── Management dashboard ───────────────────────────────────────────────────────

@frappe.whitelist()
def get_management_dashboard(date=None):
    """HR Manager or Management sees all departments with all employees."""
    if not ({"HR Manager", "Management"} & set(frappe.get_roles(frappe.session.user))):
        frappe.throw("Access denied. HR Manager or Management role required.", frappe.PermissionError)

    date = date or today()

    departments = frappe.get_all(
        "Department", filters={"is_group": 0}, pluck="name", order_by="name asc"
    )

    result = []
    for dept in departments:
        employees = frappe.get_all("Employee", filters={
            "department": dept, "status": "Active",
        }, fields=["name", "employee_name", "department", "designation", "user_id"])

        if not employees:
            continue

        dept_data = _build_team_data(employees, date)
        dept_data["department"] = dept
        result.append(dept_data)

    all_employees = frappe.get_all("Employee",
        filters={"status": "Active"},
        fields=["name", "employee_name", "department"],
    )
    checked_in = frappe.get_all("Daily Work Log", filters={
        "date": date, "morning_submitted": 1,
    }, pluck="employee")
    eod_done = frappe.get_all("Daily Work Log", filters={
        "date": date, "eod_submitted": 1,
    }, pluck="employee")
    on_leave = _get_employees_on_leave([e.name for e in all_employees], date)

    # Calculate rankings based on completed EOD logs for this date
    def _parse_time_to_minutes(time_str):
        if not time_str:
            return 0
        s = str(time_str).strip().lower()
        if 'h' in s or 'm' in s:
            h = m = 0
            try:
                if 'h' in s:
                    parts = s.split('h')
                    h = int(float(parts[0].strip()))
                    s = parts[1]
                if 'm' in s:
                    parts = s.split('m')
                    m = int(float(parts[0].strip()))
                return h * 60 + m
            except Exception:
                pass
        hhmm = _to_hhmm(time_str)
        if not hhmm:
            return 0
        try:
            parts = hhmm.split(":")
            return int(parts[0]) * 60 + int(parts[1])
        except Exception:
            return 0

    eod_logs = frappe.db.get_all("Daily Work Log", filters={
        "date": date,
        "eod_submitted": 1,
    }, fields=["employee", "employee_name", "net_hours", "logout_time"])

    checkin_logs = frappe.db.get_all("Daily Work Log", filters={
        "date": date,
        "morning_submitted": 1,
    }, fields=["employee", "login_time"])

    login_map = {c.employee: c.login_time for c in checkin_logs}

    half_day_leaves = frappe.db.get_all("Leave Application", filters={
        "from_date": ["<=", date], "to_date": [">=", date],
        "status": "Approved", "half_day": 1, "docstatus": 1
    }, pluck="employee")

    rankings = []
    for log in eod_logs:
        net_min = _parse_time_to_minutes(log.net_hours)
        login_val = login_map.get(log.employee)
        rankings.append({
            "employee": log.employee,
            "employee_name": log.employee_name,
            "net_hours": log.net_hours or "00:00",
            "net_minutes": net_min,
            "login_time": _to_ampm(login_val) if login_val else "—",
            "logout_time": _to_ampm(log.logout_time) if log.logout_time else "—",
            "is_half_day": log.employee in half_day_leaves
        })

    # Sort rankings descending by net minutes
    rankings.sort(key=lambda x: x["net_minutes"], reverse=True)

    standard_workday_hours = _get_attendance_settings().get("standard_workday_hours") or 8

    return {
        "departments": result,
        "date": date,
        "rankings": rankings,
        "standard_workday_minutes": int(standard_workday_hours * 60),
        "summary": {
            "total":      len(all_employees),
            "checked_in": len(set(checked_in)),
            "eod_done":   len(set(eod_done)),
            "on_leave":   len(set(on_leave)),
            "missing":    len([e for e in all_employees
                               if e.name not in checked_in
                               and e.name not in on_leave]),
        }
    }


@frappe.whitelist()
def get_employee_task_detail(employee_name, date=None):
    """Full task detail for one employee. HR Manager, Management, or their Team Leader."""
    if not ({"HR Manager", "Management"} & set(frappe.get_roles(frappe.session.user))):
        current_emp = _get_employee()
        if employee_name not in _get_team_members(current_emp.name):
            frappe.throw("Access denied.", frappe.PermissionError)

    date = date or today()

    emp = frappe.db.get_value("Employee", employee_name,
        ["name", "employee_name", "department", "designation"], as_dict=True)

    work_log = _get_work_log(employee_name, date)

    morning_log = None
    eod_log = None
    tasks = []
    if work_log:
        if work_log.morning_submitted:
            morning_log = frappe._dict({
                "name": work_log.name, "login_time": work_log.login_time, "is_late": work_log.is_late,
            })
        if work_log.eod_submitted:
            eod_log = frappe._dict({
                "name": work_log.name, "logout_time": work_log.logout_time,
                "lunch_from": work_log.lunch_from, "lunch_to": work_log.lunch_to,
                "net_hours": work_log.net_hours,
            })
        tasks = [_task_entry_dict(row, date) for row in work_log.tasks]
        _attach_task_files(tasks)

    additional_work = frappe.get_all(
        "Additional Work",
        filters={"employee": employee_name, "work_date": date},
        fields=["name", "login_time", "logout_time", "project_name", "hours_spent", "status", "description", "remarks"],
        order_by="login_time asc",
    )
    for entry in additional_work:
        entry["login_time"] = _to_hhmm(entry.get("login_time")) or None
        entry["logout_time"] = _to_hhmm(entry.get("logout_time")) or None

    return {
        "employee":    emp,
        "date":        date,
        "morning_log": morning_log,
        "eod_log":     eod_log,
        "tasks":       tasks,
        "additional_work": additional_work,
    }


# ── Employee history ───────────────────────────────────────────────────────────

@frappe.whitelist()
def get_my_history(page=0):
    """Past attendance summary for logged-in employee. 15 records per page."""
    employee = _get_employee()
    page   = int(page)
    limit  = 15
    offset = page * limit

    logs = frappe.get_all("Daily Work Log", filters={
        "employee": employee.name,
        "morning_submitted": 1,
        "date": ["<=", today()],
    }, fields=["date", "login_time", "logout_time", "net_hours",
               "lunch_from", "lunch_to", "is_late", "eod_submitted"],
        order_by="date desc", start=offset, page_length=limit)

    for log in logs:
        log["eod_done"] = 1 if log.get("eod_submitted") else 0

    dates = [log.date for log in logs]
    counts_by_date = {}
    if dates:
        rows = frappe.db.sql("""
            SELECT dwl.date as task_date,
                COUNT(*) as total,
                SUM(CASE WHEN te.status = 'Done' THEN 1 ELSE 0 END) as done
            FROM `tabTask Entry` te
            INNER JOIN `tabDaily Work Log` dwl ON dwl.name = te.parent
            WHERE dwl.employee = %(emp)s AND dwl.date IN %(dates)s
            GROUP BY dwl.date
        """, {"emp": employee.name, "dates": dates}, as_dict=True)
        counts_by_date = {row.task_date: row for row in rows}

    for log in logs:
        counts = counts_by_date.get(log.date)
        log["total_tasks"] = counts.total if counts else 0
        log["done_tasks"]  = counts.done  if counts else 0

    return {
        "logs":     logs,
        "employee": employee,
        "has_more": len(logs) == limit,
    }


@frappe.whitelist()
def get_history_day_detail(date):
    """Full task list for a specific past date."""
    employee = _get_employee()

    work_log = _get_work_log(employee.name, date)

    tasks = []
    morning_log = None
    eod_log = None
    if work_log:
        tasks = [_task_entry_dict(row, date) for row in work_log.tasks]
        if work_log.morning_submitted:
            morning_log = frappe._dict({"login_time": work_log.login_time, "is_late": work_log.is_late})
        if work_log.eod_submitted:
            eod_log = frappe._dict({
                "logout_time": work_log.logout_time, "lunch_from": work_log.lunch_from,
                "lunch_to": work_log.lunch_to, "net_hours": work_log.net_hours,
            })

    return {
        "date":        date,
        "tasks":       tasks,
        "morning_log": morning_log,
        "eod_log":     eod_log,
    }


@frappe.whitelist(methods=["POST"])
def delete_carried_task(name):
    """Delete a carried task. Employee can only delete their own tasks."""
    employee = _get_employee()
    parent = frappe.db.get_value("Task Entry", name, "parent")
    if not parent:
        frappe.throw("Not authorised to delete this task.", frappe.PermissionError)

    work_log = frappe.get_doc("Daily Work Log", parent)
    if work_log.employee != employee.name:
        frappe.throw("Not authorised to delete this task.", frappe.PermissionError)
    if work_log.eod_submitted:
        frappe.throw("Cannot delete tasks after checkout is submitted.", frappe.ValidationError)

    row = next((r for r in work_log.tasks if r.name == name), None)
    if not row:
        frappe.throw("Not authorised to delete this task.", frappe.PermissionError)

    # Clean up the lineage to prevent safety/EOD rollover from resurrecting it
    frappe.db.sql("""
        UPDATE `tabTask Entry`
        SET status = 'Rolled Over'
        WHERE series_id = %s AND status IN ('Pending', 'In Progress')
    """, (row.series_id,))

    work_log.tasks.remove(row)
    _save_work_log(work_log)
    frappe.db.commit()
    return {"success": True}


@frappe.whitelist(methods=["POST"])
def move_task_to_backlog(name):
    """Move an already-existing Task Entry — today's or a carried-forward
    one — into the backlog. The reverse of pull_backlog_item_to_today, for
    the case where a task was added for today but turns out not to belong
    there. Employee can only move their own tasks; same ownership/locked-day
    guard as delete_carried_task."""
    employee = _get_employee()
    parent = frappe.db.get_value("Task Entry", name, "parent")
    if not parent:
        frappe.throw("Not authorised to move this task.", frappe.PermissionError)

    work_log = frappe.get_doc("Daily Work Log", parent)
    if work_log.employee != employee.name:
        frappe.throw("Not authorised to move this task.", frappe.PermissionError)
    if work_log.eod_submitted:
        frappe.throw("Cannot move tasks after checkout is submitted.", frappe.ValidationError)

    row = next((r for r in work_log.tasks if r.name == name), None)
    if not row:
        frappe.throw("Not authorised to move this task.", frappe.PermissionError)
    if row.task_type == "Recurring":
        frappe.throw("Recurring tasks can't be moved to the backlog — deactivate the template instead.")
    if row.status == "Done":
        frappe.throw("Completed tasks can't be moved to the backlog.")

    backlog_doc = frappe.new_doc("Task Backlog Item")
    backlog_doc.employee = employee.name
    backlog_doc.description = row.description
    backlog_doc.project_name = row.project_name
    backlog_doc.estimated_time = _format_hours(row.estimated_time) if row.estimated_time else ""
    backlog_doc.remarks = row.remarks
    backlog_doc.assigned_by = row.assigned_by
    backlog_doc.insert(ignore_permissions=True)

    # Same lineage cleanup delete_carried_task does — without this, safety
    # rollover would resurrect this series tomorrow even though it now
    # lives in the backlog instead of on any Daily Work Log.
    frappe.db.sql("""
        UPDATE `tabTask Entry`
        SET status = 'Rolled Over'
        WHERE series_id = %s AND status IN ('Pending', 'In Progress')
    """, (row.series_id,))

    work_log.tasks.remove(row)
    _save_work_log(work_log)
    frappe.db.commit()
    return {"success": True, "backlog_name": backlog_doc.name}


@frappe.whitelist(methods=["POST"])
def upload_task_attachment(task_name):
    """Attach an uploaded file to an existing task. Employee can only attach
    to their own tasks.

    Bypasses Frappe's generic upload_file endpoint on purpose (confirmed via
    a live 403 during checkout, not assumed): that endpoint's
    check_write_permission() loads Task Entry standalone via
    frappe.get_doc(doctype, name), then calls doc.check_permission(), which
    delegates a child doctype's permission check to its parent via
    has_child_permission(). That delegation reads
    `getattr(child_doc, "parent_doc", child_doc.parent)` to find the parent
    — but every Document/BaseDocument has a `parent_doc` *property*
    (base_document.py) that exists (and returns None) on a standalone-loaded
    child, so getattr never falls through to the intended `.parent` name
    string. The parent doc permission check then runs with no document
    context at all, so an if_owner-gated permission (ours) can never
    resolve to "yes, this employee owns it" — write is always denied. This
    is a core Frappe limitation for if_owner-restricted child doctypes
    accessed this way, not something fixable via DocType permission config.
    `frappe.client.set_value` (used for inline description/remarks edits)
    is unaffected — it resolves the parent doc explicitly before calling
    save(), never hitting this code path.
    """
    employee = _get_employee()
    _assert_task_owner(task_name, employee.name)

    file_obj = frappe.request.files.get("file")
    if not file_obj:
        frappe.throw("No file uploaded.")

    doc = frappe.get_doc({
        "doctype": "File",
        "attached_to_doctype": "Task Entry",
        "attached_to_name": task_name,
        "folder": "Home",
        "file_name": file_obj.filename,
        "is_private": cint(frappe.form_dict.get("is_private", 1)),
        "content": file_obj.stream.read(),
    })
    doc.insert(ignore_permissions=True)
    return doc
@frappe.whitelist(methods=["POST"])
def reparent_additional_work_attachments(additional_work_name, attachment_names):
    """Link uploaded orphan files to the newly inserted Additional Work entry."""
    import json
    if isinstance(attachment_names, str):
        try:
            attachment_names = json.loads(attachment_names)
        except Exception:
            attachment_names = [attachment_names]
    # Check that current user is the owner of this Additional Work entry
    aw = frappe.get_doc("Additional Work", additional_work_name)
    aw._check_ownership()
    _reparent_attachments(attachment_names, additional_work_name, target_doctype="Additional Work")
    return {"status": "ok"}

@frappe.whitelist()
def get_task_attachments(task_name):
    """List files attached to a task. Visible to the owner, their Team Leader, or HR Manager."""
    _assert_task_visible(task_name)
    return get_attachments("Task Entry", task_name)


@frappe.whitelist()
def view_task_attachment(file_name, task_name):
    """Stream a task's attached file. Visible to the owner, their Team Leader, or HR Manager.

    Bypasses Frappe's private-file route on purpose: that route resolves
    access via the Task Entry doctype's permission table (empty — child
    doctypes delegate to their parent), which has no row for Team Lead
    (and can't be given one without granting every Team Lead blanket read
    access to every employee's tasks — has_permission hooks can only deny,
    never grant, beyond that table). This endpoint does the same precise
    "is this employee my report" check _assert_task_visible already does
    elsewhere, then serves the file directly.
    """
    _assert_task_visible(task_name)
    if frappe.db.get_value("File", file_name, "attached_to_name") != task_name:
        frappe.throw("Not authorised to view this file.", frappe.PermissionError)
    file_doc = frappe.get_doc("File", file_name)
    frappe.local.response.filename = file_doc.file_name
    frappe.local.response.filecontent = file_doc.get_content()
    frappe.local.response.type = "download"


@frappe.whitelist(methods=["POST"])
def delete_task_attachment(file_name, task_name):
    """Delete a task's attached file. Employee can only delete their own task's files."""
    employee = _get_employee()
    _assert_task_owner(task_name, employee.name)
    if frappe.db.get_value("File", file_name, "attached_to_name") != task_name:
        frappe.throw("Not authorised to delete this file.", frappe.PermissionError)
    frappe.delete_doc("File", file_name, ignore_permissions=True)
    return {"success": True}


@frappe.whitelist(methods=["POST"])
def delete_carried_project(project_name, task_date):
    """Delete all tasks belonging to a project for a specific date."""
    employee = _get_employee()

    work_log = _get_work_log(employee.name, task_date)
    if not work_log:
        return {"success": True}
    if work_log.eod_submitted:
        frappe.throw("Cannot delete project tasks after checkout is submitted.", frappe.ValidationError)

    matching = [r for r in work_log.tasks if (r.project_name or "") == project_name]
    if not matching:
        return {"success": True}

    for row in matching:
        # Clean up the lineage to prevent safety/EOD rollover from resurrecting it
        frappe.db.sql("""
            UPDATE `tabTask Entry`
            SET status = 'Rolled Over'
            WHERE series_id = %s AND status IN ('Pending', 'In Progress')
        """, (row.series_id,))
        work_log.tasks.remove(row)

    _save_work_log(work_log)
    frappe.db.commit()
    return {"success": True}


@frappe.whitelist(methods=["POST"])
def reset_morning_checkin():
    """Reset morning check-in: revert today's Daily Work Log to its
    pre-checkin state and preserve its Task Entry rows as Pending for
    re-check-in (replaces cancelling a separate Morning Check-In doc under
    the old submit/cancel model — there's only one doc per day now)."""
    employee = _get_employee()

    date = _resolve_active_checkin_date(employee.name)
    if not isinstance(date, str):
        date = frappe.utils.getdate(date).strftime("%Y-%m-%d")

    work_log = _get_work_log(employee.name, date)
    if not work_log or not work_log.morning_submitted:
        frappe.throw(f"You have not checked in on {date}.")
    if work_log.eod_submitted:
        frappe.throw(f"Cannot reset check-in because checkout has already been submitted for {date}.")

    # Delete Employee Checkin created by ST Daily Checkin today
    frappe.db.delete("Employee Checkin", {
        "employee": employee.name,
        "log_type": "IN",
        "device_id": "ST Daily Checkin",
        "time": ["between", [date + " 00:00:00", date + " 23:59:59"]],
    })

    # Revert tasks back to Pending so they are preserved for re-check-in.
    # 'Rolled Over' tasks are excluded: they already have a forward copy on
    # a later date, so reviving them here would leave two live copies.
    for row in work_log.tasks:
        if row.status not in ("Pending", "Rolled Over"):
            row.status = "Pending"

    work_log.morning_submitted = 0
    work_log.login_time = None
    work_log.work_location = None
    work_log.half_day_session = None
    work_log.is_late = 0
    work_log.was_reset_today = 1

    _save_work_log(work_log)
    frappe.db.commit()
    return {"success": True}


@frappe.whitelist(methods=["POST"])
def update_half_day_session(session):
    """
    Retroactively apply a half-day session to an already-submitted Morning
    Check-In — for an employee who checked in normally, then got a half-day
    leave approved later the same day. Reuses the same eligibility check
    as check-in time (_resolve_half_day_session); the page reload after this
    re-derives shift truncation and recommended logout time from the newly
    stored value.
    """
    employee = _get_employee()
    date = today()

    work_log = _get_work_log(employee.name, date)
    if not work_log or not work_log.morning_submitted:
        frappe.throw("You have not checked in today.")
    if work_log.eod_submitted:
        frappe.throw("Cannot change half-day session after checkout is submitted.")

    resolved = _resolve_half_day_session(employee.name, date, session)
    if not resolved:
        frappe.throw("You do not have an approved half-day leave for today.")

    work_log.half_day_session = resolved
    _save_work_log(work_log)
    frappe.db.commit()
    return {"success": True, "half_day_session": resolved}


# ── Shared builder ─────────────────────────────────────────────────────────────

def _build_team_data(employees, date):
    """Build attendance + task data for a list of employees on a given date."""
    if not employees:
        return {"employees": [], "summary": {}}

    emp_names = [e.name for e in employees]

    work_logs = frappe.get_all("Daily Work Log", filters={
        "employee": ["in", emp_names], "date": date,
    }, fields=["name", "employee", "login_time", "is_late", "logout_time",
               "net_hours", "morning_submitted", "eod_submitted"])
    work_log_by_emp = {w.employee: w for w in work_logs}
    parent_to_emp = {w.name: w.employee for w in work_logs}

    on_leave = _get_employees_on_leave(emp_names, date)

    all_tasks = []
    if parent_to_emp:
        raw_tasks = frappe.get_all("Task Entry", filters={
            "parent": ["in", list(parent_to_emp.keys())],
        }, fields=["name", "parent", "description", "status", "project_name",
                   "task_type", "estimated_time", "actual_time", "origin_date",
                   "assigned_by_name"])
        for t in raw_tasks:
            entry = _task_entry_dict(t, date)
            entry["employee"] = parent_to_emp.get(t.parent)
            all_tasks.append(entry)
    _attach_task_files(all_tasks)

    tasks_by_emp = {}
    for t in all_tasks:
        tasks_by_emp.setdefault(t["employee"], []).append(t)

    result_employees = []
    for emp in employees:
        work_log = work_log_by_emp.get(emp.name)
        morning = work_log if work_log and work_log.morning_submitted else None
        eod     = work_log if work_log and work_log.eod_submitted else None
        tasks   = tasks_by_emp.get(emp.name, [])
        done    = sum(1 for t in tasks if t["status"] == "Done")

        # Real attendance data always takes priority over a leave record —
        # an employee on approved half-day leave who checks in for the other
        # half must still show their actual check-in/checkout, not "On leave".
        if eod:
            status = "eod_done"
        elif morning:
            status = "late" if morning.is_late else "checked_in"
        elif emp.name in on_leave:
            status = "leave"
        else:
            status = "missing"

        result_employees.append({
            "name":          emp.name,
            "employee_name": emp.employee_name,
            "department":    emp.department,
            "designation":   emp.get("designation", ""),
            "status":        status,
            # FIX: _to_hhmm prevents trailing-colon from single-digit-hour timedeltas
            "login_time":    _to_ampm(morning.login_time)  if morning and morning.login_time  else "",
            "logout_time":   _to_ampm(eod.logout_time)     if eod     and eod.logout_time     else "",
            "net_hours":     eod.net_hours                  if eod                              else "",
            "is_late":       bool(morning.is_late)          if morning                          else False,
            "tasks":         tasks,
            "done_tasks":    done,
            "total_tasks":   len(tasks),
        })

    checked_in_count = sum(
        1 for e in result_employees
        if e["status"] in ["checked_in", "late", "eod_done"]
    )

    return {
        "employees": result_employees,
        "date": date,
        "summary": {
            "total":      len(employees),
            "checked_in": checked_in_count,
            "missing":    sum(1 for e in result_employees if e["status"] == "missing"),
            "on_leave":   sum(1 for e in result_employees if e["status"] == "leave"),
            "late":       sum(1 for e in result_employees if e["status"] == "late"),
            "eod_done":   sum(1 for w in work_logs if w.eod_submitted),
        }
    }


def _assign_task(assignee_employee, assigned_by_employee, description, project_name=None, estimated_time=None):
    """Shared by assign_task_via_agent (Telegram bot) and
    assign_task_to_employee (Team Dashboard "Assign task"). Appends an
    Ad-hoc task to the assignee's Daily Work Log for today, stamped with
    who assigned it so it can show "Assigned by {name}" wherever the
    employee sees their tasks.

    Saves with ignore_permissions=True rather than going through
    _save_work_log's normal self-service check — this is someone else's
    Daily Work Log by design (the assigner isn't its "own employee"), and
    both callers already do their own explicit authorization (role check +
    team-membership check, or team-membership check) before ever reaching
    here, so the generic per-document permission check would only ever
    incorrectly block a call this function has already vetted.
    """
    description = (description or "").strip()
    if not description:
        frappe.throw("Task description cannot be empty.", frappe.ValidationError)

    date = today()
    log = _get_or_new_work_log(assignee_employee, date)

    row = log.append("tasks", {})
    row.task_type = "Ad-hoc"
    row.status = "Pending"
    row.description = description
    row.project_name = project_name
    # Raw text passed through as-is — Daily Work Log's own validate()/
    # _prepare_tasks() parses every row's estimated_time exactly once on
    # save; pre-parsing here too would double it down to bare minutes
    # (see the matching comments on the other task_updates/carried
    # assignments above).
    row.estimated_time = estimated_time or ""
    row.series_id = frappe.generate_hash(length=32)
    row.origin_date = date
    row.assigned_by = assigned_by_employee
    row.sequence = _next_sequence(log) + 1

    frappe.flags.in_task_assignment = True
    try:
        if log.is_new():
            log.insert(ignore_permissions=True)
        else:
            log.save(ignore_permissions=True)
    finally:
        frappe.flags.in_task_assignment = False
    frappe.db.commit()
    return log, row


@frappe.whitelist()
def assign_task_via_agent(assignee_employee, description, assigned_by_employee, project_name=None, estimated_time=None):
    """
    Intended for the Hermes Agent service user only.
    Allows an AI agent to assign a task to an employee's Daily Work Log on behalf of a team leader (via WhatsApp).
    Always re-verifies that `assignee_employee` is actually a team member of `assigned_by_employee` before assigning,
    as the caller agent is acting as a proxy.
    """
    if "ST Task Assignment Agent" not in frappe.get_roles(frappe.session.user):
        frappe.throw("Not authorised to use the agent task assignment API.", frappe.PermissionError)

    # Re-verify team membership independently of the caller's role, as the agent
    # acts on behalf of the `assigned_by_employee` WhatsApp sender.
    team_members = _get_team_members(assigned_by_employee)
    if assignee_employee not in team_members:
        frappe.throw(f"{assignee_employee} is not a direct report of {assigned_by_employee}.", frappe.PermissionError)

    log, row = _assign_task(assignee_employee, assigned_by_employee, description, project_name, estimated_time)

    emp_data = frappe.db.get_value(
        "Employee", assignee_employee,
        ["employee_name", "cell_number"],
        as_dict=True,
    ) or {}

    return {
        "work_log": log.name,
        "employee": assignee_employee,
        "employee_name": emp_data.get("employee_name") or "",
        "cell_number": emp_data.get("cell_number") or None,
        "date": today(),
    }


@frappe.whitelist()
def assign_task_to_employee(assignee_employee, description, project_name=None, estimated_time=None):
    """A Team Leader assigns a task to one of their own team members,
    directly from the Team Dashboard. Re-verifies server-side that
    `assignee_employee` is actually on the calling user's team (the same
    `_get_team_members` check that gates who shows up on their Team
    Dashboard in the first place) — never trusts the client.
    """
    current_emp = _get_employee()
    team_members = _get_team_members(current_emp.name)
    if assignee_employee not in team_members:
        frappe.throw(f"{assignee_employee} is not on your team.", frappe.PermissionError)

    log, row = _assign_task(assignee_employee, current_emp.name, description, project_name, estimated_time)

    return {
        "success": True,
        "task_name": row.name,
        "series_id": row.series_id,
        "employee_name": frappe.db.get_value("Employee", assignee_employee, "employee_name"),
    }


# ── Telegram credential lookup ─────────────────────────────────────────────────

# Credential issuance is a high-value target (it mints live API secrets), so
# lookups per telegram_id are rate-limited beyond normal usage patterns to
# blunt enumeration/brute-forcing if the calling service account is ever
# compromised. ip_based=False since the caller is a single shared service
# account/IP (Hermes) — the identity that matters here is the telegram_id
# being looked up, not the caller's IP.
#
# frappe.rate_limiter.rate_limit only enforces inside a real bound HTTP
# request (frappe.request); it silently no-ops for a direct/console call
# with no request context. That's fine as long as Hermes-over-HTTP stays
# the only caller. If this is ever called from another whitelisted method,
# a scheduled job, or a console script, re-add explicit throttling that
# doesn't depend on frappe.request — don't assume the decorator alone
# still protects it.
@frappe.whitelist()
@rate_limit(key="telegram_id", limit=10, seconds=300, ip_based=False)
def get_credentials_by_telegram_id(telegram_id):
    """Whitelisted for the Hermes lookup service account only. Looks up the
    Employee with this telegram_id, finds their linked User, generates (or
    reuses) that user's own API key/secret, and returns them so subsequent
    actions execute as that real person under their own ERPNext permissions.
    No password verification - the telegram_id field itself, set by an
    admin, is the trust anchor here."""

    logger = frappe.logger("st_attendance_tracker.telegram_credential_lookup")

    if "Telegram Credential Lookup" not in frappe.get_roles(frappe.session.user):
        logger.warning(
            f"Unauthorised credential lookup by {frappe.session.user} "
            f"for telegram_id={telegram_id}"
        )
        frappe.throw("Not authorised.", frappe.PermissionError)

    # A None/blank id would make the filter below match an Employee that has no
    # telegram_id set (NULL/'') and hand out that person's credentials.
    telegram_id = telegram_id.strip() if isinstance(telegram_id, str) else ""
    if not telegram_id:
        frappe.throw("telegram_id is required.", frappe.ValidationError)

    employee = frappe.db.get_value(
        "Employee", {"telegram_id": telegram_id, "status": "Active"},
        ["name", "user_id", "employee_name"], as_dict=True,
    )
    if not employee or not employee.user_id:
        logger.warning(
            f"No active employee for telegram_id={telegram_id} "
            f"caller={frappe.session.user}"
        )
        frappe.throw(f"No active employee linked to Telegram ID {telegram_id}.",
                     frappe.DoesNotExistError)

    user_doc = frappe.get_doc("User", employee.user_id)
    needs_save = False
    if not user_doc.api_key:
        user_doc.api_key = frappe.generate_hash(length=15)
        needs_save = True
    if user_doc.api_secret:
        # reuse existing secret - fetch decrypted
        api_secret = frappe.utils.password.get_decrypted_password(
            "User", user_doc.name, "api_secret"
        )
    else:
        api_secret = frappe.generate_hash(length=15)
        user_doc.api_secret = api_secret
        needs_save = True
    # Persist whenever either half was generated, otherwise the returned key
    # would never authenticate and would change on every call.
    if needs_save:
        user_doc.save(ignore_permissions=True)
        frappe.db.commit()

    logger.info(
        f"Issued API credentials for employee={employee.name} "
        f"user={employee.user_id} via telegram_id={telegram_id} "
        f"caller={frappe.session.user}"
    )

    return {
        "user": employee.user_id,
        "api_key": user_doc.api_key,
        "api_secret": api_secret,
        "employee": employee.name,
        "employee_name": employee.employee_name,
    }


# ── Late checkout approval ─────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def request_late_checkout_approval(date, reason=None):
    """Employee asks HR to unlock checkout for one of their own locked dates.
    Identity comes from the session; only the caller's own log is touched."""
    employee = _get_employee()
    work_log = _get_work_log(employee.name, getdate(date).strftime("%Y-%m-%d"))
    if not work_log or not work_log.morning_submitted or work_log.eod_submitted:
        frappe.throw("There is no open check-in to request checkout approval for.")
    # Reason from the employee is strictly mandatory.
    reason = (reason or "").strip()
    if not reason:
        frappe.throw("A reason is required to request late checkout approval.")
    # Only the most recent working day can be requested; older forgotten
    # checkouts are an HR correction in Desk, not an employee request.
    if getdate(work_log.date) not in (getdate(today()), _get_previous_working_date(employee.name, today())):
        frappe.throw("Late checkout approval can only be requested for your last working day.")
    if not is_checkout_locked(work_log.date, work_log.checkout_unlocked):
        frappe.throw("Checkout is still open for this date; no approval is needed.")
    if work_log.unlock_requested_at:
        return {"status": "already_requested"}

    work_log.unlock_requested_at = now_datetime()
    work_log.unlock_request_reason = reason[:500]
    # Lets the controller accept this write to otherwise HR-only fields.
    frappe.flags.in_late_checkout_request = True
    try:
        _save_work_log(work_log)
    finally:
        frappe.flags.in_late_checkout_request = False

    hr_emails = [u for u in _get_hr_manager_emails() if u not in ("Administrator", "Guest")]
    if hr_emails:
        frappe.sendmail(
            recipients=hr_emails,
            subject=f"Late checkout approval requested — {employee.employee_name} ({work_log.date})",
            message=(
                f"<p>{html_escape(employee.employee_name)} could not check out for "
                f"{work_log.date} because the checkout window is closed.</p>"
                f"<p>Reason: {html_escape(work_log.unlock_request_reason or '—')}</p>"
                f"<p>Open Daily Work Log <b>{html_escape(work_log.name)}</b> and use "
                "Actions &rarr; Unlock Late Checkout.</p>"
            ),
        )
    return {"status": "requested"}


def _is_allowed_late_checkout_approval_date(employee, log_date):
    """Only the previous working day OR today (only if today is locked past cutoff)
    can be approved by HR."""
    ld = getdate(log_date)
    td = getdate(today())
    if ld == td:
        # Allowed for today only if checkout is locked (e.g. past 10 PM)
        return is_checkout_locked(td, 0)
    prev_work_date = _get_previous_working_date(employee, td)
    return ld == prev_work_date


@frappe.whitelist(methods=["POST"])
def unlock_late_checkout(employee, date, reason=None):
    """HR Manager unlocks checkout for exactly one employee and date."""
    if "HR Manager" not in frappe.get_roles(frappe.session.user):
        frappe.throw("Only an HR Manager can unlock late checkout.", frappe.PermissionError)
    reason = (reason or "").strip()

    target_date = getdate(date)
    if not _is_allowed_late_checkout_approval_date(employee, target_date):
        frappe.throw("Only the previous working day (or today if locked past cutoff) can be approved.")

    work_log = _get_work_log(employee, target_date.strftime("%Y-%m-%d"))
    if not work_log or not work_log.morning_submitted or work_log.eod_submitted:
        frappe.throw("No open check-in found for this employee and date.")

    work_log.checkout_unlocked = 1
    work_log.unlocked_by = frappe.session.user
    work_log.unlocked_at = now_datetime()
    work_log.unlock_reason = reason[:500] if reason else None
    # HR Manager role is enforced above. Employee-scoped User Permissions
    # (auto-created per Employee) would otherwise stop HR saving another
    # employee's log, so bypass only the document permission check.
    work_log.save(ignore_permissions=True)

    user_id = frappe.db.get_value("Employee", employee, "user_id")
    if user_id:
        frappe.sendmail(
            recipients=[user_id],
            subject=f"Late checkout approved for {work_log.date}",
            message=f"<p>HR approved your late checkout for {work_log.date}. Open Daily Check-in to complete it.</p>",
        )
    return {"status": "unlocked"}


@frappe.whitelist()
def get_late_checkout_requests():
    """Open late-checkout requests awaiting HR, oldest first. HR Manager only,
    because only that role can act on them. Only shows requests for the employee's
    previous working day or today if locked."""
    if "HR Manager" not in frappe.get_roles(frappe.session.user):
        frappe.throw("Only an HR Manager can view late checkout requests.", frappe.PermissionError)
    raw_requests = frappe.get_all(
        "Daily Work Log",
        filters={
            "morning_submitted": 1,
            "eod_submitted": 0,
            "checkout_unlocked": 0,
            "unlock_requested_at": ["is", "set"],
        },
        fields=["employee", "employee_name", "department", "date",
                "unlock_request_reason as reason", "unlock_requested_at as requested_at"],
        order_by="unlock_requested_at asc",
        limit=100,
    )
    # Filter to only previous working day or locked today
    valid_requests = []
    for row in raw_requests:
        if _is_allowed_late_checkout_approval_date(row.employee, row.date):
            valid_requests.append(row)
    return valid_requests
