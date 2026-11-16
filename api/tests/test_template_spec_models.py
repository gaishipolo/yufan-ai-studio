"""模板规格三张表的形状守卫。

`test_migrations.py::test_models_match_migrations` 管"列/索引一致"；这里盯的是
**结构一旦走偏就会悄悄丢掉的语义**：

1. 编号不落库 —— 槽位表里不该出现 `token` / `mixed_number` 这类列；
2. 快照表不可变 —— 同一 `(spec_id, version)` 只能有一份；
3. 槽位表只有一根 `type` 轴 —— 旧的 `source` 轴不许回来；
4. 草稿只在 head 上有一份 JSON，版本表里是展开后的两段正文。
"""

from __future__ import annotations

from db.base import Base
from db.registry import load_all_models

TABLES = ("template_specs", "template_spec_versions", "template_spec_slots")


def _columns(table: str) -> set[str]:
    load_all_models()
    return {column.name for column in Base.metadata.tables[table].columns}


def _uniques(table: str) -> set[frozenset[str]]:
    load_all_models()
    return {
        frozenset(column.name for column in constraint.columns)
        for constraint in Base.metadata.tables[table].constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }


def test_spec_tables_are_discoverable() -> None:
    load_all_models()
    assert set(TABLES) <= set(Base.metadata.tables)


def test_numbers_are_not_stored() -> None:
    """编号是编译产物：一旦落库，少填一张就会和历史对不上。"""
    leaked = _columns("template_spec_slots") & {"token", "mixed_number", "ref_token", "slot_index"}
    assert not leaked, f"槽位表不该存编号：{sorted(leaked)}"
    assert "position" in _columns("template_spec_slots")


def test_slot_points_at_one_asset_and_has_no_source_axis() -> None:
    columns = _columns("template_spec_slots")
    assert {"type", "binding", "ref_mode", "asset_id", "tag_id"} <= columns
    # 「要什么」只有一条表达：`type`。旧的 `source` 轴不许回来
    # （两根轴并存时接口能收到自相矛盾的组合）。
    assert "source" not in columns
    # 指向「一整组素材」的那一列也没有了：槽位只指向单个素材。
    assert "pack_id" not in columns


def test_version_snapshot_is_unique_per_spec() -> None:
    assert frozenset({"spec_id", "version"}) in _uniques("template_spec_versions")


def test_slot_keys_and_positions_are_unique_within_a_version() -> None:
    uniques = _uniques("template_spec_slots")
    assert frozenset({"version_id", "slot_key"}) in uniques
    assert frozenset({"version_id", "position"}) in uniques


def test_draft_lives_on_head_only() -> None:
    """草稿是整份 JSON；版本表存展开后的两段正文，不再留 JSON 草稿列。"""
    assert "draft" in _columns("template_specs")
    version_columns = _columns("template_spec_versions")
    assert "prompt" in version_columns
    # 两段合并（2026-11-14）：旧列必须真的没了，否则"哪段能被模板库改"会以列的形式残留
    assert not {"prompt_locked", "prompt_editable"} & version_columns
    assert "draft" not in version_columns


def test_spec_key_is_unique_across_the_workspace() -> None:
    assert frozenset({"spec_key"}) in _uniques("template_specs")
