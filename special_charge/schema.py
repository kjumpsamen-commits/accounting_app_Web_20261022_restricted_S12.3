"""
Special Charge Manager — Schema。

依 Architecture Reset：不沿用任何 charge_management 的 Migration 版本化
機制（那套是為了處理既有正式資料升級設計的）。這裡是全新的 3 張表，
用 `CREATE TABLE IF NOT EXISTS`（比照 database.py::_create_tables() 的
既有風格）即可，不需要版本號、不需要 Non-destructive 檢查清單。
"""

import sqlite3


# S11：參與者表。tenant_id 可空（臨時人員）；guest_name／guest_role 為臨時人員的姓名與身分。
# 每一列必須是「人員名單裡的人」或「有姓名的臨時人員」。
MEMBER_TABLE_SQL = """
        CREATE TABLE {if_not_exists}{name} (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            activity_id   INTEGER NOT NULL REFERENCES special_activity(id),
            tenant_id     INTEGER REFERENCES rent_tenants(id),
            paid_amount   REAL,
            paid          INTEGER NOT NULL DEFAULT 0,
            paid_date     TEXT,
            remark        TEXT DEFAULT '',
            guest_name    TEXT,
            guest_role    TEXT,
            CHECK (tenant_id IS NOT NULL OR length(trim(coalesce(guest_name, ''))) > 0)
        )
"""

MEMBER_COLUMNS = ("id", "activity_id", "tenant_id", "paid_amount", "paid", "paid_date", "remark")


def member_table_needs_upgrade(conn: sqlite3.Connection) -> bool:
    """S11：參與者表已存在、但還是舊結構（沒有 guest_name 欄）。"""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(special_activity_member)").fetchall()]
    return bool(cols) and "guest_name" not in cols


def upgrade_member_table(conn: sqlite3.Connection) -> bool:
    """S11（一次性）：重建參與者表，讓 tenant_id 可空並加入臨時人員欄位。

    SQLite 無法直接拿掉 NOT NULL，所以用標準做法：建新表 → 複製全部資料（id 不變）
    → 刪舊表 → 改名；AUTOINCREMENT 的序號也保留。全部在同一個 transaction 內，
    失敗時整個還原。項目表以參與者 id 對應，id 不變所以不需要動。
    升級前的備份由 database.py 的 _backup_before_migrations_if_needed 負責（tag S11）。
    回傳是否有升級。"""
    if not member_table_needs_upgrade(conn):
        return False
    conn.commit()
    cols = ", ".join(MEMBER_COLUMNS)
    try:
        conn.execute("BEGIN")
        before = conn.execute("SELECT COUNT(*) FROM special_activity_member").fetchone()[0]
        seq = conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='special_activity_member'").fetchone()
        conn.execute(MEMBER_TABLE_SQL.format(if_not_exists="", name="special_activity_member_s11"))
        conn.execute(f"INSERT INTO special_activity_member_s11 ({cols}) "
                     f"SELECT {cols} FROM special_activity_member")
        conn.execute("DROP TABLE special_activity_member")
        conn.execute("ALTER TABLE special_activity_member_s11 RENAME TO special_activity_member")
        if seq is not None:
            conn.execute("UPDATE sqlite_sequence SET seq=max(seq, ?) WHERE name='special_activity_member'",
                         (seq[0],))
        after = conn.execute("SELECT COUNT(*) FROM special_activity_member").fetchone()[0]
        if after != before:
            raise RuntimeError(f"參與者筆數不一致（升級前 {before}、升級後 {after}）")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return True


def ensure_schema(conn: sqlite3.Connection) -> None:
    """建立 special_charge 模組需要的 3 張表（idempotent）。"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS special_activity (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            name           TEXT NOT NULL,
            activity_date  TEXT,
            status         TEXT NOT NULL DEFAULT 'active',
            note           TEXT DEFAULT '',
            created_at     TEXT NOT NULL,
            completed_at   TEXT
        )
        """
    )
    conn.execute(MEMBER_TABLE_SQL.format(if_not_exists="IF NOT EXISTS ", name="special_activity_member"))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS special_activity_item (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            participant_id INTEGER NOT NULL REFERENCES special_activity_member(id),
            item_name      TEXT NOT NULL,
            unit_price     REAL NOT NULL,
            quantity       INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    conn.commit()
