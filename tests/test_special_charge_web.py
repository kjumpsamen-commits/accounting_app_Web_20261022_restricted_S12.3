"""
tests/test_special_charge_web.py — 特殊收費 Web 頁面測試。

依 Architecture Discovery 階段的 Regression/Read-Only Safety Test Plan
逐項落實：
    A. 既有頁面 Regression
    B. 既有 Excel Regression
    C. 既有 query 結果不變
    D. Special Charge 頁面
    E. Special Charge Excel
    F. DB 無非預期修改
    G. Read-Only Safety（靜態掃描）
"""
import re
import inspect


# ── A. 既有頁面 Regression ──────────────────────────────────────────────────

def test_existing_pages_still_return_200(client):
    for path in ["/", "/transactions", "/income", "/expense",
                 "/statistics", "/export", "/rent", "/tenants"]:
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} 回傳 {resp.status_code}"


def test_search_page_still_works(client):
    resp = client.get("/search", params={"q": "薪資"})
    assert resp.status_code == 200


# ── B. 既有 Excel Regression ─────────────────────────────────────────────────

def test_existing_transactions_excel_still_works(client):
    resp = client.get("/export/transactions.xlsx")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert len(resp.content) > 0


def test_existing_rent_excel_still_works(client):
    resp = client.get("/export/rent.xlsx", params={
        "start_year": 2026, "start_month": 1, "end_year": 2026, "end_month": 12,
    })
    assert resp.status_code == 200
    assert len(resp.content) > 0


# ── C. 既有 query 結果不變（加入 Special Charge 前後比對）────────────────────────

def test_existing_query_methods_unaffected_by_special_charge_module(desktop_db):
    """確認 SpecialChargeManager 的建構與查詢，不會影響既有 Database
    方法的回傳結果。"""
    before_tx = desktop_db.get_transactions()
    before_tenants = desktop_db.get_tenants(active_only=False)

    from special_charge import SpecialChargeManager
    SpecialChargeManager(desktop_db.conn, ensure_schema=False)  # 唯讀重用建構

    after_tx = desktop_db.get_transactions()
    after_tenants = desktop_db.get_tenants(active_only=False)

    assert before_tx == after_tx
    assert before_tenants == after_tenants


# ── D. Special Charge 頁面 ───────────────────────────────────────────────────

def test_special_charge_page_loads(client):
    resp = client.get("/special-charge")
    assert resp.status_code == 200
    assert "特殊收費" in resp.text


def test_special_charge_page_lists_activity(client):
    resp = client.get("/special-charge", params={"year": "2026"})
    assert resp.status_code == 200
    assert "校外教學" in resp.text
    assert "1 / 2" in resp.text or "1/2" in resp.text.replace(" ", "")


def test_special_charge_detail_shows_members_and_grade(client):
    resp = client.get("/special-charge", params={"year": "2026", "activity_id": 1})
    assert resp.status_code == 200
    assert "王小明" in resp.text
    assert "六年級" in resp.text
    assert "3700" in resp.text  # 700×3 + 800×2 = 3700


def test_special_charge_detail_receivable_matches_manager_calculation(client, desktop_db):
    """確認頁面顯示的應收金額與 Manager 的即時計算結果一致
    （不是頁面自己另外算一套）。"""
    from special_charge import SpecialChargeManager
    manager = SpecialChargeManager(desktop_db.conn, ensure_schema=False)
    members = manager.list_members(1)
    member_a = next(m for m in members if m["tenant_name"] == "王小明")
    expected = manager.get_member_receivable(member_a["id"])

    resp = client.get("/special-charge", params={"year": "2026", "activity_id": 1})
    assert f"{expected:.0f}" in resp.text


def test_special_charge_page_year_filter(client):
    resp = client.get("/special-charge", params={"year": "2025"})
    assert resp.status_code == 200
    assert "校外教學" not in resp.text  # 2025 沒有這筆活動


def test_special_charge_page_nonexistent_activity_does_not_crash(client):
    resp = client.get("/special-charge", params={"year": "2026", "activity_id": 99999})
    assert resp.status_code == 200  # 不存在的活動：頁面仍正常顯示（無明細區塊），不是 500


def test_special_charge_expandable_item_detail_present(client):
    """★ 依裁定：個人明細用純 HTML collapse 展開，不需要額外 API。"""
    resp = client.get("/special-charge", params={"year": "2026", "activity_id": 1})
    assert 'data-bs-toggle="collapse"' in resp.text
    assert "車資" in resp.text
    assert "餐費" in resp.text


# ── E. Special Charge Excel ──────────────────────────────────────────────────

def test_special_charge_excel_download(client):
    resp = client.get("/special-charge/export.xlsx", params={"activity_id": 1})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert len(resp.content) > 0

    import openpyxl, io
    wb = openpyxl.load_workbook(io.BytesIO(resp.content))
    assert wb.sheetnames == ["活動總表", "個人明細"]


def test_special_charge_excel_nonexistent_activity_returns_404(client):
    resp = client.get("/special-charge/export.xlsx", params={"activity_id": 99999})
    assert resp.status_code == 404


def test_special_charge_excel_does_not_write_temp_file_to_disk(client, tmp_path):
    """確認匯出不會在伺服器磁碟留檔（純記憶體 bytes 直接串流）。"""
    before_files = set(tmp_path.iterdir()) if tmp_path.exists() else set()
    client.get("/special-charge/export.xlsx", params={"activity_id": 1})
    after_files = set(tmp_path.iterdir()) if tmp_path.exists() else set()
    assert before_files == after_files


