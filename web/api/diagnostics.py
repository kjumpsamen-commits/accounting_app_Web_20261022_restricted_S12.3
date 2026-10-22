"""
api/diagnostics.py
──────────────────
系統診斷端點。

每次 /api/health 呼叫都輸出：
  - 目前 DB 路徑
  - SQLite 檔案修改時間
  - SQLite 檔案大小
  - 查詢總筆數
  - DB 版本號（swap 後會遞增）
  - 執行緒本地版本號（應與全域版本號一致）
  - WAL/SHM 是否存在
"""

import logging
import os
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends
from web.utils.db import get_db, ReadOnlyDB
from web.utils.cache import cache_stats, invalidate

router = APIRouter(prefix="/api", tags=["API-診斷"])
logger = logging.getLogger(__name__)


def _edition_info() -> dict:
    """S12.3：網站版本別與版本號（部署後用 /api/health 確認放上去的是哪一個 zip）。"""
    import version
    from web import edition
    try:
        from web.app import restricted_site_enabled
        site_locked = restricted_site_enabled()
    except ImportError:                          # 完整版沒有整站受限
        site_locked = False
    return {"app_version": version.version_text(), "edition": edition.EDITION,
            "edition_name": edition.EDITION_NAME, "restricted_site": site_locked}


@router.get("/health", summary="健康檢查 + DB 完整診斷")
def health(db: ReadOnlyDB = Depends(get_db)):
    from web.services.database.manager import get_active_path, get_current_version

    # ── DB 路徑與檔案資訊 ─────────────────────────────────────────────────────
    try:
        db_path = get_active_path()
        stat    = os.stat(db_path)
        mtime   = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
        size_kb = round(stat.st_size / 1024, 1)
    except Exception as e:
        db_path, mtime, size_kb = str(e), "?", 0

    # ── 查詢總筆數 ────────────────────────────────────────────────────────────
    try:
        summary = db.get_summary()
        txn_count = summary.get("count", 0)
    except Exception as e:
        txn_count = f"ERROR: {e}"

    # ── 版本號 ────────────────────────────────────────────────────────────────
    global_ver = get_current_version()
    from web.utils.db import _local
    thread_ver = getattr(_local, "db_version", -1)

    # ── WAL/SHM 殘留檢查 ──────────────────────────────────────────────────────
    wal_exists = Path(db_path + "-wal").exists() if db_path else False
    shm_exists = Path(db_path + "-shm").exists() if db_path else False

    info = {
        "status":       "ok" if isinstance(txn_count, int) else "degraded",
        "db_path":      db_path,
        "db_size_kb":   size_kb,
        "db_mtime":     mtime,
        "db_version":   global_ver,
        "thread_ver":   thread_ver,
        "version_ok":   global_ver == thread_ver,
        "total_txns":   txn_count,
        "wal_exists":   wal_exists,
        "shm_exists":   shm_exists,
    }
    info.update(_edition_info())                 # S12.3：部署的是完整版還是簡易版

    # Log 每次呼叫（方便 Render Log 追蹤）
    logger.info("[health] %s", info)
    return info


@router.get("/sync-status", summary="上次同步狀態")
def api_sync_status():
    from web.services.sync.scheduler import get_last_sync
    return get_last_sync()


@router.get("/cache-stats", summary="快取狀態")
def get_cache_stats():
    return cache_stats()


@router.get("/auth-check", summary="認證設定診斷（不含敏感資訊）")
def auth_check():
    """
    確認管理者帳號設定狀態。
    不回傳任何密碼或 hash 內容，只回傳格式診斷資訊。
    """
    import os
    raw_user = os.environ.get("ADMIN_USERNAME", "")
    raw_hash = os.environ.get("ADMIN_PASSWORD_HASH", "")

    return {
        "username_set":    bool(raw_user),
        "username_length": len(raw_user),
        "hash_set":        bool(raw_hash),
        "hash_length":     len(raw_hash),
        "hash_prefix":     raw_hash[:6] if raw_hash else "",
        "hash_valid_format": (
            raw_hash.startswith("$2b$") or
            raw_hash.startswith("$2y$") or
            raw_hash.startswith("$2a$")
        ) if raw_hash else False,
        "is_configured":   bool(raw_user and raw_hash),
        "hint": (
            "正常" if (raw_user and raw_hash and
                      (raw_hash.startswith("$2b$") or raw_hash.startswith("$2y$")))
            else "請檢查 ADMIN_USERNAME 和 ADMIN_PASSWORD_HASH 環境變數"
        )
    }


@router.get("/cache-clear", summary="清除快取")
def clear_cache():
    count = invalidate()
    logger.info("[cache-clear] cleared %d keys", count)
    return {"cleared": count}


@router.get("/db-reload", summary="強制重建 DB 連線（除錯用）")
def force_db_reload():
    """
    強制讓所有執行緒下次請求時重建連線。
    做法：遞增版本號（等同於 swap_database 的版本更新效果）。
    """
    from web.services.database.manager import _next_version, get_active_path
    new_ver = _next_version()
    cleared = invalidate()
    path    = get_active_path()
    logger.info("[db-reload] 強制重建連線  ver=v%d  path=%s", new_ver, path)
    return {"db_version": new_ver, "db_path": path, "cache_cleared": cleared}
