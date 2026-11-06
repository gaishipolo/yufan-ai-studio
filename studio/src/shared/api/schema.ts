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

/** 取值：image, video, audio */
export type AssetType = "image" | "video" | "audio";
export const AssetType_VALUES = ["image", "video", "audio"] as const;

/** 取值：all, image, video, audio */
export type AssetTypeFilter = "all" | "image" | "video" | "audio";
export const AssetTypeFilter_VALUES = ["all", "image", "video", "audio"] as const;

/** 取值：video, image, audio */
export type Capability = "video" | "image" | "audio";
export const Capability_VALUES = ["video", "image", "audio"] as const;

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

/* ------------------------------ 模型 ------------------------------ */

/** ArchiveList */
export interface ArchiveList {
  root: string;
  items: ArchivedAsset[];
  total: number;
  usedBytes: number;
}

/** ArchivedAsset */
export interface ArchivedAsset {
  id: string;
  name: string;
  category: "source" | "character" | "product" | "scene" | "audio";
  type: "image" | "video" | "audio";
  mimeType: string;
  sizeBytes: number;
  path: string;
  sha256: string;
  createdAt: string;
  width?: number;
  height?: number;
  duration?: number;
  hasAudio?: boolean;
  url: string;
  previewUrl?: string | null;
}

/** AssetCounts */
export interface AssetCounts {
  all?: number;
  image?: number;
  video?: number;
  audio?: number;
}

/** AssetListOut */
export interface AssetListOut {
  items?: AssetOut[];
  total?: number;
  counts?: AssetCounts;
}