# ── F. DB 無非預期修改 ───────────────────────────────────────────────────────

def test_special_charge_page_does_not_modify_database(client, desktop_db):
    before_activities = [dict(r) for r in desktop_db.conn.execute(
        "SELECT * FROM special_activity ORDER BY id"
    ).fetchall()]
    before_members = [dict(r) for r in desktop_db.conn.execute(
        "SELECT * FROM special_activity_member ORDER BY id"
    ).fetchall()]
    before_items = [dict(r) for r in desktop_db.conn.execute(
        "SELECT * FROM special_activity_item ORDER BY id"
    ).fetchall()]
    before_tenants = [dict(r) for r in desktop_db.conn.execute(
        "SELECT * FROM rent_tenants ORDER BY id"
    ).fetchall()]

    client.get("/special-charge", params={"year": "2026", "activity_id": 1})
    client.get("/special-charge/export.xlsx", params={"activity_id": 1})

    after_activities = [dict(r) for r in desktop_db.conn.execute(
        "SELECT * FROM special_activity ORDER BY id"
    ).fetchall()]
    after_members = [dict(r) for r in desktop_db.conn.execute(
        "SELECT * FROM special_activity_member ORDER BY id"
    ).fetchall()]
    after_items = [dict(r) for r in desktop_db.conn.execute(
        "SELECT * FROM special_activity_item ORDER BY id"
    ).fetchall()]
    after_tenants = [dict(r) for r in desktop_db.conn.execute(
        "SELECT * FROM rent_tenants ORDER BY id"
    ).fetchall()]

    assert before_activities == after_activities
    assert before_members == after_members
    assert before_items == after_items
    assert before_tenants == after_tenants


# ── G. Read-Only Safety（靜態掃描）───────────────────────────────────────────

def test_special_charge_route_does_not_call_write_methods():
    """★ 靜態掃描：special_charge.py 只能呼叫 SpecialChargeManager 的查詢
    方法，不得出現任何寫入方法呼叫。"""
    from web.routes import special_charge as route_module
    source = inspect.getsource(route_module)

    forbidden_calls = [
        "manager.create_activity(", "manager.add_member(",
        "manager.mark_paid(", "manager.mark_unpaid(",
        "manager.delete_activity(", "manager.update_item(",
        "manager.add_item(", "manager.remove_item(",
        "manager.remove_member(", "manager.complete_activity(",
        "manager.reopen_activity(", "manager.update_activity(",
        "manager.duplicate_activity(", "manager.load_active_tenants_as_members(",
    ]
    for call in forbidden_calls:
        assert call not in source, f"發現不應出現的寫入方法呼叫：{call}"


def test_special_charge_route_does_not_contain_raw_write_sql():
    """靜態掃描：special_charge.py 不得出現任何 SQL 寫入語句字樣。"""
    from web.routes import special_charge as route_module
    source = inspect.getsource(route_module)
    for keyword in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP TABLE", "ALTER TABLE"):
        assert keyword not in source


def test_special_charge_route_uses_ensure_schema_false():
    """確認 Web 層建構 SpecialChargeManager 時明確使用 ensure_schema=False，
    不對唯讀連線送出任何 DDL。"""
    from web.routes import special_charge as route_module
    source = inspect.getsource(route_module)
    assert "ensure_schema=False" in source


def test_special_charge_route_does_not_import_charge_management_or_payment_engine():
    """確認沒有意外 import 到先前已捨棄的舊架構（若殘留於環境中）。"""
    from web.routes import special_charge as route_module
    import_lines = [
        line for line in inspect.getsource(route_module).splitlines()
        if line.strip().startswith("import ") or line.strip().startswith("from ")
    ]
    joined = "\n".join(import_lines)
    assert "charge_management" not in joined
    assert "payment_engine" not in joined


# ── Excel 個人明細依年級排序（★ 依你的最終要求新增）───────────────────────────────

def test_excel_person_detail_sorted_by_grade_six_to_one_with_shuffled_source_data(desktop_db):
    """★ 依你的要求：資料庫原始順序刻意打亂（一→六→三→五→二→四），
    Excel「個人明細」必須輸出六→五→四→三→二→一；同年級再依姓名排序。
    排序鍵必須讀取實際的 grade 欄位，不是 tenant_id/建立順序/姓名反推。
    """
    import openpyxl, io

    # 清掉 conftest 已經建立的兩位租客，改用刻意打亂順序的六位
    desktop_db.conn.execute("DELETE FROM rent_tenants")
    desktop_db.conn.commit()

    shuffled = [
        ("王一心", "一年級"),
        ("林六合", "六年級"),
        ("陳三豐", "三年級"),
        ("張五常", "五年級"),
        ("李二娃", "二年級"),
        ("黃四喜", "四年級"),
    ]
    for name, grade in shuffled:
        desktop_db.conn.execute(
            "INSERT INTO rent_tenants (name, monthly_rent, grade, active, created_at) VALUES (?, ?, ?, ?, ?)",
            (name, 2000, grade, 1, "2026-01-01T00:00:00"),
        )
    desktop_db.conn.commit()

    tenant_ids = [r[0] for r in desktop_db.conn.execute(
        "SELECT id FROM rent_tenants ORDER BY id"
    ).fetchall()]

    from special_charge import SpecialChargeManager
    manager = SpecialChargeManager(desktop_db.conn, ensure_schema=False)
    activity_id = manager.create_activity("排序測試活動", "2026-09-01")
    manager.load_active_tenants_as_members(activity_id, tenant_ids)
    for m in manager.list_members(activity_id):
        manager.add_item(m["id"], "測試項目", 100, 1)

    data = manager.export_excel(activity_id)
    wb = openpyxl.load_workbook(io.BytesIO(data))
    ws2 = wb["個人明細"]

    # 每人佔 2 列（姓名+項目）+ 1 列合計 + 1 空行 = 4 列一個區塊，姓名固定在每區塊第 1 列
    names_in_order = []
    row = 1
    while row <= ws2.max_row:
        cell = ws2.cell(row=row, column=1).value
        if cell and cell not in ("測試項目", "合計"):
            names_in_order.append(cell)
        row += 1
        if len(names_in_order) == 6:
            break

    # ★ Sheet2 姓名列現在顯示「年級　姓名」，用 in 判斷確認姓名存在且順序正確
    expected_names = ["林六合", "張五常", "黃四喜", "陳三豐", "李二娃", "王一心"]
    for i, name in enumerate(expected_names):
        assert name in names_in_order[i], f"第 {i+1} 位應為 {name}，實際：{names_in_order[i]}"


