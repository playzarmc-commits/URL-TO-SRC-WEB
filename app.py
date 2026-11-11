"""URL ➜ HTML — website, public API (custom keys) and admin panel in one Flask app.

  /                          website
  /admin                     admin panel (password login)
  /api/*                     key-protected API   (header X-API-Key)
  /web/*                     website back end    (Turnstile captcha → signed token, per-IP limits)
  /admin/api/*               admin JSON API      (signed cookie + CSRF header)
"""

import hashlib
import json
import os
import time
from functools import wraps

import requests
from flask import Flask, Response, g, jsonify, request, send_file, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

import config
import captcha
import core
import db
import jobs
import keys
import ratelimit
import security

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")
app = Flask(__name__, static_folder=STATIC, static_url_path="/static")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)   # real client IP behind Render's proxy
START_TIME = time.time()
SESSION_COOKIE = "uth_admin"

keys.load()
captcha.start()


def _error(message, status):
    return jsonify({"ok": False, "error": message}), status


@app.after_request
def _headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    rl = getattr(g, "rl", None)
    if rl and rl.get("limit") is not None:
        resp.headers["X-RateLimit-Limit"] = str(rl["limit"])
        resp.headers["X-RateLimit-Remaining"] = str(rl.get("remaining", 0))
    if request.path.startswith("/admin") or request.path.startswith("/web") or request.path.startswith("/api"):
        resp.headers["Cache-Control"] = "no-store"
    if request.path.startswith("/admin"):
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; img-src 'self' data:; base-uri 'none'; form-action 'self'"
        )
    return resp


