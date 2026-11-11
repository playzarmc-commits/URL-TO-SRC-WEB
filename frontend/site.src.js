(function () {
  'use strict';
  var input = document.getElementById('url');
  var res = document.getElementById('res');
  var cfBox = document.getElementById('cf');
  var cfMsg = document.getElementById('cfmsg');
  var buttons = document.querySelectorAll('#go-site, [data-act]');
  var token = null, tokenAt = 0, ttl = 600000, busy = false, captchaOn = false, widget = null;

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function refreshButtons() {
    buttons.forEach(function (b) { b.disabled = busy || !token; });
  }
  function say(msg) { cfMsg.textContent = msg || ''; }
  function verify(cf) {
    return fetch('/web/verify', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cf: cf || '' }) })
      .then(function (r) { return r.json(); }).then(function (j) {
        if (!j.ok) throw new Error(j.error || 'Verification failed.');
        token = j.token; tokenAt = Date.now(); ttl = (j.ttl || 600) * 1000;
        say(''); refreshButtons(); return token;
      });
  }
  function needCheck(message) {
    token = null; refreshButtons();
    say(message || 'Please complete the security check to continue.');
    if (captchaOn && window.turnstile && widget !== null) window.turnstile.reset(widget);
  }
  function getToken() {
    if (token && Date.now() - tokenAt < ttl - 60000) return Promise.resolve(token);
    if (captchaOn) { needCheck('Security check expired — please complete it again.'); return Promise.reject(new Error('Please complete the security check first.')); }
    return verify('');
  }
  function api(path, opts, retried) {
    opts = opts || {};
    return getToken().then(function (t) {
      var headers = { 'X-Web-Token': t };
      if (opts.body) headers['Content-Type'] = 'application/json';
      return fetch(path, { method: opts.method || 'GET', headers: headers, body: opts.body }).then(function (r) {
        if (r.status === 401) {
          token = null;
          if (captchaOn) { needCheck('Security check expired — please complete it again.'); throw new Error('Security check expired. Complete it again, then retry.'); }
          if (!retried) return api(path, opts, true);
        }
        if (r.ok && opts.blob) return r.blob();
        return r.json().then(function (j) {
          if (!r.ok || j.ok === false) throw new Error(j.error || 'Request failed (' + r.status + ')');
          return j;
        });
      });
    });
  }
  function setBusy(v) { busy = v; refreshButtons(); }
  function panel() { res.hidden = false; res.replaceChildren(); return res; }
  function fail(msg) {
    var p = panel(); p.appendChild(el('h3', 'err', 'Something went wrong')); p.appendChild(el('p', 'err', msg));
    setBusy(false);
  }
  function normalise() {
    var v = (input.value || '').trim();
    if (!v) return null;
    if (!/^https?:\/\//i.test(v)) v = 'https://' + v;
    return /^https?:\/\/[^\s/]+\.[^\s/]+/i.test(v) ? v : null;
  }
  function fact(grid, label, value) {
    var d = el('div', null, label); d.appendChild(el('b', null, String(value))); grid.appendChild(d);
  }

  function showSeo(d) {
    var p = panel(); p.appendChild(el('h3', null, 'SEO report'));
    var g = el('div', 'kv');
    fact(g, 'Status', d.status); fact(g, 'Load time', d.load_time + ' s'); fact(g, 'Page size', d.size_kb + ' KB');
    fact(g, 'Links', d.links); fact(g, 'Images', d.images);
    p.appendChild(g);
    p.appendChild(el('p', null, 'Title: ' + d.title));
    p.appendChild(el('p', null, 'Description: ' + d.meta_description));
    var h = Object.keys(d.headings).filter(function (k) { return d.headings[k] > 0; })
      .map(function (k) { return k.toUpperCase() + ': ' + d.headings[k]; }).join('   ');
    p.appendChild(el('p', null, 'Headings: ' + (h || 'none found')));
    setBusy(false);
  }
  function showTech(d) {
    var p = panel(); p.appendChild(el('h3', null, 'Tech stack'));
    var wrap = el('div', 'chips');
    d.detected.forEach(function (t) { wrap.appendChild(el('span', 'chip', t)); });
    p.appendChild(wrap);
    p.appendChild(el('p', null, 'Detection uses HTML and header signatures and may not be 100% accurate.'));
    setBusy(false);
  }

  function download(job) {
    api('/web/jobs/' + job.job_id + '/download', { blob: true }).then(function (blob) {
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = job.result.filename;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 4000);
    }).catch(function (e) { fail(e.message); });
  }
  function showDone(job) {
    var r = job.result, p = panel();
    p.appendChild(el('h3', 'okc', 'Your package is ready'));
    var g = el('div', 'kv');
    fact(g, 'Pages', r.pages); fact(g, 'Assets', r.assets);
    if (r.skipped) fact(g, 'Skipped', r.skipped);
    fact(g, 'ZIP size', (r.size_bytes / 1048576).toFixed(2) + ' MB');
    p.appendChild(g);
    p.appendChild(el('p', null, 'Extract it and open index.html. The file is kept for 30 minutes.'));
    var b = el('button', 'btn pri', 'Download ZIP'); b.type = 'button';
    b.addEventListener('click', function () { download(job); });
    p.appendChild(b);
    setBusy(false);
  }
  function poll(id, kind) {
    api('/web/jobs/' + id).then(function (job) {
      if (job.status === 'done') return showDone(job);
      if (job.status === 'failed') return fail(job.error || 'The site could not be processed.');
      var p = panel(), prog = job.progress || {};
      p.appendChild(el('h3', null, kind === 'site' ? 'Crawling the website…' : 'Building your package…'));
      p.appendChild(el('p', null, job.status === 'queued'
        ? 'In the queue — position #' + job.queue_position
        : (kind === 'site' ? 'Pages captured: ' + (prog.pages || 0) : 'Collecting files…')));
      var bar = el('div', 'pb' + (kind === 'site' ? '' : ' ind')); var fill = el('i');
      if (kind === 'site') fill.style.width = Math.max(8, Math.min(100, (prog.pages || 0) / 150 * 100)) + '%';
      bar.appendChild(fill); p.appendChild(bar);
      if (prog.message) p.appendChild(el('p', null, prog.message));
      setTimeout(function () { poll(id, kind); }, 1500);
    }).catch(function (e) { fail(e.message); });
  }

  function run(act) {
    if (busy || !token) return;
    var url = normalise();
    if (!url) { fail('Enter a valid website address, like https://example.com'); return; }
    setBusy(true);
    var p = panel(); p.appendChild(el('h3', null, 'Working…')); p.appendChild(el('p', null, url));
    if (act === 'seo') return api('/web/seo?url=' + encodeURIComponent(url)).then(showSeo).catch(function (e) { fail(e.message); });
    if (act === 'tech') return api('/web/tech?url=' + encodeURIComponent(url)).then(showTech).catch(function (e) { fail(e.message); });
    api('/web/jobs', { method: 'POST', body: JSON.stringify({ url: url, type: act }) })
      .then(function (j) { poll(j.job_id, act); })
      .catch(function (e) { fail(e.message); });
  }


  function startCaptcha(sitekey) {
    captchaOn = true;
    say('Complete the security check to continue.');
    var sc = document.createElement('script');
    sc.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
    sc.async = true;
    sc.onload = function () {
      widget = window.turnstile.render(cfBox, {
        sitekey: sitekey, theme: 'dark',
        callback: function (cf) {
          say('Verifying…');
          verify(cf).catch(function (e) { say(e.message); window.turnstile.reset(widget); });
        },
        'expired-callback': function () { needCheck('Security check expired — please complete it again.'); },
        'error-callback': function () { say('The security check could not load. Reload the page and try again.'); }
      });
    };
    sc.onerror = function () { say('The security check could not load. Check your connection and reload.'); };
    document.head.appendChild(sc);
  }
  refreshButtons();
  fetch('/web/config').then(function (r) { return r.json(); }).then(function (c) {
    if (c.captcha && c.sitekey) startCaptcha(c.sitekey);
    else verify('').catch(function (e) { say(e.message); });
  }).catch(function () { say('Could not reach the server. Reload the page.'); });

  document.getElementById('go-site').addEventListener('click', function () { run('site'); });
  document.querySelectorAll('[data-act]').forEach(function (b) {
    b.addEventListener('click', function () { run(b.getAttribute('data-act')); });
  });
  input.addEventListener('keydown', function (e) { if (e.key === 'Enter') run('site'); });
})();
