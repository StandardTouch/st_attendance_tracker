"""Create / refresh the role-based documentation as Frappe Wiki (v3) spaces.

Content lives in docs_content/<tier>/NN-slug.md (first line "# Title").
Each space is rebuilt from the markdown, so edit the files, not the Wiki UI.
Re-run after editing:
    bench --site <site> execute st_attendance_tracker.docs_setup.sync_role_docs
"""

import os

import frappe

from st_attendance_tracker.docs_access import (
    EMPLOYEE_ROLES,
    MANAGEMENT_ROLES,
    SPACE_EMPLOYEE,
    SPACE_MANAGEMENT,
    SPACE_TEAM_LEADER,
    TEAM_LEADER_ROLES,
)

CONTENT_DIR = os.path.join(os.path.dirname(__file__), "docs_content")

# (route, space name, roles allowed to read, [(tier folder, group title), ...]).
# Higher spaces repeat the lower tiers' pages so each reader gets one sidebar.
SPACES = (
    (SPACE_EMPLOYEE, "ST Attendance Tracker Guide", EMPLOYEE_ROLES, (("employee", "Employee Guide"),)),
    (
        SPACE_TEAM_LEADER,
        "ST Attendance Tracker Guide (Team Leader)",
        TEAM_LEADER_ROLES,
        (("employee", "Employee Guide"), ("team_leader", "Team Leader Guide")),
    ),
    (
        SPACE_MANAGEMENT,
        "ST Attendance Tracker Guide (Management)",
        MANAGEMENT_ROLES,
        (("employee", "Employee Guide"), ("team_leader", "Team Leader Guide"), ("management", "Management Guide")),
    ),
)


def sync_role_docs():
    _delete_orphan_documents()
    for route, space_name, roles, tiers in SPACES:
        _delete_space(route)
        space = _create_space(route, space_name, roles)
        sort_order = 0
        for tier, group_title in tiers:
            group = _create_document(space.root_group, group_title, None, is_group=1, sort_order=sort_order)
            sort_order += 1
            for index, filename in enumerate(sorted(os.listdir(os.path.join(CONTENT_DIR, tier)))):
                if filename.endswith(".md"):
                    title, body = _read_markdown(os.path.join(CONTENT_DIR, tier, filename))
                    slug = filename[:-3].split("-", 1)[1]
                    _create_document(group.name, title, body, slug=slug, sort_order=index)
    frappe.db.commit()


def _read_markdown(path):
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    return lines[0].lstrip("# ").strip(), "\n".join(lines[1:]).strip()


def _delete_orphan_documents():
    # The Wiki v3 migration turns old v14 pages into space-less documents, and Wiki
    # serves a document with no space to everyone, bypassing the per-space roles.
    # A space's own root group also has no wiki_space, so it must be kept.
    root_groups = frappe.get_all("Wiki Space", pluck="root_group")
    orphans = frappe.get_all(
        "Wiki Document",
        filters={"wiki_space": ["is", "not set"], "route": ["like", "docs/%"], "name": ["not in", root_groups or [""]]},
        fields=["name", "is_group"],
        order_by="lft desc",
    )
    for orphan in orphans:
        frappe.delete_doc("Wiki Document", orphan.name, force=True, ignore_permissions=True)


def _delete_space(route):
    # Deleting the space removes its documents; this also clears any Wiki v14
    # pages that the v3 migration converted into this route.
    for name in frappe.get_all("Wiki Space", filters={"route": route}, pluck="name"):
        frappe.delete_doc("Wiki Space", name, force=True, ignore_permissions=True)


def _create_space(route, space_name, roles):
    return frappe.get_doc(
        {
            "doctype": "Wiki Space",
            "route": route,
            "space_name": space_name,
            "is_published": 1,
            "allow_contributions": 0,
            "show_in_switcher": 0,
            "roles": [{"role": role, "permission_level": "Read"} for role in roles],
        }
    ).insert(ignore_permissions=True)


def _create_document(parent, title, content, slug=None, is_group=0, sort_order=0):
    values = {
        "doctype": "Wiki Document",
        "title": title,
        "parent_wiki_document": parent,
        "is_group": is_group,
        "is_published": 1,
        "sort_order": sort_order,
    }
    if slug:
        values["slug"] = slug
    if content is not None:
        values["content"] = content
    return frappe.get_doc(values).insert(ignore_permissions=True)
