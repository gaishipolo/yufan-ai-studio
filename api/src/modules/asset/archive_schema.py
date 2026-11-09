"""素材归档的 HTTP 契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from core.media import MediaCategory, MediaFile
from core.schema import CamelModel, UtcDateTime

# 列表筛选：声明成 Literal 而不是裸 str，非法值由框架回 422，
# 也让契约里带上取值域（前端 `ArchiveSearch` 仍手写窄类型，二者需保持一致）。
ArchiveCategory = Literal["all", "source", "character", "product", "scene", "audio"]
#: 列表的可见域。`team` 是 M1 引入的第四个域：库是权限边界，所以它不是
#: "mine 的子集"——团队库里的素材 owner 可能是别人，`mine` 永远看不到它们。
ArchiveScope = Literal["mine", "public", "trash", "team"]
ArchiveState = Literal["all", "private", "shared"]


class ArchivedAsset(MediaFile):
    owner_id: str = ""
    shared: bool = False
    shared_at: UtcDateTime | None = None
    deleted_at: UtcDateTime | None = None
    state_version: int = 0
    editable: bool = False
    download_allowed: bool = True

    # ------------------------------------------------------------------ 生成与候选（M3）
    #: `upload` / `generate`。**不可改**：它就是"这份内容是不是 AI 产物"的事实
    origin: str = "upload"
    #: `candidate` / `adopted` / `discarded` / `expired`。列表里只会有 `adopted`——
    #: 候选走 `GET /assets/candidates`，因为它是待办而不是分类
    stage: str = "adopted"
    #: 生成型必有。为空 = 上传件，详情浮层的"生成血缘"一整段都不显示
    recipe_id: str = ""
    #: 合规四档。上传件恒为 `approved`（B2 只审 AI 产物）
    compliance_status: str = "approved"
    adopted_at: UtcDateTime | None = None
    #: 分类树上的位置。`category`（旧枚举）与它不是一回事：前者是业务类型，
    #: 后者是"在这棵树的哪个节点"。采纳一个候选时要能看到它落进了哪个分类，
    #: 只给旧枚举的话，界面上永远是那个固定的中文名
    category_id: str = ""


class AssetQuota(CamelModel):
    """账面与物理两组数字。

    为什么不塞成一个大整数：这四个数回答的是**四个不同的问题**，合并任何两个
    都会制造无法解释的现象——「删了 1.4G 空间没变」（回收站）、
    「我什么都没传空间却少了」（草稿过期）、「复制一次额度翻倍」（没去重）。
    分开显示，每条差异都有名字；合成一个，用户只能得出「这东西坏了」。
    """

    # 个人库：按 sha256 去重后的未删除素材
    personal_used: int = 0
    personal_quota: int = 0
    # 团队库：同口径，但计费主体是团队，不记在提交者头上
    team_used: int = 0
    team_quota: int = 0
    # 草稿桶：占真实空间，单独显示（藏起来会让「删了没变」成为谜）
    draft_used: int = 0
    # 回收站：账面已释放、字节还在
    trash_used: int = 0
    # 物理：所有仍被引用的 blob 之和，与上面任何一项都不相等
    physical_used: int = 0
    physical_quota: int = 0


class ArchiveList(CamelModel):
    """列表出参。

    **没有 `root`**：那是本地归档根的绝对路径，远端驱动下根本不存在，
    前端也早已不再展示（原来「归档位置」那一行在切 S3 后恒为空）。
    要位置信息就用 `storageKey`，它才是与驱动无关的那一层。
    """

    items: list[ArchivedAsset]
    total: int
    used_bytes: int
    # 兼容旧前端：等于 `quota.personalQuota` / `quota.personalUsed`。
    # 新前端应当读 `quota`——单凭这两个数解释不了回收站与草稿的差异。
    quota_bytes: int = 0
    owner_used_bytes: int = 0
    quota: AssetQuota = Field(default_factory=AssetQuota)
    page: int = 1
    page_size: int = 200


class ArchiveChange(CamelModel):
    expected_version: int = Field(ge=0)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    shared: bool | None = None
    deleted: bool | None = None


class ImportAsset(CamelModel):
    relative_path: str = Field(min_length=1, max_length=500)
    category: MediaCategory
