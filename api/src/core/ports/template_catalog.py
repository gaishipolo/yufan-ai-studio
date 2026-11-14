"""模板封装目录的对外只读注入口。

## 为什么是只读、为什么这么窄

用户拍板的分工：**封装模块只负责把封装数据管好、通过 ports 对外提供**；
生成是 `modules/task` 的消费行为，不在封装模块里实现。所以这里不提供
「创建 / 发布 / 选素材」这类动作，只回答「已发布的封装数据长什么样」。

旧的 `core.ports.template_pack` 是另一套东西（带筛选约束的封装模板，暴露
`candidates` / `verify_picks` 这类**判定动作**）。本端口刻意不合并进去：
判定动作会把消费方的业务规则吸进端口，而目录只该是数据源。两者下线时间
也不同（旧的那套随阶段 C2 一起删）。

## 未注册时的行为

返回空结果，不抛异常。「库里还没有任何已发布封装」是合法状态；
抛异常会把「模块没装配」和「用户没配置」混成一件事，排查方向被带偏。
与 `library-lookup` 的 fail-closed 刻意不同：那边缺了会静默把别人的私有
素材判成不存在，方向是多给权限；这边缺了只是少看见。

## 快照为什么用 dataclass 而不是 ORM 行

消费方拿到 ORM 行会在别人的 session 上触发 lazy 查询，还会把表结构泄漏到
模块外。这里给扁平、不可变的 core 层类型：改一列不用改所有查询方。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from core.ports.host import Port

__all__ = [
    "CatalogPack",
    "CatalogReference",
    "CatalogTemplate",
    "TemplateCatalogPort",
    "get_template_catalog",
    "has_template_catalog",
    "register_template_catalog",
    "reset_template_catalog",
]


@dataclass(frozen=True, slots=True)
class CatalogReference:
    """素材包里的一张参考图。`position` 即 `{{Mixed N}}` 的编号依据。"""

    asset_id: str
    ref_key: str
    role: str = ""
    description: str = ""
    position: int = 0


@dataclass(frozen=True, slots=True)
class CatalogPack:
    """一个已发布素材包的只读快照。"""

    id: str
    name: str
    kind: str
    version: int = 0
    required_roles: tuple[str, ...] = ()
    supported_scopes: tuple[str, ...] = ()
    wardrobe_mode: str = "preserve"
    references: tuple[CatalogReference, ...] = ()
    #: 发布回执：这次对外的是哪个包的哪一版。C2 投影到 `video_templates` 时
    #: 用它当锚点，不重新解析配置，也不把「最新一版」误当成「当时那一版」。
    published_pack_id: str = ""
    published_pack_version: int = 0


@dataclass(frozen=True, slots=True)
class CatalogTemplate:
    """一条已发布的工作台卡片（原 packager 的 `functions[]` 产物）。

    `rules` 是编译后的规则字典（`workbenchLabel` / `scopeOptions` /
    `inputMode` / `scenePromptDefault` / `referenceBindings` 等），保持 dict
    而不拆字段：它是**随业务演进的开放集合**，拆成属性会让每加一条规则都要
    改端口、改所有实现者。
    """

    id: str
    template_key: str
    name: str
    kind: str = ""
    prompt: str = ""
    rules: dict[str, Any] = field(default_factory=dict)
    media_order: tuple[str, ...] = ()
    bindings: dict[str, str] = field(default_factory=dict)
    scope_options: tuple[str, ...] = ()
    aspect_ratio: str = "9:16"
    duration: int | None = None
    source_video_asset_id: str = ""
    pack_id: str = ""
    pack_version: int = 0
    published_at: datetime | None = None
    #: 发布回执：发布时冻结的配置行 id。C2 同模块直写投影后，这里换成
    #: `video_templates.id`；消费方只认「一个能点开的 id」，不用跟着改。
    published_template_id: str = ""


@runtime_checkable
class TemplateCatalogPort(Protocol):
    """对下游暴露的窄接口：只列 / 取已发布数据。"""

    async def list_templates(
        self, session: AsyncSession, *, kind: str = ""
    ) -> list[CatalogTemplate]:
        """已发布的卡片。按 `kind` 可选过滤；草稿与停用一律不返回。"""
        ...

    async def get_template(
        self, session: AsyncSession, template_id: str
    ) -> CatalogTemplate | None:
        """按 id 取一条已发布卡片。未发布 / 不存在都返回 `None`（正常分支）。"""
        ...

    async def get_pack(
        self, session: AsyncSession, pack_id: str, version: int | None = None
    ) -> CatalogPack | None:
        """取素材包快照。`version=None` 取最新已发布版本。

        返回 `None` 而不是抛 404：调用方多半在「找一条能用的包」，
        「没有」是正常分支。
        """
        ...


_catalog: Port[TemplateCatalogPort] = Port("template-catalog", multi=False)


def register_template_catalog(port: TemplateCatalogPort) -> TemplateCatalogPort:
    """封装模块 import 期调用；重复注册（不同实例）直接报错。"""
    return _catalog.provide(port, owner="modules.template_pack")


def has_template_catalog() -> bool:
    return _catalog.has()


def get_template_catalog() -> TemplateCatalogPort | None:
    """取实现。未注册时返回 `None`，见模块文档「未注册时的行为」。"""
    return _catalog.single()


def reset_template_catalog() -> None:
    """仅测试用。"""
    _catalog.reset()
