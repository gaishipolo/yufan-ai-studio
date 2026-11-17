"""老草稿里的封装字段：读得回来，模板列表不 500。

素材封装（`kind=pack`）下线后，槽位契约里没有 `packId` 了。而入参模型是
`extra="forbid"`：老草稿里的这个键会被判成「未知键」，那份草稿**一打开就
422**，整页模板列表跟着 500 —— 模板都还在，只是没人能读它。

所以契约层要有读时兜底：把旧键丢掉。与老 `tagId` → `tagIds` 是同一个形状。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi.testclient import TestClient
from library_support import LIB_RW, admin_headers, make_user, suffix, upload_image

API = "/api/v1/template-specs"
PERMS = [
    "template_pack:read",
    "template_pack:create",
    "template_pack:publish",
    "catalog:read",
    *LIB_RW,
]
MODEL = "libtv-wan3-replacement"


def _headers(client: TestClient) -> dict[str, str]:
    admin = admin_headers(client)
    _, headers = make_user(client, admin, PERMS)
    return headers


def _draft(asset_id: str) -> dict[str, Any]:
    """一个固定图片槽位的模板（固定槽位不需要标签，发布闸才过得去）。"""
    return {
        "key": f"legacy-pack-{suffix()}",
        "name": "老封装字段",
        "model": MODEL,
        "params": {"durationSec": 6, "aspectRatio": "9:16"},
        "prompt": "一只猫在窗台上打哈欠",
        "slots": [
            {
                "key": "hero",
                "type": "image",
                "binding": "fixed",
                "assetId": asset_id,
                "minCount": 1,
                "maxCount": 1,
                "position": 0,
            }
        ],
    }


def _store_legacy_draft(spec_key: str) -> None:
    """把草稿 JSON 改成封装下线前的样子：槽位里还带着 `packId`。

    直接改库而不是走接口：接口现在会把旧键丢掉（那正是要验的读时兜底），
    走接口就存不进带 `packId` 的草稿了。
    """

    async def run() -> None:
        from sqlalchemy import select

        from db.session import get_session_factory
        from modules.template_pack.models import TemplateSpec

        async with get_session_factory()() as session:
            row = await session.scalar(
                select(TemplateSpec).where(TemplateSpec.spec_key == spec_key)
            )
            assert row is not None
            draft = dict(row.draft or {})
            slots = [dict(slot) for slot in draft.get("slots", [])]
            for slot in slots:
                slot["packId"] = None
            draft["slots"] = slots
            row.draft = draft
            await session.commit()

    asyncio.run(run())


def test_legacy_pack_field_does_not_break_the_template_list(
    client: TestClient, api: str
) -> None:
    """老草稿带着 `packId` 也要能读：列表不能 500，模板不能消失。"""
    headers = _headers(client)
    asset = upload_image(client, api, headers, name=f"legacy-{suffix()}.png")
    draft = _draft(asset["id"])

    saved = client.post(API, json=draft, headers=headers)
    assert saved.status_code == 200, saved.text
    published = client.post(f"{API}/{draft['key']}/publish", headers=headers)
    assert published.status_code == 200, published.text

    # 模拟「这份草稿是素材封装下线前存的」
    _store_legacy_draft(draft["key"])

    listed = client.get(f"{api}/template-specs/published", headers=headers)
    assert listed.status_code == 200, listed.text
    keys = [item["key"] for item in listed.json()["data"]["items"] or []]
    assert draft["key"] in keys

    detail = client.get(f"{api}/template-specs/{draft['key']}", headers=headers)
    assert detail.status_code == 200, detail.text
    slots = detail.json()["data"]["draft"]["slots"]
    assert [slot["key"] for slot in slots] == ["hero"]
