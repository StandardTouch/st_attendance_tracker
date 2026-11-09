"""Reset check-in is allowed only for 60 minutes after the real check-in."""
import json
from datetime import timedelta

import frappe
from unittest.mock import patch
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime, today

from st_attendance_tracker.api import (
    RESET_WINDOW_MINUTES, _reset_seconds_left, get_page_state, reset_morning_checkin, submit_morning_log,
)
from st_attendance_tracker.st_attendance_tracker.doctype.daily_work_log.test_daily_work_log import (
    _make_employee,
)


class TestResetWindow(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        company = frappe.db.get_single_value("Global Defaults", "default_company") or "_Test Company"
        dept = (
            frappe.db.get_value("Department", {"department_name": "_QA Dept", "company": company}, "name")
            or f"_QA Dept - {company}"
        )
        cls.user = "qa_reset_win@test.example.com"
        cls.emp = _make_employee("QAResetWin", dept, cls.user, ["Employee"])
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        cls._cleanup()
        frappe.db.sql("DELETE FROM `tabEmployee` WHERE name=%s", cls.emp)
        frappe.db.sql("DELETE FROM `tabUser` WHERE email=%s", cls.user)
        frappe.db.commit()

    @classmethod
    def _cleanup(cls):
        frappe.db.sql(
            "DELETE FROM `tabTask Entry` WHERE parent IN "
            "(SELECT name FROM `tabDaily Work Log` WHERE employee=%s)", cls.emp)
        frappe.db.sql("DELETE FROM `tabDaily Work Log` WHERE employee=%s", cls.emp)
        frappe.db.sql("DELETE FROM `tabEmployee Checkin` WHERE employee=%s", cls.emp)
        frappe.db.commit()

    def setUp(self):
        frappe.set_user("Administrator")
        patcher = patch("st_attendance_tracker.time_utils.get_checkout_cutoff", return_value=24 * 60 - 1)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._cleanup()
        frappe.set_user(self.user)
        submit_morning_log(new_tasks=json.dumps([{"description": "Plan day"}]), login_time="09:00", work_location="Office")

    def tearDown(self):
        frappe.set_user("Administrator")
        self._cleanup()

    def _age_checkin(self, minutes):
        """Make the check-in row look `minutes` old (the gate reads its creation time)."""
        frappe.db.sql(
            "UPDATE `tabEmployee Checkin` SET creation=%s WHERE employee=%s",
            (now_datetime() - timedelta(minutes=minutes), self.emp),
        )

    def test_fresh_checkin_has_nearly_the_full_window(self):
        left = _reset_seconds_left(self.emp, today())
        self.assertTrue(RESET_WINDOW_MINUTES * 60 - 30 <= left <= RESET_WINDOW_MINUTES * 60)

    def test_reset_works_inside_the_window(self):
        self._age_checkin(59)
        self.assertTrue(reset_morning_checkin()["success"])

    def test_reset_is_rejected_after_the_window(self):
        self._age_checkin(61)
        self.assertEqual(_reset_seconds_left(self.emp, today()), 0)
        with self.assertRaises(frappe.ValidationError):
            reset_morning_checkin()
        self.assertEqual(frappe.db.get_value("Daily Work Log", {"employee": self.emp, "date": today()}, "morning_submitted"), 1)

    def test_login_time_field_does_not_move_the_window(self):
        # login_time was typed as 09:00 (hours ago); only the real submit moment counts.
        self.assertGreater(_reset_seconds_left(self.emp, today()), 0)

    def test_page_state_reports_seconds_left(self):
        self.assertGreater(get_page_state()["reset_seconds_left"], 0)
        self._age_checkin(90)
        self.assertEqual(get_page_state()["reset_seconds_left"], 0)

    def test_a_second_reset_on_the_same_day_is_refused(self):
        self.assertTrue(reset_morning_checkin()["success"])
        submit_morning_log(new_tasks=json.dumps([{"description": "again"}]), login_time="09:00", work_location="Office")
        self.assertGreater(_reset_seconds_left(self.emp, today()), 0)       # a fresh window is open...
        with self.assertRaises(frappe.ValidationError):                       # ...but the daily limit still applies
            reset_morning_checkin()
        self.assertEqual(frappe.db.get_value("Daily Work Log", {"employee": self.emp, "date": today()}, "morning_submitted"), 1)

    def test_no_checkin_row_means_no_reset(self):
        frappe.db.sql("DELETE FROM `tabEmployee Checkin` WHERE employee=%s", self.emp)
        self.assertEqual(_reset_seconds_left(self.emp, today()), 0)
