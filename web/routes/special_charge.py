"""
routes/special_charge.py — 特殊收費查詢頁面（唯讀）

依裁定：直接重用桌面版 `special_charge.SpecialChargeManager`（唯讀查詢
方法），不重寫 SQL、不新增 Repository/Service/Domain/Adapter。
本檔案只呼叫 Manager 的查詢方法（list_activities/get_activity/
list_members/list_items/get_activity_total_receivable），完全不呼叫任何
寫入方法（create_activity/add_member/mark_paid/delete_activity/
update_item 等）——這點由本次新增的測試靜態掃描驗證。

`SpecialChargeManager` 用 `ensure_schema=False` 建構，避免唯讀連線對
資料庫送出任何 DDL（即使是 idempotent 的 CREATE TABLE IF NOT EXISTS）。
"""
from datetime import date

from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from urllib.parse import quote
import io

from web.utils.db import get_db, ReadOnlyDB
from web.utils.templates import templates as _T
from web.services.query import get_app_title

from fastapi import HTTPException
from special_charge import SpecialChargeManager

router = APIRouter(tags=["頁面"])

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _get_manager(db: ReadOnlyDB) -> SpecialChargeManager:
    """★ 唯讀重用：ensure_schema=False，不對連線送出任何 DDL。"""
    return SpecialChargeManager(db._db.conn, ensure_schema=False)


@router.get("/special-charge", response_class=HTMLResponse)
def special_charge_page(
    request: Request,
    year: str = Query(""),
    activity_id: int = Query(0),
    db: ReadOnlyDB = Depends(get_db),
):
    manager = _get_manager(db)
    today = date.today()

    years = manager.list_available_years()
    selected_year = year or (years[0] if years else str(today.year))

    activities = manager.list_activities(year=selected_year or None)

    selected_activity = None
    members = []
    total_receivable = 0
    total_collected = 0
    if activity_id:
        try:
            selected_activity = manager.get_activity(activity_id)
        except KeyError:
            selected_activity = None
        if selected_activity:
            members = manager.list_members(activity_id)
            # ★ 修正：先前只有 Excel「個人明細」有排序，Web 查詢明細頁面
            # 沒有套用同一套排序邏輯——這裡改成呼叫共用的
            # `sort_members_by_grade()`，兩處排序邏輯統一來源，不再各自
            #維護一份可能不一致的排序 key。
            members = manager.sort_members_by_grade(members)
            for m in members:
                m["item_list"] = manager.list_items(m["id"])
                m["final_amount"] = m["receivable"]  # 目前無折扣/免收概念，維持一致命名供樣板使用

            # ★ 新增：已收/應收統計（依你的規則：已收 = 所有 paid=1 的
            # paid_amount 加總；應收 = 所有人員所有 item 的 unit_price×
            # quantity 加總，即 m["receivable"] 的加總——不新增任何 DB
            # 欄位，完全沿用既有 Manager 的計算方式）
            total_receivable = sum(m["receivable"] for m in members)
            total_collected = sum(
                (m["paid_amount"] or 0) for m in members if m["paid"]
            )

    return _T.TemplateResponse(request, "special_charge.html", {
        "page":              "special_charge",
        "app_title":         get_app_title(db),
        "years":             years,
        "selected_year":     selected_year,
        "activities":        activities,
        "activity_id":       activity_id,
        "selected_activity": selected_activity,
        "members":           members,
        "total_receivable":  total_receivable,
        "total_collected":   total_collected,
    })


@router.get("/special-charge/export.xlsx", summary="下載特殊收費活動 Excel")
def download_special_charge_excel(
    activity_id: int = Query(...),
    db: ReadOnlyDB = Depends(get_db),
):
    manager = _get_manager(db)
    try:
        activity = manager.get_activity(activity_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="活動不存在")
    data = manager.export_excel(activity_id)  # path=None → 回傳 bytes，不落地寫檔

    today_str = date.today().isoformat()
    filename_zh = f"特殊收費_{activity['name']}_{today_str}.xlsx"
    filename_ascii = f"special_charge_{activity_id}_{today_str}.xlsx"
    encoded = quote(filename_zh, safe="", encoding="utf-8")
    cd = f"attachment; filename=\"{filename_ascii}\"; filename*=UTF-8''{encoded}"

    return StreamingResponse(
        io.BytesIO(data),
        media_type=_XLSX_MIME,
        headers={"Content-Disposition": cd},
    )
