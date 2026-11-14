/**
 * 后端接口类型（自动生成，请勿手工编辑）。
 *
 * 重新生成：`cd api && .venv/Scripts/python -m cli export-openapi`
 *
 * 用法：
 *   import type { ApiResponse, Page, TaskOut, EndpointData } from '@/shared/api/schema';
 *   const task = await api.get<TaskOut>(`/tasks/${id}`);   // 已自动解包 data
 */

/* eslint-disable */

/** 统一响应封套：所有接口都返回这个形状 */
export interface ApiResponse<T = unknown> {
  /** 0 表示成功，其余为业务错误码（见 docs/API.md 的错误码总表） */
  code: number;
  message: string;
  data: T;
}

/** 分页封套 */
export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
}

/* ------------------------------ 枚举 ------------------------------ */

/** 取值：9:16, 16:9, 1:1 */
export type AspectRatio = "9:16" | "16:9" | "1:1";
export const AspectRatio_VALUES = ["9:16", "16:9", "1:1"] as const;

/** 取值：-createdAt, createdAt, name, -sizeBytes */
export type AssetSort = "-createdAt" | "createdAt" | "name" | "-sizeBytes";
export const AssetSort_VALUES = ["-createdAt", "createdAt", "name", "-sizeBytes"] as const;

/** 取值：addTag, removeTag, rename, delete, restore, approveDraft, rejectDraft, requestRevoke, pack */
export type BulkAction = "addTag" | "removeTag" | "rename" | "delete" | "restore" | "approveDraft" | "rejectDraft" | "requestRevoke" | "pack";
export const BulkAction_VALUES = ["addTag", "removeTag", "rename", "delete", "restore", "approveDraft", "rejectDraft", "requestRevoke", "pack"] as const;

/** 取值：video, image, audio */
export type Capability = "video" | "image" | "audio";
export const Capability_VALUES = ["video", "image", "audio"] as const;

/** 取值：tasks, audit_events */
export type ExportSource = "tasks" | "audit_events";
export const ExportSource_VALUES = ["tasks", "audit_events"] as const;

/** 取值：video, image */
export type GenerationKind = "video" | "image";
export const GenerationKind_VALUES = ["video", "image"] as const;

/** 取值：video, audio, image, pack, text */
export type LibraryAssetKind = "video" | "audio" | "image" | "pack" | "text";
export const LibraryAssetKind_VALUES = ["video", "audio", "image", "pack", "text"] as const;

/** 取值：upload, generated, template_source, imported, pack */
export type LibraryAssetOrigin = "upload" | "generated" | "template_source" | "imported" | "pack";
export const LibraryAssetOrigin_VALUES = ["upload", "generated", "template_source", "imported", "pack"] as const;

/** 取值：draft, active, trash */
export type LibraryAssetState = "draft" | "active" | "trash";
export const LibraryAssetState_VALUES = ["draft", "active", "trash"] as const;

/** 取值：all, personal, group, team */
export type LibraryTab = "all" | "personal" | "group" | "team";
export const LibraryTab_VALUES = ["all", "personal", "group", "team"] as const;

/** 取值：personal, group, team */
export type LibraryVisibility = "personal" | "group" | "team";
export const LibraryVisibility_VALUES = ["personal", "group", "team"] as const;

/** 取值：share, revoke */
export type ShareAction = "share" | "revoke";
export const ShareAction_VALUES = ["share", "revoke"] as const;

/** 取值：group, team */
export type ShareTarget = "group" | "team";
export const ShareTarget_VALUES = ["group", "team"] as const;

/** 取值：mine, team, all */
export type TaskScope = "mine" | "team" | "all";
export const TaskScope_VALUES = ["mine", "team", "all"] as const;

/** 取值：createdAt_desc, createdAt_asc, title_asc */
export type TaskSort = "createdAt_desc" | "createdAt_asc" | "title_asc";
export const TaskSort_VALUES = ["createdAt_desc", "createdAt_asc", "title_asc"] as const;

/** 取值：queued, running, completed, failed */
export type TaskStatus = "queued" | "running" | "completed" | "failed";
export const TaskStatus_VALUES = ["queued", "running", "completed", "failed"] as const;

/** 取值：all, queued, running, completed, failed */
export type TaskStatusFilter = "all" | "queued" | "running" | "completed" | "failed";
export const TaskStatusFilter_VALUES = ["all", "queued", "running", "completed", "failed"] as const;

/** 取值：image, audio, video, text */
export type UploadKind = "image" | "audio" | "video" | "text";
export const UploadKind_VALUES = ["image", "audio", "video", "text"] as const;

/* ------------------------------ 模型 ------------------------------ */

/** 改名 / 换标签。**不含可见性**：共享必须走申请单，否则审批就没有意义了。 */
export interface AssetPatchIn {
  name?: string | null;
  tagIds?: string[] | null;
  /** >0 时做乐观锁校验；0 表示不校验（兼容单页操作） */
  expectedVersion?: number;
}

/** 批量写。**逐条判定、逐条提交**：一批里 3 条已共享就返回 7 成功 + 3 条明细。 */
export interface BulkIn {
  ids: string[];
  action: BulkAction;
  payload?: Record<string, unknown>;
  expectedVersions?: Record<string, number>;
}

/** BulkItemResult */
export interface BulkItemResult {
  id: string;
  ok: boolean;
  errorCode?: number;
  message?: string;
  details?: Record<string, unknown> | null;
}

/** BulkOut */
export interface BulkOut {
  succeeded?: number;
  failed?: number;
  results?: BulkItemResult[];
}

/** 哪几张素材拿到了哪个编号 —— 只给前端画角标，不参与提交。 */
export interface CompiledSlotOut {
  key: string;
  tokens?: string[];
  assetIds?: string[];
}

/** CreateExportIn */
export interface CreateExportIn {
  /** 数据源，见 enums.ExportSource */
  dataSource: ExportSource;
  /** 筛选条件；不填表示该源的全量 */
  params?: ExportParams;
}

/** 创建任务入参。

`model` / `durationSec` 不设默认值：合法取值来自模型目录（`GET /models`），
由 service 按供应商声明的 `paramSchema` 校验，写死默认值会在换模型时变成错值。
两者都可以不传：不传模型取该能力的默认模型，不传时长取模型 schema 的默认档位。 */
export interface CreateTaskIn {
  /** 画面描述 */
  prompt: string;
  /** 不传则截取 prompt */
  title?: string | null;
  /** 模型 id 或名称，缺省取默认模型 */
  model?: string | null;
  /** 视频时长（秒），缺省取模型的默认档位 */
  durationSec?: number | null;
  /** 画幅比例，缺省 9:16 竖屏 */
  aspectRatio?: AspectRatio;
  /** 参考素材的资产库 v2 资产 id，最多 20 个，**顺序即提交顺序**。受理时校验：必须存在、当前账号能读、不在回收站 / 草稿里，且是 `image` / `video` / `audio` 之一（能否收图、收几张由模型的 `maxReferences` 决定）。传 id 而不是 URL：取件链接是私签的，供应商取不到 */
  referenceLibraryIds?: string[];
  /** 要拼进提示词的文本资产 id，按顺序对应 `{{Text 1}}` / `{{Text 2}}`…；提交时按当时版本冻结正文，之后编辑资产不影响本次生成与重试。只接受当前账号可读、在库的 text 类型资产，最多 8 份 */
  textLibraryIds?: string[];
  /** 风格预设标识，留空为不套风格 */
  stylePreset?: string | null;
}

/** DeleteTasksIn */
export interface DeleteTasksIn {
  /** 要删除的任务 ID 列表 */
  ids: string[];
}

/** DeleteTasksOut */
export interface DeleteTasksOut {
  /** 实际删除的条数 */
  deleted: number;
  /** 被跳过的任务 ID：不存在，或属于他人且没有 `task:read:all` */
  skipped?: string[];
}

/** 发起打包。上限在服务层判：请求最多 200 个 id（封装要展开），
**展开后**的条目数与总字节数才是真正的闸门（100 项 / 2GB）。 */
export interface DownloadIn {
  ids: string[];
}

/** 打包任务。

`skipped` 只在 `POST /downloads` 的响应里非空：它解释的是「你选的这批里
哪几个为什么没进来」，轮询状态时不再重复（前端在发起那一刻就该提示）。
落库只存 id 列表与结果，不存这份解释——省一张明细表，也避免它变成第二真相源。 */
export interface DownloadJobOut {
  id: string;
  status: string;
  assetCount?: number;
  sizeBytes?: number;
  error?: string;
  downloadUrl?: string | null;
  expiresAt?: string | null;
  createdAt: string;
  finishedAt?: string | null;
  skipped?: DownloadSkip[];
}

/** 没能进包的条目与原因。**不整单失败**：一批 20 个里有 1 个是草稿，
剩下的 19 个仍然该能下。 */
export interface DownloadSkip {
  id: string;
  name?: string;
  reason: string;
}

/** DraftDecisionIn */
export interface DraftDecisionIn {
  reason?: string;
  expectedVersion?: number;
}

/** DriverListOut */
export interface DriverListOut {
  items?: DriverOut[];
  total?: number;
  errors?: string[];
}

/** DriverOut */
export interface DriverOut {
  name: string;
  label: string;
  version: string;
  path: string;
  capabilities?: string[];
  modelCount?: number;
  supportsPoll?: boolean;
  loaded?: boolean;
  error?: string;
}

/** 某用户的权限来源。前端只读展示，**判定永远走 effective**。 */
export interface EffectivePermissionsOut {
  /** 团队基础权限（全体启用账号） */
  team?: string[];
  /** 各所属小组授予的权限 */
  groups?: GroupLayerOut[];
  /** 单独授予的权限 */
  personal?: string[];
  /** 现有角色授予的权限 */
  roles?: string[];
  /** 四层并集；`*` 表示超级管理员。require() 唯一判据 */
  effective?: string[];
}

/** 提交或加速一条替换任务。

`credits` 只作用于这条任务：提交时关掉账号的 0 积分权益标签，走正常积分队列，
**不动账号级开关**，因此不会让同账号的其他人下一次提交莫名其妙开始花钱。
不传表示沿用档案里当前的计费模式——「继续查询 / 重试原任务」走的就是这条路，
它们不该顺带把一条已加速的任务改回免费队列。 */
export interface ExecuteReplacement {
  billingMode?: "free" | "credits" | null;
}

/** 详情多一个 `downloadable`：让前端不必重复实现「能不能下」的判断。

这条规则有两处（`succeeded` 且未被回收），前端各写一遍必然和回收任务
对不上——用户会看到「按钮是亮的，点进去 410」。 */
export interface ExportDetailOut {
  id: string;
  dataSource: string;
  params?: Record<string, unknown>;
  status: string;
  rowCount?: number;
  byteSize?: number;
  error?: string | null;
  attempts?: number;
  requestedBy: string;
  createdAt?: string | null;
  startedAt?: string | null;
  finishedAt?: string | null;
  expiresAt?: string | null;
  downloadable?: boolean;
}

/** ExportOut */
export interface ExportOut {
  id: string;
  dataSource: string;
  params?: Record<string, unknown>;
  status: string;
  rowCount?: number;
  byteSize?: number;
  error?: string | null;
  attempts?: number;
  requestedBy: string;
  createdAt?: string | null;
  startedAt?: string | null;
  finishedAt?: string | null;
  expiresAt?: string | null;
}

