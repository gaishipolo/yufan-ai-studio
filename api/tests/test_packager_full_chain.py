"""多人、产品、场景封装到输入快照；仅准备，不提交生成。"""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from tests_template_helpers import (
    add_pack,
    drop_pack,
    drop_run,
    login,
    png_bytes,
    upload,
    video_bytes,
)

from db.session import get_session_factory
from modules.template.authorization import authorization_gap
from modules.template.repository import RunRepository
from modules.template_pack.models import (
    TemplatePack,
    TemplatePackReference,
)
from template_packager.client import PackagingClient
from template_packager.manifest import load_manifest
from template_packager.runner import apply_manifest


def test_three_flows_keep_original_tokens_and_pack_identity(client, tmp_path, monkeypatch):
    source = upload(client, "original-order.mp4", "source", video_bytes(2, audio=True))
    selected = {}
    for name, kind, roles in [
        ("A", "character", ["front", "front", "side"]),
        ("B", "character", ["front", "side"]),
        ("C", "character", ["front", "side"]),
        ("product", "product", ["detail", "front"]),
        ("scene", "scene", ["detail", "main", "angle"]),
    ]:
        images = [
            upload(client, f"{name}-{index}.png", kind, png_bytes())["id"]
            for index in range(len(roles))
        ]
        pack = asyncio.run(
            add_pack(
                kind=kind,
                name=name,
                reference_ids=images,
                roles=roles,
                required_roles=["main" if kind == "scene" else "front"],
                scopes=["held"] if kind == "product" else [],
                wardrobe_mode="reference" if kind == "character" else "preserve",
            )
        )
        selected[name] = (pack, images)
    data = {
        "key": "all-flows-v3",
        "name": "按原编号封装",
        "video": {"assetId": source["id"]},
        "packs": [
            {"key": key, "kind": key, "packId": selected["A" if key == "character" else key][0].id}
            for key in ("character", "product", "scene")
        ],
        "functions": [
            {
                "key": "character",
                "name": "三人换脸",
                "pack": "character",
                "prompt": (
                    "{{Mixed 1}}、{{Mixed 2}}、{{Mixed 3}} 分别替换 "
                    "{{Mixed 4}} 中三人。声音：为完整原片音轨。"
                ),
                "bindings": {
                    "{{Mixed 1}}": "front",
                    "{{Mixed 2}}": "front",
                    "{{Mixed 3}}": "front",
                    "{{Mixed 4}}": "source_video",
                },
            },
            {
                "key": "product",
                "name": "产品替换",
                "pack": "product",
                "prompt": "用 {{Mixed 1}} 的产品替换 {{Mixed 2}} 中的目标。",
                "bindings": {"{{Mixed 1}}": "front", "{{Mixed 2}}": "source_video"},
            },
            {
                "key": "scene",
                "name": "场景替换",
                "pack": "scene",
                "prompt": "用 {{Mixed 1}}、{{Mixed 2}} 的场景替换 {{Mixed 3}} 的背景。",
                "bindings": {
                    "{{Mixed 1}}": "main",
                    "{{Mixed 2}}": "angle",
                    "{{Mixed 3}}": "source_video",
                },
            },
        ],
    }
    manifest = tmp_path / "all.json"
    manifest.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    headers = login(client, "admin", "admin@12345")
    writes = []

    def forward(request):
        if request.method != "GET":
            writes.append(request.url.path)
        response = client.request(
            request.method, str(request.url), content=request.content, headers=dict(request.headers)
        )
        return httpx.Response(response.status_code, content=response.content)

    with PackagingClient(
        "http://testserver/api/v1",
        headers["Authorization"][7:],
        transport=httpx.MockTransport(forward),
    ) as api:
        loaded = load_manifest(manifest)
        report = apply_manifest(loaded, api, tmp_path / "state.json")
        before = list(writes)
        assert apply_manifest(loaded, api, tmp_path / "state.json") == report
        assert before == writes
    assert len(writes) == 3  # 只创建模板，不改包、不上传既有资产。
    result_by_kind = {item["key"]: item for item in report["functions"]}
    # 样例包 A 有两个 front，仍只有第一个属于人物1，人物2/3留待分别选包。
    media = result_by_kind["character"]["mediaOrder"]
    assert [item.get("assetId") for item in media[:4]] == [
        selected["A"][1][0],
        None,
        None,
        source["id"],
    ]

    # 发布版本保持原用途；修改 head 模拟专员正在编辑下一版，不能污染已选版本。
    async def edit_head():
        from sqlalchemy import select

        async with get_session_factory()() as session:
            rows = (
                await session.scalars(
                    select(TemplatePackReference).where(
                        TemplatePackReference.pack_id == selected["A"][0].id
                    )
                )
            ).all()
            for row in rows:
                row.role = "side"
            await session.commit()

    asyncio.run(edit_head())
    prepared = {}
    for function in data["functions"]:
        kind = function["key"]
        default = selected["C" if kind == "character" else kind][0]
        payload = {
            "requestId": str(uuid4()),
            "kind": kind,
            "sourceLibraryId": source["id"],
            "packId": default.id,
            "packVersion": 1,
            "videoTemplateId": result_by_kind[kind]["videoTemplateId"],
        }
        if kind == "character":
            payload.update(
                extraPackIds=[selected["A"][0].id, selected["B"][0].id], extraPackVersions=[1, 1]
            )
        response = client.post("/api/v1/templates/replacement-runs", json=payload, headers=headers)
        assert response.status_code == 201, response.text
        run = response.json()["data"]
        prepared[kind] = run
        assert run["prompt"] == function["prompt"]
        tokens = {item["token"]: item["id"] for item in run["inputSummary"]["assets"]}
        source_token = next(
            token for token, role in function["bindings"].items() if role == "source_video"
        )
        assert tokens[source_token] == source["id"]
        expected = (
            [selected[name][1][0] for name in ("C", "A", "B")]
            if kind == "character"
            else [selected[kind][1][1]]
        )
        for index, asset_id in enumerate(expected, 1):
            assert tokens[f"{{{{Mixed {index}}}}}"] == asset_id
        expected_all = {
            asset_id
            for name in (("C", "A", "B") if kind == "character" else (kind,))
            for asset_id in selected[name][1]
        }
        assert {
            item["id"] for item in run["inputSummary"]["assets"] if item["type"] == "image"
        } == expected_all
        assert (
            client.post("/api/v1/templates/replacement-runs", json=payload, headers=headers).json()[
                "data"
            ]["id"]
            == run["id"]
        )
        if kind == "character":
            invalid = {
                **payload,
                "requestId": str(uuid4()),
                "extraPackIds": [selected["A"][0].id],
                "extraPackVersions": [1],
            }
            assert (
                client.post(
                    "/api/v1/templates/replacement-runs", json=invalid, headers=headers
                ).status_code
                == 400
            )
            invalid.update(
                extraPackIds=[selected["A"][0].id, selected["A"][0].id], extraPackVersions=[1, 1]
            )
            assert (
                client.post(
                    "/api/v1/templates/replacement-runs", json=invalid, headers=headers
                ).status_code
                == 400
            )
        else:
            invalid = {**payload, "requestId": str(uuid4()), "extraPackIds": [selected["A"][0].id]}
            assert (
                client.post(
                    "/api/v1/templates/replacement-runs", json=invalid, headers=headers
                ).status_code
                == 400
            )

    # 提交之前额外人物包停用，同样必须拦住，而不只检查第一个包。
    async def disable_extra_and_check():
        async with get_session_factory()() as session:
            extra = await session.get(TemplatePack, selected["B"][0].id)
            extra.status = "disabled"
            await session.commit()
            run = await RunRepository(session).get_or_404(prepared["character"]["id"])
            assert run.configuration["extra_pack_versions"] == [1, 1]
            assert [group["packId"] for group in run.prompt_spec["selectedPacks"]] == [
                selected[key][0].id for key in ("C", "A", "B")
            ]
            assert await authorization_gap(session, run) is not None

    asyncio.run(disable_extra_and_check())
    for run in prepared.values():
        asyncio.run(drop_run(run["id"]))
    for pack, _ in selected.values():
        asyncio.run(drop_pack(pack.id))


@pytest.mark.parametrize("kind", ["product", "scene"])
def test_non_character_contract_stays_single_pack(kind):
    from core.template_rules.bindings import allows_partial_slots

    assert not allows_partial_slots(["front", "front"], kind)
