import frappe
from frappe.model.document import Document
from frappe.utils import getdate, today
from st_attendance_tracker.time_utils import parse_duration_to_hours, time_to_minutes


BLOCKED_MESSAGE = "Additional work can be logged after you check out for that day, or on a day you are on leave."


def additional_work_allowed(employee, work_date):
    """Extra hours are logged once the day's work is closed: after check-out, or on a full-day leave.
    Returns (allowed, reason), where reason is "checked_out", "on_leave" or "" when not allowed."""
    if not (employee and work_date):
        return True, ""
    day = str(getdate(work_date))
    if frappe.db.get_value("Daily Work Log", {"employee": employee, "date": day}, "eod_submitted"):
        return True, "checked_out"
    from st_attendance_tracker.api import _get_employees_on_leave

    if employee in _get_employees_on_leave([employee], day) and not _has_half_day_leave(employee, day):
        return True, "on_leave"
    return False, ""


def _has_half_day_leave(employee, day):
    """A half-day leave still has a working half, so it must be checked out like a normal day.
    _get_employees_on_leave counts a half-day application as leave (dashboards and reminders rely
    on that), so this rule has to exclude it itself."""
    return bool(frappe.db.exists("Leave Application", {
        "employee": employee, "status": "Approved", "docstatus": 1,
        "half_day": 1, "half_day_date": day,
    }))


class AdditionalWork(Document):
    def before_insert(self):
        if not self.employee:
            self.employee = frappe.db.get_value(
                "Employee", {"user_id": frappe.session.user}, "name"
            )
            if not self.employee:
                frappe.throw("No Employee record linked to your user account.")

    def validate(self):
        self._check_ownership()
        # Only parse when it's still raw text — a doc loaded from the DB
        # already holds a parsed float. parse_duration_to_hours treats a
        # bare number with no unit as *minutes* (its convention for
        # unit-less input), so re-parsing an already-correct hours value on
        # every subsequent save (e.g. a Desk edit that touches another
        # field, leaving hours_spent untouched) would silently divide it by
        # 60 each time.
        if isinstance(self.hours_spent, str):
            self.hours_spent = parse_duration_to_hours(self.hours_spent)
        self._check_not_future_dated()
        self._check_day_is_closed()
        self._check_no_partial_overlap()

    def after_insert(self):
        from st_attendance_tracker.additional_work_mail import queue_additional_work_email

        queue_additional_work_email(self)

    def _check_no_partial_overlap(self):
        """Two entries on one day may not overlap in time, except an identical window: the
        "Several tasks" form saves each task as its own entry with the same login/logout.
        An end at or before the start (work past midnight) is left alone, as before."""
        if not (self.employee and self.work_date and self.login_time and self.logout_time):
            return
        start, end = time_to_minutes(self.login_time), time_to_minutes(self.logout_time)
        if end <= start:
            return
        others = frappe.get_all(
            "Additional Work",
            filters={"employee": self.employee, "work_date": self.work_date, "name": ["!=", self.name or ""]},
            fields=["name", "login_time", "logout_time"],
        )
        for other in others:
            if not (other.login_time and other.logout_time):
                continue
            o_start, o_end = time_to_minutes(other.login_time), time_to_minutes(other.logout_time)
            if (o_start, o_end) == (start, end) or o_end <= o_start:
                continue
            if start < o_end and end > o_start:
                frappe.throw(
                    "This time overlaps another additional work entry on the same day. "
                    "Change the login or logout time.",
                    frappe.ValidationError,
                )

    def _check_day_is_closed(self):
        """Only when an entry is created or moved to another day or employee; later edits to
        an existing entry (status, remarks) are not blocked."""
        if not (self.is_new() or self.has_value_changed("work_date") or self.has_value_changed("employee")):
            return
        allowed, _reason = additional_work_allowed(self.employee, self.work_date)
        if not allowed:
            frappe.throw(BLOCKED_MESSAGE, frappe.ValidationError)

    def _check_not_future_dated(self):
        if self.work_date and getdate(self.work_date) > getdate(today()):
            frappe.throw("Work date cannot be in the future.")

    def on_trash(self):
        # validate() is never called on delete — without this, the ownership
        # guard above is silently bypassed for the one action (delete) the
        # doctype's own permissions actually allow employees to do.
        self._check_ownership()

    def _check_ownership(self):
        """Block cross-employee edits (BOLA guard) — same pattern as Daily Task."""
        if frappe.session.user in ("Administrator", "Guest"):
            return

        # Team Lead oversight goes through the reports_to-scoped dashboard/API
        # (_is_team_leader), not a blanket role bypass here — that role alone
        # doesn't imply this specific employee is one of their actual reports.
        allowed_roles = {"HR Manager", "System Manager"}
        user_roles = set(frappe.get_roles(frappe.session.user))
        if allowed_roles & user_roles:
            return

        current_employee = frappe.db.get_value(
            "Employee", {"user_id": frappe.session.user}, "name"
        )
        if self.employee != current_employee:
            frappe.throw(
                "You are not allowed to edit another employee's additional work entry.",
                frappe.PermissionError,
            )


def on_doctype_update():
    # Every list/lookup filters by employee + work_date together — a composite
    # index matches that pattern far better than per-column indexes.
    frappe.db.add_index("Additional Work", ["employee", "work_date"])
