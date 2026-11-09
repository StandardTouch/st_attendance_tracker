import frappe
from frappe.model.document import Document


class TaskBacklogItem(Document):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Frappe back-fills every Time field with nowtime(); an empty start_time means "no time chosen".
        if self.is_new():
            self.set("start_time", None)
        self.dont_update_if_missing = ["start_time"]

    def before_insert(self):
        if not self.employee:
            self.employee = frappe.db.get_value(
                "Employee", {"user_id": frappe.session.user}, "name"
            )
            if not self.employee:
                frappe.throw("No Employee record linked to your user account.")

    def validate(self):
        self._check_ownership()
        if not (self.description or "").strip():
            frappe.throw("Task description cannot be empty.")

    def on_trash(self):
        # validate() is never called on delete — without this, the ownership
        # guard above is silently bypassed for delete.
        self._check_ownership()

    def _check_ownership(self):
        """Block cross-employee edits (BOLA guard) — same pattern as
        Recurring Task Template / Daily Work Log. `owner` isn't reliable here
        (a Team-Leader-pushed item is owned by the Team Leader's user, not
        the employee it belongs to), so this checks the `employee` link
        field instead — every API that mutates this doctype calls with
        ignore_permissions=True and relies on this guard as the real check."""
        if frappe.session.user in ("Administrator", "Guest"):
            return
        if getattr(frappe.flags, "in_task_backlog_push", False):
            return
        allowed_roles = {"HR Manager", "System Manager"}
        if allowed_roles & set(frappe.get_roles(frappe.session.user)):
            return
        current_employee = frappe.db.get_value(
            "Employee", {"user_id": frappe.session.user}, "name"
        )
        if self.employee != current_employee:
            frappe.throw(
                "You are not allowed to edit another employee's backlog.",
                frappe.PermissionError,
            )


def on_doctype_update():
    frappe.db.add_index("Task Backlog Item", ["employee", "creation"])
    frappe.db.add_index("Task Backlog Item", ["employee", "scheduled_for"])
