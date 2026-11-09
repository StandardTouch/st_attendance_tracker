"""Shared duration/time-parsing helpers used by api.py and the Daily Task /
Daily Task Log controllers, so a correctness fix only needs to happen once."""

import datetime as _datetime

import frappe
from frappe.utils import date_diff, today

MAX_WORKDAY_MINUTES = 24 * 60


def time_to_minutes(t):
    """Parse a Time-fieldtype value (timedelta, as Frappe hands back DB
    Time fields) or an 'HH:MM'/'HH:MM am/pm' string into minutes-since-
    midnight. Unparseable or empty input returns 0 — callers that need to
    tell "midnight" apart from "couldn't parse" must check the raw value
    themselves before calling this."""
    if isinstance(t, _datetime.timedelta):
        return int(t.total_seconds()) // 60
    s = str(t or "").strip().lower()
    if not s:
        return 0

    is_pm = "pm" in s
    is_am = "am" in s
    s = s.replace("pm", "").replace("am", "").strip()

    parts = s.split(":")
    if len(parts) >= 2:
        try:
            h = int(parts[0])
            m = int(parts[1])
            if is_pm and h < 12:
                h += 12
            elif is_am and h == 12:
                h = 0
            return h * 60 + m
        except Exception:
            pass
    return 0


def validate_lunch_hours(login_time, logout_time, lunch_from, lunch_to):
    """Reject reversed, zero-duration, or out-of-shift lunch intervals via
    frappe.throw. No-op if any of the four values is missing."""
    if not (login_time and logout_time and lunch_from and lunch_to):
        return

    login_mins = time_to_minutes(login_time)
    logout_mins = time_to_minutes(logout_time)
    lf_mins = time_to_minutes(lunch_from)
    lt_mins = time_to_minutes(lunch_to)

    # Handle overnight shift: if logout < login, treat shift as wrapping midnight
    shift_len = (logout_mins - login_mins) if logout_mins >= login_mins \
        else (logout_mins + 24 * 60 - login_mins)

    # Relative positions of lunch within shift (offset from login)
    lf_abs = (lf_mins - login_mins) if lf_mins >= login_mins \
        else (lf_mins + 24 * 60 - login_mins)
    lt_abs = (lt_mins - login_mins) if lt_mins >= login_mins \
        else (lt_mins + 24 * 60 - login_mins)

    # If lt is before lf in absolute terms, wrap it to the next day
    if lt_abs < lf_abs:
        lt_abs += 24 * 60

    lunch_duration = lt_abs - lf_abs

    if lunch_duration <= 0:
        frappe.throw(
            f"Lunch duration cannot be zero or negative. "
            f"Selected interval: {lunch_from} → {lunch_to}."
        )

    if lf_abs < 0 or lt_abs > shift_len:
        frappe.throw(
            f"Lunch interval ({lunch_from} → {lunch_to}) must fall "
            f"completely within your shift ({login_time} → {logout_time})."
        )


def calculate_net_minutes(login_time, logout_time, lunch_from, lunch_to, date):
    """Net worked minutes: (logout - login), midnight-wrap-safe, minus a
    valid lunch interval if both lunch fields are set. Returns None if
    login_time/logout_time aren't both set."""
    if not login_time or not logout_time:
        return None

    login_mins = time_to_minutes(login_time)
    logout_mins = time_to_minutes(logout_time)
    total_mins = logout_mins - login_mins
    # Handle overnight / night-shift (midnight wrap)
    if total_mins < 0:
        total_mins += 24 * 60
    elif total_mins == 0:
        total_mins = resolve_zero_diff_minutes(date)

    lunch_mins = 0
    if lunch_from and lunch_to:
        lf_mins = time_to_minutes(lunch_from)
        lt_mins = time_to_minutes(lunch_to)
        d = lt_mins - lf_mins
        if d < 0:
            d += 24 * 60
        # Only subtract valid lunch (already validated upstream)
        if 0 < d:
            lunch_mins = d

    return max(0, total_mins - lunch_mins)


def parse_duration_to_hours(s):
    """Parse a free-text duration like '1h 30m', '45m', '1:30' into hours.

    A bare number with no unit (e.g. '45') is treated as minutes, matching
    the input placeholder's own shorthand ('e.g. 1h 30m, 45m') — interpreting
    it as hours silently inflated stored durations by 60x.
    Negative results are treated as unparsed (0.0) rather than stored as-is.
    """
    if not s:
        return 0.0
    s = str(s).strip().lower()
    if not s:
        return 0.0

    if ":" in s and "h" not in s and "m" not in s:
        parts = s.split(":", 1)
        try:
            hours = float(parts[0])
            mins = float(parts[1]) if parts[1] else 0.0
            total = hours + mins / 60.0
            return total if total >= 0 else 0.0
        except ValueError:
            pass

    try:
        mins = float(s)
        return mins / 60.0 if mins >= 0 else 0.0
    except ValueError:
        pass

    h = 0.0
    m = 0.0

    for term in ["hours", "hour", "hrs", "hr"]:
        s = s.replace(term, "h")
    for term in ["minutes", "minute", "mins", "min"]:
        s = s.replace(term, "m")

    if "h" in s:
        parts = s.split("h")
        try:
            h = float(parts[0].strip())
        except ValueError:
            pass
        s = parts[1].strip()
    if "m" in s:
        parts = s.split("m")
        try:
            m = float(parts[0].strip())
        except ValueError:
            pass
    elif s:
        # No 'm' suffix but leftover text after the 'h' split
        # (e.g. "1h30") — treat it as bare minutes instead of dropping it.
        try:
            m = float(s)
        except ValueError:
            pass

    total = h + (m / 60.0)
    return total if total >= 0 else 0.0


def resolve_zero_diff_minutes(date_str):
    """A login/logout pair with identical clock time is 0 minutes unless the
    checkout is for exactly the day after login — the one legitimate case of
    a shift that ran the full 24 hours to the same time next day. A checkout
    submitted more days late than that with a coincidentally-matching time is
    not a real 24-hour shift, so it must not be fabricated as one.
    """
    if today() == str(date_str):
        return 0
    if date_diff(today(), date_str) == 1:
        return 24 * 60
    return 0


DEFAULT_CHECKOUT_CUTOFF = "22:00:00"


def get_checkout_cutoff():
    """Checkout cutoff time-of-day from ST Attendance Settings, as minutes
    since midnight. Falls back to 22:00 if the setting is blank."""
    value = frappe.db.get_single_value("ST Attendance Settings", "checkout_cutoff_time")
    return time_to_minutes(value or DEFAULT_CHECKOUT_CUTOFF)


def is_checkout_locked(log_date, unlocked, now=None, cutoff_minutes=None):
    """True when checkout for `log_date` is closed: the cutoff time on that
    date has passed and HR has not unlocked it. Any past date is therefore
    locked once its own cutoff has passed, not just today."""
    if unlocked:
        return False
    if now is None:
        now = frappe.utils.now_datetime()
    if cutoff_minutes is None:
        cutoff_minutes = get_checkout_cutoff()
    cutoff = frappe.utils.get_datetime(log_date) + _datetime.timedelta(minutes=cutoff_minutes)
    return now > cutoff
