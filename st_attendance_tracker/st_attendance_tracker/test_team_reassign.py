"""Team Leader reassigning a direct report's task to another direct report."""
import json

import frappe
from unittest.mock import patch
from frappe.tests.utils import FrappeTestCase
from frappe.utils import today

from st_attendance_tracker.api import autosave_eod_progress, submit_eod_log, submit_morning_log
from st_attendance_tracker.calendar_api import get_calendar_range, reassign_task
from st_attendance_tracker.st_attendance_tracker.doctype.daily_work_log.test_daily_work_log import (
    _make_employee,
)


class TestTeamReassign(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        company = frappe.db.get_single_value("Global Defaults", "default_company") or "_Test Company"
        cls.dept = (
            frappe.db.get_value("Department", {"department_name": "_QA Dept", "company": company}, "name")
            or f"_QA Dept - {company}"
        )
        cls.users = {k: f"qa_ra_{k}@test.example.com" for k in ("lead", "m1", "m2", "out")}
        cls.emps = {k: _make_employee(f"QARA{k}", cls.dept, u, ["Employee"]) for k, u in cls.users.items()}
        for k in ("m1", "m2"):
            frappe.db.set_value("Employee", cls.emps[k], "reports_to", cls.emps["lead"])
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        cls._cleanup()
        frappe.db.sql("DELETE FROM `tabEmployee` WHERE name IN %s", (tuple(cls.emps.values()),))
        frappe.db.sql("DELETE FROM `tabUser` WHERE email IN %s", (tuple(cls.users.values()),))
        frappe.db.commit()

    @classmethod
    def _cleanup(cls):
        n = tuple(cls.emps.values())
        frappe.db.sql("DELETE FROM `tabFile` WHERE attached_to_doctype='Task Entry' AND attached_to_name IN (SELECT name FROM `tabTask Entry` WHERE parent IN (SELECT name FROM `tabDaily Work Log` WHERE employee IN %s))", (n,))
        frappe.db.sql("DELETE FROM `tabTask Entry` WHERE parent IN (SELECT name FROM `tabDaily Work Log` WHERE employee IN %s)", (n,))
        frappe.db.sql("DELETE FROM `tabDaily Work Log` WHERE employee IN %s", (n,))
        frappe.db.sql("DELETE FROM `tabEmployee Checkin` WHERE employee IN %s", (n,))
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

    def _check_in(self, who, *descriptions):
        frappe.set_user(self.users[who])
        submit_morning_log(new_tasks=json.dumps([{"description": d, "project_name": "P", "estimated_time": "1h"} for d in descriptions]),
                           login_time="09:00", work_location="Office")
        return {t["description"]: t["name"] for t in get_calendar_range(today(), today())["tasks"]}

    def _tasks_of(self, who):
        frappe.set_user(self.users[who])
        return [(t["description"], t["task_type"]) for t in get_calendar_range(today(), today())["tasks"]]

    def test_moves_a_pending_task_to_the_other_person(self):
        tasks = self._check_in("m1", "Hand me over", "Stay put")
        self._check_in("m2", "Mine")
        frappe.set_user(self.users["lead"])
        result = reassign_task(tasks["Hand me over"], self.emps["m2"])
        self.assertEqual(result["to_employee"], self.emps["m2"])
        self.assertEqual(self._tasks_of("m1"), [("Stay put", "Planned")])
        self.assertEqual(sorted(self._tasks_of("m2")), [("Hand me over", "Ad-hoc"), ("Mine", "Planned")])
        row = frappe.db.get_value("Task Entry", result["task"], ["assigned_by", "project_name", "status"], as_dict=True)
        self.assertEqual((row.assigned_by, row.project_name, row.status), (self.emps["lead"], "P", "Pending"))

    def test_target_without_a_log_gets_one(self):
        tasks = self._check_in("m1", "Go")
        frappe.set_user(self.users["lead"])
        reassign_task(tasks["Go"], self.emps["m2"])
        self.assertEqual(self._tasks_of("m2"), [("Go", "Ad-hoc")])

    def test_started_or_finished_tasks_stay(self):
        tasks = self._check_in("m1", "Started", "Finished")
        autosave_eod_progress(task_updates=json.dumps([{"name": tasks["Started"], "status": "In Progress"},
                                                       {"name": tasks["Finished"], "status": "Done", "actual_time": "1h"}]))
        frappe.set_user(self.users["lead"])
        for desc in ("Started", "Finished"):
            with self.assertRaises(frappe.ValidationError, msg=desc):
                reassign_task(tasks[desc], self.emps["m2"])
        self.assertEqual(len(self._tasks_of("m1")), 2)

    def test_recurring_and_attached_tasks_stay(self):
        tasks = self._check_in("m1", "From template", "Has file")
        frappe.db.set_value("Task Entry", tasks["From template"], "task_type", "Recurring")
        frappe.get_doc({"doctype": "File", "file_name": "a.txt", "attached_to_doctype": "Task Entry",
                        "attached_to_name": tasks["Has file"], "content": b"x", "is_private": 1}).insert(ignore_permissions=True)
        frappe.set_user(self.users["lead"])
        for desc in ("From template", "Has file"):
            with self.assertRaises(frappe.ValidationError, msg=desc):
                reassign_task(tasks[desc], self.emps["m2"])

    def test_only_inside_the_leaders_team(self):
        tasks = self._check_in("m1", "Team task")
        outsider = self._check_in("out", "Not yours")
        frappe.set_user(self.users["lead"])
        with self.assertRaises(frappe.PermissionError):
            reassign_task(tasks["Team task"], self.emps["out"])
        with self.assertRaises(frappe.PermissionError):
            reassign_task(outsider["Not yours"], self.emps["m2"])
        with self.assertRaises(frappe.ValidationError):
            reassign_task(tasks["Team task"], self.emps["m1"])  # same person
        for who in ("m1", "m2", "out"):  # not a Team Leader
            frappe.set_user(self.users[who])
            with self.assertRaises(frappe.PermissionError, msg=who):
                reassign_task(tasks["Team task"], self.emps["m2"])
        self.assertEqual(self._tasks_of("m1"), [("Team task", "Planned")])

    def test_nothing_moves_once_someone_checked_out(self):
        tasks = self._check_in("m1", "Locked source", "Open source")
        other = self._check_in("m2", "Done early")
        submit_eod_log(lunch_from="13:00", lunch_to="14:00", logout_time="18:00",
                       task_updates=json.dumps([{"name": other["Done early"], "status": "Done", "actual_time": "1h"}]), adhoc_tasks="[]")
        frappe.set_user(self.users["lead"])
        with self.assertRaises(frappe.ValidationError):  # destination checked out
            reassign_task(tasks["Open source"], self.emps["m2"])
        frappe.set_user(self.users["m1"])
        submit_eod_log(lunch_from="13:00", lunch_to="14:00", logout_time="18:00",
                       task_updates=json.dumps([{"name": tasks["Locked source"], "status": "Done", "actual_time": "1h"},
                                                {"name": tasks["Open source"], "status": "Pending", "actual_time": "", "carry_forward": True}]), adhoc_tasks="[]")
        frappe.set_user(self.users["lead"])
        with self.assertRaises(frappe.ValidationError):  # source checked out
            reassign_task(tasks["Open source"], self.emps["m2"])


def tearDownModule():
    # HRMS creates a User Permission for every Employee user, and these tests delete their employees
    # with raw SQL, so the permissions would pile up in the site database run after run.
    frappe.db.sql("DELETE FROM `tabUser Permission` WHERE user LIKE %s", ("%@test.example.com",))
    frappe.db.commit()
