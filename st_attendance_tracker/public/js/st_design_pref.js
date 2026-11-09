/* Default design chooser: calendar (new) or classic.
   - First visit with no saved choice: asks once (dismiss with "Not now" to be asked next session).
   - "Default design" in the account menu reopens it any time.
   - The "New design" / "Classic design" switch links only change the view for this browser session
     (cookie st_view); the saved default changes only through this dialog. */
(function () {
  if (window.STDesignPref) return;
  var SUFFIX = '-classic';
  var OPTIONS = [
    { key: 'new', title: 'Calendar design', text: 'Week and day calendar with drag and drop, a panel for today, and quick add.', icon: 'ti-calendar-event' },
    { key: 'classic', title: 'Classic design', text: 'The original list layout with task rows, filters and the concise table.', icon: 'ti-layout-list' }
  ];
  var current = null, loaded = false;

  function call(method, args) {
    return new Promise(function (resolve, reject) {
      frappe.call({ method: method, args: args || {}, type: args ? 'POST' : 'GET', silent: true,
        callback: function (r) { resolve(r.message); }, error: function () { reject(new Error('Could not reach the server.')); } });
    });
  }
  function setCookie(v) { document.cookie = 'st_view=' + v + '; path=/; SameSite=Lax'; }
  function clearCookie() { document.cookie = 'st_view=; path=/; max-age=0; SameSite=Lax'; }
  function isClassic() { return location.pathname.replace(/\/$/, '').slice(-SUFFIX.length) === SUFFIX; }
  function counterpart(design) {
    var p = location.pathname.replace(/\/$/, '');
    if (design === 'classic' && !isClassic()) return p + SUFFIX;
    if (design === 'new' && isClassic()) return p.slice(0, -SUFFIX.length);
    return null;
  }
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }

  function ask(opts) {
    opts = opts || {};
    if (document.getElementById('st-design-dlg')) return;
    var choice = current || 'classic'; // matches DEFAULT_DESIGN in ui_shell.py
    var dlg = document.createElement('dialog'); dlg.id = 'st-design-dlg'; dlg.className = 'st-dsg';
    function paint() {
      dlg.innerHTML = '<form method="dialog"><h3>' + (opts.first ? 'Which design do you want to start with?' : 'Default design') + '</h3>' +
        '<p>' + (opts.first ? 'You can change this at any time from your account menu.' : 'This opens whenever you visit a page. You can still switch for one visit from the account menu.') + '</p>' +
        '<div class="st-dsg-opts" role="radiogroup" aria-label="Design">' + OPTIONS.map(function (o) {
          return '<button type="button" role="radio" class="st-dsg-opt" data-k="' + o.key + '" aria-checked="' + (choice === o.key) + '"><i class="ti ' + o.icon + '" aria-hidden="true"></i><b>' + esc(o.title) + '</b><span>' + esc(o.text) + '</span></button>';
        }).join('') + '</div><p class="st-dsg-err" role="alert"></p>' +
        '<div class="st-dsg-act"><button type="button" class="st-dsg-btn" data-x>' + (opts.first ? 'Not now' : 'Cancel') + '</button><button type="submit" class="st-dsg-btn pri">Save</button></div></form>';
    }
    paint(); document.body.appendChild(dlg); dlg.showModal();
    dlg.addEventListener('close', function () { dlg.remove(); });
    dlg.addEventListener('click', function (e) {
      var b = e.target.closest('button'); if (!b) return;
      if (b.dataset.k) { choice = b.dataset.k; paint(); var again = dlg.querySelector('[data-k="' + choice + '"]'); if (again) again.focus(); }
      else if (b.hasAttribute('data-x')) { try { sessionStorage.setItem('st.design.asked', '1'); } catch (er) { /* optional */ } dlg.close(); }
    });
    dlg.addEventListener('submit', function (e) {
      e.preventDefault();
      var save = dlg.querySelector('.pri'); save.disabled = true;
      call('st_attendance_tracker.ui_shell.set_design_preference', { design: choice }).then(function () {
        current = choice; setCookie(choice); dlg.close();
        var to = counterpart(choice); if (to) location.assign(to + location.search + location.hash);
      }).catch(function (err) { dlg.querySelector('.st-dsg-err').textContent = err.message; save.disabled = false; });
    });
  }

  function addMenuItem() {
    var menu = document.getElementById('st-user-menu') || document.getElementById('profile-dropdown');
    if (!menu || menu.querySelector('[data-st-design]')) return;
    var a = document.createElement('a'); a.href = '#'; a.setAttribute('data-st-design', '1'); a.setAttribute('role', 'menuitem');
    a.className = menu.id === 'profile-dropdown' ? 'pd-item' : '';
    a.innerHTML = '<i class="ti ti-layout-board" aria-hidden="true"></i>Default design';
    a.addEventListener('click', function (e) { e.preventDefault(); ask({}); });
    var anchor = menu.querySelector('a[href="/app"]'); menu.insertBefore(a, anchor || null);
  }

  function boot() {
    addMenuItem();
    // Switch links change the view for this browser session only.
    document.addEventListener('click', function (e) {
      var a = e.target.closest('[data-st-switch]'); if (a) setCookie(a.getAttribute('data-st-switch'));
    });
    call('st_attendance_tracker.ui_shell.get_design_preference').then(function (pref) {
      current = pref; loaded = true;
      var asked = false; try { asked = sessionStorage.getItem('st.design.asked') === '1'; } catch (e) { /* ask */ }
      if (!pref && !asked) ask({ first: true });
    }).catch(function () { /* never block the page */ });
  }
  window.STDesignPref = { open: function () { ask({}); } };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