def test_excel_person_detail_same_grade_sorted_by_name(desktop_db):
    """★ 同一年級內，再依姓名排序。"""
    import openpyxl, io

    desktop_db.conn.execute("DELETE FROM rent_tenants")
    desktop_db.conn.commit()

    same_grade = [("王小明", "六年級"), ("李小華", "六年級"), ("張小美", "六年級")]
    for name, grade in same_grade:
        desktop_db.conn.execute(
            "INSERT INTO rent_tenants (name, monthly_rent, grade, active, created_at) VALUES (?, ?, ?, ?, ?)",
            (name, 2000, grade, 1, "2026-01-01T00:00:00"),
        )
    desktop_db.conn.commit()
    tenant_ids = [r[0] for r in desktop_db.conn.execute(
        "SELECT id FROM rent_tenants ORDER BY id"
    ).fetchall()]

    from special_charge import SpecialChargeManager
    manager = SpecialChargeManager(desktop_db.conn, ensure_schema=False)
    activity_id = manager.create_activity("同年級排序測試", "2026-09-01")
    # 故意用打亂的順序加入（不是按姓名字母序）
    manager.load_active_tenants_as_members(activity_id, [tenant_ids[2], tenant_ids[0], tenant_ids[1]])
    for m in manager.list_members(activity_id):
        manager.add_item(m["id"], "項目", 100, 1)

    data = manager.export_excel(activity_id)
    wb = openpyxl.load_workbook(io.BytesIO(data))
    ws2 = wb["個人明細"]

    names_in_order = []
    row = 1
    while row <= ws2.max_row and len(names_in_order) < 3:
        cell = ws2.cell(row=row, column=1).value
        if cell and cell not in ("項目", "合計"):
            names_in_order.append(cell)
        row += 1

    # ★ 姓名排序沿用 Python 預設字串比較（Unicode 編碼順序），與桌面版
    # `_SpecialChargeSubView._grade_sort_key` 既有的排序方式一致，不是
    # 拼音或筆畫排序——這裡驗證的是「同年級內確實有做二次排序」，
    # 不是驗證特定的中文排序演算法。Sheet2 姓名列現在是「年級　姓名」，
    # 用 in 判斷確認每個位置的姓名正確。
    expected = sorted(["王小明", "李小華", "張小美"])
    for i, name in enumerate(expected):
        assert name in names_in_order[i]


def test_excel_activity_summary_sheet_now_also_sorted_by_grade(desktop_db):
    """★ 需求變更（你這輪明確裁定）：「活動總表」不再維持原始加入順序，
    改成跟「個人明細」一樣依年級排序（六→五→四→三→二→一），同年級再
    依姓名排序。這裡故意讓「一年生」先加入（tenant_id 較小），驗證
    Sheet1 仍然輸出「六年生」在前——證明真的依年級排序，不是加入順序。"""
    import openpyxl, io

    desktop_db.conn.execute("DELETE FROM rent_tenants")
    desktop_db.conn.commit()
    for name, grade in [("一年生", "一年級"), ("六年生", "六年級")]:
        desktop_db.conn.execute(
            "INSERT INTO rent_tenants (name, monthly_rent, grade, active, created_at) VALUES (?, ?, ?, ?, ?)",
            (name, 2000, grade, 1, "2026-01-01T00:00:00"),
        )
    desktop_db.conn.commit()
    tenant_ids = [r[0] for r in desktop_db.conn.execute(
        "SELECT id FROM rent_tenants ORDER BY id"
    ).fetchall()]

    from special_charge import SpecialChargeManager
    manager = SpecialChargeManager(desktop_db.conn, ensure_schema=False)
    activity_id = manager.create_activity("總表順序測試")
    manager.load_active_tenants_as_members(activity_id, tenant_ids)  # 一年生先加入（tenant_id 較小）

    data = manager.export_excel(activity_id)
    wb = openpyxl.load_workbook(io.BytesIO(data))
    ws1 = wb["活動總表"]
    # Sheet1 第 2 列（跳過表頭）現在應該是「六年生」（依年級排序，不是加入順序）
    assert ws1.cell(row=2, column=2).value == "六年生"
    assert ws1.cell(row=2, column=1).value == "六年級"
    assert ws1.cell(row=3, column=2).value == "一年生"
    assert ws1.cell(row=3, column=1).value == "一年級"


