"""Shared navigation/context for the StandardTouch-branded www/ pages.

Pages call get_shell_context() from get_context(); the Jinja macro in
templates/macros/st_shell.html renders the top bar and left rail from it.
Visibility rules mirror each page's own access check, so a link is only
shown to someone who can actually open the page.
"""
import frappe

from st_attendance_tracker.api import _is_team_leader

# (key, label, route, tabler icon, audience)
NAV_ITEMS = (
    ("checkin", "Daily Check-in", "/daily-checkin", "ti-calendar-check", "all"),
    ("backlog", "Task Backlog", "/task-backlog", "ti-list-details", "all"),
    ("additional", "Additional Work", "/additional-work", "ti-briefcase", "all"),
    ("history", "My History", "/my-history", "ti-history", "all"),
    ("recurring", "Recurring Tasks", "/recurring-tasks", "ti-repeat", "all"),
    ("team", "Team Dashboard", "/team-dashboard", "ti-users", "team_leader"),
    ("management", "Management Dashboard", "/management-dashboard", "ti-chart-bar", "hr"),
)


# The original (classic) layout of every page lives at <route>-classic.
CLASSIC_SUFFIX = "-classic"


# (label, route, audience); pages read the URL hash to open the matching dialog.
CREATE_ITEMS = (
    ("Daily task", "/daily-checkin#new-task", "employee"),
    ("Additional work", "/additional-work", "employee"),
    ("Recurring task", "/recurring-tasks#new", "employee"),
    ("Assign team task", "/team-dashboard#assign", "team_leader"),
)


def get_shell_context(active, employee):
    roles = set(frappe.get_roles(frappe.session.user))
    is_hr = bool({"HR Manager", "Management"} & roles)
    is_team_leader = bool(employee.get("name")) and _is_team_leader(employee.name)
    allowed = {"all": True, "team_leader": is_team_leader, "hr": is_hr}

    # Management users have no Employee record and never check in.
    nav = [
        {"key": key, "label": label, "href": route, "icon": icon, "active": key == active}
        for key, label, route, icon, audience in NAV_ITEMS
        if allowed[audience] and (employee.get("name") or audience == "hr")
    ]
    has_employee = bool(employee.get("name"))
    create_allowed = {"employee": has_employee, "team_leader": is_team_leader}
    create_items = [
        {"label": label, "href": href}
        for label, href, audience in CREATE_ITEMS
        if create_allowed[audience]
    ]
    name = employee.get("employee_name") or ""
    classic = next((route + CLASSIC_SUFFIX for key, _l, route, _i, _a in NAV_ITEMS if key == active), None)
    return {
        "menu_links": [{"label": "Classic design", "href": classic, "icon": "ti-layout-list", "switch": "classic"}] if classic else [],
        "create_items": create_items,
        "nav": nav,
        "name": name,
        "initials": name[:2].upper(),
        "profile_url": f"/app/employee/{employee.name}" if employee.get("name") else "",
    }


# ── Default design: calendar (new) or classic ─────────────────────────────────
# Stored per user as a Frappe user default. A session cookie ("st_view") set by the
# "New design" / "Classic design" switch links overrides it until the browser closes.
DESIGN_KEY = "st_design_preference"
DESIGN_CHOICES = ("new", "classic")
# Design for users who have not chosen one yet (no saved default, no session cookie).
DEFAULT_DESIGN = "classic"
VIEW_COOKIE = "st_view"


@frappe.whitelist()
def get_design_preference():
    """The signed-in user's default design: "new", "classic" or None when never chosen."""
    value = frappe.defaults.get_user_default(DESIGN_KEY, frappe.session.user)
    return value if value in DESIGN_CHOICES else None


@frappe.whitelist(methods=["POST"])
def set_design_preference(design):
    """Save the signed-in user's default design. Only ever changes the caller's own setting."""
    if design not in DESIGN_CHOICES:
        frappe.throw("Choose the calendar design or the classic design.", frappe.ValidationError)
    frappe.defaults.set_user_default(DESIGN_KEY, design, frappe.session.user)
    return design


def redirect_for_design():
    """Send the user to the design they chose, unless a switch link overrode it for this session."""
    request = getattr(frappe.local, "request", None)
    if not request or frappe.session.user == "Guest":
        return
    path = request.path.rstrip("/") or "/"
    is_classic = path.endswith(CLASSIC_SUFFIX)
    wanted = request.cookies.get(VIEW_COOKIE)
    if wanted not in DESIGN_CHOICES:
        wanted = get_design_preference() or DEFAULT_DESIGN
    if wanted == "classic" and not is_classic and path in {r for _k, _l, r, _i, _a in NAV_ITEMS}:
        target = path + CLASSIC_SUFFIX
    elif wanted == "new" and is_classic:
        target = path[: -len(CLASSIC_SUFFIX)]
    else:
        return
    frappe.local.flags.redirect_location = target
    raise frappe.Redirect
