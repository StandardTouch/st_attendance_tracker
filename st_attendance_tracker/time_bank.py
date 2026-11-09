"""Extra hours (the "time bank"): overtime and additional-work hours are saved up and can be used as
time off on later days. Overtime is never paid in salary.

Every function here is idempotent: a credit is keyed by its source (a Daily Work Log or an
Additional Work), so saving the same source twice never banks the hours twice."""
import frappe
from frappe.utils import add_days, cint, flt, getdate, today

DEFAULT_EXPIRY_DAYS = 60


def expiry_days():
    """Days a credit counts after its work date, from ST Attendance Settings. An unset or zero
    value (a site that never saved the field reads 0) falls back to 60 days."""
    from st_attendance_tracker.api import _get_attendance_settings

    return cint(_get_attendance_settings().get("time_bank_expiry_days")) or DEFAULT_EXPIRY_DAYS


def expires_on(work_date):
    return add_days(getdate(work_date), expiry_days())


def workday_hours(employee, date, log=None):
    """Hours that make a full working day for this employee on this date, from the settings.
    A day with an approved half-day leave (or a half-day session on the log) is half as long."""
    from st_attendance_tracker.api import _get_attendance_settings

    standard = flt(_get_attendance_settings().get("standard_workday_hours")) or 8.0
    half_session = (log.half_day_session if log else None) or frappe.db.get_value(
        "Daily Work Log", {"employee": employee, "date": str(getdate(date))}, "half_day_session"
    )
    half_leave = frappe.db.exists("Leave Application", {
        "employee": employee, "status": "Approved", "docstatus": 1,
        "half_day": 1, "half_day_date": str(getdate(date)),
    })
    return standard / 2 if (half_session or half_leave) else standard


def overtime_hours(log):
    """Task hours above the working day for a Daily Work Log (0 when none)."""
    return max(0.0, round(flt(log.working_hours) - workday_hours(log.employee, log.date, log), 4))


def _find_credit(source, **link):
    return frappe.db.get_value("Extra Hours Entry", {"entry_type": "Credit", "source": source, **link},
                               ["name", "used_hours"], as_dict=True)


def _upsert_credit(source, link, employee, date, hours, remarks=None):
    """Create, update or remove the credit for one source so it matches `hours`."""
    hours = round(flt(hours), 4)
    existing = _find_credit(source, **link)
    if hours <= 0:
        if existing and flt(existing.used_hours) <= 0:
            frappe.delete_doc("Extra Hours Entry", existing.name, ignore_permissions=True, force=True)
        return None
    if existing:
        doc = frappe.get_doc("Extra Hours Entry", existing.name)
        # Hours already used as time off are not given back by a later edit to the source.
        doc.hours = max(hours, flt(doc.used_hours))
        doc.date = date
        doc.expires_on = expires_on(date)
        if remarks:
            doc.remarks = remarks
        doc.save(ignore_permissions=True)
        return doc
    return frappe.get_doc({
        "doctype": "Extra Hours Entry", "employee": employee, "date": date, "entry_type": "Credit",
        "source": source, "hours": hours, "used_hours": 0, "expires_on": expires_on(date),
        "remarks": remarks, **link,
    }).insert(ignore_permissions=True)


def sync_overtime_credit(log):
    """Bank the overtime of a checked-out Daily Work Log."""
    if not log.eod_submitted:
        return None
    return _upsert_credit("Overtime", {"source_log": log.name}, log.employee, log.date, overtime_hours(log),
                          remarks="Task hours above the working day")


def sync_additional_work_credit(doc):
    """Bank the hours of an Additional Work entry (every entry counts, no approval)."""
    credit = _upsert_credit("Additional Work", {"source_additional_work": doc.name}, doc.employee,
                            doc.work_date, flt(doc.hours_spent), remarks=doc.project_name or None)
    _refresh_additional_work(doc.name)
    return credit


