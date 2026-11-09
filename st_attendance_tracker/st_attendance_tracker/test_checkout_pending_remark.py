"""Checkout: every task that is not Done needs a remark the employee wrote."""
import json

import frappe
from unittest.mock import patch
from frappe.tests.utils import FrappeTestCase
from frappe.utils import today

from st_attendance_tracker.api import _has_employee_remark, submit_eod_log, submit_morning_log
from st_attendance_tracker.calendar_api import get_calendar_range
from st_attendance_tracker.st_attendance_tracker.doctype.daily_work_log.test_daily_work_log import (
    _make_employee,
)


class TestCheckoutPendingRemark(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        company = frappe.db.get_single_value("Global Defaults", "default_company") or "_Test Company"
        dept = (
            frappe.db.get_value("Department", {"department_name": "_QA Dept", "company": company}, "name")
            or f"_QA Dept - {company}"
        )
        cls.user = "qa_pend_rem@test.example.com"
        cls.emp = _make_employee("QAPendRem", dept, cls.user, ["Employee"])
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
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Finish report", "estimated_time": "1h"}]),
            login_time="09:00", work_location="Office",
        )
        self.task = get_calendar_range(today(), today())["tasks"][0]

    def tearDown(self):
        frappe.set_user("Administrator")
        self._cleanup()

    def _checkout(self, **update):
        row = {"name": self.task["name"], "status": "Pending", "actual_time": ""}
        row.update(update)
        return submit_eod_log(lunch_from="13:00", lunch_to="14:00", logout_time="18:00",
                              task_updates=json.dumps([row]), adhoc_tasks="[]")

    def test_pending_task_without_remark_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            self._checkout(status="Pending", remarks="")

    def test_blank_and_missing_remark_are_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            self._checkout(status="Pending", remarks="   ")
        with self.assertRaises(frappe.ValidationError):
            self._checkout(status="Pending")

    def test_in_progress_and_dropped_need_a_remark(self):
        with self.assertRaises(frappe.ValidationError):
            self._checkout(status="In Progress", actual_time="30m", remarks="")
        with self.assertRaises(frappe.ValidationError):
            self._checkout(status="Pending", carry_forward=False, remarks="")

    def test_system_rollover_stamp_does_not_count(self):
        with self.assertRaises(frappe.ValidationError):
            self._checkout(status="Pending", remarks="[Carried from 2026-11-02]")

    def test_rejected_checkout_leaves_log_open(self):
        with self.assertRaises(frappe.ValidationError):
            self._checkout(status="Pending", remarks="")
        frappe.db.rollback()
        self.assertEqual(frappe.db.get_value("Daily Work Log", {"employee": self.emp, "date": today()}, "eod_submitted"), 0)

    def test_pending_task_with_remark_checks_out(self):
        result = self._checkout(status="Pending", remarks="Waiting for client data")
        self.assertTrue(result["success"])
        self.assertEqual(result["pending_count"], 1)

    def test_done_task_needs_no_remark(self):
        result = self._checkout(status="Done", actual_time="1h", remarks="")
        self.assertTrue(result["success"])

    def test_helper(self):
        self.assertFalse(_has_employee_remark(None))
        self.assertFalse(_has_employee_remark(" [Auto-carried from 2026-11-02] "))
        self.assertTrue(_has_employee_remark("[Carried from 2026-11-02] blocked by API"))
        self.assertTrue(_has_employee_remark("ok"))
