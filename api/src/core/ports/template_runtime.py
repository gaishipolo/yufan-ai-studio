"""替换执行链读取「封装运行投影」的注入口。

## 与 `template_catalog` 的分工

两者都读封装数据，但读的是**两套不同的东西**：

- `template_catalog`（`core/ports/template_catalog.py`）读**编写侧**：manifest /
  packs / functions，回答「运营配了什么、发布了第几版」。
- 本端口读**运行侧**：`template_packs` + `template_pack_versions` +
  `video_templates`，回答「这次替换要用哪个素材包的第几版、套哪条视频模板」。

合成一个端口会让两类读者互相牵制：编写侧每次改字段都要动运行侧的快照类型，
而运行侧只需要「冻结版本 + 模板原文 + 用途表」这几样不会随编辑界面演进的形状。

## 为什么会被替换链用到、却放在 core

搬迁目标是把这两套表整体搬进 `modules/template_pack`，`modules/template` 只留
替换执行链。搬完之后执行链不能再 `import modules.template_pack`
（`test_modules_do_not_import_each_other` 无豁免），所以它必须通过 core 的端口
拿数据。现在先落契约、实现暂时由仍持有表的 `modules/template` 注册；等表搬过去，
只需把注册点挪过去，消费方一行不用改 —— 这就是绞杀者模式的那一刀。

## 与 `template_catalog` 相反的失败姿态

目录读不到就返回空（「还没配」是合法状态）；这里读不到意味着**替换链跑不起来**，
所以 `require_template_runtime()` 直接抛 `RegistryError`：把「模块没装配」暴露成
500，而不是让每个任务在 prepare 阶段拿到一个「封装不存在」的 400 去误导用户。

## 快照为什么是 dataclass

消费方拿到 ORM 行会在别人的 session 上触发 lazy 查询，还会把表结构泄漏到模块外；
拿到另一个模块的 Pydantic 出参则等于把它的 API 契约变成跨模块契约 —— 那边加一个
必填字段，这边就 ImportError 或验证失败。这里只给扁平、不可变的 core 层类型。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.ports.host import Port
from core.ports.library_lookup import LibraryAssetRef

__all__ = [
    "RuntimePack",
    "RuntimePackVersion",
    "RuntimeTemplate",
    "RuntimeTemplateMedia",
    "TemplateRuntimePort",
    "get_template_runtime",
    "has_template_runtime",
    "register_template_runtime",
    "require_template_runtime",
    "reset_template_runtime",
]


@dataclass(frozen=True, slots=True)
class RuntimePack:
    """素材包的头部信息（不含参考图，参考图在版本行里冻结）。

    `deleted` 用布尔而不是 `deleted_at`：消费方只判「还在不在」，
    暴露时间戳会让它们开始写「多久算过期」这种业务规则。
    """

    id: str
    name: str = ""
    kind: str = ""
    owner_id: str = ""
    status: str = ""
    version: int = 0
    deleted: bool = False


@dataclass(frozen=True, slots=True)
class RuntimePackVersion:
    """一次发布的冻结证据：本次任务实际会用到的那几个字段。

    `reference_ids` 与 `bindings` **按位置对齐**，顺序即 `{{Mixed N}}` 的编号依据；
    两者长度不一致会被下游的 `zip(strict=True)` 当场发现，所以这里不做补齐 ——
    「发布时校验过」是这一行的前提，读的时候再兜底只会掩盖坏数据。
    """

    pack_id: str
    version: int = 0
    reference_ids: tuple[str, ...] = ()
    bindings: tuple[dict[str, Any], ...] = ()
    supported_scopes: tuple[str, ...] = ()
    features: str = ""
    instructions: str = ""
    wardrobe_mode: str = "preserve"


@dataclass(frozen=True, slots=True)
class RuntimeTemplateMedia:
    """视频模板声明的一个媒体槽位；下标 +1 就是 `{{Mixed N}}` 编号。"""

    type: str = "image"
    role: str = ""
    asset_id: str = ""
    in_prompt: bool = False


@dataclass(frozen=True, slots=True)
class RuntimeTemplate:
    """替换链套用视频模板所需的全部字段，**只含执行时会读到的那些**。

    `rules` 保持 dict：它是随业务演进的开放集合（`referenceBindings` /
    `fixedReferences` / `scopeOptions` / `scenePromptDefault`…），拆成属性会让
    每加一条规则都要改端口、改实现、改快照。
    """

    id: str
    name: str = ""
    prompt: str = ""
    prompt_sha256: str = ""
    rules: dict[str, Any] = field(default_factory=dict)
    media_order: tuple[RuntimeTemplateMedia, ...] = ()
    generation: dict[str, Any] = field(default_factory=dict)
    aspect_ratio: str = "9:16"
    default_kind: str = "product"
    pack_id: str = ""
    pack_version: int = 0
    source_video_asset_id: str = ""


@runtime_checkable
class TemplateRuntimePort(Protocol):
    """对替换执行链暴露的窄接口：只读运行投影，不做任何写入。"""

    async def get_pack(self, session: AsyncSession, pack_id: str) -> RuntimePack | None:
        """按 id 取素材包头部。不存在返回 `None`（调用方决定是 404 还是跳过）。"""
        ...

    async def get_pack_version(
        self, session: AsyncSession, pack_id: str, version: int
    ) -> RuntimePackVersion | None:
        """取冻结版本。没发布过这一版返回 `None`。"""
        ...

    async def available_packs(self, session: AsyncSession) -> list[dict[str, Any]]:
        """已发布素材包的**对外投影**（camelCase，交给消费方的出参模型校验）。

        返回 dict 而不是 core 类型：这张卡片的形状是封装模块自己的 API 契约
        （含预览地址、可用性判定），core 再复制一份 16 字段的 dataclass 只会
        让两边逐字段对齐、且每次加字段都要改两处。消费方拿到后用自己的出参模型
        校验一次，脏数据在入口就被挡下。
        """
        ...

    async def find_template_for_run(
        self,
        session: AsyncSession,
        *,
        source_asset_id: str,
        pack_id: str | None = None,
        kind: str | None = None,
        template_id: str | None = None,
    ) -> RuntimeTemplate | None:
        """按原片（或显式模板 id）找要套用的视频模板。

        `template_id` 给了但不存在 / 与原片或类型不匹配时抛 `BadRequest`
        （这是用户在表单里选错了，不是「没有模板」）；没给则按绑定关系找，
        找不到返回 `None` —— 普通原片本来就没有模板。
        """
        ...

    async def template_names(
        self, session: AsyncSession, template_ids: Sequence[str]
    ) -> dict[str, str]:
        """批量反查模板名。只用于给老档案补 `videoTemplateName`，查不到的不出现在结果里。"""
        ...

    async def resolve_fixed_references(
        self,
        session: AsyncSession,
        references: Sequence[Mapping[str, Any]],
        *,
        actor: CurrentUser,
    ) -> dict[str, LibraryAssetRef]:
        """把模板 `rules.fixedReferences` 解析成可用的素材引用，按 `{{Mixed N}}` token 索引。

        入参是模板里原样的 camelCase 字典（`token` / `packId` / `packVersion` /
        `assetId`）：形状校验与「图片确在该发布版本里」的判定都由封装模块负责，
        执行链只需要一份「能取字节」的结果。任何一条不合法都抛 `BadRequest`。
        """
        ...


_runtime: Port[TemplateRuntimePort] = Port("template-runtime", multi=False)


def register_template_runtime(port: TemplateRuntimePort) -> TemplateRuntimePort:
    """持有运行投影表的模块在 import 期调用；重复注册直接报错。"""
    return _runtime.provide(port, owner="modules.template")


def has_template_runtime() -> bool:
    return _runtime.has()


def get_template_runtime() -> TemplateRuntimePort | None:
    return _runtime.single()


def require_template_runtime() -> TemplateRuntimePort:
    """取实现，缺了就是装配错误（见模块文档「与 template_catalog 相反的失败姿态」）。"""
    return _runtime.require()


def reset_template_runtime() -> None:
    """仅测试用。"""
    _runtime.reset()
