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
    "DownloadIn",
    "DownloadJobOut",
    "DownloadSkip",
    "DraftDecisionIn",
    "FromGenerationIn",
    "GenerationKind",
    "GenerationSnapshotIn",
    "GroupLeadersIn",
    "IngestOut",
    "LibraryArchiveOut",
    "LibraryAssetDetailOut",
    "LibraryAssetOrigin",
    "LibraryAssetOut",
    "LibraryAssetPage",
    "LibraryFacets",
    "LibraryGenerationOut",
    "LibraryGroupOut",
    "LibraryGroupRef",
    "LibrarySelectionPage",
    "LibraryTab",
    "LibraryTagOut",
    "LibraryTagStatOut",
    "PackIn",
    "PackItemIn",
    "PackItemOut",
    "PackPatchIn",
    "ShareAction",
    "ShareDecisionIn",
    "ShareHistoryItem",
    "ShareRequestIn",
    "ShareRequestOut",
    "ShareTarget",
    "TagIn",
    "TagParentIn",
    "TextAssetIn",
    "TextAssetOut",
    "TextAssetRestoreIn",
    "TextAssetUpdateIn",
    "TextAssetVersionOut",
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


class LibraryAssetOrigin(StrEnum):
    """资产来源，取值必须和 `library_assets.origin` 的落库值一致。

    模板库切换时靠它只取「原片视频」：`template_source` 是切换后新上传的，
    `imported` 是并存期从旧库导入的存量原片（7 条）。只认前者会把存量全漏掉，
    所以列表与选择器都支持**传多个值**。
    """

    UPLOAD = "upload"
    GENERATED = "generated"
    TEMPLATE_SOURCE = "template_source"
    IMPORTED = "imported"
    PACK = "pack"


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


class LibraryTagOut(CamelModel):
    """词表里的一条标签。**固定两级**，不返回嵌套树。

    为什么仍是平铺数组：标签栏、卡片、打标选择器、上传弹窗、批量栏、素材库
    悬浮栏六个消费点全都按平铺渲染；改成嵌套树要全量重写，而且「计数与排序」
    会跟着乱。`parentId` + `depth` 足够让前端自己分两行、显示 `产品 / 3030`。

    库里用空串表示一级（见 models.py），出参统一转成 `null`：前端按真假值判断
    层级就行，不必知道哨兵串的存在。
    """

    id: str
    name: str
    parent_id: str | None = None
    #: 二级标签带父名：卡片直接显示「产品 / 3030」，不必为了一个名字再拉词表
    parent_name: str | None = None
    #: 1 = 一级，2 = 二级
    depth: int = 1


class LibraryTagStatOut(LibraryTagOut):
    """标签栏的列表项：比卡片上的标签多「素材数」「直属子标签数」与「添加时间」。

    `asset_count` 是**当前入口**（`state` + `library`）下挂了这个标签的素材数，
    不是全库总量——标签栏上点一下就按这个入口筛，数字必须与结果对得上。
    一级标签的数字是「自身 + 旗下二级」按资产去重后的合计，与「点一级展开到
    子级」的筛选口径一致；否则会出现「标签旁写 8、点进去只有 3 条」。

    `child_count` 给前端判断「这是一级分组还是可打标的叶子」。
    `created_at` 给前端按「添加时间先后」排序用。

    单独出一个类型而不是往 `LibraryTagOut` 上加这几个字段：卡片里 24 条资产
    每条都带一遍计数与时间戳，既没用也容易被人误当成卡片上的真实数据。
    """

    asset_count: int = 0
    child_count: int = 0
    created_at: UtcDateTime


