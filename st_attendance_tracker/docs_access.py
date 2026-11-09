"""Which documentation space a user lands on.

Access itself is enforced by Wiki's own per-space roles (see docs_setup.SPACES);
this only picks the highest space the user's roles open, for the /docs redirect.
"""

import frappe

SPACE_EMPLOYEE = "docs/employee"
SPACE_TEAM_LEADER = "docs/team-leader"
SPACE_MANAGEMENT = "docs/management"

MANAGEMENT_ROLES = ("HR Manager", "Management", "System Manager")
TEAM_LEADER_ROLES = ("Team Lead", *MANAGEMENT_ROLES)
EMPLOYEE_ROLES = ("Employee", *TEAM_LEADER_ROLES)


def docs_space_for(user):
    """Highest documentation space this user's roles allow."""
    roles = set(frappe.get_roles(user))
    if roles & set(MANAGEMENT_ROLES):
        return SPACE_MANAGEMENT
    if "Team Lead" in roles:
        return SPACE_TEAM_LEADER
    return SPACE_EMPLOYEE
