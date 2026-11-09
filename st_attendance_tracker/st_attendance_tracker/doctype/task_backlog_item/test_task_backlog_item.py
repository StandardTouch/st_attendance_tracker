"""Task Backlog — self-service CRUD, Team Leader push, and pull-into-today.
Tasks captured here carry no date and must never show up as a Task Entry
until pulled in; pulling must preserve assigned_by so the "Assigned by"
tag still renders on /daily-checkin."""
import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import today

from st_attendance_tracker.api import (
    get_task_backlog, save_backlog_item, save_backlog_items, delete_backlog_item,
    push_task_to_backlog, pull_backlog_item_to_today, move_task_to_backlog,
    submit_morning_log, assign_task_to_employee, _get_work_log,
)
from st_attendance_tracker.st_attendance_tracker.doctype.daily_work_log.test_daily_work_log import _make_employee


class TestTaskBacklogItem(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")

        company = frappe.db.get_single_value("Global Defaults", "default_company") or "_Test Company"
        cls.dept = (
            frappe.db.get_value("Department", {"department_name": "_QA Dept", "company": company}, "name")
            or f"_QA Dept - {company}"
        )

        cls.leader_user = "backlog_tl@test.example.com"
        cls.report_user = "backlog_emp@test.example.com"
        cls.stranger_user = "backlog_stranger@test.example.com"

        cls.leader_name = _make_employee("BacklogTL", cls.dept, cls.leader_user, ["Employee"])
        cls.report_name = _make_employee("BacklogEmp", cls.dept, cls.report_user, ["Employee"])
        cls.stranger_name = _make_employee("BacklogStranger", cls.dept, cls.stranger_user, ["Employee"])

        frappe.db.set_value("Employee", cls.report_name, "reports_to", cls.leader_name)
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        for emp in (cls.report_name, cls.stranger_name):
            frappe.db.sql("DELETE FROM `tabTask Entry` WHERE parent IN "
                           "(SELECT name FROM `tabDaily Work Log` WHERE employee=%s)", (emp,))
            frappe.db.sql("DELETE FROM `tabDaily Work Log` WHERE employee=%s", (emp,))
        frappe.db.sql("DELETE FROM `tabTask Backlog Item` WHERE employee IN (%s,%s,%s)",
                      (cls.leader_name, cls.report_name, cls.stranger_name))
        frappe.db.sql("DELETE FROM `tabEmployee` WHERE name IN (%s,%s,%s)",
                      (cls.leader_name, cls.report_name, cls.stranger_name))
        frappe.db.sql("DELETE FROM `tabUser` WHERE email IN (%s,%s,%s)",
                      (cls.leader_user, cls.report_user, cls.stranger_user))
        frappe.db.commit()

    def setUp(self):
        frappe.set_user("Administrator")

    def tearDown(self):
        frappe.set_user("Administrator")
        for emp in (self.report_name, self.stranger_name):
            frappe.db.sql("DELETE FROM `tabTask Entry` WHERE parent IN "
                           "(SELECT name FROM `tabDaily Work Log` WHERE employee=%s)", (emp,))
            frappe.db.sql("DELETE FROM `tabDaily Work Log` WHERE employee=%s", (emp,))
            frappe.db.sql("DELETE FROM `tabEmployee Checkin` WHERE employee=%s", (emp,))
        frappe.db.sql("DELETE FROM `tabTask Backlog Item` WHERE employee IN (%s,%s,%s)",
                      (self.leader_name, self.report_name, self.stranger_name))
        frappe.db.commit()

    def test_self_service_create_and_list(self):
        frappe.set_user(self.report_user)
        r = save_backlog_item(description="Write the onboarding doc", project_name="Docs", estimated_time="2h")
        self.assertTrue(r["success"])

        rows = get_task_backlog()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["description"], "Write the onboarding doc")
        self.assertEqual(rows[0]["project_name"], "Docs")
        self.assertFalse(rows[0]["is_stale"])

    def test_self_service_update_upserts_by_name(self):
        frappe.set_user(self.report_user)
        r = save_backlog_item(description="Draft v1")
        save_backlog_item(name=r["name"], description="Draft v2", estimated_time="1h")

        rows = get_task_backlog()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["description"], "Draft v2")

    def test_self_service_delete(self):
        frappe.set_user(self.report_user)
        r = save_backlog_item(description="Throwaway task")
        delete_backlog_item(r["name"])
        self.assertEqual(len(get_task_backlog()), 0)

    def test_stranger_cannot_edit_or_delete(self):
        frappe.set_user(self.report_user)
        r = save_backlog_item(description="Private task")

        frappe.set_user(self.stranger_user)
        with self.assertRaises(frappe.PermissionError):
            save_backlog_item(name=r["name"], description="Hijacked")
        with self.assertRaises(frappe.PermissionError):
            delete_backlog_item(r["name"])

    def test_save_backlog_items_bulk_matches_footer_project_task_flow(self):
        """The /task-backlog footer's Project/Task buttons batch several
        unsaved rows and Save once — mirrors add_adhoc_tasks on /daily-checkin."""
        frappe.set_user(self.report_user)
        r = save_backlog_items(items=[
            {"client_id": "row1", "description": "Design the landing page", "project_name": "Website", "estimated_time": "2h"},
            {"client_id": "row2", "description": "Write copy", "project_name": "Website", "estimated_time": "1h"},
            {"client_id": "row3", "description": "Standalone task", "project_name": ""},
        ])
        self.assertTrue(r["success"])
        self.assertEqual(len(r["created"]), 3)

        rows = get_task_backlog()
        self.assertEqual(len(rows), 3)
        by_desc = {x["description"]: x for x in rows}
        self.assertEqual(by_desc["Design the landing page"]["project_name"], "Website")
        self.assertEqual(by_desc["Standalone task"]["project_name"], "")

    def test_save_backlog_items_skips_blank_rows(self):
        frappe.set_user(self.report_user)
        r = save_backlog_items(items=[
            {"client_id": "a", "description": "   ", "project_name": "X"},
            {"client_id": "b", "description": "Real task"},
        ])
        self.assertEqual(len(r["created"]), 1)
        self.assertEqual(len(get_task_backlog()), 1)

    def test_empty_description_rejected(self):
        frappe.set_user(self.report_user)
        with self.assertRaises(frappe.ValidationError):
            save_backlog_item(description="   ")

    def test_team_leader_pushes_to_report_backlog(self):
        frappe.set_user(self.leader_user)
        r = push_task_to_backlog(assignee_employee=self.report_name, description="Prepare Q4 numbers")
        self.assertTrue(r["success"])

        rows = frappe.get_all("Task Backlog Item",
            filters={"employee": self.report_name, "description": "Prepare Q4 numbers"},
            fields=["name", "assigned_by", "assigned_by_name"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["assigned_by"], self.leader_name)
        self.assertEqual(rows[0]["assigned_by_name"], "Test BacklogTL")

    def test_push_rejects_non_team_member(self):
        frappe.set_user(self.leader_user)
        with self.assertRaises(frappe.PermissionError):
            push_task_to_backlog(assignee_employee=self.stranger_name, description="Do this")

    def test_pull_backlog_item_to_today_creates_task_entry_and_removes_item(self):
        frappe.set_user(self.report_user)
        r = save_backlog_item(description="Pull me in", project_name="Website", estimated_time="1h 30m")

        pull_r = pull_backlog_item_to_today(r["name"])
        self.assertTrue(pull_r["success"])

        work_log = _get_work_log(self.report_name, today())
        row = next(t for t in work_log.tasks if t.description == "Pull me in")
        self.assertEqual(row.task_type, "Ad-hoc")
        self.assertEqual(row.status, "Pending")
        self.assertEqual(row.project_name, "Website")
        self.assertAlmostEqual(row.estimated_time, 1.5, places=2)

        self.assertEqual(len(get_task_backlog()), 0)

    def test_pull_preserves_assigned_by_for_tl_pushed_item(self):
        frappe.set_user(self.leader_user)
        push_r = push_task_to_backlog(assignee_employee=self.report_name, description="TL pushed then pulled")

        frappe.set_user(self.report_user)
        pull_backlog_item_to_today(push_r["name"])

        work_log = _get_work_log(self.report_name, today())
        row = next(t for t in work_log.tasks if t.description == "TL pushed then pulled")
        self.assertEqual(row.assigned_by, self.leader_name)

    def test_pull_rejects_someone_elses_item(self):
        frappe.set_user(self.report_user)
        r = save_backlog_item(description="Not yours")

        frappe.set_user(self.stranger_user)
        with self.assertRaises(frappe.PermissionError):
            pull_backlog_item_to_today(r["name"])

    def test_move_existing_task_to_backlog(self):
        """The reverse of pull: an already-planned task on today's Daily
        Work Log can be sent to the backlog (the /daily-checkin "move to
        backlog" button)."""
        frappe.set_user(self.report_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Move me out", "project_name": "Website", "estimated_time": "1h 30m"}]),
            login_time="09:00",
            work_location="Office",
        )
        work_log = _get_work_log(self.report_name, today())
        row = next(t for t in work_log.tasks if t.description == "Move me out")

        r = move_task_to_backlog(row.name)
        self.assertTrue(r["success"])

        work_log = _get_work_log(self.report_name, today())
        self.assertFalse(any(t.description == "Move me out" for t in work_log.tasks))

        rows = get_task_backlog()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["description"], "Move me out")
        self.assertEqual(rows[0]["project_name"], "Website")

    def test_move_preserves_assigned_by(self):
        frappe.set_user(self.leader_user)
        assign_task_to_employee(assignee_employee=self.report_name, description="TL assigned, then moved out")

        frappe.set_user(self.report_user)
        work_log = _get_work_log(self.report_name, today())
        row = next(t for t in work_log.tasks if t.description == "TL assigned, then moved out")
        move_task_to_backlog(row.name)

        rows = get_task_backlog()
        self.assertEqual(rows[0]["assigned_by_name"], "Test BacklogTL")

    def test_move_rejects_recurring_task(self):
        frappe.set_user(self.report_user)
        submit_morning_log(new_tasks=json.dumps([{"description": "Planned task"}]), login_time="09:00", work_location="Office")
        work_log = _get_work_log(self.report_name, today())
        row = next(t for t in work_log.tasks if t.description == "Planned task")
        row.task_type = "Recurring"
        work_log.save()

        with self.assertRaises(frappe.ValidationError):
            move_task_to_backlog(row.name)

    def test_move_rejects_done_task(self):
        frappe.set_user(self.report_user)
        submit_morning_log(new_tasks=json.dumps([{"description": "Finish me"}]), login_time="09:00", work_location="Office")
        work_log = _get_work_log(self.report_name, today())
        row = next(t for t in work_log.tasks if t.description == "Finish me")
        row.status = "Done"
        row.actual_time = 1
        work_log.save()

        with self.assertRaises(frappe.ValidationError):
            move_task_to_backlog(row.name)

    def test_move_rejects_someone_elses_task(self):
        frappe.set_user(self.report_user)
        submit_morning_log(new_tasks=json.dumps([{"description": "Private task"}]), login_time="09:00", work_location="Office")
        work_log = _get_work_log(self.report_name, today())
        row = next(t for t in work_log.tasks if t.description == "Private task")

        frappe.set_user(self.stranger_user)
        with self.assertRaises(frappe.PermissionError):
            move_task_to_backlog(row.name)


def tearDownModule():
    # HRMS creates a User Permission for every Employee user, and these tests delete their employees
    # with raw SQL, so the permissions would pile up in the site database run after run.
    frappe.db.sql("DELETE FROM `tabUser Permission` WHERE user LIKE %s", ("%@test.example.com",))
    frappe.db.commit()
