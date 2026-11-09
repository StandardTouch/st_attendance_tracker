"""Emails for Additional Work, sent the same way check-in and check-out are: HR Managers and the
Team Leader get one message, the employee gets a confirmation. Entries made together ("Several
tasks") go out as one email per employee and day."""
import time

import frappe
from frappe.utils import getdate
from frappe.utils import escape_html

from st_attendance_tracker.api import (
    EMAIL_ACCENT_COLOR, EMAIL_HEADING_STYLE, EMAIL_SUBHEADING_STYLE, EMAIL_WRAPPER_STYLE,
    _employee_mail_identity, _format_action_timestamp, _format_email_date, _format_hours,
    _get_employee_email, _get_hr_manager_emails, _get_team_leader_emails, _get_work_log,
    _render_detail_table, _to_ampm,
)
from st_attendance_tracker.time_utils import time_to_minutes

# Entries saved within this many seconds of the first one share its email.
BATCH_WINDOW_SECONDS = 8
_MARKER_TTL = 120


def queue_additional_work_email(doc, method=None):
    """Additional Work after_insert."""
    if frappe.flags.in_test or frappe.flags.in_import or frappe.flags.in_migrate:
        return
    day = str(getdate(doc.work_date))
    # Atomic SET NX: only the first entry of a batch queues the email.
    cache = frappe.cache()
    if not cache.set(cache.make_key(f"st_aw_mail:{doc.employee}:{day}"), 1, nx=True, ex=_MARKER_TTL):
        return
    frappe.enqueue(
        send_additional_work_emails,
        queue="short",
        enqueue_after_commit=True,
        employee_name=doc.employee,
        work_date=day,
    )


def send_additional_work_emails(employee_name, work_date, wait=BATCH_WINDOW_SECONDS):
    try:
        if wait:
            time.sleep(wait)  # let the rest of a multi-task submit land first
        employee = frappe.db.get_value(
            "Employee", employee_name, ["name", "employee_name", "department"], as_dict=True
        )
        entries = frappe.get_all(
            "Additional Work",
            filters={"employee": employee_name, "work_date": work_date},
            fields=["name", "login_time", "logout_time", "project_name", "hours_spent", "status", "description", "remarks", "creation"],
            order_by="login_time asc, creation asc",
        )
        if not employee or not entries:
            return

        # Fetch attachments for all entries
        entry_names = [e.name for e in entries if e.name]
        files_by_entry = {}
        if entry_names:
            files = frappe.get_all(
                "File",
                filters={"attached_to_doctype": "Additional Work", "attached_to_name": ["in", entry_names]},
                fields=["name", "file_name", "file_url", "attached_to_name"],
            )
            for f in files:
                files_by_entry.setdefault(f.attached_to_name, []).append(f)
        for e in entries:
            e["attachments"] = files_by_entry.get(e.name, [])

        # Attachment specs for MIME attachments
        attachment_specs = [{"fid": f["name"]} for e in entries for f in e.get("attachments", [])]

        work_log = _get_work_log(employee_name, work_date)
        label = getdate(work_date).strftime("%d-%m-%Y")
        subject = f"{employee.employee_name} - Additional Work - {label}"
        sender, reply_to = _employee_mail_identity(employee.name, employee.employee_name)

        dept_format = frappe.db.get_value("Department", employee.department, "task_summary_email_format") if employee.department else None

        team_leaders = _get_team_leader_emails(employee.name)
        hr_emails = _get_hr_manager_emails()
        own = _get_employee_email(employee.name)

        to_recipients = list(dict.fromkeys(([own] if own else []) + team_leaders))
        cc_recipients = [hr for hr in hr_emails if hr not in to_recipients]

        if to_recipients or cc_recipients:
            recipients = to_recipients if to_recipients else cc_recipients
            cc = cc_recipients if to_recipients else []
            html = _render(employee, work_date, entries, work_log, heading=subject, dept_format=dept_format)
            frappe.sendmail(
                recipients=recipients,
                cc=cc,
                subject=subject,
                message=html,
                now=False,
                sender=sender,
                reply_to=reply_to,
                attachments=attachment_specs,
            )
    except Exception as e:
        frappe.log_error(f"Additional work email failed for {employee_name} ({work_date}): {e}", "ST Attendance Tracker")


def _render(employee, work_date, entries, work_log, heading, dept_format=None):
    total = sum(float(e.hours_spent or 0) for e in entries)
    starts = [e.login_time for e in entries if e.login_time]
    ends = [e.logout_time for e in entries if e.logout_time]
    window = (
        f"{_to_ampm(min(starts, key=time_to_minutes))} - {_to_ampm(max(ends, key=time_to_minutes))}"
        if starts and ends else None
    )
    on_leave = not (work_log and work_log.eod_submitted)
    detail = _render_detail_table([
        ("Employee", employee.employee_name),
        ("Date", _format_email_date(work_date)),
        ("Day Type", "On leave" if on_leave else "Worked, checked out"),
        ("Login Time", _to_ampm(work_log.login_time) if work_log and work_log.login_time else None),
        ("Logout Time", _to_ampm(work_log.logout_time) if work_log and work_log.logout_time else None),
        ("Work Location", work_log.work_location if work_log else None),
        ("Net Working Hours", work_log.net_hours if work_log else None),
        ("Additional Work Window", window),
        ("Entries", str(len(entries))),
        ("Total Additional Hours", _format_hours(total) or "0h"),
        ("Logged At", _format_action_timestamp(max(e.creation for e in entries)) if all(e.get("creation") for e in entries) else None),
    ])

    if dept_format == "Grouped List":
        body_html = _render_grouped_list(entries)
    else:
        body_html = _render_table(entries)

    return (
        f'<div style="{EMAIL_WRAPPER_STYLE}"><div style="{EMAIL_HEADING_STYLE}">{escape_html(heading)}</div>{detail}'
        f'<div style="{EMAIL_SUBHEADING_STYLE}">Additional Work Summary</div>{body_html}</div>'
    )


