"""槽位规则：筛选条件的解析、校验与判定。

**模块私有**。筛选词表（哪些标签算「产品」）归资产库管，本模块只关心
「一条筛选条件长什么样、选中的素材满不满足它」。

## 为什么筛选条件要固化在槽位上，而不是提示词里

用户的要求是：「提示词输入筛选资产库 v2 的图片-一级标签=产品，那么用户在
模板库使用时只能选择资产库 v2 的图片-一级标签=产品的素材」。

这条规则是**模板的业务契约**，不是提示词的一段文字。把它写成提示词里的
自然语言，服务端就永远无法判断用户选的素材合不合法——只能靠前端过滤，
而接口是可以被直接调用的。

所以筛选条件存在 `template_pack_slots` 的结构化字段里，
提示词里只留 `{{Mixed N}}` 占位符。两边由 `position` 对齐。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from core.exceptions import BadRequest

__all__ = [
    "FILTERABLE_KINDS",
    "FilteredSlot",
    "cited_tokens",
    "parse_filtered_slot",
    "token_position",
]

#: 可被筛选的素材类别。
#:
#: **不含 `text`**：文本资产是「文件型资产 + 版本快照」，不是参考图。
#: 把它当参考素材塞进供应商请求体，参数校验会失败。资产库选择器同样默认
#: 排除文本（只有显式传 `kinds` 含 `text` 才出现）。
FILTERABLE_KINDS = frozenset({"image", "video", "audio"})

#: 标签组合语义。
#:
#: **只有 `any`**：资产库的筛选口径是唯一的 —— `LibraryLookup.find_ids` 里
#: 标签条件写死为 `tag_id IN (展开后的标签集)`，也就是 OR。
#:
#: 这里**不提供 `all`**：如果接受 `all` 却按 OR 执行，用户选「全部匹配」时
#: 实际拿到的是更宽的集合 —— 筛选被**放宽**了，而筛选条件是模板的业务契约
#: （"发布后用户只能选择符合当前筛选的素材"）。宁可少一个选项，也不能给一个
#: 说了不兑现的开关。真要「同时挂两个标签」的业务语义，那是资产库筛选口径
#: 的改动，不该由本模块偷偷另立一套。
TagMatch = Literal["any"]

_TOKEN_RE = re.compile(r"\{\{Mixed ([1-9]\d*)\}\}")


@dataclass(frozen=True, slots=True)
class FilteredSlot:
    """一条已解析的筛选条件。"""

    filter_kind: str
    filter_tag_ids: tuple[str, ...]
    filter_tag_match: str

    @property
    def wants_tags(self) -> bool:
        return bool(self.filter_tag_ids)


def token_position(token: str) -> int:
    """`{{Mixed 3}}` → 3。

    只认完整形态。提示词里出现半截的 `{{Mixed` 或 `Mixed 3`（不带花括号）
    都不算槽位引用 —— 前者多半是作者还没写完，后者是给读者看的口语称呼。
    """
    match = _TOKEN_RE.fullmatch(token.strip())
    if match is None:
        raise BadRequest(f"槽位标识必须是 {{{{Mixed N}}}} 形式：{token!r}")
    return int(match.group(1))


def cited_tokens(prompt: str) -> list[int]:
    """提示词里引用到的槽位编号，按出现顺序，去重。

    顺序即作者书写顺序，**不重排**。编号的语义由槽位表决定
    （`position` → `{{Mixed N}}`），提示词只负责「引用了哪几个」。
    """
    seen: list[int] = []
    for match in _TOKEN_RE.finditer(prompt):
        number = int(match.group(1))
        if number not in seen:
            seen.append(number)
    return seen


def parse_filtered_slot(
    *,
    filter_kind: str | None,
    filter_tag_ids: list[str],
    filter_tag_match: str,
) -> FilteredSlot:
    """校验并归一化一条筛选条件。

    几处刻意的收窄：

    - `filter_kind` **必填**。允许「不限类别」会让用户在选择器里翻遍整个资产库
      才发现没有一个能用——筛选槽位的意义就是缩小范围。
    - 标签 id **去重**。同一个标签写两遍不改变结果，但会让「这条筛选条件
      是什么」在两个界面上显示得不一样。
    - `filter_tag_match` **只能是 `any`**。理由见 `TagMatch` 的注释：
      `all` 没有对应的执行口径，接受它等于承诺一个兑现不了的筛选。

    标签**存在性**与**是否一级**不在这里校验：那需要查资产库的标签表，
    而业务模块之间不许互相 import（`tests/test_modules_do_not_import_each_other`）。
    校验放在 service 层，经 `core/ports/` 的通道查。
    """
    kind = (filter_kind or "").strip()
    if kind not in FILTERABLE_KINDS:
        allowed = "、".join(sorted(FILTERABLE_KINDS))
        raise BadRequest(f"筛选素材类别必须是 {allowed} 之一：{kind!r}")
    if filter_tag_match != "any":
        raise BadRequest("标签组合方式只能是 any")
    tags = tuple(dict.fromkeys(tag_id for tag_id in filter_tag_ids if tag_id))
    return FilteredSlot(
        filter_kind=kind,
        filter_tag_ids=tags,
        filter_tag_match="any",
    )
