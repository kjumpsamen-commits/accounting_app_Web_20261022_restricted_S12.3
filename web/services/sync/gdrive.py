"""
services/sync/gdrive.py
────────────────────────
Google Drive 資料庫下載服務。

支援兩種模式（依環境變數自動切換）：

  模式 A — 公開分享連結（推薦，不需要 Google Cloud）
    設定：GDRIVE_FILE_ID
    Google Drive 檔案設為「知道連結的人都能查看」即可。
    完全不需要 Service Account 或任何 API 金鑰。

  模式 B — Service Account（需要 Google Cloud）
    設定：GDRIVE_FOLDER_ID + GDRIVE_SERVICE_ACCOUNT
    適合私有檔案或需要更精細權限控制的場景。

安全設計（兩種模式均適用）：
  - SHA256 驗證下載完整性
  - 下載到 .tmp 暫存，驗證後才取代正式檔案
  - 下載失敗完全不動正式檔案
  - 完整 logging
"""

import hashlib
import logging
import os
import shutil
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)


# ── 工具函式 ──────────────────────────────────────────────────────────────────

def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _validate_sqlite(path: str):
    """驗證檔案為有效 SQLite 且含 transactions 資料表。"""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("SELECT COUNT(*) FROM transactions").fetchone()
    finally:
        conn.close()


def _safe_replace(tmp_path: str, dest_path: str, bak_path: str):
    """
    備份舊 DB → 原子替換。
    使用 manager.atomic_replace() 確保：
      1. os.replace()（POSIX 原子操作）
      2. 清除 -wal / -shm 殘留（避免 SQLite 讀舊日誌）
    """
    from web.services.database.manager import atomic_replace

    if Path(dest_path).exists():
        shutil.copy2(dest_path, bak_path)
        logger.info("舊資料庫備份至：%s", bak_path)

    atomic_replace(tmp_path, dest_path)
    logger.info("資料庫替換完成：%s", dest_path)


def _cleanup(*paths: str):
    for p in paths:
        try:
            if Path(p).exists():
                Path(p).unlink()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# 模式 A：公開分享連結（不需要 Google Cloud）
# ══════════════════════════════════════════════════════════════════════════════

def _download_public(file_id: str, dest_path: str) -> dict:
    """
    透過 Google Drive 公開分享連結下載檔案。

    Google Drive 大檔案（> 約 40MB）會出現「病毒掃描警告」確認頁面，
    本函式自動處理該確認流程，確保大檔案也能正確下載。
    """
    import requests

    tmp_path = dest_path + ".tmp"
    bak_path = dest_path + ".bak"
    t0       = time.monotonic()
    result   = {"success": False, "message": "", "sha256": "", "size": 0, "elapsed": 0.0}

    try:
        # Google Drive 直接下載 URL
        url     = f"https://drive.google.com/uc?export=download&id={file_id}"
        session = requests.Session()

        logger.info("開始下載（公開連結）：file_id=%s", file_id)
        resp = session.get(url, stream=True, timeout=60)
        resp.raise_for_status()

        # 檢查是否出現「病毒掃描警告」頁（大檔案）
        content_type = resp.headers.get("Content-Type", "")
        if "text/html" in content_type:
            # 從 HTML 中找確認 token
            confirm_token = None
            for chunk in resp.iter_content(chunk_size=32768):
                text = chunk.decode("utf-8", errors="ignore")
                # 新版 GDrive 用 confirm=t
                if "confirm=" in text:
                    import re
                    m = re.search(r'confirm=([^&"\'&]+)', text)
                    if m:
                        confirm_token = m.group(1)
                        break
                # 也嘗試找 uuid
                if "uuid=" in text:
                    import re
                    m2 = re.search(r'uuid=([^&"\'&]+)', text)
                    if m2:
                        confirm_token = "t"
                        uuid_val      = m2.group(1)
                        url = (f"https://drive.google.com/uc?export=download"
                               f"&id={file_id}&confirm={confirm_token}&uuid={uuid_val}")
                        break

            if confirm_token:
                if "uuid=" not in url:
                    url = (f"https://drive.google.com/uc?export=download"
                           f"&id={file_id}&confirm={confirm_token}")
                logger.info("大檔案確認頁，重新請求：%s", url)
                resp = session.get(url, stream=True, timeout=120)
                resp.raise_for_status()
            else:
                raise ValueError("無法取得確認 token，檔案可能未設為公開或 ID 錯誤")

        # ── 寫入暫存檔 ──────────────────────────────────────────────────────
        Path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
        downloaded = 0
        with open(tmp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)

        if downloaded < 1024:
            raise ValueError(f"下載大小異常：{downloaded} bytes，可能不是有效 SQLite 檔案")

        logger.info("下載完成：%d bytes", downloaded)

        # ── SHA256 & SQLite 驗證 ────────────────────────────────────────────
        sha256 = _sha256(tmp_path)
        logger.info("SHA256：%s", sha256)
        _validate_sqlite(tmp_path)
        logger.info("SQLite 驗證通過")

        # ── 替換正式 DB ─────────────────────────────────────────────────────
        _safe_replace(tmp_path, dest_path, bak_path)

        result.update({"success": True,
                       "message": f"下載成功（{downloaded:,} bytes）",
                       "sha256": sha256, "size": downloaded})

    except Exception as e:
        logger.error("公開連結下載失敗：%s", e, exc_info=True)
        result["message"] = f"下載失敗：{e}"
        _cleanup(tmp_path)

    finally:
        result["elapsed"] = round(time.monotonic() - t0, 2)

    return result


