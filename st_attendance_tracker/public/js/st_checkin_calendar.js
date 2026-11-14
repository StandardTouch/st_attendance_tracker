/* Daily Check-in, calendar layout (www/checkin_calendar.html).
 * Talks to the existing check-in endpoints (api.py) plus calendar_api.py. Server rules stay on the server:
 * this file only mirrors them to explain why something is disabled.
 */
(function () {
  'use strict';

  var BOOT = window.ST_CAL;
  var API = 'st_attendance_tracker.api.';
  var CAL = 'st_attendance_tracker.calendar_api.';
  var H0 = 6, H1 = 22, PPH = 52, PPM = PPH / 60;
  var STATUS = {
    'Pending': { c: 's-pending', ic: '○', label: 'Pending' },
    'In Progress': { c: 's-prog', ic: '◐', label: 'In progress' },
    'Done': { c: 's-done', ic: '✓', label: 'Done' }
  };
  var KIND = { 'Planned': 'Planned task', 'Ad-hoc': 'Ad-hoc task', 'Recurring': 'Recurring' };
  var MON = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  var DOW = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];
  var LOCK_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>';

  /* ── helpers ── */
  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  function pad(n) { return String(n).padStart(2, '0'); }
  function iso(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function P(s) { var p = s.split('-').map(Number); return new Date(p[0], p[1] - 1, p[2]); }
  function addD(d, n) { var x = new Date(d); x.setDate(x.getDate() + n); return x; }
  function sow(d) { return addD(d, -d.getDay()); }
  function toMin(t) { var p = String(t).split(':'); return (+p[0]) * 60 + (+p[1] || 0); }
  function toT(m) { return pad(Math.floor(m / 60)) + ':' + pad(m % 60); }
  function nowMin() { var n = new Date(); return n.getHours() * 60 + n.getMinutes(); }
  function snap(m) { return Math.round(m / 15) * 15; }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function fm(m) { var h = Math.floor(m / 60), mi = m % 60; return ((h % 12) || 12) + (mi ? ':' + pad(mi) : '') + ' ' + (h < 12 ? 'AM' : 'PM'); }
  function fmFull(t) { var m = typeof t === 'number' ? t : toMin(t); return ((Math.floor(m / 60) % 12) || 12) + ':' + pad(m % 60) + ' ' + (Math.floor(m / 60) < 12 ? 'AM' : 'PM'); }
  function dayLabel(s) { var d = P(s); return DOW[d.getDay()].charAt(0) + DOW[d.getDay()].slice(1).toLowerCase() + ', ' + MON[d.getMonth()].slice(0, 3) + ' ' + d.getDate(); }
  function hoursText(h) {
    h = +h || 0; if (h <= 0) return '';
    var total = Math.round(h * 60), hh = Math.floor(total / 60), mm = total % 60;
    return hh && mm ? hh + 'h ' + mm + 'm' : hh ? hh + 'h' : mm + 'm';
  }
  // The server reads a bare number as minutes; people type hours.
  function normDuration(s) { s = String(s || '').trim(); return /^\d+(\.\d+)?$/.test(s) ? s + 'h' : s; }
  function durationHours(task) {
    var h = +task.estimated_time || +task.actual_time || 1;
    return clamp(h, 0.25, 8);
  }

  /* ── server calls ── */
  function serverMessage(xhr) {
    try {
      var json = xhr.responseJSON || JSON.parse(xhr.responseText || '{}');
      if (json._server_messages) {
        var list = JSON.parse(json._server_messages);
        return list.map(function (m) { return JSON.parse(m).message; }).join(' ').replace(/<[^>]+>/g, '');
      }
      if (json.exception) return String(json.exception).split(': ').slice(1).join(': ') || json.exception;
    } catch (e) { /* fall through */ }
    return 'Something went wrong. Please try again.';
  }
  function api(method, args, post) {
    return new Promise(function (resolve, reject) {
      frappe.call({
        method: method, args: args || {}, type: post ? 'POST' : 'GET', silent: true, freeze: false,
        callback: function (r) { resolve(r.message); },
        error: function (xhr) { reject(new Error(serverMessage(xhr))); }
      });
    });
  }

  /* ── state ── */
  var S = {
    view: window.innerWidth < 900 ? 'day' : 'week',
    cursor: P(BOOT.today),
    logs: {}, tasks: [], addl: [], sched: [],   // visible range (sched = backlog items given a day)
    day: { logs: {}, tasks: [] },          // open day + today (side panel)
    backlog: [], page: null, hide: {}, project: ''
  };
  // Remembered per browser: calendar view and which statuses are hidden.
  function remember(key, value) { try { if (value === undefined) return JSON.parse(window.localStorage.getItem('st.cal.' + key)); window.localStorage.setItem('st.cal.' + key, JSON.stringify(value)); } catch (e) { /* not remembered */ } return null; }
  (function () {
    var v = remember('view'); if (['day', 'week', 'month'].indexOf(v) >= 0 && window.innerWidth >= 900) S.view = v;
    var h = remember('hide'); if (h && typeof h === 'object') S.hide = h;
  })();
  var urlDate = new URLSearchParams(window.location.search).get('date');
  if (urlDate && /^\d{4}-\d{2}-\d{2}$/.test(urlDate)) S.cursor = P(urlDate);

  function openLog() { return S.day.logs[BOOT.active_date] || null; }
  function morningDone() { var l = openLog(); return !!(l && l.morning_submitted); }
  function eodDone() { var l = openLog(); return !!(l && l.eod_submitted); }
  function todayLog() { return S.day.logs[BOOT.today] || null; }

  function rangeFor() {
    var c = S.cursor;
    if (S.view === 'day') return [iso(c), iso(c)];
    if (S.view === 'week') return [iso(sow(c)), iso(addD(sow(c), 6))];
    var first = new Date(c.getFullYear(), c.getMonth(), 1), start = addD(first, -first.getDay());
    return [iso(start), iso(addD(start, 41))];
  }

  function loadAll(first) {
    var pageP = (first || !S.page) ? api(API + 'get_page_state') : Promise.resolve(S.page);
    return pageP.then(function (page) {
      S.page = page;
      if (page) page._reset_until = Date.now() + (page.reset_seconds_left || 0) * 1000;
      var r = rangeFor(), dayFrom = BOOT.active_date < BOOT.today ? BOOT.active_date : BOOT.today;
      return Promise.all([
        api(CAL + 'get_calendar_range', { from_date: r[0], to_date: r[1] }),
        api(CAL + 'get_calendar_range', { from_date: dayFrom, to_date: BOOT.today }),
        api(API + 'get_task_backlog')
      ]);
    }).then(function (res) {
      S.logs = indexLogs(res[0].logs); S.tasks = res[0].tasks; S.addl = res[0].additional_work; S.sched = res[0].scheduled_backlog || [];
      S.day = { logs: indexLogs(res[1].logs), tasks: res[1].tasks };
      S.backlog = res[2] || [];
      render();
    });
  }
  function indexLogs(list) { var m = {}; list.forEach(function (l) { m[l.date] = l; }); return m; }

  /* ── rules mirrored from the server ── */
  function logFor(date) { return S.logs[date] || S.day.logs[date] || null; }
  function isLocked(t) {
    var log = logFor(t.date);
    if (log && log.eod_submitted) return true;
    if (t.date < BOOT.today) return !(t.date === BOOT.active_date && log && !log.eod_submitted);
    return false;
  }
  function lockWhy(t) { return t.date < BOOT.today ? 'Past day: tasks are read-only.' : 'You have checked out: tasks are locked.'; }
  function backlogBlock(t) {
    if (isLocked(t)) return lockWhy(t);
    if (t.task_type === 'Recurring') return "Recurring tasks can't be moved. Deactivate the template instead.";
    if (t.status === 'Done') return "Completed tasks can't be moved to the backlog.";
    return '';
  }
  function canChangeStatus(t) {
    var log = logFor(t.date);
    return !isLocked(t) && !!(log && log.morning_submitted);
  }

  /* ── layout of tasks on a day ── */
  function dayEvents(date) {
    var log = logFor(date), list = [];
    var tasks = S.tasks.filter(function (t) { return t.date === date && STATUS[t.status]; });
    var lunchFrom = log && log.lunch_from ? toMin(log.lunch_from) : 13 * 60, lunchTo = log && log.lunch_to ? toMin(log.lunch_to) : 14 * 60;
    var planFrom = log && log.login_time ? toMin(log.login_time) : (date === BOOT.today ? Math.max(9 * 60, nowMin()) : 9 * 60); // before check-in, today's plan starts from now
    var cursor = Math.max(H0 * 60, Math.ceil(planFrom / 15) * 15);
    // Tasks with no saved start time are laid out one after another. When there are more of them than the
    // day can hold (a long carried-over list), shrink their blocks evenly instead of piling them at the end.
    var wanted = tasks.filter(function (t) { return !t.start_time; }).reduce(function (n, t) { return n + Math.round(durationHours(t) * 60 / 15) * 15; }, 0);
    var room = Math.max(60, H1 * 60 - cursor - Math.max(0, lunchTo - lunchFrom));
    var shrink = wanted > room ? room / wanted : 1;
    tasks.forEach(function (t) {
      var dur = Math.round(durationHours(t) * 60 / 15) * 15, start;
      if (!t.start_time && shrink < 1) dur = Math.max(15, Math.floor(dur * shrink / 15) * 15);
      if (t.start_time) start = toMin(t.start_time);
      else {
        if (cursor < lunchTo && cursor + dur > lunchFrom) cursor = lunchTo;
        start = Math.min(cursor, H1 * 60 - dur); cursor = start + dur;
      }
      list.push({ id: t.name, kind: 'task', task: t, date: date, start: start, dur: dur, status: t.status });
    });
    S.addl.filter(function (a) { return a.work_date === date && STATUS[a.status || 'Done']; }).forEach(function (a) {
      var st = a.login_time ? toMin(a.login_time) : 16 * 60, en = a.logout_time ? toMin(a.logout_time) : st + 60;
      list.push({ id: 'aw:' + a.name, kind: 'addl', addl: a, date: date, start: st, dur: Math.max(15, en - st), status: a.status || 'Done' });
    });
    S.sched.filter(function (b) { return b.scheduled_for === date; }).forEach(function (b) {
      var h = parseHours(b.estimated_time), dur = Math.round(((h && h > 0 ? h : 1) * 60) / 15) * 15, st = b.start_time ? toMin(b.start_time) : 9 * 60;
      list.push({ id: 'sb:' + b.name, kind: 'sched', item: b, date: date, start: clamp(st, H0 * 60, H1 * 60 - dur), dur: dur, status: 'Pending' });
    });
    return list.filter(function (e) { return !S.hide[e.status] && (!S.project || ((e.kind === 'addl' ? e.addl.project_name : e.kind === 'sched' ? e.item.project_name : e.task.project_name) || '') === S.project); });
  }
  function layoutDay(evs) {
    evs.sort(function (a, b) { return a.start - b.start || b.dur - a.dur; });
    var cur = null, end = 0, clusters = [];
    evs.forEach(function (e) {
      if (!cur || e.start >= end) { cur = { items: [], lanes: [] }; clusters.push(cur); end = 0; }
      var l = cur.lanes.findIndex(function (x) { return x <= e.start; });
      if (l < 0) { l = cur.lanes.length; cur.lanes.push(0); }
      cur.lanes[l] = e.start + e.dur; e._l = l; cur.items.push(e); end = Math.max(end, e.start + e.dur);
    });
    clusters.forEach(function (c) { c.items.forEach(function (e) { e._n = c.lanes.length; }); });
  }
  var eventIndex = {};

  /* ── rendering: main area ── */
  function title() {
    var c = S.cursor;
    if (S.view === 'day') return MON[c.getMonth()] + ' ' + c.getDate() + ', ' + c.getFullYear();
    if (S.view === 'month') return MON[c.getMonth()] + ' ' + c.getFullYear();
    var wk = sow(c), we = addD(wk, 6);
    return wk.getMonth() === we.getMonth() ? MON[wk.getMonth()] + ' ' + wk.getFullYear() : MON[wk.getMonth()].slice(0, 3) + ' – ' + MON[we.getMonth()].slice(0, 3) + ' ' + we.getFullYear();
  }
  function render() {
    eventIndex = {};
    if (window.STShell) { STShell.setTitle(title()); STShell.setView(S.view); STShell.setDate(S.cursor); }
    var main = $('#cal-main');
    if (S.view === 'month') main.innerHTML = monthHTML();
    else main.innerHTML = gridHTML(S.view === 'day' ? [S.cursor] : [0, 1, 2, 3, 4, 5, 6].map(function (i) { return addD(sow(S.cursor), i); }));
    if (S.view === 'month') bindMonth(main); else bindGrid(main);
    main.insertAdjacentHTML('afterbegin', totalsHTML());
    renderProjects();
    renderSide();
    renderDrafts();
  }

  // net_hours is text such as "8h 5m"
  function netMinutes(text) { var m = /(?:(\d+)h)?\s*(?:(\d+)m)?/.exec(String(text || '')); return m ? (+m[1] || 0) * 60 + (+m[2] || 0) : 0; }
  function totalsHTML() {
    // Month totals cover the month itself, not the leading/trailing days the grid shows (matches My History).
    var r = S.view === 'month' ? [iso(new Date(S.cursor.getFullYear(), S.cursor.getMonth(), 1)), iso(new Date(S.cursor.getFullYear(), S.cursor.getMonth() + 1, 0))] : rangeFor(), net = 0, days = 0, extra = 0, done = 0, total = 0;
    Object.keys(S.logs).forEach(function (k) { var l = S.logs[k]; if (k < r[0] || k > r[1] || !l.morning_submitted) return; days++; net += netMinutes(l.net_hours); });
    S.addl.forEach(function (a) { if (a.work_date >= r[0] && a.work_date <= r[1]) extra += +a.hours_spent || 0; });
    S.tasks.forEach(function (t) { if (t.date >= r[0] && t.date <= r[1] && STATUS[t.status]) { total++; if (t.status === 'Done') done++; } });
    var label = S.view === 'day' ? 'today' : S.view === 'week' ? 'this week' : 'this month';
    function tile(name, value) { return '<div class="cal-total"><small>' + name + '</small><b>' + value + '</b></div>'; }
    return '<div class="cal-totals" id="cal-totals" aria-label="Totals ' + label + '">' + tile('Days present', days) + tile('Net hours', net ? hoursText(net / 60) : '-') + tile('Extra hours', extra ? hoursText(extra) : '-') + tile('Tasks done', done + ' / ' + total) + '</div>';
  }
  function renderProjects() {
    var sel = $('#cal-project'); if (!sel) return;
    var names = {}; S.tasks.forEach(function (t) { if (t.project_name) names[t.project_name] = 1; }); S.addl.forEach(function (a) { if (a.project_name) names[a.project_name] = 1; });
    if (S.project) names[S.project] = 1;
    sel.innerHTML = '<option value="">All projects</option>' + Object.keys(names).sort().map(function (n) { return '<option value="' + esc(n) + '"' + (n === S.project ? ' selected' : '') + '>' + esc(n) + '</option>'; }).join('');
  }

  function eventHTML(e) {
    eventIndex[e.id] = e;
    var st = STATUS[e.status], top = (e.start - H0 * 60) * PPM, h = Math.max(e.dur * PPM - 2, 18), w = 100 / e._n;
    // A short block has no room for a resize handle (it would swallow drags), so only tall blocks get one.
    var lk = e.kind === 'task' && isLocked(e.task), ro = e.kind === 'addl';
    var label = e.kind === 'addl' ? e.addl.description : e.kind === 'sched' ? e.item.description : e.task.description;
    var sub = e.kind === 'addl' ? 'Additional work' : e.kind === 'sched' ? 'Scheduled' : (e.task.project_name || KIND[e.task.task_type] || 'Task');
    return '<div class="cal-ev ' + st.c + (lk ? ' lock' : '') + (ro ? ' readonly' : '') + (e.kind === 'sched' ? ' sched' : '') + '" tabindex="0" role="button" data-id="' + esc(e.id) + '" aria-label="' + esc(label) + ', ' + e.status + (lk ? ', locked' : '') + '"' +
      (lk ? ' title="' + esc(lockWhy(e.task)) + '"' : '') +
      ' style="top:' + top + 'px;height:' + h + 'px;left:calc(' + e._l * w + '% + 2px);width:calc(' + w + '% - 4px)">' + st.ic + ' ' + (lk ? LOCK_SVG + ' ' : '') + esc(label) +
      (e.dur >= 45 ? '<small>' + fm(e.start) + ' – ' + fm(e.start + e.dur) + ' · ' + esc(sub) + '</small>' : '') +
      (lk || ro || h < 30 ? '' : '<div class="cal-rz"></div>') + '</div>';
  }
  function gridHTML(days) {
    var n = days.length, head = '<div class="cal-head" style="--n:' + n + '" id="cal-head"><div></div>';
    days.forEach(function (d) {
      var k = iso(d);
      head += '<div class="cal-dh' + (k === BOOT.today ? ' today' : '') + '"><small>' + DOW[d.getDay()] + '</small><span>' + d.getDate() + '</span></div>';
    });
    head += '</div>';
    var body = '<div class="cal-scroll" id="cal-scroll"><div class="cal-in" style="--n:' + n + ';--hh:' + PPH + 'px;--minw:' + (n > 1 ? 720 : 0) + 'px"><div class="cal-gut" style="height:' + (H1 - H0) * PPH + 'px">';
    for (var h = H0 + 1; h < H1; h++) body += '<span style="top:' + (h - H0) * PPH + 'px">' + fm(h * 60) + '</span>';
    body += '</div>';
    days.forEach(function (d) {
      var k = iso(d), evs = dayEvents(k), log = logFor(k);
      layoutDay(evs);
      body += '<div class="cal-col" data-date="' + k + '" style="height:' + (H1 - H0) * PPH + 'px">';
      evs.forEach(function (e) { body += eventHTML(e); });
      if (k === BOOT.today) {
        var nm = nowMin();
        if (nm >= H0 * 60 && nm < H1 * 60) body += '<div class="cal-now" style="top:' + (nm - H0 * 60) * PPM + 'px"></div>';
      }
      if (log && log.morning_submitted && log.login_time) {
        body += '<div class="cal-mark" style="top:' + clamp((toMin(log.login_time) - H0 * 60) * PPM - 22, 0, 9999) + 'px">Checked in ' + fmFull(log.login_time) + (log.work_location ? ' · ' + esc(log.work_location) : '') + '</div>';
      }
      if (log && log.eod_submitted && log.logout_time) {
        body += '<div class="cal-mark" style="top:' + clamp((toMin(log.logout_time) - H0 * 60) * PPM - 2, 0, 9999) + 'px">Checked out ' + fmFull(log.logout_time) + '</div>';
      }
      body += '</div>';
    });
    return head + body + '</div></div>';
  }
  function monthHTML() {
    var y = S.cursor.getFullYear(), mo = S.cursor.getMonth(), first = new Date(y, mo, 1), st = addD(first, -first.getDay());
    var h = '<div class="cal-month">' + DOW.map(function (d) { return '<div class="cal-dow">' + d + '</div>'; }).join('');
    for (var i = 0; i < 42; i++) {
      var d = addD(st, i), k = iso(d), log = logFor(k);
      h += '<div class="cal-cell' + (d.getMonth() !== mo ? ' out' : '') + (k === BOOT.today ? ' today' : '') + '" data-date="' + k + '"><span class="cal-num">' + d.getDate() + '</span>';
      if (log && log.morning_submitted) {
        h += '<span class="cal-chip att' + (log.is_late ? ' late' : '') + '">' + (log.login_time ? fmFull(log.login_time) : '') + ' – ' + (log.logout_time ? fmFull(log.logout_time) : '…') + '</span>';
      }
      var evs = dayEvents(k).sort(function (a, b) { return a.start - b.start; });
      evs.slice(0, 3).forEach(function (e) {
        eventIndex[e.id] = e;
        var lbl = e.kind === 'addl' ? e.addl.description : e.kind === 'sched' ? e.item.description : e.task.description;
        h += '<button type="button" class="cal-chip ' + STATUS[e.status].c + (e.kind === 'sched' ? ' sched' : '') + '" data-id="' + esc(e.id) + '" title="' + esc(lbl) + ' (' + e.status + ')">' + STATUS[e.status].ic + ' ' + fm(e.start) + ' ' + esc(lbl) + '</button>';
      });
      if (evs.length > 3) h += '<span class="cal-more">' + (evs.length - 3) + ' more</span>';
      h += '</div>';
    }
    return h + '</div>';
  }

  /* ── main area interactions ── */
  function bindMonth(root) {
    root.onclick = function (e) {
      var chip = e.target.closest('.cal-chip[data-id]');
      if (chip) { var ev = eventIndex[chip.dataset.id]; if (ev) openPop(ev, chip); return; }
      var cell = e.target.closest('.cal-cell');
      if (cell) { S.cursor = P(cell.dataset.date); S.view = 'day'; refresh(); }
    };
  }
  function bindGrid(root) {
    var sc = $('#cal-scroll', root);
    sc.addEventListener('pointerdown', function (e) {
      var el = e.target.closest('.cal-ev'); if (!el || e.button) return;
      var ev = eventIndex[el.dataset.id]; if (!ev) return;
      var mode = e.target.closest('.cal-rz') ? 'resize' : 'move';
      var lk = ev.kind === 'addl' || (ev.kind === 'task' && isLocked(ev.task));
      var cols = ev.kind === 'sched' ? $$('.cal-col', sc) : null, r0 = el.parentElement.getBoundingClientRect(), col0 = cols ? cols.indexOf(el.parentElement) : -1;
      var sy = e.clientY, sx = e.clientX, moved = false, target = null, warned = false, onTray = false;
      var tray = document.getElementById('cal-tray');
      var small = $('small', el);
      el.setPointerCapture(e.pointerId);
      function overTray(m) { if (!tray) return false; var r = tray.getBoundingClientRect(); return m.clientX >= r.left && m.clientX <= r.right && m.clientY >= r.top && m.clientY <= r.bottom; }
      function mv(m) {
        var dy = m.clientY - sy;
        if (lk) { if (!warned && Math.hypot(m.clientX - sx, dy) > 5) { warned = true; if (ev.kind === 'task') toast(lockWhy(ev.task)); } return; }
        if (!moved && Math.hypot(m.clientX - sx, dy) < 5) return;
        moved = true; el.classList.add('drag');
        onTray = mode === 'move' && overTray(m); if (tray) tray.classList.toggle('drop', onTray);
        if (mode === 'resize') {
          var nd = Math.max(15, snap(ev.dur + dy / PPM)); el.style.height = nd * PPM - 2 + 'px'; target = { dur: nd };
          if (small) small.textContent = fm(ev.start) + ' – ' + fm(ev.start + nd);
        } else {
          var ns = clamp(snap(ev.start + dy / PPM), H0 * 60, H1 * 60 - ev.dur), dx = 0, newDate = ev.date;
          if (cols) { // scheduled backlog tasks can change day
            var ci = cols.findIndex(function (c) { var r = c.getBoundingClientRect(); return m.clientX >= r.left && m.clientX < r.right; }); if (ci < 0) ci = col0;
            dx = cols[ci].getBoundingClientRect().left - r0.left; newDate = cols[ci].dataset.date;
          }
          el.style.transform = 'translate(' + dx + 'px,' + (ns - ev.start) * PPM + 'px)'; target = { start: ns, date: newDate };
          if (small) small.textContent = fm(ns) + ' – ' + fm(ns + ev.dur);
        }
      }
      function up() {
        el.removeEventListener('pointermove', mv); el.removeEventListener('pointerup', up); el.removeEventListener('pointercancel', up);
        if (tray) tray.classList.remove('drop');
        if (!moved) { openPop(ev, el); return; }
        if (ev.kind === 'sched') { if (onTray) { unschedule(ev.item); return; } commitSched(ev, target); return; }
        if (onTray) { moveToBacklog(ev.task); return; }
        commitSchedule(ev, target);
      }
      el.addEventListener('pointermove', mv); el.addEventListener('pointerup', up); el.addEventListener('pointercancel', up);
    });
    sc.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter') return; var el = e.target.closest('.cal-ev'); if (el && eventIndex[el.dataset.id]) openPop(eventIndex[el.dataset.id], el);
    });
    sc.addEventListener('click', function (e) {
      var col = e.target.closest('.cal-col'); if (!col || e.target.closest('.cal-ev') || e.target.closest('.cal-mark')) return;
      var y = e.clientY - col.getBoundingClientRect().top;
      openAddDialog(col.dataset.date, clamp(Math.floor((H0 * 60 + y / PPM) / 15) * 15, H0 * 60, H1 * 60 - 15));
    });
    $$('.cal-col', sc).forEach(function (col) {
      col.addEventListener('dragover', function (e) { e.preventDefault(); col.classList.add('drop'); });
      col.addEventListener('dragleave', function () { col.classList.remove('drop'); });
      col.addEventListener('drop', function (e) {
        e.preventDefault(); col.classList.remove('drop');
        var parts = e.dataTransfer.getData('text/plain').split('|'); if (parts[0] !== 'bk') return;
        var y = e.clientY - col.getBoundingClientRect().top;
        placeBacklog(parts[1], col.dataset.date, clamp(snap(H0 * 60 + y / PPM), H0 * 60, H1 * 60 - 60));
      });
    });
    // Open on the current time when today is on screen, otherwise on the start of the working day.
    var todayCol = $('.cal-col[data-date="' + BOOT.today + '"]', root), nm0 = nowMin();
    if (todayCol && nm0 >= H0 * 60 && nm0 < H1 * 60) sc.scrollTop = Math.max(0, (nm0 - H0 * 60) * PPM - sc.clientHeight * 0.4);
    else sc.scrollTop = Math.max(0, (8 - H0) * PPH - 10);
    var head = $('#cal-head', root); if (head) head.style.setProperty('--sb', (sc.offsetWidth - sc.clientWidth) + 'px');
  }

  function commitSchedule(ev, target) {
    var t = ev.task, before = { start: t.start_time, est: t.estimated_time };
    var args = { name: t.name };
    if (target.start != null) { args.start_time = toT(target.start); t.start_time = args.start_time; }
    if (target.dur != null) { args.estimated_hours = target.dur / 60; t.estimated_time = target.dur / 60; }
    render();
    api(CAL + 'set_task_schedule', args, true).then(function () {
      toast(target.start != null ? 'Moved to ' + fm(target.start) : 'Duration set to ' + target.dur + ' min', function () {
        t.start_time = before.start; t.estimated_time = before.est; render();
        api(CAL + 'set_task_schedule', { name: t.name, start_time: before.start || toT(target.start != null ? ev.start : ev.start), estimated_hours: before.est || 1 }, true).then(refresh);
      });
    }).catch(function (err) { toast(err.message, null, true); refresh(); });
  }
  // Done needs a time taken (the server downgrades Done without one), so ask before saving.
  function askTimeTaken(t) {
    return new Promise(function (resolve) {
      var d = dlg(); d.className = 'cal-dlg';
      d.innerHTML = '<form method="dialog"><h3>Time taken</h3><p style="margin:0;color:var(--st-muted)">How long did "' + esc(t.description) + '" take?</p>' +
        '<label class="cal-f">Time taken<input id="tt-time" value="' + esc(hoursText(+t.actual_time || +t.estimated_time)) + '" placeholder="e.g. 1h 30m" required></label>' +
        '<p id="tt-err" class="cal-err" role="alert"></p><div class="cal-actions"><button type="button" class="cal-btn" id="tt-x">Cancel</button><button type="submit" class="cal-btn primary">Mark done</button></div></form>';
      var settled = false, finish = function (v) { if (!settled) { settled = true; resolve(v); } };
      d.showModal(); $('#tt-time').select();
      $('#tt-x').onclick = function () { d.close(); };
      d.onclose = function () { finish(null); };
      $('form', d).onsubmit = function (e) {
        e.preventDefault();
        var v = normDuration($('#tt-time').value), h = parseHours(v);
        if (!v || isNaN(h) || h <= 0 || h > 24) { $('#tt-err').textContent = 'Enter a time between 1 minute and 24 hours, for example 1h 30m.'; return; }
        finish(v); d.close();
      };
    });
  }
  function saveStatus(t, status, actual) {
    var update = { name: t.name, status: status };
    if (actual) update.actual_time = actual;
    return api(API + 'autosave_eod_progress', { task_updates: JSON.stringify([update]) }, true);
  }
  function setStatus(t, status) {
    var go = function (actual) {
      var before = t.status; t.status = status; render();
      saveStatus(t, status, actual).then(function () {
        toast('Marked ' + STATUS[status].label, function () { t.status = before; render(); saveStatus(t, before).then(refresh); });
        return refresh();
      }).catch(function (err) { toast(err.message, null, true); refresh(); });
    };
    if (status === 'Done' && !(+t.actual_time)) askTimeTaken(t).then(function (v) { if (v) go(v); else render(); });
    else go(null);
  }
    function moveToBacklog(t) {
    var why = backlogBlock(t);
    if (why) { toast(why); render(); return; }
    api(API + 'move_task_to_backlog', { name: t.name }, true).then(function () { toast('"' + t.description + '" moved to backlog'); refresh(); })
      .catch(function (err) { toast(err.message, null, true); refresh(); });
  }
  // A backlog task dropped on a day: today joins the plan now, a later day schedules it.
  function placeBacklog(name, date, start) {
    if (date < BOOT.today) { toast('A task cannot be scheduled for a day that has passed.', null, true); return; }
    if (date === BOOT.today) { pullBacklog(name, date, start); return; }
    api(CAL + 'schedule_backlog_item', { name: name, date: date, start_time: toT(start) }, true).then(function () { toast('Scheduled for ' + dayLabel(date) + ', ' + fm(start) + '.'); return refresh(); })
      .catch(function (err) { toast(err.message, null, true); refresh(); });
  }
  function commitSched(ev, target) {
    var item = ev.item, before = { date: item.scheduled_for, start: item.start_time };
    if (target.date < BOOT.today) { toast('A task cannot be scheduled for a day that has passed.', null, true); render(); return; }
    item.scheduled_for = target.date; item.start_time = toT(target.start); render();
    api(CAL + 'schedule_backlog_item', { name: item.name, date: target.date, start_time: toT(target.start) }, true).then(function () {
      toast('Moved to ' + dayLabel(target.date) + ', ' + fm(target.start), function () {
        item.scheduled_for = before.date; item.start_time = before.start; render();
        api(CAL + 'schedule_backlog_item', { name: item.name, date: before.date, start_time: before.start }, true).then(refresh);
      });
    }).catch(function (err) { toast(err.message, null, true); refresh(); });
  }
  function unschedule(item) {
    api(CAL + 'schedule_backlog_item', { name: item.name }, true).then(function () { toast('"' + item.description + '" is back in the backlog without a day.'); return refresh(); })
      .catch(function (err) { toast(err.message, null, true); refresh(); });
  }
  function pullBacklog(name, date, start) {
    if (date !== BOOT.today) { toast('Only today can be scheduled from the backlog.'); return; }
    if (eodDone() && BOOT.active_date === BOOT.today) { toast('You have already checked out today.'); return; }
    api(API + 'pull_backlog_item_to_today', { name: name }, true).then(function (r) {
      return api(CAL + 'set_task_schedule', { name: r.task_name, start_time: toT(start) }, true);
    }).then(function () { toast('Scheduled for ' + fm(start)); refresh(); }).catch(function (err) { toast(err.message, null, true); refresh(); });
  }

  /* ── popover ── */
  function closePop() { $('#cal-pop-root').innerHTML = ''; }
  function openPop(ev, el) {
    closePop();
    var root = $('#cal-pop-root'), r = el.getBoundingClientRect(), p = document.createElement('div'); p.className = 'cal-pop';
    var st = STATUS[ev.status], html;
    if (ev.kind === 'sched') {
      var it = ev.item;
      html = '<button type="button" class="cal-ibtn" style="position:absolute;right:6px;top:6px" data-a="x" aria-label="Close">✕</button><span class="cal-tag dash">Scheduled backlog task</span>' +
        '<h4>' + esc(it.description) + '</h4><div style="color:var(--st-muted)">' + dayLabel(ev.date) + ' · ' + fmFull(ev.start) + ' – ' + fmFull(ev.start + ev.dur) + '</div>' +
        (it.project_name ? '<div style="margin-top:4px"><b>Project:</b> ' + esc(it.project_name) + '</div>' : '') +
        '<p class="cal-hint" style="margin:8px 0 0">It joins your plan on that day. Drag it to another day to reschedule.</p>' +
        '<div class="cal-row"><button type="button" class="cal-btn" data-a="unsched">Remove from this day</button>' + (ev.date <= BOOT.today ? '<button type="button" class="cal-btn" data-a="dotoday">Add to today</button>' : '') + '</div>';
    } else if (ev.kind === 'addl') {
      var a = ev.addl;
      html = '<button type="button" class="cal-ibtn" style="position:absolute;right:6px;top:6px" data-a="x" aria-label="Close">✕</button><span class="cal-tag ' + st.c + '">' + st.ic + ' ' + esc(ev.status) + '</span> <span class="cal-tag">Additional work</span>' +
        '<h4>' + esc(a.description) + '</h4><div style="color:var(--st-muted)">' + dayLabel(ev.date) + ' · ' + (a.login_time ? fmFull(a.login_time) : '') + ' – ' + (a.logout_time ? fmFull(a.logout_time) : '') + '</div>' +
        (a.project_name ? '<div style="margin-top:4px"><b>Project:</b> ' + esc(a.project_name) + '</div>' : '') +
        '<div class="cal-row"><a class="cal-btn" href="/additional-work" style="display:inline-flex;align-items:center;text-decoration:none">Open Additional Work</a></div>';
    } else {
      var t = ev.task, lk = isLocked(t), bb = backlogBlock(t), canSt = canChangeStatus(t);
      html = '<button type="button" class="cal-ibtn" style="position:absolute;right:6px;top:6px" data-a="x" aria-label="Close">✕</button><span class="cal-tag ' + st.c + '">' + st.ic + ' ' + esc(ev.status) + '</span> <span class="cal-tag">' + esc(KIND[t.task_type] || 'Task') + '</span>' +
        '<h4>' + esc(t.description) + '</h4><div style="color:var(--st-muted)">' + dayLabel(ev.date) + ' · ' + fmFull(ev.start) + ' – ' + fmFull(ev.start + ev.dur) + '</div>' +
        (t.project_name ? '<div style="margin-top:4px"><b>Project:</b> ' + esc(t.project_name) + '</div>' : '') +
        (t.remarks ? '<div style="color:var(--st-muted)">' + esc(t.remarks) + '</div>' : '') +
        (lk ? '<div class="cal-tag dash" style="margin-top:8px">' + esc(lockWhy(t)) + '</div>'
          : canSt ? '<div class="cal-seg" style="margin-top:12px" role="group" aria-label="Status">' + Object.keys(STATUS).map(function (k) { return '<button type="button" data-a="st" data-s="' + k + '" aria-pressed="' + (k === t.status) + '">' + STATUS[k].label + '</button>'; }).join('') + '</div>'
            : '<div class="cal-tag dash" style="margin-top:8px">Check in to change status.</div>') +
        (lk ? '' : '<div class="cal-row"><button type="button" class="cal-btn" data-a="edit">Edit</button><button type="button" class="cal-btn" data-a="bk"' + (bb ? ' disabled' : '') + '>Move to backlog</button></div>' + (bb ? '<div style="color:var(--st-muted);font-size:11px;margin-top:6px">' + esc(bb) + '</div>' : ''));
    }
    p.innerHTML = html + (ev.kind === 'task' ? '<div class="cal-row" id="pop-files" style="margin-top:8px"></div>' : '');
    p.style.left = clamp(r.right + 8, 8, window.innerWidth - 308) + 'px'; p.style.top = clamp(r.top, 8, window.innerHeight - 280) + 'px';
    if (r.right + 316 > window.innerWidth) p.style.left = clamp(r.left - 308, 8, window.innerWidth - 308) + 'px';
    p.onclick = function (e) {
      var b = e.target.closest('[data-a]'); if (!b) return;
      var a = b.dataset.a;
      if (a === 'x') closePop();
      else if (a === 'st') { closePop(); setStatus(ev.task, b.dataset.s); }
      else if (a === 'bk') { closePop(); moveToBacklog(ev.task); }
      else if (a === 'edit') { closePop(); openEditTask(ev.task); }
      else if (a === 'unsched') { closePop(); unschedule(ev.item); }
      else if (a === 'dotoday') { closePop(); pullBacklog(ev.item.name, BOOT.today, ev.start); }
    };
    root.appendChild(p);
    if (ev.kind === 'task') mountAttachments($('#pop-files', p), ev.task.name, false);
    setTimeout(function () { document.addEventListener('pointerdown', outsidePop, { once: true }); }, 0);
  }
  function outsidePop(e) { if (!e.target.closest('.cal-pop')) closePop(); else document.addEventListener('pointerdown', outsidePop, { once: true }); }
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closePop(); });

  /* ── toast ── */
  var toastTimer = null;
  function toast(msg, undo, isErr) {
    $$('.cal-toast').forEach(function (t) { t.remove(); });
    var t = document.createElement('div'); t.className = 'cal-toast' + (isErr ? ' err' : ''); t.setAttribute('role', isErr ? 'alert' : 'status');
    t.innerHTML = '<span>' + esc(msg) + '</span>';
    if (undo) { var b = document.createElement('button'); b.type = 'button'; b.textContent = 'Undo'; b.onclick = function () { t.remove(); undo(); }; t.appendChild(b); }
    document.body.appendChild(t); clearTimeout(toastTimer); toastTimer = setTimeout(function () { t.remove(); }, isErr ? 7000 : 5000);
  }

  /* ── side panel ── */
  function taskRowHTML(v) {
    v = v || {};
    return '<div class="cal-trow mc-new"><textarea name="d" rows="1" class="cal-ta mc-d" placeholder="What will you work on?" aria-label="Task description">' + esc(v.d || '') + '</textarea><input name="p" class="mc-p" placeholder="Project" aria-label="Project" value="' + esc(v.p || '') + '"><input name="e" class="mc-e" placeholder="1h" aria-label="Estimated time" title="Estimated time, e.g. 1h 30m" value="' + esc(v.e || '') + '"></div>';
  }
  // ── unsent check-in plan, kept on the server so another device (or a reopened laptop) shows it ──
  function savedPlanRows() {
    var d = S.page && S.page.morning_draft;
    return d && d.kind === 'calendar' && d.data && Array.isArray(d.data.rows) ? d.data.rows : [];
  }
  function planRowsHTML() {
    var rows = savedPlanRows();
    return rows.length ? rows.map(taskRowHTML).join('') : taskRowHTML();
  }
  function collectPlanRows() {
    return $$('#m-rows .cal-trow').map(function (r) {
      return { d: $('[name=d]', r).value.trim(), p: $('[name=p]', r).value.trim(), e: $('[name=e]', r).value.trim() };
    }).filter(function (r) { return r.d || r.p || r.e; });
  }
  var planTimer = null;
  function planPayload() {
    var rows = collectPlanRows();
    if (S.page) S.page.morning_draft = rows.length ? { kind: 'calendar', data: { rows: rows } } : null;  // a re-render restores this
    return { kind: 'calendar', data: rows.length ? JSON.stringify({ rows: rows }) : '' };
  }
  function schedulePlanSave() {
    if (!$('#m-rows')) return;
    planPayload();
    clearTimeout(planTimer);
    planTimer = setTimeout(function () { planTimer = null; api(API + 'save_morning_draft', planPayload(), true).catch(function () { /* the next edit saves again */ }); }, 800);
  }
  // Closing the tab or laptop inside the debounce window would lose the last edit: send it now,
  // with keepalive so the browser finishes the request after the page is gone.
  function flushPlanSave() {
    if (planTimer === null || !$('#m-rows')) return;
    clearTimeout(planTimer); planTimer = null;
    try {
      fetch('/api/method/' + API + 'save_morning_draft', {
        method: 'POST', keepalive: true, credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-Frappe-CSRF-Token': frappe.csrf_token },
        body: JSON.stringify(planPayload())
      });
    } catch (e) { /* best effort */ }
  }
  function statusSeg(t) {
    var can = canChangeStatus(t);
    return '<div class="cal-seg" data-task="' + esc(t.name) + '" role="group" aria-label="Status of ' + esc(t.description) + '">' + Object.keys(STATUS).map(function (k) {
      return '<button type="button" data-s="' + k + '" aria-pressed="' + (k === t.status) + '"' + (can ? '' : ' disabled') + '>' + STATUS[k].label + '</button>';
    }).join('') + '</div>';
  }
  function trayHTML() {
    var items = S.backlog.slice(0, 4).map(function (b) {
      return '<div class="cal-task bk" draggable="true" data-bk="' + esc(b.name) + '"><span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + esc(b.description) + '</span></div>';
    }).join('');
    var open = false; try { open = localStorage.getItem('st.cal.tray') === '1'; } catch (e) { /* default closed */ }
    return '<div id="cal-tray" class="cal-tray"><div class="cal-row cal-between" style="margin-bottom:6px"><button type="button" class="tray-toggle" id="tray-toggle" aria-expanded="' + open + '" aria-controls="tray-body"><i class="ti ti-chevron-down" aria-hidden="true"></i><b>Backlog</b><span class="tray-n">' + S.backlog.length + '</span></button><a href="/task-backlog" style="color:var(--st-accent-text);font-weight:600">View all</a></div>' +
      '<div id="tray-body"' + (open ? '' : ' hidden') + '><p class="cal-hint">Drag a task onto today to schedule it. Drop a calendar task here to move it to the backlog.</p>' + (items || '<div class="cal-empty">Backlog is clear.</div>') + '</div></div>';
  }
  // Tasks the server would let me move to the backlog (it re-checks every rule).
  function bulkCandidates(onlyPending) {
    return openDayTasks().filter(function (t) { return !backlogBlock(withDate(t)) && (!onlyPending || t.status === 'Pending'); });
  }
  function bulkButton(label) {
    return '<button type="button" class="cal-btn" data-act="bulk" style="height:28px;padding:0 10px;font-size:11px">' + label + '</button>';
  }
  function openDayTasks() {
    return S.day.tasks.filter(function (t) { return t.date === BOOT.active_date && STATUS[t.status]; });
  }
  function renderSide() {
    var side = $('#cal-side'), btn = $('.st-collapse-btn', side), log = openLog();
    var html = '', late = BOOT.is_late_checkout && log && log.morning_submitted && !log.eod_submitted;
    if (late) {
      html += '<div class="cal-banner"><b>You did not check out on ' + dayLabel(BOOT.active_date) + '.</b><div style="margin-top:4px">Check out for that day first. Your tasks from today stay as they are.</div></div>';
    }
    html += lockCardHTML();
    if (!morningDone()) html += morningHTML();
    else if (!eodDone()) html += workingHTML();
    else html += summaryHTML();
    var draft = addFormDraft();
    side.innerHTML = '';
    if (btn) side.appendChild(btn);
    side.insertAdjacentHTML('beforeend', html);
    bindSide(side);
    restoreAddFormDraft(draft);
  }
  // The side panel is rebuilt on every refresh. Without this, a task typed into "Add a task" was wiped
  // (and the Save button replaced under the pointer) whenever a refresh landed before Save was pressed.
  var ADD_FIELDS = ['a-d', 'a-p', 'a-e'];
  function addFormDraft() {
    var draft = { values: {}, focus: null }, any = false;
    ADD_FIELDS.forEach(function (id) {
      var el = document.getElementById(id);
      if (el && el.value) { draft.values[id] = el.value; if (id === 'a-d' || id === 'a-p') any = true; }
      if (el && el === document.activeElement) draft.focus = id;
    });
    return any || draft.focus ? draft : null;
  }
  function restoreAddFormDraft(draft) {
    if (!draft) return;
    Object.keys(draft.values).forEach(function (id) { var el = document.getElementById(id); if (el) el.value = draft.values[id]; });
    var f = draft.focus && document.getElementById(draft.focus); if (f) f.focus();
  }

  /* Checkout closed after the cutoff: the employee asks an HR Manager to unlock it. */
  function lockCardHTML() {
    var date, text, requested;
    if (BOOT.checkout_locked) {
      date = BOOT.active_date; requested = BOOT.checkout_unlock_requested;
      text = 'Checkout closed at ' + esc(BOOT.checkout_cutoff_label) + ' for <b>' + esc(dayLabel(date)) + '</b>. Get late checkout approval by calling Muzammil Sir.';
    } else if (BOOT.missed_checkout) {
      date = BOOT.missed_checkout.date; requested = BOOT.missed_checkout.requested;
      text = 'You did not check out on <b>' + esc(dayLabel(date)) + '</b>. Get late checkout approval from HR.' + (BOOT.checkin_blocked ? ' You cannot check in until that checkout is completed.' : '');
    } else return '';
    return '<div class="cal-banner" role="alert"><i class="ti ti-lock" aria-hidden="true"></i> ' + text +
      (requested ? '<div style="margin-top:6px"><i class="ti ti-clock-check" aria-hidden="true"></i> Approval requested. HR has been notified.</div>'
        : '<div class="cal-row" style="margin-top:8px"><input class="cal-input" id="lock-reason" maxlength="200" placeholder="Reason (required)…" aria-label="Reason for late checkout" style="flex:1 1 160px">' +
          '<button type="button" class="cal-btn primary" id="lock-request" data-date="' + esc(date) + '">Request HR approval</button></div>') + '</div>';
  }
  function requestLateCheckout(btn) {
    var reason = $('#lock-reason');
    var reasonVal = reason ? reason.value.trim() : '';
    if (!reasonVal) {
      if (reason) {
        reason.focus();
        reason.style.borderColor = '#dc2626';
      }
      toast('Please enter a reason for late checkout.', null, true);
      return;
    }
    btn.disabled = true;
    api(API + 'request_late_checkout_approval', { date: btn.dataset.date, reason: reasonVal }, true).then(function () {
      toast('Request sent to HR');
      setTimeout(function () { window.location.reload(); }, 800);
    }).catch(function (e) { btn.disabled = false; toast(e.message || 'Could not send request. Try again or contact HR.', null, true); });
  }

  function morningHTML() {
    var cfg = BOOT.location_config, tasks = openDayTasks(), page = S.page || {};
    var carried = tasks.map(function (t) {
      var rec = t.task_type === 'Recurring';
      return '<div class="mc-row" data-carried="' + esc(t.name) + '"><textarea class="cal-ta mc-d" data-f="d" rows="1" aria-label="Task description">' + esc(t.description) + '</textarea>' +
        '<div class="mc-meta"><input class="mc-p" data-f="p" value="' + esc(t.project_name || '') + '" placeholder="Project" aria-label="Project"><input class="mc-e" data-f="e" value="' + esc(hoursText(t.estimated_time)) + '" placeholder="1h" aria-label="Estimated time">' +
        '<span class="mc-kind">' + (t.is_carried ? 'Carried from ' + esc(dayLabel(t.origin_date)) : esc(KIND[t.task_type] || 'Task')) + '</span><span class="mc-acts">' +
        '<button type="button" class="cal-ibtn" data-act="bk" data-name="' + esc(t.name) + '" title="Move to backlog" aria-label="Move to backlog"' + (rec ? ' disabled' : '') + '><i class="ti ti-list-details" aria-hidden="true"></i></button>' +
        '<button type="button" class="cal-ibtn" data-act="del" data-name="' + esc(t.name) + '" title="Remove task" aria-label="Remove task"' + (rec ? ' disabled' : '') + '><i class="ti ti-trash" aria-hidden="true"></i></button></span></div></div>';
    }).join('');
    var backlog = S.backlog.map(function (b) {
      return '<label class="mc-bk" title="' + esc(b.description) + '"><input type="checkbox" data-pull="' + esc(b.name) + '"><span>' + esc(b.description) + '</span></label>';
    }).join('');
    var bkOpen = true; try { var saved = localStorage.getItem('st.cal.bk'); bkOpen = saved === null ? S.backlog.length <= 3 : saved === '1'; } catch (e) { /* default */ }
    var bulk = carried && bulkCandidates(false).length > 1 ? bulkButton('Move all to backlog') : '';
    return '<div class="mc-head"><h3>Morning check-in</h3><small>' + esc(dayLabel(BOOT.today)) + '</small></div>' +
      '<form id="cal-mf" class="mc" novalidate>' +
      '<section class="mc-sec"><div class="mc-two"><label class="cal-f">Login time<input id="m-in" type="time" value="' + (S.page && S.page.current_time ? S.page.current_time : toT(nowMin())) + '" required></label>' +
      '<label class="cal-f">Work location<select id="m-loc"' + (cfg.readonly ? ' disabled' : '') + '>' + cfg.options.map(function (o) { return '<option value="' + esc(o) + '"' + (o === cfg.value ? ' selected' : '') + '>' + esc(o) + '</option>'; }).join('') + '</select></label></div>' +
      '<div id="m-late" class="mc-late"></div>' + (cfg.note ? '<small class="mc-note">' + esc(cfg.note) + '</small>' : '') +
      (BOOT.half_day_leave ? '<label class="cal-f">Half-day leave session<select id="m-ses"><option>First Half</option><option>Second Half</option></select></label>' : '') +
      (page.leave_today && !BOOT.half_day_leave ? '<div class="cal-tag dash">On approved leave today: ' + esc(page.leave_today) + '</div>' : '') + '</section>' +
      (carried ? '<section class="mc-sec"><div class="mc-h"><span>Your list today <b>' + tasks.length + '</b></span>' + bulk + '</div>' + carried + '</section>' : '') +
      (backlog ? '<section class="mc-sec mc-fold"><button type="button" class="mc-h mc-toggle" id="mc-bk-toggle" aria-expanded="' + bkOpen + '" aria-controls="mc-bks"><span>From your backlog <b>' + S.backlog.length + '</b><b class="mc-picked" id="mc-bk-picked" hidden></b></span><i class="ti ti-chevron-down" aria-hidden="true"></i></button><div class="mc-bks" id="mc-bks"' + (bkOpen ? '' : ' hidden') + '>' + backlog + '</div></section>' : '') +
      '<section class="mc-sec"><div class="mc-h"><span>Add tasks</span></div><div id="m-rows">' + planRowsHTML() + '</div><button type="button" class="mc-add" id="m-add"><i class="ti ti-plus" aria-hidden="true"></i>Add another task</button></section>' +
      '<div class="mc-bar"><p id="m-err" class="cal-err" role="alert"></p><button class="cal-btn primary block" type="submit" id="m-submit">Submit check-in</button></div></form>';
  }
  function workingHTML() {
    var log = openLog(), tasks = openDayTasks(), done = tasks.filter(function (t) { return t.status === 'Done'; }).length;
    var lateTag = log.is_late ? '<span class="cal-tag red-line">Late</span>' : '<span class="cal-tag">On time</span>';
    var so = Math.max(0, (nowMin() - toMin(log.login_time)) / 60).toFixed(1);
    return '<h3>' + (BOOT.is_late_checkout ? dayLabel(BOOT.active_date) : 'Today') + '</h3>' +
      '<div class="cal-card"><div class="cal-row cal-between"><b>Checked in ' + fmFull(log.login_time) + '</b><span class="cal-tag black">' + esc(log.work_location || '') + '</span></div>' +
      '<div class="cal-row" style="margin-top:8px">' + lateTag + (log.half_day_session ? '<span class="cal-tag">Half day · ' + esc(log.half_day_session) + '</span>' : '') + (BOOT.is_late_checkout ? '' : '<span class="cal-tag">' + so + ' h so far</span>') + '</div></div>' +
      '<div class="cal-row cal-between" style="margin:16px 0 6px"><b>Tasks</b><span class="cal-row" style="gap:6px">' + (bulkCandidates(true).length > 1 ? bulkButton('Pending to backlog') : '') + '<span class="cal-tag">' + done + ' / ' + tasks.length + ' done</span></span></div>' +
      '<div class="cal-bar" style="margin-bottom:10px"><i style="width:' + (tasks.length ? done / tasks.length * 100 : 0) + '%"></i></div>' +
      tasks.map(function (t) {
        var w = backlogBlock(t);
        return '<div class="cal-task"><div class="cal-row" style="flex-wrap:nowrap;align-items:flex-start"><div style="flex:1;min-width:0"><div style="overflow-wrap:anywhere">' + esc(t.description) + '</div><small>' + esc(t.project_name || '') + (t.project_name ? ' · ' : '') + esc(KIND[t.task_type] || 'Task') + (t.is_carried ? ' · carried' : '') + '</small></div>' +
          '<span class="cal-row" style="gap:0;flex-wrap:nowrap"><button type="button" class="cal-ibtn" data-act="edit" data-name="' + esc(t.name) + '" title="Edit task" aria-label="Edit ' + esc(t.description) + '"><i class="ti ti-pencil" aria-hidden="true"></i></button>' +
          '<button type="button" class="cal-ibtn" data-act="bk" data-name="' + esc(t.name) + '"' + (w ? ' disabled' : '') + ' title="' + esc(w || 'Move to backlog') + '" aria-label="Move ' + esc(t.description) + ' to backlog"><i class="ti ti-list-details" aria-hidden="true"></i></button></span></div>' + statusSeg(t) + '</div>';
      }).join('') +
      '<form id="cal-af" class="cal-card" style="display:flex;flex-direction:column;gap:8px;margin-top:10px" novalidate><b>Add a task</b>' +
      '<textarea class="cal-ta" id="a-d" rows="1" placeholder="Task description" aria-label="Task description" required></textarea><div class="cal-pe" style="margin-top:0"><input class="cal-input" id="a-p" placeholder="Project" aria-label="Project"><input class="cal-input" id="a-e" placeholder="1h" aria-label="Estimated time"></div>' +
      '<p id="a-err" class="cal-err" role="alert"></p><button class="cal-btn" type="submit">Save task</button></form>' +
      '<div class="cal-card" style="margin-top:14px"><b>End of day</b><p style="margin:4px 0 10px;color:var(--st-muted)">Set each task\'s status, enter lunch time, then check out.</p>' + '<button type="button" class="cal-btn dark block" id="cal-checkout"' + (BOOT.checkout_locked ? ' disabled' : '') + '>Check out</button></div>' +
      (S.page && !BOOT.is_late_checkout && S.page._reset_until > Date.now() ? (S.page.has_reset_today
        ? '<button type="button" class="cal-btn block" id="cal-reset" style="margin-top:12px" disabled aria-describedby="cal-reset-note">Reset check-in</button><p id="cal-reset-note" class="cal-hint" style="margin:6px 2px 0">Already reset once today. A check-in can be reset once a day.</p>'
        : '<button type="button" class="cal-btn block" id="cal-reset" style="margin-top:12px">Reset check-in</button>') : '') + trayHTML();
  }
  function summaryHTML() {
    var log = openLog(), tasks = openDayTasks(), done = tasks.filter(function (t) { return t.status === 'Done'; }).length;
    return '<h3>Day complete</h3><div class="cal-card"><ul class="cal-tl">' +
      '<li><span>Check-in</span><span>' + fmFull(log.login_time) + ' · ' + esc(log.work_location || '') + '</span></li>' +
      '<li><span>Lunch</span><span>' + (log.lunch_from && log.lunch_to ? fm(toMin(log.lunch_from)) + ' – ' + fm(toMin(log.lunch_to)) : '-') + '</span></li>' +
      '<li><span>Check-out</span><span>' + fmFull(log.logout_time) + '</span></li><li><span>Net hours</span><span>' + esc(log.net_hours || '-') + '</span></li>' +
      '<li><span>Tasks done</span><span>' + done + ' / ' + tasks.length + '</span></li></ul><span class="cal-tag dash" style="margin-top:10px">Locked after check-out</span></div>' +
      '<div style="margin-top:14px">' + tasks.map(function (t) { return '<div class="cal-task" style="flex-direction:row;align-items:center"><span style="flex:1;min-width:0;overflow-wrap:anywhere">' + esc(t.description) + '</span><span class="cal-tag ' + STATUS[t.status].c + '">' + STATUS[t.status].ic + ' ' + STATUS[t.status].label + '</span></div>'; }).join('') + '</div>' +
      (BOOT.is_late_checkout ? '<button type="button" class="cal-btn primary block" id="cal-reload">Continue to today</button>' : '') + trayHTML();
  }

  function bindSide(side) {
    $$('[data-bk]', side).forEach(function (t) { t.addEventListener('dragstart', function (e) { e.dataTransfer.setData('text/plain', 'bk|' + t.dataset.bk); }); });
    var tt = $('#tray-toggle', side);
    if (tt) tt.onclick = function () { var body = $('#tray-body', side), open = body.hidden; body.hidden = !open; tt.setAttribute('aria-expanded', String(open)); try { localStorage.setItem('st.cal.tray', open ? '1' : '0'); } catch (e) { /* optional */ } };
    var tray = $('#cal-tray', side);
    if (tray) {
      tray.addEventListener('dragover', function (e) { e.preventDefault(); tray.classList.add('drop'); });
      tray.addEventListener('dragleave', function () { tray.classList.remove('drop'); });
    }
    var lockBtn = $('#lock-request', side); if (lockBtn) lockBtn.onclick = function () { requestLateCheckout(lockBtn); };
    var reload = $('#cal-reload', side); if (reload) reload.onclick = function () { window.location.reload(); };
    side.onclick = function (e) {
      var seg = e.target.closest('.cal-seg[data-task] [data-s]');
      if (seg) { var t = S.day.tasks.filter(function (x) { return x.name === seg.parentElement.dataset.task; })[0]; if (t && t.status !== seg.dataset.s) { syncDayTask(t, seg.dataset.s); } return; }
      var act = e.target.closest('[data-act]');
      if (act && act.dataset.act === 'bulk') { confirmBulk(bulkCandidates(!!$('#cal-checkout', side))); return; }
      if (act) {
        var task = S.day.tasks.filter(function (x) { return x.name === act.dataset.name; })[0]; if (!task) return;
        if (act.dataset.act === 'bk') moveToBacklog(withDate(task));
        else if (act.dataset.act === 'edit') openEditTask(withDate(task));
        else if (act.dataset.act === 'del') removeTask(task);
      }
    };
    var mf = $('#cal-mf', side);
    if (mf) {
      var late = function () { var v = $('#m-in').value; $('#m-late').innerHTML = v && toMin(v) > toMin(BOOT.late_after) ? '<span class="cal-tag red-line">Late, after ' + fm(toMin(BOOT.late_after)) + '</span>' : '<span class="cal-tag">On time</span>'; };
      late(); $('#m-in').oninput = late;
      var bt = $('#mc-bk-toggle'), bl = $('#mc-bks');
      if (bt) {
        var picked = function () { var n = $$('[data-pull]:checked').length, b = $('#mc-bk-picked'); b.hidden = !n; b.textContent = n + ' selected'; };
        bt.onclick = function () { var open = bl.hidden; bl.hidden = !open; bt.setAttribute('aria-expanded', String(open)); try { localStorage.setItem('st.cal.bk', open ? '1' : '0'); } catch (e) { /* optional */ } };
        bl.addEventListener('change', picked); picked();
      }
      $('#m-add').onclick = function () { var r = $('#m-rows'); r.insertAdjacentHTML('beforeend', taskRowHTML()); r.lastElementChild.querySelector('input').focus(); };
      mf.onsubmit = function (e) { e.preventDefault(); submitMorning(); };
      mf.addEventListener('input', renderDrafts); mf.addEventListener('change', renderDrafts);
      $('#m-add').addEventListener('click', renderDrafts);
      mf.addEventListener('input', schedulePlanSave); mf.addEventListener('change', schedulePlanSave);
      $('#m-add').addEventListener('click', schedulePlanSave);
      if (window.location.hash === '#new-task') { var first = $('#m-rows input'); if (first) first.focus(); }
    }
    var af = $('#cal-af', side); if (af) af.onsubmit = function (e) { e.preventDefault(); addAdhoc(); };
    var co = $('#cal-checkout', side); if (co) co.onclick = openCheckout;
    var rs = $('#cal-reset', side);
    if (rs) {
      rs.onclick = confirmReset;
      // Remove the button when the one-hour reset window ends.
      clearTimeout(S.resetTimer);
      S.resetTimer = setTimeout(function () { var b = $('#cal-reset'); if (b) b.remove(); var n = $('#cal-reset-note'); if (n) n.remove(); }, Math.max(0, Math.min(S.page._reset_until - Date.now(), 2147483000)));
    }
    if (window.location.hash === '#new-task' && af) { $('#a-d').focus(); }
  }
  function confirmBulk(list) {
    if (!list.length) return;
    var d = dlg(); d.className = 'cal-dlg';
    d.innerHTML = '<form method="dialog"><h3>Move ' + list.length + ' tasks to the backlog?</h3>' +
      '<p style="margin:0;color:var(--st-muted)">They leave today\'s list and wait in your backlog until you schedule them again. Recurring and completed tasks stay.</p>' +
      '<ul style="margin:0;padding-left:18px;max-height:180px;overflow:auto">' + list.slice(0, 8).map(function (t) { return '<li>' + esc(t.description) + '</li>'; }).join('') + (list.length > 8 ? '<li>and ' + (list.length - 8) + ' more</li>' : '') + '</ul>' +
      '<p id="bk-err" class="cal-err" role="alert"></p><div class="cal-actions"><button type="button" class="cal-btn" id="bk-x">Cancel</button><button type="submit" class="cal-btn primary" id="bk-ok">Move to backlog</button></div></form>';
    d.showModal(); $('#bk-x').onclick = function () { d.close(); };
    $('form', d).onsubmit = function (e) {
      e.preventDefault(); $('#bk-ok').disabled = true;
      api(CAL + 'bulk_move_to_backlog', { names: JSON.stringify(list.map(function (t) { return t.name; })) }, true).then(function (r) {
        d.close(); toast(r.moved.length + ' moved to backlog' + (r.skipped.length ? ', ' + r.skipped.length + ' skipped: ' + r.skipped[0].reason : '') + '.', null, !!r.skipped.length && !r.moved.length);
        return refresh();
      }).catch(function (er) { $('#bk-err').textContent = er.message; $('#bk-ok').disabled = false; });
    };
  }
  function withDate(t) { t.date = t.date || BOOT.active_date; return t; }
  function syncDayTask(t, status) {
    if (status === 'Done' && !(+t.actual_time)) { askTimeTaken(t).then(function (v) { if (v) applySync(t, status, v); else render(); }); return; }
    applySync(t, status, null);
  }
  function applySync(t, status, actual) {
    var before = t.status; t.status = status;
    S.tasks.forEach(function (x) { if (x.name === t.name) x.status = status; });
    render();
    saveStatus(t, status, actual).then(refresh).catch(function (err) { toast(err.message, null, true); t.status = before; refresh(); });
  }
  function removeTask(t) {
    api(API + 'delete_carried_task', { name: t.name }, true).then(function () { toast('Task removed'); refresh(); }).catch(function (err) { toast(err.message, null, true); });
  }

  /* ── draft preview: tasks typed into the check-in form show on today's column before they are saved ── */
  function renderDrafts() {
    $$('.cal-ev.draft').forEach(function (n) { n.remove(); });
    if (morningDone() || S.view === 'month') return;
    var col = $('.cal-col[data-date="' + BOOT.today + '"]'); if (!col || !$('#m-rows')) return;
    var drafts = $$('#m-rows .cal-trow').map(function (r) {
      return { d: $('[name=d]', r).value.trim(), e: parseHours(normDuration($('[name=e]', r).value)) };
    }).filter(function (r) { return r.d; });
    $$('[data-pull]:checked').forEach(function (c) { drafts.push({ d: c.parentElement.textContent.trim(), e: null }); });
    if (!drafts.length) return;
    var cursor = Math.max(H0 * 60, Math.ceil(($('#m-in') && $('#m-in').value ? toMin($('#m-in').value) : nowMin()) / 15) * 15);
    dayEvents(BOOT.today).forEach(function (e) { if (e.kind === 'task') cursor = Math.max(cursor, e.start + e.dur); });
    drafts.forEach(function (x) {
      var dur = Math.round(((x.e && x.e > 0 && !isNaN(x.e) ? x.e : 1) * 60) / 15) * 15;
      var start = pendingSchedule[x.d] ? toMin(pendingSchedule[x.d]) : cursor;
      start = clamp(start, H0 * 60, H1 * 60 - dur); cursor = Math.max(cursor, start + dur);
      var el = document.createElement('div'); el.className = 'cal-ev s-pending draft';
      el.style.cssText = 'top:' + (start - H0 * 60) * PPM + 'px;height:' + Math.max(dur * PPM - 2, 18) + 'px;left:2px;width:calc(100% - 4px)';
      el.setAttribute('aria-label', 'Draft: ' + x.d); el.innerHTML = '○ ' + esc(x.d) + '<small>' + fm(start) + ' – ' + fm(start + dur) + ' · not saved until you check in</small>';
      col.appendChild(el);
    });
  }

  /* ── morning check-in ── */
  function readRows(root) {
    return $$('.cal-trow', root).map(function (r) {
      return { description: $('[name=d]', r).value.trim(), project_name: $('[name=p]', r).value.trim(), estimated_time: normDuration($('[name=e]', r).value) };
    }).filter(function (r) { return r.description; });
  }
  function submitMorning() {
    var err = $('#m-err'), btn = $('#m-submit'), tin = $('#m-in').value;
    err.textContent = '';
    if (!tin) { err.textContent = 'Enter your login time.'; return; }
    var rows = readRows($('#cal-mf')), pulls = $$('[data-pull]:checked').map(function (c) { return c.dataset.pull; });
    var carried = $$('[data-carried]').map(function (c) {
      return { name: c.dataset.carried, description: $('[data-f=d]', c).value.trim(), project_name: $('[data-f=p]', c).value.trim(), estimated_time: normDuration($('[data-f=e]', c).value) };
    });
    if (!rows.length && !pulls.length && !carried.length) { err.textContent = 'Add at least one task for today before you check in.'; return; }
    btn.disabled = true; btn.textContent = 'Checking in…';
    var chain = Promise.resolve();
    pulls.forEach(function (name) { chain = chain.then(function () { return api(API + 'pull_backlog_item_to_today', { name: name }, true); }); });
    chain.then(function () {
      return api(API + 'submit_morning_log', {
        new_tasks: JSON.stringify(rows), login_time: tin, carried_updates: JSON.stringify(carried),
        work_location: $('#m-loc').value, half_day_session: $('#m-ses') ? $('#m-ses').value : ''
      }, true);
    }).then(function (r) {
      toast('Checked in at ' + r.login_time); S.page = null; return loadAll(true).then(applyPendingSchedule).then(function () { return refresh(); }).then(function () { var sd = $('#cal-side'); if (sd) sd.scrollTop = 0; });
    }).catch(function (e) { err.textContent = e.message; btn.disabled = false; btn.textContent = 'Submit check-in'; if (pulls.length) refresh(); });
  }

  /* ── ad-hoc task ── */
  function addAdhoc() {
    var d = $('#a-d').value.trim(), err = $('#a-err'); err.textContent = '';
    if (!d) { err.textContent = 'Enter a task description.'; return; }
    api(API + 'add_adhoc_tasks', { tasks: JSON.stringify([{ client_id: 'c1', description: d, project_name: $('#a-p').value.trim(), estimated_time: normDuration($('#a-e').value) }]) }, true)
      .then(function (r) {
        var created = r.created && r.created[0];
        if (!created) return null;
        // Saved: empty the form now, or the draft kept across re-renders would put the same task back.
        ['a-d', 'a-p', 'a-e'].forEach(function (id) { var el = document.getElementById(id); if (el) el.value = ''; });
        var ends = openDayTasks().filter(function (t) { return t.start_time; }).map(function (t) { return toMin(t.start_time) + Math.round(durationHours(t) * 60); });
        var start = Math.min(Math.max(Math.ceil(nowMin() / 15) * 15, H0 * 60, Math.max.apply(null, ends.concat([0]))), H1 * 60 - 60);
        return api(CAL + 'set_task_schedule', { name: created.task_name, start_time: toT(start) }, true);
      }).then(function () { toast('Task saved. Your Team Leader can see it now.'); refresh(); })
      .catch(function (e) { err.textContent = e.message; });
  }

  /* ── attachments (existing endpoints; the server checks owner / Team Leader / HR) ── */
  function fileLink(f, taskName) {
    return '/api/method/st_attendance_tracker.api.view_task_attachment?file_name=' + encodeURIComponent(f.name) + '&task_name=' + encodeURIComponent(taskName);
  }
  function fileChip(f, taskName, canEdit) {
    return '<span class="cal-tag" data-file="' + esc(f.name) + '"><i class="ti ti-paperclip" aria-hidden="true"></i><a href="' + fileLink(f, taskName) + '" target="_blank" rel="noopener" style="color:inherit">' + esc(f.file_name) + '</a>' +
      (canEdit ? '<button type="button" class="cal-ibtn" data-del-file="' + esc(f.name) + '" aria-label="Remove ' + esc(f.file_name) + '" style="width:18px;height:18px;font-size:12px"><i class="ti ti-x" aria-hidden="true"></i></button>' : '') + '</span>';
  }
  function mountAttachments(box, taskName, canEdit) {
    api(API + 'get_task_attachments', { task_name: taskName }).then(function (files) {
      box.innerHTML = (files || []).map(function (f) { return fileChip(f, taskName, canEdit); }).join('') || (canEdit ? '' : '<span class="cal-hint" style="margin:0">No attachments.</span>');
    }).catch(function () { box.innerHTML = ''; });
    if (!canEdit) return;
    box.onclick = function (e) {
      var del = e.target.closest('[data-del-file]'); if (!del) return;
      api(API + 'delete_task_attachment', { file_name: del.dataset.delFile, task_name: taskName }, true).then(function () { del.closest('[data-file]').remove(); toast('Attachment removed.'); })
        .catch(function (er) { toast(er.message, null, true); });
    };
  }
  function uploadFiles(files, taskName, box, errEl) {
    Array.prototype.forEach.call(files, function (file) {
      var fd = new FormData(); fd.append('file', file); fd.append('is_private', '1'); fd.append('task_name', taskName);
      fetch('/api/method/st_attendance_tracker.api.upload_task_attachment', { method: 'POST', headers: { 'X-Frappe-CSRF-Token': frappe.csrf_token }, body: fd, credentials: 'same-origin' })
        .then(function (r) { return r.json(); }).then(function (r) {
          if (!r.message) throw new Error('Upload failed for ' + file.name + '.');
          box.insertAdjacentHTML('beforeend', fileChip(r.message, taskName, true));
        }).catch(function (er) { errEl.textContent = er.message; });
    });
  }

  /* ── edit a task ── */
  // "1h 30m", "90m", "1.5h" or a bare number of hours -> hours
  function parseHours(text) {
    text = String(text || '').trim().toLowerCase(); if (!text) return null;
    if (/^\d+(\.\d+)?$/.test(text)) return parseFloat(text);
    var h = /(\d+(?:\.\d+)?)\s*h/.exec(text), m = /(\d+)\s*m/.exec(text);
    if (!h && !m) return NaN;
    return (h ? parseFloat(h[1]) : 0) + (m ? parseInt(m[1], 10) / 60 : 0);
  }
  function openEditTask(t) {
    var d = dlg(), rec = t.task_type === 'Recurring', ev = eventIndex[t.name];
    d.className = 'cal-dlg';
    d.innerHTML = '<form method="dialog"><h3>Edit task</h3>' +
      '<label class="cal-f">Description<input id="et-desc" required value="' + esc(t.description) + '"' + (rec ? ' disabled' : '') + '></label>' +
      '<div class="cal-two"><label class="cal-f">Project<input id="et-proj" value="' + esc(t.project_name || '') + '"' + (rec ? ' disabled' : '') + '></label>' +
      '<label class="cal-f">Estimate<input id="et-est" value="' + esc(hoursText(durationHours(t))) + '" placeholder="e.g. 1h 30m"></label></div>' +
      '<label class="cal-f">Start time<input id="et-start" type="time" value="' + (ev ? toT(ev.start) : '') + '"></label>' +
      '<label class="cal-f">Remarks<textarea id="et-rem" rows="2" class="cal-ta" placeholder="Optional">' + esc(t.remarks || '') + '</textarea></label>' +
      '<div class="cal-f">Attachments<div class="cal-row" id="et-files"></div><input type="file" id="et-file" multiple style="height:auto;padding:6px 10px"></div>' +
      (rec || t.status !== 'Done' ? '' : '<div class="cal-f"><label class="cal-chk"><input type="checkbox" id="et-rep"' + (t.repeat_next_day ? ' checked' : '') + '><span>Repeat next day</span></label>' +
        '<span class="cal-hint" style="margin:0">A copy comes back on your next working day, even after you finish it. It keeps coming back until you turn this off.</span></div>') +
      (rec ? '<p class="cal-hint" style="margin:0">This task comes from a recurring template. Change its description or project on the Recurring Tasks page.</p>' : '') +
      '<p id="et-err" class="cal-err" role="alert"></p><div class="cal-actions"><button type="button" class="cal-btn" id="et-x">Cancel</button><button type="submit" class="cal-btn primary" id="et-ok">Save</button></div></form>';
    d.showModal(); $('#et-x').onclick = function () { d.close(); };
    mountAttachments($('#et-files'), t.name, true);
    var repeatChanged = false;
    if ($('#et-rep')) $('#et-rep').onchange = function () {
      var box = this, on = box.checked; box.disabled = true;
      api(CAL + 'set_task_repeat', { name: t.name, repeat: on ? 1 : 0 }, true).then(function () {
        repeatChanged = true; box.disabled = false; toast(on ? 'This task will come back on your next working day.' : 'This task will not repeat.');
      }).catch(function (er) { box.checked = !on; box.disabled = false; $('#et-err').textContent = er.message; });
    };
    d.onclose = function () { d.onclose = null; if (repeatChanged) refresh(); };
    $('#et-file').onchange = function () { uploadFiles(this.files, t.name, $('#et-files'), $('#et-err')); this.value = ''; };
    $('form', d).onsubmit = function (e) {
      e.preventDefault();
      var err = $('#et-err'), hrs = parseHours($('#et-est').value); err.textContent = '';
      if (hrs !== null && (isNaN(hrs) || hrs < 0.25 || hrs > 24)) { err.textContent = 'Estimate must be between 15 minutes and 24 hours, for example 1h 30m.'; return; }
      var args = { name: t.name, remarks: $('#et-rem').value };
      if (!rec) { args.description = $('#et-desc').value; args.project_name = $('#et-proj').value; }
      if (hrs !== null) args.estimated_hours = hrs;
      if ($('#et-start').value) args.start_time = $('#et-start').value;
      $('#et-ok').disabled = true;
      api(CAL + 'update_task', args, true).then(function () { d.close(); toast('Task updated.'); return refresh(); })
        .catch(function (er) { err.textContent = er.message; $('#et-ok').disabled = false; });
    };
  }

  /* ── add from the calendar: click an empty slot ── */
  var pendingSchedule = {};   // description -> 'HH:MM' for tasks planned before check-in
  function addChoices(date) {
    var choices = [];
    var todayOpen = date === BOOT.today && !(todayLog() && todayLog().eod_submitted) && !BOOT.is_late_checkout;
    if (todayOpen) choices.push(['task', 'Task for today']);
    if (date > BOOT.today) choices.push(['sched', 'Task for this day']);
    // Extra work is logged once the day is checked out (leave days are handled on the Additional Work page).
    var closed = logFor(date); if (date <= BOOT.today && closed && closed.eod_submitted) choices.push(['addl', 'Additional work']);
    choices.push(['backlog', 'Backlog (no date)']);
    return choices;
  }
  function openAddDialog(date, startMin) {
    var d = dlg(), choices = addChoices(date), first = choices[0][0];
    var past = date < BOOT.today, future = date > BOOT.today;
    d.className = 'cal-dlg';
    d.innerHTML = '<form method="dialog"><h3>Add to ' + esc(dayLabel(date)) + '</h3>' +
      (past ? '<p class="cal-hint" style="margin:0">Past days are locked for tasks. Extra work can be logged after check-out, or on a leave day, from the Additional Work page.</p>' : '') +
      (future ? '<p class="cal-hint" style="margin:0">It waits in your backlog and joins your check-in plan on that day.</p>' : '') +
      '<label class="cal-f">What is it<select id="ad-type">' + choices.map(function (c) { return '<option value="' + c[0] + '">' + c[1] + '</option>'; }).join('') + '</select></label>' +
      '<label class="cal-f">Description<input id="ad-desc" required placeholder="What are you working on?"></label>' +
      '<div class="cal-two"><label class="cal-f">Project<input id="ad-proj" placeholder="Optional"></label>' +
      '<label class="cal-f" id="ad-st-wrap">Status<select id="ad-st"><option>Pending</option><option>In Progress</option><option selected>Done</option></select></label></div>' +
      '<div class="cal-two" id="ad-times"><label class="cal-f"><span id="ad-l1">Start</span><input id="ad-s" type="time" value="' + toT(startMin) + '"></label>' +
      '<label class="cal-f"><span id="ad-l2">End</span><input id="ad-e" type="time" value="' + toT(Math.min(startMin + 60, 24 * 60 - 1)) + '"></label></div>' +
      '<p id="ad-note" class="cal-hint" style="margin:0"></p><p id="ad-err" class="cal-err" role="alert"></p>' +
      '<div class="cal-actions"><button type="button" class="cal-btn" id="ad-x">Cancel</button><button type="submit" class="cal-btn primary" id="ad-ok">Add</button></div></form>';
    var sync = function () {
      var t = $('#ad-type').value;
      $('#ad-times').hidden = t === 'backlog'; $('#ad-st-wrap').hidden = t !== 'addl';
      $('#ad-l1').textContent = t === 'addl' ? 'Login time' : 'Start'; $('#ad-l2').textContent = t === 'addl' ? 'Logout time' : 'End';
      $('#ad-note').textContent = t === 'sched' ? 'Saved to your backlog for this day. It joins your plan when the day comes.' : t === 'task' ? (morningDone() ? 'Saved to today and placed at the start time. Your Team Leader can see it.' : 'You have not checked in yet. It is added to today\'s plan and placed at this time when you check in.') :
        t === 'backlog' ? 'Saved to your backlog. Schedule it on the day you will do it.' : 'Logged as additional work for this day.';
    };
    $('#ad-type').value = first; sync(); $('#ad-type').onchange = sync;
    d.showModal(); $('#ad-desc').focus(); $('#ad-x').onclick = function () { d.close(); };
    $('form', d).onsubmit = function (e) { e.preventDefault(); submitAdd(date); };
  }
  function submitAdd(date) {
    var err = $('#ad-err'), ok = $('#ad-ok'), type = $('#ad-type').value, desc = $('#ad-desc').value.trim(), proj = $('#ad-proj').value.trim();
    var s0 = $('#ad-s').value, e0 = $('#ad-e').value; err.textContent = '';
    if (!desc) { err.textContent = 'Enter a description.'; return; }
    if (type !== 'backlog' && (!s0 || !e0 || toMin(e0) <= toMin(s0))) { err.textContent = (type === 'addl' ? 'Logout' : 'End') + ' time must be after ' + (type === 'addl' ? 'login' : 'start') + ' time.'; return; }
    var mins = type === 'backlog' ? 0 : toMin(e0) - toMin(s0), est = hoursText(mins / 60);
    ok.disabled = true; ok.textContent = 'Adding…';
    var run;
    if (type === 'task' && morningDone()) {
      run = api(API + 'add_adhoc_tasks', { tasks: JSON.stringify([{ client_id: 'cal', description: desc, project_name: proj, estimated_time: est }]) }, true).then(function (r) {
        var c = r.created && r.created[0]; return c ? api(CAL + 'set_task_schedule', { name: c.task_name, start_time: s0 }, true) : null;
      }).then(function () { toast('Task added for ' + fm(toMin(s0))); });
    } else if (type === 'task') {
      pendingSchedule[desc] = s0;
      var rows = $('#m-rows'); if (rows) {
        var first = rows.querySelector('.cal-trow'), empty = first && !$('[name=d]', first).value;
        if (!empty) { rows.insertAdjacentHTML('beforeend', taskRowHTML()); first = rows.lastElementChild; }
        $('[name=d]', first).value = desc; $('[name=p]', first).value = proj; $('[name=e]', first).value = est;
        schedulePlanSave();
      }
      run = Promise.resolve().then(function () { renderDrafts(); toast('Added to today\'s plan. Check in to place it at ' + fm(toMin(s0)) + '.'); });
    } else if (type === 'sched') {
      run = api(API + 'save_backlog_item', { description: desc, project_name: proj, estimated_time: est, remarks: '' }, true)
        .then(function (r) { return api(CAL + 'schedule_backlog_item', { name: r.name, date: date, start_time: s0 }, true); })
        .then(function () { toast('Scheduled for ' + dayLabel(date) + ', ' + fm(toMin(s0)) + '.'); });
    } else if (type === 'addl') {
      run = new Promise(function (resolve, reject) {
        frappe.call({ method: 'frappe.client.insert', silent: true, freeze: false, args: { doc: { doctype: 'Additional Work', work_date: date, project_name: proj, hours_spent: est, description: desc, login_time: s0, logout_time: e0, status: $('#ad-st').value } },
          callback: function (r) { r.message && r.message.name ? resolve() : reject(new Error('Could not save the entry.')); }, error: function (x) { reject(new Error(serverMessage(x))); } });
      }).then(function () { toast('Additional work added.'); });
    } else {
      run = api(API + 'save_backlog_item', { description: desc, project_name: proj, estimated_time: '', remarks: '' }, true).then(function () { toast('Added to your backlog.'); });
    }
    // The plan row lives in the morning form; re-rendering the panel would wipe it, so only reload when data changed on the server.
    var planOnly = type === 'task' && !morningDone();
    run.then(function () { dlg().close(); return planOnly ? null : refresh(); }).catch(function (e) { err.textContent = e.message; ok.disabled = false; ok.textContent = 'Add'; });
  }
  // Tasks planned from the calendar before check-in get their time once they exist.
  function applyPendingSchedule() {
    var names = Object.keys(pendingSchedule); if (!names.length) return Promise.resolve();
    var jobs = [];
    names.forEach(function (desc) {
      var t = S.day.tasks.filter(function (x) { return x.description === desc && !x.start_time; })[0];
      if (t) jobs.push(api(CAL + 'set_task_schedule', { name: t.name, start_time: pendingSchedule[desc] }, true).catch(function () { /* the task still exists, just unplaced */ }));
    });
    pendingSchedule = {};
    return Promise.all(jobs);
  }

  /* ── checkout dialog ── */
  var dlg = function () { return $('#cal-dlg'); };
  function openCheckout() {
    var log = openLog(), tasks = openDayTasks(), d = dlg();
    d.className = 'cal-dlg wide';
    d.innerHTML = '<form method="dialog"><h3>End of day check-out' + (BOOT.is_late_checkout ? ' · ' + esc(dayLabel(BOOT.active_date)) : '') + '</h3>' +
      (BOOT.is_late_checkout ? '<label class="cal-f">Time you actually logged out<input id="c-lo" type="time" required></label>' : '') +
      '<div class="cal-two"><label class="cal-f">Lunch from<input id="c-ls" type="time" value="' + esc(log.lunch_from || '') + '"></label><label class="cal-f">Lunch to<input id="c-le" type="time" value="' + esc(log.lunch_to || '') + '"></label></div>' +
      '<div class="cal-card cal-row cal-between"><span>Logout time, recorded when you submit</span><b>' + fmFull(nowMin()) + '</b></div>' +
      '<div class="cal-xs"><table><thead><tr><th>Task</th><th style="width:150px">Status</th><th style="width:110px">Time taken</th><th>Remarks</th><th style="width:90px">Carry on</th></tr></thead><tbody>' +
      tasks.map(function (t) {
        return '<tr data-name="' + esc(t.name) + '"><td><b style="overflow-wrap:anywhere">' + esc(t.description) + '</b><div style="color:var(--st-muted);font-size:11px">' + esc(t.project_name || '') + '</div></td>' +
          '<td><select data-f="st" aria-label="Status">' + Object.keys(STATUS).map(function (k) { return '<option value="' + k + '"' + (k === t.status ? ' selected' : '') + '>' + STATUS[k].label + '</option>'; }).join('') + '</select></td>' +
          '<td><input data-f="act" value="' + esc(hoursText(t.actual_time)) + '" placeholder="45m or 1h 30m" aria-label="Time taken (a bare number means minutes)"></td>' +
          '<td class="cal-rem"><button type="button" class="cal-link" data-f="rem-add" aria-label="Add a remark for ' + esc(t.description) + '" hidden>Add remark</button>' +
            '<textarea data-f="rem" rows="1" class="cal-ta" placeholder="Add a remark…" aria-label="Remark" hidden></textarea></td>' +
          '<td><label class="cal-chk"><input type="checkbox" data-f="carry" checked><span>Yes</span></label>' +
          (t.task_type === 'Recurring' ? '' : '<label class="cal-chk" data-rep-cell style="display:none"' + '><input type="checkbox" data-f="repeat"' + (t.repeat_next_day ? ' checked' : '') + '><span>Repeat next day</span></label>') + '</td></tr>';
      }).join('') + '</tbody></table></div>' +
      '<p id="c-note" style="margin:0;color:var(--st-muted)"></p><p id="c-err" class="cal-err" role="alert"></p>' +
      '<div class="cal-actions"><button type="button" class="cal-btn" id="c-x">Cancel</button><button type="submit" class="cal-btn primary" id="c-ok">Submit check-out</button></div></form>';
    var note = function () {
      $$('tr[data-name]', d).forEach(function (tr) {
        var st = $('[data-f=st]', tr).value, ta = $('[data-f=rem]', tr), add = $('[data-f=rem-add]', tr), eligible = st === 'Done' || st === 'In Progress';
        if (!eligible) { ta.value = ''; }
        add.hidden = !eligible || !ta.hidden;
        if (!eligible) ta.hidden = true;
      });
      var open = $$('tr[data-name]', d).filter(function (tr) { return $('[data-f=st]', tr).value !== 'Done'; });
      $$('tr[data-name]', d).forEach(function (tr) {
        var isDone = $('[data-f=st]', tr).value === 'Done', rep = $('[data-rep-cell]', tr);
        $('[data-f=carry]', tr).closest('label').style.display = isDone ? 'none' : '';
        if (rep) { rep.style.display = isDone ? '' : 'none'; if (!isDone) $('[data-f=repeat]', tr).checked = false; }
      });
      var carry = open.filter(function (tr) { return $('[data-f=carry]', tr).checked; }).length;
      $('#c-note', d).textContent = open.length ? carry + ' of ' + open.length + ' unfinished task' + (open.length > 1 ? 's' : '') + ' will carry over to the next working day.' : 'All tasks are done.';
    };
    note(); d.onchange = note;
    d.onclick = function (e) {
      var add = e.target.closest('[data-f=rem-add]');
      if (!add) return;
      var ta = $('[data-f=rem]', add.closest('tr')); ta.hidden = false; add.hidden = true; ta.focus();
    };
    d.showModal();
    $('#c-x').onclick = function () { d.close(); };
    $('form', d).onsubmit = function (e) {
      e.preventDefault();
      var err = $('#c-err'), ls = $('#c-ls').value, le = $('#c-le').value; err.textContent = '';
      var lo = BOOT.is_late_checkout ? $('#c-lo').value : toT(nowMin());
      if (!lo) { err.textContent = 'Enter the time you actually logged out.'; return; }
      if ((ls && !le) || (!ls && le)) { err.textContent = 'Enter both lunch start and end, or leave both empty.'; return; }
      if (ls && le && toMin(le) <= toMin(ls)) { err.textContent = 'Lunch end must be after lunch start.'; return; }
      var updates = [], bad = null;
      $$('tr[data-name]', d).forEach(function (tr) {
        var st = $('[data-f=st]', tr).value, act = $('[data-f=act]', tr).value.trim(), t = tasks.filter(function (x) { return x.name === tr.dataset.name; })[0];
        if (st === 'Done' && !act && !bad) bad = 'Enter the time taken for "' + t.description + '".';
        updates.push({ name: tr.dataset.name, status: st, actual_time: act, remarks: $('[data-f=rem]', tr).value.trim() || t.remarks || '', carry_forward: st === 'Done' ? true : $('[data-f=carry]', tr).checked, repeat_next_day: st === 'Done' && $('[data-f=repeat]', tr) && $('[data-f=repeat]', tr).checked ? 1 : 0 });
      });
      if (bad) { err.textContent = bad; return; }
      var ok = $('#c-ok'); ok.disabled = true; ok.textContent = 'Checking out…';
      api(API + 'submit_eod_log', { lunch_from: ls, lunch_to: le, logout_time: lo, task_updates: JSON.stringify(updates), adhoc_tasks: '[]' }, true).then(function (r) {
        d.close(); toast('Checked out at ' + r.logout_time + (r.pending_count ? '. ' + r.pending_count + ' unfinished task(s) carry over.' : '.'));
        if (BOOT.is_late_checkout) { setTimeout(function () { window.location.reload(); }, 1200); } else { S.page = null; loadAll(true); }
      }).catch(function (e2) { err.textContent = e2.message; ok.disabled = false; ok.textContent = 'Submit check-out'; });
    };
  }
  function confirmReset() {
    var d = dlg(); d.className = 'cal-dlg';
    d.innerHTML = '<form method="dialog"><h3>Reset today\'s check-in?</h3><p style="margin:0;color:var(--st-muted)">Your login time is cleared and tasks go back to Pending. You can check in again. This can be done once a day.</p><p id="r-err" class="cal-err" role="alert"></p><div class="cal-actions"><button type="button" class="cal-btn" id="r-x">Cancel</button><button type="submit" class="cal-btn primary">Reset check-in</button></div></form>';
    d.showModal(); $('#r-x').onclick = function () { d.close(); };
    $('form', d).onsubmit = function (e) {
      e.preventDefault();
      api(API + 'reset_morning_checkin', {}, true).then(function () { d.close(); toast('Check-in reset'); S.page = null; return loadAll(true); }).catch(function (er) { $('#r-err').textContent = er.message; });
    };
  }

  /* ── live view: the current-time line moves, and data refreshes quietly ── */
  var lastLoad = Date.now();
  function tickNowLine() {
    var line = $('.cal-now'), col = $('.cal-col[data-date="' + BOOT.today + '"]'), nm = nowMin();
    if (!col) return;
    if (nm < H0 * 60 || nm >= H1 * 60) { if (line) line.remove(); return; }
    if (!line) { line = document.createElement('div'); line.className = 'cal-now'; col.appendChild(line); }
    line.style.top = (nm - H0 * 60) * PPM + 'px';
  }
  function busy() {
    var d = dlg(), a = document.activeElement;
    return document.hidden || (d && d.open) || $('.cal-ev.drag, .cal-ev.resizing') ||
      (a && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName) && a.closest('#cal-side')) ||
      !!(document.getElementById('a-d') && document.getElementById('a-d').value.trim());
  }
  function liveRefresh() {
    if (busy() || Date.now() - lastLoad < 55000) return;
    lastLoad = Date.now();
    if (!morningDone()) {
      // Typing in the morning form must not be wiped: only reload once a check-in appears elsewhere (e.g. the bot).
      api(API + 'get_page_state').then(function (page) { if (page && page.morning_done) { S.page = null; loadAll(true).then(refresh); } }).catch(function () {});
      return;
    }
    var sc = $('#cal-scroll'), top = sc ? sc.scrollTop : 0, side = $('#cal-side'), sideTop = side ? side.scrollTop : 0;
    loadAll(true).then(function () {
      var sc2 = $('#cal-scroll'), side2 = $('#cal-side'); if (sc2) sc2.scrollTop = top; if (side2) side2.scrollTop = sideTop;
    }).catch(function () { /* next tick retries */ });
  }
  setInterval(function () { tickNowLine(); liveRefresh(); }, 30000);
  document.addEventListener('visibilitychange', function () { if (document.hidden) flushPlanSave(); else { tickNowLine(); liveRefresh(); } });
  window.addEventListener('pagehide', flushPlanSave);
  window.addEventListener('focus', function () { tickNowLine(); });

  /* ── shell events ── */
  function refresh() { return loadAll(false).catch(function (e) { toast(e.message, null, true); }); }
  window.addEventListener('st:nav', function (e) {
    var a = e.detail.action, c = S.cursor;
    if (a === 'today') S.cursor = P(BOOT.today);
    else {
      var dir = a === 'next' ? 1 : -1;
      if (S.view === 'month') S.cursor = new Date(c.getFullYear(), c.getMonth() + dir, 1);
      else S.cursor = addD(c, (S.view === 'week' ? 7 : 1) * dir);
    }
    refresh();
  });
  window.addEventListener('st:drop-date', function (e) {
    var parts = String(e.detail.payload || '').split('|'); if (parts[0] !== 'bk') return;
    placeBacklog(parts[1], e.detail.date, 9 * 60);
  });
  window.addEventListener('st:view', function (e) { S.view = e.detail.view; remember('view', S.view); refresh(); });
  window.addEventListener('st:goto-date', function (e) { e.preventDefault(); S.cursor = P(e.detail.date); if (S.view === 'month') S.view = 'day'; refresh(); });
  $$('[data-status-filter]').forEach(function (c) {
    c.checked = !S.hide[c.dataset.statusFilter];
    c.addEventListener('change', function () { S.hide[c.dataset.statusFilter] = !c.checked; remember('hide', S.hide); render(); });
  });

  var projectSel = $('#cal-project');
  if (projectSel) projectSel.addEventListener('change', function () { S.project = projectSel.value; render(); });

  frappe.ready(function () {
    if (window.initWhatsNew) { try { window.initWhatsNew(); } catch (e) { /* announcements are optional */ } }
    loadAll(true).catch(function (e) { $('#cal-main').innerHTML = '<div class="cal-empty" style="margin:24px">' + esc(e.message) + '</div>'; });
  });
})();
