"""
admin/routes.py
────────────────
管理介面路由（含完整認證）。

所有 /admin/* 路由均須登入，未登入重導 /admin/login。
所有管理 API 均須登入，未登入回傳 401。
"""
import logging
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from web.utils.templates import templates as _T

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["管理介面"])


# ── 工具函式 ──────────────────────────────────────────────────────────────────

def _get_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    return xff.split(",")[0].strip() if xff else (
        request.client.host if request.client else "unknown"
    )

def _get_ua(request: Request) -> str:
    return request.headers.get("user-agent", "")

def _is_https(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto", "http") == "https"

def _p(request: Request) -> str:
    """S12.1：受限版（/restricted/admin/...）的重導向要留在受限版，回傳網址前綴。"""
    return "/restricted" if request.url.path.startswith("/restricted") else ""

def _check_session(request: Request) -> dict | None:
    """回傳有效 session dict 或 None。"""
    from web.auth.session import get_session_id_from_request, get_session
    return get_session(get_session_id_from_request(request))

def _require_login(request: Request):
    """HTML 頁面：未登入回傳 RedirectResponse，已登入回傳 session dict。"""
    from web.auth.config import is_admin_configured
    if not is_admin_configured():
        return None, RedirectResponse(_p(request) + "/admin/login?error=not_configured", status_code=303)
    sess = _check_session(request)
    if not sess:
        return None, RedirectResponse(_p(request) + "/admin/login", status_code=303)
    return sess, None

def _require_login_api(request: Request):
    """API：未登入回傳 JSONResponse，已登入回傳 session dict。"""
    from web.auth.config import is_admin_configured
    if not is_admin_configured():
        return None, JSONResponse(
            {"error": "Administrator account is not configured."}, status_code=503
        )
    sess = _check_session(request)
    if not sess:
        from web.auth.audit import log_session_expired
        log_session_expired(_get_ip(request), _get_ua(request))
        return None, JSONResponse({"error": "Unauthorized"}, status_code=401)
    return sess, None


# ══════════════════════════════════════════════════════════════════════════════
# 登入 / 登出
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = ""):
    # 已登入直接跳轉
    if _check_session(request):
        return RedirectResponse(_p(request) + "/admin/settings", status_code=303)
    return _T.TemplateResponse(request, "admin/login.html", {"error": error})


@router.post("/login")
async def do_login(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
):
    from web.auth.config import is_admin_configured, check_credentials
    from web.auth.session import create_session, set_session_cookie
    from web.auth.rate_limit import is_locked, record_failure, record_success
    from web.auth.audit import log_login_success, log_login_fail, log_rate_limited

    ip = _get_ip(request)
    ua = _get_ua(request)

    # 未設定帳號
    if not is_admin_configured():
        return RedirectResponse(_p(request) + "/admin/login?error=not_configured", status_code=303)

    # IP 鎖定檢查
    locked, remain = is_locked(ip)
    if locked:
        log_rate_limited(ip, ua)
        return RedirectResponse(_p(request) + "/admin/login?error=rate_limited", status_code=303)

    # 驗證帳號密碼
    if check_credentials(username.strip(), password):
        record_success(ip)
        log_login_success(ip, ua, username.strip())
        session_id = create_session(username.strip(), ip, ua)
        resp = RedirectResponse(_p(request) + "/admin/settings", status_code=303)
        set_session_cookie(resp, session_id, is_https=_is_https(request), path=_p(request) + "/admin")
        return resp
    else:
        record_failure(ip)
        log_login_fail(ip, ua, username.strip(), "密碼錯誤")
        return RedirectResponse(_p(request) + "/admin/login?error=invalid", status_code=303)


@router.get("/logout")
def do_logout(request: Request):
    from web.auth.session import (
        get_session_id_from_request, delete_session, clear_session_cookie
    )
    from web.auth.audit import log_logout

    sid  = get_session_id_from_request(request)
    sess = _check_session(request)
    if sess:
        log_logout(_get_ip(request), _get_ua(request), sess.get("username", ""))
    delete_session(sid)
    resp = RedirectResponse(_p(request) + "/admin/login", status_code=303)
    clear_session_cookie(resp, path=_p(request) + "/admin")
    return resp