/** ExportParams */
export interface ExportParams {
  /** 仅 tasks：按任务状态过滤（如 succeeded / failed） */
  status?: string | null;
  /** 仅 tasks：按模型目录 id 过滤 */
  modelId?: string | null;
  /** 仅 audit_events：按动作前缀过滤（如 task.） */
  action?: string | null;
  /** 起始时间（含），ISO 8601 */
  since?: string | null;
  /** 结束时间（不含），ISO 8601 */
  until?: string | null;
}

/** 可导出的数据源。前端据此渲染下拉，不要硬编码数据源名单。

这份列表来自运行时注册表（`core.ports.export_sources` + 内置源），
新模块注册一个源之后这里自动多一项——前端不需要跟着发版。 */
export interface ExportSourceOut {
  name: string;
  label: string;
  /** CSV 列，同时也是列顺序 */
  columns: string[];
  /** 导出该源额外需要的权限码；null 表示只需 export:read */
  permission?: string | null;
}

/** 生成结果入库（也经 `library-ingest` 注入口供后端模块调用）。

字节来源三选一，**必须能物化**：`storageKey`（主对象存储）、
`archiveKey`（归档存储，模板库替换成片用）、`sourceUrl`（服务端下载）。
只存外部链接的详情页与下载都会裂（链接会过期）。优先级按上面顺序，
同时给多个时取靠前的那个。

`archiveKey` 只应由受信任的服务端调用方填写：它指向归档盘里的对象，
端点本身仍由 `library:generation:ingest` 守护，普通上传权限调不动。 */
export interface FromGenerationIn {
  sourceModule: string;
  sourceTaskId: string;
  sourceKey: string;
  kind: GenerationKind;
  name?: string;
  storageKey?: string;
  archiveKey?: string;
  sourceUrl?: string;
  mimeType?: string;
  sizeBytes?: number;
  tagIds?: string[];
  generation?: GenerationSnapshotIn;
}

/** GenerateImageIn */
export interface GenerateImageIn {
  /** 画面描述 */
  prompt: string;
  /** 宽x高，如 `1024x1536`；留空取模型 `paramSchema.size.default` */
  size?: string;
  /** 张数；`0` 表示取模型 `paramSchema.count.default`，上限以模型声明为准 */
  count?: number;
  /** 模型声明的其他参数，键与 `GET /models` 返回的 `paramSchema` 一致（如 `quality` / `seed`）。未声明的键会被 400 拒绝，避免打错字被静默忽略；与顶层 `size` / `count` 重复时以顶层为准 */
  params?: Record<string, unknown>;
  /** 模型 id，留空取默认 */
  model?: string;
  /** 参考图（图生图 / 多图合成）的素材库资产 id，**按顺序生效**：多图合成时提示词里的「第一张 / 第二张」指的就是这个顺序，重排会让提示词与图片错位。只接受 `image` 类型且当前账号能读的资产，张数上限由模型 `maxReferences` 决定（`0` = 该模型不支持参考图）。传 id 而不是 URL：我们的取件链接是私签的、供应商访问不到，服务端读字节后以 `data:image/...;base64,...` 交给供应商 */
  referenceLibraryIds?: string[];
  /** 要拼进提示词的文本资产 id，**按顺序生效**：`{{Text 1}}` 指第 1 份、`{{Text 2}}` 指第 2 份。提交时把正文按当时版本冻结进任务，之后编辑资产不会改变这次生成与它的重试。只接受当前账号可读、在库的 `text` 类型资产；一次最多 8 份 */
  textLibraryIds?: string[];
}

/** 生成参数快照。字段与 `library_asset_generation` 一一对应，全部可选。 */
export interface GenerationSnapshotIn {
  model?: string;
  modelId?: string;
  prompt?: string;
  size?: string;
  count?: number;
  durationSec?: number;
  aspectRatio?: string;
  stylePreset?: string;
  seed?: string;
  referenceLibraryIds?: string[];
  packId?: string;
  packVersion?: number;
  promptSpec?: Record<string, unknown>;
  configuration?: Record<string, unknown>;
  externalId?: string;
  credits?: number;
  generatedAt?: string | null;
}

/** GroupCreateIn */
export interface GroupCreateIn {
  /** 小组名称，唯一 */
  name: string;
  /** 小组说明 */
  description?: string;
}

/** 分层权限明细里的一段：这条权限来自哪个小组。 */
export interface GroupLayerOut {
  /** 小组 ID */
  id: string;
  /** 小组名称 */
  name: string;
  /** 该组授予的权限码 */
  permissions?: string[];
}

/** 整体替换某组的负责人列表（只服务删除窄口）。空数组 = 清空。
审批资格由 `library:share:approve*` 三个权限点按申请目标判定，与这张表无关。 */
export interface GroupLeadersIn {
  userIds?: string[];
}

/** GroupMembersIn */
export interface GroupMembersIn {
  /** 成员用户 id，整组替换；传空数组即清空 */
  userIds?: string[];
}

/** GroupOut */
export interface GroupOut {
  /** 小组 ID */
  id: string;
  /** 小组名称，唯一 */
  name: string;
  /** 小组说明 */
  description?: string;
  /** 创建时间（UTC） */
  createdAt: string;
  /** 小组整体权限；成员自动获得 */
  permissions?: string[];
  /** 成员用户 id；编辑成员时整组替换 */
  memberIds?: string[];
  /** 成员数 */
  memberCount?: number;
}

/** GroupUpdateIn */
export interface GroupUpdateIn {
  /** 小组名称 */
  name?: string | null;
  /** 小组说明 */
  description?: string | null;
}

/** HTTPValidationError */
export interface HTTPValidationError {
  detail?: ValidationError[];
}

/** HealthOut */
export interface HealthOut {
  status?: string;
  app: string;
  env: string;
  version?: string;
}

/** 一条生图记录的任务中心投影，字段够卡片与详情弹窗用即可。 */
export interface ImageFeedItem {
  /** 生图记录 ID */
  id: string;
  /** 展示标题（记录名） */
  title: string;
  /** 原始状态：queued / running / succeeded / failed */
  status: string;
  /** 统一筛选分组 */
  group: "all" | "queued" | "running" | "completed" | "failed" | "attention";
  /** 创建时间（UTC） */
  createdAt: string;
  /** 生成进度 0-100 */
  progress?: number;
  /** 模型可读名 */
  model?: string;
  /** 模型目录 id */
  modelId?: string | null;
  /** 尺寸，如 1024x1024 */
  size?: string;
  /** 一次生成的张数 */
  count?: number;
  /** 画面描述 */
  prompt?: string;
  /** 封面图地址（已按当前存储驱动现签，可直接用于 <img>） */
  coverUrl?: string;
  /** 成图地址（成功后才有），同样已现签 */
  outputUrl?: string | null;
  /** 失败原因 */
  errorMessage?: string | null;
  /** 供应商任务号；有值时重试需先确认可能已扣费 */
  externalId?: string | null;
  /** 生图历史页深链接 */
  detailUrl?: string | null;
}

/** ImageJobOut */
export interface ImageJobOut {
  /** 生图记录 ID */
  id: string;
  /** 名称 */
  name: string;
  /** 画面描述 */
  prompt: string;
  /** 尺寸，如 `1024x1024` */
  size: string;
  /** 一次生成的张数 */
  count?: number;
  /** 模型额外参数（不含 size / count），键按提交时的模型 `paramSchema` */
  params?: Record<string, unknown>;
  /** 状态：`queued` / `running` / `succeeded` / `failed` */
  status?: string;
  /** 进度 0-100 */
  progress?: number;
  /** 模型可读名 */
  model?: string;
  /** 模型目录 id */
  modelId?: string | null;
  /** 封面图地址（落库存对象 key，出参按当前存储驱动现签，可直接用于 <img>） */
  coverUrl?: string;
  /** 成图地址（成功后才有）：同样是按当前驱动现签的可访问地址，不要把它当「永久链接」持久化到别处 */
  outputUrl?: string | null;
  /** 失败原因 */
  errorMessage?: string | null;
  /** 供应商侧任务号；提交成功后有值，可用于对账 */
  externalId?: string | null;
  /** 上一次提交的供应商任务号；带 confirmCharged 重试后保留的对账凭证 */
  previousExternalId?: string | null;
  /** 被替换掉的全部供应商任务号（旧的在前）。连续多次确认已扣费重试时，previousExternalId 只保留最近一个，全量凭证看这里 */
  previousExternalIds?: string[];
  /** 提交时带的参考图资产 id（顺序即提交顺序）；纯文生图为空 */
  referenceLibraryIds?: string[];
  /** 绑定的文本资产 id（顺序即提示词里 `{{Text N}}` 的编号顺序） */
  textLibraryIds?: string[];
  /** 绑定的文案与版本快照（不含正文）：assetId / name / version / sha256 */
  textBindings?: Record<string, unknown>[];
  /** 创建时间（UTC） */
  createdAt: string;
}

/** 重试只回 id / status。

不回整份 `ImageJobOut`：持有 `image:retry:any` 的人可以帮别人重跑，
但没有 `image:read:all`，不该顺带读到别人的 prompt（与 task 的重试同一口径）。 */
export interface ImageRetryOut {
  /** 生图记录 ID */
  id: string;
  /** 重试后的状态，固定为 `queued` */
  status: string;
}

/** 生成入库结果。`created=False` = 命中幂等（sourceKey 重复或内容相同）。

不返回整条资产：调用方（生成侧）需要的是「拿到 id 去关联任务」，
而它可能并不持有 `library:read` 之外的权限去读详情。 */
export interface IngestOut {
  assetId: string;
  created: boolean;
}

/** 归档信息。只给所有者与管理员：存储键与幂等键对外没有意义，只会泄露内部结构。 */
export interface LibraryArchiveOut {
  storageKey?: string;
  sha256?: string;
  sourceKey?: string;
  sizeBytes?: number;
  referenceCount?: number;
}

/** 详情 = 列表项 + 标签 + 生成参数（有才返回）。

生成参数用 `None` 而不是空对象：前端要区分「不是生成来的」与
「是生成来的但历史数据没记全」，两者显示逻辑不同。

M3 补齐详情页六个区的数据源（设计 §7.2）：

- 媒体区 → `packItems`（仅封装）与 `coverUrl`；
- 基础信息 → `state` / `trashedFromState` / `reviewedBy` / `rejectReason`；
- 媒体规格 → 继承的 `sizeBytes` / `durationSec` / `width`… 由前端自己排；
- 生成参数 → `generation`；
- 使用与引用 → `references` + `shareHistory`；
- 归档信息 → `archive`（仅所有者 / 管理员可见）。 */
export interface LibraryAssetDetailOut {
  id: string;
  name: string;
  kind: LibraryAssetKind;
  /** upload / generated / template_source / imported / pack */
  origin: string;
  mimeType?: string | null;
  sizeBytes?: number;
  durationSec?: number | null;
  hasAudio?: boolean;
  ownerId: string;
  ownerName?: string | null;
  visibility: LibraryVisibility;
  state: LibraryAssetState;
  stateVersion?: number;
  coverUrl?: string | null;
  url?: string;
  previewUrl?: string | null;
  streamUrl?: string | null;
  width?: number | null;
  height?: number | null;
  groupIds?: string[];
  tags?: LibraryTagOut[];
  trashReason?: string | null;
  trashExpiresAt?: string | null;
  textExcerpt?: string | null;
  textVersion?: number;
  textUrl?: string | null;
  createdAt: string;
  updatedAt?: string | null;
  groups?: LibraryGroupRef[];
  generation?: LibraryGenerationOut | null;
  packItems?: PackItemOut[];
  references?: string[] | null;
  shareHistory?: ShareHistoryItem[];
  archive?: LibraryArchiveOut | null;
  reviewedBy?: string | null;
  reviewedAt?: string | null;
  rejectReason?: string | null;
  trashedFromState?: string | null;
}

