"""文本资产引用：`{{Text N}}` → 冻结正文快照 → 提示词注入。

这个文件钉住四条最容易「看着对、其实静默错」的口径：

1. 编号按提交顺序，重复 id 去重、越界编号当场 400；
2. 受理期冻结，提交期只读快照 —— 资产后来被编辑，历史任务不会换一段提示词；
3. 正文里的 `{{Text 1}}` 不能被二次展开（否则一份文案就是注入面）；
4. 替换链要先 mask 再注入：编译器拒绝任何花括号，直接塞正文会整条编译失败。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from library_support import suffix

from core.context import CurrentUser
from core.exceptions import BadRequest
from core.library_text import (
    MAX_TEXT_REFS,
    dumps_text_refs,
    freeze_text_refs,
    inject_masked_text_refs,
    inject_text_refs,
    mask_text_refs,
    parse_text_refs,
    text_ref_bindings,
    validate_text_refs,
)
from db.session import get_session_factory
from infra import media_probe
from modules.image.models import ImageJob
from modules.image.pipeline import ImageJobStore
from modules.image.schema import ImageJobOut

API = "/api/v1"
ADMIN = CurrentUser(id="u_admin", name="admin", roles=("admin",), permissions=frozenset({"*"}))


def _refs(*contents: str) -> list[dict[str, Any]]:
    return [
        {
            "assetId": f"text-{index}",
            "name": f"文案{index}",
            "version": 1,
            "sha256": f"hash-{index}",
            "content": content,
            "sizeBytes": len(content.encode("utf-8")),
        }
        for index, content in enumerate(contents, start=1)
    ]


def _job(**overrides: object) -> ImageJob:
    fields: dict[str, object] = {
        "id": "img-text-1",
        "name": "文本注入",
        "prompt": "请按 {{Text 1}} 的风格，画出 {{Text 2}}",
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


# --------------------------------------------------------------------------- 注入口径


def test_injection_follows_submit_order_and_never_expands_twice() -> None:
    refs = _refs("一只橘猫", "{{Text 2}} 本身是普通文字")
    rendered = inject_text_refs("请按 {{Text 1}} 的风格，画出 {{Text 2}}", refs)

    assert rendered == "请按 一只橘猫 的风格，画出 {{Text 2}} 本身是普通文字"
    # 裸 "Text 1" 是正常英文，不能被当成引用
    assert inject_text_refs("Text 1 is a label", refs) == "Text 1 is a label"


def test_out_of_range_token_is_rejected_at_intake() -> None:
    with pytest.raises(BadRequest, match="只绑定了 1 份"):
        validate_text_refs("用 {{Text 2}} 写", _refs("唯一"))
    with pytest.raises(BadRequest, match="只绑定了 0 份"):
        inject_text_refs("用 {{Text 1}} 写", [])
    # 绑定了但还没想好插在哪，是正常编辑中间态，不该拦
    assert inject_text_refs("暂不引用", _refs("备用")) == "暂不引用"


def test_mask_round_trip_survives_braced_content() -> None:
    """替换链编译器拒绝 `{{`/`}}`：先换成无花括号占位串，编译完再填正文。"""
    refs = _refs("# 标题\n含 {{Mixed 1}} 字样的文案")
    masked = mask_text_refs("场景：{{Text 1}}", refs)
    assert "{{" not in masked and "}}" not in masked
    assert "__YUFAN_TEXT_1__" in masked

    # 模拟编译器：只保留占位串，正文此时还没进来
    compiled = f"PROMPT[{masked}]"
    assert inject_masked_text_refs(compiled, refs) == (
        "PROMPT[场景：# 标题\n含 {{Mixed 1}} 字样的文案]"
    )


def test_snapshot_roundtrip_and_binding_projection() -> None:
    refs = _refs("第一段", "第二段")
    restored = parse_text_refs(dumps_text_refs(refs))
    assert [item["content"] for item in restored] == ["第一段", "第二段"]

    # 历史列表一页几十条，出参不能带 1MB 正文
    assert text_ref_bindings(refs) == [
        {"assetId": "text-1", "name": "文案1", "version": 1, "sha256": "hash-1"},
        {"assetId": "text-2", "name": "文案2", "version": 1, "sha256": "hash-2"},
    ]
    assert dumps_text_refs([]) == ""


def test_dirty_history_rows_do_not_break_the_list() -> None:
    assert parse_text_refs("") == []
    assert parse_text_refs(None) == []
    assert parse_text_refs("{not json") == []
    assert parse_text_refs('{"assetId": "a"}') == []
    # 缺 assetId 的快照拼不出编号，整条丢掉
    assert parse_text_refs('[{"content": "x"}, {"assetId": "a"}]') == [
        {
            "assetId": "a",
            "name": None,
            "version": None,
            "sha256": None,
            "content": None,
            "sizeBytes": None,
        }
    ]


# --------------------------------------------------------------------------- 重试路径


def test_retry_rebuilds_the_prompt_from_the_frozen_snapshot() -> None:
    """`_to_record` 是重试的唯一入口；这里不注入就等于重试后内容静默变了。"""
    refs = _refs("橘猫", "傍晚的屋顶")
    job = _job(text_json=dumps_text_refs(refs))
    record = ImageJobStore._to_record(job)

    assert record.prompt == "请按 橘猫 的风格，画出 傍晚的屋顶"
    # 档案里仍该是「用了哪几份」，不是把正文塞进出参
    assert ImageJobOut.model_validate(job).text_bindings == text_ref_bindings(refs)
    assert ImageJobOut.model_validate(job).text_library_ids == ["text-1", "text-2"]


# --------------------------------------------------------------------------- 冻结与权限


def _login_admin(client: TestClient) -> tuple[dict[str, str], CurrentUser]:
    """用真实登录主体：私有文本资产的 owner 必须是同一个 id 才可读。"""
    response = client.post(
        f"{API}/identity/login", json={"username": "admin", "password": "admin@12345"}
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    actor = CurrentUser(
        id=body["user"]["id"],
        name="admin",
        roles=("admin",),
        permissions=frozenset({"*"}),
    )
    return {"Authorization": f"Bearer {body['accessToken']}"}, actor


def _create_text(client: TestClient, content: str) -> dict[str, Any]:
    headers, actor = _login_admin(client)
    response = client.post(
        f"{API}/library/text-assets",
        json={"name": f"文案{suffix()}", "content": content, "extension": "md"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return {"data": response.json()["data"], "headers": headers, "actor": actor}


def _freeze(
    asset_ids: list[str], prompt: str, actor: CurrentUser = ADMIN
) -> list[dict[str, Any]]:
    async def run() -> list[dict[str, Any]]:
        async with get_session_factory()() as session:
            return await freeze_text_refs(session, actor, asset_ids, prompt=prompt)

    return asyncio.run(run())


def test_freeze_rejects_unknown_ids_instead_of_silently_skipping() -> None:
    """「查不到就跳过」会让提示词静默少一段，必须当场报错。"""
    with pytest.raises(BadRequest, match="不存在、不是文本类型"):
        _freeze(["lib_missing_text"], "用 {{Text 1}}")


def test_freeze_rejects_out_of_range_token_and_too_many_refs() -> None:
    with pytest.raises(BadRequest, match="只绑定了 0 份"):
        _freeze([], "用 {{Text 1}}")
    with pytest.raises(BadRequest, match=f"最多绑定 {MAX_TEXT_REFS}"):
        _freeze([f"text-{index}" for index in range(MAX_TEXT_REFS + 1)], "无引用")


def test_freeze_snapshots_latest_version(client: TestClient) -> None:
    created = _create_text(client, "第一版正文")
    asset_id = created["data"]["id"]

    before = _freeze([asset_id], "按 {{Text 1}} 生成", created["actor"])
    assert before[0]["content"] == "第一版正文"
    assert before[0]["version"] == 1

    edited = client.put(
        f"{API}/library/assets/{asset_id}/text",
        json={"content": "第二版正文", "expectedVersion": 1},
        headers=created["headers"],
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["data"]["version"] == 2

    after = _freeze([asset_id], "按 {{Text 1}} 生成", created["actor"])
    assert after[0]["version"] == 2
    assert after[0]["content"] == "第二版正文"
    # 旧任务手里拿的快照不受影响
    assert before[0]["content"] == "第一版正文"


def test_read_text_many_hides_other_users_private_text() -> None:
    """拿别人的私有文案当提示词 = 绕过共享审批，必须按缺失报错。"""
    from library_support import insert_asset

    asset_id = insert_asset(
        owner_id="u_other_private",
        kind="text",
        state="active",
        visibility="personal",
        mime_type="text/plain; charset=utf-8",
        storage_key="t/secret.md",
    )
    with pytest.raises(BadRequest, match="不存在、不是文本类型"):
        _freeze([asset_id], "用 {{Text 1}}")


# --------------------------------------------------------------------------- 字节探测


def test_text_probe_strips_bom_and_normalizes_bytes(tmp_path: Path) -> None:
    source = tmp_path / "note.md"
    source.write_bytes(b"\xef\xbb\xbf# \xe6\xa0\x87\xe9\xa2\x98\n")
    assert media_probe.normalize_text_file(source) == "# 标题\n"
    assert not source.read_bytes().startswith(b"\xef\xbb\xbf")

    info = media_probe.inspect(source, ".md")
    assert info == {"width": 0, "height": 0, "duration": 0, "hasAudio": False}
    assert media_probe.create_preview(source, tmp_path / "cover.jpg", "text") is False
    assert media_probe.text_excerpt("第一行\n  第二行\t结尾") == "第一行 第二行 结尾"


def test_text_probe_rejects_bad_encoding_empty_and_oversize(tmp_path: Path) -> None:
    bad = tmp_path / "gbk.txt"
    bad.write_bytes("中文".encode("gbk"))
    with pytest.raises(BadRequest, match="UTF-8"):
        media_probe.inspect(bad, ".txt")

    empty = tmp_path / "empty.txt"
    empty.write_bytes(b" \n\t ")
    with pytest.raises(BadRequest, match="不能为空"):
        media_probe.inspect(empty, ".txt")

    huge = tmp_path / "huge.txt"
    huge.write_bytes(b"x" * (media_probe.MAX_TEXT_BYTES + 1))
    with pytest.raises(BadRequest, match="1MB"):
        media_probe.inspect(huge, ".txt")
