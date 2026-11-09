"""
ST Attendance Tracker — Daily Work Log / Task Entry QA Test Suite
Ported from the old Daily Task / Daily Task Log suite (test_daily_task_log.py)
onto the parent/child model. See specs/daily-work-log-refactor.md.
"""
import json
import frappe
from frappe.utils import today, add_days, now_datetime
from frappe.utils.data import getdate
from frappe.tests.utils import FrappeTestCase
from datetime import timedelta
from unittest.mock import patch

from st_attendance_tracker.api import (
    _to_hhmm, _to_ampm,
    submit_morning_log, submit_eod_log, add_adhoc_tasks, autosave_eod_progress,
    get_page_state, get_management_dashboard, _get_team_leader_emails,
    delete_carried_task, reset_morning_checkin,
    _ensure_recurring_tasks, _rollover_pending_tasks, _get_work_log, _get_next_working_date,
    save_recurring_task, delete_recurring_task,
    get_my_history, get_history_day_detail, get_employee_task_detail,
    get_task_attachments, delete_carried_project, update_half_day_session,
    upload_task_attachment,
    _send_checkin_notifications, _send_eod_notifications,
    _get_attendance_settings, clear_attendance_settings_cache,
    _get_team_members,
    request_late_checkout_approval, unlock_late_checkout, get_late_checkout_requests, _get_previous_working_date, _resolve_active_checkin_date,
    _get_unclosed_previous_day, _resolve_late_logout_time,
)
from st_attendance_tracker.time_utils import is_checkout_locked
from st_attendance_tracker import tasks as tasks_module
from st_attendance_tracker.www import daily_checkin as daily_checkin_page
from st_attendance_tracker.www import management_dashboard as management_dashboard_page


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_employee(suffix, dept_name, user_email, roles=None):
    company = frappe.db.get_single_value("Global Defaults", "default_company") or "_Test Company"

    if not frappe.db.exists("Department", dept_name):
        frappe.get_doc({
            "doctype": "Department",
            "department_name": "_QA Dept",
            "company": company,
        }).insert(ignore_permissions=True, ignore_if_duplicate=True)

    real_dept = frappe.db.get_value(
        "Department", {"department_name": "_QA Dept", "company": company}, "name"
    ) or dept_name

    if not frappe.db.exists("User", user_email):
        u = frappe.new_doc("User")
        u.email = user_email
        u.first_name = "Test"
        u.last_name = suffix
        u.send_welcome_email = 0
        try:
            u.insert(ignore_permissions=True, ignore_if_duplicate=True)
        except Exception:
            pass

    if roles:
        user_doc = frappe.get_doc("User", user_email)
        existing = [r.role for r in user_doc.roles]
        for role in roles:
            if role not in existing:
                user_doc.append("roles", {"role": role})
        user_doc.save(ignore_permissions=True)

    existing_emp = frappe.db.get_value("Employee", {"user_id": user_email}, "name")
    if existing_emp:
        return existing_emp

    frappe.db.sql("UPDATE `tabEmployee` SET user_id = NULL WHERE user_id = %s", (user_email,))
    frappe.db.commit()

    e = frappe.new_doc("Employee")
    e.first_name = "Test"
    e.last_name = suffix
    e.gender = "Male"
    e.date_of_birth = "1990-01-01"
    e.date_of_joining = "2020-01-01"
    e.department = real_dept
    e.user_id = user_email
    e.insert(ignore_permissions=True)
    return e.name


def _task_status(employee, date, description):
    work_log = _get_work_log(employee, date)
    if not work_log:
        return None
    row = next((r for r in work_log.tasks if r.description == description), None)
    return row.status if row else None


def _task_name(employee, date, description):
    work_log = _get_work_log(employee, date)
    if not work_log:
        return None
    row = next((r for r in work_log.tasks if r.description == description), None)
    return row.name if row else None


def _make_approved_half_day_leave(employee, date):
    """A submitted, approved half-day Leave Application for `date`, created
    bypassing HRMS's own validate() (leave balance/allocation etc.) — the
    code under test only ever checks these raw fields via frappe.db.exists,
    never re-validates the leave itself. Caller must delete the row with
    frappe.db.delete (it's force-submitted, so the normal delete flow
    refuses it as a submitted record)."""
    leave_type = frappe.db.get_value("Leave Type", {}, "name")
    leave = frappe.new_doc("Leave Application")
    leave.employee = employee
    leave.leave_type = leave_type
    leave.from_date = date
    leave.to_date = date
    leave.half_day = 1
    leave.half_day_date = date
    leave.status = "Approved"
    leave.flags.ignore_permissions = True
    leave.flags.ignore_validate = True
    leave.flags.ignore_mandatory = True
    leave.insert(ignore_permissions=True)
    frappe.db.set_value("Leave Application", leave.name, "docstatus", 1)
    return leave.name


