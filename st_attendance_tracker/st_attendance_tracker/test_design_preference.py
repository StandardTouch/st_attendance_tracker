"""Default design (calendar or classic) is stored per user and applied by a redirect."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from st_attendance_tracker import ui_shell

USER = "design-pref-test@test.example.com"


class TestDesignPreference(FrappeTestCase):
    def setUp(self):
        self._user = frappe.session.user
        frappe.set_user("Administrator")
        frappe.defaults.clear_user_default(ui_shell.DESIGN_KEY, USER)

    def tearDown(self):
        frappe.defaults.clear_user_default(ui_shell.DESIGN_KEY, USER)
        frappe.set_user(self._user)

    @contextmanager
    def _as_user(self):
        # The preference belongs to whoever is signed in; no real User record is needed for that.
        frappe.set_user(USER)
        try:
            yield
        finally:
            frappe.set_user("Administrator")

    def test_unset_by_default(self):
        with self._as_user():
            self.assertIsNone(ui_shell.get_design_preference())

    def test_set_and_get_only_touch_the_callers_setting(self):
        with self._as_user():
            ui_shell.set_design_preference("classic")
            self.assertEqual(ui_shell.get_design_preference(), "classic")
            ui_shell.set_design_preference("new")
            self.assertEqual(ui_shell.get_design_preference(), "new")
        self.assertIsNone(frappe.defaults.get_user_default(ui_shell.DESIGN_KEY, "Administrator"))

    def test_rejects_unknown_design(self):
        with self._as_user(), self.assertRaises(frappe.ValidationError):
            ui_shell.set_design_preference("fancy")

    def _visit(self, path, pref=None, cookie=None):
        request = SimpleNamespace(path=path, cookies={ui_shell.VIEW_COOKIE: cookie} if cookie else {})
        with self._as_user(), patch.object(frappe.local, "request", request, create=True):
            if pref:
                ui_shell.set_design_preference(pref)
            try:
                ui_shell.redirect_for_design()
            except frappe.Redirect:
                return frappe.local.flags.redirect_location
        return None

    def test_classic_preference_redirects_new_pages(self):
        self.assertEqual(self._visit("/my-history", "classic"), "/my-history-classic")

    def test_new_preference_redirects_classic_pages(self):
        self.assertEqual(self._visit("/task-backlog-classic", "new"), "/task-backlog")

    def test_matching_page_does_not_redirect(self):
        self.assertIsNone(self._visit("/my-history", "new"))
        self.assertIsNone(self._visit("/my-history-classic", "classic"))

    def test_no_preference_defaults_to_classic(self):
        self.assertEqual(self._visit("/my-history"), "/my-history-classic")
        self.assertIsNone(self._visit("/my-history-classic"))

    def test_session_switch_cookie_overrides_the_default(self):
        self.assertIsNone(self._visit("/my-history-classic", "new", cookie="classic"))
        self.assertEqual(self._visit("/my-history", "classic", cookie="new") or "none", "none")

    def test_other_routes_are_left_alone(self):
        self.assertIsNone(self._visit("/something-else", "classic"))
