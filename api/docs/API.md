# 雨范 AI 短视频工作台 API — 接口文档

> 此文件由 `app export-openapi` 自动生成，请勿手工编辑。
>
> 版本 `0.2.0`｜共 **96** 个接口 / **10** 个模块
>
> 重新生成：`cd api && .venv/Scripts/python -m cli export-openapi`

在线调试（仅非生产环境开放）：<http://127.0.0.1:8080/docs>

**怎么查**：某个接口怎么调 → 第 4 节（按模块分）｜某个字段什么意思 → 第 5 节｜报错了是什么原因 → 第 3 节｜这个按钮要不要显示 → 第 2 节的权限码。

前端拿类型不用手抄：

```ts
import type { TaskOut, EndpointData } from '@/shared/api/schema';
```

`EndpointData` 的键是 `"METHOD 路径"`，值已经去掉 `{code,message,data}` 封套。
这份 `schema.ts` 与本文档由同一个命令生成，不会对不上。

---

## 1. 三条约定

**① 所有响应都是同一个封套**，前端统一按 `data` 取业务数据：

```json
{ "code": 0, "message": "OK", "data": { } }
```

- `code = 0` 成功；非 0 是业务错误码，**HTTP 状态同时也非 2xx**
- 出错的 `data` 为 `null`，错误详情看 `message`（可直接展示给用户）
- 响应头带 `X-Request-Id`，报错时把它给后端能直接定位日志

**② 字段一律 camelCase**，与 TypeScript 完全一致，不需要任何转换层。

**③ 时间一律 ISO 8601 UTC**，带 `Z` 后缀（如 `2026-11-05T06:24:11Z`），
前端用 `new Date(value)` 即可，展示时再转本地时区。

### 鉴权

```http
Authorization: Bearer <accessToken>
```

`accessToken` 从 `POST /api/v1/identity/login` 拿，默认有效期 2 小时。
标注 🔒 的接口必须带令牌；标「公开」的不需要。

令牌只携带身份，**权限以数据库为准**：账号被停用或角色被改，
下一个请求立刻生效（这是后端行为，前端不必轮询）。

拿不到业务数据时按状态码分流：

| 状态码 | 含义 | 前端应对 |
|---|---|---|
| 401 | 未登录 / 令牌过期 / 账号已停用 | 清本地令牌，跳登录页 |
| 403 | 已登录但缺权限 | 提示无权限，别跳登录页 |
| 404 | 资源不存在或无权访问 | 空态 |
| 409 | 状态冲突（如重复操作） | 提示后刷新列表 |
| 422 | 参数校验失败 | 按 `message` 定位到字段 |
| 5xx | 服务端问题 | 可重试，带 `X-Request-Id` 反馈 |

### 分页

列表接口统一收 `page`（从 1 开始）/ `pageSize`（默认 20，上限 100），
返回 `{ items, total, page, pageSize }`。

---

## 2. 权限点（前端控制菜单 / 按钮显隐用）

`GET /api/v1/identity/me` 返回的 `permissions` 就是这个列表的子集。
持有 `*` 表示超级管理员，所有权限点都成立。

**内容生产**

| 权限码 | 名称 | 说明 |
|---|---|---|
| `catalog:read` | 查看模型目录 |  |
| `export:create` | 创建导出 |  |
| `export:delete` | 删除导出 |  |
| `export:read` | 查看导出 |  |
| `image:create` | 提交生图任务 |  |
| `image:read` | 查看生图历史 |  |
| `image:read:all` | 查看他人生图历史 | 列表的数据范围开关；与 task:read:all 同义，不含提交他人的生图任务 |
| `image:retry:any` | 重试他人生图任务 | 只开关「替别人点重试」，不蕴含 image:read:all |
| `shot:read` | 查看镜头库 |  |
| `storyboard:read` | 查看分镜模板 |  |
| `task:create` | 创建生成任务 |  |
| `task:delete` | 删除任务 |  |
| `task:read` | 查看任务 |  |
| `task:read:all` | 查看并管理他人任务 | 列表 scope=all / 详情 / 删除的跨用户判定依据；不含重试 |
| `task:retry:any` | 重试他人任务 | 会消耗额度：重试他人任务前额外校验此权限 |
| `task:update` | 编辑/重试自己的任务 |  |
| `template:read` | 查看行业模板 |  |
| `template:review` | 评价自己的替换成片 |  |
| `template:use` | 使用替换模板并提交零积分生成 |  |

**系统**

| 权限码 | 名称 | 说明 |
|---|---|---|
| `export:read:audit` | 导出操作留痕 |  |

**素材**

| 权限码 | 名称 | 说明 |
|---|---|---|
| `asset:create` | 上传素材 |  |
| `asset:delete` | 删除素材 |  |
| `asset:read` | 查看素材 |  |
| `asset:reference:manage` | 维护封装参考素材与登记文件 |  |
| `asset:share` | 分享或撤回本人原片 |  |
| `asset:tag:manage` | 管理标签词表（新增标签） |  |
| `asset:team:manage` | 管理团队（建团队、任命管理员） | 任命其他团队管理员必须由平台管理员执行，团队管理员不能自行任命同级 |
| `asset:team:read` | 查看团队（库切换器） |  |
| `asset:update` | 修改本人素材（名称、标签） |  |
| `template:pack:manage` | 维护本人封装草稿 |  |
| `template:pack:publish` | 发布或停用本人封装对象 |  |

**组织管理**

| 权限码 | 名称 | 说明 |
|---|---|---|
| `identity:create` | 创建用户 |  |
| `identity:delete` | 停用用户 |  |
| `identity:read` | 查看用户与角色 |  |
| `identity:role:manage` | 管理角色与权限点 | 新建/删除角色、调整角色权限、给用户分配角色 |
| `identity:update` | 编辑用户（含停用、重置密码） | 不含分配角色。改他人昵称/头像：目标不能是超管；重置密码、改他人邮箱、启停账号：目标权限不得高于自己（超管仍需超管） |

> 判定规则：`*` 恒真；`task:*` 这类通配可覆盖 `task:read`；否则按字符串精确匹配。

---

## 3. 业务错误码总表

规则：`code = HTTP状态 × 100 + 模块段 × 10 + 序号`，所以
`40926` = HTTP 409 + `identity` 段 + 第 6 个错误。
**前端只需判断 `code !== 0`**，具体文案用 `message`（可直接展示）。

| code | HTTP | 来源 | 异常类 | 默认文案 |
|---|---|---|---|---|
| `40000` | 400 | 框架 | `BadRequest` | 请求参数有误 |
| `40001` | 400 | 框架 | `ValidationFailed` | 参数校验失败 |
| `40021` | 400 | identity | `OldPasswordMismatch` | 原密码不正确 |
| `40022` | 400 | identity | `UnknownPermission` | 存在无法识别的权限点 |
| `40051` | 400 | asset | `FileTypeNotAllowed` | 支持 MP4、PNG、JPG、WebP、MP3 和 WAV |
| `40052` | 400 | asset | `NotGenerated` | 这张素材是上传件，没有生成配方可提取 |
| `40100` | 401 | 框架 | `Unauthorized` | 请先登录 |
| `40121` | 401 | identity | `CredentialInvalid` | 用户名或密码错误 |
| `40122` | 401 | identity | `RefreshTokenInvalid` | 刷新令牌已失效，请重新登录 |
| `40300` | 403 | 框架 | `Forbidden` | 没有操作权限 |
| `40321` | 403 | identity | `AccountDisabled` | 账号已停用，请联系管理员 |
| `40322` | 403 | identity | `SuperuserRequired` | 该操作需要超级管理员权限 |
| `40323` | 403 | identity | `PermissionEscalation` | 不能授予自己不具备的权限 |
| `40400` | 404 | 框架 | `NotFound` | 资源不存在 |
| `40421` | 404 | identity | `UserNotFound` | 用户不存在 |
| `40422` | 404 | identity | `RoleNotFound` | 角色不存在 |
| `40423` | 404 | identity | `SessionNotFound` | 会话不存在或已失效 |
| `40451` | 404 | asset | `AssetNotFound` | 素材不存在 |
| `40452` | 404 | asset | `ViewNotFound` | 视图不存在 |
| `40900` | 409 | 框架 | `Conflict` | 资源冲突 |
| `40902` | 409 | 框架 | `InvalidState` | 当前状态不允许该操作 |
| `40921` | 409 | identity | `UsernameTaken` | 用户名已被占用 |
| `40922` | 409 | identity | `RoleCodeTaken` | 角色标识已存在 |
| `40923` | 409 | identity | `BuiltinRoleLocked` | 内置角色不可删除，标识也不可修改 |
| `40924` | 409 | identity | `RoleInUse` | 该角色仍被用户使用，请先解除绑定 |
| `40925` | 409 | identity | `CannotTargetSelf` | 不能停用或删除自己的账号 |
| `40926` | 409 | identity | `LastSuperuser` | 系统必须保留至少一个启用的超级管理员 |
| `40951` | 409 | asset | `AssetInUse` | 素材仍被引用，不能删除 |
| `40952` | 409 | asset | `TagExists` | 已存在同名标签 |
| `40953` | 409 | asset | `ViewNameExists` | 已存在同名视图 |
| `40954` | 409 | asset | `CandidateClosed` | 候选已弃用或已过期，请重新生成 |
| `40955` | 409 | asset | `ComplianceRejected` | 这张素材未通过合规审核，不能采纳 |
| `40971` | 409 | template | `TemplateBusy` | 模板正在处理中，请稍后再试 |
| `40991` | 409 | export | `ExportBusy` | 同一份数据正在导出中，请等待它完成 |
| `40992` | 409 | export | `ExportNotReady` | 导出尚未生成完成，请稍后再试 |
| `41091` | 410 | export | `ExportGone` | 导出文件已过保留期被回收，请重新导出 |
| `41351` | 413 | asset | `FileTooLarge` | 文件不能超过 50MB |
| `41352` | 413 | asset | `AssetQuotaExceeded` | 归档空间已用完，请先清理回收站或联系管理员提升配额 |
| `42251` | 422 | asset | `TagUnknown` | 标签不在词表内，请从标签列表中选择 |
| `42900` | 429 | 框架 | `RateLimited` | 请求过于频繁，请稍后再试 |
| `50100` | 501 | 框架 | `NotImplementedFeature` | 该功能尚未实现 |
| `50200` | 502 | 框架 | `UpstreamError` | 上游服务异常 |
| `50300` | 503 | 框架 | `Unavailable` | 服务暂不可用，请稍后再试 |

---

## 4. 接口清单

| 模块 | 接口数 | 说明 |
|---|---|---|
| 身份 | 26 | 见下方 `4.1` |
| 任务 | 9 | 见下方 `4.2` |
| 模型 | 1 | 见下方 `4.3` |
| 素材 | 31 | 见下方 `4.4` |
| 生图 | 3 | 见下方 `4.5` |
| 模板 | 16 | 见下方 `4.6` |
| 分镜模板 | 1 | 见下方 `4.7` |
| 镜头库 | 1 | 见下方 `4.8` |
| 系统 | 2 | 见下方 `4.9` |
| 导出 | 6 | 见下方 `4.10` |

### 4.1 身份（26 个）

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| `POST` | `/api/v1/identity/login` | 登录换取令牌 | 公开 |
| `POST` | `/api/v1/identity/logout` | 退出登录（作废当前会话，幂等） | 公开 |
| `GET` | `/api/v1/identity/me` | 当前用户（含角色与权限码） | 🔒 |
| `PATCH` | `/api/v1/identity/me` | 修改自己的资料 | 🔒 |
| `GET` | `/api/v1/identity/me/logins` | 我的登录记录 | 🔒 |
| `POST` | `/api/v1/identity/me/password` | 修改自己的密码 | 🔒 |
| `GET` | `/api/v1/identity/me/sessions` | 我的登录会话 | 🔒 |
| `DELETE` | `/api/v1/identity/me/sessions` | 退出所有其他设备（保留当前） | 🔒 |
| `DELETE` | `/api/v1/identity/me/sessions/{session_id}` | 踢掉我的一条会话 | 🔒 |
| `GET` | `/api/v1/identity/password-policy` | 口令策略（匿名） | 公开 |
| `GET` | `/api/v1/identity/permissions` | 全部权限点（按分组，供角色编辑界面） | 🔒 |
| `POST` | `/api/v1/identity/refresh` | 用刷新令牌换取新令牌 | 公开 |
| `GET` | `/api/v1/identity/roles` | 角色列表（含权限与人数） | 🔒 |
| `POST` | `/api/v1/identity/roles` | 创建角色 | 🔒 |
| `PATCH` | `/api/v1/identity/roles/{role_id}` | 编辑角色 | 🔒 |
| `DELETE` | `/api/v1/identity/roles/{role_id}` | 删除角色 | 🔒 |
| `PUT` | `/api/v1/identity/roles/{role_id}/permissions` | 设置角色权限（整组替换） | 🔒 |
| `GET` | `/api/v1/identity/users` | 用户列表 | 🔒 |
| `POST` | `/api/v1/identity/users` | 创建用户 | 🔒 |
| `GET` | `/api/v1/identity/users/{user_id}` | 用户详情 | 🔒 |
| `PATCH` | `/api/v1/identity/users/{user_id}` | 编辑用户（含停用） | 🔒 |
| `DELETE` | `/api/v1/identity/users/{user_id}` | 停用用户（保留历史归属） | 🔒 |
| `GET` | `/api/v1/identity/users/{user_id}/logins` | 某账号的登录记录 | 🔒 |
| `POST` | `/api/v1/identity/users/{user_id}/reset-password` | 管理员重置密码 | 🔒 |
| `POST` | `/api/v1/identity/users/{user_id}/revoke-sessions` | 踢下线（吊销该用户全部会话） | 🔒 |
| `PUT` | `/api/v1/identity/users/{user_id}/roles` | 设置用户角色（整组替换） | 🔒 |

#### `POST /api/v1/identity/login`

**登录换取令牌**

**请求体** `LoginIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `username` | `string` | 是 | 用户名 |
| `password` | `string` | 是 | 密码 |

**响应** `ApiResponse<TokenOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `accessToken` | `string` | 是 | 访问令牌，放进 `Authorization: Bearer <token>` |
| `tokenType` | `string` | 否 | 令牌类型，固定 Bearer |
| `expiresIn` | `number` | 否 | 有效期（秒），过期后请求返回 401 |
| `refreshToken` | `string` | 否 | 刷新令牌，只用来换新的 accessToken，不能访问业务接口 |
| `refreshExpiresIn` | `number` | 否 | 刷新令牌有效期（秒）；续期时滑动延长 |
| `user` | `MeOut` | 是 | 登录用户，可省一次 `/me` 请求 |

#### `POST /api/v1/identity/logout`

**退出登录（作废当前会话，幂等）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `authorization` | header | `string | null` | 否 |  |

**响应** `ApiResponse<null>`（前端取 `res.data`）

#### `GET /api/v1/identity/me`

**当前用户（含角色与权限码）**

**响应** `ApiResponse<MeOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 用户 ID |
| `username` | `string` | 是 | 登录名，入库前已转小写 |
| `displayName` | `string` | 否 | 昵称（顶栏展示用） |
| `email` | `string \| null` | 否 | 邮箱，未填为 null |
| `avatarUrl` | `string \| null` | 否 | 头像 URL，未设置为 null |
| `isActive` | `boolean` | 否 | 是否启用；停用后无法登录且令牌立即失效 |
| `lastLoginAt` | `string \| null` | 否 | 最后登录时间，从未登录为 null |
| `createdAt` | `string` | 是 | 创建时间（UTC） |
| `roles` | `RoleBriefOut[]` | 否 | 拥有的角色 |
| `permissions` | `string[]` | 否 | 展开后的权限码；含 `*` 表示超级管理员 |

#### `PATCH /api/v1/identity/me`

**修改自己的资料**

**请求体** `ProfileUpdateIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `displayName` | `string \| null` | 否 | 昵称 |
| `email` | `string \| null` | 否 | 邮箱；传 null 清空 |
| `avatarUrl` | `string \| null` | 否 | 头像 URL；传 null 清空 |

**响应** `ApiResponse<MeOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 用户 ID |
| `username` | `string` | 是 | 登录名，入库前已转小写 |
| `displayName` | `string` | 否 | 昵称（顶栏展示用） |
| `email` | `string \| null` | 否 | 邮箱，未填为 null |
| `avatarUrl` | `string \| null` | 否 | 头像 URL，未设置为 null |
| `isActive` | `boolean` | 否 | 是否启用；停用后无法登录且令牌立即失效 |
| `lastLoginAt` | `string \| null` | 否 | 最后登录时间，从未登录为 null |
| `createdAt` | `string` | 是 | 创建时间（UTC） |
| `roles` | `RoleBriefOut[]` | 否 | 拥有的角色 |
| `permissions` | `string[]` | 否 | 展开后的权限码；含 `*` 表示超级管理员 |

#### `GET /api/v1/identity/me/logins`

**我的登录记录**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `limit` | query | `number` | 否 | 返回条数 |

**响应** `ApiResponse<LoginEventListOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `LoginEventOut[]` | 否 | 按时间倒序 |

#### `POST /api/v1/identity/me/password`

**修改自己的密码**

**请求体** `PasswordChangeIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `oldPassword` | `string` | 是 | 当前密码 |
| `newPassword` | `string` | 是 | 新密码；至少 10 位，不能是纯数字，且不能与当前密码相同 |

**响应** `ApiResponse<null>`（前端取 `res.data`）

#### `GET /api/v1/identity/me/sessions`

**我的登录会话**

**响应** `ApiResponse<SessionOut[]>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `[].id` | `string` | 是 | 会话 ID，踢下线时用它 |
| `[].userAgent` | `string` | 否 | 登录时的 UA，未记录为空串 |
| `[].ip` | `string` | 否 | 登录时的来源 IP |
| `[].lastSeenAt` | `string \| null` | 否 | 最近一次续期时间，从未续期为 null |
| `[].expiresAt` | `string` | 是 | 刷新令牌到期时间（UTC） |
| `[].createdAt` | `string` | 是 | 登录时间（UTC） |
| `[].current` | `boolean` | 否 | 是否是发起本次请求的会话；前端据此标「本机」 |