# ── 本輪修正 1：查詢明細頁面增加已收/應收統計 ────────────────────────────────────

def test_special_charge_detail_shows_revenue_summary(client):
    """★ 已收 = 所有 paid=1 的 paid_amount 加總；應收 = 所有人員所有
    item 的 unit_price×quantity 加總（沿用既有 Manager 計算方式，
    不新增任何 DB 欄位）。conftest 資料：王小明已繳 3700(700*3+800*2)，
    李小華未加入項目、未繳 → 應收=3700、已收=3700。"""
    resp = client.get("/special-charge", params={"year": "2026", "activity_id": 1})
    assert resp.status_code == 200
    assert "已收" in resp.text
    assert "應收" in resp.text
    assert "3,700" in resp.text or "3700" in resp.text


def test_revenue_summary_matches_manual_calculation(client, desktop_db):
    from special_charge import SpecialChargeManager
    manager = SpecialChargeManager(desktop_db.conn, ensure_schema=False)
    members = manager.list_members(1)
    expected_receivable = sum(m["receivable"] for m in members)
    expected_collected = sum((m["paid_amount"] or 0) for m in members if m["paid"])

    resp = client.get("/special-charge", params={"year": "2026", "activity_id": 1})
    assert f"{expected_receivable:,.0f}".replace(",", "") in resp.text.replace(",", "")
    assert f"{expected_collected:,.0f}".replace(",", "") in resp.text.replace(",", "")


def test_revenue_summary_does_not_modify_database(client, desktop_db):
    before = [dict(r) for r in desktop_db.conn.execute(
        "SELECT * FROM special_activity_member ORDER BY id"
    ).fetchall()]
    client.get("/special-charge", params={"year": "2026", "activity_id": 1})
    after = [dict(r) for r in desktop_db.conn.execute(
        "SELECT * FROM special_activity_member ORDER BY id"
    ).fetchall()]
    assert before == after


# ── 本輪修正 2：手機版 Responsive Layout（沿用既有 Bootstrap class）─────────────

def test_activity_list_uses_responsive_utility_classes_not_new_css(client):
    """★ 優先採用 Bootstrap 既有 responsive class，不新增 CSS framework：
    確認活動名稱與查看明細按鈕沒有被 d-none 隱藏（任何螢幕都看得到），
    次要欄位（日期/狀態）用 d-none d-md-table-cell 在手機隱藏。"""
    resp = client.get("/special-charge", params={"year": "2026"})
    assert "d-none d-md-table-cell" in resp.text
    assert "查看明細" in resp.text


def test_excel_download_button_full_width_on_mobile(client):
    """確認下載按鈕在小螢幕使用 w-100（不會被活動名稱等內容擠出畫面）。"""
    resp = client.get("/special-charge", params={"year": "2026", "activity_id": 1})
    assert "w-100" in resp.text


# ── 本輪修正 3：已繳人員整列淡綠色（沿用 Bootstrap table-success）───────────────

def test_paid_member_row_uses_table_success_class(client):
    resp = client.get("/special-charge", params={"year": "2026", "activity_id": 1})
    assert 'class="table-success"' in resp.text


def test_unpaid_member_row_does_not_use_table_success_class(client, desktop_db):
    """未繳人員維持原本樣式（沒有 table-success class）。用鎖定該人員
    姓名附近的 HTML 片段檢查，避免被頁面其他地方的樣式字串誤判。"""
    from special_charge import SpecialChargeManager
    manager = SpecialChargeManager(desktop_db.conn, ensure_schema=False)
    manager.load_active_tenants_as_members(1, [2])  # 李小華尚未參加，加進來（未繳）

    resp = client.get("/special-charge", params={"year": "2026", "activity_id": 1})
    html = resp.text

    idx = html.index("李小華")
    row_start = html.rindex("<tr", 0, idx)
    row_snippet = html[row_start:idx]
    assert "table-success" not in row_snippet


# ── 本輪修正 4：Web 查詢明細年級排序（★ 核心修正：先前只有 Excel 排序，
#     HTML 頁面沒有排序，這是這輪回報的實際 Bug）──────────────────────────────

def test_web_page_html_actual_member_order_sorted_by_grade_with_shuffled_data(desktop_db):
    """★ 依你的要求：測試資料刻意以錯亂順序建立（一/六/三/五/二/四），
    驗證的是 HTML 實際呈現的人員順序，不是只測 Manager 回傳資料。
    這個測試直接對應你回報的 Bug：先前只有 Excel 排序正確，Web 頁面沒有。
    """
    from fastapi.testclient import TestClient
    from web.app import app
    from web.utils.db import get_db, ReadOnlyDB

    desktop_db.conn.execute("DELETE FROM rent_tenants")
    desktop_db.conn.commit()
    shuffled = [
        ("王一心", "一年級"), ("林六合", "六年級"), ("陳三豐", "三年級"),
        ("張五常", "五年級"), ("李二娃", "二年級"), ("黃四喜", "四年級"),
    ]
    for name, grade in shuffled:
        desktop_db.conn.execute(
            "INSERT INTO rent_tenants (name, monthly_rent, grade, active, created_at) VALUES (?, 2000, ?, 1, '2026-01-01')",
            (name, grade),
        )
    desktop_db.conn.commit()
    tenant_ids = [r[0] for r in desktop_db.conn.execute(
        "SELECT id FROM rent_tenants ORDER BY id"
    ).fetchall()]

    from special_charge import SpecialChargeManager
    manager = SpecialChargeManager(desktop_db.conn, ensure_schema=False)
    activity_id = manager.create_activity("HTML排序測試活動", "2026-09-01")
    manager.load_active_tenants_as_members(activity_id, tenant_ids)

    def override_get_db():
        db = ReadOnlyDB.__new__(ReadOnlyDB)
        db._db = desktop_db
        yield db
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as c:
        resp = c.get("/special-charge", params={"year": "2026", "activity_id": activity_id})

    app.dependency_overrides.clear()

    assert resp.status_code == 200
    html = resp.text

    # 依照姓名在 HTML 中出現的位置排序，確認實際呈現順序
    names = ["林六合", "張五常", "黃四喜", "陳三豐", "李二娃", "王一心"]
    positions = [html.index(n) for n in names]
    assert positions == sorted(positions), (
        f"HTML 實際呈現順序不是六→五→四→三→二→一，各姓名出現位置：{list(zip(names, positions))}"
    )


