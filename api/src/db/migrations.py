"""迁移判定：这个库归 Alembic 管，还是归 ORM 管。

## 为什么要单独一个模块

历史上开发/测试环境用 `Base.metadata.create_all` 建表，生产用 Alembic。
**两条路径同时管同一个库**时会产生一个��名很硬的偏差：

    表已经存在，但 `alembic_version` 还停在旧版本。

之后运维执行 `alembic upgrade head`，迁移链在 `create_table` 处撞
`DuplicateTableError`，整条链卡死——而卡住的位置离「当初是谁建的这张表」
十万八千里。解开它只能手工逐个核对「迁移最终态 vs 库现状」是否等价，
再逐个 `stamp`：本仓就为此手工核对过 7 个迁移。

`create_all` 的 `checkfirst=True` 只建**缺失的表**、**不补列**，所以它制造
偏差的方式只有一条：**ORM 抢先建出「迁移链里还没有的表」**。这恰好是新加
一个模块、迁移还没写（或还没跑）时最常见的时机。

## 判据

一个库只能有一条建表路径，由「库里有没有 `alembic_version`」决定：

- 没有 → 从未被迁移管过（测试库、一次性开发库）→ 按 ORM 建表；
- 有   → 归 Alembic 管 → ORM 不得建表。

「归 Alembic 管」不等于「不动 schema」：库落后 head 时正确的动作是**跑迁移**，
而不是让 ORM 建表（那正是把偏差造出来的动作）。

## 关于 alembic 的依赖

`alembic` 只在 `dev` 依赖里（生产镜像不带它）。所以本模块所有 Alembic 相关
调用一律**懒加载**，缺依赖时降级成「读不到 head」而不是在 import 期崩掉——
`app doctor` 与 `app init-db` 都要能在没装 alembic 的环境里给出可读结论。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from core.logging import get_logger

logger = get_logger(__name__)

# `api/` 目录（本文件在 api/src/db/migrations.py）：alembic.ini 与 migrations/ 的锚点。
# 放在模块级而不是协程里：`Path.resolve()` 是阻塞调用，写进 async 函数会被 ASYNC240 拦下。
_API_ROOT = Path(__file__).resolve().parents[2]

#: Alembic 记录当前版本的那张表。
ALEMBIC_VERSION_TABLE = "alembic_version"


class SchemaAction(StrEnum):
    """一个库该怎么建表。

    三个取值是穷举的：任何库的建表策略都必须落在这三者之一，
    「没有第四种」本身就是这条守卫要固定的事。
    """

    #: 按 ORM 元数据建表（库里没有 alembic_version：从未被迁移管过）。
    CREATE_ALL = "create_all"
    #: 跑迁移把库升到 head（库归迁移管，但版本落后）。
    UPGRADE = "upgrade"
    #: 什么都不做（库已在 head，或版本不在迁移链上）。
    NONE = "none"


@dataclass(frozen=True)
class SchemaBootstrap:
    """建表策略的判定结果。

    `reason` 是给人看的：它会原样出现在启动日志与 `app doctor` 里，
    「为什么这个库没按 ORM 建表」必须能一句话说清，否则就会有人
    「为了保险」再把 create_all 加回去。
    """

    action: SchemaAction
    reason: str
    current: str = ""
    head: str = ""
    #: 迁移图的全部 head。正常只有一个；多于一个说明迁移链分叉，
    #: `upgrade head` 会直接失败——这时 `head` 是空串、`action` 是 NONE，
    #: 但调用方（`app doctor`）必须能把它和「没装 alembic」区分开，
    #: 所以原始 head 列表要一路带出来，不能在中途塌成空串。
    heads: tuple[str, ...] = ()

    @property
    def needs_migration(self) -> bool:
        return self.action == SchemaAction.UPGRADE

    @property
    def forked(self) -> bool:
        """迁移链分叉：`upgrade head` 会失败，必须人工合并。"""
        return len(self.heads) > 1


def _alembic_config() -> Any:
    """构造指向本仓 `migrations/` 的 alembic 配置。"""
    from alembic.config import Config

    cfg = Config(str(_API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_API_ROOT / "migrations"))
    return cfg


def migration_heads() -> list[str]:
    """读迁移图的 head 列表。文件 I/O，调用方负责丢进线程池。

    读不到（没装 alembic / 打包产物里没带 migrations/）时返回空列表，
    由 `plan_schema_bootstrap` 决定怎么降级——这里不抛，因为「读不到迁移目录」
    在「没装 dev 依赖」的生产镜像里是正常状态，不是错误。
    """
    try:
        from alembic.script import ScriptDirectory

        return list(ScriptDirectory.from_config(_alembic_config()).get_heads())
    except Exception as exc:  # noqa: BLE001 - 缺 dev 依赖 / 目录缺失都是可降级的
        logger.debug("读不到迁移 head：%s: %s", type(exc).__name__, exc)
        return []


async def has_alembic_version(engine: Any) -> bool:
    """这个库里有没有 `alembic_version` 表 —— 即「归不归 Alembic 管」。"""
    from sqlalchemy import inspect

    async with engine.connect() as conn:
        return bool(
            await conn.run_sync(lambda c: inspect(c).has_table(ALEMBIC_VERSION_TABLE))
        )


async def current_revision(engine: Any) -> str:
    """当前库的迁移版本号；库里没有 `alembic_version` 时返回空串。"""
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            value = await conn.scalar(text(f"SELECT version_num FROM {ALEMBIC_VERSION_TABLE}"))
    except Exception:  # noqa: BLE001 - 表不存在就是「没跑过迁移」
        return ""
    return str(value or "")


def _revision_is_ancestor(current: str, head: str) -> bool:
    """`current` 是不是 `head` 的祖先（即从 current 到 head 是同一条链上的连续区间）。

    用来识别一种真实存在的情况：**本机的迁移文件比库里记录的版本还新**。
    这时从 current 升到 head 会跳过中间那几版，schema 直接错位——
    这种库不该被自动升级，只能让人自己决定。

    手工走 `down_revision` 链而不是用 `ScriptDirectory.iterate_revisions`：
    后者的语义是「从 upper 往下走，到 lower 为止」，而 lower 不在链上时
    它的行为依赖 alembic 版本，容易静默当成「在链上」。这里要的是
    「lower 到底是不是 upper 的祖先」这一个布尔值，自己走链最直白。
    """
    try:
        from alembic.script import ScriptDirectory

        script = ScriptDirectory.from_config(_alembic_config())
    except Exception as exc:  # noqa: BLE001 - 读不到迁移目录时不做自动升级
        logger.debug("读不到迁移目录：%s: %s", type(exc).__name__, exc)
        return False

    seen: set[str] = set()
    stack = [head]
    while stack:
        revision = stack.pop()
        if revision in seen:
            continue
        seen.add(revision)
        if revision == current:
            return True
        try:
            rev = script.get_revision(revision)
        except Exception:  # noqa: BLE001 - 链上出现不认识的 revision
            continue
        if rev is None:
            continue
        down = rev.down_revision
        if down is None:
            continue
        if isinstance(down, (tuple, list)):
            stack.extend(down)
        else:
            stack.append(down)
    return False


async def plan_schema_bootstrap(
    engine: Any, *, heads: Sequence[str] | None = None
) -> SchemaBootstrap:
    """判定这个库该怎么建表。

    `heads` 只在测试里显式传入；正常路径自己读迁移图——读文件是阻塞调用，
    所以包在 `asyncio.to_thread` 里，不占事件循环。
    """
    if heads is None:
        heads = await asyncio.to_thread(migration_heads)
    resolved_heads = tuple(heads)

    if not await has_alembic_version(engine):
        return SchemaBootstrap(
            SchemaAction.CREATE_ALL,
            "库里没有 alembic_version：这是从未被迁移管过的库，按 ORM 元数据建表",
            heads=resolved_heads,
        )

    current = await current_revision(engine)
    if not resolved_heads:
        return SchemaBootstrap(
            SchemaAction.NONE,
            "读不到迁移 head（没装 alembic 或产物里没带 migrations/）：不动 schema",
            current=current,
            heads=resolved_heads,
        )
    if len(resolved_heads) > 1:
        return SchemaBootstrap(
            SchemaAction.NONE,
            f"迁移出现分叉 {list(resolved_heads)}：`upgrade head` 会失败，必须先合并",
            current=current,
            heads=resolved_heads,
        )

    resolved_head = resolved_heads[0]
    if current == resolved_head:
        return SchemaBootstrap(
            SchemaAction.NONE,
            f"数据库已在迁移 head（{current}）",
            current=current,
            head=resolved_head,
            heads=resolved_heads,
        )
    if not current:
        return SchemaBootstrap(
            SchemaAction.NONE,
            "库里有 alembic_version 却读不到版本号：不动 schema，请人工核对",
            current=current,
            head=resolved_head,
            heads=resolved_heads,
        )
    if not _revision_is_ancestor(current, resolved_head):
        return SchemaBootstrap(
            SchemaAction.NONE,
            f"数据库版本 {current} 不在迁移链上（本地迁移比库记录的版本新，"
            "自动升级会跳过中间几版）：不动 schema，请人工核对",
            current=current,
            head=resolved_head,
            heads=resolved_heads,
        )
    return SchemaBootstrap(
        SchemaAction.UPGRADE,
        f"数据库停在 {current}，迁移 head 是 {resolved_head}：跑迁移（不是 create_all）",
        current=current,
        head=resolved_head,
        heads=resolved_heads,
    )


def upgrade_to_head() -> None:
    """把当前配置的库升到 head。**同步阻塞**，调用方负责丢进线程池。

    升的是「`get_settings().db_url` 指向的那个库」——`migrations/env.py` 就是
    从配置里取地址的，本函数不另外接受连接参数，避免出现「命令说升 A、
    实际升了 B」这种事。
    """
    from alembic import command

    command.upgrade(_alembic_config(), "head")
