"""`LibraryLookup` 步 2.5b 扩的两条查询：封装展开与按条件列 id。

为什么要有独立用例：这两个方法是**跨模块的唯一读入口**（模板库 §23.5 的
「B 选封装当参考」、seed / 导入的「按条件取 id」）。它们出错的方式都很安静——

- 封装展开静默少一张图 → 槽位顺序整体前移 → 错误一路传到成片的提示词编号；
- `find_ids` 把回收站里的资产也回出来 → 演示数据 / 历史任务指向用户已经删掉的东西。

所以这里盯的是**边界**而不只是 happy path：不可读的封装不出现在键里、不可读的成员
留在列表里但标 `readable=False`、非封装 id 不被当成空封装。
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from library_support import LIB_RW, admin_headers, insert_asset, make_user, upload_image

from core.context import CurrentUser
from core.ports.library_lookup import LibraryAssetKind, get_library_lookup

ADMIN_PERMS = frozenset({"*"})


def _actor(user_id: str) -> CurrentUser:
    return CurrentUser(id=user_id, name="tester", roles=("admin",), permissions=ADMIN_PERMS)


def _expand(pack_ids: list[str], user_id: str):
    async def run():
        from db.session import get_session_factory

        async with get_session_factory()() as session:
            return await get_library_lookup().expand_packs(
                session, _actor(user_id), pack_ids
            )

    return asyncio.run(run())


def _find(user_id: str, **kwargs):
    async def run():
        from db.session import get_session_factory

        async with get_session_factory()() as session:
            return await get_library_lookup().find_ids(session, _actor(user_id), **kwargs)

    return asyncio.run(run())


def _create_pack(client: TestClient, api: str, headers: dict, body: dict) -> dict:
    response = client.post(f"{api}/library/packs", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_expand_packs_returns_members_in_position_order(
    client: TestClient, api: str
) -> None:
    """成员顺序 = `position`，`role` / `description` 原样带出来。

    顺序是**槽位**：模板编译器按它编号 `reference-1.jpg` / `reference-2.jpg`。
    重排一次，提示词里「第一张是正面」就指向了侧面。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    first = upload_image(client, api, owner_h, name="正面.png", color="orchid")
    second = upload_image(client, api, owner_h, name="侧面.png", color="olive")

    pack = _create_pack(
        client,
        api,
        owner_h,
        {
            "name": "产品三视图",
            "items": [
                {"assetId": second["id"], "role": "side", "description": "侧面"},
                {"assetId": first["id"], "role": "front", "description": "正面"},
            ],
        },
    )

    expanded = _expand([pack["id"]], owner["id"])
    assert set(expanded) == {pack["id"]}
    members = expanded[pack["id"]]
    assert [item.id for item in members] == [second["id"], first["id"]]
    assert [item.role for item in members] == ["side", "front"]
    assert [item.description for item in members] == ["侧面", "正面"]
    # `position` 从 1 开始（封装创建时服务端按顺序编号），不是 0
    assert [item.position for item in members] == [1, 2]
    assert all(item.readable for item in members), "自己的封装成员应当全部可读"
    assert members[0].kind is LibraryAssetKind.IMAGE
    assert members[0].storage_key, "展开要带 storageKey，调用方不必再按 id 取一次"


def test_expand_packs_omits_packs_the_actor_cannot_read(
    client: TestClient, api: str
) -> None:
    """不可读的封装**不出现在键里**，不是空列表。

    空列表的含义是「这个封装是空的」，而「读不到」是另一件事：两者混在一起，
    调用方会拿一份空清单去编译，产出一个人物都没有的成片。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    stranger, _stranger_h = make_user(client, admin, LIB_RW)
    image = upload_image(client, api, owner_h, name="私有.png", color="orchid")
    pack = _create_pack(
        client, api, owner_h, {"name": "私有包", "items": [{"assetId": image["id"]}]}
    )

    assert _expand([pack["id"]], stranger["id"]) == {}
    # 自己的能看到——否则上面那条断言可能只是「所有封装都读不到」
    assert set(_expand([pack["id"]], owner["id"])) == {pack["id"]}


def test_expand_packs_keeps_orphan_members_but_marks_them_unreadable(
    client: TestClient, api: str
) -> None:
    """成员被彻底删掉（孤儿行）时仍占一个位置，`readable=False`。

    静默丢掉会让后面的槽位整体前移；占位 + 标记才能让调用方报出
    「封装《X》的第 2 张已经不存在」。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    image = upload_image(client, api, owner_h, name="会消失.png", color="orchid")
    pack = _create_pack(
        client, api, owner_h, {"name": "孤儿包", "items": [{"assetId": image["id"]}]}
    )

    async def orphan() -> None:
        from sqlalchemy import update

        from db.session import get_session_factory
        from modules.library.models import LibraryPackItem

        async with get_session_factory()() as session:
            await session.execute(
                update(LibraryPackItem)
                .where(LibraryPackItem.pack_id == pack["id"])
                .values(asset_id="0" * 32)
            )
            await session.commit()

    asyncio.run(orphan())

    members = _expand([pack["id"]], owner["id"])[pack["id"]]
    assert len(members) == 1, "孤儿成员不能从列表里消失"
    assert members[0].id == "0" * 32
    assert members[0].readable is False
    assert members[0].position == 1