def _refresh_additional_work(name):
    """Show on the Additional Work how much of it was taken and how much is left."""
    if not name or not frappe.db.exists("Additional Work", name):
        return
    credit = _find_credit("Additional Work", source_additional_work=name)
    hours = flt(frappe.db.get_value("Additional Work", name, "hours_spent"))
    used = flt(credit.used_hours) if credit else 0.0
    frappe.db.set_value("Additional Work", name,
                        {"compensated_hours": used, "remaining_hours": max(0.0, round(hours - used, 4))},
                        update_modified=False)


def remove_additional_work_credit(doc):
    """Deleting an Additional Work removes its credit, unless some of it was already used."""
    existing = _find_credit("Additional Work", source_additional_work=doc.name)
    if not existing:
        return
    if flt(existing.used_hours) > 0:
        frappe.throw("This additional work cannot be deleted because some of its hours were already used as time off.")
    frappe.delete_doc("Extra Hours Entry", existing.name, ignore_permissions=True, force=True)


def get_balance(employee, as_of=None):
    """Extra hours still available: unexpired credits minus what was used from them."""
    as_of = str(getdate(as_of or today()))
    rows = frappe.get_all(
        "Extra Hours Entry",
        filters={"employee": employee, "entry_type": "Credit"},
        or_filters=[["expires_on", "is", "not set"], ["expires_on", ">=", as_of]],
        fields=["hours", "used_hours"],
    )
    return round(sum(max(0.0, flt(r.hours) - flt(r.used_hours)) for r in rows), 4)


# ── using banked hours ────────────────────────────────────────────────────────

EPSILON = 1e-6


def _usable_credits(employee, on_date):
    """Credits that can offset `on_date`, oldest work first: worked on an earlier day (a day's own
    extra work cannot cover itself), not expired on `on_date`, with hours left."""
    day = str(getdate(on_date))
    rows = frappe.get_all(
        "Extra Hours Entry",
        filters={"employee": employee, "entry_type": "Credit", "date": ["<", day]},
        or_filters=[["expires_on", "is", "not set"], ["expires_on", ">=", day]],
        fields=["name", "hours", "used_hours", "date", "source_additional_work"],
        order_by="date asc, creation asc",
    )
    return [r for r in rows if flt(r.hours) - flt(r.used_hours) > EPSILON]


def available_hours(employee, on_date):
    """Extra hours the employee can use as time off on `on_date`."""
    return round(sum(flt(r.hours) - flt(r.used_hours) for r in _usable_credits(employee, on_date)), 4)


def validate_use(employee, on_date, hours):
    """Normalise the requested hours and reject an impossible request. Call before changing anything."""
    hours = flt(hours)
    if hours < 0:
        frappe.throw("Hours to use cannot be negative.")
    if hours <= EPSILON:
        return 0.0
    available = available_hours(employee, on_date)
    if hours > available + EPSILON:
        frappe.throw(f"You can use at most {available:g} extra hour(s) today, but you asked for {hours:g}.")
    return round(hours, 4)


def apply_use(log, hours):
    """Take `hours` out of the bank for a Daily Work Log: oldest credits first, one Use entry per day.
    Running it again for the same log replaces the earlier use."""
    release_use(log.name)
    hours = validate_use(log.employee, log.date, hours)
    if not hours:
        return None
    left, allocations = hours, []
    for credit in _usable_credits(log.employee, log.date):
        take = min(left, flt(credit.hours) - flt(credit.used_hours))
        allocations.append({"credit": credit.name, "hours": round(take, 4)})
        left = round(left - take, 4)
        if left <= EPSILON:
            break
    doc = frappe.get_doc({
        "doctype": "Extra Hours Entry", "employee": log.employee, "date": log.date, "entry_type": "Use",
        "source": "Compensation", "hours": hours, "source_log": log.name, "allocations": allocations,
        "remarks": "Extra hours used as time off",
    }).insert(ignore_permissions=True)
    for row in allocations:
        _add_used(row["credit"], row["hours"])
    return doc


