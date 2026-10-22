"""api/statistics.py — 統計 API（唯讀 GET）"""
from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Optional
from web.utils.db import get_db, ReadOnlyDB

router = APIRouter(prefix="/api/stats", tags=["API-統計"])


@router.get("/by-year", summary="年度統計")
def by_year(db: ReadOnlyDB = Depends(get_db)):
    return db.get_stats_by_year()


@router.get("/by-month", summary="月份統計")
def by_month(
    year: Optional[str] = Query(None),
    db: ReadOnlyDB = Depends(get_db),
):
    return db.get_stats_by_month(year)


@router.get("/by-day", summary="每日統計")
def by_day(
    year:  Optional[str] = Query(None),
    month: Optional[int] = Query(None, ge=1, le=12),
    db: ReadOnlyDB = Depends(get_db),
):
    return db.get_stats_by_day(year, month)


@router.get("/by-category", summary="分類統計")
def by_category(
    type:      str           = Query(..., description="income | expense"),
    date_from: Optional[str] = Query(None),
    date_to:   Optional[str] = Query(None),
    year:      Optional[str] = Query(None),
    month:     Optional[int] = Query(None, ge=1, le=12),
    db: ReadOnlyDB = Depends(get_db),
):
    if type not in ("income", "expense"):
        raise HTTPException(400, "type 須為 income 或 expense")
    f = {}
    if date_from: f["date_from"] = date_from
    if date_to:   f["date_to"]   = date_to
    if year:      f["year"]      = year
    if month:     f["month"]     = month
    rows = db.get_stats_by_category(type, f or None)
    return [{"category": c, "amount": a} for c, a in rows]