#### `DELETE /api/v1/identity/me/sessions`

**退出所有其他设备（保留当前）**

**响应** `ApiResponse<RevokedSessionsOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `revoked` | `number` | 是 | 本次吊销的会话数；已经失效的会话不计入 |

#### `DELETE /api/v1/identity/me/sessions/{session_id}`

**踢掉我的一条会话**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `session_id` | path | `string` | 是 |  |

**响应** `ApiResponse<null>`（前端取 `res.data`）

#### `GET /api/v1/identity/password-policy`

**口令策略（匿名）**

登录页与「重置密码」表单要在未登录状态下拿到策略，所以不挂鉴权依赖。

只暴露规则，不暴露任何账号信息。

**响应** `ApiResponse<PasswordPolicyOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `minLength` | `number` | 是 | 最短长度 |
| `maxLength` | `number` | 是 | 最长长度 |
| `forbidAllNumeric` | `boolean` | 是 | 是否禁止纯数字 |
| `forbidSameAsUsername` | `boolean` | 是 | 是否禁止与用户名相同；当前未启用，前端按 false 处理 |
| `message` | `string` | 是 | 可直接展示的一句话，前端不要自己拼 |

#### `GET /api/v1/identity/permissions`

**全部权限点（按分组，供角色编辑界面）**

**响应** `ApiResponse<PermissionCatalogOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `groups` | `PermissionGroupOut[]` | 否 | 按分组组织的全部权限点，角色编辑界面直接渲染 |
| `total` | `number` | 否 | 权限点总数 |

#### `POST /api/v1/identity/refresh`

**用刷新令牌换取新令牌**

**请求体** `RefreshIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `refreshToken` | `string` | 是 | 登录时拿到的刷新令牌 |

**响应** `ApiResponse<TokenOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `accessToken` | `string` | 是 | 访问令牌，放进 `Authorization: Bearer <token>` |
| `tokenType` | `string` | 否 | 令牌类型，固定 Bearer |
| `expiresIn` | `number` | 否 | 有效期（秒），过期后请求返回 401 |
| `refreshToken` | `string` | 否 | 刷新令牌，只用来换新的 accessToken，不能访问业务接口 |
| `refreshExpiresIn` | `number` | 否 | 刷新令牌有效期（秒）；续期时滑动延长 |
| `user` | `MeOut` | 是 | 登录用户，可省一次 `/me` 请求 |

#### `GET /api/v1/identity/roles`

**角色列表（含权限与人数）**

**响应** `ApiResponse<RoleOut[]>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `[].id` | `string` | 是 | 角色 ID |
| `[].code` | `string` | 是 | 角色标识，唯一；内置角色不可改 |
| `[].name` | `string` | 是 | 角色名称 |
| `[].description` | `string` | 否 | 角色说明 |
| `[].isSuperuser` | `boolean` | 否 | 持有即展开为 `*`，拥有全部权限 |
| `[].isBuiltin` | `boolean` | 否 | 内置角色：不可删除 |
| `[].sortOrder` | `number` | 否 | 排序值，小的在前 |
| `[].createdAt` | `string` | 是 | 创建时间（UTC） |
| `[].permissions` | `string[]` | 否 | 角色拥有的权限码 |
| `[].userCount` | `number` | 否 | 使用该角色的用户数 |
| `[].orphanedPermissions` | `string[]` | 否 | 已失效的权限码（权限点被删或改名后残留） |

#### `POST /api/v1/identity/roles`

**创建角色**

**请求体** `RoleCreateIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `code` | `string` | 是 | 角色标识，唯一且不可改 |
| `name` | `string` | 是 | 角色名称 |
| `description` | `string` | 否 | 角色说明 |
| `permissions` | `string[]` | 否 | 权限码列表 |
| `isSuperuser` | `boolean` | 否 | 仅超级管理员可设置 |

**响应** `ApiResponse<RoleOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 角色 ID |
| `code` | `string` | 是 | 角色标识，唯一；内置角色不可改 |
| `name` | `string` | 是 | 角色名称 |
| `description` | `string` | 否 | 角色说明 |
| `isSuperuser` | `boolean` | 否 | 持有即展开为 `*`，拥有全部权限 |
| `isBuiltin` | `boolean` | 否 | 内置角色：不可删除 |
| `sortOrder` | `number` | 否 | 排序值，小的在前 |
| `createdAt` | `string` | 是 | 创建时间（UTC） |
| `permissions` | `string[]` | 否 | 角色拥有的权限码 |
| `userCount` | `number` | 否 | 使用该角色的用户数 |
| `orphanedPermissions` | `string[]` | 否 | 已失效的权限码（权限点被删或改名后残留） |

#### `PATCH /api/v1/identity/roles/{role_id}`

**编辑角色**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `role_id` | path | `string` | 是 |  |

**请求体** `RoleUpdateIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `string \| null` | 否 | 角色名称 |
| `description` | `string \| null` | 否 | 角色说明 |
| `isSuperuser` | `boolean \| null` | 否 | 仅超级管理员可设置 |

**响应** `ApiResponse<RoleOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 角色 ID |
| `code` | `string` | 是 | 角色标识，唯一；内置角色不可改 |
| `name` | `string` | 是 | 角色名称 |
| `description` | `string` | 否 | 角色说明 |
| `isSuperuser` | `boolean` | 否 | 持有即展开为 `*`，拥有全部权限 |
| `isBuiltin` | `boolean` | 否 | 内置角色：不可删除 |
| `sortOrder` | `number` | 否 | 排序值，小的在前 |
| `createdAt` | `string` | 是 | 创建时间（UTC） |
| `permissions` | `string[]` | 否 | 角色拥有的权限码 |
| `userCount` | `number` | 否 | 使用该角色的用户数 |
| `orphanedPermissions` | `string[]` | 否 | 已失效的权限码（权限点被删或改名后残留） |

#### `DELETE /api/v1/identity/roles/{role_id}`

**删除角色**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `role_id` | path | `string` | 是 |  |

**响应** `ApiResponse<null>`（前端取 `res.data`）

#### `PUT /api/v1/identity/roles/{role_id}/permissions`

**设置角色权限（整组替换）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `role_id` | path | `string` | 是 |  |

**请求体** `RolePermissionsIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `permissions` | `string[]` | 否 | 整组替换；出现未登记的权限码会被拒绝 |

**响应** `ApiResponse<RoleOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 角色 ID |
| `code` | `string` | 是 | 角色标识，唯一；内置角色不可改 |
| `name` | `string` | 是 | 角色名称 |
| `description` | `string` | 否 | 角色说明 |
| `isSuperuser` | `boolean` | 否 | 持有即展开为 `*`，拥有全部权限 |
| `isBuiltin` | `boolean` | 否 | 内置角色：不可删除 |
| `sortOrder` | `number` | 否 | 排序值，小的在前 |
| `createdAt` | `string` | 是 | 创建时间（UTC） |
| `permissions` | `string[]` | 否 | 角色拥有的权限码 |
| `userCount` | `number` | 否 | 使用该角色的用户数 |
| `orphanedPermissions` | `string[]` | 否 | 已失效的权限码（权限点被删或改名后残留） |

#### `GET /api/v1/identity/users`

**用户列表**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `keyword` | query | `string` | 否 | 匹配用户名/昵称/邮箱 |
| `isActive` | query | `boolean | null` | 否 |  |
| `roleId` | query | `string | null` | 否 |  |
| `page` | query | `number` | 否 | 页码，从 1 开始 |
| `pageSize` | query | `number` | 否 | 每页条数 |

**响应** `ApiResponse<Page<UserOut>>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `UserOut[]` | 是 | 当前页数据 |
| `total` | `number` | 是 | 总条数（用于分页器） |
| `page` | `number` | 是 | 当前页码，从 1 开始 |
| `pageSize` | `number` | 是 | 每页条数 |

#### `POST /api/v1/identity/users`

**创建用户**

**请求体** `UserCreateIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `username` | `string` | 是 | 登录名，3-64 位字母数字或 `. _ -`，会被转成小写 |
| `password` | `string` | 是 | 初始密码，管理员指定；至少 10 位，不能是纯数字 |
| `displayName` | `string` | 否 | 昵称，留空则与用户名相同 |
| `email` | `string \| null` | 否 | 邮箱 |
| `avatarUrl` | `string \| null` | 否 | 头像 URL |
| `roleIds` | `string[]` | 否 | 角色 id 列表，可留空 |

**响应** `ApiResponse<UserOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 用户 ID |
| `username` | `string` | 是 | 登录名，入库前已转小写 |
| `displayName` | `string` | 否 | 昵称（顶栏展示用） |
| `email` | `string \| null` | 否 | 邮箱，未填为 null |
| `avatarUrl` | `string \| null` | 否 | 头像 URL，未设置为 null |
| `isActive` | `boolean` | 否 | 是否启用；停用后无法登录且令牌立即失效 |
| `lastLoginAt` | `string \| null` | 否 | 最后登录时间，从未登录为 null |
| `createdAt` | `string` | 是 | 创建时间（UTC） |
| `roles` | `RoleBriefOut[]` | 否 | 拥有的角色 |

#### `GET /api/v1/identity/users/{user_id}`

**用户详情**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `user_id` | path | `string` | 是 |  |

**响应** `ApiResponse<UserOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 用户 ID |
| `username` | `string` | 是 | 登录名，入库前已转小写 |
| `displayName` | `string` | 否 | 昵称（顶栏展示用） |
| `email` | `string \| null` | 否 | 邮箱，未填为 null |
| `avatarUrl` | `string \| null` | 否 | 头像 URL，未设置为 null |
| `isActive` | `boolean` | 否 | 是否启用；停用后无法登录且令牌立即失效 |
| `lastLoginAt` | `string \| null` | 否 | 最后登录时间，从未登录为 null |
| `createdAt` | `string` | 是 | 创建时间（UTC） |
| `roles` | `RoleBriefOut[]` | 否 | 拥有的角色 |

#### `PATCH /api/v1/identity/users/{user_id}`

**编辑用户（含停用）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `user_id` | path | `string` | 是 |  |

**请求体** `UserUpdateIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `displayName` | `string \| null` | 否 | 昵称 |
| `email` | `string \| null` | 否 | 邮箱；传 null 清空 |
| `avatarUrl` | `string \| null` | 否 | 头像 URL；传 null 清空 |
| `isActive` | `boolean \| null` | 否 | 启用/停用；不能停用自己，且必须保留一个启用的超管。不接受 null：停用请传 false，不想改就不要传这个字段 |

**响应** `ApiResponse<UserOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 用户 ID |
| `username` | `string` | 是 | 登录名，入库前已转小写 |
| `displayName` | `string` | 否 | 昵称（顶栏展示用） |
| `email` | `string \| null` | 否 | 邮箱，未填为 null |
| `avatarUrl` | `string \| null` | 否 | 头像 URL，未设置为 null |
| `isActive` | `boolean` | 否 | 是否启用；停用后无法登录且令牌立即失效 |
| `lastLoginAt` | `string \| null` | 否 | 最后登录时间，从未登录为 null |
| `createdAt` | `string` | 是 | 创建时间（UTC） |
| `roles` | `RoleBriefOut[]` | 否 | 拥有的角色 |

#### `DELETE /api/v1/identity/users/{user_id}`

**停用用户（保留历史归属）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `user_id` | path | `string` | 是 |  |

**响应** `ApiResponse<UserOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 用户 ID |
| `username` | `string` | 是 | 登录名，入库前已转小写 |
| `displayName` | `string` | 否 | 昵称（顶栏展示用） |
| `email` | `string \| null` | 否 | 邮箱，未填为 null |
| `avatarUrl` | `string \| null` | 否 | 头像 URL，未设置为 null |
| `isActive` | `boolean` | 否 | 是否启用；停用后无法登录且令牌立即失效 |
| `lastLoginAt` | `string \| null` | 否 | 最后登录时间，从未登录为 null |
| `createdAt` | `string` | 是 | 创建时间（UTC） |
| `roles` | `RoleBriefOut[]` | 否 | 拥有的角色 |

#### `GET /api/v1/identity/users/{user_id}/logins`

**某账号的登录记录**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `user_id` | path | `string` | 是 |  |
| `limit` | query | `number` | 否 | 返回条数 |

**响应** `ApiResponse<LoginEventListOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `LoginEventOut[]` | 否 | 按时间倒序 |

#### `POST /api/v1/identity/users/{user_id}/reset-password`

**管理员重置密码**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `user_id` | path | `string` | 是 |  |

**请求体** `ResetPasswordIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `newPassword` | `string` | 是 | 管理员设置的新密码；至少 10 位，不能是纯数字 |

**响应** `ApiResponse<null>`（前端取 `res.data`）

#### `POST /api/v1/identity/users/{user_id}/revoke-sessions`

**踢下线（吊销该用户全部会话）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `user_id` | path | `string` | 是 |  |

**响应** `ApiResponse<RevokedSessionsOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `revoked` | `number` | 是 | 本次吊销的会话数；已经失效的会话不计入 |

#### `PUT /api/v1/identity/users/{user_id}/roles`

**设置用户角色（整组替换）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `user_id` | path | `string` | 是 |  |

**请求体** `UserRolesIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `roleIds` | `string[]` | 否 | 整组替换，传空数组即清空 |

**响应** `ApiResponse<UserOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 用户 ID |
| `username` | `string` | 是 | 登录名，入库前已转小写 |
| `displayName` | `string` | 否 | 昵称（顶栏展示用） |
| `email` | `string \| null` | 否 | 邮箱，未填为 null |
| `avatarUrl` | `string \| null` | 否 | 头像 URL，未设置为 null |
| `isActive` | `boolean` | 否 | 是否启用；停用后无法登录且令牌立即失效 |
| `lastLoginAt` | `string \| null` | 否 | 最后登录时间，从未登录为 null |
| `createdAt` | `string` | 是 | 创建时间（UTC） |
| `roles` | `RoleBriefOut[]` | 否 | 拥有的角色 |

### 4.2 任务（9 个）

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| `GET` | `/api/v1/tasks` | 任务列表 | 🔒 |
| `POST` | `/api/v1/tasks` | 创建任务 | 🔒 |
| `DELETE` | `/api/v1/tasks` | 批量删除任务 | 🔒 |
| `GET` | `/api/v1/tasks/feed` | 统一任务中心：通用生成与本人可见的替换档案 | 🔒 |
| `GET` | `/api/v1/tasks/stats` | 任务统计（侧边栏角标） | 🔒 |
| `GET` | `/api/v1/tasks/{task_id}` | 任务详情 | 🔒 |
| `PATCH` | `/api/v1/tasks/{task_id}` | 更新任务（标题 / 收藏） | 🔒 |
| `POST` | `/api/v1/tasks/{task_id}/favorite` | 收藏 / 取消收藏 | 🔒 |
| `POST` | `/api/v1/tasks/{task_id}/retry` | 重试任务 | 🔒 |

#### `GET /api/v1/tasks`

**任务列表**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `status` | query | `TaskStatusFilter` | 否 | 状态筛选 |
| `keyword` | query | `string` | 否 | 标题 / 模型 / ID 模糊搜索 |
| `sort` | query | `TaskSort` | 否 | 排序 |
| `scope` | query | `TaskScope` | 否 | 数据范围 |
| `onlyAuthorized` | query | `boolean` | 否 | 仅显示可下载任务 |
| `page` | query | `number` | 否 | 页码，从 1 开始 |
| `pageSize` | query | `number` | 否 | 每页条数 |

**响应** `ApiResponse<TaskListOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `TaskOut[]` | 否 |  |
| `total` | `number` | 否 |  |
| `page` | `number` | 否 |  |
| `pageSize` | `number` | 否 |  |
| `stats` | `TaskStats` | 否 | 侧边栏角标用的分组计数 |
| `canReadAll` | `boolean` | 否 | 当前账号能否查看他人任务；false 时前端应收起「全部」选项 |

#### `POST /api/v1/tasks`

**创建任务**

**请求体** `CreateTaskIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `prompt` | `string` | 是 | 画面描述 |
| `title` | `string \| null` | 否 | 不传则截取 prompt |
| `model` | `string \| null` | 否 | 模型 id 或名称，缺省取默认模型 |
| `durationSec` | `number \| null` | 否 | 视频时长（秒），缺省取模型的默认档位 |
| `aspectRatio` | `AspectRatio` | 否 | 画幅比例，缺省 9:16 竖屏 |
| `referenceAssetIds` | `string[]` | 否 | 参考素材 ID 列表（最多 20 个） |
| `stylePreset` | `string \| null` | 否 | 风格预设标识，留空为不套风格 |

**响应** `ApiResponse<TaskOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 任务 ID |
| `title` | `string` | 是 | 标题 |
| `status` | `TaskStatus` | 是 | 状态：queued 排队 / running 生成中 / completed 完成 / failed 失败 |
| `model` | `string` | 是 | 模型展示名，如 `Seedance 2.5` |
| `modelId` | `string \| null` | 否 | 模型目录 id，与 `GET /api/v1/models` 的 `id` 对齐 |
| `durationSec` | `number` | 是 | 视频时长（秒） |
| `aspectRatio` | `AspectRatio` | 是 | 画幅比例 |
| `progress` | `number` | 否 | 生成进度 0-100 |
| `coverUrl` | `string` | 否 | 封面图 URL，未生成完时为空串 |
| `prompt` | `string` | 否 | 画面描述 |
| `stylePreset` | `string \| null` | 否 | 风格预设标识，未设置时为 null |
| `referenceAssetIds` | `string[]` | 否 | 参考素材 ID 列表（图生视频用） |
| `authorId` | `string` | 是 | 作者用户 ID |
| `authorName` | `string` | 否 | 作者昵称快照（改名不回填历史） |
| `favorited` | `boolean` | 否 | 当前账号是否已收藏 |
| `downloadable` | `boolean` | 否 | 是否允许下载：已成功且有产物 |
| `errorMessage` | `string \| null` | 否 | 失败原因，成功时为 null |
| `createdAt` | `string` | 是 | 创建时间（UTC） |
| `updatedAt` | `string` | 是 | 更新时间（UTC） |
| `startedAt` | `string \| null` | 否 | 开始生成时间，未开始为 null |

#### `DELETE /api/v1/tasks`

**批量删除任务**

**请求体** `DeleteTasksIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `ids` | `string[]` | 是 | 要删除的任务 ID 列表 |