/** 列表项。字段对齐 `library_assets` 表，M1 建表后不再**改**形状。

后面补的五个字段是「只增不改」：取件地址与媒体规格本来就在表里/由 id 决定，
以前让前端自己拼 URL、自己猜哪套库。模板库切换时前端只要改接口调用，
不必在业务组件里补一段「按 id 拼 /file」的适配代码。 */
export interface LibraryAssetOut {
  id: string;
  name: string;
  kind: LibraryAssetKind;
  /** upload / generated / template_source / imported / pack */
  origin: string;
  mimeType?: string | null;
  sizeBytes?: number;
  durationSec?: number | null;
  hasAudio?: boolean;
  ownerId: string;
  ownerName?: string | null;
  visibility: LibraryVisibility;
  state: LibraryAssetState;
  stateVersion?: number;
  coverUrl?: string | null;
  url?: string;
  previewUrl?: string | null;
  streamUrl?: string | null;
  width?: number | null;
  height?: number | null;
  groupIds?: string[];
  tags?: LibraryTagOut[];
  trashReason?: string | null;
  trashExpiresAt?: string | null;
  textExcerpt?: string | null;
  textVersion?: number;
  textUrl?: string | null;
  createdAt: string;
  updatedAt?: string | null;
}

/** 列表响应 = 标准分页 + 分面统计。

继承 `Page` 而不是复制 `items/total/page/pageSize`：前端的通用分页组件
认的是那四个字段，复制一遍迟早出现「这个页面 pageSize 拼错成 page_size」。 */
export interface LibraryAssetPage {
  items?: LibraryAssetOut[];
  total?: number;
  page?: number;
  pageSize?: number;
  facets?: LibraryFacets;
}

/** 分面统计：五个一级入口的条数，前端直接渲染成侧栏角标。

与 `items` 同一次查询算出，不额外发起请求——否则切换 tab 时角标会跳。 */
export interface LibraryFacets {
  all?: number;
  personal?: number;
  group?: number;
  team?: number;
  drafts?: number;
  trash?: number;
}

/** 生成溯源快照。入库那一刻冻结，不联表查；缺失字段前端显示「未记录」。 */
export interface LibraryGenerationOut {
  sourceModule: string;
  sourceTaskId: string;
  model?: string;
  modelId?: string;
  prompt?: string;
  size?: string;
  count?: number;
  durationSec?: number;
  aspectRatio?: string;
  stylePreset?: string;
  seed?: string;
  referenceLibraryIds?: string[];
  packId?: string;
  packVersion?: number;
  promptSpec?: Record<string, unknown>;
  configuration?: Record<string, unknown>;
  externalId?: string;
  credits?: number;
  generatedAt?: string | null;
}

/** 我的小组。`isLeader` 现在只用于删除窄口（§3.4.1）的入口展示；
「待我审核」按审批权限点与「是不是该组成员」收窄，与组长身份无关。 */
export interface LibraryGroupOut {
  id: string;
  name?: string;
  isLeader?: boolean;
}

/** 资产共享到的小组。名称走组织目录注入口，不在本模块 join 身份的表。 */
export interface LibraryGroupRef {
  id: string;
  name?: string;
}

/** 选择器返回：只含 `state=active` 的条目，不带分面（选择器不显示角标）。 */
export interface LibrarySelectionPage {
  items?: LibraryAssetOut[];
  total?: number;
  page?: number;
  pageSize?: number;
}

/** 词表里的一条标签。**固定两级**，不返回嵌套树。

为什么仍是平铺数组：标签栏、卡片、打标选择器、上传弹窗、批量栏、素材库
悬浮栏六个消费点全都按平铺渲染；改成嵌套树要全量重写，而且「计数与排序」
会跟着乱。`parentId` + `depth` 足够让前端自己分两行、显示 `产品 / 3030`。

库里用空串表示一级（见 models.py），出参统一转成 `null`：前端按真假值判断
层级就行，不必知道哨兵串的存在。 */
export interface LibraryTagOut {
  id: string;
  name: string;
  parentId?: string | null;
  parentName?: string | null;
  depth?: number;
}

/** 标签栏的列表项：比卡片上的标签多「素材数」「直属子标签数」与「添加时间」。

`asset_count` 是**当前入口**（`state` + `library`）下挂了这个标签的素材数，
不是全库总量——标签栏上点一下就按这个入口筛，数字必须与结果对得上。
一级标签的数字是「自身 + 旗下二级」按资产去重后的合计，与「点一级展开到
子级」的筛选口径一致；否则会出现「标签旁写 8、点进去只有 3 条」。

`child_count` 给前端判断「这是一级分组还是可打标的叶子」。
`created_at` 给前端按「添加时间先后」排序用。

单独出一个类型而不是往 `LibraryTagOut` 上加这几个字段：卡片里 24 条资产
每条都带一遍计数与时间戳，既没用也容易被人误当成卡片上的真实数据。 */
export interface LibraryTagStatOut {
  id: string;
  name: string;
  parentId?: string | null;
  parentName?: string | null;
  depth?: number;
  assetCount?: number;
  childCount?: number;
  createdAt: string;
}

/** LoginEventListOut */
export interface LoginEventListOut {
  /** 按时间倒序 */
  items?: LoginEventOut[];
}

/** 一次登录尝试。审计用，只读。

字段名 `at` 与库里的 `created_at` 不同：契约里这一列叫 `at`（前端需求文档的写法），
转换放在 service 里显式做一次，比给 `created_at` 起两个别名好读。 */
export interface LoginEventOut {
  /** 发生时间（UTC） */
  at: string;
  /** 是否成功 */
  success: boolean;
  /** 来源 IP */
  ip?: string;
  /** 客户端 UA */
  userAgent?: string;
  /** 失败原因：`bad_password` / `disabled` / `rate_limited`；成功为 null */
  reason?: string | null;
}

/** LoginIn */
export interface LoginIn {
  /** 用户名 */
  username: string;
  /** 密码 */
  password: string;
}

/** ManifestDraftIn */
export interface ManifestDraftIn {
  key: string;
  name: string;
  description?: string;
  video: ManifestVideoIn;
  packs?: ManifestPackIn[];
  functions: ManifestFunctionIn[];
}

/** ManifestFunctionIn */
export interface ManifestFunctionIn {
  key: string;
  name: string;
  pack?: string | null;
  kind?: "product" | "character" | "scene" | null;
  scenePromptDefault?: string | null;
  prompt: string;
  mediaOrder?: string[];
  bindings?: Record<string, string> | null;
  scopeOptions?: string[];
  aspectRatio?: "9:16" | "16:9" | "1:1";
  duration?: number | null;
}

/** ManifestListOut */
export interface ManifestListOut {
  items?: ManifestOut[];
  total?: number;
}

/** 配置详情。嵌套体直接复用入参模型：形状一致，不另造一份会漂移的出参。 */
export interface ManifestOut {
  id: string;
  key: string;
  name: string;
  description: string;
  ownerId: string;
  video: ManifestVideoIn;
  status: string;
  version: number;
  revision: number;
  fingerprint: string;
  publishedAt?: string | null;
  createdAt: string;
  updatedAt: string;
  packs?: ManifestPackIn[];
  functions?: ManifestFunctionIn[];
}

/** ManifestPackIn */
export interface ManifestPackIn {
  key: string;
  name?: string;
  kind: "product" | "character" | "scene";
  packId?: string | null;
  references?: ManifestReferenceIn[];
  requiredRoles?: string[];
  supportedScopes?: string[];
  wardrobeMode?: "preserve" | "reference";
}

/** 发布回执：`templateId` 即工作台卡片 id，前端拼 `workbenchPath` 用。 */
export interface ManifestPublishOut {
  manifest: ManifestOut;
  version: number;
  templates?: PublishedTemplateOut[];
}

/** ManifestReferenceIn */
export interface ManifestReferenceIn {
  key: string;
  assetId: string;
  role: string;
  description?: string;
}

/** 只预检不落库：返回会用于幂等判定的配置指纹。 */
export interface ManifestValidateOut {
  valid?: boolean;
  fingerprint: string;
}

/** ManifestVideoIn */
export interface ManifestVideoIn {
  assetId: string;
  shared?: boolean;
}

/** `GET /identity/me`：前端靠 `permissions` 渲染菜单与按钮。 */
export interface MeOut {
  /** 用户 ID */
  id: string;
  /** 登录名，入库前已转小写 */
  username: string;
  /** 昵称（顶栏展示用） */
  displayName?: string;
  /** 邮箱，未填为 null */
  email?: string | null;
  /** 头像 URL，未设置为 null */
  avatarUrl?: string | null;
  /** 手机号，仅展示，未填为 null */
  phone?: string | null;
  /** 职务，仅展示，未填为 null */
  position?: string | null;
  /** 是否启用；停用后无法登录且令牌立即失效 */
  isActive?: boolean;
  /** 最后登录时间，从未登录为 null */
  lastLoginAt?: string | null;
  /** 创建时间（UTC） */
  createdAt: string;
  /** 拥有的角色 */
  roles?: RoleBriefOut[];
  /** 展开后的权限码；含 `*` 表示超级管理员 */
  permissions?: string[];
}

/** PackAction */
export interface PackAction {
  expectedRevision: number;
}

/** 一张参考图在封装里的用途；description 只补充本次任务特有的事实。 */
export interface PackBinding {
  role?: string;
  description?: string;
}

/** PackDraftIn */
export interface PackDraftIn {
  requestId: string;
  expectedRevision?: number;
  kind: "character" | "product" | "scene";
  name?: string;
  /** 封装用的参考图 id，按顺序 */
  referenceLibraryIds?: string[];
  /** 封面的 asset id；必须出现在 referenceLibraryIds 里 */
  coverLibraryId?: string | null;
  wardrobeMode?: "preserve" | "reference";
  features?: string;
  instructions?: string;
  bindings?: PackBinding[];
  requiredRoles?: string[];
  supportedScopes?: string[];
}

/** 建封装。

`items` 是**唯一有序来源**；`assetIds` 只是给「勾选一批直接封装」用的简写，
会被追加到 `items` 之后（已在 items 里的不重复加）。两者都空 → 400。 */
export interface PackIn {
  name: string;
  assetIds?: string[];
  items?: PackItemIn[];
  coverAssetId?: string;
  tagIds?: string[];
}

/** 封装成员。`role` 预留给模板替换（产品主图 / 多视结构图…），首版可为空。 */
export interface PackItemIn {
  assetId: string;
  role?: string;
  description?: string;
}

