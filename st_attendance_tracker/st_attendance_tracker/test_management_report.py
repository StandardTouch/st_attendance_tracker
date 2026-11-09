"""Management report: access, totals, OT, late checkouts, login series, filters."""
import json

import frappe
from unittest.mock import patch
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, now_datetime, today

from st_attendance_tracker.api import submit_eod_log, submit_morning_log
from st_attendance_tracker.calendar_api import get_calendar_range
from st_attendance_tracker.management_report import get_management_report
from st_attendance_tracker.st_attendance_tracker.doctype.daily_work_log.test_daily_work_log import (
    _make_employee,
)


class TestManagementReport(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        company = frappe.db.get_single_value("Global Defaults", "default_company") or "_Test Company"
        cls.dept = (
            frappe.db.get_value("Department", {"department_name": "_QA Dept", "company": company}, "name")
            or f"_QA Dept - {company}"
        )
        cls.user_a, cls.user_b, cls.user_hr = (f"qa_rep_{k}@test.example.com" for k in ("a", "b", "hr"))
        cls.emp_a = _make_employee("QARepA", cls.dept, cls.user_a, ["Employee"])
        cls.emp_b = _make_employee("QARepB", cls.dept, cls.user_b, ["Employee"])
        cls.emp_hr = _make_employee("QARepHR", cls.dept, cls.user_hr, ["Employee", "HR Manager"])
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        cls._cleanup()
        for e in (cls.emp_a, cls.emp_b, cls.emp_hr):
            frappe.db.sql("DELETE FROM `tabEmployee` WHERE name=%s", e)
        for u in (cls.user_a, cls.user_b, cls.user_hr):
            frappe.db.sql("DELETE FROM `tabUser` WHERE email=%s", u)
        frappe.db.commit()

    @classmethod
    def _cleanup(cls):
        names = (cls.emp_a, cls.emp_b, cls.emp_hr)
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

    def _checkout_today(self, user, login, task_time):
        frappe.set_user(user)
        submit_morning_log(new_tasks=json.dumps([{"description": "job"}]), login_time=login, work_location="Office")
        task = get_calendar_range(today(), today())["tasks"][0]
        submit_eod_log(lunch_from="", lunch_to="", logout_time="23:00",
                       task_updates=json.dumps([{"name": task["name"], "status": "Done", "actual_time": task_time}]),
                       adhoc_tasks="[]")
        frappe.set_user("Administrator")

    def _report(self, **kw):
        return get_management_report(kw.pop("from_date", today()), kw.pop("to_date", today()), **kw)

    def _mine(self, report, key):
        return next((e for e in report["employees"] if e["employee"] == key), None)

    def test_denied_without_role(self):
        frappe.set_user(self.user_a)
        with self.assertRaises(frappe.PermissionError):
            get_management_report(today(), today())

    def test_allowed_for_hr_manager(self):
        frappe.set_user(self.user_hr)
        self.assertIn("employees", get_management_report(today(), today()))

    def test_totals_ot_and_login_series(self):
        self._checkout_today(self.user_a, "10:00", "9h 30m")
        report = self._report()
        a = self._mine(report, self.emp_a)
        self.assertEqual((a["days_worked"], a["days_checked_out"]), (1, 1))
        self.assertEqual((a["task_minutes"], a["task_hours"]), (570, "9h 30m"))
        self.assertEqual((a["ot_minutes"], a["ot_hours"]), (90, "1h 30m"))
        ot = [r for r in report["overtime"] if r["employee"] == self.emp_a]
        self.assertEqual((len(ot), ot[0]["ot_hours"], ot[0]["date"]), (1, "1h 30m", today()))
        series = report["login_series"][self.emp_a]
        self.assertEqual((series[0]["date"], series[0]["minutes"]), (today(), 600))

    def test_no_overtime_at_or_below_standard_day(self):
        self._checkout_today(self.user_b, "09:00", "8h")
        report = self._report()
        self.assertEqual(self._mine(report, self.emp_b)["ot_minutes"], 0)
        self.assertFalse([r for r in report["overtime"] if r["employee"] == self.emp_b])

    def test_employee_without_logs_is_listed_with_zeros(self):
        row = self._mine(self._report(), self.emp_b)
        self.assertEqual((row["days_worked"], row["task_minutes"], row["ot_minutes"]), (0, 0, 0))

    def test_date_filter(self):
        self._checkout_today(self.user_a, "09:00", "2h")
        report = self._report(from_date=add_days(today(), -10), to_date=add_days(today(), -5))
        self.assertEqual(self._mine(report, self.emp_a)["days_worked"], 0)
        self.assertNotIn(self.emp_a, report["login_series"])

    def test_department_filter(self):
        self._checkout_today(self.user_a, "09:00", "2h")
        other = self._report(department="Nonexistent Dept")
        self.assertIsNone(self._mine(other, self.emp_a))
        self.assertIsNotNone(self._mine(self._report(department=self.dept), self.emp_a))

    def test_late_checkouts_listed_with_status(self):
        frappe.set_user(self.user_a)
        submit_morning_log(new_tasks=json.dumps([{"description": "job"}]), login_time="09:00", work_location="Office")
        frappe.set_user("Administrator")
        name = frappe.db.get_value("Daily Work Log", {"employee": self.emp_a, "date": today()})
        frappe.db.set_value("Daily Work Log", name, {
            "unlock_requested_at": now_datetime(), "unlock_request_reason": "Server outage",
        })
        row = next(r for r in self._report()["late_checkouts"] if r["employee"] == self.emp_a)
        self.assertEqual((row["status"], row["reason"]), ("Pending approval", "Server outage"))
        frappe.db.set_value("Daily Work Log", name, {
            "checkout_unlocked": 1, "unlocked_by": "Administrator", "unlock_reason": "ok",
        })
        row = next(r for r in self._report()["late_checkouts"] if r["employee"] == self.emp_a)
        self.assertEqual((row["status"], row["approved_by"]), ("Approved, not checked out yet", "Administrator"))
        self.assertEqual(self._mine(self._report(), self.emp_a)["late_checkouts"], 1)

    def test_range_validation(self):
        with self.assertRaises(frappe.ValidationError):
            get_management_report(today(), add_days(today(), -1))
        with self.assertRaises(frappe.ValidationError):
            get_management_report(add_days(today(), -400), today())


class TestEmployeeReport(TestManagementReport):
    """get_employee_report: per-day times, task counts and task rows."""

    def test_denied_without_role(self):
        from st_attendance_tracker.management_report import get_employee_report
        frappe.set_user(self.user_a)
        with self.assertRaises(frappe.PermissionError):
            get_employee_report(self.emp_a, today(), today())

    def test_days_counts_and_tasks(self):
        from st_attendance_tracker.management_report import get_employee_report
        frappe.set_user(self.user_a)
        submit_morning_log(new_tasks=json.dumps([{"description": "one", "project_name": "Proj"}, {"description": "two"}]),
                           login_time="10:00", work_location="Office")
        tasks = get_calendar_range(today(), today())["tasks"]
        submit_eod_log(lunch_from="", lunch_to="", logout_time="18:00", adhoc_tasks="[]",
                       task_updates=json.dumps([
                           {"name": tasks[0]["name"], "status": "Done", "actual_time": "2h"},
                           {"name": tasks[1]["name"], "status": "Pending", "actual_time": "", "remarks": "blocked"}]))
        frappe.set_user("Administrator")
        r = get_employee_report(self.emp_a, today(), today())
        self.assertEqual((r["days_worked"], r["range_days"]), (1, 1))
        day = r["days"][0]
        self.assertEqual((day["login_minutes"], day["login_label"].lower().startswith("10:00"), day["checked_out"]), (600, True, True))
        self.assertEqual((day["tasks_total"], day["tasks_done"], day["task_hours"]), (2, 1, "2h"))
        self.assertEqual(r["avg_login_minutes"], 600)
        c = r["task_counts"]
        # An unfinished task that carries forward is stored as Rolled Over on its own day.
        self.assertEqual((c["total"], c["completed"], c["pending"], c["rolled_over"]), (2, 1, 0, 1))
        by_desc = {t["description"]: t for t in r["tasks"]}
        self.assertEqual((by_desc["one"]["status"], by_desc["one"]["actual"], by_desc["one"]["project"]), ("Done", "2h", "Proj"))
        self.assertEqual(by_desc["two"]["remarks"], "blocked")

    def test_other_employees_tasks_never_included(self):
        from st_attendance_tracker.management_report import get_employee_report
        self._checkout_today(self.user_a, "09:00", "1h")
        self._checkout_today(self.user_b, "09:00", "3h")
        r = get_employee_report(self.emp_a, today(), today())
        self.assertEqual(len(r["tasks"]), 1)
        self.assertEqual(r["tasks"][0]["actual"], "1h")

    def test_no_logs_and_bad_input(self):
        from st_attendance_tracker.management_report import get_employee_report
        r = get_employee_report(self.emp_b, today(), today())
        self.assertEqual((r["days"], r["tasks"], r["avg_login_minutes"], r["task_counts"]["total"]), ([], [], None, 0))
        with self.assertRaises(frappe.DoesNotExistError):
            get_employee_report("EMP-NOPE", today(), today())
        with self.assertRaises(frappe.ValidationError):
            get_employee_report(self.emp_b, today(), add_days(today(), -1))

    def test_late_checkout_day_is_flagged(self):
        from st_attendance_tracker.management_report import get_employee_report
        self._checkout_today(self.user_a, "09:00", "1h")
        name = frappe.db.get_value("Daily Work Log", {"employee": self.emp_a, "date": today()})
        frappe.db.set_value("Daily Work Log", name, {"unlock_requested_at": now_datetime(), "unlock_request_reason": "outage", "checkout_unlocked": 1, "unlocked_by": "Administrator"})
        r = get_employee_report(self.emp_a, today(), today())
        self.assertTrue(r["days"][0]["late_checkout"])
        self.assertEqual((len(r["late_checkouts"]), r["late_checkouts"][0]["reason"]), (1, "outage"))


class TestReportOverview(TestManagementReport):
    """Overview data: previous period, trend, overtime by department, task status, half-day overtime."""

    def _log_on(self, employee, date, task_hours, login="10:00:00", half=None):
        log = frappe.get_doc({"doctype": "Daily Work Log", "employee": employee, "date": date, "morning_submitted": 1,
                              "login_time": login, "half_day_session": half})
        log.flags.ignore_permissions = True
        log.append("tasks", {"description": "t", "status": "Done", "actual_time": task_hours, "task_type": "Ad-hoc",
                             "series_id": frappe.generate_hash(length=32), "origin_date": date, "sequence": 1})
        log.insert(ignore_permissions=True)
        return log

    def test_half_day_overtime_is_above_four_hours(self):
        self._log_on(self.emp_a, today(), 5, half="First Half")
        row = next(e for e in self._report()["employees"] if e["employee"] == self.emp_a)
        self.assertEqual(row["ot_minutes"], 60)               # 5h on a half day: 1h over 4h
        self._cleanup()
        self._log_on(self.emp_a, today(), 5)
        row = next(e for e in self._report()["employees"] if e["employee"] == self.emp_a)
        self.assertEqual(row["ot_minutes"], 0)                # 5h on a full day: none

    def test_half_day_leave_application_also_lowers_the_threshold(self):
        self._log_on(self.emp_a, today(), 5)
        with patch("st_attendance_tracker.management_report._half_day_pairs", return_value={(self.emp_a, today())}):
            row = next(e for e in self._report()["employees"] if e["employee"] == self.emp_a)
        self.assertEqual(row["ot_minutes"], 60)

    def test_previous_period_matches_the_same_length_before(self):
        yesterday = add_days(today(), -1)
        self._log_on(self.emp_a, yesterday, 10, login="09:00:00")
        report = get_management_report(today(), today())
        self.assertEqual(report["previous"]["to_date"], yesterday)
        self.assertEqual((report["previous"]["days_worked"], report["previous"]["ot_minutes"], report["previous"]["avg_login_minutes"]),
                         (1, 120, 540))
        self.assertEqual(report["previous"]["from_date"], yesterday)

    def test_trend_averages_each_day(self):
        self._log_on(self.emp_a, today(), 1, login="09:00:00")
        self._log_on(self.emp_b, today(), 1, login="11:00:00")
        report = self._report()
        self.assertEqual(report["trend"], [{"date": today(), "avg_login_minutes": 600, "count": 2}])
        self.assertEqual(report["avg_login_minutes"], 600)

    def test_overtime_by_department_and_task_status(self):
        self._log_on(self.emp_a, today(), 10)
        self._log_on(self.emp_b, today(), 9)
        report = self._report(department=self.dept)
        self.assertEqual(report["ot_by_department"], [{"department": self.dept, "ot_minutes": 180, "ot_hours": "3h"}])
        self.assertEqual((report["task_status"]["total"], report["task_status"]["completed"]), (2, 2))

    def test_empty_period_has_no_trend_and_no_division_errors(self):
        report = get_management_report("2001-01-01", "2001-01-02")
        self.assertEqual((report["trend"], report["avg_login_minutes"], report["ot_by_department"]), ([], None, []))
        self.assertEqual(report["previous"]["days_worked"], 0)

    def test_each_employee_carries_task_counts_and_overtime_rows_carry_task_minutes(self):
        self._log_on(self.emp_a, today(), 10)
        report = self._report(department=self.dept)
        row = next(e for e in report["employees"] if e["employee"] == self.emp_a)
        self.assertEqual((row["tasks_done"], row["tasks_total"]), (1, 1))
        quiet = next(e for e in report["employees"] if e["employee"] == self.emp_b)
        self.assertEqual((quiet["tasks_done"], quiet["tasks_total"]), (0, 0))
        ot = [r for r in report["overtime"] if r["employee"] == self.emp_a][0]
        self.assertEqual((ot["task_minutes"], ot["ot_minutes"]), (600, 120))


class TestEmployeeViewDetails(TestReportOverview):
    """Employee view: after-midnight checkouts, medians and the day calendar."""

    def _worked(self, employee, date, login, logout, late=0):
        log = self._log_on(employee, date, 1, login=login)
        frappe.db.set_value("Daily Work Log", log.name, {"eod_submitted": 1, "logout_time": logout, "is_late": late})
        return log

    def test_checkout_after_midnight_is_plotted_as_the_next_day(self):
        from st_attendance_tracker.management_report import get_employee_report

        self._worked(self.emp_a, today(), "10:00:00", "01:30:00")
        day = get_employee_report(self.emp_a, today(), today())["days"][0]
        self.assertEqual((day["logout_minutes"], day["logout_next_day"]), (25 * 60 + 30, True))
        self.assertIn("(next day)", day["logout_label"])

    def test_averages_and_medians_use_the_corrected_times(self):
        from st_attendance_tracker.management_report import get_employee_report

        days = [add_days(today(), -i) for i in range(3)]
        for date, logout in zip(days, ("19:00:00", "20:00:00", "01:00:00")):          # 19:00, 20:00, 01:00 next day
            self._worked(self.emp_a, date, "10:00:00", logout)
        r = get_employee_report(self.emp_a, days[-1], days[0])
        self.assertEqual(r["median_logout_minutes"], 20 * 60)
        self.assertEqual(r["avg_logout_minutes"], int((19 * 60 + 20 * 60 + 25 * 60) / 3))
        self.assertEqual(r["median_login_minutes"], 600)

    def test_median_helper(self):
        from st_attendance_tracker.management_report import _median

        self.assertEqual((_median([]), _median([5]), _median([1, 9, 3]), _median([1, 2, 3, 10])), (None, 5, 3, 2))

    def test_calendar_marks_present_late_leave_off_missing_and_future(self):
        from st_attendance_tracker.management_report import get_employee_report

        start = add_days(today(), -6)
        self._worked(self.emp_a, add_days(today(), -1), "09:00:00", "18:00:00")
        self._worked(self.emp_a, add_days(today(), -2), "11:00:00", "18:00:00", late=1)
        real = frappe.get_all
        leave_day = add_days(today(), -3)

        def fake(doctype, *args, **kwargs):
            if doctype == "Leave Application":
                return [frappe._dict(from_date=leave_day, to_date=leave_day)]
            return real(doctype, *args, **kwargs)

        with patch("frappe.get_all", side_effect=fake):
            r = get_employee_report(self.emp_a, start, add_days(today(), 1))
        cal = {d["date"]: d["status"] for d in r["calendar"]}
        special = {add_days(today(), -1), add_days(today(), -2), leave_day}
        self.assertEqual(cal[add_days(today(), -1)], "present")
        self.assertEqual(cal[add_days(today(), -2)], "late")
        self.assertEqual(cal[leave_day], "leave")
        self.assertEqual(len(r["calendar"]), 8)
        for date, status in cal.items():
            if date not in special and frappe.utils.getdate(date).weekday() == 6:
                self.assertEqual(status, "off")                       # Sunday with no check-in
            elif date not in special and date > today():
                self.assertEqual(status, "future")
            elif date not in special and date < today():
                self.assertIn(status, ("missing", "off"))             # a holiday-list day is off, else missing

    def test_department_filter_lists_only_departments_with_active_employees(self):
        depts = self._report()["departments"]
        self.assertIn(self.dept, depts)
        self.assertEqual(depts, sorted(set(depts)))
        self.assertFalse(frappe.db.exists("Department", {"name": ["in", depts], "is_group": 1}))
        empty = frappe.get_all("Department", filters={"is_group": 0}, pluck="name")
        with_staff = set(frappe.get_all("Employee", filters={"status": "Active", "department": ["is", "set"]}, pluck="department"))
        self.assertTrue(set(depts) == with_staff and len(empty) >= len(depts))