class LibraryAssetOut(CamelModel):
    """列表项。字段对齐 `library_assets` 表，M1 建表后不再**改**形状。

    后面补的五个字段是「只增不改」：取件地址与媒体规格本来就在表里/由 id 决定，
    以前让前端自己拼 URL、自己猜哪套库。模板库切换时前端只要改接口调用，
    不必在业务组件里补一段「按 id 拼 /file」的适配代码。
    """

    id: str
    name: str
    kind: LibraryAssetKind
    origin: str = Field(description="upload / generated / template_source / imported / pack")
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
    #: 三个取件地址都是「相对 API 前缀」的 URL，由后端拼好。选择器/列表拿到就能
    #: 直接给 `<img>` / `<video>` / 下载用，不用在前端按 id 拼第二套路径。
    url: str = ""
    preview_url: str | None = None
    stream_url: str | None = None
    #: 0 表示「没探测出来」。出参给 None，前端才显示「未知」而不是「0 × 0」。
    width: int | None = None
    height: int | None = None
    group_ids: list[str] = Field(default_factory=list)
    # 标签随列表一起返回：卡片要在体积下方显示它，而「先列 24 条、再逐条拉详情」
    # 是 24 次往返。详情页继承这个字段，不再重复声明。
    tags: list[LibraryTagOut] = Field(default_factory=list)
    # 回收站专用的两个字段：前端据此显示「剩余 N 天」与「因何被删」。
    # 在库里时为 null，不要在响应里省略——省略会让前端多一层「有没有这个键」的判断。
    trash_reason: str | None = None
    trash_expires_at: UtcDateTime | None = None
    #: 仅 `kind=text` 有值。列表随行返回，卡片不必为一句摘要再读 blob。
    text_excerpt: str | None = None
    #: 文本的内容版本（从 1 开始）；其它类型恒为 0。
    text_version: int = 0
    #: 正文的纯文本取件地址（`text/plain; charset=utf-8` + nosniff）。
    #: 非文本为 null，前端据此决定「详情显示 <pre> 还是播放器」。
    text_url: str | None = None
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
    """上传接口允许的文件类型。

    不复用 `LibraryAssetKind`：`pack` 不是上传出来的，它是封装动作的产物；
    让上传接口收 `pack` 只会得到一个没有字节的资产行。`TEXT` 走同一条上传链，
    但体积上限与探测规则单独一档（见 `infra/media_probe.MAX_TEXT_BYTES`）。
    """

    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    TEXT = "text"


# --------------------------------------------------------------------------- #
# 文本资产：站内新建 / 在线编辑 / 版本历史
# --------------------------------------------------------------------------- #
class TextAssetIn(CamelModel):
    """站内新建一份文本资产（不传文件，直接写正文）。

    `extension` 只认 md / txt：文件名后缀决定 `mime_type` 与内容寻址 key 的后缀，
    不能由用户随便给一个 `.html`——那会让下载下来的东西被浏览器当页面执行。
    """

    name: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=1_048_576)
    extension: str = Field(default="md", pattern="^(md|txt)$")
    tag_ids: list[str] = Field(default_factory=list)


class TextAssetUpdateIn(CamelModel):
    """在线编辑。`expected_version` 是**内容版本**，不是 `stateVersion`：
    两个人同时打开编辑框时，后保存的人必须看到「正文已被改过」而不是静默覆盖。"""

    content: str = Field(min_length=1, max_length=1_048_576)
    expected_version: int = Field(default=0, ge=0)
    note: str = Field(default="", max_length=200)


class TextAssetOut(CamelModel):
    """文本正文 + 当前版本。详情页与编辑器用，不进列表。"""

    id: str
    name: str
    content: str
    version: int
    sha256: str
    size_bytes: int
    excerpt: str
    updated_at: UtcDateTime | None = None


class TextAssetVersionOut(CamelModel):
    """一条历史版本。列表默认不带正文，按版本读时才带。"""

    version: int
    sha256: str
    size_bytes: int
    excerpt: str
    note: str = ""
    created_by: str
    created_at: UtcDateTime
    content: str | None = None


class TextAssetRestoreIn(CamelModel):
    """把某一历史版本重新写成**新的一版**，不改历史行。"""

    version: int = Field(ge=1)
    expected_version: int = Field(default=0, ge=0)


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


class PackItemOut(CamelModel):
    """封装里的一项。成员可能是别人共享给我的素材，所以带 `state` 与 `coverUrl`。"""

    asset_id: str
    name: str = ""
    kind: LibraryAssetKind
    state: LibraryAssetState
    position: int
    role: str = ""
    description: str = ""
    size_bytes: int = 0
    cover_url: str | None = None


class ShareHistoryItem(CamelModel):
    """可见性变更的一条记录。`reason` 是撤销申请里填的理由，审核人当时看的就是它。"""

    id: str
    action: ShareAction
    target: ShareTarget
    group_id: str = ""
    group_name: str = ""
    status: str
    applicant_id: str
    applicant_name: str | None = None
    reviewer_id: str | None = None
    reviewer_name: str | None = None
    reason: str = ""
    note: str = ""
    decided_at: UtcDateTime | None = None
    created_at: UtcDateTime


class LibraryArchiveOut(CamelModel):
    """归档信息。只给所有者与管理员：存储键与幂等键对外没有意义，只会泄露内部结构。"""

    storage_key: str = ""
    sha256: str = ""
    source_key: str = ""
    size_bytes: int = 0
    reference_count: int = 0


