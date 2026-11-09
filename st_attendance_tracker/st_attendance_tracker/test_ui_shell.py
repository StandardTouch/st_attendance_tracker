"""Branded page shell: navigation visibility per role and macro rendering."""
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from st_attendance_tracker import ui_shell
from st_attendance_tracker.www import recurring_tasks as recurring_tasks_page

EMPLOYEE = frappe._dict(name="HR-EMP-TEST", employee_name="Test Person", department="Dept")
SHELL_TEMPLATE = (
    '{% from "st_attendance_tracker/templates/macros/st_shell.html" import shell %}'
    '{% call shell(ctx, "Recurring Tasks", "doTour()") %}BODY-MARKER{% endcall %}'
)


def _keys(ctx):
    return [item["key"] for item in ctx["nav"]]


def _page_request(path):
    """Request for a page route. Classic is the default design now, so new-design routes ask for it via the session cookie."""
    from werkzeug.wrappers import Request

    cookie = {} if path.endswith(ui_shell.CLASSIC_SUFFIX) else {"Cookie": "st_view=new"}
    return Request.from_values(path, headers=cookie)


class TestShellNavigation(FrappeTestCase):
    def _ctx(self, roles, is_team_leader=False, employee=EMPLOYEE):
        with patch.object(ui_shell.frappe, "get_roles", return_value=roles), \
                patch.object(ui_shell, "_is_team_leader", return_value=is_team_leader):
            return ui_shell.get_shell_context("recurring", employee)

    def test_employee_sees_only_self_service_pages(self):
        keys = _keys(self._ctx(["Employee"]))
        self.assertEqual(keys, ["checkin", "backlog", "additional", "history", "recurring"])

    def test_team_leader_gets_team_dashboard_not_management(self):
        keys = _keys(self._ctx(["Employee"], is_team_leader=True))
        self.assertIn("team", keys)
        self.assertNotIn("management", keys)

    def test_hr_manager_gets_management_dashboard(self):
        self.assertIn("management", _keys(self._ctx(["Employee", "HR Manager"])))

    def test_management_user_without_employee_sees_only_management(self):
        ctx = self._ctx(["Management"], employee=frappe._dict(name="", employee_name="Boss"))
        self.assertEqual(_keys(ctx), ["management"])
        self.assertEqual(ctx["profile_url"], "")

    def test_create_menu_follows_role(self):
        labels = lambda ctx: [i["label"] for i in ctx["create_items"]]
        self.assertEqual(labels(self._ctx(["Employee"])), ["Daily task", "Additional work", "Recurring task"])
        self.assertIn("Assign team task", labels(self._ctx(["Employee"], is_team_leader=True)))
        mgmt = self._ctx(["Management"], employee=frappe._dict(name="", employee_name="Boss"))
        self.assertEqual(mgmt["create_items"], [])

    def test_active_page_is_flagged(self):
        ctx = self._ctx(["Employee"])
        self.assertEqual([i["key"] for i in ctx["nav"] if i["active"]], ["recurring"])
        self.assertEqual(ctx["initials"], "TE")


