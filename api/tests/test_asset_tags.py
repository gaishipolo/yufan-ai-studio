"""受控标签与保存视图（M2-A）。

四组用例，各自失败的症状完全不同：

- **词表坏了**：筛选条空着或出现两个同名分组，用户点哪个都像点错；
- **AND 语义写成 OR**：结果比用户以为的更宽，而界面上看不出少了一个条件；
- **未知 / 已合并的 id 处理错了**：保存视图恢复后变成空结果，用户以为筛选没了；
- **视图存了结果而不是参数**：素材增减后视图给出过期列表，而没人知道它是快照。

最后一条最难发现：它只在"过一段时间再看"时错，而那时已经没人记得当初的条件。
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from tests_template_helpers import API, login, png_bytes, upload

from db.session import get_session_factory
from modules.asset.tag_models import AssetTag, Tag
from modules.asset.tag_vocabulary import INITIAL_TAGS, TAG_TOTAL


def _headers(client: TestClient, username: str = "admin", password: str = "admin@12345"):
    return login(client, username, password)


def _tag_ids(client: TestClient, headers: dict[str, str], names: list[str]) -> list[str]:
    """按词名取 id。测试里按名字比按 id 可读，而 id 是随机的。"""
    body = client.get(f"{API}/asset-tags", headers=headers).json()["data"]
    lookup = {item["name"]: item["id"] for group in body["groups"] for item in group["items"]}
    missing = [name for name in names if name not in lookup]
    assert not missing, f"词表里没有 {missing}"
    return [lookup[name] for name in names]


def test_vocabulary_has_six_dimensions_and_46_words(client: TestClient) -> None:
    """词表定稿：6 个维度、46 个词，且分组标题是中文。

    数量写成断言而不是注释里的数字：改词表时这里会红，逼人回来确认
    「数量变了是有意的吗」。
    """
    headers = _headers(client)
    body = client.get(f"{API}/asset-tags", headers=headers).json()["data"]

    assert [group["dimension"] for group in body["groups"]] == [
        "style",
        "scene",
        "composition",
        "shot",
        "placement",
        "content",
    ]
    assert [group["label"] for group in body["groups"]] == [
        "风格",
        "场景",
        "主体构成",
        "镜头",
        "用途/版位",
        "内容属性",
    ]
    assert body["total"] == TAG_TOTAL == len(INITIAL_TAGS) == 46
    # 每个词都必须有自己的维度，不能出现「同名跨维度」
    all_names = [item["name"] for group in body["groups"] for item in group["items"]]
    assert len(all_names) == len(set(all_names)), "词表里出现了重名词"


def test_two_tags_mean_both_not_either(client: TestClient) -> None:
    """多个 `tagIds` 是**同时具备**（AND），不是任一（OR）。

    写成 OR 的结果比用户以为的更宽——这正是"筛选"最不该出的错：
    用户看到 40 张图，以为"极简留白 + 无人"有 40 张，实际两个条件各命中 20 张。
    """
    headers = _headers(client)
    a = upload(client, "and-a.png", "product", png_bytes())
    b = upload(client, "and-b.png", "product", png_bytes())
    c = upload(client, "and-c.png", "product", png_bytes())

    left, right = _tag_ids(client, headers, ["极简留白", "无人"])
    only_left = _tag_ids(client, headers, ["胶片颗粒"])[0]

    for asset_id, tag_ids in (
        (a["id"], [left]),
        (b["id"], [right]),
        (c["id"], [left, right]),
    ):
        response = client.put(
            f"{API}/assets/{asset_id}/tags", json={"tagIds": tag_ids}, headers=headers
        )
        assert response.status_code == 200, response.text

    def ids(*tag_ids: str) -> set[str]:
        params: list[tuple[str, str]] = [("keyword", "and-"), ("scope", "mine")]
        params += [("tagIds", tag_id) for tag_id in tag_ids]
        body = client.get(f"{API}/assets", params=params, headers=headers).json()["data"]
        return {item["id"] for item in body["items"]}

    assert ids(left) == {a["id"], c["id"]}
    assert ids(right) == {b["id"], c["id"]}
    # 交集：只有 c 同时具备两个词
    assert ids(left, right) == {c["id"]}
    # OR 会给出 {a,b,c}，而 AND 的答案是 {c}——两者差 3 倍
    assert ids(left, right) != ids(left) | ids(right)
    # 没被打过的词命中 0 条，不是"忽略该条件"
    assert ids(only_left) == set()


def test_comma_separated_tag_ids_equals_repeated_params(client: TestClient) -> None:
    """`?tagIds=a,b` 与 `?tagIds=a&tagIds=b` 等价。

    只认一种写法时，另一半调用方（手拼查询串的脚本、书签）会静默退化成
    「只筛第一个」——结果偏多，而请求本身是成功的。
    """
    headers = _headers(client)
    item = upload(client, "comma.png", "product", png_bytes())
    left, right = _tag_ids(client, headers, ["推拉", "环绕"])
    client.put(f"{API}/assets/{item['id']}/tags", json={"tagIds": [left]}, headers=headers)

    base = f"{API}/assets"
    repeated = client.get(
        base,
        params=[("keyword", "comma"), ("tagIds", left)],  # type: ignore[arg-type]
        headers=headers,
    ).json()["data"]
    comma = client.get(
        f"{base}?keyword=comma&tagIds={left},{right}", headers=headers
    ).json()["data"]

    assert [row["id"] for row in repeated["items"]] == [item["id"]]
    # 逗号写法带上一个没打过的词 → AND 语义下必然 0 条；若只认第一个参数就不会是 0
    assert comma["items"] == []


def test_unknown_tag_id_is_rejected_not_ignored(client: TestClient) -> None:
    """未知 id 必须 422，不能静默丢弃。

    静默丢弃让结果比用户以为的更宽，而界面上看不出少了一个条件——
    这类错误会被当成"筛选坏了"，查起来要翻请求日志。
    """
    headers = _headers(client)
    response = client.get(
        f"{API}/assets", params={"tagIds": "deadbeef" * 4}, headers=headers
    )
    assert response.status_code == 422, response.text
    # 码是 42251（asset slot 5 seq 1），断言码而不只断言状态码：
    # 422 在这个接口上还可能来自参数校验，两者对排查的意义完全不同
    assert response.json()["code"] == 42251


def test_retired_tag_leaves_the_catalog_but_still_filters(client: TestClient) -> None:
    """`retired` 的词不再出现在待选列表，但历史素材仍能按它筛出来。

    这是「不物理删除标签」的全部意义：归档改变的是**待选列表**，
    不是历史事实。若连筛选也失效，用户保存的视图会在某天静默变成空结果。
    """
    headers = _headers(client)
    item = upload(client, "retire.png", "product", png_bytes())
    (tag_id,) = _tag_ids(client, headers, ["需打码"])
    client.put(f"{API}/assets/{item['id']}/tags", json={"tagIds": [tag_id]}, headers=headers)

    async def retire() -> None:
        async with get_session_factory()() as session:
            row = await session.get(Tag, tag_id)
            assert row is not None
            row.status = "retired"
            await session.commit()

    asyncio.run(retire())
    try:
        body = client.get(f"{API}/asset-tags", headers=headers).json()["data"]
        names = [item["name"] for group in body["groups"] for item in group["items"]]
        assert "需打码" not in names

        listed = client.get(
            f"{API}/assets",
            params=[("keyword", "retire"), ("tagIds", tag_id)],  # type: ignore[arg-type]
            headers=headers,
        ).json()["data"]
        assert [row["id"] for row in listed["items"]] == [item["id"]]
    finally:
        asyncio.run(_activate(tag_id))


async def _activate(tag_id: str) -> None:
    """把词恢复成 active。用例之间共用数据库，留下一个归档词会让后来的用例莫名少词。"""
    async with get_session_factory()() as session:
        row = await session.get(Tag, tag_id)
        if row is not None:
            row.status = "active"
            await session.commit()


def test_merged_tag_redirects_saved_id(client: TestClient) -> None:
    """被合并掉的旧 id 一跳解析到新 id。

    保存视图里沉淀的是旧 id。直接删旧行会让那些视图变成空结果，
    而用户看到的是「我保存的筛选没了」，不会想到「标签合并过」。
    """
    headers = _headers(client)
    item = upload(client, "merge.png", "product", png_bytes())
    keep = _tag_ids(client, headers, ["贴片"])[0]
    client.put(f"{API}/assets/{item['id']}/tags", json={"tagIds": [keep]}, headers=headers)

    async def make_old() -> str:
        async with get_session_factory()() as session:
            old = Tag(
                dimension="placement",
                name="贴片（旧）",
                status="retired",
                source="manual",
                merged_into=keep,
            )
            session.add(old)
            await session.commit()
            return old.id

    old_id = asyncio.run(make_old())
    try:
        listed = client.get(
            f"{API}/assets",
            params=[("keyword", "merge"), ("tagIds", old_id)],  # type: ignore[arg-type]
            headers=headers,
        ).json()["data"]
        assert [row["id"] for row in listed["items"]] == [item["id"]]

        # 已合并的词不进待选列表
        body = client.get(f"{API}/asset-tags", headers=headers).json()["data"]
        names = [item["name"] for group in body["groups"] for item in group["items"]]
        assert "贴片（旧）" not in names
    finally:
        asyncio.run(_drop_tag(old_id))


async def _drop_tag(tag_id: str) -> None:
    async with get_session_factory()() as session:
        row = await session.get(Tag, tag_id)
        if row is not None:
            await session.delete(row)
            await session.commit()


def test_saved_view_stores_parameters_not_results(client: TestClient) -> None:
    """保存视图存参数：新增素材后，视图恢复出的结果会跟着变。

    存结果集的视图看起来"更稳定"，实际是给出了一个没人知道其存在时间点的快照。
    这条用例用一个新上传的素材证明它确实跟着变。
    """
    headers = _headers(client)
    first = upload(client, "view.png", "product", png_bytes())
    tag_id = _tag_ids(client, headers, ["开屏"])[0]
    client.put(f"{API}/assets/{first['id']}/tags", json={"tagIds": [tag_id]}, headers=headers)

    query = {"keyword": "view", "tagIds": [tag_id], "scope": "mine"}
    created = client.post(
        f"{API}/asset-views",
        json={"name": "开屏图", "query": query, "sort": "created_desc"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    view = created.json()["data"]
    assert view["query"] == query

    # 重名被拒（恢复时无法判断该用哪个）
    duplicate = client.post(
        f"{API}/asset-views",
        json={"name": "开屏图", "query": query},
        headers=headers,
    )
    assert duplicate.status_code == 409, duplicate.text

    # 用视图里的参数再查一次：拿到 1 条
    restored = client.get(
        f"{API}/assets",
        params=[("keyword", "view"), ("tagIds", tag_id)],  # type: ignore[arg-type]
        headers=headers,
    ).json()["data"]
    assert [row["id"] for row in restored["items"]] == [first["id"]]

    # 再传一张同样打上该词的图：同一个视图参数现在命中 2 条
    second = upload(client, "view-2.png", "product", png_bytes())
    client.put(f"{API}/assets/{second['id']}/tags", json={"tagIds": [tag_id]}, headers=headers)
    again = client.get(
        f"{API}/assets",
        params=[("keyword", "view"), ("tagIds", tag_id)],  # type: ignore[arg-type]
        headers=headers,
    ).json()["data"]
    assert {row["id"] for row in again["items"]} == {first["id"], second["id"]}

    deleted = client.delete(f"{API}/asset-views/{view['id']}", headers=headers)
    assert deleted.status_code == 204


def test_saved_views_are_private(client: TestClient) -> None:
    """视图是个人私有：别人看不到，也删不掉。

    共享视图要回答「谁改的」，那是一条独立的产品线，不在这期。
    """
    owner = _headers(client)
    created = client.post(
        f"{API}/asset-views",
        json={"name": "只有我能看", "query": {"keyword": "x"}},
        headers=owner,
    )
    assert created.status_code == 201, created.text
    view_id = created.json()["data"]["id"]

    other = _headers(client, "editor", "editor@12345")
    listed = client.get(f"{API}/asset-views", headers=other).json()["data"]["items"]
    assert view_id not in [item["id"] for item in listed]

    # 别人的视图：删除返回 404（不是 403——403 等于承认"这个 id 存在"）
    assert client.delete(f"{API}/asset-views/{view_id}", headers=other).status_code == 404
    client.delete(f"{API}/asset-views/{view_id}", headers=owner)


def test_only_tag_managers_can_add_words(client: TestClient) -> None:
    """建词要 `asset:tag:manage`；普通编辑只有 `asset:read`。

    读词表与建词是两个能力：一个词的错误会影响所有人的筛选，
    而"能上传素材"不代表"该决定全站用什么词"。
    """
    editor = _headers(client, "editor", "editor@12345")
    assert client.get(f"{API}/asset-tags", headers=editor).status_code == 200

    denied = client.post(
        f"{API}/asset-tags",
        json={"dimension": "style", "name": "高级感"},
        headers=editor,
    )
    assert denied.status_code == 403, denied.text

    admin = _headers(client)
    created = client.post(
        f"{API}/asset-tags",
        json={"dimension": "style", "name": "  粗颗粒  "},
        headers=admin,
    )
    assert created.status_code == 201, created.text
    data = created.json()["data"]
    # 首尾空白被收敛（否则「粗颗粒」与「 粗颗粒 」在界面上长得一样，却是两个词）
    assert data["name"] == "粗颗粒"
    assert data["useCount"] == 0
    assert data["pinned"] is False

    duplicate = client.post(
        f"{API}/asset-tags",
        json={"dimension": "shot", "name": "粗颗粒"},
        headers=admin,
    )
    assert duplicate.status_code == 409, duplicate.text

    bad_dimension = client.post(
        f"{API}/asset-tags",
        json={"dimension": "mood", "name": "高级感"},
        headers=admin,
    )
    assert bad_dimension.status_code == 422, bad_dimension.text


def test_usage_count_ignores_trashed_assets(client: TestClient) -> None:
    """在用数量只数未删除的素材。

    否则一个词可以靠一堆已删除的素材把自己顶进筛选条常显区，
    而用户点进去一条都看不到——「常显」的含义就没了。
    """
    headers = _headers(client)
    item = upload(client, "trash-tag.png", "product", png_bytes())
    tag_id = _tag_ids(client, headers, ["宠物"])[0]
    client.put(f"{API}/assets/{item['id']}/tags", json={"tagIds": [tag_id]}, headers=headers)

    def use_count() -> int:
        body = client.get(f"{API}/asset-tags", headers=headers).json()["data"]
        for group in body["groups"]:
            for tag in group["items"]:
                if tag["id"] == tag_id:
                    return tag["useCount"]
        raise AssertionError("词表里找不到刚用过的词")

    before = use_count()
    assert before >= 1

    client.put(
        f"{API}/assets/{item['id']}/deleted",
        json={"expectedVersion": item["stateVersion"], "deleted": True},
        headers=headers,
    )
    assert use_count() == before - 1
    # 关联行仍在：恢复后标签要回来
    async def links() -> int:
        from sqlalchemy import func, select

        async with get_session_factory()() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(AssetTag)
                    .where(AssetTag.asset_id == item["id"])
                )
                or 0
            )

    assert asyncio.run(links()) == 1


def test_tag_ids_are_rejected_when_unknown_on_write(client: TestClient) -> None:
    """写标签时同样拒绝未知 id，并且不部分生效。"""
    headers = _headers(client)
    item = upload(client, "bad-tag.png", "product", png_bytes())
    (good,) = _tag_ids(client, headers, ["中景"])
    response = client.put(
        f"{API}/assets/{item['id']}/tags",
        json={"tagIds": [good, "nope"]},
        headers=headers,
    )
    assert response.status_code == 422, response.text

    # 一个都没写进去：整体替换语义下，"部分成功"比失败更难解释
    async def count() -> int:
        from sqlalchemy import func, select

        async with get_session_factory()() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(AssetTag)
                    .where(AssetTag.asset_id == item["id"])
                )
                or 0
            )

    assert asyncio.run(count()) == 0


def test_saved_view_bad_json_does_not_break_the_list(client: TestClient) -> None:
    """库里存了坏 JSON 也不能让整个列表 500。

    这类行只会来自手工改库或迁移事故，但一次就能让"保存视图"整页打不开——
    而用户手里没有任何自助手段。
    """
    headers = _headers(client)
    created = client.post(
        f"{API}/asset-views",
        json={"name": "坏 JSON", "query": {"keyword": "y"}},
        headers=headers,
    )
    view_id = created.json()["data"]["id"]

    async def corrupt() -> None:
        async with get_session_factory()() as session:
            from modules.asset.tag_models import AssetView

            row = await session.get(AssetView, view_id)
            assert row is not None
            row.query_json = "{not json"
            await session.commit()

    asyncio.run(corrupt())
    body = client.get(f"{API}/asset-views", headers=headers)
    assert body.status_code == 200, body.text
    items = body.json()["data"]["items"]
    broken = next(item for item in items if item["id"] == view_id)
    assert broken["query"] == {}
    client.delete(f"{API}/asset-views/{view_id}", headers=headers)


def test_pinned_requires_five_uses(client: TestClient) -> None:
    """`pinned` 的门槛是 5 次，且门槛值由接口给出，前端不硬编码。

    门槛写在前端会出现"后端改了、筛选条没变"这种只在一边错误的漂移。
    """
    headers = _headers(client)
    body = client.get(f"{API}/asset-tags", headers=headers).json()["data"]
    assert body["pinnedThreshold"] == 5
    for group in body["groups"]:
        for tag in group["items"]:
            assert tag["pinned"] == (tag["useCount"] >= 5), tag


def test_query_json_keeps_chinese_readable(client: TestClient) -> None:
    """视图的 JSON 存原文中文，不转成 `\\uXXXX`。

    这份 JSON 会被人在排查「视图恢复了什么条件」时直接看库里的值。
    转义过的中文在那一步等于不可读。
    """
    headers = _headers(client)
    created = client.post(
        f"{API}/asset-views",
        json={"name": "中文条件", "query": {"keyword": "冰川", "tagIds": []}},
        headers=headers,
    )
    view_id = created.json()["data"]["id"]

    async def raw() -> str:
        async with get_session_factory()() as session:
            from modules.asset.tag_models import AssetView

            row = await session.get(AssetView, view_id)
            assert row is not None
            return row.query_json

    stored = asyncio.run(raw())
    assert "冰川" in stored and json.loads(stored)["keyword"] == "冰川"
    client.delete(f"{API}/asset-views/{view_id}", headers=headers)


@pytest.mark.parametrize("dimension", ["style", "shot"])
def test_new_word_starts_unpinned_even_in_a_hot_dimension(
    client: TestClient, dimension: str
) -> None:
    """新词默认不常显——**即使它所在维度已经有热词**。

    这条挂在维度上而不是全局：常显区按维度分组渲染，一个维度里出现
    「热词 + 一个零使用的新词」，用户会以为新词也很常用。
    """
    headers = _headers(client)
    name = f"测试词-{dimension}"
    created = client.post(
        f"{API}/asset-tags", json={"dimension": dimension, "name": name}, headers=headers
    )
    assert created.status_code == 201, created.text
    assert created.json()["data"]["pinned"] is False

    body = client.get(f"{API}/asset-tags", headers=headers).json()["data"]
    group = next(item for item in body["groups"] if item["dimension"] == dimension)
    names = [tag["name"] for tag in group["items"]]
    assert name in names
    # 零使用的词排在常显词之后
    position = names.index(name)
    pinned_flags = [tag["pinned"] for tag in group["items"]]
    assert all(flag is False for flag in pinned_flags[position:])
