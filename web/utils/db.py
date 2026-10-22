"""
utils/db.py  （v2 — 版本號連線驗證）
──────────────────────────────────────
根本修正：

問題根源
  threading.local 只快取「建立連線的那條執行緒」的 db 物件。
  swap_database() 只能清掉呼叫當下那條執行緒的快取，
  其他執行緒的 _local.db 依然指向舊 DB 連線 → 回傳舊資料。

修正方式
  在 _local 同時儲存「建立連線時的 db_version」。
  每次 _get_shared_db() 時比對 _local.db_version vs 全域 _db_version：
    - 相同 → 連線有效，直接重用
    - 不同 → 舊連線，關閉後重建新連線

  swap_database() 只需遞增全域 _db_version，
  所有執行緒下一次請求時自動感知版本變更並重建連線。
  無需任何跨執行緒清除操作。
"""

import logging
import sqlite3
import sys
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_ROOT = str(Path(__file__).parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from database import Database as _DesktopDB

# threading.local：每條執行緒獨立儲存 db 實例 + 建立時的版本號
_local = threading.local()


def _build_connection(db_path: str) -> _DesktopDB:
    """
    建立指向 db_path 的連線並設定效能 PRAGMA。
    使用 sqlite3 直接連線，不走桌面版的 _create_tables()。
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    # 效能 PRAGMA（WAL mode 提升並發讀取）
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA cache_size = -4000")   # 4MB page cache（較保守，確保讀最新）
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA synchronous = NORMAL")

    # 建立假的 _DesktopDB 物件，注入 conn
    db = _DesktopDB.__new__(_DesktopDB)
    object.__setattr__(db, "conn", conn)
    object.__setattr__(db, "db_path", db_path)

    logger.debug("[DB] 建立新連線：path=%s", db_path)
    return db


def _get_fresh_db() -> _DesktopDB:
    """
    取得目前執行緒的 DB 連線。
    若版本號不符（DB 已更新）則關閉舊連線、重建新連線。
    """
    from web.services.database.manager import get_active_path, get_current_version

    current_ver = get_current_version()
    local_ver   = getattr(_local, "db_version", -1)
    local_db    = getattr(_local, "db", None)

    # 版本號相同 → 連線有效，直接重用
    if local_db is not None and local_ver == current_ver:
        return local_db

    # 版本號不同或無連線 → 關閉舊連線，重建
    if local_db is not None:
        try:
            local_db.conn.close()
        except Exception:
            pass
        logger.info("[DB] 版本變更 v%d→v%d，重建連線", local_ver, current_ver)

    try:
        db_path = get_active_path()
    except FileNotFoundError:
        logger.error("[DB] 找不到 DB 路徑，嘗試使用桌面版預設連線")
        db      = _DesktopDB()
        _local.db         = db
        _local.db_version = current_ver
        return db

    db = _build_connection(db_path)
    _local.db         = db
    _local.db_version = current_ver
    return db


class ReadOnlyDB:
    """
    唯讀資料庫存取層。
    每次請求都經由 _get_fresh_db() 取得連線，
    DB 更新後所有後續請求自動使用新連線。
    """

    def __init__(self):
        self._db = _get_fresh_db()

    def close(self):
        pass  # 連線由 _local 管理，不在此關閉

    # ── Debug 資訊 ────────────────────────────────────────────────────────────

    def debug_info(self) -> dict:
        """回傳目前 DB 連線的診斷資訊（供 /api/health 使用）。"""
        import os
        from web.services.database.manager import get_active_path, get_current_version
        try:
            path = get_active_path()
            stat = os.stat(path)
            import datetime
            mtime = datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
            return {
                "db_path":    path,
                "db_size_kb": round(stat.st_size / 1024, 1),
                "db_mtime":   mtime,
                "db_version": get_current_version(),
                "thread_ver": getattr(_local, "db_version", -1),
            }
        except Exception as e:
            return {"error": str(e)}

    # ── Transactions ──────────────────────────────────────────────────────────

    def get_transactions(self, filters=None):
        return self._db.get_transactions(filters)

    def get_balance_before(self, date_str, type_=None, category=None):
        return self._db.get_balance_before(date_str, type_, category)

    def get_deletions(self, filters=None):
        return self._db.get_deletions(filters)

    # ── Categories ────────────────────────────────────────────────────────────

    def get_categories(self, type_=None):
        return self._db.get_categories(type_)

    # ── Statistics ────────────────────────────────────────────────────────────

    def get_summary(self, filters=None):
        return self._db.get_summary(filters)

    def get_stats_by_year(self):
        return self._db.get_stats_by_year()

    def get_stats_by_month(self, year=None):
        return self._db.get_stats_by_month(year)

    def get_stats_by_day(self, year=None, month=None):
        return self._db.get_stats_by_day(year, month)

    def get_stats_by_category(self, type_, filters=None):
        return self._db.get_stats_by_category(type_, filters)

    # ── Rent ──────────────────────────────────────────────────────────────────

    def get_tenants(self, active_only=True):
        return self._db.get_tenants(active_only)

    def get_payments_for_month(self, year, month, grade_filter=None):
        return self._db.get_payments_for_month(year, month, grade_filter)

    def get_earliest_rent_payment_year(self):
        return self._db.get_earliest_rent_payment_year()

    # ── Settings ──────────────────────────────────────────────────────────────

    def get_setting(self, key, default=""):
        return self._db.get_setting(key, default)


def get_db():
    """FastAPI Depends 工廠：每次請求建立 ReadOnlyDB（內部自動檢查版本）。"""
    db = ReadOnlyDB()
    try:
        yield db
    finally:
        pass