class TestShellRendering(FrappeTestCase):
    def test_macro_renders_shell_around_content(self):
        ctx = {"nav": [
            {"key": "checkin", "label": "Daily Check-in", "href": "/daily-checkin", "icon": "ti-calendar-check", "active": False},
            {"key": "recurring", "label": "Recurring Tasks", "href": "/recurring-tasks", "icon": "ti-repeat", "active": True},
        ], "initials": "TE", "profile_url": "/app/employee/X",
            "create_items": [{"label": "Daily task", "href": "/daily-checkin#new-task"}]}
        html = frappe.render_template(SHELL_TEMPLATE, {"ctx": ctx})
        self.assertIn("BODY-MARKER", html)
        self.assertEqual(html.count('aria-current="page"'), 1)
        self.assertIn('href="/recurring-tasks" aria-current="page"', html)
        # ids the What's New widget and shell JS mount into
        for marker in ('id="wn-mount"', 'id="wn-banner-mount"', 'id="st-avatar"', 'id="st-rail"',
                       'id="st-create"', 'id="st-mini"', 'id="st-menu-btn"'):
            self.assertIn(marker, html)
        self.assertIn("doTour()", html)
        self.assertNotIn('id="st-views"', html)

    def test_macro_optional_controls(self):
        ctx = {"nav": [], "initials": "TE", "profile_url": "", "create_items": []}
        tpl = ('{% from "st_attendance_tracker/templates/macros/st_shell.html" import shell %}'
               '{% call shell(ctx, "T", nav_controls=true, views=true, rail_extra="EXTRA-MARKER") %}x{% endcall %}')
        html = frappe.render_template(tpl, {"ctx": ctx})
        for marker in ('data-st-nav="today"', 'data-st-nav="prev"', 'id="st-views"', "EXTRA-MARKER"):
            self.assertIn(marker, html)
        self.assertNotIn('id="st-create"', html)

    def test_page_context_exposes_shell(self):
        context = frappe._dict()
        with patch("frappe.db.get_value", side_effect=[EMPLOYEE, ""]), \
                patch("frappe.session", frappe._dict(user="someone@example.com")), \
                patch.object(ui_shell.frappe, "get_roles", return_value=["Employee"]), \
                patch.object(ui_shell, "_is_team_leader", return_value=False):
            recurring_tasks_page.get_context(context)
        self.assertEqual(context.st_shell["initials"], "TE")
        self.assertIn("recurring", _keys(context.st_shell))


class TestShellPagesRendered(FrappeTestCase):
    """Each migrated page must expose the shell context and keep rendering."""

    def _render(self, route):
        from werkzeug.wrappers import Request
        from frappe.website.serve import get_response

        user = frappe.db.get_value("Employee", {"status": "Active", "user_id": ["is", "set"]}, "user_id")
        if not user:
            self.skipTest("no active employee with a user on this site")
        frappe.set_user(user)
        self.addCleanup(frappe.set_user, "Administrator")
        frappe.local.request = _page_request("/" + route)
        frappe.local.path = route
        return get_response(route).get_data(as_text=True)

    def test_recurring_tasks_page_renders_with_shell(self):
        html = self._render("recurring-tasks")
        self.assertIn('class="st-app"', html)
        self.assertIn('id="rt-list"', html)
        self.assertNotIn('<nav class="tnav">', html)

    def test_additional_work_page_renders_with_shell_and_form(self):
        html = self._render("additional-work")
        self.assertIn('class="st-app"', html)
        for form_id in ("aw-date", "aw-login", "aw-logout", "btn-aw-submit", "aw-history-area"):
            self.assertIn(f'id="{form_id}"', html)
        self.assertNotIn('<nav class="tnav">', html)

    def test_task_backlog_page_renders_with_shell(self):
        html = self._render("task-backlog")
        self.assertIn('class="st-app"', html)
        for marker in ('id="bl-list"', 'id="today-list"', 'class="app-footer"'):
            self.assertIn(marker, html)
        self.assertNotIn('<nav class="tnav">', html)
        self.assertNotIn('class="mob-nav"', html)

    def test_my_history_page_renders_with_shell(self):
        html = self._render("my-history")
        self.assertIn('class="st-app"', html)
        for marker in ('id="history-list"', 'id="load-more"'):
            self.assertIn(marker, html)  # original day list stays available under the List toggle
        for marker in ('id="hist-month"', 'id="hist-kpis"', 'id="hist-detail"', "st_history_calendar.js",
                       'data-st-collapsible="day detail"', 'data-hist-view="list"'):
            self.assertIn(marker, html)
        self.assertNotIn('<nav class="tnav">', html)


