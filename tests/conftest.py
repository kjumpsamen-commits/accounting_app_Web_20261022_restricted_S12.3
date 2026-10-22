"""
tests/conftest.py — Web 測試基礎設施。

★ 本專案（Web 子專案）原本完全沒有任何既有測試（Architecture Discovery
階段已列為 Finding）。這是第一份測試基礎設施，刻意保持最小：一個 fixture
建立測試用 sqlite 資料庫（沿用桌面版 `Database()` 建表 + 特殊收費 3 張表），
用 `app.dependency_overrides` 覆寫 `get_db`，不去碰 Google Drive 同步或
版本號快取那套機制（測試不需要，也不應該依賴外部服務）。
"""
import os
os.environ.setdefault("ADMIN_USERNAME", "")  # 避免 lifespan 對 admin 設定的警告噪音
# S12.2：既有測試會直接開完整版頁面（/、/rent…）測共用功能，所以預設關閉「整站受限」；
# 整站受限（部署時的預設）由 tests/test_s122_restricted_site.py 打開測試。
os.environ["RESTRICTED_SITE"] = "false"

import sqlite3
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def desktop_db(tmp_path):
    """建立一個完整的測試資料庫（桌面版全部既有表 + 特殊收費 3 張表），
    回傳真正的桌面版 `Database` 實例（保留其全部既有查詢方法，例如
    `get_transactions`／`get_tenants`），不是只有一個 `.conn` 的空殼——
    這樣既有頁面（dashboard/txn/rent 等）呼叫 `db._db.get_xxx()` 才能
    正常運作，不會因為 fake 物件缺方法而報錯。"""
    import database as database_module
    db_path = tmp_path / "test_accounting.db"
    database_module.DB_PATH = db_path
    d = database_module.Database()

    d.conn.execute(
        "INSERT INTO rent_tenants (name, monthly_rent, grade, active, created_at) VALUES (?, ?, ?, ?, ?)",
        ("王小明", 3000, "六年級", 1, "2026-01-01T00:00:00"),
    )
    d.conn.execute(
        "INSERT INTO rent_tenants (name, monthly_rent, grade, active, created_at) VALUES (?, ?, ?, ?, ?)",
        ("李小華", 2500, "五年級", 1, "2026-01-01T00:00:00"),
    )
    d.conn.commit()
    d.add_transaction("income", 5000, "薪資", "測試收入", "2026-07-01")

    from special_charge import SpecialChargeManager
    manager = SpecialChargeManager(d.conn)  # 測試資料建置用，ensure_schema 預設 True 沒關係
    activity_id = manager.create_activity("校外教學", "2026-05-10")
    manager.load_active_tenants_as_members(activity_id, [1, 2])
    members = manager.list_members(activity_id)
    manager.add_item(members[0]["id"], "車資", 700, 3)
    manager.add_item(members[0]["id"], "餐費", 800, 2)
    manager.mark_paid(members[0]["id"], paid_date="2026-05-08")

    return d


@pytest.fixture
def client(desktop_db, monkeypatch):
    """建立一個 FastAPI TestClient，覆寫 get_db 依賴指向測試連線
    （不使用真正的 Google Drive 同步/版本號快取機制）。"""
    from web.app import app
    from web.utils.db import get_db, ReadOnlyDB

    def override_get_db():
        db = ReadOnlyDB.__new__(ReadOnlyDB)
        db._db = desktop_db  # 真正的桌面版 Database 實例，保留全部既有方法
        yield db

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()
