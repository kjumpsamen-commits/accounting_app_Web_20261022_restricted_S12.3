"""
S9 Web：
  G-8 頁尾顯示「資料最後更新」（桌面關閉時寫入 app_settings.last_saved_at）
  W-1 搜尋引擎不收錄（robots.txt、noindex meta、X-Robots-Tag）
  仍然唯讀：頁尾讀取不寫入、不升級資料庫
"""
import sqlite3

import pytest


@pytest.fixture
def active_path(desktop_db, monkeypatch):
    import web.services.database.manager as mgr
    monkeypatch.setattr(mgr, "get_active_path", lambda: str(desktop_db.db_path))
    return desktop_db


def test_g8_footer_shows_last_saved(client, active_path):
    active_path.set_setting("last_saved_at", "2026-10-21 10:35")
    html = client.get("/").text
    assert "資料最後更新：2026-10-21 10:35（桌面存檔）" in html
    if client.get("/restricted/").status_code == 200:                 # 受限版
        assert "資料最後更新：2026-10-21 10:35" in client.get("/restricted/rent").text


def test_g8_footer_dash_when_missing(client, active_path):
    assert "資料最後更新：—（桌面存檔）" in client.get("/rent").text


def test_g8_footer_dash_when_no_db(client, monkeypatch):
    import web.services.database.manager as mgr

    def boom():
        raise FileNotFoundError("尚未下載")
    monkeypatch.setattr(mgr, "get_active_path", boom)
    assert "資料最後更新：—" in client.get("/").text


def test_g8_footer_read_never_writes(client, active_path):
    active_path.set_setting("last_saved_at", "2026-10-21 10:35")
    path = active_path.db_path
    before = list(sqlite3.connect(str(path)).iterdump())
    for u in ("/", "/rent", "/transactions", "/special-charge", "/tenants"):
        assert client.get(u).status_code == 200
    assert list(sqlite3.connect(str(path)).iterdump()) == before


def test_w1_robots_txt(client):
    r = client.get("/robots.txt")
    assert r.status_code == 200 and "Disallow: /" in r.text and "User-agent: *" in r.text


@pytest.mark.parametrize("url", ["/", "/rent", "/api/rent/tenants", "/robots.txt",
                                 "/export/transactions.xlsx"])
def test_w1_x_robots_tag_everywhere(client, url):
    r = client.get(url)
    assert r.status_code == 200
    assert r.headers.get("X-Robots-Tag") == "noindex, nofollow"


def test_w1_meta_noindex(client):
    assert '<meta name="robots" content="noindex, nofollow">' in client.get("/").text