/** 封装里的一项。成员可能是别人共享给我的素材，所以带 `state` 与 `coverUrl`。 */
export interface PackItemOut {
  assetId: string;
  name?: string;
  kind: LibraryAssetKind;
  state: LibraryAssetState;
  position: number;
  role?: string;
  description?: string;
  sizeBytes?: number;
  coverUrl?: string | null;
}

/** PackManaged */
export interface PackManaged {
  id: string;
  kind: "character" | "product" | "scene";
  name: string;
  referenceIds: string[];
  coverReferenceId?: string | null;
  wardrobeMode?: "preserve" | "reference";
  features?: string;
  instructions?: string;
  version?: number;
  available?: boolean | null;
  coverUrl?: string | null;
  previewUrls?: string[];
  referenceNames?: string[];
  bindings?: PackBinding[];
  requiredRoles?: string[];
  supportedScopes?: string[];
  revision?: number;
  status?: "draft" | "published" | "disabled";
  gaps?: string[];
}

/** PackManagedList */
export interface PackManagedList {
  items: PackManaged[];
}

/** 改封装。`items` 提供即**整体替换**（不是增量追加）——增量语义要前端自己
算差集，而算错了就是悄悄多一个成员，不如让调用方给全量。 */
export interface PackPatchIn {
  name?: string | null;
  items?: PackItemIn[] | null;
  coverAssetId?: string | null;
  tagIds?: string[] | null;
  expectedVersion?: number;
}

/** PackPublicList */
export interface PackPublicList {
  items: ReplacementPack[];
  total: number;
  page: number;
  pageSize: number;
}

/** PasswordChangeIn */
export interface PasswordChangeIn {
  /** 当前密码 */
  oldPassword: string;
  /** 新密码；至少 10 位，不能是纯数字，且不能与当前密码相同 */
  newPassword: string;
}

/** 口令策略下发。**文案也由后端给**：前端四处表单不该各拼一份。 */
export interface PasswordPolicyOut {
  /** 最短长度 */
  minLength: number;
  /** 最长长度 */
  maxLength: number;
  /** 是否禁止纯数字 */
  forbidAllNumeric: boolean;
  /** 是否禁止与用户名相同；当前未启用，前端按 false 处理 */
  forbidSameAsUsername: boolean;
  /** 可直接展示的一句话，前端不要自己拼 */
  message: string;
}

/** PermissionCatalogOut */
export interface PermissionCatalogOut {
  /** 按分组组织的全部权限点，角色编辑界面直接渲染 */
  groups?: PermissionGroupOut[];
  /** 权限点总数 */
  total?: number;
}

/** PermissionGroupOut */
export interface PermissionGroupOut {
  /** 分组名，如「任务」「身份」 */
  group: string;
  /** 该组下的权限点 */
  items?: PermissionOut[];
}

/** PermissionOut */
export interface PermissionOut {
  /** 权限码，如 `task:create` */
  code: string;
  /** 权限名称 */
  name: string;
  /** 权限说明 */
  description?: string;
}

/** 团队 / 小组 / 个人三层共用的「整组替换」入参。

三层共用一份形状不是偷懒：写接口的守卫、权限码合法性校验、以及前端的
权限勾选组件对三层完全一样，分成三个类只会让「改了一处忘两处」变大概率事件。 */
export interface PermissionsIn {
  /** 整组替换；传空数组即清空。出现未登记的权限码会被拒绝 */
  permissions?: string[];
}

/** PrepareReplacement */
export interface PrepareReplacement {
  requestId: string;
  channel?: "libtv" | "bailian" | "bailian-prime";
  kind: "character" | "product" | "scene";
  target?: string;
  /** 原片（资产库里的视频）id */
  sourceLibraryId?: string | null;
  videoTemplateId?: string | null;
  /** 文字换场景的描述；省略使用模板默认值，空白拒绝 */
  scenePrompt?: string | null;
  referenceIds?: string[];
  /** 资产库 v2 封装的 id；成员按 position 顺序当参考图，用途取自成员自己声明的 role。与 packId / packVersion / referenceIds / extraPackIds 互斥；prepare 时展开并冻结，之后封装改成员不影响已准备的任务 */
  libraryPackId?: string | null;
  /** 绑定的文本资产 id，按顺序对应 `{{Text N}}`；最多 8 份 */
  textLibraryIds?: string[];
  /** 第 2 位及以后的素材包 id，按提示词槽位顺序；第一包仍用 packId */
  extraPackIds?: string[];
  /** 与 extraPackIds 顺序对应的发布版本；旧调用省略时由服务端解析并冻结 */
  extraPackVersions?: number[];
  wardrobeMode?: "preserve" | "reference";
  replacementScope?: string;
  notes?: string;
  packId?: string | null;
  packVersion?: number | null;
  aspectRatio?: "9:16" | "16:9" | "1:1" | "adaptive";
}

/** `PATCH /identity/me` 的自助可写字段。

刻意**不含** `is_active`：停用是管理动作，用户误点一下就把自己锁在门外，
而恢复只有管理员一个入口。自助路径能改的只有「展示信息」。 */
export interface ProfileUpdateIn {
  /** 昵称 */
  displayName?: string | null;
  /** 邮箱；传 null 清空 */
  email?: string | null;
  /** 头像 URL；传 null 清空 */
  avatarUrl?: string | null;
  /** 手机号；传 null 清空 */
  phone?: string | null;
  /** 职务；传 null 清空 */
  position?: string | null;
}

/** PublishedTemplateOut */
export interface PublishedTemplateOut {
  functionKey: string;
  templateId: string;
  templateKey: string;
  name: string;
  workbenchPath: string;
}

/** 就绪状态。`checks` 说明每一项依赖的结论，status ∈ ok / degraded / down。 */
export interface ReadyOut {
  status: string;
  database: string;
  queueSize: number;
  checks?: RuntimeCheckOut[];
}

/** 确认「上一次提交可能已经花了钱」后才允许重提。

默认 False：一次误点就变成第二次计费，是这套流程里最贵的一个操作。 */
export interface ReconfirmReplacement {
  /** 确认上一次提交可能已产生费用，仍要重新提交（默认拒绝） */
  confirmCharged?: boolean;
}

/** RefreshIn */
export interface RefreshIn {
  /** 登录时拿到的刷新令牌 */
  refreshToken: string;
}

/** ReplacementCatalog */
export interface ReplacementCatalog {
  channels?: ReplacementChannelOut[];
  provider?: string;
  maxCredits?: number;
  kinds?: string[];
  runs: ReplacementRunOut[];
  packs?: ReplacementPack[];
  packRoles?: Record<string, Record<string, string>>;
  baseRoles?: Record<string, string>;
  packScopes?: Record<string, string>;
}

/** ReplacementChannelOut */
export interface ReplacementChannelOut {
  id: "libtv" | "bailian" | "bailian-prime";
  label: string;
  model: string;
  enabled: boolean;
  billing: "libtv" | "aliyun";
  unavailableReason?: string;
}

/** ReplacementInputAsset */
export interface ReplacementInputAsset {
  id: string;
  name: string;
  role: string;
  type: string;
  url: string;
  /** 当次输入的 Mixed 编号，无法从历史快照确定时留空 */
  token?: string;
  /** 当次快照中记录的媒体用途说明 */
  usage?: string;
  /** 授权读取当次输入快照的缩略图 */
  previewUrl?: string | null;
}

/** ReplacementInputSummary */
export interface ReplacementInputSummary {
  assets?: ReplacementInputAsset[];
  coverReferenceId?: string | null;
  wardrobeMode?: string;
  replacementScope?: string;
  notes?: string;
  /** 本次实际使用的可编辑场景描述 */
  scenePrompt?: string | null;
}

/** ReplacementPack */
export interface ReplacementPack {
  id: string;
  kind: "character" | "product" | "scene";
  name: string;
  referenceIds: string[];
  coverReferenceId?: string | null;
  wardrobeMode?: "preserve" | "reference";
  features?: string;
  instructions?: string;
  version?: number;
  available?: boolean | null;
  coverUrl?: string | null;
  previewUrls?: string[];
  referenceNames?: string[];
  bindings?: PackBinding[];
  requiredRoles?: string[];
  supportedScopes?: string[];
}

/** ReplacementReview */
export interface ReplacementReview {
  status?: string;
  version?: number;
  reasonCodes?: ("replacement" | "preservation" | "visual" | "audio" | "other")[];
  comment?: string;
  reviewedBy?: string | null;
  reviewedAt?: string | null;
  history?: ReplacementReviewEvent[];
}

/** ReplacementReviewEvent */
export interface ReplacementReviewEvent {
  status?: string;
  version?: number;
  reasonCodes?: ("replacement" | "preservation" | "visual" | "audio" | "other")[];
  comment?: string;
  reviewedBy?: string | null;
  reviewedAt?: string | null;
}

/** ReplacementReviewIn */
export interface ReplacementReviewIn {
  status: "accepted" | "rejected";
  expectedVersion: number;
  reasonCodes?: ("replacement" | "preservation" | "visual" | "audio" | "other")[];
  comment?: string;
}

/** ReplacementRunOut */
export interface ReplacementRunOut {
  id: string;
  channel?: "libtv" | "bailian" | "bailian-prime";
  channelLabel?: string;
  kind: "character" | "product" | "scene";
  target: string;
  status: string;
  /** 工作区绝对路径（排查用）；目录已被清理或换机器后为 null */
  archivePath?: string | null;
  createdAt: string;
  /** 确认提交（进入排队）的时间，未开始为 null */
  startedAt?: string | null;
  /** 这一轮结束的时间；未结束为 null */
  finishedAt?: string | null;
  /** 确认提交到结束的耗时（秒）；未完成时按到此刻计，未开始为 null */
  elapsedSec?: number | null;
  prompt: string;
  /** 实际发出去那段提示词的 sha256（所有后处理之后）。档案里另外两个哈希（template/spec）哈希的是模板与输入，复核不了最终字节。加这列之前准备的任务为 null，不回填。 */
  promptSha256?: string | null;
  /** 本次绑定的文本资产 id（顺序即 `{{Text N}}` 编号） */
  textLibraryIds?: string[];
  /** 绑定的文案与版本快照（不含正文） */
  textBindings?: Record<string, unknown>[];
  /** fixed=封装视频模板时写死的原文，逐字提交；compiled=按素材与替换范围现编。展示层据此告诉用户「跑的就是你封装时那份提示词」。 */
  promptMode?: "fixed" | "compiled";
  /** fixed 时出自哪条视频模板；compiled 为 null */
  videoTemplateName?: string | null;
  /** 统一展示名，列表/弹窗/任务中心共用 */
  displayTitle?: string;
  taskId?: string | null;
  estimatedCredits?: number | null;
  credits?: number | null;
  progress?: number;
  reviewStatus?: string;
  review?: ReplacementReview | null;
  inputSummary?: ReplacementInputSummary | null;
  lastError?: string | null;
  outputUrl?: string | null;
  deletedAt?: string | null;
  /** free=0 积分权益队列；credits=本次任务用积分进正常队列 */
  billingMode?: "free" | "credits";
}

/** ResetPasswordIn */
export interface ResetPasswordIn {
  /** 管理员设置的新密码；至少 10 位，不能是纯数字 */
  newPassword: string;
}

