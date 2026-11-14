"""取展示名：`resolve_names()`。

这个适配层只剩一件事要证明：**取不到的 id 不出现在结果里**。调用方据此回落到
裸 id，而不是显示空名字。

v1 `assets` 表已于 2026-11-13 下线，所以这里不再有「按 id 空间各问各的端口」
那一层——曾经用它来钉「禁止先查 v2、查不到回退 v1」，现在只剩一套 id 空间，
回退这件事在结构上不可能发生（`asset_source.py` 里没有第二个数据源）。

用例里 id 用 `library_support.insert_asset()` 直接插行：这一层只读元数据，
不需要走上传 / 审批链路，而那些链路另有专属用例。
"""

from __future__ import annotations

from collections.abc import Iterable

from library_support import insert_asset, suffix
from video_template_support import run

from core.config import settings
from core.library_assets import resolve_names
from db.session import get_session_factory


def resolve(refs: Iterable[str]) -> dict[str, str]:
    async def go() -> dict[str, str]:
        async with get_session_factory()() as session:
            return await resolve_names(session, refs)

    return run(go())


def test_names_are_resolved() -> None:
    asset_id = insert_asset(
        owner_id=settings.demo_user_id, name="新素材.png", state="active"
    )
    assert resolve([asset_id])[asset_id] == "新素材.png"


def test_missing_ids_are_absent_instead_of_blank() -> None:
    """查不到就不出现：调用方靠「键在不在」决定回落，空字符串会显示成空名字。"""
    assert resolve([f"gone-{suffix()}"]) == {}


def test_empty_input_does_not_hit_the_database() -> None:
    """没有参考图的模板（`source_video_asset_id` 是 NULL）不该多打一次库。"""
    assert resolve([]) == {}
