"""生成配方与血缘引用（M3-A）。

## 为什么配方是一张独立的表

一条素材可能被复制到三个库（三条 `assets` 行），但它们说的是**同一次生成事件**。
配方是事实，归属是视角：事实存一份、视角存三份。把配方拍平进 `assets` 之后，
复制就变成"复制一份配方"，之后有人补录了原件的配方字段，三份副本开始互相矛盾
——血缘一旦分叉就不再是证据。

## 为什么参考图同时存在两处

| 位置 | 用途 | 能不能反查 |
| --- | --- | --- |
| `generation_recipes.reference_asset_ids` | 叫模型用的**完整 ID 列表** | 不能，只作复现依据 |
| `asset_refs` | 同一批 ID 加上角色与顺序，落成行 | 能，「被哪些生成用过」走索引 |

两者不是冗余：前者是"当时提交给供应商的输入"，后者是"我们能反查的关系"。
只留 JSON，反向查血缘要全表扫；只留关联表，一条被清理过的引用会让"当时的输入"
永远少一张，而那恰好是复现失败时最需要的信息。

## 为什么配方只增不改

配方是合规与复现依据。允许编辑就等于允许事后修改"当时用了什么模型、写了什么
提示词"，那它连审计价值都没有了。需要修正时补录新配方（`parent_recipe_id`
串起衍生链），旧的原样保留。**没有任何更新接口**，`test_asset_recipes.py`
有一条守卫扫描路由表来保证这件事。

## 与 `assets.source` 的区别

`source` 是"哪条流水线登记了它"（目前恒为 `archive`），`origin` 是
"这份内容怎么来的"（`upload` / `generate`）。两个问题不同，所以是两列。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, UUIDMixin, utcnow

__all__ = ["AssetRef", "GenerationRecipe", "REFERENCE_ROLES"]

#: 参考图在配方里的角色。与模板槽位、主体成员的 `role` 不是同一个词表：
#: 这是"模型把这张图放在哪个位置"（首帧/尾帧/风格/蒙版），那是一张图在业务上是谁。
REFERENCE_ROLES = ("reference", "first_frame", "last_frame", "style", "mask")


class GenerationRecipe(Base, UUIDMixin):
    """一次生成事件的完整记录。不可变（只有插入路径）。"""

    __tablename__ = "generation_recipes"

    # 模型：版本必须记**实际调用的**版本，不是"最新"——否则复现会漂移
    model: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    provider: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    # 提示词全文入库存 Text，出参只给摘要（列表里塞一整段提示词会把响应撑大）
    prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    negative_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    prompt_language: Mapped[str] = mapped_column(String(8), default="zh", nullable=False)

    # 结构化参数（尺寸/步数/CFG/时长/帧率）。键名不做白名单校验：
    # 供应商的参数集一直在变，白名单只会让新参数在入库时被静默丢掉
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    # 种子：`seed` 是**实际生效值**，`seed_requested` 是用户提交值。
    # 随机种子时两者不同，复现必须靠前者——只存"随机"等于存不了。
    seed: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    seed_requested: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    # 出处：任务 / 模板 / 主体版本。由「模板 × 主体」产出的必须记全，
    # 否则回答不了"这批成片是哪套流程、哪个版本的产品图产的"
    task_id: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    template_id: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    template_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    subject_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # AIGC 标识：`{"aigc": true, "labelVersion": "v1", "generator": "..."}`。
    # 只做元数据留痕（导出时写进文件元数据），画面上不加可见水印
    content_label: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    # 衍生链。为空表示"这次不是从既有素材衍生的"
    parent_recipe_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 冻结的输入 ID 列表（复现依据）；反查走 `asset_refs`
    reference_asset_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    cost_units: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_by: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        # 「我的生成历史」：按人 + 时间倒序
        Index("ix_generation_recipes_owner", "created_by", "created_at"),
        # 衍生链向上走：给定一条配方，找它的父
        Index("ix_generation_recipes_parent", "parent_recipe_id"),
        # 任务详情里的「这个任务产出了什么」
        Index("ix_generation_recipes_task", "task_id"),
        # 模板版本对账：某一版模板产出的全部结果
        Index("ix_generation_recipes_template", "template_id", "template_version"),
    )


class AssetRef(Base, UUIDMixin):
    """配方用到的一张参考图。**关联表而不是 JSON 数组**。

    两个反查必须走索引：`这条素材被哪些生成用过`（删除保护、血缘展示、衍生推荐）
    和 `这次生成用了哪些素材`（详情浮层）。JSON 数组只能正查。

    `role` 与 `sort_order` 是这里存在的第二个理由：它们区分"用作首帧"和"用作风格
    参考"，而 JSON 里只能塞对象数组——那又退化成不可索引。
    """

    __tablename__ = "asset_refs"

    recipe_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    asset_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(24), default="reference", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (
        # 同一次生成里同一张图只算一条：重复会让"用了 3 张参考图"变成 5 条，
        # 而提示词里只有一个 reference-1
        UniqueConstraint("recipe_id", "asset_id", name="uq_asset_refs_pair"),
        # 反查的主形状：`WHERE asset_id = ?` 然后拿 recipe_id
        Index("ix_asset_refs_asset", "asset_id", "recipe_id"),
    )
