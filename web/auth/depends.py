"""
auth/depends.py
────────────────
FastAPI Depends：管理頁面與 API 的認證守門。

用法：
  @router.get("/admin/xxx")
  def page(admin=Depends(require_admin)):
      ...
"""

import logging
from fastapi import Depends, Request
from fastapi.responses import RedirectResponse, JSONResponse

from web.auth.config import is_admin_configured
from web.auth.session import get_session_id_from_request, get_session

logger = logging.getLogger(__name__)


def _get_client_ip(request: Request) -> str:
    """取得真實 IP（支援 Render / 反向代理的 X-Forwarded-For）。"""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _get_ua(request: Request) -> str:
    return request.headers.get("user-agent", "")


class AdminNotConfigured(Exception):
    pass


class NotAuthenticated(Exception):
    pass


class SessionExpired(Exception):
    pass


def get_current_admin(request: Request) -> dict:
    """
    取得目前登入的管理者 Session。
    失敗時拋出對應例外（由 require_admin / require_admin_api 處理）。
    """
    if not is_admin_configured():
        raise AdminNotConfigured()

    session_id = get_session_id_from_request(request)
    if not session_id:
        raise NotAuthenticated()

    sess = get_session(session_id)
    if sess is None:
        raise SessionExpired()

    return sess


async def require_admin(request: Request):
    """
    HTML 頁面用：未登入 → 重導向 /admin/login。
    """
    try:
        return get_current_admin(request)
    except AdminNotConfigured:
        return RedirectResponse("/admin/login?error=not_configured")
    except (NotAuthenticated, SessionExpired):
        return RedirectResponse("/admin/login")


async def require_admin_api(request: Request):
    """
    JSON API 用：未登入 → 401 JSON。
    """
    if not is_admin_configured():
        return JSONResponse(
            {"error": "Administrator account is not configured."},
            status_code=503,
        )

    session_id = get_session_id_from_request(request)
    if not session_id:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    sess = get_session(session_id)
    if sess is None:
        from web.auth.audit import log_session_expired
        log_session_expired(_get_client_ip(request), _get_ua(request))
        return JSONResponse({"error": "Session Expired"}, status_code=401)

    return sess
