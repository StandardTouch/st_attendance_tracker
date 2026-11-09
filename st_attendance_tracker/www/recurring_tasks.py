import frappe

from st_attendance_tracker.ui_shell import get_shell_context, redirect_for_design


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.local.flags.redirect_location = "/login?redirect-to=/recurring-tasks"
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
    context.title = "Recurring Tasks"
    context.st_shell = get_shell_context("recurring", employee)

    tours_seen = frappe.db.get_value("ST Tour Seen", frappe.session.user, "tours_seen") or ""
    context.show_tour_recurring_tasks = "recurring_tasks_v1" not in tours_seen.split(",")
