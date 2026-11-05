# 天火 AI 短视频工作台 API — 接口文档

> 此文件由 `app export-openapi` 自动生成，请勿手工编辑。
>
> 版本 `0.2.0`｜共 **37** 个接口 / **9** 个模块
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
| `image:create` | 提交生图任务 |  |
| `image:read` | 查看生图历史 |  |
| `shot:read` | 查看镜头库 |  |
| `storyboard:read` | 查看分镜模板 |  |
| `task:create` | 创建生成任务 |  |
| `task:delete` | 删除任务 |  |
| `task:read` | 查看任务 |  |
| `task:read:all` | 查看并管理他人任务 | 列表 scope=all / 详情 / 删除的跨用户判定依据；不含重试 |
| `task:retry:any` | 重试他人任务 | 会消耗额度：重试他人任务前额外校验此权限 |
| `task:update` | 编辑/重试自己的任务 |  |
| `template:read` | 查看行业模板 |  |

**素材**

| 权限码 | 名称 | 说明 |
|---|---|---|
| `asset:create` | 上传素材 |  |
| `asset:delete` | 删除素材 |  |
| `asset:read` | 查看素材 |  |

**组织管理**

| 权限码 | 名称 | 说明 |
|---|---|---|
| `identity:create` | 创建用户 |  |
| `identity:delete` | 停用用户 |  |
| `identity:read` | 查看用户与角色 |  |
| `identity:role:manage` | 管理角色与权限点 | 新建/删除角色、调整角色权限、给用户分配角色 |
| `identity:update` | 编辑用户（含停用、重置密码） | 不含分配角色；重置密码只能重置「权限不高于自己」的账号，超管仍需超管 |

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
| `40100` | 401 | 框架 | `Unauthorized` | 请先登录 |
| `40121` | 401 | identity | `CredentialInvalid` | 用户名或密码错误 |
| `40300` | 403 | 框架 | `Forbidden` | 没有操作权限 |
| `40321` | 403 | identity | `AccountDisabled` | 账号已停用，请联系管理员 |
| `40322` | 403 | identity | `SuperuserRequired` | 该操作需要超级管理员权限 |
| `40323` | 403 | identity | `PermissionEscalation` | 不能授予自己不具备的权限 |
| `40400` | 404 | 框架 | `NotFound` | 资源不存在 |
| `40421` | 404 | identity | `UserNotFound` | 用户不存在 |
| `40422` | 404 | identity | `RoleNotFound` | 角色不存在 |
| `40900` | 409 | 框架 | `Conflict` | 资源冲突 |
| `40902` | 409 | 框架 | `InvalidState` | 当前状态不允许该操作 |
| `40921` | 409 | identity | `UsernameTaken` | 用户名已被占用 |
| `40922` | 409 | identity | `RoleCodeTaken` | 角色标识已存在 |
| `40923` | 409 | identity | `BuiltinRoleLocked` | 内置角色不可删除，标识也不可修改 |
| `40924` | 409 | identity | `RoleInUse` | 该角色仍被用户使用，请先解除绑定 |
| `40925` | 409 | identity | `CannotTargetSelf` | 不能停用或删除自己的账号 |
| `40926` | 409 | identity | `LastSuperuser` | 系统必须保留至少一个启用的超级管理员 |
| `42900` | 429 | 框架 | `RateLimited` | 请求过于频繁，请稍后再试 |
| `50100` | 501 | 框架 | `NotImplementedFeature` | 该功能尚未实现 |
| `50200` | 502 | 框架 | `UpstreamError` | 上游服务异常 |

---

## 4. 接口清单

| 模块 | 接口数 | 说明 |
|---|---|---|
| 身份 | 17 | 见下方 `4.1` |
| 任务 | 8 | 见下方 `4.2` |
| 模型 | 1 | 见下方 `4.3` |
| 素材 | 4 | 见下方 `4.4` |
| 生图 | 2 | 见下方 `4.5` |
| 模板 | 1 | 见下方 `4.6` |
| 分镜模板 | 1 | 见下方 `4.7` |
| 镜头库 | 1 | 见下方 `4.8` |
| 系统 | 2 | 见下方 `4.9` |

