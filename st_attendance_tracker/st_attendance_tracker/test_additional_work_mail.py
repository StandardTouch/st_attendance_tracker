"""Additional Work emails: queued once per employee and day, HR/Team Leader and the employee both get one."""
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from st_attendance_tracker import additional_work_mail as mail

EMP = "EMP-AW-MAIL-TEST"
DAY = "2026-10-06"


def _doc():
    return frappe._dict(employee=EMP, work_date=DAY)


def _key():
    return frappe.cache().make_key(f"st_aw_mail:{EMP}:{DAY}")


class TestAdditionalWorkMail(FrappeTestCase):
    def setUp(self):
        self._flags = (frappe.flags.in_test, frappe.flags.in_import, frappe.flags.in_migrate)
        frappe.flags.in_test = frappe.flags.in_import = frappe.flags.in_migrate = False
        frappe.cache().delete(_key())

    def tearDown(self):
        frappe.flags.in_test, frappe.flags.in_import, frappe.flags.in_migrate = self._flags
        frappe.cache().delete(_key())

    def test_first_entry_queues_one_email_and_the_batch_shares_it(self):
        with patch.object(frappe, "enqueue") as enqueue:
            mail.queue_additional_work_email(_doc())
            mail.queue_additional_work_email(_doc())
            mail.queue_additional_work_email(_doc())
        self.assertEqual(enqueue.call_count, 1)
        self.assertTrue(enqueue.call_args.kwargs["enqueue_after_commit"])

    def test_inert_under_tests(self):
        frappe.flags.in_test = True
        with patch.object(frappe, "enqueue") as enqueue:
            mail.queue_additional_work_email(_doc())
        self.assertEqual(enqueue.call_count, 0)

    def _send(self, entries, employee=True, hr=("hr@test.example.com",), tl=(), own="emp@test.example.com", work_log=None):
        emp = frappe._dict(name=EMP, employee_name="Mail Tester", department="D") if employee else None
        rows = [frappe._dict(e) for e in entries]
        with patch.object(frappe.db, "get_value", return_value=emp), \
                patch.object(frappe, "get_all", return_value=rows), \
                patch.object(mail, "_get_hr_manager_emails", return_value=list(hr)), \
                patch.object(mail, "_get_team_leader_emails", return_value=list(tl)), \
                patch.object(mail, "_get_work_log", return_value=work_log), \
                patch.object(mail, "_get_employee_email", return_value=own), \
                patch.object(mail, "_employee_mail_identity", return_value=(None, None)), \
                patch.object(frappe, "sendmail") as sendmail:
            mail.send_additional_work_emails(EMP, DAY, wait=0)
        return sendmail

    ENTRY = dict(login_time="19:00:00", logout_time="21:00:00", project_name="P", hours_spent=2.0,
                 status="Done", description="Fixed <b>bug</b>", remarks="")

    def test_combined_email_sent_with_hr_in_cc(self):
        sendmail = self._send([self.ENTRY, {**self.ENTRY, "hours_spent": 1.5}], tl=("tl@test.example.com",))
        self.assertEqual(sendmail.call_count, 1)
        call = sendmail.call_args
        self.assertEqual(set(call.kwargs["recipients"]), {"emp@test.example.com", "tl@test.example.com"})
        self.assertEqual(call.kwargs["cc"], ["hr@test.example.com"])
        self.assertIn("Additional Work", call.kwargs["subject"])
        self.assertIn("3h 30m", call.kwargs["message"])
        self.assertNotIn("<b>bug</b>", call.kwargs["message"])  # user text is escaped

    def test_no_entries_or_unknown_employee_sends_nothing(self):
        self.assertEqual(self._send([]).call_count, 0)
        self.assertEqual(self._send([self.ENTRY], employee=False).call_count, 0)

    def test_missing_employee_email_still_notifies_leaders(self):
        sendmail = self._send([self.ENTRY], own=None, tl=("tl@test.example.com",))
        self.assertEqual(sendmail.call_count, 1)
        self.assertEqual(sendmail.call_args.kwargs["recipients"], ["tl@test.example.com"])
        self.assertEqual(sendmail.call_args.kwargs["cc"], ["hr@test.example.com"])

    # Same layout as the check-out email ------------------------------------------------------
    WORK_LOG = frappe._dict(eod_submitted=1, login_time="09:30:00", logout_time="18:00:00",
                            work_location="Office", net_hours="7h 30m")

    def test_message_carries_the_day_details_like_the_checkout_email(self):
        sendmail = self._send([self.ENTRY], work_log=self.WORK_LOG)
        body = sendmail.call_args_list[0].kwargs["message"]
        for expected in ("Login Time", "09:30 AM", "Logout Time", "06:00 PM", "Work Location", "Office",
                         "Net Working Hours", "7h 30m", "Additional Work Window", "07:00 PM - 09:00 PM",
                         "Total Additional Hours", "2h", "Worked, checked out", "Additional Work Summary"):
            self.assertIn(expected, body)

    def test_heading_is_in_the_email_message(self):
        sendmail = self._send([self.ENTRY], work_log=self.WORK_LOG)
        call = sendmail.call_args_list[0]
        self.assertIn("Mail Tester - Additional Work - 06-10-2026</div>", call.kwargs["message"])

    def test_leave_day_has_no_login_logout_rows(self):
        sendmail = self._send([self.ENTRY], work_log=None)
        body = sendmail.call_args_list[0].kwargs["message"]
        self.assertIn("On leave", body)
        self.assertNotIn("Login Time", body)
        self.assertNotIn("Logout Time", body)
        self.assertIn("Additional Work Window", body)

    def test_entries_are_grouped_by_project_in_the_accent_table(self):
        entries = [self.ENTRY, {**self.ENTRY, "description": "Second", "login_time": "21:00:00", "logout_time": "22:00:00"},
                   {**self.ENTRY, "project_name": "", "description": "No project"}]
        body = self._send(entries, work_log=self.WORK_LOG).call_args_list[0].kwargs["message"]
        self.assertEqual(body.count(">P</td>"), 1)  # project named once for its rows
        self.assertIn(">General</td>", body)
        self.assertIn("Task / Work", body)
