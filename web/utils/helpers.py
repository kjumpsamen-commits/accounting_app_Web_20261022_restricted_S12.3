"""
utils/helpers.py
────────────────
Jinja2 自訂過濾器與格式化工具函式。
"""

from datetime import date as _date


def fmt_money(value) -> str:
    """數字格式化為台幣千位數，例如 12345 → NT$12,345"""
    try:
        return f"NT${float(value):,.0f}"
    except (TypeError, ValueError):
        return "NT$0"


def fmt_money_signed(value) -> str:
    """帶正負號的台幣，例如 +12,345 / -3,456"""
    try:
        v = float(value)
        sign = "+" if v >= 0 else ""
        return f"{sign}NT${v:,.0f}"
    except (TypeError, ValueError):
        return "NT$0"


def fmt_date(value: str) -> str:
    """2024-03-15 → 2024/03/15"""
    if not value:
        return "—"
    return value.replace("-", "/")


def type_label(type_str: str) -> str:
    """'income' → '收入'  /  'expense' → '支出'"""
    return {"income": "收入", "expense": "支出"}.get(type_str, type_str)


def type_badge(type_str: str) -> str:
    """回傳 Bootstrap badge HTML"""
    if type_str == "income":
        return '<span class="badge bg-success">收入</span>'
    elif type_str == "expense":
        return '<span class="badge bg-danger">支出</span>'
    return f'<span class="badge bg-secondary">{type_str}</span>'


def status_badge(status: str) -> str:
    """月費繳費狀態 badge"""
    mapping = {
        "paid":    ('<span class="badge bg-success">✅ 已繳納</span>'),
        "pending": ('<span class="badge bg-warning text-dark">⏳ 未繳納</span>'),
        "exempt":  ('<span class="badge bg-secondary">➖ 免繳</span>'),
    }
    return mapping.get(status, f'<span class="badge bg-light text-dark">{status}</span>')


def pct(part, total) -> str:
    """計算百分比字串，例如 '34.5%'"""
    try:
        if float(total) == 0:
            return "0%"
        return f"{float(part) / float(total) * 100:.1f}%"
    except (TypeError, ValueError, ZeroDivisionError):
        return "0%"


def register_filters(app_templates):
    """將所有自訂過濾器掛載到 Jinja2 Templates 實例。"""
    app_templates.env.filters["money"]        = fmt_money
    app_templates.env.filters["money_signed"] = fmt_money_signed
    app_templates.env.filters["fdate"]        = fmt_date
    app_templates.env.filters["type_label"]   = type_label
    app_templates.env.filters["type_badge"]   = type_badge
    app_templates.env.filters["status_badge"] = status_badge
    app_templates.env.filters["pct"]          = pct
    app_templates.env.globals["current_year"] = _date.today().year
    app_templates.env.globals["last_saved_at"] = last_saved_at   # S9：頁尾顯示資料最後更新時間
    import version
    app_templates.env.globals["app_version"] = version.version_text()   # S12：頁尾版本
    from web import edition                                            # S12.3：完整版／簡易版
    app_templates.env.globals["app_edition_name"] = edition.EDITION_NAME
    app_templates.env.globals["app_edition"] = edition


def last_saved_at() -> str:
    """S9：桌面版最後存檔時間（桌面關閉程式時寫入 app_settings.last_saved_at）。
    讀不到（舊資料庫、資料庫尚未下載）時回傳「—」，不影響頁面顯示。"""
    # 只用唯讀連線讀一個值：不經過 _get_fresh_db()（找不到路徑時會改用桌面版
    # Database()，那會執行升級而寫入資料庫），確保 Web 絕不寫入。
    import sqlite3
    from pathlib import Path
    try:
        from web.services.database.manager import get_active_path
        path = Path(get_active_path())
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key='last_saved_at'").fetchone()
        finally:
            conn.close()
        return (row[0] if row and row[0] else "") or "—"
    except Exception:
        return "—"
