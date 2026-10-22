import json
import os
import sqlite3
from pathlib import Path
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from special_charge import schema as sc_schema   # S11：特殊收費參與者表升級


# ── S7：資料庫位置設定 ────────────────────────────────────────────────────────
# 設定檔固定放在預設資料夾；資料庫位置本身不能存在資料庫裡（要先知道位置才能開資料庫）。
CONFIG_DIR = Path.home() / ".accounting_app"
CONFIG_PATH = CONFIG_DIR / "config.json"
DEFAULT_DB_PATH = CONFIG_DIR / "data.db"
REQUIRED_TABLES = {"transactions", "categories", "rent_tenants", "rent_payments"}


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


def configured_db_path():
    """設定檔中指定的資料庫路徑；沒有設定則回傳 None（使用預設位置）。"""
    p = load_config().get("db_path")
    return Path(p) if p else None


def resolve_db_path():
    return configured_db_path() or DEFAULT_DB_PATH


def set_db_location(path):
    """寫入資料庫位置設定；path=None 表示恢復預設位置。下次啟動生效。"""
    cfg = load_config()
    if path is None or Path(path) == DEFAULT_DB_PATH:
        cfg.pop("db_path", None)
    else:
        cfg["db_path"] = str(Path(path))
    save_config(cfg)


def startup_db_missing():
    """啟動檢查：設定了自訂位置但檔案不存在 → 回傳該路徑；否則 None。
    （預設位置維持原行為：第一次使用時自動建立。）"""
    p = configured_db_path()
    if p is not None and not p.is_file():
        return p
    return None


def inspect_database(path):
    """檢查某個檔案是否為可用的本程式資料庫。回傳 (ok, 原因)。"""
    path = Path(path)
    if not path.is_file():
        return False, "檔案不存在。"
    try:
        c = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                return False, "資料庫完整性檢查未通過（檔案可能已損壞）。"
            names = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        finally:
            c.close()
    except sqlite3.DatabaseError as e:
        return False, f"無法開啟（不是資料庫檔案或已損壞）：{e}"
    missing = REQUIRED_TABLES - names
    if missing:
        return False, "不是本程式的資料庫（缺少資料表：" + "、".join(sorted(missing)) + "）。"
    return True, ""


def _table_counts(conn):
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name <> 'sqlite_sequence'")]
    return {n: conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}


def copy_database(src_conn, dst_path):
    """以 SQLite backup API 把目前資料庫完整複製到 dst_path，並驗證。

    先寫到暫存檔 → 完整性檢查＋每張表筆數一致 → 才換成正式檔名；
    任何一步失敗都刪除暫存檔並丟出錯誤（目標位置不留半成品）。
    """
    dst_path = Path(dst_path)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst_path.with_name(dst_path.name + ".tmp_copy")
    if tmp.exists():
        tmp.unlink()
    src_conn.commit()
    dst = sqlite3.connect(str(tmp))
    try:
        src_conn.backup(dst)
        dst.execute("PRAGMA journal_mode=DELETE")
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("複製後的資料庫完整性檢查未通過。")
        if _table_counts(dst) != _table_counts(src_conn):
            raise RuntimeError("複製後的資料筆數與原資料庫不一致。")
        dst.close()
        os.replace(tmp, dst_path)
    except Exception:
        try:
            dst.close()
        except Exception:
            pass
        if tmp.exists():
            tmp.unlink()
        raise
    return dst_path


# ── S9：自動備份 ───────────────────────────────────────────────────────────────

BACKUP_DIRNAME = "backups"     # 資料庫旁的備份資料夾（你的決定 B3）
BACKUP_KEEP = 10               # 上限 10 份；滿了由使用者每次選擇（不自動刪除）


def backup_dir(db_path):
    return Path(db_path).parent / BACKUP_DIRNAME


def list_backups(db_path):
    """列出自動備份（新 → 舊）。只認「資料庫檔名_日期時間*.db」的檔案。"""
    d = backup_dir(db_path)
    if not d.is_dir():
        return []
    stem = Path(db_path).stem
    out = []
    for p in d.glob(f"{stem}_*.db"):
        if p.is_file():
            st = p.stat()
            out.append({"path": p, "mtime": st.st_mtime, "size": st.st_size})
    out.sort(key=lambda b: (b["mtime"], b["path"].name), reverse=True)
    return out


