"""Lunch recorded as OUT/IN Employee Checkin rows so HRMS can leave it out of working hours."""
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from hrms.hr.doctype.employee_checkin.employee_checkin import calculate_working_hours

from st_attendance_tracker import api

EMPLOYEE = "EMP-LUNCH-TEST"
DAY = "2026-10-06"
ALTERNATING = "Alternating entries as IN and OUT during the same shift"


def _log(time):
    return frappe._dict(time=frappe.utils.get_datetime(f"{DAY} {time}"))


class TestLunchCheckins(FrappeTestCase):
    def _run(self, lunch_from, lunch_to, exists=False):
        with patch.object(api, "_make_checkin") as make, \
                patch.object(frappe.db, "exists", return_value=exists):
            api._make_lunch_checkins(EMPLOYEE, lunch_from, lunch_to, DAY)
        return make

    def test_writes_out_then_in_on_the_work_day(self):
        make = self._run("13:00:00", "14:00:00")
        self.assertEqual(make.call_count, 2)
        out_call, in_call = make.call_args_list
        self.assertEqual(out_call.args, (EMPLOYEE, "OUT", "13:00:00"))
        self.assertEqual(in_call.args, (EMPLOYEE, "IN", "14:00:00"))
        for call in (out_call, in_call):
            self.assertEqual(call.kwargs, {"on_date": DAY, "device_id": api.LUNCH_DEVICE_ID})

    def test_no_lunch_writes_nothing(self):
        self.assertEqual(self._run("", "").call_count, 0)
        self.assertEqual(self._run("13:00:00", "").call_count, 0)
        self.assertEqual(self._run(None, None).call_count, 0)

    def test_reversed_or_empty_window_writes_nothing(self):
        self.assertEqual(self._run("14:00:00", "13:00:00").call_count, 0)
        self.assertEqual(self._run("13:00:00", "13:00:00").call_count, 0)

    def test_existing_lunch_rows_are_not_duplicated(self):
        self.assertEqual(self._run("13:00:00", "14:00:00", exists=True).call_count, 0)

    def test_lunch_rows_never_trigger_bot_mail(self):
        doc = frappe._dict(employee=EMPLOYEE, log_type="OUT", device_id=api.LUNCH_DEVICE_ID,
                           owner="Administrator", time=frappe.utils.get_datetime(f"{DAY} 13:00:00"))
        in_test, frappe.flags.in_test = frappe.flags.in_test, False
        try:
            with patch.object(frappe, "enqueue") as enqueue, \
                    patch.object(frappe, "get_roles", return_value=[api.BOT_AGENT_ROLE]):
                api.notify_external_checkin(doc)
        finally:
            frappe.flags.in_test = in_test
        self.assertEqual(enqueue.call_count, 0)

    def test_hrms_leaves_lunch_out_of_working_hours(self):
        """Contract with HRMS: 9:30-18:30 with a 1h lunch is 8h under 'Every Valid ...'."""
        logs = [_log("09:30:00"), _log("13:00:00"), _log("14:00:00"), _log("18:30:00")]
        every_valid = calculate_working_hours(logs, ALTERNATING, "Every Valid Check-in and Check-out")[0]
        first_last = calculate_working_hours(logs, ALTERNATING, "First Check-in and Last Check-out")[0]
        self.assertEqual(round(every_valid, 2), 8.0)
        self.assertEqual(round(first_last, 2), 9.0)

    def test_hrms_without_lunch_rows_counts_whole_span(self):
        logs = [_log("09:30:00"), _log("18:30:00")]
        self.assertEqual(
            round(calculate_working_hours(logs, ALTERNATING, "Every Valid Check-in and Check-out")[0], 2), 9.0)