**响应** `ApiResponse<DeleteTasksOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `deleted` | `number` | 是 | 实际删除的条数 |
| `skipped` | `string[]` | 否 | 被跳过的任务 ID：不存在，或属于他人且没有 `task:read:all` |

#### `GET /api/v1/tasks/feed`

**统一任务中心：通用生成与本人可见的替换档案**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `source` | query | `"all" | "video" | "replacement"` | 否 |  |
| `status` | query | `"all" | "queued" | "running" | "completed" | "failed" | "attention"` | 否 |  |
| `keyword` | query | `string` | 否 |  |
| `sort` | query | `TaskSort` | 否 |  |
| `scope` | query | `TaskScope` | 否 |  |
| `onlyAuthorized` | query | `boolean` | 否 |  |
| `statsOnly` | query | `boolean` | 否 | 只返回统计，不组装条目；角标等轮询场景用 |
| `page` | query | `number` | 否 | 页码，从 1 开始 |
| `pageSize` | query | `number` | 否 | 每页条数 |

**响应** `ApiResponse<TaskFeedOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `TaskFeedItem[]` | 否 |  |
| `total` | `number` | 否 |  |
| `page` | `number` | 否 |  |
| `pageSize` | `number` | 否 |  |
| `stats` | `TaskFeedStats` | 否 | 筛选前分组统计 |
| `canReadAll` | `boolean` | 否 | 可否查看他人的通用任务 |
| `warnings` | `string[]` | 否 | 部分来源不可用时的提示 |

#### `GET /api/v1/tasks/stats`

**任务统计（侧边栏角标）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `scope` | query | `TaskScope` | 否 | 统计口径，与列表 scope 一致 |

**响应** `ApiResponse<TaskStats>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `all` | `number` | 否 | 总数 |
| `queued` | `number` | 否 | 排队中 |
| `running` | `number` | 否 | 生成中 |
| `completed` | `number` | 否 | 已完成 |
| `failed` | `number` | 否 | 失败 |

#### `GET /api/v1/tasks/{task_id}`

**任务详情**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `task_id` | path | `string` | 是 | 任务 ID |

**响应** `ApiResponse<TaskOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 任务 ID |
| `title` | `string` | 是 | 标题 |
| `status` | `TaskStatus` | 是 | 状态：queued 排队 / running 生成中 / completed 完成 / failed 失败 |
| `model` | `string` | 是 | 模型展示名，如 `Seedance 2.5` |
| `modelId` | `string \| null` | 否 | 模型目录 id，与 `GET /api/v1/models` 的 `id` 对齐 |
| `durationSec` | `number` | 是 | 视频时长（秒） |
| `aspectRatio` | `AspectRatio` | 是 | 画幅比例 |
| `progress` | `number` | 否 | 生成进度 0-100 |
| `coverUrl` | `string` | 否 | 封面图 URL，未生成完时为空串 |
| `prompt` | `string` | 否 | 画面描述 |
| `stylePreset` | `string \| null` | 否 | 风格预设标识，未设置时为 null |
| `referenceAssetIds` | `string[]` | 否 | 参考素材 ID 列表（图生视频用） |
| `authorId` | `string` | 是 | 作者用户 ID |
| `authorName` | `string` | 否 | 作者昵称快照（改名不回填历史） |
| `favorited` | `boolean` | 否 | 当前账号是否已收藏 |
| `downloadable` | `boolean` | 否 | 是否允许下载：已成功且有产物 |
| `errorMessage` | `string \| null` | 否 | 失败原因，成功时为 null |
| `createdAt` | `string` | 是 | 创建时间（UTC） |
| `updatedAt` | `string` | 是 | 更新时间（UTC） |
| `startedAt` | `string \| null` | 否 | 开始生成时间，未开始为 null |

#### `PATCH /api/v1/tasks/{task_id}`

**更新任务（标题 / 收藏）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `task_id` | path | `string` | 是 | 任务 ID |

**请求体** `UpdateTaskIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | `string \| null` | 否 | 新标题 |
| `favorited` | `boolean \| null` | 否 | 收藏状态 |

**响应** `ApiResponse<TaskOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 任务 ID |
| `title` | `string` | 是 | 标题 |
| `status` | `TaskStatus` | 是 | 状态：queued 排队 / running 生成中 / completed 完成 / failed 失败 |
| `model` | `string` | 是 | 模型展示名，如 `Seedance 2.5` |
| `modelId` | `string \| null` | 否 | 模型目录 id，与 `GET /api/v1/models` 的 `id` 对齐 |
| `durationSec` | `number` | 是 | 视频时长（秒） |
| `aspectRatio` | `AspectRatio` | 是 | 画幅比例 |
| `progress` | `number` | 否 | 生成进度 0-100 |
| `coverUrl` | `string` | 否 | 封面图 URL，未生成完时为空串 |
| `prompt` | `string` | 否 | 画面描述 |
| `stylePreset` | `string \| null` | 否 | 风格预设标识，未设置时为 null |
| `referenceAssetIds` | `string[]` | 否 | 参考素材 ID 列表（图生视频用） |
| `authorId` | `string` | 是 | 作者用户 ID |
| `authorName` | `string` | 否 | 作者昵称快照（改名不回填历史） |
| `favorited` | `boolean` | 否 | 当前账号是否已收藏 |
| `downloadable` | `boolean` | 否 | 是否允许下载：已成功且有产物 |
| `errorMessage` | `string \| null` | 否 | 失败原因，成功时为 null |
| `createdAt` | `string` | 是 | 创建时间（UTC） |
| `updatedAt` | `string` | 是 | 更新时间（UTC） |
| `startedAt` | `string \| null` | 否 | 开始生成时间，未开始为 null |

#### `POST /api/v1/tasks/{task_id}/favorite`

**收藏 / 取消收藏**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `task_id` | path | `string` | 是 | 任务 ID |

**响应** `ApiResponse<TaskOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 任务 ID |
| `title` | `string` | 是 | 标题 |
| `status` | `TaskStatus` | 是 | 状态：queued 排队 / running 生成中 / completed 完成 / failed 失败 |
| `model` | `string` | 是 | 模型展示名，如 `Seedance 2.5` |
| `modelId` | `string \| null` | 否 | 模型目录 id，与 `GET /api/v1/models` 的 `id` 对齐 |
| `durationSec` | `number` | 是 | 视频时长（秒） |
| `aspectRatio` | `AspectRatio` | 是 | 画幅比例 |
| `progress` | `number` | 否 | 生成进度 0-100 |
| `coverUrl` | `string` | 否 | 封面图 URL，未生成完时为空串 |
| `prompt` | `string` | 否 | 画面描述 |
| `stylePreset` | `string \| null` | 否 | 风格预设标识，未设置时为 null |
| `referenceAssetIds` | `string[]` | 否 | 参考素材 ID 列表（图生视频用） |
| `authorId` | `string` | 是 | 作者用户 ID |
| `authorName` | `string` | 否 | 作者昵称快照（改名不回填历史） |
| `favorited` | `boolean` | 否 | 当前账号是否已收藏 |
| `downloadable` | `boolean` | 否 | 是否允许下载：已成功且有产物 |
| `errorMessage` | `string \| null` | 否 | 失败原因，成功时为 null |
| `createdAt` | `string` | 是 | 创建时间（UTC） |
| `updatedAt` | `string` | 是 | 更新时间（UTC） |
| `startedAt` | `string \| null` | 否 | 开始生成时间，未开始为 null |

#### `POST /api/v1/tasks/{task_id}/retry`

**重试任务**

重试的结果只回 `id` / `status` / `updatedAt`。

不返回整份任务详情：`task:retry:any` 允许「帮别人重跑」，但没允许
「读别人的 prompt」——那是 `task:read:all` 的事（R6-05）。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `task_id` | path | `string` | 是 | 任务 ID |

**响应** `ApiResponse<TaskRetryOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 任务 ID |
| `status` | `TaskStatus` | 是 | 重试后的状态，正常为 queued |
| `updatedAt` | `string` | 是 | 状态变更时间（UTC），即重新排队的时间 |

### 4.3 模型（1 个）

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| `GET` | `/api/v1/models` | 可用模型目录（含参数 schema） | 🔒 |

#### `GET /api/v1/models`

**可用模型目录（含参数 schema）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `capability` | query | `Capability | null` | 否 | 能力筛选：video / image / audio |

**响应** `ApiResponse<ModelListOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `ModelOut[]` | 否 |  |
| `total` | `number` | 否 |  |

### 4.4 素材（31 个）

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| `GET` | `/api/v1/asset-categories` | 当前库的分类树与计数 | 🔒 |
| `GET` | `/api/v1/asset-tags` | 标签词表（按维度分组，带在用数量） | 🔒 |
| `POST` | `/api/v1/asset-tags` | 新增标签（词表外的新词） | 🔒 |
| `GET` | `/api/v1/asset-views` | 我的保存视图 | 🔒 |
| `POST` | `/api/v1/asset-views` | 保存当前筛选条件 | 🔒 |
| `DELETE` | `/api/v1/asset-views/{view_id}` | 删除我的保存视图 | 🔒 |
| `GET` | `/api/v1/assets` | 归档素材列表与空间占用 | 🔒 |
| `POST` | `/api/v1/assets/batch/adopt` | 整组采纳候选（全成或全不成） | 🔒 |
| `GET` | `/api/v1/assets/candidates` | 待确认的生成候选（按批分组） | 🔒 |
| `POST` | `/api/v1/assets/import` | 从 Z 盘 inbox 登记现有素材 | 🔒 |
| `POST` | `/api/v1/assets/upload` | 流式上传素材至归档盘 | 🔒 |
| `GET` | `/api/v1/assets/{asset_id}` | 读取本人或共享原片详情 | 🔒 |
| `DELETE` | `/api/v1/assets/{asset_id}` | 彻底删除回收站中的归档素材 | 🔒 |
| `POST` | `/api/v1/assets/{asset_id}/adopt` | 采纳生成候选（成为素材） | 🔒 |
| `PUT` | `/api/v1/assets/{asset_id}/deleted` | 将本人素材移入回收站或恢复 | 🔒 |
| `POST` | `/api/v1/assets/{asset_id}/derive` | 一键衍生：回填生成页的输入位 | 🔒 |
| `POST` | `/api/v1/assets/{asset_id}/discard` | 弃用生成候选 | 🔒 |
| `GET` | `/api/v1/assets/{asset_id}/file` | 预览或下载已归档素材 | 🔒 |
| `GET` | `/api/v1/assets/{asset_id}/lineage` | 生成血缘：配方、父链与引用关系 | 🔒 |
| `PUT` | `/api/v1/assets/{asset_id}/name` | 修改本人素材名称 | 🔒 |
| `GET` | `/api/v1/assets/{asset_id}/preview` | 受控公共原片预览 | 🔒 |
| `PUT` | `/api/v1/assets/{asset_id}/sharing` | 分享或取消分享本人原片 | 🔒 |
| `GET` | `/api/v1/assets/{asset_id}/stream` | 浏览器兼容视频预览（派生文件，不改原片） | 🔒 |
| `GET` | `/api/v1/assets/{asset_id}/tags` | 读取素材的标签 | 🔒 |
| `PUT` | `/api/v1/assets/{asset_id}/tags` | 整体替换本人素材的标签 | 🔒 |
| `GET` | `/api/v1/assets/{asset_id}/thumbnail` | 读取归档素材缩略图 | 🔒 |
| `GET` | `/api/v1/subjects` | 主体列表（带成员数与可用性） | 🔒 |
| `GET` | `/api/v1/subjects/by-legacy-pack/{pack_id}` | 按旧素材包 id 解析主体（B5） | 🔒 |
| `GET` | `/api/v1/subjects/{subject_id}` | 主体详情（含成员条带与缺项） | 🔒 |
| `POST` | `/api/v1/subjects/{subject_id}/publish` | 发布主体（过可达性后冻结版本） | 🔒 |
| `GET` | `/api/v1/teams` | 当前用户可见的团队（库切换器数据源） | 🔒 |

#### `GET /api/v1/asset-categories`

**当前库的分类树与计数**

返回当前库的分类树 + 每个分类的素材数。

计数口径与 `GET /assets` 的分支一一对应（未删除 + 当前库可见）。
两处一旦不一致，症状是「角标 149、点进去 12 条」，而这种差异
通常被当成缓存问题排查很久。

团队库会先校验成员关系：不是成员是 403，不是空列表——空列表会被读成
「这个团队还没传东西」，用户会去要权限，而实际是他根本不该看。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `library` | query | `string` | 否 |  |

**响应** `ApiResponse<AssetCategoryList>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `library` | `string` | 是 |  |
| `items` | `AssetCategoryOut[]` | 是 |  |
| `inboxCount` | `number` | 是 |  |

#### `GET /api/v1/asset-tags`

**标签词表（按维度分组，带在用数量）**

筛选条的数据源。

`useCount` 只数未删除的素材：回收站里的关联保留（恢复后标签要回来），
但不计入热度——否则一个词能靠一堆已删除的素材把自己顶进常显区。

**响应** `ApiResponse<TagCatalog>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `groups` | `TagGroup[]` | 否 |  |
| `total` | `number` | 否 |  |
| `pinnedThreshold` | `number` | 否 |  |

#### `POST /api/v1/asset-tags`

**新增标签（词表外的新词）**

新增即生效、不审批，但记 `created_by`。

**准入三问在表单上，不在接口里**：接口只能挡住"同名词"和"维度不存在"，
挡不住"这个概念两个人看着会给出不同答案"。所以这条路径要窄——只有管理员
能进，且每次新增都留痕，便于季度复查时把零使用的词收掉。

**请求体** `TagCreate`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `dimension` | `string` | 是 |  |
| `name` | `string` | 是 |  |

**响应** `ApiResponse<TagOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `dimension` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `status` | `string` | 否 |  |
| `source` | `string` | 否 |  |
| `useCount` | `number` | 否 |  |
| `pinned` | `boolean` | 否 |  |

#### `GET /api/v1/asset-views`

**我的保存视图**

**响应** `ApiResponse<AssetViewList>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `AssetViewOut[]` | 否 |  |

#### `POST /api/v1/asset-views`

**保存当前筛选条件**

存的是**筛选参数**，不是结果集。

存结果集的视图会在素材增减后给出过期的列表——用户以为"这个视图里就这些"，
而实际上它是某个时刻的快照。参数是幂等的，结果不是。

**请求体** `AssetViewCreate`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `string` | 是 |  |
| `query` | `Record<string, unknown>` | 否 |  |
| `sort` | `string` | 否 |  |

**响应** `ApiResponse<AssetViewOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `query` | `Record<string, unknown>` | 否 |  |
| `sort` | `string` | 否 |  |
| `createdAt` | `string` | 是 |  |

#### `DELETE /api/v1/asset-views/{view_id}`

**删除我的保存视图**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `view_id` | path | `string` | 是 |  |

**响应** `ApiResponse<unknown>`（前端取 `res.data`）

#### `GET /api/v1/assets`

**归档素材列表与空间占用**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `category` | query | `"all" | "source" | "character" | "product" | "scene" | "audio"` | 否 |  |
| `scope` | query | `"mine" | "public" | "trash" | "team"` | 否 |  |
| `keyword` | query | `string` | 否 |  |
| `state` | query | `"all" | "private" | "shared"` | 否 |  |
| `page` | query | `number` | 否 |  |
| `pageSize` | query | `number` | 否 |  |
| `teamId` | query | `string` | 否 |  |
| `tagIds` | query | `string[] | null` | 否 |  |
| `sort` | query | `string` | 否 |  |
| `categoryId` | query | `string` | 否 |  |
| `mediaType` | query | `string` | 否 |  |

**响应** `ApiResponse<ArchiveList>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `ArchivedAsset[]` | 是 |  |
| `total` | `number` | 是 |  |
| `usedBytes` | `number` | 是 |  |
| `quotaBytes` | `number` | 否 |  |
| `ownerUsedBytes` | `number` | 否 |  |
| `quota` | `AssetQuota` | 否 |  |
| `page` | `number` | 否 |  |
| `pageSize` | `number` | 否 |  |

#### `POST /api/v1/assets/batch/adopt`

**整组采纳候选（全成或全不成）**

**请求体** `BatchAdoptRequest`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `assetIds` | `string[]` | 是 |  |
| `categoryId` | `string` | 否 |  |
| `tagIds` | `string[]` | 否 |  |

**响应** `ApiResponse<BatchAdoptResult>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `ArchivedAsset[]` | 是 |  |
| `already` | `number` | 否 |  |

#### `GET /api/v1/assets/candidates`

**待确认的生成候选（按批分组）**

候选是**待办**，不是分类：它不进 `GET /assets`，只在这里按批出现。

返回的是「几批」而不是「一页」——同一批（同一 `recipe_id`）必须挨着，
否则"刚才那 4 张里选 2 张"这个动作在界面上无法完成。

