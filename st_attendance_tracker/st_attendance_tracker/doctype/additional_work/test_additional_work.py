"""Additional Work — doctype, ownership (BOLA), and self-service API tests."""
from unittest.mock import patch

import frappe
from frappe.utils import today, add_days
from frappe.tests.utils import FrappeTestCase

from st_attendance_tracker.api import get_additional_work, submit_morning_log, submit_eod_log


# Additional Work create/edit/delete moved from whitelisted wrappers to Frappe's own document API
# (the page calls frappe.client.insert / frappe.client.delete). These helpers do the same thing,
# permission-checked and without ignore_permissions, so the tests exercise the real path.
def save_additional_work(name=None, **fields):
    if name:
        doc = frappe.get_doc("Additional Work", name)
        doc.update(fields)
        doc.save()
    else:
        doc = frappe.get_doc({"doctype": "Additional Work", **fields}).insert()
    return {"success": True, "name": doc.name}


def delete_additional_work(name):
    frappe.delete_doc("Additional Work", name)


def _make_employee(suffix, dept_name, user_email, roles=None):
    company = frappe.db.get_single_value("Global Defaults", "default_company") or "_Test Company"

    if not frappe.db.exists("Department", dept_name):
        frappe.get_doc({
            "doctype": "Department",
            "department_name": "_QA AW Dept",
            "company": company,
        }).insert(ignore_permissions=True, ignore_if_duplicate=True)

    real_dept = frappe.db.get_value(
        "Department", {"department_name": "_QA AW Dept", "company": company}, "name"
    ) or dept_name

    if not frappe.db.exists("User", user_email):
        u = frappe.new_doc("User")
        u.email = user_email
        u.first_name = "Test"
        u.last_name = suffix
        u.send_welcome_email = 0
        u.insert(ignore_permissions=True, ignore_if_duplicate=True)

    if roles:
        user_doc = frappe.get_doc("User", user_email)
        existing = [r.role for r in user_doc.roles]
        for role in roles:
            if role not in existing:
                user_doc.append("roles", {"role": role})
        user_doc.save(ignore_permissions=True)

    existing_emp = frappe.db.get_value("Employee", {"user_id": user_email}, "name")
    if existing_emp:
        return existing_emp

    e = frappe.new_doc("Employee")
    e.first_name = "Test"
    e.last_name = suffix
    e.gender = "Male"
    e.date_of_birth = "1990-01-01"
    e.date_of_joining = "2020-01-01"
    e.department = real_dept
    e.user_id = user_email
    e.insert(ignore_permissions=True)
    return e.name


