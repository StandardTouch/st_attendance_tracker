"""Additional Work next to check-in/check-out: overlap rule, day context, extra hours for leaders and HR."""
import json

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import today

from st_attendance_tracker.api import get_employee_task_detail, submit_morning_log
from st_attendance_tracker.calendar_api import get_company_extra_hours, get_day_context, get_team_week
from st_attendance_tracker.st_attendance_tracker.doctype.daily_work_log.test_daily_work_log import (
    _make_employee,
)


class TestAdditionalWorkLink(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        company = frappe.db.get_single_value("Global Defaults", "default_company") or "_Test Company"
        cls.dept = (
            frappe.db.get_value("Department", {"department_name": "_QA Dept", "company": company}, "name")
            or f"_QA Dept - {company}"
        )
        cls.users = {
            "lead": "qa_aw_lead@test.example.com", "member": "qa_aw_member@test.example.com",
            "other": "qa_aw_other@test.example.com", "hr": "qa_aw_hr@test.example.com",
        }
        roles = {"lead": ["Employee"], "member": ["Employee"], "other": ["Employee"], "hr": ["HR Manager"]}
        cls.emps = {k: _make_employee(f"QAAW{k}", cls.dept, u, roles[k]) for k, u in cls.users.items()}
        frappe.db.set_value("Employee", cls.emps["member"], "reports_to", cls.emps["lead"])
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
        names = tuple(cls.emps.values())
        frappe.db.sql("DELETE FROM `tabAdditional Work` WHERE employee IN %s", (names,))
        frappe.db.sql("DELETE FROM `tabTask Entry` WHERE parent IN "
                      "(SELECT name FROM `tabDaily Work Log` WHERE employee IN %s)", (names,))
        frappe.db.sql("DELETE FROM `tabDaily Work Log` WHERE employee IN %s", (names,))
        frappe.db.sql("DELETE FROM `tabEmployee Checkin` WHERE employee IN %s", (names,))
        frappe.db.commit()

    def setUp(self):
        # These tests are about overlaps and day context; the "day must be closed" rule is tested in test_additional_work.
        self.day_rule = patch("st_attendance_tracker.st_attendance_tracker.doctype.additional_work.additional_work.additional_work_allowed", return_value=(True, "checked_out"))
        self.day_rule.start()
        self.addCleanup(self.day_rule.stop)
        frappe.set_user("Administrator")
        self._cleanup()

    def tearDown(self):
        frappe.set_user("Administrator")
        self._cleanup()

    def _entry(self, who, login, logout, hours=1, desc="Extra work", day=None):
        return frappe.get_doc({
            "doctype": "Additional Work", "employee": self.emps[who], "work_date": day or today(),
            "login_time": login, "logout_time": logout, "hours_spent": hours, "description": desc, "status": "Done",
        }).insert(ignore_permissions=True)

    # ── overlap rule ───────────────────────────────────────────────────────────

    def test_partial_overlap_is_rejected(self):
        self._entry("member", "09:00", "10:00")
        with self.assertRaises(frappe.ValidationError):
            self._entry("member", "09:30", "10:30")
        with self.assertRaises(frappe.ValidationError):
            self._entry("member", "08:00", "09:15")
        with self.assertRaises(frappe.ValidationError):
            self._entry("member", "09:10", "09:40")  # contained

    def test_identical_window_adjacent_and_other_people_are_allowed(self):
        self._entry("member", "09:00", "10:00")
        self._entry("member", "09:00", "10:00", desc="Same session, second task")  # "Several tasks" form
        self._entry("member", "10:00", "11:00")  # starts when the other ends
        self._entry("other", "09:30", "10:30")  # a different employee

    def test_other_day_and_editing_same_entry_are_allowed(self):
        entry = self._entry("member", "09:00", "10:00")
        self._entry("member", "09:30", "10:30", day=frappe.utils.add_days(today(), -1))
        entry.description = "Edited"
        entry.save(ignore_permissions=True)

    def test_work_past_midnight_is_not_blocked(self):
        self._entry("member", "23:00", "01:00")

    # ── day context ────────────────────────────────────────────────────────────

    def test_day_context_with_and_without_checkin(self):
        frappe.set_user(self.users["member"])
        empty = get_day_context(today())
        self.assertIsNone(empty["log"])
        self.assertEqual(empty["entries"], [])

        submit_morning_log(new_tasks=json.dumps([{"description": "T", "estimated_time": "1h"}]),
                           login_time="09:00", work_location="Office")
        frappe.set_user("Administrator")
        self._entry("member", "19:00", "20:00")
        frappe.set_user(self.users["member"])
        ctx = get_day_context(today())
        self.assertEqual(ctx["log"]["login_time"], "09:00")
        self.assertTrue(ctx["log"]["morning_submitted"])
        self.assertFalse(ctx["log"]["eod_submitted"])
        self.assertEqual([(e["login_time"], e["logout_time"]) for e in ctx["entries"]], [("19:00", "20:00")])

    def test_day_context_is_own_data_only(self):
        self._entry("member", "19:00", "20:00")
        frappe.set_user(self.users["other"])
        self.assertEqual(get_day_context(today())["entries"], [])

    # ── leaders and HR ─────────────────────────────────────────────────────────

    def test_team_week_shows_extra_hours(self):
        self._entry("member", "19:00", "20:30", hours=1.5)
        self._entry("member", "20:30", "21:00", hours=0.5)
        frappe.set_user(self.users["lead"])
        day = get_team_week(today(), today())["employees"][0]["days"][today()]
        self.assertEqual(day["extra_hours"], 2.0)
        self.assertEqual(len(day["extra_work"]), 2)

    def test_company_extra_hours_hr_only(self):
        self._entry("member", "19:00", "20:00", hours=1)
        self._entry("other", "19:00", "21:00", hours=2)
        frappe.set_user(self.users["hr"])
        result = get_company_extra_hours(today())
        self.assertEqual(result[self.emps["member"]], 1)
        self.assertEqual(result[self.emps["other"]], 2)
        for who in ("lead", "member", "other"):
            frappe.set_user(self.users[who])
            with self.assertRaises(frappe.PermissionError, msg=who):
                get_company_extra_hours(today())

    def test_employee_detail_includes_additional_work_with_same_access_rules(self):
        self._entry("member", "19:00", "20:00", desc="Evening support")
        frappe.set_user(self.users["lead"])
        detail = get_employee_task_detail(self.emps["member"], today())
        self.assertEqual([a["description"] for a in detail["additional_work"]], ["Evening support"])
        self.assertIn("tasks", detail)  # existing keys untouched
        frappe.set_user(self.users["other"])
        with self.assertRaises(frappe.PermissionError):
            get_employee_task_detail(self.emps["member"], today())


def tearDownModule():
    # HRMS creates a User Permission for every Employee user, and these tests delete their employees
    # with raw SQL, so the permissions would pile up in the site database run after run.
    frappe.db.sql("DELETE FROM `tabUser Permission` WHERE user LIKE %s", ("%@test.example.com",))
    frappe.db.commit()
