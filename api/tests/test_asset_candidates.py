"""候选生命周期：三条「不许」+ 采纳 / 弃用 / 过期（M3-B / M3-E）。

## 为什么每条「不许」都要一条独立用例

候选做错的症状不是报错，而是**悄悄混进正常素材**：列表里多出几张不需要的图、
额度莫名少了几十兆、主体里出现一张还没确认的草稿。三者各走一条查询路径，
所以必须各有一条用例——只测其中一条的话，另外两条会以「另一个接口的数字
对不上」的形式在下一次改动里冒出来。

## 过期用可控时间而不是 `sleep`

`is_expired()` 收 `now`，测试直接造一条 8 天前的候选。用 `sleep` 测过期等于
把一套用例从 2 秒变成 7 天，而且跑不了 CI。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from tests_template_helpers import ADMIN, API, login

from db.session import get_session_factory
from modules.asset.archive_service import ArchiveAssetService
from modules.asset.candidate_service import CandidateService, GeneratedDraft, expire_candidates
from modules.asset.library_models import Category
from modules.asset.models import Asset
from modules.asset.recipe_service import RecipeDraft, create_recipe
from modules.asset.subject_models import Subject, SubjectMember


def _headers(client: TestClient) -> dict[str, str]:
    return login(client)


def _candidates(client: TestClient) -> dict:
    response = client.get(f"{API}/assets/candidates", headers=_headers(client))
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _flatten(client: TestClient) -> list[dict]:
    return [item for group in _candidates(client)["groups"] for item in group["items"]]


async def _make_candidate(*, age_days: float = 0.0, name: str = "测试候选") -> str:
    """造一条候选。走真实写路径（`create_recipe` + `register_generated`），
    不直接建行：三列不变量就是那条路径保证的。"""
    async with get_session_factory()() as session:
        recipe = await create_recipe(
            session, RecipeDraft(prompt="unit test prompt", created_by=ADMIN)
        )
        service = CandidateService(session, ArchiveAssetService(session))
        row = await service.register_generated(
            ADMIN,
            GeneratedDraft(
                name=name,
                kind="image",
                mime_type="image/png",
                width=64,
                height=64,
                size_bytes=1024,
                created_at=datetime.now(UTC) - timedelta(days=age_days),
            ),
            recipe,
        )
        await session.commit()
        return row.id


async def _inbox_id() -> str:
    async with get_session_factory()() as session:
        row = await session.scalar(
            select(Category.id).where(
                Category.scope == "personal",
                Category.owner_key == ADMIN,
                Category.is_inbox.is_(True),
            )
        )
        assert row is not None
        return str(row)


async def _stage(asset_id: str) -> str:
    async with get_session_factory()() as session:
        row = await session.get(Asset, asset_id)
        assert row is not None
        return row.stage


# --------------------------------------------------------------------------- #
# 三条「不许」
# --------------------------------------------------------------------------- #
def test_candidates_are_not_in_the_asset_list(client: TestClient) -> None:
    """候选不进 `GET /assets`。列表默认只认 `stage=adopted`。"""
    listing = client.get(f"{API}/assets", headers=_headers(client)).json()["data"]
    listed = {item["id"] for item in listing["items"]}
    candidate_ids = {item["id"] for item in _flatten(client)}
    assert candidate_ids, "演示数据应当有候选，否则这条用例证明不了任何事"
    assert listed.isdisjoint(candidate_ids)


def test_candidates_do_not_count_toward_quota(client: TestClient) -> None:
    """候选不占额度：一次 8 连拍会让额度看起来被吃掉 8 倍，而其中 7 张会被弃用。"""
    before = client.get(f"{API}/assets", headers=_headers(client)).json()["data"]["quota"][
        "personalUsed"
    ]
    asyncio.run(_make_candidate(name="额度不该被它影响"))
    after = client.get(f"{API}/assets", headers=_headers(client)).json()["data"]["quota"][
        "personalUsed"
    ]
    assert after == before


def test_a_candidate_cannot_be_published_as_a_subject_member(client: TestClient) -> None:
    """候选即使被塞进主体也发布不了——合规状态是 `pending`。

    这一条守的是「候选只能在采纳后进入主体」：现在没有"往主体加成员"的接口，
    所以从数据层构造违规状态，验证发布闸门确实拦得住。
    """
    candidate_id = asyncio.run(_make_candidate(name="还没确认就想发布"))

    async def build() -> str:
        async with get_session_factory()() as session:
            subject = Subject(
                subject_type="product",
                name="非法主体 · 成员是候选",
                owner_id=ADMIN,
                visibility="personal",
                cover_asset_id=candidate_id,
                requires=[],
                required_roles=[],
                status="draft",
            )
            session.add(subject)
            await session.flush()
            session.add(
                SubjectMember(
                    subject_id=subject.id,
                    asset_id=candidate_id,
                    role="front",
                    sort_order=0,
                    grants=["generate"],
                    in_output=True,
                    added_by=ADMIN,
                )
            )
            await session.commit()
            return subject.id

    subject_id = asyncio.run(build())
    response = client.post(f"{API}/subjects/{subject_id}/publish", headers=_headers(client))
    assert response.status_code == 400, response.text
    assert "合规" in response.json()["message"]


# --------------------------------------------------------------------------- #
# 确认区
# --------------------------------------------------------------------------- #
def test_candidates_are_grouped_by_recipe(client: TestClient) -> None:
    """同一批必须挨着：分组信息（哪批、什么时候过期）跟着素材一起回来。"""
    data = _candidates(client)
    assert data["total"] > 0
    assert all(group["recipeId"] for group in data["groups"]), "批次必须带配方 id"
    assert all(group["summary"] for group in data["groups"])
    for group in data["groups"]:
        assert len({item["recipeId"] for item in group["items"]}) == 1
        assert group["expiresAt"], "批次要能说清什么时候过期"


# --------------------------------------------------------------------------- #
# 采纳
# --------------------------------------------------------------------------- #
def test_adopt_moves_the_candidate_into_the_default_library(client: TestClient) -> None:
    """采纳：进列表、落收件箱、`stage=adopted`、带 `adoptedAt`。"""
    candidate = _flatten(client)[0]
    headers = _headers(client)
    response = client.post(f"{API}/assets/{candidate['id']}/adopt", json={}, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["stage"] == "adopted"
    assert body["adoptedAt"]
    # 没给分类 → 收件箱（采纳是高频低摩擦动作，逼用户先选分类会让待办越积越多）
    assert body["categoryId"] == asyncio.run(_inbox_id())

    listing = client.get(f"{API}/assets", headers=headers).json()["data"]
    assert candidate["id"] in {item["id"] for item in listing["items"]}
    assert candidate["id"] not in {item["id"] for item in _flatten(client)}


def test_adopting_twice_is_idempotent(client: TestClient) -> None:
    """重试 / 双击不该报冲突：它们打到的是同一行。"""
    candidate = _flatten(client)[0]
    headers = _headers(client)
    first = client.post(f"{API}/assets/{candidate['id']}/adopt", json={}, headers=headers)
    second = client.post(f"{API}/assets/{candidate['id']}/adopt", json={}, headers=headers)
    assert first.status_code == 200 and second.status_code == 200, second.text
    assert first.json()["data"]["adoptedAt"] == second.json()["data"]["adoptedAt"]


def test_adopted_candidate_is_tagged_with_the_request(client: TestClient) -> None:
    """采纳时可选贴标签：与 `PUT /tags` 同一个词表。"""
    candidate = _flatten(client)[0]
    headers = _headers(client)
    groups = client.get(f"{API}/asset-tags", headers=headers).json()["data"]["groups"]
    tag_id = groups[0]["items"][0]["id"]
    response = client.post(
        f"{API}/assets/{candidate['id']}/adopt",
        json={"tagIds": [tag_id]},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    detail = client.get(f"{API}/assets/{candidate['id']}", headers=headers).json()["data"]
    assert detail["stage"] == "adopted"
    tagged = client.get(
        f"{API}/assets", headers=headers, params={"tagIds": tag_id, "pageSize": 200}
    ).json()["data"]
    assert candidate["id"] in {item["id"] for item in tagged["items"]}


# --------------------------------------------------------------------------- #
# 弃用 / 过期
# --------------------------------------------------------------------------- #
def test_discarded_candidate_cannot_be_adopted(client: TestClient) -> None:
    """弃用是**用户的决定**，再采纳等于撤销它——必须显式（重新生成），不能靠重试穿透。"""
    candidate = _flatten(client)[0]
    headers = _headers(client)
    discarded = client.post(f"{API}/assets/{candidate['id']}/discard", headers=headers)
    assert discarded.status_code == 200, discarded.text
    assert discarded.json()["data"]["stage"] == "discarded"
    assert candidate["id"] not in {item["id"] for item in _flatten(client)}

    again = client.post(f"{API}/assets/{candidate['id']}/adopt", json={}, headers=headers)
    assert again.status_code == 409, again.text


def test_expired_candidate_is_refused_even_before_the_gc_runs(client: TestClient) -> None:
    """过期判定在**读路径**也算一次：等回收任务跑完才拒绝，用户会以为系统卡住。"""
    stale = asyncio.run(_make_candidate(age_days=30, name="放了 30 天的候选"))
    headers = _headers(client)
    # 它不出现在确认区（已过期），但 id 还在
    assert stale not in {item["id"] for item in _flatten(client)}
    response = client.post(f"{API}/assets/{stale}/adopt", json={}, headers=headers)
    assert response.status_code == 409, response.text


def test_gc_flips_stale_candidates_without_deleting_them(client: TestClient) -> None:
    """过期只翻状态：字节与配方都还在，`stage` 变成 `expired` 且**不复活**。"""
    stale = asyncio.run(_make_candidate(age_days=9, name="等回收任务处理的候选"))

    async def run_gc() -> int:
        async with get_session_factory()() as session:
            return await expire_candidates(session)

    assert asyncio.run(run_gc()) >= 1
    assert asyncio.run(_stage(stale)) == "expired"

    async def still_there() -> bool:
        async with get_session_factory()() as session:
            return await session.get(Asset, stale) is not None

    assert asyncio.run(still_there()), "过期不删行——配方要留着解释血缘"

    async def run_gc_again() -> int:
        async with get_session_factory()() as session:
            return await expire_candidates(session)

    assert asyncio.run(run_gc_again()) == 0, "幂等：已经 expired 的不再处理"


def test_batch_adopt_is_all_or_nothing(client: TestClient) -> None:
    """整组采纳：任一条不可用就整批拒绝，不给「成了 5 张、差 3 张」这种半成品状态。"""
    headers = _headers(client)
    items = _flatten(client)[:2]
    assert len(items) == 2, "需要至少两条候选来验证原子性"
    good, bad = items[0]["id"], items[1]["id"]
    client.post(f"{API}/assets/{bad}/discard", headers=headers)

    response = client.post(
        f"{API}/assets/batch/adopt", json={"assetIds": [good, bad]}, headers=headers
    )
    assert response.status_code == 409, response.text
    assert asyncio.run(_stage(good)) == "candidate", "整批失败时一条都不许被采纳"


def test_batch_adopt_returns_the_adopted_rows(client: TestClient) -> None:
    headers = _headers(client)
    ids = [item["id"] for item in _flatten(client)[:2]]
    assert len(ids) == 2
    response = client.post(f"{API}/assets/batch/adopt", json={"assetIds": ids}, headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert [item["id"] for item in data["items"]] == ids
    assert all(item["stage"] == "adopted" for item in data["items"])

    # 重试同一批：全部已采纳，返回 already=2 而不是报错
    retry = client.post(f"{API}/assets/batch/adopt", json={"assetIds": ids}, headers=headers)
    assert retry.status_code == 200, retry.text
    assert retry.json()["data"]["already"] == 2