class TestDashboardsKeepAccessRules(FrappeTestCase):
    """Re-skinned dashboards must keep their server-side access checks."""

    def _render_as(self, user, route):
        from werkzeug.wrappers import Request
        from frappe.website.serve import get_response

        frappe.set_user(user)
        self.addCleanup(frappe.set_user, "Administrator")
        frappe.local.request = _page_request("/" + route)
        frappe.local.path = route
        return get_response(route)

    def _team_leader_user(self):
        from st_attendance_tracker.api import _is_team_leader

        for row in frappe.get_all("Employee", filters={"status": "Active", "user_id": ["is", "set"]},
                                  fields=["name", "user_id"], limit=200):
            if _is_team_leader(row.name):
                return row.user_id
        return None

    def test_team_dashboard_renders_for_team_leader(self):
        user = self._team_leader_user()
        if not user:
            self.skipTest("no team leader on this site")
        html = self._render_as(user, "team-dashboard").get_data(as_text=True)
        self.assertIn('class="st-app"', html)
        self.assertNotIn('<nav class="tnav">', html)
        # new week matrix
        for marker in ('id="tm-matrix"', 'id="tm-kpis"', "st_team_week.js", "st_employee_drawer.js", "window.ST_TEAM"):
            self.assertIn(marker, html)
        # everything the original page had must still be there
        for marker in ('id="assign-ov"', 'id="sel-date"', 'id="member-list"', 'class="filter-bar"', 'id="s-total"',
                       "function loadTeam(", "function openAssignModal(", "function submitAssignTask(", "function toggleCard(",
                       "function renderTeam(", "st_attendance_tracker.api.get_team_dashboard",
                       "st_attendance_tracker.api.assign_task_to_employee", "st_attendance_tracker.api.push_task_to_backlog",
                       "st_task_added"):
            self.assertIn(marker, html, marker)

    def test_team_dashboard_still_redirects_non_team_leader(self):
        from st_attendance_tracker.www import team_dashboard as page

        employee = frappe._dict(name="E1", employee_name="Not Lead", department="D")
        with patch("frappe.db.get_value", return_value=employee), \
                patch("frappe.session", frappe._dict(user="x@example.com")), \
                patch.object(page, "_is_team_leader", return_value=False):
            with self.assertRaises(frappe.Redirect):
                page.get_context(frappe._dict())

    def test_management_dashboard_renders_for_hr_manager(self):
        users = frappe.get_all("Has Role", filters={"role": "HR Manager", "parenttype": "User"}, pluck="parent", limit=20)
        users = [u for u in users if u != "Administrator" and frappe.db.get_value("User", u, "enabled")]
        if not users:
            self.skipTest("no HR Manager user on this site")
        html = self._render_as(users[0], "management-dashboard").get_data(as_text=True)
        self.assertIn('class="st-app"', html)
        self.assertNotIn('<nav class="tnav">', html)
        for marker in ('id="mg-kpis"', 'id="mg-chart"', 'id="mg-rows"', 'id="mg-lb"', "st_mgmt_overview.js", "window.ST_MGMT"):
            self.assertIn(marker, html)
        for marker in ('id="dept-list"', 'id="detail-panel"', 'id="leaderboard-body"', 'class="gstats"',
                       "function loadAll(", "function selectEmp(", "function renderLeaderboard(", "function renderDetail(",
                       "st_attendance_tracker.api.get_management_dashboard", "st_attendance_tracker.api.get_employee_task_detail"):
            self.assertIn(marker, html, marker)

    def test_management_dashboard_still_redirects_plain_employee(self):
        from st_attendance_tracker.www import management_dashboard as page

        with patch("frappe.get_roles", return_value=["Employee"]), \
                patch("frappe.session", frappe._dict(user="x@example.com")):
            with self.assertRaises(frappe.Redirect):
                page.get_context(frappe._dict())


class TestCollapsiblePanels(FrappeTestCase):
    def test_side_panels_are_marked_collapsible(self):
        import os

        www = os.path.join(frappe.get_app_path("st_attendance_tracker"), "www")
        for page, panel in (("additional_work.html", 'id="aw-sidebar" data-st-collapsible'),
                            ("task_backlog.html", '<aside class="rp" data-st-collapsible')):
            with open(os.path.join(www, page)) as f:
                self.assertIn(panel, f.read(), page)


