import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime, flt
from st_attendance_tracker.time_utils import (
    parse_duration_to_hours, time_to_minutes, validate_lunch_hours, calculate_net_minutes,
    is_checkout_locked,
)
from st_attendance_tracker.api import _get_attendance_settings


_TIME_FIELDS = ("login_time", "logout_time", "lunch_from", "lunch_to")
_UNLOCK_FIELDS = ("checkout_unlocked", "unlocked_by", "unlocked_at", "unlock_reason")
_UNLOCK_REQUEST_FIELDS = ("unlock_requested_at", "unlock_request_reason")
_UNLOCK_ROLES = {"HR Manager", "System Manager"}


class DailyWorkLog(Document):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Every Time-fieldtype field gets auto-defaulted to nowtime() twice:
        # once by frappe.new_doc() (frappe.model.create_new, before this
        # __init__ even runs), and again on *every* insert() via
        # Document._set_defaults() -> update_if_missing(), which re-pulls
        # from a fresh new_doc() template for any field still None. Since
        # all four now live on one doc (unlike the old two-doc model, where
        # a Morning Check-In log's unused logout/lunch fields were never
        # read), an unchecked-out check-in would otherwise inherit a
        # garbage logout_time/lunch window and fail lunch validation.
        # dont_update_if_missing (base_document.py) is the documented way
        # to suppress the second backfill; clearing here handles the first.
        if self.is_new():
            for fieldname in _TIME_FIELDS:
                self.set(fieldname, None)
        self.dont_update_if_missing = list(_TIME_FIELDS)

    def before_insert(self):
        if not self.employee:
            self.employee = frappe.db.get_value(
                "Employee", {"user_id": frappe.session.user}, "name"
            )
            if not self.employee:
                frappe.throw("No Employee record linked to your user account.")

    def validate(self):
        self._check_locked()
        self._guard_unlock_fields()
        self._check_checkout_window()
        self._validate_no_duplicate()
        self._prepare_tasks()
        if self.eod_submitted and not self.login_time:
            frappe.throw(
                "Morning Check-In is required before submitting End of Day.",
                frappe.ValidationError,
            )
        self._check_late()
        self._validate_lunch_hours()
        self._calculate_net_hours()
        self._calculate_working_hours()
        if self.eod_submitted and not self.locked_at:
            self.locked_at = now_datetime()
        elif not self.eod_submitted:
            self.locked_at = None

    def on_update(self):
        before = self.get_doc_before_save()
        if self.eod_submitted and before and not before.eod_submitted:
            self._trigger_eod_automation()

    def _trigger_eod_automation(self):
        from st_attendance_tracker.api import (
            _cascade_series_done, _make_checkin, _make_lunch_checkins, _rollover_pending_tasks,
            _send_eod_notifications
        )
        
        # Cascade done tasks
        newly_done_series = [row.series_id for row in self.tasks if row.status == "Done" and row.series_id]
        for series_id in newly_done_series:
            _cascade_series_done(series_id, self.date)
            
        # Lunch goes in before the final OUT so no auto-attendance run sees a partial set
        _make_lunch_checkins(self.employee, self.lunch_from, self.lunch_to, self.date)

        # Check out
        _make_checkin(self.employee, "OUT", self.logout_time, on_date=self.date)
        
        # Rollover
        _rollover_pending_tasks(self.employee, self.date)

        # Notify
        is_late_checkout = str(self.date) != str(now_datetime().strftime("%Y-%m-%d"))
        frappe.enqueue(
            _send_eod_notifications,
            queue="short",
            enqueue_after_commit=True,
            employee_name=self.employee,
            date=self.date,
            checkout_action_time=now_datetime(),
            is_late_checkout=is_late_checkout,
        )


    def _check_locked(self):
        """Block edits once End of Day has been submitted (regular employee only)."""
        if self.is_new():
            return
        if frappe.session.user in ("Administrator", "Guest"):
            return
        allowed_roles = {"HR Manager", "System Manager", "ST Task Assignment Agent"}
        if allowed_roles & set(frappe.get_roles(frappe.session.user)):
            return
        was_locked = frappe.db.get_value("Daily Work Log", self.name, "eod_submitted")
        if was_locked and self.eod_submitted:
            frappe.throw(
                "Cannot modify this log after End of Day has been submitted.",
                frappe.ValidationError,
            )

    def _guard_unlock_fields(self):
        """The late-checkout approval fields are read-only in the form, but
        that does not stop a direct REST write — enforce it here. Only an HR
        Manager/System Manager may unlock; an employee may only record their
        own request, and only through request_late_checkout_approval."""
        if frappe.session.user == "Administrator":
            return
        before = self.get_doc_before_save()

        def changed(fieldnames):
            return any(
                (self.get(f) or None) != ((before.get(f) if before else None) or None)
                for f in fieldnames
            )

        if changed(_UNLOCK_FIELDS) and not (_UNLOCK_ROLES & set(frappe.get_roles(frappe.session.user))):
            frappe.throw("Only an HR Manager can unlock late checkout.", frappe.PermissionError)
        if changed(_UNLOCK_REQUEST_FIELDS) and not frappe.flags.in_late_checkout_request \
                and not (_UNLOCK_ROLES & set(frappe.get_roles(frappe.session.user))):
            frappe.throw("Request late checkout approval from the check-in page.", frappe.PermissionError)

    def _check_checkout_window(self):
        """Checkout closes at the cutoff time unless HR unlocked this log.
        Enforced on the document so REST/desk writes cannot bypass the
        whitelisted checkout API's own gate."""
        if not self.eod_submitted or frappe.session.user == "Administrator":
            return
        before = self.get_doc_before_save()
        if before and before.eod_submitted:
            return
        if is_checkout_locked(self.date, self.checkout_unlocked):
            frappe.throw(
                "Checkout window is closed. Get late checkout approval from HR.",
                frappe.PermissionError,
            )

    def _validate_no_duplicate(self):
        existing = frappe.db.exists("Daily Work Log", {
            "employee": self.employee,
            "date": self.date,
            "name": ["!=", self.name or ""],
        })
        if existing:
            frappe.throw(
                f"A Daily Work Log for {self.employee_name} on {self.date} already exists: {existing}"
            )

    def _prepare_tasks(self):
        for row in self.tasks:
            if not row.series_id:
                row.series_id = frappe.generate_hash(length=32)
            if not row.origin_date:
                row.origin_date = self.date
            # Only parse when it's still raw text — a row loaded from the DB
            # already holds a parsed float. parse_duration_to_hours treats a
            # bare number with no unit as *minutes* (its convention for
            # unit-less input), so re-parsing an already-correct hours value
            # like 2.0 on every subsequent save of this Daily Work Log (EOD,
            # rollover, any edit that doesn't touch this row) would silently
            # divide it by 60 each time.
            if isinstance(row.estimated_time, str):
                row.estimated_time = parse_duration_to_hours(row.estimated_time)
            if isinstance(row.actual_time, str):
                row.actual_time = parse_duration_to_hours(row.actual_time)
            if not (row.description or "").strip():
                frappe.throw("Task description cannot be empty.")
            if row.status == "Done" and not row.actual_time:
                if getattr(frappe.flags, "in_eod_draft", False):
                    # Mid-edit autosave — the employee may have flipped the
                    # status just before typing the time. Downgrade instead
                    # of hard-failing a background save; submit_eod_log's
                    # own path never sets this flag, so its "Done requires
                    # actual_time" guarantee at final checkout is unchanged.
                    row.status = "In Progress"
                else:
                    frappe.throw("Time Taken for Task Completion is mandatory for completed tasks.")

    def _check_late(self):
        if not self.login_time:
            return
        try:
            threshold = _get_attendance_settings().get("late_checkin_threshold")
            if not threshold:
                return
            login_mins = time_to_minutes(self.login_time)
            threshold_mins = time_to_minutes(threshold)
            self.is_late = 1 if login_mins > threshold_mins else 0
        except Exception:
            pass

    def _validate_lunch_hours(self):
        validate_lunch_hours(self.login_time, self.logout_time, self.lunch_from, self.lunch_to)

    def _calculate_net_hours(self):
        if not self.login_time or not self.logout_time:
            return
        try:
            net_mins = calculate_net_minutes(
                self.login_time, self.logout_time, self.lunch_from, self.lunch_to, self.date
            )
            hours = net_mins // 60
            mins = net_mins % 60
            self.net_hours = f"{hours}h {mins}m"
        except Exception:
            frappe.log_error(frappe.get_traceback(), "ST Attendance Tracker — net hours calculation failed")
            frappe.msgprint(
                "Could not calculate your net working hours automatically. "
                "Your checkout has still been recorded — please contact HR to verify your hours.",
                indicator="orange", alert=True,
            )

    def _calculate_working_hours(self):
        if not self.eod_submitted:
            self.working_hours = 0
            return
        self.working_hours = sum(flt(row.actual_time) for row in self.tasks)


def on_doctype_update():
    # Every check-in/checkout lookup in this app filters by employee + date
    # together — a composite index matches that pattern far better than
    # per-column indexes.
    frappe.db.add_index("Daily Work Log", ["employee", "date"])