/** 吊销会话的结果。两个「踢下线」类接口都返回这个形状。 */
export interface RevokedSessionsOut {
  /** 本次吊销的会话数；已经失效的会话不计入 */
  revoked: number;
}

/** 用户出参里内嵌的角色摘要。 */
export interface RoleBriefOut {
  /** 角色 ID */
  id: string;
  /** 角色标识，如 `editor` */
  code: string;
  /** 角色名称 */
  name: string;
  /** 是否超级管理员角色（权限展开为 `*`） */
  isSuperuser?: boolean;
}

/** RoleCreateIn */
export interface RoleCreateIn {
  /** 角色标识，唯一且不可改 */
  code: string;
  /** 角色名称 */
  name: string;
  /** 角色说明 */
  description?: string;
  /** 权限码列表 */
  permissions?: string[];
  /** 仅超级管理员可设置 */
  isSuperuser?: boolean;
}

/** RoleOut */
export interface RoleOut {
  /** 角色 ID */
  id: string;
  /** 角色标识，唯一；内置角色不可改 */
  code: string;
  /** 角色名称 */
  name: string;
  /** 角色说明 */
  description?: string;
  /** 持有即展开为 `*`，拥有全部权限 */
  isSuperuser?: boolean;
  /** 内置角色：不可删除 */
  isBuiltin?: boolean;
  /** 排序值，小的在前 */
  sortOrder?: number;
  /** 创建时间（UTC） */
  createdAt: string;
  /** 角色拥有的权限码 */
  permissions?: string[];
  /** 使用该角色的用户数 */
  userCount?: number;
  /** 已失效的权限码（权限点被删或改名后残留） */
  orphanedPermissions?: string[];
}

/** RolePermissionsIn */
export interface RolePermissionsIn {
  /** 整组替换；出现未登记的权限码会被拒绝 */
  permissions?: string[];
}

/** RoleUpdateIn */
export interface RoleUpdateIn {
  /** 角色名称 */
  name?: string | null;
  /** 角色说明 */
  description?: string | null;
  /** 仅超级管理员可设置 */
  isSuperuser?: boolean | null;
}

/** RuntimeCheckOut */
export interface RuntimeCheckOut {
  name: string;
  ok: boolean;
  detail: string;
  required?: boolean;
}

/** 一条登录会话。前端「我的登录设备」列表用它。 */
export interface SessionOut {
  /** 会话 ID，踢下线时用它 */
  id: string;
  /** 登录时的 UA，未记录为空串 */
  userAgent?: string;
  /** 登录时的来源 IP */
  ip?: string;
  /** 最近一次续期时间，从未续期为 null */
  lastSeenAt?: string | null;
  /** 刷新令牌到期时间（UTC） */
  expiresAt: string;
  /** 登录时间（UTC） */
  createdAt: string;
  /** 是否是发起本次请求的会话；前端据此标「本机」 */
  current?: boolean;
}

/** 审批 / 驳回 / 撤回申请单。`note` 会进申请单与审计。 */
export interface ShareDecisionIn {
  note?: string;
  expectedVersion?: number;
}

/** 可见性变更的一条记录。`reason` 是撤销申请里填的理由，审核人当时看的就是它。 */
export interface ShareHistoryItem {
  id: string;
  action: ShareAction;
  target: ShareTarget;
  groupId?: string;
  groupName?: string;
  status: string;
  applicantId: string;
  applicantName?: string | null;
  reviewerId?: string | null;
  reviewerName?: string | null;
  reason?: string;
  note?: string;
  decidedAt?: string | null;
  createdAt: string;
}

/** 发起共享或撤销共享。

`action=revoke` 必须带 `reason`：撤销会让正在用这个素材的同事突然拿不到文件，
理由要留在申请单里（设计 §3.5）。 */
export interface ShareRequestIn {
  assetId: string;
  action?: ShareAction;
  target?: ShareTarget;
  groupId?: string;
  reason?: string;
  expectedVersion?: number;
}

/** ShareRequestOut */
export interface ShareRequestOut {
  id: string;
  assetId: string;
  assetName?: string;
  applicantId: string;
  applicantName?: string | null;
  action: ShareAction;
  target: ShareTarget;
  groupId?: string;
  groupName?: string | null;
  status: string;
  reviewerId?: string | null;
  note?: string;
  reason?: string;
  decidedAt?: string | null;
  createdAt: string;
}

/** ShotListOut */
export interface ShotListOut {
  items?: ShotPresetOut[];
  total?: number;
}

/** ShotPresetOut */
export interface ShotPresetOut {
  id: string;
  name: string;
  promptFragment: string;
  durationSec: number;
  coverUrl: string;
  popularity: number;
}

/** 填表结果：`{slotKey: [assetId…]}`，可带一份改写后的可编辑段提示词。

`source=pack` 的槽位传一个封装 id；`source=asset` 的槽位按用户挑选顺序传。
`version` 为空表示用当前发布版本。

`promptOverride` 是**填表时改写的 `editable` 段**（`locked` 段永不可改）。
不传 = 用发布版本里那份原文。模板的 `editable` 为空（提示词全固定）时传它会 400 ——
那是模板作者明确关掉的入口，不该由调用方绕开。 */
export interface SpecCompileIn {
  version?: number | null;
  slotValues?: Record<string, string[]>;
  /** 改写后的可编辑段提示词；`locked` 段不受影响 */
  promptOverride?: string | null;
}

/** 编译结果（模板层）。

**不是**任何端点的请求体：`/tasks` 的时长与画幅是顶层字段、不接受自由
`params`。摊平由提交方按目标端点做（模板库页面对照生成的 `CreateTaskIn` 类型），
这里刻意不假设端点，否则换端点就要改编译规则。 */
export interface SpecCompileOut {
  version: number;
  model: string;
  prompt: string;
  params: Record<string, unknown>;
  referenceLibraryIds?: string[];
  textLibraryIds?: string[];
  slots?: CompiledSlotOut[];
}

/** SpecPromptIn */
export interface SpecPromptIn {
  locked?: string;
  editable?: string;
}

/** SpecPublishOut */
export interface SpecPublishOut {
  version: number;
  spec: TemplateSpecOut;
}

/** SpecSlotIn */
export interface SpecSlotIn {
  key: string;
  type: "image" | "video" | "audio" | "text";
  source?: "asset" | "pack";
  binding?: "fixed" | "user";
  refMode?: "mixed" | "attachment";
  minCount?: number;
  maxCount?: number;
  label?: string;
  usage?: string;
  assetId?: string | null;
  packId?: string | null;
  position?: number;
}

/** 槽位出参：与入参同形。 */
export interface SpecSlotOut {
  key: string;
  type: "image" | "video" | "audio" | "text";
  source?: "asset" | "pack";
  binding?: "fixed" | "user";
  refMode?: "mixed" | "attachment";
  minCount?: number;
  maxCount?: number;
  label?: string;
  usage?: string;
  assetId?: string | null;
  packId?: string | null;
  position?: number;
}

/** SpecValidateOut */
export interface SpecValidateOut {
  valid?: boolean;
  fingerprint: string;
}

/** 一次发布快照。`slots` 的顺序就是编号顺序（`position` 升序返回）。 */
export interface SpecVersionOut {
  version: number;
  model: string;
  params: Record<string, unknown>;
  prompt: SpecPromptIn;
  slots: SpecSlotOut[];
  fingerprint?: string;
  publishedBy?: string;
  publishedAt?: string | null;
}

/** StoryboardListOut */
export interface StoryboardListOut {
  items?: StoryboardOut[];
  total?: number;
}

/** StoryboardOut */
export interface StoryboardOut {
  id: string;
  name: string;
  description: string;
  shotCount: number;
  durationSec: number;
  aspectRatio: AspectRatio;
  coverUrl: string;
  createdAt: string;
}

/** SubmitIn */
export interface SubmitIn {
  model: string;
  prompt: string;
  name?: string;
  params?: Record<string, unknown>;
  referenceUrls?: string[];
  source?: string;
}

/** 新建 / 重命名标签。

`parent_id` **只在新建时生效**：给一个一级标签的 id 就建成二级，留 null
建成一级。改名时不改层级，走 `PATCH /tags/{id}/parent`——把改名与移动混在
一个接口里，会让「只想改个错别字」的请求顺手改变整棵树的形状。 */
export interface TagIn {
  name: string;
  parentId?: string | null;
}

/** 移动标签：`parentId` 指向一个一级标签，`null` 表示提升为一级。 */
export interface TagParentIn {
  parentId?: string | null;
}

/** TaskFeedItem */
export interface TaskFeedItem {
  /** 带来源前缀的唯一键，不是上游任务 ID */
  key: string;
  /** 原始任务、生图记录或替换档案 ID */
  id: string;
  /** 任务来源 */
  source: "video" | "image" | "replacement";
  /** 展示标题 */
  title: string;
  /** 原始状态，保留各链路的精确状态 */
  status: string;
  /** 统一筛选分组；running 包含排队 */
  group: "all" | "queued" | "running" | "completed" | "failed" | "attention";
  /** 创建时间 */
  createdAt: string;
  /** 生成进度 */
  progress?: number;
  /** 替换原片名称 */
  sourceName?: string;
  /** 成片 / 成图地址，三路都已按当前存储驱动现签；没有产物时为 null */
  outputUrl?: string | null;
  /** 失败或需处理原因 */
  errorMessage?: string | null;
  /** 来源页面的深链接 */
  detailUrl?: string | null;
  /** 通用视频任务数据；其他来源为 null */
  video?: modules__task__schema__TaskOut | null;
  /** 生图任务数据；其他来源为 null */
  image?: ImageFeedItem | null;
}

/** TaskFeedOut */
export interface TaskFeedOut {
  items?: TaskFeedItem[];
  total?: number;
  page?: number;
  pageSize?: number;
  /** 筛选前分组统计 */
  stats?: TaskFeedStats;
  /** 可否查看他人的通用任务 */
  canReadAll?: boolean;
  /** 部分来源不可用时的提示 */
  warnings?: string[];
}

/** TaskFeedStats */
export interface TaskFeedStats {
  /** 总数 */
  all?: number;
  /** 正在执行，包含排队 */
  running?: number;
  /** 生成完成，不代表人工验收通过 */
  completed?: number;
  /** 执行失败 */
  failed?: number;
  /** 等待核对、免费额度、登录或人工处理 */
  attention?: number;
}

/** TaskListOut */
export interface TaskListOut {
  items?: modules__task__schema__TaskOut[];
  total?: number;
  page?: number;
  pageSize?: number;
  /** 侧边栏角标用的分组计数 */
  stats?: TaskStats;
  /** 当前账号能否查看他人任务；false 时前端应收起「全部」选项 */
  canReadAll?: boolean;
}

/** 重试的结果：只描述「这一次动作」。

刻意**不**返回整份 `TaskOut`（R6-05）：`task:retry:any` 与 `task:read:all`
是两个独立开关，客服只需要「帮别人重跑一次」，不该顺带获得
「按 id 读任意失败任务的 prompt 与作者」这条读通道。
重试是一个动作，不是一次查询——前端拿到结果后刷新列表即可。 */
export interface TaskRetryOut {
  /** 任务 ID */
  id: string;
  /** 重试后的状态，正常为 queued */
  status: TaskStatus;
  /** 状态变更时间（UTC），即重新排队的时间 */
  updatedAt: string;
}

