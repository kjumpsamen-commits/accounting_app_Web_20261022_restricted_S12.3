"""
S12.1：受限版（/restricted）不可以「點一下就跳回完整版」。

問題（使用者回報）：受限版原本只能按「繳費狀況」與「特殊收費」，但按了繳費狀況之後所有按鈕都解開了。
原因：受限版頁面上還有指向完整版的連結／表單／重導向——
  1. 首頁 /restricted/ 內容區的按鈕（本月月費概況「查看」→ /rent、匯出、統計頁、收入頁、查看全部）
  2. 左側搜尋框 → /search
  3. 管理設定：登入表單、登入後／登出／儲存後的重導向、操作紀錄的「返回」都指向 /admin/...
     （session cookie 也固定只在 /admin 有效，受限版登入後無法留在受限版）
一旦到了完整版網址，左側選單就全部解鎖。

這裡模擬使用者：從受限版首頁出發，把畫面上「點得到」的連結全部點過（含登入後的管理頁），
到達的每一頁都必須仍是受限版、而且左側選單一樣是鎖住的。
"""
import re
from urllib.parse import urljoin, urlsplit

import pytest

LOCKED_MENU = ["首頁 Dashboard", "全部交易", "收入明細", "支出明細", "統計分析", "Excel 匯出", "人員清單"]


def _is_restricted_app(client):
    return client.get("/restricted/rent").status_code == 200


@pytest.fixture
def rclient(client):
    if not _is_restricted_app(client):
        pytest.skip("完整版沒有 /restricted")
    return client


def _clickable(html):
    """畫面上點得到的目標：<a href>（排除 disabled／#）、<form action>（排除鎖住的）、fetch('...')。"""
    out = []
    for m in re.finditer(r"<a\b([^>]*)>", html):
        attrs = m.group(1)
        href = re.search(r'href="([^"]*)"', attrs)
        if not href or href.group(1).startswith(("#", "http", "javascript:")):
            continue
        if re.search(r'class="[^"]*\bdisabled\b', attrs) or "aria-disabled" in attrs:
            continue
        out.append(("a", href.group(1)))
    for m in re.finditer(r"<form\b([^>]*)>", html):
        attrs = m.group(1)
        act = re.search(r'action="([^"]*)"', attrs)
        if act and not act.group(1).startswith("#") and "return false" not in attrs:
            out.append(("form", act.group(1)))
    for m in re.finditer(r"fetch\('([^']+)'", html):
        out.append(("fetch", m.group(1)))
    return [(k, u.replace("&amp;", "&")) for k, u in out]


def _sidebar_locked(html):
    """左側選單：回傳每個鎖住項目是否 disabled。"""
    res = {}
    for name in LOCKED_MENU:
        m = re.search(r'<a class="nav-link([^"]*)"[^>]*>\s*<i[^>]*></i>\s*' + re.escape(name), html)
        res[name] = (not m) or "disabled" in m.group(1)       # S12.3：隱藏（找不到）也算鎖住
    return res


def _login(c, base):
    import bcrypt
    return c.post(f"{base}/admin/login", data={"username": "tester", "password": "pw-123"}, follow_redirects=False)


@pytest.fixture
def admin_env(monkeypatch):
    bcrypt = pytest.importorskip("bcrypt")
    monkeypatch.setenv("ADMIN_USERNAME", "tester")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", bcrypt.hashpw(b"pw-123", bcrypt.gensalt()).decode())


def test_click_everything_stays_restricted(rclient, admin_env):
    """從受限版首頁出發，點過所有點得到的連結（含登入後的管理頁），都留在受限版，選單一直鎖住。"""
    r = _login(rclient, "/restricted")
    assert r.status_code == 303 and r.headers["location"] == "/restricted/admin/settings"
    start = ["/restricted/", "/restricted/rent", "/restricted/special-charge", "/restricted/about",
             "/restricted/admin/settings", "/restricted/admin/audit", "/restricted/admin/help"]
    seen, queue, escapes, unlocked = set(), list(start), [], []
    while queue:
        url = queue.pop(0)
        if url in seen or len(seen) > 120:
            continue
        seen.add(url)
        resp = rclient.get(url, follow_redirects=False)
        if resp.status_code in (301, 302, 303, 307):
            tgt = urljoin(url, resp.headers["location"])
            if not urlsplit(tgt).path.startswith("/restricted"):
                escapes.append((url, "redirect", tgt))
            continue
        if "text/html" not in resp.headers.get("content-type", ""):
            continue
        html = resp.text
        locked = _sidebar_locked(html)
        if not all(locked.values()):
            unlocked.append((url, [k for k, v in locked.items() if not v]))
        for kind, target in _clickable(html):
            full = urljoin(url, target)
            path = urlsplit(full).path
            if path.startswith(("/static/",)):
                continue
            if not path.startswith("/restricted"):
                escapes.append((url, kind, target))
                continue
            if kind == "a" and not path.endswith((".xlsx", "/logout")):
                queue.append(full.replace("http://testserver", "").split("#")[0])
    assert not escapes, f"受限版頁面上有會跳回完整版的地方：{escapes}"
    assert not unlocked, f"受限版有頁面的選單沒有鎖住：{unlocked}"
    # 確實點到了首頁按鈕「查看」→ 受限版月費頁、各個活動、管理頁
    assert "/restricted/rent" in seen and any("activity_id=" in u for u in seen)
    assert len(seen) >= 8                                         # 7 個起點＋活動頁


