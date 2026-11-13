"""封装模板的对外注入口：供其他模块取用已发布的封装模板。

## 为什么要有这个口

业务模块之间**禁止互相 import**（`tests/test_modules_do_not_import_each_other`
用 AST 静态检查拦）。但别的模块确实可能需要「拿一条已封装的模板来用」——
比如生成链路想按模板声明的筛选条件去准备素材。

没有这个口，只有两条路，都不好：

- **让消费方 import `modules.template_pack`**：违反架构约束，CI 直接红。
- **让消费方自己写 SQL 查 `template_pack_templates`**：表结构泄漏到模块外，
  改一列要改所有查询方；更糟的是「哪些模板已发布」这条口径会各写各的。

所以这里给一个**窄接口**：只回答「有哪些已发布的封装模板」「这一条的槽位
和提示词是什么」「某个筛选槽位的候选素材有哪些」。

## 未注册时的行为

**返回空结果，不抛异常**。与 `model-generation` 那种 fail-closed 刻意不同：

- `model-generation` 未注册 → 抛 503。理由是「空模型列表」会把装配故障报成
  「用户没配模型」，两者排查方向完全不同。
- 这里未注册 → 「没有已封装的模板」是一个**合法状态**（新装的库里就是空的）。
  抛异常会让「删掉这个模块」变成「别的模块起不来」。

所以方向是「少看见不会多看见」，与 `org-directory` 一致。

## 与 `library-lookup` 的分工

`library-lookup` 回答「这个 v2 资产是什么」；这个口回答「有哪些封装模板」。
两套 id 空间、两套语义，不合并。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.ports.host import Port

__all__ = [
    "TemplatePackCandidate",
    "TemplatePackPort",
    "TemplatePackSlot",
    "TemplatePackSummary",
    "get_template_pack",
    "has_template_pack",
    "register_template_pack",
    "reset_template_pack",
]


@dataclass(frozen=True, slots=True)
class TemplatePackSlot:
    """一个槽位的只读快照。

    与模块内部的出参**刻意同形但独立**：模块里的 `TemplatePackSlotOut` 是
    HTTP 契约（Pydantic，camelCase），这里是 core 层的数据类型（dataclass，
    snake_case）。core 不该依赖上层的数据类型。
    """

    position: int
    token: str
    slot_kind: str
    label: str = ""
    asset_id: str = ""
    media_type: str = "image"
    filter_kind: str = ""
    filter_tag_ids: tuple[str, ...] = ()
    filter_tag_match: str = "any"
    required: bool = True

    @property
    def is_filtered(self) -> bool:
        return self.slot_kind == "filtered"


@dataclass(frozen=True, slots=True)
class TemplatePackSummary:
    """一条已发布封装模板的只读快照。"""

    id: str
    template_key: str
    name: str
    description: str = ""
    prompt: str = ""
    prompt_sha256: str = ""
    owner_id: str = ""
    source_video_asset_id: str = ""
    source_duration_sec: float = 0.0
    aspect_ratio: str = "9:16"
    cover_url: str = ""
    slots: tuple[TemplatePackSlot, ...] = field(default_factory=tuple)
    published_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TemplatePackCandidate:
    """某个筛选槽位里的一条合规素材。"""

    asset_id: str
    name: str = ""
    kind: str = "image"
    cover_url: str = ""


@runtime_checkable
class TemplatePackPort(Protocol):
    """对下游暴露的窄接口。"""

    async def list_published(
        self, session: AsyncSession, *, owner_id: str = ""
    ) -> list[TemplatePackSummary]:
        """已发布的封装模板。`owner_id` 非空时只看某人的。

        **只返回已发布的**：草稿是私有工作区，别的模块不该看见。
        """
        ...

    async def get_published(
        self, session: AsyncSession, pack_id: str
    ) -> TemplatePackSummary | None:
        """按 id 取一条已发布的模板。未发布 / 不存在都返回 `None`。

        返回 `None` 而不是抛 404：调用方多半是「找一条能用的模板」，
        「没有」是正常分支，不该变成异常。
        """
        ...

    async def candidates(
        self,
        session: AsyncSession,
        actor: CurrentUser,
        pack_id: str,
        token: str,
        *,
        limit: int = 100,
    ) -> list[TemplatePackCandidate]:
        """某个筛选槽位的合规素材池（已按可见性收窄）。

        消费方**不要**自己按标签筛一遍：可见性判据在资产库模块里，
        抄一份必然漂移，而漂移的方向总是「多给权限」。
        """
        ...

    async def verify_picks(
        self,
        session: AsyncSession,
        actor: CurrentUser,
        pack_id: str,
        picks: dict[str, str],
    ) -> dict[str, str]:
        """复核用户选的素材是否仍满足筛选条件，不满足则抛 400。

        返回通过复核的 `{token: asset_id}`。
        """
        ...


_pack: Port[TemplatePackPort] = Port("template-pack", multi=False)


def register_template_pack(port: TemplatePackPort) -> TemplatePackPort:
    """封装模板模块 import 期调用；重复注册（不同实例）直接报错。"""
    return _pack.provide(port, owner="modules.template_pack")


def has_template_pack() -> bool:
    return _pack.has()


def get_template_pack() -> TemplatePackPort | None:
    """取实现。**未注册时返回 `None`** —— 见模块文档「未注册时的行为」。"""
    return _pack.single()


def reset_template_pack() -> None:
    """仅测试用。"""
    _pack.reset()
