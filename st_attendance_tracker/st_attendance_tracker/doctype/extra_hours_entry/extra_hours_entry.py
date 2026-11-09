import frappe
from frappe.model.document import Document
from frappe.utils import add_days, flt, getdate


class ExtraHoursEntry(Document):
    def validate(self):
        self.hours = flt(self.hours)
        if self.hours <= 0:
            frappe.throw("Hours must be greater than zero.")
        if self.entry_type == "Credit":
            self._validate_credit()
        elif self.source != "Compensation":
            frappe.throw("A use entry must have the source Compensation.")
        else:
            taken = sum(flt(row.hours) for row in self.allocations)
            if abs(taken - self.hours) > 1e-6:
                frappe.throw("The credits used must add up to the hours of this use.")

    def _validate_credit(self):
        if self.source == "Compensation":
            frappe.throw("A credit cannot have the source Compensation.")
        if flt(self.used_hours) > self.hours + 1e-6:
            frappe.throw("Used hours cannot be more than the extra hours earned.")
        if not self.expires_on:
            from st_attendance_tracker.time_bank import expiry_days

            self.expires_on = add_days(getdate(self.date), expiry_days())


def on_doctype_update():
    # Balance queries filter by employee, type and expiry together.
    frappe.db.add_index("Extra Hours Entry", ["employee", "entry_type", "expires_on"])
