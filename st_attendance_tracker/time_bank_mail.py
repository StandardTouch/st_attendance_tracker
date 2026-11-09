"""Extra hours in the checkout email: the full box for the employee, one line for everyone else.
Wording is kept plain: "extra hours" are overtime and additional work saved for later, and "used as
time off" means counted as worked time on a later day."""
from html import escape, unescape

from frappe.utils import getdate

from st_attendance_tracker.api import _format_hours, _parse_time_to_hours


def _h(hours):
    return _format_hours(hours) or "0m"


def _date(value):
    return getdate(value).strftime("%d %b %Y")


def _rows(rows, border):
    return "".join(
        f'<tr><td style="padding:7px 12px;font-size:13px;color:#1f2937;border-bottom:1px solid {border}">{label}</td>'
        f'<td style="padding:7px 12px;font-size:13px;font-weight:700;color:#1f2937;text-align:right;white-space:nowrap;'
        f'border-bottom:1px solid {border}">{value}</td></tr>' for label, value in rows)


def _table(rows, border):
    return (f'<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;border:1px solid {border};'
            f'border-radius:6px;background:#ffffff"><tbody>{_rows(rows, border)}</tbody></table>')


def _box(title, intro, body, tone):
    colour = {"green": ("#f0fdf4", "#15803d", "#14532d", "#bbf7d0"), "blue": ("#eff6ff", "#1d4ed8", "#1e3a8a", "#bfdbfe")}[tone]
    return (f'<div style="background:{colour[0]};border:2px solid {colour[1]};border-radius:8px;padding:16px 18px;margin:20px 0 0;'
            f"font-family:'Segoe UI', Arial, sans-serif\">"
            f'<div style="font-size:16px;font-weight:bold;color:{colour[2]};margin-bottom:4px">{title}</div>'
            f'<div style="font-size:13px;line-height:1.5;color:{colour[2]};margin-bottom:10px">{intro}</div>{body}</div>'), colour


def full_block(summary):
    """The box the employee sees. Used and earned can both appear on one day."""
    parts = []
    used, earned = summary["used_hours"], summary["earned_hours"]
    if used > 0:
        net_hours = _parse_time_to_hours(summary["net_hours_text"])
        counted = net_hours + used
        counted_label = _h(counted) + (" · Full day" if counted + 1e-6 >= summary["full_day_hours"] else "")
        came_from = "".join(
            f'<tr><td style="padding:7px 12px;font-size:13px;color:#1f2937;border-bottom:1px solid #bbf7d0">'
            f'{escape(_date(i["date"]))} · {escape(unescape(i["label"]))}</td>'
            f'<td style="padding:7px 12px;font-size:13px;font-weight:700;color:#1f2937;text-align:right;white-space:nowrap;'
            f'border-bottom:1px solid #bbf7d0">{_h(i["hours"])}</td></tr>' for i in summary["items"])
        body = (
            _table([("Net working hours today", _h(net_hours)), ("Extra hours used", "+ " + _h(used)), ("<b>Counted as</b>", counted_label)], "#bbf7d0")
            + '<div style="font-size:13px;font-weight:bold;color:#14532d;margin:14px 0 6px">Where these hours came from</div>'
            + f'<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;border:1px solid #bbf7d0;border-radius:6px;background:#ffffff"><tbody>{came_from}</tbody></table>'
        )
        box, _c = _box(f"Extra hours used today: {_h(used)}",
                       f"{_h(used)} of earlier extra work was used as time off today. These hours count as worked time, so a short day can still count as a full day.",
                       body, "green")
        parts.append(box)
    if earned > 0:
        box, _c = _box(f"Extra hours earned today: {_h(earned)}",
                       "Your task hours were more than a full day, so the extra was saved as extra hours. Overtime is counted from task hours, "
                       "not from the time you spent at work. You can use these hours as time off from tomorrow, at check-out. No salary is paid for overtime.",
                       "", "blue")
        parts.append(box)
    left = (f'<div style="margin-top:12px;font-size:14px;color:#1f2937;font-family:\'Segoe UI\', Arial, sans-serif">'
            f'<span style="display:inline-block;background:#1f2937;color:#ffffff;font-size:12px;font-weight:bold;border-radius:999px;padding:2px 10px">{_h(summary["left_hours"])}</span> '
            f'<b>Extra hours left</b>' + (" (the hours earned today can be used from tomorrow)" if earned > 0 else ". Use them on any later day, at check-out.") + '</div>')
    note = (f'<div style="margin-top:6px;font-size:12px;color:#64748b">Extra hours can be used for {summary["expiry_days"]} days after the day they were worked.'
            + (f' The earliest ones run out on {escape(_date(summary["next_expiry"]))}.' if summary["next_expiry"] else "") + "</div>")
    return "".join(parts) + left + note


def one_line(summary):
    """What the Team Leader and HR see: one short line."""
    bits = []
    if summary["used_hours"] > 0:
        bits.append(f"used {_h(summary['used_hours'])} of extra hours as time off")
    if summary["earned_hours"] > 0:
        bits.append(f"earned {_h(summary['earned_hours'])} of extra hours")
    text = "Extra hours today: " + " and ".join(bits) + f". Extra hours left: {_h(summary['left_hours'])}."
    return ('<div style="margin:16px 0 0;padding:10px 14px;background:#f1f5f9;border-radius:6px;font-size:13px;color:#1f2937;'
            f"font-family:'Segoe UI', Arial, sans-serif\">{escape(text)}</div>")
