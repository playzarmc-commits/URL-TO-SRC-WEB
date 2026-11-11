"""Custom API-key system.

Keys live in Turso (only a SHA-256 hash is stored — the full key is shown once,
at creation). The process keeps them in memory for fast checks; usage counters
are batched to the database every few seconds. Because the in-memory table is
authoritative, run the service with ONE gunicorn worker.
"""

import hashlib
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import config
import db

SCOPES = ("html", "seo", "tech", "site", "zip")
PLANS = tuple(config.PLAN_DEFAULT_LIMITS)
CUSTOM_KEY_RE = re.compile(r"^[A-Za-z0-9_\-]{16,64}$")
_COLS = ("id, label, key_hash, key_prefix, last4, plan, daily_limit, scopes, expires_at, "
         "active, system, note, created_at, last_used, total_uses")

_LOCK = threading.RLock()
_KEYS = {}        # id -> record
_BY_HASH = {}     # sha256 hex -> id
_USAGE = {}       # (key_id, "YYYY-MM-DD") -> count
_DIRTY_USAGE = set()
_DIRTY_KEYS = set()
DB_OK = False


class KeyError_(ValueError):
    """Invalid admin input (shown to the admin as a 400)."""


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def today():
    return _now().strftime("%Y-%m-%d")


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _hint(key):
    return key[:7], key[-4:]


def _from_row(r):
    return {
        "id": r[0], "label": r[1], "key_hash": r[2], "key_prefix": r[3], "last4": r[4],
        "plan": r[5], "daily_limit": r[6], "scopes": [s for s in (r[7] or "").split(",") if s],
        "expires_at": r[8], "active": bool(r[9]), "system": bool(r[10]), "note": r[11] or "",
        "created_at": r[12], "last_used": r[13], "total_uses": r[14] or 0,
    }


def _insert_sql():
    return (f"INSERT INTO api_keys ({_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")


def _insert_args(r):
    return [r["id"], r["label"], r["key_hash"], r["key_prefix"], r["last4"], r["plan"], r["daily_limit"],
            ",".join(r["scopes"]), r["expires_at"], int(r["active"]), int(r["system"]), r["note"],
            r["created_at"], r["last_used"], r["total_uses"]]


def _register(rec):
    _KEYS[rec["id"]] = rec
    _BY_HASH[rec["key_hash"]] = rec["id"]


# ───────────────────────── startup ─────────────────────────
def load():
    global DB_OK
    with _LOCK:
        _KEYS.clear(); _BY_HASH.clear(); _USAGE.clear()
        try:
            db.init_schema()
            for row in db.query(f"SELECT {_COLS} FROM api_keys"):
                _register(_from_row(row))
            for key_id, cnt in db.query("SELECT key_id, count FROM usage WHERE day = ?", [today()]):
                _USAGE[(key_id, today())] = cnt
            DB_OK = True
        except db.DBError as e:
            DB_OK = False
            print(f"❌ Turso unavailable — only config keys work until it is back: {e}")
        _seed_system_keys()


def _seed_system_keys():
    for i, key in enumerate(config.API_KEYS, 1):
        h = hash_key(key)
        if h in _BY_HASH:
            continue
        prefix, last4 = _hint(key)
        rec = {"id": "sys-" + h[:8], "label": f"System key {i} (config)", "key_hash": h,
               "key_prefix": prefix, "last4": last4, "plan": "paid", "daily_limit": None,
               "scopes": list(SCOPES), "expires_at": None, "active": True, "system": True,
               "note": "Defined in config.py", "created_at": _iso(_now()), "last_used": None, "total_uses": 0}
        if DB_OK:
            try:
                db.run([("INSERT OR IGNORE INTO api_keys (" + _COLS + ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         _insert_args(rec))])
            except db.DBError as e:
                print(f"⚠️ Could not store system key: {e}")
        _register(rec)