def release_use(log_name):
    """Give back the hours a Daily Work Log took (its checkout was undone or the log deleted)."""
    for name in frappe.get_all("Extra Hours Entry", filters={"entry_type": "Use", "source_log": log_name}, pluck="name"):
        doc = frappe.get_doc("Extra Hours Entry", name)
        for row in doc.allocations:
            _add_used(row.credit, -flt(row.hours))
        frappe.delete_doc("Extra Hours Entry", name, ignore_permissions=True, force=True)


def _add_used(credit_name, delta):
    credit = frappe.db.get_value("Extra Hours Entry", credit_name, ["used_hours", "source_additional_work"], as_dict=True)
    if not credit:
        return
    frappe.db.set_value("Extra Hours Entry", credit_name, "used_hours",
                        max(0.0, round(flt(credit.used_hours) + delta, 4)), update_modified=False)
    _refresh_additional_work(credit.source_additional_work)


def clear_log_bank(log_name):
    """A checkout was undone or its log deleted: give back what it used and drop its unused overtime credit."""
    release_use(log_name)
    _upsert_credit("Overtime", {"source_log": log_name}, None, None, 0)


def used_on(employee, date):
    """Extra hours the employee used as time off on `date`."""
    rows = frappe.get_all("Extra Hours Entry", filters={"employee": employee, "entry_type": "Use", "date": str(getdate(date))},
                          pluck="hours")
    return round(sum(flt(h) for h in rows), 4)


# ── attendance ────────────────────────────────────────────────────────────────

def apply_to_attendance(doc, method=None):
    """A day short of the Present threshold becomes Present when banked hours cover the gap.
    Only auto-attendance's hours-based Half Day is touched, never one that comes from a leave.
    The employee's real check-in and checkout times are not changed."""
    if doc.status != "Half Day" or doc.get("leave_application") or not doc.get("shift"):
        return
    threshold = flt(frappe.db.get_value("Shift Type", doc.shift, "working_hours_threshold_for_half_day"))
    if threshold <= 0:
        return
    used = used_on(doc.employee, doc.attendance_date)
    if used and flt(doc.working_hours) + used >= threshold - EPSILON:
        doc.status = "Present"
        doc.flags.time_bank_hours = used


def note_attendance(doc, method=None):
    hours = doc.flags.get("time_bank_hours")
    if hours:
        doc.add_comment("Info", f"Marked Present because {hours:g} extra hour(s) were used as time off on this day "
                                f"(worked {flt(doc.working_hours):g}h).")


# ── reading the bank ──────────────────────────────────────────────────────────

def balances_for(employees, as_of=None):
    """{employee: balance} for many employees with one query."""
    if not employees:
        return {}
    as_of = str(getdate(as_of or today()))
    rows = frappe.get_all(
        "Extra Hours Entry",
        filters={"employee": ["in", list(employees)], "entry_type": "Credit"},
        or_filters=[["expires_on", "is", "not set"], ["expires_on", ">=", as_of]],
        fields=["employee", "hours", "used_hours"],
    )
    totals = {}
    for r in rows:
        totals[r.employee] = totals.get(r.employee, 0.0) + max(0.0, flt(r.hours) - flt(r.used_hours))
    return {e: round(totals.get(e, 0.0), 4) for e in employees}


