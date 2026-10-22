"""
S12 Web：
  A  頁尾顯示版本（與桌面同號），點了開啟公開的「關於」頁（不含 File ID）
  W  管理設定 →「資料來源說明」頁：需要登入；File ID 只顯示前後 4 碼；
     顯示目前來源（環境變數／config.yaml／程式預設值）與要改的檔案行號（自動從檔案找出）
"""
import os
import re

import pytest

FULL_ID = "1q9ERU2odCIrcbMqMCD4v3fwskKrwGEvo"


def _restricted(client):
    return client.get("/restricted/rent").status_code == 200


# ── A：版本與關於 ─────────────────────────────────────────────────────────────

def test_version_module():
    import version
    assert re.fullmatch(r"\d{8} V3\.\d{2}", version.version_text())
    assert version.PRODUCER == "Samen" and version.AUTHOR == "Claude"
    assert version.HISTORY[0][1] == version.APP_VERSION and version.HISTORY[0][0] == version.VERSION_DATE
    assert [h[1] for h in version.HISTORY][-1] == "V3.00"


@pytest.mark.parametrize("url", ["/", "/rent", "/special-charge", "/about"])
def test_footer_shows_version_link(client, url):
    import version
    h = client.get(url).text
    assert f"版本 {version.version_text()}" in h
    assert 'href="/about"' in h


def test_about_page_public_and_without_file_id(client):
    import version
    r = client.get("/about")
    assert r.status_code == 200
    h = r.text
    assert "監製者" in h and "Samen" in h and "程式撰寫者" in h and "Claude" in h
    for d, v, text in version.HISTORY:
        assert v in h and text[:10] in h
    assert FULL_ID not in h and "1q9E" not in h


def test_restricted_about_keeps_restricted_menu(client):
    if not _restricted(client):
        pytest.skip("完整版沒有 /restricted")
    h = client.get("/restricted/rent").text
    assert 'href="/restricted/about"' in h
    r = client.get("/restricted/about")
    assert r.status_code == 200 and "唯讀模式（簡易版）" in r.text and "監製者" in r.text   # S12.3：徽章改「簡易版」


# ── W：資料來源說明 ───────────────────────────────────────────────────────────

@pytest.fixture
def admin(client, monkeypatch):
    bcrypt = pytest.importorskip("bcrypt")
    monkeypatch.setenv("ADMIN_USERNAME", "tester")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", bcrypt.hashpw(b"pw-123", bcrypt.gensalt()).decode())
    r = client.post("/admin/login", data={"username": "tester", "password": "pw-123"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin/settings"
    return client


def test_help_requires_login(client, monkeypatch):
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)
    r = client.get("/admin/help", follow_redirects=False)
    assert r.status_code == 303 and "/admin/login" in r.headers["location"]


def test_help_page_content_masked_with_line_numbers(admin):
    from web.config import settings
    settings.reload()
    h = admin.get("/admin/help").text
    assert "資料來源說明" in h
    assert FULL_ID not in h                                               # 不顯示完整 File ID
    assert "1q9E…GEvo（共 33 碼）" in h
    assert "web/config/config.yaml（程式內附的設定檔）" in h                     # 目前來源
    assert "沒有設定（目前用程式內附的值）" in h
    # 行號自動從檔案找出
    from pathlib import Path
    lines = Path("web/config/settings.py").read_text(encoding="utf-8").splitlines()
    ln = next(i for i, l in enumerate(lines, 1) if '"file_id"' in l)
    assert re.search(rf"web/config/settings.py</td><td>{ln}</td>", h)
    ylines = Path("web/config/config.yaml").read_text(encoding="utf-8").splitlines()
    yln = next(i for i, l in enumerate(ylines, 1) if l.strip().startswith("file_id:"))
    assert re.search(rf"web/config/config.yaml</td><td>{yln}</td>", h)
    assert "GDRIVE_FILE_ID</code>，貼上新的 File ID" in h and "不用改" in h
    assert "15 分鐘" in h and "私人" in h


def test_help_page_detects_env_var(admin, monkeypatch):
    from web.config import settings
    monkeypatch.setenv("GDRIVE_FILE_ID", "ENVabcdefghijklmnopqrstuvwxyz123456")
    settings.reload()
    try:
        h = admin.get("/admin/help").text
        assert "Render 環境變數 GDRIVE_FILE_ID</td>" in h
        assert "ENVa…3456（共 35 碼）" in h and "ENVabcdefghijklmnop" not in h
    finally:
        monkeypatch.delenv("GDRIVE_FILE_ID")
        settings.reload()


def test_settings_page_links_to_help(admin):
    h = admin.get("/admin/settings").text
    assert 'href="help"' in h and "資料來源說明" in h


def test_mask_id():
    from web.services.datasource_help import mask_id
    assert mask_id("") == "（未設定）"
    assert mask_id(FULL_ID) == "1q9E…GEvo（共 33 碼）"
    assert mask_id("short") == "sh…"
    from web.services.datasource_help import mask_text
    assert mask_text(f"下載失敗 uc?export=download&id={FULL_ID} (Proxy)") == "下載失敗 uc?export=download&id=1q9E…GEvo (Proxy)"
