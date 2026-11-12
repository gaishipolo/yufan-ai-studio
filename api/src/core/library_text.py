"""文本资产引用：`{{Text N}}` → 正文快照注入。

方案 C 的落地。生图、视频、替换链三条生成路径都要「从资产库挑一段文案，
把它接进提示词」，规则必须只有一份，否则档案里写 `{{Text 1}}`、请求体里
却是另一段文字，出图正常只是内容不对，极难发现。

## 为什么是快照而不是 id

注入是把正文**抄进**提示词。用户提交后仍可能编辑那份文案；只存 assetId，
重试 / 恢复 / 归档回看时读到的就是改过稿的另一段文字，历史不可复现。
所以受理时就把 `id / name / version / sha256 / content` 冻结进任务的
`text_json`，提交期只读这份快照，**不再查库、不再读 blob**。

## 与 `core.prompt_refs` 的分工

那边处理 `{{Mixed N}}` → 「视频1 / 图2 / 音频1」的**类型内重编号**（替换链
专用，素材本身由驱动上传）。这里处理 `{{Text N}}` → **正文内联**。两者互不
相识：文本不是上传给供应商的素材，不能塞进 `entries` 里让编号错位。

只认带花括号的 `{{Text N}}`，不认裸 `Text 1`：提示词里出现 "Text 1" 是正常
英文，宽松匹配会把用户写的句子改掉。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.exceptions import BadRequest
from core.ports.library_lookup import get_library_lookup

#: 单次生成最多绑几份文案。每份上限 1MB，不封顶会让一条任务把整页正文
#: 反复拼进提示词，供应商侧按 token 计费时这是一笔看不见的开销。
MAX_TEXT_REFS = 8

#: 单次生成注入的正文**总量**上限（字节）。
#:
#: 「几份」与「每份多大」两个闸门缺一不可：8 份 × 1MB = 8MB 的提示词，
#: 供应商侧会被自己的长度上限拒掉，用户只看到一句「请求参数非法」，
#: 查不出是自己贴了 8 份长文。这个值已经远大于正常用法（一两段文案），
#: 又小到任何供应商都不会拒。
MAX_TEXT_REF_BYTES = 256 * 1024

_TOKEN = re.compile(r"\{\{\s*Text\s+(\d+)\s*\}\}")

_SNAPSHOT_KEYS = ("assetId", "name", "version", "sha256", "content", "sizeBytes")


def _total_bytes(refs: Sequence[Mapping[str, Any]]) -> int:
    return sum(len(str(item.get("content") or "")) for item in refs)


def dumps_text_refs(refs: Sequence[Mapping[str, Any]]) -> str:
    """冻结快照 → JSON 文本。空列表写空串，与 `dumps_params` 同一判空口径。"""
    if not refs:
        return ""
    return json.dumps([dict(item) for item in refs], ensure_ascii=False)


def parse_text_refs(raw: str | None) -> list[dict[str, Any]]:
    """读快照。**读路径对脏数据宽容**：一条历史坏行不该让整页列表 500。

    正文可能很长，但只有本任务自己读；不做截断，否则注入出来的提示词会
    在用户不知情的情况下少半句话。
    """
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    items: list[dict[str, Any]] = []
    for entry in data:
        if not isinstance(entry, dict) or not str(entry.get("assetId") or "").strip():
            continue
        items.append({key: entry.get(key) for key in _SNAPSHOT_KEYS})
    return items


def validate_text_refs(prompt: str, refs: Sequence[Mapping[str, Any]]) -> None:
    """受理期校验：提示词里的每个 `{{Text N}}` 都必须落在绑定范围内。

    放在受理而不是提交，是为了让「编号写错」当场 400，不是等异步任务跑起来
    才失败。反过来不要求每个绑定都被引用——用户先挑了文案、还没想好插在哪，
    是一次正常的编辑中间态，不该拦。

    总量上限也在这里拦：份数与单份大小是两个闸门，缺一个都会让「8 份长文」
    拼出几 MB 的提示词，被供应商的长度上限拒成一句看不懂的参数错误。
    """
    for match in _TOKEN.finditer(prompt):
        number = int(match[1])
        if number < 1 or number > len(refs):
            raise BadRequest(
                f"提示词引用了 {{{{Text {number}}}}}, 但本次只绑定了 {len(refs)} 份文本素材"
            )
    total = _total_bytes(refs)
    if total > MAX_TEXT_REF_BYTES:
        raise BadRequest(
            f"本次绑定的文本素材合计 {total // 1024} KB，超过 "
            f"{MAX_TEXT_REF_BYTES // 1024} KB 上限，请精简后再试"
        )


def inject_text_refs(prompt: str, refs: Sequence[Mapping[str, Any]]) -> str:
    """把 `{{Text N}}` 换成第 N 份文案的正文。

    `re.sub` 不会重新扫描替换进去的内容：文案里就算写着 `{{Text 1}}` 也只会被
    当成普通文本交给供应商，不会被二次展开成注入攻击面。

    ## 读路径必须宽容

    这是**读**路径（轮询 / 重试 / 启动恢复都会重建记录），不是受理路径：

    - 历史任务的 `text_json` 可能为空，而提示词里留着 `{{Text 1}}`——那是
      文本资产功能上线**之前**的老记录，或者数据被裁剪过。此时抛 400 会让
      这条记录从此**既不能轮询也不能重试**，一个坏字段就报废整条任务。
    - 快照里某一份正文缺失（脏数据）同理。

    所以这里**不抛错**：找不到正文就把占位符原样留着，交给供应商。产物内容
    差一点，总好过任务卡死。真正的口径错误在受理期（`freeze_text_refs`
    → `validate_text_refs`）就已经拦掉了——**这里刻意不再调它**。
    """
    if not refs and not _TOKEN.search(prompt):
        return prompt

    def replace(match: re.Match[str]) -> str:
        number = int(match[1])
        if number < 1 or number > len(refs):
            # 编号越界：留原文，不抛。理由同上。
            return match[0]
        return str(refs[number - 1].get("content") or "")

    return _TOKEN.sub(replace, prompt)


async def freeze_text_refs(
    session: AsyncSession,
    actor: CurrentUser,
    asset_ids: Sequence[str],
    *,
    prompt: str,
) -> list[dict[str, Any]]:
    """把「挑中的文本资产 id」解析成冻结快照，顺序即 `{{Text N}}` 的编号顺序。

    可读性判据**只有 `LibraryLookup.read_text_many` 一份**：拿别人的私有文案
    当提示词，和拿别人的私图当参考图一样是绕过共享审批。查不到 / 非文本 /
    不在库一律报错，绝不「查不到就跳过」——那会让提示词静默少一段。
    """
    wanted = [str(item).strip() for item in asset_ids]
    wanted = [item for item in dict.fromkeys(wanted) if item]
    if len(wanted) > MAX_TEXT_REFS:
        raise BadRequest(f"一次最多绑定 {MAX_TEXT_REFS} 份文本素材")
    if not wanted:
        validate_text_refs(prompt, [])
        return []

    found = await get_library_lookup().read_text_many(session, actor, wanted)
    missing = [asset_id for asset_id in wanted if asset_id not in found]
    if missing:
        raise BadRequest(
            "文本素材不存在、不是文本类型、或当前账号无权使用："
            f"{missing}"
        )

    snapshot = [
        {
            "assetId": asset_id,
            "name": found[asset_id].name,
            "version": found[asset_id].version,
            "sha256": found[asset_id].sha256,
            "content": found[asset_id].content,
            "sizeBytes": found[asset_id].size_bytes,
        }
        for asset_id in wanted
    ]
    validate_text_refs(prompt, snapshot)
    return snapshot


#: 替换链的编译器（Node）对用户输入一律拒绝 `{{` / `}}`，最终提示词里也只认
#: `{{Mixed N}}`。所以那条链不能直接把正文塞进编译器输入，要先把 `{{Text N}}`
#: 换成不含花括号的占位串，编译完再换回正文。
_MASK = "__YUFAN_TEXT_{}__"
_MASK_TOKEN = re.compile(r"__YUFAN_TEXT_(\d+)__")


def mask_text_refs(prompt: str, refs: Sequence[Mapping[str, Any]]) -> str:
    """编译前：`{{Text N}}` → 编译器能接受的普通占位串。"""
    if not refs and not _TOKEN.search(prompt):
        return prompt
    validate_text_refs(prompt, refs)
    return _TOKEN.sub(lambda match: _MASK.format(int(match[1])), prompt)


def inject_masked_text_refs(
    prompt: str, refs: Sequence[Mapping[str, Any]]
) -> str:
    """编译后：占位串 → 正文。此时正文里就算有 `{{...}}` 也已是纯文本。

    与 `inject_text_refs` 同口径：这是**读路径**（重试 / 恢复会重跑编译），
    快照缺失时把占位串原样留下，不抛错。受理期（`mask_text_refs`）已经
    拦过编号越界，这里的容错只兜住历史脏数据。
    """
    if not refs:
        return prompt

    def replace(match: re.Match[str]) -> str:
        number = int(match[1])
        if number < 1 or number > len(refs):
            return match[0]
        return str(refs[number - 1].get("content") or "")

    return _MASK_TOKEN.sub(replace, prompt)


def text_ref_bindings(refs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """出参用的精简投影：**不带正文**。

    历史列表一页几十条，把 1MB 正文塞进出参会让接口体积炸掉，前端要显示
    的只是「用了哪几份文案、各是第几版」。
    """
    return [
        {
            "assetId": item.get("assetId") or "",
            "name": item.get("name") or "",
            "version": int(item.get("version") or 0),
            "sha256": item.get("sha256") or "",
        }
        for item in refs
    ]
