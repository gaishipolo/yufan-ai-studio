"""资产库 v2 出入参（字段用 snake_case 写，序列化自动转 camelCase）。

M0 只定「列表 + 分面」这一组，因为它是五个一级入口共用的壳。详情、封装、
共享申请、下载任务等出入参在 M2/M3 建表时随实现一起加——**先定壳、后定肉**，
避免 M0 凭空写一批没实现支撑的字段，M1 建表时又全改一遍。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from core.ports.library_lookup import (
    LibraryAssetKind,
    LibraryAssetState,
    LibraryVisibility,
)
from core.response import Page, PageParams
from core.schema import CamelModel, UtcDateTime

__all__ = [
    "AssetPatchIn",
    "AssetSort",
    "BulkAction",
    "BulkIn",
    "BulkItemResult",
    "BulkOut",
    "DraftDecisionIn",
    "GroupLeadersIn",
    "LibraryAssetDetailOut",
    "LibraryAssetOut",
    "LibraryAssetPage",
    "LibraryFacets",
    "LibraryGenerationOut",
    "LibraryGroupOut",
    "LibraryGroupRef",
    "LibrarySelectionPage",
    "LibraryTab",
    "LibraryTagOut",
    "ShareAction",
    "ShareDecisionIn",
    "ShareRequestIn",
    "ShareRequestOut",
    "ShareTarget",
    "TagIn",
    "UploadKind",
]


class LibraryTab(StrEnum):
    """列表的可见范围（对应侧栏一级入口的个人/小组/团队/全部）。

    草稿箱与回收站不在这里：它们是 `state` 的两个值，与可见范围正交。
    把「草稿」做成 `library=draft` 会让「草稿属于谁」变成一个可见范围问题，
    而实际不变式是「草稿必须是 personal」。
    """

    ALL = "all"
    PERSONAL = "personal"
    GROUP = "group"
    TEAM = "team"


class AssetSort(StrEnum):
    """排序键。默认按创建时间倒序。"""

    CREATED_DESC = "-createdAt"
    CREATED_ASC = "createdAt"
    NAME = "name"
    SIZE_DESC = "-sizeBytes"


class LibraryFacets(CamelModel):
    """分面统计：五个一级入口的条数，前端直接渲染成侧栏角标。

    与 `items` 同一次查询算出，不额外发起请求——否则切换 tab 时角标会跳。
    """

    all: int = 0
    personal: int = 0
    group: int = 0
    team: int = 0
    drafts: int = 0
    trash: int = 0


class LibraryAssetOut(CamelModel):
    """列表项。字段对齐 `library_assets` 表，M1 建表后不再改形状。"""

    id: str
    name: str
    kind: LibraryAssetKind
    origin: str = Field(description="upload / generated / template_source / imported")
    mime_type: str | None = None
    size_bytes: int = 0
    duration_sec: float | None = None
    has_audio: bool = False
    owner_id: str
    owner_name: str | None = None
    visibility: LibraryVisibility
    state: LibraryAssetState
    state_version: int = 1
    cover_url: str | None = None
    group_ids: list[str] = Field(default_factory=list)
    # 回收站专用的两个字段：前端据此显示「剩余 N 天」与「因何被删」。
    # 在库里时为 null，不要在响应里省略——省略会让前端多一层「有没有这个键」的判断。
    trash_reason: str | None = None
    trash_expires_at: UtcDateTime | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime | None = None


class LibraryAssetPage(Page[LibraryAssetOut]):
    """列表响应 = 标准分页 + 分面统计。

    继承 `Page` 而不是复制 `items/total/page/pageSize`：前端的通用分页组件
    认的是那四个字段，复制一遍迟早出现「这个页面 pageSize 拼错成 page_size」。
    """

    facets: LibraryFacets = Field(default_factory=LibraryFacets)

    @classmethod
    def empty(
        cls, params: PageParams, facets: LibraryFacets | None = None
    ) -> LibraryAssetPage:
        """空页。父类的 `create()` 返回 `Page[T]`，泛型子类的额外字段会被丢掉，
        所以这里显式构造——M0 骨架与真实空库都用这条路径。"""
        return cls(
            items=[],
            total=0,
            page=params.page,
            page_size=params.page_size,
            facets=facets or LibraryFacets(),
        )


class UploadKind(StrEnum):
    """上传接口允许的媒体类型。

    分成三个值而不是复用 `LibraryAssetKind`：`pack` 不是上传出来的，
    它是封装动作的产物；让上传接口收 `pack` 只会得到一个没有字节的资产行。
    """

    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"


class LibraryTagOut(CamelModel):
    id: str
    name: str


class LibraryGenerationOut(CamelModel):
    """生成溯源快照。入库那一刻冻结，不联表查；缺失字段前端显示「未记录」。"""

    source_module: str
    source_task_id: str
    model: str = ""
    model_id: str = ""
    prompt: str = ""
    size: str = ""
    count: int = 0
    duration_sec: float = 0.0
    aspect_ratio: str = ""
    style_preset: str = ""
    seed: str = ""
    reference_asset_ids: list[str] = Field(default_factory=list)
    pack_id: str = ""
    pack_version: int = 0
    prompt_spec: dict = Field(default_factory=dict)
    configuration: dict = Field(default_factory=dict)
    external_id: str = ""
    credits: float = 0.0
    generated_at: UtcDateTime | None = None


class LibraryAssetDetailOut(LibraryAssetOut):
    """详情 = 列表项 + 标签 + 生成参数（有才返回）。

    生成参数用 `None` 而不是空对象：前端要区分「不是生成来的」与
    「是生成来的但历史数据没记全」，两者显示逻辑不同。
    """

    tags: list[LibraryTagOut] = Field(default_factory=list)
    groups: list[LibraryGroupRef] = Field(default_factory=list)
    generation: LibraryGenerationOut | None = None


# --------------------------------------------------------------------------- #
# M2：可见性、共享、生命周期、标签、批量
# --------------------------------------------------------------------------- #
class LibraryGroupRef(CamelModel):
    """资产共享到的小组。名称走组织目录注入口，不在本模块 join 身份的表。"""

    id: str
    name: str = ""


class LibraryGroupOut(CamelModel):
    """我的小组。`isLeader` 决定「待我审核」里会不会出现该组的申请。"""

    id: str
    name: str = ""
    is_leader: bool = False


class AssetPatchIn(CamelModel):
    """改名 / 换标签。**不含可见性**：共享必须走申请单，否则审批就没有意义了。"""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    tag_ids: list[str] | None = None
    expected_version: int = Field(
        default=0, description=">0 时做乐观锁校验；0 表示不校验（兼容单页操作）"
    )


class TagIn(CamelModel):
    name: str = Field(min_length=1, max_length=40)


class ShareAction(StrEnum):
    SHARE = "share"
    REVOKE = "revoke"


class ShareTarget(StrEnum):
    GROUP = "group"
    TEAM = "team"


class ShareRequestIn(CamelModel):
    """发起共享或撤销共享。

    `action=revoke` 必须带 `reason`：撤销会让正在用这个素材的同事突然拿不到文件，
    理由要留在申请单里（设计 §3.5）。
    """

    asset_id: str
    action: ShareAction = ShareAction.SHARE
    target: ShareTarget = ShareTarget.GROUP
    group_id: str = ""
    reason: str = Field(default="", max_length=200)
    expected_version: int = 0


class ShareDecisionIn(CamelModel):
    """审批 / 驳回 / 撤回申请单。`note` 会进申请单与审计。"""

    note: str = Field(default="", max_length=200)
    expected_version: int = 0


class ShareRequestOut(CamelModel):
    id: str
    asset_id: str
    asset_name: str = ""
    applicant_id: str
    applicant_name: str | None = None
    action: ShareAction
    target: ShareTarget
    group_id: str = ""
    group_name: str | None = None
    status: str
    reviewer_id: str | None = None
    note: str = ""
    reason: str = ""
    decided_at: UtcDateTime | None = None
    created_at: UtcDateTime


class DraftDecisionIn(CamelModel):
    reason: str = Field(default="", max_length=200)
    expected_version: int = 0


class BulkAction(StrEnum):
    """批量动作。每一条都复用单条接口的服务方法，不另写一份状态机。

    `purge` 刻意不在列表里：彻底删除不可逆，一批 10 条点错一次就没了。
    """

    ADD_TAG = "addTag"
    REMOVE_TAG = "removeTag"
    RENAME = "rename"
    DELETE = "delete"
    RESTORE = "restore"
    APPROVE_DRAFT = "approveDraft"
    REJECT_DRAFT = "rejectDraft"
    REQUEST_REVOKE = "requestRevoke"


class BulkIn(CamelModel):
    """批量写。**逐条判定、逐条提交**：一批里 3 条已共享就返回 7 成功 + 3 条明细。"""

    ids: list[str] = Field(min_length=1, max_length=100)
    action: BulkAction
    payload: dict = Field(default_factory=dict)
    expected_versions: dict[str, int] = Field(default_factory=dict)


class BulkItemResult(CamelModel):
    id: str
    ok: bool
    error_code: int = 0
    message: str = ""
    details: dict | None = None


class BulkOut(CamelModel):
    succeeded: int = 0
    failed: int = 0
    results: list[BulkItemResult] = Field(default_factory=list)


class LibrarySelectionPage(Page[LibraryAssetOut]):
    """选择器返回：只含 `state=active` 的条目，不带分面（选择器不显示角标）。"""


class GroupLeadersIn(CamelModel):
    """整体替换某组的负责人列表。空数组 = 清空（该组退回「只能由持
    `library:share:approve` 的人审批」）。"""

    user_ids: list[str] = Field(default_factory=list)
