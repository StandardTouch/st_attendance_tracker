import frappe

from st_attendance_tracker.docs_access import docs_space_for

no_cache = 1


def get_context(context):
    """/docs sends each user to the documentation space matching their role."""
    if frappe.session.user == "Guest":
        frappe.local.flags.redirect_location = "/login?redirect-to=/docs"
        raise frappe.Redirect
    frappe.local.flags.redirect_location = f"/{docs_space_for(frappe.session.user)}"
    raise frappe.Redirect
