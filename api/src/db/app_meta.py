"""框架级的键值表（`app_meta`）：**存状态，不存缓存**。

## 为什么需要它

有一类信息既不属于某个业务实体，也不能放配置：它是「上一次运行时系统认定的事实」。
第一个消费方是权限点目录的快照——见 `IdentityService.sync_new_permissions`：
启动时要把「新增的权限点」自动补进内置角色，但**不能**把管理员手工移除的旧权限
再塞回去。要区分「新」与「被删掉的」，就必须记住上一次看见过哪些。

放进程内存不行（多副本各记各的、重启就丢），放配置文件不行（它是运行期写出的），
放缓存也不行（缓存会过期，而这个事实不能过期）。所以一张两列表最合适。

## 为什么键是字符串而不是枚举

它刻意保持「无 schema」：写下 `permissions.catalog` 的人负责它的格式，读的人负责
解析。加一个键不需要迁移——这正是它相对「给每件事建一张表」的价值。
代价是没人管得住乱写，所以约定：**键带域名前缀**（`permissions.` / `flags.`），
值统一是 JSON（标量也 JSON 编码，读的时候不必猜类型）。

## 并发语义：不是「最后写入者胜」

这张表最初只有 `key` + `value`，语义是最后写入者胜。对「两个实例同时启动」这件事
来说那是错的：**并发写的不一定是同一份判断**。真实场景是滚动发布——旧版本实例
（只知道 29 个权限点）和新版本实例（31 个）同时启动，旧实例最后写，快照就退回 29；
下一次启动把少掉的那 2 个当成「新增」又补进内置角色，管理员手工摘掉的权限被悄悄
加回来。全程没有任何报错。

所以现在有三样东西：

1. `version`：每次写入 +1，并且是 ORM 的 `version_id_col`——任何 `UPDATE` 都自带
   `WHERE version = <读到的值>`，被别人抢先改过就抛 `StaleDataError`，而不是盖掉。
2. `set_json(..., expected_version=...)`：把「我据以决策的那个版本」显式提出来。
   CAS 失败返回 `False`，调用方重读再算一次——**决策要跟着数据一起重来**。
3. `merge_json(...)`：读-改-写的封装，`updater` 在冲突后会拿到新值重算。

### 「只增不减」是自动路径的默认

自动执行的动作（每次启动都跑的引导）不允许**缩**集合，只允许增：`sync_new_permissions`
写快照时取 `declared ∪ previous`。因为「谁少写了」和「谁写得晚」从数据上分不清，
而两种写错的代价不对称——多留一个历史权限码只是少补一次，少留一个会把管理员
手工移除的权限加回来。真要收缩（删掉的权限点不再声明）由人跑
`app sync-roles --prune-snapshot`，那是一次明确的、可解释的动作。

## 审计

`app_meta_history` 记「谁、什么时候、把哪份状态从第几版改到了第几版」。
写入口径是**与主写入同一个事务**：主写入回滚了，审计行也得跟着消失，否则它会
留下一条「改过但没改成」的记录，比没有更误导。每个键只保留最近若干条
（`HISTORY_KEEP`）——这是状态表不是日志表，留太多反而没人看。
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import DateTime, Integer, String, Text, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm.exc import StaleDataError

from core.config import get_settings
from db.base import Base, TimestampMixin, utcnow

# 每个键保留的审计条数。这张表是「状态表的历史」，不是业务日志：
# 一个人排查「谁把它改回去了」只会看最近几次，留 50 条足够覆盖一个发版周期。
HISTORY_KEEP = 50

# CAS 冲突后的重试次数。冲突只可能来自「另一个实例同时启动」，
# 一两轮内必然收敛；连续失败说明有实例在循环写，那就别再陪它转。
WRITE_RETRIES = 3


class AppMeta(Base, TimestampMixin):
    __tablename__ = "app_meta"

    # 键就是主键：一个键一份状态
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # 写入序号。它同时是 ORM 的乐观锁列，见 `__mapper_args__`
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # 最后一次写入者（`host:pid`）。排查「谁改的」第一眼要靠它，
    # 完整轨迹在 `app_meta_history`
    updated_by: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    __mapper_args__ = {"version_id_col": version}


class AppMetaHistory(Base):
    """一次写入的历史（append-only，同事务写入）。

    只记「改到第几版」和一句人写的摘要，不存完整旧值：
    旧值可以从上一行推、也可以从代码推断，但把每份历史 JSON 都留着，
    这张表迟早变成没人清理的副本。
    """

    __tablename__ = "app_meta_history"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    actor: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    detail: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


@dataclass(frozen=True, slots=True)
class Entry:
    """一次读取的完整结果：值 + 它的版本。"""

    value: Any
    version: int
    updated_by: str = ""


def current_actor() -> str:
    """本进程的标识。`host:pid` 而不是 UUID：排查时能直接去那台机器上翻日志。"""
    return f"{socket.gethostname()}:{os.getpid()}"


async def read_entry(session: AsyncSession, key: str) -> Entry | None:
    """读值与版本号。绕开 ORM 身份映射，每次都真查库。

    「读到的版本」是 CAS 的凭据，绝不能是会话里缓存的旧对象——
    那正是这个函数存在的理由，所以这里查列而不是 `session.get(AppMeta)`。
    """
    row = (
        await session.execute(
            select(AppMeta.value, AppMeta.version, AppMeta.updated_by).where(
                AppMeta.key == key
            )
        )
    ).first()
    if row is None:
        return None
    raw, version, updated_by = row
    try:
        value = json.loads(raw)
    except ValueError:
        # 写坏的值当作不存在：调用方会按「首次运行」处理，比抛异常让整个启动失败安全
        value = None
    return Entry(value=value, version=int(version), updated_by=str(updated_by))


async def get_json(session: AsyncSession, key: str) -> Any | None:
    """读一个键并反序列化；键不存在返回 None（**不是** KeyError）。

    「没有这个键」和「值是 False」必须能区分，所以这里不用 `default=False` 那种写法。
    """
    entry = await read_entry(session, key)
    return None if entry is None else entry.value


async def set_json(
    session: AsyncSession,
    key: str,
    value: Any,
    *,
    expected_version: int | None = None,
    actor: str = "",
    detail: str = "",
) -> bool:
    """写一个键（不 commit），成功返回 `True`。

    `expected_version` 的三种取值：

    - `None`：覆盖写（最后写入者胜）。只适用于「谁写都一样」的键；
    - `0`：只允许**新建**。已存在则不写，返回 `False`；
    - `n>0`：只有当前版本仍是 `n` 时才写，否则返回 `False`（别人先改了）。

    返回布尔值而不是抛异常：CAS 失败是**预期内**的并发结果，不是错误，
    调用方要重读数据重新决策，而不是让整个启动流程失败。
    """
    actor = actor or current_actor()
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True)

    row = await session.get(AppMeta, key, populate_existing=True)
    if row is None:
        if expected_version not in (None, 0):
            return False
        ok = await _insert(session, key, payload, actor)
        if ok:
            await _record(session, key, 1, actor, detail)
        return ok

    if expected_version == 0 or (
        expected_version is not None and row.version != expected_version
    ):
        return False

    row.value = payload
    row.updated_by = actor
    try:
        await session.flush()
    except StaleDataError:
        # `version_id_col` 在 flush 时补上了 `WHERE version = <读到的值>`：
        # 读到之后被别人改过，这里就不会命中任何行
        return False
    await _record(session, key, row.version, actor, detail)
    return True


async def _insert(session: AsyncSession, key: str, payload: str, actor: str) -> bool:
    """插入新键；已存在返回 `False`。

    用 `INSERT ... ON CONFLICT DO NOTHING` 而不是「先查后插 + 捕获 IntegrityError」：
    PostgreSQL 下 `IntegrityError` 会**作废整个事务**，调用方连重试都做不了
    （后续语句全报 `current transaction is aborted`）。

    也没有用 `SAVEPOINT` 包住 `IntegrityError`：sqlite 驱动在隐式事务里执行
    `RELEASE SAVEPOINT` 时**会直接提交**，于是「审计与主写入同事务」这个约定
    在 sqlite 上就悄悄失效了（回滚主写入，审计行还留着）。一条方言无关的
    upsert 语句既没有嵌套事务问题，也不挑驱动。
    """
    values = {"key": key, "value": payload, "version": 1, "updated_by": actor}
    # 两个方言的 `Insert` 是不同的类（`on_conflict_do_nothing` 只存在于子类上），
    # 只在这一处分叉，其余逻辑共用
    statement: Any = (
        sqlite_insert(AppMeta) if get_settings().is_sqlite else pg_insert(AppMeta)
    )
    result = await session.execute(
        statement.values(**values).on_conflict_do_nothing(index_elements=[AppMeta.key])
    )
    # `AsyncSession.execute` 的静态返回类型是 `Result`，实际给的是游标结果（有 rowcount）
    return (cast(CursorResult[Any], result).rowcount or 0) == 1


async def _record(
    session: AsyncSession, key: str, version: int, actor: str, detail: str
) -> None:
    """写一条审计，并裁掉这个键过老的记录（同事务）。"""
    session.add(
        AppMetaHistory(
            id=uuid.uuid4().hex,
            key=key,
            version=version,
            actor=actor,
            detail=detail[:200],
        )
    )
    await session.flush()
    keep = (
        select(AppMetaHistory.id)
        .where(AppMetaHistory.key == key)
        .order_by(AppMetaHistory.created_at.desc(), AppMetaHistory.version.desc())
        .limit(HISTORY_KEEP)
    )
    await session.execute(
        delete(AppMetaHistory).where(
            AppMetaHistory.key == key, AppMetaHistory.id.not_in(keep)
        )
    )


async def merge_json(
    session: AsyncSession,
    key: str,
    updater: Callable[[Any], Any],
    *,
    actor: str = "",
    detail: str = "",
) -> tuple[Any, bool]:
    """读-改-写：冲突后重读并**重新计算**，返回 `(最终值, 是否由本次写入)`。

    `updater` 必须是无副作用的纯函数（拿到旧值，返回新值）——它会被调用多次。
    这一点是它与「读一次、算完直接写」的全部区别：后者在冲突时只能放弃或覆盖，
    前者能基于新数据重新做判断。

    连续 `WRITE_RETRIES` 轮都被抢先时返回 `(当前值, False)`：让人继续启动，
    而不是在这里死循环。代价是这一轮引导没落地，下次启动会重做（决策是幂等的）。
    """
    actor = actor or current_actor()
    for _ in range(WRITE_RETRIES):
        entry = await read_entry(session, key)
        old = None if entry is None else entry.value
        new = updater(old)
        if entry is not None and new == old:
            return old, False
        expected = 0 if entry is None else entry.version
        if await set_json(
            session, key, new, expected_version=expected, actor=actor, detail=detail
        ):
            return new, True
        # 丢掉会话里的旧对象，否则下一轮读到的还是同一个旧版本
        session.expire_all()
    entry = await read_entry(session, key)
    return (None if entry is None else entry.value), False


async def history(session: AsyncSession, key: str, *, limit: int = 20) -> list[AppMetaHistory]:
    """读某个键的最近写入记录（从新到旧）。"""
    rows = await session.scalars(
        select(AppMetaHistory)
        .where(AppMetaHistory.key == key)
        .order_by(AppMetaHistory.created_at.desc(), AppMetaHistory.version.desc())
        .limit(limit)
    )
    return list(rows)


__all__ = [
    "HISTORY_KEEP",
    "WRITE_RETRIES",
    "AppMeta",
    "AppMetaHistory",
    "Entry",
    "current_actor",
    "get_json",
    "history",
    "merge_json",
    "read_entry",
    "set_json",
]
