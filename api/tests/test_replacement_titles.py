"""展示名规则：列表、核对弹窗、档案详情与任务中心必须逐字一致。"""

from __future__ import annotations

from modules.template.titles import task_name


def test_long_source_name_is_truncated_not_wrapped() -> None:
    title = task_name("abcdefghijklmnopqrstuvwxyz.mp4", "character", "张三", "ab" * 16)
    assert title == "abcdefghijklmnopqr… · 人物替换 · 张三 · #ABABABAB"


def test_short_source_name_keeps_extension_stripped() -> None:
    assert task_name("vlog.MP4", "product", "水杯", "1234abcd" * 4) == (
        "vlog · 产品替换 · 水杯 · #1234ABCD"
    )


def test_missing_and_empty_source_names_fall_back() -> None:
    assert task_name(None, "scene", "海边", "0" * 32).startswith("历史档案未记录原片 · ")
    assert task_name("   ", "scene", "海边", "0" * 32).startswith("未命名原片 · ")


def test_target_is_optional() -> None:
    """没填对象时不能留下「· ·」这种空洞，也不能少一段分隔。"""
    assert task_name("vlog.mp4", "scene", "  ", "a" * 32) == "vlog · 场景替换 · #AAAAAAAA"