**响应** `ApiResponse<CandidateList>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `groups` | `CandidateGroup[]` | 否 |  |
| `total` | `number` | 否 |  |

#### `POST /api/v1/assets/import`

**从 Z 盘 inbox 登记现有素材**

**请求体** `ImportAsset`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `relativePath` | `string` | 是 |  |
| `category` | `"source" \| "character" \| "product" \| "scene" \| "audio"` | 是 |  |

**响应** `ApiResponse<ArchivedAsset>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `category` | `"source" \| "character" \| "product" \| "scene" \| "audio"` | 是 |  |
| `type` | `"image" \| "video" \| "audio"` | 是 |  |
| `mimeType` | `string` | 是 |  |
| `sizeBytes` | `number` | 是 |  |
| `path` | `string` | 是 |  |
| `sha256` | `string` | 是 |  |
| `createdAt` | `string` | 是 |  |
| `width` | `number` | 否 |  |
| `height` | `number` | 否 |  |
| `duration` | `number` | 否 |  |
| `hasAudio` | `boolean` | 否 |  |
| `url` | `string` | 是 |  |
| `previewUrl` | `string \| null` | 否 |  |
| `ownerId` | `string` | 否 |  |
| `shared` | `boolean` | 否 |  |
| `sharedAt` | `string \| null` | 否 |  |
| `deletedAt` | `string \| null` | 否 |  |
| `stateVersion` | `number` | 否 |  |
| `editable` | `boolean` | 否 |  |
| `downloadAllowed` | `boolean` | 否 |  |
| `origin` | `string` | 否 |  |
| `stage` | `string` | 否 |  |
| `recipeId` | `string` | 否 |  |
| `complianceStatus` | `string` | 否 |  |
| `adoptedAt` | `string \| null` | 否 |  |
| `categoryId` | `string` | 否 |  |

#### `POST /api/v1/assets/upload`

**流式上传素材至归档盘**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `category` | query | `"source" | "character" | "product" | "scene" | "audio"` | 是 |  |
| `filename` | query | `string` | 是 |  |

**响应** `ApiResponse<ArchivedAsset>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `category` | `"source" \| "character" \| "product" \| "scene" \| "audio"` | 是 |  |
| `type` | `"image" \| "video" \| "audio"` | 是 |  |
| `mimeType` | `string` | 是 |  |
| `sizeBytes` | `number` | 是 |  |
| `path` | `string` | 是 |  |
| `sha256` | `string` | 是 |  |
| `createdAt` | `string` | 是 |  |
| `width` | `number` | 否 |  |
| `height` | `number` | 否 |  |
| `duration` | `number` | 否 |  |
| `hasAudio` | `boolean` | 否 |  |
| `url` | `string` | 是 |  |
| `previewUrl` | `string \| null` | 否 |  |
| `ownerId` | `string` | 否 |  |
| `shared` | `boolean` | 否 |  |
| `sharedAt` | `string \| null` | 否 |  |
| `deletedAt` | `string \| null` | 否 |  |
| `stateVersion` | `number` | 否 |  |
| `editable` | `boolean` | 否 |  |
| `downloadAllowed` | `boolean` | 否 |  |
| `origin` | `string` | 否 |  |
| `stage` | `string` | 否 |  |
| `recipeId` | `string` | 否 |  |
| `complianceStatus` | `string` | 否 |  |
| `adoptedAt` | `string \| null` | 否 |  |
| `categoryId` | `string` | 否 |  |

#### `GET /api/v1/assets/{asset_id}`

**读取本人或共享原片详情**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**响应** `ApiResponse<ArchivedAsset>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `category` | `"source" \| "character" \| "product" \| "scene" \| "audio"` | 是 |  |
| `type` | `"image" \| "video" \| "audio"` | 是 |  |
| `mimeType` | `string` | 是 |  |
| `sizeBytes` | `number` | 是 |  |
| `path` | `string` | 是 |  |
| `sha256` | `string` | 是 |  |
| `createdAt` | `string` | 是 |  |
| `width` | `number` | 否 |  |
| `height` | `number` | 否 |  |
| `duration` | `number` | 否 |  |
| `hasAudio` | `boolean` | 否 |  |
| `url` | `string` | 是 |  |
| `previewUrl` | `string \| null` | 否 |  |
| `ownerId` | `string` | 否 |  |
| `shared` | `boolean` | 否 |  |
| `sharedAt` | `string \| null` | 否 |  |
| `deletedAt` | `string \| null` | 否 |  |
| `stateVersion` | `number` | 否 |  |
| `editable` | `boolean` | 否 |  |
| `downloadAllowed` | `boolean` | 否 |  |
| `origin` | `string` | 否 |  |
| `stage` | `string` | 否 |  |
| `recipeId` | `string` | 否 |  |
| `complianceStatus` | `string` | 否 |  |
| `adoptedAt` | `string \| null` | 否 |  |
| `categoryId` | `string` | 否 |  |

#### `DELETE /api/v1/assets/{asset_id}`

**彻底删除回收站中的归档素材**

清空单条回收站记录。与 `PUT /deleted` 分成两步：软删可恢复，真删不可逆。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**响应** `ApiResponse<unknown>`（前端取 `res.data`）

#### `POST /api/v1/assets/{asset_id}/adopt`

**采纳生成候选（成为素材）**

补分类（可选）后把候选转正。

幂等：已经采纳过的再点一次返回现状，而不是 409——"全选"与双击都会打到
同一行，回冲突会让用户以为自己的操作没生效。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**请求体** `AdoptRequest`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `categoryId` | `string` | 否 |  |
| `tagIds` | `string[]` | 否 |  |

**响应** `ApiResponse<ArchivedAsset>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `category` | `"source" \| "character" \| "product" \| "scene" \| "audio"` | 是 |  |
| `type` | `"image" \| "video" \| "audio"` | 是 |  |
| `mimeType` | `string` | 是 |  |
| `sizeBytes` | `number` | 是 |  |
| `path` | `string` | 是 |  |
| `sha256` | `string` | 是 |  |
| `createdAt` | `string` | 是 |  |
| `width` | `number` | 否 |  |
| `height` | `number` | 否 |  |
| `duration` | `number` | 否 |  |
| `hasAudio` | `boolean` | 否 |  |
| `url` | `string` | 是 |  |
| `previewUrl` | `string \| null` | 否 |  |
| `ownerId` | `string` | 否 |  |
| `shared` | `boolean` | 否 |  |
| `sharedAt` | `string \| null` | 否 |  |
| `deletedAt` | `string \| null` | 否 |  |
| `stateVersion` | `number` | 否 |  |
| `editable` | `boolean` | 否 |  |
| `downloadAllowed` | `boolean` | 否 |  |
| `origin` | `string` | 否 |  |
| `stage` | `string` | 否 |  |
| `recipeId` | `string` | 否 |  |
| `complianceStatus` | `string` | 否 |  |
| `adoptedAt` | `string \| null` | 否 |  |
| `categoryId` | `string` | 否 |  |

#### `PUT /api/v1/assets/{asset_id}/deleted`

**将本人素材移入回收站或恢复**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**请求体** `ArchiveChange`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `expectedVersion` | `number` | 是 |  |
| `name` | `string \| null` | 否 |  |
| `shared` | `boolean \| null` | 否 |  |
| `deleted` | `boolean \| null` | 否 |  |

**响应** `ApiResponse<ArchivedAsset>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `category` | `"source" \| "character" \| "product" \| "scene" \| "audio"` | 是 |  |
| `type` | `"image" \| "video" \| "audio"` | 是 |  |
| `mimeType` | `string` | 是 |  |
| `sizeBytes` | `number` | 是 |  |
| `path` | `string` | 是 |  |
| `sha256` | `string` | 是 |  |
| `createdAt` | `string` | 是 |  |
| `width` | `number` | 否 |  |
| `height` | `number` | 否 |  |
| `duration` | `number` | 否 |  |
| `hasAudio` | `boolean` | 否 |  |
| `url` | `string` | 是 |  |
| `previewUrl` | `string \| null` | 否 |  |
| `ownerId` | `string` | 否 |  |
| `shared` | `boolean` | 否 |  |
| `sharedAt` | `string \| null` | 否 |  |
| `deletedAt` | `string \| null` | 否 |  |
| `stateVersion` | `number` | 否 |  |
| `editable` | `boolean` | 否 |  |
| `downloadAllowed` | `boolean` | 否 |  |
| `origin` | `string` | 否 |  |
| `stage` | `string` | 否 |  |
| `recipeId` | `string` | 否 |  |
| `complianceStatus` | `string` | 否 |  |
| `adoptedAt` | `string \| null` | 否 |  |
| `categoryId` | `string` | 否 |  |

#### `POST /api/v1/assets/{asset_id}/derive`

**一键衍生：回填生成页的输入位**

只回填输入，**不建任务、不扣费**：生成有额度与模型选择，提交由生成页自己做。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**请求体** `DeriveRequest`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `mode` | `"image_ref" \| "video_first_frame" \| "prompt_extract"` | 是 |  |

**响应** `ApiResponse<DeriveIntent>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `mode` | `string` | 是 |  |
| `assetId` | `string` | 是 |  |
| `prompt` | `string` | 否 |  |
| `referenceAssetIds` | `string[]` | 否 |  |
| `referenceRole` | `string` | 否 |  |
| `sourceRecipeId` | `string \| null` | 否 |  |

#### `POST /api/v1/assets/{asset_id}/discard`

**弃用生成候选**

弃用**不删字节**：字节等宽限期后由 `asset.gc` 回收，配方永久保留
—— 衍生链可能从它经过，删了会让成品的血缘在中途断掉。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**响应** `ApiResponse<ArchivedAsset>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `category` | `"source" \| "character" \| "product" \| "scene" \| "audio"` | 是 |  |
| `type` | `"image" \| "video" \| "audio"` | 是 |  |
| `mimeType` | `string` | 是 |  |
| `sizeBytes` | `number` | 是 |  |
| `path` | `string` | 是 |  |
| `sha256` | `string` | 是 |  |
| `createdAt` | `string` | 是 |  |
| `width` | `number` | 否 |  |
| `height` | `number` | 否 |  |
| `duration` | `number` | 否 |  |
| `hasAudio` | `boolean` | 否 |  |
| `url` | `string` | 是 |  |
| `previewUrl` | `string \| null` | 否 |  |
| `ownerId` | `string` | 否 |  |
| `shared` | `boolean` | 否 |  |
| `sharedAt` | `string \| null` | 否 |  |
| `deletedAt` | `string \| null` | 否 |  |
| `stateVersion` | `number` | 否 |  |
| `editable` | `boolean` | 否 |  |
| `downloadAllowed` | `boolean` | 否 |  |
| `origin` | `string` | 否 |  |
| `stage` | `string` | 否 |  |
| `recipeId` | `string` | 否 |  |
| `complianceStatus` | `string` | 否 |  |
| `adoptedAt` | `string \| null` | 否 |  |
| `categoryId` | `string` | 否 |  |

#### `GET /api/v1/assets/{asset_id}/file`

**预览或下载已归档素材**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**响应** `ApiResponse<unknown>`（前端取 `res.data`）

#### `GET /api/v1/assets/{asset_id}/lineage`

**生成血缘：配方、父链与引用关系**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**响应** `ApiResponse<LineageOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `assetId` | `string` | 是 |  |
| `recipe` | `RecipeOut \| null` | 否 |  |
| `references` | `AssetRefOut[]` | 否 |  |
| `parents` | `LineageNode[]` | 否 |  |
| `children` | `LineageNode[]` | 否 |  |
| `usedBy` | `AssetUsage[]` | 否 |  |

#### `PUT /api/v1/assets/{asset_id}/name`

**修改本人素材名称**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**请求体** `ArchiveChange`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `expectedVersion` | `number` | 是 |  |
| `name` | `string \| null` | 否 |  |
| `shared` | `boolean \| null` | 否 |  |
| `deleted` | `boolean \| null` | 否 |  |

**响应** `ApiResponse<ArchivedAsset>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `category` | `"source" \| "character" \| "product" \| "scene" \| "audio"` | 是 |  |
| `type` | `"image" \| "video" \| "audio"` | 是 |  |
| `mimeType` | `string` | 是 |  |
| `sizeBytes` | `number` | 是 |  |
| `path` | `string` | 是 |  |
| `sha256` | `string` | 是 |  |
| `createdAt` | `string` | 是 |  |
| `width` | `number` | 否 |  |
| `height` | `number` | 否 |  |
| `duration` | `number` | 否 |  |
| `hasAudio` | `boolean` | 否 |  |
| `url` | `string` | 是 |  |
| `previewUrl` | `string \| null` | 否 |  |
| `ownerId` | `string` | 否 |  |
| `shared` | `boolean` | 否 |  |
| `sharedAt` | `string \| null` | 否 |  |
| `deletedAt` | `string \| null` | 否 |  |
| `stateVersion` | `number` | 否 |  |
| `editable` | `boolean` | 否 |  |
| `downloadAllowed` | `boolean` | 否 |  |
| `origin` | `string` | 否 |  |
| `stage` | `string` | 否 |  |
| `recipeId` | `string` | 否 |  |
| `complianceStatus` | `string` | 否 |  |
| `adoptedAt` | `string \| null` | 否 |  |
| `categoryId` | `string` | 否 |  |

#### `GET /api/v1/assets/{asset_id}/preview`

**受控公共原片预览**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**响应** `ApiResponse<unknown>`（前端取 `res.data`）

#### `PUT /api/v1/assets/{asset_id}/sharing`

**分享或取消分享本人原片**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**请求体** `ArchiveChange`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `expectedVersion` | `number` | 是 |  |
| `name` | `string \| null` | 否 |  |
| `shared` | `boolean \| null` | 否 |  |
| `deleted` | `boolean \| null` | 否 |  |

**响应** `ApiResponse<ArchivedAsset>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `category` | `"source" \| "character" \| "product" \| "scene" \| "audio"` | 是 |  |
| `type` | `"image" \| "video" \| "audio"` | 是 |  |
| `mimeType` | `string` | 是 |  |
| `sizeBytes` | `number` | 是 |  |
| `path` | `string` | 是 |  |
| `sha256` | `string` | 是 |  |
| `createdAt` | `string` | 是 |  |
| `width` | `number` | 否 |  |
| `height` | `number` | 否 |  |
| `duration` | `number` | 否 |  |
| `hasAudio` | `boolean` | 否 |  |
| `url` | `string` | 是 |  |
| `previewUrl` | `string \| null` | 否 |  |
| `ownerId` | `string` | 否 |  |
| `shared` | `boolean` | 否 |  |
| `sharedAt` | `string \| null` | 否 |  |
| `deletedAt` | `string \| null` | 否 |  |
| `stateVersion` | `number` | 否 |  |
| `editable` | `boolean` | 否 |  |
| `downloadAllowed` | `boolean` | 否 |  |
| `origin` | `string` | 否 |  |
| `stage` | `string` | 否 |  |
| `recipeId` | `string` | 否 |  |
| `complianceStatus` | `string` | 否 |  |
| `adoptedAt` | `string \| null` | 否 |  |
| `categoryId` | `string` | 否 |  |

#### `GET /api/v1/assets/{asset_id}/stream`

**浏览器兼容视频预览（派生文件，不改原片）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**响应** `ApiResponse<unknown>`（前端取 `res.data`）

#### `GET /api/v1/assets/{asset_id}/tags`

**读取素材的标签**

与 `PUT` 成对：只写不读的话，详情浮层里的标签只能靠列表接口顺带带回来，
而列表一次 200 条、每条都带标签会让响应体大一个量级。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**响应** `ApiResponse<AssetTagList>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `assetId` | `string` | 是 |  |
| `items` | `TagOut[]` | 否 |  |

#### `PUT /api/v1/assets/{asset_id}/tags`

**整体替换本人素材的标签**

整体替换而不是逐条增删：前端是勾选面板，它天然知道勾了哪几个。

「整体替换」的代价是并发下后写覆盖（两个人同时改同一条素材），
但素材标签不是协同编辑的对象——它是一组个人描述，覆盖比冲突提示更符合预期。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**请求体** `AssetTagSet`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `tagIds` | `string[]` | 否 |  |

**响应** `ApiResponse<AssetTagList>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `assetId` | `string` | 是 |  |
| `items` | `TagOut[]` | 否 |  |

#### `GET /api/v1/assets/{asset_id}/thumbnail`

**读取归档素材缩略图**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**响应** `ApiResponse<unknown>`（前端取 `res.data`）

#### `GET /api/v1/subjects`

**主体列表（带成员数与可用性）**

列表项不带成员明细：一页 24 个主体 × 10 个成员 = 240 条素材塞进一次响应，
而其中绝大多数不会被看。要看成员就点进详情。

`availability` 三态在服务端算（`ready` / `broken` / `incomplete`）。
前端复算必然与后端漂移，症状是"卡片显示可发布、点发布报缺少能力"。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `subjectType` | query | `string` | 否 |  |
| `page` | query | `number` | 否 |  |
| `pageSize` | query | `number` | 否 |  |

**响应** `ApiResponse<SubjectList>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `SubjectOut[]` | 是 |  |
| `total` | `number` | 否 |  |
| `page` | `number` | 否 |  |
| `pageSize` | `number` | 否 |  |

#### `GET /api/v1/subjects/by-legacy-pack/{pack_id}`

**按旧素材包 id 解析主体（B5）**

旧链接、外部系统、历史工单里全是 `packId`。这条让它们还能打开。

解析不到是 404 而不是空主体：空主体会让前端渲染一张"什么都是空的"卡片，
看起来像数据坏了；404 至少说清了"这个 id 没有对应主体"。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `pack_id` | path | `string` | 是 |  |

**响应** `ApiResponse<SubjectDetail>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `subjectType` | `string` | 是 |  |
| `status` | `string` | 是 |  |
| `visibility` | `string` | 是 |  |
| `teamId` | `string` | 否 |  |
| `categoryId` | `string` | 否 |  |
| `coverAssetId` | `string` | 否 |  |
| `coverUrl` | `string` | 否 |  |
| `memberCount` | `number` | 否 |  |
| `currentVersion` | `number` | 否 |  |
| `availability` | `string` | 否 |  |
| `availabilityNote` | `string` | 否 |  |
| `members` | `SubjectMemberOut[]` | 否 |  |
| `attributes` | `Record<string, unknown>` | 否 |  |
| `requires` | `string[]` | 否 |  |
| `requiredRoles` | `string[]` | 否 |  |
| `missingCapabilities` | `string[]` | 否 |  |
| `missingRoles` | `string[]` | 否 |  |
| `memberGaps` | `Record<string, string[]>` | 否 |  |

#### `GET /api/v1/subjects/{subject_id}`

**主体详情（含成员条带与缺项）**

详情带成员条带，每条含 `grants` 与 `missing`。

`memberGaps` 是"到底谁不给哪项能力"的答案。只给主体级结论（缺少 download）
的话，用户只能一张张点开看——主体最多 10 张，但没人会真的去数。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `subject_id` | path | `string` | 是 |  |

**响应** `ApiResponse<SubjectDetail>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `subjectType` | `string` | 是 |  |
| `status` | `string` | 是 |  |
| `visibility` | `string` | 是 |  |
| `teamId` | `string` | 否 |  |
| `categoryId` | `string` | 否 |  |
| `coverAssetId` | `string` | 否 |  |
| `coverUrl` | `string` | 否 |  |
| `memberCount` | `number` | 否 |  |
| `currentVersion` | `number` | 否 |  |
| `availability` | `string` | 否 |  |
| `availabilityNote` | `string` | 否 |  |
| `members` | `SubjectMemberOut[]` | 否 |  |
| `attributes` | `Record<string, unknown>` | 否 |  |
| `requires` | `string[]` | 否 |  |
| `requiredRoles` | `string[]` | 否 |  |
| `missingCapabilities` | `string[]` | 否 |  |
| `missingRoles` | `string[]` | 否 |  |
| `memberGaps` | `Record<string, string[]>` | 否 |  |

