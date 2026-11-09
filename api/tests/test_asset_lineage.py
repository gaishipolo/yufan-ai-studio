"""生成血缘与一键衍生（M3-C / M3-E）。

## 三条断言的核心

- **上传件的血缘是空**，而且"空"不是错误：它不是生成产物，界面上那一整段直接不显示
- **血缘是图不是树**：`parents` / `children` / `usedBy` 三段分开，谁在用它也要能查到
- **衍生只回填输入**：不建任务、不扣费、不产生素材——提交由生成页自己做

## 为什么"提取提示词"要单独一条 400

上传件没有配方。这时给 404 会让人以为素材没了，给 200 + 空串会让编辑器
悄悄变成空白。400 + 一句"这是上传件"才指向正确动作（去生成页写提示词）。
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import select
from tests_template_helpers import API, login

from db.session import get_session_factory
from modules.asset.models import Asset


def _headers(client: TestClient) -> dict[str, str]:
    return login(client)


async def _by_name(name: str) -> Asset:
    async with get_session_factory()() as session:
        row = await session.scalar(select(Asset).where(Asset.name == name))
        assert row is not None, f"演示数据里没有 {name}"
        return row


async def _a_candidate_id() -> str:
    async with get_session_factory()() as session:
        row = await session.scalar(
            select(Asset.id).where(Asset.stage == "candidate").order_by(Asset.created_at.desc())
        )
        assert row is not None, "演示数据里没有候选"
        return str(row)


def test_uploaded_asset_has_no_recipe(client: TestClient) -> None:
    """上传件：`recipe` 为 null，参考图为空，`usedBy` 里只有主体那一条。"""
    asset = asyncio.run(_by_name("城市航拍片段"))
    response = client.get(f"{API}/assets/{asset.id}/lineage", headers=_headers(client))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["recipe"] is None
    assert data["references"] == []
    assert data["parents"] == [] and data["children"] == []
    assert all(usage["kind"] != "asset" for usage in data["usedBy"])


def test_candidate_lineage_shows_recipe_and_references(client: TestClient) -> None:
    """生成件：配方全文、参考图（带名字与缩略图）都要能取到。"""
    candidate_id = asyncio.run(_a_candidate_id())
    data = client.get(f"{API}/assets/{candidate_id}/lineage", headers=_headers(client)).json()[
        "data"
    ]
    recipe = data["recipe"]
    assert recipe is not None
    assert recipe["prompt"], "提示词必须是全文，不能被截断"
    assert recipe["model"] and recipe["modelVersion"]
    assert recipe["referenceAssetIds"] == [ref["assetId"] for ref in data["references"]]
    assert data["references"][0]["role"] == "reference"
    assert data["references"][0]["name"], "参考图要带名字，否则浮层里只能显示一串 id"


def test_lineage_lists_who_uses_the_asset(client: TestClient) -> None:
    """「谁在用它」两种消费者都要在：生成引用了它、主体把它当成员。"""
    asset = asyncio.run(_by_name("人物参考半身像"))
    data = client.get(f"{API}/assets/{asset.id}/lineage", headers=_headers(client)).json()["data"]
    kinds = {usage["kind"] for usage in data["usedBy"]}
    assert "asset" in kinds, "演示候选的配方引用了这张图，血缘里应当体现"
    assert "subject" in kinds, "它同时是示例主体的成员"


def test_derive_prompt_extract_requires_a_recipe(client: TestClient) -> None:
    """上传件没有配方：400 而不是 404（素材在，只是没那句话）。

    刻意不用示例主体里的成员：那种素材会先被"未开放衍生"挡在 403，
    于是这条用例验的就不是配方缺失了。
    """
    asset = asyncio.run(_by_name("城市航拍片段"))
    response = client.post(
        f"{API}/assets/{asset.id}/derive", json={"mode": "prompt_extract"}, headers=_headers(client)
    )
    assert response.status_code == 400, response.text
    assert response.json()["code"] == 40052
    assert "上传件" in response.json()["message"]


def test_derive_prompt_extract_returns_the_full_prompt(client: TestClient) -> None:
    candidate_id = asyncio.run(_a_candidate_id())
    data = client.post(
        f"{API}/assets/{candidate_id}/derive",
        json={"mode": "prompt_extract"},
        headers=_headers(client),
    ).json()["data"]
    assert data["mode"] == "prompt_extract"
    assert data["prompt"]
    assert data["sourceRecipeId"]


def test_derive_image_ref_fills_the_reference_slot(client: TestClient) -> None:
    """图生图参考：回填这张图与它的 role，**不产生素材、不扣费**。"""
    asset = asyncio.run(_by_name("材质贴图-金属"))
    data = client.post(
        f"{API}/assets/{asset.id}/derive", json={"mode": "image_ref"}, headers=_headers(client)
    ).json()["data"]
    assert data["referenceAssetIds"] == [asset.id]
    assert data["referenceRole"] == "reference"

    # 素材数没变：衍生只回填输入
    listing = client.get(f"{API}/assets", headers=_headers(client)).json()["data"]
    assert asset.id in {item["id"] for item in listing["items"]}


def test_derive_video_first_frame_requires_an_image(client: TestClient) -> None:
    """首帧要图片位。视频不该在这里被隐式抽帧——那会产生新素材，是另一条写路径。"""
    asset = asyncio.run(_by_name("霓虹街道素材"))
    response = client.post(
        f"{API}/assets/{asset.id}/derive",
        json={"mode": "video_first_frame"},
        headers=_headers(client),
    )
    assert response.status_code == 400, response.text
    assert "图片位" in response.json()["message"]


def test_derive_is_blocked_by_the_subject_grant(client: TestClient) -> None:
    """成员授权里没给 `derive` → 403。承诺是逐成员的，界面据此降级为"仅预览"。"""
    asset = asyncio.run(_by_name("人物参考半身像"))
    response = client.post(
        f"{API}/assets/{asset.id}/derive", json={"mode": "image_ref"}, headers=_headers(client)
    )
    assert response.status_code == 403, response.text


def test_recipes_have_no_write_routes(client: TestClient) -> None:
    """配方只增不改：路由表里不许出现 recipe 相关的写接口。

    允许编辑就等于允许事后修改"当时用了什么模型、写了什么提示词"，
    那它连审计价值都没有了。修正是补录新配方，不是改旧的。
    """
    paths = {getattr(route, "path", "") for route in client.app.routes}
    assert not [path for path in paths if "recipe" in path.lower()]
