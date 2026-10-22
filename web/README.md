# 個人記帳簿 Web 唯讀查詢系統

FastAPI + Jinja2 + SQLite + Google Drive 同步，支援本機執行 & Render 雲端部署。

## 本機執行

```bash
cd accounting_app_v996_
pip install -r requirements.txt
uvicorn web.app:app --reload --port 8000
```

資料庫自動使用 `~/.accounting_app/data.db`（桌面版共用）。

## Render 部署

1. Push 到 GitHub
2. Render → New Web Service → 連結 repo（自動讀取 `render.yaml`）
3. 在 Render Dashboard → Environment 填入環境變數

## 環境變數

| 變數 | 必填 | 說明 |
|---|---|---|
| GDRIVE_FOLDER_ID | ✅ | Google Drive 資料夾 ID |
| GDRIVE_SERVICE_ACCOUNT | ✅ | Service Account JSON 字串 |
| GDRIVE_DB_FILENAME | ☐ | 資料庫檔名（預設 accounting.db）|
| SYNC_INTERVAL_MINUTES | ☐ | 同步間隔分鐘（預設 30）|
| AUTO_DOWNLOAD | ☐ | 啟動時自動下載（預設 true）|
| DB_LOCAL_PATH | ☐ | 本地 DB 路徑（預設 /tmp/accounting.db）|

## Google Drive 設定

1. GCP 建立 Service Account，下載 JSON 金鑰
2. Google Drive 建立資料夾，上傳 `accounting.db`
3. 資料夾共用給 Service Account email（檢視者權限）
4. 取得 Folder ID（資料夾 URL 最後的字串）
5. 填入 Render 環境變數

## 同步流程

```
啟動 → Google Drive 搜尋 → 下載 .tmp → MD5 驗證 → SQLite 驗證
→ 備份舊 DB → 取代 → 熱替換（無需重啟）
失敗任何步驟 → 保留舊資料庫，記錄 Log
```

## 管理介面

`/admin/settings` — 設定 GDrive、手動同步、查看同步狀態

## 架構說明

詳見 [ARCHITECTURE.md](../Architecture.md)
