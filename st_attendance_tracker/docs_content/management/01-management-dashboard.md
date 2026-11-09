# Management Dashboard

For HR Managers and Management. A company-wide view of attendance for any date.

![Management Dashboard, Classic design](/assets/st_attendance_tracker/docs/c_mgmt.png)

## Header

Shows *Company Attendance Overview*. Use the **date** box to pick a day and **Refresh** to reload the figures.

## Filter chips

**All**, **Missing**, **Late**, **On leave** and **Checkout done** filter the department cards.

## Departments

Each department card shows its name, number of employees, and a progress figure such as *0/3* (checked in out of total). Below the header are employee tiles with:

- initials and name,
- a status (for example **Missing**),
- check-in detail (*Not in*, or the time).

## Employee details

Click an employee tile to open the dark detail panel on the right.

![Employee detail panel](/assets/st_attendance_tracker/docs/c_mgmt_open.png)

It shows their task summary (**Completed**, **Pending**, **Total**) and their **Login** and **Logout** times for the selected date.

## Right-hand panels

- **Late Checkout Requests** – requests waiting for approval. See *Late Checkout Approvals*.
- **Hours Leaderboard** – employees ranked by net hours for people who submitted checkout. If nobody has, it says *No checkout logs submitted yet for this date.*
- **Employee detail** – described above.

## Access

- **HR Manager** and **Management** can open this page.
- Management users have no employee record, so they open this dashboard instead of Daily Check-in.
- HR Managers also use the Employee pages, and reach this one from **Management Dashboard** in the left menu.

## Good habits

- Review **Missing** each morning and **No checkout** at the end of the day.
- Clear the *Late Checkout Requests* panel daily so employees are not blocked.