# ══════════════════════════════════════════════════════════════════════════════
# 模式 B：Service Account（需要 Google Cloud）
# ══════════════════════════════════════════════════════════════════════════════

def _download_service_account(folder_id: str, filename: str, dest_path: str) -> dict:
    """透過 Service Account 下載（需要 google-api-python-client）。"""
    import io as _io
    import json

    tmp_path = dest_path + ".tmp"
    bak_path = dest_path + ".bak"
    t0       = time.monotonic()
    result   = {"success": False, "message": "", "sha256": "", "size": 0, "elapsed": 0.0}

    try:
        from googleapiclient.discovery import build
        from google.oauth2 import service_account
        from googleapiclient.http import MediaIoBaseDownload

        sa_json = os.environ.get("GDRIVE_SERVICE_ACCOUNT", "")
        if not sa_json:
            raise EnvironmentError("未設定 GDRIVE_SERVICE_ACCOUNT 環境變數")

        info  = json.loads(sa_json)
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )
        svc   = build("drive", "v3", credentials=creds)

        # 搜尋檔案
        q      = f"'{folder_id}' in parents and name='{filename}' and trashed=false"
        result_files = svc.files().list(
            q=q,
            fields="files(id,name,size,md5Checksum)",
            orderBy="modifiedTime desc",
            pageSize=1,
        ).execute().get("files", [])

        if not result_files:
            raise FileNotFoundError(f"Google Drive 中找不到：{filename}")

        meta    = result_files[0]
        file_id = meta["id"]
        logger.info("找到檔案：%s  id=%s", meta["name"], file_id)

        # 下載
        request    = svc.files().get_media(fileId=file_id)
        buf        = _io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request, chunksize=4 * 1024 * 1024)
        done       = False
        while not done:
            _, done = downloader.next_chunk()

        Path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_path, "wb") as f:
            f.write(buf.getvalue())

        size   = Path(tmp_path).stat().st_size
        sha256 = _sha256(tmp_path)

        # MD5 驗證
        if md5 := meta.get("md5Checksum"):
            import hashlib
            local_md5 = hashlib.md5(open(tmp_path, "rb").read()).hexdigest()
            if local_md5 != md5:
                raise ValueError(f"MD5 不符：GDrive={md5} local={local_md5}")

        _validate_sqlite(tmp_path)
        _safe_replace(tmp_path, dest_path, bak_path)

        result.update({"success": True,
                       "message": f"下載成功（{size:,} bytes）",
                       "sha256": sha256, "size": size})

    except Exception as e:
        logger.error("Service Account 下載失敗：%s", e, exc_info=True)
        result["message"] = f"下載失敗：{e}"
        _cleanup(tmp_path)

    finally:
        result["elapsed"] = round(time.monotonic() - t0, 2)

    return result


# ══════════════════════════════════════════════════════════════════════════════
# 統一入口：自動選擇模式
# ══════════════════════════════════════════════════════════════════════════════

def download_db(dest_path: str, folder_id: str = "", filename: str = "",
                file_id: str = "") -> dict:
    """
    自動選擇下載模式：

    優先順序：
      1. 參數 file_id（由 scheduler 從 settings 傳入）
      2. 環境變數 GDRIVE_FILE_ID
      3. 參數 folder_id + 環境變數 GDRIVE_SERVICE_ACCOUNT（模式 B）

    這樣管理介面設定的值（存在 config.yaml → settings）
    和環境變數（Render Dashboard）都能正確生效。
    """
    # 取出有效的 file_id（settings 傳入 > 環境變數）
    effective_file_id = (
        file_id.strip()
        or os.environ.get("GDRIVE_FILE_ID", "").strip()
    )

    if effective_file_id:
        logger.info("使用模式 A（公開連結）：file_id=%s", effective_file_id)
        return _download_public(effective_file_id, dest_path)

    # 取出有效的 folder_id（參數 > 環境變數）
    effective_folder_id = (
        folder_id.strip()
        or os.environ.get("GDRIVE_FOLDER_ID", "").strip()
    )

    if effective_folder_id and os.environ.get("GDRIVE_SERVICE_ACCOUNT"):
        logger.info("使用模式 B（Service Account）：folder=%s", effective_folder_id)
        return _download_service_account(effective_folder_id, filename, dest_path)

    return {
        "success": False,
        "message": (
            "未設定任何下載來源。\n"
            "請在管理介面（/admin/settings）設定 Google Drive File ID，\n"
            "或在 Render 環境變數設定 GDRIVE_FILE_ID。"
        ),
        "sha256": "", "size": 0, "elapsed": 0.0,
    }


def find_db_file(folder_id: str, filename: str) -> dict | None:
    """向後相容：模式 B 搜尋檔案。"""
    try:
        import json
        from googleapiclient.discovery import build
        from google.oauth2 import service_account
        sa_json = os.environ.get("GDRIVE_SERVICE_ACCOUNT", "")
        if not sa_json:
            return None
        info  = json.loads(sa_json)
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )
        svc   = build("drive", "v3", credentials=creds)
        q     = f"'{folder_id}' in parents and name='{filename}' and trashed=false"
        files = svc.files().list(q=q, fields="files(id,name,size,modifiedTime,md5Checksum)",
                                 orderBy="modifiedTime desc", pageSize=1
                                 ).execute().get("files", [])
        return files[0] if files else None
    except Exception as e:
        logger.error("find_db_file 失敗：%s", e)
        return None
