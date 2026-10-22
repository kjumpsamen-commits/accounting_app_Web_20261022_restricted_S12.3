"""
config/settings.py
──────────────────
中央設定管理器。

來源優先順序（高 → 低）：
  1. 環境變數（Render Dashboard 設定）
  2. config.yaml（管理介面儲存）
  3. 預設值

環境變數列表：
  GDRIVE_FOLDER_ID        Google Drive 資料夾 ID
  GDRIVE_DB_FILENAME      資料庫檔名（預設 accounting.db）
  GDRIVE_SERVICE_ACCOUNT  Google Service Account JSON（整串 JSON 字串）
  SYNC_INTERVAL_MINUTES   自動同步間隔（分鐘）
  DB_LOCAL_PATH           本機資料庫路徑（預設 /tmp/accounting.db）
  AUTO_DOWNLOAD           啟動時自動下載（true/false）
"""

import os
import yaml
import threading
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent / "config.yaml"
_lock = threading.Lock()

_DEFAULTS: dict = {
    "render": {
        "auto_download": True,
    },
    "database": {
        "local_path": "/tmp/accounting.db",
        "fallback_path": str(Path(__file__).parent.parent.parent / ".accounting_app" / "data.db"),
    },
    "google_drive": {
        # 預設公開分享的 data.db
        # 來源：https://drive.google.com/file/d/1q9ERU2odCIrcbMqMCD4v3fwskKrwGEvo/view
        "file_id": "1q9ERU2odCIrcbMqMCD4v3fwskKrwGEvo",
        "folder_id": "",
        "database_filename": "accounting.db",
    },
    "sync": {
        "interval_minutes": 30,
        "enabled": True,
    },
    "admin": {
        "password": "",
    },
}


def _load_yaml() -> dict:
    if _CONFIG_PATH.exists():
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception:
            return {}
    return {}


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


class Settings:
    """執行緒安全的設定管理器（Singleton）。"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._loaded = False
        return cls._instance

    def reload(self):
        with _lock:
            yaml_cfg  = _load_yaml()
            self._cfg = _deep_merge(_DEFAULTS, yaml_cfg)
            # 環境變數覆蓋
            self._apply_env()
            self._loaded = True

    def _apply_env(self):
        env = os.environ
        if env.get("GDRIVE_FOLDER_ID"):
            self._cfg["google_drive"]["folder_id"] = env["GDRIVE_FOLDER_ID"]
        if env.get("GDRIVE_FILE_ID"):
            self._cfg["google_drive"]["file_id"] = env["GDRIVE_FILE_ID"]
        if env.get("GDRIVE_DB_FILENAME"):
            self._cfg["google_drive"]["database_filename"] = env["GDRIVE_DB_FILENAME"]
        if env.get("SYNC_INTERVAL_MINUTES"):
            try:
                self._cfg["sync"]["interval_minutes"] = int(env["SYNC_INTERVAL_MINUTES"])
            except ValueError:
                pass
        if env.get("DB_LOCAL_PATH"):
            self._cfg["database"]["local_path"] = env["DB_LOCAL_PATH"]
        if env.get("AUTO_DOWNLOAD", "").lower() in ("false", "0", "no"):
            self._cfg["render"]["auto_download"] = False

    def _ensure(self):
        if not getattr(self, "_loaded", False):
            self.reload()

    # ── 讀取方法 ──────────────────────────────────────────────────────────────

    @property
    def db_local_path(self) -> str:
        self._ensure()
        return self._cfg["database"]["local_path"]

    @property
    def db_fallback_path(self) -> str:
        self._ensure()
        return self._cfg["database"]["fallback_path"]

    @property
    def gdrive_file_id(self) -> str:
        """Google Drive 公開分享的 File ID（模式 A，不需要 Google Cloud）。"""
        self._ensure()
        return self._cfg["google_drive"].get("file_id", "")

    @property
    def gdrive_folder_id(self) -> str:
        self._ensure()
        return self._cfg["google_drive"]["folder_id"]

    @property
    def gdrive_db_filename(self) -> str:
        self._ensure()
        return self._cfg["google_drive"]["database_filename"]

    @property
    def sync_interval_minutes(self) -> int:
        self._ensure()
        return int(self._cfg["sync"]["interval_minutes"])

    @property
    def sync_enabled(self) -> bool:
        self._ensure()
        return bool(self._cfg["sync"]["enabled"])

    @property
    def auto_download(self) -> bool:
        self._ensure()
        return bool(self._cfg["render"]["auto_download"])

    @property
    def gdrive_service_account_json(self) -> str:
        """Google Service Account JSON 字串（從環境變數讀取）。"""
        return os.environ.get("GDRIVE_SERVICE_ACCOUNT", "")

    def as_dict(self) -> dict:
        self._ensure()
        import copy
        return copy.deepcopy(self._cfg)

    # ── 寫入方法（管理介面使用）──────────────────────────────────────────────

    def update(self, **kwargs):
        """
        更新設定並寫回 config.yaml。
        支援的 key：file_id, folder_id, db_filename, interval_minutes, sync_enabled, auto_download
        """
        self._ensure()
        with _lock:
            if "file_id" in kwargs:
                self._cfg["google_drive"]["file_id"] = kwargs["file_id"]
            if "folder_id" in kwargs:
                self._cfg["google_drive"]["folder_id"] = kwargs["folder_id"]
            if "db_filename" in kwargs:
                self._cfg["google_drive"]["database_filename"] = kwargs["db_filename"]
            if "interval_minutes" in kwargs:
                self._cfg["sync"]["interval_minutes"] = int(kwargs["interval_minutes"])
            if "sync_enabled" in kwargs:
                self._cfg["sync"]["enabled"] = bool(kwargs["sync_enabled"])
            if "auto_download" in kwargs:
                self._cfg["render"]["auto_download"] = bool(kwargs["auto_download"])
            self._save()

    def _save(self):
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(self._cfg, f, allow_unicode=True, default_flow_style=False)


# Singleton 實例
settings = Settings()
