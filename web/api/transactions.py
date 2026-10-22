"""api/transactions.py — 交易記帳 API（唯讀 GET）"""
from fastapi import APIRouter, Depends, Query
from typing import Optional
from web.utils.db import get_db, ReadOnlyDB

router = APIRouter(prefix="/api/transactions", tags=["API-交易"])


@router.get("", summary="查詢收支清單")
def list_transactions(
    type:      Optional[str] = Query(None, description="income | expense"),
    category:  Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD"),
    date_to:   Optional[str] = Query(None, description="YYYY-MM-DD"),
    year:      Optional[str] = Query(None),
    month:     Optional[int] = Query(None, ge=1, le=12),
    db: ReadOnlyDB = Depends(get_db),
):
    f = {}
    if type:      f["type"]      = type
    if category:  f["category"]  = category
    if date_from: f["date_from"] = date_from
    if date_to:   f["date_to"]   = date_to
    if year:      f["year"]      = year
    if month:     f["month"]     = month
    return db.get_transactions(f or None)


@router.get("/summary", summary="收支加總摘要")
def summary(
    type:      Optional[str] = Query(None),
    category:  Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to:   Optional[str] = Query(None),
    year:      Optional[str] = Query(None),
    month:     Optional[int] = Query(None, ge=1, le=12),
    db: ReadOnlyDB = Depends(get_db),
):
    f = {}
    if type:      f["type"]      = type
    if category:  f["category"]  = category
    if date_from: f["date_from"] = date_from
    if date_to:   f["date_to"]   = date_to
    if year:      f["year"]      = year
    if month:     f["month"]     = month
    return db.get_summary(f or None)


@router.get("/deletions", summary="刪除紀錄（稽核軌跡）")
def deletions(
    type:      Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to:   Optional[str] = Query(None),
    db: ReadOnlyDB = Depends(get_db),
):
    f = {}
    if type:      f["type"]      = type
    if date_from: f["date_from"] = date_from
    if date_to:   f["date_to"]   = date_to
    return db.get_deletions(f or None)


@router.get("/balance", summary="指定日期前累積餘額")
def balance(
    date:     str           = Query(..., description="YYYY-MM-DD"),
    type:     Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    db: ReadOnlyDB = Depends(get_db),
):
    return {"balance": db.get_balance_before(date, type or None, category or None)}