class TestQACheckinFull(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")

        company = frappe.db.get_single_value("Global Defaults", "default_company") or "_Test Company"
        cls.dept = (
            frappe.db.get_value("Department", {"department_name": "_QA Dept", "company": company}, "name")
            or f"_QA Dept - {company}"
        )
        cls.emp_user = "qa_emp@test.example.com"
        cls.hr_user  = "qa_hr@test.example.com"

        cls.emp_name = _make_employee("QA001", cls.dept, cls.emp_user, ["Employee"])
        cls.hr_name  = _make_employee("QAHR",  cls.dept, cls.hr_user,  ["HR Manager"])
        # Since the permission hooks were removed, an Employee is kept to their own records by a
        # User Permission on their Employee (what HRMS creates for every employee user). Without
        # one the test user could edit anyone's log, which is not how a real site is set up.
        frappe.db.sql("DELETE FROM `tabUser Permission` WHERE user=%s AND allow='Employee'", (cls.emp_user,))
        frappe.get_doc({"doctype": "User Permission", "user": cls.emp_user, "allow": "Employee",
                        "for_value": cls.emp_name, "apply_to_all_doctypes": 1}).insert(ignore_permissions=True)
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        frappe.db.sql("DELETE FROM `tabTask Entry` WHERE parent IN "
                       "(SELECT name FROM `tabDaily Work Log` WHERE employee IN (%s,%s))",
                       (cls.emp_name, cls.hr_name))
        frappe.db.sql("DELETE FROM `tabDaily Work Log` WHERE employee IN (%s,%s)", (cls.emp_name, cls.hr_name))
        frappe.db.sql("DELETE FROM `tabEmployee Checkin` WHERE employee IN (%s,%s)", (cls.emp_name, cls.hr_name))
        frappe.db.sql("DELETE FROM `tabUser Permission` WHERE user IN (%s,%s)", (cls.emp_user, cls.hr_user))
        frappe.db.sql("DELETE FROM `tabEmployee` WHERE name IN (%s,%s)", (cls.emp_name, cls.hr_name))
        frappe.db.sql("DELETE FROM `tabUser` WHERE email IN (%s,%s)", (cls.emp_user, cls.hr_user))
        frappe.db.commit()

    def setUp(self):
        frappe.set_user("Administrator")
        # These flows are clock-independent; the 22:00 checkout cutoff has its
        # own tests in TestCheckoutCutoff.
        for target in ("st_attendance_tracker.api.is_checkout_locked",
                       "st_attendance_tracker.st_attendance_tracker.doctype.daily_work_log.daily_work_log.is_checkout_locked"):
            patcher = patch(target, return_value=False)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        frappe.set_user("Administrator")
        # Both QA employees: a test that logs for the HR employee must not leak into the next one.
        for emp in (self.emp_name, self.hr_name):
            frappe.db.sql("DELETE FROM `tabTask Entry` WHERE parent IN "
                           "(SELECT name FROM `tabDaily Work Log` WHERE employee=%s)", (emp,))
            frappe.db.sql("DELETE FROM `tabDaily Work Log` WHERE employee=%s", (emp,))
            frappe.db.sql("DELETE FROM `tabEmployee Checkin` WHERE employee=%s", (emp,))
        frappe.db.commit()

    # ── SECTION 1 — Functional Core ────────────────────────────────────────────

    def test_1_1_happy_path_checkin_creates_log(self):
        """TC-1.1: Valid check-in creates a Daily Work Log and Employee Checkin record."""
        frappe.set_user(self.emp_user)
        r = submit_morning_log(
            new_tasks=json.dumps([{"description": "QA happy path task", "estimated_time": "1h"}]),
            work_location="Office"
        )
        self.assertTrue(r.get("success"))
        work_log = _get_work_log(self.emp_name, today())
        self.assertIsNotNone(work_log, "Daily Work Log not created")
        self.assertTrue(work_log.morning_submitted)
        checkin = frappe.db.exists("Employee Checkin", {
            "employee": self.emp_name, "log_type": "IN", "device_id": "ST Daily Checkin"
        })
        self.assertIsNotNone(checkin, "Employee Checkin IN record not created")

    def test_1_2_double_checkin_blocked(self):
        """TC-1.2: Checking in twice on the same day is blocked."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "First check-in"}]),
            work_location="Office"
        )
        with self.assertRaises(frappe.ValidationError):
            submit_morning_log(
                new_tasks=json.dumps([{"description": "Second check-in"}]),
                work_location="Office"
            )

    def test_1_3_eod_without_checkin_blocked(self):
        """TC-1.3: EOD submission without morning check-in is blocked."""
        frappe.set_user(self.emp_user)
        with self.assertRaises(frappe.ValidationError):
            submit_eod_log(
                lunch_from="13:00", lunch_to="14:00",
                logout_time="18:00",
                task_updates="[]", adhoc_tasks="[]"
            )

    def test_1_4_full_checkin_checkout_cycle(self):
        """TC-1.4: Full check-in -> EOD cycle updates the same Daily Work Log and creates Employee Checkin OUT."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Morning task"}]),
            login_time="09:00",
            work_location="Office"
        )
        r = submit_eod_log(
            lunch_from="13:00", lunch_to="14:00",
            logout_time="18:00",
            task_updates="[]", adhoc_tasks="[]"
        )
        self.assertTrue(r.get("success"))
        work_log = _get_work_log(self.emp_name, today())
        self.assertTrue(work_log.eod_submitted)
        checkout = frappe.db.exists("Employee Checkin", {
            "employee": self.emp_name, "log_type": "OUT", "device_id": "ST Daily Checkin"
        })
        self.assertIsNotNone(checkout, "Employee Checkin OUT record not created")

    def test_1_5_double_eod_blocked(self):
        """TC-1.5: Submitting EOD twice on same day is blocked."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Task"}]),
            login_time="09:00",
            work_location="Office"
        )
        submit_eod_log(
            lunch_from="", lunch_to="", logout_time="18:00",
            task_updates="[]", adhoc_tasks="[]"
        )
        with self.assertRaises(frappe.ValidationError):
            submit_eod_log(
                lunch_from="", lunch_to="", logout_time="19:00",
                task_updates="[]", adhoc_tasks="[]"
            )

    def test_1_6_no_tasks_checkin_blocked(self):
        """TC-1.6: Check-in with zero tasks and no carried tasks is blocked."""
        frappe.set_user(self.emp_user)
        with self.assertRaises(frappe.ValidationError):
            submit_morning_log(new_tasks="[]", work_location="Office")

    def test_1_7_new_planned_task_estimated_time_parsed(self):
        """Regression: a brand-new planned task's free-text estimated_time
        ('2h 15m') must reach Daily Work Log's validate()/_prepare_tasks()
        as raw text and be parsed there exactly once. Pre-parsing it in the
        API layer first would hand _prepare_tasks() an already-numeric hour
        value, which parse_duration_to_hours treats as bare minutes and
        divides by 60."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Planned with estimate", "estimated_time": "2h 15m"}]),
            login_time="09:00",
            work_location="Office",
        )
        work_log = _get_work_log(self.emp_name, today())
        row = next((t for t in work_log.tasks if t.description == "Planned with estimate"), None)
        self.assertIsNotNone(row)
        self.assertAlmostEqual(row.estimated_time, 2.25)

    def test_1_7b_estimated_time_survives_a_later_unrelated_save(self):
        """Regression: found via manual browser verification, not caught by
        any existing automated test. _prepare_tasks() ran on EVERY save of
        the parent Daily Work Log and unconditionally re-parsed every row's
        estimated_time/actual_time — including rows the current save wasn't
        touching. A row loaded from the DB already holds a parsed float
        (e.g. 2.0), and parse_duration_to_hours treats a bare number as
        *minutes*, so a second save (e.g. the EOD submission, which saves
        the same parent doc again) silently divided an untouched task's
        already-correct estimated_time by 60 on every subsequent save."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Untouched at EOD", "estimated_time": "2h"}]),
            login_time="09:00",
            work_location="Office",
        )
        task_name = _task_name(self.emp_name, today(), "Untouched at EOD")
        # Leave it In Progress (not Done) with unrelated actual_time, so the
        # parent Daily Work Log gets saved again without this task's
        # estimated_time being part of the update payload at all.
        submit_eod_log(
            lunch_from="", lunch_to="", logout_time="18:00",
            task_updates=json.dumps([{"name": task_name, "status": "In Progress", "actual_time": "30m"}]),
            adhoc_tasks="[]",
        )
        work_log = _get_work_log(self.emp_name, today())
        row = next((t for t in work_log.tasks if t.description == "Untouched at EOD"), None)
        self.assertIsNotNone(row)
        self.assertAlmostEqual(row.estimated_time, 2.0,
            msg="estimated_time must survive a later save that doesn't touch it")

    def test_1_8_stale_task_reference_gives_friendly_error_and_logs(self):
        """Regression: if a task row the client is trying to update no
        longer exists on today's Daily Work Log (deleted/changed elsewhere
        between page load and submit — another tab/device, or a Team
        Leader/HR edit via Desk), the API must not claim a permission
        violation. It should ask the employee to refresh, and log
        diagnostics so a real occurrence is traceable instead of an
        untraceable one-off support report."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Task that vanishes"}]),
            login_time="09:00",
            work_location="Office",
        )
        task_name = _task_name(self.emp_name, today(), "Task that vanishes")
        # Simulate the row being removed/changed elsewhere between page load and submit.
        frappe.db.delete("Task Entry", {"name": task_name})
        frappe.db.commit()

        errors_before = frappe.db.count("Error Log")
        with self.assertRaises(frappe.ValidationError):
            submit_eod_log(
                lunch_from="", lunch_to="", logout_time="18:00",
                task_updates=json.dumps([{"name": task_name, "status": "Done", "actual_time": "1h"}]),
                adhoc_tasks="[]",
            )
        self.assertGreater(frappe.db.count("Error Log"), errors_before,
            "A stale task reference must be logged for diagnosis")

    def test_1_9_has_permission_uses_employee_field_not_owner(self):
        """Regression: has_permission_daily_work_log gates on the `employee`
        link field, not Frappe's if_owner (creation-time `owner`) — a
        record created on an employee's behalf by someone/something else
        (e.g. the task-assignment agent) must not lock the real employee
        out of reading/writing their own record."""
        frappe.set_user("Administrator")
        log = frappe.new_doc("Daily Work Log")
        log.employee = self.emp_name
        log.date = add_days(today(), -10)
        log.insert()  # owner = 'Administrator', employee = self.emp_name — deliberately mismatched
        try:
            frappe.set_user(self.emp_user)
            fetched = frappe.get_doc("Daily Work Log", log.name)
            self.assertTrue(fetched.has_permission("read"))
            fetched.work_location = "Office"
            fetched.save()  # must not raise — doc.employee matches this session's employee
        finally:
            frappe.set_user("Administrator")
            frappe.db.sql("DELETE FROM `tabDaily Work Log` WHERE name=%s", (log.name,))
            frappe.db.commit()

    def test_1_10_has_permission_still_blocks_cross_employee_access(self):
        """Regression: removing if_owner from the DocPerm row must not open
        up cross-employee access — has_permission_daily_work_log must still
        deny a plain Employee trying to load/save someone else's record."""
        frappe.set_user("Administrator")
        other_log = frappe.new_doc("Daily Work Log")
        other_log.employee = self.hr_name
        other_log.date = add_days(today(), -11)
        other_log.insert()
        try:
            frappe.set_user(self.emp_user)
            fetched = frappe.get_doc("Daily Work Log", other_log.name)
            self.assertFalse(fetched.has_permission("read"))
            with self.assertRaises(frappe.PermissionError):
                fetched.work_location = "Office"
                fetched.save()
        finally:
            frappe.set_user("Administrator")
            frappe.db.sql("DELETE FROM `tabDaily Work Log` WHERE name=%s", (other_log.name,))
            frappe.db.commit()

    # ── SECTION 2 — Edge Cases & Invalid Inputs (Daily Work Log controller) ────

    def test_2_1_reversed_lunch_blocked(self):
        """TC-2.1: Reversed lunch times (from 14:00 to 13:00) blocked by controller."""
        log = frappe.new_doc("Daily Work Log")
        log.employee = self.emp_name
        log.date = today()
        log.login_time = "09:00:00"
        log.logout_time = "18:00:00"
        log.lunch_from = "14:00:00"
        log.lunch_to = "13:00:00"
        with self.assertRaises(frappe.ValidationError):
            log.save(ignore_permissions=True)

    def test_2_2_lunch_exceeding_4h_allowed(self):
        """TC-2.2: Lunch break > 4 hours is allowed, net hours computed correctly."""
        log = frappe.new_doc("Daily Work Log")
        log.employee = self.emp_name
        log.date = today()
        log.login_time = "09:00:00"
        log.logout_time = "18:00:00"
        log.lunch_from = "10:00:00"
        log.lunch_to = "15:00:00"  # 5 hours
        log.eod_submitted = 1
        log.insert(ignore_permissions=True)
        self.assertEqual(log.net_hours, "4h 0m")
        log.delete()

    def test_2_3_lunch_outside_shift_blocked(self):
        """TC-2.3: Lunch interval outside work shift boundaries is blocked."""
        log = frappe.new_doc("Daily Work Log")
        log.employee = self.emp_name
        log.date = today()
        log.login_time = "09:00:00"
        log.logout_time = "17:00:00"
        log.lunch_from = "17:30:00"  # After logout
        log.lunch_to = "18:30:00"
        with self.assertRaises(frappe.ValidationError):
            log.save(ignore_permissions=True)

    def test_2_4_midnight_wrap_shift_net_hours(self):
        """TC-2.4: Night shift crossing midnight calculates correct net hours."""
        log = frappe.new_doc("Daily Work Log")
        log.employee = self.emp_name
        log.date = today()
        log.login_time = "22:00:00"
        log.logout_time = "06:00:00"  # Next day
        log.eod_submitted = 1
        log.insert(ignore_permissions=True)
        self.assertEqual(log.net_hours, "8h 0m")
        log.delete()

    def test_2_5_no_lunch_net_hours_correct(self):
        """TC-2.5: Net hours without lunch is correctly calculated."""
        log = frappe.new_doc("Daily Work Log")
        log.employee = self.emp_name
        log.date = today()
        log.login_time = "09:00:00"
        log.logout_time = "17:30:00"
        log.eod_submitted = 1
        log.insert(ignore_permissions=True)
        self.assertEqual(log.net_hours, "8h 30m")
        log.delete()

    def test_2_6_lunch_deducted_correctly(self):
        """TC-2.6: Standard lunch is correctly deducted from net hours."""
        log = frappe.new_doc("Daily Work Log")
        log.employee = self.emp_name
        log.date = today()
        log.login_time = "09:00:00"
        log.logout_time = "18:00:00"
        log.lunch_from = "13:00:00"
        log.lunch_to = "14:00:00"
        log.eod_submitted = 1
        log.insert(ignore_permissions=True)
        self.assertEqual(log.net_hours, "8h 0m")
        log.delete()

    def test_2_7_empty_logout_throws(self):
        """TC-2.7: EOD with empty logout_time is blocked."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Task"}]),
            work_location="Office"
        )
        with self.assertRaises(frappe.ValidationError):
            submit_eod_log(
                lunch_from="", lunch_to="", logout_time="",
                task_updates="[]", adhoc_tasks="[]"
            )

    def test_2_8_xss_in_task_description_stored_safely(self):
        """TC-2.8: XSS payload in task description is stored as plain text, not executed."""
        frappe.set_user(self.emp_user)
        xss = "<script>alert('xss')</script>"
        submit_morning_log(
            new_tasks=json.dumps([{"description": xss}]),
            work_location="Office"
        )
        work_log = _get_work_log(self.emp_name, today())
        self.assertIn("script", work_log.tasks[0].description)

    def test_2_9_sql_injection_in_task_description_safe(self):
        """TC-2.9: SQL-like input in task description does not cause errors."""
        frappe.set_user(self.emp_user)
        sql_input = "'; DROP TABLE `tabTask Entry`; --"
        r = submit_morning_log(
            new_tasks=json.dumps([{"description": sql_input}]),
            work_location="Office"
        )
        self.assertTrue(r.get("success"))
        work_log = _get_work_log(self.emp_name, today())
        self.assertEqual(work_log.tasks[0].description, sql_input)

    def test_2_10_long_task_description_handled(self):
        """TC-2.10: 500+ char task description is accepted without crash."""
        frappe.set_user(self.emp_user)
        long_desc = "A" * 600
        r = submit_morning_log(
            new_tasks=json.dumps([{"description": long_desc}]),
            work_location="Office"
        )
        self.assertTrue(r.get("success"))

    # ── SECTION 3 — Authentication & Authorization ─────────────────────────────

    def test_3_1_guest_has_no_employee_access(self):
        """TC-3.1: Non-employee user cannot call employee API methods (raises PermissionError)."""
        no_emp_email = "no_emp_test_user@test.example.com"
        if not frappe.db.exists("User", no_emp_email):
            u = frappe.new_doc("User")
            u.email = no_emp_email
            u.first_name = "No"
            u.last_name = "Emp"
            u.send_welcome_email = 0
            try:
                u.insert(ignore_permissions=True, ignore_if_duplicate=True)
            except Exception:
                pass

        frappe.set_user(no_emp_email)
        with self.assertRaises(frappe.PermissionError):
            submit_morning_log(new_tasks="[]", work_location="Office")

    def test_3_2_management_dashboard_blocks_employee(self):
        """TC-3.2: Regular employee cannot access management dashboard API."""
        frappe.set_user(self.emp_user)
        with self.assertRaises(frappe.PermissionError):
            get_management_dashboard(today())

    def test_3_4_bola_task_edit_blocked(self):
        """TC-3.4: Employee cannot read or edit another employee's log through the document API.

        Enforced by the User Permission on the employee's own Employee record (set up in
        setUpClass), which the Employee link on Daily Work Log is checked against.
        """
        frappe.set_user("Administrator")
        other_log = frappe.new_doc("Daily Work Log")
        other_log.employee = self.hr_name
        other_log.date = today()
        other_log.append("tasks", {"description": "HR task", "status": "Pending", "task_type": "Planned"})
        other_log.insert(ignore_permissions=True)
        name = other_log.name
        try:
            frappe.set_user(self.emp_user)
            # Load a fresh copy: the inserted object still carries Administrator's ignore_permissions flag.
            mine = frappe.get_doc("Daily Work Log", name)
            with self.assertRaises(frappe.PermissionError):
                mine.check_permission("read")
            mine.employee_name = "Tampered by emp"
            with self.assertRaises(frappe.PermissionError):
                mine.save()
            with self.assertRaises(frappe.PermissionError):
                frappe.delete_doc("Daily Work Log", name)
        finally:
            frappe.set_user("Administrator")
            frappe.db.sql("DELETE FROM `tabTask Entry` WHERE parent=%s", (name,))
            frappe.db.sql("DELETE FROM `tabDaily Work Log` WHERE name=%s", (name,))
            frappe.db.commit()

    def test_3_5_delete_carried_task_blocked_after_eod(self):
        """TC-3.5: Cannot delete a task after EOD has been submitted for that date."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Task to delete after EOD"}]),
            login_time="09:00",
            work_location="Office"
        )
        task_name = _task_name(self.emp_name, today(), "Task to delete after EOD")
        submit_eod_log(
            lunch_from="", lunch_to="", logout_time="18:00",
            task_updates=json.dumps([{"name": task_name, "status": "Done", "actual_time": "1h", "description": "Task to delete after EOD"}]),
            adhoc_tasks="[]"
        )
        with self.assertRaises(frappe.ValidationError):
            delete_carried_task(task_name)

    def test_3_6_delete_another_employee_task_blocked_bola(self):
        """TC-3.6: Employee cannot delete another employee's task via API."""
        frappe.set_user("Administrator")
        other_log = frappe.new_doc("Daily Work Log")
        other_log.employee = self.hr_name
        other_log.date = today()
        other_log.append("tasks", {"description": "HR task to delete", "status": "Pending", "task_type": "Planned"})
        other_log.insert(ignore_permissions=True)
        other_task_name = other_log.tasks[0].name

        frappe.set_user(self.emp_user)
        with self.assertRaises(frappe.PermissionError):
            delete_carried_task(other_task_name)

        frappe.set_user("Administrator")
        other_log.delete()

    def test_3_7_guest_cannot_delete_task(self):
        """TC-3.7: Guest user cannot call delete_carried_task."""
        frappe.set_user("Guest")
        with self.assertRaises(frappe.PermissionError):
            delete_carried_task("some_task_name")

    def test_3_8_save_task_blocked_after_eod(self):
        """TC-3.8: Employee cannot edit a task's parent Daily Work Log after EOD is submitted."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Task for EOD block"}]),
            login_time="09:00",
            work_location="Office"
        )
        task_name = _task_name(self.emp_name, today(), "Task for EOD block")
        submit_eod_log(
            lunch_from="12:00", lunch_to="13:00", logout_time="18:00",
            task_updates=json.dumps([{"name": task_name, "status": "Done", "actual_time": "1h", "description": "Task for EOD block"}]),
            adhoc_tasks="[]"
        )

        work_log = _get_work_log(self.emp_name, today())
        work_log.tasks[0].description = "Updated task description after EOD"
        with self.assertRaises(frappe.ValidationError):
            work_log.save()

    # ── SECTION 4 — Time / Calculation Edge Cases ──────────────────────────────

    def test_4_1_to_hhmm_timedelta(self):
        """TC-4.1: _to_hhmm handles timedelta from DB correctly."""
        td = timedelta(hours=9, minutes=30)
        self.assertEqual(_to_hhmm(td), "09:30")

    def test_4_2_to_hhmm_single_digit_hour_no_trailing_colon(self):
        """TC-4.2: _to_hhmm converts '9:30:00' without trailing colon."""
        self.assertEqual(_to_hhmm("9:30:00"), "09:30")

    def test_4_3_to_hhmm_ampm(self):
        """TC-4.3: _to_hhmm converts AM/PM format correctly."""
        self.assertEqual(_to_hhmm("05:15 pm"), "17:15")
        self.assertEqual(_to_hhmm("12:00 am"), "00:00")
        self.assertEqual(_to_hhmm("12:00 pm"), "12:00")

    def test_4_4_to_hhmm_none_returns_empty(self):
        """TC-4.4: _to_hhmm with None/empty returns empty string."""
        self.assertEqual(_to_hhmm(None), "")
        self.assertEqual(_to_hhmm(""), "")

    def test_4_5_to_ampm_midnight(self):
        """TC-4.5: _to_ampm converts midnight and noon correctly."""
        self.assertEqual(_to_ampm("00:00"), "12:00 AM")
        self.assertEqual(_to_ampm("12:00"), "12:00 PM")

    def test_4_6_net_hours_sanity_zero_diff_uses_shared_helper(self):
        """TC-4.6: Same login/logout time on a past date doesn't fabricate a 24h shift."""
        yesterday = frappe.utils.add_days(today(), -1)
        log = frappe.new_doc("Daily Work Log")
        log.employee = self.emp_name
        log.date = yesterday
        log.login_time = "06:00:00"
        log.logout_time = "06:00:00"
        log.eod_submitted = 1
        log.insert(ignore_permissions=True)
        # resolve_zero_diff_minutes governs this value; just assert it computed
        # something sane rather than blowing up or wrapping to 24h.
        self.assertIsNotNone(log.net_hours)
        log.delete()

    def test_4_10_midnight_wrap_exact_midnight_logout(self):
        """TC-4.10: Night shift wrapping midnight with logout exactly at 00:00:00 calculates correct net hours."""
        log = frappe.new_doc("Daily Work Log")
        log.employee = self.emp_name
        log.date = today()
        log.login_time = "18:00:00"
        log.logout_time = "00:00:00"
        log.eod_submitted = 1
        log.insert(ignore_permissions=True)
        self.assertEqual(log.net_hours, "6h 0m")
        log.delete()

    def test_4_12_reset_checkin_preserves_tasks(self):
        """TC-4.12: Resetting morning check-in preserves planned tasks and reverts status to Pending."""
        frappe.set_user(self.emp_user)

        submit_morning_log(
            new_tasks=json.dumps([{"description": "Test preservation task", "status": "In Progress"}]),
            login_time="09:00",
            work_location="Office"
        )

        work_log = _get_work_log(self.emp_name, today())
        self.assertEqual(len(work_log.tasks), 1)

        r = reset_morning_checkin()
        self.assertTrue(r.get("success"))

        work_log = _get_work_log(self.emp_name, today())
        self.assertEqual(len(work_log.tasks), 1)
        self.assertEqual(work_log.tasks[0].status, "Pending")
        self.assertFalse(work_log.morning_submitted)
        self.assertTrue(work_log.was_reset_today)

    # ── SECTION 5 — Data Integrity ─────────────────────────────────────────────

    def test_5_1_task_rollover_idempotent(self):
        """TC-5.1: Pending tasks rolled over only once (no duplicate on re-trigger)."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Rollover task"}]),
            login_time="09:00",
            work_location="Office"
        )
        submit_eod_log(
            lunch_from="", lunch_to="", logout_time="18:00",
            task_updates="[]", adhoc_tasks="[]"
        )
        next_date = str(_get_next_working_date(self.emp_name, today()))
        next_log = _get_work_log(self.emp_name, next_date)
        self.assertIsNotNone(next_log)
        matching = [t for t in next_log.tasks if t.description == "Rollover task"]
        self.assertEqual(len(matching), 1, "Task was duplicated on rollover")

        # re-triggering rollover for the same source day must not duplicate it
        _rollover_pending_tasks(self.emp_name, today())
        next_log.reload()
        matching = [t for t in next_log.tasks if t.description == "Rollover task"]
        self.assertEqual(len(matching), 1, "Task was duplicated on re-triggered rollover")

    def test_5_2_adhoc_task_created_on_eod(self):
        """TC-5.2: Ad-hoc tasks submitted during EOD are correctly created."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Planned task"}]),
            login_time="09:00",
            work_location="Office"
        )
        submit_eod_log(
            lunch_from="", lunch_to="", logout_time="18:00",
            task_updates="[]",
            adhoc_tasks=json.dumps([{"description": "Ad-hoc task done", "status": "Done", "actual_time": "1h"}])
        )
        work_log = _get_work_log(self.emp_name, today())
        adhoc = next((t for t in work_log.tasks if t.description == "Ad-hoc task done"), None)
        self.assertIsNotNone(adhoc)
        self.assertEqual(adhoc.task_type, "Ad-hoc")
        self.assertAlmostEqual(adhoc.actual_time, 1.0,
            msg="Ad-hoc actual_time must be parsed from '1h' into hours, not stored raw/zeroed")

    def test_5_2b_adhoc_duration_format_and_in_progress_status_parsed(self):
        """Regression: ad-hoc estimated_time/actual_time must be parsed from
        free-text duration ('1h 30m') into hours exactly once. The API layer
        used to pre-parse these into a float and then hand that float to
        Daily Work Log's own validate()/_prepare_tasks(), which parses every
        row's estimated_time/actual_time again on save — and
        parse_duration_to_hours treats an already-numeric value as bare
        minutes (its documented convention for unit-less input), dividing a
        correct hour value by 60. An In Progress task left unfinished at EOD
        is legitimately rolled over to tomorrow (status becomes 'Rolled
        Over') — this test only checks the parsed hours survive that."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Planned task"}]),
            login_time="09:00",
            work_location="Office"
        )
        submit_eod_log(
            lunch_from="", lunch_to="", logout_time="18:00",
            task_updates="[]",
            adhoc_tasks=json.dumps([{
                "description": "Ad-hoc task in progress",
                "status": "In Progress",
                "estimated_time": "2 hrs",
                "actual_time": "1h 30m",
            }])
        )
        work_log = _get_work_log(self.emp_name, today())
        adhoc = next((t for t in work_log.tasks if t.description == "Ad-hoc task in progress"), None)
        self.assertIsNotNone(adhoc)
        self.assertAlmostEqual(adhoc.actual_time, 1.5)
        self.assertAlmostEqual(adhoc.estimated_time, 2.0)

    def test_5_3_empty_adhoc_description_ignored(self):
        """TC-5.3: Ad-hoc task with empty description is not inserted."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Task"}]),
            login_time="09:00",
            work_location="Office"
        )
        before = len(_get_work_log(self.emp_name, today()).tasks)
        submit_eod_log(
            lunch_from="", lunch_to="", logout_time="18:00",
            task_updates="[]",
            adhoc_tasks=json.dumps([{"description": "   ", "status": "Done"}])
        )
        after = len(_get_work_log(self.emp_name, today()).tasks)
        self.assertEqual(before, after, "Empty adhoc description created a task")

    def test_5_4_net_hours_stored_in_db(self):
        """TC-5.4: Net hours are persisted on the Daily Work Log after EOD."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Task"}]),
            login_time="09:00",
            work_location="Office"
        )
        submit_eod_log(
            lunch_from="13:00", lunch_to="14:00",
            logout_time="18:00",
            task_updates="[]", adhoc_tasks="[]"
        )
        net_hours = frappe.db.get_value("Daily Work Log", {
            "employee": self.emp_name, "date": today(), "eod_submitted": 1,
        }, "net_hours")
        self.assertIsNotNone(net_hours)
        self.assertNotEqual(net_hours, "")
        self.assertIn("h", net_hours)

    def test_5_5_duplicate_work_log_controller_blocked(self):
        """TC-5.5: Direct duplicate insert of a Daily Work Log for the same employee+date is blocked."""
        log1 = frappe.new_doc("Daily Work Log")
        log1.employee = self.emp_name
        log1.date = today()
        log1.morning_submitted = 1
        log1.login_time = "09:00:00"
        log1.insert(ignore_permissions=True)

        log2 = frappe.new_doc("Daily Work Log")
        log2.employee = self.emp_name
        log2.date = today()
        log2.morning_submitted = 1
        log2.login_time = "10:00:00"
        with self.assertRaises(frappe.ValidationError):
            log2.insert(ignore_permissions=True)

        frappe.set_user("Administrator")
        log1.delete()

    # ── SECTION 5B — Autosave (draft) EOD progress ─────────────────────────────

    def test_5b_1_autosave_persists_without_flipping_eod_submitted(self):
        """Autosave writes status/actual_time/remarks to the DB but does not
        submit checkout — eod_submitted stays 0 and the row stays editable."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Autosave task"}]),
            login_time="09:00", work_location="Office",
        )
        task_name = _task_name(self.emp_name, today(), "Autosave task")

        r = autosave_eod_progress(
            task_updates=json.dumps([{
                "name": task_name, "status": "In Progress",
                "actual_time": "45m", "remarks": "still working on it",
            }]),
        )
        self.assertTrue(r.get("success"))
        self.assertIn("server_time", r)

        work_log = _get_work_log(self.emp_name, today())
        self.assertFalse(work_log.eod_submitted, "Autosave must not submit checkout")
        row = next(t for t in work_log.tasks if t.name == task_name)
        self.assertEqual(row.status, "In Progress")
        self.assertAlmostEqual(row.actual_time, 0.75)
        self.assertEqual(row.remarks, "still working on it")

    def test_5b_2_autosave_done_without_actual_time_downgrades_instead_of_throwing(self):
        """Draft path: flipping a task to Done before typing the time must not
        hard-fail a background autosave — it downgrades to In Progress."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Mid-edit task"}]),
            login_time="09:00", work_location="Office",
        )
        task_name = _task_name(self.emp_name, today(), "Mid-edit task")

        autosave_eod_progress(
            task_updates=json.dumps([{"name": task_name, "status": "Done", "actual_time": ""}]),
        )
        self.assertEqual(_task_status(self.emp_name, today(), "Mid-edit task"), "In Progress")

    def test_5b_3_final_submit_still_hard_requires_actual_time_for_done(self):
        """Regression: submit_eod_log's own path never sets in_eod_draft, so
        its existing hard-required "Done needs actual_time" guarantee at
        final checkout must be completely unaffected by the autosave flag."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Final submit task"}]),
            login_time="09:00", work_location="Office",
        )
        task_name = _task_name(self.emp_name, today(), "Final submit task")
        with self.assertRaises(frappe.ValidationError):
            submit_eod_log(
                lunch_from="", lunch_to="", logout_time="18:00",
                task_updates=json.dumps([{"name": task_name, "status": "Done", "actual_time": ""}]),
                adhoc_tasks="[]",
            )

    def test_5b_4_autosave_persists_lunch_fields_before_final_submit(self):
        """Lunch fields reach the DB via autosave, before Check out & submit."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Task"}]),
            login_time="09:00", work_location="Office",
        )
        autosave_eod_progress(lunch_from="13:15", lunch_to="14:00")

        work_log = _get_work_log(self.emp_name, today())
        self.assertFalse(work_log.eod_submitted)
        self.assertEqual(str(work_log.lunch_from)[:5], "13:15")
        self.assertEqual(str(work_log.lunch_to)[:5], "14:00")

    def test_5b_4b_lunch_prefilled_on_page_reload_before_final_submit(self):
        """Regression: get_context() used to only compute lunch_from/lunch_to
        display values when eod_submitted was already true, so a value saved
        by autosave_eod_progress() mid-day (before "Check out & submit") was
        never redisplayed on a refresh — the pre-checkout lunch inputs always
        rendered the hardcoded 14:00/15:00 unchecked defaults regardless of
        what was actually in the DB. Verifies the fix: the checkbox/time
        inputs are pre-filled from the Daily Work Log's real DB value even
        while eod_submitted is still 0."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Task"}]),
            login_time="09:00", work_location="Office",
        )
        autosave_eod_progress(lunch_from="13:15", lunch_to="14:00")

        context = frappe._dict()
        daily_checkin_page.get_context(context)
        self.assertFalse(context.get("eod_done"))
        self.assertTrue(context.get("include_lunch"),
            "Lunch checkbox must be pre-checked once a lunch time is on the DB record")
        self.assertEqual(context.get("lunch_from_input"), "13:15")
        self.assertEqual(context.get("lunch_to_input"), "14:00")

    def test_5b_4c_no_lunch_saved_yet_shows_unchecked(self):
        """The pre-fill fix must not force-check the lunch box before the
        employee has ever set a lunch time."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Task"}]),
            login_time="09:00", work_location="Office",
        )
        context = frappe._dict()
        daily_checkin_page.get_context(context)
        self.assertFalse(context.get("include_lunch"))
        self.assertEqual(context.get("lunch_from_input"), "")
        self.assertEqual(context.get("lunch_to_input"), "")

    def test_5b_5_autosave_blocked_after_eod_submitted(self):
        """Once checked out, autosave must refuse — nothing left to draft."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Task"}]),
            login_time="09:00", work_location="Office",
        )
        submit_eod_log(
            lunch_from="", lunch_to="", logout_time="18:00",
            task_updates="[]", adhoc_tasks="[]",
        )
        with self.assertRaises(frappe.ValidationError):
            autosave_eod_progress(task_updates="[]")

    def test_5b_6_autosave_blocked_before_morning_submitted(self):
        """Nothing to attach a draft to before check-in has happened."""
        frappe.set_user(self.emp_user)
        with self.assertRaises(frappe.ValidationError):
            autosave_eod_progress(task_updates="[]")

    def test_5b_7_autosave_cross_employee_task_rejected(self):
        """BOLA regression: an employee cannot autosave onto another
        employee's task row by passing its name. _resolve_task_row only
        looks up rows on the CALLER's own Daily Work Log, so the other
        employee's row is simply absent from it — surfaced as the same
        "stale row, please refresh" ValidationError as test_1_8, not a
        PermissionError (deliberate: see _resolve_task_row's docstring).
        Either way, nothing crosses over into the other employee's record."""
        frappe.set_user("Administrator")
        other_log = frappe.new_doc("Daily Work Log")
        other_log.employee = self.hr_name
        other_log.date = today()
        other_log.morning_submitted = 1
        other_log.append("tasks", {"description": "HR task", "status": "Pending", "task_type": "Planned"})
        other_log.insert(ignore_permissions=True)
        other_task_name = other_log.tasks[0].name

        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "My own task"}]),
            login_time="09:00", work_location="Office",
        )
        with self.assertRaises(frappe.ValidationError):
            autosave_eod_progress(
                task_updates=json.dumps([{"name": other_task_name, "status": "Done", "actual_time": "1h"}]),
            )

        frappe.set_user("Administrator")
        other_log.reload()
        self.assertEqual(other_log.tasks[0].status, "Pending",
            "The other employee's task must be untouched")
        other_log.delete()

    # ── SECTION 6 — Multi-Department Team Leader Notification ─────────────────
    # (unrelated to the schema change — kept as regression coverage)

    def test_6_1_team_leader_fallback_to_reports_to(self):
        """TC-6.1: No Employee Department Assignment rows -> falls back to reports_to."""
        tl_user = "qa_tl_fallback@test.example.com"
        tl_name = _make_employee("QATLFallback", self.dept, tl_user, ["Employee"])
        subj_user = "qa_subj_fallback@test.example.com"
        subj_name = _make_employee("QASubjFallback", self.dept, subj_user, ["Employee"])
        try:
            frappe.db.set_value("Employee", subj_name, "reports_to", tl_name)
            emails = _get_team_leader_emails(subj_name)
            self.assertEqual(emails, [tl_user])
        finally:
            frappe.db.sql("DELETE FROM `tabEmployee` WHERE name IN (%s,%s)", (tl_name, subj_name))
            frappe.db.sql("DELETE FROM `tabUser` WHERE email IN (%s,%s)", (tl_user, subj_user))
            frappe.db.commit()

    def test_6_2_team_leader_multiple_department_assignments(self):
        """TC-6.2: Department Assignment rows notify every listed Team Leader, ignoring reports_to."""
        tl1_user = "qa_tl1@test.example.com"
        tl1_name = _make_employee("QATL1", self.dept, tl1_user, ["Employee"])
        tl2_user = "qa_tl2@test.example.com"
        tl2_name = _make_employee("QATL2", self.dept, tl2_user, ["Employee"])
        subj_user = "qa_subj_multi@test.example.com"
        subj_name = _make_employee("QASubjMulti", self.dept, subj_user, ["Employee"])
        try:
            doc = frappe.get_doc("Employee", subj_name)
            doc.reports_to = tl1_name
            doc.append("department_assignments", {"department": self.dept, "team_leader": tl1_name})
            doc.append("department_assignments", {"department": self.dept, "team_leader": tl2_name})
            doc.save(ignore_permissions=True)

            emails = _get_team_leader_emails(subj_name)
            self.assertEqual(sorted(emails), sorted([tl1_user, tl2_user]))
        finally:
            frappe.db.sql(
                "DELETE FROM `tabEmployee Department Assignment` WHERE parent IN (%s,%s,%s)",
                (tl1_name, tl2_name, subj_name)
            )
            frappe.db.sql("DELETE FROM `tabEmployee` WHERE name IN (%s,%s,%s)", (tl1_name, tl2_name, subj_name))
            frappe.db.sql("DELETE FROM `tabUser` WHERE email IN (%s,%s,%s)", (tl1_user, tl2_user, subj_user))
            frappe.db.commit()

    # ── SECTION 7 — Carry-Forward Confirmation & Recurring Tasks ───────────────

    def test_7_1_carry_forward_declined_drops_task_no_rollover(self):
        """TC-7.1: Unchecking carry-forward marks the task Dropped and skips rollover."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Task to drop"}]),
            login_time="09:00",
            work_location="Office"
        )
        task_name = _task_name(self.emp_name, today(), "Task to drop")

        submit_eod_log(
            lunch_from="", lunch_to="", logout_time="18:00",
            task_updates=json.dumps([{
                "name": task_name, "status": "Pending", "actual_time": "", "carry_forward": False
            }]),
            adhoc_tasks="[]"
        )

        self.assertEqual(_task_status(self.emp_name, today(), "Task to drop"), "Dropped")

        next_date = str(_get_next_working_date(self.emp_name, today()))
        next_log = _get_work_log(self.emp_name, next_date)
        rolled = next_log and any(t.description == "Task to drop" for t in next_log.tasks)
        self.assertFalse(rolled, "Dropped task should not roll over")

    def test_7_2_recurring_task_auto_created_and_never_rolls_over(self):
        """TC-7.2: Recurring Task Template auto-creates a fresh Task Entry daily; never carried forward."""
        tpl = frappe.get_doc({
            "doctype": "Recurring Task Template",
            "employee": self.emp_name,
            "description": "Daily Scrum Standup",
            "is_active": 1,
        }).insert(ignore_permissions=True)
        try:
            _ensure_recurring_tasks(self.emp_name, today())
            work_log = _get_work_log(self.emp_name, today())
            scrum_today = next((t for t in work_log.tasks
                                 if t.task_type == "Recurring" and t.description == "Daily Scrum Standup"), None)
            self.assertIsNotNone(scrum_today, "Recurring task not auto-created")

            # Left Pending — EOD-style rollover must NOT carry a Recurring task forward
            _rollover_pending_tasks(self.emp_name, today())
            next_date = str(_get_next_working_date(self.emp_name, today()))
            next_log = _get_work_log(self.emp_name, next_date)
            rolled = next_log and any(t.series_id == scrum_today.series_id for t in next_log.tasks)
            self.assertFalse(rolled, "Recurring task should never roll over")

            # A fresh copy still appears tomorrow — from the template, not the carry chain
            _ensure_recurring_tasks(self.emp_name, next_date)
            next_log = _get_work_log(self.emp_name, next_date)
            scrum_tomorrow = next_log and any(
                t.task_type == "Recurring" and t.description == "Daily Scrum Standup" for t in next_log.tasks
            )
            self.assertTrue(scrum_tomorrow, "Recurring task did not auto-create for the next day")
        finally:
            frappe.db.sql("DELETE FROM `tabRecurring Task Template` WHERE name=%s", (tpl.name,))
            frappe.db.sql("DELETE FROM `tabTask Entry` WHERE parent IN "
                           "(SELECT name FROM `tabDaily Work Log` WHERE employee=%s)", (self.emp_name,))
            frappe.db.sql("DELETE FROM `tabDaily Work Log` WHERE employee=%s", (self.emp_name,))
            frappe.db.commit()

    def test_7_3_deleting_template_removes_todays_pending_instance(self):
        """TC-7.3: Deleting a Recurring Task Template immediately drops today's
        not-yet-started instance instead of leaving a stale copy behind."""
        frappe.set_user(self.emp_user)
        r = save_recurring_task(description="Stale Standup", is_active=1)
        work_log = _get_work_log(self.emp_name, today())
        self.assertIsNotNone(next((t for t in work_log.tasks if t.description == "Stale Standup"), None))

        delete_recurring_task(r["name"])

        work_log = _get_work_log(self.emp_name, today())
        still_there = work_log and any(t.description == "Stale Standup" for t in work_log.tasks)
        self.assertFalse(still_there, "Deleted template's Pending instance should be removed immediately")

    def test_7_4_deactivating_template_removes_todays_pending_instance(self):
        """TC-7.4: Deactivating (not deleting) a template also syncs today's instance away."""
        frappe.set_user(self.emp_user)
        r = save_recurring_task(description="Toggle Standup", is_active=1)
        save_recurring_task(name=r["name"], description="Toggle Standup", is_active=0)

        work_log = _get_work_log(self.emp_name, today())
        still_there = work_log and any(t.description == "Toggle Standup" for t in work_log.tasks)
        self.assertFalse(still_there, "Deactivated template's Pending instance should be removed immediately")

        frappe.db.sql("DELETE FROM `tabRecurring Task Template` WHERE name=%s", (r["name"],))
        frappe.db.commit()

    def test_7_5_editing_template_updates_todays_pending_instance(self):
        """TC-7.5: Editing a template's description syncs an untouched instance in place."""
        frappe.set_user(self.emp_user)
        r = save_recurring_task(description="Old Wording", is_active=1)
        save_recurring_task(name=r["name"], description="New Wording", is_active=1)

        work_log = _get_work_log(self.emp_name, today())
        self.assertFalse(any(t.description == "Old Wording" for t in work_log.tasks))
        self.assertTrue(any(t.description == "New Wording" for t in work_log.tasks))

        frappe.db.sql("DELETE FROM `tabRecurring Task Template` WHERE name=%s", (r["name"],))
        frappe.db.commit()

    def test_7_6_started_instance_not_touched_by_template_change(self):
        """TC-7.6: A recurring instance already marked In Progress/Done is left
        alone even if its template is edited or deleted afterward."""
        frappe.set_user(self.emp_user)
        r = save_recurring_task(description="In-Flight Standup", is_active=1)
        work_log = _get_work_log(self.emp_name, today())
        row = next(t for t in work_log.tasks if t.description == "In-Flight Standup")
        row.status = "In Progress"
        work_log.save(ignore_permissions=True)

        delete_recurring_task(r["name"])

        work_log = _get_work_log(self.emp_name, today())
        still_there = next((t for t in work_log.tasks if t.description == "In-Flight Standup"), None)
        self.assertIsNotNone(still_there, "An already-started instance must not be silently removed")
        self.assertEqual(still_there.status, "In Progress")

    # ── SECTION 8 — Secondary/read endpoints (history, dashboards, attachments,
    #    half-day session, project delete) rewritten for the new schema but not
    #    exercised by Sections 1-7's check-in/EOD flow ────────────────────────

    def test_8_1_get_my_history_lists_past_checked_in_days(self):
        """TC-8.1: A past day's Daily Work Log (checked in) appears in history."""
        frappe.set_user("Administrator")
        past_date = add_days(today(), -3)
        log = frappe.new_doc("Daily Work Log")
        log.employee = self.emp_name
        log.date = past_date
        log.morning_submitted = 1
        log.login_time = "09:00:00"
        log.append("tasks", {
            "description": "Past task", "status": "Done", "task_type": "Planned",
            "actual_time": 1, "series_id": "hist1", "origin_date": past_date,
        })
        log.insert(ignore_permissions=True)
        frappe.db.commit()

        frappe.set_user(self.emp_user)
        result = get_my_history(0)
        entry = next((l for l in result["logs"] if str(l["date"]) == str(past_date)), None)
        self.assertIsNotNone(entry, "Past checked-in day missing from history")
        self.assertEqual(entry["total_tasks"], 1)
        self.assertEqual(entry["done_tasks"], 1)

    def test_8_2_get_history_day_detail_returns_tasks_and_logs(self):
        """TC-8.2: Day-detail drill-down returns the day's tasks and log fields."""
        frappe.set_user("Administrator")
        past_date = add_days(today(), -4)
        log = frappe.new_doc("Daily Work Log")
        log.employee = self.emp_name
        log.date = past_date
        log.morning_submitted = 1
        log.eod_submitted = 1
        log.login_time = "09:00:00"
        log.logout_time = "18:00:00"
        log.append("tasks", {
            "description": "Detail task", "status": "Done", "task_type": "Planned",
            "actual_time": 2, "series_id": "hist2", "origin_date": past_date,
        })
        log.insert(ignore_permissions=True)
        frappe.db.commit()

        frappe.set_user(self.emp_user)
        detail = get_history_day_detail(str(past_date))
        self.assertEqual(len(detail["tasks"]), 1)
        self.assertEqual(detail["tasks"][0]["description"], "Detail task")
        self.assertIsNotNone(detail["morning_log"])
        self.assertIsNotNone(detail["eod_log"])

    def test_8_3_get_employee_task_detail_hr_allowed_stranger_denied(self):
        """TC-8.3: HR Manager can view any employee's task detail; an unrelated
        employee (not HR, not their Team Leader) cannot."""
        frappe.set_user(self.hr_user)
        detail = get_employee_task_detail(self.emp_name, today())
        self.assertEqual(detail["employee"]["name"], self.emp_name)

        frappe.set_user(self.emp_user)
        with self.assertRaises(frappe.PermissionError):
            get_employee_task_detail(self.hr_name, today())

    def test_8_4_get_task_attachments_owner_allowed_stranger_denied(self):
        """TC-8.4: Task attachments are visible to the owner, blocked for a stranger."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Attachment task"}]),
            work_location="Office"
        )
        task_name = _task_name(self.emp_name, today(), "Attachment task")

        atts = get_task_attachments(task_name)
        self.assertEqual(atts, [])

        frappe.set_user("Administrator")
        other_log = frappe.new_doc("Daily Work Log")
        other_log.employee = self.hr_name
        other_log.date = today()
        other_log.append("tasks", {"description": "HR-only task", "status": "Pending", "task_type": "Planned"})
        other_log.insert(ignore_permissions=True)
        other_task_name = other_log.tasks[0].name

        frappe.set_user(self.emp_user)
        with self.assertRaises(frappe.PermissionError):
            get_task_attachments(other_task_name)

        frappe.set_user("Administrator")
        other_log.delete()

    def test_8_9_upload_task_attachment_stranger_denied(self):
        """TC-8.9: upload_task_attachment blocks attaching to another
        employee's task before ever touching the uploaded file — regression
        for the 403 found in browser QA (Frappe's generic upload_file can't
        correctly permission-check a child-table doctype loaded standalone;
        see upload_task_attachment's docstring)."""
        frappe.set_user("Administrator")
        other_log = frappe.new_doc("Daily Work Log")
        other_log.employee = self.hr_name
        other_log.date = today()
        other_log.append("tasks", {"description": "HR-only task 2", "status": "Pending", "task_type": "Planned"})
        other_log.insert(ignore_permissions=True)
        other_task_name = other_log.tasks[0].name

        frappe.set_user(self.emp_user)
        with self.assertRaises(frappe.PermissionError):
            upload_task_attachment(other_task_name)

        frappe.set_user("Administrator")
        other_log.delete()

    def test_8_10_upload_task_attachment_owner_succeeds(self):
        """TC-8.10: The task's own employee can attach a file to it."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Upload task"}]),
            work_location="Office"
        )
        task_name = _task_name(self.emp_name, today(), "Upload task")

        class _FakeStream:
            def read(self):
                return b"hello world"

        class _FakeFile:
            filename = "note.txt"
            stream = _FakeStream()

        class _FakeFiles:
            def get(self, key):
                return _FakeFile() if key == "file" else None

        class _FakeRequest:
            files = _FakeFiles()
            host = "127.0.0.1:8000"
            headers = {}

        frappe.local.request = _FakeRequest()
        try:
            file_doc = upload_task_attachment(task_name)
            self.assertEqual(file_doc.attached_to_doctype, "Task Entry")
            self.assertEqual(file_doc.attached_to_name, task_name)
            self.assertTrue(file_doc.file_name.startswith("note") and file_doc.file_name.endswith(".txt"))
        finally:
            frappe.local.request = None
            for fname in frappe.get_all("File",
                    filters={"attached_to_doctype": "Task Entry", "attached_to_name": task_name}, pluck="name"):
                frappe.delete_doc("File", fname, ignore_permissions=True)
            frappe.db.commit()

    def test_8_11_reparent_attachments_blocks_hijacking_anothers_file(self):
        """Regression: _reparent_attachments used to reparent ANY existing
        File onto a new task purely because the docname existed — no check
        that it was actually an unattached upload owned by the caller. An
        employee could pass another employee's File docname and silently
        steal that attachment onto their own task."""
        frappe.set_user(self.emp_user)
        orphan = frappe.get_doc({
            "doctype": "File",
            "file_name": "victim_secret.txt",
            "is_private": 1,
            "content": b"belongs to emp_user",
        }).insert()
        try:
            frappe.set_user(self.hr_user)
            submit_morning_log(
                new_tasks=json.dumps([{"description": "HR planned task"}]),
                login_time="09:00",
                work_location="Office",
            )
            submit_eod_log(
                lunch_from="", lunch_to="", logout_time="18:00",
                task_updates="[]",
                adhoc_tasks=json.dumps([{
                    "description": "HR ad-hoc task",
                    "status": "Done",
                    "actual_time": "1h",
                    "attachment_names": [orphan.name],
                }]),
            )
            orphan.reload()
            self.assertFalse(orphan.attached_to_doctype,
                "Another employee's orphan File must not be hijacked onto my task")
        finally:
            frappe.set_user("Administrator")
            frappe.delete_doc("File", orphan.name, ignore_permissions=True, force=True)
            frappe.db.sql("DELETE FROM `tabTask Entry` WHERE parent IN "
                           "(SELECT name FROM `tabDaily Work Log` WHERE employee=%s)", (self.hr_name,))
            frappe.db.sql("DELETE FROM `tabDaily Work Log` WHERE employee=%s", (self.hr_name,))
            frappe.db.commit()

    def test_8_12_reparent_attachments_own_orphan_file_succeeds(self):
        """The legitimate case must keep working: an employee's own
        just-uploaded orphan File gets attached to their own new task."""
        frappe.set_user(self.emp_user)
        orphan = frappe.get_doc({
            "doctype": "File",
            "file_name": "my_note.txt",
            "is_private": 1,
            "content": b"my own file",
        }).insert()
        try:
            submit_morning_log(
                new_tasks=json.dumps([{"description": "Planned task"}]),
                login_time="09:00",
                work_location="Office",
            )
            submit_eod_log(
                lunch_from="", lunch_to="", logout_time="18:00",
                task_updates="[]",
                adhoc_tasks=json.dumps([{
                    "description": "My ad-hoc task",
                    "status": "Done",
                    "actual_time": "1h",
                    "attachment_names": [orphan.name],
                }]),
            )
            orphan.reload()
            self.assertEqual(orphan.attached_to_doctype, "Task Entry")
        finally:
            frappe.delete_doc("File", orphan.name, ignore_permissions=True, force=True)

    def test_8_5_delete_carried_project_removes_all_its_tasks(self):
        """TC-8.5: Deleting a project removes every task under it, and is
        blocked once checkout has been submitted."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([
                {"description": "Proj task 1", "project_name": "QA Project"},
                {"description": "Proj task 2", "project_name": "QA Project"},
                {"description": "Other task"},
            ]),
            work_location="Office"
        )
        delete_carried_project("QA Project", today())

        work_log = _get_work_log(self.emp_name, today())
        remaining = [t.description for t in work_log.tasks]
        self.assertNotIn("Proj task 1", remaining)
        self.assertNotIn("Proj task 2", remaining)
        self.assertIn("Other task", remaining)

        submit_eod_log(
            lunch_from="", lunch_to="", logout_time="18:00",
            task_updates="[]", adhoc_tasks="[]"
        )
        with self.assertRaises(frappe.ValidationError):
            delete_carried_project("General", today())

    def test_8_6_update_half_day_session_without_leave_blocked(self):
        """TC-8.6: Applying a half-day session without an approved half-day leave is blocked."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Task"}]),
            work_location="Office"
        )
        with self.assertRaises(frappe.ValidationError):
            update_half_day_session("First Half")

    def test_8_7_update_half_day_session_with_approved_leave_succeeds(self):
        """TC-8.7: With an approved half-day leave on file, the session is recorded."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Task"}]),
            work_location="Office"
        )
        leave_name = _make_approved_half_day_leave(self.emp_name, today())
        try:
            r = update_half_day_session("Second Half")
            self.assertTrue(r["success"])
            self.assertEqual(r["half_day_session"], "Second Half")
            work_log = _get_work_log(self.emp_name, today())
            self.assertEqual(work_log.half_day_session, "Second Half")
        finally:
            frappe.set_user("Administrator")
            frappe.db.delete("Leave Application", {"name": leave_name})
            frappe.db.commit()

    def test_8_8_management_dashboard_returns_departments_and_summary(self):
        """TC-8.8: HR Manager's management dashboard returns per-department
        data and an org-wide summary."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Dashboard task"}]),
            login_time="09:00",
            work_location="Office"
        )

        frappe.set_user(self.hr_user)
        result = get_management_dashboard(today())
        self.assertIn("departments", result)
        self.assertIn("summary", result)
        self.assertGreaterEqual(result["summary"]["total"], 1)
        self.assertGreaterEqual(result["summary"]["checked_in"], 1)

    # ── SECTION 9 — Notification jobs ──────────────────────────────────────────
    # submit_morning_log/submit_eod_log now hand these to frappe.enqueue()
    # instead of calling them inline (large checkout attachments were making
    # base64-encoding two Email Queue records a multi-second part of the
    # synchronous checkout request). bench run-tests has no worker draining
    # the queue, so without these the email-building code would silently
    # stop being covered by every check-in/EOD test above.

    def test_9_1_checkin_notification_job_runs_without_error(self):
        """TC-9.1: The job submit_morning_log enqueues builds and queues
        both emails without error, called directly since no worker runs
        during tests."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Notify task"}]),
            login_time="09:00",
            work_location="Office"
        )
        _send_checkin_notifications(self.emp_name, today(), now_datetime())

    def test_9_2_eod_notification_job_runs_with_attachment(self):
        """TC-9.2: The job submit_eod_log enqueues builds and queues both
        emails, including a real task attachment, without error."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Notify task with file"}]),
            login_time="09:00",
            work_location="Office"
        )
        task_name = _task_name(self.emp_name, today(), "Notify task with file")

        class _FakeStream:
            def read(self):
                return b"attachment content"

        class _FakeFile:
            # Plain .txt on purpose — Frappe validates real PDF/office-doc
            # content (e.g. scanning PDFs for embedded JS) on insert, which
            # this fake byte content would fail; the point of this test is
            # the attachment-fetch path in _send_eod_notifications, not
            # Frappe's own file-content validation.
            filename = "report.txt"
            stream = _FakeStream()

        class _FakeFiles:
            def get(self, key):
                return _FakeFile() if key == "file" else None

        class _FakeRequest:
            files = _FakeFiles()
            host = "127.0.0.1:8000"
            headers = {}

        frappe.local.request = _FakeRequest()
        try:
            upload_task_attachment(task_name)
        finally:
            frappe.local.request = None

        submit_eod_log(
            lunch_from="", lunch_to="", logout_time="18:00",
            task_updates=json.dumps([{"name": task_name, "status": "Done", "actual_time": "1h"}]),
            adhoc_tasks="[]"
        )
        _send_eod_notifications(self.emp_name, today(), now_datetime(), is_late_checkout=False)

        for fname in frappe.get_all("File",
                filters={"attached_to_doctype": "Task Entry", "attached_to_name": task_name}, pluck="name"):
            frappe.delete_doc("File", fname, ignore_permissions=True)
        frappe.db.commit()

    # ── SECTION 10 — ST Attendance Settings cache ──────────────────────────────
    # Same pattern as _get_team_members()'s Redis cache: read on nearly every
    # check-in/checkout/dashboard load, changes only a handful of times a
    # year, invalidated explicitly on save rather than relying on the 3600s
    # TTL to catch up. ST Attendance Settings is a real Single shared with the
    # whole site, so every test here restores the original value afterward.

    def test_10_1_attendance_settings_cache_returns_correct_values(self):
        """TC-10.1: _get_attendance_settings reflects the real singleton
        value on a cache miss."""
        original = frappe.db.get_single_value("ST Attendance Settings", "standard_workday_hours")
        settings = frappe.get_single("ST Attendance Settings")
        settings.standard_workday_hours = 7.5
        settings.save(ignore_permissions=True)
        try:
            clear_attendance_settings_cache()
            result = _get_attendance_settings()
            self.assertEqual(result.get("standard_workday_hours"), 7.5)
        finally:
            settings.reload()
            settings.standard_workday_hours = original
            settings.save(ignore_permissions=True)
            clear_attendance_settings_cache()

    def test_10_2_attendance_settings_cache_hit_skips_db(self):
        """TC-10.2: A second call within the cache window doesn't re-fetch
        the singleton — proven by making frappe.get_single fail if called."""
        clear_attendance_settings_cache()
        _get_attendance_settings()  # warm the cache

        original_get_single = frappe.get_single

        def _fail_if_called(*args, **kwargs):
            self.fail("frappe.get_single was called on what should have been a cache hit")

        frappe.get_single = _fail_if_called
        try:
            result = _get_attendance_settings()
            self.assertIsNotNone(result)
        finally:
            frappe.get_single = original_get_single
            clear_attendance_settings_cache()

    def test_10_3_saving_settings_invalidates_cache(self):
        """TC-10.3: Saving ST Attendance Settings clears the cache immediately
        — the very next read reflects the new value, not a stale cached one."""
        settings = frappe.get_single("ST Attendance Settings")
        original = settings.standard_workday_hours
        try:
            clear_attendance_settings_cache()
            _get_attendance_settings()  # warm the cache with the original value

            settings.standard_workday_hours = 6.25
            settings.save(ignore_permissions=True)  # on_update hook should clear the cache

            result = _get_attendance_settings()
            self.assertEqual(result.get("standard_workday_hours"), 6.25)
        finally:
            settings.reload()
            settings.standard_workday_hours = original
            settings.save(ignore_permissions=True)
            clear_attendance_settings_cache()

    def test_10_4_get_team_members_reflects_changes_immediately(self):
        """TC-10.4: _get_team_members is read live (the old Redis cache was removed in the
        phase 1 clean-up), so a changed reports_to shows up on the very next call."""
        before = _get_team_members(self.emp_name)
        frappe.db.set_value("Employee", self.hr_name, "reports_to", self.emp_name)
        try:
            self.assertEqual(sorted(set(_get_team_members(self.emp_name)) - set(before)), [self.hr_name])
        finally:
            frappe.db.set_value("Employee", self.hr_name, "reports_to", None)
            frappe.db.commit()
        self.assertEqual(sorted(_get_team_members(self.emp_name)), sorted(before))

    # ── SECTION 11 — Mid-day ad-hoc task save (add_adhoc_tasks) ────────────────
    # A single "Save" button in the /daily-checkin footer persists every
    # not-yet-saved ad-hoc task/project in one batch, instead of waiting for
    # checkout — so a Team Leader can see them in real time on
    # /team-dashboard via publish_realtime. One button for the whole page,
    # not one per row.

    def test_11_1_add_adhoc_tasks_persists_immediately(self):
        """Mid-day ad-hoc tasks are written to the DB as soon as they're
        saved, without waiting for checkout."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Planned task"}]),
            login_time="09:00",
            work_location="Office",
        )
        r = add_adhoc_tasks(json.dumps([{"client_id": "ad1", "description": "Mid-day ad-hoc task", "estimated_time": "30m"}]))
        self.assertTrue(r.get("success"))
        self.assertEqual(len(r.get("created")), 1)
        self.assertEqual(r["created"][0]["client_id"], "ad1")

        work_log = _get_work_log(self.emp_name, today())
        row = next((t for t in work_log.tasks if t.description == "Mid-day ad-hoc task"), None)
        self.assertIsNotNone(row, "Ad-hoc task was not persisted immediately")
        self.assertEqual(row.task_type, "Ad-hoc")
        self.assertEqual(row.status, "Pending")
        self.assertEqual(row.series_id, r["created"][0]["series_id"])
        self.assertAlmostEqual(row.estimated_time, 0.5)

    def test_11_1b_add_adhoc_tasks_saves_a_whole_batch_in_one_call(self):
        """Several tasks added since the last Save all persist from one
        call — the point of a single footer button instead of per-row ones."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Planned task"}]),
            login_time="09:00",
            work_location="Office",
        )
        batch = [{"client_id": f"ad{i}", "description": f"Batch task {i}"} for i in range(5)]
        r = add_adhoc_tasks(json.dumps(batch))
        self.assertEqual(len(r["created"]), 5)

        work_log = _get_work_log(self.emp_name, today())
        descriptions = {t.description for t in work_log.tasks}
        for i in range(5):
            self.assertIn(f"Batch task {i}", descriptions)

    def test_11_2_add_adhoc_tasks_requires_checkin(self):
        """Cannot add mid-day tasks before checking in."""
        frappe.set_user(self.emp_user)
        with self.assertRaises(frappe.ValidationError):
            add_adhoc_tasks(json.dumps([{"client_id": "ad1", "description": "Too early"}]))

    def test_11_3_add_adhoc_tasks_blocked_after_eod(self):
        """Cannot add mid-day tasks after checkout."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Planned task"}]),
            login_time="09:00",
            work_location="Office",
        )
        submit_eod_log(
            lunch_from="", lunch_to="", logout_time="18:00",
            task_updates="[]", adhoc_tasks="[]",
        )
        with self.assertRaises(frappe.ValidationError):
            add_adhoc_tasks(json.dumps([{"client_id": "ad1", "description": "Too late"}]))

    def test_11_4_add_adhoc_tasks_skips_blank_descriptions(self):
        """A blank description in the batch is skipped, not persisted —
        and doesn't block the rest of the batch from saving."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Planned task"}]),
            login_time="09:00",
            work_location="Office",
        )
        r = add_adhoc_tasks(json.dumps([
            {"client_id": "blank", "description": "   "},
            {"client_id": "real", "description": "Real task"},
        ]))
        self.assertEqual(len(r["created"]), 1)
        self.assertEqual(r["created"][0]["client_id"], "real")

    def test_11_5_eod_updates_pre_saved_adhoc_task_no_duplicate(self):
        """A task saved mid-day via add_adhoc_tasks must be updated in place
        by submit_eod_log (matched by series_id), not duplicated."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Planned task"}]),
            login_time="09:00",
            work_location="Office",
        )
        saved = add_adhoc_tasks(json.dumps([{"client_id": "ad1", "description": "Pre-saved ad-hoc task"}]))
        series_id = saved["created"][0]["series_id"]

        submit_eod_log(
            lunch_from="", lunch_to="", logout_time="18:00",
            task_updates="[]",
            adhoc_tasks=json.dumps([{
                "description": "Pre-saved ad-hoc task",
                "status": "Done",
                "actual_time": "45m",
                "series_id": series_id,
            }]),
        )
        work_log = _get_work_log(self.emp_name, today())
        matching = [t for t in work_log.tasks if t.description == "Pre-saved ad-hoc task"]
        self.assertEqual(len(matching), 1, "Pre-saved ad-hoc task was duplicated at checkout")
        self.assertEqual(matching[0].series_id, series_id)
        self.assertEqual(matching[0].status, "Done")
        self.assertAlmostEqual(matching[0].actual_time, 0.75)

    def test_11_6_eod_still_appends_adhoc_tasks_without_series_id(self):
        """An ad-hoc task typed at checkout without ever clicking Save (no
        series_id) is still appended as a new row, same as before."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Planned task"}]),
            login_time="09:00",
            work_location="Office",
        )
        submit_eod_log(
            lunch_from="", lunch_to="", logout_time="18:00",
            task_updates="[]",
            adhoc_tasks=json.dumps([{"description": "Never pre-saved", "status": "Done", "actual_time": "1h"}]),
        )
        work_log = _get_work_log(self.emp_name, today())
        matching = [t for t in work_log.tasks if t.description == "Never pre-saved"]
        self.assertEqual(len(matching), 1)

    def test_11_7_add_adhoc_tasks_notifies_team_leader_via_realtime(self):
        """The employee's Team Leader is notified in real time when a batch
        of mid-day ad-hoc tasks is saved, once per Save click (not once per
        task)."""
        tl_user = "qa_tl_realtime@test.example.com"
        tl_name = _make_employee("QATLRealtime", self.dept, tl_user, ["Employee"])
        try:
            frappe.db.set_value("Employee", self.emp_name, "reports_to", tl_name)

            # Only capture our own "st_task_added" event — Document.save()
            # triggers its own internal publish_realtime calls (list/doc
            # update notifications) that are irrelevant here.
            published = []
            original_publish_realtime = frappe.publish_realtime
            def fake_publish_realtime(event, *args, **kwargs):
                if event == "st_task_added":
                    published.append((event, args[0] if args else kwargs.get("message"), kwargs.get("user")))
                return original_publish_realtime(event, *args, **kwargs)

            frappe.set_user(self.emp_user)
            submit_morning_log(
                new_tasks=json.dumps([{"description": "Planned task"}]),
                login_time="09:00",
                work_location="Office",
            )
            with patch("frappe.publish_realtime", side_effect=fake_publish_realtime):
                add_adhoc_tasks(json.dumps([
                    {"client_id": "ad1", "description": "Notify my team leader"},
                    {"client_id": "ad2", "description": "Second task"},
                ]))

            self.assertEqual(len(published), 1, "Expected one realtime event per Save click, not one per task")
            event, message, user = published[0]
            self.assertEqual(event, "st_task_added")
            self.assertEqual(user, tl_user)
            self.assertEqual(message["count"], 2)
            self.assertIn("Notify my team leader", message["descriptions"])
        finally:
            frappe.set_user("Administrator")
            frappe.db.set_value("Employee", self.emp_name, "reports_to", None)
            frappe.db.sql("DELETE FROM `tabEmployee` WHERE name=%s", (tl_name,))
            frappe.db.sql("DELETE FROM `tabUser` WHERE email=%s", (tl_user,))
            frappe.db.commit()

    def test_11_8_delete_carried_task_removes_a_saved_adhoc_task(self):
        """Deleting an ad-hoc task that was already persisted via
        add_adhoc_tasks (the footer Save button) must remove it from the
        database, not just the page — otherwise it silently survives and
        still shows up for the Team Leader / at checkout."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Planned task"}]),
            login_time="09:00",
            work_location="Office",
        )
        r = add_adhoc_tasks(json.dumps([{"client_id": "ad1", "description": "Saved then deleted"}]))
        task_name = r["created"][0]["task_name"]

        delete_carried_task(task_name)

        work_log = _get_work_log(self.emp_name, today())
        self.assertIsNone(next((t for t in work_log.tasks if t.name == task_name), None),
            "Saved ad-hoc task was not actually removed from the Daily Work Log")

    def test_11_9_delete_carried_project_removes_saved_adhoc_project_tasks(self):
        """Deleting a whole project removes every ad-hoc task saved under
        that project name, not just what's visible on the page."""
        frappe.set_user(self.emp_user)
        submit_morning_log(
            new_tasks=json.dumps([{"description": "Planned task"}]),
            login_time="09:00",
            work_location="Office",
        )
        add_adhoc_tasks(json.dumps([
            {"client_id": "ad1", "description": "Project task 1", "project_name": "QA Ad-hoc Project"},
            {"client_id": "ad2", "description": "Project task 2", "project_name": "QA Ad-hoc Project"},
            {"client_id": "ad3", "description": "Unrelated standalone task"},
        ]))

        delete_carried_project("QA Ad-hoc Project", today())

        work_log = _get_work_log(self.emp_name, today())
        remaining_descriptions = {t.description for t in work_log.tasks}
        self.assertNotIn("Project task 1", remaining_descriptions)
        self.assertNotIn("Project task 2", remaining_descriptions)
        self.assertIn("Unrelated standalone task", remaining_descriptions,
            "Deleting a project must not touch standalone tasks (both have project_name == '')")


