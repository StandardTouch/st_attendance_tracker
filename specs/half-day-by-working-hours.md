# Half Day by net working hours

## Goal
A day with less than 8 hours of work (lunch excluded) is marked Half Day by HRMS auto-attendance.
Absent stays reserved for days with no check-in or check-out.

## Current behavior
- Shift Type "Day" has both thresholds at 0, so any check-in makes a full Present.
- Present blocks Leave Application (`validate_attendance`, hrms 15.64.2), so half-day leave
  cannot be applied or approved after the employee has worked part of the day.
- Working hours are first check-in to last check-out, so lunch counts as work.

## Change
- Code: checkout also writes lunch as an OUT (lunch start) and an IN (lunch end) `Employee Checkin`,
  device `ST Daily Checkin Lunch`, dated to the work day (`api._make_lunch_checkins`, called from
  `DailyWorkLog._trigger_eod_automation` before the final OUT). Idempotent per day. Skipped without a valid lunch window.
- Config, on the Shift Type document (nothing hardcoded):
  - `working_hours_calculation_based_on` = Every Valid Check-in and Check-out
  - `working_hours_threshold_for_half_day` = 8
  - `working_hours_threshold_for_absent` = 0

## Data model, migration
No schema change. Not retroactive: existing attendance and check-ins are untouched, and
`process_attendance_after` / `last_sync_of_checkin` are not changed.

## Permissions
None changed. Lunch rows never trigger bot check-in emails (`notify_external_checkin`).

## Test plan
`test_lunch_checkins.py`: lunch OUT/IN order and device, no lunch, reversed window, no duplicates,
no bot mail, HRMS contract (8h with lunch rows, whole span without).
Tracer on a copy site: 9:30-18:30 + 1h lunch = 8.0h Present; 9:30-18:00 + 1h = 7.5h Half Day; 4.5h no lunch = Half Day.

## Risks
- A day with no lunch entered has no deduction (counts the whole span).
- About 30% of days in the clone data would be Half Day under an 8h net rule; confirm with management.
- Not built: relaxing HRMS `validate_attendance` for a half-day leave on an already-Present day
  (only needed for days of 8h or more). Cancelling an approved leave also cancels that day's attendance (HRMS behavior).
- Production: set the three Shift Type values only after the code is deployed.