### 4.1 身份（17 个）

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| `POST` | `/api/v1/identity/login` | 登录换取令牌 | 公开 |
| `GET` | `/api/v1/identity/me` | 当前用户（含角色与权限码） | 🔒 |
| `PATCH` | `/api/v1/identity/me` | 修改自己的资料 | 🔒 |
| `POST` | `/api/v1/identity/me/password` | 修改自己的密码 | 🔒 |
| `GET` | `/api/v1/identity/permissions` | 全部权限点（按分组，供角色编辑界面） | 🔒 |
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
| `POST` | `/api/v1/identity/users/{user_id}/reset-password` | 管理员重置密码 | 🔒 |
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
| `user` | `MeOut` | 是 | 登录用户，可省一次 `/me` 请求 |

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

#### `POST /api/v1/identity/me/password`

**修改自己的密码**

**请求体** `PasswordChangeIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `oldPassword` | `string` | 是 | 当前密码 |
| `newPassword` | `string` | 是 | 新密码；至少 10 位，不能是纯数字，且不能与当前密码相同 |

**响应** `ApiResponse<null>`（前端取 `res.data`）

#### `GET /api/v1/identity/permissions`

**全部权限点（按分组，供角色编辑界面）**

**响应** `ApiResponse<PermissionCatalogOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `groups` | `PermissionGroupOut[]` | 否 | 按分组组织的全部权限点，角色编辑界面直接渲染 |
| `total` | `number` | 否 | 权限点总数 |

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
| `isActive` | `boolean \| null` | 否 | 启用/停用；不能停用自己，且必须保留一个启用的超管 |

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

### 4.2 任务（8 个）

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| `GET` | `/api/v1/tasks` | 任务列表 | 🔒 |
| `POST` | `/api/v1/tasks` | 创建任务 | 🔒 |
| `DELETE` | `/api/v1/tasks` | 批量删除任务 | 🔒 |
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

### 4.4 素材（4 个）

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| `GET` | `/api/v1/assets` | 素材列表（按类型分面计数） | 🔒 |
| `POST` | `/api/v1/assets/upload-ticket` | 申请上传凭证（待实现） | 🔒 |
| `DELETE` | `/api/v1/assets/{asset_id}` | 删除素材（待实现） | 🔒 |
| `GET` | `/api/v1/assets/{asset_id}` | 素材详情（待实现） | 🔒 |

#### `GET /api/v1/assets`

**素材列表（按类型分面计数）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `type` | query | `AssetTypeFilter` | 否 | 类型筛选 |
| `keyword` | query | `string` | 否 |  |

**响应** `ApiResponse<AssetListOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `AssetOut[]` | 否 |  |
| `total` | `number` | 否 |  |
| `counts` | `AssetCounts` | 否 |  |

#### `POST /api/v1/assets/upload-ticket`

**申请上传凭证（待实现）**

**请求体** `UploadAssetIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `filename` | `string` | 是 |  |
| `contentType` | `string` | 否 |  |
| `sizeBytes` | `number` | 否 |  |

**响应** `ApiResponse<UploadTicketOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `assetId` | `string` | 是 |  |
| `uploadUrl` | `string` | 是 |  |
| `key` | `string` | 是 |  |
| `expiresIn` | `number` | 否 |  |

#### `DELETE /api/v1/assets/{asset_id}`

**删除素材（待实现）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**响应** `ApiResponse<unknown>`（前端取 `res.data`）

#### `GET /api/v1/assets/{asset_id}`

**素材详情（待实现）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**响应** `ApiResponse<null>`（前端取 `res.data`）

### 4.5 生图（2 个）

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| `POST` | `/api/v1/generations/image` | 提交生图（待实现） | 🔒 |
| `GET` | `/api/v1/images` | 生图历史 | 🔒 |

#### `POST /api/v1/generations/image`

**提交生图（待实现）**

**请求体** `GenerateImageIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `prompt` | `string` | 是 |  |
| `size` | `string` | 否 | 宽x高 |
| `count` | `number` | 否 |  |

**响应** `ApiResponse<ImageJobOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `prompt` | `string` | 是 |  |
| `size` | `string` | 是 |  |
| `coverUrl` | `string` | 是 |  |
| `createdAt` | `string` | 是 |  |

#### `GET /api/v1/images`

**生图历史**

**响应** `ApiResponse<ImageJobListOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `ImageJobOut[]` | 否 |  |
| `total` | `number` | 否 |  |

### 4.6 模板（1 个）

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| `GET` | `/api/v1/templates` | 模板列表 | 🔒 |

#### `GET /api/v1/templates`

