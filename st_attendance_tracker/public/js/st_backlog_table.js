/* Task Backlog as a table (www/task_backlog.html). Uses the original backlog endpoints:
 * get_task_backlog, save_backlog_item, delete_backlog_item, pull_backlog_item_to_today.
 * The original card view stays under the "Cards" toggle.
 */
(function () {
  'use strict';
  var BOOT = window.ST_BL;
  var DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  var rows = [];

  function $(s, r) { return (r || document).querySelector(s); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function serverMessage(xhr) {
    try {
      var json = xhr.responseJSON || JSON.parse(xhr.responseText || '{}');
      if (json._server_messages) return JSON.parse(json._server_messages).map(function (m) { return JSON.parse(m).message; }).join(' ').replace(/<[^>]+>/g, '');
    } catch (e) { /* fall through */ }
    return 'Something went wrong. Please try again.';
  }
  function call(method, args) {
    return new Promise(function (resolve, reject) {
      frappe.call({ method: 'st_attendance_tracker.' + (method.indexOf('.') >= 0 ? method : 'api.' + method), args: args || {}, type: 'POST', silent: true, freeze: false, callback: function (r) { resolve(r.message); }, error: function (x) { reject(new Error(serverMessage(x))); } });
    });
  }
  function toast(msg, isErr) {
    document.querySelectorAll('.cal-toast').forEach(function (t) { t.remove(); });
    var t = document.createElement('div'); t.className = 'cal-toast' + (isErr ? ' err' : ''); t.setAttribute('role', isErr ? 'alert' : 'status'); t.textContent = msg;
    document.body.appendChild(t); setTimeout(function () { t.remove(); }, isErr ? 6000 : 3500);
  }
  function carriedFrom(row) {
    if (!row.creation) return '-';
    var d = new Date(String(row.creation).replace(' ', 'T'));
    return isNaN(d) ? '-' : DOW[d.getDay()] + ', ' + d.getDate() + ' ' + d.toLocaleString('en', { month: 'short' });
  }

  function scheduledLabel(r) {
    if (!r.scheduled_for) return '—';
    var d = new Date(r.scheduled_for + 'T00:00:00');
    return DOW[d.getDay()] + ', ' + d.getDate() + ' ' + d.toLocaleString('en', { month: 'short' }) + (r.start_time ? ' · ' + r.start_time : '');
  }

  function render() {
    var body = $('#bl-rows'), empty = $('#bl-empty');
    $('#bl-count').textContent = rows.length;
    $('#bl-all').disabled = !rows.length || BOOT.checked_out;
    if (!rows.length) { body.innerHTML = ''; empty.hidden = false; return; }
    empty.hidden = true;
    body.innerHTML = rows.map(function (r) {
      var age = r.age_days || 0;
      return '<tr draggable="true" data-name="' + esc(r.name) + '"><td><b style="overflow-wrap:anywhere">' + esc(r.description) + '</b>' +
        (r.assigned_by_name ? '<div><span class="cal-tag black">Assigned by ' + esc(r.assigned_by_name) + '</span></div>' : '') + (r.remarks ? '<small class="pg-sub">' + esc(r.remarks) + '</small>' : '') + '</td>' +
        '<td>' + (r.project_name ? esc(r.project_name) : '—') + '</td><td>' + (r.estimated_time ? esc(r.estimated_time) : '—') + '</td><td>' + esc(carriedFrom(r)) + '</td><td>' + (r.scheduled_for ? '<span class="cal-tag black">' + esc(scheduledLabel(r)) + '</span>' : '—') + '</td>' +
        '<td><span class="cal-tag' + (r.is_stale ? ' red-line' : '') + '">' + age + ' day' + (age === 1 ? '' : 's') + '</span></td>' +
        '<td class="pg-actions-cell"><button type="button" class="cal-btn" data-act="today">Schedule today</button>' + (r.scheduled_for ? '<button type="button" class="cal-btn" data-act="unsched">Clear day</button>' : '') +
        '<button type="button" class="cal-ibtn" data-act="edit" title="Edit" aria-label="Edit task"><i class="ti ti-pencil" aria-hidden="true"></i></button>' +
        '<button type="button" class="cal-ibtn" data-act="del" title="Delete" aria-label="Delete task"><i class="ti ti-trash" aria-hidden="true"></i></button></td></tr>';
    }).join('');
  }

  function load() {
    return call('get_task_backlog').then(function (r) { rows = r || []; render(); }).catch(function (e) { $('#bl-rows').innerHTML = '<tr><td colspan="7"><div class="cal-empty">' + esc(e.message) + '</div></td></tr>'; });
  }

  function pull(name) {
    if (BOOT.checked_out) { toast('You have already checked out today.', true); return Promise.resolve(); }
    return call('pull_backlog_item_to_today', { name: name }).then(function () { toast('Scheduled for today.'); return load(); }).catch(function (e) { toast(e.message, true); });
  }

  function openDialog(row) {
    var d = $('#bl-dlg');
    d.innerHTML = '<form method="dialog"><h3>' + (row ? 'Edit backlog task' : 'Add backlog task') + '</h3>' +
      '<label class="cal-f">Task<input id="bd-desc" required value="' + esc(row ? row.description : '') + '" placeholder="What needs doing?"></label>' +
      '<div class="cal-two"><label class="cal-f">Project<input id="bd-proj" value="' + esc(row ? row.project_name || '' : '') + '"></label>' +
      '<label class="cal-f">Estimated time<input id="bd-est" value="' + esc(row ? row.estimated_time || '' : '') + '" placeholder="e.g. 1h 30m"></label></div>' +
      '<label class="cal-f">Remarks<input id="bd-rem" value="' + esc(row ? row.remarks || '' : '') + '" placeholder="Optional"></label>' +
      '<p id="bd-err" class="cal-err" role="alert"></p><div class="cal-actions"><button type="button" class="cal-btn" id="bd-x">Cancel</button><button type="submit" class="cal-btn primary" id="bd-ok">Save</button></div></form>';
    d.showModal(); $('#bd-desc').focus();
    $('#bd-x').onclick = function () { d.close(); };
    $('form', d).onsubmit = function (e) {
      e.preventDefault();
      var desc = $('#bd-desc').value.trim(); if (!desc) { $('#bd-err').textContent = 'Enter a task description.'; return; }
      var est = $('#bd-est').value.trim(); if (/^\d+(\.\d+)?$/.test(est)) est += 'h'; // a bare number would be read as minutes
      $('#bd-ok').disabled = true;
      call('save_backlog_item', { name: row ? row.name : null, description: desc, project_name: $('#bd-proj').value.trim(), estimated_time: est, remarks: $('#bd-rem').value.trim() })
        .then(function () { d.close(); toast('Saved.'); return load(); }).catch(function (er) { $('#bd-err').textContent = er.message; $('#bd-ok').disabled = false; });
    };
  }

  function bind() {
    var tbody = $('#bl-rows');
    tbody.addEventListener('click', function (e) {
      var b = e.target.closest('[data-act]'); if (!b) return;
      var name = b.closest('tr').dataset.name, row = rows.filter(function (r) { return r.name === name; })[0];
      if (b.dataset.act === 'today') pull(name);
      else if (b.dataset.act === 'edit') openDialog(row);
      else if (b.dataset.act === 'unsched') call('calendar_api.schedule_backlog_item', { name: name }).then(function () { toast('Day cleared.'); return load(); }).catch(function (er) { toast(er.message, true); });
      else if (b.dataset.act === 'del') call('delete_backlog_item', { name: name }).then(function () { toast('Task removed from backlog.'); return load(); }).catch(function (er) { toast(er.message, true); });
    });
    tbody.addEventListener('dragstart', function (e) { var tr = e.target.closest('tr[data-name]'); if (tr) e.dataTransfer.setData('text/plain', 'bk|' + tr.dataset.name); });
    $('#bl-add').addEventListener('click', function () { openDialog(null); });
    $('#bl-all').addEventListener('click', function () {
      var chain = Promise.resolve();
      rows.slice().forEach(function (r) { chain = chain.then(function () { return call('pull_backlog_item_to_today', { name: r.name }); }); });
      chain.then(function () { toast('All backlog tasks scheduled for today.'); return load(); }).catch(function (e) { toast(e.message, true); return load(); });
    });
    // Mini calendar: only today can be scheduled (a backlog task has no date of its own)
    window.addEventListener('st:drop-date', function (e) {
      var parts = String(e.detail.payload || '').split('|'); if (parts[0] !== 'bk') return;
      if (e.detail.date === BOOT.today) pull(parts[1]);
      else if (e.detail.date < BOOT.today) toast('A task cannot be scheduled for a day that has passed.', true);
      else call('calendar_api.schedule_backlog_item', { name: parts[1], date: e.detail.date }).then(function () { toast('Scheduled for ' + e.detail.date + '.'); return load(); }).catch(function (er) { toast(er.message, true); });
    });
    var btns = document.querySelectorAll('[data-bl-view]');
    btns.forEach(function (b) {
      b.addEventListener('click', function () {
        var cards = b.dataset.blView === 'cards';
        $('#bl-table-view').hidden = cards; $('#bl-cards').hidden = !cards;
        document.querySelector('.cols').classList.toggle('bl-cards-mode', cards);
        btns.forEach(function (x) { x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); });
      });
    });
    // The table needs sideways scrolling on a phone; start on the card view there.
    if (window.matchMedia('(max-width: 700px)').matches) {
      var cardBtn = document.querySelector('[data-bl-view="cards"]'); if (cardBtn) cardBtn.click();
    }
    if (window.location.hash === '#new') openDialog(null);
  }

  frappe.ready(function () { bind(); load(); });
})();