def get_ledger(employee, from_date, to_date):
    """Time bank rows for one employee, newest first: everything dated inside the range, plus any
    older credit that still has unused hours. Each use lists the credits it took hours from."""
    start, end, today_ = str(getdate(from_date)), str(getdate(to_date)), str(getdate(today()))
    rows = frappe.get_all(
        "Extra Hours Entry", filters={"employee": employee},
        fields=["name", "date", "entry_type", "source", "hours", "used_hours", "expires_on", "remarks"],
        order_by="date desc, creation desc",
    )
    allocations = {}
    use_names = [r.name for r in rows if r.entry_type == "Use"]
    for a in (frappe.get_all("Extra Hours Source", filters={"parenttype": "Extra Hours Entry", "parent": ["in", use_names]},
                             fields=["parent", "credit_date", "hours"], order_by="parent asc, idx asc") if use_names else []):
        allocations.setdefault(a.parent, []).append(a)
    ledger = []
    for r in rows:
        in_range = start <= str(r.date) <= end
        expired = r.entry_type == "Credit" and bool(r.expires_on) and str(r.expires_on) < today_
        remaining = max(0.0, flt(r.hours) - flt(r.used_hours)) if r.entry_type == "Credit" else 0.0
        if not (in_range or (r.entry_type == "Credit" and remaining > 0 and not expired)):
            continue
        ledger.append({
            "date": str(r.date), "entry_type": r.entry_type, "source": r.source, "hours": flt(r.hours),
            "used_hours": flt(r.used_hours), "remaining_hours": round(remaining, 4),
            "expires_on": str(r.expires_on or ""), "expired": expired, "note": r.remarks or "",
            "taken_from": [{"date": str(a.credit_date), "hours": flt(a.hours)} for a in allocations.get(r.name, [])],
        })
    return ledger


def get_my_ledger(days=90):
    """The signed-in employee's own bank: balance available today and recent entries."""
    from st_attendance_tracker.api import _get_employee

    employee = _get_employee().name
    to_date = today()
    return {
        "employee": employee,
        "balance": get_balance(employee),
        "expiry_days": expiry_days(),
        "entries": get_ledger(employee, add_days(to_date, -cint(days)), to_date),
    }


# ── what the checkout email says ──────────────────────────────────────────────

def checkout_summary(employee, date, log):
    """Extra hours used and earned on the day of this checkout, for the checkout email. None when the
    day had neither, so a normal day's email stays exactly as it is."""
    use = frappe.db.get_value("Extra Hours Entry", {"entry_type": "Use", "source_log": log.name}, ["name", "hours"], as_dict=True)
    items = []
    if use:
        for alloc in frappe.get_all("Extra Hours Source", filters={"parent": use.name, "parenttype": "Extra Hours Entry"},
                                    fields=["credit", "credit_date", "hours"], order_by="idx asc"):
            credit = frappe.db.get_value("Extra Hours Entry", alloc.credit, ["source", "source_additional_work"], as_dict=True) or {}
            label = "Overtime"
            if credit.get("source_additional_work"):
                work = frappe.db.get_value("Additional Work", credit["source_additional_work"], ["project_name", "description"], as_dict=True) or {}
                what = (work.get("project_name") or (work.get("description") or "")[:40]).strip()
                label = "Additional work" + (f" ({what})" if what else "")
            items.append({"date": str(alloc.credit_date), "label": label, "hours": flt(alloc.hours)})
    earned = flt(frappe.db.get_value("Extra Hours Entry", {"entry_type": "Credit", "source": "Overtime", "source_log": log.name}, "hours") or 0)
    used = flt(use.hours) if use else 0.0
    if used <= 0 and earned <= 0:
        return None

    as_of = str(getdate(date))
    open_credits = [r for r in frappe.get_all(
        "Extra Hours Entry", filters={"employee": employee, "entry_type": "Credit"},
        or_filters=[["expires_on", "is", "not set"], ["expires_on", ">=", as_of]],
        fields=["hours", "used_hours", "expires_on"]) if flt(r.hours) - flt(r.used_hours) > EPSILON]
    expiries = [r.expires_on for r in open_credits if r.expires_on]
    return {
        "used_hours": used, "items": items, "earned_hours": earned,
        "left_hours": get_balance(employee, as_of=as_of),
        "next_expiry": str(min(expiries)) if expiries else "",
        "net_hours_text": log.net_hours or "", "full_day_hours": workday_hours(employee, date, log),
        "expiry_days": expiry_days(),
    }