/** 各状态任务数：不受状态筛选影响，但受搜索/范围影响。 */
export interface TaskStats {
  /** 总数 */
  all?: number;
  /** 排队中 */
  queued?: number;
  /** 生成中 */
  running?: number;
  /** 已完成 */
  completed?: number;
  /** 失败 */
  failed?: number;
}

/** 一份规格（草稿的整份内容）。`key` 是幂等边界，发布后不可改名。 */
export interface TemplateSpecIn {
  key: string;
  name: string;
  description?: string;
  model: string;
  params?: Record<string, unknown>;
  prompt: SpecPromptIn;
  slots?: SpecSlotIn[];
  attributes?: Record<string, string>;
  revision?: number | null;
}

/** TemplateSpecListOut */
export interface TemplateSpecListOut {
  items?: TemplateSpecOut[];
  total?: number;
}

/** TemplateSpecOut */
export interface TemplateSpecOut {
  id: string;
  key: string;
  name: string;
  description: string;
  ownerId: string;
  status: "draft" | "published" | "disabled";
  revision: number;
  fingerprint: string;
  currentVersion: number;
  publishedAt?: string | null;
  disabledAt?: string | null;
  createdAt: string;
  updatedAt: string;
  draft?: TemplateSpecIn | null;
  latest?: SpecVersionOut | null;
}

/** 站内新建一份文本资产（不传文件，直接写正文）。

`extension` 只认 md / txt：文件名后缀决定 `mime_type` 与内容寻址 key 的后缀，
不能由用户随便给一个 `.html`——那会让下载下来的东西被浏览器当页面执行。 */
export interface TextAssetIn {
  name: string;
  content: string;
  extension?: string;
  tagIds?: string[];
}

/** 文本正文 + 当前版本。详情页与编辑器用，不进列表。 */
export interface TextAssetOut {
  id: string;
  name: string;
  content: string;
  version: number;
  sha256: string;
  sizeBytes: number;
  excerpt: string;
  updatedAt?: string | null;
}

/** 把某一历史版本重新写成**新的一版**，不改历史行。 */
export interface TextAssetRestoreIn {
  version: number;
  expectedVersion?: number;
}

/** 在线编辑。`expected_version` 是**内容版本**，不是 `stateVersion`：
两个人同时打开编辑框时，后保存的人必须看到「正文已被改过」而不是静默覆盖。 */
export interface TextAssetUpdateIn {
  content: string;
  expectedVersion?: number;
  note?: string;
}

/** 一条历史版本。列表默认不带正文，按版本读时才带。 */
export interface TextAssetVersionOut {
  version: number;
  sha256: string;
  sizeBytes: number;
  excerpt: string;
  note?: string;
  createdBy: string;
  createdAt: string;
  content?: string | null;
}

/** TokenOut */
export interface TokenOut {
  /** 访问令牌，放进 `Authorization: Bearer <token>` */
  accessToken: string;
  /** 令牌类型，固定 Bearer */
  tokenType?: string;
  /** 有效期（秒），过期后请求返回 401 */
  expiresIn?: number;
  /** 刷新令牌，只用来换新的 accessToken，不能访问业务接口 */
  refreshToken?: string;
  /** 刷新令牌有效期（秒）；续期时滑动延长 */
  refreshExpiresIn?: number;
  /** 登录用户，可省一次 `/me` 请求 */
  user: MeOut;
}

/** 局部更新（目前只开放展示属性，生成参数不可改，需重试）。 */
export interface UpdateTaskIn {
  /** 新标题 */
  title?: string | null;
  /** 收藏状态 */
  favorited?: boolean | null;
}

/** UserCreateIn */
export interface UserCreateIn {
  /** 登录名，3-64 位字母数字或 `. _ -`，会被转成小写 */
  username: string;
  /** 初始密码，管理员指定；至少 10 位，不能是纯数字 */
  password: string;
  /** 昵称，留空则与用户名相同 */
  displayName?: string;
  /** 邮箱 */
  email?: string | null;
  /** 头像 URL */
  avatarUrl?: string | null;
  /** 手机号，仅展示 */
  phone?: string | null;
  /** 职务，仅展示 */
  position?: string | null;
  /** 角色 id 列表，可留空 */
  roleIds?: string[];
  /** 建号时加入的小组 id 列表，可留空；小组不存在会报 404 */
  groupIds?: string[];
}

/** UserOut */
export interface UserOut {
  /** 用户 ID */
  id: string;
  /** 登录名，入库前已转小写 */
  username: string;
  /** 昵称（顶栏展示用） */
  displayName?: string;
  /** 邮箱，未填为 null */
  email?: string | null;
  /** 头像 URL，未设置为 null */
  avatarUrl?: string | null;
  /** 手机号，仅展示，未填为 null */
  phone?: string | null;
  /** 职务，仅展示，未填为 null */
  position?: string | null;
  /** 是否启用；停用后无法登录且令牌立即失效 */
  isActive?: boolean;
  /** 最后登录时间，从未登录为 null */
  lastLoginAt?: string | null;
  /** 创建时间（UTC） */
  createdAt: string;
  /** 拥有的角色 */
  roles?: RoleBriefOut[];
}

/** UserRolesIn */
export interface UserRolesIn {
  /** 整组替换，传空数组即清空 */
  roleIds?: string[];
}

/** UserUpdateIn */
export interface UserUpdateIn {
  /** 昵称 */
  displayName?: string | null;
  /** 邮箱；传 null 清空 */
  email?: string | null;
  /** 头像 URL；传 null 清空 */
  avatarUrl?: string | null;
  /** 手机号；传 null 清空 */
  phone?: string | null;
  /** 职务；传 null 清空 */
  position?: string | null;
  /** 启用/停用；不能停用自己，且必须保留一个启用的超管。不接受 null：停用请传 false，不想改就不要传这个字段 */
  isActive?: boolean | null;
}

/** ValidationError */
export interface ValidationError {
  loc: (string | number)[];
  msg: string;
  type: string;
  input?: unknown;
  ctx?: Record<string, unknown>;
}

/** 把样片登记后的 source 资产绑定到模板：绑定后它才是「全部模板」里可选的卡片。 */
export interface VideoTemplateAttachSourceIn {
  /** 原片 id（资产库里的视频） */
  sourceVideoAssetId: string;
}

/** 封装一条新的视频模板。

`mediaOrder` 决定 mixedList 顺序（即 `{{Mixed N}}` 指向谁）：显式给出时按它拼，
旧模式必须原片第1位、其余图片与素材包同一批；用途绑定模式不传 mediaOrder，
服务端按 referenceBindings 生成，保留视频所在编号并完整附带辅助参考。 */
export interface VideoTemplateDraftIn {
  /** 模板名称 */
  name: string;
  /** 稳定标识：同键同配置返回原模板，同键异配置409；留空自动生成。固定蓝本状态独立管理 */
  templateKey?: string;
  /** 模板说明 */
  description?: string;
  /** 固定提示词原文，含 {{Mixed N}} 占位符 */
  prompt: string;
  /** 样片文件名 */
  sourceVideoName?: string;
  /** 样片在资产库中的 asset id */
  sourceVideoAssetId?: string | null;
  /** 样片本地路径（可选） */
  sourceVideoPath?: string;
  /** 样片真实时长，用于提示与档位说明 */
  sourceDurationSec?: number;
  /** 输出画幅 */
  aspectRatio?: string;
  /** 关联的已发布替换素材包；**可省略**——用途绑定模式下省略即「只声明槽位」，调用时按用途从用户自己选的包里取图 */
  packId?: string | null;
  /** 选中这张原片卡片时，替换配置默认切到的替换类型 */
  defaultKind?: "character" | "product" | "scene";
  /** 留空则由素材包顺序生成 */
  mediaOrder?: VideoTemplateMedia[];
  /** 生成参数，覆盖默认值 */
  generation?: Record<string, unknown>;
  /** 封装规则；referenceBindings 将连续 Mixed 编号绑定到 source_video/图片用途，服务端排列声明槽位并完整附带当前包其余图片，无需逐图引用 */
  rules?: Record<string, unknown>;
  /** 封面地址 */
  coverUrl?: string;
  /** 是否设为固定蓝本（全库仅一个） */
  pinned?: boolean;
}

/** VideoTemplateListOut */
export interface VideoTemplateListOut {
  items?: VideoTemplateOut[];
  total?: number;
  /** 当前固定蓝本的 id */
  pinnedId?: string | null;
}

/** mixedList 里的一项；下标 +1 就是 {{Mixed N}} 编号。 */
export interface VideoTemplateMedia {
  /** 形如 {{Mixed 3}}，与所在下标严格对应 */
  token: string;
  /** source_video / front / multiview / detail 等用途 */
  role?: string;
  /** video / image / audio */
  type?: string;
  /** 素材库 asset id */
  assetId?: string | null;
  /** 素材显示名 */
  name?: string;
  /** 本地文件相对路径（可选） */
  file?: string;
  /** 是否被提示词显式引用 */
  inPrompt?: boolean;
}

/** VideoTemplateOut */
export interface VideoTemplateOut {
  id: string;
  templateKey: string;
  name: string;
  description: string;
  prompt: string;
  /** 提示词 sha256，防止文字被悄悄改掉 */
  promptSha256: string;
  sourceVideoName: string;
  sourceVideoAssetId?: string | null;
  sourceVideoPath: string;
  sourceDurationSec: number;
  aspectRatio: string;
  packId?: string | null;
  packName?: string;
  packVersion?: number;
  defaultKind?: "character" | "product" | "scene";
  mediaOrder?: VideoTemplateMedia[];
  generation?: Record<string, unknown>;
  rules?: Record<string, unknown>;
  coverUrl?: string;
  pinned?: boolean;
  createdAt: string;
  updatedAt: string;
}

/** VideoTemplatePackReferenceIn */
export interface VideoTemplatePackReferenceIn {
  token: string;
  packId: string;
  packVersion: number;
  assetId: string;
}

/** 原地保存固定提示词及跨封装图片引用，不改原片、档位或默认素材包。

既有用途引用按新原文重算；跨封装引用锁定包与版本，图片追加到基础媒体之后。
不再点名的图片仍可作为辅助输入；references 省略时保留，显式空数组清除固定引用。 */
export interface VideoTemplatePromptIn {
  /** 新的固定提示词原文，含 {{Mixed N}} 占位符 */
  prompt: string;
  /** 跨封装固定图片引用；省略保留原绑定，空数组清除 */
  references?: VideoTemplatePackReferenceIn[] | null;
}

/** ApiResponse[ModelListOut] */
export interface core__response__ApiResponse_ModelListOut___1 {
  /** 0 表示成功，其余为业务错误码 */
  code?: number;
  message?: string;
  data?: modules__catalog__schema__ModelListOut | null;
}

/** ApiResponse[ModelListOut] */
export interface core__response__ApiResponse_ModelListOut___2 {
  /** 0 表示成功，其余为业务错误码 */
  code?: number;
  message?: string;
  data?: modules__model_driver__schema__ModelListOut | null;
}

/** ApiResponse[TaskOut] */
export interface core__response__ApiResponse_TaskOut___1 {
  /** 0 表示成功，其余为业务错误码 */
  code?: number;
  message?: string;
  data?: modules__model_driver__schema__TaskOut | null;
}

