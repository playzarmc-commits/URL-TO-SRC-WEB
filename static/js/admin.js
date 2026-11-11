(function () {
  'use strict';
  var root = document.getElementById('app');
  var csrf = '', meta = { scopes: [], plans: {} };
  var main = null, current = 'overview', navBtns = {};

  // ───────────── helpers (all text goes through textContent: no HTML injection) ─────────────
  function h(tag, attrs, kids) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v === null || v === undefined || v === false) return;
      if (k === 'class') e.className = v;
      else if (k.slice(0, 2) === 'on') e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? '' : String(v));
    });
    (kids || []).forEach(function (c) { if (c !== null && c !== undefined && c !== false) e.append(c); });
    return e;
  }
  function svg(tag, attrs) {
    var e = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, String(attrs[k])); });
    return e;
  }
  function fmtIso(iso) { return iso ? new Date(iso).toLocaleString() : '—'; }
  function fmtEpoch(s) { return s ? new Date(s * 1000).toLocaleString() : '—'; }
  function toast(msg, bad) {
    var box = document.getElementById('toast');
    var t = h('div', { class: 'tt' + (bad ? ' bad' : '') }, [msg]);
    box.append(t);
    setTimeout(function () { t.remove(); }, 3800);
  }
  function api(method, path, body) {
    var opts = { method: method, headers: {}, credentials: 'same-origin' };
    if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    if (method !== 'GET') opts.headers['X-CSRF'] = csrf;
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return { ok: false, error: 'Bad server response' }; }).then(function (j) {
        if (r.status === 401 && path !== '/admin/login') { showLogin(); throw new Error(j.error || 'Signed out'); }
        if (!r.ok || j.ok === false) throw new Error(j.error || 'Error ' + r.status);
        return j;
      });
    });
  }
  function guard(p) { return p.catch(function (e) { toast(e.message, true); throw e; }); }
  function field(label, input, cls) { return h('label', { class: 'f' + (cls ? ' ' + cls : '') }, [label, input]); }
  function chip(text, cls) { return h('span', { class: 'st ' + cls }, [text]); }
  function statusChip(s) {
    return chip(s, s === 'active' ? 'g' : s === 'limit reached' ? 'y' : s === 'disabled' ? 'n' : 'r');
  }
  function header(title, extra) {
    return h('div', { class: 'top' }, [
      h('h1', {}, [title]),
      h('div', { class: 'r' }, (extra || []).concat([
        h('button', { class: 'btn ghost sm', type: 'button', onclick: function () { go(current); } }, ['Refresh'])
      ]))
    ]);
  }
  function tableWrap(table) { return h('div', { class: 'box tw' }, [table]); }
  function th(cols) { return h('thead', {}, [h('tr', {}, cols.map(function (c) { return h('th', {}, [c]); }))]); }
  function td(content) { return h('td', {}, Array.isArray(content) ? content : [content]); }
  function num(v) {
    var e = h('div', { class: 'num' });
    if (v === null || v === undefined) e.textContent = '—';
    else { e.style.setProperty('--n', String(v)); e.setAttribute('aria-label', String(v)); }
    return e;
  }

  // ───────────── login ─────────────
  function showLogin() {
    var pw = h('input', { type: 'password', placeholder: 'Admin password', 'aria-label': 'Admin password', autocomplete: 'current-password' });
    var err = h('div', { class: 'lerr', role: 'alert' });
    var btn = h('button', { class: 'btn', type: 'submit' }, ['Sign in']);
    var form = h('form', { class: 'lcard' }, [
      h('div', { class: 'lg', style: 'margin:0 0 6px' }, [h('i'), 'Admin']),
      pw, err, btn
    ]);
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      btn.disabled = true; err.textContent = '';
      api('POST', '/admin/login', { password: pw.value }).then(function (j) {
        csrf = j.csrf; boot();
      }).catch(function (e) { err.textContent = e.message; btn.disabled = false; });
    });
    root.replaceChildren(h('div', { class: 'login' }, [form]));
    pw.focus();
  }

  // ───────────── shell ─────────────
  function shell() {
    var aside = h('aside', {}, [h('div', { class: 'lg' }, [h('i'), 'Admin'])]);
    navBtns = {};
    [['overview', 'Overview'], ['keys', 'API keys'], ['users', 'Users'], ['jobs', 'Jobs']].forEach(function (p) {
      var b = h('button', { class: 'nav', type: 'button', onclick: function () { go(p[0]); } }, [p[1]]);
      navBtns[p[0]] = b; aside.append(b);
    });
    aside.append(h('div', { class: 'grow' }));
    aside.append(h('button', { class: 'nav', type: 'button', onclick: function () {
      api('POST', '/admin/logout', {}).catch(function () {}).then(function () { csrf = ''; showLogin(); });
    } }, ['Sign out']));
    main = h('main');
    root.replaceChildren(h('div', { class: 'adm' }, [aside, main]));
  }
  function go(name) {
    current = name;
    Object.keys(navBtns).forEach(function (k) {
      if (k === name) navBtns[k].setAttribute('aria-current', 'page'); else navBtns[k].removeAttribute('aria-current');
    });
    main.replaceChildren(h('p', { class: 'mut' }, ['Loading…']));
    ({ overview: viewOverview, keys: viewKeys, users: viewUsers, jobs: viewJobs })[name]();
  }

  // ───────────── overview ─────────────
  function chart(data) {
    var W = 400, H = 170, max = Math.max.apply(null, data.map(function (d) { return d.count; }).concat([1]));
    var pts = data.map(function (d, i) { return [(i / (data.length - 1)) * W, H - 20 - (d.count / max) * (H - 50)]; });
    var line = 'M' + pts.map(function (p) { return p[0].toFixed(1) + ' ' + p[1].toFixed(1); }).join(' L');
    var s = svg('svg', { 'class': 'ch', viewBox: '0 0 ' + W + ' ' + H, role: 'img', 'aria-label': 'Requests over the last seven days' });
    var defs = svg('defs'), grad = svg('linearGradient', { id: 'g', x1: 0, y1: 0, x2: 0, y2: 1 });
    grad.append(svg('stop', { offset: 0, 'stop-color': '#7c5cff', 'stop-opacity': .45 }), svg('stop', { offset: 1, 'stop-color': '#7c5cff', 'stop-opacity': 0 }));
    defs.append(grad);
    s.append(defs, svg('path', { d: line + ' V' + H + ' H0Z', fill: 'url(#g)' }),
      svg('path', { 'class': 'dr', d: line, fill: 'none', stroke: '#22d3ee', 'stroke-width': 3, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
    var days = h('div', { class: 'days' }, data.map(function (d) {
      return h('span', {}, [new Date(d.day + 'T00:00:00Z').toLocaleDateString(undefined, { weekday: 'short', timeZone: 'UTC' }) + ' ' + d.count]);
    }));
    return h('div', {}, [s, days]);
  }
  function viewOverview() {
    guard(api('GET', '/admin/api/overview')).then(function (d) {
      var cap = d.captcha || {};
      var kids = [header('Overview', [chip(d.database ? 'Database online' : 'Database offline', d.database ? 'g' : 'r'),
        chip(cap.active ? 'Captcha on' : 'Captcha off', cap.active ? 'g' : 'y')])];
      if (cap.enabled && !cap.active) kids.push(h('div', { class: 'warn' }, ['Website captcha is not active' + (cap.required ? ' (the site is blocked until it works)' : ' (the site runs with rate limits only)') + ': ' + (cap.error || 'unknown reason') + '. The server retries every 10 minutes.']));
      if (!d.database) kids.push(h('div', { class: 'warn' }, ['The database is not reachable. Only the config keys work, and new keys cannot be saved until it is back.']));
      kids.push(h('div', { class: 'stats' }, [
        [d.users_total, 'Total users (bot)'], [d.conversions, 'Total conversions (bot)'],
        [d.requests_total, 'API requests, all time'], [d.requests_today, 'Requests today'], [d.active_keys, 'Active API keys']
      ].map(function (s) { return h('div', { class: 'box' }, [h('div', { class: 'lb' }, [s[1]]), num(s[0])]); })));
      var jobs = d.recent.length ? d.recent.map(function (j) {
        return h('div', { class: 'row' }, [h('span', {}, [j.host + ' · ' + j.kind + ' · ' + j.owner]), chip(j.status, j.status === 'done' ? 'g' : 'r')]);
      }) : [h('p', { class: 'mut' }, ['No jobs since the last restart.'])];
      kids.push(h('div', { class: 'cols' }, [
        h('div', { class: 'box' }, [h('div', { class: 'lb' }, ['Requests, last 7 days (UTC)']), chart(d.chart)]),
        h('div', { class: 'box' }, [h('div', { class: 'lb', style: 'margin-bottom:6px' }, ['Recent jobs'])].concat(jobs))
      ]));
      main.replaceChildren.apply(main, kids);
    }).catch(function () {});
  }

  // ───────────── API keys ─────────────
  function revealKey(box, plaintext, text) {
    var copy = h('button', { class: 'btn ghost sm', type: 'button', onclick: function () {
      (navigator.clipboard ? navigator.clipboard.writeText(plaintext) : Promise.reject()).then(
        function () { toast('Key copied'); }, function () { toast('Select the key and copy it manually', true); });
    } }, ['Copy']);
    box.replaceChildren(h('div', { class: 'ring' }, [h('div', { class: 'nk' }, [h('span', {}, [plaintext]), copy])]),
      h('p', { class: 'mut' }, [text + ' It is shown only once — store it now.']));
  }
  function scopeBoxes(selected) {
    var inputs = {};
    var wrap = h('div', { class: 'scopes' }, meta.scopes.map(function (s) {
      var i = h('input', { type: 'checkbox' }); i.checked = selected.indexOf(s) > -1; inputs[s] = i;
      return h('label', {}, [i, s]);
    }));
    return { el: wrap, get: function () { return meta.scopes.filter(function (s) { return inputs[s].checked; }); } };
  }
  function planSelect(value) {
    var sel = h('select', {}, Object.keys(meta.plans).map(function (p) {
      var lim = meta.plans[p];
      return h('option', { value: p }, [p.charAt(0).toUpperCase() + p.slice(1) + (lim === null ? ' (unlimited)' : ' (' + lim + '/day)')]);
    }));
    sel.value = value; return sel;
  }
  function limitValue(input) { var v = String(input.value).trim(); return v === '' ? null : Number(v); }

  function openEdit(k, reload) {
    var dlg = h('dialog');
    var label = h('input', { maxlength: 60 }); label.value = k.label;
    var plan = planSelect(k.plan);
    var limit = h('input', { type: 'number', min: 1, placeholder: 'unlimited' }); limit.value = k.daily_limit === null ? '' : k.daily_limit;
    plan.addEventListener('change', function () { var d = meta.plans[plan.value]; limit.value = d === null ? '' : d; });
    var exp = h('input', { type: 'date' }); exp.value = k.expires_at ? k.expires_at.slice(0, 10) : '';
    var note = h('input', { maxlength: 200 }); note.value = k.note || '';
    var sc = scopeBoxes(k.scopes);
    var save = h('button', { class: 'btn', type: 'button', onclick: function () {
      save.disabled = true;
      guard(api('PATCH', '/admin/api/keys/' + k.id, {
        label: label.value, plan: plan.value, daily_limit: limitValue(limit), scopes: sc.get(), note: note.value, expires_at: exp.value
      })).then(function () { toast('Key updated'); dlg.close(); dlg.remove(); reload(); }).catch(function () { save.disabled = false; });
    } }, ['Save changes']);
    var cancel = h('button', { class: 'btn ghost', type: 'button', onclick: function () { dlg.close(); dlg.remove(); } }, ['Cancel']);
    dlg.append(h('h2', {}, ['Edit key']), h('div', { class: 'gen' }, [
      field('Label', label), field('Plan', plan), field('Daily limit (empty = unlimited)', limit),
      field('Expires on (empty = never)', exp), field('Note', note, ''), sc.el, h('div', { class: 'dfoot' }, [cancel, save])
    ]));
    document.body.append(dlg); dlg.showModal();
  }

  function keyRow(k, box, reload) {
    var pct = k.daily_limit ? Math.min(100, k.used_today / k.daily_limit * 100) : 0;
    var usage = [h('span', {}, [k.used_today + ' / ' + (k.daily_limit === null ? '∞' : k.daily_limit)])];
    if (k.daily_limit) usage.push(h('div', { class: 'us' }, [h('i', { style: 'width:' + pct.toFixed(0) + '%' })]));
    function act(text, fn, cls) { return h('button', { class: 'btn ghost sm ' + (cls || ''), type: 'button', onclick: fn }, [text]); }
    function run(p, msg) { guard(p).then(function (j) { toast(msg); reload(); return j; }).catch(function () {}); }
    var actions = [
      act('Edit', function () { openEdit(k, reload); }),
      act(k.active ? 'Disable' : 'Enable', function () { run(api('PATCH', '/admin/api/keys/' + k.id, { active: !k.active }), k.active ? 'Key disabled' : 'Key enabled'); }),
      act('Reset today', function () { run(api('POST', '/admin/api/keys/' + k.id + '/reset', {}), 'Today’s usage reset'); })
    ];
    if (!k.system) {
      actions.push(act('Rotate', function () {
        if (!confirm('Rotate "' + k.label + '"? The old key stops working immediately.')) return;
        guard(api('POST', '/admin/api/keys/' + k.id + '/rotate', {})).then(function (j) { revealKey(box, j.plaintext, 'New key for “' + k.label + '”.'); reload(); }).catch(function () {});
      }));
      actions.push(act('Delete', function () {
        if (!confirm('Delete "' + k.label + '" permanently?')) return;
        run(api('DELETE', '/admin/api/keys/' + k.id), 'Key deleted');
      }, 'dng'));
    }
    return h('tr', {}, [
      td([k.label, k.system ? ' ' : null, k.system ? chip('system', 'v') : null, k.note ? h('small', {}, [k.note]) : null]),
      td(h('code', {}, [k.hint])),
      td(chip(k.plan, k.plan === 'paid' ? 'v' : 'n')),
      td(h('div', { class: 'sc' }, k.scopes.map(function (s) { return h('span', {}, [s]); }))),
      td(usage), td(String(k.total_uses)), td(fmtIso(k.last_used)),
      td(k.expires_at ? fmtIso(k.expires_at) : 'never'), td(statusChip(k.status)),
      td(h('div', { class: 'acts' }, actions))
    ]);
  }

  function viewKeys() {
    var box = h('div'), tableBox = h('div', { style: 'margin-top:16px' });
    var firstPlan = Object.keys(meta.plans)[0];
    var label = h('input', { maxlength: 60, placeholder: 'Partner [NAME]' });
    var plan = planSelect(firstPlan);
    var limit = h('input', { type: 'number', min: 1, placeholder: 'unlimited' });
    limit.value = meta.plans[firstPlan] === null ? '' : meta.plans[firstPlan];
    plan.addEventListener('change', function () { var d = meta.plans[plan.value]; limit.value = d === null ? '' : d; });
    var days = h('input', { type: 'number', min: 1, placeholder: 'never' });
    var custom = h('input', { placeholder: 'leave empty to auto-generate', autocomplete: 'off', spellcheck: 'false' });
    var note = h('input', { maxlength: 200, placeholder: 'optional' });
    var sc = scopeBoxes(meta.scopes);
    function reload() {
      guard(api('GET', '/admin/api/keys')).then(function (j) {
        var tbody = h('tbody', {}, j.keys.map(function (k) { return keyRow(k, box, reload); }));
        tableBox.replaceChildren(tableWrap(h('table', {}, [th(['Label', 'Key', 'Plan', 'Scopes', 'Used today', 'Total', 'Last used', 'Expires', 'Status', 'Actions']), tbody])));
      }).catch(function () {});
    }
    var gen = h('button', { class: 'btn', type: 'button', onclick: function () {
      gen.disabled = true;
      guard(api('POST', '/admin/api/keys', {
        label: label.value, plan: plan.value, daily_limit: limitValue(limit), expires_days: String(days.value).trim() === '' ? null : Number(days.value),
        scopes: sc.get(), custom_key: custom.value.trim(), note: note.value
      })).then(function (j) {
        revealKey(box, j.plaintext, 'Key “' + j.key.label + '” created.');
        label.value = ''; custom.value = ''; note.value = ''; days.value = '';
        reload();
      }).catch(function () {}).then(function () { gen.disabled = false; });
    } }, ['Generate key']);
    main.replaceChildren(
      header('API keys'),
      h('div', { class: 'box' }, [h('h2', {}, ['Create a key']), h('div', { class: 'gen' }, [
        field('Label', label), field('Plan', plan), field('Daily limit (empty = unlimited)', limit), field('Expires in days', days),
        field('Custom key value (16–64 chars)', custom), field('Note', note), sc.el, gen
      ]), box]),
      tableBox
    );
    reload();
  }

  // ───────────── users ─────────────
  function viewUsers(query, page) {
    query = query || ''; page = page || 0;
    var q = h('input', { type: 'search', placeholder: 'Search name, @username or ID', 'aria-label': 'Search users' }); q.value = query;
    var body = h('div');
    function search() { viewUsers(q.value.trim(), 0); }
    q.addEventListener('keydown', function (e) { if (e.key === 'Enter') search(); });
    guard(api('GET', '/admin/api/users?page=' + page + '&q=' + encodeURIComponent(query))).then(function (j) {
      var rows = j.users.map(function (u) {
        return h('tr', {}, [td(String(u.id)), td(u.name || '—'), td(u.username ? '@' + u.username : '—'), td(u.joined || '—'), td(u.last_seen || '—')]);
      });
      var pages = Math.max(1, Math.ceil(j.total / j.size));
      body.append(tableWrap(h('table', { style: 'min-width:600px' }, [th(['ID', 'Name', 'Username', 'Joined', 'Last seen']), h('tbody', {}, rows)])),
        h('div', { class: 'pager' }, [
          h('span', { class: 'mut' }, [j.total + ' users · page ' + (j.page + 1) + ' / ' + pages]),
          h('button', { class: 'btn ghost sm', type: 'button', disabled: j.page === 0, onclick: function () { viewUsers(query, page - 1); } }, ['Previous']),
          h('button', { class: 'btn ghost sm', type: 'button', disabled: j.page + 1 >= pages, onclick: function () { viewUsers(query, page + 1); } }, ['Next'])
        ]));
    }).catch(function () {});
    main.replaceChildren(header('Users'), h('div', { class: 'search' }, [q, h('button', { class: 'btn sm', type: 'button', onclick: search }, ['Search'])]), body);
  }

  // ───────────── jobs ─────────────
  function viewJobs() {
    guard(api('GET', '/admin/api/jobs')).then(function (j) {
      var s = j.stats;
      var rows = j.jobs.map(function (x) {
        return h('tr', {}, [td(fmtEpoch(x.time)), td(x.kind), td(x.host), td(x.owner), td(chip(x.status, x.status === 'done' ? 'g' : 'r')),
          td(x.seconds + ' s'), td(x.pages === null || x.pages === undefined ? '—' : String(x.pages)), td(x.error || '')]);
      });
      main.replaceChildren(
        header('Jobs', [chip(s.running + ' running', 'v'), chip(s.queued + ' queued', 'n')]),
        j.jobs.length ? tableWrap(h('table', {}, [th(['Finished', 'Type', 'Site', 'Owner', 'Status', 'Took', 'Pages', 'Error']), h('tbody', {}, rows)]))
          : h('p', { class: 'mut' }, ['No finished jobs since the last restart.'])
      );
    }).catch(function () {});
  }

  // ───────────── boot ─────────────
  function boot() {
    api('GET', '/admin/api/me').then(function (j) {
      csrf = j.csrf; meta = { scopes: j.scopes, plans: j.plans };
      shell(); go('overview');
    }).catch(function () { /* 401 already shows the login screen */ });
  }
  boot();
})();