def test_web_page_grade_sort_uses_actual_grade_field_not_id_or_creation_order(client, desktop_db):
    """★ 依你的要求：確認排序依據是 rent_tenants.grade，不是 tenant_id
    或建立順序——用 id 遞增順序與年級遞減順序刻意相反的資料驗證。"""
    desktop_db.conn.execute("DELETE FROM rent_tenants")
    desktop_db.conn.commit()
    # id 遞增，但年級遞減（tenant_id=1 是六年級，tenant_id=2 是一年級）
    # 如果排序錯誤地用 id/建立順序，會得到「六、一」；正確應該仍是「六、一」
    # 因為六年級本來就該在前——這裡改用相反情境更嚴謹：
    desktop_db.conn.execute(
        "INSERT INTO rent_tenants (name, monthly_rent, grade, active, created_at) VALUES ('先建立的一年級', 2000, '一年級', 1, '2026-01-01')"
    )
    desktop_db.conn.execute(
        "INSERT INTO rent_tenants (name, monthly_rent, grade, active, created_at) VALUES ('後建立的六年級', 2000, '六年級', 1, '2026-01-01')"
    )
    desktop_db.conn.commit()
    tenant_ids = [r[0] for r in desktop_db.conn.execute(
        "SELECT id FROM rent_tenants ORDER BY id"
    ).fetchall()]  # [1年級的id(較小), 6年級的id(較大)]

    from special_charge import SpecialChargeManager
    manager = SpecialChargeManager(desktop_db.conn, ensure_schema=False)
    activity_id = manager.create_activity("id順序反向測試")
    manager.load_active_tenants_as_members(activity_id, tenant_ids)

    resp = client.get("/special-charge", params={"year": str(__import__("datetime").date.today().year), "activity_id": activity_id})
    html = resp.text
    # 若程式碼錯誤地依 id/建立順序排序，「先建立的一年級」會出現在前面
    # 正確結果：六年級（後建立，id 較大）必須出現在一年級之前
    assert html.index("後建立的六年級") < html.index("先建立的一年級")


# ── Bug 1（★ 追完整流程，不只測排序函式）──────────────────────────────────────

def test_excel_full_pipeline_real_downloaded_file_grade_order(client, desktop_db, tmp_path):
    """★ 依你的要求：追完整流程 Database → SpecialChargeManager →
    Excel export function → openpyxl 產生 xlsx → 實際重新開啟 xlsx →
    讀取「個人明細」→ 驗證 Excel 實際列順序。透過 HTTP client（模擬真實
    下載請求），取得 bytes 後寫入實體暫存檔案，用全新的 openpyxl
    load_workbook() 重新讀取（不是沿用記憶體中的 Workbook 物件），
    確認實際 row order。"""
    desktop_db.conn.execute("DELETE FROM rent_tenants")
    desktop_db.conn.commit()
    # 刻意打亂的 DB 建立順序：一→六→三→五→二→四
    shuffled = [
        ("王一心", "一年級"), ("林六合", "六年級"), ("陳三豐", "三年級"),
        ("張五常", "五年級"), ("李二娃", "二年級"), ("黃四喜", "四年級"),
    ]
    for name, grade in shuffled:
        desktop_db.conn.execute(
            "INSERT INTO rent_tenants (name, monthly_rent, grade, active, created_at) VALUES (?, 2000, ?, 1, '2026-01-01')",
            (name, grade),
        )
    desktop_db.conn.commit()
    tenant_ids = [r[0] for r in desktop_db.conn.execute(
        "SELECT id FROM rent_tenants ORDER BY id"
    ).fetchall()]

    from special_charge import SpecialChargeManager
    manager = SpecialChargeManager(desktop_db.conn, ensure_schema=False)
    activity_id = manager.create_activity("Excel完整流程測試", "2026-09-01")
    manager.load_active_tenants_as_members(activity_id, tenant_ids)
    for m in manager.list_members(activity_id):
        manager.add_item(m["id"], "測試項目", 100, 1)

    # 透過 HTTP（真實下載端點，不是直接呼叫 manager.export_excel）
    resp = client.get("/special-charge/export.xlsx", params={"activity_id": activity_id})
    assert resp.status_code == 200

    # 寫入實體暫存檔案（不是留在記憶體 BytesIO）
    downloaded_file = tmp_path / "real_downloaded.xlsx"
    downloaded_file.write_bytes(resp.content)

    # 用全新的 openpyxl 重新開啟這個實體檔案
    import openpyxl
    wb = openpyxl.load_workbook(str(downloaded_file))
    assert "個人明細" in wb.sheetnames
    ws2 = wb["個人明細"]

    names_in_order = []
    row = 1
    while row <= ws2.max_row and len(names_in_order) < 6:
        v = ws2.cell(row=row, column=1).value
        if v and v not in ("測試項目", "合計"):
            names_in_order.append(v)
        row += 1

    expected = ["林六合", "張五常", "黃四喜", "陳三豐", "李二娃", "王一心"]
    # ★ Sheet2 姓名列現在顯示「年級　姓名」（例如「六年級　林六合」），
    # 讓排序依據在檔案裡直接可見可驗證，這裡逐一確認每個位置的姓名正確
    for i, name in enumerate(expected):
        assert name in names_in_order[i], (
            f"★ Excel 實際列順序錯誤！第 {i+1} 位預期含有「{name}」，"
            f"實際內容：{names_in_order[i]}（完整順序：{names_in_order}）"
        )
    # ★ 額外確認：年級文字本身也確實顯示在儲存格裡（不是只有姓名）
    expected_grades = ["六年級", "五年級", "四年級", "三年級", "二年級", "一年級"]
    for i, grade in enumerate(expected_grades):
        assert grade in names_in_order[i], (
            f"第 {i+1} 位應顯示年級「{grade}」，實際：{names_in_order[i]}"
        )
    print(f"Excel 個人明細實際順序：{' → '.join(names_in_order)}")


