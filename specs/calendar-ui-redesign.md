# Calendar-style UI Redesign

Prototype: https://claude.ai/artifact/LNTBxQ8QPKjLXCf2eLLtsJ (Google-Calendar layout, StandardTouch brand).

## Goal
Re-skin all `www/` pages to the StandardTouch brand (red `#ED1D24`, black, white, stone `#818285`; Poppins headings, Montserrat body) with a shared shell (top bar + left rail) and a calendar view for tasks. Keep every existing API, permission rule and attendance rule.

## Constraints
- Frappe website pages, Jinja/JS/CSS only. No new frontend stack.
- Existing endpoints keep their contracts. New endpoints only where the calendar needs data that does not exist yet.
- Task status colours stay inside the brand palette: Pending = stone, In Progress = red, Done = black. Icon + text also carry status.
- Do not change scheduler, late threshold, rollover or lock-after-checkout rules.

## Phases (each: implement, test, regression-check, then next)
| # | Scope | Schema change | Risk |
|---|-------|---------------|------|
| 0 | Test baseline on a dev site, spec + progress file | none | low |
| 1 | Brand tokens + shared shell (`st_brand.css`, `st_shell.js`, Jinja shell include). Applied to Recurring Tasks (tracer bullet) | none | low |
| 2 | Additional Work page (form with login/logout/project/status, hours calc, stats, table) | none | low |
| 3 | Task Backlog + My History (month calendar, day detail) | none | low |
| 4 | Team + Management dashboards: employee record drawer using `get_employee_task_detail`; employee table | none | medium (permissions) |
| 5 | Daily Check-in calendar: status colours, lock hints, move to backlog, drag/resize within today | `Task Entry.start_time`, `Task Entry.duration` (patch) | high |
| 6 | Future-day scheduling via backlog `scheduled_for` + drag across days | `Task Backlog Item.scheduled_for/start_time` (patch) | high |

## Permission model
Unchanged. Employee: own data. Team Leader: direct reports. HR Manager / Management: all. Employee drawer reuses `get_employee_task_detail` (server-side check).

## Phase 5/6 open design points
- Tasks live on a per-day `Daily Work Log`. Moving a task to another day cannot create a future log without touching check-in rules. Proposal: future-day plans live as backlog items with a scheduled date; check-in pulls them (existing flow).
- Calendar edits are blocked server-side once `eod_submitted` (same guard as `move_task_to_backlog`).

## Test plan
Each phase: run `bench --site <dev-site> run-tests --app st_attendance_tracker`, compare with baseline, load each touched page in a browser (desktop + phone), check console. Permission changes need allowed + denied tests.

## Risks
`daily_checkin.html` is ~5600 lines with inline CSS/JS and localStorage drafts. Re-skin by additive CSS first; extract only what a phase touches.
