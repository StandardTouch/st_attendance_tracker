import frappe
from frappe.utils import today


def execute():
    """Switch on the "no check-in until last working day is checked out" block from the
    deploy day, so days that were already unclosed before it are never held against anyone.

    Existing Single rows do not pick up a new field's default, so both values are written."""
    current = frappe.db.get_single_value("ST Attendance Settings", "checkin_block_effective_from")
    # An empty Date field reads back as 0001-01-01 here, not None.
    if not current or str(current).startswith("0001"):
        frappe.db.set_single_value("ST Attendance Settings", "block_checkin_until_checkout_done", 1)
        frappe.db.set_single_value("ST Attendance Settings", "checkin_block_effective_from", today())
