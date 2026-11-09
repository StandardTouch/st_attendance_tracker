# Time bank: compensate overtime and additional work with less work later

## Goal
Overtime and Additional Work hours are banked. On a later day the employee may use banked hours at checkout so a short day still counts as a full day. Overtime is never paid in salary.

## Decisions (from user)
- Credits: overtime (task hours above 8 on a full day, above 4 on a half day) and every Additional Work entry (`hours_spent`). No approval.
- Using hours: no approval. Chosen at checkout with a checkbox and an hours box.
- Amount: any amount up to the balance (not capped by the day's shortfall). Partial use is allowed; the rest stays for later days.
- Expiry: 60 days from the credit's work date, then the credit stops counting.
- Source update: Additional Work shows read-only compensated hours and remaining hours. Overtime days appear in the ledger with the same figures. Each use records date and hours.

## Current behavior
- Overtime is computed on the fly from `Task Entry.actual_time`; nothing is stored.
- Additional Work has `hours_spent` and no link to compensation.
- Shift Type "Day": under 8h net (lunch excluded) is Half Day.

## Data model impact
New DocType `Extra Hours Entry` (one row per credit or use):
`employee`, `date`, `entry_type` (Credit | Use), `hours`, `source` (Overtime | Additional Work | Compensation), `source_log` (Daily Work Log), `source_additional_work`, `expires_on`, plus for a Use row a child table `Extra Hours Source` (credit entry, hours) so a use lists exactly which credits it consumed (oldest first).
Additional Work gets read-only `compensated_hours`, `remaining_hours`.
Balance = sum of unexpired credit hours minus allocated use hours.

## Flow
1. Checkout: overtime credit is created or updated for that log (idempotent, keyed by log). Additional Work save creates or updates its credit (keyed by the entry).
2. Checkout form: if balance > 0, show "Use banked hours" checkbox and hours box (max = balance). Server re-validates.
3. On submit: a Use row is created inside the same transaction as checkout, allocated oldest credit first, and the source docs' compensated/remaining fields are updated.
4. Attendance: when net hours plus used hours reach the Shift Type half-day threshold, the Attendance is set to Present and noted with the link to the Use row. Raw check-in and checkout times are never changed.

## Permission model
Employee: own ledger only. HR Manager and Management: all (report and list). Credits and uses are created only by server code, never by client-supplied hours; a Use is validated against the server-side balance.

## API / UI
- `get_page_state` adds `time_bank_balance`.
- `submit_eod_log` accepts optional `use_bank_hours`.
- New check-in card "Time bank". Report gets a balance column and ledger list.

## Edge cases
- Editing or deleting a credit that was already used is blocked.
- Re-saving a source never duplicates a credit.
- A cancelled or reset checkout removes that day's Use row and restores the credits.
- A credit older than 60 days is excluded from balance and from allocation.

## Test plan
Credit creation and idempotence; balance maths; partial use then later use; oldest-first allocation; expiry boundary; use above balance rejected; Attendance reaches Present with used hours (HRMS contract test); employee cannot read another's ledger; checkout rollback restores credits.

## Risks
- Attendance hook depends on auto-attendance timing: the Use row is written at checkout, before attendance processing, so the hook sees it. A manual attendance run for a past date must also respect it.
- The overtime credit depends on final task hours at checkout; later edits to a locked day are not possible, so credits stay stable.

## Phases
1. DocType, credit creation (overtime and Additional Work), balance logic, tests.
2. Use at checkout (API, form, allocation, source field updates, Attendance hook), tests.
3. Expiry job, report column and ledger view.
4. Browser checks, regression run.
