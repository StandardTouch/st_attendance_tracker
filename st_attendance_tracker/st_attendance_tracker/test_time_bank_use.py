"""Time bank phase 2: taking banked hours at checkout, allocation, release, attendance."""
import json

import frappe
from unittest.mock import patch
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, today

from st_attendance_tracker.api import get_page_state, submit_eod_log, submit_morning_log
from st_attendance_tracker.calendar_api import get_calendar_range
from st_attendance_tracker.st_attendance_tracker.doctype.daily_work_log.test_daily_work_log import (
    _make_employee,
)
from st_attendance_tracker.time_bank import (
    apply_to_attendance, apply_use, available_hours, get_balance, release_use, used_on, validate_use,
)

YESTERDAY, TWO_DAYS_AGO = add_days(today(), -1), add_days(today(), -2)


class TestTimeBankUse(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        company = frappe.db.get_single_value("Global Defaults", "default_company") or "_Test Company"
        cls.dept = (
            frappe.db.get_value("Department", {"department_name": "_QA Dept", "company": company}, "name")
            or f"_QA Dept - {company}"
        )
        cls.user_a, cls.user_b = (f"qa_tbu_{k}@test.example.com" for k in ("a", "b"))
        cls.emp_a = _make_employee("QATbuA", cls.dept, cls.user_a, ["Employee"])
        cls.emp_b = _make_employee("QATbuB", cls.dept, cls.user_b, ["Employee"])
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        cls._cleanup()
        for e in (cls.emp_a, cls.emp_b):
            frappe.db.sql("DELETE FROM `tabEmployee` WHERE name=%s", e)
        for u in (cls.user_a, cls.user_b):
            frappe.db.sql("DELETE FROM `tabUser` WHERE email=%s", u)
        frappe.db.commit()

    @classmethod
    def _cleanup(cls):
        names = (cls.emp_a, cls.emp_b)
        frappe.db.sql("DELETE FROM `tabExtra Hours Source` WHERE parent IN (SELECT name FROM `tabExtra Hours Entry` WHERE employee IN %s)", (names,))
        frappe.db.sql("DELETE FROM `tabExtra Hours Entry` WHERE employee IN %s", (names,))
        frappe.db.sql("DELETE FROM `tabAdditional Work` WHERE employee IN %s", (names,))
        frappe.db.sql("DELETE FROM `tabTask Entry` WHERE parent IN (SELECT name FROM `tabDaily Work Log` WHERE employee IN %s)", (names,))
        frappe.db.sql("DELETE FROM `tabDaily Work Log` WHERE employee IN %s", (names,))
        frappe.db.sql("DELETE FROM `tabEmployee Checkin` WHERE employee IN %s", (names,))
        frappe.db.sql("DELETE FROM `tabAttendance` WHERE employee IN %s", (names,))
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

    def _credit(self, employee, hours, date, source="Overtime", **extra):
        return frappe.get_doc({"doctype": "Extra Hours Entry", "employee": employee, "date": date, "entry_type": "Credit",
                               "source": source, "hours": hours, **extra}).insert(ignore_permissions=True)

    def _checkin(self, user, work_hours="7h"):
        frappe.set_user(user)
        submit_morning_log(new_tasks=json.dumps([{"description": "job"}]), login_time="09:00", work_location="Office")
        self.task = get_calendar_range(today(), today())["tasks"][0]
        self.work_hours = work_hours

    def _checkout(self, **kw):
        return submit_eod_log(lunch_from="", lunch_to="", logout_time="18:00", adhoc_tasks="[]",
                              task_updates=json.dumps([{"name": self.task["name"], "status": "Done", "actual_time": self.work_hours}]), **kw)

    def _log(self, employee):
        return frappe.get_doc("Daily Work Log", {"employee": employee, "date": today()})

    def _used(self, credit):
        return frappe.db.get_value("Extra Hours Entry", credit.name, "used_hours")

    # ── availability ──────────────────────────────────────────────────────────

    def test_only_earlier_days_can_cover_a_day(self):
        self._credit(self.emp_a, 2, YESTERDAY)
        self._credit(self.emp_a, 5, today())            # same day's own extra work cannot cover itself
        self.assertEqual(available_hours(self.emp_a, today()), 2.0)
        self.assertEqual(get_balance(self.emp_a), 7.0)  # but the whole bank still counts

    def test_expired_credit_is_not_available(self):
        self._credit(self.emp_a, 2, add_days(today(), -61))
        self.assertEqual(available_hours(self.emp_a, today()), 0.0)

    def test_validate_use(self):
        self._credit(self.emp_a, 2, YESTERDAY)
        self.assertEqual(validate_use(self.emp_a, today(), 0), 0.0)
        self.assertEqual(validate_use(self.emp_a, today(), "1.5"), 1.5)
        self.assertEqual(validate_use(self.emp_a, today(), 2), 2.0)
        for bad in (-1, 2.5):
            with self.assertRaises(frappe.ValidationError):
                validate_use(self.emp_a, today(), bad)

    # ── allocation ────────────────────────────────────────────────────────────

    def test_oldest_credit_is_used_first_and_a_use_can_span_credits(self):
        old = self._credit(self.emp_a, 1, TWO_DAYS_AGO)
        new = self._credit(self.emp_a, 2, YESTERDAY)
        self._checkin(self.user_a)
        frappe.set_user("Administrator")
        use = apply_use(self._log(self.emp_a), 1.5)
        self.assertEqual([(r.credit, r.hours) for r in use.allocations], [(old.name, 1.0), (new.name, 0.5)])
        self.assertEqual((self._used(old), self._used(new)), (1.0, 0.5))
        self.assertEqual(available_hours(self.emp_a, add_days(today(), 1)), 1.5)

    def test_partial_use_now_and_the_rest_later(self):
        credit = self._credit(self.emp_a, 2, YESTERDAY)
        self._checkin(self.user_a)
        frappe.set_user("Administrator")
        apply_use(self._log(self.emp_a), 1)
        self.assertEqual(self._used(credit), 1.0)
        self.assertEqual(available_hours(self.emp_a, add_days(today(), 3)), 1.0)   # the remaining hour stays for later

    def test_apply_use_twice_replaces_the_first_use(self):
        credit = self._credit(self.emp_a, 3, YESTERDAY)
        self._checkin(self.user_a)
        frappe.set_user("Administrator")
        log = self._log(self.emp_a)
        apply_use(log, 1)
        apply_use(log, 2)
        self.assertEqual(self._used(credit), 2.0)
        self.assertEqual(used_on(self.emp_a, today()), 2.0)

    def test_release_gives_the_hours_back(self):
        credit = self._credit(self.emp_a, 2, YESTERDAY)
        self._checkin(self.user_a)
        frappe.set_user("Administrator")
        log = self._log(self.emp_a)
        apply_use(log, 1.5)
        release_use(log.name)
        self.assertEqual(self._used(credit), 0.0)
        self.assertEqual(used_on(self.emp_a, today()), 0.0)

    def test_additional_work_shows_compensated_and_remaining(self):
        aw = frappe.get_doc({"doctype": "Additional Work", "employee": self.emp_a, "work_date": YESTERDAY, "hours_spent": 2,
                             "description": "extra", "status": "Done", "login_time": "19:00:00", "logout_time": "21:00:00"})
        with patch("st_attendance_tracker.additional_work_mail.queue_additional_work_email"), patch(
            "st_attendance_tracker.st_attendance_tracker.doctype.additional_work.additional_work.additional_work_allowed",
            return_value=(True, "checked_out"),
        ):
            aw.insert(ignore_permissions=True)
        self.assertEqual(frappe.db.get_value("Additional Work", aw.name, ["compensated_hours", "remaining_hours"]), (0.0, 2.0))
        self._checkin(self.user_a)
        frappe.set_user("Administrator")
        apply_use(self._log(self.emp_a), 1)
        self.assertEqual(frappe.db.get_value("Additional Work", aw.name, ["compensated_hours", "remaining_hours"]), (1.0, 1.0))
        release_use(self._log(self.emp_a).name)
        self.assertEqual(frappe.db.get_value("Additional Work", aw.name, ["compensated_hours", "remaining_hours"]), (0.0, 2.0))

    # ── checkout flow ─────────────────────────────────────────────────────────

    def test_checkout_with_banked_hours_records_the_use(self):
        credit = self._credit(self.emp_a, 2, YESTERDAY)
        self._checkin(self.user_a)
        self.assertEqual(get_page_state()["time_bank_balance"], 2.0)
        result = self._checkout(use_bank_hours=1)
        self.assertTrue(result["success"])
        frappe.set_user("Administrator")
        self.assertEqual(used_on(self.emp_a, today()), 1.0)
        self.assertEqual(self._used(credit), 1.0)
        use = frappe.get_all("Extra Hours Entry", filters={"employee": self.emp_a, "entry_type": "Use"}, fields=["source", "source_log", "hours"])
        self.assertEqual([(u.source, u.source_log, u.hours) for u in use], [("Compensation", self._log(self.emp_a).name, 1.0)])

    def test_checkout_without_the_option_uses_nothing(self):
        credit = self._credit(self.emp_a, 2, YESTERDAY)
        self._checkin(self.user_a)
        self._checkout()
        frappe.set_user("Administrator")
        self.assertEqual((self._used(credit), used_on(self.emp_a, today())), (0.0, 0.0))

    def test_checkout_rejects_more_than_the_balance_and_changes_nothing(self):
        credit = self._credit(self.emp_a, 1, YESTERDAY)
        self._checkin(self.user_a)
        with self.assertRaises(frappe.ValidationError):
            self._checkout(use_bank_hours=2)
        frappe.db.rollback()
        frappe.set_user("Administrator")
        self.assertEqual(self._log(self.emp_a).eod_submitted, 0)
        self.assertEqual((self._used(credit), used_on(self.emp_a, today())), (0.0, 0.0))

    def test_another_employees_bank_is_not_touched(self):
        mine = self._credit(self.emp_a, 1, YESTERDAY)
        theirs = self._credit(self.emp_b, 5, YESTERDAY)
        self._checkin(self.user_a)
        self._checkout(use_bank_hours=1)
        frappe.set_user("Administrator")
        self.assertEqual((self._used(mine), self._used(theirs)), (1.0, 0.0))

    def test_undoing_a_checkout_gives_the_hours_back(self):
        credit = self._credit(self.emp_a, 2, YESTERDAY)
        self._checkin(self.user_a)
        self._checkout(use_bank_hours=1)
        frappe.set_user("Administrator")
        log = self._log(self.emp_a)
        log.eod_submitted = 0
        log.save(ignore_permissions=True)
        self.assertEqual((self._used(credit), used_on(self.emp_a, today())), (0.0, 0.0))

    def test_deleting_a_log_gives_the_hours_back(self):
        credit = self._credit(self.emp_a, 2, YESTERDAY)
        self._checkin(self.user_a)
        self._checkout(use_bank_hours=1)
        frappe.set_user("Administrator")
        frappe.delete_doc("Daily Work Log", self._log(self.emp_a).name, force=True)
        self.assertEqual(self._used(credit), 0.0)

    # ── attendance ────────────────────────────────────────────────────────────

    def _shift(self, threshold=8):
        name = "QA TB Shift"
        if not frappe.db.exists("Shift Type", name):
            frappe.get_doc({"doctype": "Shift Type", "name": name, "start_time": "09:00:00", "end_time": "18:00:00",
                            "working_hours_threshold_for_half_day": threshold}).insert()
        frappe.db.set_value("Shift Type", name, "working_hours_threshold_for_half_day", threshold)
        return name

    def _attendance(self, status, hours, **extra):
        return frappe._dict(doctype="Attendance", employee=self.emp_a, attendance_date=add_days(today(), -3), status=status,
                            working_hours=hours, shift=self._shift(), leave_application=None, flags=frappe._dict(), **extra)

    def _book(self, hours):
        """A Use entry of `hours` on the attendance date."""
        credit = self._credit(self.emp_a, hours, add_days(today(), -10))
        frappe.get_doc({"doctype": "Extra Hours Entry", "employee": self.emp_a, "date": add_days(today(), -3), "entry_type": "Use",
                        "source": "Compensation", "hours": hours, "allocations": [{"credit": credit.name, "hours": hours}]}).insert(ignore_permissions=True)

    def test_banked_hours_turn_a_short_day_into_present(self):
        self._book(1)
        doc = self._attendance("Half Day", 7)
        apply_to_attendance(doc)
        self.assertEqual(doc.status, "Present")
        self.assertEqual(doc.flags.time_bank_hours, 1.0)

    def test_not_enough_banked_hours_stay_half_day(self):
        self._book(0.5)
        doc = self._attendance("Half Day", 7)
        apply_to_attendance(doc)
        self.assertEqual(doc.status, "Half Day")

    def test_no_use_means_no_change(self):
        doc = self._attendance("Half Day", 7)
        apply_to_attendance(doc)
        self.assertEqual(doc.status, "Half Day")

    def test_leave_based_half_day_and_other_statuses_are_untouched(self):
        self._book(1)
        leave = self._attendance("Half Day", 7)
        leave.leave_application = "HR-LAP-X"
        apply_to_attendance(leave)
        self.assertEqual(leave.status, "Half Day")
        absent = self._attendance("Absent", 0)
        apply_to_attendance(absent)
        self.assertEqual(absent.status, "Absent")

    def test_real_attendance_insert_goes_through_the_hook(self):
        self._book(1)
        doc = frappe.get_doc({"doctype": "Attendance", "employee": self.emp_a, "attendance_date": add_days(today(), -3),
                              "status": "Half Day", "working_hours": 7, "shift": self._shift(), "company": frappe.db.get_value("Employee", self.emp_a, "company")})
        doc.insert(ignore_permissions=True)
        self.assertEqual(doc.status, "Present")
        self.assertTrue(frappe.db.exists("Comment", {"reference_doctype": "Attendance", "reference_name": doc.name, "comment_type": "Info"}))


class TestTimeBankReading(TestTimeBankUse):
    """Ledger, balances for many employees, the self-service endpoint and the report fields."""

    def test_balances_for_many_employees_in_one_call(self):
        from st_attendance_tracker.time_bank import balances_for

        self._credit(self.emp_a, 2, YESTERDAY)
        self._credit(self.emp_a, 1, add_days(today(), -61))      # expired, not counted
        self.assertEqual(balances_for([self.emp_a, self.emp_b]), {self.emp_a: 2.0, self.emp_b: 0.0})
        self.assertEqual(balances_for([]), {})

    def test_ledger_lists_range_entries_and_unused_older_credits(self):
        from st_attendance_tracker.time_bank import get_ledger

        old_unused = self._credit(self.emp_a, 3, add_days(today(), -40))
        old_used = self._credit(self.emp_a, 1, add_days(today(), -41))
        frappe.db.set_value("Extra Hours Entry", old_used.name, "used_hours", 1)       # fully used: hidden outside the range
        self._credit(self.emp_a, 2, YESTERDAY)
        rows = get_ledger(self.emp_a, add_days(today(), -5), today())
        self.assertEqual([(r["date"], r["hours"]) for r in rows], [(YESTERDAY, 2.0), (str(old_unused.date), 3.0)])

    def test_ledger_shows_where_a_use_took_its_hours_from(self):
        from st_attendance_tracker.time_bank import get_ledger

        self._credit(self.emp_a, 1, TWO_DAYS_AGO)
        self._credit(self.emp_a, 2, YESTERDAY)
        self._checkin(self.user_a)
        frappe.set_user("Administrator")
        apply_use(self._log(self.emp_a), 1.5)
        use = [r for r in get_ledger(self.emp_a, TWO_DAYS_AGO, today()) if r["entry_type"] == "Use"][0]
        self.assertEqual((use["hours"], use["taken_from"]), (1.5, [{"date": TWO_DAYS_AGO, "hours": 1.0}, {"date": YESTERDAY, "hours": 0.5}]))

    def test_my_time_bank_is_private_to_the_caller(self):
        from st_attendance_tracker.api import get_my_time_bank

        self._credit(self.emp_a, 2, YESTERDAY)
        self._credit(self.emp_b, 9, YESTERDAY)
        frappe.set_user(self.user_a)
        mine = get_my_time_bank()
        self.assertEqual((mine["employee"], mine["balance"]), (self.emp_a, 2.0))
        self.assertTrue(all(e["hours"] == 2.0 for e in mine["entries"]))

    def test_report_carries_the_bank_balance_and_ledger(self):
        from st_attendance_tracker.management_report import get_employee_report, get_management_report

        self._credit(self.emp_a, 2, YESTERDAY)
        report = get_management_report(add_days(today(), -2), today())
        row = next(e for e in report["employees"] if e["employee"] == self.emp_a)
        self.assertEqual((row["bank_hours"], row["bank_label"]), (2.0, "2h"))
        detail = get_employee_report(self.emp_a, add_days(today(), -2), today())
        self.assertEqual((detail["time_bank"]["balance"], detail["time_bank"]["balance_label"]), (2.0, "2h"))
        self.assertEqual([e["hours"] for e in detail["time_bank"]["entries"]], [2.0])


class TestMyHistoryExtraHours(TestTimeBankUse):
    """My History shows the overtime of a day and what was used from it."""

    def test_history_labels_overtime_and_use(self):
        from st_attendance_tracker.api import get_my_history

        frappe.set_user(self.user_a)
        submit_morning_log(new_tasks=json.dumps([{"description": "long day"}]), login_time="09:00", work_location="Office")
        task = get_calendar_range(today(), today())["tasks"][0]
        submit_eod_log(lunch_from="", lunch_to="", logout_time="23:00", adhoc_tasks="[]",
                       task_updates=json.dumps([{"name": task["name"], "status": "Done", "actual_time": "10h"}]))
        row = get_my_history()["logs"][0]
        self.assertEqual((row["overtime_hours"], row["overtime_used_hours"], row["extra_hours_used_today"]), (2.0, 0.0, 0.0))
        frappe.db.set_value("Extra Hours Entry", {"employee": self.emp_a, "source": "Overtime"}, "used_hours", 1.5)
        self.assertEqual(get_my_history()["logs"][0]["overtime_used_hours"], 1.5)

    def test_history_shows_hours_used_on_the_day(self):
        from st_attendance_tracker.api import get_my_history

        self._credit(self.emp_a, 2, YESTERDAY)
        self._checkin(self.user_a)
        self._checkout(use_bank_hours=1)
        row = get_my_history()["logs"][0]
        self.assertEqual((row["overtime_hours"], row["extra_hours_used_today"]), (0, 1.0))

    def test_history_is_private_and_a_normal_day_has_no_labels(self):
        from st_attendance_tracker.api import get_my_history

        self._credit(self.emp_b, 2, YESTERDAY)
        self._checkin(self.user_a, "7h")
        self._checkout()
        row = get_my_history()["logs"][0]
        self.assertEqual((row["overtime_hours"], row["extra_hours_used_today"]), (0, 0))
