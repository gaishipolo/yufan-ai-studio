"""老 `catalog.json` → `video_templates` 表的导入（`app import-video-templates`）。

这条路径只有一次执行机会却决定「升级后模板库是空还是有内容」，所以断言要盯住
三件容易静默出错的事：

1. **幂等**：重跑一次必须只报「未变」，不能复制出第二份；中断后能直接重跑。
2. **两种命名都认**：老文件历史上出现过 camelCase（`model_dump(by_alias=True)`）
   和 snake_case，认不出来不会报错，只会把字段全填成默认值。
3. **`--dry-run` 真的不写库**：它存在的意义就是「先看看会改什么」，
   写完再 rollback 也一样要保证 commit 没被调用。

数据库是整场共用的一个 sqlite 文件，所以每个用例用自己的 id 前缀，
并在最后把自己写进去的行删掉（尤其蓝本行：它会挡住后面用例写入）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from video_template_support import remove, run, unpin_all

from core.config import settings
from core.exceptions import BadRequest
from db.session import get_session_factory
from modules.template_pack.models import VideoTemplate
from modules.template_pack.video_template_import import (
    import_catalog,
    legacy_catalog_path,
)


def _write(tmp_path: Path, items: list[object]) -> Path:
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps({"version": 1, "items": items}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _row(**overrides: object) -> dict[str, object]:
    """最小可用记录：只给导入真正需要的字段，其余走默认值。

    `templateKey` 缺省跟着 `id` 走 —— 它带唯一约束，写死一个值会让
    「同一份目录里两条记录」直接撞索引，而在生产里两条不同 id 的模板本来就该
    有不同的 `templateKey`。
    """
    template_id = str(overrides.get("id", "imp-1"))
    base: dict[str, object] = {
        "id": template_id,
        "templateKey": template_id,
        "name": "导入样本",
        "prompt": "对 {{Mixed 1}} 做替换，全片无字幕。",
        "sourceVideoAssetId": None,
        "sourceVideoName": "",
        "mediaOrder": [],
        "generation": {},
        "rules": {},
        "pinned": False,
    }
    base.update(overrides)
    return base


async def _get(template_id: str) -> VideoTemplate | None:
    async with get_session_factory()() as session:
        return await session.get(VideoTemplate, template_id)


def _import(path: Path, *, dry_run: bool = False):
    async def go():
        async with get_session_factory()() as session:
            return await import_catalog(session, path, dry_run=dry_run)

    return run(go())


def test_import_creates_rows_and_fills_defaults(tmp_path: Path) -> None:
    """新建两条，字段按 JSON 落库；没写的列按模型默认值落，不是 NULL。"""
    path = _write(
        tmp_path,
        [
            _row(id="imp-a", templateKey="imp-a", name="A", packId="pack-a", packVersion=3),
            _row(id="imp-b", templateKey="imp-b", name="B"),
        ],
    )
    try:
        report = _import(path)
        assert (report.created, report.updated, report.unchanged) == (2, 0, 0)

        first = run(_get("imp-a"))
        assert first is not None
        assert first.name == "A"
        assert first.pack_id == "pack-a"
        assert first.pack_version == 3
        assert first.aspect_ratio == "9:16"
        assert first.default_kind == "product"
        assert first.pinned is False
    finally:
        remove("imp-a")
        remove("imp-b")


def test_import_accepts_snake_case_records(tmp_path: Path) -> None:
    """老文件里的 snake_case 也要认得：认不出来只会静默填空值。"""
    path = _write(
        tmp_path,
        [
            {
                "id": "imp-snake",
                "template_key": "snake-key",
                "name": "下划线",
                "prompt": "对 {{Mixed 1}} 做替换",
                "source_video_asset_id": "source-snake",
                "aspect_ratio": "16:9",
                "default_kind": "character",
                "pack_version": 5,
            }
        ],
    )
    try:
        report = _import(path)
        assert report.created == 1
        row = run(_get("imp-snake"))
        assert row is not None
        assert row.template_key == "snake-key"
        assert row.source_video_asset_id == "source-snake"
        assert row.aspect_ratio == "16:9"
        assert row.default_kind == "character"
        assert row.pack_version == 5
    finally:
        remove("imp-snake")


def test_second_import_is_a_no_op(tmp_path: Path) -> None:
    """幂等：同一份文件第二次导入只报「未变」，不复制也不逐行重写。"""
    path = _write(tmp_path, [_row(id="imp-idem-a"), _row(id="imp-idem-b")])
    try:
        assert _import(path).created == 2
        again = _import(path)
        assert (again.created, again.updated, again.unchanged) == (0, 0, 2)
    finally:
        remove("imp-idem-a")
        remove("imp-idem-b")


def test_import_updates_changed_fields(tmp_path: Path) -> None:
    """改过提示词的那条要更新，没改的不动 —— 报告里分开计数。"""
    path = _write(tmp_path, [_row(id="imp-up-a", name="旧名"), _row(id="imp-up-b")])
    try:
        assert _import(path).created == 2

        path = _write(tmp_path, [_row(id="imp-up-a", name="新名"), _row(id="imp-up-b")])
        report = _import(path)
        assert (report.created, report.updated, report.unchanged) == (0, 1, 1)
        row = run(_get("imp-up-a"))
        assert row is not None
        assert row.name == "新名"
    finally:
        remove("imp-up-a")
        remove("imp-up-b")


def test_import_keeps_only_the_newest_pinned(tmp_path: Path) -> None:
    """多条 `pinned` 收敛成一条：留 `createdAt` 最新的，并在报告里说明。

    `uq_video_templates_pinned` 只允许一条，不收敛的话导入会直接撞索引失败，
    而那时的报错信息指向「约束冲突」，不会告诉你「老目录里有三个蓝本」。
    """
    unpin_all()
    path = _write(
        tmp_path,
        [
            _row(id="imp-pin-old", createdAt="2024-01-01T00:00:00Z", pinned=True),
            _row(id="imp-pin-new", createdAt="2025-06-01T00:00:00Z", pinned=True),
            _row(id="imp-pin-mid", createdAt="2025-01-01T00:00:00Z", pinned=True),
        ],
    )
    try:
        report = _import(path)
        assert report.created == 3
        assert any("pinned" in note for note in report.notes)

        newest = run(_get("imp-pin-new"))
        assert newest is not None and newest.pinned is True
        for template_id in ("imp-pin-old", "imp-pin-mid"):
            other = run(_get(template_id))
            assert other is not None and other.pinned is False
    finally:
        for template_id in ("imp-pin-old", "imp-pin-new", "imp-pin-mid"):
            remove(template_id)


def test_duplicate_template_key_is_skipped_and_reported(tmp_path: Path) -> None:
    """同键两条只进一条：`template_key` 唯一，一条坏数据不该让整份导入失败。

    旧实现把 `template_key` 当「脚本重试的稳定标识」（同键不同配置直接 409），
    所以真实目录里不该重复；`catalog.json` 是人工可编辑的，重复时就按先到先得
    跳过，并在报告里点名，避免演变成 `IntegrityError` 把全部模板挡在门外。
    """
    path = _write(
        tmp_path,
        [
            _row(id="imp-dup-a", templateKey="dup-key", name="先到"),
            _row(id="imp-dup-b", templateKey="dup-key", name="后到"),
        ],
    )
    try:
        report = _import(path)
        assert (report.created, report.skipped) == (1, 1)
        assert any("template_key" in note for note in report.notes)
        assert run(_get("imp-dup-a")) is not None
        assert run(_get("imp-dup-b")) is None
    finally:
        remove("imp-dup-a")
        remove("imp-dup-b")


def test_dry_run_reports_without_writing(tmp_path: Path) -> None:
    """`--dry-run` 只出报告：行数照报，但库里一行都不该多。"""
    path = _write(tmp_path, [_row(id="imp-dry")])
    try:
        report = _import(path, dry_run=True)
        assert report.created == 1
        assert run(_get("imp-dry")) is None
    finally:
        remove("imp-dry")


def test_missing_catalog_is_not_an_error(tmp_path: Path) -> None:
    """没装过老目录（新部署）应当安静跳过，而不是把升级脚本卡死。"""
    report = _import(tmp_path / "nope.json")
    assert (report.created, report.updated, report.unchanged) == (0, 0, 0)
    assert report.notes


def test_broken_catalog_raises_instead_of_reporting_success(tmp_path: Path) -> None:
    """两份坏文件都必须抛：静默返回「导入 0 条」等于告诉运维「本来就没有模板」。"""
    broken = tmp_path / "broken.json"
    broken.write_text("{ not json", encoding="utf-8")
    with pytest.raises(BadRequest):
        _import(broken)

    no_items = tmp_path / "no-items.json"
    no_items.write_text(json.dumps({"version": 1}), encoding="utf-8")
    with pytest.raises(BadRequest):
        _import(no_items)


def test_legacy_catalog_path_follows_the_setting(monkeypatch, tmp_path: Path) -> None:
    """`VIDEO_TEMPLATE_CATALOG` 现在只有「导入源位置」这一个用途。"""
    monkeypatch.setattr(settings, "video_template_catalog", str(tmp_path / "custom.json"))
    assert legacy_catalog_path() == tmp_path / "custom.json"

    monkeypatch.setattr(settings, "video_template_catalog", "")
    fallback = legacy_catalog_path()
    assert fallback.name == "catalog.json"
    assert fallback.parent.name == "video_templates"
