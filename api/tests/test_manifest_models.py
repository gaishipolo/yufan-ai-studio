"""manifest DB 化的数据形状守卫。

这些断言盯的不是「列存不存在」（`test_migrations.py::test_models_match_migrations`
已经管了），而是**迁移 packager 时最容易悄悄丢掉的语义**：

1. 素材从资产库取 —— 不许再出现本地文件路径列；
2. 提示词无长度上限 —— 换回 `String(n)` 会让长文案「发布成功但内容没了」；
3. 幂等 / 复用 / 版本 / 文字换场景这些能力都要有落点，不能因为 DB 化被简化掉。
"""

from __future__ import annotations

from sqlalchemy import Text

from db.base import Base
from db.registry import load_all_models

TABLES = (
    "template_manifests",
    "template_manifest_packs",
    "template_manifest_references",
    "template_manifest_functions",
)


def _columns(table: str) -> set[str]:
    load_all_models()
    return {column.name for column in Base.metadata.tables[table].columns}


def test_manifest_tables_are_discoverable() -> None:
    """`db.registry` 只扫 `modules.*.models`，新表放错文件就两边都缺表。"""
    load_all_models()
    assert set(TABLES) <= set(Base.metadata.tables)


def test_manifest_media_is_library_reference_only() -> None:
    """视频与参考图一律是 `library_assets.id`，不再有本地文件。"""
    forbidden = {
        "file",
        "prompt_file",
        "source_video_path",
        "source_video_file",
        "source_video_name",
    }
    for table in TABLES:
        leaked = _columns(table) & forbidden
        assert not leaked, f"{table} 残留本地文件列：{sorted(leaked)}"

    assert "video_asset_id" in _columns("template_manifests")
    assert "video_shared" in _columns("template_manifests")
    assert "asset_id" in _columns("template_manifest_references")


def test_manifest_prompt_has_no_length_limit() -> None:
    """与 `library_asset_generation.prompt` 同一个坑：sqlite 本地永远测不出超长。"""
    load_all_models()
    column = Base.metadata.tables["template_manifest_functions"].c.prompt
    assert isinstance(column.type, Text)
    assert column.type.length is None


def test_manifest_key_is_unique() -> None:
    """幂等边界：同 key 同配置返回原行，靠唯一索引兜住而不是应用层先查后写。"""
    load_all_models()
    table = Base.metadata.tables["template_manifests"]
    unique = {
        index.name: [column.name for column in index.columns]
        for index in table.indexes
        if index.unique
    }
    assert unique.get("uq_template_manifests_key") == ["template_key"]


def test_manifest_keeps_fingerprint_and_optimistic_lock() -> None:
    """幂等指纹与 version / revision 分工不能混。"""
    assert {
        "fingerprint",
        "version",
        "revision",
        "status",
        "published_at",
    } <= _columns("template_manifests")


def test_manifest_keeps_pack_reuse_and_version_semantics() -> None:
    """packs[] 的复用、必备用途、支持范围、发布回执都要有落点。"""
    assert {
        "reuse_pack_id",
        "required_roles",
        "supported_scopes",
        "wardrobe_mode",
        "published_pack_id",
        "published_pack_version",
    } <= _columns("template_manifest_packs")


def test_manifest_keeps_function_capabilities() -> None:
    """functions[]：文字换场景、用途绑定、媒体顺序、画幅时长、发布回执。"""
    assert {
        "scene_prompt_default",
        "input_mode",
        "bindings",
        "media_order",
        "scope_options",
        "aspect_ratio",
        "duration",
        "prompt_sha256",
        "published_template_id",
    } <= _columns("template_manifest_functions")


def test_manifest_reference_order_and_deletion_protection() -> None:
    """position 决定 {{Mixed N}} 编号；asset_id 复合索引是删除保护的依据。"""
    load_all_models()
    table = Base.metadata.tables["template_manifest_references"]
    assert "position" in {column.name for column in table.columns}
    assert "role" in {column.name for column in table.columns}
    composite = {
        tuple(column.name for column in index.columns)
        for index in table.indexes
        if not index.unique
    }
    assert ("asset_id", "pack_id") in composite
