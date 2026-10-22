"""
routes/export_.py
──────────────────
Excel 匯出頁面與下載端點。

注意：檔名加底線 export_ 是為了避免與 Python 內建概念衝突，
與桌面版 export_view.py 完全獨立，不互相 import。
所有匯出僅讀取資料（SELECT），不寫入資料庫，也不在伺服器留檔，
直接以 StreamingResponse 串流回傳給瀏覽器下載。
"""
from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from datetime import date
from urllib.parse import quote
import io

from web.utils.db import get_db, ReadOnlyDB
from web.utils.templates import templates as _T
from web.services.query import get_app_title
from web.services.export_svc import export_transactions_xlsx, export_rent_xlsx

router = APIRouter(tags=["頁面"])

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx_response(data: bytes, filename_zh: str, filename_ascii: str) -> StreamingResponse:
    """
    回傳 xlsx StreamingResponse。
    Content-Disposition 使用 ASCII 備用名 + RFC 5987 UTF-8 編碼的中文名，
    確保 Starlette / latin-1 header 限制與瀏覽器相容性。
    """
    encoded = quote(filename_zh, safe="", encoding="utf-8")
    cd = f"attachment; filename=\"{filename_ascii}\"; filename*=UTF-8''{encoded}"
    return StreamingResponse(
        io.BytesIO(data),
        media_type=_XLSX_MIME,
        headers={"Content-Disposition": cd},
    )




@router.get("/export", response_class=HTMLResponse)
def export_page(request: Request, db: ReadOnlyDB = Depends(get_db)):
    today = date.today()
    earliest = db.get_earliest_rent_payment_year() or today.year
    years = list(range(today.year + 1, earliest - 1, -1))

    return _T.TemplateResponse(request, "export.html", {
        "page":      "export",
        "app_title": get_app_title(db),
        "today":     today.isoformat(),
        "years":     years,
        "cur_year":  today.year,
        "cur_month": today.month,
    })


@router.get("/export/transactions.xlsx", summary="下載收支明細 Excel")
def download_transactions(
    date_from: str = Query(""),
    date_to:   str = Query(""),
    type:      str = Query("all"),
    year_filter: str = Query(""),
    include_detail:   bool = Query(True),
    include_year:     bool = Query(True),
    include_month:    bool = Query(True),
    include_category: bool = Query(True),
    db: ReadOnlyDB = Depends(get_db),
):
    data = export_transactions_xlsx(
        db, date_from, date_to, type, year_filter,
        include_detail, include_year, include_month, include_category,
    )
    today_str = date.today().isoformat()
    return _xlsx_response(data,
                          f"收支明細_{today_str}.xlsx",
                          f"transactions_{today_str}.xlsx")


@router.get("/export/rent.xlsx", summary="下載月費繳交明細 Excel")
def download_rent(
    start_year:  int = Query(...),
    start_month: int = Query(..., ge=1, le=12),
    end_year:    int = Query(...),
    end_month:   int = Query(..., ge=1, le=12),
    db: ReadOnlyDB = Depends(get_db),
):
    data = export_rent_xlsx(db, start_year, start_month, end_year, end_month)
    today_str = date.today().isoformat()
    return _xlsx_response(data,
                          f"月費明細_{today_str}.xlsx",
                          f"rent_{today_str}.xlsx")