/** ApiResponse[TaskOut] */
export interface core__response__ApiResponse_TaskOut___2 {
  /** 0 表示成功，其余为业务错误码 */
  code?: number;
  message?: string;
  data?: modules__task__schema__TaskOut | null;
}

/** ModelListOut */
export interface modules__catalog__schema__ModelListOut {
  items?: modules__catalog__schema__ModelOut[];
  total?: number;
}

/** ModelOut */
export interface modules__catalog__schema__ModelOut {
  id: string;
  label: string;
  capability: Capability;
  provider: string;
  description?: string;
  paramSchema?: Record<string, unknown>;
  /** 这个模型最多接受几张参考图（图生图 / 多图合成），`0` 表示纯文生图。参考图不是 `paramSchema` 里的参数：它的值是「一批图片」，提交时走 `referenceLibraryIds` 这条独立入参 */
  maxReferences?: number;
  isDefault?: boolean;
}

/** ModelListOut */
export interface modules__model_driver__schema__ModelListOut {
  items?: modules__model_driver__schema__ModelOut[];
  total?: number;
}

/** ModelOut */
export interface modules__model_driver__schema__ModelOut {
  id: string;
  label: string;
  capability: string;
  provider: string;
  description?: string;
  paramSchema?: Record<string, unknown>;
  /** 这个模型最多接受几张参考图（图生图 / 多图合成），`0` 表示纯文生图。参考图不是 `paramSchema` 里的参数：它的值是「一批图片」，提交时走 `referenceUrls` 这条独立入参 */
  maxReferences?: number;
}

/** TaskOut */
export interface modules__model_driver__schema__TaskOut {
  id: string;
  name?: string;
  model: string;
  capability: string;
  status: string;
  progress?: number;
  outputUrl?: string | null;
  coverUrl?: string | null;
  error?: string | null;
  externalId?: string | null;
  ownerId?: string;
  source?: string;
  submitAttempts?: number;
  createdAt?: string | null;
}

/** 任务出参：字段名与前端 `VideoTask` 完全一致。 */
export interface modules__task__schema__TaskOut {
  /** 任务 ID */
  id: string;
  /** 标题 */
  title: string;
  /** 状态：queued 排队 / running 生成中 / completed 完成 / failed 失败 */
  status: TaskStatus;
  /** 模型展示名，如 `Seedance 2.5` */
  model: string;
  /** 模型目录 id，与 `GET /api/v1/models` 的 `id` 对齐 */
  modelId?: string | null;
  /** 视频时长（秒） */
  durationSec: number;
  /** 画幅比例 */
  aspectRatio: AspectRatio;
  /** 生成进度 0-100 */
  progress?: number;
  /** 封面图 URL，未生成完时为空串 */
  coverUrl?: string;
  /** 画面描述 */
  prompt?: string;
  /** 风格预设标识，未设置时为 null */
  stylePreset?: string | null;
  /** 参考图资产 id（资产库 v2），**按顺序生效**；图生视频用。只接受 `image` 类型、当前账号可读、未在回收站或草稿里的资产 */
  referenceLibraryIds?: string[];
  /** 绑定的文本资产 id（顺序即 `{{Text N}}` 编号） */
  textLibraryIds?: string[];
  /** 绑定的文案与版本快照（不含正文） */
  textBindings?: Record<string, unknown>[];
  /** 作者用户 ID */
  authorId: string;
  /** 作者昵称快照（改名不回填历史） */
  authorName?: string;
  /** 当前账号是否已收藏 */
  favorited?: boolean;
  /** 是否允许下载：已成功且有产物 */
  downloadable?: boolean;
  /** 失败原因，成功时为 null */
  errorMessage?: string | null;
  /** 创建时间（UTC） */
  createdAt: string;
  /** 更新时间（UTC） */
  updatedAt: string;
  /** 开始生成时间，未开始为 null */
  startedAt?: string | null;
}

/* --------------------- 接口 → data 类型（已解包封套） --------------------- */