class LibraryAssetDetailOut(LibraryAssetOut):
    """详情 = 列表项 + 标签 + 生成参数（有才返回）。

    生成参数用 `None` 而不是空对象：前端要区分「不是生成来的」与
    「是生成来的但历史数据没记全」，两者显示逻辑不同。

    M3 补齐详情页六个区的数据源（设计 §7.2）：

    - 媒体区 → `packItems`（仅封装）与 `coverUrl`；
    - 基础信息 → `state` / `trashedFromState` / `reviewedBy` / `rejectReason`；
    - 媒体规格 → 继承的 `sizeBytes` / `durationSec` / `width`… 由前端自己排；
    - 生成参数 → `generation`；
    - 使用与引用 → `references` + `shareHistory`；
    - 归档信息 → `archive`（仅所有者 / 管理员可见）。
    """

    tags: list[LibraryTagOut] = Field(default_factory=list)
    groups: list[LibraryGroupRef] = Field(default_factory=list)
    generation: LibraryGenerationOut | None = None
    #: 封装的成员（`kind=pack` 时有值，其它类型恒为空数组）。
    pack_items: list[PackItemOut] = Field(default_factory=list)
    #: 谁在用它。`None` 表示**查不了**（`library-references` 未接上），
    #: 与空数组（查过、没人用）是两件事——后者能删，前者不能下结论。
    references: list[str] | None = None
    #: 可见性变更历史（共享 / 撤销，按时间倒序，最多 20 条）。
    share_history: list[ShareHistoryItem] = Field(default_factory=list)
    #: 归档信息。只有所有者与 `library:delete:any` 拿得到，其他人是 `None`。
    archive: LibraryArchiveOut | None = None
    #: 草稿最近一次审核的结论（审核人、时间、驳回原因）。
    reviewed_by: str | None = None
    reviewed_at: UtcDateTime | None = None
    reject_reason: str | None = None
    #: 回收站条目的来源状态：决定「恢复」之后回到草稿箱还是个人库。
    trashed_from_state: str | None = None


# --------------------------------------------------------------------------- #
# M2：可见性、共享、生命周期、标签、批量
# --------------------------------------------------------------------------- #
class LibraryGroupRef(CamelModel):
    """资产共享到的小组。名称走组织目录注入口，不在本模块 join 身份的表。"""

    id: str
    name: str = ""


class LibraryGroupOut(CamelModel):
    """我的小组。`isLeader` 现在只用于删除窄口（§3.4.1）的入口展示；
    「待我审核」按审批权限点与「是不是该组成员」收窄，与组长身份无关。"""

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
    """新建 / 重命名标签。

    `parent_id` **只在新建时生效**：给一个一级标签的 id 就建成二级，留 null
    建成一级。改名时不改层级，走 `PATCH /tags/{id}/parent`——把改名与移动混在
    一个接口里，会让「只想改个错别字」的请求顺手改变整棵树的形状。
    """

    name: str = Field(min_length=1, max_length=40)
    parent_id: str | None = Field(default=None, max_length=32)


class TagParentIn(CamelModel):
    """移动标签：`parentId` 指向一个一级标签，`null` 表示提升为一级。"""

    parent_id: str | None = Field(default=None, max_length=32)


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
    #: 把勾选的这批直接封装起来（M3）。`payload.name` 必填，`payload.coverAssetId` 可选。
    PACK = "pack"


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
    """整体替换某组的负责人列表（只服务删除窄口）。空数组 = 清空。
    审批资格由 `library:share:approve*` 三个权限点按申请目标判定，与这张表无关。"""

    user_ids: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# M3：封装、批量下载、生成入库
# --------------------------------------------------------------------------- #
class PackItemIn(CamelModel):
    """封装成员。`role` 预留给模板替换（产品主图 / 多视结构图…），首版可为空。"""

    asset_id: str = Field(min_length=1, max_length=32)
    role: str = Field(default="", max_length=40)
    description: str = Field(default="", max_length=200)


class PackIn(CamelModel):
    """建封装。

    `items` 是**唯一有序来源**；`assetIds` 只是给「勾选一批直接封装」用的简写，
    会被追加到 `items` 之后（已在 items 里的不重复加）。两者都空 → 400。
    """

    name: str = Field(min_length=1, max_length=200)
    asset_ids: list[str] = Field(default_factory=list, max_length=100)
    items: list[PackItemIn] = Field(default_factory=list, max_length=100)
    cover_asset_id: str = Field(default="", max_length=32)
    tag_ids: list[str] = Field(default_factory=list)


