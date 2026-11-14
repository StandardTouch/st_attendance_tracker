/* Management report: filters, tabs, tables and the employee view.
 * Data: management_report.get_management_report(from_date, to_date, department)
 *       management_report.get_employee_report(employee, from_date, to_date)
 * Same script serves the new-design page and the classic page (markup: templates/report_body.html). */
(function () {
  'use strict';

  var BOOT = window.ST_REPORT;
  var MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var TABS = ['overview', 'employees', 'late', 'overtime'];
  var S = { q: { employees: '', late: '', overtime: '' }, chip: { employees: 'all', late: 'all' }, sort: { employees: { key: 'ot_minutes', dir: -1 }, overtime: { key: 'date', dir: -1 } }, data: null, detail: null, tab: 'overview', emp: '', detailTab: 'attendance', metric: 'in', taskStatus: 'all', taskQ: '', deptsFilled: false };
  var gen = 0, detailGen = 0;

  function $(s) { return document.querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function pad(n) { return String(n).padStart(2, '0'); }
  function iso(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function P(s) { var p = s.split('-').map(Number); return new Date(p[0], p[1] - 1, p[2]); }
  function shortDate(s) { var d = P(s); return d.getDate() + ' ' + MON[d.getMonth()]; }
  function fullDate(s) { var d = P(s); return d.getDate() + ' ' + MON[d.getMonth()] + ' ' + d.getFullYear(); }
  function clock(min) {
    var h = Math.floor(min / 60) % 24, m = min % 60, ap = h >= 12 ? 'PM' : 'AM', h12 = h % 12 || 12;
    return h12 + (m ? ':' + pad(m) : '') + ' ' + ap;
  }
  function axisClock(min) { return clock(min) + (min >= 48 * 60 ? ' (+2)' : (min >= 24 * 60 ? ' (+1)' : '')); }
  function hoursLabel(min) { return Math.floor(min / 60) + 'h' + (min % 60 ? ' ' + (min % 60) + 'm' : ''); }
  /* Read-only calls go straight to the whitelisted method. frappe.call never reports a server-side
     validation error (HTTP 417) back to its error callback, which left the page stuck "loading". */
  function messageFrom(body) {
    try {
      if (body._server_messages) return JSON.parse(body._server_messages).map(function (m) { return JSON.parse(m).message; }).join(' ').replace(/<[^>]+>/g, '');
    } catch (e) { /* fall through */ }
    return 'Could not load the report.';
  }
  function call(method, args) {
    var qs = Object.keys(args || {}).map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(args[k] == null ? '' : args[k]); }).join('&');
    return fetch('/api/method/' + method + '?' + qs, { credentials: 'same-origin', headers: { Accept: 'application/json' } }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (body) {
        if (!res.ok) throw new Error(messageFrom(body));
        return body.message;
      });
    }, function () { throw new Error('Could not reach the server. Check your connection and try again.'); });
  }
  function empty(cols, text) { return '<tr><td colspan="' + cols + '"><div class="rp-empty">' + esc(text) + '</div></td></tr>'; }
  function tag(text, kind) { return '<span class="rp-tag ' + (kind || '') + '">' + esc(text) + '</span>'; }

  /* ── filters ── */
  function presetRange(preset) {
    var end = new Date(), start;
    if (preset === 'this-month') start = new Date(end.getFullYear(), end.getMonth(), 1);
    else if (preset === 'last-month') { start = new Date(end.getFullYear(), end.getMonth() - 1, 1); end = new Date(end.getFullYear(), end.getMonth(), 0); }
    else { start = new Date(end); start.setDate(start.getDate() - (parseInt(preset, 10) - 1)); }
    return [iso(start), iso(end)];
  }
  function markPreset(name) {
    $$('#rp-presets .rp-chip').forEach(function (b) { b.classList.toggle('on', b.dataset.preset === name); b.setAttribute('aria-pressed', b.dataset.preset === name ? 'true' : 'false'); });
  }
  function matchPreset() {
    var from = $('#rp-from').value, to = $('#rp-to').value, hit = 'custom';
    ['7', '30', 'this-month', 'last-month'].forEach(function (p) { var r = presetRange(p); if (r[0] === from && r[1] === to) hit = p; });
    markPreset(hit);
  }

  function load() {
    var my = ++gen, err = $('#rp-err'), from = $('#rp-from').value, to = $('#rp-to').value;
    err.textContent = '';
    if (!from || !to) { err.textContent = 'Choose both dates.'; return Promise.resolve(); }
    if (from > to) { err.textContent = 'From date must be on or before To date.'; return Promise.resolve(); }
    $('#rp-root').setAttribute('aria-busy', 'true');
    return call('st_attendance_tracker.management_report.get_management_report', { from_date: from, to_date: to, department: $('#rp-dept').value })
      .then(function (d) { if (my !== gen) return; S.data = d; render(); })
      .catch(function (e) { if (my === gen) err.textContent = e.message; })
      .then(function () { if (my === gen) $('#rp-root').removeAttribute('aria-busy'); });
  }

  function fillDepartments(d) {
    if (S.deptsFilled) return;
    $('#rp-dept').innerHTML = '<option value="">All departments</option>' + d.departments.map(function (x) { return '<option value="' + esc(x) + '">' + esc(x) + '</option>'; }).join('');
    S.deptsFilled = true;
  }

  /* ── overview ── */
  function kpiCard(label, value, sub, hot, subClass) {
    return '<div class="rp-kpi' + (hot ? ' hot' : '') + '"><div class="rp-kl">' + esc(label) + '</div><div class="rp-kv">' + esc(value) + '</div><div class="rp-kd ' + (subClass || '') + '">' + esc(sub || '') + '</div></div>';
  }
  function pctDelta(cur, prev, goodWhenUp) {
    if (!prev) return { text: prev === 0 && !cur ? 'no data in either period' : 'no previous data', cls: '' };
    var p = Math.round((cur - prev) / prev * 100);
    if (!p) return { text: 'same as previous period', cls: '' };
    var up = p > 0;
    return { text: (up ? '▲ ' : '▼ ') + Math.abs(p) + '% vs previous period', cls: goodWhenUp === null ? '' : (up === goodWhenUp ? 'rp-up' : 'rp-down') };
  }
  function clockDelta(cur, prev) {
    if (cur == null || prev == null) return { text: prev == null ? 'no previous data' : '', cls: '' };
    var diff = cur - prev;
    if (!diff) return { text: 'same as previous period', cls: '' };
    return { text: (diff < 0 ? '▲ ' + (-diff) + ' min earlier' : '▼ ' + diff + ' min later'), cls: diff < 0 ? 'rp-up' : 'rp-down' };
  }
  function kpis(d) {
    var worked = 0, ot = 0, withDays = 0, pending = 0, ts = d.task_status;
    d.employees.forEach(function (e) { worked += e.days_worked; ot += e.ot_minutes; if (e.days_worked) withDays++; });
    d.late_checkouts.forEach(function (r) { if (r.status === 'Pending approval') pending++; });
    var dd = pctDelta(worked, d.previous.days_worked, true), od = pctDelta(ot, d.previous.ot_minutes, null), cd = clockDelta(d.avg_login_minutes, d.previous.avg_login_minutes);
    var pct = ts.total ? Math.round(ts.completed / ts.total * 100) : null;
    $('#rp-kpis').innerHTML =
      kpiCard('Employees', d.employees.length, withDays + ' with check-ins') +
      kpiCard('Days worked', worked, dd.text, false, dd.cls) +
      kpiCard('Average check-in', d.avg_login_minutes == null ? '-' : clock(d.avg_login_minutes), cd.text, false, cd.cls) +
      kpiCard('Overtime', hoursLabel(ot), od.text, false, od.cls) +
      kpiCard('Late checkouts', d.late_checkouts.length, pending ? pending + ' awaiting approval' : 'none awaiting', pending) +
      kpiCard('Task completion', pct == null ? '-' : pct + '%', ts.total + ' tasks in the period');
    var c = $('#rp-c-late'); c.hidden = !d.late_checkouts.length; c.textContent = d.late_checkouts.length;
  }

  /* Company average check-in per working day. */
  function trendChart(d) {
    var pts = d.trend;
    if (!pts.length) return '<div class="rp-empty">No check-ins in this period.</div>';
    var W = 620, H = 230, L = 56, R = 14, T = 14, B = 40;
    var lo = Math.min.apply(null, pts.map(function (p) { return p.avg_login_minutes; })), hi = Math.max.apply(null, pts.map(function (p) { return p.avg_login_minutes; }));
    var yMin = Math.max(0, Math.floor((lo - 30) / 60) * 60), yMax = Math.min(24 * 60, Math.ceil((hi + 30) / 60) * 60);
    if (yMax - yMin < 120) yMax = Math.min(24 * 60, yMin + 120);
    var start = P(d.from_date).getTime(), span = Math.max(P(d.to_date).getTime() - start, 86400000);
    function X(date) { return L + (P(date).getTime() - start) / span * (W - L - R); }
    function Y(min) { return T + (yMax - min) / (yMax - yMin) * (H - T - B); }
    var grid = '', xl = '';
    for (var m = yMin; m <= yMax; m += 60) grid += '<line x1="' + L + '" x2="' + (W - R) + '" y1="' + Y(m) + '" y2="' + Y(m) + '" stroke="var(--rp-line)"/><text x="' + (L - 8) + '" y="' + (Y(m) + 4) + '" text-anchor="end" font-size="11" fill="var(--rp-muted)">' + clock(m) + '</text>';
    var ticks = Math.min(6, Math.round(span / 86400000) + 1);
    for (var i = 0; i < ticks; i++) { var t = new Date(start + (ticks === 1 ? 0 : i * span / (ticks - 1))); xl += '<text x="' + X(iso(t)) + '" y="' + (H - B + 18) + '" text-anchor="middle" font-size="11" fill="var(--rp-muted)">' + esc(shortDate(iso(t))) + '</text>'; }
    var line = pts.map(function (p, i) { return (i ? 'L' : 'M') + X(p.date).toFixed(1) + ' ' + Y(p.avg_login_minutes).toFixed(1); }).join(' ');
    var dots = pts.map(function (p) { return '<circle class="rp-pt" tabindex="0" cx="' + X(p.date).toFixed(1) + '" cy="' + Y(p.avg_login_minutes).toFixed(1) + '" r="4" fill="var(--rp-ink)"><title>' + esc(fullDate(p.date) + ': average check-in ' + clock(p.avg_login_minutes) + ' (' + p.count + ' employee' + (p.count === 1 ? '' : 's') + ')') + '</title></circle>'; }).join('');
    return '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Company average check-in time per day">' + grid + xl + '<path d="' + line + '" fill="none" stroke="var(--rp-ink)" stroke-width="2"/>' + dots + '</svg>';
  }
  function otBars(d) {
    var rows = d.ot_by_department;
    if (!rows.length) return '<div class="rp-empty">No overtime in this period.</div>';
    var max = rows[0].ot_minutes;
    return rows.slice(0, 8).map(function (r) {
      return '<div class="rp-li" style="display:grid;grid-template-columns:minmax(90px,150px) 1fr 56px;gap:10px"><span>' + esc(r.department) + '</span><div class="rp-bar"><i style="width:' + Math.max(3, Math.round(r.ot_minutes / max * 100)) + '%"></i></div><b style="text-align:right">' + esc(r.ot_hours) + '</b></div>';
    }).join('');
  }
  function overview(d) {
    var pend = d.late_checkouts.filter(function (r) { return r.status === 'Pending approval'; });
    var lateIn = d.employees.filter(function (e) { return e.late_checkins >= 3; }).sort(function (a, b) { return b.late_checkins - a.late_checkins; }).slice(0, 5);
    var top = d.employees.filter(function (e) { return e.ot_minutes > 0; }).sort(function (a, b) { return b.ot_minutes - a.ot_minutes; }).slice(0, 5);
    var ts = d.task_status;
    function seg(n, color) { return ts.total ? '<div style="width:' + (n / ts.total * 100) + '%;background:' + color + '"></div>' : ''; }
    var attention = pend.map(function (r) { return '<div class="rp-li"><span><button type="button" class="rp-row-btn" data-open="' + esc(r.employee) + '">' + esc(r.employee_name) + '</button><small>Late checkout, ' + esc(fullDate(r.date)) + (r.reason ? ' · ' + esc(r.reason) : '') + '</small></span>' + tag('Awaiting approval', 'amber') + '</div>'; }).join('') +
      lateIn.map(function (e) { return '<div class="rp-li"><span><button type="button" class="rp-row-btn" data-open="' + esc(e.employee) + '">' + esc(e.employee_name) + '</button><small>' + e.late_checkins + ' late check-ins</small></span>' + tag('Review', 'red') + '</div>'; }).join('');
    $('#rp-overview').innerHTML =
      '<div class="rp-grid two"><div class="rp-card"><h3 class="rp-ct">Average check-in time</h3><p class="rp-cs">Company average for each working day.</p><div class="rp-chart">' + trendChart(d) + '</div></div>' +
      '<div class="rp-card"><h3 class="rp-ct">Overtime by department</h3><p class="rp-cs">Task time above the working day, summed over the period.</p>' + otBars(d) + '</div></div>' +
      '<div class="rp-grid three"><div class="rp-card"><h3 class="rp-ct">Needs attention</h3><p class="rp-cs">Decisions waiting and repeated late check-ins.</p>' + (attention || '<div class="rp-empty">Nothing needs attention.</div>') + '</div>' +
      '<div class="rp-card"><h3 class="rp-ct">Most overtime</h3><p class="rp-cs">Top five in the period.</p>' +
      (top.map(function (e) { return '<div class="rp-li"><span><button type="button" class="rp-row-btn" data-open="' + esc(e.employee) + '">' + esc(e.employee_name) + '</button><small>' + esc(e.department || '') + '</small></span><b>' + esc(e.ot_hours) + '</b></div>'; }).join('') || '<div class="rp-empty">No overtime.</div>') + '</div>' +
      '<div class="rp-card"><h3 class="rp-ct">Task completion</h3><p class="rp-cs">' + ts.total + ' tasks in the period.</p>' +
      (ts.total ? '<div class="rp-stack" role="img" aria-label="' + ts.completed + ' completed, ' + ts.pending + ' pending, ' + ts.rolled_over + ' carried over, ' + ts.dropped + ' dropped">' + seg(ts.completed, 'var(--rp-green)') + seg(ts.pending, 'var(--rp-amber)') + seg(ts.rolled_over, 'var(--rp-muted)') + seg(ts.dropped, 'var(--rp-line)') + '</div>' +
        '<div class="rp-legend" style="margin-top:10px"><span><b>' + ts.completed + '</b> completed</span><span><b>' + ts.pending + '</b> pending</span><span><b>' + ts.rolled_over + '</b> carried over</span><span><b>' + ts.dropped + '</b> dropped</span></div>' : '<div class="rp-empty">No tasks.</div>') + '</div></div>';
  }

  /* ── tables: search, chips and sorting ── */
  function has(text, q) { return !q || String(text || '').toLowerCase().indexOf(q.toLowerCase()) >= 0; }
  function sorted(rows, sort, cols) {
    var col = cols.filter(function (c) { return c.key === sort.key; })[0];
    return rows.slice().sort(function (a, b) {
      var x = col.val(a), y = col.val(b);
      if (typeof x === 'string' || typeof y === 'string') return String(x || '').localeCompare(String(y || '')) * sort.dir;
      return ((x || 0) - (y || 0)) * sort.dir || String(a.employee_name || '').localeCompare(String(b.employee_name || ''));
    });
  }
  function head(id, cols, sort) {
    $('#' + id).innerHTML = '<tr>' + cols.map(function (c) {
      var on = c.key === sort.key;
      return '<th class="' + (c.num ? 'rp-num' : '') + '"' + (on ? ' aria-sort="' + (sort.dir > 0 ? 'ascending' : 'descending') + '"' : '') + '><button type="button" class="rp-sort" data-sort="' + c.key + '">' + esc(c.label) + (on ? (sort.dir > 0 ? ' ▲' : ' ▼') : '') + '</button></th>';
    }).join('') + '</tr>';
  }
  function chips(id, defs, current) {
    $('#' + id).innerHTML = defs.map(function (c) { return '<button type="button" class="rp-chip' + (c[0] === current ? ' on' : '') + '" data-chip="' + c[0] + '" aria-pressed="' + (c[0] === current) + '">' + esc(c[1]) + '</button>'; }).join('');
  }

  var EMP_COLS = [
    { key: 'employee_name', label: 'Employee', val: function (e) { return e.employee_name; } },
    { key: 'department', label: 'Department', val: function (e) { return e.department; } },
    { key: 'days_worked', label: 'Days', num: 1, val: function (e) { return e.days_worked; } },
    { key: 'task_minutes', label: 'Task hours', num: 1, val: function (e) { return e.task_minutes; } },
    { key: 'net_minutes', label: 'Net hours', num: 1, val: function (e) { return e.net_minutes; } },
    { key: 'completion', label: 'Task completion', val: function (e) { return e.tasks_total ? e.tasks_done / e.tasks_total : -1; } },
    { key: 'ot_minutes', label: 'Overtime', num: 1, val: function (e) { return e.ot_minutes; } },
    { key: 'late_checkins', label: 'Late in', num: 1, val: function (e) { return e.late_checkins; } },
    { key: 'late_checkouts', label: 'Late out', num: 1, val: function (e) { return e.late_checkouts; } }
  ];
  var EMP_FILTERS = {
    all: function () { return true; },
    ot: function (e) { return e.ot_minutes > 0; },
    late_in: function (e) { return e.late_checkins > 0; },
    late_out: function (e) { return e.late_checkouts > 0; },
    none: function (e) { return e.days_worked === 0; }
  };
  function visibleEmployees(d) {
    return sorted(d.employees.filter(function (e) { return EMP_FILTERS[S.chip.employees](e) && (has(e.employee_name, S.q.employees) || has(e.department, S.q.employees)); }), S.sort.employees, EMP_COLS);
  }
  function employeeRows(d) {
    var n = function (f) { return d.employees.filter(EMP_FILTERS[f]).length; };
    chips('rp-chips-employees', [['all', 'All ' + n('all')], ['ot', 'With overtime ' + n('ot')], ['late_in', 'Late check-ins ' + n('late_in')], ['late_out', 'Late checkouts ' + n('late_out')], ['none', 'No check-ins ' + n('none')]], S.chip.employees);
    head('rp-head-employees', EMP_COLS, S.sort.employees);
    var rows = visibleEmployees(d);
    $('#rp-n-employees').textContent = rows.length + ' of ' + d.employees.length + ' employees';
    $('#rp-emps').innerHTML = rows.map(function (e) {
      var pct = e.tasks_total ? Math.round(e.tasks_done / e.tasks_total * 100) : null;
      return '<tr data-emp="' + esc(e.employee) + '"><td><button type="button" class="rp-row-btn" data-open="' + esc(e.employee) + '">' + esc(e.employee_name) + '</button></td><td>' + esc(e.department || '') + '</td>' +
        '<td class="rp-num">' + e.days_worked + '</td><td class="rp-num">' + esc(e.task_hours) + '</td><td class="rp-num">' + esc(e.net_hours) + '</td>' +
        '<td>' + (pct == null ? '-' : '<div class="rp-bar-cell"><div class="rp-bar"><i style="width:' + pct + '%;background:var(--rp-green)"></i></div><b>' + pct + '%</b></div>') + '</td>' +
        '<td class="rp-num">' + (e.ot_minutes ? '<b>' + esc(e.ot_hours) + '</b>' : '-') + '</td>' +
        '<td class="rp-num">' + e.late_checkins + '</td><td class="rp-num">' + e.late_checkouts + '</td></tr>';
    }).join('') || empty(9, 'No employees match.');
  }

  var LATE_FILTERS = {
    all: function () { return true; },
    pending: function (r) { return r.status === 'Pending approval'; },
    approved: function (r) { return r.status !== 'Pending approval'; }
  };
  function visibleLate(d) {
    return d.late_checkouts.filter(function (r) { return LATE_FILTERS[S.chip.late](r) && (has(r.employee_name, S.q.late) || has(r.department, S.q.late) || has(r.reason, S.q.late)); });
  }
  function lateRows(d) {
    var n = function (f) { return d.late_checkouts.filter(LATE_FILTERS[f]).length; };
    chips('rp-chips-late', [['all', 'All ' + n('all')], ['pending', 'Awaiting approval ' + n('pending')], ['approved', 'Approved ' + n('approved')]], S.chip.late);
    var rows = visibleLate(d);
    $('#rp-n-late').textContent = rows.length + ' of ' + d.late_checkouts.length;
    $('#rp-late').innerHTML = rows.map(function (r) {
      var kind = r.status === 'Pending approval' ? 'amber' : (r.status === 'Approved, checked out' ? 'green' : '');
      return '<tr><td><button type="button" class="rp-row-btn" data-open="' + esc(r.employee) + '">' + esc(r.employee_name) + '</button></td><td>' + esc(r.department || '') + '</td><td>' + esc(fullDate(r.date)) + '</td>' +
        '<td>' + tag(r.status, kind) + '</td><td class="rp-wrap">' + esc(r.reason || '-') + '</td><td>' + esc(r.approved_by || '-') + '</td><td class="rp-wrap">' + esc(r.approval_note || '-') + '</td></tr>';
    }).join('') || empty(7, d.late_checkouts.length ? 'No late checkouts match.' : 'No late checkouts in this range.');
  }

  var OT_COLS = [
    { key: 'employee_name', label: 'Employee', val: function (r) { return r.employee_name; } },
    { key: 'department', label: 'Department', val: function (r) { return r.department; } },
    { key: 'date', label: 'Date', val: function (r) { return r.date; } },
    { key: 'task_minutes', label: 'Task hours', num: 1, val: function (r) { return r.task_minutes; } },
    { key: 'ot_minutes', label: 'Overtime', num: 1, val: function (r) { return r.ot_minutes; } }
  ];
  function visibleOvertime(d) {
    return sorted(d.overtime.filter(function (r) { return has(r.employee_name, S.q.overtime) || has(r.department, S.q.overtime); }), S.sort.overtime, OT_COLS);
  }
  function overtimeRows(d) {
    var std = Math.round(d.standard_workday_minutes / 60 * 100) / 100;
    $('#rp-ot-note').textContent = 'Task time above ' + std + ' hours in a full day, or ' + (std / 2) + ' hours in a half day.';
    head('rp-head-overtime', OT_COLS, S.sort.overtime);
    var rows = visibleOvertime(d);
    $('#rp-n-overtime').textContent = rows.length + ' of ' + d.overtime.length + ' days';
    $('#rp-ot').innerHTML = rows.map(function (r) {
      return '<tr><td><button type="button" class="rp-row-btn" data-open="' + esc(r.employee) + '">' + esc(r.employee_name) + '</button></td><td>' + esc(r.department || '') + '</td><td>' + esc(fullDate(r.date)) + '</td>' +
        '<td class="rp-num">' + esc(r.task_hours) + '</td><td class="rp-num"><b>' + esc(r.ot_hours) + '</b></td></tr>';
    }).join('') || empty(5, d.overtime.length ? 'No overtime matches.' : 'No overtime in this range.');
  }

  function setTab(name, quiet) {
    if (TABS.indexOf(name) < 0) name = 'overview';
    S.tab = name;
    $$('#rp-tabs .rp-tab').forEach(function (b) { var on = b.dataset.tab === name; b.classList.toggle('on', on); b.setAttribute('aria-selected', on ? 'true' : 'false'); b.tabIndex = on ? 0 : -1; });
    TABS.forEach(function (t) { $('#rp-p-' + t).hidden = t !== name; });
    if (!quiet) writeHash();
  }

  /* ── employee view ── */
  var ETABS = [['attendance', 'Attendance'], ['tasks', 'Tasks'], ['late', 'Late checkouts']];
  function openEmployee(key) {
    S.emp = key; S.detailTab = 'attendance'; S.metric = 'in'; S.taskStatus = 'all'; S.taskQ = '';
    showEmployeeView(true); writeHash(); loadDetail();
    var r = $('#rp-root'); if (r.scrollIntoView) r.scrollIntoView({ block: 'start' });
  }
  function closeEmployee() { S.emp = ''; S.detail = null; showEmployeeView(false); writeHash(); }
  function showEmployeeView(on) {
    $('#rp-list-view').hidden = on; $('#rp-employee-view').hidden = !on;
    // The filter bar stays pinned over the lists; in the employee view it would only cover content.
    $('#rp-form').classList.toggle('rp-static', on);
  }

  function loadDetail() {
    var my = ++detailGen, body = $('#rp-detail-body');
    var from = S.data ? S.data.from_date : $('#rp-from').value, to = S.data ? S.data.to_date : $('#rp-to').value;
    $('#rp-emp-head').innerHTML = ''; $('#rp-etabs').innerHTML = '';
    body.innerHTML = '<div class="rp-empty">Loading…</div>';
    call('st_attendance_tracker.management_report.get_employee_report', { employee: S.emp, from_date: from, to_date: to })
      .then(function (r) { if (my !== detailGen) return; S.detail = r; renderDetail(); })
      .catch(function (e) { if (my === detailGen) body.innerHTML = '<div class="rp-empty">' + esc(e.message) + '</div>'; });
  }

  function avg(min) { return min == null ? '-' : clock(min); }
  function detailKpis(r) {
    var c = r.task_counts, pct = c.total ? Math.round(c.completed / c.total * 100) : 0;
    return '<div class="rp-kpis">' +
      kpiCard('Days worked', r.days_worked + ' of ' + r.range_days) +
      kpiCard('Average check-in', avg(r.avg_login_minutes), r.median_login_minutes == null ? '' : 'median ' + clock(r.median_login_minutes)) +
      kpiCard('Average checkout', avg(r.avg_logout_minutes), r.median_logout_minutes == null ? '' : 'median ' + clock(r.median_logout_minutes) + (r.days.some(function (d) { return d.logout_next_day; }) ? ', after-midnight counted' : '')) +
      kpiCard('Late checkouts', r.late_checkouts.length, '', r.late_checkouts.length) +
      kpiCard('Tasks', c.total, c.completed + ' completed (' + pct + '%)') + '</div>';
  }

  function series(r) {
    var inn = { name: 'Check-in', color: 'var(--rp-ink)', pts: [] }, out = { name: 'Checkout', color: 'var(--rp-blue)', pts: [] };
    r.days.forEach(function (d) {
      if (d.login_minutes != null) inn.pts.push({ date: d.date, minutes: d.login_minutes, label: d.login_label, late: d.late_checkin, tip: d.late_checkin ? ' (late)' : '' });
      if (d.logout_minutes != null) out.pts.push({ date: d.date, minutes: d.logout_minutes, label: d.logout_label, late: d.late_checkout, tip: d.late_checkout ? ' (late checkout)' : '' });
    });
    return S.metric === 'in' ? [inn] : S.metric === 'out' ? [out] : [inn, out];
  }
  /* Line graph: x = dates in the chosen range, y = clock time (later = higher). One line per series. */
  function chart(r) {
    var ss = series(r), all = [];
    ss.forEach(function (x) { all = all.concat(x.pts); });
    if (!all.length) return '<div class="rp-empty">No ' + (S.metric === 'out' ? 'checkouts' : 'check-ins') + ' recorded in this range.</div>';
    var W = 760, H = 300, L = 74, R = 16, T = 14, B = 44;
    var lo = Math.min.apply(null, all.map(function (p) { return p.minutes; })), hi = Math.max.apply(null, all.map(function (p) { return p.minutes; }));
    var yMin = Math.max(0, Math.floor((lo - 30) / 60) * 60), yMax = Math.min(60 * 60, Math.ceil((hi + 30) / 60) * 60);
    if (yMax - yMin < 120) yMax = Math.min(60 * 60, yMin + 120);
    var start = P(r.from_date).getTime(), span = Math.max(P(r.to_date).getTime() - start, 86400000);
    function X(date) { return L + (P(date).getTime() - start) / span * (W - L - R); }
    function Y(min) { return T + (yMax - min) / (yMax - yMin) * (H - T - B); }
    var step = (yMax - yMin) > 1080 ? 360 : ((yMax - yMin) > 480 ? 120 : 60), grid = '';
    for (var m = yMin; m <= yMax; m += step) {
      grid += '<line x1="' + L + '" x2="' + (W - R) + '" y1="' + Y(m) + '" y2="' + Y(m) + '" stroke="var(--rp-line)"/><text x="' + (L - 8) + '" y="' + (Y(m) + 4) + '" text-anchor="end" font-size="11" fill="var(--rp-muted)">' + axisClock(m) + '</text>';
    }
    var days = Math.round(span / 86400000), ticks = Math.min(8, days + 1), xl = '';
    for (var i = 0; i < ticks; i++) {
      var t = new Date(start + (ticks === 1 ? 0 : i * span / (ticks - 1)));
      xl += '<text x="' + X(iso(t)) + '" y="' + (H - B + 18) + '" text-anchor="middle" font-size="11" fill="var(--rp-muted)">' + esc(shortDate(iso(t))) + '</text>';
    }
    var body = ss.map(function (x) {
      var line = x.pts.map(function (p, i) { return (i ? 'L' : 'M') + X(p.date).toFixed(1) + ' ' + Y(p.minutes).toFixed(1); }).join(' ');
      var dots = x.pts.map(function (p) {
        return '<circle class="rp-pt" tabindex="0" cx="' + X(p.date).toFixed(1) + '" cy="' + Y(p.minutes).toFixed(1) + '" r="5" fill="' + x.color + '"' + (p.late ? ' stroke="var(--rp-accent)" stroke-width="3"' : '') + '>' +
          '<title>' + esc(fullDate(p.date) + ': ' + x.name.toLowerCase() + ' at ' + p.label + p.tip) + '</title></circle>';
      }).join('');
      return '<path d="' + line + '" fill="none" stroke="' + x.color + '" stroke-width="1.5" opacity=".5"/>' + dots;
    }).join('');
    return '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="' + esc(S.metric === 'both' ? 'Check-in and checkout' : (S.metric === 'out' ? 'Checkout' : 'Check-in')) + ' time by date for ' + esc(r.employee_name) + '">' + grid + xl +
      '<line x1="' + L + '" x2="' + L + '" y1="' + T + '" y2="' + (H - B) + '" stroke="var(--rp-line)"/>' + body + '</svg>';
  }
  function chartBlock(r) {
    var ss = series(r), shown = 0;
    ss.forEach(function (x) { shown = Math.max(shown, x.pts.length); });
    var legend = ss.map(function (x) { return '<span><i style="background:' + x.color + '"></i>' + x.name + '</span>'; }).join('') +
      '<span><i style="background:transparent;border:3px solid var(--rp-accent)"></i>Late</span>';
    return '<div class="rp-card"><div class="rp-ctl"><label class="rp-field"><span class="rp-label">Show</span><select id="rp-metric" class="rp-input" aria-label="Which times to plot">' +
      '<option value="in"' + (S.metric === 'in' ? ' selected' : '') + '>Check-in time</option><option value="out"' + (S.metric === 'out' ? ' selected' : '') + '>Checkout time</option>' +
      '<option value="both"' + (S.metric === 'both' ? ' selected' : '') + '>Both</option></select></label>' +
      '<span class="rp-hint">' + shown + ' day' + (shown === 1 ? '' : 's') + ' plotted out of ' + r.range_days + ' in the range (' + r.days_worked + ' worked).</span></div>' +
      '<div class="rp-chart">' + chart(r) + '</div><div class="rp-legend">' + legend + '</div></div>';
  }
  function dayRows(r) {
    return r.days.slice().reverse().map(function (d) {
      return '<tr><td>' + esc(fullDate(d.date)) + '</td><td>' + esc(d.login_label || '-') + (d.late_checkin ? ' ' + tag('Late', 'red') : '') + '</td>' +
        '<td>' + esc(d.logout_label || (d.checked_out ? '-' : 'Not checked out')) + (d.late_checkout ? ' ' + tag('Late checkout', 'red') : '') + '</td>' +
        '<td class="rp-num">' + esc(d.task_hours) + '</td><td class="rp-num">' + esc(d.net_hours || '-') + '</td><td class="rp-num">' + d.tasks_done + ' / ' + d.tasks_total + '</td></tr>';
    }).join('') || empty(6, 'No check-ins in this range.');
  }
  var CAL_LABEL = { present: 'Present', late: 'Late check-in', leave: 'On leave', missing: 'No check-in', off: 'Weekly off or holiday', future: 'Upcoming' };
  function heatBlock(r) {
    var counts = { present: 0, late: 0, leave: 0, missing: 0 };
    var cells = r.calendar.map(function (c) {
      if (counts[c.status] != null) counts[c.status]++;
      var label = fullDate(c.date) + ': ' + CAL_LABEL[c.status];
      return '<span class="rp-cell s-' + c.status + '" role="img" aria-label="' + esc(label) + '" title="' + esc(label) + '">' + P(c.date).getDate() + '</span>';
    }).join('');
    function key(cls, text) { return '<span><i class="rp-cell s-' + cls + '" style="width:12px;height:12px;border-radius:3px;display:inline-block;vertical-align:-1px;margin-right:6px"></i>' + text + '</span>'; }
    return '<div class="rp-card"><h3 class="rp-ct">Day by day</h3><p class="rp-cs">One cell for each calendar day: ' + counts.present + ' present, ' + counts.late + ' late, ' + counts.leave + ' on leave, ' + counts.missing + ' without a check-in.</p>' +
      '<div class="rp-heat">' + cells + '</div><div class="rp-legend">' + key('present', 'Present') + key('late', 'Late check-in') + key('leave', 'On leave') + key('missing', 'No check-in') + key('off', 'Weekly off or holiday') + key('future', 'Upcoming') + '</div></div>';
  }
  function attendancePane(r) {
    return detailKpis(r) + heatBlock(r) + chartBlock(r) +
      '<h3 class="rp-sub">Times for each worked day</h3><div class="rp-tw"><table class="rp-table"><thead><tr><th>Date</th><th>Check-in</th><th>Checkout</th><th class="rp-num">Task hours</th><th class="rp-num">Net hours</th><th class="rp-num">Tasks done</th></tr></thead><tbody>' + dayRows(r) + '</tbody></table></div>';
  }

  var FILTERS = { all: null, Done: ['Done'], open: ['Pending', 'In Progress'], 'Rolled Over': ['Rolled Over'], Dropped: ['Dropped'] };
  function filteredTasks() {
    var allow = FILTERS[S.taskStatus], q = S.taskQ.toLowerCase();
    return S.detail.tasks.filter(function (t) {
      if (allow && allow.indexOf(t.status) < 0) return false;
      return !q || (t.description + ' ' + t.project + ' ' + t.remarks).toLowerCase().indexOf(q) >= 0;
    });
  }
  function taskRows() {
    return filteredTasks().map(function (t) {
      var kind = t.status === 'Done' ? 'green' : (t.status === 'Dropped' ? '' : 'amber');
      return '<tr><td>' + esc(shortDate(t.date)) + '</td><td>' + esc(t.project || '-') + '</td><td class="rp-wrap">' + esc(t.description) + (t.carried_from ? ' <small class="rp-hint">carried from ' + esc(shortDate(t.carried_from)) + '</small>' : '') + '</td>' +
        '<td>' + esc(t.task_type || '-') + '</td><td>' + tag(t.status, kind) + '</td><td class="rp-num">' + esc(t.estimated) + '</td><td class="rp-num">' + esc(t.actual) + '</td><td class="rp-wrap">' + esc(t.remarks || '-') + '</td></tr>';
    }).join('') || empty(8, 'No tasks match.');
  }
  function tasksPane(r) {
    var c = r.task_counts, pct = c.total ? Math.round(c.completed / c.total * 100) : 0;
    function seg(n, color) { return c.total ? '<div style="width:' + (n / c.total * 100) + '%;background:' + color + '"></div>' : ''; }
    var opt = function (v, l) { return '<option value="' + v + '"' + (S.taskStatus === v ? ' selected' : '') + '>' + l + '</option>'; };
    return '<div class="rp-card"><h3 class="rp-ct">Task status</h3><p class="rp-cs">' + c.total + ' tasks in the period.</p>' +
      '<div class="rp-stack" role="img" aria-label="' + c.completed + ' completed, ' + c.pending + ' pending, ' + c.rolled_over + ' carried over, ' + c.dropped + ' dropped">' + seg(c.completed, 'var(--rp-green)') + seg(c.pending, 'var(--rp-amber)') + seg(c.rolled_over, 'var(--rp-muted)') + seg(c.dropped, 'var(--rp-line)') + '</div>' +
      '<div class="rp-legend"><span><b>' + c.completed + '</b> completed (' + pct + '%)</span><span><b>' + c.pending + '</b> pending</span><span><b>' + c.rolled_over + '</b> carried over</span><span><b>' + c.dropped + '</b> dropped</span></div></div>' +
      '<div class="rp-ctl"><select id="rp-tstatus" class="rp-input" aria-label="Filter tasks by status">' + opt('all', 'All statuses') + opt('Done', 'Completed') + opt('open', 'Pending / in progress') + opt('Rolled Over', 'Carried over') + opt('Dropped', 'Dropped') + '</select>' +
      '<input id="rp-tq" type="search" class="rp-input" placeholder="Search tasks, projects, remarks" aria-label="Search tasks" value="' + esc(S.taskQ) + '">' +
      '<button type="button" class="rp-btn" id="rp-csv-tasks">Export CSV</button></div>' +
      '<div class="rp-tw"><table class="rp-table"><thead><tr><th>Date</th><th>Project</th><th>Task</th><th>Type</th><th>Status</th><th class="rp-num">Estimated</th><th class="rp-num">Actual</th><th>Remarks</th></tr></thead><tbody id="rp-trows">' + taskRows() + '</tbody></table></div>';
  }
  function latePane(r) {
    return '<div class="rp-tw"><table class="rp-table"><thead><tr><th>Date</th><th>Status</th><th>Reason</th><th>Approved by</th><th>Approval note</th></tr></thead><tbody>' +
      (r.late_checkouts.map(function (x) { return '<tr><td>' + esc(fullDate(x.date)) + '</td><td>' + tag(x.status, x.status === 'Pending approval' ? 'amber' : '') + '</td><td class="rp-wrap">' + esc(x.reason || '-') + '</td><td>' + esc(x.approved_by || '-') + '</td><td class="rp-wrap">' + esc(x.approval_note || '-') + '</td></tr>'; }).join('') || empty(5, 'No late checkouts for this employee.')) + '</tbody></table></div>';
  }
  function renderDetail() {
    var r = S.detail, ini = (r.employee_name || '?').split(' ').filter(Boolean).slice(0, 2).map(function (w) { return w[0]; }).join('').toUpperCase();
    $('#rp-emp-range').textContent = fullDate(r.from_date) + ' to ' + fullDate(r.to_date);
    $('#rp-emp-head').innerHTML = '<div class="rp-av" aria-hidden="true">' + esc(ini) + '</div><div><div class="rp-en">' + esc(r.employee_name) + '</div><div class="rp-hint">' + esc(r.department || '') + ' · ' + esc(r.employee) + '</div></div>';
    $('#rp-etabs').innerHTML = ETABS.map(function (t) {
      var on = t[0] === S.detailTab, n = t[0] === 'tasks' ? ' (' + r.task_counts.total + ')' : t[0] === 'late' ? ' (' + r.late_checkouts.length + ')' : '';
      return '<button type="button" class="rp-tab' + (on ? ' on' : '') + '" role="tab" aria-selected="' + on + '" data-etab="' + t[0] + '"' + (on ? '' : ' tabindex="-1"') + '>' + t[1] + n + '</button>';
    }).join('');
    $('#rp-detail-body').innerHTML = S.detailTab === 'tasks' ? tasksPane(r) : S.detailTab === 'late' ? latePane(r) : attendancePane(r);
  }

  /* ── CSV export (client side, from the rows already loaded) ── */
  function csv(rows, name) {
    var text = rows.map(function (r) { return r.map(function (c) { var v = String(c == null ? '' : c); if (/^[=+\-@]/.test(v)) v = "'" + v; return '"' + v.replace(/"/g, '""') + '"'; }).join(','); }).join('\r\n');
    var a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob(['﻿' + text], { type: 'text/csv;charset=utf-8' }));
    a.download = name; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
  }

  /* ── hash: #employees, #emp=EMP-0001 ── */
  function writeHash() {
    var h = S.emp ? 'emp=' + encodeURIComponent(S.emp) : (S.tab === 'overview' ? '' : S.tab);
    if (('#' + h) !== (location.hash || '#')) { try { history.replaceState(null, '', location.pathname + location.search + (h ? '#' + h : '')); } catch (e) { /* ignore */ } }
  }
  function readHash() {
    var h = (location.hash || '').replace(/^#/, '');
    if (h.indexOf('emp=') === 0) return { emp: decodeURIComponent(h.slice(4)) };
    return { tab: TABS.indexOf(h) >= 0 ? h : 'overview' };
  }

  function render() {
    var d = S.data;
    fillDepartments(d);
    kpis(d); overview(d); employeeRows(d); lateRows(d); overtimeRows(d);
    if (S.emp) loadDetail();
  }

  function bind() {
    $('#rp-form').addEventListener('submit', function (e) { e.preventDefault(); load(); });
    $$('#rp-presets .rp-chip').forEach(function (b) {
      b.addEventListener('click', function () {
        var p = b.dataset.preset;
        if (p === 'custom') { markPreset('custom'); $('#rp-from').focus(); return; }
        var r = presetRange(p); $('#rp-from').value = r[0]; $('#rp-to').value = r[1]; markPreset(p); load();
      });
    });
    ['#rp-from', '#rp-to', '#rp-dept'].forEach(function (s) { $(s).addEventListener('change', function () { if (s !== '#rp-dept') matchPreset(); load(); }); });
    $('#rp-tabs').addEventListener('click', function (e) { var b = e.target.closest('[data-tab]'); if (b) setTab(b.dataset.tab); });
    $('#rp-tabs').addEventListener('keydown', function (e) {
      if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
      var i = TABS.indexOf(S.tab) + (e.key === 'ArrowRight' ? 1 : -1); i = (i + TABS.length) % TABS.length;
      setTab(TABS[i]); $('#rp-t-' + TABS[i]).focus();
    });
    document.addEventListener('click', function (e) { var o = e.target.closest('[data-open]'); if (o && $('#rp-root').contains(o)) openEmployee(o.dataset.open); });
    $('#rp-emps').addEventListener('click', function (e) { var tr = e.target.closest('tr[data-emp]'); if (tr && !e.target.closest('[data-open]')) openEmployee(tr.dataset.emp); });
    $('#rp-back').addEventListener('click', closeEmployee);
    $('#rp-etabs').addEventListener('click', function (e) { var b = e.target.closest('[data-etab]'); if (b && S.detail) { S.detailTab = b.dataset.etab; renderDetail(); } });
    var body = $('#rp-detail-body');
    body.addEventListener('change', function (e) {
      if (e.target.id === 'rp-metric') { S.metric = e.target.value; renderDetail(); var m = $('#rp-metric'); if (m) m.focus(); }
      if (e.target.id === 'rp-tstatus') { S.taskStatus = e.target.value; $('#rp-trows').innerHTML = taskRows(); }
    });
    body.addEventListener('input', function (e) { if (e.target.id === 'rp-tq') { S.taskQ = e.target.value; $('#rp-trows').innerHTML = taskRows(); } });
    body.addEventListener('click', function (e) {
      if (e.target.id !== 'rp-csv-tasks') return;
      csv([['Date', 'Project', 'Task', 'Type', 'Status', 'Estimated', 'Actual', 'Remarks']].concat(filteredTasks().map(function (t) { return [t.date, t.project, t.description, t.task_type, t.status, t.estimated, t.actual, t.remarks]; })),
        'tasks-' + S.detail.employee + '-' + S.detail.from_date + '-to-' + S.detail.to_date + '.csv');
    });
    function tableControls(tab, redraw) {
      $('#rp-q-' + tab).addEventListener('input', function (e) { S.q[tab] = e.target.value; redraw(S.data); });
      var chipBox = $('#rp-chips-' + tab);
      if (chipBox) chipBox.addEventListener('click', function (e) { var b = e.target.closest('[data-chip]'); if (b) { S.chip[tab] = b.dataset.chip; redraw(S.data); } });
      var headBox = $('#rp-head-' + tab);
      if (headBox) headBox.addEventListener('click', function (e) {
        var b = e.target.closest('[data-sort]'); if (!b) return;
        var cur = S.sort[tab]; S.sort[tab] = { key: b.dataset.sort, dir: cur.key === b.dataset.sort ? -cur.dir : (b.dataset.sort === 'employee_name' || b.dataset.sort === 'department' ? 1 : -1) };
        redraw(S.data); var again = $('#rp-head-' + tab + ' [data-sort="' + b.dataset.sort + '"]'); if (again) again.focus();
      });
    }
    tableControls('employees', employeeRows); tableControls('late', lateRows); tableControls('overtime', overtimeRows);
    function range() { return S.data.from_date + '-to-' + S.data.to_date; }
    $('#rp-csv-emps').addEventListener('click', function () {
      if (!S.data) return;
      csv([['Employee', 'Department', 'Days worked', 'Task hours', 'Net hours', 'Task completion %', 'OT hours', 'Late check-ins', 'Late checkouts']].concat(visibleEmployees(S.data).map(function (e) {
        return [e.employee_name, e.department, e.days_worked, e.task_hours, e.net_hours, e.tasks_total ? Math.round(e.tasks_done / e.tasks_total * 100) : '', e.ot_hours, e.late_checkins, e.late_checkouts]; })), 'employees-' + range() + '.csv');
    });
    $('#rp-csv-late').addEventListener('click', function () {
      if (!S.data) return;
      csv([['Employee', 'Department', 'Date', 'Status', 'Reason', 'Approved by', 'Approval note']].concat(visibleLate(S.data).map(function (r) { return [r.employee_name, r.department, r.date, r.status, r.reason, r.approved_by, r.approval_note]; })), 'late-checkouts-' + range() + '.csv');
    });
    $('#rp-csv-ot').addEventListener('click', function () {
      if (!S.data) return;
      csv([['Employee', 'Department', 'Date', 'Task hours', 'Overtime']].concat(visibleOvertime(S.data).map(function (r) { return [r.employee_name, r.department, r.date, r.task_hours, r.ot_hours]; })), 'overtime-' + range() + '.csv');
    });
    window.addEventListener('hashchange', function () {
      var h = readHash();
      if (h.emp) { if (h.emp !== S.emp) { S.emp = h.emp; showEmployeeView(true); loadDetail(); } }
      else { if (S.emp) { S.emp = ''; showEmployeeView(false); } setTab(h.tab, true); }
    });
  }

  frappe.ready(function () {
    bind(); matchPreset();
    var h = readHash();
    if (h.emp) { S.emp = h.emp; showEmployeeView(true); } else { setTab(h.tab, true); }
    load();
  });
})();