class TestCheckinCalendarPage(FrappeTestCase):
    def _render(self, route):
        from werkzeug.wrappers import Request
        from frappe.website.serve import get_response

        user = frappe.db.get_value("Employee", {"status": "Active", "user_id": ["is", "set"]}, "user_id")
        if not user:
            self.skipTest("no active employee with a user on this site")
        frappe.set_user(user)
        self.addCleanup(frappe.set_user, "Administrator")
        frappe.local.request = _page_request("/" + route)
        frappe.local.path = route
        return get_response(route).get_data(as_text=True)

    def test_daily_checkin_route_serves_calendar(self):
        html = self._render("daily-checkin")
        for marker in ('id="cal-main"', 'id="cal-side"', "window.ST_CAL", 'id="st-views"', 'data-st-nav="today"',
                       "st_checkin_calendar.js", 'data-st-collapsible="today panel"', "Classic check-in"):
            self.assertIn(marker, html)

    def test_classic_route_still_serves_previous_page(self):
        html = self._render("daily-checkin-classic")
        self.assertNotIn('id="cal-main"', html)
        self.assertIn("morning", html.lower())

    def test_guest_is_sent_to_login(self):
        from st_attendance_tracker.www import checkin_calendar as page

        with patch("frappe.session", frappe._dict(user="Guest")):
            with self.assertRaises(frappe.Redirect):
                page.get_context(frappe._dict())

    def test_management_user_is_sent_to_management_dashboard(self):
        from st_attendance_tracker.www import checkin_calendar as page

        with patch("frappe.session", frappe._dict(user="boss@example.com")), \
                patch("frappe.get_roles", return_value=["Management"]):
            with self.assertRaises(frappe.Redirect):
                page.get_context(frappe._dict())
            self.assertEqual(frappe.local.flags.redirect_location, "/management-dashboard")

    def test_boot_data_has_what_the_ui_needs(self):
        from werkzeug.wrappers import Request

        user = frappe.db.get_value("Employee", {"status": "Active", "user_id": ["is", "set"]}, "user_id")
        if not user:
            self.skipTest("no active employee with a user on this site")
        from st_attendance_tracker.www import checkin_calendar as page

        frappe.set_user(user)
        self.addCleanup(frappe.set_user, "Administrator")
        context = frappe._dict()
        page.get_context(context)
        # Frappe's own web boot keys (e.g. assets_json) must survive: frappe.require()
        # on the page needs assets_json to resolve bundles such as the file uploader.
        self.assertIn("assets_json", context.boot)
        app_keys = ["employee", "today", "active_date", "is_late_checkout", "location_config",
                    "half_day_leave", "is_team_leader", "late_after", "checkout_locked", "checkout_unlock_requested",
                    "checkout_cutoff_label", "missed_checkout"]
        self.assertTrue(set(app_keys) <= set(context.boot))
        self.assertIn(context.boot["location_config"]["value"], context.boot["location_config"]["options"])


