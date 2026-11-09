"""候选确认区的 HTTP 契约（M3）。

候选是**待办**，不是分类：它不进 `GET /assets`，只在顶部横幅里按批出现。
所以这里的出参不是「一页素材」，而是「几批待确认的产物」——分组信息（这批是
什么、什么时候过期）必须跟素材一起回来，否则前端要按 `recipeId` 再查一次。
"""

from __future__ import annotations

from pydantic import Field

from core.schema import CamelModel, UtcDateTime
from modules.asset.archive_schema import ArchivedAsset


class AdoptRequest(CamelModel):
    """采纳一个候选。

    `category_id` 为空时落到个人库的「未分类收件箱」——采纳是一个高频、
    低摩擦的动作，逼用户先选分类会让"待办"越积越多。
    """

    category_id: str = Field(default="", max_length=32)
    #: 可选标签，采纳时一并贴上（与 `PUT /assets/{id}/tags` 同一个词表校验）
    tag_ids: list[str] = Field(default_factory=list, max_length=20)


class BatchAdoptRequest(CamelModel):
    """整组采纳。**全成或全不成**：一次事务，任一条不可采纳就整批拒绝。

    半成功是这里最坏的形态——前端要记住哪几条成功、失败的重试会不会重复，
    而用户看到的是"选了 8 张，成了 5 张"，且不知道差在哪。
    """

    asset_ids: list[str] = Field(min_length=1, max_length=32)
    category_id: str = Field(default="", max_length=32)
    tag_ids: list[str] = Field(default_factory=list, max_length=20)


class BatchAdoptResult(CamelModel):
    items: list[ArchivedAsset]
    #: 已经采纳过的（重试时返回它，不算失败）
    already: int = 0


class CandidateGroup(CamelModel):
    """一批同源候选（同一 `recipe_id`）。

    `expires_at` 取组内**最早**的那个：一批里但凡有一张到期，这一组就该消失，
    否则会出现"确认界面里还留着 3 张已经过期的"。
    """

    recipe_id: str = ""
    #: 这批是什么（模型 + 尺寸 / 数量），横幅上只显示这一行
    summary: str = ""
    #: 提示词摘要，便于"这批是刚才调的哪一版"
    prompt_summary: str = ""
    created_at: UtcDateTime | None = None
    expires_at: UtcDateTime | None = None
    items: list[ArchivedAsset] = Field(default_factory=list)


class CandidateList(CamelModel):
    groups: list[CandidateGroup] = Field(default_factory=list)
    total: int = 0
