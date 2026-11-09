import frappe
from frappe.model.document import Document


class TaskEntry(Document):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Frappe back-fills every Time field with nowtime() on new docs (create_new)
        # and again on insert (_set_defaults). An empty start_time must stay empty:
        # it means "no calendar position chosen yet". Same fix as Daily Work Log.
        if self.is_new():
            self.set("start_time", None)
        self.dont_update_if_missing = ["start_time"]

    def on_trash(self):
        if self.series_id:
            frappe.db.sql("""
                UPDATE `tabTask Entry`
                SET status = 'Rolled Over'
                WHERE series_id = %s AND status IN ('Pending', 'In Progress')
            """, (self.series_id,))


def on_doctype_update():
    # Rollover status cascade (Section 4.2) does UPDATE ... WHERE series_id = %s
    # across every copy of a task's lineage — this replaces the old
    # rolled_over_from chain walk with a single indexed query.
    frappe.db.add_index("Task Entry", ["series_id"])
