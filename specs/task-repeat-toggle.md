# Repeat next day (per task)

## Goal
An employee marks specific ordinary tasks to come back on the next working day even after they are Done.
Not related to Recurring Tasks (those repeat on a schedule and are managed on their own page).

## Design
- New check field `repeat_next_day` on Task Entry (child of Daily Work Log).
- Checkout rollover (`_rollover_pending_tasks`) also copies flagged Done tasks to the next working day via
  `_repeat_copies`: fresh `series_id` (a shared one would make the copy look Done), Pending, flag copied so it
  keeps repeating until the employee turns it off. Idempotent (same text/project already created for that day).
- Unfinished flagged tasks use the normal carry-forward and keep the flag.
- `_safety_rollover` (forgotten checkout) brings flagged Done tasks of the latest worked day to today.
- Recurring-type tasks cannot be flagged.

## Permission model
Own task only; Daily Work Log must not be checked out.

## API
`calendar_api.set_task_repeat(name, repeat)` POST -> `{repeat}`. `repeat_next_day` is exposed by the calendar
range and `_task_entry_dict`.

## UI
Classic check-in row: "Repeat next day" checkbox. Calendar Edit task dialog: same checkbox.

## Migration
New column defaults to 0; `bench migrate`. No backfill.

## Test plan
`test_task_repeat.py`: toggle, guards, Done task returns, non-flagged does not, flag persists, unfinished
carry keeps flag, no duplicate/idempotent, forgotten-checkout path, Recurring untouched.
