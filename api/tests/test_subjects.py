"""主体：可用性三态、发布闸门、版本冻结、以及三条「不许越界」的铁律。

## 为什么这一组用例的断言都落在"状态"而不是"页面"

主体做错的典型症状不是报错，而是**卡片说能发、点了不能发**，或者**发布了但模板
还是老图**。两者都只在状态层面能看见：可用性是服务端算的、版本号是数据库保证的。
所以这里不去数 DOM，而是直接问接口"这个主体现在处于哪一态"。

## 三条铁律各有一条用例

- 删主体不删成员（铁律①）：成员是**证据**，主体下架只是不再被调用
- 同一素材可以同时在两个主体里：唯一约束只管**组内**
- 版本冻结之后改成员，历史版本不许改口：否则"上周那张图"再也无法复现
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from tests_template_helpers import ADMIN, API, login

from db.session import get_session_factory
from modules.asset.models import Asset
from modules.asset.subject_models import Subject, SubjectLegacyPack, SubjectMember, SubjectVersion

SEEDED_READY = "示例主体 · 人物替换"
SEEDED_DRAFT = "示例主体 · 产品细节待补"
EDITOR = ("editor", "editor@12345")

ScratchFactory = Callable[..., str]


def _headers(client: TestClient) -> dict[str, str]:
    return login(client)


# --------------------------------------------------------------------------- #
# 现场构造
# --------------------------------------------------------------------------- #
async def _personal_asset_ids(limit: int) -> list[str]:
    """演示账号**个人库**里的**已采纳**素材。团队库里的素材不属于他的私人主体。

    `stage == "adopted"` 这一条不能省：候选也是 `assets` 行（`origin=generate`、
    合规 `pending`），把它们挑进来会让主体发布被"合规未通过"拦下——
    而失败信息指向的是合规，不是"这个测试选错了素材"。
    """
    async with get_session_factory()() as session:
        rows = await session.scalars(
            select(Asset.id)
            .where(
                Asset.owner_id == ADMIN,
                Asset.visibility == "personal",
                Asset.deleted_at.is_(None),
                Asset.stage == "adopted",
            )
            .order_by(Asset.created_at)
            .limit(limit)
        )
        return list(rows.all())


async def _create_subject(
    *,
    name: str,
    grants: list[list[str]],
    requires: list[str],
    roles: list[str],
    asset_ids: list[str] | None = None,
) -> str:
    if asset_ids is None:
        asset_ids = await _personal_asset_ids(len(grants))
    assert len(asset_ids) == len(grants), "个人库里的演示素材不够，现场构造失败"
    async with get_session_factory()() as session:
        subject = Subject(
            subject_type="product",
            name=name,
            owner_id=ADMIN,
            requires=list(requires),
            required_roles=list(roles),
            status="draft",
            revision=1,
        )
        session.add(subject)
        await session.flush()
        for index, (asset_id, granted) in enumerate(zip(asset_ids, grants, strict=True)):
            session.add(
                SubjectMember(
                    subject_id=subject.id,
                    asset_id=asset_id,
                    # 角色按声明顺序铺开：`required_roles` 要能靠成员满足，
                    # 全填 front 的话「缺角色」会变成一条无法消除的假红
                    role=roles[min(index, len(roles) - 1)],
                    sort_order=index,
                    grants=list(granted),
                    in_output=True,
                    added_by=ADMIN,
                )
            )
        await session.commit()
        return subject.id


async def _purge(subject_ids: list[str]) -> None:
    if not subject_ids:
        return
    async with get_session_factory()() as session:
        for model in (SubjectMember, SubjectVersion, SubjectLegacyPack):
            column = model.__table__.c["subject_id"]
            await session.execute(delete(model).where(column.in_(subject_ids)))
        # `subjects` 的主键叫 `id`，不是 `subject_id`（它自己就是主体）
        await session.execute(delete(Subject).where(Subject.id.in_(subject_ids)))
        await session.commit()


@pytest.fixture
def scratch() -> Iterator[ScratchFactory]:
    """临时主体工厂：用例结束把它们连成员、版本一起清掉。

    不清的话「列表里只有 2 个主体」这类断言会随执行顺序变红——而这类红最容易被
    当成"功能坏了"，实际是测试污染。
    """
    created: list[str] = []

    def factory(
        *,
        name: str,
        grants: list[list[str]],
        requires: list[str] | None = None,
        roles: list[str] | None = None,
        asset_ids: list[str] | None = None,
    ) -> str:
        subject_id = asyncio.run(
            _create_subject(
                name=name,
                grants=grants,
                requires=["generate", "download"] if requires is None else requires,
                roles=["front"] if roles is None else roles,
                asset_ids=asset_ids,
            )
        )
        created.append(subject_id)
        return subject_id

    yield factory
    asyncio.run(_purge(created))


# --------------------------------------------------------------------------- #
# 可用性三态
# --------------------------------------------------------------------------- #
def test_seed_ships_one_ready_and_one_incomplete_subject(client: TestClient) -> None:
    """演示数据必须同时有 `ready` 与 `incomplete`。

    只有一种状态的话，可用性角标在演示环境里永远是同一个颜色，而"缺什么"正是
    这一批要验收的东西。`incomplete` 那条还必须**缺具体一项**（download），
    否则说明不了"角标上的字是从哪来的"。
    """
    body = client.get(f"{API}/subjects", headers=_headers(client)).json()["data"]
    by_name = {item["name"]: item for item in body["items"]}
    assert SEEDED_READY in by_name and SEEDED_DRAFT in by_name, by_name.keys()

    ready = by_name[SEEDED_READY]
    assert ready["availability"] == "ready", ready
    assert ready["availabilityNote"] == ""
    assert ready["memberCount"] == 2
    assert ready["currentVersion"] == 1

    draft = by_name[SEEDED_DRAFT]
    assert draft["availability"] == "incomplete", draft
    assert "download" in draft["availabilityNote"]


def test_detail_explains_which_member_is_missing_what(client: TestClient) -> None:
    """主体级结论不够，要能点到**是哪一张**不给能力。

    只给"缺少 download"的话，用户得自己一张张点开看——而主体最多 10 张，
    没人会真的去数。
    """
    listing = client.get(f"{API}/subjects", headers=_headers(client)).json()["data"]["items"]
    draft = next(item for item in listing if item["name"] == SEEDED_DRAFT)

    detail = client.get(f"{API}/subjects/{draft['id']}", headers=_headers(client))
    assert detail.status_code == 200, detail.text
    body = detail.json()["data"]
    assert body["missingCapabilities"] == ["download"]
    assert body["missingRoles"] == ["detail"]
    assert len(body["members"]) == 1
    member = body["members"][0]
    assert member["grants"] == ["generate"]
    assert body["memberGaps"] == {member["assetId"]: ["download"]}
    assert member["missing"] is False


def test_list_does_not_carry_member_details(client: TestClient) -> None:
    """列表项不塞成员明细：24 × 10 条素材换来的信息几乎不会被看。"""
    items = client.get(f"{API}/subjects", headers=_headers(client)).json()["data"]["items"]
    for item in items:
        assert "members" not in item
        assert "memberGaps" not in item


# --------------------------------------------------------------------------- #
# 发布闸门
# --------------------------------------------------------------------------- #
def test_publish_rejects_a_subject_that_is_missing_a_capability(
    client: TestClient, scratch: ScratchFactory
) -> None:
    """能力不可达是 400，并且**不落任何版本**。

    返回 200 + 空结果会让"发布了但模板还是老图"变成靠猜的问题：模板按
    `current_version` 取图，版本没涨就永远读老版本。
    """
    subject_id = scratch(name="发布闸门 · 缺能力", grants=[["generate"]])

    response = client.post(f"{API}/subjects/{subject_id}/publish", headers=_headers(client))
    assert response.status_code == 400, response.text
    assert "download" in response.json()["message"]

    detail = client.get(f"{API}/subjects/{subject_id}", headers=_headers(client)).json()["data"]
    assert detail["status"] == "draft"
    assert detail["currentVersion"] == 0


def test_publish_freezes_a_new_version_each_time(
    client: TestClient, scratch: ScratchFactory
) -> None:
    """每次发布 +1 版本，版本行**只增不改**。

    `current_version` 是模板缓存的失效点，`revision` 是保存的乐观锁——混用会让
    "保存了但没发布"把版本号推上去，模板于是读到一份没有发布过的快照。

    用临时主体而不是演示主体：演示主体被这条用例推到 v3 之后，
    「seed 出来的版本是 1」那条断言就会随执行顺序变红。
    """
    subject_id = scratch(name="发布两次", grants=[["generate", "download"]])

    first = client.post(f"{API}/subjects/{subject_id}/publish", headers=_headers(client))
    assert first.status_code == 200, first.text
    assert first.json()["data"]["version"] == 1

    second = client.post(f"{API}/subjects/{subject_id}/publish", headers=_headers(client))
    assert second.status_code == 200, second.text
    assert second.json()["data"]["version"] == 2

    async def versions() -> list[int]:
        async with get_session_factory()() as session:
            rows = await session.scalars(
                select(SubjectVersion.version)
                .where(SubjectVersion.subject_id == subject_id)
                .order_by(SubjectVersion.version)
            )
            return list(rows.all())

    assert asyncio.run(versions()) == [1, 2]


def test_publish_refuses_a_subject_whose_member_image_was_deleted(client: TestClient) -> None:
    """成员被软删 → 409（不是 400）。

    请求本身没毛病，是**当前状态**不允许。400 会让客户端以为参数写错了，
    于是去检查 payload；而真正要做的是去回收站把图恢复出来。
    """
    listing = client.get(f"{API}/subjects", headers=_headers(client)).json()["data"]["items"]
    ready = next(item for item in listing if item["name"] == SEEDED_READY)
    detail = client.get(f"{API}/subjects/{ready['id']}", headers=_headers(client)).json()["data"]
    asset_id = detail["members"][0]["assetId"]

    async def set_deleted(value: datetime | None) -> None:
        async with get_session_factory()() as session:
            row = await session.get(Asset, asset_id)
            assert row is not None
            row.deleted_at = value
            await session.commit()

    asyncio.run(set_deleted(datetime.now(UTC)))
    try:
        response = client.post(f"{API}/subjects/{ready['id']}/publish", headers=_headers(client))
        assert response.status_code == 409, response.text
        assert "已被删除" in response.json()["message"]

        broken = client.get(
            f"{API}/subjects/{ready['id']}", headers=_headers(client)
        ).json()["data"]
        assert broken["availability"] == "broken"
        assert broken["members"][0]["missing"] is True
        # 成员行**不许**被静默删掉：主体要能显示"这里缺一张图"
        assert len(broken["members"]) == 2
    finally:
        asyncio.run(set_deleted(None))


def test_cannot_publish_someone_elses_subject(client: TestClient) -> None:
    """别人的主体是 403，与"不存在"分开：能区分就等于泄露了这个 id 存在。"""
    listing = client.get(f"{API}/subjects", headers=_headers(client)).json()["data"]["items"]
    ready = next(item for item in listing if item["name"] == SEEDED_READY)

    response = client.post(
        f"{API}/subjects/{ready['id']}/publish", headers=login(client, *EDITOR)
    )
    assert response.status_code == 403, response.text


def test_subject_list_hides_other_peoples_subjects(client: TestClient) -> None:
    """没有 `asset:team:manage` 的人只看自己的主体。"""
    body = client.get(f"{API}/subjects", headers=login(client, *EDITOR)).json()["data"]
    assert body["items"] == []
    assert body["total"] == 0


# --------------------------------------------------------------------------- #
# 铁律
# --------------------------------------------------------------------------- #
def test_archiving_a_subject_leaves_its_members_alone(client: TestClient) -> None:
    """铁律①：删主体不删成员。

    成员是"当时用了哪几张图"的证据。级联删除之后，历史工单里那句
    "用的是这张封面"就再也对不上了。
    """
    listing = client.get(f"{API}/subjects", headers=_headers(client)).json()["data"]["items"]
    ready = next(item for item in listing if item["name"] == SEEDED_READY)

    async def set_archived(value: datetime | None) -> tuple[int, int]:
        async with get_session_factory()() as session:
            subject = await session.get(Subject, ready["id"])
            assert subject is not None
            subject.archived_at = value
            await session.commit()
            members = await session.scalars(
                select(SubjectMember.id).where(SubjectMember.subject_id == ready["id"])
            )
            versions = await session.scalars(
                select(SubjectVersion.id).where(SubjectVersion.subject_id == ready["id"])
            )
            return len(members.all()), len(versions.all())

    member_count, version_count = asyncio.run(set_archived(datetime.now(UTC)))
    try:
        assert member_count == ready["memberCount"]
        assert version_count >= 1
        # 下架之后详情不给（404），但行还在
        response = client.get(f"{API}/subjects/{ready['id']}", headers=_headers(client))
        assert response.status_code == 404, response.text
    finally:
        asyncio.run(set_archived(None))


def test_one_asset_can_sit_in_two_subjects(client: TestClient, scratch: ScratchFactory) -> None:
    """同一张图可以同时是两个主体的成员。

    唯一约束是 `(subject_id, asset_id)`——只管组内。按 `asset_id` 全局唯一的话，
    "同一张主图被产品与场景两个主体共用"这个再普通不过的用法会被拒，
    而拒绝的理由（唯一约束）在界面上只会显示成"保存失败"。
    """
    asset_ids = asyncio.run(_personal_asset_ids(1))
    first = scratch(name="共用素材 · A", grants=[["generate", "download"]], asset_ids=asset_ids)
    second = scratch(name="共用素材 · B", grants=[["generate", "download"]], asset_ids=asset_ids)
    assert first != second

    async def pairs() -> list[tuple[str, str]]:
        async with get_session_factory()() as session:
            rows = await session.execute(
                select(SubjectMember.subject_id, SubjectMember.asset_id).where(
                    SubjectMember.subject_id.in_([first, second])
                )
            )
            return [(str(sid), str(aid)) for sid, aid in rows.all()]

    assert sorted(asyncio.run(pairs())) == sorted(
        [(first, asset_ids[0]), (second, asset_ids[0])]
    )


def test_published_version_does_not_follow_later_member_edits(
    client: TestClient, scratch: ScratchFactory
) -> None:
    """版本冻结之后改成员的能力，历史版本的 JSON 一个字都不许变。

    这就是"上周那张图"能被复现的唯一原因。指向活表的话，改一次成员就让
    历史版本改了口。
    """
    subject_id = scratch(
        name="冻结后改成员",
        grants=[["generate", "download"], ["generate", "download"]],
        roles=["front", "scene"],
    )
    published = client.post(f"{API}/subjects/{subject_id}/publish", headers=_headers(client))
    assert published.status_code == 200, published.text

    async def latest_snapshot() -> list[dict[str, object]]:
        async with get_session_factory()() as session:
            row = await session.scalar(
                select(SubjectVersion)
                .where(SubjectVersion.subject_id == subject_id)
                .order_by(SubjectVersion.version.desc())
            )
            assert row is not None
            return list(row.members)

    before = asyncio.run(latest_snapshot())
    assert {str(item["role"]) for item in before} == {"front", "scene"}

    async def set_grants(grants: list[str], role: str) -> None:
        async with get_session_factory()() as session:
            members = await session.scalars(
                select(SubjectMember)
                .where(SubjectMember.subject_id == subject_id)
                .order_by(SubjectMember.sort_order)
            )
            for member in members.all():
                member.grants = list(grants)
                member.role = role
            await session.commit()

    asyncio.run(set_grants(["derive"], "changed"))
    assert asyncio.run(latest_snapshot()) == before
    # 断言"改成功了"：否则上面那条相等可能只是因为根本没改到
    detail = client.get(f"{API}/subjects/{subject_id}", headers=_headers(client)).json()["data"]
    assert {member["role"] for member in detail["members"]} == {"changed"}
    assert detail["availability"] == "incomplete", "成员被改成只有 derive，能力不该还可达"


# --------------------------------------------------------------------------- #
# B5：旧 packId 解析
# --------------------------------------------------------------------------- #
def test_legacy_pack_resolves_to_a_subject_and_unknown_ids_are_404(client: TestClient) -> None:
    """旧 id 能解析到主体；解析不到是 404 而不是空主体。

    空主体会渲染成一张"什么都是空的"卡片，看起来像数据坏了；404 至少说清了
    "这个 id 没有对应主体"。
    """
    listing = client.get(f"{API}/subjects", headers=_headers(client)).json()["data"]["items"]
    ready = next(item for item in listing if item["name"] == SEEDED_READY)

    async def link(link_id: str) -> None:
        async with get_session_factory()() as session:
            session.add(SubjectLegacyPack(pack_id="pkg_legacy_probe", subject_id=link_id))
            await session.commit()

    asyncio.run(link(ready["id"]))
    try:
        response = client.get(
            f"{API}/subjects/by-legacy-pack/pkg_legacy_probe", headers=_headers(client)
        )
        assert response.status_code == 200, response.text
        body = response.json()["data"]
        # 路由顺序的探针：`/by-legacy-pack/x` 不能被 `/{subject_id}` 吃掉
        assert body["id"] == ready["id"]
        assert body["name"] == SEEDED_READY
        assert len(body["members"]) == 2
    finally:

        async def unlink() -> None:
            async with get_session_factory()() as session:
                await session.execute(
                    delete(SubjectLegacyPack).where(SubjectLegacyPack.pack_id == "pkg_legacy_probe")
                )
                await session.commit()

        asyncio.run(unlink())

    missing = client.get(f"{API}/subjects/by-legacy-pack/no-such-pack", headers=_headers(client))
    assert missing.status_code == 404, missing.text
