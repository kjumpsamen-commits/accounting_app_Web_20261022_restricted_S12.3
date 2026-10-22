"""
web/app.py
──────────
FastAPI 應用程式入口（Render 部署版）。

啟動指令：
    uvicorn web.app:app --host 0.0.0.0 --port $PORT

本機開發：
    uvicorn web.app:app --reload --port 8000
"""

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# 確保專案根目錄在 sys.path
_ROOT = str(Path(__file__).parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ── Lifespan（啟動 / 關閉）────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app):
    # ── 啟動 ──────────────────────────────────────────────────────────────────
    logger.info("=== 應用程式啟動 ===")
    try:                                                   # S12.3：Render Log 可看出部署的是哪一個 zip
        from web.api.diagnostics import _edition_info
        logger.warning("=== 網站版本別：%(edition_name)s｜版本 %(app_version)s｜整站受限：%(restricted_site)s ===", _edition_info())
    except Exception as e:  # noqa: BLE001
        logger.warning("版本別資訊讀取失敗：%s", e)
    from web.config import settings
    from web.services.sync.scheduler import startup_sync, stop_scheduler
    from web.auth.config import log_startup_check
    import threading, time

    settings.reload()
    logger.info(
        "設定載入：DB=%s  file_id=%s  folder_id=%s  interval=%dmin",
        settings.db_local_path,
        settings.gdrive_file_id or "（未設定）",
        settings.gdrive_folder_id or "（未設定）",
        settings.sync_interval_minutes,
    )

    # 管理者帳號狀態提示
    log_startup_check()

    # 啟動時同步（失敗跳過）
    startup_sync()

    # Session 清理背景執行緒（每 5 分鐘）
    def _session_cleaner():
        from web.auth.session import cleanup_expired, SESSION_CLEANUP_S
        while True:
            time.sleep(SESSION_CLEANUP_S)
            try:
                cleanup_expired()
            except Exception:
                pass

    t = threading.Thread(target=_session_cleaner, daemon=True, name="session-cleaner")
    t.start()

    yield

    # ── 關閉 ──────────────────────────────────────────────────────────────────
    logger.info("=== 應用程式關閉 ===")
    stop_scheduler()


# ── FastAPI App ───────────────────────────────────────────────────────────────

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware

app = FastAPI(
    title="個人記帳簿 Web（唯讀）",
    description="桌面版記帳軟體的唯讀 Web 查詢介面，所有 API 僅 GET。",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=lifespan,
)


# ── 全域例外 Handler：DB 尚未下載時顯示等待頁 ────────────────────────────────

@app.exception_handler(FileNotFoundError)
async def db_not_found_handler(request: Request, exc: FileNotFoundError):
    """DB 尚未從 Google Drive 下載時的友善提示頁面。"""
    if request.url.path.startswith("/api/"):
        from fastapi.responses import JSONResponse
        return JSONResponse(
            {"status": "error", "detail": "資料庫尚未下載，請稍後再試"},
            status_code=503,
        )
    html = """
    <!DOCTYPE html><html lang="zh-Hant">
    <head><meta charset="UTF-8"><meta http-equiv="refresh" content="10">
    <title>資料庫載入中</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    </head>
    <body class="bg-light d-flex align-items-center justify-content-center" style="min-height:100vh">
    <div class="text-center p-5">
      <div class="spinner-border text-primary mb-3" style="width:3rem;height:3rem"></div>
      <h4>資料庫載入中...</h4>
      <p class="text-muted">系統正在從 Google Drive 下載資料庫，<br>通常需要 10–30 秒，頁面將自動重新整理。</p>
      <a href="/api/sync-status" class="btn btn-outline-primary btn-sm mt-2">查看同步狀態</a>
    </div>
    </body></html>
    """
    return HTMLResponse(html, status_code=503)


# ── Middleware ─────────────────────────────────────────────────────────────────

class CacheControlMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        path = request.url.path
        if path.startswith("/static/"):
            # CSS / JS：7 天強制快取（有版本號時可永久快取）
            response.headers["Cache-Control"] = (
                "public, max-age=604800, stale-while-revalidate=86400"
            )
        elif path.startswith("/api/") or path.endswith(".xlsx"):
            # API 資料 / 下載檔案：不快取
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"]        = "no-cache"
        else:
            # HTML 頁面：強制每次重新向伺服器驗證，不使用任何快取版本
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"]        = "no-cache"
            response.headers["Expires"]       = "0"
        # S9（W-1）：所有回應都請搜尋引擎不要收錄（網址仍可正常開啟）
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response


@app.get("/robots.txt", include_in_schema=False)
def robots_txt():
    """S9（W-1）：禁止搜尋引擎收錄整個網站。"""
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse("User-agent: *\nDisallow: /\n")


app.add_middleware(CacheControlMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1024)

# ── S12.2：受限版網站「整站只開放繳費狀況與特殊收費」──────────────────────────
# 你的實際用法：受限版 zip 單獨部署成一個網站（report-submission），只給人查看繳費狀況
# 與特殊收費。原本的設計（受限版裁決 Q2～Q4）是完整版與 /restricted 放在同一個網站、
# 只鎖畫面，所以從網址根目錄「/」進來是完整版，全部按鈕都能按。
# 這裡改成在伺服器端擋：不在白名單內的網址一律轉回「繳費狀況」，資料 API 回 404。
# 環境變數 RESTRICTED_SITE=false 可關閉（只給測試舊有完整版頁面用；部署時不要設定）。