/** 键是 `METHOD path`，值是去掉 {code,message,data} 封套后的类型 */
export interface EndpointData {
  /** 我的导出记录 */
  "GET /api/v1/exports": Page<ExportOut>;
  /** 创建导出（异步生成，202） */
  "POST /api/v1/exports": unknown;
  /** 可导出的数据源 */
  "GET /api/v1/exports/sources": ExportSourceOut[];
  /** 删除导出（连带产物） */
  "DELETE /api/v1/exports/{export_id}": unknown;
  /** 导出详情 */
  "GET /api/v1/exports/{export_id}": ExportDetailOut;
  /** 下载导出文件（本地驱动直出，远端驱动 307） */
  "GET /api/v1/exports/{export_id}/download": unknown;
  /** 提交生图 */
  "POST /api/v1/generations/image": ImageJobOut;
  /** 存活探针 */
  "GET /api/v1/health": HealthOut;
  /** 就绪探针（检查依赖） */
  "GET /api/v1/health/ready": ReadyOut;
  /** 小组列表（含成员与权限） */
  "GET /api/v1/identity/groups": GroupOut[];
  /** 新建小组 */
  "POST /api/v1/identity/groups": GroupOut;
  /** 删除小组（同时清成员与授权） */
  "DELETE /api/v1/identity/groups/{group_id}": null;
  /** 小组详情 */
  "GET /api/v1/identity/groups/{group_id}": GroupOut;
  /** 编辑小组资料 */
  "PATCH /api/v1/identity/groups/{group_id}": GroupOut;
  /** 小组成员列表 */
  "GET /api/v1/identity/groups/{group_id}/members": UserOut[];
  /** 设置小组成员（整组替换） */
  "PUT /api/v1/identity/groups/{group_id}/members": GroupOut;
  /** 设置小组整体权限（整组替换） */
  "PUT /api/v1/identity/groups/{group_id}/permissions": GroupOut;
  /** 登录换取令牌 */
  "POST /api/v1/identity/login": TokenOut;
  /** 退出登录（作废当前会话，幂等） */
  "POST /api/v1/identity/logout": null;
  /** 当前用户（含角色与权限码） */
  "GET /api/v1/identity/me": MeOut;
  /** 修改自己的资料 */
  "PATCH /api/v1/identity/me": MeOut;
  /** 我的登录记录 */
  "GET /api/v1/identity/me/logins": LoginEventListOut;
  /** 修改自己的密码 */
  "POST /api/v1/identity/me/password": null;
  /** 退出所有其他设备（保留当前） */
  "DELETE /api/v1/identity/me/sessions": RevokedSessionsOut;
  /** 我的登录会话 */
  "GET /api/v1/identity/me/sessions": SessionOut[];
  /** 踢掉我的一条会话 */
  "DELETE /api/v1/identity/me/sessions/{session_id}": null;
  /** 口令策略（匿名） */
  "GET /api/v1/identity/password-policy": PasswordPolicyOut;
  /** 全部权限点（按分组，供角色编辑界面） */
  "GET /api/v1/identity/permissions": PermissionCatalogOut;
  /** 用刷新令牌换取新令牌 */
  "POST /api/v1/identity/refresh": TokenOut;
  /** 角色列表（含权限与人数） */
  "GET /api/v1/identity/roles": RoleOut[];
  /** 创建角色 */
  "POST /api/v1/identity/roles": RoleOut;
  /** 删除角色 */
  "DELETE /api/v1/identity/roles/{role_id}": null;
  /** 编辑角色 */
  "PATCH /api/v1/identity/roles/{role_id}": RoleOut;
  /** 设置角色权限（整组替换） */
  "PUT /api/v1/identity/roles/{role_id}/permissions": RoleOut;
  /** 团队基础权限（全体启用账号） */
  "GET /api/v1/identity/team/permissions": string[];
  /** 设置团队基础权限（整组替换） */
  "PUT /api/v1/identity/team/permissions": string[];
  /** 用户列表 */
  "GET /api/v1/identity/users": Page<UserOut>;
  /** 创建用户 */
  "POST /api/v1/identity/users": UserOut;
  /** 停用用户（保留历史归属） */
  "DELETE /api/v1/identity/users/{user_id}": UserOut;
  /** 用户详情 */
  "GET /api/v1/identity/users/{user_id}": UserOut;
  /** 编辑用户（含停用） */
  "PATCH /api/v1/identity/users/{user_id}": UserOut;
  /** 用户有效权限与四层来源 */
  "GET /api/v1/identity/users/{user_id}/effective-permissions": EffectivePermissionsOut;
  /** 某账号的登录记录 */
  "GET /api/v1/identity/users/{user_id}/logins": LoginEventListOut;
  /** 用户单独授予的权限 */
  "GET /api/v1/identity/users/{user_id}/permissions": string[];
  /** 设置个人权限（整组替换） */
  "PUT /api/v1/identity/users/{user_id}/permissions": string[];
  /** 管理员重置密码 */
  "POST /api/v1/identity/users/{user_id}/reset-password": null;
  /** 踢下线（吊销该用户全部会话） */
  "POST /api/v1/identity/users/{user_id}/revoke-sessions": RevokedSessionsOut;
  /** 设置用户角色（整组替换） */
  "PUT /api/v1/identity/users/{user_id}/roles": UserOut;
  /** 生图历史（分页） */
  "GET /api/v1/images": Page<ImageJobOut>;
  /** 重试失败的生图 */
  "POST /api/v1/images/{job_id}/retry": ImageRetryOut;
  /** 资产库列表（分页 + 分面） */
  "GET /api/v1/library/assets": LibraryAssetPage;
  /** 批量写：逐条判定，部分失败返回明细 */
  "POST /api/v1/library/assets/bulk": BulkOut;
  /** 生成结果入库（落草稿箱，按 sourceKey 幂等） */
  "POST /api/v1/library/assets/from-generation": IngestOut;
  /** 上传图片 / 音频 / 文本（流式，按内容秒传） */
  "POST /api/v1/library/assets/upload": LibraryAssetOut;
  /** 移入回收站（软删，可恢复） */
  "DELETE /api/v1/library/assets/{asset_id}": null;
  /** 资产详情（含标签与生成参数） */
  "GET /api/v1/library/assets/{asset_id}": LibraryAssetDetailOut;
  /** 改名 / 换标签（不含可见性） */
  "PATCH /api/v1/library/assets/{asset_id}": LibraryAssetDetailOut;
  /** 下载或预览原文件 */
  "GET /api/v1/library/assets/{asset_id}/file": unknown;
  /** 受控公共预览（视频给派生流） */
  "GET /api/v1/library/assets/{asset_id}/preview": unknown;
  /** 浏览器兼容视频预览（派生流，不改原片） */
  "GET /api/v1/library/assets/{asset_id}/stream": unknown;
  /** 读取文本正文与当前版本 */
  "GET /api/v1/library/assets/{asset_id}/text": TextAssetOut;
  /** 在线编辑文本（追加一版，可带 expectedVersion 防覆盖） */
  "PUT /api/v1/library/assets/{asset_id}/text": TextAssetOut;
  /** 纯文本取件（text/plain + nosniff） */
  "GET /api/v1/library/assets/{asset_id}/text/raw": unknown;
  /** 恢复历史版本（写成新的一版，不改历史行） */
  "POST /api/v1/library/assets/{asset_id}/text/restore": TextAssetOut;
  /** 文本版本历史（默认不带正文） */
  "GET /api/v1/library/assets/{asset_id}/text/versions": TextAssetVersionOut[];
  /** 读取某一历史版本正文 */
  "GET /api/v1/library/assets/{asset_id}/text/versions/{number}": TextAssetVersionOut;
  /** 读取缩略图 */
  "GET /api/v1/library/assets/{asset_id}/thumbnail": unknown;
  /** 我最近发起的打包任务 */
  "GET /api/v1/library/downloads": DownloadJobOut[];
  /** 发起批量打包（异步，返回任务） */
  "POST /api/v1/library/downloads": unknown;
  /** 打包任务状态 */
  "GET /api/v1/library/downloads/{job_id}": DownloadJobOut;
  /** 取打包好的 zip */
  "GET /api/v1/library/downloads/{job_id}/file": unknown;
  /** 草稿采纳：进入个人素材库 */
  "POST /api/v1/library/drafts/{asset_id}/approve": LibraryAssetDetailOut;
  /** 草稿驳回：进回收站，可恢复回草稿 */
  "POST /api/v1/library/drafts/{asset_id}/reject": LibraryAssetDetailOut;
  /** 资产库分面统计 */
  "GET /api/v1/library/facets": LibraryFacets;
  /** 我的小组（含是否负责人） */
  "GET /api/v1/library/groups": LibraryGroupOut[];
  /** 设置小组负责人（整体替换） */
  "PUT /api/v1/library/groups/{group_id}/leaders": string[];
  /** 建封装（成员必须是我看得见且在库的） */
  "POST /api/v1/library/packs": LibraryAssetDetailOut;
  /** 解散封装（进回收站，成员不受影响） */
  "DELETE /api/v1/library/packs/{pack_id}": null;
  /** 封装详情（含成员） */
  "GET /api/v1/library/packs/{pack_id}": LibraryAssetDetailOut;
  /** 改封装：改名 / 整体替换成员 / 换封面 / 换标签 */
  "PATCH /api/v1/library/packs/{pack_id}": LibraryAssetDetailOut;
  /** 选择器：只返回库内资产 */
  "GET /api/v1/library/selection": LibrarySelectionPage;
  /** 申请单列表（我提交的 / 待我审核） */
  "GET /api/v1/library/share-requests": ShareRequestOut[];
  /** 发起共享 / 撤销共享申请 */
  "POST /api/v1/library/share-requests": ShareRequestOut;
  /** 通过申请 */
  "POST /api/v1/library/share-requests/{request_id}/approve": ShareRequestOut;
  /** 撤回自己提交的申请 */
  "POST /api/v1/library/share-requests/{request_id}/cancel": ShareRequestOut;
  /** 驳回申请 */
  "POST /api/v1/library/share-requests/{request_id}/reject": ShareRequestOut;
  /** 上传模板库参考视频（只收视频） */
  "POST /api/v1/library/source-videos": LibraryAssetOut;
  /** 标签列表（两级平铺，含当前入口的素材数，按添加时间排序） */
  "GET /api/v1/library/tags": LibraryTagStatOut[];
  /** 新建标签（同名返回已有那条） */
  "POST /api/v1/library/tags": LibraryTagOut;
  /** 删除标签（旗下二级提升为一级，资产不受影响） */
  "DELETE /api/v1/library/tags/{tag_id}": null;
  /** 重命名标签（不做隐式合并） */
  "PATCH /api/v1/library/tags/{tag_id}": LibraryTagOut;
  /** 移动标签层级（parentId 为空则提升为一级） */
  "PATCH /api/v1/library/tags/{tag_id}/parent": LibraryTagOut;
  /** 站内新建文本资产（直接写正文，不传文件） */
  "POST /api/v1/library/text-assets": TextAssetOut;
  /** 彻底删除（不可逆，先过引用检查） */
  "DELETE /api/v1/library/trash/{asset_id}": null;
  /** 从回收站恢复（强制回到个人库，不恢复共享） */
  "POST /api/v1/library/trash/{asset_id}/restore": LibraryAssetDetailOut;
  /** 已自动载入的模型驱动清单 */
  "GET /api/v1/model-driver/drivers": DriverListOut;
  /** 统一生成接口可用的模型目录 */
  "GET /api/v1/model-driver/models": core__response__ApiResponse_ModelListOut___2;
  /** 提交一次统一生成任务 */
  "POST /api/v1/model-driver/tasks": core__response__ApiResponse_TaskOut___1;
  /** 查询统一生成任务 */
  "GET /api/v1/model-driver/tasks/{task_id}": core__response__ApiResponse_TaskOut___1;
  /** 取消生成任务 */
  "POST /api/v1/model-driver/tasks/{task_id}/cancel": core__response__ApiResponse_TaskOut___1;
  /** 人工重试失败的生成任务 */
  "POST /api/v1/model-driver/tasks/{task_id}/retry": core__response__ApiResponse_TaskOut___1;
  /** 可用模型目录（含参数 schema） */
  "GET /api/v1/models": core__response__ApiResponse_ModelListOut___1;
  /** 镜头词条列表 */
  "GET /api/v1/shots": ShotListOut;
  /** 分镜模板列表 */
  "GET /api/v1/storyboards": StoryboardListOut;
  /** 批量删除任务 */
  "DELETE /api/v1/tasks": DeleteTasksOut;
  /** 任务列表 */
  "GET /api/v1/tasks": TaskListOut;
  /** 创建任务 */
  "POST /api/v1/tasks": core__response__ApiResponse_TaskOut___2;
  /** 统一任务中心：视频生成、生图与本人可见的替换档案 */
  "GET /api/v1/tasks/feed": TaskFeedOut;
  /** 任务统计（侧边栏角标） */
  "GET /api/v1/tasks/stats": TaskStats;
  /** 任务详情 */
  "GET /api/v1/tasks/{task_id}": core__response__ApiResponse_TaskOut___2;
  /** 更新任务（标题 / 收藏） */
  "PATCH /api/v1/tasks/{task_id}": core__response__ApiResponse_TaskOut___2;
  /** 收藏 / 取消收藏 */
  "POST /api/v1/tasks/{task_id}/favorite": core__response__ApiResponse_TaskOut___2;
  /** 重试任务 */
  "POST /api/v1/tasks/{task_id}/retry": TaskRetryOut;
  /** manifest 配置列表 */
  "GET /api/v1/template-manifests": ManifestListOut;
  /** 保存 manifest 草稿 */
  "POST /api/v1/template-manifests": ManifestOut;
  /** 预检 manifest 配置（不落库） */
  "POST /api/v1/template-manifests/validate": ManifestValidateOut;
  /** manifest 配置详情 */
  "GET /api/v1/template-manifests/{key}": ManifestOut;
  /** 停用 manifest 配置 */
  "POST /api/v1/template-manifests/{key}/disable": ManifestOut;
  /** 发布 manifest 配置 */
  "POST /api/v1/template-manifests/{key}/publish": ManifestPublishOut;
  /** 本人可见的规格列表 */
  "GET /api/v1/template-specs": TemplateSpecListOut;
  /** 保存草稿（同内容幂等，revision 冲突 409） */
  "POST /api/v1/template-specs": TemplateSpecOut;
  /** 模板库：已发布的规格卡片 */
  "GET /api/v1/template-specs/published": TemplateSpecListOut;
  /** 预检模板规格（不落库） */
  "POST /api/v1/template-specs/validate": SpecValidateOut;
  /** 规格详情（含草稿与最近发布） */
  "GET /api/v1/template-specs/{key}": TemplateSpecOut;
  /** 填表结果 → 执行请求体（纯映射，不落库） */
  "POST /api/v1/template-specs/{key}/compile": SpecCompileOut;
  /** 下架（历史版本保留） */
  "POST /api/v1/template-specs/{key}/disable": TemplateSpecOut;
  /** 发布：把当前草稿冻结成新版本 */
  "POST /api/v1/template-specs/{key}/publish": SpecPublishOut;
  /** 人物/产品/场景替换模板与调用档案 */
  "GET /api/v1/templates/replacement-catalog": ReplacementCatalog;
  /** 本人封装草稿和历史包 */
  "GET /api/v1/templates/replacement-packs": PackManagedList;
  /** 全工作台已发布对象 */
  "GET /api/v1/templates/replacement-packs/available": PackPublicList;
  /** 保存封装草稿（请求标识及版本防重） */
  "PUT /api/v1/templates/replacement-packs/draft": PackManaged;
  /** 停用已发布封装对象 */
  "POST /api/v1/templates/replacement-packs/{pack_id}/disable": PackManaged;
  /** 读取授权发布版本的包内预览 */
  "GET /api/v1/templates/replacement-packs/{pack_id}/preview/{asset_id}": unknown;
  /** 发布不可变封装版本 */
  "POST /api/v1/templates/replacement-packs/{pack_id}/publish": PackManaged;
  /** 归档输入并预览最终提示词，不提交生成 */
  "POST /api/v1/templates/replacement-runs": ReplacementRunOut;
  /** 将本人替换任务与调用档案移入回收站，不取消生成 */
  "DELETE /api/v1/templates/replacement-runs/{run_id}": ReplacementRunOut;
  /** 替换任务状态与提示词 */
  "GET /api/v1/templates/replacement-runs/{run_id}": ReplacementRunOut;
  /** 提交替换任务；billingMode=credits 表示本任务用积分加速 */
  "POST /api/v1/templates/replacement-runs/{run_id}/execute": ReplacementRunOut;
  /** 读取当次归档输入快照 */
  "GET /api/v1/templates/replacement-runs/{run_id}/inputs/{asset_id}": unknown;
  /** 读取当次输入快照缩略图 */
  "GET /api/v1/templates/replacement-runs/{run_id}/inputs/{asset_id}/thumbnail": unknown;
  /** 预览或下载原始生成结果 */
  "GET /api/v1/templates/replacement-runs/{run_id}/output": unknown;
  /** 确认上次提交可能已计费后放行重提（默认拒绝） */
  "POST /api/v1/templates/replacement-runs/{run_id}/reconfirm": ReplacementRunOut;
  /** 恢复本人回收站中的替换任务与调用档案 */
  "POST /api/v1/templates/replacement-runs/{run_id}/restore": ReplacementRunOut;
  /** 保存人工验收，版本冲突需重新核对 */
  "PUT /api/v1/templates/replacement-runs/{run_id}/review": ReplacementRunOut;
  /** 视频模板列表（固定蓝本优先） */
  "GET /api/v1/templates/video-templates": VideoTemplateListOut;
  /** 封装一条视频模板 */
  "POST /api/v1/templates/video-templates": VideoTemplateOut;
  /** 视频模板详情（含提示词与媒体顺序） */
  "GET /api/v1/templates/video-templates/{template_id}": VideoTemplateOut;
  /** 绑定样片原片资产 */
  "POST /api/v1/templates/video-templates/{template_id}/attach-source": VideoTemplateOut;
  /** 设为固定蓝本（全库仅一个） */
  "POST /api/v1/templates/video-templates/{template_id}/pin": VideoTemplateOut;
  /** 保存固定提示词与跨封装图片引用 */
  "PATCH /api/v1/templates/video-templates/{template_id}/prompt": VideoTemplateOut;
}