# ───────────────────────── validation ─────────────────────────
def _clean(data: dict, base: dict) -> dict:
    """Validate admin input against the current record; returns the changed fields."""
    out = {}
    if "label" in data:
        label = str(data["label"] or "").strip()
        if not 1 <= len(label) <= 60:
            raise KeyError_("Label must be 1–60 characters.")
        out["label"] = label
    if "note" in data:
        out["note"] = str(data["note"] or "").strip()[:200]
    if "plan" in data:
        if data["plan"] not in PLANS:
            raise KeyError_(f"Plan must be one of: {', '.join(PLANS)}.")
        out["plan"] = data["plan"]
        if "daily_limit" not in data:
            out["daily_limit"] = config.PLAN_DEFAULT_LIMITS[data["plan"]]
    if "daily_limit" in data:
        v = data["daily_limit"]
        if v in (None, ""):
            out["daily_limit"] = None
        else:
            try:
                n = int(v)
            except (TypeError, ValueError):
                raise KeyError_("Daily limit must be a whole number (or empty for unlimited).")
            if not 1 <= n <= 10_000_000:
                raise KeyError_("Daily limit must be between 1 and 10,000,000.")
            out["daily_limit"] = n
    if "scopes" in data:
        sc = data["scopes"]
        if not isinstance(sc, list) or not sc or any(s not in SCOPES for s in sc):
            raise KeyError_(f"Pick at least one scope from: {', '.join(SCOPES)}.")
        out["scopes"] = [s for s in SCOPES if s in sc]
    if "expires_days" in data:  # relative, from now
        v = data["expires_days"]
        if v in (None, ""):
            out["expires_at"] = None
        else:
            try:
                d = int(v)
            except (TypeError, ValueError):
                raise KeyError_("Expiry must be a whole number of days.")
            if not 1 <= d <= 3650:
                raise KeyError_("Expiry must be between 1 and 3650 days.")
            out["expires_at"] = _iso(_now() + timedelta(days=d))
    if "expires_at" in data and "expires_days" not in data:  # absolute date (YYYY-MM-DD) or empty
        v = data["expires_at"]
        if v in (None, ""):
            out["expires_at"] = None
        else:
            try:
                dt = datetime.strptime(str(v)[:10], "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
            except ValueError:
                raise KeyError_("Expiry date must look like 2026-12-31.")
            out["expires_at"] = _iso(dt)
    if "active" in data:
        out["active"] = bool(data["active"])
    return out


def _view(rec):
    day = today()
    used = _USAGE.get((rec["id"], day), 0)
    return {
        "id": rec["id"], "label": rec["label"], "hint": f"{rec['key_prefix']}…{rec['last4']}",
        "plan": rec["plan"], "daily_limit": rec["daily_limit"], "scopes": rec["scopes"],
        "expires_at": rec["expires_at"], "active": rec["active"], "system": rec["system"],
        "note": rec["note"], "created_at": rec["created_at"], "last_used": rec["last_used"],
        "total_uses": rec["total_uses"], "used_today": used, "status": _status(rec, used),
    }


def _status(rec, used=None):
    if not rec["active"]:
        return "disabled"
    if rec["expires_at"] and rec["expires_at"] < _iso(_now()):
        return "expired"
    if used is None:
        used = _USAGE.get((rec["id"], today()), 0)
    if rec["daily_limit"] is not None and used >= rec["daily_limit"]:
        return "limit reached"
    return "active"


# ───────────────────────── admin operations ─────────────────────────
def create(data: dict):
    """Returns (view, plaintext_key). Raises KeyError_ (bad input) or db.DBError."""
    body = dict(data)
    body.setdefault("label", "")
    body.setdefault("plan", "free")
    body.setdefault("scopes", list(SCOPES))
    fields = _clean(body, {})
    custom = str(data.get("custom_key") or "").strip()
    if custom:
        if not CUSTOM_KEY_RE.match(custom):
            raise KeyError_("Custom key: 16–64 characters, letters, digits, _ or - only.")
        plaintext = custom
    else:
        plaintext = config.KEY_PREFIX + secrets.token_urlsafe(24)
    h = hash_key(plaintext)
    with _LOCK:
        if h in _BY_HASH:
            raise KeyError_("That key already exists.")
        prefix, last4 = _hint(plaintext)
        rec = {"id": uuid.uuid4().hex[:12], "key_hash": h, "key_prefix": prefix, "last4": last4,
               "system": False, "created_at": _iso(_now()), "last_used": None, "total_uses": 0,
               "active": True, "note": "", "expires_at": None,
               "daily_limit": config.PLAN_DEFAULT_LIMITS[body["plan"]], **fields}
        db.run([(_insert_sql(), _insert_args(rec))])   # DB first: a failure leaves nothing half-created
        _register(rec)
        return _view(rec), plaintext


def _persist(rec):
    db.run([("UPDATE api_keys SET label=?, plan=?, daily_limit=?, scopes=?, expires_at=?, active=?, note=?, "
             "key_hash=?, key_prefix=?, last4=? WHERE id=?",
             [rec["label"], rec["plan"], rec["daily_limit"], ",".join(rec["scopes"]), rec["expires_at"],
              int(rec["active"]), rec["note"], rec["key_hash"], rec["key_prefix"], rec["last4"], rec["id"]])])


def update(key_id: str, data: dict):
    with _LOCK:
        rec = _KEYS.get(key_id)
        if not rec:
            return None
        changes = _clean(data, rec)
        new = {**rec, **changes}
        _persist(new)
        rec.update(changes)
        return _view(rec)


def rotate(key_id: str):
    """New secret, same settings. Returns (view, plaintext) or None. Not for system keys."""
    with _LOCK:
        rec = _KEYS.get(key_id)
        if not rec:
            return None
        if rec["system"]:
            raise KeyError_("System keys live in config.py — change them there.")
        plaintext = config.KEY_PREFIX + secrets.token_urlsafe(24)
        new = {**rec, "key_hash": hash_key(plaintext)}
        new["key_prefix"], new["last4"] = _hint(plaintext)
        _persist(new)
        _BY_HASH.pop(rec["key_hash"], None)
        rec.update(key_hash=new["key_hash"], key_prefix=new["key_prefix"], last4=new["last4"])
        _BY_HASH[rec["key_hash"]] = rec["id"]
        return _view(rec), plaintext


def reset_today(key_id: str):
    with _LOCK:
        rec = _KEYS.get(key_id)
        if not rec:
            return None
        db.run([("DELETE FROM usage WHERE key_id = ? AND day = ?", [key_id, today()])])
        _USAGE.pop((key_id, today()), None)
        _DIRTY_USAGE.discard((key_id, today()))
        return _view(rec)


def delete(key_id: str):
    with _LOCK:
        rec = _KEYS.get(key_id)
        if not rec:
            return False
        if rec["system"]:
            raise KeyError_("System keys live in config.py — disable it instead of deleting.")
        db.run([("DELETE FROM api_keys WHERE id = ?", [key_id]), ("DELETE FROM usage WHERE key_id = ?", [key_id])])
        _BY_HASH.pop(rec["key_hash"], None)
        _KEYS.pop(key_id, None)
        for k in [k for k in _USAGE if k[0] == key_id]:
            _USAGE.pop(k, None)
        return True


def list_keys():
    with _LOCK:
        return sorted((_view(r) for r in _KEYS.values()),
                      key=lambda v: (not v["system"], v["created_at"] or ""), reverse=False)


# ───────────────────────── request-time checks ─────────────────────────
def authenticate(plaintext):
    if not plaintext or len(plaintext) > 200:
        return None
    with _LOCK:
        kid = _BY_HASH.get(hash_key(plaintext))
        return _KEYS.get(kid) if kid else None


def authorize(rec, scope):
    """Checks active / expiry / scope / daily quota and counts one use.
    Returns (ok, http_status, message, info) — info has limit/remaining/reset."""
    day = today()
    with _LOCK:
        if not rec["active"]:
            return False, 403, "This API key is disabled.", {}
        if rec["expires_at"] and rec["expires_at"] < _iso(_now()):
            return False, 403, "This API key has expired.", {}
        if scope not in rec["scopes"]:
            return False, 403, f"This key is not allowed to use '{scope}'.", {}
        used = _USAGE.get((rec["id"], day), 0)
        lim = rec["daily_limit"]
        if lim is not None and used >= lim:
            return False, 429, "Daily limit reached for this key. It resets at 00:00 UTC.", {"limit": lim, "remaining": 0}
        _USAGE[(rec["id"], day)] = used + 1
        rec["total_uses"] += 1
        rec["last_used"] = _iso(_now())
        _DIRTY_USAGE.add((rec["id"], day))
        _DIRTY_KEYS.add(rec["id"])
        return True, 200, "", {"limit": lim, "remaining": None if lim is None else lim - used - 1}


def consume_web() -> bool:
    """Counts one anonymous website request against the global daily cap."""
    day = today()
    with _LOCK:
        used = _USAGE.get(("web", day), 0)
        if used >= config.WEB_DAILY_CAP:
            return False
        _USAGE[("web", day)] = used + 1
        _DIRTY_USAGE.add(("web", day))
        return True


# ───────────────────────── stats + persistence ─────────────────────────
def usage_today_total():
    day = today()
    with _LOCK:
        return sum(c for (k, d), c in _USAGE.items() if d == day)


def active_count():
    with _LOCK:
        return sum(1 for r in _KEYS.values() if _status(r) in ("active", "limit reached"))


def flush():
    with _LOCK:
        usage = [(k, d, _USAGE.get((k, d), 0)) for (k, d) in _DIRTY_USAGE]
        keys = [(i, _KEYS[i]["total_uses"], _KEYS[i]["last_used"]) for i in _DIRTY_KEYS if i in _KEYS]
        _DIRTY_USAGE.clear(); _DIRTY_KEYS.clear()
    if not (usage or keys) or not db.ENABLED:
        return
    stmts = [("INSERT INTO usage (key_id, day, count) VALUES (?,?,?) "
              "ON CONFLICT(key_id, day) DO UPDATE SET count = excluded.count", [k, d, c]) for k, d, c in usage]
    stmts += [("UPDATE api_keys SET total_uses = ?, last_used = ? WHERE id = ?", [t, lu, i]) for i, t, lu in keys]
    try:
        db.run(stmts)
    except db.DBError as e:
        print(f"⚠️ Usage flush failed, will retry: {e}")
        with _LOCK:
            _DIRTY_USAGE.update((k, d) for k, d, _ in usage)
            _DIRTY_KEYS.update(i for i, _, _ in keys)


def usage_by_day(days=7):
    """[(YYYY-MM-DD, requests)] for the last `days` days, oldest first."""
    flush()
    start = (_now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    totals = {}
    try:
        for day, cnt in db.query("SELECT day, SUM(count) FROM usage WHERE day >= ? GROUP BY day", [start]):
            totals[day] = int(cnt or 0)
    except db.DBError:
        totals[today()] = usage_today_total()
    return [((_now() - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d"),
             totals.get((_now() - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d"), 0)) for i in range(days)]


def usage_total():
    flush()
    try:
        return int(db.query("SELECT COALESCE(SUM(count), 0) FROM usage")[0][0])
    except db.DBError:
        return usage_today_total()


def _flusher():
    while True:
        time.sleep(10)
        try:
            flush()
        except Exception as e:  # never let the thread die
            print(f"⚠️ flusher error: {e}")


threading.Thread(target=_flusher, daemon=True).start()


def view(rec):
    with _LOCK:
        return _view(rec)
