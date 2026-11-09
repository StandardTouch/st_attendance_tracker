"""
Scheduled email tasks for ST Attendance Tracker.
  10:30 AM IST Mon-Sat → send_employee_checkin_reminder   (to employees who haven't checked in)
  11:30 AM IST Mon-Sat → send_morning_combined_report     (to HR: missing + late summary)
  10:00 PM IST Mon-Sat → send_eod_missing_report          (to HR: employees without EOD)
  9:00 PM IST Mon-Sat  → send_employee_checkout_reminder  (to employees who haven't checked out)

HR reports go to all users with HR Manager role.
Employee reminders go directly to each employee's work email.
"""

from datetime import timedelta

import frappe
from frappe.utils import today, getdate, now_datetime
from st_attendance_tracker.api import _to_ampm, _to_hhmm, _get_employees_on_leave, _get_hr_manager_emails
from st_attendance_tracker.time_utils import get_checkout_cutoff


# ─────────────────────────────────────────────────────────────────────────────
# EMPLOYEE REMINDER — 10:30 AM
# ─────────────────────────────────────────────────────────────────────────────

def send_employee_checkin_reminder():
    """
    Sent at 10:30 AM IST Mon-Sat.
    Emails each employee individually who hasn't checked in today.
    """
    date = today()
    if _skip_if_not_due((10, 30)) or _already_sent_today("last_checkin_reminder_date", date):
        return
    day_label = getdate(date).strftime("%A, %d %B %Y")

    expected = _get_expected_employees(date)
    if not expected:
        return

    emp_names = [e.name for e in expected]
    checked_in = {r.employee for r in frappe.get_all("Daily Work Log", filters={
        "date": date,
        "morning_submitted": 1,
        "employee": ["in", emp_names],
    }, fields=["employee"])}

    not_checked = [e for e in expected if e.name not in checked_in]
    if not not_checked:
        return

    for emp in not_checked:
        # Get employee's personal email or company email
        emp_email = frappe.db.get_value("Employee", emp.name,
            ["prefered_email", "company_email", "personal_email"], as_dict=True)

        email = (
            emp_email.get("prefered_email") or
            emp_email.get("company_email") or
            emp_email.get("personal_email")
        )

        # Also try user account email
        if not email:
            user_id = frappe.db.get_value("Employee", emp.name, "user_id")
            if user_id:
                email = frappe.db.get_value("User", user_id, "email") or user_id

        if not email:
            continue

        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto">
          <div style="background:#EE1C29;padding:20px 24px;border-radius:6px 6px 0 0">
            <h2 style="color:#fff;margin:0;font-size:18px">Check-In Reminder</h2>
            <p style="color:rgba(255,255,255,0.8);margin:5px 0 0;font-size:13px">
              StandardTouch e-Solutions · Attendance System
            </p>
          </div>
          <div style="background:#fff;padding:20px 24px;border:1px solid #e0e0e0;
                      border-top:none;border-radius:0 0 6px 6px">
            <p style="font-size:15px;color:#111;margin:0 0 10px">
              Hi <strong>{emp.employee_name}</strong>,
            </p>
            <p style="font-size:13px;color:#444;line-height:1.6;margin:0 0 16px">
              You have not checked in for today — <strong>{day_label}</strong>.
            </p>
            <p style="font-size:13px;color:#444;line-height:1.6;margin:0 0 20px">
              Please check in as soon as possible by visiting the attendance portal.
            </p>
            <a href="/daily-checkin"
               style="display:inline-block;background:#EE1C29;color:#fff;
                      padding:10px 22px;border-radius:6px;text-decoration:none;
                      font-size:13px;font-weight:500">
              Check In Now →
            </a>
          </div>
          <p style="font-size:11px;color:#aaa;text-align:center;margin-top:10px">
            Automated reminder · StandardTouch Attendance System · Do not reply
          </p>
        </div>"""

        frappe.sendmail(
            recipients=[email],
            subject=f"Reminder: You have not checked in today ({date})",
            message=html,
            now=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# MORNING HR REPORT — 11:30 AM
# ─────────────────────────────────────────────────────────────────────────────

def send_morning_combined_report():
    """
    Combined report: Section 1 = not checked in, Section 2 = checked in late.
    Sent at 11:30 AM IST Mon-Sat to all HR Manager role users.
    """
    date = today()
    if _skip_if_not_due((11, 30)) or _already_sent_today("last_morning_report_date", date):
        return
    expected = _get_expected_employees(date)
    if not expected:
        return

    emp_names = [e.name for e in expected]

    morning_logs = frappe.get_all("Daily Work Log", filters={
        "date": date,
        "morning_submitted": 1,
        "employee": ["in", emp_names],
    }, fields=["employee", "login_time", "is_late"])

    checked_in     = {l.employee for l in morning_logs}
    late_employees = [l for l in morning_logs if l.is_late]
    missing        = [e for e in expected if e.name not in checked_in]

    if not missing and not late_employees:
        return

    late_emp_map = {l.employee: _to_hhmm(l.login_time) for l in late_employees}

    missing_rows = ""
    for i, emp in enumerate(
        sorted(missing, key=lambda e: (e.department or "", e.employee_name)), 1
    ):
        bg = "#ffffff" if i % 2 == 0 else "#fafafa"
        missing_rows += f"""
        <tr style="background:{bg}">
          <td style="padding:8px 10px;border-bottom:1px solid #eee">{i}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #eee;font-weight:500">{emp.employee_name}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #eee;color:#666">{emp.department or "—"}</td>
        </tr>"""

    late_emp_objs = [e for e in expected if e.name in late_emp_map]
    late_rows = ""
    for i, emp in enumerate(
        sorted(late_emp_objs, key=lambda e: (e.department or "", e.employee_name)), 1
    ):
        bg = "#ffffff" if i % 2 == 0 else "#fffbeb"
        late_rows += f"""
        <tr style="background:{bg}">
          <td style="padding:8px 10px;border-bottom:1px solid #eee">{i}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #eee;font-weight:500">{emp.employee_name}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #eee;color:#666">{emp.department or "—"}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #eee;color:#d97706;font-weight:500">{late_emp_map.get(emp.name, "—")}</td>
        </tr>"""

    missing_section = ""
    if missing:
        missing_section = f"""
        <h3 style="font-size:14px;color:#131419;margin:0 0 10px">
          Not checked in &nbsp;<span style="background:#fee2e2;color:#991b1b;
          font-size:12px;padding:2px 8px;border-radius:20px">{len(missing)}</span>
        </h3>
        <table width="100%" cellpadding="0" cellspacing="0"
               style="border-collapse:collapse;border:1px solid #ddd;border-radius:4px;margin-bottom:20px">
          <thead>
            <tr style="background:#131419">
              <th style="padding:9px 10px;text-align:left;color:#fff;font-size:12px;width:36px">#</th>
              <th style="padding:9px 10px;text-align:left;color:#fff;font-size:12px">Employee</th>
              <th style="padding:9px 10px;text-align:left;color:#fff;font-size:12px">Department</th>
            </tr>
          </thead>
          <tbody>{missing_rows}</tbody>
        </table>"""

    late_section = ""
    if late_employees:
        late_section = f"""
        <h3 style="font-size:14px;color:#131419;margin:0 0 10px">
          Late check-ins &nbsp;<span style="background:#fef9c3;color:#854d0e;
          font-size:12px;padding:2px 8px;border-radius:20px">{len(late_employees)}</span>
        </h3>
        <table width="100%" cellpadding="0" cellspacing="0"
               style="border-collapse:collapse;border:1px solid #ddd;border-radius:4px;margin-bottom:20px">
          <thead>
            <tr style="background:#131419">
              <th style="padding:9px 10px;text-align:left;color:#fff;font-size:12px;width:36px">#</th>
              <th style="padding:9px 10px;text-align:left;color:#fff;font-size:12px">Employee</th>
              <th style="padding:9px 10px;text-align:left;color:#fff;font-size:12px">Department</th>
              <th style="padding:9px 10px;text-align:left;color:#fff;font-size:12px">Check-In Time</th>
            </tr>
          </thead>
          <tbody>{late_rows}</tbody>
        </table>"""

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:620px;margin:0 auto">
      <div style="background:#EE1C29;padding:20px 24px;border-radius:6px 6px 0 0">
        <h2 style="color:#fff;margin:0;font-size:18px">Morning Attendance Report</h2>
        <p style="color:rgba(255,255,255,0.8);margin:5px 0 0;font-size:13px">
          As of 11:30 AM &nbsp;·&nbsp; {date} &nbsp;·&nbsp; StandardTouch e-Solutions
        </p>
      </div>
      <div style="background:#fff;padding:20px 24px;border:1px solid #e0e0e0;
                  border-top:none;border-radius:0 0 6px 6px">
        {missing_section}
        {late_section}
      </div>
      <p style="font-size:11px;color:#aaa;text-align:center;margin-top:10px">
        Automated report · StandardTouch Attendance System · Do not reply
      </p>
    </div>"""

    _send_to_hr_managers(
        subject=f"[{date}] Morning Report — {len(missing)} missing, {len(late_employees)} late",
        message=html,
    )