# ── SECTION 12 — Scheduler reminders read Daily Work Log, not the retired
#    Daily Task Log doctype ─────────────────────────────────────────────────

class TestCheckoutReminderReadsDailyWorkLog(FrappeTestCase):
    """Regression: send_employee_checkin_reminder/send_employee_checkout_reminder
    must key off Daily Work Log (morning_submitted/eod_submitted) — the doctype
    that actually gets written on check-in/checkout. They used to read the
    retired Daily Task Log doctype, which stopped receiving writes once the
    app moved to Daily Work Log, so an employee who genuinely checked out
    could still be flagged 'pending' (or, once no Daily Task Log rows exist at
    all, the job would go silent for everyone instead)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        company = frappe.db.get_single_value("Global Defaults", "default_company") or "_Test Company"
        cls.dept = (
            frappe.db.get_value("Department", {"department_name": "_QA Dept", "company": company}, "name")
            or f"_QA Dept - {company}"
        )
        cls.done_user = "qa_sched_done@test.example.com"
        cls.pending_user = "qa_sched_pending@test.example.com"
        # Check-in/checkout below use the real clock; pin the site's checkout cutoff so the
        # lock does not depend on the time of day or the site setting.
        cls._cutoff_patch = patch("st_attendance_tracker.time_utils.get_checkout_cutoff", return_value=24 * 60 - 1)
        cls._cutoff_patch.start()
        cls.addClassCleanup(cls._cutoff_patch.stop)
        # An earlier aborted run leaves committed rows behind (tearDownClass never ran).
        for stale in frappe.get_all("Employee", filters={"user_id": ["in", [cls.done_user, cls.pending_user]]}, pluck="name"):
            frappe.db.sql("DELETE FROM `tabTask Entry` WHERE parent IN "
                          "(SELECT name FROM `tabDaily Work Log` WHERE employee=%s)", (stale,))
            frappe.db.sql("DELETE FROM `tabDaily Work Log` WHERE employee=%s", (stale,))
            frappe.db.sql("DELETE FROM `tabEmployee Checkin` WHERE employee=%s", (stale,))
        cls.done_name = _make_employee("QASchedDone", cls.dept, cls.done_user, ["Employee"])
        cls.pending_name = _make_employee("QASchedPending", cls.dept, cls.pending_user, ["Employee"])
        frappe.db.commit()

        frappe.set_user(cls.done_user)
        submit_morning_log(new_tasks=json.dumps([{"description": "T"}]), login_time="09:00", work_location="Office")
        submit_eod_log(lunch_from="", lunch_to="", logout_time="18:00", task_updates="[]", adhoc_tasks="[]")

        frappe.set_user(cls.pending_user)
        submit_morning_log(new_tasks=json.dumps([{"description": "T"}]), login_time="09:00", work_location="Office")

        frappe.set_user("Administrator")
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        for emp in (cls.done_name, cls.pending_name):
            frappe.db.sql("DELETE FROM `tabTask Entry` WHERE parent IN "
                           "(SELECT name FROM `tabDaily Work Log` WHERE employee=%s)", (emp,))
            frappe.db.sql("DELETE FROM `tabDaily Work Log` WHERE employee=%s", (emp,))
            frappe.db.sql("DELETE FROM `tabEmployee Checkin` WHERE employee=%s", (emp,))
        frappe.db.sql("DELETE FROM `tabEmployee` WHERE name IN (%s,%s)", (cls.done_name, cls.pending_name))
        frappe.db.sql("DELETE FROM `tabUser` WHERE email IN (%s,%s)", (cls.done_user, cls.pending_user))
        frappe.db.commit()

    def setUp(self):
        frappe.set_user("Administrator")

    def test_checkout_reminder_skips_checked_out_flags_pending(self):
        sent_to = []

        def fake_sendmail(recipients=None, **kwargs):
            sent_to.extend(recipients or [])

        with patch.object(tasks_module, "_skip_if_not_due", return_value=False), \
             patch.object(tasks_module, "_already_sent_today", return_value=False), \
             patch("frappe.sendmail", side_effect=fake_sendmail):
            tasks_module.send_employee_checkout_reminder()

        self.assertNotIn(self.done_user, sent_to,
            "Employee who already completed EOD must not get a checkout reminder")
        self.assertIn(self.pending_user, sent_to,
            "Employee who checked in but not out must get a checkout reminder")

    def test_checkout_reminder_states_cutoff_and_hr_approval(self):
        messages = []

        def fake_sendmail(recipients=None, message="", **kwargs):
            messages.append(message)

        with patch.object(tasks_module, "_skip_if_not_due", return_value=False), \
             patch.object(tasks_module, "_already_sent_today", return_value=False), \
             patch("st_attendance_tracker.tasks.get_checkout_cutoff", return_value=22 * 60), \
             patch("frappe.sendmail", side_effect=fake_sendmail):
            tasks_module.send_employee_checkout_reminder()

        self.assertTrue(messages)
        self.assertIn("before 10:00 PM", messages[0])
        self.assertIn("late checkout approval from HR", messages[0])
        self.assertNotIn("marked as absent", messages[0])

    def test_checkin_reminder_skips_already_checked_in(self):
        sent_to = []

        def fake_sendmail(recipients=None, **kwargs):
            sent_to.extend(recipients or [])

        with patch.object(tasks_module, "_skip_if_not_due", return_value=False), \
             patch.object(tasks_module, "_already_sent_today", return_value=False), \
             patch("frappe.sendmail", side_effect=fake_sendmail):
            tasks_module.send_employee_checkin_reminder()

        self.assertNotIn(self.done_user, sent_to,
            "Employee who already checked in must not get a check-in reminder")
        self.assertNotIn(self.pending_user, sent_to,
            "Employee who already checked in must not get a check-in reminder")


# ── SECTION 13 — "Management" role routing & HR Manager check-in access ────
# HR Manager must check in/out like any other employee (no forced redirect
# away from /daily-checkin); only the "Management" role — which has no
# Employee record — is redirected straight to /management-dashboard.

class TestManagementRoleRouting(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")

        company = frappe.db.get_single_value("Global Defaults", "default_company") or "_Test Company"
        cls.dept = (
            frappe.db.get_value("Department", {"department_name": "_QA Dept", "company": company}, "name")
            or f"_QA Dept - {company}"
        )
        cls.emp_user = "qa_route_emp@test.example.com"
        cls.hr_user = "qa_route_hr@test.example.com"
        cls.mgmt_user = "qa_route_mgmt@test.example.com"

        cls.emp_name = _make_employee("QARouteEmp", cls.dept, cls.emp_user, ["Employee"])
        cls.hr_name = _make_employee("QARouteHR", cls.dept, cls.hr_user, ["HR Manager"])

        if not frappe.db.exists("Role", "Management"):
            frappe.get_doc({"doctype": "Role", "role_name": "Management", "desk_access": 0}).insert(ignore_permissions=True)
        if not frappe.db.exists("User", cls.mgmt_user):
            u = frappe.new_doc("User")
            u.email = cls.mgmt_user
            u.first_name = "QA"
            u.last_name = "RouteMgmt"
            u.send_welcome_email = 0
            u.append("roles", {"role": "Management"})
            try:
                u.insert(ignore_permissions=True, ignore_if_duplicate=True)
            except Exception:
                pass
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        for emp in (cls.emp_name, cls.hr_name):
            frappe.db.sql("DELETE FROM `tabTask Entry` WHERE parent IN "
                           "(SELECT name FROM `tabDaily Work Log` WHERE employee=%s)", (emp,))
            frappe.db.sql("DELETE FROM `tabDaily Work Log` WHERE employee=%s", (emp,))
            frappe.db.sql("DELETE FROM `tabEmployee Checkin` WHERE employee=%s", (emp,))
        frappe.db.sql("DELETE FROM `tabEmployee` WHERE name IN (%s,%s)", (cls.emp_name, cls.hr_name))
        frappe.db.sql("DELETE FROM `tabUser` WHERE email IN (%s,%s,%s)",
                      (cls.emp_user, cls.hr_user, cls.mgmt_user))
        frappe.db.commit()

    def setUp(self):
        frappe.set_user("Administrator")

    def test_hr_manager_not_redirected_from_daily_checkin(self):
        """HR Manager must check in/out like any employee — no forced redirect."""
        frappe.set_user(self.hr_user)
        context = frappe._dict()
        try:
            daily_checkin_page.get_context(context)
        except frappe.Redirect:
            self.fail("HR Manager was redirected away from /daily-checkin")
        self.assertTrue(context.get("is_hr_manager"))

    def test_plain_employee_not_redirected_from_daily_checkin(self):
        frappe.set_user(self.emp_user)
        context = frappe._dict()
        try:
            daily_checkin_page.get_context(context)
        except frappe.Redirect:
            self.fail("Plain employee was redirected away from /daily-checkin")
        self.assertFalse(context.get("is_hr_manager"))

    def test_management_role_redirected_from_daily_checkin(self):
        """Management role has no Employee record and never checks in/out."""
        frappe.set_user(self.mgmt_user)
        context = frappe._dict()
        with self.assertRaises(frappe.Redirect):
            daily_checkin_page.get_context(context)
        self.assertEqual(frappe.local.flags.redirect_location, "/management-dashboard")

    def test_guest_redirected_to_login_from_daily_checkin(self):
        """Regression: a logged-out visitor must be sent to /login, not hit
        the "not linked to an Employee record" ValidationError traceback
        that used to surface for Guest (who obviously has no Employee
        record either)."""
        frappe.set_user("Guest")
        context = frappe._dict()
        with self.assertRaises(frappe.Redirect):
            daily_checkin_page.get_context(context)
        self.assertEqual(frappe.local.flags.redirect_location, "/login?redirect-to=/daily-checkin")

    def test_hr_manager_can_access_management_dashboard(self):
        frappe.set_user(self.hr_user)
        context = frappe._dict()
        try:
            management_dashboard_page.get_context(context)
        except frappe.Redirect:
            self.fail("HR Manager was denied the management dashboard")

    def test_management_role_can_access_management_dashboard(self):
        frappe.set_user(self.mgmt_user)
        context = frappe._dict()
        try:
            management_dashboard_page.get_context(context)
        except frappe.Redirect:
            self.fail("Management role was denied the management dashboard")

    def test_plain_employee_blocked_from_management_dashboard(self):
        frappe.set_user(self.emp_user)
        context = frappe._dict()
        with self.assertRaises(frappe.Redirect):
            management_dashboard_page.get_context(context)

    def test_management_role_can_call_get_management_dashboard_api(self):
        frappe.set_user(self.mgmt_user)
        result = get_management_dashboard(today())
        self.assertIn("summary", result)

    def test_plain_employee_blocked_from_get_management_dashboard_api(self):
        frappe.set_user(self.emp_user)
        with self.assertRaises(frappe.PermissionError):
            get_management_dashboard(today())


# ── SECTION 14 — Leave exclusion (reminders/reports) & Missed Checkin ──────
#    Checkout report. Regression coverage for: an employee on leave must
#    never get a checkin/checkout reminder or appear as "missing" — whether
#    the leave is recorded as a Leave Application (any non-rejected/
#    non-cancelled state, including still-Draft) OR directly on Attendance
#    (status "On Leave") — and a User with the Management role must never
#    appear here even if they also have their own Employee record.

class TestLeaveExclusionAndMissedReport(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.company = frappe.db.get_single_value("Global Defaults", "default_company") or "_Test Company"
        cls.dept = (
            frappe.db.get_value("Department", {"department_name": "_QA Dept", "company": cls.company}, "name")
            or f"_QA Dept - {cls.company}"
        )
        cls.emp_user = "qa_leave_emp@test.example.com"
        cls.emp_name = _make_employee("QALeaveEmp", cls.dept, cls.emp_user, ["Employee"])

        # A real employee who *also* holds the Management role — the case
        # that broke the old "Management users never have an Employee
        # record" assumption in _get_expected_employees.
        if not frappe.db.exists("Role", "Management"):
            frappe.get_doc({"doctype": "Role", "role_name": "Management", "desk_access": 0}).insert(ignore_permissions=True)
        cls.mgmt_emp_user = "qa_leave_mgmt_emp@test.example.com"
        cls.mgmt_emp_name = _make_employee("QALeaveMgmtEmp", cls.dept, cls.mgmt_emp_user, ["Employee", "Management"])

        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        for emp_name, emp_user in ((cls.emp_name, cls.emp_user), (cls.mgmt_emp_name, cls.mgmt_emp_user)):
            frappe.db.sql("DELETE FROM `tabTask Entry` WHERE parent IN "
                           "(SELECT name FROM `tabDaily Work Log` WHERE employee=%s)", (emp_name,))
            frappe.db.sql("DELETE FROM `tabDaily Work Log` WHERE employee=%s", (emp_name,))
            frappe.db.sql("DELETE FROM `tabEmployee Checkin` WHERE employee=%s", (emp_name,))
            frappe.db.sql("DELETE FROM `tabEmployee` WHERE name=%s", (emp_name,))
            frappe.db.sql("DELETE FROM `tabUser` WHERE email=%s", (emp_user,))
        frappe.db.commit()

    def setUp(self):
        frappe.set_user("Administrator")

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.db.sql("DELETE FROM `tabLeave Application` WHERE employee=%s", (self.emp_name,))
        frappe.db.sql("DELETE FROM `tabAttendance` WHERE employee=%s", (self.emp_name,))
        frappe.db.commit()

    def _make_attendance(self, date, status):
        att = frappe.new_doc("Attendance")
        att.employee = self.emp_name
        att.attendance_date = date
        att.status = status
        att.company = self.company
        att.flags.ignore_permissions = True
        att.flags.ignore_validate = True
        att.flags.ignore_mandatory = True
        att.insert(ignore_permissions=True)
        frappe.db.set_value("Attendance", att.name, "docstatus", 1)
        return att.name

    def test_expected_employees_excludes_full_day_leave_application(self):
        date = add_days(today(), 10)
        leave_type = frappe.db.get_value("Leave Type", {}, "name")
        leave = frappe.new_doc("Leave Application")
        leave.employee = self.emp_name
        leave.leave_type = leave_type
        leave.from_date = date
        leave.to_date = date
        leave.status = "Approved"
        leave.flags.ignore_permissions = True
        leave.flags.ignore_validate = True
        leave.flags.ignore_mandatory = True
        leave.insert(ignore_permissions=True)
        frappe.db.set_value("Leave Application", leave.name, "docstatus", 1)

        expected = tasks_module._get_expected_employees(date)
        self.assertNotIn(self.emp_name, [e.name for e in expected])

    def test_expected_employees_excludes_attendance_marked_on_leave(self):
        """Regression: leave recorded directly on Attendance (status='On
        Leave'), not via a Leave Application, must still exclude the
        employee from reminders/reports."""
        date = add_days(today(), 11)
        self._make_attendance(date, "On Leave")

        expected = tasks_module._get_expected_employees(date)
        self.assertNotIn(self.emp_name, [e.name for e in expected])

    def test_expected_employees_keeps_half_day_attendance(self):
        """A Half Day attendance record does not fully exclude — the
        employee is still expected to check in/out for their working half."""
        date = add_days(today(), 12)
        self._make_attendance(date, "Half Day")

        expected = tasks_module._get_expected_employees(date)
        self.assertIn(self.emp_name, [e.name for e in expected])

    def _make_leave_application(self, date, status, docstatus):
        leave_type = frappe.db.get_value("Leave Type", {}, "name")
        leave = frappe.new_doc("Leave Application")
        leave.employee = self.emp_name
        leave.leave_type = leave_type
        leave.from_date = date
        leave.to_date = date
        leave.status = status
        leave.total_leave_days = 1
        leave.docstatus = docstatus
        # Write the row directly. A normal insert fires on_update, and this site has a Server Script
        # (auto_approve_one_day_leave) that submits one-day leave, which HRMS then rejects because
        # validate() was skipped (total_leave_days is None). The tests only need the row to exist.
        leave.name = frappe.generate_hash(length=10)
        leave.set_user_and_timestamp()
        leave.db_insert()
        return leave.name

    def test_expected_employees_excludes_draft_leave_application(self):
        """A still-Draft (never submitted, docstatus 0) leave request for
        today must already exclude the employee — don't wait for HR to
        approve it before we stop nagging them."""
        date = add_days(today(), 14)
        self._make_leave_application(date, "Open", docstatus=0)

        expected = tasks_module._get_expected_employees(date)
        self.assertNotIn(self.emp_name, [e.name for e in expected])

    def test_expected_employees_excludes_open_submitted_leave_application(self):
        """Submitted but not yet approved (status Open, docstatus 1) also
        excludes — still a pending request, not a rejected/cancelled one."""
        date = add_days(today(), 15)
        self._make_leave_application(date, "Open", docstatus=1)

        expected = tasks_module._get_expected_employees(date)
        self.assertNotIn(self.emp_name, [e.name for e in expected])

    def test_expected_employees_keeps_rejected_leave_application(self):
        """A Rejected leave request does not excuse the employee — they're
        still expected to check in/out."""
        date = add_days(today(), 16)
        self._make_leave_application(date, "Rejected", docstatus=1)

        expected = tasks_module._get_expected_employees(date)
        self.assertIn(self.emp_name, [e.name for e in expected])

    def test_expected_employees_excludes_management_role_employee(self):
        """A real Employee record whose linked User also holds the
        Management role must be excluded — regression for the old code
        that only inferred "Management" by the *absence* of an Employee
        record, which breaks the moment Management is granted to someone
        who also checks in as a normal employee."""
        expected = tasks_module._get_expected_employees(today())
        self.assertNotIn(self.mgmt_emp_name, [e.name for e in expected])

    def test_missed_report_excludes_attendance_marked_on_leave(self):
        from st_attendance_tracker.st_attendance_tracker.report.missed_checkin_checkout.missed_checkin_checkout import get_data
        date = add_days(today(), 13)
        self._make_attendance(date, "On Leave")

        rows = get_data(date)
        self.assertNotIn(self.emp_name, [r["employee"] for r in rows])

    def test_missed_report_excludes_draft_leave_application(self):
        """The report shares _get_expected_employees with the scheduler
        jobs — a still-Draft leave request for today must keep the
        employee off the missing-checkin/checkout report too, not just
        out of the reminder emails."""
        from st_attendance_tracker.st_attendance_tracker.report.missed_checkin_checkout.missed_checkin_checkout import get_data
        date = add_days(today(), 17)
        self._make_leave_application(date, "Open", docstatus=0)

        rows = get_data(date)
        self.assertNotIn(self.emp_name, [r["employee"] for r in rows])

    def test_missed_report_lists_employee_who_never_checked_in(self):
        from st_attendance_tracker.st_attendance_tracker.report.missed_checkin_checkout.missed_checkin_checkout import get_data
        date = add_days(today(), 14)
        rows = get_data(date)
        row = next((r for r in rows if r["employee"] == self.emp_name), None)
        self.assertIsNotNone(row)
        self.assertEqual(row["missed_checkin"], "Yes")
        self.assertEqual(row["missed_checkout"], "Yes")

    def test_missed_report_excludes_fully_checked_out_employee(self):
        from st_attendance_tracker.st_attendance_tracker.report.missed_checkin_checkout.missed_checkin_checkout import get_data
        date = add_days(today(), 15)
        log = frappe.new_doc("Daily Work Log")
        log.employee = self.emp_name
        log.date = date
        log.morning_submitted = 1
        log.eod_submitted = 1
        log.login_time = "09:00:00"
        log.logout_time = "18:00:00"
        log.append("tasks", {"description": "Task", "status": "Done", "task_type": "Planned", "actual_time": 1})
        log.insert(ignore_permissions=True)
        try:
            rows = get_data(date)
            self.assertNotIn(self.emp_name, [r["employee"] for r in rows])
        finally:
            frappe.db.sql("DELETE FROM `tabTask Entry` WHERE parent=%s", (log.name,))
            frappe.db.delete("Daily Work Log", log.name)
            frappe.db.commit()

    def test_missed_report_department_filter(self):
        from st_attendance_tracker.st_attendance_tracker.report.missed_checkin_checkout.missed_checkin_checkout import get_data
        date = add_days(today(), 16)
        rows = get_data(date, department="Some Nonexistent Department - XX")
        self.assertEqual(rows, [])


def tearDownModule():
    # HRMS creates a User Permission for every Employee user, and these tests delete their employees
    # with raw SQL, so the permissions would pile up in the site database run after run.
    frappe.db.sql("DELETE FROM `tabUser Permission` WHERE user LIKE %s", ("%@test.example.com",))
    frappe.db.commit()


class TestCheckoutCutoff(FrappeTestCase):
    """Checkout closes at the cutoff time; only an HR Manager can reopen it
    for one employee and date."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        company = frappe.db.get_single_value("Global Defaults", "default_company") or "_Test Company"
        cls.dept = (
            frappe.db.get_value("Department", {"department_name": "_QA Dept", "company": company}, "name")
            or f"_QA Dept - {company}"
        )
        cls.emp_user = "cutoff_emp@test.example.com"
        cls.hr_user = "cutoff_hr@test.example.com"
        cls.sysmgr_user = "cutoff_sys@test.example.com"
        cls.emp_name = _make_employee("CUT001", cls.dept, cls.emp_user, ["Employee"])
        cls.hr_name = _make_employee("CUTHR", cls.dept, cls.hr_user, ["HR Manager"])
        cls.sys_name = _make_employee("CUTSYS", cls.dept, cls.sysmgr_user, ["System Manager"])
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        names = (cls.emp_name, cls.hr_name, cls.sys_name)
        frappe.db.sql("DELETE FROM `tabTask Entry` WHERE parent IN "
                      "(SELECT name FROM `tabDaily Work Log` WHERE employee IN (%s,%s,%s))", names)
        frappe.db.sql("DELETE FROM `tabDaily Work Log` WHERE employee IN (%s,%s,%s)", names)
        frappe.db.sql("DELETE FROM `tabEmployee Checkin` WHERE employee IN (%s,%s,%s)", names)
        frappe.db.sql("DELETE FROM `tabEmployee` WHERE name IN (%s,%s,%s)", names)
        frappe.db.sql("DELETE FROM `tabUser Permission` WHERE user IN (%s,%s,%s)",
                      (cls.emp_user, cls.hr_user, cls.sysmgr_user))
        frappe.db.sql("DELETE FROM `tabUser` WHERE email IN (%s,%s,%s)",
                      (cls.emp_user, cls.hr_user, cls.sysmgr_user))
        frappe.db.commit()

    def setUp(self):
        frappe.set_user("Administrator")
        # The cutoff is a site setting; pin it so these tests do not depend on it.
        patcher = patch("st_attendance_tracker.time_utils.get_checkout_cutoff", return_value=22 * 60)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.db.sql("DELETE FROM `tabTask Entry` WHERE parent IN "
                      "(SELECT name FROM `tabDaily Work Log` WHERE employee=%s)", (self.emp_name,))
        frappe.db.sql("DELETE FROM `tabDaily Work Log` WHERE employee=%s", (self.emp_name,))
        frappe.db.sql("DELETE FROM `tabEmployee Checkin` WHERE employee=%s", (self.emp_name,))
        frappe.db.commit()

    def _at(self, date, hhmm):
        return frappe.utils.get_datetime(f"{date} {hhmm}:00")

    def _checked_in_log(self, date):
        """A checked-in, not-checked-out log for `date`, written directly so
        the test does not depend on the real clock."""
        log = frappe.get_doc({
            "doctype": "Daily Work Log", "employee": self.emp_name, "date": date,
            "morning_submitted": 1, "login_time": "09:00:00", "work_location": "Office",
        })
        log.insert(ignore_permissions=True)
        return log

    # ── is_checkout_locked ──
    def test_locked_only_after_cutoff(self):
        d = today()
        self.assertFalse(is_checkout_locked(d, 0, self._at(d, "21:59"), 22 * 60))
        self.assertFalse(is_checkout_locked(d, 0, self._at(d, "22:00"), 22 * 60))
        self.assertTrue(is_checkout_locked(d, 0, self._at(d, "22:01"), 22 * 60))

    def test_past_date_is_locked_next_morning(self):
        y = add_days(today(), -1)
        self.assertTrue(is_checkout_locked(y, 0, self._at(today(), "09:00"), 22 * 60))

    def test_unlock_flag_overrides_cutoff(self):
        y = add_days(today(), -1)
        self.assertFalse(is_checkout_locked(y, 1, self._at(today(), "09:00"), 22 * 60))

    # ── submit / autosave guard ──
    def test_submit_eod_blocked_after_cutoff(self):
        self._checked_in_log(today())
        frappe.set_user(self.emp_user)
        with patch("frappe.utils.now_datetime", return_value=self._at(today(), "22:30")):
            with self.assertRaises(frappe.PermissionError):
                submit_eod_log(lunch_from="", lunch_to="", logout_time="22:30",
                               task_updates="[]", adhoc_tasks="[]")
            with self.assertRaises(frappe.PermissionError):
                autosave_eod_progress(task_updates="[]")
        self.assertFalse(_get_work_log(self.emp_name, today()).eod_submitted)

    # ── resolver ──
    def test_stale_locked_log_does_not_hijack_page(self):
        y = add_days(today(), -1)
        self._checked_in_log(y)
        with patch("frappe.utils.now_datetime", return_value=self._at(today(), "09:00")):
            self.assertEqual(str(_resolve_active_checkin_date(self.emp_name)), str(today()))

    def test_unlocked_stale_log_is_resumed(self):
        y = add_days(today(), -1)
        log = self._checked_in_log(y)
        frappe.db.set_value("Daily Work Log", log.name, "checkout_unlocked", 1)
        self.assertEqual(str(_resolve_active_checkin_date(self.emp_name)), str(y))

    # ── unlock permissions ──
    def test_hr_manager_unlocks_one_employee_date(self):
        y = add_days(today(), -1)
        log = self._checked_in_log(y)
        frappe.set_user(self.hr_user)
        with patch("frappe.sendmail"):
            unlock_late_checkout(self.emp_name, y, "Client call overran")
        log.reload()
        self.assertTrue(log.checkout_unlocked)
        self.assertEqual(log.unlocked_by, self.hr_user)

    def test_unlock_reason_is_optional(self):
        y = add_days(today(), -1)
        log = self._checked_in_log(y)
        frappe.set_user(self.hr_user)
        with patch("frappe.sendmail"):
            unlock_late_checkout(self.emp_name, y, "  ")
        log.reload()
        self.assertTrue(log.checkout_unlocked)
        self.assertIsNone(log.unlock_reason)

    def test_request_requires_reason(self):
        y = add_days(today(), -1)
        self._checked_in_log(y)
        frappe.set_user(self.emp_user)
        with patch("frappe.utils.now_datetime", return_value=self._at(today(), "09:00")):
            with self.assertRaises(frappe.ValidationError):
                request_late_checkout_approval(y, "")
            with self.assertRaises(frappe.ValidationError):
                request_late_checkout_approval(y, "   ")

    def test_non_hr_cannot_unlock(self):
        y = add_days(today(), -1)
        log = self._checked_in_log(y)
        for user in (self.emp_user, self.sysmgr_user):
            frappe.set_user(user)
            with self.assertRaises(frappe.PermissionError):
                unlock_late_checkout(self.emp_name, y, "self approve")
        log.reload()
        self.assertFalse(log.checkout_unlocked)

    # ── controller guards (direct document writes, e.g. native REST) ──
    def test_direct_save_cannot_checkout_after_cutoff(self):
        self._checked_in_log(today())
        frappe.set_user(self.emp_user)
        with patch("frappe.utils.now_datetime", return_value=self._at(today(), "22:30")):
            doc = _get_work_log(self.emp_name, today())
            doc.eod_submitted = 1
            doc.logout_time = "22:30:00"
            with self.assertRaises(frappe.PermissionError):
                doc.save()

    def test_direct_save_can_checkout_when_unlocked(self):
        log = self._checked_in_log(add_days(today(), -1))
        frappe.db.set_value("Daily Work Log", log.name, "checkout_unlocked", 1)
        frappe.set_user(self.emp_user)
        with patch("frappe.utils.now_datetime", return_value=self._at(today(), "09:00")), \
                patch("frappe.enqueue"), patch("st_attendance_tracker.api._make_checkin"), \
                patch("st_attendance_tracker.api._rollover_pending_tasks"):
            doc = _get_work_log(self.emp_name, add_days(today(), -1))
            doc.eod_submitted = 1
            doc.logout_time = "18:00:00"
            doc.save()
        self.assertTrue(_get_work_log(self.emp_name, add_days(today(), -1)).eod_submitted)

    def test_employee_cannot_self_unlock_via_direct_save(self):
        log = self._checked_in_log(add_days(today(), -1))
        frappe.set_user(self.emp_user)
        doc = _get_work_log(self.emp_name, log.date)
        doc.checkout_unlocked = 1
        with self.assertRaises(frappe.PermissionError):
            doc.save()
        frappe.db.rollback()
        self.assertFalse(frappe.db.get_value("Daily Work Log", log.name, "checkout_unlocked"))

    def test_employee_cannot_fake_unlock_request_via_direct_save(self):
        log = self._checked_in_log(add_days(today(), -1))
        frappe.set_user(self.emp_user)
        doc = _get_work_log(self.emp_name, log.date)
        doc.unlock_requested_at = frappe.utils.now_datetime()
        with self.assertRaises(frappe.PermissionError):
            doc.save()

    # ── request ──
    def test_request_notifies_hr_once(self):
        y = _get_previous_working_date(self.emp_name, today())
        self._checked_in_log(y)
        frappe.set_user(self.emp_user)
        with patch("frappe.sendmail") as sendmail, \
                patch("frappe.utils.now_datetime", return_value=self._at(today(), "09:00")):
            first = request_late_checkout_approval(y, "Forgot")
            second = request_late_checkout_approval(y, "Forgot")
        self.assertEqual(first["status"], "requested")
        self.assertEqual(second["status"], "already_requested")
        self.assertEqual(sendmail.call_count, 1)

    def test_request_rejected_while_checkout_open(self):
        self._checked_in_log(today())
        frappe.set_user(self.emp_user)
        with patch("frappe.utils.now_datetime", return_value=self._at(today(), "15:00")):
            with self.assertRaises(frappe.ValidationError):
                request_late_checkout_approval(today(), "early")

    # ── page context ──
    def test_page_context_locks_today_after_cutoff(self):
        self._checked_in_log(today())
        frappe.set_user(self.emp_user)
        context = frappe._dict()
        with patch("frappe.utils.now_datetime", return_value=self._at(today(), "22:30")):
            daily_checkin_page.get_context(context)
        self.assertTrue(context.checkout_locked)
        self.assertFalse(context.checkout_unlock_requested)

    def test_page_context_open_before_cutoff(self):
        self._checked_in_log(today())
        frappe.set_user(self.emp_user)
        context = frappe._dict()
        with patch("frappe.utils.now_datetime", return_value=self._at(today(), "15:00")):
            daily_checkin_page.get_context(context)
        self.assertFalse(context.checkout_locked)
        self.assertFalse(context.missed_checkout)

    def test_page_context_shows_missed_checkout_and_checkin_screen(self):
        y = _get_previous_working_date(self.emp_name, today())
        self._checked_in_log(y)
        frappe.set_user(self.emp_user)
        context = frappe._dict()
        with patch("frappe.utils.now_datetime", return_value=self._at(today(), "09:00")):
            daily_checkin_page.get_context(context)
        self.assertEqual(str(context.missed_checkout.date), str(y))
        self.assertFalse(context.is_checked_in, "Employee must land on today's check-in")
        self.assertFalse(context.checkout_locked)

    def _render_page(self):
        """Render the page_content block with the real get_context output —
        the base web template needs a full request, the block does not."""
        import os
        context = frappe._dict()
        daily_checkin_page.get_context(context)
        path = os.path.join(os.path.dirname(daily_checkin_page.__file__), "daily_checkin.html")
        with open(path) as f:
            template = frappe.get_jenv().from_string(f.read())
        return "".join(template.blocks["page_content"](template.new_context(dict(context))))

    def test_rendered_page_shows_lock_card_and_hides_checkout_button(self):
        self._checked_in_log(today())
        frappe.set_user(self.emp_user)
        with patch("frappe.utils.now_datetime", return_value=self._at(today(), "22:30")):
            html = self._render_page()
        self.assertIn("Get late checkout approval from HR", html)
        self.assertIn("requestLateCheckout(", html)
        self.assertNotIn('id="btn-eod"', html)

    def test_rendered_page_has_checkout_button_before_cutoff(self):
        self._checked_in_log(today())
        frappe.set_user(self.emp_user)
        with patch("frappe.utils.now_datetime", return_value=self._at(today(), "15:00")):
            html = self._render_page()
        self.assertIn('id="btn-eod"', html)
        self.assertNotIn("Get late checkout approval from HR", html)

    # ── pending requests list ──
    def test_pending_requests_listed_for_hr_only_until_unlocked(self):
        y = _get_previous_working_date(self.emp_name, today())
        self._checked_in_log(y)
        frappe.set_user(self.emp_user)
        with patch("frappe.sendmail"), \
                patch("frappe.utils.now_datetime", return_value=self._at(today(), "09:00")):
            request_late_checkout_approval(y, "Forgot")

        frappe.set_user(self.hr_user)
        rows = [r for r in get_late_checkout_requests() if r.employee == self.emp_name]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].reason, "Forgot")
        self.assertEqual(str(rows[0].date), str(y))

        for user in (self.emp_user, self.sysmgr_user):
            frappe.set_user(user)
            with self.assertRaises(frappe.PermissionError):
                get_late_checkout_requests()

        frappe.set_user(self.hr_user)
        with patch("frappe.sendmail"):
            unlock_late_checkout(self.emp_name, y, "ok")
        self.assertFalse([r for r in get_late_checkout_requests() if r.employee == self.emp_name])

    def _render_management_page(self, is_hr_manager):
        import os
        context = frappe._dict()
        management_dashboard_page.get_context(context)
        context.is_hr_manager = is_hr_manager
        path = os.path.join(os.path.dirname(management_dashboard_page.__file__), "management_dashboard.html")
        with open(path) as f:
            template = frappe.get_jenv().from_string(f.read())
        return "".join(template.blocks["page_content"](template.new_context(dict(context))))

    def test_management_dashboard_requests_panel_for_hr_manager(self):
        frappe.set_user(self.hr_user)
        context = frappe._dict()
        management_dashboard_page.get_context(context)
        self.assertTrue(context.is_hr_manager)
        self.assertIn('id="lcr-panel"', self._render_management_page(True))

    def test_management_dashboard_hides_requests_panel_without_hr_role(self):
        frappe.set_user(self.hr_user)
        self.assertNotIn('id="lcr-panel"', self._render_management_page(False))

    # ── last-working-day window ──
    def test_previous_working_date_skips_dates_in_the_holiday_list(self):
        # Weekly offs and public holidays are rows of the Holiday List; no weekday is special.
        monday = getdate("2026-10-05")
        self.assertEqual(monday.weekday(), 0)
        holidays = {getdate("2026-10-04"), getdate("2026-10-03")}  # Sunday weekly off + Saturday public holiday
        with patch("st_attendance_tracker.api._employee_holidays", return_value=holidays):
            self.assertEqual(str(_get_previous_working_date(self.emp_name, monday)), "2026-10-02")
            self.assertEqual(str(_get_next_working_date(self.emp_name, getdate("2026-10-02"))), "2026-10-05")

    def test_sunday_is_a_working_day_unless_the_holiday_list_says_otherwise(self):
        monday = getdate("2026-10-05")
        with patch("st_attendance_tracker.api._employee_holidays", return_value=set()):
            self.assertEqual(str(_get_previous_working_date(self.emp_name, monday)), "2026-10-04")

    def test_holidays_come_from_the_list_assigned_to_the_employee(self):
        from st_attendance_tracker.api import _employee_holidays

        frappe.db.set_value("Employee", self.emp_name, "holiday_list", None)
        self.assertEqual(_employee_holidays(self.emp_name), set())  # no list, no days off (no Company fallback)
        lst = frappe.get_doc({
            "doctype": "Holiday List", "holiday_list_name": "ST Test Own List",
            "from_date": "2026-10-01", "to_date": "2026-10-31",
            "holidays": [{"holiday_date": "2026-10-04", "description": "Sunday", "weekly_off": 1},
                         {"holiday_date": "2026-10-20", "description": "Public holiday"}],
        }).insert(ignore_permissions=True)
        self.addCleanup(frappe.delete_doc, "Holiday List", lst.name, force=True)
        frappe.db.set_value("Employee", self.emp_name, "holiday_list", lst.name)
        self.assertEqual(_employee_holidays(self.emp_name), {getdate("2026-10-04"), getdate("2026-10-20")})
        self.assertEqual(str(_get_previous_working_date(self.emp_name, getdate("2026-10-05"))), "2026-10-03")
        self.assertEqual(str(_get_previous_working_date(self.emp_name, getdate("2026-10-21"))), "2026-10-19")

    def test_request_rejected_for_older_than_last_working_day(self):
        old = add_days(today(), -4)
        self._checked_in_log(old)
        frappe.set_user(self.emp_user)
        with patch("frappe.sendmail") as sendmail, \
                patch("frappe.utils.now_datetime", return_value=self._at(today(), "09:00")):
            with self.assertRaises(frappe.ValidationError):
                request_late_checkout_approval(old, "too old")
        sendmail.assert_not_called()

    def test_page_context_ignores_older_open_logs(self):
        self._checked_in_log(add_days(today(), -4))
        frappe.set_user(self.emp_user)
        context = frappe._dict()
        with patch("frappe.utils.now_datetime", return_value=self._at(today(), "09:00")):
            daily_checkin_page.get_context(context)
        self.assertFalse(context.missed_checkout)


