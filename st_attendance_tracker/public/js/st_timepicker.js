/* StandardTouch time picker.
   Enhances every <input type="time"> (also ones added later, e.g. inside dialogs).
   The native input stays in the page, hidden, and keeps its "HH:MM" value, so existing code that
   reads .value, sets .value or listens for input/change keeps working unchanged. */
(function () {
  if (window.STTimePicker) return;
  var MIN_STEP = 5;
  var valueDesc = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
  var openPicker = null;

  function pad(n) { return String(n).padStart(2, '0'); }
  function toDisplay(v) {
    var m = /^(\d{1,2}):(\d{2})/.exec(v || ''); if (!m) return '';
    var h = +m[1], ap = h >= 12 ? 'PM' : 'AM';
    return ((h % 12) || 12) + ':' + m[2] + ' ' + ap;
  }
  // Accepts 930, 9:30, 9.30 pm, 21:15, 9pm
  function parse(text) {
    var t = String(text || '').trim().toLowerCase().replace(/\./g, ':');
    if (!t) return '';
    var ap = /pm?$/.test(t) ? 'pm' : /am?$/.test(t) ? 'am' : '';
    t = t.replace(/[ap]m?$/, '').trim();
    var h, m;
    if (t.indexOf(':') > -1) { var p = t.split(':'); h = +p[0]; m = +p[1]; }
    else if (/^\d{3,4}$/.test(t)) { h = +t.slice(0, t.length - 2); m = +t.slice(-2); }
    else if (/^\d{1,2}$/.test(t)) { h = +t; m = 0; }
    else return null;
    if (isNaN(h) || isNaN(m) || m > 59) return null;
    if (ap === 'pm' && h < 12) h += 12;
    if (ap === 'am' && h === 12) h = 0;
    if (h > 23) return null;
    return pad(h) + ':' + pad(m);
  }
  function nowValue() { var d = new Date(); return pad(d.getHours()) + ':' + pad(d.getMinutes()); }

  function enhance(input) {
    if (input.dataset.stTime || input.type !== 'time') return;
    input.dataset.stTime = '1';
    var wrap = document.createElement('span'); wrap.className = 'stp';
    var text = document.createElement('input');
    text.type = 'text'; text.className = 'stp-text'; text.inputMode = 'text'; text.autocomplete = 'off';
    text.placeholder = 'e.g. 9:30 AM';
    text.setAttribute('aria-label', (input.getAttribute('aria-label') || labelFor(input) || 'Time'));
    var btn = document.createElement('button');
    btn.type = 'button'; btn.className = 'stp-btn'; btn.tabIndex = -1; btn.setAttribute('aria-label', 'Choose time');
    btn.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>';
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(text); wrap.appendChild(btn); wrap.appendChild(input);
    input.classList.add('stp-native'); input.tabIndex = -1; input.setAttribute('aria-hidden', 'true');
    // Copy the sizing the page gave the native input onto the wrapper
    if (input.style.width) wrap.style.width = input.style.width;
    if (input.style.height) wrap.style.setProperty('--stp-h', input.style.height);

    function sync() {
      text.value = toDisplay(valueDesc.get.call(input));
      var locked = input.readOnly || input.disabled;
      text.readOnly = locked; btn.disabled = locked; wrap.classList.toggle('is-locked', locked);
    }
    Object.defineProperty(input, 'value', {
      configurable: true,
      get: function () { return valueDesc.get.call(input); },
      set: function (v) { valueDesc.set.call(input, v); sync(); }
    });
    new MutationObserver(sync).observe(input, { attributes: true, attributeFilter: ['readonly', 'disabled'] });

    function commit(v) {
      if (v === valueDesc.get.call(input)) { sync(); return; }
      valueDesc.set.call(input, v); sync();
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
    }
    function fromText() {
      var v = parse(text.value);
      if (v === null) { wrap.classList.add('is-bad'); return false; }
      wrap.classList.remove('is-bad');
      if (v === '' && input.required) { wrap.classList.add('is-bad'); return false; }
      commit(v); return true;
    }
    text.addEventListener('change', fromText);
    text.addEventListener('blur', function () { if (!fromText()) sync(), wrap.classList.remove('is-bad'); });
    text.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); if (fromText()) closePicker(); }
      else if (e.key === 'Escape') { closePicker(); }
      else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
        e.preventDefault();
        var cur = valueDesc.get.call(input) || nowValue(), p = cur.split(':'), mins = +p[0] * 60 + +p[1];
        mins = (mins + (e.key === 'ArrowUp' ? MIN_STEP : -MIN_STEP) + 1440) % 1440;
        commit(pad(Math.floor(mins / 60)) + ':' + pad(mins % 60));
      }
    });
    function toggle() { if (wrap.classList.contains('is-locked')) return; if (openPicker && openPicker.input === input) closePicker(); else showPicker(input, wrap, commit); }
    btn.addEventListener('click', toggle);
    text.addEventListener('focus', function () { text.select(); });
    text.addEventListener('click', function () { if (!openPicker || openPicker.input !== input) toggle(); });
    // A label click should focus the visible field
    input.addEventListener('focus', function () { text.focus(); });
    sync();
  }

  function labelFor(input) {
    var l = input.closest('label'); if (l) return (l.firstChild && l.firstChild.textContent || '').trim();
    if (input.id) { var f = document.querySelector('label[for="' + input.id + '"]'); if (f) return f.textContent.trim(); }
    return '';
  }

  function closePicker() {
    if (!openPicker) return;
    openPicker.el.remove(); document.removeEventListener('mousedown', outside, true); window.removeEventListener('resize', closePicker);
    openPicker = null;
  }
  function outside(e) { if (openPicker && !openPicker.el.contains(e.target) && !openPicker.wrap.contains(e.target)) closePicker(); }

  // Clock-face picker: pick the hour on the dial, then the minute; OK applies, Cancel discards.
  function showPicker(input, wrap, commit) {
    closePicker();
    var cur = valueDesc.get.call(input) || nowValue(), p = cur.split(':'), H = +p[0], M = +p[1];
    var st = { h12: (H % 12) || 12, m: M, pm: H >= 12, phase: 'h' };
    var R = 96, C = 120, NS = 'http://www.w3.org/2000/svg';
    var el = document.createElement('div'); el.className = 'stp-pop'; el.setAttribute('role', 'dialog'); el.setAttribute('aria-label', 'Select time');
    function value() { return pad(st.h12 % 12 + (st.pm ? 12 : 0)) + ':' + pad(st.m); }
    function angle() { return st.phase === 'h' ? (st.h12 % 12) * 30 : st.m * 6; }
    function pt(deg, r) { var a = (deg - 90) * Math.PI / 180; return [C + r * Math.cos(a), C + r * Math.sin(a)]; }
    function dial() {
      var deg = angle(), end = pt(deg, R), nums = '';
      var labels = st.phase === 'h' ? [12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11] : [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55];
      labels.forEach(function (n, i) { var q = pt(i * 30, R); nums += '<text x="' + q[0] + '" y="' + q[1] + '" class="stp-n">' + (st.phase === 'm' ? pad(n) : n) + '</text>'; });
      var odd = st.phase === 'm' && st.m % 5 !== 0;
      return '<svg viewBox="0 0 240 240" class="stp-dial" aria-hidden="true"><circle cx="120" cy="120" r="116" class="stp-face"/>' +
        '<line x1="120" y1="120" x2="' + end[0] + '" y2="' + end[1] + '" class="stp-hand"/><circle cx="120" cy="120" r="3.5" class="stp-pin"/>' +
        '<circle cx="' + end[0] + '" cy="' + end[1] + '" r="' + (odd ? 5 : 19) + '" class="stp-knob"/>' + nums +
        (odd ? '' : '') + '</svg>';
    }
    function render() {
      el.innerHTML = '<div class="stp-title">Select time</div>' +
        '<div class="stp-head"><button type="button" data-phase="h" class="stp-box' + (st.phase === 'h' ? ' on' : '') + '">' + pad(st.h12) + '</button><span class="stp-colon">:</span>' +
        '<button type="button" data-phase="m" class="stp-box' + (st.phase === 'm' ? ' on' : '') + '">' + pad(st.m) + '</button>' +
        '<div class="stp-ap" role="group" aria-label="AM or PM"><button type="button" data-ap="AM"' + (!st.pm ? ' class="on"' : '') + '>a.m.</button><button type="button" data-ap="PM"' + (st.pm ? ' class="on"' : '') + '>p.m.</button></div></div>' +
        '<div class="stp-dialwrap" tabindex="0" aria-label="Clock face">' + dial() + '</div>' +
        '<div class="stp-foot"><button type="button" class="stp-ico" data-kbd aria-label="Type the time" title="Type the time"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/></svg></button>' +
        '<span style="flex:1"></span><button type="button" class="stp-text-btn" data-now>Now</button><button type="button" class="stp-text-btn" data-cancel>Cancel</button><button type="button" class="stp-text-btn strong" data-ok>OK</button></div>';
    }
    function setFromPointer(ev, final) {
      var svg = el.querySelector('.stp-dial'); if (!svg) return;
      var r = svg.getBoundingClientRect(), x = ev.clientX - (r.left + r.width / 2), y = ev.clientY - (r.top + r.height / 2);
      var deg = (Math.atan2(y, x) * 180 / Math.PI + 90 + 360) % 360;
      if (st.phase === 'h') { st.h12 = Math.round(deg / 30) % 12 || 12; }
      else { st.m = Math.round(deg / 6) % 60; }
      if (final && st.phase === 'h') st.phase = 'm';
      render();
    }
    var dragging = false;
    el.addEventListener('pointerdown', function (e) {
      if (!e.target.closest('.stp-dialwrap')) return;
      dragging = true; el.setPointerCapture && el.setPointerCapture(e.pointerId); setFromPointer(e, false);
    });
    el.addEventListener('pointermove', function (e) { if (dragging) setFromPointer(e, false); });
    el.addEventListener('pointerup', function (e) { if (!dragging) return; dragging = false; setFromPointer(e, true); });
    el.addEventListener('click', function (e) {
      var b = e.target.closest('button'); if (!b) return;
      if (b.dataset.phase) st.phase = b.dataset.phase;
      else if (b.dataset.ap) st.pm = b.dataset.ap === 'PM';
      else if (b.hasAttribute('data-now')) { var n = nowValue().split(':'); st.pm = +n[0] >= 12; st.h12 = (+n[0] % 12) || 12; st.m = +n[1]; }
      else if (b.hasAttribute('data-cancel')) { closePicker(); return; }
      else if (b.hasAttribute('data-ok')) { commit(value()); closePicker(); return; }
      else if (b.hasAttribute('data-kbd')) { closePicker(); var t = wrap.querySelector('.stp-text'); t.focus(); t.select(); return; }
      render();
    });
    el.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { closePicker(); wrap.querySelector('.stp-text').focus(); return; }
      if (e.key === 'Enter') { commit(value()); closePicker(); return; }
      var d = e.key === 'ArrowUp' || e.key === 'ArrowRight' ? 1 : e.key === 'ArrowDown' || e.key === 'ArrowLeft' ? -1 : 0;
      if (!d) return; e.preventDefault();
      if (st.phase === 'h') st.h12 = ((st.h12 - 1 + d + 12) % 12) + 1; else st.m = (st.m + d * (e.shiftKey ? 1 : MIN_STEP) + 60) % 60;
      render();
    });
    render();
    var host = wrap.closest('dialog') || wrap.closest('.st-app') || document.body; // inside .st-app so the theme tokens apply
    host.appendChild(el);
    var r = wrap.getBoundingClientRect(), pr = el.getBoundingClientRect();
    var top = r.bottom + 6; if (top + pr.height > innerHeight - 8) top = Math.max(8, r.top - pr.height - 6);
    var left = Math.min(Math.max(8, r.left), innerWidth - pr.width - 8);
    el.style.top = top + 'px'; el.style.left = left + 'px';
    openPicker = { el: el, wrap: wrap, input: input };
    document.addEventListener('mousedown', outside, true); window.addEventListener('resize', closePicker);
  }

  function scan(root) { (root || document).querySelectorAll('input[type=time]').forEach(enhance); }
  function start() {
    scan();
    new MutationObserver(function (muts) {
      muts.forEach(function (m) { m.addedNodes.forEach(function (n) { if (n.nodeType === 1) { if (n.matches && n.matches('input[type=time]')) enhance(n); else if (n.querySelectorAll) scan(n); } }); });
    }).observe(document.body, { childList: true, subtree: true });
  }
  window.STTimePicker = { enhance: enhance, parse: parse, toDisplay: toDisplay };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
