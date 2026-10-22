"""
auth/session.py
────────────────
Session 管理（HttpOnly Cookie + 閒置逾時）。

設計：
  - Session 資料存在伺服器記憶體（dict）
  - Cookie 只存 session_id（隨機 UUID），不含任何使用者資訊
  - HttpOnly + SameSite=Lax + Secure（HTTPS）
  - 閒置 30 分鐘自動失效
  - 任何管理 API 呼叫都更新 last_active
  - 不使用 localStorage / sessionStorage / URL / JS 全域變數
"""

import logging
import secrets
import threading
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ── 設定 ─────────────────────────────────────────────────────────────────────

SESSION_COOKIE    = "admin_session"
SESSION_TTL_MIN   = 30        # 閒置逾時（分鐘）
SESSION_CLEANUP_S = 300       # 清理過期 session 的間隔（秒）

# ── 伺服器端 Session Store ────────────────────────────────────────────────────

_sessions: dict[str, dict] = {}   # {session_id: {username, created_at, last_active, ip, ua}}
_lock = threading.Lock()


def create_session(username: str, ip: str, ua: str) -> str:
    """建立新 Session，回傳 session_id。"""
    session_id = secrets.token_urlsafe(32)
    now = datetime.utcnow()
    with _lock:
        _sessions[session_id] = {
            "username":    username,
            "created_at":  now.isoformat(),
            "last_active": now,
            "ip":          ip,
            "ua":          ua,
        }
    logger.info("[session] 建立：user=%s  ip=%s  id=%s…", username, ip, session_id[:8])
    return session_id


def get_session(session_id: str) -> Optional[dict]:
    """
    取得有效 Session。
    若過期或不存在，回傳 None。
    有效時自動更新 last_active。
    """
    if not session_id:
        return None

    with _lock:
        sess = _sessions.get(session_id)
        if sess is None:
            return None

        # 閒置逾時檢查
        elapsed = datetime.utcnow() - sess["last_active"]
        if elapsed > timedelta(minutes=SESSION_TTL_MIN):
            logger.info("[session] 已逾時：id=%s…", session_id[:8])
            del _sessions[session_id]
            return None

        # 更新 last_active（延長有效時間）
        sess["last_active"] = datetime.utcnow()
        return dict(sess)


def delete_session(session_id: str) -> bool:
    """刪除 Session（登出）。"""
    with _lock:
        if session_id in _sessions:
            sess = _sessions.pop(session_id)
            logger.info("[session] 刪除（登出）：user=%s  id=%s…",
                        sess.get("username"), session_id[:8])
            return True
    return False


def cleanup_expired():
    """清理所有過期 Session（定期呼叫）。"""
    cutoff = datetime.utcnow() - timedelta(minutes=SESSION_TTL_MIN)
    with _lock:
        expired = [sid for sid, s in _sessions.items() if s["last_active"] < cutoff]
        for sid in expired:
            logger.debug("[session] 清理過期：id=%s…", sid[:8])
            del _sessions[sid]
    if expired:
        logger.info("[session] 清理 %d 個過期 session", len(expired))


def active_session_count() -> int:
    with _lock:
        return len(_sessions)


# ── Cookie 工具 ───────────────────────────────────────────────────────────────

def set_session_cookie(response, session_id: str, is_https: bool = True, path: str = "/admin"):
    """在 response 上設定 HttpOnly session cookie。

    S12.1：受限版從 /restricted/admin 登入時 path="/restricted/admin"（原本固定 /admin，
    受限版登入後只能跳回完整版）。
    """
    response.set_cookie(
        key      = SESSION_COOKIE,
        value    = session_id,
        httponly = True,                # 禁止 JavaScript 存取
        samesite = "lax",              # CSRF 基本防護
        secure   = is_https,           # HTTPS only（Render 上為 True）
        max_age  = SESSION_TTL_MIN * 60,
        path     = path,               # 只在 /admin（或 /restricted/admin）路徑有效
    )


def clear_session_cookie(response, path: str = "/admin"):
    """清除 session cookie。"""
    response.delete_cookie(SESSION_COOKIE, path=path)


def get_session_id_from_request(request) -> str:
    """從 Request 的 Cookie 取出 session_id。"""
    return request.cookies.get(SESSION_COOKIE, "")
