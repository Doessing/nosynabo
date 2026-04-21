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

  // Immediately probe whether the page's own JS functions are defined.
  // If toggleSidebarMobile is undefined, the onclick attribute silently
  // does nothing even though click events fire.
  setTimeout(function () {
    try {
      send('_fns', {
        target: 'global-function-check',
        path: 'toggle=' + typeof window.toggleSidebarMobile +
              ' open=' + typeof window.openSidebarMobile +
              ' close=' + typeof window.closeSidebarMobile +
              ' isMobile=' + typeof window.isMobileViewport +
              ' addAddress=' + typeof window.addAddress +
              ' L=' + typeof window.L +
              ' map=' + typeof window.map,
        defaultPrevented: false,
      });
    } catch (e) {
      send('_fns_error', {target: String(e.message), path: '', defaultPrevented: false});
    }
  }, 500);

  // Monkey-patch toggleSidebarMobile to observe each invocation.
  setTimeout(function () {
    try {
      if (typeof window.toggleSidebarMobile === 'function') {
        var orig = window.toggleSidebarMobile;
        window.toggleSidebarMobile = function () {
          send('_fn_called', {
            target: 'toggleSidebarMobile',
            path: 'before: bodyOpen=' + document.body.classList.contains('sidebar-open'),
            defaultPrevented: false,
          });
          try {
            var r = orig.apply(this, arguments);
            send('_fn_called', {
              target: 'toggleSidebarMobile',
              path: 'after: bodyOpen=' + document.body.classList.contains('sidebar-open'),
              defaultPrevented: false,
            });
            return r;
          } catch (e) {
            send('_fn_error', {
              target: 'toggleSidebarMobile threw',
              path: String(e.message || e),
              defaultPrevented: false,
            });
          }
        };
      } else {
        send('_fn_missing', {target: 'toggleSidebarMobile undefined', path: '', defaultPrevented: false});
      }
    } catch (_) {}
  }, 800);

  // Also expose a hook so the user (or a test script) can manually probe.
  window.__debugPing = function (label) {
    send('_ping', {target: String(label || ''), path: '', defaultPrevented: false});
  };

  // Every 2s while the page is open, probe the hamburger button AND sidebar
  // state so we can see exactly what's rendered vs what we expect.
  var probeCount = 0;
  var probeInterval = setInterval(function () {
    if (++probeCount > 15) { clearInterval(probeInterval); return; }
    try {
      var b = document.getElementById('sidebar-toggle');
      var sb = document.getElementById('sidebar');
      var bd = document.getElementById('sidebar-backdrop');
      var btnInfo = null, sbInfo = null, bdInfo = null;
      if (b) {
        var br = b.getBoundingClientRect();
        var bcs = window.getComputedStyle(b);
        btnInfo = {
          rect: [Math.round(br.x), Math.round(br.y), Math.round(br.width), Math.round(br.height)],
          display: bcs.display,
        };
      }
      if (sb) {
        var sr = sb.getBoundingClientRect();
        var scs = window.getComputedStyle(sb);
        sbInfo = {
          rect: [Math.round(sr.x), Math.round(sr.y), Math.round(sr.width), Math.round(sr.height)],
          display: scs.display,
          visibility: scs.visibility,
          position: scs.position,
          transform: scs.transform,
          zIndex: scs.zIndex,
          opacity: scs.opacity,
          width: scs.width,
          left: scs.left,
          top: scs.top,
        };
      }
      if (bd) {
        var dr = bd.getBoundingClientRect();
        var dcs = window.getComputedStyle(bd);
        bdInfo = {
          rect: [Math.round(dr.x), Math.round(dr.y), Math.round(dr.width), Math.round(dr.height)],
          display: dcs.display,
          opacity: dcs.opacity,
        };
      }
      // Element at sidebar's expected center (if open)
      var elAt = null;
      try {
        var el = document.elementFromPoint(50, window.innerHeight / 2);
        if (el) elAt = el.nodeName + (el.id ? '#' + el.id : '') + (el.className ? '.' + String(el.className).split(/\s+/).slice(0,2).join('.') : '');
      } catch (_) {}
      send('_probe', {
        target: 'full-state',
        path: 'bodyOpen=' + document.body.classList.contains('sidebar-open') + ' elAt50x=' + elAt,
        defaultPrevented: false,
        extra: {
          btn: btnInfo,
          sidebar: sbInfo,
          backdrop: bdInfo,
          matchesMobileMQ: window.matchMedia('(max-width: 767px), (max-height: 500px) and (max-width: 900px)').matches,
        },
      });
    } catch (e) {
      send('_probe_error', {target: String(e.message), path: '', defaultPrevented: false});
    }
  }, 2000);
})();
