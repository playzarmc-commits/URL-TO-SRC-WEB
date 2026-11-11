"""Turso (libSQL) client over the HTTP pipeline API — synchronous, requests only."""

import requests

import config

_BASE = str(config.TURSO_URL).strip().replace("libsql://", "https://", 1).rstrip("/")
ENDPOINT = f"{_BASE}/v2/pipeline"
ENABLED = bool(config.TURSO_URL and config.TURSO_TOKEN)
_HEADERS = {"Authorization": f"Bearer {config.TURSO_TOKEN}", "Content-Type": "application/json"}


class DBError(Exception):
    pass


def _arg(v):
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "integer", "value": str(int(v))}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    return {"type": "text", "value": str(v)}


def _cell(c):
    t = c.get("type")
    if t == "null":
        return None
    v = c.get("value")
    if t == "integer":
        return int(v)
    if t == "float":
        return float(v)
    return v


def run(statements, timeout=20):
    """statements: list of (sql, args). One HTTP request. Returns one row-list per statement."""
    if not ENABLED:
        raise DBError("Database is not configured.")
    reqs = [
        {"type": "execute", "stmt": {"sql": sql, "args": [_arg(a) for a in (args or [])]}}
        for sql, args in statements
    ]
    reqs.append({"type": "close"})
    try:
        r = requests.post(ENDPOINT, headers=_HEADERS, json={"requests": reqs}, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        raise DBError(f"Database unreachable: {e.__class__.__name__}") from e
    out = []
    for res in data["results"]:
        if res["type"] == "error":
            raise DBError(res["error"].get("message", "Database error"))
        if res["type"] == "ok" and res["response"]["type"] == "execute":
            rows = res["response"]["result"].get("rows", [])
            out.append([[_cell(c) for c in row] for row in rows])
    return out


def query(sql, args=None, timeout=20):
    return run([(sql, args or [])], timeout)[0]


def init_schema():
    run([
        ("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, joined TEXT, last_seen TEXT)", []),
        ("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)", []),
        ("CREATE TABLE IF NOT EXISTS api_keys ("
         "id TEXT PRIMARY KEY, label TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE, key_prefix TEXT, last4 TEXT, "
         "plan TEXT NOT NULL, daily_limit INTEGER, scopes TEXT NOT NULL, expires_at TEXT, active INTEGER NOT NULL DEFAULT 1, "
         "system INTEGER NOT NULL DEFAULT 0, note TEXT, created_at TEXT, last_used TEXT, total_uses INTEGER NOT NULL DEFAULT 0)", []),
        ("CREATE TABLE IF NOT EXISTS usage (key_id TEXT NOT NULL, day TEXT NOT NULL, count INTEGER NOT NULL, PRIMARY KEY (key_id, day))", []),
    ])
