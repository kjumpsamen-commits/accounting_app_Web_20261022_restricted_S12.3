"""routes/txn.py — 交易查詢、搜尋頁面"""
from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse
from typing import Optional
from datetime import date, timedelta
from urllib.parse import urlencode

from web.utils.db import get_db, ReadOnlyDB
from web.utils.templates import templates as _T
from web.services.query import (get_app_title, build_filters,
                                 search_transactions, calc_summary,
                                 get_cached_categories)

router = APIRouter(tags=["頁面"])




@router.get("/transactions", response_class=HTMLResponse)
def transactions_page(
    request:   Request,
    type:      str = "",
    category:  str = "",
    date_from: str = "",
    date_to:   str = "",
    keyword:   str = "",
    page:      int = Query(1, ge=1),
    db: ReadOnlyDB = Depends(get_db),
):
    today = date.today()
    if not date_from: date_from = (today - timedelta(days=30)).isoformat()
    if not date_to:   date_to   = today.isoformat()

    f    = build_filters(type, category, date_from, date_to)
    rows = search_transactions(db, keyword, f)
    cats = get_cached_categories(db)
    smry = calc_summary(rows)

    # 累計結餘：所有時間、不限區間，套用相同的 type/category 篩選
    cumulative_filters = {}
    if type:     cumulative_filters["type"]     = type
    if category: cumulative_filters["category"] = category
    cumulative_summary = db.get_summary(cumulative_filters if cumulative_filters else None)
    cumulative_balance = cumulative_summary["balance"]   # S10：含期初、扣交接轉出

    # 分頁
    per_page = 50
    total    = len(rows)
    pages    = max(1, (total + per_page - 1) // per_page)
    page     = min(page, pages)
    paged    = rows[(page - 1) * per_page : page * per_page]

    return _T.TemplateResponse(request, "transactions.html", {
        "page":      "transactions",
        "app_title": get_app_title(db),
        "rows":      paged,
        "cats":      cats,
        "summary":   smry,
        "cumulative_balance": cumulative_balance,
        "f_type":    type,
        "f_category": category,
        "f_date_from": date_from,
        "f_date_to":   date_to,
        "f_keyword":   keyword,
        "cur_page":  page,
        "total_pages": pages,
        "total_count": total,
        "querystring": urlencode({
            "type": type, "category": category,
            "date_from": date_from, "date_to": date_to, "keyword": keyword,
        }),
    })


@router.get("/income", response_class=HTMLResponse)
def income_page(
    request:   Request,
    category:  str = "",
    date_from: str = "",
    date_to:   str = "",
    keyword:   str = "",
    page:      int = Query(1, ge=1),
    db: ReadOnlyDB = Depends(get_db),
):
    today = date.today()
    if not date_from: date_from = (today - timedelta(days=30)).isoformat()
    if not date_to:   date_to   = today.isoformat()

    f    = build_filters("income", category, date_from, date_to)
    rows = search_transactions(db, keyword, f)
    cats = get_cached_categories(db, "income")
    smry = calc_summary(rows)

    per_page = 50
    total    = len(rows)
    pages    = max(1, (total + per_page - 1) // per_page)
    page     = min(page, pages)
    paged    = rows[(page - 1) * per_page : page * per_page]

    return _T.TemplateResponse(request, "income.html", {
        "page":      "income",
        "app_title": get_app_title(db),
        "rows":      paged,
        "cats":      cats,
        "summary":   smry,
        "f_category": category,
        "f_date_from": date_from,
        "f_date_to":   date_to,
        "f_keyword":   keyword,
        "cur_page":  page,
        "total_pages": pages,
        "total_count": total,
        "querystring": urlencode({
            "category": category,
            "date_from": date_from, "date_to": date_to, "keyword": keyword,
        }),
    })


@router.get("/expense", response_class=HTMLResponse)
def expense_page(
    request:   Request,
    category:  str = "",
    date_from: str = "",
    date_to:   str = "",
    keyword:   str = "",
    page:      int = Query(1, ge=1),
    db: ReadOnlyDB = Depends(get_db),
):
    today = date.today()
    if not date_from: date_from = (today - timedelta(days=30)).isoformat()
    if not date_to:   date_to   = today.isoformat()

    f    = build_filters("expense", category, date_from, date_to)
    rows = search_transactions(db, keyword, f)
    cats = get_cached_categories(db, "expense")
    smry = calc_summary(rows)

    per_page = 50
    total    = len(rows)
    pages    = max(1, (total + per_page - 1) // per_page)
    page     = min(page, pages)
    paged    = rows[(page - 1) * per_page : page * per_page]

    return _T.TemplateResponse(request, "expense.html", {
        "page":      "expense",
        "app_title": get_app_title(db),
        "rows":      paged,
        "cats":      cats,
        "summary":   smry,
        "f_category": category,
        "f_date_from": date_from,
        "f_date_to":   date_to,
        "f_keyword":   keyword,
        "cur_page":  page,
        "total_pages": pages,
        "total_count": total,
        "querystring": urlencode({
            "category": category,
            "date_from": date_from, "date_to": date_to, "keyword": keyword,
        }),
    })
