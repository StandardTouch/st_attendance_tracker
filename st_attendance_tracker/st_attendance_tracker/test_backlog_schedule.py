"""Scheduling backlog items for a day, and pulling due ones into the check-in plan."""
import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, today

from st_attendance_tracker.api import (
    get_page_state,
    get_task_backlog,
    pull_backlog_item_to_today,
    save_backlog_item,
    submit_morning_log,
)
from st_attendance_tracker.calendar_api import get_calendar_range, schedule_backlog_item
from st_attendance_tracker.st_attendance_tracker.doctype.daily_work_log.test_daily_work_log import (
    _make_employee,
)


class TestBacklogSchedule(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        company = frappe.db.get_single_value("Global Defaults", "default_company") or "_Test Company"
        cls.dept = (
            frappe.db.get_value("Department", {"department_name": "_QA Dept", "company": company}, "name")
            or f"_QA Dept - {company}"
        )
        cls.user_a, cls.user_b = "qa_bs_a@test.example.com", "qa_bs_b@test.example.com"
        cls.emp_a = _make_employee("QABSA", cls.dept, cls.user_a, ["Employee"])
        cls.emp_b = _make_employee("QABSB", cls.dept, cls.user_b, ["Employee"])
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
        self._cleanup()

    def tearDown(self):
        frappe.set_user("Administrator")
        self._cleanup()

    def _item(self, user, description="Backlog thing", **kw):
        frappe.set_user(user)
        name = save_backlog_item(description=description, project_name=kw.get("project", ""), estimated_time=kw.get("est", ""))["name"]
        return name

    def _todays_tasks(self):
        return get_calendar_range(today(), today())["tasks"]

    # ── scheduling endpoint ────────────────────────────────────────────────────

    def test_schedule_and_clear(self):
        name = self._item(self.user_a)
        tomorrow = add_days(today(), 1)
        out = schedule_backlog_item(name, tomorrow, "10:30")
        self.assertEqual((out["scheduled_for"], out["start_time"]), (tomorrow, "10:30"))
        row = [b for b in get_task_backlog() if b["name"] == name][0]
        self.assertEqual((row["scheduled_for"], row["start_time"]), (tomorrow, "10:30"))
        ranged = get_calendar_range(tomorrow, tomorrow)["scheduled_backlog"]
        self.assertEqual([r["name"] for r in ranged], [name])
        cleared = schedule_backlog_item(name)
        self.assertEqual((cleared["scheduled_for"], cleared["start_time"]), (None, None))
        self.assertEqual(get_calendar_range(tomorrow, tomorrow)["scheduled_backlog"], [])

    def test_unscheduled_backlog_has_no_empty_time_artifact(self):
        name = self._item(self.user_a)
        row = [b for b in get_task_backlog() if b["name"] == name][0]
        self.assertEqual((row["scheduled_for"], row["start_time"]), (None, None))  # Frappe must not back-fill "now"

    def test_validation_and_ownership(self):
        name = self._item(self.user_a)
        with self.assertRaises(frappe.ValidationError):
            schedule_backlog_item(name, add_days(today(), -1))
        with self.assertRaises(frappe.ValidationError):
            schedule_backlog_item(name, today(), "7pm")
        frappe.set_user(self.user_b)
        with self.assertRaises(frappe.PermissionError):
            schedule_backlog_item(name, add_days(today(), 2))
        with self.assertRaises(frappe.PermissionError):
            schedule_backlog_item("does-not-exist", add_days(today(), 2))
        frappe.set_user(self.user_a)
        self.assertIsNone([b for b in get_task_backlog() if b["name"] == name][0]["scheduled_for"])

    # ── pulled into the plan ───────────────────────────────────────────────────

    def test_due_item_joins_the_plan_before_checkin(self):
        name = self._item(self.user_a, "Scheduled for today", project="Proj", est="2h")
        schedule_backlog_item(name, today(), "15:00")
        get_page_state()
        tasks = self._todays_tasks()
        self.assertEqual([(t["description"], t["status"], t["task_type"], t["project_name"], t["start_time"]) for t in tasks],
                         [("Scheduled for today", "Pending", "Ad-hoc", "Proj", "15:00")])
        self.assertEqual(tasks[0]["estimated_time"], 2)
        self.assertEqual([b["name"] for b in get_task_backlog()], [])
        get_page_state()  # again: nothing duplicated
        self.assertEqual(len(self._todays_tasks()), 1)

    def test_overdue_scheduled_item_is_not_lost(self):
        name = self._item(self.user_a, "Missed day")
        schedule_backlog_item(name, today())
        frappe.db.set_value("Task Backlog Item", name, "scheduled_for", add_days(today(), -2))  # the day passed unnoticed
        get_page_state()
        self.assertEqual([t["description"] for t in self._todays_tasks()], ["Missed day"])

    def test_future_and_unscheduled_items_stay_in_the_backlog(self):
        future = self._item(self.user_a, "Later")
        schedule_backlog_item(future, add_days(today(), 3))
        self._item(self.user_a, "No day")
        get_page_state()
        self.assertEqual(self._todays_tasks(), [])
        self.assertEqual(sorted(b["description"] for b in get_task_backlog()), ["Later", "No day"])

    def test_other_employees_items_are_not_pulled(self):
        name = self._item(self.user_b, "B's scheduled")
        schedule_backlog_item(name, today())
        frappe.set_user(self.user_a)
        get_page_state()
        self.assertEqual(self._todays_tasks(), [])
        frappe.set_user(self.user_b)
        self.assertEqual([b["name"] for b in get_task_backlog()], [name])

    def test_checkin_works_with_only_a_scheduled_task(self):
        name = self._item(self.user_a, "Only scheduled")
        schedule_backlog_item(name, today())
        result = submit_morning_log(new_tasks="[]", login_time="09:00", work_location="Office")
        self.assertTrue(result["success"])
        self.assertEqual([t["description"] for t in self._todays_tasks()], ["Only scheduled"])

    def test_nothing_is_pulled_after_checkin(self):
        frappe.set_user(self.user_a)
        submit_morning_log(new_tasks=json.dumps([{"description": "Plan"}]), login_time="09:00", work_location="Office")
        name = self._item(self.user_a, "Scheduled after check-in")
        schedule_backlog_item(name, today())
        get_page_state()
        self.assertEqual([t["description"] for t in self._todays_tasks()], ["Plan"])
        self.assertEqual([b["name"] for b in get_task_backlog()], [name])

    def test_manual_pull_keeps_the_start_time(self):
        name = self._item(self.user_a, "Manual pull")
        schedule_backlog_item(name, add_days(today(), 1), "11:00")
        pull_backlog_item_to_today(name)
        self.assertEqual([(t["description"], t["start_time"]) for t in self._todays_tasks()], [("Manual pull", "11:00")])


def tearDownModule():
    # HRMS creates a User Permission for every Employee user, and these tests delete their employees
    # with raw SQL, so the permissions would pile up in the site database run after run.
    frappe.db.sql("DELETE FROM `tabUser Permission` WHERE user LIKE %s", ("%@test.example.com",))
    frappe.db.commit()