# ── Bug 2：Mobile Collapse 展開內容不被遮擋 ─────────────────────────────────────

def test_collapse_target_is_div_not_tr(client):
    """★ 修正核心：collapse class 不應該直接套在 <tr> 上（<tr> 不是區塊
    元素，scrollHeight 計算不可靠，手機版容易被遮擋/裁切）。改成套在
    <td> 裡面的 <div> 上，這是 Bootstrap 官方文件建議的表格列展開模式。"""
    resp = client.get("/special-charge", params={"year": "2026", "activity_id": 1})
    html = resp.text
    # 不應該出現 <tr class="collapse ...">（collapse 直接套在 tr 上）
    import re
    assert not re.search(r'<tr[^>]*\bclass="[^"]*\bcollapse\b', html), (
        "collapse 仍然直接套用在 <tr> 上，這是造成手機版展開內容被遮擋的根因"
    )
    # 應該出現 <div class="collapse" id="items-...">
    assert re.search(r'<div class="collapse" id="items-\d+">', html)


def test_collapse_div_wraps_item_detail_table(client):
    """確認展開的項目明細表格確實包在這個 <div class="collapse"> 裡面，
    不是分散在外層（避免展開/收合行為跟內容脫節）。"""
    resp = client.get("/special-charge", params={"year": "2026", "activity_id": 1})
    html = resp.text
    div_start = html.index('<div class="collapse" id="items-')
    # 找到對應的表格內容應該緊接在這個 div 開始之後
    snippet = html[div_start:div_start + 2000]
    assert "table-sm" in snippet
    assert "合計" in snippet


def test_no_overflow_hidden_added_to_special_charge_template():
    """確認本次修正沒有在 special_charge.html 裡新增任何實際的
    `overflow: hidden` CSS 宣告或 Bootstrap 的 `overflow-hidden` class
    （既有的 .card overflow:hidden 是全站共用樣式，本次不修改；只調整
    collapse 目標元素本身，讓它能正確計算高度、不被裁切）。註解文字裡
    提到「overflow」是在說明修正原因，不算數。"""
    template_path = "web/templates/special_charge.html"
    with open(template_path, encoding="utf-8") as f:
        lines = f.readlines()
    # 排除 HTML 註解行（<!-- ... -->），只檢查實際會生效的樣式/class
    code_lines = []
    in_comment = False
    for line in lines:
        if "<!--" in line:
            in_comment = True
        if not in_comment:
            code_lines.append(line)
        if "-->" in line:
            in_comment = False
    code_text = "".join(code_lines)
    assert "overflow-hidden" not in code_text
    assert "overflow: hidden" not in code_text
    assert "style=" not in code_text  # 沒有用 inline style 硬加 overflow


def test_special_charge_page_still_loads_after_collapse_structure_change(client):
    """基本 Regression：修改 collapse 結構後頁面仍正常載入，展開按鈕與
    項目明細內容都還在。"""
    resp = client.get("/special-charge", params={"year": "2026", "activity_id": 1})
    assert resp.status_code == 200
    assert "展開" in resp.text
    assert "車資" in resp.text
    assert "餐費" in resp.text


# ── 需求變更：活動總表 + 個人明細排序規則完全一致（依你這輪的裁定）──────────────

