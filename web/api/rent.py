"""api/rent.py — 月費 API（唯讀 GET）"""
from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Optional
from web.utils.db import get_db, ReadOnlyDB

router = APIRouter(prefix="/api/rent", tags=["API-月費"])


@router.get("/tenants", summary="人員清單")
def tenants(
    active_only: bool = Query(True),
    db: ReadOnlyDB = Depends(get_db),
):
    return db.get_tenants(active_only)


@router.get("/payments", summary="某月繳費狀況")
def payments(
    year:  int           = Query(...),
    month: int           = Query(..., ge=1, le=12),
    grade: Optional[str] = Query(None),
    db: ReadOnlyDB = Depends(get_db),
):
    return db.get_payments_for_month(year, month, grade or None)


@router.get("/earliest-year", summary="最早有記錄的年份")
def earliest_year(db: ReadOnlyDB = Depends(get_db)):
    return {"year": db.get_earliest_rent_payment_year()}
