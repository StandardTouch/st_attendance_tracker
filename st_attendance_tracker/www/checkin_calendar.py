"""Daily Check-in, calendar layout. Served at /daily-checkin (the previous page stays at /daily-checkin-classic)."""
import frappe
from frappe.utils import getdate, today
from frappe.website.utils import get_boot_data

from st_attendance_tracker.api import (
    _get_attendance_settings,
    _is_half_day_leave_today,
    _is_team_leader,
    _get_previous_working_date,
    _get_unclosed_previous_day,
    _get_work_log,
    _resolve_active_checkin_date,
    _to_ampm,
    _to_hhmm,
)
from st_attendance_tracker.time_utils import get_checkout_cutoff, is_checkout_locked
from st_attendance_tracker.ui_shell import get_shell_context, redirect_for_design
from st_attendance_tracker.www.daily_checkin import _get_work_location_config


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.local.flags.redirect_location = "/login?redirect-to=/daily-checkin"
        raise frappe.Redirect

    redirect_for_design()

    # Management has no Employee record and never checks in.
    if "Management" in frappe.get_roles(frappe.session.user):
        frappe.local.flags.redirect_location = "/management-dashboard"
        raise frappe.Redirect

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

    # The open check-in may be yesterday's (forgot to check out): checkout then acts on that date.
    active_date = str(getdate(_resolve_active_checkin_date(employee.name)))
    todays_date = today()

    # Checkout window: the open log may be locked after the cutoff, or an older
    # open log the resolver skipped (shown as a banner so the employee can ask HR).
    active_log = _get_work_log(employee.name, active_date)
    checkout_locked = bool(
        active_log and active_log.morning_submitted and not active_log.eod_submitted
        and is_checkout_locked(active_log.date, active_log.checkout_unlocked)
    )
    missed_checkout = None
    if not (checkout_locked or active_date != todays_date):
        missed = frappe.db.get_value("Daily Work Log", {
            "employee": employee.name,
            "morning_submitted": 1,
            "eod_submitted": 0,
            "checkout_unlocked": 0,
            "date": _get_previous_working_date(employee.name, todays_date),
        }, ["date", "unlock_requested_at"], as_dict=True)
        if missed:
            missed_checkout = {"date": str(missed.date), "requested": bool(missed.unlock_requested_at)}

    context.no_cache = 1
    context.employee = employee
    context.title = "Daily Check-In"
    context.st_shell = get_shell_context("checkin", employee)
    context.st_shell["menu_links"] = [
        {"label": "Classic check-in", "href": "/daily-checkin-classic", "icon": "ti-layout-list", "switch": "classic"},
    ]
    # Keep Frappe's own web boot (it carries assets_json, which frappe.require needs for file uploads).
    context.boot = {
        **get_boot_data(),
        "employee": {"name": employee.name, "employee_name": employee.employee_name},
        "today": todays_date,
        "active_date": active_date,
        "is_late_checkout": active_date != todays_date,
        "checkout_locked": checkout_locked,
        "checkout_unlock_requested": bool(checkout_locked and active_log.unlock_requested_at),
        "checkout_cutoff_label": _to_ampm("%02d:%02d:00" % divmod(get_checkout_cutoff(), 60)),
        "missed_checkout": missed_checkout,
        "checkin_blocked": bool(missed_checkout and _get_unclosed_previous_day(employee.name, todays_date)),
        "location_config": _get_work_location_config(employee, getdate(active_date)),
        "half_day_leave": bool(_is_half_day_leave_today(employee.name, todays_date)),
        "is_team_leader": _is_team_leader(employee.name),
        "late_after": _to_hhmm(_get_attendance_settings().get("late_checkin_threshold")) or "09:35",
    }