# ══════════════════════════════════════════════════════════════════════════════
# 管理設定頁
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/settings", response_class=HTMLResponse)
def admin_settings(request: Request):
    sess, redir = _require_login(request)
    if redir:
        return redir

    from web.config import settings
    from web.services.sync.scheduler import get_last_sync
    from web.services.database.manager import get_active_path
    from web.auth.config import get_admin_username
    from pathlib import Path

    try:
        db_path  = get_active_path()
        db_exists = Path(db_path).exists()
    except FileNotFoundError:
        db_path, db_exists = "（未找到）", False

    return _T.TemplateResponse(request, "admin/settings.html", {
        "page":       "admin",
        "app_title":  "管理設定",
        "cfg":        settings.as_dict(),
        "last_sync":  get_last_sync(),
        "db_path":    db_path,
        "db_exists":  db_exists,
        "intervals":  [5, 10, 30, 60, 1440],
        "admin_user": get_admin_username(),
        "sess":       sess,
    })


@router.post("/settings")
async def save_settings(
    request: Request,
    file_id:          str = Form(""),
    folder_id:        str = Form(""),
    db_filename:      str = Form("accounting.db"),
    interval_minutes: int = Form(30),
    sync_enabled:     str = Form("off"),
    auto_download:    str = Form("off"),
):
    sess, redir = _require_login(request)
    if redir:
        return redir

    from web.config import settings
    from web.services.sync.scheduler import restart_scheduler
    settings.update(
        file_id          = file_id.strip(),
        folder_id        = folder_id.strip(),
        db_filename      = db_filename.strip() or "accounting.db",
        interval_minutes = interval_minutes,
        sync_enabled     = (sync_enabled == "on"),
        auto_download    = (auto_download == "on"),
    )
    restart_scheduler()
    logger.info("[admin] 設定已儲存  user=%s", sess.get("username"))
    return RedirectResponse(_p(request) + "/admin/settings?saved=1", status_code=303)


@router.get("/help", response_class=HTMLResponse)
def admin_help(request: Request):
    """S12：資料來源說明（Google Drive File ID、交接時要改哪裡）。需要登入。"""
    sess, redir = _require_login(request)
    if redir:
        return redir
    from web.services.datasource_help import describe, mask_text
    from web.services.sync.scheduler import get_last_sync
    last_sync = dict(get_last_sync() or {})
    last_sync["message"] = mask_text(str(last_sync.get("message") or ""))   # 失敗訊息含下載網址（完整 File ID）
    return _T.TemplateResponse(request, "admin/help.html", {
        "page":      "admin",
        "app_title": "資料來源說明",
        "info":      describe(),
        "last_sync": last_sync,
    })


# ══════════════════════════════════════════════════════════════════════════════
# 管理 API（均需認證）
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/sync")
async def trigger_sync(request: Request):
    sess, err = _require_login_api(request)
    if err:
        return err
    from web.services.sync.scheduler import manual_sync
    logger.info("[admin] 手動同步  user=%s  ip=%s",
                sess.get("username"), _get_ip(request))
    result = manual_sync()
    return JSONResponse(result)


@router.get("/sync-status")
def sync_status(request: Request):
    sess, err = _require_login_api(request)
    if err:
        return err
    from web.services.sync.scheduler import get_last_sync
    return JSONResponse(get_last_sync())


# ══════════════════════════════════════════════════════════════════════════════
# 稽核紀錄
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/audit", response_class=HTMLResponse)
def audit_log(request: Request):
    sess, redir = _require_login(request)
    if redir:
        return redir
    from web.auth.audit import get_log
    return _T.TemplateResponse(request, "admin/audit.html", {
        "page":      "admin",
        "app_title": "登入稽核紀錄",
        "logs":      get_log(200),
        "sess":      sess,
    })


@router.get("/audit/api")
def audit_log_api(request: Request):
    sess, err = _require_login_api(request)
    if err:
        return err
    from web.auth.audit import get_log
    return JSONResponse(get_log(100))