**模板列表**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `industry` | query | `string | null` | 否 |  |

**响应** `ApiResponse<TemplateListOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `TemplateOut[]` | 否 |  |
| `total` | `number` | 否 |  |

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

**响应** `ApiResponse<ReadyOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `status` | `string` | 是 |  |
| `database` | `string` | 是 |  |
| `queueSize` | `number` | 是 |  |

---

## 5. 数据模型

### `AspectRatio`（枚举）

取值：`9:16`、`16:9`、`1:1`

### `AssetCounts`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `all` | `number` | 否 |  |
| `image` | `number` | 否 |  |
| `video` | `number` | 否 |  |
| `audio` | `number` | 否 |  |

### `AssetListOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `AssetOut[]` | 否 |  |
| `total` | `number` | 否 |  |
| `counts` | `AssetCounts` | 否 |  |

### `AssetOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `type` | `AssetType` | 是 |  |
| `mimeType` | `string` | 是 |  |
| `sizeBytes` | `number` | 是 |  |
| `coverUrl` | `string` | 是 |  |
| `width` | `number` | 是 |  |
| `height` | `number` | 是 |  |
| `refCount` | `number` | 是 |  |
| `createdAt` | `string` | 是 |  |

### `AssetType`（枚举）

取值：`image`、`video`、`audio`

### `AssetTypeFilter`（枚举）

取值：`all`、`image`、`video`、`audio`

### `Capability`（枚举）

取值：`video`、`image`、`audio`

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

### `GenerateImageIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `prompt` | `string` | 是 |  |
| `size` | `string` | 否 | 宽x高 |
| `count` | `number` | 否 |  |

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

### `ImageJobListOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `ImageJobOut[]` | 否 |  |
| `total` | `number` | 否 |  |

### `ImageJobOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `prompt` | `string` | 是 |  |
| `size` | `string` | 是 |  |
| `coverUrl` | `string` | 是 |  |
| `createdAt` | `string` | 是 |  |

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

### `PasswordChangeIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `oldPassword` | `string` | 是 | 当前密码 |
| `newPassword` | `string` | 是 | 新密码；至少 10 位，不能是纯数字，且不能与当前密码相同 |

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

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `status` | `string` | 是 |  |
| `database` | `string` | 是 |  |
| `queueSize` | `number` | 是 |  |

### `ResetPasswordIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `newPassword` | `string` | 是 | 管理员设置的新密码；至少 10 位，不能是纯数字 |

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

### `TemplateListOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `TemplateOut[]` | 否 |  |
| `total` | `number` | 否 |  |

### `TemplateOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `description` | `string` | 是 |  |
| `industry` | `string` | 是 |  |
| `shotCount` | `number` | 是 |  |
| `usageCount` | `number` | 是 |  |
| `coverUrl` | `string` | 是 |  |
| `tags` | `string[]` | 否 |  |
| `createdAt` | `string` | 是 |  |

### `TokenOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `accessToken` | `string` | 是 | 访问令牌，放进 `Authorization: Bearer <token>` |
| `tokenType` | `string` | 否 | 令牌类型，固定 Bearer |
| `expiresIn` | `number` | 否 | 有效期（秒），过期后请求返回 401 |
| `user` | `MeOut` | 是 | 登录用户，可省一次 `/me` 请求 |

### `UpdateTaskIn`

局部更新（目前只开放展示属性，生成参数不可改，需重试）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | `string \| null` | 否 | 新标题 |
| `favorited` | `boolean \| null` | 否 | 收藏状态 |

### `UploadAssetIn`

预签名上传：前端拿到 uploadUrl 后直传对象存储，再回调确认。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `filename` | `string` | 是 |  |
| `contentType` | `string` | 否 |  |
| `sizeBytes` | `number` | 否 |  |

### `UploadTicketOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `assetId` | `string` | 是 |  |
| `uploadUrl` | `string` | 是 |  |
| `key` | `string` | 是 |  |
| `expiresIn` | `number` | 否 |  |

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
| `isActive` | `boolean \| null` | 否 | 启用/停用；不能停用自己，且必须保留一个启用的超管 |

### `ValidationError`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `loc` | `(string \| number)[]` | 是 |  |
| `msg` | `string` | 是 |  |
| `type` | `string` | 是 |  |
| `input` | `unknown` | 否 |  |
| `ctx` | `Record<string, unknown>` | 否 |  |
