"""Time bank phase 1: credits from overtime and additional work, balance, expiry, permissions."""
import json

import frappe
from unittest.mock import patch
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, today

from st_attendance_tracker.api import submit_eod_log, submit_morning_log
from st_attendance_tracker.calendar_api import get_calendar_range
from st_attendance_tracker.st_attendance_tracker.doctype.daily_work_log.test_daily_work_log import (
    _make_employee,
)
from st_attendance_tracker.time_bank import (
    expiry_days, get_balance, overtime_hours, remove_additional_work_credit, workday_hours,
)


def credits(employee, **extra):
    return frappe.get_all("Extra Hours Entry", filters={"employee": employee, "entry_type": "Credit", **extra},
                          fields=["name", "hours", "used_hours", "source", "date", "expires_on",
                                  "source_log", "source_additional_work"])


class TestTimeBank(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        company = frappe.db.get_single_value("Global Defaults", "default_company") or "_Test Company"
        cls.dept = (
            frappe.db.get_value("Department", {"department_name": "_QA Dept", "company": company}, "name")
            or f"_QA Dept - {company}"
        )
        cls.user_a, cls.user_b, cls.user_hr = (f"qa_tb_{k}@test.example.com" for k in ("a", "b", "hr"))
        cls.emp_a = _make_employee("QATbA", cls.dept, cls.user_a, ["Employee"])
        cls.emp_b = _make_employee("QATbB", cls.dept, cls.user_b, ["Employee"])
        cls.emp_hr = _make_employee("QATbHR", cls.dept, cls.user_hr, ["Employee", "HR Manager"])
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        cls._cleanup()
        for e in (cls.emp_a, cls.emp_b, cls.emp_hr):
            frappe.db.sql("DELETE FROM `tabEmployee` WHERE name=%s", e)
        for u in (cls.user_a, cls.user_b, cls.user_hr):
            frappe.db.sql("DELETE FROM `tabUser` WHERE email=%s", u)
        frappe.db.commit()

    @classmethod
    def _cleanup(cls):
        names = (cls.emp_a, cls.emp_b, cls.emp_hr)
        frappe.db.sql("DELETE FROM `tabExtra Hours Entry` WHERE employee IN %s", (names,))
        frappe.db.sql("DELETE FROM `tabAdditional Work` WHERE employee IN %s", (names,))
        frappe.db.sql("DELETE FROM `tabTask Entry` WHERE parent IN (SELECT name FROM `tabDaily Work Log` WHERE employee IN %s)", (names,))
        frappe.db.sql("DELETE FROM `tabDaily Work Log` WHERE employee IN %s", (names,))
        frappe.db.sql("DELETE FROM `tabEmployee Checkin` WHERE employee IN %s", (names,))
        frappe.db.commit()

    def setUp(self):
        frappe.set_user("Administrator")
        patcher = patch("st_attendance_tracker.time_utils.get_checkout_cutoff", return_value=24 * 60 - 1)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._cleanup()

    def tearDown(self):
        frappe.set_user("Administrator")
        self._cleanup()

    def _day(self, user, task_time, **eod_extra):
        frappe.set_user(user)
        submit_morning_log(new_tasks=json.dumps([{"description": "job"}]), login_time="09:00", work_location="Office")
        task = get_calendar_range(today(), today())["tasks"][0]
        submit_eod_log(lunch_from="", lunch_to="", logout_time="23:00", adhoc_tasks="[]",
                       task_updates=json.dumps([{"name": task["name"], "status": "Done", "actual_time": task_time}]))
        frappe.set_user("Administrator")

    def _additional_work(self, user, hours, work_date=None, project="Extra"):
        frappe.set_user(user)
        doc = frappe.get_doc({"doctype": "Additional Work", "work_date": work_date or today(), "hours_spent": hours,
                              "project_name": project, "description": "extra work", "status": "Done",
                              "login_time": "19:00:00", "logout_time": "20:00:00"})
        with patch("st_attendance_tracker.additional_work_mail.queue_additional_work_email"):
            doc.insert()
        frappe.set_user("Administrator")
        return doc

    # ── overtime credits ──────────────────────────────────────────────────────

    def test_overtime_over_eight_hours_is_banked(self):
        self._day(self.user_a, "9h")
        rows = credits(self.emp_a, source="Overtime")
        self.assertEqual([(r.hours, r.used_hours, str(r.date)) for r in rows], [(1.0, 0.0, today())])
        self.assertEqual(get_balance(self.emp_a), 1.0)

    def test_no_credit_at_or_below_the_working_day(self):
        self._day(self.user_a, "8h")
        self.assertEqual(credits(self.emp_a), [])
        self.assertEqual(get_balance(self.emp_a), 0.0)

    def test_half_day_uses_a_four_hour_threshold(self):
        frappe.set_user(self.user_a)
        submit_morning_log(new_tasks=json.dumps([{"description": "half"}]), login_time="09:00", work_location="Office")
        # check-in only accepts a half-day session for an approved leave; set it directly here
        frappe.db.set_value("Daily Work Log", {"employee": self.emp_a, "date": today()}, "half_day_session", "First Half")
        task = get_calendar_range(today(), today())["tasks"][0]
        submit_eod_log(lunch_from="", lunch_to="", logout_time="23:00", adhoc_tasks="[]",
                       task_updates=json.dumps([{"name": task["name"], "status": "Done", "actual_time": "5h"}]))
        frappe.set_user("Administrator")
        self.assertEqual([r.hours for r in credits(self.emp_a, source="Overtime")], [1.0])

    def test_overtime_credit_is_idempotent(self):
        from st_attendance_tracker.time_bank import sync_overtime_credit

        self._day(self.user_a, "10h")
        log = frappe.get_doc("Daily Work Log", {"employee": self.emp_a, "date": today()})
        sync_overtime_credit(log)
        sync_overtime_credit(log)
        rows = credits(self.emp_a, source="Overtime")
        self.assertEqual([r.hours for r in rows], [2.0])

    def test_checkout_survives_a_bank_failure(self):
        with patch("st_attendance_tracker.time_bank.sync_overtime_credit", side_effect=RuntimeError("boom")):
            self._day(self.user_a, "9h")
        self.assertEqual(frappe.db.get_value("Daily Work Log", {"employee": self.emp_a, "date": today()}, "eod_submitted"), 1)
        self.assertEqual(credits(self.emp_a), [])

    # ── additional work credits ───────────────────────────────────────────────

    def test_additional_work_is_banked_and_follows_edits(self):
        self._day(self.user_a, "2h")
        doc = self._additional_work(self.user_a, 2)
        rows = credits(self.emp_a, source="Additional Work")
        self.assertEqual([(r.hours, r.source_additional_work) for r in rows], [(2.0, doc.name)])
        doc.reload(); doc.hours_spent = 3; doc.save(ignore_permissions=True)
        self.assertEqual([r.hours for r in credits(self.emp_a, source="Additional Work")], [3.0])

    def test_deleting_additional_work_removes_its_credit(self):
        self._day(self.user_a, "2h")
        doc = self._additional_work(self.user_a, 2)
        frappe.set_user(self.user_a)
        frappe.delete_doc("Additional Work", doc.name)
        frappe.set_user("Administrator")
        self.assertEqual(credits(self.emp_a), [])

    def test_used_credit_blocks_delete_and_is_not_reduced_below_used(self):
        self._day(self.user_a, "2h")
        doc = self._additional_work(self.user_a, 2)
        name = credits(self.emp_a)[0].name
        frappe.db.set_value("Extra Hours Entry", name, "used_hours", 1.5)
        with self.assertRaises(frappe.ValidationError):
            remove_additional_work_credit(doc)
        doc.reload(); doc.hours_spent = 1; doc.save(ignore_permissions=True)
        self.assertEqual(credits(self.emp_a)[0].hours, 1.5)  # never below what was already taken

    def test_zero_hours_additional_work_banks_nothing(self):
        self._day(self.user_a, "2h")
        self._additional_work(self.user_a, 0)
        self.assertEqual(credits(self.emp_a), [])

    # ── balance and expiry ────────────────────────────────────────────────────

    def test_balance_adds_overtime_and_additional_work_minus_used(self):
        self._day(self.user_a, "9h")                      # 1h overtime
        self._additional_work(self.user_a, 2)             # 2h additional
        self.assertEqual(get_balance(self.emp_a), 3.0)
        frappe.db.set_value("Extra Hours Entry", credits(self.emp_a, source="Additional Work")[0].name, "used_hours", 1)
        self.assertEqual(get_balance(self.emp_a), 2.0)

    def test_balance_is_per_employee(self):
        self._day(self.user_a, "9h")
        self.assertEqual(get_balance(self.emp_b), 0.0)

    def test_credit_expires_after_the_window(self):
        self._day(self.user_a, "9h")
        row = credits(self.emp_a)[0]
        self.assertEqual(str(row.expires_on), add_days(today(), expiry_days()))
        self.assertEqual(get_balance(self.emp_a, as_of=add_days(today(), expiry_days())), 1.0)       # last valid day
        self.assertEqual(get_balance(self.emp_a, as_of=add_days(today(), expiry_days() + 1)), 0.0)   # expired

    def test_expiry_days_default_and_setting(self):
        with patch("st_attendance_tracker.api._get_attendance_settings", return_value={"time_bank_expiry_days": 0}):
            self.assertEqual(expiry_days(), 60)
        with patch("st_attendance_tracker.api._get_attendance_settings", return_value={"time_bank_expiry_days": 30}):
            self.assertEqual(expiry_days(), 30)

    def test_workday_hours_follows_the_settings(self):
        with patch("st_attendance_tracker.api._get_attendance_settings", return_value={"standard_workday_hours": 9}):
            self.assertEqual(workday_hours(self.emp_a, today()), 9.0)
        with patch("st_attendance_tracker.api._get_attendance_settings", return_value={}):
            self.assertEqual(workday_hours(self.emp_a, today()), 8.0)

    # ── validation and permissions ────────────────────────────────────────────

    def test_entry_validation(self):
        base = {"doctype": "Extra Hours Entry", "employee": self.emp_a, "date": today(), "entry_type": "Credit", "source": "Overtime"}
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({**base, "hours": 0}).insert()
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({**base, "hours": 1, "source": "Compensation"}).insert()
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({**base, "hours": 1, "used_hours": 2}).insert()
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({**base, "hours": 1, "entry_type": "Use", "source": "Overtime"}).insert()

    def test_employee_sees_only_own_entries_and_cannot_write(self):
        self._day(self.user_a, "9h")
        self._day(self.user_b, "10h")
        frappe.set_user(self.user_a)
        mine = frappe.get_list("Extra Hours Entry", fields=["employee"])
        self.assertTrue(mine and all(r.employee == self.emp_a for r in mine))
        with self.assertRaises(frappe.PermissionError):
            frappe.get_doc({"doctype": "Extra Hours Entry", "employee": self.emp_a, "date": today(), "entry_type": "Credit",
                            "source": "Overtime", "hours": 5}).insert()
        # HR reads everything through the report API; here only the role-level right is checked
        self.assertTrue(frappe.has_permission("Extra Hours Entry", "read", user=self.user_hr))
        self.assertFalse(frappe.has_permission("Extra Hours Entry", "write", user=self.user_a))
