"""Team week and company week endpoints: who may see what."""
import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, today

from st_attendance_tracker.api import submit_morning_log
from st_attendance_tracker.calendar_api import get_company_week, get_team_week
from st_attendance_tracker.st_attendance_tracker.doctype.daily_work_log.test_daily_work_log import (
    _make_employee,
)


class TestTeamCalendarApi(FrappeTestCase):
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
            "lead": "qa_tw_lead@test.example.com",
            "member": "qa_tw_member@test.example.com",
            "other": "qa_tw_other@test.example.com",
            "hr": "qa_tw_hr@test.example.com",
        }
        roles = {"lead": ["Employee"], "member": ["Employee"], "other": ["Employee"], "hr": ["HR Manager"]}
        cls.emps = {k: _make_employee(f"QATW{k}", cls.dept, u, roles[k]) for k, u in cls.users.items()}
        frappe.db.set_value("Employee", cls.emps["member"], "reports_to", cls.emps["lead"])
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        cls._cleanup()
        names = tuple(cls.emps.values())
        frappe.db.sql("DELETE FROM `tabEmployee` WHERE name IN %s", (names,))
        frappe.db.sql("DELETE FROM `tabUser` WHERE email IN %s", (tuple(cls.users.values()),))
        frappe.db.commit()

    @classmethod
    def _cleanup(cls):
        names = tuple(cls.emps.values())
        frappe.db.sql("DELETE FROM `tabTask Entry` WHERE parent IN "
                      "(SELECT name FROM `tabDaily Work Log` WHERE employee IN %s)", (names,))
        frappe.db.sql("DELETE FROM `tabDaily Work Log` WHERE employee IN %s", (names,))
        frappe.db.sql("DELETE FROM `tabEmployee Checkin` WHERE employee IN %s", (names,))
        frappe.db.commit()

    def setUp(self):
        frappe.set_user("Administrator")
        self._cleanup()

    def tearDown(self):
        frappe.set_user("Administrator")
        self._cleanup()

    def _member_checks_in(self):
        frappe.set_user(self.users["member"])
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Member task", "estimated_time": "1h"}]),
            login_time="09:00", work_location="Office",
        )
        frappe.set_user(self.users["lead"])

    # ── team week ──────────────────────────────────────────────────────────────

    def test_leader_sees_only_direct_reports(self):
        frappe.set_user(self.users["lead"])
        result = get_team_week(today(), today())
        self.assertEqual([e["name"] for e in result["employees"]], [self.emps["member"]])

    def test_leader_sees_checkin_status_and_tasks(self):
        self._member_checks_in()
        day = get_team_week(today(), today())["employees"][0]["days"][today()]
        self.assertEqual(day["status"], "checked_in")
        self.assertEqual(day["login_time"], "09:00")
        self.assertEqual([t["description"] for t in day["tasks"]], ["Member task"])

    def test_missing_checkin_and_future_days(self):
        frappe.set_user(self.users["lead"])
        days = get_team_week(add_days(today(), -1), add_days(today(), 1))["employees"][0]["days"]
        self.assertEqual(days[add_days(today(), -1)]["status"], "missing")
        self.assertIsNone(days[add_days(today(), 1)]["status"])

    def test_non_leader_is_denied(self):
        for who in ("member", "other"):
            frappe.set_user(self.users[who])
            with self.assertRaises(frappe.PermissionError, msg=who):
                get_team_week(today(), today())

    def test_team_week_range_is_limited(self):
        frappe.set_user(self.users["lead"])
        with self.assertRaises(frappe.ValidationError):
            get_team_week(add_days(today(), -30), today())
        with self.assertRaises(frappe.ValidationError):
            get_team_week(today(), add_days(today(), -1))

    # ── company week ───────────────────────────────────────────────────────────

    def test_hr_sees_company_counts(self):
        self._member_checks_in()
        frappe.set_user(self.users["hr"])
        result = get_company_week(today(), today())
        self.assertGreaterEqual(result["total"], 4)
        self.assertEqual(result["days"][0]["date"], today())
        self.assertGreaterEqual(result["days"][0]["present"], 1)

    def test_company_week_denied_for_non_hr(self):
        for who in ("lead", "member", "other"):
            frappe.set_user(self.users[who])
            with self.assertRaises(frappe.PermissionError, msg=who):
                get_company_week(today(), today())

    def test_company_week_range_is_limited(self):
        frappe.set_user(self.users["hr"])
        with self.assertRaises(frappe.ValidationError):
            get_company_week(add_days(today(), -30), today())


def tearDownModule():
    # HRMS creates a User Permission for every Employee user, and these tests delete their employees
    # with raw SQL, so the permissions would pile up in the site database run after run.
    frappe.db.sql("DELETE FROM `tabUser Permission` WHERE user LIKE %s", ("%@test.example.com",))
    frappe.db.commit()