def make_backup(conn, db_path, suffix=""):
    """以 S7 的 copy_database（backup API＋完整性檢查＋筆數比對）在 backups 資料夾建立一份備份。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = Path(db_path).stem
    d = backup_dir(db_path)
    dst = d / f"{stem}_{stamp}{suffix}.db"
    n = 2
    while dst.exists():
        dst = d / f"{stem}_{stamp}_{n}{suffix}.db"
        n += 1
    return copy_database(conn, dst)


def restore_backup(conn, backup_path):
    """以備份檔取代目前資料庫的內容（backup API，整份覆蓋）。
    呼叫前應先 make_backup(...) 另存目前資料；完成後程式需重新啟動。"""
    ok, reason = inspect_database(backup_path)
    if not ok:
        raise RuntimeError(f"備份檔無法使用：{reason}")
    conn.commit()
    src = sqlite3.connect(f"file:{Path(backup_path).as_posix()}?mode=ro", uri=True)
    try:
        src.backup(conn)
    finally:
        src.close()
    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise RuntimeError("還原後的資料庫完整性檢查未通過。")


DB_PATH = resolve_db_path()

# 月費人員顯示順序（S1）：六年級 → 一年級，其他／未填年級排最後，同年級依姓名。
# grade 為中文字串，直接 ORDER BY grade 會變成字碼順序（一→三→二→五→六→四）。
_TENANT_ORDER_BY = (
    " ORDER BY CASE grade"
    " WHEN '六年級' THEN 1 WHEN '五年級' THEN 2 WHEN '四年級' THEN 3"
    " WHEN '三年級' THEN 4 WHEN '二年級' THEN 5 WHEN '一年級' THEN 6"
    " ELSE 7 END, name"
)


# ── S10：期初餘額／交接轉出 ─────────────────────────────────────────────────────
# 兩者都是真實的資金移動，要算在「結餘」裡，但不是這段期間的收入或支出：
#   結餘 ＝ 期初 ＋ 收入 － 支出 － 交接轉出（與 S10 之前「收入 － 支出」數字相同）
# 判斷時「類型＋分類」都要符合（期初＝收入＋期初餘額；交接轉出＝支出＋交接轉出）。
# 兩者都不列入傳票編號（當月 000 號）。
OPENING_CATEGORY = "期初餘額"
TRANSFER_OUT_CATEGORY = "交接轉出"
SPECIAL_CATEGORIES = {OPENING_CATEGORY: "income", TRANSFER_OUT_CATEGORY: "expense"}
_OPENING_SQL = f"(type='income' AND category='{OPENING_CATEGORY}')"
_TRANSFER_OUT_SQL = f"(type='expense' AND category='{TRANSFER_OUT_CATEGORY}')"
_INCOME_SQL = f"(type='income' AND NOT {_OPENING_SQL})"
_EXPENSE_SQL = f"(type='expense' AND NOT {_TRANSFER_OUT_SQL})"


def special_kind(type_, category):
    """'opening'（期初）／'transfer_out'（交接轉出）／None（一般收支）。"""
    if type_ == "income" and category == OPENING_CATEGORY:
        return "opening"
    if type_ == "expense" and category == TRANSFER_OUT_CATEGORY:
        return "transfer_out"
    return None


def summarize_rows(rows):
    """S10：記帳列的合計（桌面記帳紀錄、Web 記帳／搜尋與 get_summary 共用同一份計算）。
    rows：含 type／category／amount 的 dict 或 sqlite3.Row。"""
    opening = income = expense = transfer_out = 0.0
    for r in rows:
        kind = special_kind(r["type"], r["category"])
        if kind == "opening":
            opening += r["amount"]
        elif kind == "transfer_out":
            transfer_out += r["amount"]
        elif r["type"] == "income":
            income += r["amount"]
        elif r["type"] == "expense":
            expense += r["amount"]
    return {
        "total_income": income, "total_expense": expense,
        "total_opening": opening, "total_transfer_out": transfer_out,
        "balance": opening + income - expense - transfer_out,
        "count": len(rows),
    }


class Database:
    def __init__(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.db_path = Path(DB_PATH)
        self._use_rollback_journal()
        self._backup_before_migrations_if_needed()
        self._create_tables()

    def _use_rollback_journal(self):
        """S7：桌面改用一般日誌模式（DELETE）。

        WAL 模式下最近的寫入會暫存在 data.db-wal，只複製／上傳 data.db 會少資料。
        切換時 SQLite 會先把 -wal 內容完整寫回主檔。若檔案正被其他程式使用而無法切換，
        維持原模式繼續執行（不影響資料正確性），回傳實際模式。
        """
        try:
            row = self.conn.execute("PRAGMA journal_mode=DELETE").fetchone()
            return (row[0] if row else "").lower()
        except sqlite3.DatabaseError:
            return ""

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS transactions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                type        TEXT    NOT NULL CHECK(type IN ('income','expense')),
                amount      REAL    NOT NULL,
                category    TEXT    NOT NULL,
                note        TEXT    DEFAULT '',
                date        TEXT    NOT NULL,
                created_at  TEXT    NOT NULL,
                voucher_no  TEXT    DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS categories (
                id    INTEGER PRIMARY KEY AUTOINCREMENT,
                name  TEXT    NOT NULL UNIQUE,
                type  TEXT    NOT NULL CHECK(type IN ('income','expense','both'))
            );

            CREATE TABLE IF NOT EXISTS rent_tenants (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT    NOT NULL,
                monthly_rent  REAL    NOT NULL,
                note          TEXT    DEFAULT '',
                grade         TEXT    DEFAULT '',
                active        INTEGER NOT NULL DEFAULT 1,
                created_at    TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS rent_payments (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id      INTEGER NOT NULL REFERENCES rent_tenants(id),
                year           INTEGER NOT NULL,
                month          INTEGER NOT NULL,
                status         TEXT    NOT NULL DEFAULT 'pending'
                               CHECK(status IN ('paid','pending','exempt')),
                paid_date      TEXT    DEFAULT NULL,
                note           TEXT    DEFAULT '',
                transaction_id INTEGER DEFAULT NULL,
                UNIQUE(tenant_id, year, month)
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS transaction_deletions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                original_id   INTEGER NOT NULL,
                type          TEXT    NOT NULL,
                amount        REAL    NOT NULL,
                category      TEXT    NOT NULL,
                note          TEXT    DEFAULT '',
                date          TEXT    NOT NULL,
                created_at    TEXT    NOT NULL,
                deleted_at    TEXT    NOT NULL,
                deleted_note  TEXT    DEFAULT '',
                voucher_no    TEXT    DEFAULT ''
            );
        """)
        self._migrate()
        self._seed_categories()
        self._migrate_s10_special_categories()   # 必須在預設分類之後（否則新資料庫不會建立預設分類）
        # S11：重建特殊收費參與者表（tenant_id 可空、加臨時人員欄位）；已升級或新資料庫不動
        self.s11_upgraded = sc_schema.upgrade_member_table(self.conn)
        self.conn.commit()

    def _migrate(self):
        """相容舊資料庫：缺欄位時補上，不更動既有資料。"""
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(rent_tenants)").fetchall()]
        if "grade" not in cols:
            self.conn.execute("ALTER TABLE rent_tenants ADD COLUMN grade TEXT DEFAULT ''")

        pay_cols = [r[1] for r in self.conn.execute("PRAGMA table_info(rent_payments)").fetchall()]
        if "transaction_id" not in pay_cols:
            self.conn.execute(
                "ALTER TABLE rent_payments ADD COLUMN transaction_id INTEGER DEFAULT NULL"
            )

        # 若舊資料庫缺少 voucher_no 欄位，則新增
        tx_cols = [r[1] for r in self.conn.execute("PRAGMA table_info(transactions)").fetchall()]
        if "voucher_no" not in tx_cols:
            self.conn.execute("ALTER TABLE transactions ADD COLUMN voucher_no TEXT DEFAULT ''")

        del_cols = [r[1] for r in self.conn.execute("PRAGMA table_info(transaction_deletions)").fetchall()]
        if "voucher_no" not in del_cols:
            # transaction_deletions 可能剛剛建立，上方有 CREATE IF NOT EXISTS
            self.conn.execute("ALTER TABLE transaction_deletions ADD COLUMN voucher_no TEXT DEFAULT ''")

        # 舊資料庫沒有 transaction_deletions 時建立
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS transaction_deletions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                original_id   INTEGER NOT NULL,
                type          TEXT    NOT NULL,
                amount        REAL    NOT NULL,
                category      TEXT    NOT NULL,
                note          TEXT    DEFAULT '',
                date          TEXT    NOT NULL,
                created_at    TEXT    NOT NULL,
                deleted_at    TEXT    NOT NULL,
                deleted_note  TEXT    DEFAULT ''
            )
        """)
        self.conn.commit()
        self._migrate_s2_billing_period()
        self._migrate_s3_fee()

    # ── S2：月費收費期間（起算月份／最後收費月份）──────────────────────────────

    def _migrate_s2_billing_period(self):
        """rent_tenants 新增 start_ym／end_ym（YYYYMM 整數）。

        - 只在欄位尚不存在時執行一次（已升級的資料庫不做任何寫入）。
        - 備份在 __init__ 最前面、任何寫入之前完成（_backup_before_migrations_if_needed）。
        - 新增欄位與初始化在同一個 transaction 內完成，失敗則整批回復。
        - start_ym：此人最早一筆 rent_payments 月份；沒有紀錄則用 created_at 月份。
        - end_ym（僅停用者）：此人最後一筆 rent_payments 月份；沒有紀錄則用 created_at 月份。
        - 不寫入 rent_payments／transactions 等其他資料表。
        """
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(rent_tenants)").fetchall()]
        missing = [c for c in ("start_ym", "end_ym") if c not in cols]
        if not missing:
            return
        self.conn.commit()
        created_ym = "CAST(substr(replace(created_at, '-', ''), 1, 6) AS INTEGER)"
        try:
            self.conn.execute("BEGIN")
            for c in missing:
                self.conn.execute(f"ALTER TABLE rent_tenants ADD COLUMN {c} INTEGER DEFAULT NULL")
            self.conn.execute(
                "UPDATE rent_tenants SET start_ym = COALESCE("
                " (SELECT MIN(p.year*100+p.month) FROM rent_payments p WHERE p.tenant_id = rent_tenants.id),"
                f" {created_ym}) WHERE start_ym IS NULL"
            )
            self.conn.execute(
                "UPDATE rent_tenants SET end_ym = COALESCE("
                " (SELECT MAX(p.year*100+p.month) FROM rent_payments p WHERE p.tenant_id = rent_tenants.id),"
                f" {created_ym}) WHERE active = 0 AND end_ym IS NULL"
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def _pending_migrations(self):
        """尚未套用、且需要備份的遷移（只看既有資料庫；全新資料庫回傳空清單）。"""
        pending = []
        exists = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rent_tenants'"
        ).fetchone()
        if exists and self.conn.execute("SELECT COUNT(*) FROM rent_tenants").fetchone()[0]:
            cols = [r[1] for r in self.conn.execute("PRAGMA table_info(rent_tenants)").fetchall()]
            if not ("start_ym" in cols and "end_ym" in cols):
                pending.append("S2")
            if not self._has_fee_tables():
                pending.append("S3")
        # S10：只要有記帳就要備份（不管有沒有人員資料）
        if self._s10_pending():
            pending.append("S10")
        # S11：特殊收費參與者表（臨時人員）需要重建
        if sc_schema.member_table_needs_upgrade(self.conn):
            pending.append("S11")
        return pending

    def _s10_pending(self):
        """S10：已有記帳、但還沒有「期初餘額」「交接轉出」分類的舊資料庫。"""
        names = {r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if not {"transactions", "categories"} <= names:
            return False
        if self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0:
            return False
        have = {r[0] for r in self.conn.execute(
            "SELECT name FROM categories WHERE name IN (?, ?)", tuple(SPECIAL_CATEGORIES))}
        return have != set(SPECIAL_CATEGORIES)

    def _migrate_s10_special_categories(self):
        """S10（一次性）：建立「期初餘額」（收入）、「交接轉出」（支出）分類；
        並把現有的「前期轉入」（收入、分類＝其他收入、備註＝前期轉入）改成「期初餘額」分類——
        只有剛好一筆時才改，0 筆或多筆都不改。只改分類，金額、日期、傳票編號都不變。
        升級前備份由 _backup_before_migrations_if_needed 負責（tag S10）。
        結果放在 self.s10_notice，供主視窗開啟時提示一次。"""
        self.s10_notice = None
        have = {r[0] for r in self.conn.execute(
            "SELECT name FROM categories WHERE name IN (?, ?)", tuple(SPECIAL_CATEGORIES))}
        missing = [n for n in SPECIAL_CATEGORIES if n not in have]
        if not missing:
            return
        convert_opening = OPENING_CATEGORY in missing
        self.conn.commit()
        try:
            self.conn.execute("BEGIN")
            for name in missing:
                self.conn.execute("INSERT INTO categories(name, type) VALUES(?, ?)",
                                  (name, SPECIAL_CATEGORIES[name]))
            if convert_opening:
                rows = self.conn.execute(
                    "SELECT id, date, amount FROM transactions "
                    "WHERE type='income' AND category='其他收入' AND trim(note)='前期轉入'"
                ).fetchall()
                if len(rows) == 1:
                    self.conn.execute("UPDATE transactions SET category=? WHERE id=?",
                                      (OPENING_CATEGORY, rows[0]["id"]))
                    self.s10_notice = {"converted": True, "id": rows[0]["id"],
                                       "date": rows[0]["date"], "amount": rows[0]["amount"]}
                elif self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]:
                    self.s10_notice = {"converted": False, "matches": len(rows)}
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def _backup_before_migrations_if_needed(self):
        """啟動時、任何寫入之前：舊資料庫需要升級時先完整備份（S2、S3 一次完成，只備份一份）。"""
        pending = self._pending_migrations()
        if not pending:
            return None
        return self._backup_database("_".join(pending))

    def _backup_database(self, tag):
        """以 SQLite backup API 將目前資料庫完整複製到同資料夾的備份檔，回傳備份路徑。"""
        self.conn.commit()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dst_path = DB_PATH.with_name(f"{DB_PATH.name}.bak_{tag}_{stamp}")
        dst = sqlite3.connect(str(dst_path))
        try:
            self.conn.backup(dst)
        finally:
            dst.close()
        return dst_path

    def _has_billing_period(self):
        """資料庫是否已有 start_ym／end_ym（Web 可能讀到尚未升級的舊資料庫）。"""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(rent_tenants)").fetchall()}
        return "start_ym" in cols and "end_ym" in cols

    @staticmethod
    def is_tenant_billable(tenant, year, month):
        """某人某月是否在收費期間內（與 get_payments_for_month 的 SQL 條件相同）。

        未升級的舊資料（沒有 start_ym／end_ym）→ 依 active：在職＝每月皆列入，停用＝不列入。
        """
        ym = year * 100 + month
        start = tenant.get("start_ym")
        end = tenant.get("end_ym")
        if start is not None and ym < start:
            return False
        if end is not None:
            return ym <= end
        return bool(tenant.get("active", 1))

    def _record_month_range(self, tid):
        row = self.conn.execute(
            "SELECT MIN(year*100+month) AS lo, MAX(year*100+month) AS hi "
            "FROM rent_payments WHERE tenant_id=?", (tid,)
        ).fetchone()
        return (row["lo"], row["hi"]) if row else (None, None)

    @staticmethod
    def _fmt_ym(ym):
        return f"{ym // 100}-{ym % 100:02d}"

    def _validate_start_ym(self, tid, start_ym):
        lo, _ = self._record_month_range(tid)
        if lo is not None and start_ym > lo:
            raise ValueError(
                f"此人在 {self._fmt_ym(lo)} 已有繳費紀錄，起算月份不可晚於 {self._fmt_ym(lo)}。"
            )
        row = self.conn.execute("SELECT end_ym FROM rent_tenants WHERE id=?", (tid,)).fetchone()
        if row and row["end_ym"] is not None and start_ym > row["end_ym"]:
            raise ValueError("起算月份不可晚於最後收費月份。")

    @staticmethod
    def _current_ym():
        now = datetime.now()
        return now.year * 100 + now.month

    # ── S3：標準月費／個人優惠／已繳金額固定 ──────────────────────────────────

    def _has_fee_tables(self):
        """資料庫是否已有 S3 結構（Web 可能讀到尚未升級的舊資料庫）。"""
        names = {r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if not {"rent_fee_schedule", "rent_fee_discount"} <= names:
            return False
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(rent_payments)").fetchall()}
        return "amount" in cols

    def _migrate_s3_fee(self):
        """建立 rent_fee_schedule／rent_fee_discount，rent_payments 新增 amount。

        - 只在尚未升級時執行一次；同一個 transaction，失敗整批回復。
        - 標準月費：一筆（最早起算月份, 在職者最常見的 monthly_rent）。
        - monthly_rent 與標準不同的人：建立一筆減額優惠（生效＝其起算月份）。
        - 既有已繳／免繳紀錄的 amount 回填該人當時的 monthly_rent。
        - 不改動 rent_tenants 與 transactions 的任何資料。
        """
        if self._has_fee_tables():
            return
        self.conn.commit()
        pay_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(rent_payments)").fetchall()}
        try:
            self.conn.execute("BEGIN")
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS rent_fee_schedule (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    effective_ym INTEGER NOT NULL UNIQUE,
                    amount       REAL    NOT NULL,
                    note         TEXT    DEFAULT '',
                    created_at   TEXT    NOT NULL
                )""")
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS rent_fee_discount (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id    INTEGER NOT NULL REFERENCES rent_tenants(id),
                    effective_ym INTEGER NOT NULL,
                    kind         TEXT    NOT NULL CHECK(kind IN ('none','amount','percent')),
                    value        REAL    NOT NULL DEFAULT 0,
                    note         TEXT    DEFAULT '',
                    created_at   TEXT    NOT NULL,
                    UNIQUE(tenant_id, effective_ym)
                )""")
            if "amount" not in pay_cols:
                self.conn.execute("ALTER TABLE rent_payments ADD COLUMN amount REAL DEFAULT NULL")

            now = datetime.now().isoformat()
            row = self.conn.execute(
                "SELECT monthly_rent, COUNT(*) AS n FROM rent_tenants WHERE active=1 "
                "GROUP BY monthly_rent ORDER BY n DESC, monthly_rent DESC LIMIT 1"
            ).fetchone() or self.conn.execute(
                "SELECT monthly_rent, COUNT(*) AS n FROM rent_tenants "
                "GROUP BY monthly_rent ORDER BY n DESC, monthly_rent DESC LIMIT 1"
            ).fetchone()
            if row is not None:
                standard = row["monthly_rent"]
                eff = self.conn.execute("SELECT MIN(start_ym) FROM rent_tenants").fetchone()[0] \
                    or self._current_ym()
                self.conn.execute(
                    "INSERT INTO rent_fee_schedule(effective_ym, amount, note, created_at) "
                    "VALUES(?,?,?,?)", (eff, standard, "S3 初始化（由既有月費轉入）", now))
                for t in self.conn.execute(
                        "SELECT id, monthly_rent, start_ym FROM rent_tenants "
                        "WHERE monthly_rent <> ?", (standard,)).fetchall():
                    self.conn.execute(
                        "INSERT INTO rent_fee_discount(tenant_id, effective_ym, kind, value, note, created_at) "
                        "VALUES(?,?,?,?,?,?)",
                        (t["id"], t["start_ym"] or eff, "amount", standard - t["monthly_rent"],
                         "S3 初始化（由既有月費轉入）", now))
            self.conn.execute(
                "UPDATE rent_payments SET amount = (SELECT monthly_rent FROM rent_tenants r "
                "WHERE r.id = rent_payments.tenant_id) "
                "WHERE status IN ('paid','exempt') AND amount IS NULL"
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    @staticmethod
    def _round_yuan(x):
        return float(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    @staticmethod
    def apply_discount(base, kind, value):
        """標準月費套用個人優惠。amount＝減額（不低於 0）；percent＝折數×10（85＝8.5 折），四捨五入到元。"""
        if kind == "amount":
            return max(0.0, float(base) - float(value))
        if kind == "percent":
            return Database._round_yuan(float(base) * float(value) / 100.0)
        return float(base)

    @staticmethod
    def discount_label(kind, value):
        if kind == "amount" and value:
            return f"−{value:,.0f}"
        if kind == "percent":
            v = value / 10.0
            return f"{v:g} 折"
        return ""

    def standard_fee_for(self, ym):
        """某月的標準月費；沒有任何標準月費時回傳 None。生效月份之前的月份沿用最早一筆。"""
        row = self.conn.execute(
            "SELECT amount FROM rent_fee_schedule WHERE effective_ym <= ? "
            "ORDER BY effective_ym DESC LIMIT 1", (ym,)).fetchone()
        if row is None:
            row = self.conn.execute(
                "SELECT amount FROM rent_fee_schedule ORDER BY effective_ym LIMIT 1").fetchone()
        return row["amount"] if row else None

    def discount_for(self, tid, ym):
        """某人某月適用的優惠 (kind, value)；沒有則 ('none', 0)。"""
        row = self.conn.execute(
            "SELECT kind, value FROM rent_fee_discount WHERE tenant_id=? AND effective_ym <= ? "
            "ORDER BY effective_ym DESC LIMIT 1", (tid, ym)).fetchone()
        return (row["kind"], row["value"]) if row else ("none", 0)

    def computed_fee(self, tenant, year, month):
        """依規則計算某人某月金額（不看已固定的 amount）。未升級的舊資料庫退回 monthly_rent。"""
        if not self._has_fee_tables():
            return tenant["monthly_rent"]
        ym = year * 100 + month
        base = self.standard_fee_for(ym)
        if base is None:
            return tenant["monthly_rent"]
        kind, value = self.discount_for(tenant["id"], ym)
        return self.apply_discount(base, kind, value)

    def fee_for_month(self, tenant, year, month, payment=None):
        """某人某月的實際金額：已繳／免繳且已固定 → 固定金額；否則依規則計算。

        payment：該月 rent_payments 列（sqlite3.Row 或 dict），可省略（會自行查詢）。
        """
        if payment is None and self._has_fee_tables():
            payment = self.conn.execute(
                "SELECT status, amount FROM rent_payments WHERE tenant_id=? AND year=? AND month=?",
                (tenant["id"], year, month)).fetchone()
        if payment is not None:
            keys = payment.keys()
            if ("amount" in keys and payment["amount"] is not None
                    and payment["status"] in ("paid", "exempt")):
                return payment["amount"]
        return self.computed_fee(tenant, year, month)

    # 標準月費 CRUD
    def list_fee_schedule(self):
        if not self._has_fee_tables():
            return []
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM rent_fee_schedule ORDER BY effective_ym DESC").fetchall()]

    def add_fee_schedule(self, effective_ym, amount, note=""):
        if amount < 0:
            raise ValueError("金額不可為負數。")
        if self.conn.execute("SELECT 1 FROM rent_fee_schedule WHERE effective_ym=?",
                             (effective_ym,)).fetchone():
            raise ValueError(
                f"{self._fmt_ym(effective_ym)} 已經有一筆標準月費。\n"
                "若要修改：尚未生效的可先刪除再新增；已生效的請新增下一個月份的更正。")
        cur = self.conn.execute(
            "INSERT INTO rent_fee_schedule(effective_ym, amount, note, created_at) VALUES(?,?,?,?)",
            (effective_ym, amount, note, datetime.now().isoformat()))
        self.conn.commit()
        return cur.lastrowid

    def delete_fee_schedule(self, schedule_id):
        """只能刪除尚未生效（生效月份在本月之後）的標準月費。"""
        row = self.conn.execute("SELECT effective_ym FROM rent_fee_schedule WHERE id=?",
                                (schedule_id,)).fetchone()
        if row is None:
            raise ValueError("找不到這筆標準月費。")
        if row["effective_ym"] <= self._current_ym():
            raise ValueError("已生效的標準月費不可刪除，請新增一筆更正。")
        self.conn.execute("DELETE FROM rent_fee_schedule WHERE id=?", (schedule_id,))
        self.conn.commit()

    def fee_change_impact(self, effective_ym, new_amount):
        """新增標準月費前的影響摘要（不寫入）。"""
        y, m = divmod(effective_ym, 100)
        tenants = [t for t in self.get_tenants(active_only=False)
                   if self.is_tenant_billable(t, y, m)
                   or (t["active"] and (t.get("start_ym") or 0) > effective_ym)]
        plain, discounted = 0, []
        for t in tenants:
            kind, value = self.discount_for(t["id"], effective_ym)
            if kind == "none":
                plain += 1
            else:
                discounted.append((t["name"], self.discount_label(kind, value),
                                   self.apply_discount(new_amount, kind, value)))
        fixed = self.conn.execute(
            "SELECT COUNT(*) FROM rent_payments WHERE (year*100+month) >= ? "
            "AND status IN ('paid','exempt')", (effective_ym,)).fetchone()[0]
        past_months, past_diff = 0, 0.0
        cur = self._current_ym()
        if effective_ym < cur:
            yy, mm = y, m
            while yy * 100 + mm < cur:
                for t in self.get_payments_for_month(yy, mm):
                    if t["status"] == "pending":
                        kind, value = self.discount_for(t["tenant_id"], yy * 100 + mm)
                        past_months += 1
                        past_diff += self.apply_discount(new_amount, kind, value) - t["monthly_rent"]
                mm += 1
                if mm > 12:
                    yy, mm = yy + 1, 1
        return {"plain": plain, "discounted": discounted, "fixed_records": fixed,
                "past_unpaid": past_months, "past_diff": past_diff,
                "old_amount": self.standard_fee_for(effective_ym)}

    # 個人優惠
    def set_discount(self, tid, kind, value, effective_ym, note=""):
        """設定某人自某月起的優惠。kind：none／amount（減額，≥0）／percent（60～95，以 5 為單位）。"""
        if kind not in ("none", "amount", "percent"):
            raise ValueError("優惠類型錯誤。")
        if kind == "amount" and value < 0:
            raise ValueError("減額不可為負數。")
        if kind == "percent" and not (60 <= value <= 95 and int(value) % 5 == 0 and value == int(value)):
            raise ValueError("折數須為 6～9.5 折，以 0.5 折為單位。")
        if kind == "none":
            value = 0
        self.conn.execute(
            "INSERT INTO rent_fee_discount(tenant_id, effective_ym, kind, value, note, created_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(tenant_id, effective_ym) DO UPDATE SET "
            "kind=excluded.kind, value=excluded.value, note=excluded.note",
            (tid, effective_ym, kind, value, note, datetime.now().isoformat()))
        self.conn.commit()


    def _seed_categories(self):
        row = self.conn.execute("SELECT COUNT(*) as cnt FROM categories").fetchone()
        if row and row["cnt"] > 0:
            return

        defaults = [
            ("薪資收入", "income"), ("獎金", "income"), ("投資收益", "income"),
            ("兼職收入", "income"), ("其他收入", "income"),
            ("餐飲", "expense"), ("交通", "expense"), ("購物", "expense"),
            ("娛樂", "expense"), ("醫療", "expense"), ("教育", "expense"),
            ("住房", "expense"), ("水電費", "expense"), ("通訊", "expense"),
            ("旅遊", "expense"), ("保險", "expense"), ("其他支出", "expense"),
            ("月費", "both"),
        ]
        for name, ctype in defaults:
            self.conn.execute(
                "INSERT OR IGNORE INTO categories(name, type) VALUES(?,?)",
                (name, ctype)
            )
        self.conn.commit()

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def add_transaction(self, type_, amount, category, note, date_str):
        # 產生每月序號並格式化為傳票編號，儲存於資料庫中
        # S10：期初餘額、交接轉出不列入傳票編號，使用當月 000 號
        if special_kind(type_, category):
            voucher_no = self._voucher_prefix(date_str) + "000"
        else:
            voucher_no = self._next_voucher_for_month(date_str)
        cur = self.conn.execute(
            "INSERT INTO transactions(type,amount,category,note,date,created_at,voucher_no) VALUES(?,?,?,?,?,?,?)",
            (type_, amount, category, note, date_str, datetime.now().isoformat(), voucher_no)
        )
        self.conn.commit()
        return cur.lastrowid

    def _next_voucher_for_month(self, date_str):
        """回傳指定日期（yyyy-mm-dd）所在月的下一個傳票編號（ROC年+月+3位序號）。

        S5.1：已刪除記帳的傳票編號保留不再使用（刪除紀錄 transaction_deletions 仍保有該編號）。
        下一號＝max（當月現有記帳筆數, 當月記帳與刪除紀錄中已用過的最大序號）＋1。
        沒有刪除、沒有缺號的月份，結果與原本「筆數＋1」完全相同。
        S10：000 號（期初餘額、交接轉出，不列入編號）不算在筆數內。
        原本 6 月有 000～012 共 13 筆，下一號會是 014（跳過 013）；修正後為 013。
        """
        if not date_str or len(date_str) < 7:
            month_key = "0000-00"
        else:
            month_key = date_str[:7]
        prefix = self._voucher_prefix(date_str)
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM transactions WHERE substr(date,1,7)=? "
            "AND COALESCE(voucher_no,'') <> ?",
            (month_key, prefix + "000")
        ).fetchone()
        cnt = int(row["cnt"] or 0)
        used = 0
        for table in ("transactions", "transaction_deletions"):
            r = self.conn.execute(
                f"SELECT MAX(CAST(substr(voucher_no, {len(prefix) + 1}) AS INTEGER)) AS mx "
                f"FROM {table} WHERE voucher_no LIKE ? AND length(voucher_no) = ?",
                (prefix + "%", len(prefix) + 3)
            ).fetchone()
            used = max(used, int(r["mx"] or 0))
        seq = max(cnt, used) + 1
        return f"{prefix}{seq:03d}"

    @staticmethod
    def _voucher_prefix(date_str):
        """傳票編號前 5 碼：民國年 3 碼＋月 2 碼。"""
        try:
            year = int(date_str[:4])
            month = int(date_str[5:7])
        except Exception:
            year = 0
            month = 0
        return f"{year - 1911:03d}{month:02d}"

    def get_transactions(self, filters=None):
        sql = "SELECT * FROM transactions"
        params = []
        if filters:
            clauses = []
            if filters.get("type"):
                clauses.append("type=?"); params.append(filters["type"])
            if filters.get("category"):
                clauses.append("category=?"); params.append(filters["category"])
            if filters.get("date_from"):
                clauses.append("date>=?"); params.append(filters["date_from"])
            if filters.get("date_to"):
                clauses.append("date<=?"); params.append(filters["date_to"])
            if filters.get("year"):
                clauses.append("strftime('%Y',date)=?"); params.append(str(filters["year"]))
            if filters.get("month"):
                clauses.append("strftime('%m',date)=?"); params.append(f"{filters['month']:02d}")
            if filters.get("keyword"):
                # S9：關鍵字比對備註與傳票編號（不分大小寫；% 與 _ 視為一般文字）
                kw = str(filters["keyword"]).strip()
                kw = kw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                clauses.append("(note LIKE ? ESCAPE '\\' OR voucher_no LIKE ? ESCAPE '\\')")
                params += [f"%{kw}%", f"%{kw}%"]
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY date DESC, id DESC"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def get_balance_before(self, date_str, type_=None, category=None):
        sql = "SELECT SUM(CASE WHEN type='income' THEN amount ELSE -amount END) as balance FROM transactions WHERE date < ?"
        params = [date_str]
        if type_:
            sql += " AND type=?"
            params.append(type_)
        if category:
            sql += " AND category=?"
            params.append(category)
        row = self.conn.execute(sql, params).fetchone()
        return float(row["balance"] or 0)

    def delete_transaction(self, tid, deleted_note=""):
        # 先查出原始資料，寫入刪除紀錄
        row = self.conn.execute(
            "SELECT * FROM transactions WHERE id=?", (tid,)
        ).fetchone()
        if row:
            self.conn.execute(
                "INSERT INTO transaction_deletions"
                "(original_id,type,amount,category,note,date,created_at,deleted_at,deleted_note,voucher_no)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    row["id"], row["type"], row["amount"],
                    row["category"], row["note"] or "",
                    row["date"], row["created_at"],
                    datetime.now().isoformat(), deleted_note,
                    row["voucher_no"] if row and "voucher_no" in row.keys() else ""
                )
            )
        # 單向連動：記帳清單這端的異動不回改月費管理（月費管理為主控端）
        self.conn.execute("DELETE FROM transactions WHERE id=?", (tid,))
        self.conn.commit()

    def get_deletions(self, filters=None):
        """查詢刪除紀錄。"""
        sql = "SELECT * FROM transaction_deletions"
        params = []
        clauses = []
        if filters:
            if filters.get("date_from"):
                clauses.append("date>=?"); params.append(filters["date_from"])
            if filters.get("date_to"):
                clauses.append("date<=?"); params.append(filters["date_to"])
            if filters.get("type"):
                clauses.append("type=?"); params.append(filters["type"])
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY deleted_at DESC"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def get_earliest_rent_payment_year(self):
        row = self.conn.execute(
            "SELECT MIN(year) as min_year FROM rent_payments"
        ).fetchone()
        return int(row["min_year"]) if row and row["min_year"] else None

    def update_transaction(self, tid, type_, amount, category, note, date_str):
        self.conn.execute(
            "UPDATE transactions SET type=?,amount=?,category=?,note=?,date=? WHERE id=?",
            (type_, amount, category, note, date_str, tid)
        )
        self.conn.commit()

    # ── Categories ────────────────────────────────────────────────────────────

    def get_categories(self, type_=None):
        if type_:
            rows = self.conn.execute(
                # S10：期初餘額／交接轉出排最後（否則「交接轉出」會是支出的第一個、成為預設分類）
                "SELECT name FROM categories WHERE type=? OR type='both' "
                "ORDER BY name IN (?, ?), name",
                (type_, *SPECIAL_CATEGORIES)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT name,type FROM categories ORDER BY type,name"
            ).fetchall()
        return [dict(r) for r in rows]

    def add_category(self, name, type_):
        """新增分類。S8：回傳結果，不再靜默忽略同名分類。

        回傳 (status, existing_type)：
          ("added", None)            已新增
          ("exists", existing_type)  同名分類已存在（未新增、未修改），由呼叫端決定是否改類型
        """
        existing = self.get_category_type(name)
        if existing is not None:
            return ("exists", existing)
        self.conn.execute(
            "INSERT OR IGNORE INTO categories(name,type) VALUES(?,?)", (name, type_)
        )
        self.conn.commit()
        return ("added", None)

    # S8：分類類型
    CATEGORY_TYPES = ("income", "expense", "both")
    CATEGORY_TYPE_LABELS = {"income": "收入", "expense": "支出", "both": "收支"}

    def get_category_type(self, name):
        row = self.conn.execute(
            "SELECT type FROM categories WHERE name=?", (name,)
        ).fetchone()
        return row["type"] if row else None

    def category_usage(self, name):
        """此分類在記帳中的使用筆數：{"income": n, "expense": n}。"""
        out = {"income": 0, "expense": 0}
        for r in self.conn.execute(
            "SELECT type, COUNT(*) AS n FROM transactions WHERE category=? GROUP BY type",
            (name,)
        ):
            if r["type"] in out:
                out[r["type"]] = r["n"]
        return out

    def check_category_type_change(self, name, new_type):
        """改類型前檢查。回傳 (ok, reason)。

        規則：
          • 改成只有收入／只有支出時，不能還有另一種類型的記帳使用這個分類
            （否則那些記帳在新增／修改畫面的分類選單中會找不到這個分類）。
          • 「月費」必須保留收入（月費連動記帳使用）。
        """
        if new_type not in self.CATEGORY_TYPES:
            return (False, "類型不正確")
        cur = self.get_category_type(name)
        if cur is None:
            return (False, f"找不到分類「{name}」")
        if cur == new_type:
            return (False, f"「{name}」已經是{self.CATEGORY_TYPE_LABELS[new_type]}分類")
        if name in SPECIAL_CATEGORIES:
            fixed = self.CATEGORY_TYPE_LABELS[SPECIAL_CATEGORIES[name]]
            return (False, f"「{name}」固定為{fixed}分類（用於結餘計算，不算在收入／支出內），不能更改類型")
        if name == "月費" and new_type == "expense":
            return (False, "「月費」用於月費連動記帳，必須保留收入，可設為收入或收支")
        if new_type != "both":
            other = "expense" if new_type == "income" else "income"
            n = self.category_usage(name)[other]
            if n:
                return (False,
                        f"「{name}」還有 {n} 筆{self.CATEGORY_TYPE_LABELS[other]}記帳，"
                        f"不能改成只有{self.CATEGORY_TYPE_LABELS[new_type]}；可改為收支")
        return (True, "")

    def set_category_type(self, name, new_type):
        """改分類類型（只改 categories，不動任何記帳）。回傳 (ok, reason)。"""
        ok, reason = self.check_category_type_change(name, new_type)
        if not ok:
            return (ok, reason)
        self.conn.execute("UPDATE categories SET type=? WHERE name=?", (new_type, name))
        self.conn.commit()
        return (True, "")

    def delete_category(self, name):
        """刪除分類。S10：「期初餘額」「交接轉出」不能刪除。回傳 (ok, reason)。"""
        if name in SPECIAL_CATEGORIES:
            return (False, f"「{name}」用於結餘計算，不能刪除")
        self.conn.execute("DELETE FROM categories WHERE name=?", (name,))
        self.conn.commit()
        return (True, "")

    # ── Statistics ────────────────────────────────────────────────────────────
    # S10：收入不含期初、支出不含交接轉出；get_summary 另外回傳期初、轉出與結餘。

    def get_summary(self, filters=None):
        return summarize_rows(self.get_transactions(filters))

    def get_stats_by_year(self):
        sql = f"""
            SELECT strftime('%Y',date) as year,
                   SUM(CASE WHEN {_INCOME_SQL} THEN amount ELSE 0 END) as income,
                   SUM(CASE WHEN {_EXPENSE_SQL} THEN amount ELSE 0 END) as expense,
                   COUNT(*) as count
            FROM transactions GROUP BY year ORDER BY year DESC
        """
        return [dict(r) for r in self.conn.execute(sql).fetchall()]

    def get_stats_by_month(self, year=None):
        where = f"WHERE strftime('%Y',date)='{year}'" if year else ""
        sql = f"""
            SELECT strftime('%Y-%m',date) as month,
                   SUM(CASE WHEN {_INCOME_SQL} THEN amount ELSE 0 END) as income,
                   SUM(CASE WHEN {_EXPENSE_SQL} THEN amount ELSE 0 END) as expense,
                   COUNT(*) as count
            FROM transactions {where} GROUP BY month ORDER BY month DESC
        """
        return [dict(r) for r in self.conn.execute(sql).fetchall()]

    def get_stats_by_day(self, year=None, month=None):
        clauses = []
        if year:  clauses.append(f"strftime('%Y',date)='{year}'")
        if month: clauses.append(f"strftime('%m',date)='{int(month):02d}'")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"""
            SELECT date,
                   SUM(CASE WHEN {_INCOME_SQL} THEN amount ELSE 0 END) as income,
                   SUM(CASE WHEN {_EXPENSE_SQL} THEN amount ELSE 0 END) as expense,
                   COUNT(*) as count
            FROM transactions {where} GROUP BY date ORDER BY date DESC
        """
        return [dict(r) for r in self.conn.execute(sql).fetchall()]

    def get_stats_by_category(self, type_, filters=None):
        rows = self.get_transactions(filters)
        typed = [r for r in rows if r["type"] == type_ and not special_kind(r["type"], r["category"])]
        totals = {}
        for r in typed:
            totals[r["category"]] = totals.get(r["category"], 0) + r["amount"]
        return sorted(totals.items(), key=lambda x: x[1], reverse=True)

    # ── Rent Tenants ──────────────────────────────────────────────────────────

    def get_tenants(self, active_only=True):
        """S3：monthly_rent 為「本月」實際適用金額（標準月費套用個人優惠）；
        另附 discount_kind／discount_value／discount_text。未升級的舊資料庫維持原值。"""
        sql = "SELECT * FROM rent_tenants"
        if active_only:
            sql += " WHERE active=1"
        sql += _TENANT_ORDER_BY
        tenants = [dict(r) for r in self.conn.execute(sql).fetchall()]
        if self._has_fee_tables():
            ym = self._current_ym()
            for t in tenants:
                kind, value = self.discount_for(t["id"], ym)
                t["monthly_rent"] = self.computed_fee(t, ym // 100, ym % 100)
                t["discount_kind"], t["discount_value"] = kind, value
                t["discount_text"] = self.discount_label(kind, value)
        return tenants

    def add_tenant(self, name, monthly_rent=None, note="", grade="", start_ym=None):
        """start_ym：起算月份 YYYYMM；未指定＝本月（S2）。
        monthly_rent：S3 起月費由標準月費決定；未指定時寫入目前標準月費（僅供舊程式相容）。"""
        if start_ym is None:
            start_ym = self._current_ym()
        if monthly_rent is None:
            std = self.standard_fee_for(start_ym) if self._has_fee_tables() else None
            monthly_rent = std if std is not None else 0
        cur = self.conn.execute(
            "INSERT INTO rent_tenants(name,monthly_rent,note,grade,active,created_at,start_ym) "
            "VALUES(?,?,?,?,1,?,?)",
            (name, monthly_rent, note, grade, datetime.now().isoformat(), start_ym)
        )
        self.conn.commit()
        return cur.lastrowid

    def update_tenant(self, tid, name, monthly_rent=None, note="", grade="", start_ym=None):
        """start_ym 未指定＝不變更起算月份；指定時不可晚於此人最早的繳費紀錄（S2）。
        monthly_rent 未指定＝不變更（S3 起月費由標準月費＋優惠決定）。"""
        if start_ym is not None:
            self._validate_start_ym(tid, start_ym)
        self.conn.execute(
            "UPDATE rent_tenants SET name=?,note=?,grade=? WHERE id=?",
            (name, note, grade, tid)
        )
        if monthly_rent is not None:
            self.conn.execute("UPDATE rent_tenants SET monthly_rent=? WHERE id=?", (monthly_rent, tid))
        if start_ym is not None:
            self.conn.execute("UPDATE rent_tenants SET start_ym=? WHERE id=?", (start_ym, tid))
        self.conn.commit()

    def blocking_records_after(self, tid, end_ym):
        """最後收費月份之後的「已繳／免繳」紀錄（停用前必須先手動處理）。"""
        rows = self.conn.execute(
            "SELECT year, month, status FROM rent_payments "
            "WHERE tenant_id=? AND (year*100+month) > ? AND status IN ('paid','exempt') "
            "ORDER BY year, month",
            (tid, end_ym)
        ).fetchall()
        return [dict(r) for r in rows]

    def deactivate_tenant(self, tid, end_ym=None):
        """停用人員並設定最後收費月份（未指定＝本月）。

        Raises ValueError：最後收費月份早於起算月份，或之後仍有已繳／免繳紀錄。
        """
        if end_ym is None:
            end_ym = self._current_ym()
        row = self.conn.execute("SELECT start_ym FROM rent_tenants WHERE id=?", (tid,)).fetchone()
        if row and row["start_ym"] is not None and end_ym < row["start_ym"]:
            raise ValueError(
                f"最後收費月份不可早於起算月份（{self._fmt_ym(row['start_ym'])}）。"
            )
        blocking = self.blocking_records_after(tid, end_ym)
        if blocking:
            label = {"paid": "已繳", "exempt": "免繳"}
            months = "、".join(
                f"{b['year']}-{b['month']:02d}（{label[b['status']]}）" for b in blocking
            )
            raise ValueError(
                f"最後收費月份 {self._fmt_ym(end_ym)} 之後仍有紀錄：\n{months}\n\n"
                "請先到月費管理手動處理這些月份，再停用。"
            )
        self.conn.execute(
            "UPDATE rent_tenants SET active=0, end_ym=? WHERE id=?", (end_ym, tid)
        )
        self.conn.commit()

    def reactivate_tenant(self, tid):
        """恢復在職：active=1、清除最後收費月份；起算月份與所有繳費紀錄不變。"""
        self.conn.execute(
            "UPDATE rent_tenants SET active=1, end_ym=NULL WHERE id=?", (tid,)
        )
        self.conn.commit()

    # ── Rent Payments ─────────────────────────────────────────────────────────

    def get_payments_for_month(self, year, month, grade_filter=None):
        if self._has_billing_period():
            # S2：只列出該月在收費期間內的人（起算月份 ≤ 該月 ≤ 最後收費月份）
            ym = year * 100 + month
            sql = ("SELECT * FROM rent_tenants"
                   " WHERE (start_ym IS NULL OR start_ym <= ?)"
                   " AND (CASE WHEN end_ym IS NOT NULL THEN ? <= end_ym ELSE active = 1 END)")
            params = [ym, ym]
        else:
            # 尚未升級的舊資料庫（例如 Web 讀到的舊版 DB）：維持原行為
            sql = "SELECT * FROM rent_tenants WHERE active=1"
            params = []
        if grade_filter:
            sql += " AND grade=?"
            params.append(grade_filter)
        sql += _TENANT_ORDER_BY
        tenants = [dict(r) for r in self.conn.execute(sql, params).fetchall()]
        result = []
        for t in tenants:
            row = self.conn.execute(
                "SELECT * FROM rent_payments WHERE tenant_id=? AND year=? AND month=?",
                (t["id"], year, month)
            ).fetchone()
            if row:
                status = row["status"]
                paid_date = row["paid_date"]
                note = row["note"]
                payment_id = row["id"]
            else:
                status = "pending"
                paid_date = None
                note = ""
                payment_id = None
            result.append({
                "tenant_id":    t["id"],
                "active":       t["active"],
                "name":         t["name"],
                "grade":        t.get("grade", ""),
                "monthly_rent": self.fee_for_month(t, year, month, row),   # S3：該月實際金額
                "fee_fixed":    bool(row is not None and "amount" in row.keys()
                                     and row["amount"] is not None
                                     and row["status"] in ("paid", "exempt")),
                "tenant_note":  t["note"],
                "status":       status,
                "paid_date":    paid_date,
                "payment_note": note,
                "payment_id":   payment_id,
            })
        return result

    def set_payment_status(self, tenant_id, year, month, status, paid_date=None, note="", sync_transaction=False):
        """設定某人某月繳費狀態。
        
        sync_transaction=False：僅更新月費繳費狀態，不對 transactions 表做任何變更。
        sync_transaction=True：由新增記帳頁面標記月費繳納時，建立/更新對應的收入記帳。
        """
        try:
            self._write_payment_status(tenant_id, year, month, status, paid_date, note, sync_transaction)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def _write_payment_status(self, tenant_id, year, month, status, paid_date=None, note="",
                              sync_transaction=False):
        """set_payment_status 的寫入本體（不 commit），供多月份一次寫入共用（S4）。"""
        existing = self.conn.execute(
            "SELECT * FROM rent_payments "
            "WHERE tenant_id=? AND year=? AND month=?",
            (tenant_id, year, month)
        ).fetchone()
        old_tx_id = existing["transaction_id"] if existing else None
        tx_id = old_tx_id

        # S3：已繳／免繳時固定當月金額；已固定者保留原值；改回未繳時清除
        has_fee = self._has_fee_tables()
        tenant = self.conn.execute(
            "SELECT * FROM rent_tenants WHERE id=?", (tenant_id,)
        ).fetchone()
        fixed_amount = None
        if status in ("paid", "exempt"):
            if (existing is not None and has_fee and existing["status"] in ("paid", "exempt")
                    and existing["amount"] is not None):
                fixed_amount = existing["amount"]
            elif tenant is not None:
                fixed_amount = self.computed_fee(dict(tenant), year, month)

        if status == "paid" and sync_transaction:
            amount = fixed_amount if fixed_amount is not None else 0
            name   = tenant["name"] if tenant else ""
            eff_date = paid_date or datetime.now().strftime("%Y-%m-%d")
            tx_note  = f"{name} {year}年{month:02d}月 月費"
            if note:
                tx_note += f"（{note}）"
            # 確認舊連動記帳是否仍存在（可能已在記帳清單被手動刪除）
            tx_exists = bool(old_tx_id) and self.conn.execute(
                "SELECT 1 FROM transactions WHERE id=?", (old_tx_id,)
            ).fetchone()
            if tx_exists:
                self.conn.execute(
                    "UPDATE transactions SET type='income',amount=?,category='月費',"
                    "note=?,date=? WHERE id=?",
                    (amount, tx_note, eff_date, old_tx_id)
                )
                tx_id = old_tx_id
            else:
                cur = self.conn.execute(
                    "INSERT INTO transactions(type,amount,category,note,date,created_at,voucher_no) "
                    "VALUES('income',?,?,?,?,?,?)",
                    (amount, "月費", tx_note, eff_date, datetime.now().isoformat(),
                     self._next_voucher_for_month(eff_date))   # S5：補傳票編號
                )
                tx_id = cur.lastrowid
            paid_date = eff_date
        elif status != "paid":
            # pending / exempt：不論是否同步，都不保留月費管理頁面產生的連動記帳
            if sync_transaction and old_tx_id:
                self.conn.execute("DELETE FROM transactions WHERE id=?", (old_tx_id,))
            tx_id = None
            paid_date = None

        if existing:
            self.conn.execute(
                "UPDATE rent_payments SET status=?,paid_date=?,note=?,transaction_id=? WHERE id=?",
                (status, paid_date, note, tx_id, existing["id"])
            )
        else:
            self.conn.execute(
                "INSERT INTO rent_payments(tenant_id,year,month,status,paid_date,note,transaction_id) "
                "VALUES(?,?,?,?,?,?,?)",
                (tenant_id, year, month, status, paid_date, note, tx_id)
            )
        if has_fee:
            self.conn.execute(
                "UPDATE rent_payments SET amount=? WHERE tenant_id=? AND year=? AND month=?",
                (fixed_amount, tenant_id, year, month)
            )

    # ── S4：新增記帳「月費連動」逐月驗證 ─────────────────────────────────────

    MAX_FEE_MONTHS = 12

    def first_unpaid_month(self, tid, from_ym=None):
        """此人第一個需要繳費的月份（起算月份起，略過已繳／免繳），最多往後找 10 年。"""
        t = self.conn.execute("SELECT * FROM rent_tenants WHERE id=?", (tid,)).fetchone()
        if t is None:
            return None
        t = dict(t)
        ym = t.get("start_ym") or from_ym or self._current_ym()
        y, m = divmod(ym, 100)
        for _ in range(120):
            if t.get("end_ym") is not None and y * 100 + m > t["end_ym"]:
                return None
            row = self.conn.execute(
                "SELECT status FROM rent_payments WHERE tenant_id=? AND year=? AND month=?",
                (tid, y, m)).fetchone()
            if row is None or row["status"] == "pending":
                return y * 100 + m
            m += 1
            if m > 12:
                y, m = y + 1, 1
        return None

    def plan_fee_payment(self, tid, year, month, amount, max_months=None):
        """新增記帳月費連動的試算（不寫入）。

        從起始月份逐月往後：早於起算月份→拒絕；已繳→拒絕；免繳→略過；超過最後收費月份→拒絕；
        未繳→加入（金額＝該月實際金額）。累加剛好等於輸入金額才允許。
        回傳 dict：ok, months[(y,m,金額)], skipped[(y,m)], total, error, suggestions[(金額, 月數)]
        """
        max_months = max_months or self.MAX_FEE_MONTHS
        res = {"ok": False, "months": [], "skipped": [], "total": 0.0,
               "error": "", "suggestions": []}
        t = self.conn.execute("SELECT * FROM rent_tenants WHERE id=?", (tid,)).fetchone()
        if t is None:
            res["error"] = "找不到這位人員。"
            return res
        t = dict(t)
        if amount <= 0:
            res["error"] = "請輸入金額。"
            return res
        start_ym = t.get("start_ym")
        if start_ym is not None and year * 100 + month < start_ym:
            res["error"] = (f"起算月份是 {self._fmt_ym(start_ym)}，"
                            f"不可登記 {self._fmt_ym(year * 100 + month)} 的月費。")
            return res
        y, m = year, month
        total = 0.0
        cum = []          # 每加入一個月後的累計 (金額, 月數)
        while len(res["months"]) < max_months:
            ym = y * 100 + m
            if t.get("end_ym") is not None and ym > t["end_ym"]:
                res["error"] = f"超過最後收費月份 {self._fmt_ym(t['end_ym'])}。"
                break
            row = self.conn.execute(
                "SELECT * FROM rent_payments WHERE tenant_id=? AND year=? AND month=?",
                (tid, y, m)).fetchone()
            status = row["status"] if row else "pending"
            if status == "paid":
                nxt = self.first_unpaid_month(tid, ym)
                res["error"] = (f"{self._fmt_ym(ym)} 已繳，不會覆蓋既有紀錄。"
                                + (f"第一個未繳月份為 {self._fmt_ym(nxt)}。" if nxt else ""))
                break
            if status == "exempt":
                res["skipped"].append((y, m))
            else:
                fee = self.computed_fee(t, y, m)
                res["months"].append((y, m, fee))
                total += fee
                cum.append((total, len(res["months"])))
                if abs(total - amount) < 0.005:
                    res["ok"] = True
                    res["total"] = total
                    return res
                if total > amount:
                    break
            m += 1
            if m > 12:
                y, m = y + 1, 1
        # 不成立：提供最接近的可用金額
        lower = [c for c in cum if c[0] < amount]
        upper = [c for c in cum if c[0] > amount]
        if lower:
            res["suggestions"].append(lower[-1])
        if upper:
            res["suggestions"].append(upper[0])
        if not res["error"]:
            if len(res["months"]) >= max_months and total < amount:
                res["error"] = f"一次最多登記 {max_months} 個月。"
            else:
                res["error"] = "金額不符，月費只能整月繳納。"
        res["months"], res["total"] = [], 0.0
        return res

    @staticmethod
    def fee_months_label(months, ref_year=None):
        """月份文字（S5）。months：[(y, m), ...]，依時間排序。
        例：[(2026,10)]→「10月」；10～12 月→「10-12月」；12 月～隔年 2 月→「12月-2027年2月」；
        6 月與 9 月（中間略過免繳）→「6、9月」。第一段不寫年份，之後跨到別的年份才寫年份。"""
        if not months:
            return ""
        months = sorted(months)
        # S5.1：ref_year＝記帳日期的年份；月份年份與它不同時就寫出年份（未指定時以第一個月份為準）
        base_year = ref_year if ref_year is not None else months[0][0]
        runs = [[months[0], months[0]]]
        for y, m in months[1:]:
            py, pm = runs[-1][1]
            ny, nm = (py, pm + 1) if pm < 12 else (py + 1, 1)
            if (y, m) == (ny, nm):
                runs[-1][1] = (y, m)
            else:
                runs.append([(y, m), (y, m)])

        def ym_text(y, m):
            return f"{m}月" if y == base_year else f"{y}年{m}月"

        # S8.1：同一年份的相鄰段落共用一次年份與一個「月」
        #   例：2027 年 1 月、3～4 月（2 月免繳）→「2027年1、3-4月」；6 月、8～9 月 →「6、8-9月」
        #   跨年的段落維持原寫法（「12月-2027年2月」）
        parts = []
        group_year, group_segs = None, []

        def flush():
            if group_segs:
                prefix = "" if group_year == base_year else f"{group_year}年"
                parts.append(prefix + "、".join(group_segs) + "月")

        for (y1, m1), (y2, m2) in runs:
            if y1 != y2:                                   # 跨年段落
                flush(); group_year, group_segs = None, []
                parts.append(f"{ym_text(y1, m1)}-{y2}年{m2}月")
                continue
            seg = f"{m1}" if m1 == m2 else f"{m1}-{m2}"
            if y1 != group_year:
                flush(); group_year, group_segs = y1, []
            group_segs.append(seg)
        flush()
        return "、".join(parts)

    def fee_payment_note(self, tid, months, form_note="", record_date=None):
        """月費連動收入記帳備註（S5）：姓名＋月份＋人員備註＋（表單備註）。
        S5.1：record_date（記帳日期 yyyy-mm-dd）的年份與月份年份不同時，月份寫出年份。"""
        t = self.conn.execute("SELECT name, note FROM rent_tenants WHERE id=?", (tid,)).fetchone()
        name = t["name"] if t else ""
        ref_year = int(record_date[:4]) if record_date and len(record_date) >= 4 else None
        text = f"{name}{self.fee_months_label(months, ref_year)}"
        tenant_note = (t["note"] or "").strip() if t else ""
        if tenant_note:
            text += f" {tenant_note}"
        if form_note:
            text += f"（{form_note}）"
        return text

    def pay_fee_months(self, tid, year, month, amount, paid_date, note=""):
        """依 plan_fee_payment 的結果，在同一個 transaction 寫入：

        - 每個月份：已繳＋固定金額（S3），繳費備註＝表單備註
        - 收入記帳：只有 **1 筆**（S5），分類「月費」、金額＝合計、日期＝記帳日期、
          傳票編號＝當月下一號、備註＝fee_payment_note()；所有月份都連結到這一筆
        Raises ValueError：試算不成立（不寫入任何資料）。
        回傳 plan，另加 transaction_id／voucher_no／tx_note。
        """
        plan = self.plan_fee_payment(tid, year, month, amount)
        if not plan["ok"]:
            raise ValueError(plan["error"])
        months = [(y, m) for y, m, _ in plan["months"]]
        tx_note = self.fee_payment_note(tid, months, note, record_date=paid_date)
        try:
            for (y, m) in months:
                self._write_payment_status(tid, y, m, "paid", paid_date=paid_date,
                                           note=note, sync_transaction=False)
            voucher = self._next_voucher_for_month(paid_date)
            cur = self.conn.execute(
                "INSERT INTO transactions(type,amount,category,note,date,created_at,voucher_no) "
                "VALUES('income',?,?,?,?,?,?)",
                (plan["total"], "月費", tx_note, paid_date, datetime.now().isoformat(), voucher))
            tx_id = cur.lastrowid
            for (y, m) in months:
                self.conn.execute(
                    "UPDATE rent_payments SET transaction_id=? WHERE tenant_id=? AND year=? AND month=?",
                    (tx_id, tid, y, m))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        plan.update(transaction_id=tx_id, voucher_no=voucher, tx_note=tx_note)
        return plan

    # ── S12：多人月費收款（M）─────────────────────────────────────────────────

    MULTI_FEE_DEFAULT_CATEGORY = "單日總月"     # 你的決定 Q6

    def plan_fee_by_count(self, tid, year, month, count):
        """S12：從 (year, month) 起登記 count 個要繳的月份（試算，不寫入）。
        規則與 plan_fee_payment（S4）相同：早於起算月份→拒絕；已繳→拒絕；免繳→略過；
        超過最後收費月份→拒絕；未繳→加入（金額＝該月實際金額，含標準月費與個人優惠）。
        回傳 dict：ok, months[(y,m,金額)], skipped[(y,m)], total, error。"""
        res = {"ok": False, "months": [], "skipped": [], "total": 0.0, "error": ""}
        t = self.conn.execute("SELECT * FROM rent_tenants WHERE id=?", (tid,)).fetchone()
        if t is None:
            res["error"] = "找不到這位人員。"
            return res
        t = dict(t)
        count = int(count or 0)
        if count < 1 or count > self.MAX_FEE_MONTHS:
            res["error"] = f"月數必須是 1～{self.MAX_FEE_MONTHS}。"
            return res
        start_ym = t.get("start_ym")
        if start_ym is not None and year * 100 + month < start_ym:
            res["error"] = (f"起算月份是 {self._fmt_ym(start_ym)}，"
                            f"不可登記 {self._fmt_ym(year * 100 + month)} 的月費。")
            return res
        y, m = year, month
        for _ in range(self.MAX_FEE_MONTHS * 3):
            ym = y * 100 + m
            if t.get("end_ym") is not None and ym > t["end_ym"]:
                res["error"] = f"超過最後收費月份 {self._fmt_ym(t['end_ym'])}。"
                break
            row = self.conn.execute(
                "SELECT status FROM rent_payments WHERE tenant_id=? AND year=? AND month=?",
                (tid, y, m)).fetchone()
            status = row["status"] if row else "pending"
            if status == "paid":
                nxt = self.first_unpaid_month(tid, ym)
                res["error"] = (f"{self._fmt_ym(ym)} 已繳，不會覆蓋既有紀錄。"
                                + (f"第一個未繳月份為 {self._fmt_ym(nxt)}。" if nxt else ""))
                break
            if status == "exempt":
                res["skipped"].append((y, m))
            else:
                fee = self.computed_fee(t, y, m)
                res["months"].append((y, m, fee))
                res["total"] += fee
                if len(res["months"]) == count:
                    res["ok"] = True
                    return res
            m += 1
            if m > 12:
                y, m = y + 1, 1
        res["months"], res["total"] = [], 0.0
        return res

    def multi_fee_note(self, rows, record_date=None):
        """S12：多人月費收款的記帳備註（你 10 月以後的寫法）：
        「月費，姓名月份(後五碼)、姓名月份(後五碼)」。月份文字沿用 S5／S8.1 規則；
        後五碼取自人員備註（與 S11.1 特殊收費備註相同），多個帳號全部列出（a/b）。
        rows：[{"tenant_id", "months": [(y, m), ...]}]（依畫面順序）。"""
        from special_charge.manager import SpecialChargeManager
        ref_year = int(record_date[:4]) if record_date and len(record_date) >= 4 else None
        parts = []
        for r in rows:
            t = self.conn.execute("SELECT name, note FROM rent_tenants WHERE id=?",
                                  (r["tenant_id"],)).fetchone()
            if t is None:
                continue
            piece = f"{t['name']}{self.fee_months_label(r['months'], ref_year)}"
            codes = SpecialChargeManager.parse_account_codes(t["note"] or "")
            if codes:
                piece += f"({'/'.join(codes)})"
            parts.append(piece)
        return "月費，" + "、".join(parts) if parts else ""

    def plan_multi_fee(self, entries, paid_date):
        """S12：多人試算。entries：[{"tenant_id", "year", "month", "count"}]。
        回傳 {"ok", "rows": [{..., "plan"}], "total", "errors": [(列序, 姓名, 原因)], "note"}。"""
        out = {"ok": True, "rows": [], "total": 0.0, "errors": [], "note": ""}
        seen = set()
        for i, e in enumerate(entries):
            t = self.conn.execute("SELECT name FROM rent_tenants WHERE id=?", (e["tenant_id"],)).fetchone()
            name = t["name"] if t else f"#{e['tenant_id']}"
            if e["tenant_id"] in seen:
                plan = {"ok": False, "months": [], "skipped": [], "total": 0.0, "error": "同一人重複，請合併成一列。"}
            else:
                plan = self.plan_fee_by_count(e["tenant_id"], e["year"], e["month"], e["count"])
            seen.add(e["tenant_id"])
            out["rows"].append(dict(e, name=name, plan=plan))
            if plan["ok"]:
                out["total"] += plan["total"]
            else:
                out["ok"] = False
                out["errors"].append((i, name, plan["error"]))
        if not entries:
            out["ok"] = False
            out["errors"].append((None, "", "請至少加入一位人員。"))
        out["note"] = self.multi_fee_note(
            [{"tenant_id": r["tenant_id"], "months": [(y, m) for y, m, _ in r["plan"]["months"]]}
             for r in out["rows"] if r["plan"]["ok"]], paid_date)
        return out

    def pay_fee_multi(self, entries, paid_date, category=None, note=None):
        """S12：多人月費收款（同一個 transaction）：
          ・1 筆收入記帳：金額＝合計、分類（預設「單日總月」）、日期＝記帳日期、
            傳票編號＝當月下一號（S5）、備註＝note（未指定時用 multi_fee_note）
          ・每個人的月份：已繳、繳費日期＝記帳日期、連結到這筆記帳（單向，月費管理為主控端）
        任何一人試算不成立 → ValueError（errors 屬性列出每一列原因），不寫入任何資料。"""
        if not category:
            # 預設「單日總月」（Q6）；新資料庫沒有這個分類時用「月費」
            category = (self.MULTI_FEE_DEFAULT_CATEGORY
                        if self.get_category_type(self.MULTI_FEE_DEFAULT_CATEGORY) else "月費")
        ctype = self.get_category_type(category)
        if ctype not in ("income", "both") or special_kind("income", category):
            raise ValueError(f"分類「{category}」不能用於月費收款（必須是一般收入分類）。")
        plan = self.plan_multi_fee(entries, paid_date)
        if not plan["ok"]:
            err = ValueError("；".join(f"{n}：{e}" if n else e for _, n, e in plan["errors"]))
            err.errors = plan["errors"]
            raise err
        tx_note = plan["note"] if note is None else note
        self.conn.commit()
        try:
            for r in plan["rows"]:
                for (y, m, _) in r["plan"]["months"]:
                    self._write_payment_status(r["tenant_id"], y, m, "paid", paid_date=paid_date,
                                               note="", sync_transaction=False)
            voucher = self._next_voucher_for_month(paid_date)
            cur = self.conn.execute(
                "INSERT INTO transactions(type,amount,category,note,date,created_at,voucher_no) "
                "VALUES('income',?,?,?,?,?,?)",
                (plan["total"], category, tx_note, paid_date, datetime.now().isoformat(), voucher))
            tx_id = cur.lastrowid
            for r in plan["rows"]:
                for (y, m, _) in r["plan"]["months"]:
                    self.conn.execute(
                        "UPDATE rent_payments SET transaction_id=? WHERE tenant_id=? AND year=? AND month=?",
                        (tx_id, r["tenant_id"], y, m))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        plan.update(transaction_id=tx_id, voucher_no=voucher, tx_note=tx_note, category=category)
        return plan

    # ── S12：七月升年級（Y）──────────────────────────────────────────────────

    GRADE_SEQUENCE = ("一年級", "二年級", "三年級", "四年級", "五年級", "六年級")
    RETIRE_GRADE = "四年級"          # 三年級升上來的人停用（你的規則）
    GRADE_PROMOTED_KEY = "grade_promoted_year"

    def last_grade_promotion_year(self):
        v = self.get_setting(self.GRADE_PROMOTED_KEY, "")
        return int(v) if str(v).isdigit() else None

    def plan_grade_promotion(self, year, end_month=6):
        """S12：升年級試算（不寫入）。依你的決定：
          ・只處理啟用中的人（Q3）；已停用的人列出、不處理
          ・一→二、二→三：升級；三→四：升級並停用，最後收費月份預設 year 年 end_month 月（Q4）
          ・已經是四年級以上仍啟用（手動恢復收費）：年級＋1，維持啟用（Q5）
          ・年級不是一～六年級、或已是六年級：列出、不處理
          ・最後收費月份之後已有已繳／免繳 → 最後收費月份改成最後一筆的月份（可再改），標示提醒
        回傳 [{"tenant_id","name","grade","new_grade","action","end_ym","warning"}]，
        action：promote／retire／skip_inactive／skip_unknown。依年級（六→一）排序。"""
        end_default = year * 100 + int(end_month)
        rows = []
        tenants = self.conn.execute("SELECT * FROM rent_tenants" + _TENANT_ORDER_BY).fetchall()
        for t in tenants:
            t = dict(t)
            g = (t.get("grade") or "").strip()
            r = {"tenant_id": t["id"], "name": t["name"], "grade": g, "new_grade": g,
                 "action": "", "end_ym": None, "warning": ""}
            if not t["active"]:
                r["action"] = "skip_inactive"
                r["warning"] = "已停用，不處理"
            elif g not in self.GRADE_SEQUENCE or g == self.GRADE_SEQUENCE[-1]:
                r["action"] = "skip_unknown"
                r["warning"] = "年級不是一～五年級，不處理" if g != self.GRADE_SEQUENCE[-1] else "已是六年級，不處理"
            else:
                r["new_grade"] = self.GRADE_SEQUENCE[self.GRADE_SEQUENCE.index(g) + 1]
                if g == "三年級":
                    r["action"] = "retire"
                    end_ym = end_default
                    notes = []
                    if t.get("start_ym") is not None and end_ym < t["start_ym"]:
                        end_ym = t["start_ym"]
                        notes.append(f"起算月份是 {self._fmt_ym(t['start_ym'])}")
                    blocking = self.blocking_records_after(t["id"], end_ym)
                    if blocking:
                        last = blocking[-1]
                        end_ym = last["year"] * 100 + last["month"]
                        notes.append(f"已繳／免繳到 {self._fmt_ym(end_ym)}")
                    r["end_ym"] = end_ym
                    if notes:
                        r["warning"] = "、".join(notes) + f"，最後收費改為 {self._fmt_ym(end_ym)}"
                else:
                    r["action"] = "promote"
            rows.append(r)
        return rows

    def apply_grade_promotion(self, rows, year, backup=True):
        """S12：依試算結果升年級（同一個 transaction；任何一人失敗整批不改）。
        rows：plan_grade_promotion 的結果（畫面可改 retire 的 end_ym）。
        backup：先在 backups 資料夾備份（檔名含「升年級」）。記錄升年級年份（同一年再開會提醒）。
        回傳 {"promoted", "retired", "skipped", "backup"}。"""
        out = {"promoted": 0, "retired": 0, "skipped": 0, "backup": None}
        for r in rows:
            if r["action"] == "retire":
                t = self.conn.execute("SELECT start_ym FROM rent_tenants WHERE id=?", (r["tenant_id"],)).fetchone()
                if t and t["start_ym"] is not None and r["end_ym"] < t["start_ym"]:
                    raise ValueError(f"{r['name']}：最後收費月份不可早於起算月份（{self._fmt_ym(t['start_ym'])}）。")
                blocking = self.blocking_records_after(r["tenant_id"], r["end_ym"])
                if blocking:
                    last = blocking[-1]
                    raise ValueError(f"{r['name']}：最後收費月份 {self._fmt_ym(r['end_ym'])} 之後仍有紀錄"
                                     f"（{last['year']}-{last['month']:02d}），請把最後收費月份改晚。")
        if backup:
            out["backup"] = str(make_backup(self.conn, self.db_path, "_升年級"))
        self.conn.commit()
        try:
            for r in rows:
                if r["action"] == "promote":
                    self.conn.execute("UPDATE rent_tenants SET grade=? WHERE id=?", (r["new_grade"], r["tenant_id"]))
                    out["promoted"] += 1
                elif r["action"] == "retire":
                    self.conn.execute("UPDATE rent_tenants SET grade=?, active=0, end_ym=? WHERE id=?",
                                      (r["new_grade"], r["end_ym"], r["tenant_id"]))
                    out["retired"] += 1
                else:
                    out["skipped"] += 1
            self.conn.execute(
                "INSERT INTO app_settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (self.GRADE_PROMOTED_KEY, str(year)))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return out

    def has_changes(self):
        """S9：這次開啟後是否有任何寫入（新增／修改／刪除）。只是開來查看時為 False。"""
        try:
            return self.conn.total_changes > 0
        except sqlite3.ProgrammingError:
            return False

    def mark_saved(self):
        """S9：記錄最後存檔時間（Web 頁尾顯示用）。"""
        self.set_setting("last_saved_at", datetime.now().strftime("%Y-%m-%d %H:%M"))

    def close(self):
        """S7：關閉前先 commit，確保所有寫入都已在 data.db。可重複呼叫。"""
        try:
            self.conn.commit()
        except sqlite3.ProgrammingError:
            return          # 已關閉
        self.conn.close()

    # ── App Settings ──────────────────────────────────────────────────────────

    def get_setting(self, key, default=""):
        row = self.conn.execute(
            "SELECT value FROM app_settings WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key, value):
        self.conn.execute(
            "INSERT INTO app_settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value)
        )
        self.conn.commit()