#### `POST /api/v1/subjects/{subject_id}/publish`

**发布主体（过可达性后冻结版本）**

发布有三道前置，缺一不可：至少一张成员、角色可达、能力可达。

失败时**不返回 200 + 空结果**：发布是模板侧缓存的失效点，静默失败会让
"发布了但模板还是老图"变成一个要靠猜的问题。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `subject_id` | path | `string` | 是 |  |

**响应** `ApiResponse<SubjectPublishResult>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `subjectId` | `string` | 是 |  |
| `version` | `number` | 是 |  |
| `publishedAt` | `string` | 是 |  |

#### `GET /api/v1/teams`

**当前用户可见的团队（库切换器数据源）**

可见范围：有 `asset:team:manage` 的人看全部，其余人只看自己所在的团队。

**跨团队不可见是默认行为**，不是 M5 才生效的策略：三个团队并存时，
「能看见但不能改」会让每个团队都变成别人的参考库，边界形同虚设。
要跨团队共享走「提升到公共库」那条路，那条路有审核也有审计。

**响应** `ApiResponse<TeamList>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `TeamOut[]` | 是 |  |
| `canManage` | `boolean` | 否 |  |

### 4.5 生图（3 个）

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| `POST` | `/api/v1/generations/image` | 提交生图 | 🔒 |
| `GET` | `/api/v1/images` | 生图历史（分页） | 🔒 |
| `POST` | `/api/v1/images/{job_id}/retry` | 重试失败的生图 | 🔒 |

#### `POST /api/v1/generations/image`

**提交生图**

受理即返回 `queued`，实际生成由后台队列完成；客户端轮询 `GET /images` 或明细接口取 `status` / `outputUrl`。

**请求体** `GenerateImageIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `prompt` | `string` | 是 | 画面描述 |
| `size` | `string` | 否 | 宽x高，如 `1024x1536` |
| `count` | `number` | 否 | 张数 |
| `model` | `string` | 否 | 模型 id，留空取默认 |

**响应** `ApiResponse<ImageJobOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 生图记录 ID |
| `name` | `string` | 是 | 名称 |
| `prompt` | `string` | 是 | 画面描述 |
| `size` | `string` | 是 | 尺寸，如 `1024x1024` |
| `count` | `number` | 否 | 一次生成的张数 |
| `status` | `string` | 否 | 状态：`queued` / `running` / `succeeded` / `failed` |
| `progress` | `number` | 否 | 进度 0-100 |
| `model` | `string` | 否 | 模型可读名 |
| `modelId` | `string \| null` | 否 | 模型目录 id |
| `coverUrl` | `string` | 否 | 封面图地址（落库存对象 key，出参按当前存储驱动现签，可直接用于 <img>） |
| `outputUrl` | `string \| null` | 否 | 成图地址（成功后才有）：同样是按当前驱动现签的可访问地址，不要把它当「永久链接」持久化到别处 |
| `errorMessage` | `string \| null` | 否 | 失败原因 |
| `externalId` | `string \| null` | 否 | 供应商侧任务号；提交成功后有值，可用于对账 |
| `previousExternalId` | `string \| null` | 否 | 上一次提交的供应商任务号；带 confirmCharged 重试后保留的对账凭证 |
| `previousExternalIds` | `string[]` | 否 | 被替换掉的全部供应商任务号（旧的在前）。连续多次确认已扣费重试时，previousExternalId 只保留最近一个，全量凭证看这里 |
| `createdAt` | `string` | 是 | 创建时间（UTC） |

#### `GET /api/v1/images`

**生图历史（分页）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `page` | query | `number` | 否 | 页码，从 1 开始 |
| `pageSize` | query | `number` | 否 | 每页条数 |

**响应** `ApiResponse<Page<ImageJobOut>>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `ImageJobOut[]` | 是 | 当前页数据 |
| `total` | `number` | 是 | 总条数（用于分页器） |
| `page` | `number` | 是 | 当前页码，从 1 开始 |
| `pageSize` | `number` | 是 | 每页条数 |

#### `POST /api/v1/images/{job_id}/retry`

**重试失败的生图**

`SUBMIT_UNKNOWN_MESSAGE` 承诺的「手动重试」出口（R11-02 / 手册 §5.6②）。

重试会再向供应商提交一次，因此要 `image:create`；替别人点还要 `image:retry:any`。
没有这个出口时，`failed` 记录会被 `handle_submit` 的终态早退永久锁死：
文案说「请核对后手动重试」，而用户根本无处可点。

记录带 `externalId`（供应商任务号）时说明**上一次可能已经扣费**，默认返回
409 要求先核对；只有带 `confirmCharged=true` 才允许重新提交。不带参数的
旧调用行为不变（R12-01）。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `job_id` | path | `string` | 是 |  |
| `confirmCharged` | query | `boolean` | 否 | 确认上一次可能已扣费，仍要重新提交 |

**响应** `ApiResponse<ImageRetryOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 生图记录 ID |
| `status` | `string` | 是 | 重试后的状态，固定为 `queued` |

### 4.6 模板（16 个）

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| `GET` | `/api/v1/templates/replacement-catalog` | 人物/产品/场景替换模板与调用档案 | 🔒 |
| `GET` | `/api/v1/templates/replacement-packs` | 本人封装草稿和历史包 | 🔒 |
| `GET` | `/api/v1/templates/replacement-packs/available` | 全工作台已发布对象 | 🔒 |
| `PUT` | `/api/v1/templates/replacement-packs/draft` | 保存封装草稿（请求标识及版本防重） | 🔒 |
| `POST` | `/api/v1/templates/replacement-packs/{pack_id}/disable` | 停用已发布封装对象 | 🔒 |
| `GET` | `/api/v1/templates/replacement-packs/{pack_id}/preview/{asset_id}` | 读取授权发布版本的包内预览 | 🔒 |
| `POST` | `/api/v1/templates/replacement-packs/{pack_id}/publish` | 发布不可变封装版本 | 🔒 |
| `POST` | `/api/v1/templates/replacement-runs` | 归档输入并预览最终提示词，不提交生成 | 🔒 |
| `DELETE` | `/api/v1/templates/replacement-runs/{run_id}` | 将本人替换任务与调用档案移入回收站，不取消生成 | 🔒 |
| `GET` | `/api/v1/templates/replacement-runs/{run_id}` | 替换任务状态与提示词 | 🔒 |
| `POST` | `/api/v1/templates/replacement-runs/{run_id}/execute` | 提交替换任务；billingMode=credits 表示本任务用积分加速 | 🔒 |
| `GET` | `/api/v1/templates/replacement-runs/{run_id}/inputs/{asset_id}` | 读取当次归档输入快照 | 🔒 |
| `GET` | `/api/v1/templates/replacement-runs/{run_id}/output` | 预览或下载原始生成结果 | 🔒 |
| `POST` | `/api/v1/templates/replacement-runs/{run_id}/reconfirm` | 确认上次提交可能已计费后放行重提（默认拒绝） | 🔒 |
| `POST` | `/api/v1/templates/replacement-runs/{run_id}/restore` | 恢复本人回收站中的替换任务与调用档案 | 🔒 |
| `PUT` | `/api/v1/templates/replacement-runs/{run_id}/review` | 保存人工验收，版本冲突需重新核对 | 🔒 |

#### `GET /api/v1/templates/replacement-catalog`

**人物/产品/场景替换模板与调用档案**

**响应** `ApiResponse<ReplacementCatalog>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `provider` | `string` | 否 |  |
| `maxCredits` | `number` | 否 |  |
| `kinds` | `string[]` | 否 |  |
| `runs` | `ReplacementRunOut[]` | 是 |  |
| `packs` | `ReplacementPack[]` | 否 |  |
| `packRoles` | `Record<string, Record<string, string>>` | 否 |  |
| `baseRoles` | `Record<string, string>` | 否 |  |
| `packScopes` | `Record<string, string>` | 否 |  |

#### `GET /api/v1/templates/replacement-packs`

**本人封装草稿和历史包**

**响应** `ApiResponse<PackManagedList>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `PackManaged[]` | 是 |  |

#### `GET /api/v1/templates/replacement-packs/available`

**全工作台已发布对象**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `kind` | query | `string` | 否 |  |
| `keyword` | query | `string` | 否 |  |
| `page` | query | `number` | 否 |  |
| `pageSize` | query | `number` | 否 |  |

**响应** `ApiResponse<PackPublicList>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `ReplacementPack[]` | 是 |  |
| `total` | `number` | 是 |  |
| `page` | `number` | 是 |  |
| `pageSize` | `number` | 是 |  |

#### `PUT /api/v1/templates/replacement-packs/draft`

**保存封装草稿（请求标识及版本防重）**

**请求体** `PackDraftIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `requestId` | `string` | 是 |  |
| `expectedRevision` | `number` | 否 |  |
| `kind` | `"character" \| "product" \| "scene"` | 是 |  |
| `name` | `string` | 否 |  |
| `referenceIds` | `string[]` | 否 |  |
| `coverReferenceId` | `string \| null` | 否 |  |
| `wardrobeMode` | `"preserve" \| "reference"` | 否 |  |
| `features` | `string` | 否 |  |
| `instructions` | `string` | 否 |  |
| `bindings` | `PackBinding[]` | 否 |  |
| `requiredRoles` | `string[]` | 否 |  |
| `supportedScopes` | `string[]` | 否 |  |

**响应** `ApiResponse<PackManaged>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `kind` | `"character" \| "product" \| "scene"` | 是 |  |
| `name` | `string` | 是 |  |
| `referenceIds` | `string[]` | 是 |  |
| `coverReferenceId` | `string \| null` | 否 |  |
| `wardrobeMode` | `"preserve" \| "reference"` | 否 |  |
| `features` | `string` | 否 |  |
| `instructions` | `string` | 否 |  |
| `version` | `number` | 否 |  |
| `available` | `boolean \| null` | 否 |  |
| `coverUrl` | `string \| null` | 否 |  |
| `previewUrls` | `string[]` | 否 |  |
| `bindings` | `PackBinding[]` | 否 |  |
| `requiredRoles` | `string[]` | 否 |  |
| `supportedScopes` | `string[]` | 否 |  |
| `revision` | `number` | 否 |  |
| `status` | `"draft" \| "published" \| "disabled"` | 否 |  |
| `gaps` | `string[]` | 否 |  |

#### `POST /api/v1/templates/replacement-packs/{pack_id}/disable`

**停用已发布封装对象**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `pack_id` | path | `string` | 是 |  |

**请求体** `PackAction`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `expectedRevision` | `number` | 是 |  |

**响应** `ApiResponse<PackManaged>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `kind` | `"character" \| "product" \| "scene"` | 是 |  |
| `name` | `string` | 是 |  |
| `referenceIds` | `string[]` | 是 |  |
| `coverReferenceId` | `string \| null` | 否 |  |
| `wardrobeMode` | `"preserve" \| "reference"` | 否 |  |
| `features` | `string` | 否 |  |
| `instructions` | `string` | 否 |  |
| `version` | `number` | 否 |  |
| `available` | `boolean \| null` | 否 |  |
| `coverUrl` | `string \| null` | 否 |  |
| `previewUrls` | `string[]` | 否 |  |
| `bindings` | `PackBinding[]` | 否 |  |
| `requiredRoles` | `string[]` | 否 |  |
| `supportedScopes` | `string[]` | 否 |  |
| `revision` | `number` | 否 |  |
| `status` | `"draft" \| "published" \| "disabled"` | 否 |  |
| `gaps` | `string[]` | 否 |  |

#### `GET /api/v1/templates/replacement-packs/{pack_id}/preview/{asset_id}`

**读取授权发布版本的包内预览**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `pack_id` | path | `string` | 是 |  |
| `asset_id` | path | `string` | 是 |  |
| `version` | query | `number` | 是 |  |

**响应** `ApiResponse<unknown>`（前端取 `res.data`）

#### `POST /api/v1/templates/replacement-packs/{pack_id}/publish`

**发布不可变封装版本**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `pack_id` | path | `string` | 是 |  |

**请求体** `PackAction`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `expectedRevision` | `number` | 是 |  |

**响应** `ApiResponse<PackManaged>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `kind` | `"character" \| "product" \| "scene"` | 是 |  |
| `name` | `string` | 是 |  |
| `referenceIds` | `string[]` | 是 |  |
| `coverReferenceId` | `string \| null` | 否 |  |
| `wardrobeMode` | `"preserve" \| "reference"` | 否 |  |
| `features` | `string` | 否 |  |
| `instructions` | `string` | 否 |  |
| `version` | `number` | 否 |  |
| `available` | `boolean \| null` | 否 |  |
| `coverUrl` | `string \| null` | 否 |  |
| `previewUrls` | `string[]` | 否 |  |
| `bindings` | `PackBinding[]` | 否 |  |
| `requiredRoles` | `string[]` | 否 |  |
| `supportedScopes` | `string[]` | 否 |  |
| `revision` | `number` | 否 |  |
| `status` | `"draft" \| "published" \| "disabled"` | 否 |  |
| `gaps` | `string[]` | 否 |  |

#### `POST /api/v1/templates/replacement-runs`

**归档输入并预览最终提示词，不提交生成**

**请求体** `PrepareReplacement`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `requestId` | `string` | 是 |  |
| `kind` | `"character" \| "product" \| "scene"` | 是 |  |
| `target` | `string` | 否 |  |
| `sourceId` | `string` | 是 |  |
| `referenceIds` | `string[]` | 否 |  |
| `wardrobeMode` | `"preserve" \| "reference"` | 否 |  |
| `replacementScope` | `string` | 否 |  |
| `notes` | `string` | 否 |  |
| `packId` | `string \| null` | 否 |  |
| `packVersion` | `number \| null` | 否 |  |
| `aspectRatio` | `"9:16" \| "16:9" \| "1:1" \| "adaptive"` | 否 |  |

**响应** `ApiResponse<ReplacementRunOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `kind` | `"character" \| "product" \| "scene"` | 是 |  |
| `target` | `string` | 是 |  |
| `status` | `string` | 是 |  |
| `archivePath` | `string` | 是 |  |
| `createdAt` | `string` | 是 |  |
| `prompt` | `string` | 是 |  |
| `displayTitle` | `string` | 否 | 统一展示名，列表/弹窗/任务中心共用 |
| `taskId` | `string \| null` | 否 |  |
| `estimatedCredits` | `number \| null` | 否 |  |
| `credits` | `number \| null` | 否 |  |
| `progress` | `number` | 否 |  |
| `reviewStatus` | `string` | 否 |  |
| `review` | `ReplacementReview \| null` | 否 |  |
| `inputSummary` | `ReplacementInputSummary \| null` | 否 |  |
| `lastError` | `string \| null` | 否 |  |
| `outputUrl` | `string \| null` | 否 |  |
| `deletedAt` | `string \| null` | 否 |  |
| `billingMode` | `"free" \| "credits"` | 否 | free=0 积分权益队列；credits=本次任务用积分进正常队列 |

#### `DELETE /api/v1/templates/replacement-runs/{run_id}`

**将本人替换任务与调用档案移入回收站，不取消生成**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `run_id` | path | `string` | 是 |  |

**响应** `ApiResponse<ReplacementRunOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `kind` | `"character" \| "product" \| "scene"` | 是 |  |
| `target` | `string` | 是 |  |
| `status` | `string` | 是 |  |
| `archivePath` | `string` | 是 |  |
| `createdAt` | `string` | 是 |  |
| `prompt` | `string` | 是 |  |
| `displayTitle` | `string` | 否 | 统一展示名，列表/弹窗/任务中心共用 |
| `taskId` | `string \| null` | 否 |  |
| `estimatedCredits` | `number \| null` | 否 |  |
| `credits` | `number \| null` | 否 |  |
| `progress` | `number` | 否 |  |
| `reviewStatus` | `string` | 否 |  |
| `review` | `ReplacementReview \| null` | 否 |  |
| `inputSummary` | `ReplacementInputSummary \| null` | 否 |  |
| `lastError` | `string \| null` | 否 |  |
| `outputUrl` | `string \| null` | 否 |  |
| `deletedAt` | `string \| null` | 否 |  |
| `billingMode` | `"free" \| "credits"` | 否 | free=0 积分权益队列；credits=本次任务用积分进正常队列 |

#### `GET /api/v1/templates/replacement-runs/{run_id}`

**替换任务状态与提示词**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `run_id` | path | `string` | 是 |  |

**响应** `ApiResponse<ReplacementRunOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `kind` | `"character" \| "product" \| "scene"` | 是 |  |
| `target` | `string` | 是 |  |
| `status` | `string` | 是 |  |
| `archivePath` | `string` | 是 |  |
| `createdAt` | `string` | 是 |  |
| `prompt` | `string` | 是 |  |
| `displayTitle` | `string` | 否 | 统一展示名，列表/弹窗/任务中心共用 |
| `taskId` | `string \| null` | 否 |  |
| `estimatedCredits` | `number \| null` | 否 |  |
| `credits` | `number \| null` | 否 |  |
| `progress` | `number` | 否 |  |
| `reviewStatus` | `string` | 否 |  |
| `review` | `ReplacementReview \| null` | 否 |  |
| `inputSummary` | `ReplacementInputSummary \| null` | 否 |  |
| `lastError` | `string \| null` | 否 |  |
| `outputUrl` | `string \| null` | 否 |  |
| `deletedAt` | `string \| null` | 否 |  |
| `billingMode` | `"free" \| "credits"` | 否 | free=0 积分权益队列；credits=本次任务用积分进正常队列 |

