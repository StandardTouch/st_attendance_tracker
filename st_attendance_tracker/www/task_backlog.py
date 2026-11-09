import frappe
from st_attendance_tracker.api import _is_team_leader
from st_attendance_tracker.ui_shell import get_shell_context, redirect_for_design


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.local.flags.redirect_location = "/login?redirect-to=/task-backlog"
        raise frappe.Redirect

    redirect_for_design()

    employee = frappe.db.get_value(
        "Employee", {"user_id": frappe.session.user, "status": "Active"},
        ["name", "employee_name", "department"], as_dict=True,
    )
    if not employee:
        frappe.throw(
            "Your account is not linked to an Employee record. "
            "Please contact HR."
        )

    context.no_cache = 1
    context.employee = employee
    context.title = "Task Backlog"
    context.st_shell = get_shell_context("backlog", employee)
    context.today = frappe.utils.today()
    # Pulling a task into a day that is already checked out is refused server-side; the page uses this to explain it.
    context.checked_out = bool(frappe.db.get_value(
        "Daily Work Log", {"employee": employee.name, "date": context.today, "eod_submitted": 1}, "name"))
    context.is_team_leader = _is_team_leader(employee.name)
    context.is_hr_manager = "HR Manager" in frappe.get_roles(frappe.session.user)
    context.recurring_count = frappe.db.count(
        "Recurring Task Template", {"employee": employee.name, "is_active": 1}
    )
    context.backlog_count = frappe.db.count(
        "Task Backlog Item", {"employee": employee.name}
    )

    tours_seen = frappe.db.get_value("ST Tour Seen", frappe.session.user, "tours_seen") or ""
    context.show_tour_task_backlog = "task_backlog_v1" not in tours_seen.split(",")
