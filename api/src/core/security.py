"""鉴权原语：JWT 签发/校验 + 口令哈希。

当前阶段只提供能力，不强制校验（`require_user` 依赖可随时接入路由）。
换用 OAuth2 / LDAP / 企业 SSO 时只需替换本文件，路由层不动。

令牌里携带的是**已展开的权限码**（不是角色名）。角色 → 权限的展开发生在登录时，
业务代码因此只需要判断权限码，永远不关心「用户是什么角色」。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from core.config import settings
from core.context import CurrentUser
from core.exceptions import Unauthorized

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1

# 口令哈希专用线程池：**有界**，且不占用事件循环。
#
# 单次 scrypt 本机约 150ms，且 `hashlib.scrypt` 会释放 GIL（是真正的并行 CPU 工作）。
# 如果直接在协程里同步调用，这 150ms 里整个事件循环被占住——不是「登录慢」，
# 而是同一进程内的所有请求（健康检查、静态接口、别人的轮询）一起停摆；
# 10 个并发匿名登录就能把 API 按住 1.5 秒（R6-02）。
#
# 为什么不用 `asyncio.to_thread`：它用的是默认执行器（上限 `min(32, cpu+4)`），
# 32 个并发 scrypt 会把 CPU 打满，把「事件循环不阻塞」换成「谁也别想算完」。
# 这里按核数收窄到 2~4，超出的请求在队列里等——登录是低频动作，排队比雪崩好。
_HASH_WORKERS = min(4, max(2, (os.cpu_count() or 2) // 2))
_HASH_POOL = ThreadPoolExecutor(max_workers=_HASH_WORKERS, thread_name_prefix="scrypt")


# --------------------------------------------------------------------------- #
# 口令哈希（标准库 scrypt，无第三方依赖）
# --------------------------------------------------------------------------- #
def hash_password(raw: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        raw.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(raw: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = encoded.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            raw.encode(),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(digest_hex)),
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


async def hash_password_async(raw: str) -> str:
    """`hash_password` 的线程池版本。**请求路径只用这个**。"""
    return await asyncio.get_running_loop().run_in_executor(_HASH_POOL, hash_password, raw)


async def verify_password_async(raw: str, encoded: str) -> bool:
    """`verify_password` 的线程池版本。**请求路径只用这个**。

    并发上限由 `_HASH_POOL` 的 worker 数决定，超出的调用在队列里排队，
    事件循环始终可以继续调度其它协程。
    """
    return await asyncio.get_running_loop().run_in_executor(
        _HASH_POOL, verify_password, raw, encoded
    )


# --------------------------------------------------------------------------- #
# JWT
# --------------------------------------------------------------------------- #
def create_access_token(
    subject: str,
    *,
    name: str = "",
    roles: list[str] | None = None,
    permissions: list[str] | tuple[str, ...] | None = None,
    expires_in: int | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(tz=UTC)
    ttl = expires_in if expires_in is not None else settings.access_token_ttl_seconds
    payload: dict[str, Any] = {
        "sub": subject,
        "name": name,
        "roles": roles or [],
        "perms": sorted(permissions or ()),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        "typ": "access",
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """校验签名、有效期、用途与主体。

    `typ` 必须显式校验：将来加 refresh token 时它同样用 `JWT_SECRET` 签名，
    不区分用途就等于把「换票凭证」当成「访问凭证」接受。`sub` 同理——
    没有主体的令牌在「按 sub 查库」的解析器下会变成「查不到用户」，报错位置
    离真正的原因很远。
    """
    try:
        claims = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("登录已过期，请重新登录") from exc
    except jwt.InvalidTokenError as exc:
        raise Unauthorized("登录凭证无效") from exc

    if claims.get("typ") != "access":
        raise Unauthorized("登录凭证无效")
    if not str(claims.get("sub") or "").strip():
        raise Unauthorized("登录凭证无效")
    return claims


def user_from_token(token: str) -> CurrentUser:
    claims = decode_access_token(token)
    return CurrentUser(
        id=str(claims.get("sub", "")),
        name=str(claims.get("name", "")),
        roles=tuple(claims.get("roles", [])),
        permissions=frozenset(claims.get("perms", [])),
        issued_at=int(claims.get("iat") or 0),
    )