# ─────────────────────────────────────────────────────────────────────────────
# EOD HR REPORT — 10:00 PM
# ─────────────────────────────────────────────────────────────────────────────

def send_eod_missing_report():
    """EOD missing report sent at 10:00 PM IST Mon-Sat to all HR Manager role users."""
    date = today()
    if _skip_if_not_due((22, 0)) or _already_sent_today("last_eod_missing_report_date", date):
        return
    expected = _get_expected_employees(date)
    if not expected:
        return
    emp_names = [e.name for e in expected]

    submitted = {r.employee for r in frappe.get_all("Daily Work Log", filters={
        "date": date,
        "eod_submitted": 1,
        "employee": ["in", emp_names],
    }, fields=["employee"])}

    missing = [e for e in expected if e.name not in submitted]
    if not missing:
        return

    rows = ""
    for i, emp in enumerate(
        sorted(missing, key=lambda e: (e.department or "", e.employee_name)), 1
    ):
        bg = "#ffffff" if i % 2 == 0 else "#fafafa"
        rows += f"""
        <tr style="background:{bg}">
          <td style="padding:8px 10px;border-bottom:1px solid #eee">{i}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #eee;font-weight:500">{emp.employee_name}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #eee;color:#666">{emp.department or "—"}</td>
        </tr>"""

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:620px;margin:0 auto">
      <div style="background:#131419;padding:20px 24px;border-radius:6px 6px 0 0">
        <h2 style="color:#fff;margin:0;font-size:18px">EOD Report Missing</h2>
        <p style="color:rgba(255,255,255,0.6);margin:5px 0 0;font-size:13px">
          As of 10:00 PM &nbsp;·&nbsp; {date} &nbsp;·&nbsp; StandardTouch e-Solutions
        </p>
      </div>
      <div style="background:#fff;padding:20px 24px;border:1px solid #e0e0e0;
                  border-top:none;border-radius:0 0 6px 6px">
        <p style="font-size:13px;color:#333;margin:0 0 14px">
          The following employees have not submitted their End-of-Day report.
        </p>
        <table width="100%" cellpadding="0" cellspacing="0"
               style="border-collapse:collapse;border:1px solid #ddd;border-radius:4px">
          <thead>
            <tr style="background:#131419">
              <th style="padding:9px 10px;text-align:left;color:#fff;font-size:12px;width:36px">#</th>
              <th style="padding:9px 10px;text-align:left;color:#fff;font-size:12px">Employee</th>
              <th style="padding:9px 10px;text-align:left;color:#fff;font-size:12px">Department</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
        <p style="margin:14px 0 0;font-size:13px;color:#444">
          Total missing: <strong>{len(missing)}</strong>
        </p>
      </div>
      <p style="font-size:11px;color:#aaa;text-align:center;margin-top:10px">
        Automated report · StandardTouch Attendance System · Do not reply
      </p>
    </div>"""

    _send_to_hr_managers(
        subject=f"[{date}] EOD Report Missing — {len(missing)} employee(s)",
        message=html,
    )


# ─────────────────────────────────────────────────────────────────────────────
# EMPLOYEE CHECKOUT REMINDER — 9:00 PM
# ─────────────────────────────────────────────────────────────────────────────

def send_employee_checkout_reminder():
    """
    Sent at 9:00 PM IST Mon-Sat.
    Emails each employee individually who checked in today but hasn't checked out yet.
    """
    date = today()
    if _skip_if_not_due((21, 0)) or _already_sent_today("last_checkout_reminder_date", date):
        return
    day_label = getdate(date).strftime("%A, %d %B %Y")
    cutoff_minutes = get_checkout_cutoff()
    cutoff_label = _to_ampm("%02d:%02d:00" % divmod(cutoff_minutes, 60))

    expected = _get_expected_employees(date)
    if not expected:
        return
    emp_names = [e.name for e in expected]

    work_logs = frappe.get_all("Daily Work Log", filters={
        "date": date,
        "employee": ["in", emp_names],
    }, fields=["employee", "morning_submitted", "eod_submitted"])

    checked_in = {r.employee for r in work_logs if r.morning_submitted}
    checked_out = {r.employee for r in work_logs if r.eod_submitted}

    pending = [e for e in expected if e.name in checked_in and e.name not in checked_out]
    if not pending:
        return

    for emp in pending:
        emp_email = frappe.db.get_value("Employee", emp.name,
            ["prefered_email", "company_email", "personal_email"], as_dict=True)

        email = (
            emp_email.get("prefered_email") or
            emp_email.get("company_email") or
            emp_email.get("personal_email")
        )

        if not email:
            user_id = frappe.db.get_value("Employee", emp.name, "user_id")
            if user_id:
                email = frappe.db.get_value("User", user_id, "email") or user_id

        if not email:
            continue

        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto">
          <div style="background:#EE1C29;padding:20px 24px;border-radius:6px 6px 0 0">
            <h2 style="color:#fff;margin:0;font-size:18px">Checkout Reminder</h2>
            <p style="color:rgba(255,255,255,0.8);margin:5px 0 0;font-size:13px">
              StandardTouch e-Solutions · Attendance System
            </p>
          </div>
          <div style="background:#fff;padding:20px 24px;border:1px solid #e0e0e0;
                      border-top:none;border-radius:0 0 6px 6px">
            <p style="font-size:15px;color:#111;margin:0 0 10px">
              Hi <strong>{emp.employee_name}</strong>,
            </p>
            <p style="font-size:13px;color:#444;line-height:1.6;margin:0 0 16px">
              You checked in today — <strong>{day_label}</strong> — but haven't checked out yet.
            </p>
            <p style="font-size:13px;color:#444;line-height:1.6;margin:0 0 16px">
              Please check out before {cutoff_label}. After that, checkout is locked and you will need late checkout approval from HR.
            </p>
            <p style="font-size:13px;color:#444;line-height:1.6;margin:0 0 20px">
              If you are continuing to work beyond your regular hours, please check out now and log the remaining work under Additional Work.
            </p>
            <a href="/daily-checkin"
               style="display:inline-block;background:#EE1C29;color:#fff;
                      padding:10px 22px;border-radius:6px;text-decoration:none;
                      font-size:13px;font-weight:500">
              Check Out Now →
            </a>
          </div>
          <p style="font-size:11px;color:#aaa;text-align:center;margin-top:10px">
            Automated reminder · StandardTouch Attendance System · Do not reply
          </p>
        </div>"""

        frappe.sendmail(
            recipients=[email],
            subject=f"Reminder: You have not checked out today ({date})",
            message=html,
            now=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _skip_if_not_due(scheduled_time, grace_minutes=1):
    """True if called meaningfully earlier than `scheduled_time` (hour, minute) —
    a sign of a spurious early re-fire (e.g. a worker restart/redeploy re-queuing
    the job) rather than the real scheduled run, which Frappe's own scheduler
    already gates from firing early under normal operation. Small grace window
    absorbs ordinary scheduler tick jitter."""
    hour, minute = scheduled_time
    due_at = now_datetime().replace(hour=hour, minute=minute, second=0, microsecond=0)
    return now_datetime() < due_at - timedelta(minutes=grace_minutes)


def _already_sent_today(field_name, date):
    """Lock ST Attendance Settings and check/mark a scheduled job as sent
    for `date`. Returns True if already sent (caller should skip); otherwise
    marks it sent and returns False.

    Guards against the scheduler firing a job twice the same day (a worker
    restart/redeploy can re-queue a job before its last_execution commits) —
    the row lock serializes concurrent fires instead of racing on a plain
    check, and the marker write rides in the same transaction the scheduler
    wrapper commits after the job returns / rolls back if it raises, so a
    failed send never gets falsely marked sent.
    """
    frappe.db.sql(
        "select value from `tabSingles` where doctype = %s for update",
        ("ST Attendance Settings",),
    )
    if frappe.db.get_single_value("ST Attendance Settings", field_name) == getdate(date):
        return True
    frappe.db.set_single_value("ST Attendance Settings", field_name, date)
    return False


def _get_expected_employees(date):
    """Active employees excluding: approved leave (Leave Application or an
    Attendance record already marked "On Leave"), a Leave Application for
    today in ANY non-rejected/non-cancelled state — including still-Draft/
    Open, awaiting approval — holiday today, or a User with the
    "Management" role (oversight-only; excluded regardless of whether that
    User also happens to have their own Employee record, so this is a real
    role check, not just "no Employee record to find")."""
    all_employees = frappe.get_all("Employee",
        filters={"status": "Active"},
        fields=["name", "employee_name", "department", "holiday_list", "user_id"],
    )
    emp_names = [e.name for e in all_employees]
    on_leave = _get_employees_on_leave(emp_names, date)
    leave_requested = _get_employees_with_leave_requested(emp_names, date)
    # Deliberately not frappe.utils.user.get_users_with_role: that helper
    # also drops disabled users and Administrator, which would put a
    # disabled-but-still-Active-Employee Management user back into the
    # reminder pool. This exemption is a role check only, independent of
    # the User account's enabled state.
    management_users = set(frappe.get_all(
        "Has Role", filters={"role": "Management", "parenttype": "User"}, pluck="parent"
    ))

    result = []
    for emp in all_employees:
        if emp.name in on_leave or emp.name in leave_requested:
            continue
        if _is_holiday(emp.holiday_list, date):
            continue
        if emp.user_id and emp.user_id in management_users:
            continue
        result.append(emp)
    return result


def _get_employees_with_leave_requested(employee_names, date):
    """Employee names with a Leave Application covering `date` in any state
    except Rejected/Cancelled — including a still-Draft (docstatus 0) or
    Open-awaiting-approval request. Deliberately broader than
    api._get_employees_on_leave (Approved + submitted only, what dashboards
    use for the "on leave" badge): a pending request is reason enough to
    stop nagging someone with check-in/checkout reminders or flagging them
    "missing" in the HR report before HR has even acted on it.
    """
    if not employee_names:
        return set()
    return set(frappe.get_all("Leave Application", filters={
        "employee": ["in", employee_names],
        "from_date": ["<=", date],
        "to_date": [">=", date],
        "status": ["not in", ["Rejected", "Cancelled"]],
        "docstatus": ["!=", 2],
    }, pluck="employee"))


def _is_holiday(holiday_list_name, date):
    if not holiday_list_name:
        return False
    return bool(frappe.db.exists("Holiday", {
        "parent":       holiday_list_name,
        "holiday_date": getdate(date),
    }))


def _send_to_hr_managers(subject, message):
    """Fetch all enabled users with HR Manager role and send email."""
    emails = _get_hr_manager_emails()

    if not emails:
        frappe.log_error(
            "ST Attendance Tracker: No users with HR Manager role found. "
            "Report not sent. Please assign HR Manager role to at least one user.",
            "ST Attendance Tracker — Report"
        )
        return

    frappe.sendmail(
        recipients=emails,
        subject=subject,
        message=message,
        now=True,
    )