/** AssetOut */
export interface AssetOut {
  id: string;
  name: string;
  type: AssetType;
  mimeType: string;
  sizeBytes: number;
  coverUrl: string;
  width: number;
  height: number;
  refCount: number;
  createdAt: string;
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
  /** 参考素材 ID 列表（最多 20 个） */
  referenceAssetIds?: string[];
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

/** GenerateImageIn */
export interface GenerateImageIn {
  /** 画面描述 */
  prompt: string;
  /** 宽x高，如 `1024x1536` */
  size?: string;
  /** 张数 */
  count?: number;
  /** 模型 id，留空取默认 */
  model?: string;
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
  /** 状态：`queued` / `running` / `succeeded` / `failed` */
  status?: string;
  /** 进度 0-100 */
  progress?: number;
  /** 模型可读名 */
  model?: string;
  /** 模型目录 id */
  modelId?: string | null;
  /** 封面图 URL */
  coverUrl?: string;
  /** 成图 URL（成功后才有） */
  outputUrl?: string | null;
  /** 失败原因 */
  errorMessage?: string | null;
  /** 供应商侧任务号；提交成功后有值，可用于对账 */
  externalId?: string | null;
  /** 上一次提交的供应商任务号；带 confirmCharged 重试后保留的对账凭证 */
  previousExternalId?: string | null;
  /** 被替换掉的全部供应商任务号（旧的在前）。连续多次确认已扣费重试时，previousExternalId 只保留最近一个，全量凭证看这里 */
  previousExternalIds?: string[];
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

/** ImportAsset */
export interface ImportAsset {
  relativePath: string;
  category: "source" | "character" | "product" | "scene" | "audio";
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

/** ModelListOut */
export interface ModelListOut {
  items?: ModelOut[];
  total?: number;
}

/** ModelOut */
export interface ModelOut {
  id: string;
  label: string;
  capability: Capability;
  provider: string;
  description?: string;
  paramSchema?: Record<string, unknown>;
  isDefault?: boolean;
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

/** PrepareReplacement */
export interface PrepareReplacement {
  requestId: string;
  kind: "character" | "product" | "scene";
  target: string;
  sourceId: string;
  referenceIds: string[];
  wardrobeMode?: "preserve" | "reference";
  replacementScope?: string;
  notes?: string;
  packId?: string | null;
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
}

/** ReadyOut */
export interface ReadyOut {
  status: string;
  database: string;
  queueSize: number;
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
  provider?: string;
  maxCredits?: number;
  kinds?: string[];
  runs: ReplacementRunOut[];
  packs?: ReplacementPack[];
}

/** ReplacementPack */
export interface ReplacementPack {
  id: string;
  kind: "character" | "product" | "scene";
  name: string;
  referenceIds: string[];
  wardrobeMode?: "preserve" | "reference";
  features?: string;
  instructions?: string;
}

/** ReplacementRunOut */
export interface ReplacementRunOut {
  id: string;
  kind: "character" | "product" | "scene";
  target: string;
  status: string;
  archivePath: string;
  createdAt: string;
  prompt: string;
  taskId?: string | null;
  estimatedCredits?: number | null;
  credits?: number | null;
  progress?: number;
  reviewStatus?: string;
  lastError?: string | null;
  outputUrl?: string | null;
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

/** TaskListOut */
export interface TaskListOut {
  items?: TaskOut[];
  total?: number;
  page?: number;
  pageSize?: number;
  /** 侧边栏角标用的分组计数 */
  stats?: TaskStats;
  /** 当前账号能否查看他人任务；false 时前端应收起「全部」选项 */
  canReadAll?: boolean;
}

/** 任务出参：字段名与前端 `VideoTask` 完全一致。 */
export interface TaskOut {
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
  /** 参考素材 ID 列表（图生视频用） */
  referenceAssetIds?: string[];
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

/** TemplateListOut */
export interface TemplateListOut {
  items?: TemplateOut[];
  total?: number;
}

/** TemplateOut */
export interface TemplateOut {
  id: string;
  name: string;
  description: string;
  industry: string;
  shotCount: number;
  usageCount: number;
  coverUrl: string;
  tags?: string[];
  createdAt: string;
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

/** 预签名上传：前端拿到 uploadUrl 后直传对象存储，再回调确认。 */
export interface UploadAssetIn {
  filename: string;
  contentType?: string;
  sizeBytes?: number;
}

/** UploadTicketOut */
export interface UploadTicketOut {
  assetId: string;
  uploadUrl: string;
  key: string;
  expiresIn?: number;
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
  /** 角色 id 列表，可留空 */
  roleIds?: string[];
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

/* --------------------- 接口 → data 类型（已解包封套） --------------------- */

/** 键是 `METHOD path`，值是去掉 {code,message,data} 封套后的类型 */
export interface EndpointData {
  /** 素材列表（按类型分面计数） */
  "GET /api/v1/assets": AssetListOut;
  /** 归档素材列表与空间占用 */
  "GET /api/v1/assets/archive": ArchiveList;
  /** 从 Z 盘 inbox 登记现有素材 */
  "POST /api/v1/assets/archive/import": ArchivedAsset;
  /** 流式上传素材至归档盘 */
  "POST /api/v1/assets/archive/upload": ArchivedAsset;
  /** 预览或下载已归档素材 */
  "GET /api/v1/assets/archive/{asset_id}/file": unknown;
  /** 浏览器兼容视频预览（派生文件，不改原片） */
  "GET /api/v1/assets/archive/{asset_id}/stream": unknown;
  /** 读取归档素材缩略图 */
  "GET /api/v1/assets/archive/{asset_id}/thumbnail": unknown;
  /** 申请上传凭证（待实现） */
  "POST /api/v1/assets/upload-ticket": UploadTicketOut;
  /** 删除素材（待实现） */
  "DELETE /api/v1/assets/{asset_id}": unknown;
  /** 素材详情（待实现） */
  "GET /api/v1/assets/{asset_id}": null;
  /** 提交生图 */
  "POST /api/v1/generations/image": ImageJobOut;
  /** 存活探针 */
  "GET /api/v1/health": HealthOut;
  /** 就绪探针（检查依赖） */
  "GET /api/v1/health/ready": ReadyOut;
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
  /** 某账号的登录记录 */
  "GET /api/v1/identity/users/{user_id}/logins": LoginEventListOut;
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
  /** 可用模型目录（含参数 schema） */
  "GET /api/v1/models": ModelListOut;
  /** 镜头词条列表 */
  "GET /api/v1/shots": ShotListOut;
  /** 分镜模板列表 */
  "GET /api/v1/storyboards": StoryboardListOut;
  /** 批量删除任务 */
  "DELETE /api/v1/tasks": DeleteTasksOut;
  /** 任务列表 */
  "GET /api/v1/tasks": TaskListOut;
  /** 创建任务 */
  "POST /api/v1/tasks": TaskOut;
  /** 任务统计（侧边栏角标） */
  "GET /api/v1/tasks/stats": TaskStats;
  /** 任务详情 */
  "GET /api/v1/tasks/{task_id}": TaskOut;
  /** 更新任务（标题 / 收藏） */
  "PATCH /api/v1/tasks/{task_id}": TaskOut;
  /** 收藏 / 取消收藏 */
  "POST /api/v1/tasks/{task_id}/favorite": TaskOut;
  /** 重试任务 */
  "POST /api/v1/tasks/{task_id}/retry": TaskRetryOut;
  /** 模板列表 */
  "GET /api/v1/templates": TemplateListOut;
  /** 人物/产品/场景替换模板与调用档案 */
  "GET /api/v1/templates/replacement-catalog": ReplacementCatalog;
  /** 归档输入并预览最终提示词，不提交生成 */
  "POST /api/v1/templates/replacement-runs": ReplacementRunOut;
  /** 替换任务状态与提示词 */
  "GET /api/v1/templates/replacement-runs/{run_id}": ReplacementRunOut;
  /** 仅 0 积分提交或继续查询已有 LibTV 任务 */
  "POST /api/v1/templates/replacement-runs/{run_id}/execute": ReplacementRunOut;
  /** 预览或下载 Z 盘原始生成结果 */
  "GET /api/v1/templates/replacement-runs/{run_id}/output": unknown;
  /** 确认上次提交可能已计费后放行重提（默认拒绝） */
  "POST /api/v1/templates/replacement-runs/{run_id}/reconfirm": ReplacementRunOut;
}