class TestCheckinBlockedUntilCheckout(FrappeTestCase):
    """An employee who did not check out on their last working day cannot check in
    until that checkout is completed. Switch and start date live in ST Attendance Settings."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        company = frappe.db.get_single_value("Global Defaults", "default_company") or "_Test Company"
        cls.dept = (
            frappe.db.get_value("Department", {"department_name": "_QA Dept", "company": company}, "name")
            or f"_QA Dept - {company}"
        )
        cls.emp_user = "blk_emp@test.example.com"
        cls.emp_name = _make_employee("BLK001", cls.dept, cls.emp_user, ["Employee"])
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        frappe.db.sql("DELETE FROM `tabTask Entry` WHERE parent IN "
                      "(SELECT name FROM `tabDaily Work Log` WHERE employee=%s)", (cls.emp_name,))
        frappe.db.sql("DELETE FROM `tabDaily Work Log` WHERE employee=%s", (cls.emp_name,))
        frappe.db.sql("DELETE FROM `tabEmployee Checkin` WHERE employee=%s", (cls.emp_name,))
        frappe.db.sql("DELETE FROM `tabEmployee` WHERE name=%s", (cls.emp_name,))
        frappe.db.sql("DELETE FROM `tabUser Permission` WHERE user=%s", (cls.emp_user,))
        frappe.db.sql("DELETE FROM `tabUser` WHERE email=%s", (cls.emp_user,))
        frappe.db.commit()

    def setUp(self):
        frappe.set_user("Administrator")
        self.previous_day = _get_previous_working_date(self.emp_name, today())

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.db.sql("DELETE FROM `tabTask Entry` WHERE parent IN "
                      "(SELECT name FROM `tabDaily Work Log` WHERE employee=%s)", (self.emp_name,))
        frappe.db.sql("DELETE FROM `tabDaily Work Log` WHERE employee=%s", (self.emp_name,))
        frappe.db.sql("DELETE FROM `tabEmployee Checkin` WHERE employee=%s", (self.emp_name,))
        frappe.db.commit()

    def _settings(self, enabled=1, effective_from=None):
        values = dict(_get_attendance_settings())
        values["block_checkin_until_checkout_done"] = enabled
        values["checkin_block_effective_from"] = effective_from or add_days(today(), -30)
        return patch("st_attendance_tracker.api._get_attendance_settings", return_value=values)

    def _open_log(self, date, **extra):
        log = frappe.get_doc({
            "doctype": "Daily Work Log", "employee": self.emp_name, "date": date,
            "morning_submitted": 1, "login_time": "09:00:00", "work_location": "Office", **extra,
        })
        log.insert(ignore_permissions=True)
        return log

    def _check_in(self):
        frappe.set_user(self.emp_user)
        return submit_morning_log(
            new_tasks=json.dumps([{"description": "Plan the day", "estimated_time": ""}]),
            work_location="Office",
        )

    def test_unclosed_last_working_day_blocks_checkin(self):
        self._open_log(self.previous_day)
        with self._settings(), self.assertRaises(frappe.ValidationError) as ctx:
            self._check_in()
        self.assertIn("did not check out", str(ctx.exception))
        self.assertFalse(_get_work_log(self.emp_name, today()))

    def test_checked_out_last_working_day_allows_checkin(self):
        self._open_log(self.previous_day, eod_submitted=1, logout_time="18:00:00")
        with self._settings():
            self._check_in()
        self.assertTrue(_get_work_log(self.emp_name, today()).morning_submitted)

    def test_hr_approved_but_not_yet_checked_out_still_blocks(self):
        self._open_log(self.previous_day, checkout_unlocked=1)
        with self._settings(), self.assertRaises(frappe.ValidationError):
            self._check_in()

    def test_days_before_the_start_date_are_ignored(self):
        self._open_log(self.previous_day)
        with self._settings(effective_from=today()):
            self._check_in()
        self.assertTrue(_get_work_log(self.emp_name, today()).morning_submitted)

    def test_switch_off_or_no_start_date_disables_the_block(self):
        self._open_log(self.previous_day)
        with self._settings(enabled=0):
            self.assertIsNone(_get_unclosed_previous_day(self.emp_name, today()))
        values = dict(_get_attendance_settings(), block_checkin_until_checkout_done=1, checkin_block_effective_from=None)
        with patch("st_attendance_tracker.api._get_attendance_settings", return_value=values):
            self.assertIsNone(_get_unclosed_previous_day(self.emp_name, today()))

    def test_only_the_last_working_day_is_considered(self):
        older = _get_previous_working_date(self.emp_name, self.previous_day)
        self._open_log(older)
        with self._settings():
            self.assertIsNone(_get_unclosed_previous_day(self.emp_name, today()))

    def test_no_log_on_last_working_day_does_not_block(self):
        with self._settings():
            self.assertIsNone(_get_unclosed_previous_day(self.emp_name, today()))

    # ── real logout time for an approved late checkout ──
    def test_failed_employee_checkin_does_not_abort_the_caller(self):
        from st_attendance_tracker.api import _make_checkin

        from unittest.mock import MagicMock

        broken = MagicMock()
        broken.insert.side_effect = frappe.ValidationError("x" * 400)  # long enough to overflow an Error Log title
        with patch("frappe.new_doc", return_value=broken):
            _make_checkin(self.emp_name, "OUT", "18:00:00", on_date=self.previous_day)  # must not raise

    def test_late_checkout_requires_a_real_logout_time(self):
        log = self._open_log(self.previous_day)
        for bad in ("", "abc", "25:10", "08:30"):
            with self.assertRaises(frappe.ValidationError):
                _resolve_late_logout_time(bad, log)
        self.assertEqual(_resolve_late_logout_time("18:15", log), "18:15:00")

    def test_late_checkout_saves_the_typed_time_on_the_work_date(self):
        self._open_log(self.previous_day, checkout_unlocked=1)
        frappe.set_user(self.emp_user)
        submit_eod_log(lunch_from="", lunch_to="", logout_time="18:15", task_updates="[]", adhoc_tasks="[]")
        log = _get_work_log(self.emp_name, self.previous_day)
        self.assertTrue(log.eod_submitted)
        self.assertEqual(_to_hhmm(log.logout_time), "18:15")
        out_log = frappe.db.get_value(
            "Employee Checkin", {"employee": self.emp_name, "log_type": "OUT"}, "time", order_by="creation desc"
        )
        self.assertEqual(str(getdate(out_log)), str(self.previous_day))
