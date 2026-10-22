"""routes/stats.py — 統計分析頁面（年度／月份／分類統計）"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from datetime import date

from web.utils.db import get_db, ReadOnlyDB
from web.utils.templates import templates as _T
from web.services.query import get_app_title

router = APIRouter(tags=["頁面"])




@router.get("/statistics", response_class=HTMLResponse)
def statistics_page(
    request: Request,
    year:  str = "",
    month: str = "",
    db: ReadOnlyDB = Depends(get_db),
):
    today = date.today()
    sel_year  = year  or str(today.year)
    sel_month = month or ""

    filters: dict = {"year": sel_year}
    if sel_month:
        filters["month"] = int(sel_month)

    summary      = db.get_summary(filters)
    year_stats   = db.get_stats_by_year()
    month_stats  = db.get_stats_by_month(sel_year)
    expense_cats = db.get_stats_by_category("expense", filters)
    income_cats  = db.get_stats_by_category("income",  filters)

    # 若有選擇月份，額外提供該月每日統計（對齊桌面版 statistics_view.py 的「每日」分頁）
    day_stats = []
    if sel_month:
        day_stats = db.get_stats_by_day(sel_year, int(sel_month))

    exp_total = sum(v for _, v in expense_cats) or 1
    inc_total = sum(v for _, v in income_cats) or 1

    # 年度統計圖表用：由舊到新排序
    year_stats_chart = list(reversed(year_stats[:10]))
    month_stats_chart = list(reversed(month_stats[:12]))
    day_stats_chart   = list(reversed(day_stats))

    years = sorted({int(r["year"]) for r in year_stats} | {today.year}, reverse=True)
    months = [str(m) for m in range(1, 13)]

    return _T.TemplateResponse(request, "statistics.html", {
        "page":         "statistics",
        "app_title":    get_app_title(db),
        "summary":      summary,
        "year_stats":   year_stats[:10],
        "month_stats":  month_stats[:12],
        "day_stats":    day_stats,
        "year_stats_chart":  year_stats_chart,
        "month_stats_chart": month_stats_chart,
        "day_stats_chart":   day_stats_chart,
        "expense_cats": expense_cats[:12],
        "income_cats":  income_cats[:12],
        "exp_total":    exp_total,
        "inc_total":    inc_total,
        "sel_year":     sel_year,
        "sel_month":    sel_month,
        "years":        years,
        "months":       months,
    })
