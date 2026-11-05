"""鉴权原语：JWT 签发/校验 + 口令哈希。

当前阶段只提供能力，不强制校验（`require_user` 依赖可随时接入路由）。
换用 OAuth2 / LDAP / 企业 SSO 时只需替换本文件，路由层不动。

令牌里携带的是**已展开的权限码**（不是角色名）。角色 → 权限的展开发生在登录时，
业务代码因此只需要判断权限码，永远不关心「用户是什么角色」。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from core.config import settings
from core.context import CurrentUser
from core.exceptions import Unauthorized

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


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
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("登录已过期，请重新登录") from exc
    except jwt.InvalidTokenError as exc:
        raise Unauthorized("登录凭证无效") from exc


def user_from_token(token: str) -> CurrentUser:
    claims = decode_access_token(token)
    return CurrentUser(
        id=str(claims.get("sub", "")),
        name=str(claims.get("name", "")),
        roles=tuple(claims.get("roles", [])),
        permissions=frozenset(claims.get("perms", [])),
    )
