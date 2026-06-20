"""认证服务：密码哈希、token 签发与验证、用户管理。

Token 方案：HMAC-SHA256 签名的 JSON payload，含 user_id / username / exp。
前端刷新页面后从 localStorage 读取 token 即可恢复登录态。
"""

import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime
from functools import wraps
from typing import Optional

from fastapi import Header, HTTPException

from app.config import JWT_SECRET, USERS_FILE
from app.services.storage import read_json, write_json

logger = logging.getLogger(__name__)

# ── 密码哈希（PBKDF2-HMAC-SHA256）────────────────────────────────
_SALT_LENGTH = 32
_HASH_ITERATIONS = 100_000


def hash_password(password: str) -> str:
    """单向哈希密码，格式: salt$iterations$hex_hash。"""
    salt = os.urandom(_SALT_LENGTH)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _HASH_ITERATIONS)
    return f"{salt.hex()}${_HASH_ITERATIONS}${dk.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    """验证密码是否匹配已存储的哈希值。"""
    try:
        salt_hex, iterations_str, dk_hex = hashed.split("$")
        salt = bytes.fromhex(salt_hex)
        iterations = int(iterations_str)
        expected = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(expected.hex(), dk_hex)
    except Exception:
        return False


# ── Token 签发与验证 ─────────────────────────────────────────────
_TOKEN_TTL = 7 * 24 * 3600  # 7 天

_HEADER = json.dumps({"alg": "HS256", "typ": "JWT"}).encode("utf-8")


def _b64url(data: bytes) -> str:
    return data.hex()


def _b64url_decode(s: str) -> bytes:
    return bytes.fromhex(s)


def create_token(user_id: str, username: str) -> str:
    """签发登录 token（HMAC-SHA256 签名）。"""
    payload = json.dumps({
        "user_id": user_id,
        "username": username,
        "exp": int(time.time()) + _TOKEN_TTL,
        "iat": int(time.time()),
    }).encode("utf-8")

    header_b64 = _b64url(_HEADER)
    payload_b64 = _b64url(payload)
    signing_input = f"{header_b64}.{payload_b64}"

    signature = hmac.new(
        JWT_SECRET.encode("utf-8"),
        signing_input.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return f"{header_b64}.{payload_b64}.{signature}"


def verify_token(token: str) -> Optional[dict]:
    """验证 token 并返回 payload，失效/伪造返回 None。"""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        header_b64, payload_b64, signature = parts
        signing_input = f"{header_b64}.{payload_b64}"
        expected_sig = hmac.new(
            JWT_SECRET.encode("utf-8"),
            signing_input.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected_sig, signature):
            return None

        payload = json.loads(_b64url_decode(payload_b64))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


# ── 用户管理 ─────────────────────────────────────────────────────
def load_users() -> list:
    """加载用户列表。"""
    return read_json(USERS_FILE, [])


def save_users(users: list) -> None:
    """持久化用户列表。"""
    write_json(USERS_FILE, users)


def find_user(username: str) -> Optional[dict]:
    """按用户名查找用户。"""
    for u in load_users():
        if u.get("username") == username:
            return u
    return None


def create_default_user() -> None:
    """首次启动时创建默认测试用户（已存在则跳过）。"""
    users = load_users()
    if any(u["username"] == "admin" for u in users):
        return
    user = {
        "id": "user_001",
        "username": "admin",
        "password_hash": hash_password("nutrifit2024"),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    users.append(user)
    save_users(users)
    logger.info("默认测试用户已创建: admin / nutrifit2024")


# ── FastAPI 依赖 ─────────────────────────────────────────────────
def get_current_user(authorization: str = Header(None)) -> dict:
    """FastAPI 依赖：从 Authorization header 解析当前登录用户。

    用法:
        @app.post("/api/xxx")
        def my_endpoint(user: dict = Depends(get_current_user)):
            ...
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="未提供认证信息")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="认证格式错误，请使用 Bearer token")
    payload = verify_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    # 验证用户仍存在于数据库
    user = find_user(payload["username"])
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    return {"user_id": user["id"], "username": user["username"]}


def get_optional_user(authorization: str = Header(None)) -> Optional[dict]:
    """FastAPI 依赖：可选解析用户（未登录时返回 None，不报 401）。"""
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    payload = verify_token(token)
    if payload is None:
        return None
    user = find_user(payload["username"])
    if user is None:
        return None
    return {"user_id": user["id"], "username": user["username"]}
