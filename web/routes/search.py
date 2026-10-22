"""routes/search.py — 全站搜尋頁面（交易紀錄 ＋ 月費人員，唯讀）"""
from fastapi import APIRouter, Depends, Request

from web.utils.db import get_db, ReadOnlyDB
from web.utils.templates import templates as _T
from web.services.query import get_app_title, global_search, calc_summary

router = APIRouter(tags=["頁面"])


@router.get("/search")
def search_page(
    request: Request,
    q:         str = "",
    date_from: str = "",
    date_to:   str = "",
    db: ReadOnlyDB = Depends(get_db),
):
    keyword = q.strip()
    result  = global_search(db, keyword, date_from, date_to) if keyword else {"transactions": [], "tenants": []}

    txns    = result["transactions"]
    tenants = result["tenants"]
    summary = calc_summary(txns) if txns else None

    return _T.TemplateResponse(request, "search.html", {
        "page":       "search",
        "app_title":  get_app_title(db),
        "q":          keyword,
        "txns":       txns,
        "tenants":    tenants,
        "summary":    summary,
        "date_from":  date_from,
        "date_to":    date_to,
        "has_searched": bool(keyword),
    })
