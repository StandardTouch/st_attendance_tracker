"""Bulk move to backlog: same rules as moving one task, partial success, own tasks only."""
import json

import frappe
from unittest.mock import patch
from frappe.tests.utils import FrappeTestCase
from frappe.utils import today

from st_attendance_tracker.api import get_page_state, get_task_backlog, submit_eod_log, submit_morning_log
from st_attendance_tracker.calendar_api import bulk_move_to_backlog, get_calendar_range
from st_attendance_tracker.st_attendance_tracker.doctype.daily_work_log.test_daily_work_log import (
    _make_employee,
)


class TestBulkMoveToBacklog(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        company = frappe.db.get_single_value("Global Defaults", "default_company") or "_Test Company"
        cls.dept = (
            frappe.db.get_value("Department", {"department_name": "_QA Dept", "company": company}, "name")
            or f"_QA Dept - {company}"
        )
        cls.user_a, cls.user_b = "qa_bm_a@test.example.com", "qa_bm_b@test.example.com"
        cls.emp_a = _make_employee("QABMA", cls.dept, cls.user_a, ["Employee"])
        cls.emp_b = _make_employee("QABMB", cls.dept, cls.user_b, ["Employee"])
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
        e = (cls.emp_a, cls.emp_b)
        frappe.db.sql("DELETE FROM `tabTask Backlog Item` WHERE employee IN (%s,%s)", e)
        frappe.db.sql("DELETE FROM `tabTask Entry` WHERE parent IN (SELECT name FROM `tabDaily Work Log` WHERE employee IN (%s,%s))", e)
        frappe.db.sql("DELETE FROM `tabDaily Work Log` WHERE employee IN (%s,%s)", e)
        frappe.db.sql("DELETE FROM `tabEmployee Checkin` WHERE employee IN (%s,%s)", e)
        frappe.db.commit()

    def setUp(self):
        frappe.set_user("Administrator")
        # These flows run on the real clock; pin the site's checkout cutoff so the lock does not interfere.
        patcher = patch("st_attendance_tracker.time_utils.get_checkout_cutoff", return_value=24 * 60 - 1)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._cleanup()

    def tearDown(self):
        frappe.set_user("Administrator")
        self._cleanup()

    def _check_in(self, user, *descriptions):
        frappe.set_user(user)
        submit_morning_log(new_tasks=json.dumps([{"description": d, "estimated_time": "1h"} for d in descriptions]),
                           login_time="09:00", work_location="Office")
        return {t["description"]: t["name"] for t in get_calendar_range(today(), today())["tasks"]}

    def test_moves_all_eligible_tasks(self):
        tasks = self._check_in(self.user_a, "One", "Two", "Three")
        result = bulk_move_to_backlog(json.dumps(list(tasks.values())))
        self.assertEqual(sorted(result["moved"]), sorted(tasks.values()))
        self.assertEqual(result["skipped"], [])
        self.assertEqual(sorted(b["description"] for b in get_task_backlog()), ["One", "Three", "Two"])
        self.assertEqual(get_calendar_range(today(), today())["tasks"], [])

    def test_done_tasks_are_skipped_and_the_rest_move(self):
        tasks = self._check_in(self.user_a, "Finished", "Open")
        from st_attendance_tracker.api import autosave_eod_progress
        autosave_eod_progress(task_updates=json.dumps([{"name": tasks["Finished"], "status": "Done", "actual_time": "1h"}]))
        result = bulk_move_to_backlog([tasks["Finished"], tasks["Open"]])
        self.assertEqual(result["moved"], [tasks["Open"]])
        self.assertEqual([s["name"] for s in result["skipped"]], [tasks["Finished"]])
        self.assertIn("Completed", result["skipped"][0]["reason"])
        self.assertEqual([t["description"] for t in get_calendar_range(today(), today())["tasks"]], ["Finished"])

    def test_other_employees_tasks_are_refused_and_untouched(self):
        mine = self._check_in(self.user_b, "B's task")
        frappe.set_user(self.user_a)
        result = bulk_move_to_backlog([mine["B's task"]])
        self.assertEqual(result["moved"], [])
        self.assertEqual(len(result["skipped"]), 1)
        frappe.set_user(self.user_b)
        self.assertEqual([t["description"] for t in get_calendar_range(today(), today())["tasks"]], ["B's task"])
        self.assertEqual(get_task_backlog(), [])

    def test_nothing_moves_after_checkout(self):
        tasks = self._check_in(self.user_a, "Late one")
        submit_eod_log(lunch_from="13:00", lunch_to="14:00", logout_time="18:00",
                       task_updates=json.dumps([{"name": tasks["Late one"], "status": "Pending", "actual_time": "", "carry_forward": True}]),
                       adhoc_tasks="[]")
        result = bulk_move_to_backlog([tasks["Late one"]])
        self.assertEqual(result["moved"], [])
        self.assertIn("checkout", result["skipped"][0]["reason"].lower())

    def test_input_is_validated(self):
        frappe.set_user(self.user_a)
        for bad in ([], "[]", json.dumps(["x"] * 101)):
            with self.assertRaises(frappe.ValidationError):
                bulk_move_to_backlog(bad)
        result = bulk_move_to_backlog(["does-not-exist", "does-not-exist"])  # unknown names: skipped once, no crash
        self.assertEqual(result["moved"], [])
        self.assertEqual(len(result["skipped"]), 1)


def tearDownModule():
    # HRMS creates a User Permission for every Employee user, and these tests delete their employees
    # with raw SQL, so the permissions would pile up in the site database run after run.
    frappe.db.sql("DELETE FROM `tabUser Permission` WHERE user LIKE %s", ("%@test.example.com",))
    frappe.db.commit()
