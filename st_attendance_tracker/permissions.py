"""Document-level permission hooks (registered in hooks.py).

The Daily Work Log Employee role grants read/write/create with no `if_owner`,
so without these hooks any Employee could read or rewrite another employee's
log through the REST API or Desk.
"""

import frappe

# Roles that may act on any employee's log. Mirrors the bypass sets already used
# in daily_work_log.py (_check_locked) so this adds no new privileged role.
_UNRESTRICTED_ROLES = {"System Manager", "HR Manager", "ST Task Assignment Agent"}


def _is_unrestricted(user):
    return user == "Administrator" or bool(_UNRESTRICTED_ROLES & set(frappe.get_roles(user)))


def _own_employee(user):
    return frappe.db.get_value("Employee", {"user_id": user}, "name")


def _led_employees(employee_name):
    # Imported here: api.py imports a lot at module load.
    from st_attendance_tracker.api import _get_team_members

    return _get_team_members(employee_name)


def has_permission_daily_work_log(doc, ptype=None, user=None):
    """Deny-only hook: own log, Team Leader (read only), or an unrestricted role."""
    user = user or frappe.session.user
    if _is_unrestricted(user):
        return None
    # New log with no employee yet: before_insert fills in the caller's own Employee.
    if not doc.employee:
        return None

    own = _own_employee(user)
    if own and doc.employee == own:
        return None
    if ptype == "read" and own and doc.employee in _led_employees(own):
        return None
    return False


def daily_work_log_query_conditions(user=None):
    """Limit get_list/Desk list views to own log (plus led employees' logs)."""
    user = user or frappe.session.user
    if _is_unrestricted(user):
        return ""

    own = _own_employee(user)
    if not own:
        return "1=0"
    visible = {own, *_led_employees(own)}
    escaped = ", ".join(frappe.db.escape(name) for name in sorted(visible))
    return f"`tabDaily Work Log`.`employee` in ({escaped})"
