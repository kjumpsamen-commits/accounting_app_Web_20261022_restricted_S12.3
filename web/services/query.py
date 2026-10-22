"""
services/query.py
─────────────────
各頁面共用的查詢邏輯，避免在 routes 重複寫相同程式碼。
所有函式均只呼叫 ReadOnlyDB 的 SELECT 方法。

效能優化（第九階段）：
  - get_app_title、get_cached_categories、get_cached_year_stats、get_cached_tenants
    使用 TTL 快取（60 秒）避免相同資料被重複查詢
  - 交易明細、月費明細等有篩選條件的查詢不快取，保持即時性
"""

from datetime import date, timedelta
from web.utils.db import ReadOnlyDB
from web.utils import cache as _cache


def build_filters(
    type_: str = "",
    category: str = "",
    date_from: str = "",
    date_to: str = "",
    year: str = "",
    month: str = "",
    keyword: str = "",
) -> dict:
    """
    將 URL 查詢參數轉換成 database.py 的 filters dict。
    空字串視為未指定，不加入 filters。
    """
    f: dict = {}
    if type_:     f["type"]      = type_
    if category:  f["category"]  = category
    if date_from: f["date_from"] = date_from
    if date_to:   f["date_to"]   = date_to
    if year:      f["year"]      = year
    if month:
        try:
            f["month"] = int(month)
        except ValueError:
            pass
    return f


def default_date_range() -> tuple[str, str]:
    """預設日期範圍：過去 30 天到今天。"""
    today = date.today()
    return (today - timedelta(days=30)).isoformat(), today.isoformat()


def get_app_title(db: ReadOnlyDB) -> str:
    """取得 app_title 設定值（TTL 快取 60 秒）。"""
    hit = _cache.get_cached("setting:app_title")
    if hit is not None:
        return hit
    raw = db.get_setting("app_title", "個人記帳簿").strip()
    result = raw or "個人記帳簿"
    return _cache.set_cached("setting:app_title", result, ttl=60)


def get_cached_categories(db: ReadOnlyDB, type_: str | None = None) -> list[dict]:
    """取得分類清單（TTL 快取 120 秒，分類不常變動）。"""
    key = f"categories:{type_ or 'all'}"
    hit = _cache.get_cached(key)
    if hit is not None:
        return hit
    result = db.get_categories(type_)
    return _cache.set_cached(key, result, ttl=120)


def get_cached_year_stats(db: ReadOnlyDB) -> list[dict]:
    """取得年度統計（TTL 快取 60 秒，每分鐘最多查一次）。"""
    hit = _cache.get_cached("stats:by_year")
    if hit is not None:
        return hit
    result = db.get_stats_by_year()
    return _cache.set_cached("stats:by_year", result, ttl=60)


def get_cached_tenants(db: ReadOnlyDB, active_only: bool = True) -> list[dict]:
    """取得月費人員清單（TTL 快取 60 秒）。"""
    key = f"tenants:{'active' if active_only else 'all'}"
    hit = _cache.get_cached(key)
    if hit is not None:
        return hit
    result = db.get_tenants(active_only)
    return _cache.set_cached(key, result, ttl=60)


def calc_summary(rows: list[dict]) -> dict:
    """
    從 get_transactions 回傳的 rows 計算摘要。
    避免對同樣資料再查一次 DB。
    """
    # S10：與桌面共用同一份計算——收入不含期初、支出不含交接轉出；
    # balance＝期初＋收入－支出－交接轉出（與 S10 之前的「收入－支出」數字相同）
    from database import summarize_rows
    return summarize_rows(rows)


def search_transactions(db: ReadOnlyDB, keyword: str,
                        filters: dict) -> list[dict]:
    """
    全文搜尋：從 get_transactions 結果再進行 Python 層過濾。
    搜尋範圍：category + note + voucher_no + amount 字串。
    不快取（使用者的搜尋條件組合多，保持即時性）。
    """
    rows = db.get_transactions(filters or None)
    if not keyword:
        return rows
    kw = keyword.lower()
    return [
        r for r in rows
        if kw in (r.get("category") or "").lower()
        or kw in (r.get("note") or "").lower()
        or kw in str(r.get("voucher_no") or "").lower()
        or kw in str(r.get("amount") or "")
    ]


def search_tenants(db: ReadOnlyDB, keyword: str) -> list[dict]:
    """
    搜尋月費人員：姓名 / 備註 / 年級。
    使用快取的人員清單，不額外查 DB。
    """
    if not keyword:
        return []
    kw = keyword.lower()
    all_tenants = get_cached_tenants(db, active_only=False)
    return [
        t for t in all_tenants
        if kw in (t.get("name") or "").lower()
        or kw in (t.get("note") or "").lower()
        or kw in (t.get("grade") or "").lower()
    ]


def global_search(db: ReadOnlyDB, keyword: str, date_from: str = "",
                   date_to: str = "") -> dict:
    """
    全站搜尋：同時搜尋交易紀錄與月費人員。
    回傳 {"transactions": [...], "tenants": [...]}
    """
    if not keyword.strip():
        return {"transactions": [], "tenants": []}

    filters: dict = {}
    if date_from: filters["date_from"] = date_from
    if date_to:   filters["date_to"]   = date_to

    txns    = search_transactions(db, keyword, filters)
    tenants = search_tenants(db, keyword)

    return {"transactions": txns, "tenants": tenants}
