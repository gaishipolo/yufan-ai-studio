"""配方的出参与血缘、衍生的请求/响应契约（M3）。

## 出参里的提示词是**全文**

别的列表接口都在截断文本，这里不截：详情浮层的用途就是"把当时的提示词复制出来
再调一次"。截断之后它只剩展示价值，而那段文字恰恰是最该被完整拿走的资产。

## 血缘分三段，不是一棵树

| 段 | 回答的问题 |
| --- | --- |
| `parents` | 我这张图**是怎么来的**（衍生链向上，就近在前） |
| `children` | 从同样的输入**又生出了什么**（同 `parent_recipe_id` 的兄弟） |
| `usedBy` | 谁**在用它**（别人的配方引用了它 / 主体把它当成员） |

合成一棵树会让人以为"血缘是一棵有根的树"，而实际情况是有向无环图：
一张图可以被 20 次生成用过，那 20 个结果之间没有父子关系。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from core.schema import CamelModel, UtcDateTime

#: 一键衍生的三个入口。前两个只回填生成页的输入位、不产生素材；
#: 第三个把配方里的提示词取出来给编辑器（也不产生素材）。
DeriveMode = Literal["image_ref", "video_first_frame", "prompt_extract"]


class RecipeOut(CamelModel):
    id: str
    model: str = ""
    model_version: str = ""
    provider: str = ""
    prompt: str = ""
    negative_prompt: str = ""
    prompt_language: str = "zh"
    params: dict[str, Any] = Field(default_factory=dict)
    seed: str = ""
    seed_requested: str = ""
    task_id: str = ""
    template_id: str = ""
    template_version: int = 0
    subject_version: int = 0
    content_label: dict[str, Any] = Field(default_factory=dict)
    parent_recipe_id: str | None = None
    reference_asset_ids: list[str] = Field(default_factory=list)
    cost_units: float = 0.0
    created_by: str = ""
    created_at: UtcDateTime | None = None


class AssetRefOut(CamelModel):
    """配方里的一张参考图。带名字与缩略图——详情浮层要能直接点开。"""

    asset_id: str
    role: str = "reference"
    sort_order: int = 0
    name: str = ""
    preview_url: str | None = None


class LineageNode(CamelModel):
    """血缘链上的一个节点：一条配方 + 它产出的那张素材。"""

    recipe: RecipeOut
    asset_id: str = ""
    name: str = ""
    preview_url: str | None = None


class AssetUsage(CamelModel):
    """「谁在用它」的一条记录。

    两种消费者分开标：`asset` 是别的配方引用了它（生成血缘），
    `subject` 是某个主体把它当成员（业务装配）。合并成一种会让"这张图能不能删"
    的答案少一半——两者各自都会阻止删除。
    """

    kind: Literal["asset", "subject"]
    id: str
    name: str = ""


class LineageOut(CamelModel):
    asset_id: str
    #: 为空 = 这张素材不是生成产物（上传件），血缘为空不是错误
    recipe: RecipeOut | None = None
    references: list[AssetRefOut] = Field(default_factory=list)
    parents: list[LineageNode] = Field(default_factory=list)
    children: list[LineageNode] = Field(default_factory=list)
    used_by: list[AssetUsage] = Field(default_factory=list)


class DeriveRequest(CamelModel):
    mode: DeriveMode


class DeriveIntent(CamelModel):
    """「照这个素材再生成一次」所需的全部输入。

    只回填输入位、**不落库**：真正发起生成走的是生成任务自己的接口（它有额度、
    模型选择、参数校验）。在这里顺手建一个任务会让"点了衍生"变成一次扣费。
    """

    mode: str
    asset_id: str
    prompt: str = ""
    reference_asset_ids: list[str] = Field(default_factory=list)
    reference_role: str = ""
    source_recipe_id: str | None = None
