"""
S11 Web：特殊收費的臨時人員
  年級欄顯示身分、姓名後標「（臨時）」、排在所有學生之後；Excel 同樣
  仍然唯讀：讀到尚未升級（S10 結構）的資料庫也能顯示，而且不寫入、不升級
"""
import io
import re
import sqlite3

import pytest


def _ok(client, url):
    r = client.get(url)
    if r.status_code == 404:
        pytest.skip(f"{url} 不在這個版本")
    assert r.status_code == 200, url
    return r


def _sc_urls(client):
    base = "/special-charge"
    if client.get("/restricted/special-charge").status_code == 200:
        return [base, "/restricted/special-charge"]
    return [base]


@pytest.fixture
def guest(desktop_db):
    from special_charge import SpecialChargeManager
    m = SpecialChargeManager(desktop_db.conn)
    aid = m.list_activities()[0]["id"]
    gid = m.add_guest(aid, "蕭淳如", "校外選手")
    m.add_item(gid, "雙打", 700, 2)
    m.mark_paid(gid, paid_date="2026-08-17")
    return m, aid, gid


def test_guest_shown_with_role_and_marker_last(client, guest):
    m, aid, gid = guest
    for base in _sc_urls(client):
        h = _ok(client, f"{base}?year=2026&activity_id={aid}").text
        assert "蕭淳如" in h and "（臨時）" in h and "校外選手" in h
        names = re.findall(r"<td>([^<]+?)(?:<span[^>]*>（臨時）</span>)?</td>", h)
        people = [n for n in names if n in ("王小明", "李小華", "蕭淳如")]
        assert people[-1] == "蕭淳如"                                   # 排在學生之後


def test_guest_totals(client, guest):
    m, aid, gid = guest
    for base in _sc_urls(client):
        h = _ok(client, f"{base}?year=2026&activity_id={aid}").text
        total = m.get_activity_total_receivable(aid)
        assert f"已收：<strong>$5,100</strong>" in h                    # 王小明 3700（已繳）＋蕭淳如 1400
        assert f"應收：<strong>${total:,.0f}</strong>" in h
        assert m.get_activity_collected(aid) == 5100                   # 與桌面同一個算法


def test_guest_in_excel(client, guest):
    import openpyxl
    m, aid, gid = guest
    r = _ok(client, f"/special-charge/export.xlsx?activity_id={aid}")
    ws = openpyxl.load_workbook(io.BytesIO(r.content))["活動總表"]
    rows = [[c.value for c in row] for row in ws.iter_rows()]
    people = [row for row in rows[1:] if row and row[1] in ("王小明", "李小華", "蕭淳如")]
    assert people[-1][:3] == ["校外選手", "蕭淳如", 1400]
    summ = {row[0]: row[1] for row in rows if row and row[0] in ("應收", "已收")}
    assert summ["已收"] == 5100


def test_old_schema_database_read_only(client, desktop_db):
    """Web 讀到 S10 結構（tenant_id NOT NULL、沒有 guest 欄）的資料庫：正常顯示，不寫入。"""
    c = desktop_db.conn
    c.executescript("""
        CREATE TABLE m_old (id INTEGER PRIMARY KEY AUTOINCREMENT,
            activity_id INTEGER NOT NULL REFERENCES special_activity(id),
            tenant_id INTEGER NOT NULL REFERENCES rent_tenants(id),
            paid_amount REAL, paid INTEGER NOT NULL DEFAULT 0, paid_date TEXT, remark TEXT DEFAULT '');
        INSERT INTO m_old SELECT id, activity_id, tenant_id, paid_amount, paid, paid_date, remark
            FROM special_activity_member;
        DROP TABLE special_activity_member;
        ALTER TABLE m_old RENAME TO special_activity_member;
    """)
    c.commit()
    path = desktop_db.db_path
    before = list(sqlite3.connect(str(path)).iterdump())
    from special_charge import SpecialChargeManager
    aid = SpecialChargeManager(c, ensure_schema=False).list_activities()[0]["id"]
    h = _ok(client, f"/special-charge?year=2026&activity_id={aid}").text
    assert "王小明" in h and "（臨時）" not in h
    _ok(client, f"/special-charge/export.xlsx?activity_id={aid}")
    after = sqlite3.connect(str(path))
    assert list(after.iterdump()) == before
    assert "guest_name" not in [r[1] for r in after.execute("PRAGMA table_info(special_activity_member)")]
