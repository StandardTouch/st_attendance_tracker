(function () { // design chooser
  var l = document.createElement('link'); l.rel = 'stylesheet'; l.href = '/assets/st_attendance_tracker/css/st_design_pref.css?v=2'; document.head.appendChild(l);
  var j = document.createElement('script'); j.src = '/assets/st_attendance_tracker/js/st_design_pref.js?v=1'; document.head.appendChild(j);
})();
(function () { // time picker for every <input type="time">
  var l = document.createElement('link'); l.rel = 'stylesheet'; l.href = '/assets/st_attendance_tracker/css/st_timepicker.css?v=2'; document.head.appendChild(l);
  var j = document.createElement('script'); j.src = '/assets/st_attendance_tracker/js/st_timepicker.js?v=4'; document.head.appendChild(j);
})();
/* Adds a "New design" link to the top bar of a classic page. */
(function () {
  var s = document.currentScript, target = s && s.dataset.new;
  if (!target) return;
  function add() {
    var bar = document.querySelector('.tnav-r');
    if (!bar || bar.querySelector('.st-switch')) return;
    var a = document.createElement('a');
    a.className = 'st-switch'; a.href = target; a.setAttribute('data-st-switch', 'new'); a.title = 'Open the new calendar design';
    a.innerHTML = '<i class="ti ti-layout-dashboard" aria-hidden="true"></i>New design';
    var bell = bar.querySelector('.wn-bell-wrap'); bar.insertBefore(a, bell || bar.lastElementChild);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', add); else add();
})();
