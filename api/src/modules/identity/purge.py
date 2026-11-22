"""按用户名模式批量清理账号（`app purge-users`）。

## 为什么需要它

接口冒烟测试每跑一次留一批 `smoke_xxx`；界面上的「停用」把它们留在列表里
（`DELETE /users/{id}` 的语义就是停用，见 `service.deactivate_user`），于是
**没有任何入口能把它们弄走**。剩下唯一的路是手写 SQL —— 而那正是这条命令要替掉
的事：把 `video_tasks.author_id` 当垃圾一起删掉不会报任何错，只会让「谁做的」
变成永远查不出来的悬空值。

## 三种动作，默认只读

`--dry-run`（默认）/ `--deactivate` / `--delete`。默认只读不是保守，是因为这条
命令的输入是一个**模式**：写宽一个字符，删掉的就不只是冒烟账号。

## 物理删除的三道闸门

1. **名下有内容**（任务 / 生图 / 素材 / 模板 / 驱动记录）→ 拒绝，要 `--allow-orphans`。
   这些列是历史归属，删用户只是让它悬空；而「删了会剩下什么」必须由人看过再决定。
2. **命中超级管理员** → 拒绝，要 `--include-admins`；且无论如何都不许把**启用中的
   超管**清成 0（那之后没人能管理系统，只能改库）。
3. **模式没有任何约束**（`*`）→ 拒绝。这条挡的是最常见的手滑：`--pattern '*'`
   看起来像「全部试一下」，实际是把整个用户表删空。

## 为什么用 `table()/column()` 而不是 ORM 模型

模块之间不许互相 import（`test_modules_do_not_import_each_other`），而这里要数的
是**别的模块的表**里有多少行指着他。那是部署事实（表名、列名），不是业务关系，
用 Core 构造表达式就能在不 import 那些模型的前提下查。表名/列名写错由
`tests/test_identity_purge.py` 里的守卫对着 `Base.metadata` 校验 —— 不写守卫的话，
写错的后果是「永远数出 0 行」，报告看起来一切正常。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import and_, column, delete, func, select, table, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import TableClause

from modules.identity.models import Role, User, UserRole
from modules.identity.service import IdentityService

Mode = Literal["dry-run", "deactivate", "delete"]


@dataclass(frozen=True, slots=True)
class Reference:
    """别的表里有多少行指着他。

    `where` 用于「同一张表里只数某一类行」：`permission_grants` 三层共用一张表，
    只有 `scope='user'` 的那些才是这个人的个人例外。
    """

    table_name: str
    column_name: str
    label: str
    where: str = ""


#: 删账号时一并清掉的行。判据：这些行**只表达「某人绑定了什么」**，
#: 离开人就没有独立含义（收藏、会话、组成员、角色、个人权限例外、下载记录）。
#:
#: `login_events` 也在这里：它是安全审计，账号都不存在了留着只有 id 没有主体，
#: 属于「删号」这件事的一部分。**`audit_events` 不在这里** —— 操作留痕要长期保留，
#: 它是「谁在什么时候干了什么」的证据，不该跟着账号消失。
CLEARED: tuple[Reference, ...] = (
    Reference("user_roles", "user_id", "角色绑定"),
    Reference("group_members", "user_id", "小组成员"),
    Reference("user_sessions", "user_id", "登录会话"),
    Reference("login_events", "user_id", "登录审计"),
    Reference("permission_grants", "scope_id", "个人权限例外", "scope = 'user'"),
    Reference("task_favorites", "user_id", "任务收藏"),
    Reference("library_group_leaders", "user_id", "小组负责人"),
    Reference("library_download_jobs", "owner_id", "下载记录"),
)

#: 只计数、不删除的行：内容是内容，留痕是留痕，都不是「绑定关系」。
#: 它们就是 `--allow-orphans` 那句话在说的东西。
KEPT: tuple[Reference, ...] = (
    Reference("video_tasks", "author_id", "视频任务"),
    Reference("image_jobs", "owner_id", "生图任务"),
    Reference("driver_jobs", "owner_id", "驱动作业"),
    Reference("library_assets", "owner_id", "素材"),
    Reference("library_text_versions", "created_by", "文本版本"),
    Reference("template_specs", "owner_id", "模板规格"),
    Reference("library_share_requests", "applicant_id", "共享申请"),
    Reference("library_share_requests", "reviewer_id", "共享审批"),
    Reference("library_draft_reviews", "reviewer_id", "草稿审核"),
    Reference("audit_events", "actor_id", "操作留痕"),
)


@dataclass(frozen=True, slots=True)
class Account:
    user_id: str
    username: str
    is_active: bool
    is_superuser: bool
    last_login_at: datetime | None


@dataclass(frozen=True, slots=True)
class PurgeReport:
    pattern: str
    mode: Mode
    accounts: tuple[Account, ...]
    #: `--delete` 会清掉的行数（按 `CLEARED` 的 label 分组）。
    #: `--deactivate` 不清行，只标记会话 —— 报告给的是事实，怎么解释归 CLI。
    cleared: dict[str, int]
    #: 会留下来、变成悬空引用的行数（按 `KEPT` 的 label 分组）。
    kept: dict[str, int]
    #: 拒绝执行的原因；预演时也照样算出来，好让人一次改对参数。
    blockers: tuple[str, ...]
    executed: bool
    revoked_sessions: int = 0

    @property
    def matched(self) -> int:
        return len(self.accounts)

    @property
    def kept_rows(self) -> int:
        return sum(self.kept.values())


def _to_like(pattern: str) -> str:
    """glob → SQL LIKE：`*` 任意串、`?` 单字符，其余字符按字面。

    为什么 `_` 必须转义：`smoke_*` 里的 `_` 在 LIKE 里是「任意单字符」，
    不转义时 `smokeXowner` 也会被匹配进来 —— 清理命令里这种「多删一个」
    没有任何地方会报错。
    """
    out: list[str] = []
    for ch in pattern:
        if ch == "*":
            out.append("%")
        elif ch == "?":
            out.append("_")
        elif ch in ("%", "_", "\\"):
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def _scope(ref: Reference, ids: Sequence[str]) -> tuple[TableClause, ColumnElement[bool]]:
    """返回 `(表, 条件)`，**两者必须是同一个 Table 对象**。

    分开建两个 `table("user_roles", ...)` 会各自变成一个匿名 Table，SQL 里就出现
    `FROM user_roles, user_roles`：PG 直接报 `DuplicateAliasError`，sqlite 则悄悄
    算成笛卡尔积（数字翻倍）。两个库错法不同，只有真跑一遍才看得出。
    """
    tbl = table(ref.table_name, column(ref.column_name))
    predicate: ColumnElement[bool] = tbl.c[ref.column_name].in_(list(ids))
    if ref.where:
        predicate = and_(predicate, text(ref.where))
    return tbl, predicate


async def _count(session: AsyncSession, ref: Reference, ids: Sequence[str]) -> int:
    if not ids:
        return 0
    tbl, predicate = _scope(ref, ids)
    stmt = select(func.count()).select_from(tbl).where(predicate)
    return int((await session.execute(stmt)).scalar_one())


async def _superuser_ids(session: AsyncSession, ids: Sequence[str]) -> set[str]:
    role_ids = (
        (await session.execute(select(Role.id).where(Role.is_superuser.is_(True)))).scalars().all()
    )
    if not role_ids:
        return set()
    rows = (
        await session.execute(
            select(UserRole.user_id).where(
                UserRole.user_id.in_(list(ids)), UserRole.role_id.in_(list(role_ids))
            )
        )
    ).scalars()
    return set(rows)


async def _active_superusers_outside(session: AsyncSession, ids: Sequence[str]) -> int:
    """这批之外还剩几个启用中的超管。删/停之后必须 > 0。"""
    role_ids = (
        (await session.execute(select(Role.id).where(Role.is_superuser.is_(True)))).scalars().all()
    )
    if not role_ids:
        return 0
    stmt = (
        select(func.count(func.distinct(User.id)))
        .select_from(User)
        .join(UserRole, UserRole.user_id == User.id)
        .where(
            User.is_active.is_(True),
            UserRole.role_id.in_(list(role_ids)),
            User.id.not_in(list(ids)),
        )
    )
    return int((await session.execute(stmt)).scalar_one())


async def purge_users(
    session: AsyncSession,
    *,
    pattern: str,
    mode: Mode = "dry-run",
    allow_orphans: bool = False,
    include_admins: bool = False,
) -> PurgeReport:
    """算出「会动什么」，`mode != "dry-run"` 且没有 blocker 时真的执行。

    不做任何打印：措辞与排版归 CLI，这里只返回事实，测试才好断言。
    """
    like = _to_like(pattern)
    rows = (
        (
            await session.execute(
                select(User).where(User.username.like(like, escape="\\")).order_by(User.username)
            )
        )
        .scalars()
        .all()
    )

    ids = [row.id for row in rows]
    total_users = int((await session.execute(select(func.count()).select_from(User))).scalar_one())
    if not rows:
        return PurgeReport(
            pattern=pattern,
            mode=mode,
            accounts=(),
            cleared={},
            kept={},
            blockers=(),
            executed=False,
        )

    supers = await _superuser_ids(session, ids)
    accounts = tuple(
        Account(
            user_id=row.id,
            username=row.username,
            is_active=row.is_active,
            is_superuser=row.id in supers,
            last_login_at=row.last_login_at,
        )
        for row in rows
    )
    cleared = {ref.label: await _count(session, ref, ids) for ref in CLEARED}
    kept = {ref.label: await _count(session, ref, ids) for ref in KEPT}

    blockers: list[str] = []
    if pattern.strip() in {"*", "**", "?"}:
        blockers.append(
            f"模式 {pattern!r} 没有任何约束（会命中库里的全部 {total_users} 个账号）。"
            "清理请给一段真实前缀，例如 --pattern 'smoke_*'"
        )
    admin_names = [account.username for account in accounts if account.is_superuser]
    if admin_names and not include_admins:
        shown = ", ".join(admin_names[:5]) + ("…" if len(admin_names) > 5 else "")
        blockers.append(
            f"命中了 {len(admin_names)} 个超级管理员（{shown}）；"
            "确认要一并处理请加 --include-admins"
        )
    if admin_names and await _active_superusers_outside(session, ids) == 0:
        blockers.append(
            "这批账号包含了全部启用中的超级管理员：处理之后没有任何人能管理系统，"
            "只能连库改数据。请先给另一个人授予超管"
        )
    kept_total = sum(kept.values())
    if mode == "delete" and kept_total > 0 and not allow_orphans:
        detail = "、".join(f"{label} {count}" for label, count in kept.items() if count)
        blockers.append(
            f"名下还有 {kept_total} 行内容/留痕（{detail}）。删账号不会删它们，"
            "只会让「谁做的」变成悬空引用；确认接受请加 --allow-orphans"
        )

    executed = False
    revoked = 0
    if mode != "dry-run" and not blockers:
        service = IdentityService(session)
        if mode == "deactivate":
            revoked = await service.deactivate_many(rows, reason="purge")
        else:
            for ref in CLEARED:
                tbl, predicate = _scope(ref, ids)
                await session.execute(delete(tbl).where(predicate))
            await session.execute(delete(User).where(User.id.in_(ids)))
        await session.commit()
        executed = True
        await _audit(pattern, mode, accounts, cleared, kept)

    return PurgeReport(
        pattern=pattern,
        mode=mode,
        accounts=accounts,
        cleared=cleared,
        kept=kept,
        blockers=tuple(blockers),
        executed=executed,
        revoked_sessions=revoked,
    )


async def _audit(
    pattern: str,
    mode: Mode,
    accounts: Sequence[Account],
    cleared: dict[str, int],
    kept: dict[str, int],
) -> None:
    """留一条痕：批量清理最容易变成「谁在什么时候把谁删了」查不出来。

    CLI 不走 `create_app()`，扩展点不会自动挂上；写入端在这里显式 import
    （`import` 即注册）。缺了它 `record()` 会**静默跳过**，命令看起来一切正常。
    """
    from core.audit import record
    from db import audit_sink as _audit_sink  # noqa: F401

    await record(
        "identity.user.purge",
        target_type="user",
        detail={
            "pattern": pattern,
            "mode": mode,
            "count": len(accounts),
            "usernames": [account.username for account in accounts[:50]],
            "truncated": max(0, len(accounts) - 50),
            "cleared": cleared,
            "kept": kept,
            "via": "cli",
        },
    )


__all__ = [
    "Account",
    "CLEARED",
    "KEPT",
    "Mode",
    "PurgeReport",
    "Reference",
    "purge_users",
]