def test_expand_packs_ignores_non_pack_ids(client: TestClient, api: str) -> None:
    """拿一张图片的 id 进来，不该被当成「空封装」展开。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    image = upload_image(client, api, owner_h, name="不是包.png", color="orchid")

    assert _expand([image["id"]], owner["id"]) == {}


def test_expand_packs_returns_empty_list_for_a_pack_without_members(
    client: TestClient, api: str
) -> None:
    """可读但没有成员的封装：键在、值为空列表。空封装是合法状态（草稿阶段）。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    image = upload_image(client, api, owner_h, name="临时.png", color="orchid")
    pack = _create_pack(
        client, api, owner_h, {"name": "空包", "items": [{"assetId": image["id"]}]}
    )

    async def clear() -> None:
        from sqlalchemy import delete

        from db.session import get_session_factory
        from modules.library.models import LibraryPackItem

        async with get_session_factory()() as session:
            await session.execute(
                delete(LibraryPackItem).where(LibraryPackItem.pack_id == pack["id"])
            )
            await session.commit()

    asyncio.run(clear())
    assert _expand([pack["id"]], owner["id"]) == {pack["id"]: []}


def test_find_ids_only_returns_active_readable_assets(
    client: TestClient, api: str
) -> None:
    """`state=active` 是硬过滤，别人的私有不出现，`kind` 生效。

    这一条守的是「演示数据与导入器不会指向用户已经删掉 / 无权访问的东西」。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    stranger, _stranger_h = make_user(client, admin, LIB_RW)

    active = upload_image(client, api, owner_h, name="在库.png", color="orchid")
    draft = insert_asset(
        name="草稿.png", kind="image", owner_id=owner["id"], state="draft"
    )
    trashed = insert_asset(
        name="回收站.png", kind="image", owner_id=owner["id"], state="trash"
    )
    foreign = upload_image(client, api, _stranger_h, name="别人的.png", color="olive")

    found = _find(owner["id"], kind=LibraryAssetKind.IMAGE, limit=200)
    assert active["id"] in found
    assert draft not in found, "草稿不能进候选：调用方会把它塞进历史任务"
    assert trashed not in found, "回收站的不能进候选"
    assert foreign not in found, "别人的私有不出现"


def test_find_ids_fills_the_limit_even_with_unreadable_rows_ahead(
    client: TestClient, api: str
) -> None:
    """别人的私有资产排在前面时，仍要凑满 `limit` 条可读 id。

    这条是 CI 抓到的真实缺陷的回归：`find_ids` 的可读性只能逐行判（要查小组），
    没法压进一条 SQL，所以第一版写成了「取 limit 条候选 → 过滤」。库里别人先有
    20 张私有图时，候选全被过滤掉，调用方静默拿到一个偏短的列表——演示数据少
    一半，而且 `app seed` 照常 exit 0。

    「别人的私有不出现」和「凑得满」是两件事，必须同时成立。
    """
    admin = admin_headers(client)
    stranger, stranger_h = make_user(client, admin, LIB_RW)
    # 先让别人的私有资产占满时间序的前排
    foreign = [
        insert_asset(
            name=f"别人的私有 {index}.png",
            kind="image",
            owner_id=stranger["id"],
            state="active",
        )
        for index in range(20)
    ]
    owner, owner_h = make_user(client, admin, LIB_RW)
    # 颜色必须各不相同：内容寻址会去重，同色的三张图会落成同一个资产 id，
    # 那样「凑满 3 条」永远做不到（第一次写这条用例就踩了）。
    for color in ("orchid", "olive", "teal"):
        upload_image(client, api, owner_h, name=f"我的 {color}.png", color=color)

    found = _find(owner["id"], kind=LibraryAssetKind.IMAGE, limit=3)
    # 不写 `set(found) == set(mine)`：整场会话里别的用例可能留下了团队可见的图，
    # 那些也算「可读」。要守的是两条独立性质——凑得满、且别人的私有没混进来。
    assert len(found) == 3, f"没凑满：{found}"
    assert not (set(found) & set(foreign)), f"别人的私有资产混进来了：{found}"


def test_find_ids_expands_parent_tags(client: TestClient, api: str) -> None:
    """选中一级标签要连旗下二级一起命中（与 `/library/selection` 同口径）。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    image = upload_image(client, api, owner_h, name="带标签.png", color="orchid")

    parent = client.post(
        f"{api}/library/tags", json={"name": "产品图"}, headers=admin
    ).json()["data"]
    child = client.post(
        f"{api}/library/tags",
        json={"name": "三视图", "parentId": parent["id"]},
        headers=admin,
    ).json()["data"]
    # 换标签走 PATCH 资产（`tagIds` 整体替换），不是单独的 /tags 子资源
    response = client.patch(
        f"{api}/library/assets/{image['id']}",
        json={"tagIds": [child["id"]]},
        headers=owner_h,
    )
    assert response.status_code == 200, response.text

    by_parent = _find(owner["id"], tag_ids=[parent["id"]])
    by_child = _find(owner["id"], tag_ids=[child["id"]])
    assert image["id"] in by_parent, "选中一级没展开到二级"
    assert image["id"] in by_child
