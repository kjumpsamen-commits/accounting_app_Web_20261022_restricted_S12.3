"""
utils/cache.py
──────────────
輕量 in-process TTL 快取，用於加速唯讀系統中的重複彙整查詢。

設計原則：
  - 只快取唯讀資料（統計彙整、分類清單、設定值）
  - TTL 預設 60 秒：桌面版新增資料後，Web 端最多 60 秒後即反映最新狀態
  - 不快取交易明細列表（使用者篩選組合太多、期望即時性）
  - 執行緒安全：使用 threading.Lock 保護快取讀寫
  - 不依賴任何外部套件（無 Redis / Memcached）
"""

import threading
import time
from typing import Any, Callable, TypeVar

T = TypeVar("T")

_cache: dict[str, tuple[Any, float]] = {}
_lock  = threading.Lock()


def get_cached(key: str, ttl: float = 60.0) -> Any | None:
    """
    取得快取值。若不存在或已過期，回傳 None。
    ttl: 存活秒數（預設 60 秒）
    """
    with _lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() > expires_at:
            del _cache[key]
            return None
        return value


def set_cached(key: str, value: Any, ttl: float = 60.0) -> Any:
    """存入快取並設定過期時間。回傳 value 本身方便鏈式呼叫。"""
    with _lock:
        _cache[key] = (value, time.monotonic() + ttl)
    return value


def cached(key_fn: Callable[..., str], ttl: float = 60.0):
    """
    Decorator 工廠：快取函式的回傳值。
    key_fn 接受與被裝飾函式相同的引數，回傳快取 key 字串。

    用法：
        @cached(lambda db: "stats_by_year", ttl=60)
        def get_stats_by_year(db):
            ...
    """
    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        def wrapper(*args, **kwargs):
            key = key_fn(*args, **kwargs)
            hit = get_cached(key, ttl)
            if hit is not None:
                return hit
            result = fn(*args, **kwargs)
            return set_cached(key, result, ttl)
        return wrapper
    return decorator


def invalidate(prefix: str = "") -> int:
    """
    清除符合 prefix 的所有快取 key。
    prefix="" 代表清除全部。回傳清除的 key 數量。
    """
    with _lock:
        if not prefix:
            count = len(_cache)
            _cache.clear()
            return count
        keys = [k for k in _cache if k.startswith(prefix)]
        for k in keys:
            del _cache[k]
        return len(keys)


def cache_stats() -> dict:
    """回傳目前快取狀態（供 /api/cache-stats 診斷端點使用）。"""
    with _lock:
        now = time.monotonic()
        alive = {k: v for k, v in _cache.items() if v[1] > now}
        return {
            "total_keys":  len(_cache),
            "alive_keys":  len(alive),
            "expired_keys": len(_cache) - len(alive),
        }
