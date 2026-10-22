"""routes/rent.py — 月費（租屋）查詢頁面（唯讀）"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from datetime import date

from web.utils.db import get_db, ReadOnlyDB
from web.utils.templates import templates as _T
from web.services.query import get_app_title

router = APIRouter(tags=["頁面"])

_GRADES = ["一年級", "二年級", "三年級", "四年級", "五年級", "六年級"]




@router.get("/rent", response_class=HTMLResponse)
def rent_page(
    request: Request,
    year:  int = 0,
    month: int = 0,
    grade: str = "",
    db: ReadOnlyDB = Depends(get_db),
):
    today = date.today()
    if year == 0:  year  = today.year
    if month == 0: month = today.month

    payments = db.get_payments_for_month(year, month, grade or None)

    total   = sum(p["monthly_rent"] for p in payments if p["status"] != "exempt")
    paid    = sum(p["monthly_rent"] for p in payments if p["status"] == "paid")
    pending_cnt = sum(1 for p in payments if p["status"] == "pending")
    exempt_cnt  = sum(1 for p in payments if p["status"] == "exempt")
    paid_cnt    = sum(1 for p in payments if p["status"] == "paid")

    earliest = db.get_earliest_rent_payment_year() or today.year
    years = list(range(today.year + 1, earliest - 1, -1))

    return _T.TemplateResponse(request, "rent.html", {
        "page":       "rent",
        "app_title":  get_app_title(db),
        "payments":   payments,
        "year":       year,
        "month":      month,
        "grade":      grade,
        "grades":     _GRADES,
        "total":      total,
        "paid":       paid,
        "unpaid":     total - paid,
        "paid_cnt":   paid_cnt,
        "pending_cnt": pending_cnt,
        "exempt_cnt": exempt_cnt,
        "years":      years,
    })


@router.get("/tenants", response_class=HTMLResponse)
def tenants_page(request: Request, db: ReadOnlyDB = Depends(get_db)):
    all_tenants = db.get_tenants(active_only=False)
    active   = [t for t in all_tenants if t["active"]]
    inactive = [t for t in all_tenants if not t["active"]]

    by_grade: dict[str, list] = {}
    for t in active:
        by_grade.setdefault(t.get("grade") or "未分級", []).append(t)

    return _T.TemplateResponse(request, "tenants.html", {
        "page":      "tenants",
        "app_title": get_app_title(db),
        "active":    active,
        "inactive":  inactive,
        "by_grade":  by_grade,
        "total_monthly": sum(t["monthly_rent"] for t in active),
    })
