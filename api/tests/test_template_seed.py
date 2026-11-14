"""模板演示数据：走 `LibraryLookup` 取素材。

这条用例是「步 1：seed 去裸 SQL」的验收：换成 `find_ids()` 之后演示数据仍然成立。
原来那条 `SELECT id FROM assets WHERE category = ...` 换个来源后如果什么都不产出，
`app seed` 会照常 exit 0，只是第一屏少了「可用的包」；这种退化没人会报。

（`asset_id_space` 曾经是这里的第二条断言——并存期默认值是 v1，用 v2 资产的地方
必须显式写。v1 `assets` 表已于 2026-11-13 下线，那一列连同这条断言一起消失了。）
"""

from __future__ import annotations

import asyncio

from library_support import insert_asset, suffix

from api.module import SeedContext

#: 每个测试会话一个独一无二的演示账号。
#
# 必须唯一：`find_ids` 按 `created_at` 取最早的一批，而测试库是 session 级共享的——
# 写死账号名，第二次跑同一份代码（或同一会话里再插图）就会取到上一次的数据，
# 断言 `seeded[:3] == ids[:3]` 于是随机失败。踩过一次。
DEMO_USER = f"u_seed_{suffix()}"
#: 一条素材都没有的账号：用来验「空库只种草稿」。
EMPTY_USER = f"u_seed_empty_{suffix()}"


def _seed(owner_id: str) -> tuple[int, list[tuple], list[tuple]]:
    """跑一次 seed，返回（创建数, 该账号的参考行, 该账号的冻结版本）。

    按**账号**过滤而不是全表读：测试库是同一个，上一条用例种的封装会留在表里，
    全表读会让「空库只种草稿」这条断言永远看到别人的数据。
    """

    async def run():
        from sqlalchemy import select

        from db.session import get_session_factory
        from modules.template_pack.models import (
    TemplatePack,
    TemplatePackReference,
    TemplatePackVersion,
)
        from modules.template_pack.seed import seed

        async with get_session_factory()() as session:
            created = await seed(
                session, SeedContext(demo_user_id=owner_id, demo_user_name="演示")
            )
            my_packs = select(TemplatePack.id).where(TemplatePack.owner_id == owner_id)
            refs = (
                await session.execute(
                    select(TemplatePackReference.asset_id)
                    .where(TemplatePackReference.pack_id.in_(my_packs))
                    .order_by(TemplatePackReference.pack_id, TemplatePackReference.position)
                )
            ).all()
            versions = (
                await session.execute(
                    select(TemplatePackVersion.reference_ids).where(
                        TemplatePackVersion.pack_id.in_(my_packs)
                    )
                )
            ).all()
            return created, [tuple(row) for row in refs], [tuple(row) for row in versions]

    return asyncio.run(run())


def _make_images(count: int) -> list[str]:
    return [
        insert_asset(
            name=f"演示图 {index}.png",
            kind="image",
            owner_id=DEMO_USER,
            state="active",
        )
        for index in range(count)
    ]


def test_seed_builds_both_packs_from_v2_assets() -> None:
    ids = _make_images(5)
    created, refs, versions = _seed(DEMO_USER)

    assert created == 2, "应当同时种出「已发布的人物包」与「待补范围的产品草稿」"
    seeded = [asset_id for (asset_id,) in refs]
    assert set(seeded) <= set(ids), "演示封装引用了不是 v2 的资产 id"
    assert len(seeded) == 5, "3 张人物 + 2 张产品，一张都不该丢"
    assert set(seeded) == set(ids), "5 张图应当全部被收进两个封装"
    # 不比较「哪三张进了人物包」：`find_ids` 的排序是 `(created_at, id)`，
    # 同一瞬间插入的行按 id 决胜，而 id 是随机 hex。切片顺序本身不重要，
    # 演示数据要证明的是「有两种封装形态」，不是「哪张图属于哪个包」。

    assert versions, "已发布的包必须写一条冻结版本"


def test_seed_without_v2_assets_only_creates_the_draft() -> None:
    """v2 库是空的：只种草稿，不抛异常（`app seed` 在空环境里也要成功）。"""
    created, refs, versions = _seed(EMPTY_USER)
    assert created == 0
    assert refs == []
    assert versions == []
