// Mobile debug instrumentation. Only loaded when ?debug=1 is in the URL.
// Captures touch/pointer/click events and POSTs them to /api/_debug so we
// can diagnose tap/click bugs on physical mobile devices we don't have
// access to (no ADB from Oracle Cloud → user's home Xiaomi).
(function () {
  'use strict';
  var sid = Math.random().toString(36).slice(2, 10);

  function pathDesc(e) {
    var p = e.composedPath ? e.composedPath() : [];
    return p.slice(0, 8).map(function (n) {
      if (!n || !n.nodeName) return '?';
      var s = n.nodeName;
      if (n.id) s += '#' + n.id;
      if (n.className && typeof n.className === 'string') {
        var cls = n.className.split(/\s+/).filter(Boolean).slice(0, 3);
        if (cls.length) s += '.' + cls.join('.');
      }
      return s;
    }).join(' > ');
  }

  function send(type, detail) {
    try {
      var payload = JSON.stringify({
        sid: sid,
        t: Date.now(),
        type: type,
        target: detail.target,
        path: detail.path,
        defaultPrevented: detail.defaultPrevented,
        extra: detail.extra || null,
        ua: navigator.userAgent,
        vp: [window.innerWidth, window.innerHeight, window.devicePixelRatio],
      });
      if (navigator.sendBeacon) {
        navigator.sendBeacon('/api/_debug', new Blob([payload], {type: 'application/json'}));
      } else {
        // Fallback for browsers without sendBeacon (rare in 2026).
        var xhr = new XMLHttpRequest();
        xhr.open('POST', '/api/_debug', true);
        xhr.setRequestHeader('Content-Type', 'application/json');
        xhr.send(payload);
      }
    } catch (_) { /* swallow */ }
  }

  function wrap(type) {
    return function (e) {
      var tgt = e.target;
      var targetHtml = '';
      try {
        if (tgt && tgt.outerHTML) targetHtml = tgt.outerHTML.slice(0, 200);
        else if (tgt && tgt.nodeName) targetHtml = tgt.nodeName;
      } catch (_) {}
      var extra = null;
      if (e.touches && e.touches[0]) {
        extra = {x: Math.round(e.touches[0].clientX), y: Math.round(e.touches[0].clientY)};
      } else if (e.clientX !== undefined) {
        extra = {x: Math.round(e.clientX), y: Math.round(e.clientY)};
      }
      send(type, {
        target: targetHtml,
        path: pathDesc(e),
        defaultPrevented: e.defaultPrevented,
        extra: extra,
      });
    };
  }

  var types = ['touchstart', 'touchend', 'touchcancel',
               'pointerdown', 'pointerup', 'click'];
  types.forEach(function (t) {
    document.addEventListener(t, wrap(t), {capture: true, passive: true});
  });

  window.addEventListener('error', function (ev) {
    send('jserror', {
      target: String(ev.message || '').slice(0, 200),
      path: (ev.filename || '') + ':' + (ev.lineno || 0),
      defaultPrevented: false,
    });
  });

  // Heartbeat: confirms the script actually loaded and ran on the device.
  send('_loaded', {
    target: 'instrumentation-ready',
    path: 'sid=' + sid,
    defaultPrevented: false,
  });

  // Also expose a hook so the user (or a test script) can manually probe.
  window.__debugPing = function (label) {
    send('_ping', {target: String(label || ''), path: '', defaultPrevented: false});
  };

  // Every 2s while the page is open, probe the hamburger button and report
  // its state (position, computed display, element-from-point at its center).
  // This catches the case where the button is where we think it is in DOM
  // but the user's device renders or layers it differently.
  var probeCount = 0;
  var probeInterval = setInterval(function () {
    if (++probeCount > 10) { clearInterval(probeInterval); return; }
    try {
      var b = document.getElementById('sidebar-toggle');
      if (!b) { send('_probe', {target: 'sidebar-toggle-missing', path: '', defaultPrevented: false}); return; }
      var r = b.getBoundingClientRect();
      var cs = window.getComputedStyle(b);
      var cx = r.x + r.width / 2, cy = r.y + r.height / 2;
      var topEl = document.elementFromPoint(cx, cy);
      send('_probe', {
        target: 'sidebar-toggle',
        path: topEl ? (topEl.nodeName + (topEl.id ? '#' + topEl.id : '')) : 'null',
        defaultPrevented: false,
        extra: {
          rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
          display: cs.display,
          visibility: cs.visibility,
          pointerEvents: cs.pointerEvents,
          touchAction: cs.touchAction,
          zIndex: cs.zIndex,
          bodyOpen: document.body.classList.contains('sidebar-open'),
        },
      });
    } catch (_) {}
  }, 2000);
})();
