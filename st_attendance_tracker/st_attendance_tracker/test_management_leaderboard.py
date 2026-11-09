"""Management dashboard leaderboard ranks by task hours, not clock net hours."""
import json

import frappe
from unittest.mock import patch
from frappe.tests.utils import FrappeTestCase
from frappe.utils import today

from st_attendance_tracker.api import (
    _task_minutes_by_log, get_management_dashboard, submit_eod_log, submit_morning_log,
)
from st_attendance_tracker.calendar_api import get_calendar_range
from st_attendance_tracker.st_attendance_tracker.doctype.daily_work_log.test_daily_work_log import (
    _make_employee,
)


class TestManagementLeaderboard(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        company = frappe.db.get_single_value("Global Defaults", "default_company") or "_Test Company"
        dept = (
            frappe.db.get_value("Department", {"department_name": "_QA Dept", "company": company}, "name")
            or f"_QA Dept - {company}"
        )
        cls.users = {k: f"qa_lb_{k}@test.example.com" for k in ("a", "b", "c")}
        cls.emps = {k: _make_employee(f"QALb{k.upper()}", dept, u, ["Employee"]) for k, u in cls.users.items()}
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        cls._cleanup()
        for e in cls.emps.values():
            frappe.db.sql("DELETE FROM `tabEmployee` WHERE name=%s", e)
        for u in cls.users.values():
            frappe.db.sql("DELETE FROM `tabUser` WHERE email=%s", u)
        frappe.db.commit()

    @classmethod
    def _cleanup(cls):
        names = tuple(cls.emps.values())
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

    def _day(self, key, login, logout, task_time, lunch=("", "")):
        frappe.set_user(self.users[key])
        submit_morning_log(new_tasks=json.dumps([{"description": f"work {key}"}]), login_time=login, work_location="Office")
        task = get_calendar_range(today(), today())["tasks"][0]
        submit_eod_log(lunch_from=lunch[0], lunch_to=lunch[1], logout_time=logout,
                       task_updates=json.dumps([{"name": task["name"], "status": "Done", "actual_time": task_time}]),
                       adhoc_tasks="[]")
        frappe.set_user("Administrator")

    def _rank(self):
        return {r["employee"]: r for r in get_management_dashboard(today())["rankings"]}

    def test_ranks_by_task_hours_even_when_net_hours_say_otherwise(self):
        self._day("a", "09:00", "20:00", "2h")   # long clock span, little task time
        self._day("b", "09:00", "13:00", "6h")   # short clock span, lots of task time
        ranking = get_management_dashboard(today())["rankings"]
        mine = [r for r in ranking if r["employee"] in self.emps.values()]
        self.assertEqual([r["employee"] for r in mine], [self.emps["b"], self.emps["a"]])
        by = self._rank()
        self.assertEqual((by[self.emps["b"]]["task_minutes"], by[self.emps["b"]]["task_hours"]), (360, "6h"))
        self.assertEqual(by[self.emps["a"]]["task_minutes"], 120)

    def test_sums_every_task_and_formats_minutes(self):
        frappe.set_user(self.users["c"])
        submit_morning_log(new_tasks=json.dumps([{"description": "one"}, {"description": "two"}]),
                           login_time="09:00", work_location="Office")
        tasks = get_calendar_range(today(), today())["tasks"]
        submit_eod_log(lunch_from="", lunch_to="", logout_time="18:00", adhoc_tasks="[]",
                       task_updates=json.dumps([{"name": tasks[0]["name"], "status": "Done", "actual_time": "1h 30m"},
                                                {"name": tasks[1]["name"], "status": "Done", "actual_time": "45m"}]))
        frappe.set_user("Administrator")
        row = self._rank()[self.emps["c"]]
        self.assertEqual((row["task_minutes"], row["task_hours"]), (135, "2h 15m"))

    def test_only_checked_out_employees_are_ranked(self):
        frappe.set_user(self.users["a"])
        submit_morning_log(new_tasks=json.dumps([{"description": "still working"}]), login_time="09:00", work_location="Office")
        frappe.set_user("Administrator")
        self.assertNotIn(self.emps["a"], self._rank())

    def test_helper_handles_empty_input(self):
        self.assertEqual(_task_minutes_by_log([]), {})

    def test_overtime_follows_the_half_day_rule(self):
        self._day("a", "09:00", "20:00", "5h")
        self._day("b", "09:00", "20:00", "9h")
        by = self._rank()
        self.assertEqual((by[self.emps["a"]]["ot_minutes"], by[self.emps["b"]]["ot_minutes"]), (0, 60))
        real = frappe.db.get_all
        with patch("frappe.db.get_all") as fake:
            fake.side_effect = lambda doctype, *a, **k: [self.emps["a"]] if doctype == "Leave Application" else real(doctype, *a, **k)
            by = self._rank()
        self.assertEqual(by[self.emps["a"]]["ot_minutes"], 60)    # 5h with a half-day leave: over the 4h half day
