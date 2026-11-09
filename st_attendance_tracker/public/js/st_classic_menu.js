/* Classic pages on phones: the left navigation is a slide-in menu opened from the top-bar hamburger. */
(function () {
  var burger = document.getElementById('cl-burger'), drawer = document.getElementById('cl-drawer'),
      scrim = document.getElementById('cl-scrim'), closeBtn = document.getElementById('cl-close');
  if (!burger || !drawer) return;
  function setOpen(open) {
    document.body.classList.toggle('cl-menu-open', open);
    burger.setAttribute('aria-expanded', open ? 'true' : 'false');
    burger.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
    if (open) { var first = drawer.querySelector('.sb-link'); if (first) first.focus(); } else burger.focus({ preventScroll: true });
  }
  burger.addEventListener('click', function () { setOpen(!document.body.classList.contains('cl-menu-open')); });
  if (scrim) scrim.addEventListener('click', function () { setOpen(false); });
  if (closeBtn) closeBtn.addEventListener('click', function () { setOpen(false); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && document.body.classList.contains('cl-menu-open')) setOpen(false); });
  // Rotating or widening to desktop must not leave the page locked behind the menu
  window.addEventListener('resize', function () { if (window.innerWidth > 900 && document.body.classList.contains('cl-menu-open')) setOpen(false); });
})();
