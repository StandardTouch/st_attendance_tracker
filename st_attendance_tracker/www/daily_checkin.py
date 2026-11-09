import frappe

from st_attendance_tracker.ui_shell import get_shell_context, redirect_for_design
from frappe.utils import today, now_datetime, getdate, get_datetime
from st_attendance_tracker.api import (
    _to_hhmm, _to_ampm, _format_hours, _is_half_day_leave_today, _is_team_leader,
    _attach_task_files, _resolve_active_checkin_date, _get_work_log, _task_entry_dict, _get_morning_draft,
    _get_attendance_settings, _get_previous_working_date, _get_unclosed_previous_day,
)
from st_attendance_tracker.time_utils import get_checkout_cutoff, is_checkout_locked


def get_context(context):
    # NOTE: frappe.clear_cache() was removed here — it was wiping the site-wide
    # Redis cache (doctype meta, sessions, boot info for ALL users) on every
    # single page load of the app's busiest page. context.no_cache = 1 (below)
    # is the correct way to prevent stale page-level caching.

    if frappe.session.user == "Guest":
        frappe.local.flags.redirect_location = "/login?redirect-to=/daily-checkin"
        raise frappe.Redirect

    redirect_for_design()

    # Management role has no Employee record and never checks in/out —
    # redirect straight to the management dashboard. HR Manager DOES check
    # in/out like any other employee; they just get a nav link (below) to
    # reach the management dashboard instead of being redirected there.
    user_roles = frappe.get_roles(frappe.session.user)
    if "Management" in user_roles:
        frappe.local.flags.redirect_location = "/management-dashboard"
        raise frappe.Redirect
    is_hr_manager = "HR Manager" in user_roles

    employee = frappe.db.get_value(
        "Employee",
        {"user_id": frappe.session.user, "status": "Active"},
        ["name", "employee_name", "department", "reports_to", "work_type"],
        as_dict=True,
    )
    if not employee:
        frappe.throw(
            "Your account is not linked to an Employee record. "
            "Please contact HR or System Administrator."
        )

    actual_today = today()
    date = _resolve_active_checkin_date(employee.name)
    is_late_checkout = str(date) != str(actual_today)
    date_obj = getdate(date)

    # ── Work location configuration ───────────────────────────────────────
    work_location_config = _get_work_location_config(employee, date_obj)

    # ── Safety rollover — ALWAYS runs against today() ─────────────────────
    # Even if the page renders an older open check-in date, we must still
    # carry forward any missed Pending/In-Progress tasks to today's date.
    today_log = _get_work_log(employee.name, actual_today)
    if not (today_log and today_log.morning_submitted):
        from st_attendance_tracker.api import _safety_rollover, _ensure_recurring_tasks, _pull_scheduled_backlog
        _safety_rollover(employee.name, actual_today)
        _ensure_recurring_tasks(employee.name, actual_today)
        _pull_scheduled_backlog(employee.name, actual_today)

    work_log = _get_work_log(employee.name, date)
    morning_log = bool(work_log and work_log.morning_submitted)
    eod_log = bool(work_log and work_log.eod_submitted)

    # Pre-fill the (pre-checkout) lunch checkbox/time inputs from whatever
    # was last saved on this Daily Work Log — via autosave_eod_progress()
    # mid-day, or from a prior EOD submission on a reopened late-checkout
    # day. Computed unconditionally (not gated on eod_submitted) so a
    # refresh redisplays the real DB value instead of always resetting to
    # the unchecked/14:00-15:00 defaults.
    lunch_from_input = _to_hhmm(work_log.lunch_from) if work_log and work_log.lunch_from else ""
    lunch_to_input = _to_hhmm(work_log.lunch_to) if work_log and work_log.lunch_to else ""

    login_time_val  = ""
    net_hours_val   = ""
    logout_time_val = ""
    work_location_val = ""
    lunch_from_val  = ""
    lunch_to_val    = ""
    half_day_session_val = ""

    # Check if employee has an approved half-day leave today
    try:
        is_half_day_leave = _is_half_day_leave_today(employee.name, date)
    except frappe.PermissionError:
        raise
    except Exception:
        is_half_day_leave = False


    # Always load tasks for today — needed for pre-checkin carried display
    tasks = [_task_entry_dict(row, date) for row in work_log.tasks] if work_log else []
    _attach_task_files(tasks)

    for task in tasks:
        # Redisplay as "1h 30m" style, not the raw decimal-hours DB value —
        # keeps the box round-trippable with _parse_time_to_hours on re-save.
        task["estimated_time_display"] = _format_hours(task.get("estimated_time"))
        task["actual_time_display"] = _format_hours(task.get("actual_time"))
        task["days_pending"] = (
            (frappe.utils.getdate(date) - frappe.utils.getdate(task["origin_date"])).days
            if task["is_carried"] else 0
        )

    if morning_log:
        # FIX: _to_ampm converts timedelta → 'HH:MM AM/PM' with leading zero.
        # The old str(timedelta)[:5] produced '9:30:' (trailing colon) for
        # single-digit hours, breaking both display and recalc().
        login_time_val = _to_ampm(work_log.login_time or "")
        work_location_val = work_log.work_location or "Office"
        half_day_session_val = work_log.half_day_session or ""
    else:
        work_location_val = work_location_config.get("value", "Office")

    working_hours_val = ""
    if eod_log:
        net_hours_val   = work_log.net_hours or ""
        # FIX: use _to_ampm for all time fields — timedelta → 'HH:MM AM/PM'
        logout_time_val = _to_ampm(work_log.logout_time)
        lunch_from_val  = _to_ampm(work_log.lunch_from)
        lunch_to_val    = _to_ampm(work_log.lunch_to)
        working_hours_val = _format_hours(work_log.working_hours) or "0h"

    # Recurring tasks get their own section at the top, not scattered into
    # whichever project they happen to belong to.
    recurring_tasks = [t for t in tasks if t.get("task_type") == "Recurring"]

    # Group the rest by project
    projects = {}
    standalone = []
    for task in tasks:
        if task.get("task_type") == "Recurring":
            continue
        pname = (task.get("project_name") or "").strip()
        if pname:
            if pname not in projects:
                projects[pname] = []
            projects[pname].append(task)
        else:
            standalone.append(task)

    # Team Leader via reports_to OR Employee Department Assignment — matches
    # the access gate on /team-dashboard and the get_team_dashboard API.
    is_team_leader = _is_team_leader(employee.name)

    done_count = sum(1 for t in tasks if t.get("status") == "Done")

    # ── Fetch Active Shift, Leaves & Reset Status ─────────────────────
    # Uses HRMS's own get_shifts_for_date (date-only match, correctly handles
    # midnight-spanning shifts via its prev/next-day margin window) instead of
    # a hand-rolled query. NOTE: deliberately NOT get_employee_shift() — that
    # helper is time-of-day sensitive (checks the current moment against shift
    # start/end + grace margins) and would go blank outside shift hours, which
    # is wrong for an all-day informational display like this one.
    shift_info = None
    try:
        from hrms.hr.doctype.shift_assignment.shift_assignment import get_shifts_for_date
        shifts = get_shifts_for_date(employee.name, get_datetime(date))
        shift_name = shifts[0].shift_type if shifts else None

        if not shift_name:
            shift_name = frappe.db.get_value("Employee", employee.name, "default_shift")

        if shift_name:
            shift_doc = frappe.get_cached_value("Shift Type", shift_name,
                ["name", "start_time", "end_time"], as_dict=True)
            if shift_doc:
                shift_info = {
                    "name": shift_doc.name,
                    "start_time": _to_hhmm(shift_doc.start_time),
                    "end_time": _to_hhmm(shift_doc.end_time),
                    "start_time_ampm": _to_ampm(shift_doc.start_time),
                    "end_time_ampm": _to_ampm(shift_doc.end_time),
                }
    except Exception as e:
        frappe.log_error(f"Error fetching shift for {employee.name}: {e}", "Daily Check-In Shift Error")

    if shift_info and half_day_session_val:
        shift_info = _split_shift_for_half_day(shift_info, half_day_session_val)

    leave_today = None
    try:
        leave_today = frappe.db.get_value("Leave Application", {
            "employee": employee.name,
            "from_date": ["<=", date],
            "to_date": [">=", date],
            "status": "Approved",
            "docstatus": 1
        }, "leave_type")
    except frappe.PermissionError:
        raise
    except Exception:
        pass

    has_reset_today = bool(work_log and work_log.was_reset_today)

    # Checkout window: today's log after the cutoff, or an older open log
    # the resolver skipped (shown as a banner so the employee can ask HR).
    checkout_locked = bool(
        morning_log and not eod_log and is_checkout_locked(date, work_log.checkout_unlocked)
    )
    missed_checkout = frappe.db.get_value("Daily Work Log", {
        "employee": employee.name,
        "morning_submitted": 1,
        "eod_submitted": 0,
        "checkout_unlocked": 0,
        "date": _get_previous_working_date(employee.name, actual_today),
    }, ["date", "unlock_requested_at"], as_dict=True)
    cutoff_minutes = get_checkout_cutoff()

    context.checkin_blocked = bool(missed_checkout and _get_unclosed_previous_day(employee.name, actual_today))
    context.no_cache = 1
    context.employee = employee
    context.date = date
    context.actual_today = actual_today
    context.recurring_count = frappe.db.count(
        "Recurring Task Template", {"employee": employee.name, "is_active": 1}
    )
    context.backlog_count = frappe.db.count(
        "Task Backlog Item", {"employee": employee.name}
    )
    context.current_time = now_datetime().strftime("%H:%M")
    context.current_time_ampm = _to_ampm(now_datetime().strftime("%H:%M:%S"))
    context.is_checked_in = bool(morning_log)
    context.eod_done = bool(eod_log)
    context.tasks = tasks
    context.projects = projects
    context.standalone = standalone
    context.recurring_tasks = recurring_tasks
    context.carried_count = sum(1 for t in tasks if t.get("is_carried"))
    context.login_time = login_time_val
    # Unsent check-in plan saved from another device (or an earlier visit); only the classic page's own kind.
    draft = None if morning_log else _get_morning_draft()
    context.morning_draft = draft if draft and draft.get("kind") == "classic" else None
    context.logout_time = logout_time_val
    context.net_hours = net_hours_val
    context.lunch_from = lunch_from_val
    context.lunch_to   = lunch_to_val
    context.lunch_from_input = lunch_from_input
    context.lunch_to_input = lunch_to_input
    context.include_lunch = bool(lunch_from_input)
    context.work_location = work_location_val
    context.is_team_leader = is_team_leader
    context.is_hr_manager = is_hr_manager
    context.done_count = done_count
    context.total_count = len(tasks)
    context.work_location_config = work_location_config
    context.is_half_day_leave = is_half_day_leave
    context.half_day_session = half_day_session_val
    context.shift_info = shift_info
    context.leave_today = leave_today
    context.has_reset_today = has_reset_today
    context.working_hours = working_hours_val
    context.title = "Daily Check-In"
    context.st_shell = get_shell_context("checkin", employee)
    context.is_late_checkout = is_late_checkout
    context.checkout_locked = checkout_locked
    context.checkout_unlock_requested = bool(checkout_locked and work_log.unlock_requested_at)
    context.checkout_cutoff_label = _to_ampm("%02d:%02d:00" % divmod(cutoff_minutes, 60))
    context.missed_checkout = missed_checkout
    context.missed_checkout_label = getdate(missed_checkout.date).strftime("%A, %d %B %Y") if missed_checkout else ""
    context.checkout_date_label = date_obj.strftime("%A, %d %B %Y")

    tours_seen = frappe.db.get_value("ST Tour Seen", frappe.session.user, "tours_seen") or ""
    context.show_tour_daily_checkin = "daily_checkin_v1" not in tours_seen.split(",")


