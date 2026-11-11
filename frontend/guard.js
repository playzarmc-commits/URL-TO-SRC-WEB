// Anti-inspection layer (a deterrent, not real security — real protection is server-side).
(function () {
  var mobile = /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent);
  function lock() {
    try {
      document.documentElement.innerHTML =
        '<body style="margin:0;display:grid;place-items:center;height:100vh;background:#07070d;color:#f3f3fa;font-family:sans-serif;text-align:center">' +
        '<div><h2>Developer tools detected</h2><p style="color:#a6a6bd">Close them and reload the page.</p></div></body>';
    } catch (e) {}
  }
  document.addEventListener('contextmenu', function (e) { e.preventDefault(); });
  document.addEventListener('keydown', function (e) {
    var k = (e.key || '').toLowerCase();
    if (e.keyCode === 123 || (e.ctrlKey && e.shiftKey && 'ijc'.indexOf(k) > -1) || (e.ctrlKey && (k === 'u' || k === 's'))) {
      e.preventDefault();
    }
  });
  if (mobile) return;
  setInterval(function () {
    var t = performance.now();
    debugger;
    if (performance.now() - t > 120) lock();
    else if (window.outerWidth - window.innerWidth > 220 || window.outerHeight - window.innerHeight > 260) lock();
  }, 1000);
})();
