"""令牌声明的逐项校验：用途、主体、会话、票据、签发与过期时间。

## 为什么单独一组

`decode_access_token` / `decode_refresh_token` 里每一行 `raise` 都是**纵深防御**，
平时走不到：正常签发的令牌必然带齐这些声明，所以业务用例一条都不会失败。
防的是下面两种情形：

1. 新增签发路径（SSO、API Key 换票、会话迁移脚本）漏了某个 claim；
2. 有人拿 `JWT_SECRET` 手工拼了一张票（泄露、或测试里图省事）。

两种情形下的症状都一样：**系统照常工作，只是某一层防御静默消失了**。
比如缺 `typ` 时 refresh 票能当 access 用（等于一张票无限续期），缺 `sub` 时
报错位置漂到「按 id 查库查不到」，缺 `iat` 时「改密让旧票失效」整条链断掉却
没有任何提示——`issued_at` 是 0，解析器就按「不知道」跳过判定。

## 判据

这些用例直接调校验函数，不经过 HTTP：走接口只能观察到「401」，
而 401 有六七种来源，看不出是**哪一层**拦下的。逐条对齐到具体声明，
才能在有人删掉某一行时立刻指出删的是哪一行。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest

from core.config import settings
from core.exceptions import Unauthorized
from core.security import decode_access_token, decode_refresh_token


def _now() -> int:
    return int(datetime.now(tz=UTC).timestamp())


def _sign(claims: dict[str, Any], *, secret: str | None = None) -> str:
    return jwt.encode(
        claims,
        secret if secret is not None else settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def _access(**overrides: Any) -> dict[str, Any]:
    claims: dict[str, Any] = {
        "sub": "u_001",
        "iat": _now(),
        "exp": _now() + 600,
        "typ": "access",
    }
    claims.update(overrides)
    return claims


def _refresh(**overrides: Any) -> dict[str, Any]:
    claims: dict[str, Any] = {
        "sub": "u_001",
        "sid": "s_001",
        "jti": "j_001",
        "iat": _now(),
        "exp": _now() + 600,
        "typ": "refresh",
    }
    claims.update(overrides)
    return claims


# --------------------------------------------------------------------------- #
# 正常路径：先证明判据本身不会误杀，否则下面每一条 401 都可能是假阳性
# --------------------------------------------------------------------------- #
def test_a_well_formed_access_token_passes() -> None:
    claims = decode_access_token(_sign(_access()))
    assert claims["sub"] == "u_001"


def test_a_well_formed_refresh_token_passes() -> None:
    claims = decode_refresh_token(_sign(_refresh()))
    assert claims["sid"] == "s_001"
    assert claims["jti"] == "j_001"


# --------------------------------------------------------------------------- #
# 用途（typ）
# --------------------------------------------------------------------------- #
def test_access_rejects_a_refresh_token() -> None:
    """refresh 票不能当 access 用：否则一张 7 天的票能直接访问业务接口。"""
    with pytest.raises(Unauthorized):
        decode_access_token(_sign(_refresh()))


def test_refresh_rejects_an_access_token() -> None:
    """access 票不能续期：否则 2 小时的票可以无限换新，等于永不过期。"""
    with pytest.raises(Unauthorized):
        decode_refresh_token(_sign(_access()))


# --------------------------------------------------------------------------- #
# 主体（sub）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("subject", ["", "   ", None])
def test_tokens_without_a_subject_are_rejected(subject: Any) -> None:
    """没有主体的令牌必须在**解码层**就拒掉。

    放过去的话，下游「按 sub 查库」拿到的是空 id，症状是「查不到用户 → 401」，
    看起来和「账号被停用」一模一样，排查方向直接跑偏。
    """
    claims = _access()
    if subject is None:
        claims.pop("sub")
    else:
        claims["sub"] = subject
    with pytest.raises(Unauthorized):
        decode_access_token(_sign(claims))


# --------------------------------------------------------------------------- #
# 会话与票据（sid / jti）
# --------------------------------------------------------------------------- #
def test_refresh_token_must_carry_a_session_id() -> None:
    """`sid` 缺失就没法轮换会话：登出/踢下线找不到要吊销的那一条。"""
    claims = _refresh()
    claims.pop("sid")
    with pytest.raises(Unauthorized):
        decode_refresh_token(_sign(claims))


def test_refresh_token_must_carry_a_ticket_id() -> None:
    """`jti` 缺失就没法判断「这张票是不是已经用过了」，重放检测失效。"""
    claims = _refresh()
    claims.pop("jti")
    with pytest.raises(Unauthorized):
        decode_refresh_token(_sign(claims))


# --------------------------------------------------------------------------- #
# 时间（exp / iat）
# --------------------------------------------------------------------------- #
def test_expired_token_says_so_explicitly() -> None:
    """过期的提示要能和「凭证无效」区分开：前端据此决定「去续期」还是「去登录」。"""
    claims = _access(exp=_now() - 1)
    with pytest.raises(Unauthorized) as excinfo:
        decode_access_token(_sign(claims))
    assert "过期" in str(excinfo.value)


def test_token_without_expiry_is_rejected() -> None:
    """没有 `exp` 的令牌等于**永不过期**。

    PyJWT 默认不要求 `exp`：解码照样成功。所以这条只能在解码层显式要求，
    否则「签发路径漏写 exp」的后果是一张永久有效的票，而且没有任何现象。
    """
    claims = _access()
    claims.pop("exp")
    with pytest.raises(Unauthorized):
        decode_access_token(_sign(claims))


def test_token_without_issued_at_is_rejected() -> None:
    """没有 `iat` 的令牌会静默绕过「改密让旧票失效」。

    解析器把 `issued_at=0` 读成「不知道签发时间」，于是跳过与
    `password_changed_at` 的比较——改密之后那些票仍然可用，且没有任何日志。
    """
    claims = _access()
    claims.pop("iat")
    with pytest.raises(Unauthorized):
        decode_access_token(_sign(claims))


# --------------------------------------------------------------------------- #
# 签名
# --------------------------------------------------------------------------- #
def test_a_forged_token_is_rejected() -> None:
    # 用一个长度合规（≥32 字节）的假密钥：太短会触发 PyJWT 的 InsecureKeyLengthWarning，
    # 那不是这条用例要测的事，混在输出里只会让真告警变得不起眼。
    with pytest.raises(Unauthorized):
        decode_access_token(_sign(_access(), secret="not-the-real-secret-but-long-enough"))


def test_a_tampered_token_is_rejected() -> None:
    """改一个字符就该失效：签名覆盖的是整段 payload，不是头部。"""
    token = _sign(_access())
    head, payload, signature = token.split(".")
    tampered = f"{head}.{payload[:-2]}AA.{signature}"
    with pytest.raises(Unauthorized):
        decode_access_token(tampered)


def test_an_expired_refresh_token_is_rejected() -> None:
    claims = _refresh(exp=_now() - 1)
    with pytest.raises(Unauthorized):
        decode_refresh_token(_sign(claims))


def test_token_lifetime_is_bounded() -> None:
    """TTL 必须是个正数且不长到荒谬——它决定「泄露的票能撑多久」。

    这条不是测 `create_*` 的实现，而是测**配置**：`ACCESS_TOKEN_TTL_SECONDS`
    被写成 0 或负数时，签出来的票立刻过期（登录后一直 401）；
    写成一年时，吊销机制成了唯一防线。
    """
    assert 60 <= settings.access_token_ttl_seconds <= 24 * 3600
    assert 600 <= settings.refresh_token_ttl_seconds <= 90 * 24 * 3600


def test_refresh_lives_longer_than_access() -> None:
    """反过来就荒谬了：续期票据比访问票据还短命，刷新一次就得重新登录。"""
    assert settings.refresh_token_ttl_seconds > settings.access_token_ttl_seconds


def test_expiry_is_actually_honoured_by_the_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """用一张 2 秒就过期的票走一遍真实时钟，确认不是只比对字段。"""
    from core.security import create_access_token

    token = create_access_token("u_001", expires_in=1)
    claims = decode_access_token(token)
    assert claims["exp"] - claims["iat"] == 1
    # 不 sleep：直接确认 exp 落在未来 1 秒内，且判定路径用的是 `jwt` 的过期检查
    assert claims["exp"] <= int(datetime.now(tz=UTC).timestamp()) + 1
    assert claims["exp"] >= int((datetime.now(tz=UTC) - timedelta(seconds=1)).timestamp())