def _get_work_location_config(employee, date_obj):
    """
    Returns work location field config for the check-in form as a DICT.
    Jinja will serialize with | tojson.

    Rules:
      - Saturday          → WFH, readonly, no validation (everyone)
      - Remote employee   → Remote, readonly, no validation
      - Office employee   → Office / WFH (WFH needs Attendance Request)
      - Hybrid + office day (Tue/Thu by default)
                           → Office / WFH (WFH needs Attendance Request,
                             since they're expected in office that day)
      - Hybrid + non-office day
                           → Office / Hybrid (WFH) — no Attendance Request
                             needed, this IS their normal routine. They can
                             still choose Office if they want to come in.
    """
    from st_attendance_tracker.api import _wfh_request_exists

    work_type = (employee.get("work_type") or "Office").strip()
    weekday_num = date_obj.weekday()  # 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
    is_saturday = weekday_num == 5
    has_wfh_request = _wfh_request_exists(employee.get("name"), date_obj)

    # Saturday — everyone is WFH, no validation needed
    if is_saturday:
        return {
            "value":    "WFH",
            "readonly": True,
            "options":  ["WFH"],
            "validate": False,
            "note":     "All employees work from home on Saturdays.",
        }

    # Remote employee — always remote, readonly
    if work_type == "Remote":
        return {
            "value":    "Remote",
            "readonly": True,
            "options":  ["Remote"],
            "validate": False,
            "note":     "You are a remote employee.",
        }

    # Get hybrid office days from settings (dynamic)
    hybrid_days = _get_hybrid_office_days()
    is_hybrid_office_day = (work_type == "Hybrid") and (weekday_num in hybrid_days)

    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    if work_type == "Hybrid":
        if is_hybrid_office_day:
            # Office day for hybrid — same strict rule as Office employees
            note = (
                f"Today ({day_names[weekday_num]}) is an office day for hybrid "
                f"employees. Select Office, or apply for WFH first."
            )
            if has_wfh_request:
                note = f"Today ({day_names[weekday_num]}) — your WFH request is on file."
            return {
                "value":    "WFH" if has_wfh_request else "Office",
                "readonly": False,
                "options":  ["Office", "WFH"],
                "validate": True,
                "hybrid_office_day": True,
                "note":     note,
            }
        else:
            # Non-office day — WFH is their normal routine, no AR needed.
            # Still allow them to choose Office if they want to come in.
            # NOTE: value/options must stay within Daily Work Log.work_location's
            # allowed Select values ("", "Office", "WFH", "Remote"). The friendlier
            # "Hybrid (WFH)" wording is applied client-side as a display label only —
            # the underlying value saved to the database is always "WFH".
            note = (
                f"Today ({day_names[weekday_num]}) is a regular WFH day for "
                f"hybrid employees. No Attendance Request needed."
            )
            return {
                "value":    "WFH",
                "readonly": False,
                "options":  ["WFH", "Office"],
                "validate": False,
                "hybrid_office_day": False,
                "hybrid_routine_day": True,
                "note":     note,
            }

    # Office employee
    return {
        "value":    "WFH" if has_wfh_request else "Office",
        "readonly": False,
        "options":  ["Office", "WFH"],
        "validate": True,
        "hybrid_office_day": False,
        "note":     "Your WFH request is on file." if has_wfh_request else "Select your work location for today.",
    }


