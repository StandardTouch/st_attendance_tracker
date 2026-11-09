"""Unsent check-in plan is saved on the server so any device can restore it."""
import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import today

from st_attendance_tracker.api import (
    _get_morning_draft, get_page_state, save_morning_draft, submit_morning_log,
)
from st_attendance_tracker.st_attendance_tracker.doctype.daily_work_log.test_daily_work_log import _make_employee


class TestMorningDraft(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        company = frappe.db.get_single_value("Global Defaults", "default_company") or "_Test Company"
        cls.dept = (
            frappe.db.get_value("Department", {"department_name": "_QA Dept", "company": company}, "name")
            or f"_QA Dept - {company}"
        )
        cls.user_a, cls.user_b = "qa_draft_a@test.example.com", "qa_draft_b@test.example.com"
        cls.emp_a = _make_employee("QADraftA", cls.dept, cls.user_a, ["Employee"])
        cls.emp_b = _make_employee("QADraftB", cls.dept, cls.user_b, ["Employee"])
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        cls._cleanup()
        frappe.db.sql("DELETE FROM `tabEmployee` WHERE name IN (%s,%s)", (cls.emp_a, cls.emp_b))
        frappe.db.sql("DELETE FROM `tabUser` WHERE email IN (%s,%s)", (cls.user_a, cls.user_b))
        frappe.db.commit()

    @classmethod
    def _cleanup(cls):
        emps = (cls.emp_a, cls.emp_b)
        frappe.db.sql("DELETE FROM `tabTask Entry` WHERE parent IN "
                      "(SELECT name FROM `tabDaily Work Log` WHERE employee IN (%s,%s))", emps)
        frappe.db.sql("DELETE FROM `tabDaily Work Log` WHERE employee IN (%s,%s)", emps)
        frappe.db.sql("DELETE FROM `tabEmployee Checkin` WHERE employee IN (%s,%s)", emps)
        frappe.db.sql("DELETE FROM `tabDefaultValue` WHERE defkey='st_morning_draft' AND parent IN (%s,%s)",
                      (cls.user_a, cls.user_b))
        frappe.db.commit()

    def setUp(self):
        frappe.set_user("Administrator")
        self._cleanup()
        frappe.set_user(self.user_a)

    def tearDown(self):
        frappe.set_user("Administrator")
        self._cleanup()

    ROWS = {"rows": [{"d": "Plan the release", "p": "Billing", "e": "2h"}]}

    def test_saved_draft_comes_back_in_the_page_state(self):
        self.assertEqual(save_morning_draft("calendar", json.dumps(self.ROWS)), {"saved": True})
        state = get_page_state()
        self.assertEqual(state["morning_draft"], {"kind": "calendar", "data": self.ROWS})

    def test_no_draft_by_default(self):
        self.assertIsNone(get_page_state()["morning_draft"])

    def test_empty_data_clears_the_draft(self):
        save_morning_draft("calendar", json.dumps(self.ROWS))
        save_morning_draft("calendar", "")
        self.assertIsNone(_get_morning_draft())

    def test_draft_is_private_to_its_owner(self):
        save_morning_draft("classic", json.dumps(self.ROWS))
        frappe.set_user(self.user_b)
        self.assertIsNone(get_page_state()["morning_draft"])

    def test_draft_from_another_day_is_ignored(self):
        frappe.defaults.set_user_default(
            "st_morning_draft", json.dumps({"date": "2020-01-01", "kind": "calendar", "data": self.ROWS}), self.user_a)
        self.assertIsNone(_get_morning_draft())

    def test_bad_input_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            save_morning_draft("other", "{}")
        with self.assertRaises(frappe.ValidationError):
            save_morning_draft("calendar", "{not json")
        with self.assertRaises(frappe.ValidationError):
            save_morning_draft("calendar", json.dumps({"x": "y" * 30000}))

    def test_check_in_clears_the_draft_and_later_saves_are_ignored(self):
        save_morning_draft("calendar", json.dumps(self.ROWS))
        submit_morning_log(new_tasks=json.dumps([{"description": "Real task", "project_name": "P", "estimated_time": "1h"}]),
                           login_time="09:00", work_location="Office")
        self.assertIsNone(_get_morning_draft())
        self.assertEqual(save_morning_draft("calendar", json.dumps(self.ROWS)), {"saved": False})
        self.assertIsNone(get_page_state()["morning_draft"])
