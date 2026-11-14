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
from types import SimpleNamespace

import pytest
from video_template_support import IMAGES, PACK_ID, SOURCE_ID, put, record, run

from core.exceptions import BadRequest
from core.ports.template_runtime import RuntimeTemplate
from db.session import get_session_factory
from modules.template import video_template_service as service
from modules.template.replacement_service import (
    _check_video_template,
    _media_in_mixed_order,
    _prompt_origin,
)
from modules.template.runtime_port_impl import to_runtime
from modules.template.video_template_media import apply_pack_roles
from modules.template.video_template_schema import VideoTemplateMedia, VideoTemplateOut
from modules.template.workspace import RUNTIME


def template(**overrides: object) -> RuntimeTemplate:
    """样板模板的**执行链快照**。

    走 `to_runtime()` 而不是自己映射：执行链（`_media_in_mixed_order` /
    `_check_video_template`）读的是 core 层快照，测试若拿业务出参模型去调，
    `dataclasses.replace()` 会在运行时才发现对象不对。
    """
    return to_runtime(VideoTemplateOut.model_validate(record(**overrides)))


def find_for_run(*args: object, **kwargs: object) -> VideoTemplateOut | None:
    """同步调用包装：这条链上的用例都不走 HTTP，自建一个事件循环就够。"""

    async def go():
        async with get_session_factory()() as session:
            return await service.find_for_run(*args, session=session, **kwargs)

    return run(go())


def test_find_for_run_needs_both_source_and_pack() -> None:
    owner_source = "source-dual-key"
    put(**record(id="older", templateKey="older", pinned=False, sourceVideoAssetId=owner_source,
                 createdAt="2026-01-01T00:00:00Z"))
    put(**record(id="pinned", templateKey="pinned", pinned=True, sourceVideoAssetId=owner_source,
                 createdAt="2026-01-02T00:00:00Z"))
    put(**record(id="other-pack", templateKey="other-pack", packId="another-pack",
                 sourceVideoAssetId=owner_source, pinned=False))
    put(**record(id="no-source", templateKey="no-source", sourceVideoAssetId=None, pinned=False))

    found = find_for_run(owner_source, PACK_ID)
    assert found is not None
    assert found.id == "pinned"
    assert found.default_kind == "product"
    assert find_for_run(owner_source, "another-pack") is not None
    assert find_for_run("0507-source", PACK_ID) is None
    assert find_for_run(owner_source, None) is None
    assert find_for_run("", "") is None


def test_explicit_function_selection_cannot_fall_back() -> None:
    # 用自己的原片 id：测试库整场共用，共用一个 id 会让别的用例留下的行混进候选集
    owner_source = "source-explicit"
    put(**record(id="a", templateKey="a", pinned=True, sourceVideoAssetId=owner_source))
    put(**record(id="b", templateKey="b", pinned=False, sourceVideoAssetId=owner_source))
    assert find_for_run(owner_source, PACK_ID, "product", "b").id == "b"
    for source, kind, selected in [
        (owner_source, "product", "missing"),
        ("another-source", "product", "b"),
        (owner_source, "character", "b"),
    ]:
        with pytest.raises(BadRequest):
            find_for_run(source, PACK_ID, kind, selected)


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
    from core.template_rules.options import validate_options

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
    store_apply = apply_pack_roles
    store_apply(media, refs)
    assert [item.role for item in media] == ["source_video", "front", "multiview"]


async def test_pinning_writes_an_audit_event(monkeypatch) -> None:
    """切固定蓝本影响所有后续封装：要留审计，否则「谁把蓝本换了」查不出来。

    与 pack publish/disable 同一口径；create/attach-source 只算保存，不记。
    """
    from modules.template import video_template_router as router_module

    seen: dict[str, object] = {}
    events: list[tuple[str, dict[str, object]]] = []

    async def fake_pin(template_id: str, session: object) -> RuntimeTemplate:
        seen["id"] = template_id
        seen["session"] = session
        return template(id=template_id)

    async def fake_record(action: str, **kwargs: object) -> None:
        events.append((action, kwargs))

    monkeypatch.setattr(router_module.service, "pin", fake_pin)
    monkeypatch.setattr(router_module.audit, "record", fake_record)

    marker = object()
    result = await router_module.pin_video_template(
        "template-1", SimpleNamespace(id="u_001"), marker
    )

    assert seen["id"] == "template-1"
    # 会话必须由路由透传下去，别在 service 里另开一个（那样审计与写入不在一事务）
    assert seen["session"] is marker
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


def test_find_for_run_is_pack_agnostic() -> None:
    """同一原片换任意产品包仍命中同一条模板；未绑定的原片仍走通用提示词。"""
    # 独立原片 id：见上一个用例的说明（库是整场共用的）
    owner_source = "source-agnostic"
    put(**record(id="fixed", templateKey="fixed", pinned=True,
                 sourceVideoAssetId=owner_source))
    put(**record(id="another", templateKey="another", packId="other-pack",
                 sourceVideoAssetId="three-ladies", defaultKind="product", pinned=False))
    assert find_for_run(owner_source, PACK_ID).id == "fixed"
    assert find_for_run(owner_source, "another-pack").id == "fixed"
    assert find_for_run("three-ladies", "any-product-pack").id == "another"
    assert find_for_run("three-ladies", "any-product-pack", kind="character") is None
    assert find_for_run("0507-source", PACK_ID) is None


def test_prompt_origin_tells_where_the_prompt_came_from() -> None:
    """档案要能回答「提示词是谁写的」：两种文本长得一样，用户没法自己判断。

    `compiled` 说清楚是现编的，`fixed` 要说清出自哪条模板；老档案只有模板 id
    时从调用方预热好的名字表里取，取不到就只报 fixed 不报名字（展示层自己降级）。

    名字表本身怎么来（经 `template-runtime` 端口批量反查）由
    `test_template_runtime_port.py` 覆盖——这里只验投影的判定逻辑，不碰库。
    """
    assert _prompt_origin(None, {}) == ("compiled", None)
    assert _prompt_origin({"promptMode": "compiled"}, {}) == ("compiled", None)
    named = {"promptMode": "fixed_video_template", "videoTemplateName": "三人高端姐"}
    assert _prompt_origin(named, {}) == ("fixed", "三人高端姐")

    names = {"legacy-id": "老档案用的模板"}
    assert _prompt_origin(
        {"promptMode": "fixed_video_template", "videoTemplateId": "legacy-id"}, names
    ) == ("fixed", "老档案用的模板")
    assert _prompt_origin(
        {"promptMode": "fixed_video_template", "videoTemplateId": "missing-id"}, names
    ) == ("fixed", None)


def test_prompt_origin_survives_string_spec() -> None:
    """历史档案里 `prompt_spec` 可能是 JSON 文本：不能因此把列表接口打成 500。"""
    assert _prompt_origin('{"promptMode": "fixed_video_template"}', {}) == ("compiled", None)
    assert _prompt_origin({}, {}) == ("compiled", None)
