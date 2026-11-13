"""建表路径唯一性：`create_all` 与 Alembic 不得同时管一个库。

## 这条守卫在守什么

历史上开发/测试环境用 `Base.metadata.create_all` 建表，生产用 Alembic，
两条路径**同时**管同一个库时会产生一个很难解的偏差：

    表已经存在，但 `alembic_version` 还停在旧版本。

之后执行 `alembic upgrade head`，迁移链在 `create_table` 处撞
`DuplicateTableError`，整条链卡死。解开它只能手工逐个核对「迁移最终态 vs
库现状」是否等价，再逐个 `stamp` —— 本仓为此手工核对过 7 个迁移才把本机库对齐。

症状出现在**迁移执行那一刻**，而原因在**几个月前那次启动**。所以守卫必须
在「两条路径会不会同时生效」这一层守住，而不是等迁移撞墙。

## 判据

一个库只能有一条建表路径，由「库里有没有 `alembic_version`」决定：

- 没有 → 从未被迁移管过（测试库、一次性开发库）→ 按 ORM 建表；
- 有   → 归 Alembic 管 → ORM 不得建表。

「归 Alembic 管」时正确的动作是**跑迁移**，不是让 ORM 建表——那正是
把偏差造出来的动作。

## 用例覆盖

1. 判据本身：三种库状态 → 三种动作；
2. 真实冲突：`create_all` 建过表之后，`alembic upgrade head` 撞墙（复现原故障）；
3. `init_db` 的行为：有版本号就不建表、无版本号才建表、`force=True` 无条件建表；
4. 启动路径：归迁移管的库走「跑迁移」而不是「ORM 建表」。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

API_ROOT = Path(__file__).resolve().parents[1]


def _real_head() -> str:
    """迁移链的真实 head。

    不写死 revision 号：新增一个迁移就该让这些用例继续成立，
    否则每次加迁移都要来改一遍测试，改的人迟早会顺手把断言改松。
    """
    from db.migrations import migration_heads

    heads = migration_heads()
    assert len(heads) == 1, f"迁移链分叉：{heads}"
    return heads[0]


#: 当前迁移链的唯一 head。用真实值而不是写死的号，见 `_real_head()`。
HEAD = _real_head()


def _config() -> Config:
    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "migrations"))
    return cfg


@pytest.fixture()
def sqlite_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """一个独立的 sqlite 文件库；用例结束后把配置与引擎还原。"""
    from core.config import reload_settings
    from db import session as db_session

    db_file = tmp_path / "schema.db"
    monkeypatch.setenv("DB_URL", f"sqlite+aiosqlite:///{db_file.as_posix()}")
    reload_settings()
    db_session.reset_state_for_tests()
    try:
        yield db_file
    finally:
        reload_settings()
        db_session.reset_state_for_tests()


def _table_names(db_file: Path) -> list[str]:
    engine = create_engine(f"sqlite:///{db_file.as_posix()}")
    try:
        with engine.connect() as conn:
            return sorted(inspect(conn).get_table_names())
    finally:
        engine.dispose()


# --------------------------------------------------------------------------- #
# 判据本身
# --------------------------------------------------------------------------- #
def test_plan_says_create_all_for_a_fresh_database(sqlite_db: Path) -> None:
    """从没被迁移管过的库 → 按 ORM 建表。"""
    from db.migrations import SchemaAction, plan_schema_bootstrap
    from db.session import get_engine

    plan = asyncio.run(plan_schema_bootstrap(get_engine(), heads=(HEAD,)))
    assert plan.action == SchemaAction.CREATE_ALL
    assert "alembic_version" in plan.reason


def test_plan_says_upgrade_when_the_version_lags(sqlite_db: Path) -> None:
    """库归迁移管但版本落后 → 跑迁移，而不是让 ORM 建表。"""
    from db.migrations import SchemaAction, plan_schema_bootstrap
    from db.session import get_engine

    command.upgrade(_config(), "b7c4e2a18d63")
    plan = asyncio.run(plan_schema_bootstrap(get_engine(), heads=(HEAD,)))
    assert plan.action == SchemaAction.UPGRADE
    assert plan.current == "b7c4e2a18d63"
    assert plan.head == HEAD
    assert plan.needs_migration


def test_plan_says_none_when_already_at_head(sqlite_db: Path) -> None:
    """已经在 head → 什么都不做。"""
    from db.migrations import SchemaAction, plan_schema_bootstrap
    from db.session import get_engine

    command.upgrade(_config(), "head")
    plan = asyncio.run(plan_schema_bootstrap(get_engine(), heads=(HEAD,)))
    assert plan.action == SchemaAction.NONE
    assert plan.current == HEAD


def test_plan_refuses_to_upgrade_an_unknown_version(sqlite_db: Path) -> None:
    """库里记着一个**迁移链上不存在**的版本 → 不自动升级。

    这种情况真实存在：本机迁移文件比库里记录的版本还新（拉了别人的分支）。
    这时从 current 升到 head 会跳过中间那几版，schema 直接错位——
    宁可不动让人来核对，也不能自作主张升上去。
    """
    from db.migrations import SchemaAction, plan_schema_bootstrap
    from db.session import get_engine

    command.upgrade(_config(), "b7c4e2a18d63")
    engine = create_engine(f"sqlite:///{sqlite_db.as_posix()}")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("UPDATE alembic_version SET version_num = 'deadbeefdead'")
    finally:
        engine.dispose()

    plan = asyncio.run(plan_schema_bootstrap(get_engine(), heads=(HEAD,)))
    assert plan.action == SchemaAction.NONE
    assert "不在迁移链上" in plan.reason


def test_plan_refuses_when_the_migration_chain_is_forked(sqlite_db: Path) -> None:
    """head 不唯一（迁移分叉）→ 不动 schema，且必须能被识别成「分叉」。

    分叉时 `upgrade head` 直接报错，而自动升级会把库推到某一条分支上，
    另一条分支的迁移永远执行不到。让人来解分叉。

    `forked` 是给 `app doctor` 用的：分叉必须报 FAIL，而「没装 alembic」
    只该报 WARN —— 两者在 `head` 字段上都会塌成空串，只能靠这个属性区分。
    """
    from db.migrations import SchemaAction, plan_schema_bootstrap
    from db.session import get_engine

    command.upgrade(_config(), "b7c4e2a18d63")
    plan = asyncio.run(
        plan_schema_bootstrap(get_engine(), heads=(HEAD, "deadbeefdead"))
    )
    assert plan.action == SchemaAction.NONE
    assert plan.forked
    assert plan.head == ""
    assert "分叉" in plan.reason


def test_plan_reports_missing_alembic_as_not_forked(sqlite_db: Path) -> None:
    """读不到迁移 head（没装 alembic）→ `forked` 为假。

    生产镜像不带 alembic，这时 `migration_heads()` 返回空列表。它和分叉
    在 `head` 上都表现为空串，但处理方式完全不同：一个该 WARN 静默降级，
    一个该 FAIL 拦住 `upgrade head`。判据必须把两者分开。
    """
    from db.migrations import SchemaAction, plan_schema_bootstrap
    from db.session import get_engine

    command.upgrade(_config(), "b7c4e2a18d63")
    plan = asyncio.run(plan_schema_bootstrap(get_engine(), heads=()))
    assert plan.action == SchemaAction.NONE
    assert not plan.forked
    assert "读不到迁移 head" in plan.reason


# --------------------------------------------------------------------------- #
# 真实冲突：复现原故障
# --------------------------------------------------------------------------- #
def test_create_all_before_migration_breaks_the_upgrade(sqlite_db: Path) -> None:
    """**复现原故障**：ORM 抢先建表 → `alembic upgrade head` 撞 DuplicateTableError。

    这条用例的价值在于「证明原故障确实存在」。有了它，反向验证才有意义：
    修好之后本用例的冲突不会发生，而如果有人把判据删掉，冲突就会重现。
    """
    from db import registry
    from db.base import Base

    registry.load_all_models()
    engine = create_engine(f"sqlite:///{sqlite_db.as_posix()}")
    try:
        with engine.begin() as conn:
            Base.metadata.create_all(conn)
    finally:
        engine.dispose()

    # ORM 建出了全部表，但 alembic_version 不存在 → 迁移链从第一步就撞墙
    assert "alembic_version" not in _table_names(sqlite_db)
    assert "users" in _table_names(sqlite_db)

    with pytest.raises(Exception) as excinfo:
        command.upgrade(_config(), "head")
    assert "already exists" in str(excinfo.value).lower()


def test_the_judgement_prevents_that_collision(sqlite_db: Path) -> None:
    """**修好之后**：判据生效时，迁移链能一路走通、不被 ORM 抢先建的表挡住。

    与上一条用例的差别是它验的是**「之后还能不能升级」**：
    库停在旧版本 + 模型里多了一张迁移链还没有的表，启动跑完判据之后，
    `alembic upgrade head` 必须仍然成功——这正是老行为下会失败的那一步。
    """
    from sqlalchemy import Column, Integer, MetaData, Table

    from db import registry
    from db.base import Base
    from db.session import init_db

    registry.load_all_models()
    command.upgrade(_config(), "b7c4e2a18d63")

    # 模拟「加了模块、迁移还没写」：模型里多一张迁移链没有的表
    extra = Table(
        "zz_probe_only_model",
        MetaData(),
        Column("id", Integer, primary_key=True),
    )
    extra.to_metadata(Base.metadata)
    try:
        # 判据生效 → ORM 不建表
        asyncio.run(init_db())
        assert "zz_probe_only_model" not in _table_names(sqlite_db)

        # 迁移链照样能走完（老行为下这里会撞 DuplicateTableError）
        command.upgrade(_config(), "head")

        engine = create_engine(f"sqlite:///{sqlite_db.as_posix()}")
        try:
            with engine.connect() as conn:
                version = conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
        finally:
            engine.dispose()
        assert version == HEAD
    finally:
        Base.metadata.remove(extra)


# --------------------------------------------------------------------------- #
# init_db 的行为
# --------------------------------------------------------------------------- #
def test_init_db_does_not_create_tables_in_a_migrated_database(sqlite_db: Path) -> None:
    """库归迁移管 → `init_db()` **不建任何表**。

    判据是「ORM 没建表」，不是「版本号没变」——后者钉不住：
    `alembic_version` 不在 ORM 元数据里，所以无论建不建表，版本号都是原样。
    真正会被 ORM 建出来的是**模型里有、迁移链里还没有的表**，那才是偏差的源头。

    所以这条用例临时往 ORM 元数据里加一张表（模拟「加了模块还没写迁移」），
    再看 `init_db()` 会不会把它建出来。
    """
    from sqlalchemy import Column, Integer, MetaData, Table

    from db import registry
    from db.base import Base
    from db.session import init_db

    registry.load_all_models()
    command.upgrade(_config(), "head")

    # 一张「模型里有、迁移链里没有」的表
    extra = Table(
        "zz_probe_only_model",
        MetaData(),
        Column("id", Integer, primary_key=True),
    )
    extra.to_metadata(Base.metadata)

    try:
        asyncio.run(init_db())
        names = _table_names(sqlite_db)
        assert "zz_probe_only_model" not in names, (
            "归 Alembic 管的库被 ORM 建了表：这条表一旦存在，"
            "将来迁移里的 create_table 会撞 DuplicateTableError"
        )
    finally:
        Base.metadata.remove(extra)


def test_init_db_still_creates_tables_for_a_fresh_database(sqlite_db: Path) -> None:
    """没有 alembic_version → `init_db()` 照旧建表（测试库与一次性开发库的路）。"""
    from db.session import init_db

    asyncio.run(init_db())
    names = _table_names(sqlite_db)
    assert "users" in names
    assert "alembic_version" not in names

    engine = create_engine(f"sqlite:///{sqlite_db.as_posix()}")
    try:
        with engine.connect() as conn:
            count = conn.exec_driver_sql("SELECT count(*) FROM users").scalar()
    finally:
        engine.dispose()
    assert count == 0


def test_init_db_force_overrides_the_judgement(sqlite_db: Path) -> None:
    """`force=True` 无条件建表（`app init-db --force` 的语义）。

    钉的是「ORM 真的建了表」——这是 `--force` 与默认行为的唯一区别，
    也是判据是否生效的可观测点。
    """
    from sqlalchemy import Column, Integer, MetaData, Table

    from db import registry
    from db.base import Base
    from db.session import init_db

    registry.load_all_models()
    command.upgrade(_config(), "head")

    extra = Table(
        "zz_probe_only_model",
        MetaData(),
        Column("id", Integer, primary_key=True),
    )
    extra.to_metadata(Base.metadata)

    try:
        asyncio.run(init_db(force=True))
        assert "zz_probe_only_model" in _table_names(sqlite_db), "--force 没有真的建表"
    finally:
        Base.metadata.remove(extra)

    # `alembic_version` 归 Alembic 管，不在 ORM 元数据里，force 也不该碰它
    engine = create_engine(f"sqlite:///{sqlite_db.as_posix()}")
    try:
        with engine.connect() as conn:
            version = conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
    finally:
        engine.dispose()
    assert version == HEAD, f"force 建表破坏了迁移版本号：{version}"


# --------------------------------------------------------------------------- #
# 启动路径
# --------------------------------------------------------------------------- #
def test_startup_upgrades_a_migrated_database_instead_of_creating_tables(
    sqlite_db: Path,
) -> None:
    """启动时归迁移管的库走「跑迁移」，不是「ORM 建表」。

    判据有两条，缺一不可：

    1. **版本号前进了** —— 老行为下启动只会 `create_all`，版本号永远停在
       旧版，迁移欠账永远补不上；
    2. **ORM 没建表** —— 老行为下 `create_all` 会把「模型里有、迁移链里
       还没有」的表建出来，那张表正是将来 `create_table` 撞墙的根源。
    """
    from sqlalchemy import Column, Integer, MetaData, Table

    from app import _bootstrap_dev_schema
    from db import registry
    from db.base import Base

    registry.load_all_models()
    command.upgrade(_config(), "b7c4e2a18d63")

    extra = Table(
        "zz_probe_only_model",
        MetaData(),
        Column("id", Integer, primary_key=True),
    )
    extra.to_metadata(Base.metadata)

    try:
        asyncio.run(_bootstrap_dev_schema())

        engine = create_engine(f"sqlite:///{sqlite_db.as_posix()}")
        try:
            with engine.connect() as conn:
                version = conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
        finally:
            engine.dispose()
        assert version == HEAD, f"启动没有把库升到 head：{version}"
        assert "zz_probe_only_model" not in _table_names(sqlite_db), (
            "启动时归迁移管的库被 ORM 建了表：这张表会让后续迁移撞 DuplicateTableError"
        )
    finally:
        Base.metadata.remove(extra)


def test_startup_creates_tables_for_a_fresh_database(sqlite_db: Path) -> None:
    """从没被迁移管过的库，启动时按 ORM 建表（开发体验不变）。"""
    from sqlalchemy import Column, Integer, MetaData, Table

    from app import _bootstrap_dev_schema
    from db import registry
    from db.base import Base

    registry.load_all_models()
    extra = Table(
        "zz_probe_only_model",
        MetaData(),
        Column("id", Integer, primary_key=True),
    )
    extra.to_metadata(Base.metadata)

    try:
        asyncio.run(_bootstrap_dev_schema())
        assert "zz_probe_only_model" in _table_names(sqlite_db), (
            "没有被迁移管过的库应当按 ORM 建表：开发环境不该要求先跑迁移"
        )
        assert "alembic_version" not in _table_names(sqlite_db)
    finally:
        Base.metadata.remove(extra)


def test_startup_survives_a_broken_migration(sqlite_db: Path) -> None:
    """迁移失败**不该让开发环境起不来**。

    开发环境里迁移写错了还要改代码继续调，起不来就连改都改不了。
    失败必须留下痕迹（异常日志），但服务要能起来。

    断言只钉「没升到 head」，不钉停在哪个版本——因为**方言不同**：

    - PostgreSQL 的 DDL 是事务性的：一次 `upgrade` 中后续迁移失败，
      前面已执行的 DDL **整体回滚**，版本号退回起点；
    - SQLite 的 DDL **非事务性**（alembic 日志里那句
      `Will assume non-transactional DDL`）：前面已执行的迁移**留在原地**，
      版本号停在失败点的前一步。

    钉死具体版本号就等于把本仓的 PG 行为写进一条 sqlite 用例里——
    换到 PG 上这条用例就会红，而代码其实是对的。
    """
    from app import _bootstrap_dev_schema

    command.upgrade(_config(), "b7c4e2a18d63")

    # 让升级必然失败：`e5b91c4d7a26` 会 create_table('video_templates')，
    # 而这张表已经存在 → DuplicateTableError
    engine = create_engine(f"sqlite:///{sqlite_db.as_posix()}")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE video_templates (id VARCHAR(32) PRIMARY KEY)"
            )
    finally:
        engine.dispose()

    # 不抛异常就是「服务能起来」
    asyncio.run(_bootstrap_dev_schema())

    engine = create_engine(f"sqlite:///{sqlite_db.as_posix()}")
    try:
        with engine.connect() as conn:
            version = conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
    finally:
        engine.dispose()
    assert version != HEAD, "迁移失败了却报已升到 head"


# --------------------------------------------------------------------------- #
# 迁移链自身
# --------------------------------------------------------------------------- #
def test_every_revision_is_on_the_head_chain() -> None:
    """迁移链上不能有「挂在别处」的分支。

    `_revision_is_ancestor` 手工走 `down_revision` 链来判祖先关系，所以它
    依赖「每个 revision 都在 head 的祖先集合里」。真有分叉时
    `alembic upgrade head` 会直接报错——但那条守卫（`test_single_head`）
    只查 head 数量，查不出「某个 revision 被孤立在别处」。
    """
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_config())
    heads = script.get_heads()
    assert len(heads) == 1, f"迁移出现分叉：{heads}"

    seen: set[str] = set()
    stack = list(heads)
    while stack:
        revision = stack.pop()
        if revision in seen:
            continue
        seen.add(revision)
        rev = script.get_revision(revision)
        assert rev is not None, f"链上出现不认识的 revision：{revision}"
        down = rev.down_revision
        if down is None:
            continue
        stack.extend(down if isinstance(down, (tuple, list)) else [down])

    all_revisions = {rev.revision for rev in script.walk_revisions()}
    orphans = all_revisions - seen
    assert not orphans, f"这些迁移不在 head 的祖先链上：{sorted(orphans)}"
