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
    from core.config import get_settings
    from db import session as db_session

    db_file = tmp_path / "migrated.db"
    monkeypatch.setenv("DB_URL", f"sqlite+aiosqlite:///{db_file.as_posix()}")
    get_settings.cache_clear()
    db_session.reset_state_for_tests()
    try:
        command.upgrade(_config(), "head")
        yield db_file
    finally:
        get_settings.cache_clear()
        db_session.reset_state_for_tests()


def test_single_head() -> None:
    """迁移链只能有一个 head：分叉后 `upgrade head` 失败，而报错指向的是文件而不是原因。"""
    heads = ScriptDirectory.from_config(_config()).get_heads()
    assert len(heads) == 1, f"迁移出现分叉：{heads}"


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