#### `POST /api/v1/templates/replacement-runs/{run_id}/execute`

**提交替换任务；billingMode=credits 表示本任务用积分加速**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `run_id` | path | `string` | 是 |  |

**请求体**（内联结构）

**响应** `ApiResponse<ReplacementRunOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `kind` | `"character" \| "product" \| "scene"` | 是 |  |
| `target` | `string` | 是 |  |
| `status` | `string` | 是 |  |
| `archivePath` | `string` | 是 |  |
| `createdAt` | `string` | 是 |  |
| `prompt` | `string` | 是 |  |
| `displayTitle` | `string` | 否 | 统一展示名，列表/弹窗/任务中心共用 |
| `taskId` | `string \| null` | 否 |  |
| `estimatedCredits` | `number \| null` | 否 |  |
| `credits` | `number \| null` | 否 |  |
| `progress` | `number` | 否 |  |
| `reviewStatus` | `string` | 否 |  |
| `review` | `ReplacementReview \| null` | 否 |  |
| `inputSummary` | `ReplacementInputSummary \| null` | 否 |  |
| `lastError` | `string \| null` | 否 |  |
| `outputUrl` | `string \| null` | 否 |  |
| `deletedAt` | `string \| null` | 否 |  |
| `billingMode` | `"free" \| "credits"` | 否 | free=0 积分权益队列；credits=本次任务用积分进正常队列 |

#### `GET /api/v1/templates/replacement-runs/{run_id}/inputs/{asset_id}`

**读取当次归档输入快照**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `run_id` | path | `string` | 是 |  |
| `asset_id` | path | `string` | 是 |  |

**响应** `ApiResponse<unknown>`（前端取 `res.data`）

#### `GET /api/v1/templates/replacement-runs/{run_id}/output`

**预览或下载原始生成结果**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `run_id` | path | `string` | 是 |  |

**响应** `ApiResponse<unknown>`（前端取 `res.data`）

#### `POST /api/v1/templates/replacement-runs/{run_id}/reconfirm`

**确认上次提交可能已计费后放行重提（默认拒绝）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `run_id` | path | `string` | 是 |  |

**请求体** `ReconfirmReplacement`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `confirmCharged` | `boolean` | 否 | 确认上一次提交可能已产生费用，仍要重新提交（默认拒绝） |

**响应** `ApiResponse<ReplacementRunOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `kind` | `"character" \| "product" \| "scene"` | 是 |  |
| `target` | `string` | 是 |  |
| `status` | `string` | 是 |  |
| `archivePath` | `string` | 是 |  |
| `createdAt` | `string` | 是 |  |
| `prompt` | `string` | 是 |  |
| `displayTitle` | `string` | 否 | 统一展示名，列表/弹窗/任务中心共用 |
| `taskId` | `string \| null` | 否 |  |
| `estimatedCredits` | `number \| null` | 否 |  |
| `credits` | `number \| null` | 否 |  |
| `progress` | `number` | 否 |  |
| `reviewStatus` | `string` | 否 |  |
| `review` | `ReplacementReview \| null` | 否 |  |
| `inputSummary` | `ReplacementInputSummary \| null` | 否 |  |
| `lastError` | `string \| null` | 否 |  |
| `outputUrl` | `string \| null` | 否 |  |
| `deletedAt` | `string \| null` | 否 |  |
| `billingMode` | `"free" \| "credits"` | 否 | free=0 积分权益队列；credits=本次任务用积分进正常队列 |

#### `POST /api/v1/templates/replacement-runs/{run_id}/restore`

**恢复本人回收站中的替换任务与调用档案**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `run_id` | path | `string` | 是 |  |

**响应** `ApiResponse<ReplacementRunOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `kind` | `"character" \| "product" \| "scene"` | 是 |  |
| `target` | `string` | 是 |  |
| `status` | `string` | 是 |  |
| `archivePath` | `string` | 是 |  |
| `createdAt` | `string` | 是 |  |
| `prompt` | `string` | 是 |  |
| `displayTitle` | `string` | 否 | 统一展示名，列表/弹窗/任务中心共用 |
| `taskId` | `string \| null` | 否 |  |
| `estimatedCredits` | `number \| null` | 否 |  |
| `credits` | `number \| null` | 否 |  |
| `progress` | `number` | 否 |  |
| `reviewStatus` | `string` | 否 |  |
| `review` | `ReplacementReview \| null` | 否 |  |
| `inputSummary` | `ReplacementInputSummary \| null` | 否 |  |
| `lastError` | `string \| null` | 否 |  |
| `outputUrl` | `string \| null` | 否 |  |
| `deletedAt` | `string \| null` | 否 |  |
| `billingMode` | `"free" \| "credits"` | 否 | free=0 积分权益队列；credits=本次任务用积分进正常队列 |

#### `PUT /api/v1/templates/replacement-runs/{run_id}/review`

**保存人工验收，版本冲突需重新核对**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `run_id` | path | `string` | 是 |  |

**请求体** `ReplacementReviewIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `status` | `"accepted" \| "rejected"` | 是 |  |
| `expectedVersion` | `number` | 是 |  |
| `reasonCodes` | `("replacement" \| "preservation" \| "visual" \| "audio" \| "other")[]` | 否 |  |
| `comment` | `string` | 否 |  |

**响应** `ApiResponse<ReplacementRunOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `kind` | `"character" \| "product" \| "scene"` | 是 |  |
| `target` | `string` | 是 |  |
| `status` | `string` | 是 |  |
| `archivePath` | `string` | 是 |  |
| `createdAt` | `string` | 是 |  |
| `prompt` | `string` | 是 |  |
| `displayTitle` | `string` | 否 | 统一展示名，列表/弹窗/任务中心共用 |
| `taskId` | `string \| null` | 否 |  |
| `estimatedCredits` | `number \| null` | 否 |  |
| `credits` | `number \| null` | 否 |  |
| `progress` | `number` | 否 |  |
| `reviewStatus` | `string` | 否 |  |
| `review` | `ReplacementReview \| null` | 否 |  |
| `inputSummary` | `ReplacementInputSummary \| null` | 否 |  |
| `lastError` | `string \| null` | 否 |  |
| `outputUrl` | `string \| null` | 否 |  |
| `deletedAt` | `string \| null` | 否 |  |
| `billingMode` | `"free" \| "credits"` | 否 | free=0 积分权益队列；credits=本次任务用积分进正常队列 |

### 4.7 分镜模板（1 个）

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| `GET` | `/api/v1/storyboards` | 分镜模板列表 | 🔒 |

#### `GET /api/v1/storyboards`

**分镜模板列表**

**响应** `ApiResponse<StoryboardListOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `StoryboardOut[]` | 否 |  |
| `total` | `number` | 否 |  |

### 4.8 镜头库（1 个）

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| `GET` | `/api/v1/shots` | 镜头词条列表 | 🔒 |

#### `GET /api/v1/shots`

**镜头词条列表**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `keyword` | query | `string` | 否 |  |

**响应** `ApiResponse<ShotListOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `ShotPresetOut[]` | 否 |  |
| `total` | `number` | 否 |  |

### 4.9 系统（2 个）

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| `GET` | `/api/v1/health` | 存活探针 | 公开 |
| `GET` | `/api/v1/health/ready` | 就绪探针（检查依赖） | 公开 |

#### `GET /api/v1/health`

**存活探针**

**响应** `ApiResponse<HealthOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `status` | `string` | 否 |  |
| `app` | `string` | 是 |  |
| `env` | `string` | 是 |  |
| `version` | `string` | 否 |  |

#### `GET /api/v1/health/ready`

**就绪探针（检查依赖）**

`status=down` 时返回 **503**，不是 200。

编排系统只看状态码：返回 200 时，无论响应体里写的是 `degraded` 还是 `down`，
k8s 都会把这台实例继续留在 Service 后面——判定了却没人执行，等于没判。
这是探针和普通接口的关键区别：普通接口用响应体表达业务结果，探针必须用状态码。

`degraded` 仍然返回 200：outbox 积压、缓存不可用这些「能服务但在漏」的情况，
摘掉实例只会让剩下的实例扛更多，反而更容易雪崩。它该触发的是告警，不是摘除。

**响应** `ApiResponse<ReadyOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `status` | `string` | 是 |  |
| `database` | `string` | 是 |  |
| `queueSize` | `number` | 是 |  |
| `checks` | `RuntimeCheckOut[]` | 否 |  |

### 4.10 导出（6 个）

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| `GET` | `/api/v1/exports` | 我的导出记录 | 🔒 |
| `POST` | `/api/v1/exports` | 创建导出（异步生成，202） | 🔒 |
| `GET` | `/api/v1/exports/sources` | 可导出的数据源 | 🔒 |
| `GET` | `/api/v1/exports/{export_id}` | 导出详情 | 🔒 |
| `DELETE` | `/api/v1/exports/{export_id}` | 删除导出（连带产物） | 🔒 |
| `GET` | `/api/v1/exports/{export_id}/download` | 下载导出文件（本地驱动直出，远端驱动 307） | 🔒 |

#### `GET /api/v1/exports`

**我的导出记录**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `status` | query | `string | null` | 否 | 状态筛选：queued/running/succeeded/failed/… |
| `data_source` | query | `string | null` | 否 | 数据源筛选 |
| `page` | query | `number` | 否 | 页码，从 1 开始 |
| `pageSize` | query | `number` | 否 | 每页条数 |

**响应** `ApiResponse<Page<ExportOut>>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `ExportOut[]` | 是 | 当前页数据 |
| `total` | `number` | 是 | 总条数（用于分页器） |
| `page` | `number` | 是 | 当前页码，从 1 开始 |
| `pageSize` | `number` | 是 | 每页条数 |

#### `POST /api/v1/exports`

**创建导出（异步生成，202）**

**请求体** `CreateExportIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `dataSource` | `ExportSource` | 是 | 数据源，见 enums.ExportSource |
| `params` | `ExportParams` | 否 | 筛选条件；不填表示该源的全量 |

**响应** `ApiResponse<unknown>`（前端取 `res.data`）

#### `GET /api/v1/exports/sources`

**可导出的数据源**

**响应** `ApiResponse<ExportSourceOut[]>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `[].name` | `string` | 是 |  |
| `[].label` | `string` | 是 |  |
| `[].columns` | `string[]` | 是 | CSV 列，同时也是列顺序 |
| `[].permission` | `string \| null` | 否 | 导出该源额外需要的权限码；null 表示只需 export:read |

#### `GET /api/v1/exports/{export_id}`

**导出详情**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `export_id` | path | `string` | 是 |  |

**响应** `ApiResponse<ExportDetailOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `dataSource` | `string` | 是 |  |
| `params` | `Record<string, unknown>` | 否 |  |
| `status` | `string` | 是 |  |
| `rowCount` | `number` | 否 |  |
| `byteSize` | `number` | 否 |  |
| `error` | `string \| null` | 否 |  |
| `attempts` | `number` | 否 |  |
| `requestedBy` | `string` | 是 |  |
| `createdAt` | `string \| null` | 否 |  |
| `startedAt` | `string \| null` | 否 |  |
| `finishedAt` | `string \| null` | 否 |  |
| `expiresAt` | `string \| null` | 否 |  |
| `downloadable` | `boolean` | 否 |  |

#### `DELETE /api/v1/exports/{export_id}`

**删除导出（连带产物）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `export_id` | path | `string` | 是 |  |

**响应** `ApiResponse<unknown>`（前端取 `res.data`）

#### `GET /api/v1/exports/{export_id}/download`

**下载导出文件（本地驱动直出，远端驱动 307）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `export_id` | path | `string` | 是 |  |

**响应** `ApiResponse<unknown>`（前端取 `res.data`）

---

## 5. 数据模型

### `AdoptRequest`

采纳一个候选。

`category_id` 为空时落到个人库的「未分类收件箱」——采纳是一个高频、
低摩擦的动作，逼用户先选分类会让"待办"越积越多。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `categoryId` | `string` | 否 |  |
| `tagIds` | `string[]` | 否 |  |

### `ArchiveChange`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `expectedVersion` | `number` | 是 |  |
| `name` | `string \| null` | 否 |  |
| `shared` | `boolean \| null` | 否 |  |
| `deleted` | `boolean \| null` | 否 |  |

### `ArchiveList`

列表出参。

**没有 `root`**：那是本地归档根的绝对路径，远端驱动下根本不存在，
前端也早已不再展示（原来「归档位置」那一行在切 S3 后恒为空）。
要位置信息就用 `storageKey`，它才是与驱动无关的那一层。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `ArchivedAsset[]` | 是 |  |
| `total` | `number` | 是 |  |
| `usedBytes` | `number` | 是 |  |
| `quotaBytes` | `number` | 否 |  |
| `ownerUsedBytes` | `number` | 否 |  |
| `quota` | `AssetQuota` | 否 |  |
| `page` | `number` | 否 |  |
| `pageSize` | `number` | 否 |  |

### `ArchivedAsset`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `category` | `"source" \| "character" \| "product" \| "scene" \| "audio"` | 是 |  |
| `type` | `"image" \| "video" \| "audio"` | 是 |  |
| `mimeType` | `string` | 是 |  |
| `sizeBytes` | `number` | 是 |  |
| `path` | `string` | 是 |  |
| `sha256` | `string` | 是 |  |
| `createdAt` | `string` | 是 |  |
| `width` | `number` | 否 |  |
| `height` | `number` | 否 |  |
| `duration` | `number` | 否 |  |
| `hasAudio` | `boolean` | 否 |  |
| `url` | `string` | 是 |  |
| `previewUrl` | `string \| null` | 否 |  |
| `ownerId` | `string` | 否 |  |
| `shared` | `boolean` | 否 |  |
| `sharedAt` | `string \| null` | 否 |  |
| `deletedAt` | `string \| null` | 否 |  |
| `stateVersion` | `number` | 否 |  |
| `editable` | `boolean` | 否 |  |
| `downloadAllowed` | `boolean` | 否 |  |
| `origin` | `string` | 否 |  |
| `stage` | `string` | 否 |  |
| `recipeId` | `string` | 否 |  |
| `complianceStatus` | `string` | 否 |  |
| `adoptedAt` | `string \| null` | 否 |  |
| `categoryId` | `string` | 否 |  |

### `AspectRatio`（枚举）

取值：`9:16`、`16:9`、`1:1`

### `AssetCategoryList`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `library` | `string` | 是 |  |
| `items` | `AssetCategoryOut[]` | 是 |  |
| `inboxCount` | `number` | 是 |  |

### `AssetCategoryOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `kind` | `string` | 是 |  |
| `isInbox` | `boolean` | 否 |  |
| `sortOrder` | `number` | 否 |  |
| `count` | `number` | 否 |  |

### `AssetQuota`

账面与物理两组数字。

为什么不塞成一个大整数：这四个数回答的是**四个不同的问题**，合并任何两个
都会制造无法解释的现象——「删了 1.4G 空间没变」（回收站）、
「我什么都没传空间却少了」（草稿过期）、「复制一次额度翻倍」（没去重）。
分开显示，每条差异都有名字；合成一个，用户只能得出「这东西坏了」。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `personalUsed` | `number` | 否 |  |
| `personalQuota` | `number` | 否 |  |
| `teamUsed` | `number` | 否 |  |
| `teamQuota` | `number` | 否 |  |
| `draftUsed` | `number` | 否 |  |
| `trashUsed` | `number` | 否 |  |
| `physicalUsed` | `number` | 否 |  |
| `physicalQuota` | `number` | 否 |  |

### `AssetRefOut`

配方里的一张参考图。带名字与缩略图——详情浮层要能直接点开。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `assetId` | `string` | 是 |  |
| `role` | `string` | 否 |  |
| `sortOrder` | `number` | 否 |  |
| `name` | `string` | 否 |  |
| `previewUrl` | `string \| null` | 否 |  |

### `AssetTagList`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `assetId` | `string` | 是 |  |
| `items` | `TagOut[]` | 否 |  |

### `AssetTagSet`

整体替换一条素材的标签集合。

空列表是合法输入，含义是「清空标签」——不要用「不传该字段」表达清空，
那样和「没改」无法区分。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `tagIds` | `string[]` | 否 |  |

### `AssetUsage`

「谁在用它」的一条记录。

两种消费者分开标：`asset` 是别的配方引用了它（生成血缘），
`subject` 是某个主体把它当成员（业务装配）。合并成一种会让"这张图能不能删"
的答案少一半——两者各自都会阻止删除。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `kind` | `"asset" \| "subject"` | 是 |  |
| `id` | `string` | 是 |  |
| `name` | `string` | 否 |  |

### `AssetViewCreate`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `string` | 是 |  |
| `query` | `Record<string, unknown>` | 否 |  |
| `sort` | `string` | 否 |  |

### `AssetViewList`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `AssetViewOut[]` | 否 |  |

### `AssetViewOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `query` | `Record<string, unknown>` | 否 |  |
| `sort` | `string` | 否 |  |
| `createdAt` | `string` | 是 |  |

### `BatchAdoptRequest`

整组采纳。**全成或全不成**：一次事务，任一条不可采纳就整批拒绝。

半成功是这里最坏的形态——前端要记住哪几条成功、失败的重试会不会重复，
而用户看到的是"选了 8 张，成了 5 张"，且不知道差在哪。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `assetIds` | `string[]` | 是 |  |
| `categoryId` | `string` | 否 |  |
| `tagIds` | `string[]` | 否 |  |

### `BatchAdoptResult`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `ArchivedAsset[]` | 是 |  |
| `already` | `number` | 否 |  |

### `CandidateGroup`

一批同源候选（同一 `recipe_id`）。

