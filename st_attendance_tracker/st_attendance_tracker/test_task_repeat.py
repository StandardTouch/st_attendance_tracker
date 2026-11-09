"""Repeat next day: a flagged ordinary task comes back on the next working day, even when Done."""
import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, today

from st_attendance_tracker.api import (
    _ensure_recurring_tasks, _get_next_working_date, _get_work_log, _rollover_pending_tasks,
    _safety_rollover, save_recurring_task, submit_eod_log, submit_morning_log,
)
from st_attendance_tracker.calendar_api import get_calendar_range, set_task_repeat
from st_attendance_tracker.st_attendance_tracker.doctype.daily_work_log.test_daily_work_log import _make_employee


class TestTaskRepeat(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        company = frappe.db.get_single_value("Global Defaults", "default_company") or "_Test Company"
        cls.dept = (
            frappe.db.get_value("Department", {"department_name": "_QA Dept", "company": company}, "name")
            or f"_QA Dept - {company}"
        )
        cls.user_a, cls.user_b = "qa_rep_a@test.example.com", "qa_rep_b@test.example.com"
        cls.emp_a = _make_employee("QARepA", cls.dept, cls.user_a, ["Employee"])
        cls.emp_b = _make_employee("QARepB", cls.dept, cls.user_b, ["Employee"])
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
        emps = (cls.emp_a, cls.emp_b)
        frappe.db.sql("DELETE FROM `tabTask Entry` WHERE parent IN "
                      "(SELECT name FROM `tabDaily Work Log` WHERE employee IN (%s,%s))", emps)
        frappe.db.sql("DELETE FROM `tabDaily Work Log` WHERE employee IN (%s,%s)", emps)
        frappe.db.sql("DELETE FROM `tabEmployee Checkin` WHERE employee IN (%s,%s)", emps)
        frappe.db.sql("DELETE FROM `tabRecurring Task Template` WHERE employee IN (%s,%s)", emps)
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

    # helpers ---------------------------------------------------------------
    def _check_in(self, user, *descriptions):
        frappe.set_user(user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": d, "project_name": "Proj", "estimated_time": "1h"} for d in descriptions]),
            login_time="09:00", work_location="Office",
        )
        return {t["description"]: t for t in get_calendar_range(today(), today())["tasks"]}

    def _mark(self, emp, description, status):
        log = _get_work_log(emp, today())
        row = [r for r in log.tasks if r.description == description][0]
        row.status = status
        if status == "Done":
            row.actual_time = 1
        log.save(ignore_permissions=True)

    def _rows(self, emp, date, description=None):
        log = _get_work_log(emp, date)
        rows = list(log.tasks) if log else []
        return [r for r in rows if description is None or r.description == description]

    def _next_day(self):
        return str(_get_next_working_date(self.emp_a, today()))

    def _checkout_rollover(self):
        _rollover_pending_tasks(self.emp_a, today())

    # toggle ----------------------------------------------------------------
    def test_toggle_sets_and_clears_the_flag_on_that_task_only(self):
        tasks = self._check_in(self.user_a, "Repeat me", "Plain task")
        self.assertEqual(set_task_repeat(tasks["Repeat me"]["name"], 1), {"repeat": True})
        self.assertEqual(self._rows(self.emp_a, today(), "Repeat me")[0].repeat_next_day, 1)
        self.assertEqual(self._rows(self.emp_a, today(), "Plain task")[0].repeat_next_day, 0)
        self.assertEqual(set_task_repeat(tasks["Repeat me"]["name"], 0), {"repeat": False})
        self.assertEqual(self._rows(self.emp_a, today(), "Repeat me")[0].repeat_next_day, 0)

    def test_toggle_creates_no_recurring_template(self):
        task = self._check_in(self.user_a, "No template")["No template"]
        set_task_repeat(task["name"], 1)
        self.assertFalse(frappe.get_all("Recurring Task Template", filters={"employee": self.emp_a}))
        self.assertEqual(self._rows(self.emp_a, today(), "No template")[0].task_type, "Planned")

    def test_calendar_range_exposes_the_flag(self):
        task = self._check_in(self.user_a, "Shown")["Shown"]
        self.assertFalse(task["repeat_next_day"])
        set_task_repeat(task["name"], 1)
        shown = {t["description"]: t for t in get_calendar_range(today(), today())["tasks"]}["Shown"]
        self.assertTrue(shown["repeat_next_day"])

    # the behaviour that was asked for --------------------------------------
    def test_done_flagged_task_comes_back_next_working_day(self):
        task = self._check_in(self.user_a, "Daily follow up")["Daily follow up"]
        set_task_repeat(task["name"], 1)
        self._mark(self.emp_a, "Daily follow up", "Done")
        self._checkout_rollover()
        copy = self._rows(self.emp_a, self._next_day(), "Daily follow up")
        self.assertEqual(len(copy), 1)
        self.assertEqual((copy[0].status, copy[0].task_type, copy[0].repeat_next_day), ("Pending", "Planned", 1))
        self.assertEqual(str(copy[0].origin_date), self._next_day())
        self.assertNotEqual(copy[0].series_id, self._rows(self.emp_a, today(), "Daily follow up")[0].series_id)

    def test_done_task_without_the_flag_does_not_come_back(self):
        self._check_in(self.user_a, "One off")
        self._mark(self.emp_a, "One off", "Done")
        self._checkout_rollover()
        self.assertFalse(self._rows(self.emp_a, self._next_day(), "One off"))

    def test_only_flagged_tasks_repeat(self):
        tasks = self._check_in(self.user_a, "Repeats", "Stays done")
        set_task_repeat(tasks["Repeats"]["name"], 1)
        self._mark(self.emp_a, "Repeats", "Done")
        self._mark(self.emp_a, "Stays done", "Done")
        self._checkout_rollover()
        self.assertEqual([r.description for r in self._rows(self.emp_a, self._next_day())], ["Repeats"])

    def test_repeat_is_idempotent(self):
        task = self._check_in(self.user_a, "Once only")["Once only"]
        set_task_repeat(task["name"], 1)
        self._mark(self.emp_a, "Once only", "Done")
        self._checkout_rollover()
        self._checkout_rollover()
        self.assertEqual(len(self._rows(self.emp_a, self._next_day(), "Once only")), 1)

    def test_flag_persists_so_it_keeps_coming_back_until_turned_off(self):
        task = self._check_in(self.user_a, "Chain")["Chain"]
        set_task_repeat(task["name"], 1)
        self._mark(self.emp_a, "Chain", "Done")
        self._checkout_rollover()
        next_day = self._next_day()
        copy = self._rows(self.emp_a, next_day, "Chain")[0]
        log = _get_work_log(self.emp_a, next_day)
        row = [r for r in log.tasks if r.name == copy.name][0]
        row.status, row.actual_time = "Done", 1
        log.save(ignore_permissions=True)
        _rollover_pending_tasks(self.emp_a, next_day)
        self.assertEqual(len(self._rows(self.emp_a, str(_get_next_working_date(self.emp_a, next_day)), "Chain")), 1)

    def test_turned_off_task_stops_repeating(self):
        task = self._check_in(self.user_a, "Switch off")["Switch off"]
        set_task_repeat(task["name"], 1)
        set_task_repeat(task["name"], 0)
        self._mark(self.emp_a, "Switch off", "Done")
        self._checkout_rollover()
        self.assertFalse(self._rows(self.emp_a, self._next_day(), "Switch off"))

    def test_unfinished_flagged_task_carries_once_and_keeps_its_flag(self):
        task = self._check_in(self.user_a, "Still open")["Still open"]
        set_task_repeat(task["name"], 1)
        self._checkout_rollover()
        copies = self._rows(self.emp_a, self._next_day(), "Still open")
        self.assertEqual(len(copies), 1, "carried once, not carried and repeated")
        self.assertEqual(copies[0].repeat_next_day, 1)
        self.assertEqual(copies[0].series_id, self._rows(self.emp_a, today(), "Still open")[0].series_id)

    def test_end_to_end_checkout_creates_the_copy(self):
        task = self._check_in(self.user_a, "Full flow")["Full flow"]
        set_task_repeat(task["name"], 1)
        submit_eod_log(lunch_from="", lunch_to="", logout_time="18:00",
                       task_updates=json.dumps([{"name": task["name"], "status": "Done", "actual_time": "1h", "remarks": ""}]),
                       adhoc_tasks="[]")
        self.assertEqual(len(self._rows(self.emp_a, self._next_day(), "Full flow")), 1)

    def test_forgotten_checkout_brings_it_to_today(self):
        # a flagged Done task on the last worked day, checkout never run
        yesterday = add_days(today(), -1)
        log = frappe.new_doc("Daily Work Log")
        log.employee, log.date, log.morning_submitted, log.login_time = self.emp_a, yesterday, 1, "09:00:00"
        row = log.append("tasks", {})
        row.description, row.status, row.task_type, row.actual_time, row.repeat_next_day = "Missed checkout", "Done", "Planned", 1, 1
        row.series_id, row.origin_date = frappe.generate_hash(length=32), yesterday
        log.insert(ignore_permissions=True)
        _safety_rollover(self.emp_a, today())
        _safety_rollover(self.emp_a, today())
        self.assertEqual(len(self._rows(self.emp_a, today(), "Missed checkout")), 1)

    # guards ----------------------------------------------------------------
    def test_cannot_flag_someone_elses_task(self):
        task = self._check_in(self.user_a, "Private")["Private"]
        frappe.set_user(self.user_b)
        with self.assertRaises(frappe.PermissionError):
            set_task_repeat(task["name"], 1)
        self.assertEqual(self._rows(self.emp_a, today(), "Private")[0].repeat_next_day, 0)

    def test_unknown_task_rejected(self):
        self._check_in(self.user_a, "x")
        with self.assertRaises(frappe.PermissionError):
            set_task_repeat("does-not-exist", 1)

    def test_blocked_after_checkout(self):
        task = self._check_in(self.user_a, "Late toggle")["Late toggle"]
        submit_eod_log(lunch_from="", lunch_to="", logout_time="18:00", task_updates="[]", adhoc_tasks="[]")
        with self.assertRaises(frappe.ValidationError):
            set_task_repeat(task["name"], 1)

    def test_recurring_task_cannot_be_flagged(self):
        frappe.set_user(self.user_a)
        save_recurring_task(description="Scrum", project_name="Proj", estimated_time="30m", recurring_days="[]", is_active=1)
        submit_morning_log(new_tasks=json.dumps([{"description": "other"}]), login_time="09:00", work_location="Office")
        recurring = [r for r in self._rows(self.emp_a, today()) if r.task_type == "Recurring"]
        self.assertEqual(len(recurring), 1)
        with self.assertRaises(frappe.ValidationError):
            set_task_repeat(recurring[0].name, 1)

    def test_recurring_templates_still_work_as_before(self):
        frappe.set_user(self.user_a)
        save_recurring_task(description="Scrum", project_name="Proj", estimated_time="30m", recurring_days="[]", is_active=1)
        _ensure_recurring_tasks(self.emp_a, add_days(today(), 1))
        rows = self._rows(self.emp_a, add_days(today(), 1), "Scrum")
        self.assertEqual((len(rows), rows[0].task_type, rows[0].repeat_next_day), (1, "Recurring", 0))

    # checkout dialog sends the choice with the task update --------------------
    def test_checkout_update_can_turn_repeat_on_for_a_done_task(self):
        task = self._check_in(self.user_a, "Chosen at checkout")["Chosen at checkout"]
        submit_eod_log(lunch_from="", lunch_to="", logout_time="18:00",
                       task_updates=json.dumps([{"name": task["name"], "status": "Done", "actual_time": "1h",
                                                 "remarks": "", "repeat_next_day": 1}]), adhoc_tasks="[]")
        self.assertEqual(len(self._rows(self.emp_a, self._next_day(), "Chosen at checkout")), 1)

    def test_checkout_without_the_choice_keeps_an_existing_flag_on_a_done_task(self):
        task = self._check_in(self.user_a, "Flagged earlier")["Flagged earlier"]
        set_task_repeat(task["name"], 1)
        submit_eod_log(lunch_from="", lunch_to="", logout_time="18:00",
                       task_updates=json.dumps([{"name": task["name"], "status": "Done", "actual_time": "1h", "remarks": ""}]),
                       adhoc_tasks="[]")
        self.assertEqual(len(self._rows(self.emp_a, self._next_day(), "Flagged earlier")), 1)

    # ad-hoc tasks added after check-in -----------------------------------------
    def test_new_adhoc_task_done_with_repeat_comes_back_next_day(self):
        self._check_in(self.user_a, "Morning plan")
        submit_eod_log(lunch_from="", lunch_to="", logout_time="18:00", task_updates="[]",
                       adhoc_tasks=json.dumps([{"description": "Added after check-in", "status": "Done",
                                                "actual_time": "1h", "project_name": "New proj", "repeat_next_day": 1}]))
        today_row = self._rows(self.emp_a, today(), "Added after check-in")[0]
        self.assertEqual(today_row.repeat_next_day, 1)
        copies = self._rows(self.emp_a, self._next_day(), "Added after check-in")
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0].project_name, "New proj")

    def test_saved_adhoc_task_keeps_repeat_choice_made_at_checkout(self):
        from st_attendance_tracker.api import add_adhoc_tasks

        self._check_in(self.user_a, "Morning plan")
        created = add_adhoc_tasks(json.dumps([{"client_id": "c1", "description": "Saved mid-day", "project_name": "P"}]))["created"][0]
        submit_eod_log(lunch_from="", lunch_to="", logout_time="18:00", task_updates="[]",
                       adhoc_tasks=json.dumps([{"description": "Saved mid-day", "status": "Done", "actual_time": "30m",
                                                "project_name": "P", "series_id": created["series_id"], "repeat_next_day": 1}]))
        self.assertEqual(len(self._rows(self.emp_a, today(), "Saved mid-day")), 1)
        self.assertEqual(len(self._rows(self.emp_a, self._next_day(), "Saved mid-day")), 1)

    def test_adhoc_repeat_is_ignored_when_not_done(self):
        self._check_in(self.user_a, "Morning plan")
        submit_eod_log(lunch_from="", lunch_to="", logout_time="18:00", task_updates="[]",
                       adhoc_tasks=json.dumps([{"description": "Unfinished extra", "status": "In Progress",
                                                "actual_time": "10m", "repeat_next_day": 1}]))
        self.assertEqual(self._rows(self.emp_a, today(), "Unfinished extra")[0].repeat_next_day, 0)

    def test_adhoc_without_the_choice_does_not_repeat(self):
        self._check_in(self.user_a, "Morning plan")
        submit_eod_log(lunch_from="", lunch_to="", logout_time="18:00", task_updates="[]",
                       adhoc_tasks=json.dumps([{"description": "No choice", "status": "Done", "actual_time": "1h"}]))
        self.assertEqual(self._rows(self.emp_a, today(), "No choice")[0].repeat_next_day, 0)
        self.assertEqual(len(self._rows(self.emp_a, self._next_day(), "No choice")), 0)

    def test_checkout_clears_the_flag_on_a_task_that_is_not_done(self):
        task = self._check_in(self.user_a, "Not finished")["Not finished"]
        set_task_repeat(task["name"], 1)
        submit_eod_log(lunch_from="", lunch_to="", logout_time="18:00",
                       task_updates=json.dumps([{"name": task["name"], "status": "In Progress", "actual_time": "30m",
                                                 "remarks": "", "repeat_next_day": 1}]), adhoc_tasks="[]")
        self.assertEqual(self._rows(self.emp_a, today(), "Not finished")[0].repeat_next_day, 0)
        copies = self._rows(self.emp_a, self._next_day(), "Not finished")
        self.assertEqual((len(copies), copies[0].repeat_next_day), (1, 0), "carried like any unfinished task, not repeated")
