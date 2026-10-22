"""
S12.2：受限版網站整站只開放「繳費狀況」與「特殊收費」（伺服器端擋，不只是畫面鎖住）。

問題（使用者回報，部署 S12.1 後）：打開 https://report-submission-i4gx.onrender.com/ 全部按鈕都能按。
原因：受限版 zip 一直同時包含完整版（網址 /）與受限版（/restricted/...），只靠網址開頭判斷要不要鎖，
從根目錄進來就是完整版。S12.1 只修了「受限版頁面上點得到的連結」，沒想到根目錄本身就是完整版。
"""
import re

import pytest

LOCKED_MENU = ["首頁 Dashboard", "全部交易", "收入明細", "支出明細", "統計分析", "Excel 匯出", "人員清單"]
OPEN_MENU = ["繳費狀況", "特殊收費"]


@pytest.fixture
def site(client, monkeypatch):
    monkeypatch.setenv("RESTRICTED_SITE", "true")       # 部署時的預設
    if client.get("/restricted/rent").status_code != 200:
        pytest.skip("完整版沒有 /restricted")
    return client


def _menu(html):
    res = {}
    for name in LOCKED_MENU + OPEN_MENU:
        m = re.search(r'<a class="nav-link([^"]*)"[^>]*>\s*<i[^>]*></i>\s*' + re.escape(name), html)
        res[name] = (m is not None) and "disabled" not in m.group(1)
    return res


def test_default_is_on_when_env_missing(monkeypatch):
    from web.app import restricted_site_enabled
    monkeypatch.delenv("RESTRICTED_SITE", raising=False)
    assert restricted_site_enabled()                     # 沒設定環境變數 → 整站受限
    for v in ("false", "0", "off", "no", "FALSE"):
        monkeypatch.setenv("RESTRICTED_SITE", v)
        assert not restricted_site_enabled()


def test_root_opens_rent_with_only_two_menu_items(site):
    """使用者實際的情境：打開網站根目錄。"""
    r = site.get("/")                                     # 跟著轉址
    assert r.status_code == 200 and str(r.url).endswith("/restricted/rent")
    m = _menu(r.text)
    assert [k for k, v in m.items() if v] == OPEN_MENU, m
    assert "唯讀模式（簡易版）" in r.text


@pytest.mark.parametrize("path, target", [
    ("/", "/restricted/rent"),
    ("/restricted", "/restricted/rent"),
    ("/restricted/", "/restricted/rent"),                # 受限版首頁（有總收支金額）也不開放
    ("/rent", "/restricted/rent"),
    ("/rent?year=2026&month=9", "/restricted/rent"),
    ("/special-charge", "/restricted/special-charge"),
    ("/special-charge?year=2026&activity_id=1", "/restricted/special-charge?year=2026&activity_id=1"),
    ("/special-charge/export.xlsx?activity_id=1", "/restricted/special-charge/export.xlsx?activity_id=1"),
    ("/about", "/restricted/about"),
    ("/admin/settings", "/restricted/admin/settings"),
    ("/admin", "/restricted/admin/settings"),
    ("/admin/login", "/restricted/admin/login"),
    ("/transactions", "/restricted/rent"),
    ("/income", "/restricted/rent"),
    ("/expense", "/restricted/rent"),
    ("/statistics?year=2026", "/restricted/rent"),
    ("/export", "/restricted/rent"),
    ("/tenants", "/restricted/rent"),
    ("/search?q=王", "/restricted/rent"),
    ("/restricted/transactions", "/restricted/rent"),
    ("/restricted/income", "/restricted/rent"),
    ("/restricted/expense", "/restricted/rent"),
    ("/restricted/statistics", "/restricted/rent"),
    ("/restricted/export", "/restricted/rent"),
    ("/restricted/tenants", "/restricted/rent"),
    ("/restricted/search?q=王", "/restricted/rent"),
    ("/no-such-page", "/restricted/rent"),
])
def test_everything_else_redirects(site, path, target):
    r = site.get(path, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == target, (path, r.status_code, r.headers.get("location"))


@pytest.mark.parametrize("path", ["/api/transactions", "/api/transactions/summary", "/api/statistics/by-year",
                                  "/api/rent/tenants", "/api/rent/payments?year=2026&month=7", "/api/categories",
                                  "/api/settings/app_title", "/api/cache-stats", "/api/openapi.json",
                                  "/api/transactions/deletions", "/api/db-reload", "/api/docs", "/api/redoc"])
def test_data_api_blocked(site, path):
    r = site.get(path, follow_redirects=False)
    assert r.status_code == 404, (path, r.status_code)


def test_allowed_pages_work(site):
    assert site.get("/restricted/rent?year=2026&month=7&grade=", follow_redirects=False).status_code == 200
    h = site.get("/restricted/special-charge", follow_redirects=False)
    assert h.status_code == 200
    aid = re.search(r"activity_id=(\d+)", h.text).group(1)
    assert site.get(f"/restricted/special-charge?year=2026&activity_id={aid}", follow_redirects=False).status_code == 200
    x = site.get(f"/restricted/special-charge/export.xlsx?activity_id={aid}", follow_redirects=False)
    assert x.status_code == 200 and x.content[:2] == b"PK"
    assert site.get("/restricted/about", follow_redirects=False).status_code == 200
    assert site.get("/restricted/admin/login", follow_redirects=False).status_code == 200
    assert site.get("/robots.txt").text.startswith("User-agent: *")
    assert site.get("/api/health", follow_redirects=False).status_code in (200, 503)   # Render 健康檢查要能用
    assert site.get("/api/sync-status", follow_redirects=False).status_code == 200
    assert site.get("/static/css/style.css", follow_redirects=False).status_code == 200


def test_admin_login_still_works(site, monkeypatch):
    bcrypt = pytest.importorskip("bcrypt")
    monkeypatch.setenv("ADMIN_USERNAME", "tester")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", bcrypt.hashpw(b"pw-123", bcrypt.gensalt()).decode())
    r = site.post("/admin/login", data={"username": "tester", "password": "pw-123"}, follow_redirects=False)
    assert r.status_code == 404                           # 完整版登入網址不開放
    r = site.post("/restricted/admin/login", data={"username": "tester", "password": "pw-123"}, follow_redirects=False)
    assert r.headers["location"] == "/restricted/admin/settings"
    s = site.get("/restricted/admin/settings", follow_redirects=False)
    assert s.status_code == 200 and "唯讀模式（簡易版）" in s.text
    m = _menu(s.text)
    assert [k for k, v in m.items() if v] == OPEN_MENU


def test_click_everything_on_every_open_page(site):
    """開放的每一頁，畫面上點得到的連結都必須在開放範圍內（不會被轉走）。"""
    from web.app import restricted_redirect_target
    from urllib.parse import urljoin, urlsplit
    for start in ("/restricted/rent", "/restricted/special-charge", "/restricted/about"):
        h = site.get(start).text
        for href in re.findall(r'<a\b(?![^>]*disabled)[^>]*href="([^"#][^"]*)"', h):
            if href.startswith("http"):
                continue
            p = urlsplit(urljoin("http://x" + start, href.replace("&amp;", "&"))).path
            assert restricted_redirect_target(p) is None, (start, href)
