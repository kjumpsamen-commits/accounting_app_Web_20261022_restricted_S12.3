"""
auth/config.py
──────────────
管理者帳號設定。

帳號密碼 100% 來自環境變數，程式碼中沒有任何明文。

必要環境變數：
  ADMIN_USERNAME        管理者帳號（例如 admin）
  ADMIN_PASSWORD_HASH   bcrypt hash（見下方產生方式）

產生 bcrypt hash（本機執行）：
  python -c "import bcrypt; print(bcrypt.hashpw(b'your_password', bcrypt.gensalt()).decode())"

Render 設定：
  Dashboard → Environment → 加入上述兩個變數
"""

import logging
import os

logger = logging.getLogger(__name__)

# ── 從環境變數讀取（不可有預設值！）────────────────────────────────────────

def get_admin_username() -> str | None:
    return os.environ.get("ADMIN_USERNAME", "").strip() or None


def get_admin_password_hash() -> str | None:
    return os.environ.get("ADMIN_PASSWORD_HASH", "").strip() or None


def is_admin_configured() -> bool:
    """檢查環境變數是否已設定。"""
    return bool(get_admin_username() and get_admin_password_hash())


def check_credentials(username: str, password: str) -> bool:
    """
    驗證帳號密碼。
    使用 bcrypt 比對，不儲存或比對明文密碼。
    """
    if not is_admin_configured():
        logger.warning("[auth] 帳號未設定，拒絕登入")
        return False

    stored_hash = get_admin_password_hash()
    stored_user = get_admin_username()

    # 詳細 log（不輸出密碼，只輸出長度）
    logger.info("[auth] 嘗試登入  username=%r  pw_len=%d  stored_user=%r  hash_len=%d",
                username, len(password), stored_user, len(stored_hash) if stored_hash else 0)

    if username != stored_user:
        logger.warning("[auth] 帳號不符  input=%r  stored=%r", username, stored_user)
        return False

    if not stored_hash:
        logger.warning("[auth] ADMIN_PASSWORD_HASH 為空")
        return False

    # 驗證 hash 格式
    if not (stored_hash.startswith("$2b$") or stored_hash.startswith("$2y$") or stored_hash.startswith("$2a$")):
        logger.error("[auth] ADMIN_PASSWORD_HASH 格式錯誤，前10字：%r", stored_hash[:10])
        return False

    try:
        import bcrypt
        result = bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8"))
        logger.info("[auth] bcrypt 驗證結果：%s", result)
        return result
    except Exception as e:
        logger.error("[auth] bcrypt 驗證例外：%s  hash前10字=%r", e, stored_hash[:10])
        return False


def log_startup_check():
    """啟動時在 Console 提示設定狀態，並輸出詳細診斷資訊。"""
    import os
    username = get_admin_username()
    pw_hash  = get_admin_password_hash()

    # 診斷資訊（不輸出密碼，只輸出長度與格式）
    raw_user = os.environ.get("ADMIN_USERNAME", "")
    raw_hash = os.environ.get("ADMIN_PASSWORD_HASH", "")

    logger.info("=== 管理者帳號診斷 ===")
    logger.info("ADMIN_USERNAME  長度=%d  值=%r", len(raw_user), raw_user)
    logger.info("ADMIN_PASSWORD_HASH 長度=%d  前6字=%r  後4字=%r",
                len(raw_hash),
                raw_hash[:6]  if raw_hash else "",
                raw_hash[-4:] if raw_hash else "")
    logger.info("hash 以 $2b$ 開頭: %s", raw_hash.startswith("$2b$") if raw_hash else False)
    logger.info("hash 以 $2y$ 開頭: %s", raw_hash.startswith("$2y$") if raw_hash else False)
    logger.info("is_admin_configured: %s", is_admin_configured())

    if is_admin_configured():
        logger.info("✅ 管理者帳號已設定：ADMIN_USERNAME=%s", username)
    else:
        logger.warning(
            "⚠️  管理者帳號未設定！請在 Render Dashboard 設定：\n"
            "   ADMIN_USERNAME      管理者帳號\n"
            "   ADMIN_PASSWORD_HASH bcrypt 密碼雜湊\n"
            "   未設定前管理頁無法登入，相關 API 回傳 503。"
        )
