"""按 id 空间分流取展示名：`resolve_names()`。

这个适配层存在的唯一理由，是 v1 `assets.id` 与 v2 `library_assets.id` 形状相同、
而端口注释明令禁止「先查 v2、查不到回退 v1」。所以它必须证明两件事：

1. **按空间各问各的端口**，不是「先 v2 后 v1」的兜底；
2. **取不到的 id 不出现在结果里**（调用方据此回落到裸 id，而不是显示空名字）。

用例里 v2 的 id 用 `library_support.insert_asset()` 直接插行：这一层只读元数据，
不需要走上传 / 审批链路，而那些链路另有专属用例。
"""

from __future__ import annotations

from library_support import insert_asset, suffix
from video_template_support import run

from core.config import settings
from db.session import get_session_factory
from modules.asset.models import Asset
from modules.template.asset_source import resolve_names


def v1_asset(asset_id: str, name: str) -> None:
    """造一条 v1 素材（同 id 覆盖：测试库整场共用）。"""

    async def go() -> None:
        async with get_session_factory()() as session:
            row = await session.get(Asset, asset_id)
            if row is None:
                session.add(
                    Asset(
                        id=asset_id,
                        name=name,
                        type="image",
                        category="product",
                        owner_id="u_001",
                    )
                )
            else:
                row.name = name
            await session.commit()

    run(go())


def resolve(refs: list[tuple[str | None, str]]) -> dict[str, str]:
    async def go() -> dict[str, str]:
        async with get_session_factory()() as session:
            return await resolve_names(session, refs)

    return run(go())


def test_each_space_is_asked_separately() -> None:
    """同一批里混着两套 id：v1 走 `AssetLookup`、v2 走 `LibraryLookup`。"""
    old_id = f"v1-{suffix()}"
    v1_asset(old_id, "旧素材.jpg")
    new_id = insert_asset(owner_id=settings.demo_user_id, name="新素材.png", state="active")

    names = resolve([(old_id, "v1"), (new_id, "v2")])
    assert names[old_id] == "旧素材.jpg"
    assert names[new_id] == "新素材.png"


def test_missing_ids_are_absent_instead_of_blank() -> None:
    """查不到就不出现：调用方靠「键在不在」决定回落，空字符串会显示成空名字。"""
    assert resolve([(f"gone-{suffix()}", "v1"), (f"gone-{suffix()}", "v2")]) == {}


def test_space_is_honoured_instead_of_guessed() -> None:
    """标了 v2 的 v1 id **不能**被回退找到。

    这条是「禁止隐式兜底」的可执行版本：如果实现写成「先查 v2、查不到再查 v1」，
    切换期就会出现「某一侧悄悄读到另一半数据」，而且只在删除之后才暴露成裂图。
    名字这一层回退错了还只是显示问题；同一个写法搬到授权闸，就是把别人的私有图
    交给供应商。
    """
    old_id = f"v1-{suffix()}"
    v1_asset(old_id, "只在 v1 里.jpg")
    assert resolve([(old_id, "v2")]) == {}


def test_none_and_unknown_space_do_not_raise() -> None:
    """没有原片的模板（`source_video_asset_id` 是 NULL）与坏标记都不该让保存失败。"""
    old_id = f"v1-{suffix()}"
    v1_asset(old_id, "有名字.jpg")
    names = resolve([(None, "v1"), (old_id, "v1"), (old_id, "v9")])
    assert names == {old_id: "有名字.jpg"}
