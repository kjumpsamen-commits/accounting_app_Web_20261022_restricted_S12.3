"""
services/sync/scheduler.py
───────────────────────────
背景排程同步服務（APScheduler）。

功能：
  - 啟動時依設定決定是否立即下載
  - 依設定間隔（5/10/30/60 分鐘 / 每天）定期從 Google Drive 同步
  - 同步完成後熱替換 DB，無需重啟
  - 執行緒安全，同一時間只允許一個同步作業
  - 完整 Log
"""

import logging
import threading
import time
from datetime import datetime

logger = logging.getLogger(__name__)

_scheduler = None
_sync_lock = threading.Lock()
_last_sync: dict = {
    "time":    None,
    "success": None,
    "message": "",
    "elapsed": 0.0,
}


def _do_sync() -> dict:
    """執行一次完整同步流程。"""
    from web.config import settings
    from web.services.sync.gdrive import download_db
    from web.services.database.manager import swap_database, get_current_version
    from web.utils.cache import invalidate
    import os
    from pathlib import Path

    file_id   = settings.gdrive_file_id
    folder_id = settings.gdrive_folder_id
    filename  = settings.gdrive_db_filename
    dest_path = settings.db_local_path

    if not file_id and not folder_id:
        return {"success": False, "message": "尚未設定 GDRIVE_FILE_ID 或 GDRIVE_FOLDER_ID"}

    logger.info("=== 開始同步 ===  dest=%s  ver_before=v%d",
                dest_path, get_current_version())

    result = download_db(
        dest_path = dest_path,
        folder_id = folder_id,
        filename  = filename,
        file_id   = file_id,
    )

    if result["success"]:
        # Step A：版本號遞增（所有執行緒下次請求時自動重建連線）
        ok = swap_database(dest_path)

        # Step B：清除 in-process TTL 快取（統計、分類等）
        cleared = invalidate()

        # Step C：log 確認
        stat     = Path(dest_path).stat()
        ver_after = get_current_version()
        logger.info(
            "=== 同步完成 ===  ver=v%d  size=%dKB  mtime=%s  cache_cleared=%d",
            ver_after,
            stat.st_size // 1024,
            stat.st_mtime,
            cleared,
        )
        result["db_version_after"] = ver_after
        result["swap_ok"]          = ok
        result["cache_cleared"]    = cleared
    else:
        logger.warning("同步失敗，保留舊資料庫：%s", result["message"])

    return result


def _safe_sync():
    """帶鎖保護的同步入口（排程器呼叫此函式）。"""
    global _last_sync
    if not _sync_lock.acquire(blocking=False):
        logger.info("同步已在進行中，跳過本次排程")
        return

    try:
        result = _do_sync()
        _last_sync = {
            "time":    datetime.now().isoformat(timespec="seconds"),
            "success": result["success"],
            "message": result["message"],
            "elapsed": result.get("elapsed", 0),
        }
    except Exception as e:
        logger.error("同步例外：%s", e, exc_info=True)
        _last_sync = {
            "time":    datetime.now().isoformat(timespec="seconds"),
            "success": False,
            "message": str(e),
            "elapsed": 0,
        }
    finally:
        _sync_lock.release()


def manual_sync() -> dict:
    """手動觸發同步（管理介面呼叫）。"""
    _safe_sync()
    return _last_sync


def get_last_sync() -> dict:
    return dict(_last_sync)


def start_scheduler():
    """
    啟動 APScheduler 背景排程。
    間隔從 settings.sync_interval_minutes 讀取。
    """
    global _scheduler
    from apscheduler.schedulers.background import BackgroundScheduler
    from web.config import settings

    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)

    interval = settings.sync_interval_minutes
    _scheduler = BackgroundScheduler(timezone="Asia/Taipei")
    _scheduler.add_job(
        _safe_sync,
        trigger="interval",
        minutes=interval,
        id="db_sync",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.start()
    logger.info("排程同步已啟動，間隔：%d 分鐘", interval)


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("排程同步已停止")


def restart_scheduler():
    """重新載入設定後重啟排程（管理介面變更設定後呼叫）。"""
    stop_scheduler()
    from web.config import settings
    if settings.sync_enabled:
        start_scheduler()


def startup_sync():
    """
    應用程式啟動時執行的同步邏輯。

    行為：
      1. 永遠嘗試下載（預設 file_id 已內建）
      2. 下載失敗 → 只記錄警告，不拋例外，應用程式繼續啟動
         - 若本機已有 /tmp/accounting.db → 繼續使用
         - 若本機也沒有 → 等待後續手動同步或排程同步
      3. 成功後啟動定期排程
    """
    from web.config import settings

    settings.reload()

    has_source = bool(settings.gdrive_file_id or settings.gdrive_folder_id)

    if settings.auto_download and has_source:
        logger.info(
            "啟動時自動同步（file_id=%s）...",
            settings.gdrive_file_id or settings.gdrive_folder_id
        )
        # 在背景執行，不阻塞 FastAPI 啟動
        # 下載失敗由 _safe_sync 內部處理，不會拋例外到主執行緒
        t = threading.Thread(
            target=_startup_sync_worker,
            daemon=True,
            name="startup-sync",
        )
        t.start()
    else:
        logger.info(
            "略過啟動同步（auto_download=%s, 已設定來源=%s）",
            settings.auto_download, has_source,
        )

    # 無論啟動同步是否執行，都啟動定期排程
    if settings.sync_enabled and has_source:
        start_scheduler()


def _startup_sync_worker():
    """
    啟動同步的背景 Worker。
    下載失敗時只記錄警告，不影響應用程式運作。
    """
    global _last_sync
    try:
        logger.info("[startup-sync] 開始下載預設資料庫...")
        result = _do_sync()

        if result["success"]:
            logger.info(
                "[startup-sync] ✅ 成功  size=%s  elapsed=%.2fs",
                result.get("message", ""),
                result.get("elapsed", 0),
            )
        else:
            logger.warning(
                "[startup-sync] ⚠️  下載失敗（跳過，使用舊資料庫）：%s",
                result["message"],
            )
            # 確認是否有可用的舊資料庫
            _log_fallback_status()

        _last_sync = {
            "time":    datetime.now().isoformat(timespec="seconds"),
            "success": result["success"],
            "message": result["message"],
            "elapsed": result.get("elapsed", 0),
        }

    except Exception as e:
        # 任何例外都不影響啟動
        logger.warning("[startup-sync] ⚠️  例外（跳過）：%s", e)
        _last_sync = {
            "time":    datetime.now().isoformat(timespec="seconds"),
            "success": False,
            "message": f"啟動同步失敗（已跳過）：{e}",
            "elapsed": 0,
        }


def _log_fallback_status():
    """記錄目前可用的 fallback 資料庫狀態。"""
    from pathlib import Path
    from web.config import settings

    paths_to_check = [
        settings.db_local_path,
        str(Path.home() / ".accounting_app" / "data.db"),
    ]
    for p in paths_to_check:
        if Path(p).exists():
            size = Path(p).stat().st_size // 1024
            logger.info("[startup-sync] 使用現有資料庫：%s (%d KB)", p, size)
            return
    logger.warning("[startup-sync] ⚠️  找不到任何可用資料庫，API 查詢將回傳錯誤直到同步成功")
