"""Checkout email on days with extra hours: full box for the employee, one line for Team Leader and HR."""
import json

import frappe
from unittest.mock import patch
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, now_datetime, today

from st_attendance_tracker.api import _send_eod_notifications, submit_eod_log, submit_morning_log
from st_attendance_tracker.calendar_api import get_calendar_range
from st_attendance_tracker.st_attendance_tracker.doctype.daily_work_log.test_daily_work_log import (
    _make_employee,
)

TL, HR = "tl_mail@test.example.com", "hr_mail@test.example.com"
YESTERDAY = add_days(today(), -1)


class TestCheckoutExtraHoursMail(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        company = frappe.db.get_single_value("Global Defaults", "default_company") or "_Test Company"
        dept = (frappe.db.get_value("Department", {"department_name": "_QA Dept", "company": company}, "name")
                or f"_QA Dept - {company}")
        cls.user = "qa_xmail@test.example.com"
        cls.emp = _make_employee("QAXMail", dept, cls.user, ["Employee"])
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
        frappe.db.sql("DELETE FROM `tabExtra Hours Source` WHERE parent IN (SELECT name FROM `tabExtra Hours Entry` WHERE employee=%s)", cls.emp)
        frappe.db.sql("DELETE FROM `tabExtra Hours Entry` WHERE employee=%s", cls.emp)
        frappe.db.sql("DELETE FROM `tabAdditional Work` WHERE employee=%s", cls.emp)
        frappe.db.sql("DELETE FROM `tabTask Entry` WHERE parent IN (SELECT name FROM `tabDaily Work Log` WHERE employee=%s)", cls.emp)
        frappe.db.sql("DELETE FROM `tabDaily Work Log` WHERE employee=%s", cls.emp)
        frappe.db.sql("DELETE FROM `tabEmployee Checkin` WHERE employee=%s", cls.emp)
        frappe.db.commit()

    def setUp(self):
        frappe.set_user("Administrator")
        for target, value in (("st_attendance_tracker.time_utils.get_checkout_cutoff", 24 * 60 - 1),):
            p = patch(target, return_value=value); p.start(); self.addCleanup(p.stop)
        for target, value in (("st_attendance_tracker.api._get_team_leader_emails", [TL]),
                              ("st_attendance_tracker.api._get_hr_manager_emails", [HR])):
            p = patch(target, return_value=value); p.start(); self.addCleanup(p.stop)
        self._cleanup()

    def tearDown(self):
        frappe.set_user("Administrator")
        self._cleanup()

    def _credit(self, hours, date=YESTERDAY, **extra):
        return frappe.get_doc({"doctype": "Extra Hours Entry", "employee": self.emp, "date": date, "entry_type": "Credit",
                               "source": "Overtime", "hours": hours, **extra}).insert(ignore_permissions=True)

    def _day(self, task_time, use=0, lunch=("", "")):
        frappe.set_user(self.user)
        submit_morning_log(new_tasks=json.dumps([{"description": "job"}]), login_time="09:00", work_location="Office")
        task = get_calendar_range(today(), today())["tasks"][0]
        submit_eod_log(lunch_from=lunch[0], lunch_to=lunch[1], logout_time="18:00", adhoc_tasks="[]", use_bank_hours=use,
                       task_updates=json.dumps([{"name": task["name"], "status": "Done", "actual_time": task_time}]))
        frappe.set_user("Administrator")

    def _mails(self, emp_email=True):
        sent = []
        with patch("frappe.sendmail", side_effect=lambda **k: sent.append(k)), \
             patch("st_attendance_tracker.api._get_employee_email", return_value=self.user if emp_email else None):
            _send_eod_notifications(self.emp, today(), now_datetime(), False)
        return sent

    def test_used_hours_split_the_email(self):
        self._credit(2, source="Overtime")
        self._day("5h", use=1.5)
        mine, others = self._mails()
        self.assertEqual((mine["recipients"], mine["cc"]), ([self.user], []))
        self.assertIn("Extra hours used today: 1h 30m", mine["message"])
        self.assertIn("Where these hours came from", mine["message"])
        self.assertIn("Overtime", mine["message"])
        self.assertIn("Extra hours left", mine["message"])
        self.assertEqual((others["recipients"], others["cc"]), ([TL], [HR]))
        self.assertIn("used 1h 30m of extra hours as time off", others["message"])
        self.assertNotIn("Where these hours came from", others["message"])
        self.assertNotIn("Extra hours used today:", others["message"])
        self.assertEqual(mine["subject"], others["subject"])

    def test_each_earlier_work_item_is_listed(self):
        aw = frappe.get_doc({"doctype": "Additional Work", "employee": self.emp, "work_date": YESTERDAY, "hours_spent": 1,
                             "project_name": "Website fixes", "description": "x", "status": "Done",
                             "login_time": "19:00:00", "logout_time": "20:00:00"})
        with patch("st_attendance_tracker.additional_work_mail.queue_additional_work_email"), patch(
            "st_attendance_tracker.st_attendance_tracker.doctype.additional_work.additional_work.additional_work_allowed",
            return_value=(True, "checked_out")):
            aw.insert(ignore_permissions=True)
        self._credit(0.5, date=add_days(today(), -2))
        self._day("6h", use=1.2)
        mine = self._mails()[0]["message"]
        self.assertIn("Additional work (Website fixes)", mine)
        self.assertEqual(mine.count("Overtime"), 1)           # the older 30m credit is listed on its own line

    def test_full_day_label_when_used_hours_fill_the_gap(self):
        self._credit(2)
        self._day("6h", use=2)                                # net hours come from check-in/out; label is about the total
        mine = self._mails()[0]["message"]
        self.assertIn("Counted as", mine)

    def test_earned_hours_box_uses_task_hours(self):
        self._day("9h")                                        # 9h of tasks, 1h over a full day
        mine, others = self._mails()
        self.assertIn("Extra hours earned today: 1h", mine["message"])
        self.assertIn("task hours", mine["message"])
        self.assertIn("earned 1h of extra hours", others["message"])
        self.assertEqual(others["recipients"], [TL])

    def test_normal_day_email_is_unchanged(self):
        self._day("7h")
        sent = self._mails()
        self.assertEqual(len(sent), 1)
        self.assertEqual((sent[0]["recipients"], sent[0]["cc"]), ([self.user, TL], [HR]))
        self.assertNotIn("Extra hours", sent[0]["message"])

    def test_without_an_employee_email_leaders_get_the_one_line(self):
        self._day("9h")
        sent = self._mails(emp_email=False)
        self.assertEqual(len(sent), 1)
        self.assertIn("Extra hours today: earned 1h", sent[0]["message"])
        self.assertNotIn("Where these hours came from", sent[0]["message"])

    def test_names_are_escaped(self):
        aw = frappe.get_doc({"doctype": "Additional Work", "employee": self.emp, "work_date": YESTERDAY, "hours_spent": 1,
                             "project_name": "<script>alert(1)</script> & Co", "description": "x", "status": "Done",
                             "login_time": "19:00:00", "logout_time": "20:00:00"})
        with patch("st_attendance_tracker.additional_work_mail.queue_additional_work_email"), patch(
            "st_attendance_tracker.st_attendance_tracker.doctype.additional_work.additional_work.additional_work_allowed",
            return_value=(True, "checked_out")):
            aw.insert(ignore_permissions=True)
        self._day("6h", use=1)
        mine = self._mails()[0]["message"]
        self.assertNotIn("<script>alert(1)</script>", mine)
        self.assertIn("&amp; Co", mine)                         # Frappe 16 strips the tag at save; 15 keeps it, escaped
        self.assertNotIn("&amp;lt;", mine)                       # escaped once, not twice