class PackPatchIn(CamelModel):
    """改封装。`items` 提供即**整体替换**（不是增量追加）——增量语义要前端自己
    算差集，而算错了就是悄悄多一个成员，不如让调用方给全量。"""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    items: list[PackItemIn] | None = Field(default=None, max_length=100)
    cover_asset_id: str | None = Field(default=None, max_length=32)
    tag_ids: list[str] | None = None
    expected_version: int = 0


class DownloadIn(CamelModel):
    """发起打包。上限在服务层判：请求最多 200 个 id（封装要展开），
    **展开后**的条目数与总字节数才是真正的闸门（100 项 / 2GB）。"""

    ids: list[str] = Field(min_length=1, max_length=200)


class DownloadSkip(CamelModel):
    """没能进包的条目与原因。**不整单失败**：一批 20 个里有 1 个是草稿，
    剩下的 19 个仍然该能下。"""

    id: str
    name: str = ""
    reason: str


class DownloadJobOut(CamelModel):
    """打包任务。

    `skipped` 只在 `POST /downloads` 的响应里非空：它解释的是「你选的这批里
    哪几个为什么没进来」，轮询状态时不再重复（前端在发起那一刻就该提示）。
    落库只存 id 列表与结果，不存这份解释——省一张明细表，也避免它变成第二真相源。
    """

    id: str
    status: str
    asset_count: int = 0
    size_bytes: int = 0
    error: str = ""
    download_url: str | None = None
    expires_at: UtcDateTime | None = None
    created_at: UtcDateTime
    finished_at: UtcDateTime | None = None
    skipped: list[DownloadSkip] = Field(default_factory=list)


class GenerationKind(StrEnum):
    """生成结果的类型。只有这两种能进草稿箱：`audio` / `pack` 没有生成链路。"""

    VIDEO = "video"
    IMAGE = "image"


class IngestOut(CamelModel):
    """生成入库结果。`created=False` = 命中幂等（sourceKey 重复或内容相同）。

    不返回整条资产：调用方（生成侧）需要的是「拿到 id 去关联任务」，
    而它可能并不持有 `library:read` 之外的权限去读详情。
    """

    asset_id: str
    created: bool


class GenerationSnapshotIn(CamelModel):
    """生成参数快照。字段与 `library_asset_generation` 一一对应，全部可选。"""

    model: str = Field(default="", max_length=80)
    model_id: str = Field(default="", max_length=80)
    prompt: str = Field(default="", max_length=4000)
    size: str = Field(default="", max_length=32)
    count: int = Field(default=0, ge=0)
    duration_sec: float = Field(default=0.0, ge=0)
    aspect_ratio: str = Field(default="", max_length=16)
    style_preset: str = Field(default="", max_length=40)
    seed: str = Field(default="", max_length=64)
    reference_asset_ids: list[str] = Field(default_factory=list)
    pack_id: str = Field(default="", max_length=32)
    pack_version: int = Field(default=0, ge=0)
    prompt_spec: dict = Field(default_factory=dict)
    configuration: dict = Field(default_factory=dict)
    external_id: str = Field(default="", max_length=120)
    credits: float = 0.0
    generated_at: UtcDateTime | None = None


class FromGenerationIn(CamelModel):
    """生成结果入库（也经 `library-ingest` 注入口供后端模块调用）。

    字节来源三选一，**必须能物化**：`storageKey`（主对象存储）、
    `archiveKey`（归档存储，模板库替换成片用）、`sourceUrl`（服务端下载）。
    只存外部链接的详情页与下载都会裂（链接会过期）。优先级按上面顺序，
    同时给多个时取靠前的那个。

    `archiveKey` 只应由受信任的服务端调用方填写：它指向归档盘里的对象，
    端点本身仍由 `library:generation:ingest` 守护，普通上传权限调不动。
    """

    source_module: str = Field(pattern="^(image|template|task)$")
    source_task_id: str = Field(min_length=1, max_length=64)
    source_key: str = Field(min_length=1, max_length=160)
    kind: GenerationKind
    name: str = Field(default="", max_length=200)
    storage_key: str = Field(default="", max_length=256)
    archive_key: str = Field(default="", max_length=256)
    source_url: str = Field(default="", max_length=1000)
    mime_type: str = Field(default="", max_length=64)
    size_bytes: int = Field(default=0, ge=0)
    tag_ids: list[str] = Field(default_factory=list)
    generation: GenerationSnapshotIn = Field(default_factory=GenerationSnapshotIn)
