"""Cross-employee access control for Daily Work Log (permissions.py hooks)."""
import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import today

from st_attendance_tracker.st_attendance_tracker.doctype.daily_work_log.test_daily_work_log import _make_employee


class TestDailyWorkLogPermissions(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        company = frappe.db.get_single_value("Global Defaults", "default_company") or "_Test Company"
        dept = (
            frappe.db.get_value("Department", {"department_name": "_QA Dept", "company": company}, "name")
            or f"_QA Dept - {company}"
        )
        cls.user_a = "dwlperm_a@test.example.com"
        cls.user_b = "dwlperm_b@test.example.com"
        cls.user_lead = "dwlperm_lead@test.example.com"
        cls.user_hr = "dwlperm_hr@test.example.com"
        cls.emp_a = _make_employee("PermA", dept, cls.user_a, ["Employee"])
        cls.emp_b = _make_employee("PermB", dept, cls.user_b, ["Employee"])
        cls.emp_lead = _make_employee("PermLead", dept, cls.user_lead, ["Employee"])
        _make_employee("PermHR", dept, cls.user_hr, ["Employee", "HR Manager"])
        frappe.db.set_value("Employee", cls.emp_a, "reports_to", cls.emp_lead)

        existing = frappe.db.exists("Daily Work Log", {"employee": cls.emp_a, "date": today()})
        cls.log_name = existing or frappe.get_doc(
            {"doctype": "Daily Work Log", "employee": cls.emp_a, "date": today()}
        ).insert(ignore_permissions=True).name

    def setUp(self):
        # Fresh doc: insert(ignore_permissions=True) leaves that flag on the
        # instance, and has_permission() would then short-circuit to True.
        self.log_a = frappe.get_doc("Daily Work Log", self.log_name)

    def tearDown(self):
        frappe.set_user("Administrator")

    def test_owner_can_read_and_write_own_log(self):
        self.assertTrue(self.log_a.has_permission("read", user=self.user_a))
        self.assertTrue(self.log_a.has_permission("write", user=self.user_a))

    def test_other_employee_denied_read_and_write(self):
        self.assertFalse(self.log_a.has_permission("read", user=self.user_b))
        self.assertFalse(self.log_a.has_permission("write", user=self.user_b))

    # The two tests below call the hook directly: ERPNext auto-creates an
    # Employee User Permission per user, which the framework applies before the
    # hook runs and which would mask what the hook itself decides.
    def test_hook_lets_team_leader_read_but_not_write(self):
        from st_attendance_tracker.permissions import has_permission_daily_work_log as hook

        self.assertIsNone(hook(self.log_a, "read", self.user_lead))
        self.assertFalse(hook(self.log_a, "write", self.user_lead))

    def test_hook_does_not_restrict_hr_manager(self):
        from st_attendance_tracker.permissions import has_permission_daily_work_log as hook

        self.assertIsNone(hook(self.log_a, "read", self.user_hr))
        self.assertIsNone(hook(self.log_a, "write", self.user_hr))

    def test_get_list_hides_other_employees_logs(self):
        frappe.set_user(self.user_b)
        employees = {row.employee for row in frappe.get_list("Daily Work Log", fields=["employee"], limit=500)}
        self.assertNotIn(self.emp_a, employees)

    def test_query_condition_scopes_leader_to_own_and_led_employees(self):
        from st_attendance_tracker.permissions import daily_work_log_query_conditions

        condition = daily_work_log_query_conditions(self.user_lead)
        self.assertIn(self.emp_lead, condition)
        self.assertIn(self.emp_a, condition)
        self.assertNotIn(self.emp_b, condition)

    def test_query_condition_unrestricted_for_hr(self):
        from st_attendance_tracker.permissions import daily_work_log_query_conditions

        self.assertEqual(daily_work_log_query_conditions(self.user_hr), "")
