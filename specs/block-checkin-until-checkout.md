# Block check-in until the last working day is checked out

## Goal
An employee who checked in on their last working day but never checked out cannot check in
today until that checkout is completed. Once the cutoff has passed, completing it needs HR approval.

## User behavior
1. Employee checks in on day D and does not check out before the cutoff (ST Attendance Settings
   `checkout_cutoff_time`, default 22:00). The existing lock applies: checkout needs HR approval.
2. On the next working day the Today page shows the existing "You did not check out on D" banner
   plus "You cannot check in until that checkout is completed". `submit_morning_log` refuses with
   "Checkout pending".
3. Employee presses **Request HR approval**; HR approves from the Management Dashboard
   (existing flow, mandatory note).
4. The page switches to day D's checkout. The employee types the **real logout time** (the
   field is empty and editable) and checks out. The OUT Employee Checkin is stamped on day D.
5. The employee can now check in for today.

## Rules
- Only the **last working day** is considered (`_get_previous_working_date`: skips every date in the
  Holiday List assigned in the Employee master, weekly offs and public holidays; no Company
  default and no weekday is hard-coded as a day off). Older unclosed days are ignored.
- No log on the last working day (absent, on leave) does not block.
- Applies to every employee. HR approval does not lift the block; only completing the checkout does.
- Everything is configured in ST Attendance Settings, no code change needed:
  - `checkout_cutoff_time` (existing)
  - `block_checkin_until_checkout_done` (switch, default on)
  - `checkin_block_effective_from` (date; empty switches the block off). A patch sets it to the
    deploy day so previously unclosed days are never held against anyone.

## Current behavior (before)
`_resolve_active_checkin_date` skipped a locked, unapproved day and let the employee check in.
`submit_eod_log` always stamped the logout time with "now", so a late checkout recorded the wrong
time (and `_make_checkin` put the OUT log on today's date).

## Data model impact
Two fields on the ST Attendance Settings single doctype. No new DocTypes.

## Permission model
Unchanged. Request: the employee (own log). Approve: HR Manager. Checkout: the employee.

## API changes
- `submit_morning_log`: throws "Checkout pending" when `_get_unclosed_previous_day` finds a day.
- `submit_eod_log`: for a late checkout (log date is not today) the client-supplied `logout_time`
  is required, validated (`_resolve_late_logout_time`: valid clock time, after login) and used.
- `_make_checkin(..., on_date)`: OUT check-in is created on the work date.

## UI states
Classic and calendar pages: banner text, editable required "Logout" input for a late checkout.

## Migration
Patch `set_checkin_block_start_date` (post_model_sync, idempotent) switches the block on from deploy day.

## Test plan
`TestCheckinBlockedUntilCheckout` (9 tests): blocked, allowed after checkout, approved-but-not-checked-out
still blocked, start date, switch/no date, only last working day, no log, logout-time validation,
OUT stamped on the work date. Browser run on a production-backup clone: blocked, request, HR approve,
real logout time, check-in succeeds.

## Risks
- A day approved by HR but never completed keeps blocking until the employee finishes it.
- An older approved-but-uncompleted day (any date) is resumed first by `_resolve_active_checkin_date`.
- Calendar-design dialog for the late logout time was not exercised in a browser.
