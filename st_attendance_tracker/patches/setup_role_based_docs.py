import frappe

from st_attendance_tracker.docs_setup import sync_role_docs


def execute():
    # Docs are Wiki spaces; sites without the Wiki app simply have no docs.
    if "wiki" not in frappe.get_installed_apps():
        return
    sync_role_docs()
