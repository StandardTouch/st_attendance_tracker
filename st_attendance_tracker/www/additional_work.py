import frappe
from frappe.utils import today, getdate, add_days, get_first_day_of_week

from st_attendance_tracker.ui_shell import get_shell_context, redirect_for_design


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.local.flags.redirect_location = "/login?redirect-to=/additional-work"
        raise frappe.Redirect

    redirect_for_design()

    employee = frappe.db.get_value(
        "Employee", {"user_id": frappe.session.user, "status": "Active"},
        ["name", "employee_name", "department"], as_dict=True,
    )
    if not employee:
        frappe.throw(
            "Your account is not linked to an Employee record. "
            "Please contact HR."
        )

    today_date = today()

    # ── Summary stats ──
    week_start = get_first_day_of_week(today_date)
    month_start = getdate(today_date).replace(day=1)

    week_hours = frappe.db.sql("""
        SELECT COALESCE(SUM(hours_spent), 0) FROM `tabAdditional Work`
        WHERE employee = %s AND work_date >= %s
    """, (employee.name, week_start))[0][0] or 0

    month_hours = frappe.db.sql("""
        SELECT COALESCE(SUM(hours_spent), 0) FROM `tabAdditional Work`
        WHERE employee = %s AND work_date >= %s
    """, (employee.name, month_start))[0][0] or 0

    total_entries = frappe.db.count("Additional Work", {"employee": employee.name})

    completed_count = frappe.db.count("Additional Work", {
        "employee": employee.name, "status": "Done"
    })
    pending_count = frappe.db.count("Additional Work", {
        "employee": employee.name, "status": ["in", ["Pending", "In Progress"]]
    })

    # ── Recent history (last 10 entries for server-side render) ──
    recent_entries = frappe.get_all("Additional Work",
        filters={"employee": employee.name},
        fields=["name", "work_date", "project_name", "hours_spent",
                "description", "remarks", "login_time", "logout_time", "status"],
        order_by="work_date desc, creation desc",
        page_length=10)

    context.no_cache = 1
    context.employee = employee
    context.date = today_date
    context.title = "Additional Work"
    context.st_shell = get_shell_context("additional", employee)
    context.week_start = str(week_start)
    context.month_start = str(month_start)
    context.week_hours = round(week_hours, 1)
    context.month_hours = round(month_hours, 1)
    context.total_entries = total_entries
    context.completed_count = completed_count
    context.pending_count = pending_count
    context.recent_entries = recent_entries

    tours_seen = frappe.db.get_value("ST Tour Seen", frappe.session.user, "tours_seen") or ""
    context.show_tour_additional_work = "additional_work_v1" not in tours_seen.split(",")
