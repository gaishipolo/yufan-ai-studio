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
import weakref
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from core.config import settings
from core.context import CurrentUser
from core.exceptions import Unauthorized

# 口令哈希的成本参数。
#
# `N=2**14` 是抗爆破的下限：单次约 150ms，登录每验一次就要走一遍，攻击者承担
# 的也是这个成本。但测试会**真登录**几百次（实测 847 次哈希 ≈ 157s，占全量
# 一半以上），所以 `APP_ENV=test` 时降到 `2**10`（约 9ms）。
#
# 这不削弱线上任何路径，依据两条：
#   - 参数**编码在哈希串里**（`scrypt$N$r$p$salt$digest`），`verify_password`
#     按串里读到的 N 来算，所以不同强度的哈希天然共存，不需要迁移；
#   - 降级只认 `app_env == "test"`，生产是 `prod`。
_PROD_SCRYPT_N = 2**14
_TEST_SCRYPT_N = 2**10
_SCRYPT_R = 8
_SCRYPT_P = 1


def _scrypt_n() -> int:
    """当前进程该用的 scrypt N。

    每次调用现读 `settings`（原地刷新的单例），**不要在 import 期定格**：
    `reload_settings()` 之后必须立刻生效，否则 monkeypatch 了 `APP_ENV` 的
    用例会继续拿到上一个值。
    """
    return _TEST_SCRYPT_N if settings.app_env == "test" else _PROD_SCRYPT_N

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
#
# 「排队比雪崩好」这句话只在**排队的东西不占别的资源**时成立，所以队列的入口
# 不能是这里，而是下面的 `password_slot()`：线程池只决定「同时算几个」，
# 闸门决定「同时有几个请求在等算」。
_HASH_WORKERS = min(4, max(2, (os.cpu_count() or 2) // 2))
_HASH_POOL: ThreadPoolExecutor | None = None

# 口令运算名额：与线程池容量对齐。
#
# 线程池只收窄了「同时算几个」，**没有收窄「同时有几个请求在等算」**——等待发生在
# `verify_password_async` 里，而调用它的协程此时已经取走了一条数据库连接（R7-01）。
# 于是匿名登录洪水从「卡住事件循环」变成「占满连接池」：几十个并发就能让所有要查库的
# 接口一起变慢，攻击者一个有效请求都不用发对。
#
# 所以闸门要前置到**第一次查库之前**，包住「查库 + 验密」整段；
# 只包验密没有意义（连接早就拿走了），把 `throttle.penalty()` 的惩罚等待包进来更糟
# （爆破者用 5 秒等待就能占满闸门）。
_GATES: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)

# 「当前协程已经在名额里」的标记。`asyncio.Semaphore` 不可重入，业务方法包一层、
# 哈希原语再包一层就是自锁；用 ContextVar 表示已有名额，内层直接放行。
# 注意它是按任务上下文传递的：在闸门里 `create_task` 出去的协程会继承这个标记，
# 也就不再单独取名額——那种写法下并发度由外层那一个名额兜着，仍然有界。
_GATE_DEPTH: ContextVar[int] = ContextVar("password_gate_depth", default=0)


def _hash_pool() -> ThreadPoolExecutor:
    """惰性建池：`shutdown_hash_pool()` 之后还能再用（测试、reload 都走这条）。"""
    global _HASH_POOL
    if _HASH_POOL is None:
        _HASH_POOL = ThreadPoolExecutor(
            max_workers=_HASH_WORKERS, thread_name_prefix="scrypt"
        )
    return _HASH_POOL


def shutdown_hash_pool() -> None:
    """关闭线程池；下次调用 `hash_password_async` 会重新建。

    import 期创建、进程退出时靠 `concurrent.futures` 的 atexit join：
    `uvicorn --reload` 每次重载都会把上一轮的 worker 留在进程里（R7-06）。
    挂在 lifespan 的收尾段，重载时就真的回收了。
    """
    global _HASH_POOL
    pool, _HASH_POOL = _HASH_POOL, None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


def _hash_gate() -> asyncio.Semaphore:
    """按事件循环取闸门。

    模块级单例在「一个进程一个 loop」的生产环境够用，但测试里每个 TestClient 请求、
    每条 `asyncio.run` 都会新建 loop，而 `asyncio.Semaphore` 一旦在有竞争时被使用，
    就会绑定到当时的 loop，换 loop 直接 `RuntimeError`。按 loop 存一份既保住了
    「同一 loop 内并发受限」，也不会把上一个 loop 的状态带进来。
    """
    loop = asyncio.get_running_loop()
    gate = _GATES.get(loop)
    if gate is None:
        gate = asyncio.Semaphore(_HASH_WORKERS)
        _GATES[loop] = gate
    return gate


@asynccontextmanager
async def password_slot() -> AsyncIterator[None]:
    """占一个口令运算名额，直到退出。

    这个函数有**两个用途**，别只当成装饰器看：

    1. `hash_password_async` / `verify_password_async` 自己会取名额（见下），
       所以「谁忘了包」不会变成「同时几十个 scrypt 抢 CPU」。
    2. 业务方法显式包一层，是为了把「**先排队、再拿连接**」这件事写进代码：
       闸门包在查库之前，等待发生在还没借连接的时候。这一条安全层兜不了底——
       它不知道调用方手里有没有连接（R7-01 / R8-01）。

    嵌套是安全的：`asyncio.Semaphore` 不可重入，重复取会自锁，所以用
    `_GATE_DEPTH` 标记「我已经在名额里」，内层直接放行。
    """
    if _GATE_DEPTH.get():
        yield
        return
    async with _hash_gate():
        token = _GATE_DEPTH.set(1)
        try:
            yield
        finally:
            _GATE_DEPTH.reset(token)


# --------------------------------------------------------------------------- #
# 口令哈希（标准库 scrypt，无第三方依赖）
# --------------------------------------------------------------------------- #
def hash_password(raw: str) -> str:
    salt = secrets.token_bytes(16)
    # 同一次调用里只读一次：串里记的 N 必须与实际算的 N 一致，否则
    # `verify_password` 按串重算出来的永远不匹配。
    n = _scrypt_n()
    digest = hashlib.scrypt(
        raw.encode(), salt=salt, n=n, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return f"scrypt${n}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


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
    """`hash_password` 的线程池版本。**请求路径只用这个**。

    自带名额：调用方没包 `password_slot()` 也不会把线程池挤爆。
    已经在名额里的调用直接放行（`_GATE_DEPTH`）。
    """
    async with password_slot():
        return await asyncio.get_running_loop().run_in_executor(
            _hash_pool(), hash_password, raw
        )


async def verify_password_async(raw: str, encoded: str) -> bool:
    """`verify_password` 的线程池版本。**请求路径只用这个**。

    并发上限由 `_HASH_POOL` 的 worker 数决定，超出的调用在队列里排队，
    事件循环始终可以继续调度其它协程。排队要配 `password_slot()`——见上面的注释。
    同样自带名额，理由同 `hash_password_async`。
    """
    async with password_slot():
        return await asyncio.get_running_loop().run_in_executor(
            _hash_pool(), verify_password, raw, encoded
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
    session_id: str | None = None,
) -> str:
    """签发访问令牌。

    `session_id` 会写成 `sid` claim：解析器据此每次请求校验「这个会话还活着吗」，
    登出 / 踢下线才能立刻生效。**不传就是无会话令牌**（旧令牌、测试里直接签的令牌），
    解析器跳过会话校验——它们只能靠 TTL 和 `password_changed_at` 兜底。
    """
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
    if session_id:
        payload["sid"] = session_id
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(
    subject: str,
    *,
    session_id: str,
    jti: str,
    expires_in: int | None = None,
) -> str:
    """签发刷新令牌。

    只带 `sub` / `sid` / `jti` / `typ`，**不带 `perms`**：
    权限每次都由解析器从库里取，refresh 里放一份权限快照只会让人误以为它有用。
    """
    now = datetime.now(tz=UTC)
    ttl = (
        expires_in if expires_in is not None else settings.refresh_token_ttl_seconds
    )
    payload: dict[str, Any] = {
        "sub": subject,
        "sid": session_id,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        "typ": "refresh",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _decode(token: str, *, expected_typ: str) -> dict[str, Any]:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            # `exp` / `iat` 必须**存在**，不只是「存在时校验」。
            #
            # PyJWT 默认只在声明存在时判断有效期：一张不带 `exp` 的票永远不过期，
            # 不带 `iat` 的票会被解析器读成「不知道签发时间」，从而静默跳过
            # 「改密让旧票失效」（见 `modules/identity/principal.py`）。
            # 两种情形都只可能来自「签发路径漏写」或「有人拿密钥手工拼票」，
            # 没有一种是我们愿意放行的，所以这里失败关闭。
            options={"require": ["exp", "iat"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("登录已过期，请重新登录") from exc
    except jwt.InvalidTokenError as exc:
        raise Unauthorized("登录凭证无效") from exc

    # `typ` 必须显式校验：两种令牌同样用 `JWT_SECRET` 签名，不区分用途就等于
    # 把「换票凭证」当成「访问凭证」接受（反过来更糟：刷新接口会接受 access 令牌，
    # 于是一张 2 小时的票可以无限续期）。
    if claims.get("typ") != expected_typ:
        raise Unauthorized("登录凭证无效")
    if not str(claims.get("sub") or "").strip():
        raise Unauthorized("登录凭证无效")
    return claims


def decode_access_token(token: str) -> dict[str, Any]:
    """校验签名、有效期、用途与主体。

    `typ` 必须显式校验：refresh token 同样用 `JWT_SECRET` 签名，
    不区分用途就等于把「换票凭证」当成「访问凭证」接受。`sub` 同理——
    没有主体的令牌在「按 sub 查库」的解析器下会变成「查不到用户」，报错位置
    离真正的原因很远。
    """
    return _decode(token, expected_typ="access")


def decode_refresh_token(token: str) -> dict[str, Any]:
    """校验签名、有效期、用途，并要求带 `sid` 与 `jti`。

    两个都必须有：`sid` 找不到会话就无从轮换，`jti` 缺失则无法比对「这张票是不是
    已经用过了」。
    """
    claims = _decode(token, expected_typ="refresh")
    if not str(claims.get("sid") or "").strip():
        raise Unauthorized("登录凭证无效")
    if not str(claims.get("jti") or "").strip():
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
        session_id=str(claims.get("sid") or ""),
    )