class TestRebuiltPagesKeepEverything(FrappeTestCase):
    """Additional Work, Task Backlog and Recurring Tasks: new layout, nothing from the old pages lost."""

    def _render(self, route):
        from werkzeug.wrappers import Request
        from frappe.website.serve import get_response

        user = frappe.db.get_value("Employee", {"status": "Active", "user_id": ["is", "set"]}, "user_id")
        if not user:
            self.skipTest("no active employee with a user on this site")
        frappe.set_user(user)
        self.addCleanup(frappe.set_user, "Administrator")
        frappe.local.request = _page_request("/" + route)
        frappe.local.path = route
        return get_response(route).get_data(as_text=True)

    def _assert_all(self, html, markers):
        for marker in markers:
            self.assertIn(marker, html, marker)

    def test_additional_work(self):
        html = self._render("additional-work")
        self._assert_all(html, (
            'id="ne-form"', 'id="ne-hrs"', 'class="pg-kpis"', 'data-aw-mode="multi"', "function submitSingleEntry(",
            # original multi-task form, history, summary and draft handling
            'id="aw-multi"', 'id="aw-date"', 'id="aw-login"', 'id="aw-logout"', 'id="aw-new-area"', 'id="btn-aw-submit"',
            'id="aw-history-area"', 'id="aw-load-more"', "function submitAdditionalWork(", "function addAWPG(", "function addAWTask(",
            "function deleteAWEntry(", "function loadAWHistory(", "function refreshAWSummary(", "function saveAWDraft(",
            "st_attendance_tracker.api.get_additional_work", "frappe.client.insert", "frappe.client.delete",
            'id="sum-week"', 'id="sum-month"', 'id="sum-total"', 'id="sum-done"', 'id="sum-pend"', 'class="aw-table-head"',
            # day context: check-in window and overlap hints next to the form
            'id="ne-ctx"', "function neLoadContext(", "calendar_api.get_day_context"))

    def test_task_backlog(self):
        html = self._render("task-backlog")
        self._assert_all(html, (
            'id="bl-rows"', 'id="bl-add"', 'id="bl-all"', 'data-bl-view="cards"', "window.ST_BL", "st_backlog_table.js",
            # original card view
            'id="bl-cards"', 'id="bl-list"', 'id="today-list"', 'id="btn-save-backlog"', 'class="app-footer"',
            "function loadBacklog(", "function renderCard(", "function saveNewBacklogItems(", "function onTodayDrop(",
            "st_attendance_tracker.api.get_task_backlog", "st_attendance_tracker.api.pull_backlog_item_to_today",
            "st_attendance_tracker.api.delete_backlog_item", "st_attendance_tracker.api.save_backlog_items",
            "st_attendance_tracker.api.get_today_task_summary"))

    def test_recurring_tasks(self):
        html = self._render("recurring-tasks")
        self._assert_all(html, (
            'id="rt-rows"', 'id="rt-new"', 'id="rt-week"', 'data-rt-view="cards"', "function renderRecurringTable(", "function saveTemplateDays(",
            # original cards, modal and actions
            'id="rt-cards"', 'id="rt-list"', 'id="rt-ov"', 'id="rt-daygrid"', "function openModal(", "function saveTask(", "function toggleActive(",
            "function deleteTask(", "st_attendance_tracker.api.get_recurring_tasks", "st_attendance_tracker.api.save_recurring_task",
            "st_attendance_tracker.api.delete_recurring_task"))

    def test_theme_toggle_is_in_the_shell(self):
        self._assert_all(self._render("recurring-tasks"), ('id="st-theme-btn"', "st.theme"))


class TestClassicPages(FrappeTestCase):
    """Every navigation entry has a classic (original layout) and a new design."""

    def test_every_nav_route_has_a_classic_route_and_files(self):
        import os
        from st_attendance_tracker import hooks

        routes = {rule["from_route"]: rule["to_route"] for rule in hooks.website_route_rules}
        www = os.path.join(os.path.dirname(os.path.dirname(__file__)), "www")
        for _key, _label, route, _icon, _audience in ui_shell.NAV_ITEMS:
            classic = route + ui_shell.CLASSIC_SUFFIX
            self.assertIn(classic, routes, classic)
            for ext in ("html", "py"):
                self.assertTrue(os.path.exists(os.path.join(www, f"{routes[classic]}.{ext}")), routes[classic])

    def test_new_pages_link_to_their_classic_page(self):
        ctx = ui_shell.get_shell_context("history", EMPLOYEE)
        self.assertEqual(ctx["menu_links"][0]["href"], "/my-history-classic")
        self.assertEqual(ui_shell.get_shell_context("nope", EMPLOYEE)["menu_links"], [])


class TestDefaultDesign(FrappeTestCase):
    """A user who never chose a design starts on the classic layout."""

    def _redirect_target(self, path, cookie=None, saved=None):
        from werkzeug.wrappers import Request

        frappe.set_user("Administrator")
        frappe.local.flags.redirect_location = None
        headers = {"Cookie": f"st_view={cookie}"} if cookie else {}
        frappe.local.request = Request.from_values(path, headers=headers)
        with patch.object(ui_shell, "get_design_preference", return_value=saved):
            try:
                ui_shell.redirect_for_design()
            except frappe.Redirect:
                return frappe.local.flags.redirect_location
        return None

    def test_no_choice_goes_to_classic(self):
        self.assertEqual(self._redirect_target("/daily-checkin"), "/daily-checkin-classic")

    def test_saved_new_design_is_respected(self):
        self.assertIsNone(self._redirect_target("/daily-checkin", saved="new"))

    def test_session_cookie_overrides_default(self):
        self.assertIsNone(self._redirect_target("/daily-checkin", cookie="new"))

    def test_classic_page_stays_for_default_user(self):
        self.assertIsNone(self._redirect_target("/daily-checkin-classic"))
