"""生图参考图：入参校验 → 落库 → 重建记录 → 内联成 Data URI。

Agnes 的 `submit()` 一直支持 `reference_urls`（`extra_body.image`），断的是**业务侧**：
`GenerateImageIn` 没有这个入参、`ImageJob` 没有这一列、`build_request` 不透传。
结果「图生图 / 多图融合」这类能力前端根本传不进来，模型能做的和产品能用的不是一回事。

这个文件钉住三件容易悄悄退化的事：

1. **受理前就拒**：张数超限 / 非图片 / 无权读 必须是 400/403，不能放进队列再异步失败；
2. **落库并重试可还原**：重试是从 `_to_record` 重建请求的，丢一列就静默变回纯文生图；
3. **字节真的进去了**：供应商拿不到我们私签的取件链接，只有 Data URI 一条路。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from core.context import CurrentUser
from core.exceptions import BadRequest, Forbidden
from db.session import get_session_factory
from infra import get_storage
from infra.ai.base import Capability, ModelSpec
from modules.image.models import (
    ImageJob,
    dumps_references,
    parse_references_json,
)
from modules.image.pipeline import ImageJobStore, _reference_data_uris
from modules.image.schema import ImageJobOut
from modules.image.service import _resolve_references
from tests.library_support import insert_asset, png_bytes, upload_image

API = "/api/v1"

ADMIN = CurrentUser(id="u_admin", name="admin", roles=("admin",), permissions=frozenset({"*"}))


def _login(client: TestClient) -> tuple[dict[str, str], CurrentUser]:
    resp = client.post(
        f"{API}/identity/login", json={"username": "admin", "password": "admin@12345"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    actor = CurrentUser(
        id=body["user"]["id"], name="admin", roles=("admin",), permissions=frozenset({"*"})
    )
    return {"Authorization": f"Bearer {body['accessToken']}"}, actor


def _spec(max_references: int = 4, model_id: str = "agnes-image-2.5-flash") -> ModelSpec:
    return ModelSpec(
        id=model_id,
        label="Agnes Image 2.5 Flash",
        capability=Capability.IMAGE,
        provider="agnes",
        max_references=max_references,
    )


def _job(**overrides: object) -> ImageJob:
    fields: dict[str, object] = {
        "id": "img-ref-1",
        "name": "参考图",
        "prompt": "把第一张图的角色放进第二张图的场景",
        "size": "1024x1024",
        "count": 1,
        "owner_id": "u_001",
        "status": "queued",
        "progress": 0,
        "model": "Agnes Image 2.5 Flash",
        "cover_url": "",
        "created_at": datetime(2026, 11, 11, tzinfo=UTC),
    }
    fields.update(overrides)
    return ImageJob(**fields)  # type: ignore[arg-type]


async def _resolve(asset_ids: list[str], spec: ModelSpec, actor: CurrentUser = ADMIN) -> list[dict]:
    async with get_session_factory()() as session:
        return await _resolve_references(session, actor, asset_ids, spec)


# --------------------------------------------------------------------------- 落库 / 契约


def test_reference_snapshot_survives_the_record_mapping() -> None:
    """重试走 `_to_record`：这里丢掉就等于重试时把参考图静默丢了。"""
    job = _job(
        reference_json=dumps_references(
            [
                {"assetId": "a-1", "storageKey": "media/aa/one.png", "mimeType": "image/png"},
                {"assetId": "a-2", "storageKey": "media/bb/two.jpg", "mimeType": "image/jpeg"},
            ]
        )
    )
    record = ImageJobStore._to_record(job)

    assert record.reference_urls == ("media/aa/one.png", "media/bb/two.jpg")
    # params 里不该出现参考图：那是模型参数面，键要按 paramSchema 校验
    assert "referenceLibraryIds" not in record.params


def test_out_schema_exposes_reference_ids() -> None:
    """历史卡片要能显示「用了 2 张参考图」，只回 id 不回字节。"""
    job = _job(
        reference_json=dumps_references(
            [{"assetId": "a-1", "storageKey": "k1", "mimeType": "image/png"}]
        )
    )
    assert ImageJobOut.model_validate(job).reference_library_ids == ["a-1"]


def test_broken_reference_json_is_treated_as_none() -> None:
    """历史脏行不能让列表接口 500（与 `parse_params_json` 同一条原则）。"""
    assert parse_references_json("") == []
    assert parse_references_json(None) == []
    assert parse_references_json("{not json") == []
    assert parse_references_json('{"assetId": "a"}') == []
    # 缺 assetId 的元素整条丢弃：留着它也拼不出可用的参考图
    assert parse_references_json('[{"storageKey": "k"}, {"assetId": "a"}]') == [
        {"assetId": "a", "storageKey": "", "mimeType": ""}
    ]
    assert dumps_references([]) == ""


# --------------------------------------------------------------------------- 读字节 → Data URI


async def test_reference_bytes_are_inlined_as_data_uri() -> None:
    """供应商只认公网 URL 与 Data URI；我们的取件链接是私签的，只能内联。"""
    import base64

    payload = png_bytes("orange")
    await get_storage().put("tests/ref-orange.png", payload, "image/png")

    uris = await _reference_data_uris(
        ImageJobStore._to_record(
            _job(
                reference_json=dumps_references(
                    [
                        {
                            "assetId": "a-1",
                            "storageKey": "tests/ref-orange.png",
                            "mimeType": "image/png",
                        }
                    ]
                )
            )
        )
    )

    assert len(uris) == 1
    prefix, _, encoded = uris[0].partition(",")
    assert prefix == "data:image/png;base64"
    assert base64.b64decode(encoded) == payload


async def test_unsupported_suffix_is_rejected_before_submit() -> None:
    """后缀不在白名单就抛：静默退回纯文生图会让用户拿到一张毫不相关的图。"""
    await get_storage().put("tests/ref-anim.gif", b"GIF89a", "image/gif")

    record = ImageJobStore._to_record(
        _job(
            reference_json=dumps_references(
                [{"assetId": "a-1", "storageKey": "tests/ref-anim.gif", "mimeType": "image/gif"}]
            )
        )
    )
    with pytest.raises(ValueError, match="格式不支持"):
        await _reference_data_uris(record)


async def test_no_references_means_no_conversion() -> None:
    assert await _reference_data_uris(ImageJobStore._to_record(_job())) == ()


# --------------------------------------------------------------------------- 受理前校验


def test_resolves_uploaded_asset_in_submit_order(client: TestClient) -> None:
    headers, actor = _login(client)
    first = upload_image(client, API, headers, name="first.png", color="red")
    second = upload_image(client, API, headers, name="second.png", color="blue")

    # 故意乱序传、并把第一张重复一次：顺序要保留，重复要去掉
    refs = asyncio.run(_resolve([second["id"], first["id"], second["id"]], _spec(), actor))

    assert [item["assetId"] for item in refs] == [second["id"], first["id"]]
    # 快照要带存储键（提交时靠它读字节），但契约里不暴露它，所以按后缀断言
    assert refs[0]["storageKey"].endswith(".png")
    assert refs[0]["mimeType"] == "image/png"


def test_text_to_image_model_rejects_references(client: TestClient) -> None:
    """`max_references=0` 的模型（如 echo 的 seedream-3）要当场 400。

    否则用户传了图、请求体内却没有 image 字段：图照样出，只是完全不像参考图，
    而界面上没有任何异常——比报错难查得多。
    """
    headers, actor = _login(client)
    asset = upload_image(client, API, headers)

    with pytest.raises(BadRequest, match="不支持参考图"):
        asyncio.run(_resolve([asset["id"]], _spec(max_references=0), actor))


def test_too_many_references_is_rejected(client: TestClient) -> None:
    headers, actor = _login(client)
    asset = upload_image(client, API, headers)

    with pytest.raises(BadRequest, match="最多接受 1 张"):
        asyncio.run(_resolve([asset["id"], asset["id"] + "-x"], _spec(max_references=1), actor))


async def test_unknown_asset_is_rejected() -> None:
    with pytest.raises(BadRequest, match="不存在"):
        await _resolve(["lib_missing"], _spec())


def test_non_image_asset_is_rejected() -> None:
    asset_id = insert_asset(
        owner_id="u_admin", kind="video", mime_type="video/mp4", storage_key="v/1.mp4"
    )

    with pytest.raises(BadRequest, match="不是图片"):
        asyncio.run(_resolve([asset_id], _spec()))


def test_other_users_private_asset_is_forbidden() -> None:
    """能读到别人的私有素材当参考图 = 让供应商复制一份再取回来，绕过整个共享审批。"""
    asset_id = insert_asset(
        owner_id="u_other",
        state="active",
        visibility="personal",
        mime_type="image/png",
        storage_key="p/secret.png",
    )

    with pytest.raises(Forbidden):
        asyncio.run(_resolve([asset_id], _spec()))


def test_team_visible_asset_is_allowed() -> None:
    """共享到团队的素材本来就人人可见，不该被误挡。"""
    asset_id = insert_asset(
        owner_id="u_other",
        state="active",
        visibility="team",
        mime_type="image/jpeg",
        storage_key="t/shared.jpg",
    )

    refs = asyncio.run(_resolve([asset_id], _spec()))

    assert refs == [{"assetId": asset_id, "storageKey": "t/shared.jpg", "mimeType": "image/jpeg"}]


async def test_empty_list_is_a_noop() -> None:
    assert await _resolve([], _spec()) == []
    assert await _resolve(["  "], _spec()) == []
