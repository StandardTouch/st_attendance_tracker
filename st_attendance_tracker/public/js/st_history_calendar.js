/* My History as a month calendar (www/my_history.html). Read-only: data comes from calendar_api.get_calendar_range. */
(function () {
  'use strict';

  var BOOT = window.ST_HIST;
  var MON = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  var DOW = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];
  var STATUS = {
    'Pending': { c: 's-pending', ic: '○', label: 'Pending' },
    'In Progress': { c: 's-prog', ic: '◐', label: 'In progress' },
    'Done': { c: 's-done', ic: '✓', label: 'Done' }
  };

  function $(s, r) { return (r || document).querySelector(s); }
  function pad(n) { return String(n).padStart(2, '0'); }
  function iso(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function P(s) { var p = s.split('-').map(Number); return new Date(p[0], p[1] - 1, p[2]); }
  function addD(d, n) { var x = new Date(d); x.setDate(x.getDate() + n); return x; }
  function toMin(t) { var p = String(t).split(':'); return (+p[0]) * 60 + (+p[1] || 0); }
  function fmFull(t) { var m = toMin(t); return ((Math.floor(m / 60) % 12) || 12) + ':' + pad(m % 60) + ' ' + (Math.floor(m / 60) < 12 ? 'AM' : 'PM'); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function dayLabel(s) { var d = P(s); return DOW[d.getDay()].charAt(0) + DOW[d.getDay()].slice(1).toLowerCase() + ', ' + MON[d.getMonth()].slice(0, 3) + ' ' + d.getDate() + ', ' + d.getFullYear(); }
  function hoursText(h) {
    h = +h || 0; if (h <= 0) return '';
    var total = Math.round(h * 60), hh = Math.floor(total / 60), mm = total % 60;
    return hh && mm ? hh + 'h ' + mm + 'm' : hh ? hh + 'h' : mm + 'm';
  }
  // net_hours is text like "8h 5m"
  function netMinutes(text) {
    var m = /(?:(\d+)h)?\s*(?:(\d+)m)?/.exec(String(text || ''));
    return m ? (+m[1] || 0) * 60 + (+m[2] || 0) : 0;
  }
  function serverMessage(xhr) {
    try {
      var json = xhr.responseJSON || JSON.parse(xhr.responseText || '{}');
      if (json._server_messages) return JSON.parse(json._server_messages).map(function (m) { return JSON.parse(m).message; }).join(' ').replace(/<[^>]+>/g, '');
    } catch (e) { /* fall through */ }
    return 'Could not load your history. Please try again.';
  }
  function api(method, args) {
    return new Promise(function (resolve, reject) {
      frappe.call({ method: method, args: args, silent: true, freeze: false, callback: function (r) { resolve(r.message); }, error: function (x) { reject(new Error(serverMessage(x))); } });
    });
  }

  var S = { cursor: P(BOOT.today), selected: null, logs: {}, tasks: [], addl: [] };

  function gridStart() { var first = new Date(S.cursor.getFullYear(), S.cursor.getMonth(), 1); return addD(first, -first.getDay()); }
  function load() {
    var a = gridStart();
    return api('st_attendance_tracker.calendar_api.get_calendar_range', { from_date: iso(a), to_date: iso(addD(a, 41)) }).then(function (r) {
      S.logs = {}; r.logs.forEach(function (l) { S.logs[l.date] = l; });
      S.tasks = r.tasks; S.addl = r.additional_work;
      render();
    });
  }

  function tasksOn(k) { return S.tasks.filter(function (t) { return t.date === k && STATUS[t.status]; }); }
  function addlOn(k) { return S.addl.filter(function (a) { return a.work_date === k; }); }

  function kpis() {
    var y = S.cursor.getFullYear(), mo = S.cursor.getMonth(), present = 0, late = 0, mins = 0, days = 0, done = 0, total = 0;
    Object.keys(S.logs).forEach(function (k) {
      var d = P(k), l = S.logs[k]; if (d.getMonth() !== mo || d.getFullYear() !== y || !l.morning_submitted) return;
      present++; if (l.is_late) late++;
      var n = netMinutes(l.net_hours); if (n) { mins += n; days++; }
    });
    S.tasks.forEach(function (t) { var d = P(t.date); if (d.getMonth() === mo && d.getFullYear() === y && STATUS[t.status]) { total++; if (t.status === 'Done') done++; } });
    var avg = days ? (mins / days / 60).toFixed(1) : '-';
    return [['Days present', present], ['Late arrivals', late], ['Avg. hours / day', avg], ['Tasks done', done + ' / ' + total]];
  }

  function title() { return 'My history · ' + MON[S.cursor.getMonth()] + ' ' + S.cursor.getFullYear(); }

  function render() {
    if (window.STShell) { STShell.setTitle(title()); STShell.setDate(S.selected ? P(S.selected) : S.cursor); }
    var k = kpis();
    $('#hist-kpis').innerHTML = k.map(function (x) { return '<div class="hist-kpi"><small>' + x[0] + '</small><b>' + x[1] + '</b></div>'; }).join('');
    var y = S.cursor.getFullYear(), mo = S.cursor.getMonth(), st = gridStart();
    var h = DOW.map(function (d) { return '<div class="cal-dow">' + d + '</div>'; }).join('');
    for (var i = 0; i < 42; i++) {
      var d = addD(st, i), key = iso(d), log = S.logs[key], chips = '';
      if (d.getDay() === 0 && !log) chips += '<span class="cal-more">Weekly off</span>';
      if (log && log.morning_submitted) {
        chips += '<button type="button" class="cal-chip att' + (log.is_late ? ' late' : '') + '" data-date="' + key + '">' + fmFull(log.login_time) + ' – ' + (log.logout_time ? fmFull(log.logout_time) : '…') + '</button>';
        if (log.is_late) chips += '<span class="cal-more" style="color:var(--st-accent-text)">Late</span>';
      }
      var ts = tasksOn(key);
      if (ts.length) {
        var done = ts.filter(function (t) { return t.status === 'Done'; }).length;
        chips += '<span class="cal-more">' + done + ' / ' + ts.length + ' tasks done</span>';
      }
      h += '<div class="cal-cell' + (d.getMonth() !== mo ? ' out' : '') + (key === BOOT.today ? ' today' : '') + (key === S.selected ? ' sel' : '') + '" data-date="' + key + '" tabindex="0" role="button" aria-label="' + esc(dayLabel(key)) + '"><span class="cal-num">' + d.getDate() + '</span>' + chips + '</div>';
    }
    $('#hist-month').innerHTML = h;
    renderDetail();
  }

  function renderDetail() {
    var box = $('#hist-detail');
    if (!S.selected) { box.innerHTML = '<div class="cal-empty">Select a day to see check-in, lunch, hours and tasks.</div>'; return; }
    var k = S.selected, log = S.logs[k], ts = tasksOn(k), aw = addlOn(k), html = '<h3>' + esc(dayLabel(k)) + '</h3>';
    if (!log || !log.morning_submitted) {
      html += '<div class="cal-card"><div class="cal-row cal-between"><b>' + (P(k).getDay() === 0 ? 'Weekly off' : 'No check-in recorded') + '</b><span class="cal-tag dash">' + (P(k).getDay() === 0 ? 'Off' : 'No record') + '</span></div></div>';
    } else {
      html += '<div class="cal-card"><div class="cal-row cal-between"><b>' + (log.is_late ? 'Late' : 'Present') + '</b><span class="cal-tag ' + (log.is_late ? 'red-line' : '') + '">' + (log.is_late ? 'Late' : 'On time') + '</span></div>' +
        '<ul class="cal-tl" style="margin-top:8px"><li><span>Check-in</span><span>' + fmFull(log.login_time) + (log.work_location ? ' · ' + esc(log.work_location) : '') + '</span></li>' +
        '<li><span>Lunch</span><span>' + (log.lunch_from && log.lunch_to ? fmFull(log.lunch_from) + ' – ' + fmFull(log.lunch_to) : '-') + '</span></li>' +
        '<li><span>Check-out</span><span>' + (log.logout_time ? fmFull(log.logout_time) : '-') + '</span></li>' +
        '<li><span>Hours worked</span><span>' + esc(log.net_hours || '-') + '</span></li>' +
        (log.half_day_session ? '<li><span>Half-day session</span><span>' + esc(log.half_day_session) + '</span></li>' : '') + '</ul></div>';
    }
    if (ts.length) {
      var done = ts.filter(function (t) { return t.status === 'Done'; }).length;
      html += '<div class="cal-row cal-between" style="margin:16px 0 6px"><b>Tasks</b><span class="cal-tag">' + done + ' / ' + ts.length + ' done</span></div>' +
        '<div class="cal-bar" style="margin-bottom:10px"><i style="width:' + done / ts.length * 100 + '%"></i></div>' +
        ts.map(function (t) {
          var st = STATUS[t.status];
          return '<div class="cal-task"><div class="cal-row cal-between" style="flex-wrap:nowrap;align-items:flex-start"><span style="overflow-wrap:anywhere">' + esc(t.description) + '</span><span class="cal-tag ' + st.c + '" style="flex:none">' + st.ic + ' ' + st.label + '</span></div>' +
            '<small>' + esc(t.project_name || '') + (t.project_name ? ' · ' : '') + esc(t.task_type || 'Task') + (t.estimated_time ? ' · est ' + hoursText(t.estimated_time) : '') + (t.actual_time ? ' · actual ' + hoursText(t.actual_time) : '') + '</small>' +
            (t.remarks ? '<small>' + esc(t.remarks) + '</small>' : '') + '</div>';
        }).join('');
    }
    if (aw.length) {
      html += '<div class="cal-row cal-between" style="margin:16px 0 6px"><b>Additional work</b><a href="/additional-work" style="color:var(--st-accent-text);font-weight:600">Open</a></div>' +
        aw.map(function (a) {
          var st = STATUS[a.status] || STATUS.Done;
          return '<div class="cal-task"><div class="cal-row cal-between" style="flex-wrap:nowrap;align-items:flex-start"><span style="overflow-wrap:anywhere">' + esc(a.description) + '</span><span class="cal-tag ' + st.c + '" style="flex:none">' + st.ic + ' ' + st.label + '</span></div>' +
            '<small>' + esc(a.project_name || '') + ' · ' + (a.login_time ? fmFull(a.login_time) : '') + ' – ' + (a.logout_time ? fmFull(a.logout_time) : '') + '</small></div>';
        }).join('');
    }
    if (!ts.length && !aw.length && log && log.morning_submitted) html += '<div class="cal-empty" style="margin-top:12px">No tasks logged this day.</div>';
    html += '<p class="cal-hint" style="margin-top:12px">Read-only. Past days cannot be edited.</p>';
    box.innerHTML = html;
  }

  function select(key) { S.selected = key; S.cursor = P(key); render(); }
  function bind() {
    $('#hist-month').addEventListener('click', function (e) { var c = e.target.closest('.cal-cell'); if (c) select(c.dataset.date); });
    $('#hist-month').addEventListener('keydown', function (e) { if (e.key === 'Enter') { var c = e.target.closest('.cal-cell'); if (c) select(c.dataset.date); } });
    window.addEventListener('st:nav', function (e) {
      var a = e.detail.action;
      if (a === 'today') { S.cursor = P(BOOT.today); S.selected = BOOT.today; }
      else S.cursor = new Date(S.cursor.getFullYear(), S.cursor.getMonth() + (a === 'next' ? 1 : -1), 1);
      load().catch(fail);
    });
    window.addEventListener('st:goto-date', function (e) {
      e.preventDefault(); var key = e.detail.date, sameMonth = P(key).getMonth() === S.cursor.getMonth() && P(key).getFullYear() === S.cursor.getFullYear();
      S.selected = key; S.cursor = P(key); if (sameMonth) render(); else load().catch(fail);
    });
    $$toggle();
  }
  // Month calendar <-> the original day-by-day list
  function $$toggle() {
    var listLoaded = false, cal = $('#hist-cal'), list = $('#hist-list-view'), btns = document.querySelectorAll('[data-hist-view]');
    btns.forEach(function (b) {
      b.addEventListener('click', function () {
        var showList = b.dataset.histView === 'list';
        cal.hidden = showList; list.hidden = !showList;
        if (showList && !listLoaded && window.loadHistory) { listLoaded = true; window.loadHistory(true); }
        btns.forEach(function (x) { x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); });
        if (window.STShell) STShell.setTitle(showList ? 'My History · List' : title());
        document.querySelectorAll('[data-st-nav]').forEach(function (n) { n.style.visibility = showList ? 'hidden' : ''; });
      });
    });
  }
  function fail(e) { $('#hist-detail').innerHTML = '<div class="cal-empty">' + esc(e.message) + '</div>'; }

  frappe.ready(function () {
    bind();
    S.selected = BOOT.today;
    load().catch(fail);
  });
})();
