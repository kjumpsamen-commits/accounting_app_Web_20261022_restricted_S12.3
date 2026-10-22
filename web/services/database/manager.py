"""
services/database/manager.py
─────────────────────────────
資料庫路徑管理器 + DatabaseManager（連線生命週期控制）。

根本修正（v2）：
  1. 用「版本號」取代 threading.local 快取的連線重用
     每次 swap 後 _db_version 遞增，任何執行緒拿到舊版本號的連線
     都會在下次請求時被拋棄並重建 → 所有執行緒都用新 DB
  2. os.replace() 取代 shutil.move() 保證原子性
  3. 清除 SQLite WAL/SHM 殘留檔，避免舊資料污染
  4. 每次請求都從 _active_path 取最新路徑，不快取路徑
"""

import logging
import os
import sqlite3
import threading
from pathlib import Path

from web.config import settings

logger = logging.getLogger(__name__)

_path_lock    = threading.RLock()
_active_path: str | None = None

# ── 版本號：每次 swap 遞增，用於讓所有執行緒的連線快取失效 ──────────────────
_db_version      = 0
_version_lock    = threading.Lock()


def _next_version() -> int:
    global _db_version
    with _version_lock:
        _db_version += 1
        return _db_version


def get_current_version() -> int:
    with _version_lock:
        return _db_version


# ── DB 路徑決策 ───────────────────────────────────────────────────────────────

def resolve_db_path() -> str:
    """
    決定目前應使用的 DB 路徑。
    優先使用已下載至 /tmp 的版本；若不存在，退回桌面版路徑。
    """
    tmp_path = settings.db_local_path
    if Path(tmp_path).exists():
        return tmp_path

    fallback = os.path.expanduser(settings.db_fallback_path)
    if Path(fallback).exists():
        logger.info("使用本機資料庫：%s", fallback)
        return fallback

    desktop_default = str(Path.home() / ".accounting_app" / "data.db")
    if Path(desktop_default).exists():
        return desktop_default

    raise FileNotFoundError(
        f"找不到可用的資料庫。\n"
        f"嘗試路徑：\n  {tmp_path}\n  {fallback}\n  {desktop_default}"
    )


def get_active_path() -> str:
    """取得目前啟用的 DB 路徑（執行緒安全）。"""
    global _active_path
    with _path_lock:
        if _active_path is None:
            _active_path = resolve_db_path()
        return _active_path


# ── 原子替換：os.replace + 清 WAL/SHM ────────────────────────────────────────

def atomic_replace(src: str, dest: str):
    """
    原子性地將 src 替換為 dest。
    使用 os.replace()（POSIX 保證原子性）。
    同時清除 dest 的 WAL / SHM 殘留，防止 SQLite 讀到舊日誌。
    """
    # 先清掉舊的 WAL / SHM
    for suffix in ("-wal", "-shm"):
        stale = dest + suffix
        if Path(stale).exists():
            try:
                os.remove(stale)
                logger.info("清除殘留 WAL/SHM：%s", stale)
            except OSError as e:
                logger.warning("無法清除 %s：%s", stale, e)

    os.replace(src, dest)   # POSIX 原子操作，比 shutil.move 更安全
    logger.info("atomic_replace 完成：%s → %s", src, dest)


# ── swap_database：讓所有執行緒連線失效 ──────────────────────────────────────

def swap_database(new_path: str) -> bool:
    """
    熱替換資料庫。
    核心機制：遞增 _db_version，所有執行緒下次取連線時
    發現版本不符就拋棄舊連線、重建新連線。
    不需要逐一清除每條執行緒的 _local.db（那樣不可靠）。
    """
    global _active_path

    # 驗證新 DB 可用
    try:
        conn = sqlite3.connect(new_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        conn.close()
        logger.info("新資料庫驗證：transactions=%d 筆", count)
    except Exception as e:
        logger.error("新資料庫驗證失敗，保留舊版本：%s", e)
        return False

    with _path_lock:
        old = _active_path
        _active_path = new_path
        new_ver = _next_version()   # ← 版本號遞增，所有執行緒的 _local.db 自動失效
        logger.info("DB 版本 → v%d  路徑：%s → %s", new_ver, old, new_path)

    return True
