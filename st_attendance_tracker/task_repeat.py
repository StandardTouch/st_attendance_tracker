"""Turn a task's "Repeat next day" flag on or off.

A flagged task is copied to the next working day even after it is Done (see api._repeat_copies, called by the
checkout rollover). This is separate from Recurring Task Templates: those repeat on a schedule; this is the
employee's own "bring this one back tomorrow" for an ordinary task.
"""
import frappe

from st_attendance_tracker.api import _save_work_log


def set_task_repeat(employee_name, task_name, repeat):
    """Own task, before checkout. Returns {repeat}."""
    parent = frappe.db.get_value("Task Entry", task_name, "parent")
    if not parent:
        frappe.throw("Not authorised to edit this task.", frappe.PermissionError)
    work_log = frappe.get_doc("Daily Work Log", parent)
    if work_log.employee != employee_name:
        frappe.throw("Not authorised to edit this task.", frappe.PermissionError)
    if work_log.eod_submitted:
        frappe.throw("Cannot change tasks after checkout is submitted.", frappe.ValidationError)
    row = next((r for r in work_log.tasks if r.name == task_name), None)
    if not row:
        frappe.throw("Not authorised to edit this task.", frappe.PermissionError)
    if row.task_type == "Recurring":
        frappe.throw(
            "This task already repeats from your Recurring Tasks. Change its schedule on the Recurring Tasks page.",
            frappe.ValidationError,
        )

    row.repeat_next_day = 1 if repeat else 0
    _save_work_log(work_log)
    return {"repeat": bool(repeat)}