class TestAdditionalWork(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        company = frappe.db.get_single_value("Global Defaults", "default_company") or "_Test Company"
        cls.dept = (
            frappe.db.get_value("Department", {"department_name": "_QA AW Dept", "company": company}, "name")
            or f"_QA AW Dept - {company}"
        )
        cls.emp_user = "qa_aw_emp@test.example.com"
        cls.other_user = "qa_aw_other@test.example.com"
        cls.emp_name = _make_employee("AW001", cls.dept, cls.emp_user, ["Employee"])
        cls.other_name = _make_employee("AW002", cls.dept, cls.other_user, ["Employee"])
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        frappe.db.sql("DELETE FROM `tabAdditional Work` WHERE employee IN (%s,%s)", (cls.emp_name, cls.other_name))
        frappe.db.sql("DELETE FROM `tabDaily Task Log` WHERE employee=%s", (cls.emp_name,))
        frappe.db.sql("DELETE FROM `tabDaily Task` WHERE employee=%s", (cls.emp_name,))
        frappe.db.sql("DELETE FROM `tabEmployee Checkin` WHERE employee=%s", (cls.emp_name,))
        frappe.db.sql("DELETE FROM `tabEmployee` WHERE name IN (%s,%s)", (cls.emp_name, cls.other_name))
        frappe.db.sql("DELETE FROM `tabUser` WHERE email IN (%s,%s)", (cls.emp_user, cls.other_user))
        frappe.db.commit()

    def setUp(self):
        frappe.set_user("Administrator")
        # These tests are about ownership, hours and overlaps; the "day must be closed" rule has its own tests below.
        self.day_rule = patch("st_attendance_tracker.st_attendance_tracker.doctype.additional_work.additional_work.additional_work_allowed", return_value=(True, "checked_out"))
        self.day_rule.start()
        self.addCleanup(self.day_rule.stop)

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.db.sql("DELETE FROM `tabAdditional Work` WHERE employee IN (%s,%s)", (self.emp_name, self.other_name))
        frappe.db.sql("DELETE FROM `tabDaily Task Log` WHERE employee=%s", (self.emp_name,))
        frappe.db.sql("DELETE FROM `tabDaily Task` WHERE employee=%s", (self.emp_name,))
        frappe.db.sql("DELETE FROM `tabEmployee Checkin` WHERE employee=%s", (self.emp_name,))
        frappe.db.sql("DELETE t FROM `tabTask Entry` t JOIN `tabDaily Work Log` l ON l.name=t.parent WHERE l.employee=%s", (self.emp_name,))
        frappe.db.sql("DELETE FROM `tabDaily Work Log` WHERE employee=%s", (self.emp_name,))
        frappe.db.commit()

    # ── Doctype validation ───────────────────────────────────────────────

    def test_happy_path_insert(self):
        doc = frappe.get_doc({
            "doctype": "Additional Work",
            "employee": self.emp_name,
            "work_date": today(),
            "description": "Fixed prod incident",
            "hours_spent": "1h 30m",
        }).insert(ignore_permissions=True)
        self.assertEqual(doc.employee_name, frappe.db.get_value("Employee", self.emp_name, "employee_name"))
        self.assertAlmostEqual(doc.hours_spent, 1.5)

    def test_hours_spent_survives_a_later_unrelated_save(self):
        """Regression: validate() unconditionally re-parsed hours_spent on
        every save, including one that only touches another field. A doc
        loaded from the DB already holds a parsed float (e.g. 1.5), and
        parse_duration_to_hours treats a bare number as *minutes*, so a
        later Desk-style edit (e.g. just updating remarks) would silently
        divide an already-correct hours_spent by 60."""
        doc = frappe.get_doc({
            "doctype": "Additional Work",
            "employee": self.emp_name,
            "work_date": today(),
            "description": "Original",
            "hours_spent": "1h 30m",
        }).insert(ignore_permissions=True)
        doc.remarks = "Just editing remarks, not hours_spent"
        doc.save(ignore_permissions=True)
        doc.reload()
        self.assertAlmostEqual(doc.hours_spent, 1.5,
            msg="hours_spent must survive a later save that doesn't touch it")

    def test_future_dated_blocked(self):
        doc = frappe.get_doc({
            "doctype": "Additional Work",
            "employee": self.emp_name,
            "work_date": add_days(today(), 1),
            "description": "Future work",
            "hours_spent": "1h",
        })
        with self.assertRaises(frappe.ValidationError):
            doc.insert(ignore_permissions=True)

    def test_backdated_allowed(self):
        doc = frappe.get_doc({
            "doctype": "Additional Work",
            "employee": self.emp_name,
            "work_date": add_days(today(), -30),
            "description": "Old backfilled work",
            "hours_spent": "45m",
        }).insert(ignore_permissions=True)
        self.assertTrue(doc.name)

    def test_missing_description_blocked(self):
        doc = frappe.get_doc({
            "doctype": "Additional Work",
            "employee": self.emp_name,
            "work_date": today(),
            "hours_spent": "1h",
        })
        with self.assertRaises(frappe.ValidationError):
            doc.insert(ignore_permissions=True)

    # ── BOLA ─────────────────────────────────────────────────────────────

    def test_bola_edit_blocked(self):
        other_doc = frappe.get_doc({
            "doctype": "Additional Work",
            "employee": self.other_name,
            "work_date": today(),
            "description": "Other employee's work",
            "hours_spent": "1h",
        }).insert(ignore_permissions=True)

        frappe.set_user(self.emp_user)
        other_doc.description = "Tampered"
        with self.assertRaises(frappe.PermissionError):
            other_doc.save()

    def test_bola_delete_via_api_blocked(self):
        other_doc = frappe.get_doc({
            "doctype": "Additional Work",
            "employee": self.other_name,
            "work_date": today(),
            "description": "Other employee's work",
            "hours_spent": "1h",
        }).insert(ignore_permissions=True)

        frappe.set_user(self.emp_user)
        with self.assertRaises(frappe.PermissionError):
            delete_additional_work(other_doc.name)

    def test_guest_blocked(self):
        frappe.set_user("Guest")
        with self.assertRaises(frappe.PermissionError):
            save_additional_work(work_date=today(), description="x", hours_spent="1h")

    # ── API create/list/update/delete ───────────────────────────────────

    def test_save_creates_owned_entry(self):
        frappe.set_user(self.emp_user)
        r = save_additional_work(work_date=today(), description="API created", hours_spent="2h")
        self.assertTrue(r.get("success"))
        owner = frappe.db.get_value("Additional Work", r["name"], "employee")
        self.assertEqual(owner, self.emp_name)

    def test_save_additional_work_hours_spent_parsed_via_api(self):
        """Regression: save_additional_work used to pre-parse hours_spent
        ('1h 30m' -> 1.5) before assigning it, and AdditionalWork.validate()
        parsed it again on save — parse_duration_to_hours treats an
        already-numeric value as bare minutes (its documented convention for
        unit-less input) and divides by 60, silently shrinking 1.5h to
        0.025h. test_happy_path_insert alone can't catch this: it inserts
        the doctype directly, bypassing this API layer entirely."""
        frappe.set_user(self.emp_user)
        r = save_additional_work(work_date=today(), description="Duration check", hours_spent="1h 30m")
        hours = frappe.db.get_value("Additional Work", r["name"], "hours_spent")
        self.assertAlmostEqual(hours, 1.5)

    def test_save_updates_existing_entry(self):
        frappe.set_user(self.emp_user)
        r = save_additional_work(work_date=today(), description="Original", hours_spent="1h")
        save_additional_work(name=r["name"], work_date=today(), description="Updated", hours_spent="2h")
        desc = frappe.db.get_value("Additional Work", r["name"], "description")
        self.assertEqual(desc, "Updated")

    def test_get_additional_work_scoped_to_caller(self):
        frappe.set_user(self.emp_user)
        save_additional_work(work_date=today(), description="Mine", hours_spent="1h")
        frappe.set_user("Administrator")
        frappe.get_doc({
            "doctype": "Additional Work",
            "employee": self.other_name,
            "work_date": today(),
            "description": "Not mine",
            "hours_spent": "1h",
        }).insert(ignore_permissions=True)

        frappe.set_user(self.emp_user)
        result = get_additional_work()
        descriptions = [e.description for e in result["entries"]]
        self.assertIn("Mine", descriptions)
        self.assertNotIn("Not mine", descriptions)

    def test_delete_removes_entry(self):
        frappe.set_user(self.emp_user)
        r = save_additional_work(work_date=today(), description="To delete", hours_spent="1h")
        delete_additional_work(r["name"])
        self.assertFalse(frappe.db.exists("Additional Work", r["name"]))

    # ── Independence from Daily Task Log ────────────────────────────────

    def test_independent_of_submitted_eod_log(self):
        """Logging additional work for a date with a submitted EOD must not
        raise, and must not touch that log's hours/docstatus."""
        frappe.set_user(self.emp_user)
        import json
        submit_morning_log(new_tasks=json.dumps([{"description": "Task"}]), login_time="09:00", work_location="Office")
        submit_eod_log(lunch_from="", lunch_to="", logout_time="18:00", task_updates="[]", adhoc_tasks="[]")

        eod_before = frappe.db.get_value(
            "Daily Task Log",
            {"employee": self.emp_name, "date": today(), "log_type": "End of Day"},
            ["net_hours", "working_hours", "docstatus"], as_dict=True,
        )

        r = save_additional_work(work_date=today(), description="Extra after checkout", hours_spent="1h")
        self.assertTrue(r.get("success"))

        eod_after = frappe.db.get_value(
            "Daily Task Log",
            {"employee": self.emp_name, "date": today(), "log_type": "End of Day"},
            ["net_hours", "working_hours", "docstatus"], as_dict=True,
        )
        self.assertEqual(eod_before, eod_after)

    def _entry(self, **extra):
        return frappe.get_doc({"doctype": "Additional Work", "employee": self.emp_name, "work_date": today(),
                               "login_time": "19:00:00", "logout_time": "20:00:00", "hours_spent": "1h",
                               "description": "rule test", "status": "Done", **extra})

    def _log(self, **fields):
        frappe.get_doc({"doctype": "Daily Work Log", "employee": self.emp_name, "date": today(), **fields}).insert(ignore_permissions=True)

    def test_blocked_before_checkout_and_without_a_check_in(self):
        self.day_rule.stop()
        with self.assertRaises(frappe.ValidationError):
            self._entry().insert(ignore_permissions=True)

    def test_blocked_while_checked_in_but_not_checked_out(self):
        self.day_rule.stop()
        self._log(morning_submitted=1, login_time="09:30:00")
        with self.assertRaises(frappe.ValidationError):
            self._entry().insert(ignore_permissions=True)

    def test_allowed_after_checkout(self):
        self.day_rule.stop()
        self._log(morning_submitted=1, eod_submitted=1, login_time="09:30:00", logout_time="18:00:00")
        self.assertTrue(self._entry().insert(ignore_permissions=True).name)

    def test_allowed_on_a_leave_day(self):
        self.day_rule.stop()
        with patch("st_attendance_tracker.api._get_employees_on_leave", return_value={self.emp_name}):
            self.assertTrue(self._entry().insert(ignore_permissions=True).name)

    def test_half_day_leave_does_not_open_the_day(self):
        """A half-day leave still has a working half, so check-out is still required."""
        from st_attendance_tracker.st_attendance_tracker.doctype.daily_work_log.test_daily_work_log import (
            _make_approved_half_day_leave,
        )

        self.day_rule.stop()
        leave = _make_approved_half_day_leave(self.emp_name, today())
        try:
            with self.assertRaises(frappe.ValidationError):
                self._entry().insert(ignore_permissions=True)
        finally:
            frappe.db.delete("Leave Application", {"name": leave})

    def test_full_day_leave_application_opens_the_day(self):
        from st_attendance_tracker.st_attendance_tracker.doctype.daily_work_log.test_daily_work_log import (
            _make_approved_half_day_leave,
        )

        self.day_rule.stop()
        leave = _make_approved_half_day_leave(self.emp_name, today())
        frappe.db.set_value("Leave Application", leave, "half_day", 0)
        try:
            self.assertTrue(self._entry().insert(ignore_permissions=True).name)
        finally:
            frappe.db.delete("Leave Application", {"name": leave})

    def test_editing_an_existing_entry_is_not_blocked(self):
        self.day_rule.stop()
        doc = self._entry()
        with patch("st_attendance_tracker.api._get_employees_on_leave", return_value={self.emp_name}):
            doc.insert(ignore_permissions=True)
        doc.reload(); doc.remarks = "later note"
        doc.save(ignore_permissions=True)  # the day is not closed, but this is not a new entry or a new day
        self.assertEqual(doc.remarks, "later note")

    def test_day_context_reports_the_rule(self):
        self.day_rule.stop()
        from st_attendance_tracker.calendar_api import get_day_context

        frappe.set_user(self.emp_user)
        ctx = get_day_context(today())
        self.assertFalse(ctx["can_log"])
        self.assertIn("check out", ctx["blocked_message"])


def tearDownModule():
    # HRMS creates a User Permission for every Employee user, and these tests delete their employees
    # with raw SQL, so the permissions would pile up in the site database run after run.
    frappe.db.sql("DELETE FROM `tabUser Permission` WHERE user LIKE %s", ("%@test.example.com",))
    frappe.db.commit()
