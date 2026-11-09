"""State-changing endpoints must be POST-only (a GET carries the session cookie
cross-site with no CSRF token)."""
import frappe
from frappe.tests.utils import FrappeTestCase

from st_attendance_tracker import api

POST_ONLY = [
    "save_recurring_task", "delete_recurring_task", "save_backlog_item", "save_backlog_items",
    "delete_backlog_item", "push_task_to_backlog", "pull_backlog_item_to_today",
    "submit_morning_log", "submit_eod_log", "autosave_eod_progress", "add_adhoc_tasks",
    "delete_carried_task", "move_task_to_backlog", "delete_carried_project",
    "reset_morning_checkin", "update_half_day_session",
]


class TestApiHttpMethods(FrappeTestCase):
    def test_mutating_endpoints_are_post_only(self):
        for name in POST_ONLY:
            fn = getattr(api, name)
            self.assertEqual(
                frappe.allowed_http_methods_for_whitelisted_func.get(fn), ["POST"], name
            )