RESTRICTED_ALLOWED_PREFIXES = (
    "/restricted/rent",                  # 繳費狀況（含換年／月／年級）
    "/restricted/special-charge",        # 特殊收費（含活動明細、活動 Excel 下載）
    "/restricted/about",                 # 關於／版本說明
    "/restricted/admin/",                # 管理設定（需登入；同步資料庫用）
    "/static/",
)
RESTRICTED_ALLOWED_EXACT = {"/robots.txt", "/api/health", "/api/sync-status", "/favicon.ico"}
RESTRICTED_HOME = "/restricted/rent"


def restricted_site_enabled() -> bool:
    return os.environ.get("RESTRICTED_SITE", "true").strip().lower() not in ("0", "false", "no", "off")


def restricted_redirect_target(path: str) -> str | None:
    """不允許的網址要轉到哪裡；允許的回傳 None。"""
    if path in RESTRICTED_ALLOWED_EXACT or path.startswith(RESTRICTED_ALLOWED_PREFIXES):
        return None
    p = path[len("/restricted"):] if path.startswith("/restricted") else path
    for full, target in (("/special-charge", "/restricted/special-charge"),
                         ("/about", "/restricted/about")):
        if p == full or p.startswith(full + "/"):
            return target + p[len(full):]
    if p.startswith("/admin/") or p == "/admin":
        return "/restricted/admin/" + p[len("/admin/"):] if p != "/admin" else "/restricted/admin/settings"
    return RESTRICTED_HOME


class RestrictedSiteMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if restricted_site_enabled():
            path = request.url.path
            target = restricted_redirect_target(path)
            if target is not None:
                from fastapi.responses import JSONResponse, RedirectResponse
                if path.startswith("/api/") or request.method not in ("GET", "HEAD"):
                    return JSONResponse({"detail": "Not Found"}, status_code=404)
                q = request.url.query
                if target != RESTRICTED_HOME and q:
                    target += "?" + q
                return RedirectResponse(target, status_code=303)
        return await call_next(request)


app.add_middleware(RestrictedSiteMiddleware)


# ── 靜態檔案 ──────────────────────────────────────────────────────────────────

_STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")


# ── HTML 頁面 Router ──────────────────────────────────────────────────────────

from web.routes.dashboard import router as dashboard_router
from web.routes.txn       import router as txn_router
from web.routes.stats     import router as stats_router
from web.routes.rent      import router as rent_router
from web.routes.export_   import router as export_router
from web.routes.search    import router as search_router
from web.routes.special_charge import router as special_charge_router
from web.admin.routes     import router as admin_router
from web.routes.about     import router as about_router     # S12

app.include_router(dashboard_router)
app.include_router(txn_router)
app.include_router(stats_router)
app.include_router(rent_router)
app.include_router(export_router)
app.include_router(search_router)
app.include_router(special_charge_router)
app.include_router(admin_router)
app.include_router(about_router)     # S12：關於（公開）

# ── Restricted UI Version ────────────────────────────────────────────────────
# ★ 依裁決 Q2/Q3：同一個 Render Service、同一個執行程序、同一個 DB／
# SQLite 連線，不複製任何 route handler 或 business logic——直接把
# 「完全相同」的 router 物件，再用 /restricted 前綴掛載一次。FastAPI 允許
# 同一個 APIRouter 被 include_router() 多次，兩組路徑背後呼叫的是同一個
# Python 函式物件，商業邏輯、SQL、DB 存取完全沒有第二份。
#
# base.html／rent.html／special_charge.html／tenants.html 透過
# `request.url.path.startswith('/restricted')` 判斷目前是否為受限模式，
# 決定哪些 nav 項目顯示 disabled（純 UI 層判斷，見各樣板檔案）。
#
# ★ 依裁決 Q4：這裡刻意不加任何 Depends()／權限檢查——受限版與完整版的
# 後端路由本身都可以正常回應，這是刻意接受的設計，不是本次要補的安全
# 邊界。
app.include_router(dashboard_router, prefix="/restricted")
app.include_router(txn_router, prefix="/restricted")
app.include_router(stats_router, prefix="/restricted")
app.include_router(rent_router, prefix="/restricted")
app.include_router(export_router, prefix="/restricted")
app.include_router(special_charge_router, prefix="/restricted")
app.include_router(admin_router, prefix="/restricted")
app.include_router(about_router, prefix="/restricted")     # S12：受限版的關於頁（保留受限選單）
# 注意：search_router 沒有在需求驗收表中被提及，也不是 sidebar 既有連結
# 項目，依「不要自行增加功能」原則，本次不掛載 /restricted/search。


# ── JSON API Router ───────────────────────────────────────────────────────────

from web.api.transactions import router as api_txn
from web.api.statistics   import router as api_stats
from web.api.rent         import router as api_rent
from web.api.categories   import router as api_cats
from web.api.settings     import router as api_settings
from web.api.diagnostics  import router as api_diag

app.include_router(api_txn)
app.include_router(api_stats)
app.include_router(api_rent)
app.include_router(api_cats)
app.include_router(api_settings)
app.include_router(api_diag)


# ── 啟動入口（本機直接執行）──────────────────────────────────────────────────

def main():
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("web.app:app", host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    main()
