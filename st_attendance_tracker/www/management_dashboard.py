import frappe
from frappe.utils import today
from st_attendance_tracker.ui_shell import get_shell_context, redirect_for_design


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.local.flags.redirect_location = "/login?redirect-to=/management-dashboard"
        raise frappe.Redirect

    redirect_for_design()

    user_roles = frappe.get_roles(frappe.session.user)
    if not ({"HR Manager", "Management"} & set(user_roles)):
        frappe.local.flags.redirect_location = "/daily-checkin"
        raise frappe.Redirect

    employee = frappe.db.get_value(
        "Employee", {"user_id": frappe.session.user, "status": "Active"},
        ["name", "employee_name", "department"], as_dict=True,
    )
    if not employee:
        full_name = frappe.db.get_value("User", frappe.session.user, "full_name") or "Management"
        employee = frappe._dict({
            "name": "",
            "employee_name": full_name,
            "department": ""
        })

    context.no_cache = 1
    context.employee = employee
    context.date = today()
    context.title = "Management Dashboard"
    # Only HR Manager can unlock late checkout, so only they get the requests panel.
    context.is_hr_manager = "HR Manager" in user_roles
    context.st_shell = get_shell_context("management", employee)

    tours_seen = frappe.db.get_value("ST Tour Seen", frappe.session.user, "tours_seen") or ""
    context.show_tour_management_dashboard = "management_dashboard_v1" not in tours_seen.split(",")