`expires_at` 取组内**最早**的那个：一批里但凡有一张到期，这一组就该消失，
否则会出现"确认界面里还留着 3 张已经过期的"。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `recipeId` | `string` | 否 |  |
| `summary` | `string` | 否 |  |
| `promptSummary` | `string` | 否 |  |
| `createdAt` | `string \| null` | 否 |  |
| `expiresAt` | `string \| null` | 否 |  |
| `items` | `ArchivedAsset[]` | 否 |  |

### `CandidateList`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `groups` | `CandidateGroup[]` | 否 |  |
| `total` | `number` | 否 |  |

### `Capability`（枚举）

取值：`video`、`image`、`audio`

### `CreateExportIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `dataSource` | `ExportSource` | 是 | 数据源，见 enums.ExportSource |
| `params` | `ExportParams` | 否 | 筛选条件；不填表示该源的全量 |

### `CreateTaskIn`

创建任务入参。

`model` / `durationSec` 不设默认值：合法取值来自模型目录（`GET /models`），
由 service 按供应商声明的 `paramSchema` 校验，写死默认值会在换模型时变成错值。
两者都可以不传：不传模型取该能力的默认模型，不传时长取模型 schema 的默认档位。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `prompt` | `string` | 是 | 画面描述 |
| `title` | `string \| null` | 否 | 不传则截取 prompt |
| `model` | `string \| null` | 否 | 模型 id 或名称，缺省取默认模型 |
| `durationSec` | `number \| null` | 否 | 视频时长（秒），缺省取模型的默认档位 |
| `aspectRatio` | `AspectRatio` | 否 | 画幅比例，缺省 9:16 竖屏 |
| `referenceAssetIds` | `string[]` | 否 | 参考素材 ID 列表（最多 20 个） |
| `stylePreset` | `string \| null` | 否 | 风格预设标识，留空为不套风格 |

### `DeleteTasksIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `ids` | `string[]` | 是 | 要删除的任务 ID 列表 |

### `DeleteTasksOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `deleted` | `number` | 是 | 实际删除的条数 |
| `skipped` | `string[]` | 否 | 被跳过的任务 ID：不存在，或属于他人且没有 `task:read:all` |

### `DeriveIntent`

「照这个素材再生成一次」所需的全部输入。

只回填输入位、**不落库**：真正发起生成走的是生成任务自己的接口（它有额度、
模型选择、参数校验）。在这里顺手建一个任务会让"点了衍生"变成一次扣费。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `mode` | `string` | 是 |  |
| `assetId` | `string` | 是 |  |
| `prompt` | `string` | 否 |  |
| `referenceAssetIds` | `string[]` | 否 |  |
| `referenceRole` | `string` | 否 |  |
| `sourceRecipeId` | `string \| null` | 否 |  |

### `DeriveRequest`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `mode` | `"image_ref" \| "video_first_frame" \| "prompt_extract"` | 是 |  |

### `ExecuteReplacement`

提交或加速一条替换任务。

`credits` 只作用于这条任务：提交时关掉账号的 0 积分权益标签，走正常积分队列，
**不动账号级开关**，因此不会让同账号的其他人下一次提交莫名其妙开始花钱。
不传表示沿用档案里当前的计费模式——「继续查询 / 重试原任务」走的就是这条路，
它们不该顺带把一条已加速的任务改回免费队列。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `billingMode` | `"free" \| "credits" \| null` | 否 |  |

### `ExportDetailOut`

详情多一个 `downloadable`：让前端不必重复实现「能不能下」的判断。

这条规则有两处（`succeeded` 且未被回收），前端各写一遍必然和回收任务
对不上——用户会看到「按钮是亮的，点进去 410」。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `dataSource` | `string` | 是 |  |
| `params` | `Record<string, unknown>` | 否 |  |
| `status` | `string` | 是 |  |
| `rowCount` | `number` | 否 |  |
| `byteSize` | `number` | 否 |  |
| `error` | `string \| null` | 否 |  |
| `attempts` | `number` | 否 |  |
| `requestedBy` | `string` | 是 |  |
| `createdAt` | `string \| null` | 否 |  |
| `startedAt` | `string \| null` | 否 |  |
| `finishedAt` | `string \| null` | 否 |  |
| `expiresAt` | `string \| null` | 否 |  |
| `downloadable` | `boolean` | 否 |  |

### `ExportOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `dataSource` | `string` | 是 |  |
| `params` | `Record<string, unknown>` | 否 |  |
| `status` | `string` | 是 |  |
| `rowCount` | `number` | 否 |  |
| `byteSize` | `number` | 否 |  |
| `error` | `string \| null` | 否 |  |
| `attempts` | `number` | 否 |  |
| `requestedBy` | `string` | 是 |  |
| `createdAt` | `string \| null` | 否 |  |
| `startedAt` | `string \| null` | 否 |  |
| `finishedAt` | `string \| null` | 否 |  |
| `expiresAt` | `string \| null` | 否 |  |

### `ExportParams`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `status` | `string \| null` | 否 | 仅 tasks：按任务状态过滤（如 succeeded / failed） |
| `modelId` | `string \| null` | 否 | 仅 tasks：按模型目录 id 过滤 |
| `action` | `string \| null` | 否 | 仅 audit_events：按动作前缀过滤（如 task.） |
| `since` | `string \| null` | 否 | 起始时间（含），ISO 8601 |
| `until` | `string \| null` | 否 | 结束时间（不含），ISO 8601 |

### `ExportSource`（枚举）

取值：`tasks`、`audit_events`

### `ExportSourceOut`

可导出的数据源。前端据此渲染下拉，不要硬编码数据源名单。

这份列表来自运行时注册表（`core.ports.export_sources` + 内置源），
新模块注册一个源之后这里自动多一项——前端不需要跟着发版。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `string` | 是 |  |
| `label` | `string` | 是 |  |
| `columns` | `string[]` | 是 | CSV 列，同时也是列顺序 |
| `permission` | `string \| null` | 否 | 导出该源额外需要的权限码；null 表示只需 export:read |

### `GenerateImageIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `prompt` | `string` | 是 | 画面描述 |
| `size` | `string` | 否 | 宽x高，如 `1024x1536` |
| `count` | `number` | 否 | 张数 |
| `model` | `string` | 否 | 模型 id，留空取默认 |

### `HTTPValidationError`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `detail` | `ValidationError[]` | 否 |  |

### `HealthOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `status` | `string` | 否 |  |
| `app` | `string` | 是 |  |
| `env` | `string` | 是 |  |
| `version` | `string` | 否 |  |

### `ImageJobOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 生图记录 ID |
| `name` | `string` | 是 | 名称 |
| `prompt` | `string` | 是 | 画面描述 |
| `size` | `string` | 是 | 尺寸，如 `1024x1024` |
| `count` | `number` | 否 | 一次生成的张数 |
| `status` | `string` | 否 | 状态：`queued` / `running` / `succeeded` / `failed` |
| `progress` | `number` | 否 | 进度 0-100 |
| `model` | `string` | 否 | 模型可读名 |
| `modelId` | `string \| null` | 否 | 模型目录 id |
| `coverUrl` | `string` | 否 | 封面图地址（落库存对象 key，出参按当前存储驱动现签，可直接用于 <img>） |
| `outputUrl` | `string \| null` | 否 | 成图地址（成功后才有）：同样是按当前驱动现签的可访问地址，不要把它当「永久链接」持久化到别处 |
| `errorMessage` | `string \| null` | 否 | 失败原因 |
| `externalId` | `string \| null` | 否 | 供应商侧任务号；提交成功后有值，可用于对账 |
| `previousExternalId` | `string \| null` | 否 | 上一次提交的供应商任务号；带 confirmCharged 重试后保留的对账凭证 |
| `previousExternalIds` | `string[]` | 否 | 被替换掉的全部供应商任务号（旧的在前）。连续多次确认已扣费重试时，previousExternalId 只保留最近一个，全量凭证看这里 |
| `createdAt` | `string` | 是 | 创建时间（UTC） |

### `ImageRetryOut`

重试只回 id / status。

不回整份 `ImageJobOut`：持有 `image:retry:any` 的人可以帮别人重跑，
但没有 `image:read:all`，不该顺带读到别人的 prompt（与 task 的重试同一口径）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 生图记录 ID |
| `status` | `string` | 是 | 重试后的状态，固定为 `queued` |

### `ImportAsset`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `relativePath` | `string` | 是 |  |
| `category` | `"source" \| "character" \| "product" \| "scene" \| "audio"` | 是 |  |

### `LineageNode`

血缘链上的一个节点：一条配方 + 它产出的那张素材。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `recipe` | `RecipeOut` | 是 |  |
| `assetId` | `string` | 否 |  |
| `name` | `string` | 否 |  |
| `previewUrl` | `string \| null` | 否 |  |

### `LineageOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `assetId` | `string` | 是 |  |
| `recipe` | `RecipeOut \| null` | 否 |  |
| `references` | `AssetRefOut[]` | 否 |  |
| `parents` | `LineageNode[]` | 否 |  |
| `children` | `LineageNode[]` | 否 |  |
| `usedBy` | `AssetUsage[]` | 否 |  |

### `LoginEventListOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `LoginEventOut[]` | 否 | 按时间倒序 |

### `LoginEventOut`

一次登录尝试。审计用，只读。

字段名 `at` 与库里的 `created_at` 不同：契约里这一列叫 `at`（前端需求文档的写法），
转换放在 service 里显式做一次，比给 `created_at` 起两个别名好读。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `at` | `string` | 是 | 发生时间（UTC） |
| `success` | `boolean` | 是 | 是否成功 |
| `ip` | `string` | 否 | 来源 IP |
| `userAgent` | `string` | 否 | 客户端 UA |
| `reason` | `string \| null` | 否 | 失败原因：`bad_password` / `disabled` / `rate_limited`；成功为 null |

### `LoginIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `username` | `string` | 是 | 用户名 |
| `password` | `string` | 是 | 密码 |

### `MeOut`

`GET /identity/me`：前端靠 `permissions` 渲染菜单与按钮。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 用户 ID |
| `username` | `string` | 是 | 登录名，入库前已转小写 |
| `displayName` | `string` | 否 | 昵称（顶栏展示用） |
| `email` | `string \| null` | 否 | 邮箱，未填为 null |
| `avatarUrl` | `string \| null` | 否 | 头像 URL，未设置为 null |
| `isActive` | `boolean` | 否 | 是否启用；停用后无法登录且令牌立即失效 |
| `lastLoginAt` | `string \| null` | 否 | 最后登录时间，从未登录为 null |
| `createdAt` | `string` | 是 | 创建时间（UTC） |
| `roles` | `RoleBriefOut[]` | 否 | 拥有的角色 |
| `permissions` | `string[]` | 否 | 展开后的权限码；含 `*` 表示超级管理员 |

### `ModelListOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `ModelOut[]` | 否 |  |
| `total` | `number` | 否 |  |

### `ModelOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `label` | `string` | 是 |  |
| `capability` | `Capability` | 是 |  |
| `provider` | `string` | 是 |  |
| `description` | `string` | 否 |  |
| `paramSchema` | `Record<string, unknown>` | 否 |  |
| `isDefault` | `boolean` | 否 |  |

### `PackAction`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `expectedRevision` | `number` | 是 |  |

### `PackBinding`

一张参考图在封装里的用途；description 只补充本次任务特有的事实。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `role` | `string` | 否 |  |
| `description` | `string` | 否 |  |

### `PackDraftIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `requestId` | `string` | 是 |  |
| `expectedRevision` | `number` | 否 |  |
| `kind` | `"character" \| "product" \| "scene"` | 是 |  |
| `name` | `string` | 否 |  |
| `referenceIds` | `string[]` | 否 |  |
| `coverReferenceId` | `string \| null` | 否 |  |
| `wardrobeMode` | `"preserve" \| "reference"` | 否 |  |
| `features` | `string` | 否 |  |
| `instructions` | `string` | 否 |  |
| `bindings` | `PackBinding[]` | 否 |  |
| `requiredRoles` | `string[]` | 否 |  |
| `supportedScopes` | `string[]` | 否 |  |

### `PackManaged`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `kind` | `"character" \| "product" \| "scene"` | 是 |  |
| `name` | `string` | 是 |  |
| `referenceIds` | `string[]` | 是 |  |
| `coverReferenceId` | `string \| null` | 否 |  |
| `wardrobeMode` | `"preserve" \| "reference"` | 否 |  |
| `features` | `string` | 否 |  |
| `instructions` | `string` | 否 |  |
| `version` | `number` | 否 |  |
| `available` | `boolean \| null` | 否 |  |
| `coverUrl` | `string \| null` | 否 |  |
| `previewUrls` | `string[]` | 否 |  |
| `bindings` | `PackBinding[]` | 否 |  |
| `requiredRoles` | `string[]` | 否 |  |
| `supportedScopes` | `string[]` | 否 |  |
| `revision` | `number` | 否 |  |
| `status` | `"draft" \| "published" \| "disabled"` | 否 |  |
| `gaps` | `string[]` | 否 |  |

### `PackManagedList`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `PackManaged[]` | 是 |  |

### `PackPublicList`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `ReplacementPack[]` | 是 |  |
| `total` | `number` | 是 |  |
| `page` | `number` | 是 |  |
| `pageSize` | `number` | 是 |  |

### `PasswordChangeIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `oldPassword` | `string` | 是 | 当前密码 |
| `newPassword` | `string` | 是 | 新密码；至少 10 位，不能是纯数字，且不能与当前密码相同 |

### `PasswordPolicyOut`

口令策略下发。**文案也由后端给**：前端四处表单不该各拼一份。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `minLength` | `number` | 是 | 最短长度 |
| `maxLength` | `number` | 是 | 最长长度 |
| `forbidAllNumeric` | `boolean` | 是 | 是否禁止纯数字 |
| `forbidSameAsUsername` | `boolean` | 是 | 是否禁止与用户名相同；当前未启用，前端按 false 处理 |
| `message` | `string` | 是 | 可直接展示的一句话，前端不要自己拼 |

### `PermissionCatalogOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `groups` | `PermissionGroupOut[]` | 否 | 按分组组织的全部权限点，角色编辑界面直接渲染 |
| `total` | `number` | 否 | 权限点总数 |

### `PermissionGroupOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `group` | `string` | 是 | 分组名，如「任务」「身份」 |
| `items` | `PermissionOut[]` | 否 | 该组下的权限点 |

### `PermissionOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `code` | `string` | 是 | 权限码，如 `task:create` |
| `name` | `string` | 是 | 权限名称 |
| `description` | `string` | 否 | 权限说明 |

### `PrepareReplacement`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `requestId` | `string` | 是 |  |
| `kind` | `"character" \| "product" \| "scene"` | 是 |  |
| `target` | `string` | 否 |  |
| `sourceId` | `string` | 是 |  |
| `referenceIds` | `string[]` | 否 |  |
| `wardrobeMode` | `"preserve" \| "reference"` | 否 |  |
| `replacementScope` | `string` | 否 |  |
| `notes` | `string` | 否 |  |
| `packId` | `string \| null` | 否 |  |
| `packVersion` | `number \| null` | 否 |  |
| `aspectRatio` | `"9:16" \| "16:9" \| "1:1" \| "adaptive"` | 否 |  |

### `ProfileUpdateIn`

`PATCH /identity/me` 的自助可写字段。

刻意**不含** `is_active`：停用是管理动作，用户误点一下就把自己锁在门外，
而恢复只有管理员一个入口。自助路径能改的只有「展示信息」。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `displayName` | `string \| null` | 否 | 昵称 |
| `email` | `string \| null` | 否 | 邮箱；传 null 清空 |
| `avatarUrl` | `string \| null` | 否 | 头像 URL；传 null 清空 |

### `ReadyOut`

就绪状态。`checks` 说明每一项依赖的结论，status ∈ ok / degraded / down。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `status` | `string` | 是 |  |
| `database` | `string` | 是 |  |
| `queueSize` | `number` | 是 |  |
| `checks` | `RuntimeCheckOut[]` | 否 |  |

### `RecipeOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `model` | `string` | 否 |  |
| `modelVersion` | `string` | 否 |  |
| `provider` | `string` | 否 |  |
| `prompt` | `string` | 否 |  |
| `negativePrompt` | `string` | 否 |  |
| `promptLanguage` | `string` | 否 |  |
| `params` | `Record<string, unknown>` | 否 |  |
| `seed` | `string` | 否 |  |
| `seedRequested` | `string` | 否 |  |
| `taskId` | `string` | 否 |  |
| `templateId` | `string` | 否 |  |
| `templateVersion` | `number` | 否 |  |
| `subjectVersion` | `number` | 否 |  |
| `contentLabel` | `Record<string, unknown>` | 否 |  |
| `parentRecipeId` | `string \| null` | 否 |  |
| `referenceAssetIds` | `string[]` | 否 |  |
| `costUnits` | `number` | 否 |  |
| `createdBy` | `string` | 否 |  |
| `createdAt` | `string \| null` | 否 |  |

### `ReconfirmReplacement`

确认「上一次提交可能已经花了钱」后才允许重提。

默认 False：一次误点就变成第二次计费，是这套流程里最贵的一个操作。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `confirmCharged` | `boolean` | 否 | 确认上一次提交可能已产生费用，仍要重新提交（默认拒绝） |

### `RefreshIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `refreshToken` | `string` | 是 | 登录时拿到的刷新令牌 |

### `ReplacementCatalog`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `provider` | `string` | 否 |  |
| `maxCredits` | `number` | 否 |  |
| `kinds` | `string[]` | 否 |  |
| `runs` | `ReplacementRunOut[]` | 是 |  |
| `packs` | `ReplacementPack[]` | 否 |  |
| `packRoles` | `Record<string, Record<string, string>>` | 否 |  |
| `baseRoles` | `Record<string, string>` | 否 |  |
| `packScopes` | `Record<string, string>` | 否 |  |

### `ReplacementInputAsset`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `role` | `string` | 是 |  |
| `type` | `string` | 是 |  |
| `url` | `string` | 是 |  |

