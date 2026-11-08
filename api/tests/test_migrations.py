"""迁移与模型的一致性守卫（数据线第一道闸）。

## 为什么必须有这条测试

开发与测试建表走 `Base.metadata.create_all`（`db/session.py`），生产走 Alembic。
**两条路径从来没有被对齐验证过**：加一列、忘了写迁移，测试照样全绿，
生产启动后第一条查询就 `no such column`。这类故障的修复成本极高
（要停服、补迁移、对已经写坏的数据做判断），而提前发现只需要一条测试。

## 它验什么

1. 空库能一路 `upgrade head`（迁移链没有断点、没有依赖历史数据）；
2. 升到 head 之后，`compare_metadata` 与模型零差异（没人改模型不改迁移）；
3. 只有一个 head（分叉的迁移在 `upgrade head` 时会报错，但报错信息很难读）。

测试用**临时 sqlite 文件**而不是内存库：迁移里的 `batch_alter_table`
会重建表，需要真实的文件语义才和落盘行为一致。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

API_ROOT = Path(__file__).resolve().parents[1]


def _config() -> Config:
    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "migrations"))
    return cfg


@pytest.fixture()
def migrated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """把库迁到 head，返回 sqlite 文件路径；用例结束后把配置与引擎还原。"""
    from core.config import reload_settings
    from db import session as db_session

    db_file = tmp_path / "migrated.db"
    monkeypatch.setenv("DB_URL", f"sqlite+aiosqlite:///{db_file.as_posix()}")
    reload_settings()
    db_session.reset_state_for_tests()
    try:
        command.upgrade(_config(), "head")
        yield db_file
    finally:
        reload_settings()
        db_session.reset_state_for_tests()


def test_single_head() -> None:
    """迁移链只能有一个 head：分叉后 `upgrade head` 失败，而报错指向的是文件而不是原因。"""
    heads = ScriptDirectory.from_config(_config()).get_heads()
    assert len(heads) == 1, f"迁移出现分叉：{heads}"


# `sa.DATETIME` / `sa.TIME` / `sa.DATE` 是 **sqlite 方言专有**类型
# （`sqlalchemy.dialects.sqlite`），通用类型是 `sa.DateTime` / `sa.Time` / `sa.Date`。
# 它们进迁移的唯一途径是 autogenerate 对着一个 **sqlite 库**做反射。
_SQLITE_ONLY_TYPES = (
    "sa.DATETIME(",
    "sa.TIME(",
    "sa.DATE(",
    "from sqlalchemy.dialects.sqlite",
    "from sqlalchemy.dialects import sqlite",
)


def test_migrations_do_not_use_sqlite_only_types() -> None:
    """迁移必须能在 PostgreSQL 上执行：sqlite 专有类型在 PG 上是 `type does not exist`。

    这条守卫的由来：`df546efef239` 的 `downgrade()` 用 `sa.DATETIME()` 重建
    `video_templates`，那是对着 sqlite 反射出来的。upgrade 能过（新表都用通用类型），
    但 `alembic downgrade base` 在 PG 上报 `type "datetime" does not exist`
    —— **回滚部署这条路是断的**，而 sqlite 上一切正常，本地永远测不出来。

    CI 的 PG job 从 run 40 起一直红就是这个（观察期 continue-on-error，
    所以没人被挡住，也就没人发现）。

    纯文本扫描，不需要数据库：本仓的 sqlite 测试跑不到 PG 方言。
    """
    offenders: list[str] = []
    for path in sorted((API_ROOT / "migrations" / "versions").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for needle in _SQLITE_ONLY_TYPES:
            if needle in text:
                offenders.append(f"{path.name}: {needle}")

    assert not offenders, (
        "迁移里出现了 sqlite 专有类型，PG 上会失败（改用 sa.DateTime / sa.Time / sa.Date）：\n  "
        + "\n  ".join(offenders)
    )


def test_every_migration_defines_downgrade() -> None:
    """每个迁移都要有 `downgrade()`：没有它，出事时回滚不了。

    空实现是允许的（数据迁移常常无法逆向），但**函数必须存在**——
    缺了它 alembic 到这一步会直接 `NotImplementedError`，而发现时机是
    「生产要回滚」的那一刻。
    """
    missing = [
        path.name
        for path in sorted((API_ROOT / "migrations" / "versions").glob("*.py"))
        if "def downgrade(" not in path.read_text(encoding="utf-8")
    ]
    assert not missing, "缺少 downgrade()：" + ", ".join(missing)


def test_migrations_apply_to_empty_database(migrated: Path) -> None:
    """空库能升到 head。这条同时保证「新部署」这条路是通的。"""
    from db import registry

    registry.load_all_models()
    engine = create_engine(f"sqlite:///{migrated.as_posix()}")
    try:
        with engine.connect() as conn:
            version = conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
    finally:
        engine.dispose()
    assert version, "迁移执行后 alembic_version 应当有值"


def test_models_match_migrations(migrated: Path) -> None:
    """模型与迁移零差异。

    出现差异说明有人改了 ORM 却没生成迁移——测试环境（create_all）看不出来，
    生产（Alembic）会缺列缺表。修复方式：`alembic revision --autogenerate`。
    """
    from db import registry
    from db.base import Base

    registry.load_all_models()
    engine = create_engine(f"sqlite:///{migrated.as_posix()}")
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(conn, opts={"compare_type": True})
            diff = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    assert diff == [], _render_diff(diff)


def test_upgrade_keeps_existing_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """**带数据的库**也要能升上来。

    空库跑迁移是一条几乎不会失败的路径，而事故都发生在有数据的库上：sqlite 的
    batch 模式重建表时，`INSERT ... SELECT` 只带**原有**列，新加列的值全靠它自己的
    `server_default`。若「加列 + 去默认值」写在同一个 batch 块里，重建时那一列
    已经没有默认值，而 SELECT 又不带它 → `NOT NULL constraint failed`。
    本仓库刚踩过一次（`app_meta.version`），空库绿、开发库一升就炸。

    做法：升到 `app_meta` 建表那一版 → 手工塞一行 → 升到 head → 数据还在、新列有值。
    """
    import sqlite3

    from core.config import reload_settings
    from db import session as db_session

    db_file = tmp_path / "seeded.db"
    monkeypatch.setenv("DB_URL", f"sqlite+aiosqlite:///{db_file.as_posix()}")
    reload_settings()
    db_session.reset_state_for_tests()
    try:
        config = _config()
        command.upgrade(config, "dbbea50e7828")
        # 显式 close：`with sqlite3.connect(...)` 只提交不关闭，连接留给 GC 回收时
        # 会在别的线程里抛异常，变成一条与用例无关的 PytestUnhandledThreadException
        conn = sqlite3.connect(db_file)
        try:
            conn.execute(
                "INSERT INTO app_meta (key, value, created_at, updated_at)"
                " VALUES (?, ?, ?, ?)",
                (
                    "permissions.catalog",
                    '["asset:read"]',
                    "2026-01-01 00:00:00.000000",
                    "2026-01-01 00:00:00.000000",
                ),
            )
            conn.commit()
        finally:
            conn.close()
        command.upgrade(config, "head")
        conn = sqlite3.connect(db_file)
        try:
            rows = conn.execute(
                "SELECT key, value, version, updated_by FROM app_meta"
            ).fetchall()
        finally:
            conn.close()
    finally:
        reload_settings()
        db_session.reset_state_for_tests()

    assert rows == [("permissions.catalog", '["asset:read"]', 1, "")], (
        "升级把已有数据弄丢/弄坏：" + repr(rows)
    )


def test_inflight_jobs_survive_the_lease_token_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """加 `lease_token` 会重建 `queued_jobs` 表，在途任务一条都不能丢。

    丢一行的后果不是「少了个任务」：那一行是用户点下去的那次生成的唯一凭证，
    行没了就再也没人会去跑它，页面永远转圈。所以这条用例盯的是「行还在」，
    而不是「列加上了」——后者 `compare_metadata` 已经管了。
    """
    import sqlite3

    from core.config import reload_settings
    from db import session as db_session

    db_file = tmp_path / "jobs.db"
    monkeypatch.setenv("DB_URL", f"sqlite+aiosqlite:///{db_file.as_posix()}")
    reload_settings()
    db_session.reset_state_for_tests()
    try:
        config = _config()
        command.upgrade(config, "a1c4e7f2b930")  # 加列之前的最后一版
        conn = sqlite3.connect(db_file)
        try:
            conn.execute(
                "INSERT INTO queued_jobs (id, name, payload, status, run_at, attempts,"
                " max_attempts, trace_id, last_error, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "job-under-upgrade",
                    "image.poll",
                    '{"task": "t-1"}',
                    "running",
                    "2026-01-01 00:00:00.000000",
                    1,
                    3,
                    "trace-1",
                    "",
                    "2026-01-01 00:00:00.000000",
                    "2026-01-01 00:00:00.000000",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        command.upgrade(config, "head")
        conn = sqlite3.connect(db_file)
        try:
            rows = conn.execute(
                "SELECT id, name, status, attempts, lease_token FROM queued_jobs"
            ).fetchall()
        finally:
            conn.close()
    finally:
        reload_settings()
        db_session.reset_state_for_tests()

    assert rows == [("job-under-upgrade", "image.poll", "running", 1, None)], (
        "升级把在途任务弄丢/弄坏了：" + repr(rows)
    )


def _render_diff(diff: list) -> str:
    lines = ["模型与迁移不一致（生产会用迁移建表，缺的列/表只会在生产炸）："]
    for item in diff:
        # compare_metadata 的返回是「元组或元组列表」，两种都要能读
        entries = item if isinstance(item, list) else [item]
        for entry in entries:
            if not isinstance(entry, tuple):
                lines.append(f"  - {entry}")
                continue
            kind = entry[0]
            if kind == "add_table":
                lines.append(f"  - 迁移缺表：{entry[1].name}")
            elif kind == "remove_table":
                lines.append(f"  - 迁移里多出表：{entry[1].name}")
            elif kind == "add_column":
                lines.append(f"  - 迁移缺列：{entry[2]} . {entry[3].name}")
            elif kind == "remove_column":
                lines.append(f"  - 迁移里多出列：{entry[2]} . {entry[3].name}")
            else:
                lines.append(f"  - {kind}: {entry[1:]}")
    return "\n".join(lines)
