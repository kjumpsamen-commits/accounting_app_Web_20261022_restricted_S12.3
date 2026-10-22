"""api/categories.py — 分類 API（唯讀 GET）"""
from fastapi import APIRouter, Depends, Query
from typing import Optional
from web.utils.db import get_db, ReadOnlyDB

router = APIRouter(prefix="/api/categories", tags=["API-分類"])


@router.get("", summary="分類清單")
def list_categories(
    type: Optional[str] = Query(None, description="income | expense | 不帶=全部"),
    db: ReadOnlyDB = Depends(get_db),
):
    return db.get_categories(type or None)
