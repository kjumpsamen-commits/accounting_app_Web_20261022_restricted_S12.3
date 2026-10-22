"""routes/dashboard.py — 首頁 Dashboard"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from datetime import date

from web.utils.db import get_db, ReadOnlyDB
from web.utils.templates import templates as _T
from web.services.query import get_app_title

router = APIRouter(tags=["頁面"])


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: ReadOnlyDB = Depends(get_db)):

    today  = date.today()
    yr     = str(today.year)
    mo     = today.month

    summary       = db.get_summary()
    month_summary = db.get_summary({"year": yr, "month": mo})
    year_stats    = db.get_stats_by_year()[:5]   # 最近 5 年
    month_stats   = db.get_stats_by_month(yr)[:6] # 本年最近 6 月
    expense_cats  = db.get_stats_by_category("expense", {"year": yr})[:6]
    income_cats   = db.get_stats_by_category("income",  {"year": yr})[:5]
    recent_txns   = db.get_transactions({"date_from": f"{yr}-01-01",
                                          "date_to": today.isoformat()})[:10]

    # 本月月費繳費概況（用於 Dashboard 小卡）
    rent_payments = db.get_payments_for_month(today.year, today.month)
    rent_total   = sum(p["monthly_rent"] for p in rent_payments if p["status"] != "exempt")
    rent_paid    = sum(p["monthly_rent"] for p in rent_payments if p["status"] == "paid")
    rent_pending_cnt = sum(1 for p in rent_payments if p["status"] == "pending")

    # 折線圖／長條圖用的反序資料（由舊到新）
    month_stats_chart = list(reversed(month_stats))
    expense_cats_total = sum(v for _, v in expense_cats) or 1
    income_cats_total  = sum(v for _, v in income_cats) or 1

    return _T.TemplateResponse(request, "dashboard.html", {
        "page":          "dashboard",
        "app_title":     get_app_title(db),
        "today":         today.strftime("%Y年%m月%d日"),
        "summary":       summary,
        "month_summary": month_summary,
        "year_stats":    year_stats,
        "month_stats":   month_stats,
        "month_stats_chart": month_stats_chart,
        "expense_cats":  expense_cats,
        "income_cats":   income_cats,
        "expense_cats_total": expense_cats_total,
        "income_cats_total":  income_cats_total,
        "recent_txns":   recent_txns,
        "current_year":  today.year,
        "current_month": today.month,
        "rent_total":    rent_total,
        "rent_paid":     rent_paid,
        "rent_unpaid":   rent_total - rent_paid,
        "rent_pending_cnt": rent_pending_cnt,
        "rent_tenant_cnt":  len(rent_payments),
    })
