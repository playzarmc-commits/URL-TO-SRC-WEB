"""Signed admin sessions, CSRF tokens and short-lived website tokens."""

import base64
import hashlib
import hmac
import secrets
import time

import config

_SECRET = config.SESSION_SECRET.encode()


def _sig(data: str) -> str:
    mac = hmac.new(_SECRET, data.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def check_password(supplied: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(str(supplied).encode()).digest(),
        hashlib.sha256(config.ADMIN_PASSWORD.encode()).digest(),
    )


# ── admin session cookie: sid.exp.sig ──
def make_session() -> str:
    sid = secrets.token_urlsafe(16)
    exp = int(time.time() + config.ADMIN_SESSION_HOURS * 3600)
    body = f"{sid}.{exp}"
    return f"{body}.{_sig('s:' + body)}"


def session_sid(cookie):
    """Returns the session id if the cookie is valid and unexpired, else None."""
    try:
        sid, exp, sig = cookie.split(".")
        if not hmac.compare_digest(sig, _sig(f"s:{sid}.{exp}")) or int(exp) < time.time():
            return None
        return sid
    except (ValueError, AttributeError):
        return None


def csrf_for(sid: str) -> str:
    return _sig("csrf:" + sid)[:32]


def csrf_ok(sid: str, supplied: str) -> bool:
    return bool(supplied) and hmac.compare_digest(supplied, csrf_for(sid))


# ── website token: exp.nonce.sig (bound to the visitor's IP) ──
def make_web_token(ip: str) -> str:
    exp = int(time.time() + config.WEB_TOKEN_TTL)
    nonce = secrets.token_urlsafe(8)
    return f"{exp}.{nonce}.{_sig(f'w:{exp}.{nonce}.{ip}')}"


def verify_web_token(token: str, ip: str) -> bool:
    try:
        exp, nonce, sig = token.split(".")
        return hmac.compare_digest(sig, _sig(f"w:{exp}.{nonce}.{ip}")) and int(exp) >= time.time()
    except (ValueError, AttributeError):
        return False
