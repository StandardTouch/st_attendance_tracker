import frappe


RENAMES = (("Time Bank Entry", "Extra Hours Entry"), ("Time Bank Allocation", "Extra Hours Source"))


def execute():
    """Sites that already created the two lists under their first names keep their rows: rename them in place,
    before the schema sync creates the new ones. Sites that never had the old names do nothing."""
    for old, new in RENAMES:
        if frappe.db.exists("DocType", old) and not frappe.db.exists("DocType", new):
            frappe.rename_doc("DocType", old, new, force=True)
