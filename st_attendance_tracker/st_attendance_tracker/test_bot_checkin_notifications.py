"""Emails for check-ins made by the Hermes bot (bare Employee Checkin rows)."""
import datetime
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from st_attendance_tracker import api

EMPLOYEE = "EMP-BOT-MAIL-TEST"
BOT_USER = "bot-mail-test@test.example.com"


def _checkin(log_type="IN", device_id=None, owner=BOT_USER, when="2026-10-06 10:35:00"):
    return frappe._dict(
        employee=EMPLOYEE, log_type=log_type, device_id=device_id, owner=owner,
        time=frappe.utils.get_datetime(when),
    )


class TestBotCheckinNotifications(FrappeTestCase):
    def setUp(self):
        self._flags = (frappe.flags.in_test, frappe.flags.in_import, frappe.flags.in_migrate)
        # The hook is deliberately inert under tests; switch it on for these cases.
        frappe.flags.in_test = frappe.flags.in_import = frappe.flags.in_migrate = False
        for key in ("IN", "OUT"):
            frappe.cache().delete(frappe.cache().make_key(f"st_bot_checkin_mail:{EMPLOYEE}:2026-10-06:{key}"))

    def tearDown(self):
        frappe.flags.in_test, frappe.flags.in_import, frappe.flags.in_migrate = self._flags
        for key in ("IN", "OUT"):
            frappe.cache().delete(frappe.cache().make_key(f"st_bot_checkin_mail:{EMPLOYEE}:2026-10-06:{key}"))

    def _fire(self, doc, roles=(api.BOT_AGENT_ROLE,)):
        with patch.object(frappe, "get_roles", return_value=list(roles)), \
                patch.object(frappe, "enqueue") as enqueue:
            api.notify_external_checkin(doc)
        return enqueue

    def test_bot_check_in_enqueues_once(self):
        enqueue = self._fire(_checkin("IN"))
        self.assertEqual(enqueue.call_count, 1)
        kwargs = enqueue.call_args.kwargs
        self.assertEqual((kwargs["employee_name"], kwargs["log_type"]), (EMPLOYEE, "IN"))
        self.assertTrue(kwargs["enqueue_after_commit"])

    def test_repeat_is_not_sent_twice(self):
        self._fire(_checkin("IN"))
        self.assertEqual(self._fire(_checkin("IN")).call_count, 0)

    def test_in_and_out_are_separate(self):
        self.assertEqual(self._fire(_checkin("IN")).call_count, 1)
        self.assertEqual(self._fire(_checkin("OUT")).call_count, 1)

    def test_web_page_rows_are_skipped(self):
        self.assertEqual(self._fire(_checkin("IN", device_id="ST Daily Checkin")).call_count, 0)

    def test_other_users_are_skipped(self):
        self.assertEqual(self._fire(_checkin("IN"), roles=("HR Manager",)).call_count, 0)

    def test_inert_under_tests(self):
        frappe.flags.in_test = True
        self.assertEqual(self._fire(_checkin("IN")).call_count, 0)

    def _run_job(self, log_type, work_log=None):
        emp = frappe._dict(name=EMPLOYEE, employee_name="Bot Tester", department="D")
        with patch.object(frappe.db, "get_value", return_value=emp), \
                patch.object(api, "_get_work_log", return_value=work_log), \
                patch.object(api, "_notify_hr_and_team_leader") as notify:
            api._send_external_checkin_notifications(EMPLOYEE, log_type, "2026-10-06 10:35:00")
        return notify

    def test_job_check_in_sends_notification(self):
        notify = self._run_job("IN")
        self.assertEqual(notify.call_args.args[2], "checkin")
        self.assertEqual(notify.call_count, 1)
        self.assertIn(("Checked in via", "Telegram bot"), notify.call_args.args[3])

    def test_job_check_out_sends_notification(self):
        notify = self._run_job("OUT")
        self.assertEqual(notify.call_args.args[2], "checkout")
        self.assertEqual(notify.call_count, 1)

    def test_job_skips_when_web_already_emailed(self):
        notify = self._run_job("IN", work_log=frappe._dict(morning_submitted=1, eod_submitted=0))
        self.assertEqual(notify.call_count, 0)
        notify = self._run_job("OUT", work_log=frappe._dict(morning_submitted=1, eod_submitted=1))
        self.assertEqual(notify.call_count, 0)

    def test_job_unknown_employee_is_silent(self):
        with patch.object(frappe.db, "get_value", return_value=None), \
                patch.object(api, "_notify_hr_and_team_leader") as notify:
            api._send_external_checkin_notifications("NOPE", "IN", "2026-10-06 10:35:00")
        self.assertEqual(notify.call_count, 0)
