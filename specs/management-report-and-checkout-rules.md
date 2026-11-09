# Management report, leaderboard, reset window, pending-task remarks

## Goal
Six changes requested by management, shipped in phases. Each phase: implement, test, run the full suite, fix regressions, then move on.

## Decisions (from user)
- Leaderboard ranks by task hours = sum of `Task Entry.actual_time` for the day (any status).
- OT per day = max(0, task hours - `standard_workday_hours`, default 8).
- Reset check-in: styled in-page confirm dialog; button visible only for 60 minutes after the real check-in time (`Employee Checkin.time`, device `ST Daily Checkin`); server rejects reset after that.
- Every task still Pending at checkout needs its own non-empty remark (server enforced, UI marks the fields).
- UI changes go in both new and classic designs.
- Report lives on a new page `/management-report`, linked by a button under "Management Dashboard" in the nav. Roles: HR Manager, Management.

## Current behavior
- Late checkout request data lives on `Daily Work Log`: `unlock_requested_at`, `unlock_request_reason` (employee request); `checkout_unlocked`, `unlocked_by`, `unlocked_at`, `unlock_reason` (HR approval).
  Written by `api.request_late_checkout_approval` and `api.unlock_late_checkout`. Read by `api.get_late_checkout_requests` (pending only).
- Leaderboard: `api.get_management_dashboard` builds `rankings` from `Daily Work Log.net_hours` (EOD submitted).
- Reset: `api.reset_morning_checkin`, button `#btn-reset-ci` in `www/daily_checkin.html` with a browser `confirm()`.

## Phases
1. Pending-task remark at checkout (API validation + UI).
2. Reset check-in: dialog + 1-hour window (API gate + UI + flag in page context).
3. Management dashboard: leaderboard by task hours; "Active" filter (checked in, no checkout).
4. Report API `get_management_report(from_date, to_date, department)`: per employee worked hours, task hours, OT, late checkouts, login-time series.
5. Report page `/management-report`: filters, table, late-checkout section, OT section, login-time line chart (x = date, y = clock time), nav button.

## Data model impact
None. No new DocTypes or fields. Late checkouts are read from existing `Daily Work Log` fields.

## Permission model
Report API: HR Manager or Management only (same check as `get_management_dashboard`). Reset gate and remark rule: server side.

## Test plan
Unit tests per phase in `st_attendance_tracker/test_*.py`; full app suite after each phase; browser check of each UI change.

## Risks
- Existing tests may create EOD with pending tasks and no remark (phase 1).
- Employees on old data have no `Employee Checkin` row: reset window falls back to hidden (phase 2).
- Report over large ranges: aggregate in few queries, cap range.
