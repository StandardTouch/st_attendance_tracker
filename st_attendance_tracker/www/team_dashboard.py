import frappe
from frappe.utils import today
from st_attendance_tracker.api import _is_team_leader
from st_attendance_tracker.ui_shell import get_shell_context, redirect_for_design


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.local.flags.redirect_location = "/login?redirect-to=/team-dashboard"
        raise frappe.Redirect

    redirect_for_design()

    employee = frappe.db.get_value(
        "Employee", {"user_id": frappe.session.user, "status": "Active"},
        ["name", "employee_name", "department"], as_dict=True,
    )
    if not employee:
        frappe.local.flags.redirect_location = "/daily-checkin"
        raise frappe.Redirect

    # Team Leader via reports_to OR Employee Department Assignment —
    # same check the dashboard's own data API (_get_team_members) already uses.
    if not _is_team_leader(employee.name):
        frappe.local.flags.redirect_location = "/daily-checkin"
        raise frappe.Redirect

    context.no_cache = 1
    context.employee = employee
    context.date = today()
    context.title = "Team Dashboard"
    context.st_shell = get_shell_context("team", employee)

    tours_seen = frappe.db.get_value("ST Tour Seen", frappe.session.user, "tours_seen") or ""
    context.show_tour_team_dashboard = "team_dashboard_v1" not in tours_seen.split(",")
