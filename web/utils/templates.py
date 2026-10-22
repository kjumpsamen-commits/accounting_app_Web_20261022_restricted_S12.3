"""
utils/templates.py
───────────────────
全域共用的單一 Jinja2Templates 實例。

所有 route 模組皆從這裡 import `templates`，
避免每個模組各自建立 Jinja2Templates 造成 cache 衝突或設定不一致。
"""
from pathlib import Path
from fastapi.templating import Jinja2Templates
from web.utils.helpers import register_filters

templates = Jinja2Templates(
    directory=str(Path(__file__).parent.parent / "templates")
)
register_filters(templates)
