"""What's New bell — audience filtering, unread counting, mark-as-seen."""
import frappe
from frappe.tests.utils import FrappeTestCase

from st_attendance_tracker.announcements import get_announcements, mark_announcements_seen


def _make_user(email, roles):
    if not frappe.db.exists("User", email):
        u = frappe.new_doc("User")
        u.email = email
        u.first_name = "QA"
        u.last_name = "Announce"
        u.send_welcome_email = 0
        u.insert(ignore_permissions=True, ignore_if_duplicate=True)
    user_doc = frappe.get_doc("User", email)
    existing = [r.role for r in user_doc.roles]
    for role in roles:
        if role not in existing:
            user_doc.append("roles", {"role": role})
    user_doc.save(ignore_permissions=True)
    return email


def _make_employee_for(user_email, dept_name):
    """ERPNext's own User.validate hook (validate_employee_role) silently
    strips the "Employee" role from any User with no linked Employee
    record — so a test User needs one to actually keep that role."""
    company = frappe.db.get_single_value("Global Defaults", "default_company") or "_Test Company"
    if not frappe.db.exists("Department", dept_name):
        frappe.get_doc({
            "doctype": "Department",
            "department_name": "_QA Announce Dept",
            "company": company,
        }).insert(ignore_permissions=True, ignore_if_duplicate=True)
    real_dept = frappe.db.get_value(
        "Department", {"department_name": "_QA Announce Dept", "company": company}, "name"
    ) or dept_name

    existing_emp = frappe.db.get_value("Employee", {"user_id": user_email}, "name")
    if existing_emp:
        return existing_emp

    e = frappe.new_doc("Employee")
    e.company = company
    e.first_name = "QA"
    e.last_name = "Announce"
    e.gender = "Male"
    e.date_of_birth = "1990-01-01"
    e.date_of_joining = "2020-01-01"
    e.department = real_dept
    e.user_id = user_email
    e.insert(ignore_permissions=True)
    return e.name


class TestSTAnnouncement(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.dept = "_QA Announce Dept"
        cls.emp_user = _make_user("qa_announce_emp@test.example.com", ["Employee"])
        cls.emp_name = _make_employee_for(cls.emp_user, cls.dept)
        cls.hr_user = _make_user("qa_announce_hr@test.example.com", ["HR Manager"])

        cls.everyone_ann = frappe.get_doc({
            "doctype": "ST Announcement",
            "title": "QA Everyone Announcement",
            "category": "Feature",
            "audience": "Everyone",
            "description": "Visible to all roles.",
            "is_published": 1,
            "publish_date": "2020-01-01 00:00:00",
        }).insert(ignore_permissions=True)

        cls.hr_only_ann = frappe.get_doc({
            "doctype": "ST Announcement",
            "title": "QA HR-Only Announcement",
            "category": "Report",
            "audience": "HR Manager & Management",
            "description": "Visible to HR Manager/Management only.",
            "is_published": 1,
            "publish_date": "2020-01-01 00:00:00",
        }).insert(ignore_permissions=True)

        cls.draft_ann = frappe.get_doc({
            "doctype": "ST Announcement",
            "title": "QA Draft Announcement",
            "category": "Feature",
            "audience": "Everyone",
            "description": "Not published yet.",
            "is_published": 0,
            "publish_date": "2020-01-01 00:00:00",
        }).insert(ignore_permissions=True)

        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        for ann in (cls.everyone_ann.name, cls.hr_only_ann.name, cls.draft_ann.name):
            frappe.db.delete("ST Announcement", ann)
        for user in (cls.emp_user, cls.hr_user):
            frappe.db.delete("ST Announcement Seen", {"user": user})
        frappe.db.sql("DELETE FROM `tabEmployee` WHERE name=%s", (cls.emp_name,))
        frappe.db.sql("DELETE FROM `tabUser` WHERE email IN (%s,%s)", (cls.emp_user, cls.hr_user))
        frappe.db.commit()

    def setUp(self):
        frappe.set_user("Administrator")

    def tearDown(self):
        frappe.set_user("Administrator")
        for user in (self.emp_user, self.hr_user):
            frappe.db.delete("ST Announcement Seen", {"user": user})
        frappe.db.commit()

    def test_employee_only_sees_everyone_tier(self):
        frappe.set_user(self.emp_user)
        result = get_announcements()
        titles = {i["title"] for i in result["items"]}
        self.assertIn("QA Everyone Announcement", titles)
        self.assertNotIn("QA HR-Only Announcement", titles)

    def test_hr_manager_sees_both_tiers(self):
        frappe.set_user(self.hr_user)
        result = get_announcements()
        titles = {i["title"] for i in result["items"]}
        self.assertIn("QA Everyone Announcement", titles)
        self.assertIn("QA HR-Only Announcement", titles)

    def test_unpublished_announcement_never_shown(self):
        frappe.set_user(self.emp_user)
        result = get_announcements()
        titles = {i["title"] for i in result["items"]}
        self.assertNotIn("QA Draft Announcement", titles)

    def test_guest_gets_empty_result_not_an_error(self):
        frappe.set_user("Guest")
        result = get_announcements()
        self.assertEqual(result, {"items": [], "unread_count": 0})

    def test_unread_count_and_mark_seen(self):
        frappe.set_user(self.emp_user)
        frappe.db.delete("ST Announcement Seen", {"user": self.emp_user})
        frappe.db.commit()

        # No Seen record yet -> existing announcements count as new.
        before = get_announcements()
        self.assertGreaterEqual(before["unread_count"], 1)
        self.assertTrue(any(i["name"] == self.everyone_ann.name and i["is_new"] for i in before["items"]))

        mark_announcements_seen()

        after = get_announcements()
        self.assertEqual(after["unread_count"], 0)
        self.assertTrue(all(not i["is_new"] for i in after["items"]))

    def test_mark_seen_is_idempotent_update_not_duplicate(self):
        frappe.set_user(self.emp_user)
        mark_announcements_seen()
        mark_announcements_seen()
        self.assertEqual(
            frappe.db.count("ST Announcement Seen", {"user": self.emp_user}), 1
        )
