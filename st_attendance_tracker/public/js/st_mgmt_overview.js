/* Management Dashboard overview for HR Manager / Management.
 * Data: api.get_management_dashboard(date) and calendar_api.get_company_week. The original department
 * view stays under the Departments toggle.
 */
(function () {
  'use strict';

  var BOOT = window.ST_MGMT;
  var DOW = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];
  var MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var DAY_STATUS = {
    checked_in: { label: 'Checked in', tag: 'black' }, late: { label: 'Late', tag: 'red-line' }, eod_done: { label: 'Checked out', tag: '' },
    missing: { label: 'Missing', tag: 'red' }, leave: { label: 'On leave', tag: 'dash' }
  };

  function $(s, r) { return (r || document).querySelector(s); }
  function pad(n) { return String(n).padStart(2, '0'); }
  function iso(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function P(s) { var p = s.split('-').map(Number); return new Date(p[0], p[1] - 1, p[2]); }
  function addD(d, n) { var x = new Date(d); x.setDate(x.getDate() + n); return x; }
  function sow(d) { return addD(d, -d.getDay()); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function dayLabel(s) { var d = P(s); return DOW[d.getDay()].charAt(0) + DOW[d.getDay()].slice(1).toLowerCase() + ', ' + MON[d.getMonth()] + ' ' + d.getDate() + ', ' + d.getFullYear(); }
  function serverMessage(xhr) {
    try {
      var json = xhr.responseJSON || JSON.parse(xhr.responseText || '{}');
      if (json._server_messages) return JSON.parse(json._server_messages).map(function (m) { return JSON.parse(m).message; }).join(' ').replace(/<[^>]+>/g, '');
    } catch (e) { /* fall through */ }
    return 'Could not load the dashboard.';
  }
  function call(method, args) {
    return new Promise(function (resolve, reject) {
      frappe.call({ method: method, args: args, silent: true, freeze: false, callback: function (r) { resolve(r.message); }, error: function (x) { reject(new Error(serverMessage(x))); } });
    });
  }

  var S = { date: BOOT.today, dash: null, week: null, extra: {}, dept: '', status: 'all', q: '' };
  var gen = 0;

  function load() {
    var my = ++gen, a = sow(P(S.date));
    $('#mg-overview').classList.add('loading');
    return Promise.all([
      call('st_attendance_tracker.api.get_management_dashboard', { date: S.date }),
      call('st_attendance_tracker.calendar_api.get_company_week', { from_date: iso(a), to_date: iso(addD(a, 6)) }),
      call('st_attendance_tracker.calendar_api.get_company_extra_hours', { date: S.date })
    ]).then(function (r) { if (my !== gen) return; S.dash = r[0]; S.week = r[1]; S.extra = r[2] || {}; render(); })
      .catch(function (e) { if (my === gen) $('#mg-body').innerHTML = '<div class="cal-empty" style="margin:16px">' + esc(e.message) + '</div>'; });
  }

  function allEmployees() {
    var out = [];
    (S.dash.departments || []).forEach(function (d) { d.employees.forEach(function (e) { out.push(e); }); });
    return out;
  }

  function chart() {
    var W = 520, H = 210, L = 34, B = 26, bw = 40, gap = (W - L) / 7, total = S.week.total || 1;
    var grid = [0, 25, 50, 75, 100].map(function (v) {
      var y = H - B - (H - B - 12) * v / 100;
      return '<line x1="' + L + '" x2="' + W + '" y1="' + y + '" y2="' + y + '" stroke="var(--st-line)"/><text x="' + (L - 6) + '" y="' + (y + 4) + '" text-anchor="end" font-size="10" fill="var(--st-muted)">' + v + '</text>';
    }).join('');
    var bars = S.week.days.map(function (d, i) {
      var pct = Math.min(100, Math.round(d.present / total * 100)), h = (H - B - 12) * pct / 100, x = L + i * gap + (gap - bw) / 2, sel = d.date === S.date, future = d.date > S.week.today;
      return '<g><title>' + dayLabel(d.date) + ': ' + d.present + ' of ' + S.week.total + ' checked in, ' + d.late + ' late</title>' +
        '<rect x="' + x + '" y="' + (H - B - h) + '" width="' + bw + '" height="' + Math.max(h, future ? 0 : 1) + '" rx="4" fill="' + (sel ? 'var(--st-red)' : 'var(--st-ink)') + '" opacity="' + (future ? .15 : 1) + '"/>' +
        (future ? '' : '<text x="' + (x + bw / 2) + '" y="' + (H - B - h - 5) + '" text-anchor="middle" font-size="11" font-weight="600" fill="var(--st-ink)">' + pct + '%</text>') +
        '<text x="' + (x + bw / 2) + '" y="' + (H - 8) + '" text-anchor="middle" font-size="11" fill="var(--st-muted)">' + DOW[P(d.date).getDay()].charAt(0) + DOW[P(d.date).getDay()].slice(1).toLowerCase() + '</text></g>';
    }).join('');
    return '<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" role="img" aria-label="Share of employees checked in per day">' + grid + bars + '</svg>';
  }

  function deptBars() {
    return (S.dash.departments || []).map(function (d) {
      var s = d.summary || {}, total = s.total || 0, present = s.checked_in || 0;
      return '<div style="margin-bottom:12px"><div class="cal-row cal-between"><b>' + esc(d.department) + '</b><span style="color:var(--st-muted);font-variant-numeric:tabular-nums">' + present + '/' + total + ' in' + (s.late ? ' · ' + s.late + ' late' : '') + (s.missing ? ' · ' + s.missing + ' missing' : '') + '</span></div>' +
        '<div class="cal-bar"' + (total && present / total < .85 ? '' : '') + '><i style="width:' + (total ? present / total * 100 : 0) + '%;background:' + (total && present / total < .85 ? 'var(--st-red)' : 'var(--st-ink)') + '"></i></div></div>';
    }).join('') || '<div class="cal-empty">No departments.</div>';
  }

  function rows() {
    return allEmployees().filter(function (e) {
      if (S.dept && e.department !== S.dept) return false;
      if (S.status !== 'all' && e.status !== S.status) return false;
      return !S.q || (e.employee_name + ' ' + (e.designation || '')).toLowerCase().indexOf(S.q) >= 0;
    }).map(function (e) {
      var st = DAY_STATUS[e.status] || DAY_STATUS.missing, initials = e.employee_name.split(' ').filter(Boolean).slice(0, 2).map(function (w) { return w[0]; }).join('').toUpperCase();
      return '<tr tabindex="0" data-open="' + esc(e.name) + '"><td><div class="cal-row" style="flex-wrap:nowrap"><span class="tm-av">' + esc(initials) + '</span><div><b>' + esc(e.employee_name) + '</b><div style="color:var(--st-muted);font-size:11px">' + esc(e.designation || '') + '</div></div></div></td>' +
        '<td>' + esc(e.department || '') + '</td><td><span class="cal-tag ' + st.tag + '">' + st.label + '</span></td><td>' + esc(e.login_time || '-') + '</td><td>' + esc(e.logout_time || '-') + '</td><td>' + esc(e.net_hours || '-') + '</td>' +
        '<td>' + (S.extra[e.name] ? '+' + S.extra[e.name] + 'h' : '-') + '</td>' +
        '<td>' + (e.total_tasks ? e.done_tasks + ' / ' + e.total_tasks : '-') + '</td></tr>';
    }).join('');
  }

  function leaderboard() {
    var r = S.dash.rankings || [], std = S.dash.standard_workday_minutes || 480;
    if (!r.length) return '<div class="cal-empty">Nobody has checked out for this day yet.</div>';
    return '<ol class="mg-lb">' + r.map(function (x, i) {
      var pct = Math.min(100, Math.round(x.net_minutes / std * 100));
      return '<li><span class="n">' + (i + 1) + '</span><div style="flex:1;min-width:0"><div class="cal-row cal-between" style="flex-wrap:nowrap"><b style="overflow-wrap:anywhere">' + esc(x.employee_name) + '</b><span style="font-variant-numeric:tabular-nums;font-weight:600">' + esc(x.net_hours) + '</span></div>' +
        '<div class="cal-bar" style="margin:4px 0"><i style="width:' + pct + '%;background:var(--st-ink)"></i></div><small style="color:var(--st-muted)">' + esc(x.login_time) + ' – ' + esc(x.logout_time) + (x.is_half_day ? ' · half day' : '') + '</small></div></li>';
    }).join('') + '</ol>';
  }

  function render() {
    $('#mg-overview').classList.remove('loading');
    if (window.STShell) { STShell.setTitle('Management · ' + dayLabel(S.date)); STShell.setDate(P(S.date)); }
    var s = S.dash.summary || {}, depts = (S.dash.departments || []).map(function (d) { return d.department; });
    $('#mg-kpis').innerHTML =
      '<div class="tm-kpi"><small>Employees</small><b>' + (s.total || 0) + '</b></div><div class="tm-kpi"><small>Checked in</small><b>' + (s.checked_in || 0) + '</b></div>' +
      '<div class="tm-kpi"><small>Checked out</small><b>' + (s.eod_done || 0) + '</b></div><div class="tm-kpi"><small>On leave</small><b>' + (s.on_leave || 0) + '</b></div>' +
      '<div class="tm-kpi hot"><small>Missing check-in</small><b>' + (s.missing || 0) + '</b></div>';
    $('#mg-chart').innerHTML = chart();
    $('#mg-depts-bars').innerHTML = deptBars();
    var sel = $('#mg-dept'), cur = S.dept;
    sel.innerHTML = '<option value="">All departments</option>' + depts.map(function (d) { return '<option' + (d === cur ? ' selected' : '') + ' value="' + esc(d) + '">' + esc(d) + '</option>'; }).join('');
    $('#mg-rows').innerHTML = rows() || '<tr><td colspan="8"><div class="cal-empty">No employees match.</div></td></tr>';
    $('#mg-lb').innerHTML = leaderboard();
  }

  function open(name) {
    var emp = allEmployees().filter(function (e) { return e.name === name; })[0]; if (!emp) return;
    STDrawer.open(emp, S.date, { today: BOOT.today });
  }

  function bind() {
    $('#mg-rows').addEventListener('click', function (e) { var r = e.target.closest('[data-open]'); if (r) open(r.dataset.open); });
    $('#mg-rows').addEventListener('keydown', function (e) { if (e.key === 'Enter') { var r = e.target.closest('[data-open]'); if (r) open(r.dataset.open); } });
    $('#mg-dept').addEventListener('change', function (e) { S.dept = e.target.value; render(); });
    $('#mg-status').addEventListener('change', function (e) { S.status = e.target.value; render(); });
    $('#mg-q').addEventListener('input', function (e) { S.q = e.target.value.trim().toLowerCase(); if (S.dash) $('#mg-rows').innerHTML = rows() || '<tr><td colspan="8"><div class="cal-empty">No employees match.</div></td></tr>'; });
    window.addEventListener('st:nav', function (e) {
      var a = e.detail.action; S.date = a === 'today' ? BOOT.today : iso(addD(P(S.date), a === 'next' ? 1 : -1)); load();
    });
    window.addEventListener('st:goto-date', function (e) { e.preventDefault(); S.date = e.detail.date; load(); });
    var btns = document.querySelectorAll('[data-mg-view]');
    btns.forEach(function (b) {
      b.addEventListener('click', function () {
        var list = b.dataset.mgView === 'departments';
        $('#mg-overview').hidden = list; $('#mg-depts-view').hidden = !list;
        btns.forEach(function (x) { x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); });
        document.querySelectorAll('[data-st-nav]').forEach(function (n) { n.style.visibility = list ? 'hidden' : ''; });
        if (window.STShell) STShell.setTitle(list ? 'Management · Departments' : 'Management · ' + dayLabel(S.date));
        if (window.STDrawer) STDrawer.close();
      });
    });
  }

  frappe.ready(function () { bind(); load(); });
})();
