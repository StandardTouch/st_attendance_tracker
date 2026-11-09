/* Team Dashboard: week matrix (employees x days) for a Team Leader. Data: calendar_api.get_team_week.
 * The original card list stays under the List toggle; "Assign task" reuses its modal (openAssignModal).
 */
(function () {
  'use strict';

  var BOOT = window.ST_TEAM;
  var MON = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  var DOW = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];
  var STATUS = {
    'Pending': { c: 's-pending', ic: '○' },
    'In Progress': { c: 's-prog', ic: '◐' },
    'Done': { c: 's-done', ic: '✓' }
  };
  var DAY_STATUS = {
    checked_in: { label: 'Checked in', cls: '' }, late: { label: 'Late', cls: 'miss' }, eod_done: { label: 'Checked out', cls: '' },
    missing: { label: 'Missing', cls: 'miss' }, leave: { label: 'On leave', cls: 'mute' }, off: { label: 'Weekly off', cls: 'mute' }
  };

  function $(s, r) { return (r || document).querySelector(s); }
  function pad(n) { return String(n).padStart(2, '0'); }
  function iso(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function P(s) { var p = s.split('-').map(Number); return new Date(p[0], p[1] - 1, p[2]); }
  function addD(d, n) { var x = new Date(d); x.setDate(x.getDate() + n); return x; }
  function sow(d) { return addD(d, -d.getDay()); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function toMin(t) { var p = String(t).split(':'); return (+p[0]) * 60 + (+p[1] || 0); }
  function fm(t) { var m = toMin(t); return ((Math.floor(m / 60) % 12) || 12) + ':' + pad(m % 60) + ' ' + (Math.floor(m / 60) < 12 ? 'AM' : 'PM'); }
  function serverMessage(xhr) {
    try {
      var json = xhr.responseJSON || JSON.parse(xhr.responseText || '{}');
      if (json._server_messages) return JSON.parse(json._server_messages).map(function (m) { return JSON.parse(m).message; }).join(' ').replace(/<[^>]+>/g, '');
    } catch (e) { /* fall through */ }
    return 'Could not load the team week.';
  }

  var S = { cursor: P(BOOT.today), data: null, filter: 'all', q: '' };
  var loadGen = 0;

  function load() {
    var a = sow(S.cursor), my = ++loadGen;
    $('#tm-matrix').innerHTML = '<div class="cal-empty" style="grid-column:1/-1;margin:12px">Loading team…</div>';
    return new Promise(function (resolve, reject) {
      frappe.call({
        method: 'st_attendance_tracker.calendar_api.get_team_week', args: { from_date: iso(a), to_date: iso(addD(a, 6)) }, silent: true, freeze: false,
        callback: function (r) { resolve(r.message); }, error: function (x) { reject(new Error(serverMessage(x))); }
      });
    }).then(function (data) { if (my === loadGen) { S.data = data; render(); } }).catch(function (e) { if (my === loadGen) $('#tm-matrix').innerHTML = '<div class="cal-empty" style="grid-column:1/-1;margin:12px">' + esc(e.message) + '</div>'; });
  }

  function title() {
    var wk = sow(S.cursor), we = addD(wk, 6);
    return 'Team · ' + (wk.getMonth() === we.getMonth() ? MON[wk.getMonth()] + ' ' + wk.getDate() + ' – ' + we.getDate() : MON[wk.getMonth()].slice(0, 3) + ' ' + wk.getDate() + ' – ' + MON[we.getMonth()].slice(0, 3) + ' ' + we.getDate()) + ', ' + we.getFullYear();
  }

  // Sunday is the weekly off: a day with no check-in is not "missing".
  function statusOf(day, dateKey) { return day && day.status === 'missing' && P(dateKey).getDay() === 0 ? 'off' : (day ? day.status : null); }

  function focusDate() { var t = BOOT.today, a = iso(sow(S.cursor)), b = iso(addD(sow(S.cursor), 6)); return t >= a && t <= b ? t : a; }

  function visible(emp) {
    if (S.q && (emp.employee_name + ' ' + (emp.designation || '')).toLowerCase().indexOf(S.q) < 0) return false;
    if (S.filter === 'all') return true;
    var d = emp.days[focusDate()]; return !!d && d.status === S.filter;
  }

  function render() {
    if (window.STShell) { STShell.setTitle(title()); STShell.setDate(S.cursor); }
    var emps = S.data.employees, fd = focusDate(), count = function (k) { return emps.filter(function (e) { var d = e.days[fd]; return d && d.status === k; }).length; };
    var inCount = count('checked_in') + count('late') + count('eod_done');
    $('#tm-kpis').innerHTML =
      '<div class="tm-kpi"><small>Team members</small><b>' + emps.length + '</b></div>' +
      '<div class="tm-kpi"><small>Checked in</small><b>' + inCount + '</b></div>' +
      '<div class="tm-kpi"><small>Checked out</small><b>' + count('eod_done') + '</b></div>' +
      '<div class="tm-kpi"><small>Late</small><b>' + count('late') + '</b></div>' +
      '<div class="tm-kpi hot"><small>Missing</small><b>' + count('missing') + '</b></div>';
    var days = [0, 1, 2, 3, 4, 5, 6].map(function (i) { return addD(sow(S.cursor), i); });
    var h = '<div class="tm-h" style="text-align:left">Team member</div>' + days.map(function (d) {
      return '<div class="tm-h' + (iso(d) === BOOT.today ? ' today' : '') + '">' + DOW[d.getDay()] + ' ' + d.getDate() + '</div>';
    }).join('');
    var rows = emps.filter(visible);
    rows.forEach(function (emp) {
      var td = emp.days[focusDate()], st = td && statusOf(td, focusDate()) ? DAY_STATUS[statusOf(td, focusDate())] : null;
      var initials = emp.employee_name.split(' ').filter(Boolean).slice(0, 2).map(function (w) { return w[0]; }).join('').toUpperCase();
      h += '<div class="tm-emp" role="button" tabindex="0" data-emp="' + esc(emp.name) + '" title="Open ' + esc(emp.employee_name) + '\'s record"><span class="tm-av">' + esc(initials) + '</span><div style="min-width:0"><b style="overflow-wrap:anywhere">' + esc(emp.employee_name) + '</b>' +
        '<div style="color:var(--st-muted);font-size:11px">' + esc(emp.designation || emp.department || '') + '</div>' + (st ? '<div class="tm-st ' + st.cls + '">' + st.label + (td.login_time ? ' · ' + fm(td.login_time) : '') + '</div>' : '') +
        '<button type="button" class="cal-btn" data-assign="' + esc(emp.name) + '" style="height:26px;padding:0 10px;font-size:11px;margin-top:4px">Assign task</button></div></div>';
      days.forEach(function (d) {
        var k = iso(d), day = emp.days[k], chips = '';
        if (day && day.status) {
          var s = DAY_STATUS[statusOf(day, k)];
          chips += '<span class="tm-st ' + s.cls + '">' + s.label + (day.status === 'eod_done' && day.net_hours ? ' · ' + esc(day.net_hours) : '') + (day.login_time && day.status !== 'eod_done' ? ' · ' + fm(day.login_time) : '') + '</span>';
          var tasks = day.tasks.filter(function (t) { return STATUS[t.status]; });
          tasks.slice(0, 3).forEach(function (t) { var mv = k === BOOT.today && t.status === 'Pending' && t.task_type !== 'Recurring'; chips += '<span class="cal-chip tm-chip ' + STATUS[t.status].c + '"' + (mv ? ' draggable="true" data-task="' + esc(t.name) + '" data-from="' + esc(emp.name) + '"' : '') + ' title=""' + esc(t.description) + ' (' + esc(t.status) + ')' + (mv ? ' - drag to hand over' : '') + '">' + STATUS[t.status].ic + ' ' + esc(t.description) + '</span>'; });
          if (tasks.length > 3) chips += '<span class="cal-more">' + (tasks.length - 3) + ' more</span>';
          if (day.extra_hours) chips += '<span class="cal-tag" title="Additional work logged">+' + day.extra_hours + 'h extra</span>';
        }
        h += '<div class="tm-cell' + (k === BOOT.today ? ' today' : '') + '" data-emp="' + esc(emp.name) + '" data-date="' + k + '">' + chips + '</div>';
      });
    });
    if (!rows.length) h += '<div class="cal-empty" style="grid-column:1/-1;margin:12px">' + (emps.length ? 'No team members match this filter.' : 'No team members found.') + '</div>';
    $('#tm-matrix').innerHTML = h;
  }

  // Hand a not-yet-started task to another team member (server re-checks every rule).
  function reassign(taskName, toEmp) {
    return new Promise(function (resolve, reject) {
      frappe.call({
        method: 'st_attendance_tracker.calendar_api.reassign_task', args: { name: taskName, to_employee: toEmp }, type: 'POST', silent: true, freeze: false,
        callback: function (r) {
          var to = S.data.employees.filter(function (e) { return e.name === toEmp; })[0];
          toast('Handed over to ' + (to ? to.employee_name : 'teammate') + '.', function () { reassign(r.message.task, r.message.from_employee).then(load); });
          load(); resolve(r.message);
        },
        error: function (x) { toast(serverMessage(x), null, true); reject(new Error('failed')); }
      });
    });
  }
  function toast(msg, undo, isErr) {
    document.querySelectorAll('.cal-toast').forEach(function (t) { t.remove(); });
    var t = document.createElement('div'); t.className = 'cal-toast' + (isErr ? ' err' : ''); t.setAttribute('role', isErr ? 'alert' : 'status'); t.innerHTML = '<span>' + esc(msg) + '</span>';
    if (undo) { var b = document.createElement('button'); b.type = 'button'; b.textContent = 'Undo'; b.onclick = function () { t.remove(); undo(); }; t.appendChild(b); }
    document.body.appendChild(t); setTimeout(function () { t.remove(); }, isErr ? 7000 : 5000);
  }

  function openRecord(empName, date) {
    var emp = S.data && S.data.employees.filter(function (e) { return e.name === empName; })[0]; if (!emp) return;
    STDrawer.open(emp, date, { today: BOOT.today, team: S.data.employees, onReassign: reassign, onAssign: function (e) { if (window.openAssignModal) window.openAssignModal(e.name, e.employee_name); } });
  }

  function bind() {
    var m = $('#tm-matrix');
    m.addEventListener('click', function (e) {
      var a = e.target.closest('[data-assign]');
      if (a) { e.stopPropagation(); if (window.openAssignModal) { var emp = S.data.employees.filter(function (x) { return x.name === a.dataset.assign; })[0]; window.openAssignModal(emp.name, emp.employee_name); } return; }
      var cell = e.target.closest('[data-emp]'); if (cell) openRecord(cell.dataset.emp, cell.dataset.date || focusDate());
    });
    m.addEventListener('keydown', function (e) { if (e.key === 'Enter') { var c = e.target.closest('[data-emp]'); if (c && !e.target.closest('[data-assign]')) openRecord(c.dataset.emp, c.dataset.date || focusDate()); } });
    m.addEventListener('dragstart', function (e) { var c = e.target.closest('[data-task]'); if (c) e.dataTransfer.setData('text/plain', 'tm|' + c.dataset.task + '|' + c.dataset.from); });
    // Delegated, because the grid is re-rendered on every load.
    m.addEventListener('dragover', function (e) { var cell = e.target.closest('.tm-cell'); if (cell && cell.dataset.date === BOOT.today) { e.preventDefault(); cell.classList.add('drop'); } });
    m.addEventListener('dragleave', function (e) { var cell = e.target.closest('.tm-cell'); if (cell) cell.classList.remove('drop'); });
    m.addEventListener('drop', function (e) {
      var cell = e.target.closest('.tm-cell'); if (!cell) return; cell.classList.remove('drop');
      var p = e.dataTransfer.getData('text/plain').split('|'); if (p[0] !== 'tm' || cell.dataset.date !== BOOT.today || cell.dataset.emp === p[2]) return;
      e.preventDefault(); reassign(p[1], cell.dataset.emp).catch(function () { /* the toast already explains */ });
    });
    $('#tm-q').addEventListener('input', function (e) { S.q = e.target.value.trim().toLowerCase(); if (S.data) render(); });
    $('#tm-filter').addEventListener('change', function (e) { S.filter = e.target.value; if (S.data) render(); });
    window.addEventListener('st:nav', function (e) {
      var a = e.detail.action;
      S.cursor = a === 'today' ? P(BOOT.today) : addD(S.cursor, a === 'next' ? 7 : -7); load();
    });
    window.addEventListener('st:goto-date', function (e) { e.preventDefault(); S.cursor = P(e.detail.date); load(); });
    // Matrix <-> original card list
    var btns = document.querySelectorAll('[data-team-view]');
    btns.forEach(function (b) {
      b.addEventListener('click', function () {
        var list = b.dataset.teamView === 'list';
        $('#tm-week').hidden = list; $('#tm-list-view').hidden = !list;
        btns.forEach(function (x) { x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); });
        document.querySelectorAll('[data-st-nav]').forEach(function (n) { n.style.visibility = list ? 'hidden' : ''; });
        if (window.STShell) STShell.setTitle(list ? 'Team Dashboard · List' : title());
        if (window.STDrawer) STDrawer.close();
      });
    });
    // A team member saved an ad-hoc task: refresh like the original list does.
    try { if (frappe.realtime && frappe.realtime.on) frappe.realtime.on('st_task_added', function () { if (!$('#tm-week').hidden) load(); }); } catch (err) { /* realtime is optional */ }
    if (window.location.hash === '#assign') window.__tmPick = true;
  }

  // "Create > Assign team task": choose who, then reuse the original assign modal.
  function pickAssignee() {
    var d = $('#tm-pick'); if (!d || !S.data) return;
    d.innerHTML = '<form method="dialog"><h3>Assign a task</h3><label class="cal-f">Team member<select id="tm-pick-sel">' + S.data.employees.map(function (e) { return '<option value="' + esc(e.name) + '">' + esc(e.employee_name) + '</option>'; }).join('') + '</select></label>' +
      '<div class="cal-actions"><button type="button" class="cal-btn" id="tm-pick-x">Cancel</button><button type="submit" class="cal-btn primary">Continue</button></div></form>';
    d.showModal();
    $('#tm-pick-x').onclick = function () { d.close(); };
    $('form', d).onsubmit = function (e) { e.preventDefault(); var emp = S.data.employees.filter(function (x) { return x.name === $('#tm-pick-sel').value; })[0]; d.close(); window.openAssignModal(emp.name, emp.employee_name); };
  }

  // Fired by the original assign modal so the grid and an open record show the new task.
  window.addEventListener('st:team-changed', function () { load().then(function () { if (window.STDrawer) STDrawer.refresh(); }); });

  frappe.ready(function () {
    bind();
    load().then(function () { if (window.__tmPick && S.data && S.data.employees.length) pickAssignee(); });
  });
})();
