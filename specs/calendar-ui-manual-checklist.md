# Manual test checklist: calendar-style pages

For the parts that automated tests and the browser checks could not cover. Do each step, tick it, and note anything odd.

**Before you start**
- Use a test employee, or an employee whose data you can clean up. Check-in and check-out send emails to HR and the Team Leader (emails are currently suspended on `st-prod`; confirm before testing elsewhere).
- You need four logins: an Employee, that employee's Team Leader, a second Employee in the same team, and an HR Manager.
- Test in light and dark theme (sun/moon button in the top bar) and once on a phone-width window.
- Old versions are still available: `/daily-checkin-classic`.

## 1. Employee: Daily Check-in (`/daily-checkin`)

| # | Do this | Expect |
|---|---------|--------|
| 1.1 | Open the page before checking in | Week calendar, "Morning check-in" panel on the right, your carried-over and recurring tasks listed |
| 1.2 | Set a login time later than the late cut-off and look at the tag | "Late, after HH:MM" appears; earlier time shows "On time" |
| 1.3 | Pick WFH on a day that needs an Attendance Request and submit | Clear error, nothing is checked in |
| 1.4 | Submit with no tasks at all | "Add at least one task" message |
| 1.5 | Add two tasks (one with project and "1h 30m"), tick one backlog item, submit | Toast "Checked in at ...", panel switches to "Today", all tasks appear on today's column |
| 1.6 | Click an empty slot on today | Dialog opens with Task / Additional work / Backlog |
| 1.7 | Add a task there | It appears at the clicked time, Team Leader can see it |
| 1.8 | Click a slot on a future day | Options: Task for this day, Backlog |
| 1.9 | Add a task for a future day | Dashed block on that day; on that day it is in your plan after reload |
| 1.10 | Drag a dashed block to another day | It moves; Undo works |
| 1.11 | Drag a normal task up/down | New start time kept after reload |
| 1.12 | Click a task, choose Edit, change remarks and estimate | Saved; a recurring task has description/project locked |
| 1.13 | Attach a file in Edit, reload, open the task | File listed; removing it works |
| 1.14 | Change a task to In progress, then Done from the panel | Colour changes on the calendar; survives reload |
| 1.15 | Move a task to the backlog (button and by dragging onto the Backlog box) | Leaves the calendar, appears in `/task-backlog` |
| 1.16 | With 2+ pending tasks press "Pending to backlog" | Confirm dialog, then they move; Done and recurring tasks stay |
| 1.17 | Use Day, Week, Month, project filter and status filters; reload | View and status filters are remembered |
| 1.18 | Open the totals strip on week and month | Numbers match My History |
| 1.19 | Open Check out | Table of tasks; Done without "time taken" is refused |
| 1.20 | Set lunch end before start | Error, nothing submitted |
| 1.21 | Leave one task Pending with "carry on" ticked and submit | Toast shows carry-over count; day is locked; padlocks appear; no drag, edit or backlog move |
| 1.22 | Next working day: open the page | The carried task is in the list |
| 1.23 | Forget to check out, open the page the next morning | Banner asks you to check out for the earlier day first |
| 1.24 | Press Reset check-in (once) | Check-in cleared, tasks back to Pending |
| 1.25 | Employee on approved half-day leave checks in | Half-day session select shown; value stored |

## 2. Employee: other pages

| # | Page | Do this | Expect |
|---|------|---------|--------|
| 2.1 | `/additional-work` | Pick a date, times inside your working hours | Warning "falls inside your working hours" |
| 2.2 | | Times after check-out | "Extra hours: after check-out" |
| 2.3 | | Times overlapping another entry | Red message; Submit disabled |
| 2.4 | | Submit a valid entry | In history table, on the calendar, tiles update |
| 2.5 | | "Several tasks" mode, submit 2 tasks with the same login/logout | Both saved |
| 2.6 | `/task-backlog` | Add, edit, delete a task | Works |
| 2.7 | | Drag a row onto a future date in the mini calendar | Scheduled; shows in the Scheduled column and on the calendar |
| 2.8 | | Drag a row onto a past date | Refused with a message |
| 2.9 | | "Schedule all for today" | All move to today's plan (not after check-out) |
| 2.10 | | Cards toggle | Original card view still works |
| 2.11 | `/recurring-tasks` | Tap a weekday button | Saved immediately, "This week" strip updates |
| 2.12 | | Active switch, edit, delete, New template | Work; new template shows next working day |
| 2.13 | `/my-history` | Click a day | Detail panel with check-in, lunch, hours, tasks, extra work |
| 2.14 | | List toggle | Original day list loads |

## 3. Team Leader (`/team-dashboard`)

| # | Do this | Expect |
|---|---------|--------|
| 3.1 | Open the page | Only your direct reports; week grid; tiles for today |
| 3.2 | Click a name, then a day cell | Record drawer: times, tasks, attachments, extra work |
| 3.3 | "Assign task" on a person, both "today" and "backlog" modes | Task appears on their day / their backlog |
| 3.4 | Drag a Pending chip from person A to person B (today) | Moves, toast with Undo |
| 3.5 | Try the same with a started, Done, recurring or attached task | Not draggable, or refused with a reason |
| 3.6 | Reassign after either person checked out | Refused |
| 3.7 | In the drawer use "Reassign to..." | Same result as dragging |
| 3.8 | List toggle | Original cards, filters and detail panel work |
| 3.9 | A person in another team's id typed into the URL/API | Access denied |

## 4. HR Manager / Management (`/management-dashboard`)

| # | Do this | Expect |
|---|---------|--------|
| 4.1 | Open the page | Tiles, weekly chart, department bars, employees table, leaderboard |
| 4.2 | Filter by department, status, search | Table follows |
| 4.3 | Click a row | Drawer with the employee's day, including extra hours |
| 4.4 | Previous/next day | Everything reloads |
| 4.5 | Departments toggle | Original page works |
| 4.6 | Log in as a plain Employee and open the URL | Redirected away |

## 5. Appearance and devices

- Dark and light theme on every page; reload keeps the choice.
- Left menu collapses with the hamburger and stays collapsed after reload; right panels collapse.
- Phone width (about 390 px): no sideways scrolling of the whole page, top bar fits, tables scroll inside their box.
- No red errors in the browser console other than Frappe's own `file_uploader.bundle.js` message.

## 6. After testing

- Reset test check-ins, delete test tasks and entries.
- Note failures with: page, step number, what you saw, what you expected.
