(function () { // design chooser
  var l = document.createElement('link'); l.rel = 'stylesheet'; l.href = '/assets/st_attendance_tracker/css/st_design_pref.css?v=2'; document.head.appendChild(l);
  var j = document.createElement('script'); j.src = '/assets/st_attendance_tracker/js/st_design_pref.js?v=1'; document.head.appendChild(j);
})();
(function () { // time picker for every <input type="time">
  var l = document.createElement('link'); l.rel = 'stylesheet'; l.href = '/assets/st_attendance_tracker/css/st_timepicker.css?v=2'; document.head.appendChild(l);
  var j = document.createElement('script'); j.src = '/assets/st_attendance_tracker/js/st_timepicker.js?v=4'; document.head.appendChild(j);
})();
/* Behaviour for the branded page shell (templates/macros/st_shell.html).
 * Pages talk to the shell through DOM events:
 *   st:nav        {action: 'today'|'prev'|'next'}   top-bar date controls
 *   st:view       {view: 'day'|'week'|'month'}      view switch
 *   st:goto-date  {date: 'YYYY-MM-DD'}              mini calendar click (preventDefault to handle in-page)
 * and the STShell API below.
 */
(function () {
  var DESKTOP = window.matchMedia('(min-width: 901px)');
  var MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

  function store(key, value) {
    try {
      if (value === undefined) return window.localStorage.getItem(key);
      window.localStorage.setItem(key, value);
    } catch (e) { /* storage blocked: state just isn't remembered */ }
    return null;
  }
  function iso(d) { return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }
  function parse(s) { var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s || ''); return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null; }
  function emit(name, detail) { return window.dispatchEvent(new CustomEvent(name, { detail: detail, cancelable: true })); }
  function ready(fn) { if (document.readyState !== 'loading') fn(); else document.addEventListener('DOMContentLoaded', fn); }

  var api = window.STShell = {
    selected: parse(window.ST_SHELL_DATE) || new Date(),
    // On a phone the top bar is crowded: shorten month names and a trailing ", 2026" so a date title is not cut off.
    setTitle: function (text) {
      var t = document.getElementById('st-title'); if (!t) return;
      var shown = text;
      if (window.innerWidth <= 600) {
        shown = String(text).replace(/\b(January|February|March|April|May|June|July|August|September|October|November|December)\b/g, function (m) { return m.slice(0, 3); })
          .replace(/,\s*\d{4}$/, '');
      }
      t.textContent = shown; t.title = text;
    },
    setView: function (view) {
      document.querySelectorAll('[data-st-view]').forEach(function (b) { b.setAttribute('aria-pressed', b.dataset.stView === view ? 'true' : 'false'); });
    },
    setDate: function (date) { api.selected = date; if (api.renderMini) api.renderMini(true); }
  };

  ready(function () {
    var body = document.body;
    var menuBtn = document.getElementById('st-menu-btn');
    var scrim = document.getElementById('st-scrim');
    var avatar = document.getElementById('st-avatar');
    var userMenu = document.getElementById('st-user-menu');
    if (!menuBtn || !avatar) return;

    /* ── left rail: collapses on desktop (remembered), slides in on phones ── */
    function railOpen() { return DESKTOP.matches ? !body.classList.contains('st-rail-collapsed') : body.classList.contains('st-rail-open'); }
    function setRail(open) {
      if (DESKTOP.matches) { body.classList.toggle('st-rail-collapsed', !open); store('st.rail.collapsed', open ? '0' : '1'); }
      else body.classList.toggle('st-rail-open', open);
      menuBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
    if (DESKTOP.matches && store('st.rail.collapsed') === '1') body.classList.add('st-rail-collapsed');
    menuBtn.setAttribute('aria-expanded', railOpen() ? 'true' : 'false');
    menuBtn.addEventListener('click', function () { setRail(!railOpen()); });
    scrim.addEventListener('click', function () { setRail(false); });

    /* ── theme: light / dark, remembered; defaults to the system setting ── */
    var themeBtn = document.getElementById('st-theme-btn');
    function isDark() {
      var t = document.documentElement.getAttribute('data-theme');
      return t ? t === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
    }
    function paintThemeBtn() {
      if (!themeBtn) return;
      themeBtn.innerHTML = '<i class="ti ' + (isDark() ? 'ti-sun' : 'ti-moon') + '" aria-hidden="true"></i>';
      themeBtn.setAttribute('aria-label', isDark() ? 'Switch to light theme' : 'Switch to dark theme');
    }
    if (themeBtn) {
      paintThemeBtn();
      themeBtn.addEventListener('click', function () {
        var next = isDark() ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', next); store('st.theme', next); paintThemeBtn();
        emit('st:theme', { theme: next });
      });
    }

    /* ── popup menus ── */
    function popup(btn, menu) {
      function set(open) { menu.hidden = !open; btn.setAttribute('aria-expanded', open ? 'true' : 'false'); }
      btn.addEventListener('click', function (e) { e.stopPropagation(); set(menu.hidden); });
      document.addEventListener('click', function (e) { if (!menu.contains(e.target)) set(false); });
      document.addEventListener('keydown', function (e) { if (e.key === 'Escape') set(false); });
    }
    popup(avatar, userMenu);
    var createBtn = document.getElementById('st-create'), createMenu = document.getElementById('st-create-menu');
    if (createBtn && createMenu) popup(createBtn, createMenu);
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !DESKTOP.matches) setRail(false); });

    /* ── top-bar date controls and view switch ── */
    document.querySelectorAll('[data-st-nav]').forEach(function (b) {
      b.addEventListener('click', function () { emit('st:nav', { action: b.dataset.stNav }); });
    });
    var views = document.getElementById('st-views');
    if (views) views.addEventListener('click', function (e) {
      var b = e.target.closest('[data-st-view]'); if (!b) return;
      api.setView(b.dataset.stView); emit('st:view', { view: b.dataset.stView });
    });

    /* ── mini month calendar ── */
    var mini = document.getElementById('st-mini');
    if (mini) {
      var month = new Date(api.selected.getFullYear(), api.selected.getMonth(), 1);
      api.renderMini = function (follow) {
        if (follow) month = new Date(api.selected.getFullYear(), api.selected.getMonth(), 1);
        var today = iso(new Date()), sel = iso(api.selected), first = month.getDay();
        var h = '<div class="st-mini-h"><b>' + MONTHS[month.getMonth()] + ' ' + month.getFullYear() + '</b><span>' +
          '<button type="button" class="st-icon-btn st-sm" data-m="-1" aria-label="Previous month"><i class="ti ti-chevron-left" aria-hidden="true"></i></button>' +
          '<button type="button" class="st-icon-btn st-sm" data-m="1" aria-label="Next month"><i class="ti ti-chevron-right" aria-hidden="true"></i></button></span></div>' +
          '<div class="st-mini-g">' + 'SMTWTFS'.split('').map(function (c) { return '<span>' + c + '</span>'; }).join('');
        for (var i = 0; i < 42; i++) {
          var d = new Date(month.getFullYear(), month.getMonth(), 1 - first + i), k = iso(d);
          var cls = (d.getMonth() !== month.getMonth() ? 'out ' : '') + (k === today ? 'today' : (k === sel ? 'sel' : ''));
          h += '<button type="button" data-date="' + k + '" class="' + cls + '" aria-label="' + d.toDateString() + '">' + d.getDate() + '</button>';
        }
        mini.innerHTML = h + '</div>';
      };
      mini.addEventListener('click', function (e) {
        var m = e.target.closest('[data-m]');
        if (m) { month = new Date(month.getFullYear(), month.getMonth() + (+m.dataset.m), 1); api.renderMini(); return; }
        var d = e.target.closest('[data-date]'); if (!d) return;
        if (emit('st:goto-date', { date: d.dataset.date })) window.location.href = '/daily-checkin?date=' + d.dataset.date;
      });
      mini.addEventListener('dragover', function (e) {
        var b = e.target.closest('[data-date]'); if (!b) return;
        e.preventDefault(); mini.querySelectorAll('.over').forEach(function (x) { x.classList.remove('over'); }); b.classList.add('over');
      });
      mini.addEventListener('dragleave', function (e) { if (!e.relatedTarget || !mini.contains(e.relatedTarget)) mini.querySelectorAll('.over').forEach(function (x) { x.classList.remove('over'); }); });
      mini.addEventListener('drop', function (e) {
        var b = e.target.closest('[data-date]'); if (!b) return;
        e.preventDefault(); mini.querySelectorAll('.over').forEach(function (x) { x.classList.remove('over'); });
        emit('st:drop-date', { date: b.dataset.date, payload: e.dataTransfer.getData('text/plain') });
      });
      api.renderMini();
    }

    /* ── collapsible side panels: any [data-st-collapsible="Label"] element ── */
    document.querySelectorAll('[data-st-collapsible]').forEach(function (panel) {
      var label = panel.dataset.stCollapsible || 'panel';
      var key = 'st.panel.' + window.location.pathname + '.' + label;
      var btn = document.createElement('button');
      btn.type = 'button'; btn.className = 'st-collapse-btn';
      function set(collapsed, remember) {
        panel.classList.toggle('st-collapsed', collapsed);
        btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        btn.setAttribute('aria-label', (collapsed ? 'Expand ' : 'Collapse ') + label);
        btn.title = btn.getAttribute('aria-label');
        btn.innerHTML = '<i class="ti ' + (collapsed ? 'ti-chevrons-left' : 'ti-chevrons-right') + '" aria-hidden="true"></i>';
        if (remember) store(key, collapsed ? '1' : '0');
      }
      panel.insertBefore(btn, panel.firstChild);
      set(store(key) === '1', false);
      btn.addEventListener('click', function () { set(!panel.classList.contains('st-collapsed'), true); });
    });
  });
})();