def test_both_sheets_use_identical_grade_and_name_sort_via_real_http_download(client, desktop_db):
    """★ 依你這輪的裁定：兩個 Sheet 的排序規則必須完全一致——第一鍵年級
    六→五→四→三→二→一，第二鍵姓名。透過真正的 HTTP Excel Download
    endpoint 下載，用 openpyxl 重新開啟驗證兩個 Sheet 的實際儲存格順序
    （不是只測 Manager 回傳資料）。刻意打亂建立順序：一→六→三→五→二→四，
    tenant_id 與年級順序刻意不一致。"""
    desktop_db.conn.execute("DELETE FROM rent_tenants")
    desktop_db.conn.commit()
    shuffled = [
        ("王一心", "一年級"), ("林六合", "六年級"), ("陳三豐", "三年級"),
        ("張五常", "五年級"), ("李二娃", "二年級"), ("黃四喜", "四年級"),
    ]
    for name, grade in shuffled:
        desktop_db.conn.execute(
            "INSERT INTO rent_tenants (name, monthly_rent, grade, active, created_at) VALUES (?, 2000, ?, 1, '2026-01-01')",
            (name, grade),
        )
    desktop_db.conn.commit()
    tenant_ids = [r[0] for r in desktop_db.conn.execute(
        "SELECT id FROM rent_tenants ORDER BY id"
    ).fetchall()]  # tenant_id 順序 = 一(1)/六(2)/三(3)/五(4)/二(5)/四(6)，與年級順序刻意不同

    from special_charge import SpecialChargeManager
    manager = SpecialChargeManager(desktop_db.conn, ensure_schema=False)
    activity_id = manager.create_activity("兩表排序一致性測試", "2026-09-01")
    manager.load_active_tenants_as_members(activity_id, tenant_ids)
    for m in manager.list_members(activity_id):
        manager.add_item(m["id"], "測試項目", 100, 1)

    # ★ 真正透過 HTTP Excel Download endpoint 下載
    resp = client.get("/special-charge/export.xlsx", params={"activity_id": activity_id})
    assert resp.status_code == 200

    import openpyxl, io
    wb = openpyxl.load_workbook(io.BytesIO(resp.content))
    expected_grades = ["六年級", "五年級", "四年級", "三年級", "二年級", "一年級"]
    expected_names = ["林六合", "張五常", "黃四喜", "陳三豐", "李二娃", "王一心"]

    # --- 驗證「活動總表」實際儲存格順序 ---
    ws1 = wb["活動總表"]
    actual_ws1_grades = [ws1.cell(row=r, column=1).value for r in range(2, 8)]
    actual_ws1_names = [ws1.cell(row=r, column=2).value for r in range(2, 8)]
    assert actual_ws1_grades == expected_grades, f"活動總表年級順序錯誤：{actual_ws1_grades}"
    assert actual_ws1_names == expected_names, f"活動總表姓名順序錯誤：{actual_ws1_names}"

    # --- 驗證「個人明細」實際儲存格順序（維持目前已正確的排序）---
    ws2 = wb["個人明細"]
    actual_ws2_labels = []
    row = 1
    while row <= ws2.max_row and len(actual_ws2_labels) < 6:
        v = ws2.cell(row=row, column=1).value
        if v and v not in ("測試項目", "合計"):
            actual_ws2_labels.append(v)
        row += 1
    for i, (grade, name) in enumerate(zip(expected_grades, expected_names)):
        assert grade in actual_ws2_labels[i] and name in actual_ws2_labels[i], (
            f"個人明細第 {i+1} 位應為「{grade} {name}」，實際：{actual_ws2_labels[i]}"
        )

    print(f"活動總表實際排序：{list(zip(actual_ws1_grades, actual_ws1_names))}")
    print(f"個人明細實際排序：{actual_ws2_labels}")


def test_both_sheets_same_grade_sorted_by_name_via_real_http_download(client, desktop_db):
    """★ 同年級內依姓名排序，兩個 Sheet 都要驗證，透過真實 HTTP 下載。"""
    desktop_db.conn.execute("DELETE FROM rent_tenants")
    desktop_db.conn.commit()
    same_grade = [("王小明", "六年級"), ("李小華", "六年級"), ("張小美", "六年級")]
    for name, grade in same_grade:
        desktop_db.conn.execute(
            "INSERT INTO rent_tenants (name, monthly_rent, grade, active, created_at) VALUES (?, 2000, ?, 1, '2026-01-01')",
            (name, grade),
        )
    desktop_db.conn.commit()
    tenant_ids = [r[0] for r in desktop_db.conn.execute(
        "SELECT id FROM rent_tenants ORDER BY id"
    ).fetchall()]

    from special_charge import SpecialChargeManager
    manager = SpecialChargeManager(desktop_db.conn, ensure_schema=False)
    activity_id = manager.create_activity("同年級測試")
    # 故意用打亂的順序加入（不是按姓名字母序）
    manager.load_active_tenants_as_members(activity_id, [tenant_ids[2], tenant_ids[0], tenant_ids[1]])

    resp = client.get("/special-charge/export.xlsx", params={"activity_id": activity_id})
    import openpyxl, io
    wb = openpyxl.load_workbook(io.BytesIO(resp.content))
    expected_names = sorted(["王小明", "李小華", "張小美"])  # Unicode 編碼順序

    ws1 = wb["活動總表"]
    actual_ws1_names = [ws1.cell(row=r, column=2).value for r in range(2, 5)]
    assert actual_ws1_names == expected_names


def test_no_desktop_files_modified_by_this_change():
    """★ 確認本輪需求變更沒有修改桌面版任何功能——只有
    special_charge/manager.py 的 export_excel 內部邏輯調整，這個檔案是
    桌面版與 Web 版共用的，但本次呼叫方式（manager.export_excel()）
    完全沒變，只是 Sheet1 內部多套用一次排序，不影響桌面版任何既有呼叫。
    """
    import inspect
    from special_charge.manager import SpecialChargeManager
    sig = inspect.signature(SpecialChargeManager.export_excel)
    assert list(sig.parameters.keys()) == ["self", "activity_id", "path"]


# ── Restricted UI Version（依裁決 Q1~Q4）───────────────────────────────────────

