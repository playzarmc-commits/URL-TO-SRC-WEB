"""Cloudflare Turnstile captcha for the public website.

Keys come from config (TURNSTILE_SITEKEY / TURNSTILE_SECRET), or — when those
are empty — from Turso (kv key "turnstile"), or are created once through the
Cloudflare API with CF_API_TOKEN and then saved to Turso.
"""

import json
import threading
import time

import requests

import config
import db

_LOCK = threading.Lock()
_STATE = {"sitekey": "", "secret": "", "active": False, "error": "", "source": ""}


class CaptchaError(Exception):
    pass


def _cf_api(method, path, **kw):
    try:
        r = requests.request(method, config.CF_API_BASE + path, timeout=15,
                             headers={"Authorization": f"Bearer {config.CF_API_TOKEN}"}, **kw)
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        raise CaptchaError(f"Cloudflare API unreachable: {e.__class__.__name__}") from e
    if not data.get("success"):
        msgs = "; ".join(str(e.get("message", "")) for e in data.get("errors", [])) or f"HTTP {r.status_code}"
        raise CaptchaError(f"Cloudflare API: {msgs}")
    return data["result"]


def _db_load():
    rows = db.query("SELECT value FROM kv WHERE key = 'turnstile'")
    return json.loads(rows[0][0]) if rows else None


def _db_save(d):
    db.run([("INSERT INTO kv (key, value) VALUES ('turnstile', ?) "
             "ON CONFLICT(key) DO UPDATE SET value = excluded.value", [json.dumps(d)])])


def _activate(d, source):
    with _LOCK:
        _STATE.update(sitekey=d["sitekey"], secret=d["secret"], active=True, error="", source=source)


def setup() -> bool:
    """One attempt to get a working widget. Returns True when the captcha is active."""
    if not config.CAPTCHA_ENABLED:
        with _LOCK:
            _STATE.update(active=False, error="Captcha is disabled in config.py.")
        return False
    if config.TURNSTILE_SITEKEY and config.TURNSTILE_SECRET:
        _activate({"sitekey": config.TURNSTILE_SITEKEY, "secret": config.TURNSTILE_SECRET}, "config")
        return True
    try:
        saved = _db_load() if db.ENABLED else None
        if saved and sorted(saved.get("domains", [])) == sorted(config.TURNSTILE_DOMAINS):
            _activate(saved, "database")
            return True
        if not (config.CF_ACCOUNT_ID and config.CF_API_TOKEN):
            raise CaptchaError("No Cloudflare account id / API token configured.")
        result = _cf_api("POST", f"/accounts/{config.CF_ACCOUNT_ID}/challenges/widgets", json={
            "name": "URL to HTML website", "domains": list(config.TURNSTILE_DOMAINS), "mode": config.TURNSTILE_MODE})
        d = {"sitekey": result["sitekey"], "secret": result["secret"], "domains": list(config.TURNSTILE_DOMAINS)}
        try:
            _db_save(d)
        except db.DBError as e:
            print(f"⚠️ Turnstile keys could not be saved to Turso (a new widget will be created next start): {e}")
        _activate(d, "created")
        print("✅ Turnstile widget created and active")
        return True
    except (CaptchaError, db.DBError, KeyError, ValueError) as e:
        with _LOCK:
            _STATE.update(active=False, error=str(e)[:200])
        print(f"⚠️ Captcha not active: {e}")
        return False


def start():
    """First attempt synchronously; if it fails keep retrying in the background."""
    if setup() or not config.CAPTCHA_ENABLED:
        return

    def loop():
        while True:
            time.sleep(600)
            if setup():
                return

    threading.Thread(target=loop, daemon=True).start()


def active() -> bool:
    with _LOCK:
        return _STATE["active"]


def public_config() -> dict:
    with _LOCK:
        return {"captcha": _STATE["active"], "sitekey": _STATE["sitekey"] if _STATE["active"] else ""}


def status() -> dict:
    """For the admin panel — never includes the secret."""
    with _LOCK:
        return {"enabled": config.CAPTCHA_ENABLED, "active": _STATE["active"],
                "error": _STATE["error"], "source": _STATE["source"], "required": config.CAPTCHA_REQUIRED}


def verify(cf_token: str, ip: str):
    """Returns (ok, message). Fails closed if Cloudflare cannot be reached."""
    with _LOCK:
        secret = _STATE["secret"]
    try:
        r = requests.post(config.TURNSTILE_VERIFY_URL, timeout=8,
                          data={"secret": secret, "response": cf_token, "remoteip": ip})
        ok = bool(r.json().get("success"))
    except (requests.RequestException, ValueError):
        return False, "The verification service could not be reached. Please try again."
    return (True, "") if ok else (False, "Verification failed. Please try again.")
