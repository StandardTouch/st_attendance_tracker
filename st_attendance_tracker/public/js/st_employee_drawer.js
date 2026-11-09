/* Read-only employee record drawer for Team Leaders and HR / Management.
 * Data: api.get_employee_task_detail(employee_name, date); the server decides who may look.
 * Usage: STDrawer.open({name, employee_name, department, designation}, 'YYYY-MM-DD', {onAssign: fn})
 */
(function () {
  'use strict';
  var STATUS = {
    'Pending': { c: 's-pending', ic: '○', label: 'Pending' },
    'In Progress': { c: 's-prog', ic: '◐', label: 'In progress' },
    'Done': { c: 's-done', ic: '✓', label: 'Done' }
  };
  var DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  var MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  function pad(n) { return String(n).padStart(2, '0'); }
  function iso(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function P(s) { var p = s.split('-').map(Number); return new Date(p[0], p[1] - 1, p[2]); }
  function addD(d, n) { var x = new Date(d); x.setDate(x.getDate() + n); return x; }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function toMin(t) { var p = String(t).split(':'); return (+p[0]) * 60 + (+p[1] || 0); }
  function fmFull(t) { if (!t) return '-'; var m = toMin(t); return ((Math.floor(m / 60) % 12) || 12) + ':' + pad(m % 60) + ' ' + (Math.floor(m / 60) < 12 ? 'AM' : 'PM'); }
  function hoursText(h) {
    h = +h || 0; if (h <= 0) return '';
    var total = Math.round(h * 60), hh = Math.floor(total / 60), mm = total % 60;
    return hh && mm ? hh + 'h ' + mm + 'm' : hh ? hh + 'h' : mm + 'm';
  }
  function dayLabel(s) { var d = P(s); return DOW[d.getDay()] + ', ' + MON[d.getMonth()] + ' ' + d.getDate(); }
  function serverMessage(xhr) {
    try {
      var json = xhr.responseJSON || JSON.parse(xhr.responseText || '{}');
      if (json._server_messages) return JSON.parse(json._server_messages).map(function (m) { return JSON.parse(m).message; }).join(' ').replace(/<[^>]+>/g, '');
    } catch (e) { /* fall through */ }
    return 'Could not load this record.';
  }

  var state = null, gen = 0;

  function close() {
    state = null; gen++;
    var d = $('#st-drawer'); if (d) d.innerHTML = '';
  }

  function attachments(t) {
    return (t.attachments || []).map(function (f) {
      return '<a class="cal-tag" target="_blank" rel="noopener" href="/api/method/st_attendance_tracker.api.view_task_attachment?file_name=' + encodeURIComponent(f.name) + '&task_name=' + encodeURIComponent(t.name) + '"><i class="ti ti-paperclip" aria-hidden="true"></i>' + esc(f.file_name) + '</a>';
    }).join(' ');
  }

  function extraHTML(data) {
    var extra = data.additional_work || [];
    if (!extra.length) return '';
    return '<div class="cal-row cal-between" style="margin:16px 0 6px"><b>Additional work</b></div>' + extra.map(function (a) {
      var st = STATUS[a.status] || STATUS.Done;
      return '<div class="cal-task"><div class="cal-row cal-between" style="flex-wrap:nowrap;align-items:flex-start"><b style="overflow-wrap:anywhere">' + esc(a.description) + '</b><span class="cal-tag ' + st.c + '" style="flex:none">' + st.ic + ' ' + st.label + '</span></div><small>' + (a.login_time ? fmFull(a.login_time) : '') + ' – ' + (a.logout_time ? fmFull(a.logout_time) : '') + ' · ' + hoursText(a.hours_spent) + '</small></div>';
    }).join('');
  }

  function body(data) {
    var m = data.morning_log, e = data.eod_log, tasks = (data.tasks || []).filter(function (t) { return STATUS[t.status]; });
    if (!m && !tasks.length) return '<div class="cal-empty">No check-in recorded for this day.</div>' + extraHTML(data);
    var done = tasks.filter(function (t) { return t.status === 'Done'; }).length;
    var html = !m ? '<div class="cal-empty" style="margin-bottom:8px">Not checked in yet. These tasks are planned for the day.</div>' : '<div class="cal-card"><ul class="cal-tl">' +
      '<li><span>Check-in</span><span>' + fmFull(m.login_time) + (m.is_late ? ' <span class="cal-tag red-line">Late</span>' : '') + '</span></li>' +
      '<li class="' + (e && e.lunch_from ? '' : 'todo') + '"><span>Lunch</span><span>' + (e && e.lunch_from && e.lunch_to ? fmFull(e.lunch_from) + ' – ' + fmFull(e.lunch_to) : '-') + '</span></li>' +
      '<li class="' + (e ? '' : 'todo') + '"><span>Check-out</span><span>' + (e ? fmFull(e.logout_time) : 'Not yet') + '</span></li>' +
      '<li class="' + (e ? '' : 'todo') + '"><span>Net hours</span><span>' + (e ? esc(e.net_hours || '-') : 'In progress') + '</span></li></ul></div>';
    html += '<div class="cal-row cal-between" style="margin:16px 0 6px"><b>Tasks</b><span class="cal-tag">' + done + ' / ' + tasks.length + ' done</span></div>' +
      '<div class="cal-bar" style="margin-bottom:10px"><i style="width:' + (tasks.length ? done / tasks.length * 100 : 0) + '%"></i></div>';
    var canMove = state.team && state.onReassign && state.date === state.today && !e;
    html += tasks.map(function (t) {
      var st = STATUS[t.status];
      var movable = canMove && t.status === 'Pending' && t.task_type !== 'Recurring' && !(t.attachments || []).length;
      return '<div class="cal-task"><div class="cal-row cal-between" style="flex-wrap:nowrap;align-items:flex-start"><b style="overflow-wrap:anywhere">' + esc(t.description) + '</b><span class="cal-tag ' + st.c + '" style="flex:none">' + st.ic + ' ' + st.label + '</span></div>' +
        '<small>' + esc(t.project_name || '') + (t.project_name ? ' · ' : '') + esc(t.task_type || 'Task') + (t.is_carried ? ' · carried' : '') + (t.assigned_by_name ? ' · assigned by ' + esc(t.assigned_by_name) : '') +
        (t.estimated_time ? ' · est ' + hoursText(t.estimated_time) : '') + (t.actual_time ? ' · actual ' + hoursText(t.actual_time) : '') + '</small>' +
        (t.remarks ? '<small>' + esc(t.remarks) + '</small>' : '') + (attachments(t) ? '<div class="cal-row">' + attachments(t) + '</div>' : '') +
        (movable ? '<select class="cal-input" data-reassign="' + esc(t.name) + '" aria-label="Reassign ' + esc(t.description) + '" style="height:30px;margin-top:4px"><option value="">Reassign to…</option>' +
          state.team.filter(function (m) { return m.name !== state.emp.name; }).map(function (m) { return '<option value="' + esc(m.name) + '">' + esc(m.employee_name) + '</option>'; }).join('') + '</select>' : '') + '</div>';
    }).join('');
    if (!tasks.length) html += '<div class="cal-empty">No tasks on this day.</div>';
    var extra = data.additional_work || [];
    if (extra.length) {
      var total = extra.reduce(function (t, a) { return t + (+a.hours_spent || 0); }, 0);
      html += '<div class="cal-row cal-between" style="margin:16px 0 6px"><b>Additional work</b><span class="cal-tag">' + hoursText(total) + '</span></div>' + extra.map(function (a) {
        var st = STATUS[a.status] || STATUS.Done;
        return '<div class="cal-task"><div class="cal-row cal-between" style="flex-wrap:nowrap;align-items:flex-start"><b style="overflow-wrap:anywhere">' + esc(a.description) + '</b><span class="cal-tag ' + st.c + '" style="flex:none">' + st.ic + ' ' + st.label + '</span></div>' +
          '<small>' + esc(a.project_name || '') + (a.project_name ? ' · ' : '') + (a.login_time ? fmFull(a.login_time) : '') + ' – ' + (a.logout_time ? fmFull(a.logout_time) : '') + ' · ' + hoursText(a.hours_spent) + '</small>' + (a.remarks ? '<small>' + esc(a.remarks) + '</small>' : '') + '</div>';
      }).join('');
    }
    return html;
  }

  function render(data, err) {
    var emp = state.emp, today = state.today, isToday = state.date >= today;
    var initials = (emp.employee_name || '').split(' ').filter(Boolean).slice(0, 2).map(function (w) { return w[0]; }).join('').toUpperCase();
    var d = $('#st-drawer'); if (!d) return;
    d.innerHTML = '<aside class="st-drawer" role="dialog" aria-label="' + esc(emp.employee_name) + ' record">' +
      '<div class="st-d-head"><span class="st-d-av">' + esc(initials) + '</span><div style="flex:1;min-width:0"><h3 style="margin:0;font-size:17px">' + esc(emp.employee_name) + '</h3><div style="color:var(--st-muted)">' + esc(emp.department || '') + (emp.designation ? ' · ' + esc(emp.designation) : '') + '</div></div>' +
      '<button type="button" class="cal-ibtn" id="std-close" aria-label="Close"><i class="ti ti-x" aria-hidden="true"></i></button></div>' +
      '<div class="st-d-nav"><button type="button" class="cal-ibtn" id="std-prev" aria-label="Previous day"><i class="ti ti-chevron-left" aria-hidden="true"></i></button><b>' + dayLabel(state.date) + (state.date === today ? ' · Today' : '') + '</b>' +
      '<button type="button" class="cal-ibtn" id="std-next" aria-label="Next day"' + (isToday ? ' disabled' : '') + '><i class="ti ti-chevron-right" aria-hidden="true"></i></button><span style="flex:1"></span>' +
      (state.onAssign ? '<button type="button" class="cal-btn" id="std-assign">Assign task</button>' : '') + '</div>' +
      '<div class="st-d-body">' + (err ? '<div class="cal-empty">' + esc(err) + '</div>' : data ? body(data) : '<div class="cal-empty">Loading…</div>') + '</div>' +
      '<div class="st-d-foot"><i class="ti ti-lock" aria-hidden="true"></i><span>Read-only. Visible to this employee\'s Team Leader and to HR / Management.</span></div></aside>';
    $('#std-close').onclick = close;
    $('#std-prev').onclick = function () { open(emp, iso(addD(P(state.date), -1)), { onAssign: state.onAssign, today: today, team: state.team, onReassign: state.onReassign }); };
    $('#std-next').onclick = function () { open(emp, iso(addD(P(state.date), 1)), { onAssign: state.onAssign, today: today, team: state.team, onReassign: state.onReassign }); };
    var as = $('#std-assign'); if (as) as.onclick = function () { state.onAssign(emp); };
    $$('[data-reassign]', d).forEach(function (sel) {
      sel.onchange = function () {
        if (!sel.value) return; sel.disabled = true;
        Promise.resolve(state.onReassign(sel.dataset.reassign, sel.value)).then(function () { open(emp, state.date, { onAssign: state.onAssign, today: today, team: state.team, onReassign: state.onReassign }); })
          .catch(function () { sel.disabled = false; sel.value = ''; });
      };
    });
    $('#std-close').focus();
  }

  function open(emp, date, opts) {
    opts = opts || {};
    state = { emp: emp, date: date, onAssign: opts.onAssign || null, today: opts.today || iso(new Date()), team: opts.team || null, onReassign: opts.onReassign || null };
    var my = ++gen;
    render(null);
    frappe.call({
      method: 'st_attendance_tracker.api.get_employee_task_detail', args: { employee_name: emp.name, date: date }, silent: true, freeze: false,
      callback: function (r) { if (my === gen && state) render(r.message); },
      error: function (xhr) { if (my === gen && state) render(null, serverMessage(xhr)); }
    });
  }

  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && state) close(); });
  function refresh() { if (state) open(state.emp, state.date, state); }
  window.STDrawer = { open: open, close: close, refresh: refresh };
})();
