import frappe
from frappe.utils import add_days, today
from st_attendance_tracker.ui_shell import get_shell_context


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.local.flags.redirect_location = "/login?redirect-to=/management-report"
        raise frappe.Redirect

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
        employee = frappe._dict({"name": "", "employee_name": full_name, "department": ""})

    context.no_cache = 1
    context.employee = employee
    context.title = "Management Report"
    context.to_date = today()
    context.from_date = add_days(today(), -29)
    context.st_shell = get_shell_context("report", employee)
