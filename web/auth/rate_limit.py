"""
auth/rate_limit.py
───────────────────
登入暴力破解防護。

規則：
  同一 IP 在視窗期（WINDOW_MINUTES）內連續失敗 MAX_FAILURES 次
  → 鎖定 LOCKOUT_MINUTES 分鐘，回傳 429
"""

import logging
import threading
from collections import defaultdict
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

MAX_FAILURES     = 5    # 最多失敗次數
WINDOW_MINUTES   = 10   # 計算視窗（分鐘）
LOCKOUT_MINUTES  = 10   # 鎖定時間（分鐘）

_lock    = threading.Lock()
_records: dict[str, dict] = defaultdict(
    lambda: {"failures": [], "locked_until": None}
)


def _clean_old_failures(record: dict):
    """移除視窗期外的舊失敗紀錄。"""
    cutoff = datetime.utcnow() - timedelta(minutes=WINDOW_MINUTES)
    record["failures"] = [t for t in record["failures"] if t > cutoff]


def is_locked(ip: str) -> tuple[bool, int]:
    """
    檢查 IP 是否被鎖定。
    回傳 (is_locked, remain_seconds)
    """
    with _lock:
        rec = _records[ip]
        if rec["locked_until"] and datetime.utcnow() < rec["locked_until"]:
            remain = int((rec["locked_until"] - datetime.utcnow()).total_seconds())
            return True, remain
        return False, 0


def record_failure(ip: str):
    """記錄一次失敗，若達上限則鎖定。"""
    with _lock:
        rec = _records[ip]
        _clean_old_failures(rec)
        rec["failures"].append(datetime.utcnow())

        if len(rec["failures"]) >= MAX_FAILURES:
            rec["locked_until"] = datetime.utcnow() + timedelta(minutes=LOCKOUT_MINUTES)
            logger.warning(
                "[rate_limit] IP %s 登入失敗 %d 次，鎖定 %d 分鐘",
                ip, len(rec["failures"]), LOCKOUT_MINUTES
            )


def record_success(ip: str):
    """登入成功，清除失敗紀錄。"""
    with _lock:
        _records[ip] = {"failures": [], "locked_until": None}


def failure_count(ip: str) -> int:
    with _lock:
        rec = _records[ip]
        _clean_old_failures(rec)
        return len(rec["failures"])
