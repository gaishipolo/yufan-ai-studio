"""视频模板与原片卡片绑定后的生成链路。

四件事各守一处：
1. `find_for_run` 只认「原片 asset id + 素材包」双键，普通原片（0507 高端姐）不受影响；
2. 编译器在 `fixed_prompt` 下仍产出完整 bindings，只换最终文案；
3. 命中模板时 mixedList 顺序按**模板自己**的 mediaOrder 拼（`{{Mixed 1}}` 是原片，
   第 2 位可以是三视图而不是素材包里排第一的主图），漏列的图补在后面一张不丢；
4. 素材包升版 / 换图 / 少图 / 提示词越界引用必须在 prepare 前拦下。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.exceptions import BadRequest
from modules.template import video_template_store as store
from modules.template.replacement_service import (
    _check_video_template,
    _media_in_mixed_order,
    _prompt_origin,
)
from modules.template.video_template_schema import VideoTemplateMedia, VideoTemplateOut
from modules.template.workspace import RUNTIME

NOW = datetime.now(UTC).isoformat().replace("+00:00", "Z")
SOURCE_ID = "ac1d588d36294fa49c809d46bb1dbb6d"
PACK_ID = "1cbf52ffeeb044f1ad2cc84aa484b941"
IMAGES = ["front-asset", "multiview-asset", "detail-asset"]


def record(**overrides: object) -> dict:
    base = {
        "id": "template-1",
        "templateKey": "milan_night_3030_pink",
        "name": "米兰之夜 · 3030 粉色",
        "description": "",
        "prompt": "对 {{Mixed 1}} 做替换，以 {{Mixed 3}} 三视图为准。",
        "promptSha256": "0" * 64,
        "sourceVideoName": "我穿的是米兰之夜模板测试.mp4",
        "sourceVideoAssetId": SOURCE_ID,
        "sourceVideoPath": "",
        "sourceDurationSec": 5.312,
        "aspectRatio": "9:16",
        "packId": PACK_ID,
        "packName": "3030 粉色",
        "packVersion": 2,
        "defaultKind": "product",
        "mediaOrder": [
            {"token": "{{Mixed 1}}", "role": "source_video", "type": "video", "name": "样片"},
            *[
                {"token": f"{{{{Mixed {index}}}}}", "role": "multiview", "type": "image",
                 "assetId": asset_id, "name": asset_id}
                for index, asset_id in enumerate(IMAGES, 2)
            ],
        ],
        "generation": {"duration": 5, "ratio": "9:16"},
        "rules": {"promptMode": "fixed_no_compilation"},
        "coverUrl": "",
        "pinned": True,
        "createdAt": NOW,
        "updatedAt": NOW,
    }
    base.update(overrides)
    return base


def template(**overrides: object) -> VideoTemplateOut:
    return VideoTemplateOut.model_validate(record(**overrides))


def catalog(path: Path, items: list[dict]) -> None:
    path.write_text(
        json.dumps({"version": 1, "items": items}, ensure_ascii=False), encoding="utf-8"
    )


def test_find_for_run_needs_both_source_and_pack(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "catalog.json"
    monkeypatch.setattr(store, "CATALOG_PATH", target)
    catalog(target, [
        record(id="older", pinned=False, createdAt="2026-01-01T00:00:00Z"),
        record(id="pinned", pinned=True, createdAt="2026-01-02T00:00:00Z"),
        record(id="other-pack", packId="another-pack", pinned=False),
        record(id="no-source", sourceVideoAssetId=None, pinned=False),
    ])
    found = store.find_for_run(SOURCE_ID, PACK_ID)
    assert found is not None and found.id == "pinned"
    assert found.default_kind == "product"
    assert store.find_for_run(SOURCE_ID, "another-pack") is not None
    assert store.find_for_run("0507-source", PACK_ID) is None
    assert store.find_for_run(SOURCE_ID, None) is None
    assert store.find_for_run("", "") is None


def test_explicit_function_selection_cannot_fall_back(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "catalog.json"
    monkeypatch.setattr(store, "CATALOG_PATH", target)
    catalog(target, [record(id="a", pinned=True), record(id="b", pinned=False)])
    assert store.find_for_run(SOURCE_ID, PACK_ID, "product", "b").id == "b"
    for source, kind, selected in [
        (SOURCE_ID, "product", "missing"),
        ("another-source", "product", "b"),
        (SOURCE_ID, "character", "b"),
    ]:
        with pytest.raises(BadRequest):
            store.find_for_run(source, PACK_ID, kind, selected)


def test_template_scope_is_optional_and_prompt_owned() -> None:
    from modules.template.replacement_service import _check_scope
    from modules.template.schema import PrepareReplacement

    payload = PrepareReplacement(
        requestId="00000000-0000-0000-0000-000000000001",
        sourceId=SOURCE_ID, kind="product",
    )
    _check_scope([], payload, template())
    scoped = template(rules={"scopeOptions": ["只换前景", "全片替换"]})
    with pytest.raises(BadRequest):
        _check_scope([], payload, scoped)
    _check_scope([], payload.model_copy(update={"replacement_scope": "只换前景"}), scoped)
    with pytest.raises(BadRequest):
        _check_scope([], payload.model_copy(update={"replacement_scope": "任意位置"}), scoped)


def test_fixed_prompt_scope_parameter_is_rendered() -> None:
    result = compile_spec("编辑 {{Mixed 1}}，使用 {{Mixed 2}}，范围：{{ReplacementScope}}。")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["prompt"].endswith("范围：手持展示产品。")


def test_scope_configuration_must_fit_prepare_request() -> None:
    from modules.template.video_template_options import validate_options

    with pytest.raises(BadRequest):
        validate_options("{{Mixed 1}} {{Mixed 2}} {{ReplacementScope}}", {
            "scopeOptions": [str(index) + "范" * 98 for index in range(11)],
        })


def compile_spec(fixed_prompt: str, usage: str = "") -> subprocess.CompletedProcess[str]:
    node = shutil.which("node")
    if not node:
        pytest.skip("本机没有 node，跳过编译器回归")
    spec = {
        "task": "product",
        "duration": 5,
        "aspect_ratio": "9:16",
        "wardrobe_mode": "preserve",
        "references": [
            {"id": "video", "role": "source_video", "type": "video",
             "file": "inputs/reference-video.mp4"},
            {"id": "front", "role": "target_product", "type": "image",
             "file": "inputs/reference-2.png"},
            {"id": "multiview", "role": "target_product", "type": "image",
             "file": "inputs/reference-3.png"},
            {"id": "detail", "role": "target_product", "type": "image",
             "file": "inputs/reference-4.png"},
        ],
        "variables": {
            "source_product": "原片中文胸",
            "target_product": "3030 粉色",
            "replacement_scope": "手持展示产品",
            "state_constraints": "",
        },
        "fixed_prompt": fixed_prompt,
    }
    if usage:
        for ref in spec["references"]:
            if ref["type"] == "image":
                ref["usage"] = usage
    return subprocess.run(
        [node, str(RUNTIME / "workbench/compile.mjs")],
        input=json.dumps(spec, ensure_ascii=False),
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )


def test_fixed_prompt_replaces_text_but_keeps_bindings() -> None:
    fixed = "对输入的原视频 {{Mixed 1}} 做替换，目标款式由 {{Mixed 3}} 三视图决定。"
    result = compile_spec(fixed)
    assert result.returncode == 0, result.stderr
    compiled = json.loads(result.stdout)
    assert compiled["prompt"] == fixed
    assert compiled["compiler_version"].endswith("+fixed-video-template-v1")
    assert [item["token"] for item in compiled["bindings"]] == [
        "{{Mixed 1}}", "{{Mixed 2}}", "{{Mixed 3}}", "{{Mixed 4}}"
    ]
    assert compiled["bindings"][0]["type"] == "video"


def test_fixed_prompt_rejects_out_of_range_token() -> None:
    result = compile_spec("只引用 {{Mixed 9}}，越界。")
    assert result.returncode != 0
    assert "unresolved reference tokens" in result.stderr


def test_media_order_follows_template_not_pack() -> None:
    def ref(asset_id: str) -> SimpleNamespace:
        return SimpleNamespace(id=asset_id)

    source, front, multiview, detail = (ref(item) for item in ["source-video", *IMAGES])
    reordered = template(mediaOrder=[
        {"token": "{{Mixed 1}}", "role": "source_video", "type": "video", "name": "样片"},
        {"token": "{{Mixed 2}}", "role": "multiview", "type": "image",
         "assetId": IMAGES[1], "name": "三视图"},
        {"token": "{{Mixed 3}}", "role": "front", "type": "image",
         "assetId": IMAGES[0], "name": "主图"},
        {"token": "{{Mixed 4}}", "role": "detail", "type": "image",
         "assetId": IMAGES[2], "name": "细节"},
    ])
    # 模板说三视图在第二位，接口就照做——素材包内部的顺序不再决定 mixedList
    assert _media_in_mixed_order(reordered, source, [front, multiview, detail]) == [
        source, multiview, front, detail
    ]
    # 没有绑定模板的普通原片，行为一个字都不变
    assert _media_in_mixed_order(None, source, [front, multiview, detail]) == [
        front, multiview, detail, source
    ]
    # 模板漏列的图补在后面，不能因为没列就少插一张
    assert _media_in_mixed_order(template(), source, [front, multiview, detail]) == [
        source, front, multiview, detail
    ]


def test_check_video_template_matches_roles_from_selected_pack() -> None:
    """动态选包：换包、升版、换 asset id 都不用重新封装，凑不齐用途才拦。"""
    roles = {
        asset: role
        for asset, role in zip(IMAGES, ["front", "multiview", "detail"], strict=True)
    }
    _check_video_template(template(), roles, "product")
    another_pack = {f"other-{index}": role for index, role in enumerate(roles.values())}
    _check_video_template(template(), another_pack, "product")
    with pytest.raises(BadRequest, match="缺少"):
        _check_video_template(template(), {"only-front": "front"}, "product")
    with pytest.raises(BadRequest, match="越界引用"):
        _check_video_template(template(prompt="引用 {{Mixed 9}}"), roles, "product")


def test_token_ceiling_follows_the_selected_pack_not_the_pack_used_at_wrap_time() -> None:
    """换了一个图更少的包：封装时合法、运行时不存在的编号必须在 prepare 前拦下。"""
    roles = {"a": "front", "b": "multiview", "c": "detail"}
    # 封装时 5 张图，提示词引用 {{Mixed 5}}；当前包只有 3 张，mixedList 仅 4 项
    images = [
        {"token": f"{{{{Mixed {index}}}}}", "role": role, "type": "image",
         "assetId": f"asset-{index}", "name": f"图 {index}"}
        for index, role in enumerate(
            ["front", "multiview", "detail", "front", "multiview"], 2
        )
    ]
    stale = template(
        prompt="引用 {{Mixed 1}} 与 {{Mixed 5}}",
        mediaOrder=[
            {"token": "{{Mixed 1}}", "role": "source_video", "type": "video", "name": "样片"},
            *images,
        ],
    )
    with pytest.raises(BadRequest, match="越界引用.*最大可用 \\{\\{Mixed 4\\}\\}"):
        _check_video_template(stale, roles, "product")


def test_media_order_roles_are_taken_from_the_pack_not_the_client() -> None:
    """封装界面提交的 role 是空的：必须以素材包为准补齐，否则「按用途换包取图」失效。"""
    media = [
        VideoTemplateMedia(token="{{Mixed 1}}", role="source_video", type="video"),
        VideoTemplateMedia(token="{{Mixed 2}}", role="", type="image", asset_id="a"),
        VideoTemplateMedia(token="{{Mixed 3}}", role="", type="image", asset_id="b"),
    ]
    refs = [
        SimpleNamespace(asset_id="a", role="front"),
        SimpleNamespace(asset_id="b", role="multiview"),
    ]
    store._apply_pack_roles(media, refs)
    assert [item.role for item in media] == ["source_video", "front", "multiview"]


async def test_pinning_writes_an_audit_event(monkeypatch) -> None:
    """切固定蓝本影响所有后续封装：要留审计，否则「谁把蓝本换了」查不出来。

    与 pack publish/disable 同一口径；create/attach-source 只算保存，不记。
    """
    from modules.template import video_template_router as router_module

    seen: dict[str, object] = {}
    events: list[tuple[str, dict[str, object]]] = []

    def fake_pin(template_id: str) -> VideoTemplateOut:
        seen["id"] = template_id
        return template(id=template_id)

    async def fake_record(action: str, **kwargs: object) -> None:
        events.append((action, kwargs))

    monkeypatch.setattr(router_module.store, "pin", fake_pin)
    monkeypatch.setattr(router_module.audit, "record", fake_record)

    result = await router_module.pin_video_template(
        "template-1", SimpleNamespace(id="u_001")
    )

    assert seen["id"] == "template-1"
    assert result.data.id == "template-1"
    assert events == [
        (
            "template.video_template.pin",
            {
                "actor_id": "u_001",
                "target_type": "video_template",
                "target_id": "template-1",
                "detail": {"name": "米兰之夜 · 3030 粉色", "packId": PACK_ID},
            },
        )
    ]


def test_find_for_run_is_pack_agnostic(tmp_path: Path, monkeypatch) -> None:
    """同一原片换任意产品包仍命中同一条模板；未绑定的原片仍走通用提示词。"""
    target = tmp_path / "catalog.json"
    monkeypatch.setattr(store, "CATALOG_PATH", target)
    catalog(target, [
        record(id="fixed", pinned=True),
        record(id="another", packId="other-pack", sourceVideoAssetId="three-ladies",
               defaultKind="product", pinned=False),
    ])
    assert store.find_for_run(SOURCE_ID, PACK_ID).id == "fixed"
    assert store.find_for_run(SOURCE_ID, "another-pack").id == "fixed"
    assert store.find_for_run("three-ladies", "any-product-pack").id == "another"
    assert store.find_for_run("three-ladies", "any-product-pack", kind="character") is None
    assert store.find_for_run("0507-source", PACK_ID) is None


def test_prompt_origin_tells_where_the_prompt_came_from(tmp_path: Path, monkeypatch) -> None:
    """档案要能回答「提示词是谁写的」：两种文本长得一样，用户没法自己判断。

    `compiled` 说清楚是现编的，`fixed` 要说清出自哪条模板；老档案只有模板 id
    时反查一次，模板被删了就只报 fixed 不报名字（展示层自己降级）。
    """
    assert _prompt_origin(None) == ("compiled", None)
    assert _prompt_origin({"promptMode": "compiled"}) == ("compiled", None)
    named = {"promptMode": "fixed_video_template", "videoTemplateName": "三人高端姐"}
    assert _prompt_origin(named) == ("fixed", "三人高端姐")

    target = tmp_path / "catalog.json"
    monkeypatch.setattr(store, "CATALOG_PATH", target)
    catalog(target, [record(id="legacy-id", name="老档案用的模板")])
    assert _prompt_origin(
        {"promptMode": "fixed_video_template", "videoTemplateId": "legacy-id"}
    ) == ("fixed", "老档案用的模板")
    assert _prompt_origin(
        {"promptMode": "fixed_video_template", "videoTemplateId": "missing-id"}
    ) == ("fixed", None)


def test_prompt_origin_survives_string_spec(tmp_path: Path, monkeypatch) -> None:
    """历史档案里 `prompt_spec` 可能是 JSON 文本：不能因此把列表接口打成 500。"""
    assert _prompt_origin('{"promptMode": "fixed_video_template"}') == ("compiled", None)
    assert _prompt_origin({}) == ("compiled", None)
