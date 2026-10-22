"""
月費修正 S4（Web）— Web 只有一項可見變更：月費 Excel 免繳格斜線改為「/」。
（S4 新增的 plan_fee_payment／pay_fee_months 為桌面專用寫入功能，Web 不呼叫。）
"""
import io
import os
os.environ.setdefault("ADMIN_USERNAME", "")

import openpyxl
from fastapi.testclient import TestClient


def test_w1_exempt_cell_slash_and_web_has_no_write_calls(tmp_path):
    import database as database_module
    database_module.DB_PATH = tmp_path / "data.db"
    d = database_module.Database()
    d.add_fee_schedule(202607, 3000)
    tid = d.add_tenant("免繳者", None, grade="一年級", start_ym=202607)
    d.set_payment_status(tid, 2026, 7, "exempt")
    d.set_payment_status(tid, 2026, 8, "paid", paid_date="2026-08-01")

    from web.app import app
    from web.utils.db import get_db, ReadOnlyDB, _build_connection
    conn_db = _build_connection(str(database_module.DB_PATH))

    def override():
        db = ReadOnlyDB.__new__(ReadOnlyDB)
        db._db = conn_db
        yield db

    app.dependency_overrides[get_db] = override
    try:
        with TestClient(app) as c:
            r = c.get("/export/rent.xlsx?start_year=2026&start_month=7&end_year=2026&end_month=8")
        assert r.status_code == 200
        wb = openpyxl.load_workbook(io.BytesIO(r.content))
        for ws in wb.worksheets[:2]:
            cell = ws.cell(4, 4)
            assert cell.border.diagonalUp is True and cell.border.diagonalDown is False
    finally:
        app.dependency_overrides.clear()

    # 靜態檢查：Web 程式碼不呼叫 S4 的寫入函式
    import pathlib
    web_dir = pathlib.Path(__file__).resolve().parent.parent / "web"
    src = "\n".join(p.read_text(encoding="utf-8") for p in web_dir.rglob("*.py"))
    for name in ("pay_fee_months", "plan_fee_payment", "set_payment_status", "_write_payment_status"):
        assert name not in src, name
