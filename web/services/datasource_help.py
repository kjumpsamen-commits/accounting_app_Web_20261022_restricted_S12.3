"""
S12：Web「資料來源說明」頁需要的資訊（只讀，不改任何設定）。

File ID 的取得順序與 web/config/settings.py 相同：
  1. Render 環境變數 GDRIVE_FILE_ID
  2. web/config/config.yaml 的 google_drive.file_id
  3. web/config/settings.py 的程式預設值（_DEFAULTS）
行號在每次開頁面時直接讀檔案找出，程式改動後說明也不會過時。
"""

import os
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent          # 專案根目錄
SETTINGS_PY = Path("web/config/settings.py")
CONFIG_YAML = Path("web/config/config.yaml")
RENDER_YAML = Path("render.yaml")


def mask_id(file_id: str) -> str:
    """只顯示前 4 碼與後 4 碼（File ID 等於資料的鑰匙，頁面上不顯示完整內容）。"""
    file_id = (file_id or "").strip()
    if not file_id:
        return "（未設定）"
    if len(file_id) <= 10:
        return file_id[:2] + "…"
    return f"{file_id[:4]}…{file_id[-4:]}（共 {len(file_id)} 碼）"


def mask_text(text: str) -> str:
    """把文字中所有像 File ID 的長字串（25 碼以上）遮蔽，例如同步失敗訊息中的下載網址。"""
    return re.sub(r"[A-Za-z0-9_-]{25,}", lambda m: mask_id(m.group(0)).split("（")[0], text or "")


def _lines_matching(rel_path: Path, pattern: str):
    """回傳 [(行號, 該行內容（File ID 遮蔽）)]。"""
    p = _ROOT / rel_path
    if not p.exists():
        return []
    out = []
    rx = re.compile(pattern)
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        if rx.search(line):
            masked = mask_text(line.strip())
            out.append((i, masked))
    return out


def _yaml_file_id():
    try:
        import yaml
        p = _ROOT / CONFIG_YAML
        if not p.exists():
            return ""
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return ((data.get("google_drive") or {}).get("file_id") or "").strip()
    except Exception:
        return ""


def _default_file_id():
    try:
        from web.config.settings import _DEFAULTS
        return (_DEFAULTS.get("google_drive", {}).get("file_id") or "").strip()
    except Exception:
        return ""


def describe():
    """說明頁的資料。effective＝目前生效的 File ID（只回傳遮蔽後的文字）。"""
    from web.config import settings
    env_id = os.environ.get("GDRIVE_FILE_ID", "").strip()
    yaml_id = _yaml_file_id()
    default_id = _default_file_id()
    effective = (settings.gdrive_file_id or "").strip()
    if env_id and effective == env_id:
        source = "env"
    elif yaml_id and effective == yaml_id:
        source = "yaml"
    elif effective and effective == default_id:
        source = "default"
    else:
        source = "runtime"                 # 管理設定頁剛存過、尚未重新啟動
    return {
        "effective": mask_id(effective),
        "source": source,
        "env_set": bool(env_id),
        "env": mask_id(env_id) if env_id else "",
        "yaml": mask_id(yaml_id) if yaml_id else "",
        "default": mask_id(default_id) if default_id else "",
        "same_as_default": bool(effective) and effective == default_id,
        "folder_mode": bool((settings.gdrive_folder_id or "").strip()),
        "settings_lines": _lines_matching(SETTINGS_PY, r'"file_id"\s*:|drive\.google\.com/file/d/'),
        "yaml_lines": _lines_matching(CONFIG_YAML, r"^\s*file_id\s*:|drive\.google\.com/file/d/"),
        "render_lines": _lines_matching(RENDER_YAML, r"GDRIVE_FILE_ID"),
        "settings_path": str(SETTINGS_PY), "yaml_path": str(CONFIG_YAML), "render_path": str(RENDER_YAML),
    }
