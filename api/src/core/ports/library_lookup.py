"""资产库 v2 的只读注入口：按 id 批量取资产元数据。

与 `core/ports/media.py`（旧素材库）是**两套独立的对象**，不要合并：

- `core.ports.media` 回答的是 `assets` 表里的旧素材（模板库 prepare 用它）；
- 这个文件回答的是 `library_assets` 表里的 v2 资产（草稿 / 小组 / 团队 / 回收站）。

并存期两个库同时在线，调用方必须显式选择问哪一个。合并成一个「素材查询」
会让「模板库拿到的到底是旧素材还是 v2 资产」变成隐式行为，切换那天才发现
某一侧悄悄读到了另一半的数据。

## 为什么 fail-closed

与 `media` 的理由相同：未注册时抛 `Unavailable`(503)，不返回空映射。调用方
拿到空映射会报「资产不存在」，把装配故障说成用户数据问题，排查方向被带偏。
这是 `api/ports.py` 里「未注册必须返回中性值」的刻意例外，已在 `PORT_CATALOG`
的 `when_unregistered` 里写明。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.exceptions import Unavailable
from core.ports.host import Port

__all__ = [
    "LibraryAssetRef",
    "LibraryAssetState",
    "LibraryAssetKind",
    "LibraryTextRef",
    "LibraryVisibility",
    "LibraryLookup",
    "get_library_lookup",
    "has_library_lookup",
    "register_library_lookup",
    "reset_library_lookup",
]


class LibraryAssetKind(StrEnum):
    """资产类型。

    `text` 是第 5 种「文件型资产」：`.txt` / `.md`，正文即文件内容，
    不是另起一张文本表。它有版本历史（`library_text_versions`），
    但去重、权限、可见性、回收站与取件都和前四种走同一套。
    """

    VIDEO = "video"
    AUDIO = "audio"
    IMAGE = "image"
    TEXT = "text"
    #: 封装：一行**没有自己字节**的资产，成员在 `library_pack_items` 里。
    #:
    #: 2026-11-16 下线过一次，2026-11-19 重新引入（模板里「同一产品多个视角」
    #: 要一次选完）。它进这个枚举而不是另成一个概念，是因为列表、标签、回收站、
    #: 权限都要按「它是一条资产」来处理；但它**不是**槽位类型 ——
    #: `SLOT_TYPES` 里没有 pack，取件路径也不为它开口子（没有 storage_key）。
    PACK = "pack"


class LibraryAssetState(StrEnum):
    """生命周期状态。与 `visibility` 正交，别把草稿/回收站塞进可见性。"""

    DRAFT = "draft"
    ACTIVE = "active"
    TRASH = "trash"


class LibraryVisibility(StrEnum):
    """可见范围。`personal` 是唯一允许草稿与回收站存在的范围。"""

    PERSONAL = "personal"
    GROUP = "group"
    TEAM = "team"


@dataclass(frozen=True, slots=True)
class LibraryAssetRef:
    """跨模块只读的资产快照。

    刻意是**扁平的、与 ORM 解耦的**：消费方（模板、任务、选择器）不该拿到
    一个带 lazy 关系的 ORM 对象，否则它会在别人的 session 上触发查询。
    """

    id: str
    owner_id: str
    kind: LibraryAssetKind
    name: str
    state: LibraryAssetState
    visibility: LibraryVisibility
    mime_type: str = ""
    storage_key: str = ""
    #: 我们托管的缩略图 key（音轨 / 文本没有）。模板包内预览优先用它：
    #: 列表页一次几十张图，拿原件当缩略图是纯浪费带宽。空串表示没有缩略图。
    cover_key: str = ""
    size_bytes: int = 0
    # 内容寻址的摘要。`library_assets.sha256` 允许为空（历史行 / 未探测完），
    # 消费方必须能自己算，不能假设它一定有值。
    sha256: str = ""
    # 宽高时长是**生成请求体**的一部分（执行器按文件拼 `media-inputs.json`），
    # 缺了只能传 0，模型侧会把图当未知尺寸。所以和 `duration_ms` 一起给出来。
    width: int = 0
    height: int = 0
    duration_ms: int | None = None
    has_audio: bool = False


@dataclass(frozen=True, slots=True)
class LibraryTextRef:
    """一条可读文本资产的内容快照，供 `{{Text N}}` 注入用。

    刻意带 `version` / `sha256`：注入是把内容**抄进**生成的提示词，
    之后资产可能被编辑。生成档案必须能回答「当时用的是哪一版正文」，
    只存 assetId 会在用户改完稿后变成不可复现的历史。

    `content` 是已经严格 UTF-8 解码、去掉 BOM 的纯文本；调用方不得把它
    当 HTML 渲染，也不得二次解析其中的 `{{...}}`（见 `core.prompt_refs`）。
    """

    id: str
    name: str
    version: int
    sha256: str
    content: str
    size_bytes: int


@runtime_checkable
class LibraryLookup(Protocol):
    """按 id 批量取 v2 资产元数据，并回答「这批 id 里哪些我能读」。"""

    async def get_many(
        self, session: AsyncSession, asset_ids: Sequence[str]
    ) -> Mapping[str, LibraryAssetRef]:
        """返回 `{asset_id: LibraryAssetRef}`，不存在的 id 不出现在结果里。

        不要为缺失 id 造占位对象：调用方靠「键是否存在」区分「已删除」与
        「还没上传完」。`session` 由调用方传入，保证与业务读取同一个快照。
        """
        ...

    async def readable_ids(
        self, session: AsyncSession, actor: CurrentUser, asset_ids: Sequence[str]
    ) -> set[str]:
        """这批 id 里，`actor` **现在**还能读的那些。

        为什么元数据之外还要一个独立判定：`LibraryAssetRef` 里没有「共享到哪些
        小组」，只凭 `visibility=group` 就放行会把**别人组里**的素材判成可见。
        判据只有 `modules/library/access.py` 一份（含「封装成员」这条读入口），
        这里刻意只做转发，不在别的模块里重写一遍——重写必然漂移，而漂移的方向
        总是「多给权限」，不会有人报错。

        调用方（生成链路把素材当参考图）必须过这一关：能读到 id 不等于能读到
        内容以外的用途，但**读不到内容却能把图交给供应商、再取回产物**，等于
        绕过整个共享审批把别人的私有素材复制出来。
        """
        ...

    async def read_text_many(
        self, session: AsyncSession, actor: CurrentUser, asset_ids: Sequence[str]
    ) -> Mapping[str, LibraryTextRef]:
        """批量读文本正文，**只返回 actor 现在可读的 `active` / `text` 条目**。

        为什么单独一个方法而不塞进 `get_many()`：正文是字节（可能 1MB），
        列表 / 溯源查询只需要元数据，带上正文会让 24 条列表白读 24 份文件。
        可读性判据仍只有 `library/access.py` 一份——正文比缩略图更敏感，
        这里不做第二套「共享」判断，也不接受「查不到就当没引用」。

        返回顺序**不保证**与入参一致：调用方按 id 取，顺序由自己的列表决定。
        """
        ...

    async def find_ids(
        self,
        session: AsyncSession,
        actor: CurrentUser,
        *,
        kind: LibraryAssetKind | None = None,
        tag_ids: Sequence[str] = (),
        owner_id: str | None = None,
        limit: int = 50,
    ) -> list[str]:
        """按条件列 id：**只要 actor 现在可读、且已采纳（`active`）的那批**。

        给演示数据（`seed.py`）与旧库导入用。它们原来的做法是裸 SQL
        `SELECT id FROM assets WHERE category = ...`——v2 没有 `category`，
        也不该在模板模块里再写一条 SQL 去猜「哪个标签相当于产品图」。

        判据与选择器接口（`GET /library/selection`）同口径：`state=active` 硬过滤、
        标签多选按 OR 展开到二级。草稿与回收站永远不出现——调用方忘了传就会把
        用户删掉的东西塞进演示数据或历史任务。

        `owner_id` 不是筛选糖，是**确定性开关**：可读性是逐行判的（要查小组），
        没法在一条 SQL 里表达，所以实现只能「先取一批候选再过滤」。不给 owner 时
        「取到多少条」取决于库里别人有多少资产——演示数据要的是「就是这几张」，
        必须把范围收死。
        """
        ...


_lookup: Port[LibraryLookup] = Port("library-lookup", multi=False)


def register_library_lookup(port: LibraryLookup) -> LibraryLookup:
    """资产库模块 import 期调用；重复注册（不同实例）直接报错。"""
    return _lookup.provide(port, owner="modules.library")


def has_library_lookup() -> bool:
    return _lookup.has()


def get_library_lookup() -> LibraryLookup:
    impl = _lookup.single()
    if impl is None:
        raise Unavailable(
            "资产库查询不可用：没有任何模块注册 LibraryLookup。"
            "通常是启动时漏了 `discover_modules()`。"
        )
    return impl


def reset_library_lookup() -> None:
    """仅测试用。"""
    _lookup.reset()