def _shift_midpoint_minutes(start_hhmm, end_hhmm):
    """Midpoint of a shift window in minutes-of-day, overnight-safe."""
    def to_mins(hhmm):
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    start_min, end_min = to_mins(start_hhmm), to_mins(end_hhmm)
    duration = end_min - start_min
    if duration <= 0:
        duration += 24 * 60
    return start_min, end_min, (start_min + duration // 2) % (24 * 60)


def _split_shift_for_half_day(shift_info, session):
    """Returns a copy of shift_info truncated to just the chosen half."""
    start_min, end_min, midpoint = _shift_midpoint_minutes(
        shift_info["start_time"], shift_info["end_time"]
    )
    new_start, new_end = (midpoint, end_min) if session == "Second Half" else (start_min, midpoint)
    result = dict(shift_info)
    result["start_time"] = f"{new_start // 60:02d}:{new_start % 60:02d}"
    result["end_time"] = f"{new_end // 60:02d}:{new_end % 60:02d}"
    result["start_time_ampm"] = _to_ampm(result["start_time"])
    result["end_time_ampm"] = _to_ampm(result["end_time"])
    return result


def _get_hybrid_office_days():
    """
    Returns list of weekday numbers for hybrid office days.
    Reads from ST Attendance Settings — fully dynamic.
    0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat
    """
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
        return days if days else [1, 3]  # Default: Tue, Thu
    except Exception:
        return [1, 3]