### `ReplacementInputSummary`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `assets` | `ReplacementInputAsset[]` | 否 |  |
| `coverReferenceId` | `string \| null` | 否 |  |
| `wardrobeMode` | `string` | 否 |  |
| `replacementScope` | `string` | 否 |  |
| `notes` | `string` | 否 |  |

### `ReplacementPack`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `kind` | `"character" \| "product" \| "scene"` | 是 |  |
| `name` | `string` | 是 |  |
| `referenceIds` | `string[]` | 是 |  |
| `coverReferenceId` | `string \| null` | 否 |  |
| `wardrobeMode` | `"preserve" \| "reference"` | 否 |  |
| `features` | `string` | 否 |  |
| `instructions` | `string` | 否 |  |
| `version` | `number` | 否 |  |
| `available` | `boolean \| null` | 否 |  |
| `coverUrl` | `string \| null` | 否 |  |
| `previewUrls` | `string[]` | 否 |  |
| `bindings` | `PackBinding[]` | 否 |  |
| `requiredRoles` | `string[]` | 否 |  |
| `supportedScopes` | `string[]` | 否 |  |

### `ReplacementReview`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `status` | `string` | 否 |  |
| `version` | `number` | 否 |  |
| `reasonCodes` | `("replacement" \| "preservation" \| "visual" \| "audio" \| "other")[]` | 否 |  |
| `comment` | `string` | 否 |  |
| `reviewedBy` | `string \| null` | 否 |  |
| `reviewedAt` | `string \| null` | 否 |  |
| `history` | `ReplacementReviewEvent[]` | 否 |  |

### `ReplacementReviewEvent`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `status` | `string` | 否 |  |
| `version` | `number` | 否 |  |
| `reasonCodes` | `("replacement" \| "preservation" \| "visual" \| "audio" \| "other")[]` | 否 |  |
| `comment` | `string` | 否 |  |
| `reviewedBy` | `string \| null` | 否 |  |
| `reviewedAt` | `string \| null` | 否 |  |

### `ReplacementReviewIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `status` | `"accepted" \| "rejected"` | 是 |  |
| `expectedVersion` | `number` | 是 |  |
| `reasonCodes` | `("replacement" \| "preservation" \| "visual" \| "audio" \| "other")[]` | 否 |  |
| `comment` | `string` | 否 |  |

### `ReplacementRunOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `kind` | `"character" \| "product" \| "scene"` | 是 |  |
| `target` | `string` | 是 |  |
| `status` | `string` | 是 |  |
| `archivePath` | `string` | 是 |  |
| `createdAt` | `string` | 是 |  |
| `prompt` | `string` | 是 |  |
| `displayTitle` | `string` | 否 | 统一展示名，列表/弹窗/任务中心共用 |
| `taskId` | `string \| null` | 否 |  |
| `estimatedCredits` | `number \| null` | 否 |  |
| `credits` | `number \| null` | 否 |  |
| `progress` | `number` | 否 |  |
| `reviewStatus` | `string` | 否 |  |
| `review` | `ReplacementReview \| null` | 否 |  |
| `inputSummary` | `ReplacementInputSummary \| null` | 否 |  |
| `lastError` | `string \| null` | 否 |  |
| `outputUrl` | `string \| null` | 否 |  |
| `deletedAt` | `string \| null` | 否 |  |
| `billingMode` | `"free" \| "credits"` | 否 | free=0 积分权益队列；credits=本次任务用积分进正常队列 |

### `ResetPasswordIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `newPassword` | `string` | 是 | 管理员设置的新密码；至少 10 位，不能是纯数字 |

### `RevokedSessionsOut`

吊销会话的结果。两个「踢下线」类接口都返回这个形状。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `revoked` | `number` | 是 | 本次吊销的会话数；已经失效的会话不计入 |

### `RoleBriefOut`

用户出参里内嵌的角色摘要。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 角色 ID |
| `code` | `string` | 是 | 角色标识，如 `editor` |
| `name` | `string` | 是 | 角色名称 |
| `isSuperuser` | `boolean` | 否 | 是否超级管理员角色（权限展开为 `*`） |

### `RoleCreateIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `code` | `string` | 是 | 角色标识，唯一且不可改 |
| `name` | `string` | 是 | 角色名称 |
| `description` | `string` | 否 | 角色说明 |
| `permissions` | `string[]` | 否 | 权限码列表 |
| `isSuperuser` | `boolean` | 否 | 仅超级管理员可设置 |

### `RoleOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 角色 ID |
| `code` | `string` | 是 | 角色标识，唯一；内置角色不可改 |
| `name` | `string` | 是 | 角色名称 |
| `description` | `string` | 否 | 角色说明 |
| `isSuperuser` | `boolean` | 否 | 持有即展开为 `*`，拥有全部权限 |
| `isBuiltin` | `boolean` | 否 | 内置角色：不可删除 |
| `sortOrder` | `number` | 否 | 排序值，小的在前 |
| `createdAt` | `string` | 是 | 创建时间（UTC） |
| `permissions` | `string[]` | 否 | 角色拥有的权限码 |
| `userCount` | `number` | 否 | 使用该角色的用户数 |
| `orphanedPermissions` | `string[]` | 否 | 已失效的权限码（权限点被删或改名后残留） |

### `RolePermissionsIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `permissions` | `string[]` | 否 | 整组替换；出现未登记的权限码会被拒绝 |

### `RoleUpdateIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `string \| null` | 否 | 角色名称 |
| `description` | `string \| null` | 否 | 角色说明 |
| `isSuperuser` | `boolean \| null` | 否 | 仅超级管理员可设置 |

### `RuntimeCheckOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `string` | 是 |  |
| `ok` | `boolean` | 是 |  |
| `detail` | `string` | 是 |  |
| `required` | `boolean` | 否 |  |

### `SessionOut`

一条登录会话。前端「我的登录设备」列表用它。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 会话 ID，踢下线时用它 |
| `userAgent` | `string` | 否 | 登录时的 UA，未记录为空串 |
| `ip` | `string` | 否 | 登录时的来源 IP |
| `lastSeenAt` | `string \| null` | 否 | 最近一次续期时间，从未续期为 null |
| `expiresAt` | `string` | 是 | 刷新令牌到期时间（UTC） |
| `createdAt` | `string` | 是 | 登录时间（UTC） |
| `current` | `boolean` | 否 | 是否是发起本次请求的会话；前端据此标「本机」 |

### `ShotListOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `ShotPresetOut[]` | 否 |  |
| `total` | `number` | 否 |  |

### `ShotPresetOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `promptFragment` | `string` | 是 |  |
| `durationSec` | `number` | 是 |  |
| `coverUrl` | `string` | 是 |  |
| `popularity` | `number` | 是 |  |

### `StoryboardListOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `StoryboardOut[]` | 否 |  |
| `total` | `number` | 否 |  |

### `StoryboardOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `description` | `string` | 是 |  |
| `shotCount` | `number` | 是 |  |
| `durationSec` | `number` | 是 |  |
| `aspectRatio` | `AspectRatio` | 是 |  |
| `coverUrl` | `string` | 是 |  |
| `createdAt` | `string` | 是 |  |

### `SubjectDetail`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `subjectType` | `string` | 是 |  |
| `status` | `string` | 是 |  |
| `visibility` | `string` | 是 |  |
| `teamId` | `string` | 否 |  |
| `categoryId` | `string` | 否 |  |
| `coverAssetId` | `string` | 否 |  |
| `coverUrl` | `string` | 否 |  |
| `memberCount` | `number` | 否 |  |
| `currentVersion` | `number` | 否 |  |
| `availability` | `string` | 否 |  |
| `availabilityNote` | `string` | 否 |  |
| `members` | `SubjectMemberOut[]` | 否 |  |
| `attributes` | `Record<string, unknown>` | 否 |  |
| `requires` | `string[]` | 否 |  |
| `requiredRoles` | `string[]` | 否 |  |
| `missingCapabilities` | `string[]` | 否 |  |
| `missingRoles` | `string[]` | 否 |  |
| `memberGaps` | `Record<string, string[]>` | 否 |  |

### `SubjectList`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `SubjectOut[]` | 是 |  |
| `total` | `number` | 否 |  |
| `page` | `number` | 否 |  |
| `pageSize` | `number` | 否 |  |

### `SubjectMemberOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `assetId` | `string` | 是 |  |
| `name` | `string` | 否 |  |
| `coverUrl` | `string` | 否 |  |
| `role` | `string` | 否 |  |
| `sortOrder` | `number` | 否 |  |
| `grants` | `string[]` | 否 |  |
| `inOutput` | `boolean` | 否 |  |
| `missing` | `boolean` | 否 |  |

### `SubjectOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `subjectType` | `string` | 是 |  |
| `status` | `string` | 是 |  |
| `visibility` | `string` | 是 |  |
| `teamId` | `string` | 否 |  |
| `categoryId` | `string` | 否 |  |
| `coverAssetId` | `string` | 否 |  |
| `coverUrl` | `string` | 否 |  |
| `memberCount` | `number` | 否 |  |
| `currentVersion` | `number` | 否 |  |
| `availability` | `string` | 否 |  |
| `availabilityNote` | `string` | 否 |  |

### `SubjectPublishResult`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `subjectId` | `string` | 是 |  |
| `version` | `number` | 是 |  |
| `publishedAt` | `string` | 是 |  |

### `TagCatalog`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `groups` | `TagGroup[]` | 否 |  |
| `total` | `number` | 否 |  |
| `pinnedThreshold` | `number` | 否 |  |

### `TagCreate`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `dimension` | `string` | 是 |  |
| `name` | `string` | 是 |  |

### `TagGroup`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `dimension` | `string` | 是 |  |
| `label` | `string` | 是 |  |
| `items` | `TagOut[]` | 否 |  |

### `TagOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `dimension` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `status` | `string` | 否 |  |
| `source` | `string` | 否 |  |
| `useCount` | `number` | 否 |  |
| `pinned` | `boolean` | 否 |  |

### `TaskFeedItem`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `key` | `string` | 是 | 带来源前缀的唯一键，不是上游任务 ID |
| `id` | `string` | 是 | 原始任务或替换档案 ID |
| `source` | `"video" \| "replacement"` | 是 | 任务来源 |
| `title` | `string` | 是 | 展示标题 |
| `status` | `string` | 是 | 原始状态，保留替换链路的精确状态 |
| `group` | `"all" \| "queued" \| "running" \| "completed" \| "failed" \| "attention"` | 是 | 统一筛选分组；running 包含排队 |
| `createdAt` | `string` | 是 | 创建时间 |
| `progress` | `number` | 否 | 生成进度 |
| `sourceName` | `string` | 否 | 替换原片名称 |
| `outputUrl` | `string \| null` | 否 | 替换成片预览和下载地址 |
| `errorMessage` | `string \| null` | 否 | 失败或需处理原因 |
| `detailUrl` | `string \| null` | 否 | 替换档案深链接 |
| `video` | `TaskOut \| null` | 否 | 通用任务数据；替换任务为 null |

### `TaskFeedOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `TaskFeedItem[]` | 否 |  |
| `total` | `number` | 否 |  |
| `page` | `number` | 否 |  |
| `pageSize` | `number` | 否 |  |
| `stats` | `TaskFeedStats` | 否 | 筛选前分组统计 |
| `canReadAll` | `boolean` | 否 | 可否查看他人的通用任务 |
| `warnings` | `string[]` | 否 | 部分来源不可用时的提示 |

### `TaskFeedStats`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `all` | `number` | 否 | 总数 |
| `running` | `number` | 否 | 正在执行，包含排队 |
| `completed` | `number` | 否 | 生成完成，不代表人工验收通过 |
| `failed` | `number` | 否 | 执行失败 |
| `attention` | `number` | 否 | 等待核对、免费额度、登录或人工处理 |

### `TaskListOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `TaskOut[]` | 否 |  |
| `total` | `number` | 否 |  |
| `page` | `number` | 否 |  |
| `pageSize` | `number` | 否 |  |
| `stats` | `TaskStats` | 否 | 侧边栏角标用的分组计数 |
| `canReadAll` | `boolean` | 否 | 当前账号能否查看他人任务；false 时前端应收起「全部」选项 |

### `TaskOut`

任务出参：字段名与前端 `VideoTask` 完全一致。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 任务 ID |
| `title` | `string` | 是 | 标题 |
| `status` | `TaskStatus` | 是 | 状态：queued 排队 / running 生成中 / completed 完成 / failed 失败 |
| `model` | `string` | 是 | 模型展示名，如 `Seedance 2.5` |
| `modelId` | `string \| null` | 否 | 模型目录 id，与 `GET /api/v1/models` 的 `id` 对齐 |
| `durationSec` | `number` | 是 | 视频时长（秒） |
| `aspectRatio` | `AspectRatio` | 是 | 画幅比例 |
| `progress` | `number` | 否 | 生成进度 0-100 |
| `coverUrl` | `string` | 否 | 封面图 URL，未生成完时为空串 |
| `prompt` | `string` | 否 | 画面描述 |
| `stylePreset` | `string \| null` | 否 | 风格预设标识，未设置时为 null |
| `referenceAssetIds` | `string[]` | 否 | 参考素材 ID 列表（图生视频用） |
| `authorId` | `string` | 是 | 作者用户 ID |
| `authorName` | `string` | 否 | 作者昵称快照（改名不回填历史） |
| `favorited` | `boolean` | 否 | 当前账号是否已收藏 |
| `downloadable` | `boolean` | 否 | 是否允许下载：已成功且有产物 |
| `errorMessage` | `string \| null` | 否 | 失败原因，成功时为 null |
| `createdAt` | `string` | 是 | 创建时间（UTC） |
| `updatedAt` | `string` | 是 | 更新时间（UTC） |
| `startedAt` | `string \| null` | 否 | 开始生成时间，未开始为 null |

### `TaskRetryOut`

重试的结果：只描述「这一次动作」。

刻意**不**返回整份 `TaskOut`（R6-05）：`task:retry:any` 与 `task:read:all`
是两个独立开关，客服只需要「帮别人重跑一次」，不该顺带获得
「按 id 读任意失败任务的 prompt 与作者」这条读通道。
重试是一个动作，不是一次查询——前端拿到结果后刷新列表即可。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 任务 ID |
| `status` | `TaskStatus` | 是 | 重试后的状态，正常为 queued |
| `updatedAt` | `string` | 是 | 状态变更时间（UTC），即重新排队的时间 |

### `TaskScope`（枚举）

取值：`mine`、`team`、`all`

### `TaskSort`（枚举）

取值：`createdAt_desc`、`createdAt_asc`、`title_asc`

### `TaskStats`

各状态任务数：不受状态筛选影响，但受搜索/范围影响。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `all` | `number` | 否 | 总数 |
| `queued` | `number` | 否 | 排队中 |
| `running` | `number` | 否 | 生成中 |
| `completed` | `number` | 否 | 已完成 |
| `failed` | `number` | 否 | 失败 |

### `TaskStatus`（枚举）

取值：`queued`、`running`、`completed`、`failed`

### `TaskStatusFilter`（枚举）

取值：`all`、`queued`、`running`、`completed`、`failed`

### `TeamList`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `TeamOut[]` | 是 |  |
| `canManage` | `boolean` | 否 |  |

### `TeamOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `kind` | `string` | 是 |  |
| `role` | `string` | 否 |  |
| `memberCount` | `number` | 否 |  |

### `TokenOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `accessToken` | `string` | 是 | 访问令牌，放进 `Authorization: Bearer <token>` |
| `tokenType` | `string` | 否 | 令牌类型，固定 Bearer |
| `expiresIn` | `number` | 否 | 有效期（秒），过期后请求返回 401 |
| `refreshToken` | `string` | 否 | 刷新令牌，只用来换新的 accessToken，不能访问业务接口 |
| `refreshExpiresIn` | `number` | 否 | 刷新令牌有效期（秒）；续期时滑动延长 |
| `user` | `MeOut` | 是 | 登录用户，可省一次 `/me` 请求 |

### `UpdateTaskIn`

局部更新（目前只开放展示属性，生成参数不可改，需重试）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | `string \| null` | 否 | 新标题 |
| `favorited` | `boolean \| null` | 否 | 收藏状态 |

### `UserCreateIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `username` | `string` | 是 | 登录名，3-64 位字母数字或 `. _ -`，会被转成小写 |
| `password` | `string` | 是 | 初始密码，管理员指定；至少 10 位，不能是纯数字 |
| `displayName` | `string` | 否 | 昵称，留空则与用户名相同 |
| `email` | `string \| null` | 否 | 邮箱 |
| `avatarUrl` | `string \| null` | 否 | 头像 URL |
| `roleIds` | `string[]` | 否 | 角色 id 列表，可留空 |

### `UserOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 用户 ID |
| `username` | `string` | 是 | 登录名，入库前已转小写 |
| `displayName` | `string` | 否 | 昵称（顶栏展示用） |
| `email` | `string \| null` | 否 | 邮箱，未填为 null |
| `avatarUrl` | `string \| null` | 否 | 头像 URL，未设置为 null |
| `isActive` | `boolean` | 否 | 是否启用；停用后无法登录且令牌立即失效 |
| `lastLoginAt` | `string \| null` | 否 | 最后登录时间，从未登录为 null |
| `createdAt` | `string` | 是 | 创建时间（UTC） |
| `roles` | `RoleBriefOut[]` | 否 | 拥有的角色 |

### `UserRolesIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `roleIds` | `string[]` | 否 | 整组替换，传空数组即清空 |

### `UserUpdateIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `displayName` | `string \| null` | 否 | 昵称 |
| `email` | `string \| null` | 否 | 邮箱；传 null 清空 |
| `avatarUrl` | `string \| null` | 否 | 头像 URL；传 null 清空 |
| `isActive` | `boolean \| null` | 否 | 启用/停用；不能停用自己，且必须保留一个启用的超管。不接受 null：停用请传 false，不想改就不要传这个字段 |

### `ValidationError`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `loc` | `(string \| number)[]` | 是 |  |
| `msg` | `string` | 是 |  |
| `type` | `string` | 是 |  |
| `input` | `unknown` | 否 |  |
| `ctx` | `Record<string, unknown>` | 否 |  |
