"""Calendar endpoints: range read and task scheduling (own tasks only, locked after checkout)."""
import json

import frappe
from unittest.mock import patch
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, today

from st_attendance_tracker.api import get_page_state, submit_eod_log, submit_morning_log
from st_attendance_tracker.calendar_api import get_calendar_range, set_task_schedule
from st_attendance_tracker.st_attendance_tracker.doctype.daily_work_log.test_daily_work_log import (
    _make_employee,
)


class TestCalendarApi(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        company = frappe.db.get_single_value("Global Defaults", "default_company") or "_Test Company"
        cls.dept = (
            frappe.db.get_value("Department", {"department_name": "_QA Dept", "company": company}, "name")
            or f"_QA Dept - {company}"
        )
        cls.user_a = "qa_cal_a@test.example.com"
        cls.user_b = "qa_cal_b@test.example.com"
        cls.emp_a = _make_employee("QACalA", cls.dept, cls.user_a, ["Employee"])
        cls.emp_b = _make_employee("QACalB", cls.dept, cls.user_b, ["Employee"])
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
        frappe.db.sql(
            "DELETE FROM `tabTask Entry` WHERE parent IN "
            "(SELECT name FROM `tabDaily Work Log` WHERE employee IN (%s,%s))", emps)
        frappe.db.sql("DELETE FROM `tabDaily Work Log` WHERE employee IN (%s,%s)", emps)
        frappe.db.sql("DELETE FROM `tabEmployee Checkin` WHERE employee IN (%s,%s)", emps)
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
        submit_morning_log(
            new_tasks=json.dumps([{"description": d, "estimated_time": "1h"} for d in descriptions]),
            login_time="09:00", work_location="Office",
        )
        return get_calendar_range(today(), today())["tasks"]

    # ── range read ─────────────────────────────────────────────────────────────

    def test_range_returns_own_tasks_and_log(self):
        tasks = self._check_in(self.user_a, "Calendar task one", "Calendar task two")
        self.assertEqual([t["description"] for t in tasks], ["Calendar task one", "Calendar task two"])
        self.assertTrue(all(t["date"] == today() and t["start_time"] is None for t in tasks))
        result = get_calendar_range(today(), today())
        self.assertEqual(result["logs"][0]["login_time"], "09:00")
        self.assertTrue(result["logs"][0]["morning_submitted"])

    def test_range_never_includes_another_employees_tasks(self):
        self._check_in(self.user_a, "Private to A")
        frappe.set_user(self.user_b)
        result = get_calendar_range(today(), today())
        self.assertEqual(result["tasks"], [])
        self.assertEqual(result["logs"], [])

    def test_range_validation(self):
        frappe.set_user(self.user_a)
        with self.assertRaises(frappe.ValidationError):
            get_calendar_range(today(), add_days(today(), -1))
        with self.assertRaises(frappe.ValidationError):
            get_calendar_range(add_days(today(), -100), today())

    # ── scheduling ─────────────────────────────────────────────────────────────

    def test_set_schedule_moves_and_resizes(self):
        task = self._check_in(self.user_a, "Move me")[0]
        result = set_task_schedule(task["name"], start_time="14:30", estimated_hours=1.5)
        self.assertEqual(result["start_time"], "14:30")
        self.assertEqual(result["estimated_time"], 1.5)
        again = get_calendar_range(today(), today())["tasks"][0]
        self.assertEqual(again["start_time"], "14:30")
        self.assertEqual(again["estimated_time"], 1.5)

    def test_set_schedule_keeps_description_and_status(self):
        task = self._check_in(self.user_a, "Stays the same")[0]
        set_task_schedule(task["name"], start_time="10:15")
        again = get_calendar_range(today(), today())["tasks"][0]
        self.assertEqual((again["description"], again["status"]), ("Stays the same", "Pending"))

    def test_set_schedule_rejects_bad_input(self):
        task = self._check_in(self.user_a, "Validate me")[0]
        for kwargs in ({"start_time": "25:00"}, {"start_time": "9am"}, {"estimated_hours": 0}, {"estimated_hours": 30},
                       {"estimated_hours": "abc"}):
            with self.assertRaises(frappe.ValidationError, msg=str(kwargs)):
                set_task_schedule(task["name"], **kwargs)

    def test_set_schedule_denied_for_other_employee(self):
        task = self._check_in(self.user_a, "A's task")[0]
        frappe.set_user(self.user_b)
        with self.assertRaises(frappe.PermissionError):
            set_task_schedule(task["name"], start_time="11:00")
        frappe.set_user(self.user_a)
        self.assertIsNone(get_calendar_range(today(), today())["tasks"][0]["start_time"])

    def test_set_schedule_denied_for_unknown_task(self):
        frappe.set_user(self.user_a)
        with self.assertRaises(frappe.PermissionError):
            set_task_schedule("does-not-exist", start_time="11:00")

    def test_set_schedule_blocked_after_checkout(self):
        task = self._check_in(self.user_a, "Locked later")[0]
        submit_eod_log(
            lunch_from="13:00", lunch_to="14:00", logout_time="18:00",
            task_updates=json.dumps([{"name": task["name"], "status": "Done", "actual_time": "1h"}]),
            adhoc_tasks="[]",
        )
        with self.assertRaises(frappe.ValidationError):
            set_task_schedule(task["name"], start_time="11:00")

    def test_page_state_exposes_start_time(self):
        task = self._check_in(self.user_a, "Contract check")[0]
        set_task_schedule(task["name"], start_time="12:00")
        state = get_page_state()
        self.assertEqual(state["tasks"][0]["start_time"], "12:00")


class TestUpdateTask(TestCalendarApi):
    """Editing a task from the calendar."""

    def test_edit_fields(self):
        from st_attendance_tracker.calendar_api import update_task

        task = self._check_in(self.user_a, "Original")[0]
        result = update_task(task["name"], description="  Renamed ", project_name="Proj", remarks="note",
                             estimated_hours=2, start_time="13:15")
        self.assertEqual((result["description"], result["project_name"], result["remarks"]), ("Renamed", "Proj", "note"))
        self.assertEqual((result["estimated_time"], result["start_time"]), (2, "13:15"))
        again = get_calendar_range(today(), today())["tasks"][0]
        self.assertEqual((again["description"], again["status"]), ("Renamed", "Pending"))

    def test_only_given_fields_change(self):
        from st_attendance_tracker.calendar_api import update_task

        task = self._check_in(self.user_a, "Keep me")[0]
        update_task(task["name"], remarks="only remarks")
        again = get_calendar_range(today(), today())["tasks"][0]
        self.assertEqual((again["description"], again["remarks"]), ("Keep me", "only remarks"))

    def test_rejects_bad_input_and_other_employees_and_locked_days(self):
        from st_attendance_tracker.calendar_api import update_task

        task = self._check_in(self.user_a, "Guarded")[0]
        with self.assertRaises(frappe.ValidationError):
            update_task(task["name"], description="   ")
        with self.assertRaises(frappe.ValidationError):
            update_task(task["name"], estimated_hours=99)
        with self.assertRaises(frappe.ValidationError):
            update_task(task["name"], start_time="7pm")
        frappe.set_user(self.user_b)
        with self.assertRaises(frappe.PermissionError):
            update_task(task["name"], description="hijack")
        with self.assertRaises(frappe.PermissionError):
            update_task("nope", description="x")
        frappe.set_user(self.user_a)
        submit_eod_log(lunch_from="13:00", lunch_to="14:00", logout_time="18:00",
                       task_updates=json.dumps([{"name": task["name"], "status": "Done", "actual_time": "1h"}]), adhoc_tasks="[]")
        with self.assertRaises(frappe.ValidationError):
            update_task(task["name"], remarks="too late")

    def test_recurring_task_text_is_protected(self):
        from st_attendance_tracker.calendar_api import update_task

        task = self._check_in(self.user_a, "From template")[0]
        frappe.db.set_value("Task Entry", task["name"], "task_type", "Recurring")
        with self.assertRaises(frappe.ValidationError):
            update_task(task["name"], description="Changed")
        with self.assertRaises(frappe.ValidationError):
            update_task(task["name"], project_name="Other")
        update_task(task["name"], description="From template", remarks="allowed", start_time="10:00")  # unchanged text + other fields is fine


def tearDownModule():
    # HRMS creates a User Permission for every Employee user, and these tests delete their employees
    # with raw SQL, so the permissions would pile up in the site database run after run.
    frappe.db.sql("DELETE FROM `tabUser Permission` WHERE user LIKE %s", ("%@test.example.com",))
    frappe.db.commit()
