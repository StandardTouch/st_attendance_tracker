app_name = "st_attendance_tracker"
app_title = "ST Attendance Tracker"
app_publisher = "StandardTouch e-Solutions"
app_description = "Daily Attendance & Task Management for ERPNext v15"
app_email = "ameer@standardtouch.com"
app_license = "MIT"

# ── Desk branding polish (spacing/radius/font only, no color changes) ─────────
app_include_css = ["/assets/st_attendance_tracker/css/branding.css"]
app_include_js = ["/assets/st_attendance_tracker/js/st_attendance_tracker.js"]

# ── Shared design tokens for the www/ portal pages (app_include_css only
# reaches Desk, not website pages, so this needs its own include) ────────────
web_include_css = ["/assets/st_attendance_tracker/css/st_tracker_shared.css"]

# ── Custom fields on Employee doctype ─────────────────────────────────────────
# Automatically created on bench migrate
after_migrate = ["st_attendance_tracker.setup.create_custom_fields"]

# ── Employee validation ────────────────────────────────────────────────────────
doc_events = {
    "Employee": {
        "validate": "st_attendance_tracker.setup.validate_department_assignments",
        "on_update": "st_attendance_tracker.setup.sync_team_lead_role",
        "on_trash": "st_attendance_tracker.setup.sync_team_lead_role",
    },
    "ST Attendance Settings": {
        "on_update": "st_attendance_tracker.api.clear_attendance_settings_cache",
    },
    "Employee Checkin": {
        "after_insert": "st_attendance_tracker.api.notify_external_checkin",
    },
}


has_permission = {
    "Daily Work Log": "st_attendance_tracker.permissions.has_permission_daily_work_log",
}

permission_query_conditions = {
    "Daily Work Log": "st_attendance_tracker.permissions.daily_work_log_query_conditions",
}


# ── Scheduled Jobs ─────────────────────────────────────────────────────────────
scheduler_events = {
    "cron": {
        # 10:30 AM IST Mon-Sat
        "30 10 * * 1-6": ["st_attendance_tracker.tasks.send_employee_checkin_reminder"],
        # 11:30 AM IST Mon-Sat
        "30 11 * * 1-6": ["st_attendance_tracker.tasks.send_morning_combined_report"],
        # 10:00 PM IST Mon-Sat (22:00)
        "0 22 * * 1-6":  ["st_attendance_tracker.tasks.send_eod_missing_report"],
        # 9:00 PM IST Mon-Sat
        "0 21 * * 1-6":  ["st_attendance_tracker.tasks.send_employee_checkout_reminder"],
    }
}


# ── Web routes ─────────────────────────────────────────────────────────────────
website_route_rules = [
    {"from_route": "/daily-checkin",        "to_route": "checkin_calendar"},
    {"from_route": "/daily-checkin-classic", "to_route": "daily_checkin"},
    {"from_route": "/team-dashboard",       "to_route": "team_dashboard"},
    {"from_route": "/management-dashboard", "to_route": "management_dashboard"},
    {"from_route": "/my-history",           "to_route": "my_history"},
    {"from_route": "/recurring-tasks",      "to_route": "recurring_tasks"},
    {"from_route": "/additional-work",      "to_route": "additional_work"},
    {"from_route": "/task-backlog",         "to_route": "task_backlog"},
    # Original layouts, branded; every page has a classic and a new design.
    {"from_route": "/my-history-classic",           "to_route": "my_history_classic"},
    {"from_route": "/recurring-tasks-classic",      "to_route": "recurring_tasks_classic"},
    {"from_route": "/additional-work-classic",      "to_route": "additional_work_classic"},
    {"from_route": "/task-backlog-classic",         "to_route": "task_backlog_classic"},
    {"from_route": "/team-dashboard-classic",       "to_route": "team_dashboard_classic"},
    {"from_route": "/management-dashboard-classic", "to_route": "management_dashboard_classic"},
]
