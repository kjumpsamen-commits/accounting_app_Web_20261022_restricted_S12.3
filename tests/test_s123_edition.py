"""
S12.3：
  E  網站版本別：頁尾「版本 … ｜完整版／簡易版（受限）」、關於頁「網站版本別／開放範圍」、
     /api/health 的 edition 欄位——部署後用來確認放上去的是哪一個 zip
  H  簡易版只顯示「繳費狀況」「特殊收費」，其他項目整個隱藏；管理項目只在管理頁顯示
（完整版與簡易版共用這份測試，依 web/edition.py 判斷要檢查哪一種）
"""
import re

import pytest


@pytest.fixture
def ed():
    from web import edition
    return edition


@pytest.fixture
def site(client, monkeypatch):
    monkeypatch.setenv("RESTRICTED_SITE", "true")          # 部署時的預設（完整版沒有這個開關，無影響）
    return client


def test_edition_file(ed):
    assert (ed.EDITION, ed.EDITION_NAME) in (("full", "完整版"), ("restricted", "簡易版（受限）"))
    import os
    restricted_app = os.path.exists("web/app.py") and "RestrictedSiteMiddleware" in open("web/app.py", encoding="utf-8").read()
    assert (ed.EDITION == "restricted") == restricted_app            # 版本別與程式內容一致（不會標錯）


def test_footer_shows_edition(site, ed):
    import version
    url = "/restricted/rent" if ed.EDITION == "restricted" else "/rent"
    h = site.get(url).text
    assert f"版本 {version.version_text()}｜{ed.EDITION_NAME}</a>" in h


def test_about_page_shows_edition(site, ed):
    url = "/restricted/about" if ed.EDITION == "restricted" else "/about"
    h = site.get(url).text
    assert f'網站版本別</th><td class="fw-semibold">{ed.EDITION_NAME}</td>' in h
    assert f"開放範圍</th><td>{ed.EDITION_SCOPE}</td>" in h
    if ed.EDITION == "restricted":
        assert "只開放「繳費狀況」與「特殊收費」" in h
        assert "S12.3" in h and "S12.2" in h and "S12.1" in h            # 版本說明含簡易版的修正紀錄


def test_health_reports_edition(site, ed):
    import version
    j = site.get("/api/health").json()
    assert j["app_version"] == version.version_text()
    assert j["edition"] == ed.EDITION and j["edition_name"] == ed.EDITION_NAME
    assert j["restricted_site"] is (ed.EDITION == "restricted")


def test_restricted_sidebar_only_two(site, ed):
    if ed.EDITION != "restricted":
        pytest.skip("簡易版才檢查")
    for url in ("/restricted/rent", "/restricted/special-charge", "/restricted/about"):
        h = site.get(url).text
        side = h[h.index('<aside class="sidebar"'):h.index("</aside>")]
        labels = [m.strip() for m in re.findall(r'class="nav-link[^"]*"[^>]*>\s*<i[^>]*></i>\s*([^\n<]+)', side)]
        assert labels == ["繳費狀況", "特殊收費"], (url, labels)
        assert "disabled" not in h.replace("data-bs-toggle", ""), url          # 整頁沒有任何灰色鎖住的按鈕
        assert "sidebar-search" not in h and "唯讀模式（簡易版）" in h


def test_restricted_admin_pages_show_system_items(site, ed, monkeypatch):
    if ed.EDITION != "restricted":
        pytest.skip("簡易版才檢查")
    bcrypt = pytest.importorskip("bcrypt")
    monkeypatch.setenv("ADMIN_USERNAME", "tester")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", bcrypt.hashpw(b"pw-123", bcrypt.gensalt()).decode())
    site.post("/restricted/admin/login", data={"username": "tester", "password": "pw-123"})
    h = site.get("/restricted/admin/settings").text
    side = h[h.index('<aside class="sidebar"'):h.index("</aside>")]
    labels = [m.strip() for m in re.findall(r'class="nav-link[^"]*"[^>]*>\s*<i[^>]*></i>\s*([^\n<]+)', side)]
    assert labels == ["繳費狀況", "特殊收費", "管理設定", "登入紀錄", "登出"]
    assert 'href="/restricted/admin/logout"' in side


def test_full_sidebar_unchanged(client, ed):
    """完整版頁面：12 項選單與搜尋框照舊（簡易版的測試環境關閉整站受限時，"/" 也用同一份樣板）。"""
    h = client.get("/").text
    side = h[h.index('<aside class="sidebar"'):h.index("</aside>")]
    labels = [m.strip() for m in re.findall(r'class="nav-link[^"]*"[^>]*>\s*<i[^>]*></i>\s*([^\n<]+)', side)]
    assert labels == ["首頁 Dashboard", "全部交易", "收入明細", "支出明細", "統計分析", "Excel 匯出",
                      "繳費狀況", "人員清單", "特殊收費", "管理設定", "登入紀錄", "登出"]
    assert '<form method="get" action="/search">' in h