def _fetch_errors(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except core.UnsafeURL as e:
            return _error(str(e), 400)
        except core.ResponseTooLarge as e:
            return _error(str(e), 413)
        except requests.exceptions.Timeout:
            return _error("The target site timed out.", 504)
        except requests.exceptions.RequestException as e:
            return _error(f"Could not fetch the URL: {str(e)[:200]}", 502)
    return wrapper


def _url_arg(source):
    url = (source.get("url") or "").strip()
    return url if core.is_valid_url(url) else None


# ═══════════════════════ pages ═══════════════════════
@app.get("/")
def page_index():
    return send_from_directory(STATIC, "index.html")


@app.get("/admin")
@app.get("/admin/")
def page_admin():
    return send_from_directory(STATIC, "admin.html")


@app.get("/health")
def health():
    return jsonify({"status": "ok", "uptime_seconds": int(time.time() - START_TIME),
                    "jobs": jobs.stats(), "database": keys.DB_OK,
                    "captcha": captcha.active()})


# ═══════════════════════ public API (custom keys) ═══════════════════════
def _authenticate():
    supplied = request.headers.get("X-API-Key") or request.args.get("key") or ""
    return keys.authenticate(supplied)


def _spend(rec, scope):
    """Counts one use against the key. Returns an error response, or None if allowed."""
    ok, status, message, info = keys.authorize(rec, scope)
    g.rl = info
    return None if ok else _error(message, status)


def _api_simple(scope, fn):
    rec = _authenticate()
    if not rec:
        return _error("Invalid or missing API key.", 401)
    url = _url_arg(request.args)
    if not url:
        return _error("Send a valid http(s) URL in ?url=", 400)
    denied = _spend(rec, scope)
    if denied:
        return denied
    return fn(url)


@app.get("/api/me")
def api_me():
    rec = _authenticate()
    if not rec:
        return _error("Invalid or missing API key.", 401)
    v = keys.view(rec)
    return jsonify({"ok": True, "label": v["label"], "plan": v["plan"], "scopes": v["scopes"],
                    "daily_limit": v["daily_limit"], "used_today": v["used_today"],
                    "expires_at": v["expires_at"], "status": v["status"]})


@app.get("/api/html")
@_fetch_errors
def api_html():
    def run(url):
        data = core.fetch_html(url)
        if request.args.get("raw") == "1":
            return Response(data["html"], mimetype="text/html", headers={"X-Upstream-Status": str(data["status"])})
        return jsonify({"ok": True, **data})
    return _api_simple("html", run)


@app.get("/api/seo")
@_fetch_errors
def api_seo():
    return _api_simple("seo", lambda url: jsonify({"ok": True, **core.seo_report(url)}))


@app.get("/api/tech")
@_fetch_errors
def api_tech():
    return _api_simple("tech", lambda url: jsonify({"ok": True, **core.tech_report(url)}))


def _create_job(owner_id, owner_label, body):
    url = _url_arg(body)
    if not url:
        return None, _error('Send JSON like {"url": "https://example.com", "type": "site"}', 400)
    kind = (body.get("type") or "site").lower()
    if kind not in ("site", "zip"):
        return None, _error('type must be "site" or "zip".', 400)
    try:
        core._validated_public_url(url)
    except core.UnsafeURL as e:
        return None, _error(str(e), 400)
    return (kind, url), None


@app.post("/api/jobs")
def api_job_create():
    rec = _authenticate()
    if not rec:
        return _error("Invalid or missing API key.", 401)
    parsed, err = _create_job(rec["id"], rec["label"], request.get_json(silent=True) or {})
    if err:
        return err
    kind, url = parsed
    denied = _spend(rec, kind)
    if denied:
        return denied
    try:
        view = jobs.submit(rec["id"], rec["label"], kind, url)
    except jobs.QueueFull:
        return _error("Server queue is full — try again in a minute.", 429)
    return jsonify({"ok": True, **view}), 202


@app.get("/api/jobs/<job_id>")
def api_job_status(job_id):
    rec = _authenticate()
    if not rec:
        return _error("Invalid or missing API key.", 401)
    view = jobs.get(job_id, rec["id"])
    return jsonify({"ok": True, **view}) if view else _error("Job not found or expired.", 404)


@app.get("/api/jobs/<job_id>/download")
def api_job_download(job_id):
    rec = _authenticate()
    if not rec:
        return _error("Invalid or missing API key.", 401)
    found = jobs.file_for(job_id, rec["id"])
    if not found:
        return _error("Result not ready, failed, or expired.", 404)
    path, filename = found
    return send_file(path, mimetype="application/zip", as_attachment=True, download_name=filename)


# ═══════════════════════ website back end ═══════════════════════
def _web(kind, spend=True):
    """Token + per-IP limit (+ global daily cap). Returns (owner_id, error_response)."""
    ip = request.remote_addr or "0.0.0.0"
    if not security.verify_web_token(request.headers.get("X-Web-Token", ""), ip):
        return None, _error("Session expired — reload the page.", 401)
    limit, window = config.WEB_LIMITS[kind]
    if not ratelimit.hit(f"web:{kind}:{ip}", limit, window):
        return None, _error("Too many requests from your network. Please try again later.", 429)
    if spend and not keys.consume_web():
        return None, _error("Today's free capacity is used up. Please try again tomorrow.", 429)
    return "web:" + hashlib.sha256(ip.encode()).hexdigest()[:12], None


@app.get("/web/config")
def web_config():
    return jsonify({"ok": True, **captcha.public_config()})


@app.post("/web/verify")
def web_verify():
    """Exchanges a solved Turnstile token for a short-lived signed website token."""
    ip = request.remote_addr or "0.0.0.0"
    limit, window = config.WEB_LIMITS["token"]
    if not ratelimit.hit(f"web:token:{ip}", limit, window):
        return _error("Too many attempts. Please try again later.", 429)
    if captcha.active():
        cf = (request.get_json(silent=True) or {}).get("cf", "")
        if not isinstance(cf, str) or not cf or len(cf) > 2048:
            return _error("Please complete the security check first.", 400)
        ok, message = captcha.verify(cf, ip)
        if not ok:
            return _error(message, 403)
    elif config.CAPTCHA_ENABLED and config.CAPTCHA_REQUIRED:
        return _error("Verification is temporarily unavailable. Please try again later.", 503)
    return jsonify({"ok": True, "token": security.make_web_token(ip), "ttl": config.WEB_TOKEN_TTL})


@app.get("/web/seo")
@_fetch_errors
def web_seo():
    owner, err = _web("light")
    if err:
        return err
    url = _url_arg(request.args)
    return jsonify({"ok": True, **core.seo_report(url)}) if url else _error("Enter a valid http(s) URL.", 400)


@app.get("/web/tech")
@_fetch_errors
def web_tech():
    owner, err = _web("light")
    if err:
        return err
    url = _url_arg(request.args)
    return jsonify({"ok": True, **core.tech_report(url)}) if url else _error("Enter a valid http(s) URL.", 400)


@app.post("/web/jobs")
def web_job_create():
    owner, err = _web("job")
    if err:
        return err
    parsed, err = _create_job(owner, "website", request.get_json(silent=True) or {})
    if err:
        return err
    kind, url = parsed
    try:
        view = jobs.submit(owner, "website", kind, url)
    except jobs.QueueFull:
        return _error("The server is busy — try again in a minute.", 429)
    return jsonify({"ok": True, **view}), 202


@app.get("/web/jobs/<job_id>")
def web_job_status(job_id):
    owner, err = _web("poll", spend=False)
    if err:
        return err
    view = jobs.get(job_id, owner)
    return jsonify({"ok": True, **view}) if view else _error("Job not found or expired.", 404)


@app.get("/web/jobs/<job_id>/download")
def web_job_download(job_id):
    owner, err = _web("poll", spend=False)
    if err:
        return err
    found = jobs.file_for(job_id, owner)
    if not found:
        return _error("Result not ready or expired.", 404)
    path, filename = found
    return send_file(path, mimetype="application/zip", as_attachment=True, download_name=filename)


# ═══════════════════════ admin ═══════════════════════
def admin_api(mutating=False):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            sid = security.session_sid(request.cookies.get(SESSION_COOKIE))
            if not sid:
                return _error("Not signed in.", 401)
            if mutating and not security.csrf_ok(sid, request.headers.get("X-CSRF", "")):
                return _error("Security check failed — reload the page.", 403)
            request.admin_sid = sid
            try:
                return fn(*args, **kwargs)
            except keys.KeyError_ as e:
                return _error(str(e), 400)
            except db.DBError as e:
                return _error(str(e), 503)
        return wrapper
    return deco


@app.post("/admin/login")
def admin_login():
    ip = request.remote_addr or "0.0.0.0"
    if not ratelimit.hit(f"login:{ip}", config.ADMIN_LOGIN_ATTEMPTS, 900):
        return _error("Too many attempts. Try again in 15 minutes.", 429)
    password = (request.get_json(silent=True) or {}).get("password", "")
    if not security.check_password(password):
        time.sleep(0.6)
        return _error("Wrong password.", 401)
    ratelimit.reset(f"login:{ip}")
    cookie = security.make_session()
    resp = jsonify({"ok": True, "csrf": security.csrf_for(security.session_sid(cookie))})
    resp.set_cookie(SESSION_COOKIE, cookie, max_age=config.ADMIN_SESSION_HOURS * 3600,
                    httponly=True, samesite="Strict", secure=request.is_secure, path="/admin")
    return resp


@app.post("/admin/logout")
def admin_logout():
    resp = jsonify({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/admin")
    return resp


@app.get("/admin/api/me")
@admin_api()
def admin_me():
    return jsonify({"ok": True, "csrf": security.csrf_for(request.admin_sid),
                    "scopes": list(keys.SCOPES), "plans": config.PLAN_DEFAULT_LIMITS})


@app.get("/admin/api/overview")
@admin_api()
def admin_overview():
    users_total, conversions, db_ok = None, None, True
    try:
        users_total = db.query("SELECT COUNT(*) FROM users")[0][0]
        rows = db.query("SELECT value FROM kv WHERE key = 'stats'")
        conversions = int(json.loads(rows[0][0]).get("total_conversions", 0)) if rows else 0
    except (db.DBError, ValueError, TypeError):
        db_ok = False
    return jsonify({
        "ok": True, "database": keys.DB_OK and db_ok,
        "users_total": users_total, "conversions": conversions,
        "requests_total": keys.usage_total(), "requests_today": keys.usage_today_total(),
        "active_keys": keys.active_count(),
        "chart": [{"day": d, "count": c} for d, c in keys.usage_by_day(7)],
        "recent": jobs.recent(8), "web_cap": config.WEB_DAILY_CAP, "captcha": captcha.status(),
    })


@app.get("/admin/api/keys")
@admin_api()
def admin_keys():
    return jsonify({"ok": True, "keys": keys.list_keys()})


@app.post("/admin/api/keys")
@admin_api(mutating=True)
def admin_key_create():
    view, plaintext = keys.create(request.get_json(silent=True) or {})
    return jsonify({"ok": True, "key": view, "plaintext": plaintext}), 201


@app.patch("/admin/api/keys/<key_id>")
@admin_api(mutating=True)
def admin_key_update(key_id):
    view = keys.update(key_id, request.get_json(silent=True) or {})
    return jsonify({"ok": True, "key": view}) if view else _error("Key not found.", 404)


@app.post("/admin/api/keys/<key_id>/rotate")
@admin_api(mutating=True)
def admin_key_rotate(key_id):
    result = keys.rotate(key_id)
    if not result:
        return _error("Key not found.", 404)
    return jsonify({"ok": True, "key": result[0], "plaintext": result[1]})


@app.post("/admin/api/keys/<key_id>/reset")
@admin_api(mutating=True)
def admin_key_reset(key_id):
    view = keys.reset_today(key_id)
    return jsonify({"ok": True, "key": view}) if view else _error("Key not found.", 404)


@app.delete("/admin/api/keys/<key_id>")
@admin_api(mutating=True)
def admin_key_delete(key_id):
    return jsonify({"ok": True}) if keys.delete(key_id) else _error("Key not found.", 404)


@app.get("/admin/api/users")
@admin_api()
def admin_users():
    try:
        page = max(0, int(request.args.get("page", 0)))
    except ValueError:
        return _error("Bad page.", 400)
    size, q = 25, (request.args.get("q") or "").strip()[:50]
    where, args = "", []
    if q:
        where = " WHERE username LIKE ? OR first_name LIKE ? OR CAST(id AS TEXT) LIKE ?"
        args = [f"%{q}%"] * 3
    total = db.query("SELECT COUNT(*) FROM users" + where, args)[0][0]
    rows = db.query("SELECT id, username, first_name, joined, last_seen FROM users" + where +
                    " ORDER BY last_seen DESC LIMIT ? OFFSET ?", args + [size, page * size])
    return jsonify({"ok": True, "total": total, "page": page, "size": size, "users": [
        {"id": r[0], "username": r[1], "name": r[2], "joined": r[3], "last_seen": r[4]} for r in rows]})


@app.get("/admin/api/jobs")
@admin_api()
def admin_jobs():
    return jsonify({"ok": True, "jobs": jobs.recent(60), "stats": jobs.stats()})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.PORT, threaded=True)