def test_dashboard_buttons(rclient):
    h = rclient.get("/restricted/").text
    assert 'href="/restricted/rent" class="btn btn-outline-secondary btn-sm">查看' in h        # 本月月費概況「查看」
    for name in ("匯出", "詳細分析", "統計頁", "收入頁", "查看全部"):
        m = re.search(r'<a href="([^"]*)" class="([^"]*)"[^>]*>\s*(?:<i[^>]*></i>\s*)?' + name, h)
        assert m and m.group(1) == "#" and "disabled" in m.group(2), name
    # 完整版首頁不受影響
    f = rclient.get("/").text
    assert 'href="/rent" class="btn btn-outline-secondary btn-sm">查看' in f
    for url in ("/export", "/statistics?year=", "/income?date_from=", "/transactions"):
        assert f'href="{url}' in f
    assert not re.search(r'class="btn[^"]*disabled', f)


def test_search_box_locked_only_in_restricted(rclient):
    """S12.3 改：簡易版的搜尋框整個隱藏（S12.1 時是鎖住）。"""
    h = rclient.get("/restricted/rent").text
    assert "sidebar-search" not in h and 'action="/search"' not in h
    f = rclient.get("/rent").text
    assert '<form method="get" action="/search">' in f and "受限模式無法搜尋" not in f
    assert rclient.get("/search?q=王").status_code == 200                  # 完整版搜尋照常


def test_admin_flow_restricted(rclient, admin_env):
    # 未登入 → 受限版登入頁
    r = rclient.get("/restricted/admin/settings", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/restricted/admin/login"
    assert 'action="login"' in rclient.get("/restricted/admin/login").text
    # 登入 → 留在受限版，cookie 只在 /restricted/admin 有效，登入後頁面正常
    r = _login(rclient, "/restricted")
    assert r.headers["location"] == "/restricted/admin/settings"
    assert "path=/restricted/admin" in r.headers["set-cookie"].lower()
    r = rclient.get("/restricted/admin/settings", follow_redirects=False)
    assert r.status_code == 200 and "唯讀模式（簡易版）" in r.text
    assert "fetch('sync'" in r.text and 'action="settings"' in r.text
    assert 'href="settings"' in rclient.get("/restricted/admin/audit").text
    # 已登入再開登入頁 → 受限版設定頁
    assert rclient.get("/restricted/admin/login", follow_redirects=False).headers["location"] == "/restricted/admin/settings"
    # 同步（JSON）：受限版路徑可用
    assert rclient.get("/restricted/admin/sync-status").status_code in (200, 404)
    # 登出 → 受限版登入頁，cookie 清除
    r = rclient.get("/restricted/admin/logout", follow_redirects=False)
    assert r.headers["location"] == "/restricted/admin/login" and "path=/restricted/admin" in r.headers["set-cookie"].lower()
    assert rclient.get("/restricted/admin/settings", follow_redirects=False).status_code == 303


def test_admin_flow_full_unchanged(rclient, admin_env):
    r = rclient.get("/admin/settings", follow_redirects=False)
    assert r.headers["location"] == "/admin/login"
    r = _login(rclient, "")
    assert r.headers["location"] == "/admin/settings" and "path=/admin" in r.headers["set-cookie"].lower()
    assert "path=/restricted" not in r.headers["set-cookie"].lower()
    r = rclient.get("/admin/settings", follow_redirects=False)
    assert r.status_code == 200 and "唯讀模式（簡易版）" not in r.text
    # 完整版登入不會讓受限版也變成登入（各自獨立）
    assert rclient.get("/restricted/admin/settings", follow_redirects=False).status_code == 303
    r = rclient.post("/admin/login", data={"username": "tester", "password": "bad"}, follow_redirects=False)
    assert r.headers["location"] == "/admin/login?error=invalid"
    r = rclient.post("/restricted/admin/login", data={"username": "tester", "password": "bad"}, follow_redirects=False)
    assert r.headers["location"] == "/restricted/admin/login?error=invalid"
    assert rclient.get("/admin/logout", follow_redirects=False).headers["location"] == "/admin/login"
