"""`app purge-users` 的用例。

三条线各测一遍：**预演不动库**、**停用可逆**、**物理删除会清什么、留什么**。
另外两个「不测就会静默出错」的地方：

1. `CLEARED` / `KEPT` 里的表名列名对着 `Base.metadata` 校验 —— 写错的后果是
   **永远数出 0 行**，报告看起来一切正常，直到有人发现素材作者悬空；
2. glob → LIKE 的转义 —— `smoke_*` 里的 `_` 不转义会多匹配一个 `smokeXowner`，
   而清理命令里「多删一个」没有任何地方会报错。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, update

from db.base import Base
from db.session import get_session_factory
from modules.identity.models import Role, User, UserRole, UserSession
from modules.identity.purge import CLEARED, KEPT, Mode, PurgeReport, purge_users

#: 每个用例自己造、自己清。用统一前缀而不是固定名字，是为了 autouse 的清理
#: 能一次带走（残留会让别处的「用户总数」断言莫名多几个）。
PREFIX = "purgeme_"


def _run(coro: object) -> object:
    """跑一段协程，**结束就把引擎关掉**。

    每个 `asyncio.run` 都建一个新事件循环，而连接池是全局的：池里的 aiosqlite
    连接属于上一个（已关闭的）循环，留给 GC 会打出一串
    "garbage collector is trying to clean up non-checked-in connection" ——
    在 GitHub Actions 上那会被标成 failure annotation。`dispose_engine()` 是懒建的，
    下一个 `_run` 会自己重建，代价只有一次建引擎。
    """

    from db.session import dispose_engine

    async def wrapped() -> object:
        try:
            return await coro  # type: ignore[misc]
        finally:
            # 让 aiosqlite 的 close 回调先落地再 dispose：不等这一拍，
            # 池里偶尔会留下一个「已 checkout 但已在关闭中」的连接，
            # GC 到它时打出一串 SAWarning（CI 上会变成 annotation）。
            await asyncio.sleep(0)
            await dispose_engine()

    return asyncio.run(wrapped())


async def _make_user(username: str, *, active: bool = True) -> str:
    async with get_session_factory()() as session:
        user = User(
            username=username,
            password_hash="not-a-real-hash",
            is_active=active,
            display_name=username,
        )
        session.add(user)
        await session.flush()
        await session.commit()
        return user.id


async def _make_session(user_id: str) -> str:
    async with get_session_factory()() as session:
        row = UserSession(
            user_id=user_id,
            refresh_jti=f"jti-{user_id}",
            expires_at=datetime.now(tz=UTC) + timedelta(days=7),
        )
        session.add(row)
        await session.flush()
        await session.commit()
        return row.id


async def _make_role(code: str, *, superuser: bool = False) -> str:
    async with get_session_factory()() as session:
        role = Role(code=code, name=code, is_superuser=superuser)
        session.add(role)
        await session.flush()
        await session.commit()
        return role.id


async def _bind_role(user_id: str, role_id: str) -> None:
    async with get_session_factory()() as session:
        session.add(UserRole(user_id=user_id, role_id=role_id))
        await session.commit()


async def _make_image_job(user_id: str, name: str) -> None:
    """造一条「内容」，用来验 `--delete` 的 orphan 闸门。只填必填列。"""
    from modules.image.models import ImageJob

    async with get_session_factory()() as session:
        session.add(ImageJob(name=name, prompt="x", owner_id=user_id))
        await session.commit()


async def _purge(
    pattern: str,
    *,
    mode: Mode = "dry-run",
    allow_orphans: bool = False,
    include_admins: bool = False,
) -> PurgeReport:
    async with get_session_factory()() as session:
        return await purge_users(
            session,
            pattern=pattern,
            mode=mode,
            allow_orphans=allow_orphans,
            include_admins=include_admins,
        )


@pytest.fixture(autouse=True)
def _clean_purgeme_accounts() -> Iterator[None]:
    """用例结束后把自己造的账号与关联行彻底扫掉。

    不能只靠 `--delete`：有的用例本来就断言「被拒，账号还在」。这里直接按前缀
    清库，比让每个用例自己收拾可靠 —— 之前有过一次「新用例留下的数据让别处的
    列表断言全红」的教训。

    用 `startswith` 而不是 `like(f"{PREFIX}%")`：后者里的 `_` 是通配符，会连带
    扫掉 `purgemeXalpha` 这个**故意只差一个字符**的对照账号。行为上正好是我要的，
    但靠通配符的副作用清理另一个用例的数据，下一个人改前缀时就会踩空。
    """

    def sweep() -> None:
        async def run() -> None:
            async with get_session_factory()() as session:
                ids = (
                    (
                        await session.execute(
                            select(User.id).where(User.username.startswith("purgeme"))
                        )
                    )
                    .scalars()
                    .all()
                )
                if not ids:
                    return
                await session.execute(delete(UserRole).where(UserRole.user_id.in_(ids)))
                await session.execute(delete(UserSession).where(UserSession.user_id.in_(ids)))
                await session.execute(delete(User).where(User.id.in_(ids)))
                await session.execute(
                    delete(Role).where(Role.code.in_(["purgeme_super", "purgeme_other"]))
                )
                await session.commit()

        _run(run())

    sweep()
    yield
    sweep()


def test_reference_tables_and_columns_exist() -> None:
    """表名/列名是裸字符串，写错只表现为「数出 0 行」，必须对着元数据钉一遍。"""
    problems = []
    for ref in CLEARED + KEPT:
        table = Base.metadata.tables.get(ref.table_name)
        if table is None:
            problems.append(f"表不存在：{ref.table_name}")
        elif ref.column_name not in table.c:
            problems.append(f"列不存在：{ref.table_name}.{ref.column_name}")
    assert not problems, problems


def test_dry_run_reports_but_never_writes() -> None:
    async def scenario() -> None:
        first = await _make_user(f"{PREFIX}alpha")
        second = await _make_user(f"{PREFIX}beta")
        await _make_session(first)

        report = await _purge(f"{PREFIX}*")

        assert report.matched == 2
        assert report.executed is False
        assert report.mode == "dry-run"
        assert report.cleared["登录会话"] == 1
        # 账号、会话都还在，启停状态没被动过
        async with get_session_factory()() as session:
            rows = (
                (await session.execute(select(User).where(User.id.in_([first, second]))))
                .scalars()
                .all()
            )
            pending = (
                (
                    await session.execute(
                        select(UserSession).where(UserSession.user_id == first)
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 2
        assert all(row.is_active for row in rows)
        assert len(pending) == 1
        assert pending[0].revoked_at is None

    _run(scenario())


def test_underscore_in_pattern_is_literal() -> None:
    """`purgeme_*` 不能把 `purgemeXalpha` 一起吃掉（`_` 在 LIKE 里是通配）。"""

    async def scenario() -> None:
        await _make_user(f"{PREFIX}alpha")
        await _make_user("purgemeXalpha")  # 只差一个字符，但不在模式里
        report = await _purge(f"{PREFIX}*")
        assert [account.username for account in report.accounts] == [f"{PREFIX}alpha"]
        # 对照账号由 autouse 的 sweep 带走（它按 `purgeme` 前缀扫）

    _run(scenario())


def test_deactivate_keeps_row_and_revokes_sessions() -> None:
    async def scenario() -> None:
        user_id = await _make_user(f"{PREFIX}gone")
        await _make_session(user_id)

        report = await _purge(f"{PREFIX}*", mode="deactivate")

        assert report.executed is True
        assert report.revoked_sessions == 1
        async with get_session_factory()() as session:
            user = await session.get(User, user_id)
            assert user is not None, "停用是置 is_active，不是删行"
            assert user.is_active is False
            sessions = (
                (await session.execute(select(UserSession).where(UserSession.user_id == user_id)))
                .scalars()
                .all()
            )
            assert len(sessions) == 1, "会话行要留着（标记而不是删），才看得出被切断过"
            assert sessions[0].revoked_at is not None
            assert sessions[0].revoked_reason == "purge"

    _run(scenario())


def test_delete_clears_bindings_but_keeps_content() -> None:
    async def scenario() -> None:
        user_id = await _make_user(f"{PREFIX}content")
        await _make_session(user_id)
        await _make_image_job(user_id, "purgeme 的生图")
        role_id = await _make_role("purgeme_other")
        await _bind_role(user_id, role_id)

        # 有内容 → 默认拒绝，且一个字节都不动
        blocked = await _purge(f"{PREFIX}*", mode="delete")
        assert blocked.executed is False
        assert any("--allow-orphans" in reason for reason in blocked.blockers)
        async with get_session_factory()() as session:
            assert await session.get(User, user_id) is not None

        # 明确接受悬空引用后才真删
        done = await _purge(f"{PREFIX}*", mode="delete", allow_orphans=True)
        assert done.executed is True
        async with get_session_factory()() as session:
            assert await session.get(User, user_id) is None
            left_sessions = (
                (await session.execute(select(UserSession).where(UserSession.user_id == user_id)))
                .scalars()
                .all()
            )
            assert left_sessions == []
            bound = (
                (await session.execute(select(UserRole).where(UserRole.user_id == user_id)))
                .scalars()
                .all()
            )
            assert bound == []
            # 内容不删：它只是失去了作者
            from modules.image.models import ImageJob

            jobs = (
                (await session.execute(select(ImageJob).where(ImageJob.owner_id == user_id)))
                .scalars()
                .all()
            )
            assert len(jobs) == 1
            await session.execute(delete(ImageJob).where(ImageJob.owner_id == user_id))
            await session.commit()

    _run(scenario())


def test_unconstrained_pattern_is_refused() -> None:
    async def scenario() -> None:
        await _make_user(f"{PREFIX}one")
        report = await _purge("*", mode="delete", allow_orphans=True)
        assert report.executed is False
        assert any("没有任何约束" in reason for reason in report.blockers)

    _run(scenario())


def test_superuser_needs_explicit_flag() -> None:
    async def scenario() -> None:
        user_id = await _make_user(f"{PREFIX}root")
        role_id = await _make_role("purgeme_super", superuser=True)
        await _bind_role(user_id, role_id)

        refused = await _purge(f"{PREFIX}*", mode="deactivate")
        assert refused.executed is False
        assert any("--include-admins" in reason for reason in refused.blockers)

        # 库里还有 seed 出来的 admin，所以放行后不会撞「最后一个超管」
        allowed = await _purge(f"{PREFIX}*", mode="deactivate", include_admins=True)
        assert allowed.executed is True

    _run(scenario())


def test_last_active_superuser_is_refused() -> None:
    """把其余超管临时停用，只留目标这一个 —— 再停/删就该被拦住。

    这里必须动 seed 的 admin（真实场景里「只剩一个超管」就是这样），
    所以用 try/finally 原样恢复；不恢复的话后面的权限用例会全红。
    """

    async def scenario() -> None:
        user_id = await _make_user(f"{PREFIX}last")
        role_id = await _make_role("purgeme_super", superuser=True)
        await _bind_role(user_id, role_id)

        async with get_session_factory()() as session:
            others = (
                (
                    await session.execute(
                        select(UserRole.user_id)
                        .join(Role, Role.id == UserRole.role_id)
                        .where(Role.is_superuser.is_(True), UserRole.user_id != user_id)
                    )
                )
                .scalars()
                .all()
            )
            paused = list(set(others))
            try:
                if paused:
                    await session.execute(
                        update(User).where(User.id.in_(paused)).values(is_active=False)
                    )
                    await session.commit()

                report = await _purge(f"{PREFIX}*", mode="delete", include_admins=True)
                assert report.executed is False
                assert any("全部启用中的超级管理员" in reason for reason in report.blockers)
            finally:
                if paused:
                    await session.execute(
                        update(User).where(User.id.in_(paused)).values(is_active=True)
                    )
                    await session.commit()

    _run(scenario())
