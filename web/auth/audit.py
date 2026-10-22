"""
auth/audit.py
──────────────
登入稽核紀錄。

紀錄所有登入事件：成功、失敗、登出、Session 過期。
存在記憶體（最近 500 筆），可透過 /admin/audit 查閱。
"""

import logging
import threading
from collections import deque
from datetime import datetime

logger = logging.getLogger(__name__)

_lock  = threading.Lock()
_LOG_MAX = 500
_audit_log: deque = deque(maxlen=_LOG_MAX)


def _record(event: str, ip: str, ua: str, username: str = "", detail: str = ""):
    entry = {
        "time":     datetime.now().isoformat(timespec="seconds"),
        "event":    event,
        "ip":       ip,
        "ua":       (ua or "")[:120],
        "username": username,
        "detail":   detail,
    }
    with _lock:
        _audit_log.appendleft(entry)

    logger.info("[audit] %s  user=%s  ip=%s  %s", event, username, ip, detail)
    return entry


def log_login_success(ip: str, ua: str, username: str):
    return _record("LOGIN_SUCCESS", ip, ua, username)


def log_login_fail(ip: str, ua: str, username: str, reason: str = ""):
    return _record("LOGIN_FAIL", ip, ua, username, reason)


def log_logout(ip: str, ua: str, username: str):
    return _record("LOGOUT", ip, ua, username)


def log_session_expired(ip: str, ua: str):
    return _record("SESSION_EXPIRED", ip, ua)


def log_rate_limited(ip: str, ua: str):
    return _record("RATE_LIMITED", ip, ua, detail="Too many failures")


def get_log(limit: int = 100) -> list[dict]:
    with _lock:
        return list(_audit_log)[:limit]
