"""api/settings.py — 設定 API（唯讀 GET）"""
from fastapi import APIRouter, Depends
from web.utils.db import get_db, ReadOnlyDB

router = APIRouter(prefix="/api/settings", tags=["API-設定"])


@router.get("/{key}", summary="讀取設定值")
def get_setting(key: str, db: ReadOnlyDB = Depends(get_db)):
    return {"key": key, "value": db.get_setting(key)}