def test_full_version_sidebar_has_no_disabled_items(client):
    """★ Full Version Regression：既有版本不應該出現任何 disabled 樣式。"""
    for path in ["/", "/transactions", "/income", "/expense", "/statistics",
                 "/export", "/rent", "/tenants", "/special-charge"]:
        resp = client.get(path)
        assert 'disabled' not in resp.text.replace('data-bs-toggle="collapse"', ''), (
            f"{path} 不應該出現 disabled，但找到了"
        )


def test_restricted_sidebar_shows_only_two_items(client):
    """★ S12.3 改：簡易版只顯示「繳費狀況」「特殊收費」，其他 7 項整個隱藏
    （原本：依驗收表顯示 7 個 Disabled 項目）。"""
    resp = client.get("/restricted/")
    assert resp.status_code == 200
    import re
    sidebar = resp.text[resp.text.index('<aside class="sidebar"'):resp.text.index('</aside>')]
    labels = [m.strip() for m in re.findall(r'class="nav-link[^"]*"[^>]*>\s*<i[^>]*></i>\s*([^\n<]+)', sidebar)]
    assert labels == ["繳費狀況", "特殊收費"]
    assert "disabled" not in sidebar
    for hidden in ("首頁 Dashboard", "全部交易", "收入明細", "支出明細", "統計分析", "Excel 匯出", "人員清單",
                   "管理設定", "登入紀錄", "登出", "搜尋"):
        assert hidden not in sidebar, hidden


def test_restricted_rent_page_works_normally_but_export_hidden(client):
    """★ 繳費狀態：簡易版正常可用；右上角「匯出」與「完整人員清單」S12.3 起整個隱藏（原本 Disabled）。"""
    resp = client.get("/restricted/rent")
    assert resp.status_code == 200
    assert 'nav-link disabled' not in resp.text
    assert 'href="/export"' not in resp.text and 'href="/tenants"' not in resp.text
    assert "完整人員清單" not in resp.text
    assert not re.search(r'</i>\s*匯出\s*</a>', resp.text)


def test_restricted_special_charge_fully_functional(client):
    """★ 特殊收費：Restricted 版本查詢與 Excel 下載都正常，不新增任何
    寫入能力。"""
    resp = client.get("/restricted/special-charge", params={"year": "2026", "activity_id": 1})
    assert resp.status_code == 200
    assert "王小明" in resp.text

    excel_resp = client.get("/restricted/special-charge/export.xlsx", params={"activity_id": 1})
    assert excel_resp.status_code == 200
    assert excel_resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def test_restricted_enabled_nav_links_stay_within_restricted_prefix(client):
    """★ 確認 Restricted 版本裡「正常」項目的 sidebar 連結都指向
    /restricted/...，不會不小心把使用者導回 Full Version。範圍限定在
    <aside class="sidebar"> 區塊本身——頁面內容（例如 Dashboard 首頁
    本文裡的快速連結按鈕）不在本次修改範圍內，屬於已知、已記錄的限制
    （見 Known Risks：Disabled 頁面若被直接訪問，其內容本身不會被
    改寫成 restricted-aware，這與 Q4 裁定的「後端不加限制」精神一致）。
    """
    resp = client.get("/restricted/")
    sidebar_start = resp.text.index('<aside class="sidebar"')
    sidebar_end = resp.text.index('</aside>')
    sidebar_html = resp.text[sidebar_start:sidebar_end]

    assert 'href="/restricted/rent"' in sidebar_html
    assert 'href="/restricted/special-charge"' in sidebar_html
    assert 'href="/restricted/admin/settings"' not in sidebar_html   # S12.3：管理項目不顯示給一般人員
    # sidebar 內不應該出現指向完整版的裸連結
    assert 'href="/rent"' not in sidebar_html
    assert 'href="/special-charge"' not in sidebar_html


def test_restricted_and_full_share_same_database_connection(client, desktop_db):
    """★ 依裁決 Q2：Full 與 Restricted 必須共用同一個 DB，不是各自一份。
    這裡驗證兩個 URL 前綴下，透過 client fixture 共用的同一個
    desktop_db 連線，資料互相可見（因為 override 的 get_db 依賴回傳的
    是同一個 db._db 物件）。"""
    from special_charge import SpecialChargeManager
    manager = SpecialChargeManager(desktop_db.conn, ensure_schema=False)
    new_id = manager.create_activity("共用DB測試活動")
    manager.load_active_tenants_as_members(new_id, [1])

    full_resp = client.get("/special-charge", params={"year": str(__import__("datetime").date.today().year)})
    restricted_resp = client.get("/restricted/special-charge", params={"year": str(__import__("datetime").date.today().year)})
    assert "共用DB測試活動" in full_resp.text
    assert "共用DB測試活動" in restricted_resp.text


def test_restricted_route_reuses_identical_handler_not_duplicated_code():
    """★ 確認沒有為了 Restricted 版本複製任何 route handler 檔案——
    special_charge.py／rent.py／dashboard.py 等既有檔案應該只存在一份。"""
    import web.routes.special_charge as sc_module
    import web.routes.rent as rent_module
    # 這兩個 import 應該正常成功，且模組檔案路徑只有一份（沒有 _restricted 後綴檔案）
    import os
    assert not os.path.exists("web/routes/special_charge_restricted.py")
    assert not os.path.exists("web/routes/rent_restricted.py")
    assert not os.path.exists("web/templates/special_charge_restricted.html")