def _render_table(entries):
    """Same look as the task table in the check-out email: accent header row, bordered cells,
    project name shown once at the start of each project's rows."""
    border = "1px solid #e2e8f0"
    head = (
        f"padding:12px 10px;text-align:{{align}};border:{border};background-color:{EMAIL_ACCENT_COLOR};"
        "color:#ffffff;font-size:12.5px;font-weight:bold"
    )
    cell = f"padding:10px 12px;font-size:13px;color:#334155;border:{border};vertical-align:top;word-wrap:break-word"
    centred = f"padding:10px 12px;font-size:12px;color:#475569;border:{border};text-align:center;vertical-align:middle"
    columns = (("Client", "left", "20%"), ("Task / Work", "left", "36%"), ("Time", "center", "20%"),
               ("Hours", "center", "12%"), ("Status", "center", "12%"))
    header = "".join(
        f'<th style="{head.format(align=align)};width:{width}">{label}</th>' for label, align, width in columns
    )
    rows, seen = [], set()
    for e in entries:
        project = (e.project_name or "").strip() or "General"
        first = project not in seen
        seen.add(project)
        project_cell = (
            f'<td style="{cell};font-weight:bold;color:{EMAIL_ACCENT_COLOR}">{escape_html(project) if first else ""}</td>'
        )
        work = f"{escape_html(e.description or '')}"
        if e.remarks:
            work += f'<div style="color:#6b7280;font-size:12px;margin-top:4px">Remark: {escape_html(e.remarks)}</div>'
        att_names = [escape_html(f["file_name"]) for f in (e.get("attachments") or [])]
        if att_names:
            work += f'<div style="font-size:11.5px;color:#64748b;margin-top:4px">📎 {", ".join(att_names)}</div>'

        rows.append(
            f"<tr>{project_cell}<td style=\"{cell}\">{work}</td>"
            f"<td style=\"{centred}\">{escape_html(_window(e))}</td>"
            f"<td style=\"{centred}\">{escape_html(_format_hours(e.hours_spent) or '-')}</td>"
            f"<td style=\"{centred}\">{escape_html(e.status or '')}</td></tr>"
        )
    return (
        '<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin-top:16px;'
        f"font-family:'Segoe UI', Arial, sans-serif;border:{border};table-layout:fixed\">"
        f"<thead><tr>{header}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def _render_grouped_list(entries):
    """Grouped List layout matching department view for Grouped List email format."""
    groups = {}
    for e in entries:
        pname = (e.project_name or "").strip() or "General"
        groups.setdefault(pname, []).append(e)

    sorted_group_names = list(groups.keys())
    FONT = "font-family:'Segoe UI',Arial,sans-serif"
    WRAP = f"{FONT};line-height:1.6;color:#1f2937;margin-top:16px"
    PROJ_HEAD = (
        f"{FONT};font-size:15px;font-weight:700;color:#1e293b;"
        "padding:0 0 5px 0;margin:0 0 8px 0;"
        "border-bottom:1px solid #e2e8f0"
    )
    TASK_LINE = f"{FONT};font-size:14px;color:#334155;margin:0;padding:0;white-space:pre-wrap"
    META_LINE = f"{FONT};font-size:12px;color:#64748b;margin:2px 0 0 18px;padding:0"

    group_blocks = []
    for pname in sorted_group_names:
        lines = []
        for idx, e in enumerate(groups[pname]):
            desc = escape_html(e.description or "").replace('\n', '<br>')
            num = idx + 1
            task_html = f'<p style="{TASK_LINE}">&nbsp;&nbsp;{num}. {desc}</p>'

            meta_parts = []
            if e.status:
                if e.status == "Done":
                    meta_parts.append("✔ Done")
                else:
                    meta_parts.append(f"○ {escape_html(e.status)}")

            win = _window(e)
            if win and win != "-":
                meta_parts.append(f"Time: {escape_html(win)}")

            hrs = _format_hours(e.hours_spent)
            if hrs:
                meta_parts.append(f"Hours: {escape_html(hrs)}")

            if e.remarks:
                meta_parts.append(f"Remark: {escape_html(e.remarks)}")

            att_names = [escape_html(f["file_name"]) for f in (e.get("attachments") or [])]
            if att_names:
                meta_parts.append(f'📎 {", ".join(att_names)}')

            if meta_parts:
                task_html += f'<p style="{META_LINE}">{" · ".join(meta_parts)}</p>'

            lines.append(task_html)

        task_count = len(groups[pname])
        count_note = f' — {task_count} task{"s" if task_count != 1 else ""}'
        project_block = (
            f'<div style="margin:0 0 18px 0">'
            f'<p style="{PROJ_HEAD}">'
            f'{escape_html(pname)}'
            f'<span style="font-weight:400;font-size:12px;color:#94a3b8">{count_note}</span></p>'
            f'{"".join(lines)}'
            f'</div>'
        )
        group_blocks.append(project_block)

    return f'<div style="{WRAP}">{"".join(group_blocks)}</div>'


def _window(entry):
    start, end = _to_ampm(entry.login_time), _to_ampm(entry.logout_time)
    return f"{start} - {end}" if start and end else (start or end or "-")
