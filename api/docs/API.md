# 雨范 AI 短视频工作台 API — 接口文档

> 此文件由 `app export-openapi` 自动生成，请勿手工编辑。
>
> 版本 `0.2.0`｜共 **150** 个接口 / **12** 个模块
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
| `model_driver:cancel` | 取消生成任务 |  |
| `model_driver:create` | 提交统一生成任务并重试失败记录 |  |
| `model_driver:read` | 查看模型驱动、模型目录与生成任务 |  |
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
| `template_pack:create` | 创建或保存模板封装草稿 |  |
| `template_pack:publish` | 发布或停用模板封装 |  |
| `template_pack:read` | 查看模板封装 |  |

**系统**

| 权限码 | 名称 | 说明 |
|---|---|---|
| `export:read:audit` | 导出操作留痕 |  |

**素材**

| 权限码 | 名称 | 说明 |
|---|---|---|
| `template:pack:manage` | 维护本人封装草稿 |  |
| `template:pack:publish` | 发布或停用本人封装对象 |  |

**组织管理**

| 权限码 | 名称 | 说明 |
|---|---|---|
| `identity:create` | 创建用户 |  |
| `identity:delete` | 停用用户 |  |
| `identity:group:create` | 新建小组 |  |
| `identity:group:delete` | 删除小组 |  |
| `identity:group:member:manage` | 分配小组成员 | 整组替换成员；一人可属多组 |
| `identity:group:permission:manage` | 设置小组整体权限 | 该组所有成员自动获得；个人权限只增不减 |
| `identity:group:read` | 查看小组与成员 |  |
| `identity:group:update` | 编辑小组资料 |  |
| `identity:read` | 查看用户与角色 |  |
| `identity:role:manage` | 管理角色与权限点 | 新建/删除角色、调整角色权限、给用户分配角色 |
| `identity:team:manage` | 设置团队基础权限 | 团队 = 所有启用账号；禁止把 identity:* 或 * 放进这一层 |
| `identity:update` | 编辑用户（含停用、重置密码） | 不含分配角色。改他人昵称/头像：目标不能是超管；重置密码、改他人邮箱、启停账号：目标权限不得高于自己（超管仍需超管） |
| `identity:user:permission:manage` | 设置个人权限 | 单独授予，与团队/小组/角色取并集；不能授出自己没有的权限 |

**资产库**

| 权限码 | 名称 | 说明 |
|---|---|---|
| `library:create` | 上传与封装资产 |  |
| `library:delete` | 删除本人资产（移入回收站） |  |
| `library:delete:any` | 删除 / 恢复任何人的资产 |  |
| `library:draft:review` | 审核他人草稿 |  |
| `library:generation:ingest` | 生成结果入库（草稿箱） |  |
| `library:read` | 查看资产库 |  |
| `library:share` | 发起共享与撤销共享申请 |  |
| `library:share:approve` | 全站审批共享与撤销申请（兼容码，等同同时勾选下面两项） |  |
| `library:share:approve:group` | 审批所在小组的共享与撤销申请 |  |
| `library:share:approve:team` | 审批团队级共享与撤销申请 |  |
| `library:share:direct` | 共享无需审批（撤销仍需审批） |  |
| `library:tag:manage` | 管理标签词表（改名 / 删除） |  |
| `library:update` | 修改本人资产的名称与标签 |  |

> 判定规则：`*` 恒真；`task:*` 这类通配可覆盖 `task:read`；否则按字符串精确匹配。

---

## 3. 业务错误码总表

规则：`code = HTTP状态 × 100 + 模块段 × 10 + 序号`，所以
`40926` = HTTP 409 + `identity` 段 + 第 6 个错误。
**前端只需判断 `code !== 0`**，具体文案用 `message`（可直接展示）。

| code | HTTP | 来源 | 异常类 | 默认文案 |
|---|---|---|---|---|
| `40000` | 400 | 框架 | `BadRequest` | 请求参数有误 |
| `40001` | 400 | 框架 | `ModelUnavailable` | 模型已下线 |
| `40021` | 400 | identity | `OldPasswordMismatch` | 原密码不正确 |
| `40022` | 400 | identity | `UnknownPermission` | 存在无法识别的权限点 |
| `40023` | 400 | identity | `TeamGrantForbidden` | 团队基础权限不能包含管理类权限或通配符 |
| `40081` | 400 | library | `RevokeReasonRequired` | 撤销共享必须填写理由 |
| `40082` | 400 | library | `LibraryFileTypeNotAllowed` | 支持 MP4、PNG、JPG、WebP、MP3、WAV、TXT 和 MD |
| `40083` | 400 | library | `PackItemInvalid` | 封装里有不可用的成员 |
| `40084` | 400 | library | `PackEmpty` | 封装至少需要一个成员 |
| `40085` | 400 | library | `GenerationSourceInvalid` | 生成结果必须提供可读取的 storageKey 或 sourceUrl |
| `40086` | 400 | library | `DownloadEmpty` | 选中的条目都不在可下载范围内 |
| `40087` | 400 | library | `PackHasNoOriginal` | 封装没有单独的原文件，请使用「下载」打包其中素材 |
| `40088` | 400 | library | `AssetNotEditable` | 回收站里的素材不能修改，请先恢复 |
| `40089` | 400 | library | `LibraryTextInvalid` | 文本资产只支持 UTF-8 纯文本（.txt / .md），且不能为空 |
| `40100` | 401 | 框架 | `Unauthorized` | 请先登录 |
| `40121` | 401 | identity | `CredentialInvalid` | 用户名或密码错误 |
| `40122` | 401 | identity | `RefreshTokenInvalid` | 刷新令牌已失效，请重新登录 |
| `40300` | 403 | 框架 | `Forbidden` | 没有操作权限 |
| `40321` | 403 | identity | `AccountDisabled` | 账号已停用，请联系管理员 |
| `40322` | 403 | identity | `SuperuserRequired` | 该操作需要超级管理员权限 |
| `40323` | 403 | identity | `PermissionEscalation` | 不能授予自己不具备的权限 |
| `40381` | 403 | library | `DraftReviewForbidden` | 没有审核他人草稿的权限 |
| `40382` | 403 | library | `ShareNotOwned` | 撤销共享只能由资产所有者发起 |
| `40383` | 403 | library | `PackMemberShareBlocked` | 封装内的素材不能单独调整共享范围 |
| `40400` | 404 | 框架 | `NotFound` | 资源不存在 |
| `40421` | 404 | identity | `UserNotFound` | 用户不存在 |
| `40422` | 404 | identity | `RoleNotFound` | 角色不存在 |
| `40423` | 404 | identity | `SessionNotFound` | 会话不存在或已失效 |
| `40424` | 404 | identity | `GroupNotFound` | 小组不存在 |
| `40900` | 409 | 框架 | `Conflict` | 资源冲突 |
| `40902` | 409 | 框架 | `InvalidState` | 当前状态不允许该操作 |
| `40921` | 409 | identity | `UsernameTaken` | 用户名已被占用 |
| `40922` | 409 | identity | `RoleCodeTaken` | 角色标识已存在 |
| `40923` | 409 | identity | `BuiltinRoleLocked` | 内置角色不可删除，标识也不可修改 |
| `40924` | 409 | identity | `RoleInUse` | 该角色仍被用户使用，请先解除绑定 |
| `40925` | 409 | identity | `CannotTargetSelf` | 不能停用或删除自己的账号 |
| `40926` | 409 | identity | `LastSuperuser` | 系统必须保留至少一个启用的超级管理员 |
| `40927` | 409 | identity | `GroupNameTaken` | 小组名称已被占用 |
| `40971` | 409 | template | `TemplateBusy` | 模板正在处理中，请稍后再试 |
| `40981` | 409 | library | `SharedAssetNotDeletable` | 该资产仍处于共享状态，请先撤销共享后再删除 |
| `40982` | 409 | library | `TeamDeleteForbidden` | 团队库资产不能直接删除，请先撤销团队共享 |
| `40983` | 409 | library | `ShareRevokePending` | 撤销共享正在审批中，通过后再删除 |
| `40984` | 409 | library | `DraftNotReviewable` | 该草稿已被处理，请刷新后重试 |
| `40985` | 409 | library | `AssetNotInTrash` | 该资产不在回收站 |
| `40986` | 409 | library | `TrashEntryReferenced` | 资产仍被引用，不能彻底删除 |
| `40987` | 409 | library | `ShareTargetInvalid` | 共享目标与该资产的当前状态不符 |
| `40988` | 409 | library | `RequestNotPending` | 该申请已被处理，请刷新列表 |
| `40989` | 409 | library | `DownloadNotReady` | 打包任务尚未完成或已失效 |
| `40991` | 409 | export | `ExportBusy` | 同一份数据正在导出中，请等待它完成 |
| `40992` | 409 | export | `ExportNotReady` | 导出尚未生成完成，请稍后再试 |
| `41091` | 410 | export | `ExportGone` | 导出文件已过保留期被回收，请重新导出 |
| `41381` | 413 | library | `LibraryFileTooLarge` | 文件不能超过 300MB |
| `41382` | 413 | library | `DownloadTooLarge` | 单次打包超出上限 |
| `41383` | 413 | library | `LibraryTextTooLarge` | 文本资产不能超过 1MB |
| `42900` | 429 | 框架 | `RateLimited` | 请求过于频繁，请稍后再试 |
| `50100` | 501 | 框架 | `NotImplementedFeature` | 该功能尚未实现 |
| `50200` | 502 | 框架 | `UpstreamError` | 上游服务异常 |
| `50300` | 503 | 框架 | `Unavailable` | 服务暂不可用，请稍后再试 |

---

## 4. 接口清单

| 模块 | 接口数 | 说明 |
|---|---|---|
| 身份 | 39 | 见下方 `4.1` |
| 任务 | 9 | 见下方 `4.2` |
| 模型 | 1 | 见下方 `4.3` |
| 生图 | 3 | 见下方 `4.4` |
| 模板 | 11 | 见下方 `4.5` |
| 分镜模板 | 1 | 见下方 `4.6` |
| 镜头库 | 1 | 见下方 `4.7` |
| 系统 | 2 | 见下方 `4.8` |
| 导出 | 6 | 见下方 `4.9` |
| 资产库 | 45 | 见下方 `4.10` |
| 模型驱动 | 6 | 见下方 `4.11` |
| 模板封装 | 26 | 见下方 `4.12` |

### 4.1 身份（39 个）

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| `GET` | `/api/v1/identity/groups` | 小组列表（含成员与权限） | 🔒 |
| `POST` | `/api/v1/identity/groups` | 新建小组 | 🔒 |
| `GET` | `/api/v1/identity/groups/{group_id}` | 小组详情 | 🔒 |
| `PATCH` | `/api/v1/identity/groups/{group_id}` | 编辑小组资料 | 🔒 |
| `DELETE` | `/api/v1/identity/groups/{group_id}` | 删除小组（同时清成员与授权） | 🔒 |
| `GET` | `/api/v1/identity/groups/{group_id}/members` | 小组成员列表 | 🔒 |
| `PUT` | `/api/v1/identity/groups/{group_id}/members` | 设置小组成员（整组替换） | 🔒 |
| `PUT` | `/api/v1/identity/groups/{group_id}/permissions` | 设置小组整体权限（整组替换） | 🔒 |
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
| `GET` | `/api/v1/identity/team/permissions` | 团队基础权限（全体启用账号） | 🔒 |
| `PUT` | `/api/v1/identity/team/permissions` | 设置团队基础权限（整组替换） | 🔒 |
| `GET` | `/api/v1/identity/users` | 用户列表 | 🔒 |
| `POST` | `/api/v1/identity/users` | 创建用户 | 🔒 |
| `GET` | `/api/v1/identity/users/{user_id}` | 用户详情 | 🔒 |
| `PATCH` | `/api/v1/identity/users/{user_id}` | 编辑用户（含停用） | 🔒 |
| `DELETE` | `/api/v1/identity/users/{user_id}` | 停用用户（保留历史归属） | 🔒 |
| `GET` | `/api/v1/identity/users/{user_id}/effective-permissions` | 用户有效权限与四层来源 | 🔒 |
| `GET` | `/api/v1/identity/users/{user_id}/logins` | 某账号的登录记录 | 🔒 |
| `GET` | `/api/v1/identity/users/{user_id}/permissions` | 用户单独授予的权限 | 🔒 |
| `PUT` | `/api/v1/identity/users/{user_id}/permissions` | 设置个人权限（整组替换） | 🔒 |
| `POST` | `/api/v1/identity/users/{user_id}/reset-password` | 管理员重置密码 | 🔒 |
| `POST` | `/api/v1/identity/users/{user_id}/revoke-sessions` | 踢下线（吊销该用户全部会话） | 🔒 |
| `PUT` | `/api/v1/identity/users/{user_id}/roles` | 设置用户角色（整组替换） | 🔒 |

#### `GET /api/v1/identity/groups`

**小组列表（含成员与权限）**

**响应** `ApiResponse<GroupOut[]>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `[].id` | `string` | 是 | 小组 ID |
| `[].name` | `string` | 是 | 小组名称，唯一 |
| `[].description` | `string` | 否 | 小组说明 |
| `[].createdAt` | `string` | 是 | 创建时间（UTC） |
| `[].permissions` | `string[]` | 否 | 小组整体权限；成员自动获得 |
| `[].memberIds` | `string[]` | 否 | 成员用户 id；编辑成员时整组替换 |
| `[].memberCount` | `number` | 否 | 成员数 |

#### `POST /api/v1/identity/groups`

**新建小组**

**请求体** `GroupCreateIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `string` | 是 | 小组名称，唯一 |
| `description` | `string` | 否 | 小组说明 |

**响应** `ApiResponse<GroupOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 小组 ID |
| `name` | `string` | 是 | 小组名称，唯一 |
| `description` | `string` | 否 | 小组说明 |
| `createdAt` | `string` | 是 | 创建时间（UTC） |
| `permissions` | `string[]` | 否 | 小组整体权限；成员自动获得 |
| `memberIds` | `string[]` | 否 | 成员用户 id；编辑成员时整组替换 |
| `memberCount` | `number` | 否 | 成员数 |

#### `GET /api/v1/identity/groups/{group_id}`

**小组详情**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `group_id` | path | `string` | 是 |  |

**响应** `ApiResponse<GroupOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 小组 ID |
| `name` | `string` | 是 | 小组名称，唯一 |
| `description` | `string` | 否 | 小组说明 |
| `createdAt` | `string` | 是 | 创建时间（UTC） |
| `permissions` | `string[]` | 否 | 小组整体权限；成员自动获得 |
| `memberIds` | `string[]` | 否 | 成员用户 id；编辑成员时整组替换 |
| `memberCount` | `number` | 否 | 成员数 |

#### `PATCH /api/v1/identity/groups/{group_id}`

**编辑小组资料**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `group_id` | path | `string` | 是 |  |

**请求体** `GroupUpdateIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `string \| null` | 否 | 小组名称 |
| `description` | `string \| null` | 否 | 小组说明 |

**响应** `ApiResponse<GroupOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 小组 ID |
| `name` | `string` | 是 | 小组名称，唯一 |
| `description` | `string` | 否 | 小组说明 |
| `createdAt` | `string` | 是 | 创建时间（UTC） |
| `permissions` | `string[]` | 否 | 小组整体权限；成员自动获得 |
| `memberIds` | `string[]` | 否 | 成员用户 id；编辑成员时整组替换 |
| `memberCount` | `number` | 否 | 成员数 |

#### `DELETE /api/v1/identity/groups/{group_id}`

**删除小组（同时清成员与授权）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `group_id` | path | `string` | 是 |  |

**响应** `ApiResponse<null>`（前端取 `res.data`）

#### `GET /api/v1/identity/groups/{group_id}/members`

**小组成员列表**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `group_id` | path | `string` | 是 |  |

**响应** `ApiResponse<UserOut[]>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `[].id` | `string` | 是 | 用户 ID |
| `[].username` | `string` | 是 | 登录名，入库前已转小写 |
| `[].displayName` | `string` | 否 | 昵称（顶栏展示用） |
| `[].email` | `string \| null` | 否 | 邮箱，未填为 null |
| `[].avatarUrl` | `string \| null` | 否 | 头像 URL，未设置为 null |
| `[].phone` | `string \| null` | 否 | 手机号，仅展示，未填为 null |
| `[].position` | `string \| null` | 否 | 职务，仅展示，未填为 null |
| `[].isActive` | `boolean` | 否 | 是否启用；停用后无法登录且令牌立即失效 |
| `[].lastLoginAt` | `string \| null` | 否 | 最后登录时间，从未登录为 null |
| `[].createdAt` | `string` | 是 | 创建时间（UTC） |
| `[].roles` | `RoleBriefOut[]` | 否 | 拥有的角色 |

#### `PUT /api/v1/identity/groups/{group_id}/members`

**设置小组成员（整组替换）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `group_id` | path | `string` | 是 |  |

**请求体** `GroupMembersIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `userIds` | `string[]` | 否 | 成员用户 id，整组替换；传空数组即清空 |

**响应** `ApiResponse<GroupOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 小组 ID |
| `name` | `string` | 是 | 小组名称，唯一 |
| `description` | `string` | 否 | 小组说明 |
| `createdAt` | `string` | 是 | 创建时间（UTC） |
| `permissions` | `string[]` | 否 | 小组整体权限；成员自动获得 |
| `memberIds` | `string[]` | 否 | 成员用户 id；编辑成员时整组替换 |
| `memberCount` | `number` | 否 | 成员数 |

#### `PUT /api/v1/identity/groups/{group_id}/permissions`

**设置小组整体权限（整组替换）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `group_id` | path | `string` | 是 |  |

**请求体** `PermissionsIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `permissions` | `string[]` | 否 | 整组替换；传空数组即清空。出现未登记的权限码会被拒绝 |

**响应** `ApiResponse<GroupOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 小组 ID |
| `name` | `string` | 是 | 小组名称，唯一 |
| `description` | `string` | 否 | 小组说明 |
| `createdAt` | `string` | 是 | 创建时间（UTC） |
| `permissions` | `string[]` | 否 | 小组整体权限；成员自动获得 |
| `memberIds` | `string[]` | 否 | 成员用户 id；编辑成员时整组替换 |
| `memberCount` | `number` | 否 | 成员数 |

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
| `phone` | `string \| null` | 否 | 手机号，仅展示，未填为 null |
| `position` | `string \| null` | 否 | 职务，仅展示，未填为 null |
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
| `phone` | `string \| null` | 否 | 手机号；传 null 清空 |
| `position` | `string \| null` | 否 | 职务；传 null 清空 |

**响应** `ApiResponse<MeOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 用户 ID |
| `username` | `string` | 是 | 登录名，入库前已转小写 |
| `displayName` | `string` | 否 | 昵称（顶栏展示用） |
| `email` | `string \| null` | 否 | 邮箱，未填为 null |
| `avatarUrl` | `string \| null` | 否 | 头像 URL，未设置为 null |
| `phone` | `string \| null` | 否 | 手机号，仅展示，未填为 null |
| `position` | `string \| null` | 否 | 职务，仅展示，未填为 null |
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

#### `GET /api/v1/identity/team/permissions`

**团队基础权限（全体启用账号）**

**响应** `ApiResponse<string[]>`（前端取 `res.data`）

#### `PUT /api/v1/identity/team/permissions`

**设置团队基础权限（整组替换）**

**请求体** `PermissionsIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `permissions` | `string[]` | 否 | 整组替换；传空数组即清空。出现未登记的权限码会被拒绝 |

**响应** `ApiResponse<string[]>`（前端取 `res.data`）

#### `GET /api/v1/identity/users`

**用户列表**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `keyword` | query | `string` | 否 | 匹配用户名/昵称/邮箱/手机号/职务 |
| `name` | query | `string` | 否 | 按姓名（昵称）模糊筛选 |
| `username` | query | `string` | 否 | 按账号模糊筛选 |
| `phone` | query | `string` | 否 | 按手机号模糊筛选 |
| `isActive` | query | `boolean | null` | 否 |  |
| `roleId` | query | `string | null` | 否 |  |
| `groupId` | query | `string | null` | 否 | 只看该小组成员 |
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
| `phone` | `string \| null` | 否 | 手机号，仅展示 |
| `position` | `string \| null` | 否 | 职务，仅展示 |
| `roleIds` | `string[]` | 否 | 角色 id 列表，可留空 |
| `groupIds` | `string[]` | 否 | 建号时加入的小组 id 列表，可留空；小组不存在会报 404 |

**响应** `ApiResponse<UserOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 用户 ID |
| `username` | `string` | 是 | 登录名，入库前已转小写 |
| `displayName` | `string` | 否 | 昵称（顶栏展示用） |
| `email` | `string \| null` | 否 | 邮箱，未填为 null |
| `avatarUrl` | `string \| null` | 否 | 头像 URL，未设置为 null |
| `phone` | `string \| null` | 否 | 手机号，仅展示，未填为 null |
| `position` | `string \| null` | 否 | 职务，仅展示，未填为 null |
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
| `phone` | `string \| null` | 否 | 手机号，仅展示，未填为 null |
| `position` | `string \| null` | 否 | 职务，仅展示，未填为 null |
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
| `phone` | `string \| null` | 否 | 手机号；传 null 清空 |
| `position` | `string \| null` | 否 | 职务；传 null 清空 |
| `isActive` | `boolean \| null` | 否 | 启用/停用；不能停用自己，且必须保留一个启用的超管。不接受 null：停用请传 false，不想改就不要传这个字段 |

**响应** `ApiResponse<UserOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 用户 ID |
| `username` | `string` | 是 | 登录名，入库前已转小写 |
| `displayName` | `string` | 否 | 昵称（顶栏展示用） |
| `email` | `string \| null` | 否 | 邮箱，未填为 null |
| `avatarUrl` | `string \| null` | 否 | 头像 URL，未设置为 null |
| `phone` | `string \| null` | 否 | 手机号，仅展示，未填为 null |
| `position` | `string \| null` | 否 | 职务，仅展示，未填为 null |
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
| `phone` | `string \| null` | 否 | 手机号，仅展示，未填为 null |
| `position` | `string \| null` | 否 | 职务，仅展示，未填为 null |
| `isActive` | `boolean` | 否 | 是否启用；停用后无法登录且令牌立即失效 |
| `lastLoginAt` | `string \| null` | 否 | 最后登录时间，从未登录为 null |
| `createdAt` | `string` | 是 | 创建时间（UTC） |
| `roles` | `RoleBriefOut[]` | 否 | 拥有的角色 |

#### `GET /api/v1/identity/users/{user_id}/effective-permissions`

**用户有效权限与四层来源**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `user_id` | path | `string` | 是 |  |

**响应** `ApiResponse<EffectivePermissionsOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `team` | `string[]` | 否 | 团队基础权限（全体启用账号） |
| `groups` | `GroupLayerOut[]` | 否 | 各所属小组授予的权限 |
| `personal` | `string[]` | 否 | 单独授予的权限 |
| `roles` | `string[]` | 否 | 现有角色授予的权限 |
| `effective` | `string[]` | 否 | 四层并集；`*` 表示超级管理员。require() 唯一判据 |

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

#### `GET /api/v1/identity/users/{user_id}/permissions`

**用户单独授予的权限**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `user_id` | path | `string` | 是 |  |

**响应** `ApiResponse<string[]>`（前端取 `res.data`）

#### `PUT /api/v1/identity/users/{user_id}/permissions`

**设置个人权限（整组替换）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `user_id` | path | `string` | 是 |  |

**请求体** `PermissionsIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `permissions` | `string[]` | 否 | 整组替换；传空数组即清空。出现未登记的权限码会被拒绝 |

**响应** `ApiResponse<string[]>`（前端取 `res.data`）

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
| `phone` | `string \| null` | 否 | 手机号，仅展示，未填为 null |
| `position` | `string \| null` | 否 | 职务，仅展示，未填为 null |
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
| `GET` | `/api/v1/tasks/feed` | 统一任务中心：视频生成、生图与本人可见的替换档案 | 🔒 |
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
| `items` | `modules__task__schema__TaskOut[]` | 否 |  |
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
| `referenceLibraryIds` | `string[]` | 否 | 参考素材的资产库 v2 资产 id，最多 20 个，**顺序即提交顺序**。受理时校验：必须存在、当前账号能读、不在回收站 / 草稿里，且是 `image` / `video` / `audio` 之一（能否收图、收几张由模型的 `maxReferences` 决定）。传 id 而不是 URL：取件链接是私签的，供应商取不到 |
| `textLibraryIds` | `string[]` | 否 | 要拼进提示词的文本资产 id，按顺序对应 `{{Text 1}}` / `{{Text 2}}`…；提交时按当时版本冻结正文，之后编辑资产不影响本次生成与重试。只接受当前账号可读、在库的 text 类型资产，最多 8 份 |
| `stylePreset` | `string \| null` | 否 | 风格预设标识，留空为不套风格 |

**响应** `ApiResponse<core__response__ApiResponse_TaskOut___2>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `code` | `number` | 否 | 0 表示成功，其余为业务错误码 |
| `message` | `string` | 否 |  |
| `data` | `modules__task__schema__TaskOut \| null` | 否 |  |

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

**统一任务中心：视频生成、生图与本人可见的替换档案**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `source` | query | `"all" | "video" | "image" | "replacement"` | 否 |  |
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

**响应** `ApiResponse<core__response__ApiResponse_TaskOut___2>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `code` | `number` | 否 | 0 表示成功，其余为业务错误码 |
| `message` | `string` | 否 |  |
| `data` | `modules__task__schema__TaskOut \| null` | 否 |  |

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

**响应** `ApiResponse<core__response__ApiResponse_TaskOut___2>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `code` | `number` | 否 | 0 表示成功，其余为业务错误码 |
| `message` | `string` | 否 |  |
| `data` | `modules__task__schema__TaskOut \| null` | 否 |  |

#### `POST /api/v1/tasks/{task_id}/favorite`

**收藏 / 取消收藏**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `task_id` | path | `string` | 是 | 任务 ID |

**响应** `ApiResponse<core__response__ApiResponse_TaskOut___2>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `code` | `number` | 否 | 0 表示成功，其余为业务错误码 |
| `message` | `string` | 否 |  |
| `data` | `modules__task__schema__TaskOut \| null` | 否 |  |

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

**响应** `ApiResponse<core__response__ApiResponse_ModelListOut___1>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `code` | `number` | 否 | 0 表示成功，其余为业务错误码 |
| `message` | `string` | 否 |  |
| `data` | `modules__catalog__schema__ModelListOut \| null` | 否 |  |

### 4.4 生图（3 个）

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
| `size` | `string` | 否 | 宽x高，如 `1024x1536`；留空取模型 `paramSchema.size.default` |
| `count` | `number` | 否 | 张数；`0` 表示取模型 `paramSchema.count.default`，上限以模型声明为准 |
| `params` | `Record<string, unknown>` | 否 | 模型声明的其他参数，键与 `GET /models` 返回的 `paramSchema` 一致（如 `quality` / `seed`）。未声明的键会被 400 拒绝，避免打错字被静默忽略；与顶层 `size` / `count` 重复时以顶层为准 |
| `model` | `string` | 否 | 模型 id，留空取默认 |
| `referenceLibraryIds` | `string[]` | 否 | 参考图（图生图 / 多图合成）的素材库资产 id，**按顺序生效**：多图合成时提示词里的「第一张 / 第二张」指的就是这个顺序，重排会让提示词与图片错位。只接受 `image` 类型且当前账号能读的资产，张数上限由模型 `maxReferences` 决定（`0` = 该模型不支持参考图）。传 id 而不是 URL：我们的取件链接是私签的、供应商访问不到，服务端读字节后以 `data:image/...;base64,...` 交给供应商 |
| `textLibraryIds` | `string[]` | 否 | 要拼进提示词的文本资产 id，**按顺序生效**：`{{Text 1}}` 指第 1 份、`{{Text 2}}` 指第 2 份。提交时把正文按当时版本冻结进任务，之后编辑资产不会改变这次生成与它的重试。只接受当前账号可读、在库的 `text` 类型资产；一次最多 8 份 |

**响应** `ApiResponse<ImageJobOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 生图记录 ID |
| `name` | `string` | 是 | 名称 |
| `prompt` | `string` | 是 | 画面描述 |
| `size` | `string` | 是 | 尺寸，如 `1024x1024` |
| `count` | `number` | 否 | 一次生成的张数 |
| `params` | `Record<string, unknown>` | 否 | 模型额外参数（不含 size / count），键按提交时的模型 `paramSchema` |
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
| `referenceLibraryIds` | `string[]` | 否 | 提交时带的参考图资产 id（顺序即提交顺序）；纯文生图为空 |
| `textLibraryIds` | `string[]` | 否 | 绑定的文本资产 id（顺序即提示词里 `{{Text N}}` 的编号顺序） |
| `textBindings` | `Record<string, unknown>[]` | 否 | 绑定的文案与版本快照（不含正文）：assetId / name / version / sha256 |
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

### 4.5 模板（11 个）

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| `GET` | `/api/v1/templates/replacement-catalog` | 人物/产品/场景替换模板与调用档案 | 🔒 |
| `POST` | `/api/v1/templates/replacement-runs` | 归档输入并预览最终提示词，不提交生成 | 🔒 |
| `DELETE` | `/api/v1/templates/replacement-runs/{run_id}` | 将本人替换任务与调用档案移入回收站，不取消生成 | 🔒 |
| `GET` | `/api/v1/templates/replacement-runs/{run_id}` | 替换任务状态与提示词 | 🔒 |
| `POST` | `/api/v1/templates/replacement-runs/{run_id}/execute` | 提交替换任务；billingMode=credits 表示本任务用积分加速 | 🔒 |
| `GET` | `/api/v1/templates/replacement-runs/{run_id}/inputs/{asset_id}` | 读取当次归档输入快照 | 🔒 |
| `GET` | `/api/v1/templates/replacement-runs/{run_id}/inputs/{asset_id}/thumbnail` | 读取当次输入快照缩略图 | 🔒 |
| `GET` | `/api/v1/templates/replacement-runs/{run_id}/output` | 预览或下载原始生成结果 | 🔒 |
| `POST` | `/api/v1/templates/replacement-runs/{run_id}/reconfirm` | 确认上次提交可能已计费后放行重提（默认拒绝） | 🔒 |
| `POST` | `/api/v1/templates/replacement-runs/{run_id}/restore` | 恢复本人回收站中的替换任务与调用档案 | 🔒 |
| `PUT` | `/api/v1/templates/replacement-runs/{run_id}/review` | 保存人工验收，版本冲突需重新核对 | 🔒 |

#### `GET /api/v1/templates/replacement-catalog`

**人物/产品/场景替换模板与调用档案**

**响应** `ApiResponse<ReplacementCatalog>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `channels` | `ReplacementChannelOut[]` | 否 |  |
| `provider` | `string` | 否 |  |
| `maxCredits` | `number` | 否 |  |
| `kinds` | `string[]` | 否 |  |
| `runs` | `ReplacementRunOut[]` | 是 |  |
| `packs` | `ReplacementPack[]` | 否 |  |
| `packRoles` | `Record<string, Record<string, string>>` | 否 |  |
| `baseRoles` | `Record<string, string>` | 否 |  |
| `packScopes` | `Record<string, string>` | 否 |  |

#### `POST /api/v1/templates/replacement-runs`

**归档输入并预览最终提示词，不提交生成**

**请求体** `PrepareReplacement`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `requestId` | `string` | 是 |  |
| `channel` | `"libtv" \| "bailian" \| "bailian-prime"` | 否 |  |
| `kind` | `"character" \| "product" \| "scene"` | 是 |  |
| `target` | `string` | 否 |  |
| `sourceLibraryId` | `string \| null` | 否 | 原片（资产库里的视频）id |
| `videoTemplateId` | `string \| null` | 否 |  |
| `scenePrompt` | `string \| null` | 否 | 文字换场景的描述；省略使用模板默认值，空白拒绝 |
| `referenceIds` | `string[]` | 否 |  |
| `libraryPackId` | `string \| null` | 否 | 资产库 v2 封装的 id；成员按 position 顺序当参考图，用途取自成员自己声明的 role。与 packId / packVersion / referenceIds / extraPackIds 互斥；prepare 时展开并冻结，之后封装改成员不影响已准备的任务 |
| `textLibraryIds` | `string[]` | 否 | 绑定的文本资产 id，按顺序对应 `{{Text N}}`；最多 8 份 |
| `extraPackIds` | `string[]` | 否 | 第 2 位及以后的素材包 id，按提示词槽位顺序；第一包仍用 packId |
| `extraPackVersions` | `number[]` | 否 | 与 extraPackIds 顺序对应的发布版本；旧调用省略时由服务端解析并冻结 |
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
| `channel` | `"libtv" \| "bailian" \| "bailian-prime"` | 否 |  |
| `channelLabel` | `string` | 否 |  |
| `kind` | `"character" \| "product" \| "scene"` | 是 |  |
| `target` | `string` | 是 |  |
| `status` | `string` | 是 |  |
| `archivePath` | `string \| null` | 否 | 工作区绝对路径（排查用）；目录已被清理或换机器后为 null |
| `createdAt` | `string` | 是 |  |
| `startedAt` | `string \| null` | 否 | 确认提交（进入排队）的时间，未开始为 null |
| `finishedAt` | `string \| null` | 否 | 这一轮结束的时间；未结束为 null |
| `elapsedSec` | `number \| null` | 否 | 确认提交到结束的耗时（秒）；未完成时按到此刻计，未开始为 null |
| `prompt` | `string` | 是 |  |
| `promptSha256` | `string \| null` | 否 | 实际发出去那段提示词的 sha256（所有后处理之后）。档案里另外两个哈希（template/spec）哈希的是模板与输入，复核不了最终字节。加这列之前准备的任务为 null，不回填。 |
| `textLibraryIds` | `string[]` | 否 | 本次绑定的文本资产 id（顺序即 `{{Text N}}` 编号） |
| `textBindings` | `Record<string, unknown>[]` | 否 | 绑定的文案与版本快照（不含正文） |
| `promptMode` | `"fixed" \| "compiled"` | 否 | fixed=封装视频模板时写死的原文，逐字提交；compiled=按素材与替换范围现编。展示层据此告诉用户「跑的就是你封装时那份提示词」。 |
| `videoTemplateName` | `string \| null` | 否 | fixed 时出自哪条视频模板；compiled 为 null |
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
| `channel` | `"libtv" \| "bailian" \| "bailian-prime"` | 否 |  |
| `channelLabel` | `string` | 否 |  |
| `kind` | `"character" \| "product" \| "scene"` | 是 |  |
| `target` | `string` | 是 |  |
| `status` | `string` | 是 |  |
| `archivePath` | `string \| null` | 否 | 工作区绝对路径（排查用）；目录已被清理或换机器后为 null |
| `createdAt` | `string` | 是 |  |
| `startedAt` | `string \| null` | 否 | 确认提交（进入排队）的时间，未开始为 null |
| `finishedAt` | `string \| null` | 否 | 这一轮结束的时间；未结束为 null |
| `elapsedSec` | `number \| null` | 否 | 确认提交到结束的耗时（秒）；未完成时按到此刻计，未开始为 null |
| `prompt` | `string` | 是 |  |
| `promptSha256` | `string \| null` | 否 | 实际发出去那段提示词的 sha256（所有后处理之后）。档案里另外两个哈希（template/spec）哈希的是模板与输入，复核不了最终字节。加这列之前准备的任务为 null，不回填。 |
| `textLibraryIds` | `string[]` | 否 | 本次绑定的文本资产 id（顺序即 `{{Text N}}` 编号） |
| `textBindings` | `Record<string, unknown>[]` | 否 | 绑定的文案与版本快照（不含正文） |
| `promptMode` | `"fixed" \| "compiled"` | 否 | fixed=封装视频模板时写死的原文，逐字提交；compiled=按素材与替换范围现编。展示层据此告诉用户「跑的就是你封装时那份提示词」。 |
| `videoTemplateName` | `string \| null` | 否 | fixed 时出自哪条视频模板；compiled 为 null |
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
| `channel` | `"libtv" \| "bailian" \| "bailian-prime"` | 否 |  |
| `channelLabel` | `string` | 否 |  |
| `kind` | `"character" \| "product" \| "scene"` | 是 |  |
| `target` | `string` | 是 |  |
| `status` | `string` | 是 |  |
| `archivePath` | `string \| null` | 否 | 工作区绝对路径（排查用）；目录已被清理或换机器后为 null |
| `createdAt` | `string` | 是 |  |
| `startedAt` | `string \| null` | 否 | 确认提交（进入排队）的时间，未开始为 null |
| `finishedAt` | `string \| null` | 否 | 这一轮结束的时间；未结束为 null |
| `elapsedSec` | `number \| null` | 否 | 确认提交到结束的耗时（秒）；未完成时按到此刻计，未开始为 null |
| `prompt` | `string` | 是 |  |
| `promptSha256` | `string \| null` | 否 | 实际发出去那段提示词的 sha256（所有后处理之后）。档案里另外两个哈希（template/spec）哈希的是模板与输入，复核不了最终字节。加这列之前准备的任务为 null，不回填。 |
| `textLibraryIds` | `string[]` | 否 | 本次绑定的文本资产 id（顺序即 `{{Text N}}` 编号） |
| `textBindings` | `Record<string, unknown>[]` | 否 | 绑定的文案与版本快照（不含正文） |
| `promptMode` | `"fixed" \| "compiled"` | 否 | fixed=封装视频模板时写死的原文，逐字提交；compiled=按素材与替换范围现编。展示层据此告诉用户「跑的就是你封装时那份提示词」。 |
| `videoTemplateName` | `string \| null` | 否 | fixed 时出自哪条视频模板；compiled 为 null |
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
| `channel` | `"libtv" \| "bailian" \| "bailian-prime"` | 否 |  |
| `channelLabel` | `string` | 否 |  |
| `kind` | `"character" \| "product" \| "scene"` | 是 |  |
| `target` | `string` | 是 |  |
| `status` | `string` | 是 |  |
| `archivePath` | `string \| null` | 否 | 工作区绝对路径（排查用）；目录已被清理或换机器后为 null |
| `createdAt` | `string` | 是 |  |
| `startedAt` | `string \| null` | 否 | 确认提交（进入排队）的时间，未开始为 null |
| `finishedAt` | `string \| null` | 否 | 这一轮结束的时间；未结束为 null |
| `elapsedSec` | `number \| null` | 否 | 确认提交到结束的耗时（秒）；未完成时按到此刻计，未开始为 null |
| `prompt` | `string` | 是 |  |
| `promptSha256` | `string \| null` | 否 | 实际发出去那段提示词的 sha256（所有后处理之后）。档案里另外两个哈希（template/spec）哈希的是模板与输入，复核不了最终字节。加这列之前准备的任务为 null，不回填。 |
| `textLibraryIds` | `string[]` | 否 | 本次绑定的文本资产 id（顺序即 `{{Text N}}` 编号） |
| `textBindings` | `Record<string, unknown>[]` | 否 | 绑定的文案与版本快照（不含正文） |
| `promptMode` | `"fixed" \| "compiled"` | 否 | fixed=封装视频模板时写死的原文，逐字提交；compiled=按素材与替换范围现编。展示层据此告诉用户「跑的就是你封装时那份提示词」。 |
| `videoTemplateName` | `string \| null` | 否 | fixed 时出自哪条视频模板；compiled 为 null |
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

#### `GET /api/v1/templates/replacement-runs/{run_id}/inputs/{asset_id}/thumbnail`

**读取当次输入快照缩略图**

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
| `channel` | `"libtv" \| "bailian" \| "bailian-prime"` | 否 |  |
| `channelLabel` | `string` | 否 |  |
| `kind` | `"character" \| "product" \| "scene"` | 是 |  |
| `target` | `string` | 是 |  |
| `status` | `string` | 是 |  |
| `archivePath` | `string \| null` | 否 | 工作区绝对路径（排查用）；目录已被清理或换机器后为 null |
| `createdAt` | `string` | 是 |  |
| `startedAt` | `string \| null` | 否 | 确认提交（进入排队）的时间，未开始为 null |
| `finishedAt` | `string \| null` | 否 | 这一轮结束的时间；未结束为 null |
| `elapsedSec` | `number \| null` | 否 | 确认提交到结束的耗时（秒）；未完成时按到此刻计，未开始为 null |
| `prompt` | `string` | 是 |  |
| `promptSha256` | `string \| null` | 否 | 实际发出去那段提示词的 sha256（所有后处理之后）。档案里另外两个哈希（template/spec）哈希的是模板与输入，复核不了最终字节。加这列之前准备的任务为 null，不回填。 |
| `textLibraryIds` | `string[]` | 否 | 本次绑定的文本资产 id（顺序即 `{{Text N}}` 编号） |
| `textBindings` | `Record<string, unknown>[]` | 否 | 绑定的文案与版本快照（不含正文） |
| `promptMode` | `"fixed" \| "compiled"` | 否 | fixed=封装视频模板时写死的原文，逐字提交；compiled=按素材与替换范围现编。展示层据此告诉用户「跑的就是你封装时那份提示词」。 |
| `videoTemplateName` | `string \| null` | 否 | fixed 时出自哪条视频模板；compiled 为 null |
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
| `channel` | `"libtv" \| "bailian" \| "bailian-prime"` | 否 |  |
| `channelLabel` | `string` | 否 |  |
| `kind` | `"character" \| "product" \| "scene"` | 是 |  |
| `target` | `string` | 是 |  |
| `status` | `string` | 是 |  |
| `archivePath` | `string \| null` | 否 | 工作区绝对路径（排查用）；目录已被清理或换机器后为 null |
| `createdAt` | `string` | 是 |  |
| `startedAt` | `string \| null` | 否 | 确认提交（进入排队）的时间，未开始为 null |
| `finishedAt` | `string \| null` | 否 | 这一轮结束的时间；未结束为 null |
| `elapsedSec` | `number \| null` | 否 | 确认提交到结束的耗时（秒）；未完成时按到此刻计，未开始为 null |
| `prompt` | `string` | 是 |  |
| `promptSha256` | `string \| null` | 否 | 实际发出去那段提示词的 sha256（所有后处理之后）。档案里另外两个哈希（template/spec）哈希的是模板与输入，复核不了最终字节。加这列之前准备的任务为 null，不回填。 |
| `textLibraryIds` | `string[]` | 否 | 本次绑定的文本资产 id（顺序即 `{{Text N}}` 编号） |
| `textBindings` | `Record<string, unknown>[]` | 否 | 绑定的文案与版本快照（不含正文） |
| `promptMode` | `"fixed" \| "compiled"` | 否 | fixed=封装视频模板时写死的原文，逐字提交；compiled=按素材与替换范围现编。展示层据此告诉用户「跑的就是你封装时那份提示词」。 |
| `videoTemplateName` | `string \| null` | 否 | fixed 时出自哪条视频模板；compiled 为 null |
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
| `channel` | `"libtv" \| "bailian" \| "bailian-prime"` | 否 |  |
| `channelLabel` | `string` | 否 |  |
| `kind` | `"character" \| "product" \| "scene"` | 是 |  |
| `target` | `string` | 是 |  |
| `status` | `string` | 是 |  |
| `archivePath` | `string \| null` | 否 | 工作区绝对路径（排查用）；目录已被清理或换机器后为 null |
| `createdAt` | `string` | 是 |  |
| `startedAt` | `string \| null` | 否 | 确认提交（进入排队）的时间，未开始为 null |
| `finishedAt` | `string \| null` | 否 | 这一轮结束的时间；未结束为 null |
| `elapsedSec` | `number \| null` | 否 | 确认提交到结束的耗时（秒）；未完成时按到此刻计，未开始为 null |
| `prompt` | `string` | 是 |  |
| `promptSha256` | `string \| null` | 否 | 实际发出去那段提示词的 sha256（所有后处理之后）。档案里另外两个哈希（template/spec）哈希的是模板与输入，复核不了最终字节。加这列之前准备的任务为 null，不回填。 |
| `textLibraryIds` | `string[]` | 否 | 本次绑定的文本资产 id（顺序即 `{{Text N}}` 编号） |
| `textBindings` | `Record<string, unknown>[]` | 否 | 绑定的文案与版本快照（不含正文） |
| `promptMode` | `"fixed" \| "compiled"` | 否 | fixed=封装视频模板时写死的原文，逐字提交；compiled=按素材与替换范围现编。展示层据此告诉用户「跑的就是你封装时那份提示词」。 |
| `videoTemplateName` | `string \| null` | 否 | fixed 时出自哪条视频模板；compiled 为 null |
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

### 4.6 分镜模板（1 个）

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

### 4.7 镜头库（1 个）

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

### 4.8 系统（2 个）

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

### 4.9 导出（6 个）

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

### 4.10 资产库（45 个）

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| `GET` | `/api/v1/library/assets` | 资产库列表（分页 + 分面） | 🔒 |
| `POST` | `/api/v1/library/assets/bulk` | 批量写：逐条判定，部分失败返回明细 | 🔒 |
| `POST` | `/api/v1/library/assets/from-generation` | 生成结果入库（落草稿箱，按 sourceKey 幂等） | 🔒 |
| `POST` | `/api/v1/library/assets/upload` | 上传图片 / 音频 / 文本（流式，按内容秒传） | 🔒 |
| `PATCH` | `/api/v1/library/assets/{asset_id}` | 改名 / 换标签（不含可见性） | 🔒 |
| `DELETE` | `/api/v1/library/assets/{asset_id}` | 移入回收站（软删，可恢复） | 🔒 |
| `GET` | `/api/v1/library/assets/{asset_id}` | 资产详情（含标签与生成参数） | 🔒 |
| `GET` | `/api/v1/library/assets/{asset_id}/file` | 下载或预览原文件 | 🔒 |
| `GET` | `/api/v1/library/assets/{asset_id}/preview` | 受控公共预览（视频给派生流） | 🔒 |
| `GET` | `/api/v1/library/assets/{asset_id}/stream` | 浏览器兼容视频预览（派生流，不改原片） | 🔒 |
| `GET` | `/api/v1/library/assets/{asset_id}/text` | 读取文本正文与当前版本 | 🔒 |
| `PUT` | `/api/v1/library/assets/{asset_id}/text` | 在线编辑文本（追加一版，可带 expectedVersion 防覆盖） | 🔒 |
| `GET` | `/api/v1/library/assets/{asset_id}/text/raw` | 纯文本取件（text/plain + nosniff） | 🔒 |
| `POST` | `/api/v1/library/assets/{asset_id}/text/restore` | 恢复历史版本（写成新的一版，不改历史行） | 🔒 |
| `GET` | `/api/v1/library/assets/{asset_id}/text/versions` | 文本版本历史（默认不带正文） | 🔒 |
| `GET` | `/api/v1/library/assets/{asset_id}/text/versions/{number}` | 读取某一历史版本正文 | 🔒 |
| `GET` | `/api/v1/library/assets/{asset_id}/thumbnail` | 读取缩略图 | 🔒 |
| `GET` | `/api/v1/library/downloads` | 我最近发起的打包任务 | 🔒 |
| `POST` | `/api/v1/library/downloads` | 发起批量打包（异步，返回任务） | 🔒 |
| `GET` | `/api/v1/library/downloads/{job_id}` | 打包任务状态 | 🔒 |
| `GET` | `/api/v1/library/downloads/{job_id}/file` | 取打包好的 zip | 🔒 |
| `POST` | `/api/v1/library/drafts/{asset_id}/approve` | 草稿采纳：进入个人素材库 | 🔒 |
| `POST` | `/api/v1/library/drafts/{asset_id}/reject` | 草稿驳回：进回收站，可恢复回草稿 | 🔒 |
| `GET` | `/api/v1/library/facets` | 资产库分面统计 | 🔒 |
| `GET` | `/api/v1/library/groups` | 我的小组（含是否负责人） | 🔒 |
| `PUT` | `/api/v1/library/groups/{group_id}/leaders` | 设置小组负责人（整体替换） | 🔒 |
| `POST` | `/api/v1/library/packs` | 建封装（成员必须是我看得见且在库的） | 🔒 |
| `GET` | `/api/v1/library/packs/{pack_id}` | 封装详情（含成员） | 🔒 |
| `PATCH` | `/api/v1/library/packs/{pack_id}` | 改封装：改名 / 整体替换成员 / 换封面 / 换标签 | 🔒 |
| `DELETE` | `/api/v1/library/packs/{pack_id}` | 解散封装（进回收站，成员不受影响） | 🔒 |
| `GET` | `/api/v1/library/selection` | 选择器：只返回库内资产 | 🔒 |
| `POST` | `/api/v1/library/share-requests` | 发起共享 / 撤销共享申请 | 🔒 |
| `GET` | `/api/v1/library/share-requests` | 申请单列表（我提交的 / 待我审核） | 🔒 |
| `POST` | `/api/v1/library/share-requests/{request_id}/approve` | 通过申请 | 🔒 |
| `POST` | `/api/v1/library/share-requests/{request_id}/cancel` | 撤回自己提交的申请 | 🔒 |
| `POST` | `/api/v1/library/share-requests/{request_id}/reject` | 驳回申请 | 🔒 |
| `POST` | `/api/v1/library/source-videos` | 上传模板库参考视频（只收视频） | 🔒 |
| `GET` | `/api/v1/library/tags` | 标签列表（两级平铺，含当前入口的素材数，按添加时间排序） | 🔒 |
| `POST` | `/api/v1/library/tags` | 新建标签（同名返回已有那条） | 🔒 |
| `PATCH` | `/api/v1/library/tags/{tag_id}` | 重命名标签（不做隐式合并） | 🔒 |
| `DELETE` | `/api/v1/library/tags/{tag_id}` | 删除标签（旗下二级提升为一级，资产不受影响） | 🔒 |
| `PATCH` | `/api/v1/library/tags/{tag_id}/parent` | 移动标签层级（parentId 为空则提升为一级） | 🔒 |
| `POST` | `/api/v1/library/text-assets` | 站内新建文本资产（直接写正文，不传文件） | 🔒 |
| `DELETE` | `/api/v1/library/trash/{asset_id}` | 彻底删除（不可逆，先过引用检查） | 🔒 |
| `POST` | `/api/v1/library/trash/{asset_id}/restore` | 从回收站恢复（强制回到个人库，不恢复共享） | 🔒 |

#### `GET /api/v1/library/assets`

**资产库列表（分页 + 分面）**

`state=draft|trash` 时忽略 `library`：草稿与回收站不在任何可见范围内。

代审 / 管理员看全量靠权限点（`library:draft:review` / `library:delete:any`），
不靠一个 `mine=false` 参数——参数能被任何人传，权限点不能。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `library` | query | `LibraryTab` | 否 |  |
| `state` | query | `LibraryAssetState` | 否 |  |
| `kind` | query | `LibraryAssetKind | null` | 否 |  |
| `tagIds` | query | `string[] | null` | 否 |  |
| `keyword` | query | `string` | 否 |  |
| `sort` | query | `AssetSort` | 否 |  |
| `origin` | query | `LibraryAssetOrigin[] | null` | 否 |  |
| `page` | query | `number` | 否 | 页码，从 1 开始 |
| `pageSize` | query | `number` | 否 | 每页条数 |

**响应** `ApiResponse<LibraryAssetPage>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `LibraryAssetOut[]` | 否 |  |
| `total` | `number` | 否 |  |
| `page` | `number` | 否 |  |
| `pageSize` | `number` | 否 |  |
| `facets` | `LibraryFacets` | 否 |  |

#### `POST /api/v1/library/assets/bulk`

**批量写：逐条判定，部分失败返回明细**

**请求体** `BulkIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `ids` | `string[]` | 是 |  |
| `action` | `BulkAction` | 是 |  |
| `payload` | `Record<string, unknown>` | 否 |  |
| `expectedVersions` | `Record<string, number>` | 否 |  |

**响应** `ApiResponse<BulkOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `succeeded` | `number` | 否 |  |
| `failed` | `number` | 否 |  |
| `results` | `BulkItemResult[]` | 否 |  |

#### `POST /api/v1/library/assets/from-generation`

**生成结果入库（落草稿箱，按 sourceKey 幂等）**

`state=draft` / `visibility=personal` 由服务层强制，入参给不了。

命中幂等（同一个 `sourceKey`，或同一份字节已在该 owner 的库里）时返回
**200 + `created=false`**：调用方据此知道「这条已经入过库了」，不必再关联一次。

**请求体** `FromGenerationIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sourceModule` | `string` | 是 |  |
| `sourceTaskId` | `string` | 是 |  |
| `sourceKey` | `string` | 是 |  |
| `kind` | `GenerationKind` | 是 |  |
| `name` | `string` | 否 |  |
| `storageKey` | `string` | 否 |  |
| `archiveKey` | `string` | 否 |  |
| `sourceUrl` | `string` | 否 |  |
| `mimeType` | `string` | 否 |  |
| `sizeBytes` | `number` | 否 |  |
| `tagIds` | `string[]` | 否 |  |
| `generation` | `GenerationSnapshotIn` | 否 |  |

**响应** `ApiResponse<IngestOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `assetId` | `string` | 是 |  |
| `created` | `boolean` | 是 |  |

#### `POST /api/v1/library/assets/upload`

**上传图片 / 音频 / 文本（流式，按内容秒传）**

只收图片与音频：视频走 `/source-videos`（模板库专用）。

分开的理由不是「类型不同」，而是**来源语义不同**：这个接口是用户自己挑文件，
视频上传属于模板库的参考素材流程，它需要 `origin=template_source` 才能在
切换后继续喂给模板封装。混成一个接口会让来源变成前端传参，写错了没人发现。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `filename` | query | `string` | 是 |  |
| `kind` | query | `UploadKind` | 否 |  |
| `sourceKey` | query | `string | null` | 否 |  |

**响应** `ApiResponse<LibraryAssetOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `kind` | `LibraryAssetKind` | 是 |  |
| `origin` | `string` | 是 | upload / generated / template_source / imported / pack |
| `mimeType` | `string \| null` | 否 |  |
| `sizeBytes` | `number` | 否 |  |
| `durationSec` | `number \| null` | 否 |  |
| `hasAudio` | `boolean` | 否 |  |
| `ownerId` | `string` | 是 |  |
| `ownerName` | `string \| null` | 否 |  |
| `visibility` | `LibraryVisibility` | 是 |  |
| `state` | `LibraryAssetState` | 是 |  |
| `stateVersion` | `number` | 否 |  |
| `coverUrl` | `string \| null` | 否 |  |
| `url` | `string` | 否 |  |
| `previewUrl` | `string \| null` | 否 |  |
| `streamUrl` | `string \| null` | 否 |  |
| `width` | `number \| null` | 否 |  |
| `height` | `number \| null` | 否 |  |
| `groupIds` | `string[]` | 否 |  |
| `tags` | `LibraryTagOut[]` | 否 |  |
| `trashReason` | `string \| null` | 否 |  |
| `trashExpiresAt` | `string \| null` | 否 |  |
| `textExcerpt` | `string \| null` | 否 |  |
| `textVersion` | `number` | 否 |  |
| `textUrl` | `string \| null` | 否 |  |
| `createdAt` | `string` | 是 |  |
| `updatedAt` | `string \| null` | 否 |  |

#### `PATCH /api/v1/library/assets/{asset_id}`

**改名 / 换标签（不含可见性）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**请求体** `AssetPatchIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `string \| null` | 否 |  |
| `tagIds` | `string[] \| null` | 否 |  |
| `expectedVersion` | `number` | 否 | >0 时做乐观锁校验；0 表示不校验（兼容单页操作） |

**响应** `ApiResponse<LibraryAssetDetailOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `kind` | `LibraryAssetKind` | 是 |  |
| `origin` | `string` | 是 | upload / generated / template_source / imported / pack |
| `mimeType` | `string \| null` | 否 |  |
| `sizeBytes` | `number` | 否 |  |
| `durationSec` | `number \| null` | 否 |  |
| `hasAudio` | `boolean` | 否 |  |
| `ownerId` | `string` | 是 |  |
| `ownerName` | `string \| null` | 否 |  |
| `visibility` | `LibraryVisibility` | 是 |  |
| `state` | `LibraryAssetState` | 是 |  |
| `stateVersion` | `number` | 否 |  |
| `coverUrl` | `string \| null` | 否 |  |
| `url` | `string` | 否 |  |
| `previewUrl` | `string \| null` | 否 |  |
| `streamUrl` | `string \| null` | 否 |  |
| `width` | `number \| null` | 否 |  |
| `height` | `number \| null` | 否 |  |
| `groupIds` | `string[]` | 否 |  |
| `tags` | `LibraryTagOut[]` | 否 |  |
| `trashReason` | `string \| null` | 否 |  |
| `trashExpiresAt` | `string \| null` | 否 |  |
| `textExcerpt` | `string \| null` | 否 |  |
| `textVersion` | `number` | 否 |  |
| `textUrl` | `string \| null` | 否 |  |
| `createdAt` | `string` | 是 |  |
| `updatedAt` | `string \| null` | 否 |  |
| `groups` | `LibraryGroupRef[]` | 否 |  |
| `generation` | `LibraryGenerationOut \| null` | 否 |  |
| `packItems` | `PackItemOut[]` | 否 |  |
| `references` | `string[] \| null` | 否 |  |
| `shareHistory` | `ShareHistoryItem[]` | 否 |  |
| `archive` | `LibraryArchiveOut \| null` | 否 |  |
| `reviewedBy` | `string \| null` | 否 |  |
| `reviewedAt` | `string \| null` | 否 |  |
| `rejectReason` | `string \| null` | 否 |  |
| `trashedFromState` | `string \| null` | 否 |  |

#### `DELETE /api/v1/library/assets/{asset_id}`

**移入回收站（软删，可恢复）**

删除矩阵见设计 §3.4。

已共享返回 409 `SHARED_ASSET_NOT_DELETABLE`，`details.canRevokeAndDelete`
告诉前端要不要把按钮换成「撤销共享并删除」。判定与执行共用同一个条件，
前端不自己算。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |
| `revokeOwnGroupShare` | query | `boolean` | 否 | 组长的窄口：本人所有、仅共享给本组时，一步撤销共享并删除 |
| `reason` | query | `string` | 否 |  |

**响应** `ApiResponse<null>`（前端取 `res.data`）

#### `GET /api/v1/library/assets/{asset_id}`

**资产详情（含标签与生成参数）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**响应** `ApiResponse<LibraryAssetDetailOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `kind` | `LibraryAssetKind` | 是 |  |
| `origin` | `string` | 是 | upload / generated / template_source / imported / pack |
| `mimeType` | `string \| null` | 否 |  |
| `sizeBytes` | `number` | 否 |  |
| `durationSec` | `number \| null` | 否 |  |
| `hasAudio` | `boolean` | 否 |  |
| `ownerId` | `string` | 是 |  |
| `ownerName` | `string \| null` | 否 |  |
| `visibility` | `LibraryVisibility` | 是 |  |
| `state` | `LibraryAssetState` | 是 |  |
| `stateVersion` | `number` | 否 |  |
| `coverUrl` | `string \| null` | 否 |  |
| `url` | `string` | 否 |  |
| `previewUrl` | `string \| null` | 否 |  |
| `streamUrl` | `string \| null` | 否 |  |
| `width` | `number \| null` | 否 |  |
| `height` | `number \| null` | 否 |  |
| `groupIds` | `string[]` | 否 |  |
| `tags` | `LibraryTagOut[]` | 否 |  |
| `trashReason` | `string \| null` | 否 |  |
| `trashExpiresAt` | `string \| null` | 否 |  |
| `textExcerpt` | `string \| null` | 否 |  |
| `textVersion` | `number` | 否 |  |
| `textUrl` | `string \| null` | 否 |  |
| `createdAt` | `string` | 是 |  |
| `updatedAt` | `string \| null` | 否 |  |
| `groups` | `LibraryGroupRef[]` | 否 |  |
| `generation` | `LibraryGenerationOut \| null` | 否 |  |
| `packItems` | `PackItemOut[]` | 否 |  |
| `references` | `string[] \| null` | 否 |  |
| `shareHistory` | `ShareHistoryItem[]` | 否 |  |
| `archive` | `LibraryArchiveOut \| null` | 否 |  |
| `reviewedBy` | `string \| null` | 否 |  |
| `reviewedAt` | `string \| null` | 否 |  |
| `rejectReason` | `string \| null` | 否 |  |
| `trashedFromState` | `string \| null` | 否 |  |

#### `GET /api/v1/library/assets/{asset_id}/file`

**下载或预览原文件**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**响应** `ApiResponse<unknown>`（前端取 `res.data`）

#### `GET /api/v1/library/assets/{asset_id}/preview`

**受控公共预览（视频给派生流）**

旧库把「非本人的公共原片」的 url 改写成 `/assets/{id}/preview`；v2 补同款。

可见性仍由 `load_visible` 判定，这个端点不额外发权限，只负责「视频别给原件」。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**响应** `ApiResponse<unknown>`（前端取 `res.data`）

#### `GET /api/v1/library/assets/{asset_id}/stream`

**浏览器兼容视频预览（派生流，不改原片）**

模板库悬停/播放用的取件口。旧库在 `/assets/{id}/stream`，v2 补在同位置。

`/file` 给的是原件，`/stream` 给的是 H.264 8-bit 派生版，两者不能互相代替：
10-bit 原片 Chrome 解不了，而把原件当「预览」发给别人等于放行整份下载。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**响应** `ApiResponse<unknown>`（前端取 `res.data`）

#### `GET /api/v1/library/assets/{asset_id}/text`

**读取文本正文与当前版本**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**响应** `ApiResponse<TextAssetOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `content` | `string` | 是 |  |
| `version` | `number` | 是 |  |
| `sha256` | `string` | 是 |  |
| `sizeBytes` | `number` | 是 |  |
| `excerpt` | `string` | 是 |  |
| `updatedAt` | `string \| null` | 否 |  |

#### `PUT /api/v1/library/assets/{asset_id}/text`

**在线编辑文本（追加一版，可带 expectedVersion 防覆盖）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**请求体** `TextAssetUpdateIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `content` | `string` | 是 |  |
| `expectedVersion` | `number` | 否 |  |
| `note` | `string` | 否 |  |

**响应** `ApiResponse<TextAssetOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `content` | `string` | 是 |  |
| `version` | `number` | 是 |  |
| `sha256` | `string` | 是 |  |
| `sizeBytes` | `number` | 是 |  |
| `excerpt` | `string` | 是 |  |
| `updatedAt` | `string \| null` | 否 |  |

#### `GET /api/v1/library/assets/{asset_id}/text/raw`

**纯文本取件（text/plain + nosniff）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**响应** `ApiResponse<unknown>`（前端取 `res.data`）

#### `POST /api/v1/library/assets/{asset_id}/text/restore`

**恢复历史版本（写成新的一版，不改历史行）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**请求体** `TextAssetRestoreIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `version` | `number` | 是 |  |
| `expectedVersion` | `number` | 否 |  |

**响应** `ApiResponse<TextAssetOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `content` | `string` | 是 |  |
| `version` | `number` | 是 |  |
| `sha256` | `string` | 是 |  |
| `sizeBytes` | `number` | 是 |  |
| `excerpt` | `string` | 是 |  |
| `updatedAt` | `string \| null` | 否 |  |

#### `GET /api/v1/library/assets/{asset_id}/text/versions`

**文本版本历史（默认不带正文）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**响应** `ApiResponse<TextAssetVersionOut[]>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `[].version` | `number` | 是 |  |
| `[].sha256` | `string` | 是 |  |
| `[].sizeBytes` | `number` | 是 |  |
| `[].excerpt` | `string` | 是 |  |
| `[].note` | `string` | 否 |  |
| `[].createdBy` | `string` | 是 |  |
| `[].createdAt` | `string` | 是 |  |
| `[].content` | `string \| null` | 否 |  |

#### `GET /api/v1/library/assets/{asset_id}/text/versions/{number}`

**读取某一历史版本正文**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |
| `number` | path | `number` | 是 |  |

**响应** `ApiResponse<TextAssetVersionOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `version` | `number` | 是 |  |
| `sha256` | `string` | 是 |  |
| `sizeBytes` | `number` | 是 |  |
| `excerpt` | `string` | 是 |  |
| `note` | `string` | 否 |  |
| `createdBy` | `string` | 是 |  |
| `createdAt` | `string` | 是 |  |
| `content` | `string \| null` | 否 |  |

#### `GET /api/v1/library/assets/{asset_id}/thumbnail`

**读取缩略图**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**响应** `ApiResponse<unknown>`（前端取 `res.data`）

#### `GET /api/v1/library/downloads`

**我最近发起的打包任务**

**响应** `ApiResponse<DownloadJobOut[]>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `[].id` | `string` | 是 |  |
| `[].status` | `string` | 是 |  |
| `[].assetCount` | `number` | 否 |  |
| `[].sizeBytes` | `number` | 否 |  |
| `[].error` | `string` | 否 |  |
| `[].downloadUrl` | `string \| null` | 否 |  |
| `[].expiresAt` | `string \| null` | 否 |  |
| `[].createdAt` | `string` | 是 |  |
| `[].finishedAt` | `string \| null` | 否 |  |
| `[].skipped` | `DownloadSkip[]` | 否 |  |

#### `POST /api/v1/library/downloads`

**发起批量打包（异步，返回任务）**

**可见即可下载**：能看见的 `state=active` 条目都能进包。

草稿与回收站里的 id 不整单失败，而是在 `skipped` 里逐条给原因。
超限（100 项 / 2GB）返回 413，`details` 带 `limit` 与 `actual`。

**请求体** `DownloadIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `ids` | `string[]` | 是 |  |

**响应** `ApiResponse<unknown>`（前端取 `res.data`）

#### `GET /api/v1/library/downloads/{job_id}`

**打包任务状态**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `job_id` | path | `string` | 是 |  |

**响应** `ApiResponse<DownloadJobOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `status` | `string` | 是 |  |
| `assetCount` | `number` | 否 |  |
| `sizeBytes` | `number` | 否 |  |
| `error` | `string` | 否 |  |
| `downloadUrl` | `string \| null` | 否 |  |
| `expiresAt` | `string \| null` | 否 |  |
| `createdAt` | `string` | 是 |  |
| `finishedAt` | `string \| null` | 否 |  |
| `skipped` | `DownloadSkip[]` | 否 |  |

#### `GET /api/v1/library/downloads/{job_id}/file`

**取打包好的 zip**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `job_id` | path | `string` | 是 |  |

**响应** `ApiResponse<unknown>`（前端取 `res.data`）

#### `POST /api/v1/library/drafts/{asset_id}/approve`

**草稿采纳：进入个人素材库**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**请求体** `DraftDecisionIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `reason` | `string` | 否 |  |
| `expectedVersion` | `number` | 否 |  |

**响应** `ApiResponse<LibraryAssetDetailOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `kind` | `LibraryAssetKind` | 是 |  |
| `origin` | `string` | 是 | upload / generated / template_source / imported / pack |
| `mimeType` | `string \| null` | 否 |  |
| `sizeBytes` | `number` | 否 |  |
| `durationSec` | `number \| null` | 否 |  |
| `hasAudio` | `boolean` | 否 |  |
| `ownerId` | `string` | 是 |  |
| `ownerName` | `string \| null` | 否 |  |
| `visibility` | `LibraryVisibility` | 是 |  |
| `state` | `LibraryAssetState` | 是 |  |
| `stateVersion` | `number` | 否 |  |
| `coverUrl` | `string \| null` | 否 |  |
| `url` | `string` | 否 |  |
| `previewUrl` | `string \| null` | 否 |  |
| `streamUrl` | `string \| null` | 否 |  |
| `width` | `number \| null` | 否 |  |
| `height` | `number \| null` | 否 |  |
| `groupIds` | `string[]` | 否 |  |
| `tags` | `LibraryTagOut[]` | 否 |  |
| `trashReason` | `string \| null` | 否 |  |
| `trashExpiresAt` | `string \| null` | 否 |  |
| `textExcerpt` | `string \| null` | 否 |  |
| `textVersion` | `number` | 否 |  |
| `textUrl` | `string \| null` | 否 |  |
| `createdAt` | `string` | 是 |  |
| `updatedAt` | `string \| null` | 否 |  |
| `groups` | `LibraryGroupRef[]` | 否 |  |
| `generation` | `LibraryGenerationOut \| null` | 否 |  |
| `packItems` | `PackItemOut[]` | 否 |  |
| `references` | `string[] \| null` | 否 |  |
| `shareHistory` | `ShareHistoryItem[]` | 否 |  |
| `archive` | `LibraryArchiveOut \| null` | 否 |  |
| `reviewedBy` | `string \| null` | 否 |  |
| `reviewedAt` | `string \| null` | 否 |  |
| `rejectReason` | `string \| null` | 否 |  |
| `trashedFromState` | `string \| null` | 否 |  |

#### `POST /api/v1/library/drafts/{asset_id}/reject`

**草稿驳回：进回收站，可恢复回草稿**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**请求体** `DraftDecisionIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `reason` | `string` | 否 |  |
| `expectedVersion` | `number` | 否 |  |

**响应** `ApiResponse<LibraryAssetDetailOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `kind` | `LibraryAssetKind` | 是 |  |
| `origin` | `string` | 是 | upload / generated / template_source / imported / pack |
| `mimeType` | `string \| null` | 否 |  |
| `sizeBytes` | `number` | 否 |  |
| `durationSec` | `number \| null` | 否 |  |
| `hasAudio` | `boolean` | 否 |  |
| `ownerId` | `string` | 是 |  |
| `ownerName` | `string \| null` | 否 |  |
| `visibility` | `LibraryVisibility` | 是 |  |
| `state` | `LibraryAssetState` | 是 |  |
| `stateVersion` | `number` | 否 |  |
| `coverUrl` | `string \| null` | 否 |  |
| `url` | `string` | 否 |  |
| `previewUrl` | `string \| null` | 否 |  |
| `streamUrl` | `string \| null` | 否 |  |
| `width` | `number \| null` | 否 |  |
| `height` | `number \| null` | 否 |  |
| `groupIds` | `string[]` | 否 |  |
| `tags` | `LibraryTagOut[]` | 否 |  |
| `trashReason` | `string \| null` | 否 |  |
| `trashExpiresAt` | `string \| null` | 否 |  |
| `textExcerpt` | `string \| null` | 否 |  |
| `textVersion` | `number` | 否 |  |
| `textUrl` | `string \| null` | 否 |  |
| `createdAt` | `string` | 是 |  |
| `updatedAt` | `string \| null` | 否 |  |
| `groups` | `LibraryGroupRef[]` | 否 |  |
| `generation` | `LibraryGenerationOut \| null` | 否 |  |
| `packItems` | `PackItemOut[]` | 否 |  |
| `references` | `string[] \| null` | 否 |  |
| `shareHistory` | `ShareHistoryItem[]` | 否 |  |
| `archive` | `LibraryArchiveOut \| null` | 否 |  |
| `reviewedBy` | `string \| null` | 否 |  |
| `reviewedAt` | `string \| null` | 否 |  |
| `rejectReason` | `string \| null` | 否 |  |
| `trashedFromState` | `string \| null` | 否 |  |

#### `GET /api/v1/library/facets`

**资产库分面统计**

给「不加载列表也要显示角标」的页面用（侧栏、首页卡片）。

**响应** `ApiResponse<LibraryFacets>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `all` | `number` | 否 |  |
| `personal` | `number` | 否 |  |
| `group` | `number` | 否 |  |
| `team` | `number` | 否 |  |
| `drafts` | `number` | 否 |  |
| `trash` | `number` | 否 |  |

#### `GET /api/v1/library/groups`

**我的小组（含是否负责人）**

前端用它渲染「小组素材库」的分组筛选与审批入口。

不直接调用 `/identity/groups`：那个接口要 `identity:group:read`，
普通成员没有；而且它返回全站小组，会泄露别人的组织结构。

**响应** `ApiResponse<LibraryGroupOut[]>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `[].id` | `string` | 是 |  |
| `[].name` | `string` | 否 |  |
| `[].isLeader` | `boolean` | 否 |  |

#### `PUT /api/v1/library/groups/{group_id}/leaders`

**设置小组负责人（整体替换）**

负责人表现在只服务删除窄口与 `isLeader` 展示；审批资格改由三个审批权限点
按申请目标范围判定（`_can_approve_target`），本接口仍由全站审批码守护。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `group_id` | path | `string` | 是 |  |

**请求体** `GroupLeadersIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `userIds` | `string[]` | 否 |  |

**响应** `ApiResponse<string[]>`（前端取 `res.data`）

#### `POST /api/v1/library/packs`

**建封装（成员必须是我看得见且在库的）**

封装是一行 `kind=pack` 的资产行 + 有序成员，**不复制成员**。

解散封装不影响任何成员；下载时封装会被展开成成员逐个进包。

**请求体** `PackIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `string` | 是 |  |
| `assetIds` | `string[]` | 否 |  |
| `items` | `PackItemIn[]` | 否 |  |
| `coverAssetId` | `string` | 否 |  |
| `tagIds` | `string[]` | 否 |  |

**响应** `ApiResponse<LibraryAssetDetailOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `kind` | `LibraryAssetKind` | 是 |  |
| `origin` | `string` | 是 | upload / generated / template_source / imported / pack |
| `mimeType` | `string \| null` | 否 |  |
| `sizeBytes` | `number` | 否 |  |
| `durationSec` | `number \| null` | 否 |  |
| `hasAudio` | `boolean` | 否 |  |
| `ownerId` | `string` | 是 |  |
| `ownerName` | `string \| null` | 否 |  |
| `visibility` | `LibraryVisibility` | 是 |  |
| `state` | `LibraryAssetState` | 是 |  |
| `stateVersion` | `number` | 否 |  |
| `coverUrl` | `string \| null` | 否 |  |
| `url` | `string` | 否 |  |
| `previewUrl` | `string \| null` | 否 |  |
| `streamUrl` | `string \| null` | 否 |  |
| `width` | `number \| null` | 否 |  |
| `height` | `number \| null` | 否 |  |
| `groupIds` | `string[]` | 否 |  |
| `tags` | `LibraryTagOut[]` | 否 |  |
| `trashReason` | `string \| null` | 否 |  |
| `trashExpiresAt` | `string \| null` | 否 |  |
| `textExcerpt` | `string \| null` | 否 |  |
| `textVersion` | `number` | 否 |  |
| `textUrl` | `string \| null` | 否 |  |
| `createdAt` | `string` | 是 |  |
| `updatedAt` | `string \| null` | 否 |  |
| `groups` | `LibraryGroupRef[]` | 否 |  |
| `generation` | `LibraryGenerationOut \| null` | 否 |  |
| `packItems` | `PackItemOut[]` | 否 |  |
| `references` | `string[] \| null` | 否 |  |
| `shareHistory` | `ShareHistoryItem[]` | 否 |  |
| `archive` | `LibraryArchiveOut \| null` | 否 |  |
| `reviewedBy` | `string \| null` | 否 |  |
| `reviewedAt` | `string \| null` | 否 |  |
| `rejectReason` | `string \| null` | 否 |  |
| `trashedFromState` | `string \| null` | 否 |  |

#### `GET /api/v1/library/packs/{pack_id}`

**封装详情（含成员）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `pack_id` | path | `string` | 是 |  |

**响应** `ApiResponse<LibraryAssetDetailOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `kind` | `LibraryAssetKind` | 是 |  |
| `origin` | `string` | 是 | upload / generated / template_source / imported / pack |
| `mimeType` | `string \| null` | 否 |  |
| `sizeBytes` | `number` | 否 |  |
| `durationSec` | `number \| null` | 否 |  |
| `hasAudio` | `boolean` | 否 |  |
| `ownerId` | `string` | 是 |  |
| `ownerName` | `string \| null` | 否 |  |
| `visibility` | `LibraryVisibility` | 是 |  |
| `state` | `LibraryAssetState` | 是 |  |
| `stateVersion` | `number` | 否 |  |
| `coverUrl` | `string \| null` | 否 |  |
| `url` | `string` | 否 |  |
| `previewUrl` | `string \| null` | 否 |  |
| `streamUrl` | `string \| null` | 否 |  |
| `width` | `number \| null` | 否 |  |
| `height` | `number \| null` | 否 |  |
| `groupIds` | `string[]` | 否 |  |
| `tags` | `LibraryTagOut[]` | 否 |  |
| `trashReason` | `string \| null` | 否 |  |
| `trashExpiresAt` | `string \| null` | 否 |  |
| `textExcerpt` | `string \| null` | 否 |  |
| `textVersion` | `number` | 否 |  |
| `textUrl` | `string \| null` | 否 |  |
| `createdAt` | `string` | 是 |  |
| `updatedAt` | `string \| null` | 否 |  |
| `groups` | `LibraryGroupRef[]` | 否 |  |
| `generation` | `LibraryGenerationOut \| null` | 否 |  |
| `packItems` | `PackItemOut[]` | 否 |  |
| `references` | `string[] \| null` | 否 |  |
| `shareHistory` | `ShareHistoryItem[]` | 否 |  |
| `archive` | `LibraryArchiveOut \| null` | 否 |  |
| `reviewedBy` | `string \| null` | 否 |  |
| `reviewedAt` | `string \| null` | 否 |  |
| `rejectReason` | `string \| null` | 否 |  |
| `trashedFromState` | `string \| null` | 否 |  |

#### `PATCH /api/v1/library/packs/{pack_id}`

**改封装：改名 / 整体替换成员 / 换封面 / 换标签**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `pack_id` | path | `string` | 是 |  |

**请求体** `PackPatchIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `string \| null` | 否 |  |
| `items` | `PackItemIn[] \| null` | 否 |  |
| `coverAssetId` | `string \| null` | 否 |  |
| `tagIds` | `string[] \| null` | 否 |  |
| `expectedVersion` | `number` | 否 |  |

**响应** `ApiResponse<LibraryAssetDetailOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `kind` | `LibraryAssetKind` | 是 |  |
| `origin` | `string` | 是 | upload / generated / template_source / imported / pack |
| `mimeType` | `string \| null` | 否 |  |
| `sizeBytes` | `number` | 否 |  |
| `durationSec` | `number \| null` | 否 |  |
| `hasAudio` | `boolean` | 否 |  |
| `ownerId` | `string` | 是 |  |
| `ownerName` | `string \| null` | 否 |  |
| `visibility` | `LibraryVisibility` | 是 |  |
| `state` | `LibraryAssetState` | 是 |  |
| `stateVersion` | `number` | 否 |  |
| `coverUrl` | `string \| null` | 否 |  |
| `url` | `string` | 否 |  |
| `previewUrl` | `string \| null` | 否 |  |
| `streamUrl` | `string \| null` | 否 |  |
| `width` | `number \| null` | 否 |  |
| `height` | `number \| null` | 否 |  |
| `groupIds` | `string[]` | 否 |  |
| `tags` | `LibraryTagOut[]` | 否 |  |
| `trashReason` | `string \| null` | 否 |  |
| `trashExpiresAt` | `string \| null` | 否 |  |
| `textExcerpt` | `string \| null` | 否 |  |
| `textVersion` | `number` | 否 |  |
| `textUrl` | `string \| null` | 否 |  |
| `createdAt` | `string` | 是 |  |
| `updatedAt` | `string \| null` | 否 |  |
| `groups` | `LibraryGroupRef[]` | 否 |  |
| `generation` | `LibraryGenerationOut \| null` | 否 |  |
| `packItems` | `PackItemOut[]` | 否 |  |
| `references` | `string[] \| null` | 否 |  |
| `shareHistory` | `ShareHistoryItem[]` | 否 |  |
| `archive` | `LibraryArchiveOut \| null` | 否 |  |
| `reviewedBy` | `string \| null` | 否 |  |
| `reviewedAt` | `string \| null` | 否 |  |
| `rejectReason` | `string \| null` | 否 |  |
| `trashedFromState` | `string \| null` | 否 |  |

#### `DELETE /api/v1/library/packs/{pack_id}`

**解散封装（进回收站，成员不受影响）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `pack_id` | path | `string` | 是 |  |

**响应** `ApiResponse<null>`（前端取 `res.data`）

#### `GET /api/v1/library/selection`

**选择器：只返回库内资产**

生成页 / 工作台挑素材用。

**硬过滤 `state=active`**：草稿与回收站永远不出现在选择器里。这不是一个
可选的筛选参数——调用方忘了传就会把用户删掉的东西塞进生成任务。

`library` 与列表同一个枚举、同一份判定（`access.tab_clause`）：模板工作台
的「我的原片 / 公共原片」就是 `personal` / `team`。**不新造一个
`scope=mine|public` 参数**——那等于给可见性写第二套定义，两套一漂移就是
「列表里没有、选择器里有」。默认 `all` 与切换前的行为完全一致。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `kind` | query | `LibraryAssetKind | null` | 否 |  |
| `tagIds` | query | `string[] | null` | 否 |  |
| `keyword` | query | `string` | 否 |  |
| `includePacks` | query | `boolean` | 否 |  |
| `origin` | query | `LibraryAssetOrigin[] | null` | 否 |  |
| `library` | query | `LibraryTab` | 否 |  |
| `page` | query | `number` | 否 | 页码，从 1 开始 |
| `pageSize` | query | `number` | 否 | 每页条数 |

**响应** `ApiResponse<LibrarySelectionPage>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `LibraryAssetOut[]` | 否 |  |
| `total` | `number` | 否 |  |
| `page` | `number` | 否 |  |
| `pageSize` | `number` | 否 |  |

#### `POST /api/v1/library/share-requests`

**发起共享 / 撤销共享申请**

`action=revoke` 必须带 `reason`（400 `REVOKE_REASON_REQUIRED`）。

申请人自己就是**该目标**的审批人时直接通过并记自审（全站码
`library:share:approve`，或目标对应的 `library:share:approve:team` /
`library:share:approve:group`），否则会出现「自己申请、等自己批」的死锁。

**请求体** `ShareRequestIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `assetId` | `string` | 是 |  |
| `action` | `ShareAction` | 否 |  |
| `target` | `ShareTarget` | 否 |  |
| `groupId` | `string` | 否 |  |
| `reason` | `string` | 否 |  |
| `expectedVersion` | `number` | 否 |  |

**响应** `ApiResponse<ShareRequestOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `assetId` | `string` | 是 |  |
| `assetName` | `string` | 否 |  |
| `applicantId` | `string` | 是 |  |
| `applicantName` | `string \| null` | 否 |  |
| `action` | `ShareAction` | 是 |  |
| `target` | `ShareTarget` | 是 |  |
| `groupId` | `string` | 否 |  |
| `groupName` | `string \| null` | 否 |  |
| `status` | `string` | 是 |  |
| `reviewerId` | `string \| null` | 否 |  |
| `note` | `string` | 否 |  |
| `reason` | `string` | 否 |  |
| `decidedAt` | `string \| null` | 否 |  |
| `createdAt` | `string` | 是 |  |

#### `GET /api/v1/library/share-requests`

**申请单列表（我提交的 / 待我审核）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `scope` | query | `string` | 否 |  |

**响应** `ApiResponse<ShareRequestOut[]>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `[].id` | `string` | 是 |  |
| `[].assetId` | `string` | 是 |  |
| `[].assetName` | `string` | 否 |  |
| `[].applicantId` | `string` | 是 |  |
| `[].applicantName` | `string \| null` | 否 |  |
| `[].action` | `ShareAction` | 是 |  |
| `[].target` | `ShareTarget` | 是 |  |
| `[].groupId` | `string` | 否 |  |
| `[].groupName` | `string \| null` | 否 |  |
| `[].status` | `string` | 是 |  |
| `[].reviewerId` | `string \| null` | 否 |  |
| `[].note` | `string` | 否 |  |
| `[].reason` | `string` | 否 |  |
| `[].decidedAt` | `string \| null` | 否 |  |
| `[].createdAt` | `string` | 是 |  |

#### `POST /api/v1/library/share-requests/{request_id}/approve`

**通过申请**

审批资格按申请目标范围在服务层判（全站 / 团队 / 所在小组），路由只要求
`library:read`；组长身份本身不再给审批权。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `request_id` | path | `string` | 是 |  |

**请求体** `ShareDecisionIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `note` | `string` | 否 |  |
| `expectedVersion` | `number` | 否 |  |

**响应** `ApiResponse<ShareRequestOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `assetId` | `string` | 是 |  |
| `assetName` | `string` | 否 |  |
| `applicantId` | `string` | 是 |  |
| `applicantName` | `string \| null` | 否 |  |
| `action` | `ShareAction` | 是 |  |
| `target` | `ShareTarget` | 是 |  |
| `groupId` | `string` | 否 |  |
| `groupName` | `string \| null` | 否 |  |
| `status` | `string` | 是 |  |
| `reviewerId` | `string \| null` | 否 |  |
| `note` | `string` | 否 |  |
| `reason` | `string` | 否 |  |
| `decidedAt` | `string \| null` | 否 |  |
| `createdAt` | `string` | 是 |  |

#### `POST /api/v1/library/share-requests/{request_id}/cancel`

**撤回自己提交的申请**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `request_id` | path | `string` | 是 |  |

**响应** `ApiResponse<ShareRequestOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `assetId` | `string` | 是 |  |
| `assetName` | `string` | 否 |  |
| `applicantId` | `string` | 是 |  |
| `applicantName` | `string \| null` | 否 |  |
| `action` | `ShareAction` | 是 |  |
| `target` | `ShareTarget` | 是 |  |
| `groupId` | `string` | 否 |  |
| `groupName` | `string \| null` | 否 |  |
| `status` | `string` | 是 |  |
| `reviewerId` | `string \| null` | 否 |  |
| `note` | `string` | 否 |  |
| `reason` | `string` | 否 |  |
| `decidedAt` | `string \| null` | 否 |  |
| `createdAt` | `string` | 是 |  |

#### `POST /api/v1/library/share-requests/{request_id}/reject`

**驳回申请**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `request_id` | path | `string` | 是 |  |

**请求体** `ShareDecisionIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `note` | `string` | 否 |  |
| `expectedVersion` | `number` | 否 |  |

**响应** `ApiResponse<ShareRequestOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `assetId` | `string` | 是 |  |
| `assetName` | `string` | 否 |  |
| `applicantId` | `string` | 是 |  |
| `applicantName` | `string \| null` | 否 |  |
| `action` | `ShareAction` | 是 |  |
| `target` | `ShareTarget` | 是 |  |
| `groupId` | `string` | 否 |  |
| `groupName` | `string \| null` | 否 |  |
| `status` | `string` | 是 |  |
| `reviewerId` | `string \| null` | 否 |  |
| `note` | `string` | 否 |  |
| `reason` | `string` | 否 |  |
| `decidedAt` | `string \| null` | 否 |  |
| `createdAt` | `string` | 是 |  |

#### `POST /api/v1/library/source-videos`

**上传模板库参考视频（只收视频）**

等价于旧 `POST /assets/upload?category=source`，只是路径与来源不同。

模板库前端切换时只改 `archive.api.ts` 里一个 URL 常量，界面一行不动。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `filename` | query | `string` | 是 |  |
| `sourceKey` | query | `string | null` | 否 |  |

**响应** `ApiResponse<LibraryAssetOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `kind` | `LibraryAssetKind` | 是 |  |
| `origin` | `string` | 是 | upload / generated / template_source / imported / pack |
| `mimeType` | `string \| null` | 否 |  |
| `sizeBytes` | `number` | 否 |  |
| `durationSec` | `number \| null` | 否 |  |
| `hasAudio` | `boolean` | 否 |  |
| `ownerId` | `string` | 是 |  |
| `ownerName` | `string \| null` | 否 |  |
| `visibility` | `LibraryVisibility` | 是 |  |
| `state` | `LibraryAssetState` | 是 |  |
| `stateVersion` | `number` | 否 |  |
| `coverUrl` | `string \| null` | 否 |  |
| `url` | `string` | 否 |  |
| `previewUrl` | `string \| null` | 否 |  |
| `streamUrl` | `string \| null` | 否 |  |
| `width` | `number \| null` | 否 |  |
| `height` | `number \| null` | 否 |  |
| `groupIds` | `string[]` | 否 |  |
| `tags` | `LibraryTagOut[]` | 否 |  |
| `trashReason` | `string \| null` | 否 |  |
| `trashExpiresAt` | `string \| null` | 否 |  |
| `textExcerpt` | `string \| null` | 否 |  |
| `textVersion` | `number` | 否 |  |
| `textUrl` | `string \| null` | 否 |  |
| `createdAt` | `string` | 是 |  |
| `updatedAt` | `string \| null` | 否 |  |

#### `GET /api/v1/library/tags`

**标签列表（两级平铺，含当前入口的素材数，按添加时间排序）**

计数按 `state` + `library` + `kind` 与列表同口径。

`state=draft|trash` 时忽略 `library`；`kind` 与列表的类别筛选同口径
（悬浮栏按类别看库时，胶囊上的数字必须也是「这一类里有几条」）。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `library` | query | `LibraryTab` | 否 |  |
| `state` | query | `LibraryAssetState` | 否 |  |
| `kind` | query | `LibraryAssetKind | null` | 否 |  |

**响应** `ApiResponse<LibraryTagStatOut[]>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `[].id` | `string` | 是 |  |
| `[].name` | `string` | 是 |  |
| `[].parentId` | `string \| null` | 否 |  |
| `[].parentName` | `string \| null` | 否 |  |
| `[].depth` | `number` | 否 |  |
| `[].assetCount` | `number` | 否 |  |
| `[].childCount` | `number` | 否 |  |
| `[].createdAt` | `string` | 是 |  |

#### `POST /api/v1/library/tags`

**新建标签（同名返回已有那条）**

`parentId` 给一级标签的 id 就建成二级；留空建成一级。

**请求体** `TagIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `string` | 是 |  |
| `parentId` | `string \| null` | 否 |  |

**响应** `ApiResponse<LibraryTagOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `parentId` | `string \| null` | 否 |  |
| `parentName` | `string \| null` | 否 |  |
| `depth` | `number` | 否 |  |

#### `PATCH /api/v1/library/tags/{tag_id}`

**重命名标签（不做隐式合并）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `tag_id` | path | `string` | 是 |  |

**请求体** `TagIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `string` | 是 |  |
| `parentId` | `string \| null` | 否 |  |

**响应** `ApiResponse<LibraryTagOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `parentId` | `string \| null` | 否 |  |
| `parentName` | `string \| null` | 否 |  |
| `depth` | `number` | 否 |  |

#### `DELETE /api/v1/library/tags/{tag_id}`

**删除标签（旗下二级提升为一级，资产不受影响）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `tag_id` | path | `string` | 是 |  |

**响应** `ApiResponse<null>`（前端取 `res.data`）

#### `PATCH /api/v1/library/tags/{tag_id}/parent`

**移动标签层级（parentId 为空则提升为一级）**

固定两级：父必须是一级、自己不能带子标签、不能指向自己。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `tag_id` | path | `string` | 是 |  |

**请求体** `TagParentIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `parentId` | `string \| null` | 否 |  |

**响应** `ApiResponse<LibraryTagOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `parentId` | `string \| null` | 否 |  |
| `parentName` | `string \| null` | 否 |  |
| `depth` | `number` | 否 |  |

#### `POST /api/v1/library/text-assets`

**站内新建文本资产（直接写正文，不传文件）**

`.txt` / `.md`、UTF-8、≤1MB。正文按纯文本保存，Markdown 不转 HTML。

**请求体** `TextAssetIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `string` | 是 |  |
| `content` | `string` | 是 |  |
| `extension` | `string` | 否 |  |
| `tagIds` | `string[]` | 否 |  |

**响应** `ApiResponse<TextAssetOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `content` | `string` | 是 |  |
| `version` | `number` | 是 |  |
| `sha256` | `string` | 是 |  |
| `sizeBytes` | `number` | 是 |  |
| `excerpt` | `string` | 是 |  |
| `updatedAt` | `string \| null` | 否 |  |

#### `DELETE /api/v1/library/trash/{asset_id}`

**彻底删除（不可逆，先过引用检查）**

被封装 / 下载任务引用时返回 409 `TRASH_ENTRY_REFERENCED` 并列出引用方。

不做批量彻底删除：不可逆的动作用「勾选十条点一下」来触发太危险。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**响应** `ApiResponse<null>`（前端取 `res.data`）

#### `POST /api/v1/library/trash/{asset_id}/restore`

**从回收站恢复（强制回到个人库，不恢复共享）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `asset_id` | path | `string` | 是 |  |

**响应** `ApiResponse<LibraryAssetDetailOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `kind` | `LibraryAssetKind` | 是 |  |
| `origin` | `string` | 是 | upload / generated / template_source / imported / pack |
| `mimeType` | `string \| null` | 否 |  |
| `sizeBytes` | `number` | 否 |  |
| `durationSec` | `number \| null` | 否 |  |
| `hasAudio` | `boolean` | 否 |  |
| `ownerId` | `string` | 是 |  |
| `ownerName` | `string \| null` | 否 |  |
| `visibility` | `LibraryVisibility` | 是 |  |
| `state` | `LibraryAssetState` | 是 |  |
| `stateVersion` | `number` | 否 |  |
| `coverUrl` | `string \| null` | 否 |  |
| `url` | `string` | 否 |  |
| `previewUrl` | `string \| null` | 否 |  |
| `streamUrl` | `string \| null` | 否 |  |
| `width` | `number \| null` | 否 |  |
| `height` | `number \| null` | 否 |  |
| `groupIds` | `string[]` | 否 |  |
| `tags` | `LibraryTagOut[]` | 否 |  |
| `trashReason` | `string \| null` | 否 |  |
| `trashExpiresAt` | `string \| null` | 否 |  |
| `textExcerpt` | `string \| null` | 否 |  |
| `textVersion` | `number` | 否 |  |
| `textUrl` | `string \| null` | 否 |  |
| `createdAt` | `string` | 是 |  |
| `updatedAt` | `string \| null` | 否 |  |
| `groups` | `LibraryGroupRef[]` | 否 |  |
| `generation` | `LibraryGenerationOut \| null` | 否 |  |
| `packItems` | `PackItemOut[]` | 否 |  |
| `references` | `string[] \| null` | 否 |  |
| `shareHistory` | `ShareHistoryItem[]` | 否 |  |
| `archive` | `LibraryArchiveOut \| null` | 否 |  |
| `reviewedBy` | `string \| null` | 否 |  |
| `reviewedAt` | `string \| null` | 否 |  |
| `rejectReason` | `string \| null` | 否 |  |
| `trashedFromState` | `string \| null` | 否 |  |

### 4.11 模型驱动（6 个）

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| `GET` | `/api/v1/model-driver/drivers` | 已自动载入的模型驱动清单 | 🔒 |
| `GET` | `/api/v1/model-driver/models` | 统一生成接口可用的模型目录 | 🔒 |
| `POST` | `/api/v1/model-driver/tasks` | 提交一次统一生成任务 | 🔒 |
| `GET` | `/api/v1/model-driver/tasks/{task_id}` | 查询统一生成任务 | 🔒 |
| `POST` | `/api/v1/model-driver/tasks/{task_id}/cancel` | 取消生成任务 | 🔒 |
| `POST` | `/api/v1/model-driver/tasks/{task_id}/retry` | 人工重试失败的生成任务 | 🔒 |

#### `GET /api/v1/model-driver/drivers`

**已自动载入的模型驱动清单**

**响应** `ApiResponse<DriverListOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `DriverOut[]` | 否 |  |
| `total` | `number` | 否 |  |
| `errors` | `string[]` | 否 |  |

#### `GET /api/v1/model-driver/models`

**统一生成接口可用的模型目录**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `capability` | query | `string` | 否 | 能力筛选：video / image / audio |

**响应** `ApiResponse<core__response__ApiResponse_ModelListOut___2>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `code` | `number` | 否 | 0 表示成功，其余为业务错误码 |
| `message` | `string` | 否 |  |
| `data` | `modules__model_driver__schema__ModelListOut \| null` | 否 |  |

#### `POST /api/v1/model-driver/tasks`

**提交一次统一生成任务**

**请求体** `SubmitIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `model` | `string` | 是 |  |
| `prompt` | `string` | 是 |  |
| `name` | `string` | 否 |  |
| `params` | `Record<string, unknown>` | 否 |  |
| `referenceUrls` | `string[]` | 否 |  |
| `source` | `string` | 否 |  |

**响应** `ApiResponse<core__response__ApiResponse_TaskOut___1>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `code` | `number` | 否 | 0 表示成功，其余为业务错误码 |
| `message` | `string` | 否 |  |
| `data` | `modules__model_driver__schema__TaskOut \| null` | 否 |  |

#### `GET /api/v1/model-driver/tasks/{task_id}`

**查询统一生成任务**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `task_id` | path | `string` | 是 |  |

**响应** `ApiResponse<core__response__ApiResponse_TaskOut___1>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `code` | `number` | 否 | 0 表示成功，其余为业务错误码 |
| `message` | `string` | 否 |  |
| `data` | `modules__model_driver__schema__TaskOut \| null` | 否 |  |

#### `POST /api/v1/model-driver/tasks/{task_id}/cancel`

**取消生成任务**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `task_id` | path | `string` | 是 |  |

**响应** `ApiResponse<core__response__ApiResponse_TaskOut___1>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `code` | `number` | 否 | 0 表示成功，其余为业务错误码 |
| `message` | `string` | 否 |  |
| `data` | `modules__model_driver__schema__TaskOut \| null` | 否 |  |

#### `POST /api/v1/model-driver/tasks/{task_id}/retry`

**人工重试失败的生成任务**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `task_id` | path | `string` | 是 |  |
| `confirmCharged` | query | `boolean` | 否 | 确认供应商可能已计费 |

**响应** `ApiResponse<core__response__ApiResponse_TaskOut___1>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `code` | `number` | 否 | 0 表示成功，其余为业务错误码 |
| `message` | `string` | 否 |  |
| `data` | `modules__model_driver__schema__TaskOut \| null` | 否 |  |

### 4.12 模板封装（26 个）

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| `POST` | `/api/v1/template-manifests` | 保存 manifest 草稿 | 🔒 |
| `GET` | `/api/v1/template-manifests` | manifest 配置列表 | 🔒 |
| `POST` | `/api/v1/template-manifests/validate` | 预检 manifest 配置（不落库） | 🔒 |
| `GET` | `/api/v1/template-manifests/{key}` | manifest 配置详情 | 🔒 |
| `POST` | `/api/v1/template-manifests/{key}/disable` | 停用 manifest 配置 | 🔒 |
| `POST` | `/api/v1/template-manifests/{key}/publish` | 发布 manifest 配置 | 🔒 |
| `POST` | `/api/v1/template-specs` | 保存草稿（同内容幂等，revision 冲突 409） | 🔒 |
| `GET` | `/api/v1/template-specs` | 本人可见的规格列表 | 🔒 |
| `GET` | `/api/v1/template-specs/published` | 模板库：已发布的规格卡片 | 🔒 |
| `POST` | `/api/v1/template-specs/validate` | 预检模板规格（不落库） | 🔒 |
| `GET` | `/api/v1/template-specs/{key}` | 规格详情（含草稿与最近发布） | 🔒 |
| `POST` | `/api/v1/template-specs/{key}/compile` | 填表结果 → 执行请求体（纯映射，不落库） | 🔒 |
| `POST` | `/api/v1/template-specs/{key}/disable` | 下架（历史版本保留） | 🔒 |
| `POST` | `/api/v1/template-specs/{key}/publish` | 发布：把当前草稿冻结成新版本 | 🔒 |
| `GET` | `/api/v1/templates/replacement-packs` | 本人封装草稿和历史包 | 🔒 |
| `GET` | `/api/v1/templates/replacement-packs/available` | 全工作台已发布对象 | 🔒 |
| `PUT` | `/api/v1/templates/replacement-packs/draft` | 保存封装草稿（请求标识及版本防重） | 🔒 |
| `POST` | `/api/v1/templates/replacement-packs/{pack_id}/disable` | 停用已发布封装对象 | 🔒 |
| `GET` | `/api/v1/templates/replacement-packs/{pack_id}/preview/{asset_id}` | 读取授权发布版本的包内预览 | 🔒 |
| `POST` | `/api/v1/templates/replacement-packs/{pack_id}/publish` | 发布不可变封装版本 | 🔒 |
| `GET` | `/api/v1/templates/video-templates` | 视频模板列表（固定蓝本优先） | 🔒 |
| `POST` | `/api/v1/templates/video-templates` | 封装一条视频模板 | 🔒 |
| `GET` | `/api/v1/templates/video-templates/{template_id}` | 视频模板详情（含提示词与媒体顺序） | 🔒 |
| `POST` | `/api/v1/templates/video-templates/{template_id}/attach-source` | 绑定样片原片资产 | 🔒 |
| `POST` | `/api/v1/templates/video-templates/{template_id}/pin` | 设为固定蓝本（全库仅一个） | 🔒 |
| `PATCH` | `/api/v1/templates/video-templates/{template_id}/prompt` | 保存固定提示词与跨封装图片引用 | 🔒 |

#### `POST /api/v1/template-manifests`

**保存 manifest 草稿**

幂等：同 key 同配置返回原行；已发布配置改内容 409。

**请求体** `ManifestDraftIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `key` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `description` | `string` | 否 |  |
| `video` | `ManifestVideoIn` | 是 |  |
| `packs` | `ManifestPackIn[]` | 否 |  |
| `functions` | `ManifestFunctionIn[]` | 是 |  |

**响应** `ApiResponse<ManifestOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `key` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `description` | `string` | 是 |  |
| `ownerId` | `string` | 是 |  |
| `video` | `ManifestVideoIn` | 是 |  |
| `status` | `string` | 是 |  |
| `version` | `number` | 是 |  |
| `revision` | `number` | 是 |  |
| `fingerprint` | `string` | 是 |  |
| `publishedAt` | `string \| null` | 否 |  |
| `createdAt` | `string` | 是 |  |
| `updatedAt` | `string` | 是 |  |
| `packs` | `ManifestPackIn[]` | 否 |  |
| `functions` | `ManifestFunctionIn[]` | 否 |  |

#### `GET /api/v1/template-manifests`

**manifest 配置列表**

别人的草稿不进列表；传 `mineOnly` 只看自己的全部（含草稿）。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `mineOnly` | query | `boolean` | 否 |  |
| `status` | query | `string` | 否 |  |

**响应** `ApiResponse<ManifestListOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `ManifestOut[]` | 否 |  |
| `total` | `number` | 否 |  |

#### `POST /api/v1/template-manifests/validate`

**预检 manifest 配置（不落库）**

结构、用途、Mixed 编号、素材可读性的全套预检；返回幂等指纹。

**请求体** `ManifestDraftIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `key` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `description` | `string` | 否 |  |
| `video` | `ManifestVideoIn` | 是 |  |
| `packs` | `ManifestPackIn[]` | 否 |  |
| `functions` | `ManifestFunctionIn[]` | 是 |  |

**响应** `ApiResponse<ManifestValidateOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `valid` | `boolean` | 否 |  |
| `fingerprint` | `string` | 是 |  |

#### `GET /api/v1/template-manifests/{key}`

**manifest 配置详情**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `key` | path | `string` | 是 |  |

**响应** `ApiResponse<ManifestOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `key` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `description` | `string` | 是 |  |
| `ownerId` | `string` | 是 |  |
| `video` | `ManifestVideoIn` | 是 |  |
| `status` | `string` | 是 |  |
| `version` | `number` | 是 |  |
| `revision` | `number` | 是 |  |
| `fingerprint` | `string` | 是 |  |
| `publishedAt` | `string \| null` | 否 |  |
| `createdAt` | `string` | 是 |  |
| `updatedAt` | `string` | 是 |  |
| `packs` | `ManifestPackIn[]` | 否 |  |
| `functions` | `ManifestFunctionIn[]` | 否 |  |

#### `POST /api/v1/template-manifests/{key}/disable`

**停用 manifest 配置**

停用不是删除：模板是资产，历史版本还要能回放。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `key` | path | `string` | 是 |  |

**响应** `ApiResponse<ManifestOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `key` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `description` | `string` | 是 |  |
| `ownerId` | `string` | 是 |  |
| `video` | `ManifestVideoIn` | 是 |  |
| `status` | `string` | 是 |  |
| `version` | `number` | 是 |  |
| `revision` | `number` | 是 |  |
| `fingerprint` | `string` | 是 |  |
| `publishedAt` | `string \| null` | 否 |  |
| `createdAt` | `string` | 是 |  |
| `updatedAt` | `string` | 是 |  |
| `packs` | `ManifestPackIn[]` | 否 |  |
| `functions` | `ManifestFunctionIn[]` | 否 |  |

#### `POST /api/v1/template-manifests/{key}/publish`

**发布 manifest 配置**

状态流转 + 版本号 +1；返回每张工作台卡片的 `workbenchPath`。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `key` | path | `string` | 是 |  |

**响应** `ApiResponse<ManifestPublishOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `manifest` | `ManifestOut` | 是 |  |
| `version` | `number` | 是 |  |
| `templates` | `PublishedTemplateOut[]` | 否 |  |

#### `POST /api/v1/template-specs`

**保存草稿（同内容幂等，revision 冲突 409）**

**请求体** `TemplateSpecIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `key` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `description` | `string` | 否 |  |
| `model` | `string` | 是 |  |
| `params` | `Record<string, unknown>` | 否 |  |
| `prompt` | `SpecPromptIn` | 是 |  |
| `slots` | `SpecSlotIn[]` | 否 |  |
| `attributes` | `Record<string, string>` | 否 |  |
| `revision` | `number \| null` | 否 |  |

**响应** `ApiResponse<TemplateSpecOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `key` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `description` | `string` | 是 |  |
| `ownerId` | `string` | 是 |  |
| `status` | `"draft" \| "published" \| "disabled"` | 是 |  |
| `revision` | `number` | 是 |  |
| `fingerprint` | `string` | 是 |  |
| `currentVersion` | `number` | 是 |  |
| `publishedAt` | `string \| null` | 否 |  |
| `disabledAt` | `string \| null` | 否 |  |
| `createdAt` | `string` | 是 |  |
| `updatedAt` | `string` | 是 |  |
| `draft` | `TemplateSpecIn \| null` | 否 |  |
| `latest` | `SpecVersionOut \| null` | 否 |  |

#### `GET /api/v1/template-specs`

**本人可见的规格列表**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `status` | query | `string` | 否 |  |
| `keyword` | query | `string` | 否 |  |
| `page` | query | `number` | 否 |  |
| `pageSize` | query | `number` | 否 |  |

**响应** `ApiResponse<TemplateSpecListOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `TemplateSpecOut[]` | 否 |  |
| `total` | `number` | 否 |  |

#### `GET /api/v1/template-specs/published`

**模板库：已发布的规格卡片**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `keyword` | query | `string` | 否 |  |
| `page` | query | `number` | 否 |  |
| `pageSize` | query | `number` | 否 |  |

**响应** `ApiResponse<TemplateSpecListOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `TemplateSpecOut[]` | 否 |  |
| `total` | `number` | 否 |  |

#### `POST /api/v1/template-specs/validate`

**预检模板规格（不落库）**

**请求体** `TemplateSpecIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `key` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `description` | `string` | 否 |  |
| `model` | `string` | 是 |  |
| `params` | `Record<string, unknown>` | 否 |  |
| `prompt` | `SpecPromptIn` | 是 |  |
| `slots` | `SpecSlotIn[]` | 否 |  |
| `attributes` | `Record<string, string>` | 否 |  |
| `revision` | `number \| null` | 否 |  |

**响应** `ApiResponse<SpecValidateOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `valid` | `boolean` | 否 |  |
| `fingerprint` | `string` | 是 |  |

#### `GET /api/v1/template-specs/{key}`

**规格详情（含草稿与最近发布）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `key` | path | `string` | 是 |  |

**响应** `ApiResponse<TemplateSpecOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `key` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `description` | `string` | 是 |  |
| `ownerId` | `string` | 是 |  |
| `status` | `"draft" \| "published" \| "disabled"` | 是 |  |
| `revision` | `number` | 是 |  |
| `fingerprint` | `string` | 是 |  |
| `currentVersion` | `number` | 是 |  |
| `publishedAt` | `string \| null` | 否 |  |
| `disabledAt` | `string \| null` | 否 |  |
| `createdAt` | `string` | 是 |  |
| `updatedAt` | `string` | 是 |  |
| `draft` | `TemplateSpecIn \| null` | 否 |  |
| `latest` | `SpecVersionOut \| null` | 否 |  |

#### `POST /api/v1/template-specs/{key}/compile`

**填表结果 → 执行请求体（纯映射，不落库）**

给模板库用：拿到出参后由前端直接 `POST /tasks`。

挂 `rate_limit` 是因为路由策略按 HTTP 方法扫写路由（POST 一律要限流），
而这条虽然无副作用，形状上是 POST。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `key` | path | `string` | 是 |  |

**请求体** `SpecCompileIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `version` | `number \| null` | 否 |  |
| `slotValues` | `Record<string, string[]>` | 否 |  |

**响应** `ApiResponse<SpecCompileOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `version` | `number` | 是 |  |
| `model` | `string` | 是 |  |
| `prompt` | `string` | 是 |  |
| `params` | `Record<string, unknown>` | 是 |  |
| `referenceLibraryIds` | `string[]` | 否 |  |
| `textLibraryIds` | `string[]` | 否 |  |
| `slots` | `CompiledSlotOut[]` | 否 |  |

#### `POST /api/v1/template-specs/{key}/disable`

**下架（历史版本保留）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `key` | path | `string` | 是 |  |

**响应** `ApiResponse<TemplateSpecOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `key` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `description` | `string` | 是 |  |
| `ownerId` | `string` | 是 |  |
| `status` | `"draft" \| "published" \| "disabled"` | 是 |  |
| `revision` | `number` | 是 |  |
| `fingerprint` | `string` | 是 |  |
| `currentVersion` | `number` | 是 |  |
| `publishedAt` | `string \| null` | 否 |  |
| `disabledAt` | `string \| null` | 否 |  |
| `createdAt` | `string` | 是 |  |
| `updatedAt` | `string` | 是 |  |
| `draft` | `TemplateSpecIn \| null` | 否 |  |
| `latest` | `SpecVersionOut \| null` | 否 |  |

#### `POST /api/v1/template-specs/{key}/publish`

**发布：把当前草稿冻结成新版本**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `key` | path | `string` | 是 |  |

**响应** `ApiResponse<SpecPublishOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `version` | `number` | 是 |  |
| `spec` | `TemplateSpecOut` | 是 |  |

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
| `referenceLibraryIds` | `string[]` | 否 | 封装用的参考图 id，按顺序 |
| `coverLibraryId` | `string \| null` | 否 | 封面的 asset id；必须出现在 referenceLibraryIds 里 |
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
| `referenceNames` | `string[]` | 否 |  |
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
| `referenceNames` | `string[]` | 否 |  |
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
| `referenceNames` | `string[]` | 否 |  |
| `bindings` | `PackBinding[]` | 否 |  |
| `requiredRoles` | `string[]` | 否 |  |
| `supportedScopes` | `string[]` | 否 |  |
| `revision` | `number` | 否 |  |
| `status` | `"draft" \| "published" \| "disabled"` | 否 |  |
| `gaps` | `string[]` | 否 |  |

#### `GET /api/v1/templates/video-templates`

**视频模板列表（固定蓝本优先）**

**响应** `ApiResponse<VideoTemplateListOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `VideoTemplateOut[]` | 否 |  |
| `total` | `number` | 否 |  |
| `pinnedId` | `string \| null` | 否 | 当前固定蓝本的 id |

#### `POST /api/v1/templates/video-templates`

**封装一条视频模板**

**请求体** `VideoTemplateDraftIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `string` | 是 | 模板名称 |
| `templateKey` | `string` | 否 | 稳定标识：同键同配置返回原模板，同键异配置409；留空自动生成。固定蓝本状态独立管理 |
| `description` | `string` | 否 | 模板说明 |
| `prompt` | `string` | 是 | 固定提示词原文，含 {{Mixed N}} 占位符 |
| `sourceVideoName` | `string` | 否 | 样片文件名 |
| `sourceVideoAssetId` | `string \| null` | 否 | 样片在资产库中的 asset id |
| `sourceVideoPath` | `string` | 否 | 样片本地路径（可选） |
| `sourceDurationSec` | `number` | 否 | 样片真实时长，用于提示与档位说明 |
| `aspectRatio` | `string` | 否 | 输出画幅 |
| `packId` | `string \| null` | 否 | 关联的已发布替换素材包；**可省略**——用途绑定模式下省略即「只声明槽位」，调用时按用途从用户自己选的包里取图 |
| `defaultKind` | `"character" \| "product" \| "scene"` | 否 | 选中这张原片卡片时，替换配置默认切到的替换类型 |
| `mediaOrder` | `VideoTemplateMedia[]` | 否 | 留空则由素材包顺序生成 |
| `generation` | `Record<string, unknown>` | 否 | 生成参数，覆盖默认值 |
| `rules` | `Record<string, unknown>` | 否 | 封装规则；referenceBindings 将连续 Mixed 编号绑定到 source_video/图片用途，服务端排列声明槽位并完整附带当前包其余图片，无需逐图引用 |
| `coverUrl` | `string` | 否 | 封面地址 |
| `pinned` | `boolean` | 否 | 是否设为固定蓝本（全库仅一个） |

**响应** `ApiResponse<VideoTemplateOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `templateKey` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `description` | `string` | 是 |  |
| `prompt` | `string` | 是 |  |
| `promptSha256` | `string` | 是 | 提示词 sha256，防止文字被悄悄改掉 |
| `sourceVideoName` | `string` | 是 |  |
| `sourceVideoAssetId` | `string \| null` | 否 |  |
| `sourceVideoPath` | `string` | 是 |  |
| `sourceDurationSec` | `number` | 是 |  |
| `aspectRatio` | `string` | 是 |  |
| `packId` | `string \| null` | 否 |  |
| `packName` | `string` | 否 |  |
| `packVersion` | `number` | 否 |  |
| `defaultKind` | `"character" \| "product" \| "scene"` | 否 |  |
| `mediaOrder` | `VideoTemplateMedia[]` | 否 |  |
| `generation` | `Record<string, unknown>` | 否 |  |
| `rules` | `Record<string, unknown>` | 否 |  |
| `coverUrl` | `string` | 否 |  |
| `pinned` | `boolean` | 否 |  |
| `createdAt` | `string` | 是 |  |
| `updatedAt` | `string` | 是 |  |

#### `GET /api/v1/templates/video-templates/{template_id}`

**视频模板详情（含提示词与媒体顺序）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `template_id` | path | `string` | 是 |  |

**响应** `ApiResponse<VideoTemplateOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `templateKey` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `description` | `string` | 是 |  |
| `prompt` | `string` | 是 |  |
| `promptSha256` | `string` | 是 | 提示词 sha256，防止文字被悄悄改掉 |
| `sourceVideoName` | `string` | 是 |  |
| `sourceVideoAssetId` | `string \| null` | 否 |  |
| `sourceVideoPath` | `string` | 是 |  |
| `sourceDurationSec` | `number` | 是 |  |
| `aspectRatio` | `string` | 是 |  |
| `packId` | `string \| null` | 否 |  |
| `packName` | `string` | 否 |  |
| `packVersion` | `number` | 否 |  |
| `defaultKind` | `"character" \| "product" \| "scene"` | 否 |  |
| `mediaOrder` | `VideoTemplateMedia[]` | 否 |  |
| `generation` | `Record<string, unknown>` | 否 |  |
| `rules` | `Record<string, unknown>` | 否 |  |
| `coverUrl` | `string` | 否 |  |
| `pinned` | `boolean` | 否 |  |
| `createdAt` | `string` | 是 |  |
| `updatedAt` | `string` | 是 |  |

#### `POST /api/v1/templates/video-templates/{template_id}/attach-source`

**绑定样片原片资产**

绑定后，这条模板对应的原片才会在「全部模板」里被当作可选卡片。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `template_id` | path | `string` | 是 |  |

**请求体** `VideoTemplateAttachSourceIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sourceVideoAssetId` | `string` | 是 | 原片 id（资产库里的视频） |

**响应** `ApiResponse<VideoTemplateOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `templateKey` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `description` | `string` | 是 |  |
| `prompt` | `string` | 是 |  |
| `promptSha256` | `string` | 是 | 提示词 sha256，防止文字被悄悄改掉 |
| `sourceVideoName` | `string` | 是 |  |
| `sourceVideoAssetId` | `string \| null` | 否 |  |
| `sourceVideoPath` | `string` | 是 |  |
| `sourceDurationSec` | `number` | 是 |  |
| `aspectRatio` | `string` | 是 |  |
| `packId` | `string \| null` | 否 |  |
| `packName` | `string` | 否 |  |
| `packVersion` | `number` | 否 |  |
| `defaultKind` | `"character" \| "product" \| "scene"` | 否 |  |
| `mediaOrder` | `VideoTemplateMedia[]` | 否 |  |
| `generation` | `Record<string, unknown>` | 否 |  |
| `rules` | `Record<string, unknown>` | 否 |  |
| `coverUrl` | `string` | 否 |  |
| `pinned` | `boolean` | 否 |  |
| `createdAt` | `string` | 是 |  |
| `updatedAt` | `string` | 是 |  |

#### `POST /api/v1/templates/video-templates/{template_id}/pin`

**设为固定蓝本（全库仅一个）**

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `template_id` | path | `string` | 是 |  |

**响应** `ApiResponse<VideoTemplateOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `templateKey` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `description` | `string` | 是 |  |
| `prompt` | `string` | 是 |  |
| `promptSha256` | `string` | 是 | 提示词 sha256，防止文字被悄悄改掉 |
| `sourceVideoName` | `string` | 是 |  |
| `sourceVideoAssetId` | `string \| null` | 否 |  |
| `sourceVideoPath` | `string` | 是 |  |
| `sourceDurationSec` | `number` | 是 |  |
| `aspectRatio` | `string` | 是 |  |
| `packId` | `string \| null` | 否 |  |
| `packName` | `string` | 否 |  |
| `packVersion` | `number` | 否 |  |
| `defaultKind` | `"character" \| "product" \| "scene"` | 否 |  |
| `mediaOrder` | `VideoTemplateMedia[]` | 否 |  |
| `generation` | `Record<string, unknown>` | 否 |  |
| `rules` | `Record<string, unknown>` | 否 |  |
| `coverUrl` | `string` | 否 |  |
| `pinned` | `boolean` | 否 |  |
| `createdAt` | `string` | 是 |  |
| `updatedAt` | `string` | 是 |  |

#### `PATCH /api/v1/templates/video-templates/{template_id}/prompt`

**保存固定提示词与跨封装图片引用**

只换提示词原文，`{{Mixed N}}` 引用按新原文重算。

不提供「删掉重建」：同原片同类型下 `find_for_run` 取最早创建的那条，
重建出来的新版会一直输给旧版。改提示词会影响此后每一次生成，留审计。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `template_id` | path | `string` | 是 |  |

**请求体** `VideoTemplatePromptIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `prompt` | `string` | 是 | 新的固定提示词原文，含 {{Mixed N}} 占位符 |
| `references` | `VideoTemplatePackReferenceIn[] \| null` | 否 | 跨封装固定图片引用；省略保留原绑定，空数组清除 |

**响应** `ApiResponse<VideoTemplateOut>`（前端取 `res.data`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `templateKey` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `description` | `string` | 是 |  |
| `prompt` | `string` | 是 |  |
| `promptSha256` | `string` | 是 | 提示词 sha256，防止文字被悄悄改掉 |
| `sourceVideoName` | `string` | 是 |  |
| `sourceVideoAssetId` | `string \| null` | 否 |  |
| `sourceVideoPath` | `string` | 是 |  |
| `sourceDurationSec` | `number` | 是 |  |
| `aspectRatio` | `string` | 是 |  |
| `packId` | `string \| null` | 否 |  |
| `packName` | `string` | 否 |  |
| `packVersion` | `number` | 否 |  |
| `defaultKind` | `"character" \| "product" \| "scene"` | 否 |  |
| `mediaOrder` | `VideoTemplateMedia[]` | 否 |  |
| `generation` | `Record<string, unknown>` | 否 |  |
| `rules` | `Record<string, unknown>` | 否 |  |
| `coverUrl` | `string` | 否 |  |
| `pinned` | `boolean` | 否 |  |
| `createdAt` | `string` | 是 |  |
| `updatedAt` | `string` | 是 |  |

---

## 5. 数据模型

### `AspectRatio`（枚举）

取值：`9:16`、`16:9`、`1:1`

### `AssetPatchIn`

改名 / 换标签。**不含可见性**：共享必须走申请单，否则审批就没有意义了。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `string \| null` | 否 |  |
| `tagIds` | `string[] \| null` | 否 |  |
| `expectedVersion` | `number` | 否 | >0 时做乐观锁校验；0 表示不校验（兼容单页操作） |

### `AssetSort`（枚举）

取值：`-createdAt`、`createdAt`、`name`、`-sizeBytes`

### `BulkAction`（枚举）

取值：`addTag`、`removeTag`、`rename`、`delete`、`restore`、`approveDraft`、`rejectDraft`、`requestRevoke`、`pack`

### `BulkIn`

批量写。**逐条判定、逐条提交**：一批里 3 条已共享就返回 7 成功 + 3 条明细。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `ids` | `string[]` | 是 |  |
| `action` | `BulkAction` | 是 |  |
| `payload` | `Record<string, unknown>` | 否 |  |
| `expectedVersions` | `Record<string, number>` | 否 |  |

### `BulkItemResult`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `ok` | `boolean` | 是 |  |
| `errorCode` | `number` | 否 |  |
| `message` | `string` | 否 |  |
| `details` | `Record<string, unknown> \| null` | 否 |  |

### `BulkOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `succeeded` | `number` | 否 |  |
| `failed` | `number` | 否 |  |
| `results` | `BulkItemResult[]` | 否 |  |

### `Capability`（枚举）

取值：`video`、`image`、`audio`

### `CompiledSlotOut`

哪几张素材拿到了哪个编号 —— 只给前端画角标，不参与提交。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `key` | `string` | 是 |  |
| `tokens` | `string[]` | 否 |  |
| `assetIds` | `string[]` | 否 |  |

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
| `referenceLibraryIds` | `string[]` | 否 | 参考素材的资产库 v2 资产 id，最多 20 个，**顺序即提交顺序**。受理时校验：必须存在、当前账号能读、不在回收站 / 草稿里，且是 `image` / `video` / `audio` 之一（能否收图、收几张由模型的 `maxReferences` 决定）。传 id 而不是 URL：取件链接是私签的，供应商取不到 |
| `textLibraryIds` | `string[]` | 否 | 要拼进提示词的文本资产 id，按顺序对应 `{{Text 1}}` / `{{Text 2}}`…；提交时按当时版本冻结正文，之后编辑资产不影响本次生成与重试。只接受当前账号可读、在库的 text 类型资产，最多 8 份 |
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

### `DownloadIn`

发起打包。上限在服务层判：请求最多 200 个 id（封装要展开），
**展开后**的条目数与总字节数才是真正的闸门（100 项 / 2GB）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `ids` | `string[]` | 是 |  |

### `DownloadJobOut`

打包任务。

`skipped` 只在 `POST /downloads` 的响应里非空：它解释的是「你选的这批里
哪几个为什么没进来」，轮询状态时不再重复（前端在发起那一刻就该提示）。
落库只存 id 列表与结果，不存这份解释——省一张明细表，也避免它变成第二真相源。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `status` | `string` | 是 |  |
| `assetCount` | `number` | 否 |  |
| `sizeBytes` | `number` | 否 |  |
| `error` | `string` | 否 |  |
| `downloadUrl` | `string \| null` | 否 |  |
| `expiresAt` | `string \| null` | 否 |  |
| `createdAt` | `string` | 是 |  |
| `finishedAt` | `string \| null` | 否 |  |
| `skipped` | `DownloadSkip[]` | 否 |  |

### `DownloadSkip`

没能进包的条目与原因。**不整单失败**：一批 20 个里有 1 个是草稿，
剩下的 19 个仍然该能下。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 否 |  |
| `reason` | `string` | 是 |  |

### `DraftDecisionIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `reason` | `string` | 否 |  |
| `expectedVersion` | `number` | 否 |  |

### `DriverListOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `DriverOut[]` | 否 |  |
| `total` | `number` | 否 |  |
| `errors` | `string[]` | 否 |  |

### `DriverOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `string` | 是 |  |
| `label` | `string` | 是 |  |
| `version` | `string` | 是 |  |
| `path` | `string` | 是 |  |
| `capabilities` | `string[]` | 否 |  |
| `modelCount` | `number` | 否 |  |
| `supportsPoll` | `boolean` | 否 |  |
| `loaded` | `boolean` | 否 |  |
| `error` | `string` | 否 |  |

### `EffectivePermissionsOut`

某用户的权限来源。前端只读展示，**判定永远走 effective**。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `team` | `string[]` | 否 | 团队基础权限（全体启用账号） |
| `groups` | `GroupLayerOut[]` | 否 | 各所属小组授予的权限 |
| `personal` | `string[]` | 否 | 单独授予的权限 |
| `roles` | `string[]` | 否 | 现有角色授予的权限 |
| `effective` | `string[]` | 否 | 四层并集；`*` 表示超级管理员。require() 唯一判据 |

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

### `FromGenerationIn`

生成结果入库（也经 `library-ingest` 注入口供后端模块调用）。

字节来源三选一，**必须能物化**：`storageKey`（主对象存储）、
`archiveKey`（归档存储，模板库替换成片用）、`sourceUrl`（服务端下载）。
只存外部链接的详情页与下载都会裂（链接会过期）。优先级按上面顺序，
同时给多个时取靠前的那个。

`archiveKey` 只应由受信任的服务端调用方填写：它指向归档盘里的对象，
端点本身仍由 `library:generation:ingest` 守护，普通上传权限调不动。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sourceModule` | `string` | 是 |  |
| `sourceTaskId` | `string` | 是 |  |
| `sourceKey` | `string` | 是 |  |
| `kind` | `GenerationKind` | 是 |  |
| `name` | `string` | 否 |  |
| `storageKey` | `string` | 否 |  |
| `archiveKey` | `string` | 否 |  |
| `sourceUrl` | `string` | 否 |  |
| `mimeType` | `string` | 否 |  |
| `sizeBytes` | `number` | 否 |  |
| `tagIds` | `string[]` | 否 |  |
| `generation` | `GenerationSnapshotIn` | 否 |  |

### `GenerateImageIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `prompt` | `string` | 是 | 画面描述 |
| `size` | `string` | 否 | 宽x高，如 `1024x1536`；留空取模型 `paramSchema.size.default` |
| `count` | `number` | 否 | 张数；`0` 表示取模型 `paramSchema.count.default`，上限以模型声明为准 |
| `params` | `Record<string, unknown>` | 否 | 模型声明的其他参数，键与 `GET /models` 返回的 `paramSchema` 一致（如 `quality` / `seed`）。未声明的键会被 400 拒绝，避免打错字被静默忽略；与顶层 `size` / `count` 重复时以顶层为准 |
| `model` | `string` | 否 | 模型 id，留空取默认 |
| `referenceLibraryIds` | `string[]` | 否 | 参考图（图生图 / 多图合成）的素材库资产 id，**按顺序生效**：多图合成时提示词里的「第一张 / 第二张」指的就是这个顺序，重排会让提示词与图片错位。只接受 `image` 类型且当前账号能读的资产，张数上限由模型 `maxReferences` 决定（`0` = 该模型不支持参考图）。传 id 而不是 URL：我们的取件链接是私签的、供应商访问不到，服务端读字节后以 `data:image/...;base64,...` 交给供应商 |
| `textLibraryIds` | `string[]` | 否 | 要拼进提示词的文本资产 id，**按顺序生效**：`{{Text 1}}` 指第 1 份、`{{Text 2}}` 指第 2 份。提交时把正文按当时版本冻结进任务，之后编辑资产不会改变这次生成与它的重试。只接受当前账号可读、在库的 `text` 类型资产；一次最多 8 份 |

### `GenerationKind`（枚举）

取值：`video`、`image`

### `GenerationSnapshotIn`

生成参数快照。字段与 `library_asset_generation` 一一对应，全部可选。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `model` | `string` | 否 |  |
| `modelId` | `string` | 否 |  |
| `prompt` | `string` | 否 |  |
| `size` | `string` | 否 |  |
| `count` | `number` | 否 |  |
| `durationSec` | `number` | 否 |  |
| `aspectRatio` | `string` | 否 |  |
| `stylePreset` | `string` | 否 |  |
| `seed` | `string` | 否 |  |
| `referenceLibraryIds` | `string[]` | 否 |  |
| `packId` | `string` | 否 |  |
| `packVersion` | `number` | 否 |  |
| `promptSpec` | `Record<string, unknown>` | 否 |  |
| `configuration` | `Record<string, unknown>` | 否 |  |
| `externalId` | `string` | 否 |  |
| `credits` | `number` | 否 |  |
| `generatedAt` | `string \| null` | 否 |  |

### `GroupCreateIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `string` | 是 | 小组名称，唯一 |
| `description` | `string` | 否 | 小组说明 |

### `GroupLayerOut`

分层权限明细里的一段：这条权限来自哪个小组。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 小组 ID |
| `name` | `string` | 是 | 小组名称 |
| `permissions` | `string[]` | 否 | 该组授予的权限码 |

### `GroupLeadersIn`

整体替换某组的负责人列表（只服务删除窄口）。空数组 = 清空。
审批资格由 `library:share:approve*` 三个权限点按申请目标判定，与这张表无关。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `userIds` | `string[]` | 否 |  |

### `GroupMembersIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `userIds` | `string[]` | 否 | 成员用户 id，整组替换；传空数组即清空 |

### `GroupOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 小组 ID |
| `name` | `string` | 是 | 小组名称，唯一 |
| `description` | `string` | 否 | 小组说明 |
| `createdAt` | `string` | 是 | 创建时间（UTC） |
| `permissions` | `string[]` | 否 | 小组整体权限；成员自动获得 |
| `memberIds` | `string[]` | 否 | 成员用户 id；编辑成员时整组替换 |
| `memberCount` | `number` | 否 | 成员数 |

### `GroupUpdateIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `string \| null` | 否 | 小组名称 |
| `description` | `string \| null` | 否 | 小组说明 |

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

### `ImageFeedItem`

一条生图记录的任务中心投影，字段够卡片与详情弹窗用即可。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 生图记录 ID |
| `title` | `string` | 是 | 展示标题（记录名） |
| `status` | `string` | 是 | 原始状态：queued / running / succeeded / failed |
| `group` | `"all" \| "queued" \| "running" \| "completed" \| "failed" \| "attention"` | 是 | 统一筛选分组 |
| `createdAt` | `string` | 是 | 创建时间（UTC） |
| `progress` | `number` | 否 | 生成进度 0-100 |
| `model` | `string` | 否 | 模型可读名 |
| `modelId` | `string \| null` | 否 | 模型目录 id |
| `size` | `string` | 否 | 尺寸，如 1024x1024 |
| `count` | `number` | 否 | 一次生成的张数 |
| `prompt` | `string` | 否 | 画面描述 |
| `coverUrl` | `string` | 否 | 封面图地址（已按当前存储驱动现签，可直接用于 <img>） |
| `outputUrl` | `string \| null` | 否 | 成图地址（成功后才有），同样已现签 |
| `errorMessage` | `string \| null` | 否 | 失败原因 |
| `externalId` | `string \| null` | 否 | 供应商任务号；有值时重试需先确认可能已扣费 |
| `detailUrl` | `string \| null` | 否 | 生图历史页深链接 |

### `ImageJobOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 生图记录 ID |
| `name` | `string` | 是 | 名称 |
| `prompt` | `string` | 是 | 画面描述 |
| `size` | `string` | 是 | 尺寸，如 `1024x1024` |
| `count` | `number` | 否 | 一次生成的张数 |
| `params` | `Record<string, unknown>` | 否 | 模型额外参数（不含 size / count），键按提交时的模型 `paramSchema` |
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
| `referenceLibraryIds` | `string[]` | 否 | 提交时带的参考图资产 id（顺序即提交顺序）；纯文生图为空 |
| `textLibraryIds` | `string[]` | 否 | 绑定的文本资产 id（顺序即提示词里 `{{Text N}}` 的编号顺序） |
| `textBindings` | `Record<string, unknown>[]` | 否 | 绑定的文案与版本快照（不含正文）：assetId / name / version / sha256 |
| `createdAt` | `string` | 是 | 创建时间（UTC） |

### `ImageRetryOut`

重试只回 id / status。

不回整份 `ImageJobOut`：持有 `image:retry:any` 的人可以帮别人重跑，
但没有 `image:read:all`，不该顺带读到别人的 prompt（与 task 的重试同一口径）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 生图记录 ID |
| `status` | `string` | 是 | 重试后的状态，固定为 `queued` |

### `IngestOut`

生成入库结果。`created=False` = 命中幂等（sourceKey 重复或内容相同）。

不返回整条资产：调用方（生成侧）需要的是「拿到 id 去关联任务」，
而它可能并不持有 `library:read` 之外的权限去读详情。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `assetId` | `string` | 是 |  |
| `created` | `boolean` | 是 |  |

### `LibraryArchiveOut`

归档信息。只给所有者与管理员：存储键与幂等键对外没有意义，只会泄露内部结构。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `storageKey` | `string` | 否 |  |
| `sha256` | `string` | 否 |  |
| `sourceKey` | `string` | 否 |  |
| `sizeBytes` | `number` | 否 |  |
| `referenceCount` | `number` | 否 |  |

### `LibraryAssetDetailOut`

详情 = 列表项 + 标签 + 生成参数（有才返回）。

生成参数用 `None` 而不是空对象：前端要区分「不是生成来的」与
「是生成来的但历史数据没记全」，两者显示逻辑不同。

M3 补齐详情页六个区的数据源（设计 §7.2）：

- 媒体区 → `packItems`（仅封装）与 `coverUrl`；
- 基础信息 → `state` / `trashedFromState` / `reviewedBy` / `rejectReason`；
- 媒体规格 → 继承的 `sizeBytes` / `durationSec` / `width`… 由前端自己排；
- 生成参数 → `generation`；
- 使用与引用 → `references` + `shareHistory`；
- 归档信息 → `archive`（仅所有者 / 管理员可见）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `kind` | `LibraryAssetKind` | 是 |  |
| `origin` | `string` | 是 | upload / generated / template_source / imported / pack |
| `mimeType` | `string \| null` | 否 |  |
| `sizeBytes` | `number` | 否 |  |
| `durationSec` | `number \| null` | 否 |  |
| `hasAudio` | `boolean` | 否 |  |
| `ownerId` | `string` | 是 |  |
| `ownerName` | `string \| null` | 否 |  |
| `visibility` | `LibraryVisibility` | 是 |  |
| `state` | `LibraryAssetState` | 是 |  |
| `stateVersion` | `number` | 否 |  |
| `coverUrl` | `string \| null` | 否 |  |
| `url` | `string` | 否 |  |
| `previewUrl` | `string \| null` | 否 |  |
| `streamUrl` | `string \| null` | 否 |  |
| `width` | `number \| null` | 否 |  |
| `height` | `number \| null` | 否 |  |
| `groupIds` | `string[]` | 否 |  |
| `tags` | `LibraryTagOut[]` | 否 |  |
| `trashReason` | `string \| null` | 否 |  |
| `trashExpiresAt` | `string \| null` | 否 |  |
| `textExcerpt` | `string \| null` | 否 |  |
| `textVersion` | `number` | 否 |  |
| `textUrl` | `string \| null` | 否 |  |
| `createdAt` | `string` | 是 |  |
| `updatedAt` | `string \| null` | 否 |  |
| `groups` | `LibraryGroupRef[]` | 否 |  |
| `generation` | `LibraryGenerationOut \| null` | 否 |  |
| `packItems` | `PackItemOut[]` | 否 |  |
| `references` | `string[] \| null` | 否 |  |
| `shareHistory` | `ShareHistoryItem[]` | 否 |  |
| `archive` | `LibraryArchiveOut \| null` | 否 |  |
| `reviewedBy` | `string \| null` | 否 |  |
| `reviewedAt` | `string \| null` | 否 |  |
| `rejectReason` | `string \| null` | 否 |  |
| `trashedFromState` | `string \| null` | 否 |  |

### `LibraryAssetKind`（枚举）

取值：`video`、`audio`、`image`、`pack`、`text`

### `LibraryAssetOrigin`（枚举）

取值：`upload`、`generated`、`template_source`、`imported`、`pack`

### `LibraryAssetOut`

列表项。字段对齐 `library_assets` 表，M1 建表后不再**改**形状。

后面补的五个字段是「只增不改」：取件地址与媒体规格本来就在表里/由 id 决定，
以前让前端自己拼 URL、自己猜哪套库。模板库切换时前端只要改接口调用，
不必在业务组件里补一段「按 id 拼 /file」的适配代码。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `kind` | `LibraryAssetKind` | 是 |  |
| `origin` | `string` | 是 | upload / generated / template_source / imported / pack |
| `mimeType` | `string \| null` | 否 |  |
| `sizeBytes` | `number` | 否 |  |
| `durationSec` | `number \| null` | 否 |  |
| `hasAudio` | `boolean` | 否 |  |
| `ownerId` | `string` | 是 |  |
| `ownerName` | `string \| null` | 否 |  |
| `visibility` | `LibraryVisibility` | 是 |  |
| `state` | `LibraryAssetState` | 是 |  |
| `stateVersion` | `number` | 否 |  |
| `coverUrl` | `string \| null` | 否 |  |
| `url` | `string` | 否 |  |
| `previewUrl` | `string \| null` | 否 |  |
| `streamUrl` | `string \| null` | 否 |  |
| `width` | `number \| null` | 否 |  |
| `height` | `number \| null` | 否 |  |
| `groupIds` | `string[]` | 否 |  |
| `tags` | `LibraryTagOut[]` | 否 |  |
| `trashReason` | `string \| null` | 否 |  |
| `trashExpiresAt` | `string \| null` | 否 |  |
| `textExcerpt` | `string \| null` | 否 |  |
| `textVersion` | `number` | 否 |  |
| `textUrl` | `string \| null` | 否 |  |
| `createdAt` | `string` | 是 |  |
| `updatedAt` | `string \| null` | 否 |  |

### `LibraryAssetPage`

列表响应 = 标准分页 + 分面统计。

继承 `Page` 而不是复制 `items/total/page/pageSize`：前端的通用分页组件
认的是那四个字段，复制一遍迟早出现「这个页面 pageSize 拼错成 page_size」。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `LibraryAssetOut[]` | 否 |  |
| `total` | `number` | 否 |  |
| `page` | `number` | 否 |  |
| `pageSize` | `number` | 否 |  |
| `facets` | `LibraryFacets` | 否 |  |

### `LibraryAssetState`（枚举）

取值：`draft`、`active`、`trash`

### `LibraryFacets`

分面统计：五个一级入口的条数，前端直接渲染成侧栏角标。

与 `items` 同一次查询算出，不额外发起请求——否则切换 tab 时角标会跳。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `all` | `number` | 否 |  |
| `personal` | `number` | 否 |  |
| `group` | `number` | 否 |  |
| `team` | `number` | 否 |  |
| `drafts` | `number` | 否 |  |
| `trash` | `number` | 否 |  |

### `LibraryGenerationOut`

生成溯源快照。入库那一刻冻结，不联表查；缺失字段前端显示「未记录」。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sourceModule` | `string` | 是 |  |
| `sourceTaskId` | `string` | 是 |  |
| `model` | `string` | 否 |  |
| `modelId` | `string` | 否 |  |
| `prompt` | `string` | 否 |  |
| `size` | `string` | 否 |  |
| `count` | `number` | 否 |  |
| `durationSec` | `number` | 否 |  |
| `aspectRatio` | `string` | 否 |  |
| `stylePreset` | `string` | 否 |  |
| `seed` | `string` | 否 |  |
| `referenceLibraryIds` | `string[]` | 否 |  |
| `packId` | `string` | 否 |  |
| `packVersion` | `number` | 否 |  |
| `promptSpec` | `Record<string, unknown>` | 否 |  |
| `configuration` | `Record<string, unknown>` | 否 |  |
| `externalId` | `string` | 否 |  |
| `credits` | `number` | 否 |  |
| `generatedAt` | `string \| null` | 否 |  |

### `LibraryGroupOut`

我的小组。`isLeader` 现在只用于删除窄口（§3.4.1）的入口展示；
「待我审核」按审批权限点与「是不是该组成员」收窄，与组长身份无关。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 否 |  |
| `isLeader` | `boolean` | 否 |  |

### `LibraryGroupRef`

资产共享到的小组。名称走组织目录注入口，不在本模块 join 身份的表。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 否 |  |

### `LibrarySelectionPage`

选择器返回：只含 `state=active` 的条目，不带分面（选择器不显示角标）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `LibraryAssetOut[]` | 否 |  |
| `total` | `number` | 否 |  |
| `page` | `number` | 否 |  |
| `pageSize` | `number` | 否 |  |

### `LibraryTab`（枚举）

取值：`all`、`personal`、`group`、`team`

### `LibraryTagOut`

词表里的一条标签。**固定两级**，不返回嵌套树。

为什么仍是平铺数组：标签栏、卡片、打标选择器、上传弹窗、批量栏、素材库
悬浮栏六个消费点全都按平铺渲染；改成嵌套树要全量重写，而且「计数与排序」
会跟着乱。`parentId` + `depth` 足够让前端自己分两行、显示 `产品 / 3030`。

库里用空串表示一级（见 models.py），出参统一转成 `null`：前端按真假值判断
层级就行，不必知道哨兵串的存在。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `parentId` | `string \| null` | 否 |  |
| `parentName` | `string \| null` | 否 |  |
| `depth` | `number` | 否 |  |

### `LibraryTagStatOut`

标签栏的列表项：比卡片上的标签多「素材数」「直属子标签数」与「添加时间」。

`asset_count` 是**当前入口**（`state` + `library`）下挂了这个标签的素材数，
不是全库总量——标签栏上点一下就按这个入口筛，数字必须与结果对得上。
一级标签的数字是「自身 + 旗下二级」按资产去重后的合计，与「点一级展开到
子级」的筛选口径一致；否则会出现「标签旁写 8、点进去只有 3 条」。

`child_count` 给前端判断「这是一级分组还是可打标的叶子」。
`created_at` 给前端按「添加时间先后」排序用。

单独出一个类型而不是往 `LibraryTagOut` 上加这几个字段：卡片里 24 条资产
每条都带一遍计数与时间戳，既没用也容易被人误当成卡片上的真实数据。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `parentId` | `string \| null` | 否 |  |
| `parentName` | `string \| null` | 否 |  |
| `depth` | `number` | 否 |  |
| `assetCount` | `number` | 否 |  |
| `childCount` | `number` | 否 |  |
| `createdAt` | `string` | 是 |  |

### `LibraryVisibility`（枚举）

取值：`personal`、`group`、`team`

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

### `ManifestDraftIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `key` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `description` | `string` | 否 |  |
| `video` | `ManifestVideoIn` | 是 |  |
| `packs` | `ManifestPackIn[]` | 否 |  |
| `functions` | `ManifestFunctionIn[]` | 是 |  |

### `ManifestFunctionIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `key` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `pack` | `string \| null` | 否 |  |
| `kind` | `"product" \| "character" \| "scene" \| null` | 否 |  |
| `scenePromptDefault` | `string \| null` | 否 |  |
| `prompt` | `string` | 是 |  |
| `mediaOrder` | `string[]` | 否 |  |
| `bindings` | `Record<string, string> \| null` | 否 |  |
| `scopeOptions` | `string[]` | 否 |  |
| `aspectRatio` | `"9:16" \| "16:9" \| "1:1"` | 否 |  |
| `duration` | `number \| null` | 否 |  |

### `ManifestListOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `ManifestOut[]` | 否 |  |
| `total` | `number` | 否 |  |

### `ManifestOut`

配置详情。嵌套体直接复用入参模型：形状一致，不另造一份会漂移的出参。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `key` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `description` | `string` | 是 |  |
| `ownerId` | `string` | 是 |  |
| `video` | `ManifestVideoIn` | 是 |  |
| `status` | `string` | 是 |  |
| `version` | `number` | 是 |  |
| `revision` | `number` | 是 |  |
| `fingerprint` | `string` | 是 |  |
| `publishedAt` | `string \| null` | 否 |  |
| `createdAt` | `string` | 是 |  |
| `updatedAt` | `string` | 是 |  |
| `packs` | `ManifestPackIn[]` | 否 |  |
| `functions` | `ManifestFunctionIn[]` | 否 |  |

### `ManifestPackIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `key` | `string` | 是 |  |
| `name` | `string` | 否 |  |
| `kind` | `"product" \| "character" \| "scene"` | 是 |  |
| `packId` | `string \| null` | 否 |  |
| `references` | `ManifestReferenceIn[]` | 否 |  |
| `requiredRoles` | `string[]` | 否 |  |
| `supportedScopes` | `string[]` | 否 |  |
| `wardrobeMode` | `"preserve" \| "reference"` | 否 |  |

### `ManifestPublishOut`

发布回执：`templateId` 即工作台卡片 id，前端拼 `workbenchPath` 用。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `manifest` | `ManifestOut` | 是 |  |
| `version` | `number` | 是 |  |
| `templates` | `PublishedTemplateOut[]` | 否 |  |

### `ManifestReferenceIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `key` | `string` | 是 |  |
| `assetId` | `string` | 是 |  |
| `role` | `string` | 是 |  |
| `description` | `string` | 否 |  |

### `ManifestValidateOut`

只预检不落库：返回会用于幂等判定的配置指纹。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `valid` | `boolean` | 否 |  |
| `fingerprint` | `string` | 是 |  |

### `ManifestVideoIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `assetId` | `string` | 是 |  |
| `shared` | `boolean` | 否 |  |

### `MeOut`

`GET /identity/me`：前端靠 `permissions` 渲染菜单与按钮。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 用户 ID |
| `username` | `string` | 是 | 登录名，入库前已转小写 |
| `displayName` | `string` | 否 | 昵称（顶栏展示用） |
| `email` | `string \| null` | 否 | 邮箱，未填为 null |
| `avatarUrl` | `string \| null` | 否 | 头像 URL，未设置为 null |
| `phone` | `string \| null` | 否 | 手机号，仅展示，未填为 null |
| `position` | `string \| null` | 否 | 职务，仅展示，未填为 null |
| `isActive` | `boolean` | 否 | 是否启用；停用后无法登录且令牌立即失效 |
| `lastLoginAt` | `string \| null` | 否 | 最后登录时间，从未登录为 null |
| `createdAt` | `string` | 是 | 创建时间（UTC） |
| `roles` | `RoleBriefOut[]` | 否 | 拥有的角色 |
| `permissions` | `string[]` | 否 | 展开后的权限码；含 `*` 表示超级管理员 |

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
| `referenceLibraryIds` | `string[]` | 否 | 封装用的参考图 id，按顺序 |
| `coverLibraryId` | `string \| null` | 否 | 封面的 asset id；必须出现在 referenceLibraryIds 里 |
| `wardrobeMode` | `"preserve" \| "reference"` | 否 |  |
| `features` | `string` | 否 |  |
| `instructions` | `string` | 否 |  |
| `bindings` | `PackBinding[]` | 否 |  |
| `requiredRoles` | `string[]` | 否 |  |
| `supportedScopes` | `string[]` | 否 |  |

### `PackIn`

建封装。

`items` 是**唯一有序来源**；`assetIds` 只是给「勾选一批直接封装」用的简写，
会被追加到 `items` 之后（已在 items 里的不重复加）。两者都空 → 400。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `string` | 是 |  |
| `assetIds` | `string[]` | 否 |  |
| `items` | `PackItemIn[]` | 否 |  |
| `coverAssetId` | `string` | 否 |  |
| `tagIds` | `string[]` | 否 |  |

### `PackItemIn`

封装成员。`role` 预留给模板替换（产品主图 / 多视结构图…），首版可为空。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `assetId` | `string` | 是 |  |
| `role` | `string` | 否 |  |
| `description` | `string` | 否 |  |

### `PackItemOut`

封装里的一项。成员可能是别人共享给我的素材，所以带 `state` 与 `coverUrl`。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `assetId` | `string` | 是 |  |
| `name` | `string` | 否 |  |
| `kind` | `LibraryAssetKind` | 是 |  |
| `state` | `LibraryAssetState` | 是 |  |
| `position` | `number` | 是 |  |
| `role` | `string` | 否 |  |
| `description` | `string` | 否 |  |
| `sizeBytes` | `number` | 否 |  |
| `coverUrl` | `string \| null` | 否 |  |

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
| `referenceNames` | `string[]` | 否 |  |
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

### `PackPatchIn`

改封装。`items` 提供即**整体替换**（不是增量追加）——增量语义要前端自己
算差集，而算错了就是悄悄多一个成员，不如让调用方给全量。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `string \| null` | 否 |  |
| `items` | `PackItemIn[] \| null` | 否 |  |
| `coverAssetId` | `string \| null` | 否 |  |
| `tagIds` | `string[] \| null` | 否 |  |
| `expectedVersion` | `number` | 否 |  |

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

### `PermissionsIn`

团队 / 小组 / 个人三层共用的「整组替换」入参。

三层共用一份形状不是偷懒：写接口的守卫、权限码合法性校验、以及前端的
权限勾选组件对三层完全一样，分成三个类只会让「改了一处忘两处」变大概率事件。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `permissions` | `string[]` | 否 | 整组替换；传空数组即清空。出现未登记的权限码会被拒绝 |

### `PrepareReplacement`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `requestId` | `string` | 是 |  |
| `channel` | `"libtv" \| "bailian" \| "bailian-prime"` | 否 |  |
| `kind` | `"character" \| "product" \| "scene"` | 是 |  |
| `target` | `string` | 否 |  |
| `sourceLibraryId` | `string \| null` | 否 | 原片（资产库里的视频）id |
| `videoTemplateId` | `string \| null` | 否 |  |
| `scenePrompt` | `string \| null` | 否 | 文字换场景的描述；省略使用模板默认值，空白拒绝 |
| `referenceIds` | `string[]` | 否 |  |
| `libraryPackId` | `string \| null` | 否 | 资产库 v2 封装的 id；成员按 position 顺序当参考图，用途取自成员自己声明的 role。与 packId / packVersion / referenceIds / extraPackIds 互斥；prepare 时展开并冻结，之后封装改成员不影响已准备的任务 |
| `textLibraryIds` | `string[]` | 否 | 绑定的文本资产 id，按顺序对应 `{{Text N}}`；最多 8 份 |
| `extraPackIds` | `string[]` | 否 | 第 2 位及以后的素材包 id，按提示词槽位顺序；第一包仍用 packId |
| `extraPackVersions` | `number[]` | 否 | 与 extraPackIds 顺序对应的发布版本；旧调用省略时由服务端解析并冻结 |
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
| `phone` | `string \| null` | 否 | 手机号；传 null 清空 |
| `position` | `string \| null` | 否 | 职务；传 null 清空 |

### `PublishedTemplateOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `functionKey` | `string` | 是 |  |
| `templateId` | `string` | 是 |  |
| `templateKey` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `workbenchPath` | `string` | 是 |  |

### `ReadyOut`

就绪状态。`checks` 说明每一项依赖的结论，status ∈ ok / degraded / down。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `status` | `string` | 是 |  |
| `database` | `string` | 是 |  |
| `queueSize` | `number` | 是 |  |
| `checks` | `RuntimeCheckOut[]` | 否 |  |

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
| `channels` | `ReplacementChannelOut[]` | 否 |  |
| `provider` | `string` | 否 |  |
| `maxCredits` | `number` | 否 |  |
| `kinds` | `string[]` | 否 |  |
| `runs` | `ReplacementRunOut[]` | 是 |  |
| `packs` | `ReplacementPack[]` | 否 |  |
| `packRoles` | `Record<string, Record<string, string>>` | 否 |  |
| `baseRoles` | `Record<string, string>` | 否 |  |
| `packScopes` | `Record<string, string>` | 否 |  |

### `ReplacementChannelOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `"libtv" \| "bailian" \| "bailian-prime"` | 是 |  |
| `label` | `string` | 是 |  |
| `model` | `string` | 是 |  |
| `enabled` | `boolean` | 是 |  |
| `billing` | `"libtv" \| "aliyun"` | 是 |  |
| `unavailableReason` | `string` | 否 |  |

### `ReplacementInputAsset`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `role` | `string` | 是 |  |
| `type` | `string` | 是 |  |
| `url` | `string` | 是 |  |
| `token` | `string` | 否 | 当次输入的 Mixed 编号，无法从历史快照确定时留空 |
| `usage` | `string` | 否 | 当次快照中记录的媒体用途说明 |
| `previewUrl` | `string \| null` | 否 | 授权读取当次输入快照的缩略图 |

### `ReplacementInputSummary`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `assets` | `ReplacementInputAsset[]` | 否 |  |
| `coverReferenceId` | `string \| null` | 否 |  |
| `wardrobeMode` | `string` | 否 |  |
| `replacementScope` | `string` | 否 |  |
| `notes` | `string` | 否 |  |
| `scenePrompt` | `string \| null` | 否 | 本次实际使用的可编辑场景描述 |

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
| `referenceNames` | `string[]` | 否 |  |
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
| `channel` | `"libtv" \| "bailian" \| "bailian-prime"` | 否 |  |
| `channelLabel` | `string` | 否 |  |
| `kind` | `"character" \| "product" \| "scene"` | 是 |  |
| `target` | `string` | 是 |  |
| `status` | `string` | 是 |  |
| `archivePath` | `string \| null` | 否 | 工作区绝对路径（排查用）；目录已被清理或换机器后为 null |
| `createdAt` | `string` | 是 |  |
| `startedAt` | `string \| null` | 否 | 确认提交（进入排队）的时间，未开始为 null |
| `finishedAt` | `string \| null` | 否 | 这一轮结束的时间；未结束为 null |
| `elapsedSec` | `number \| null` | 否 | 确认提交到结束的耗时（秒）；未完成时按到此刻计，未开始为 null |
| `prompt` | `string` | 是 |  |
| `promptSha256` | `string \| null` | 否 | 实际发出去那段提示词的 sha256（所有后处理之后）。档案里另外两个哈希（template/spec）哈希的是模板与输入，复核不了最终字节。加这列之前准备的任务为 null，不回填。 |
| `textLibraryIds` | `string[]` | 否 | 本次绑定的文本资产 id（顺序即 `{{Text N}}` 编号） |
| `textBindings` | `Record<string, unknown>[]` | 否 | 绑定的文案与版本快照（不含正文） |
| `promptMode` | `"fixed" \| "compiled"` | 否 | fixed=封装视频模板时写死的原文，逐字提交；compiled=按素材与替换范围现编。展示层据此告诉用户「跑的就是你封装时那份提示词」。 |
| `videoTemplateName` | `string \| null` | 否 | fixed 时出自哪条视频模板；compiled 为 null |
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

### `ShareAction`（枚举）

取值：`share`、`revoke`

### `ShareDecisionIn`

审批 / 驳回 / 撤回申请单。`note` 会进申请单与审计。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `note` | `string` | 否 |  |
| `expectedVersion` | `number` | 否 |  |

### `ShareHistoryItem`

可见性变更的一条记录。`reason` 是撤销申请里填的理由，审核人当时看的就是它。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `action` | `ShareAction` | 是 |  |
| `target` | `ShareTarget` | 是 |  |
| `groupId` | `string` | 否 |  |
| `groupName` | `string` | 否 |  |
| `status` | `string` | 是 |  |
| `applicantId` | `string` | 是 |  |
| `applicantName` | `string \| null` | 否 |  |
| `reviewerId` | `string \| null` | 否 |  |
| `reviewerName` | `string \| null` | 否 |  |
| `reason` | `string` | 否 |  |
| `note` | `string` | 否 |  |
| `decidedAt` | `string \| null` | 否 |  |
| `createdAt` | `string` | 是 |  |

### `ShareRequestIn`

发起共享或撤销共享。

`action=revoke` 必须带 `reason`：撤销会让正在用这个素材的同事突然拿不到文件，
理由要留在申请单里（设计 §3.5）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `assetId` | `string` | 是 |  |
| `action` | `ShareAction` | 否 |  |
| `target` | `ShareTarget` | 否 |  |
| `groupId` | `string` | 否 |  |
| `reason` | `string` | 否 |  |
| `expectedVersion` | `number` | 否 |  |

### `ShareRequestOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `assetId` | `string` | 是 |  |
| `assetName` | `string` | 否 |  |
| `applicantId` | `string` | 是 |  |
| `applicantName` | `string \| null` | 否 |  |
| `action` | `ShareAction` | 是 |  |
| `target` | `ShareTarget` | 是 |  |
| `groupId` | `string` | 否 |  |
| `groupName` | `string \| null` | 否 |  |
| `status` | `string` | 是 |  |
| `reviewerId` | `string \| null` | 否 |  |
| `note` | `string` | 否 |  |
| `reason` | `string` | 否 |  |
| `decidedAt` | `string \| null` | 否 |  |
| `createdAt` | `string` | 是 |  |

### `ShareTarget`（枚举）

取值：`group`、`team`

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

### `SpecCompileIn`

填表结果：`{slotKey: [assetId…]}`。

`source=pack` 的槽位传一个封装 id；`source=asset` 的槽位按用户挑选顺序传。
`version` 为空表示用当前发布版本。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `version` | `number \| null` | 否 |  |
| `slotValues` | `Record<string, string[]>` | 否 |  |

### `SpecCompileOut`

可直接转发给 `POST /tasks` 的请求体。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `version` | `number` | 是 |  |
| `model` | `string` | 是 |  |
| `prompt` | `string` | 是 |  |
| `params` | `Record<string, unknown>` | 是 |  |
| `referenceLibraryIds` | `string[]` | 否 |  |
| `textLibraryIds` | `string[]` | 否 |  |
| `slots` | `CompiledSlotOut[]` | 否 |  |

### `SpecPromptIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `locked` | `string` | 否 |  |
| `editable` | `string` | 否 |  |

### `SpecPublishOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `version` | `number` | 是 |  |
| `spec` | `TemplateSpecOut` | 是 |  |

### `SpecSlotIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `key` | `string` | 是 |  |
| `type` | `"image" \| "video" \| "audio" \| "text"` | 是 |  |
| `source` | `"asset" \| "pack"` | 否 |  |
| `binding` | `"fixed" \| "user"` | 否 |  |
| `refMode` | `"mixed" \| "attachment"` | 否 |  |
| `minCount` | `number` | 否 |  |
| `maxCount` | `number` | 否 |  |
| `label` | `string` | 否 |  |
| `usage` | `string` | 否 |  |
| `assetId` | `string \| null` | 否 |  |
| `packId` | `string \| null` | 否 |  |
| `position` | `number` | 否 |  |

### `SpecSlotOut`

槽位出参：与入参同形。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `key` | `string` | 是 |  |
| `type` | `"image" \| "video" \| "audio" \| "text"` | 是 |  |
| `source` | `"asset" \| "pack"` | 否 |  |
| `binding` | `"fixed" \| "user"` | 否 |  |
| `refMode` | `"mixed" \| "attachment"` | 否 |  |
| `minCount` | `number` | 否 |  |
| `maxCount` | `number` | 否 |  |
| `label` | `string` | 否 |  |
| `usage` | `string` | 否 |  |
| `assetId` | `string \| null` | 否 |  |
| `packId` | `string \| null` | 否 |  |
| `position` | `number` | 否 |  |

### `SpecValidateOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `valid` | `boolean` | 否 |  |
| `fingerprint` | `string` | 是 |  |

### `SpecVersionOut`

一次发布快照。`slots` 的顺序就是编号顺序（`position` 升序返回）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `version` | `number` | 是 |  |
| `model` | `string` | 是 |  |
| `params` | `Record<string, unknown>` | 是 |  |
| `prompt` | `SpecPromptIn` | 是 |  |
| `slots` | `SpecSlotOut[]` | 是 |  |
| `fingerprint` | `string` | 否 |  |
| `publishedBy` | `string` | 否 |  |
| `publishedAt` | `string \| null` | 否 |  |

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

### `SubmitIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `model` | `string` | 是 |  |
| `prompt` | `string` | 是 |  |
| `name` | `string` | 否 |  |
| `params` | `Record<string, unknown>` | 否 |  |
| `referenceUrls` | `string[]` | 否 |  |
| `source` | `string` | 否 |  |

### `TagIn`

新建 / 重命名标签。

`parent_id` **只在新建时生效**：给一个一级标签的 id 就建成二级，留 null
建成一级。改名时不改层级，走 `PATCH /tags/{id}/parent`——把改名与移动混在
一个接口里，会让「只想改个错别字」的请求顺手改变整棵树的形状。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `string` | 是 |  |
| `parentId` | `string \| null` | 否 |  |

### `TagParentIn`

移动标签：`parentId` 指向一个一级标签，`null` 表示提升为一级。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `parentId` | `string \| null` | 否 |  |

### `TaskFeedItem`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `key` | `string` | 是 | 带来源前缀的唯一键，不是上游任务 ID |
| `id` | `string` | 是 | 原始任务、生图记录或替换档案 ID |
| `source` | `"video" \| "image" \| "replacement"` | 是 | 任务来源 |
| `title` | `string` | 是 | 展示标题 |
| `status` | `string` | 是 | 原始状态，保留各链路的精确状态 |
| `group` | `"all" \| "queued" \| "running" \| "completed" \| "failed" \| "attention"` | 是 | 统一筛选分组；running 包含排队 |
| `createdAt` | `string` | 是 | 创建时间 |
| `progress` | `number` | 否 | 生成进度 |
| `sourceName` | `string` | 否 | 替换原片名称 |
| `outputUrl` | `string \| null` | 否 | 成片 / 成图地址，三路都已按当前存储驱动现签；没有产物时为 null |
| `errorMessage` | `string \| null` | 否 | 失败或需处理原因 |
| `detailUrl` | `string \| null` | 否 | 来源页面的深链接 |
| `video` | `modules__task__schema__TaskOut \| null` | 否 | 通用视频任务数据；其他来源为 null |
| `image` | `ImageFeedItem \| null` | 否 | 生图任务数据；其他来源为 null |

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
| `items` | `modules__task__schema__TaskOut[]` | 否 |  |
| `total` | `number` | 否 |  |
| `page` | `number` | 否 |  |
| `pageSize` | `number` | 否 |  |
| `stats` | `TaskStats` | 否 | 侧边栏角标用的分组计数 |
| `canReadAll` | `boolean` | 否 | 当前账号能否查看他人任务；false 时前端应收起「全部」选项 |

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

### `TemplateSpecIn`

一份规格（草稿的整份内容）。`key` 是幂等边界，发布后不可改名。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `key` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `description` | `string` | 否 |  |
| `model` | `string` | 是 |  |
| `params` | `Record<string, unknown>` | 否 |  |
| `prompt` | `SpecPromptIn` | 是 |  |
| `slots` | `SpecSlotIn[]` | 否 |  |
| `attributes` | `Record<string, string>` | 否 |  |
| `revision` | `number \| null` | 否 |  |

### `TemplateSpecListOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `TemplateSpecOut[]` | 否 |  |
| `total` | `number` | 否 |  |

### `TemplateSpecOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `key` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `description` | `string` | 是 |  |
| `ownerId` | `string` | 是 |  |
| `status` | `"draft" \| "published" \| "disabled"` | 是 |  |
| `revision` | `number` | 是 |  |
| `fingerprint` | `string` | 是 |  |
| `currentVersion` | `number` | 是 |  |
| `publishedAt` | `string \| null` | 否 |  |
| `disabledAt` | `string \| null` | 否 |  |
| `createdAt` | `string` | 是 |  |
| `updatedAt` | `string` | 是 |  |
| `draft` | `TemplateSpecIn \| null` | 否 |  |
| `latest` | `SpecVersionOut \| null` | 否 |  |

### `TextAssetIn`

站内新建一份文本资产（不传文件，直接写正文）。

`extension` 只认 md / txt：文件名后缀决定 `mime_type` 与内容寻址 key 的后缀，
不能由用户随便给一个 `.html`——那会让下载下来的东西被浏览器当页面执行。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `string` | 是 |  |
| `content` | `string` | 是 |  |
| `extension` | `string` | 否 |  |
| `tagIds` | `string[]` | 否 |  |

### `TextAssetOut`

文本正文 + 当前版本。详情页与编辑器用，不进列表。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `content` | `string` | 是 |  |
| `version` | `number` | 是 |  |
| `sha256` | `string` | 是 |  |
| `sizeBytes` | `number` | 是 |  |
| `excerpt` | `string` | 是 |  |
| `updatedAt` | `string \| null` | 否 |  |

### `TextAssetRestoreIn`

把某一历史版本重新写成**新的一版**，不改历史行。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `version` | `number` | 是 |  |
| `expectedVersion` | `number` | 否 |  |

### `TextAssetUpdateIn`

在线编辑。`expected_version` 是**内容版本**，不是 `stateVersion`：
两个人同时打开编辑框时，后保存的人必须看到「正文已被改过」而不是静默覆盖。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `content` | `string` | 是 |  |
| `expectedVersion` | `number` | 否 |  |
| `note` | `string` | 否 |  |

### `TextAssetVersionOut`

一条历史版本。列表默认不带正文，按版本读时才带。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `version` | `number` | 是 |  |
| `sha256` | `string` | 是 |  |
| `sizeBytes` | `number` | 是 |  |
| `excerpt` | `string` | 是 |  |
| `note` | `string` | 否 |  |
| `createdBy` | `string` | 是 |  |
| `createdAt` | `string` | 是 |  |
| `content` | `string \| null` | 否 |  |

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

### `UploadKind`（枚举）

取值：`image`、`audio`、`video`、`text`

### `UserCreateIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `username` | `string` | 是 | 登录名，3-64 位字母数字或 `. _ -`，会被转成小写 |
| `password` | `string` | 是 | 初始密码，管理员指定；至少 10 位，不能是纯数字 |
| `displayName` | `string` | 否 | 昵称，留空则与用户名相同 |
| `email` | `string \| null` | 否 | 邮箱 |
| `avatarUrl` | `string \| null` | 否 | 头像 URL |
| `phone` | `string \| null` | 否 | 手机号，仅展示 |
| `position` | `string \| null` | 否 | 职务，仅展示 |
| `roleIds` | `string[]` | 否 | 角色 id 列表，可留空 |
| `groupIds` | `string[]` | 否 | 建号时加入的小组 id 列表，可留空；小组不存在会报 404 |

### `UserOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 用户 ID |
| `username` | `string` | 是 | 登录名，入库前已转小写 |
| `displayName` | `string` | 否 | 昵称（顶栏展示用） |
| `email` | `string \| null` | 否 | 邮箱，未填为 null |
| `avatarUrl` | `string \| null` | 否 | 头像 URL，未设置为 null |
| `phone` | `string \| null` | 否 | 手机号，仅展示，未填为 null |
| `position` | `string \| null` | 否 | 职务，仅展示，未填为 null |
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
| `phone` | `string \| null` | 否 | 手机号；传 null 清空 |
| `position` | `string \| null` | 否 | 职务；传 null 清空 |
| `isActive` | `boolean \| null` | 否 | 启用/停用；不能停用自己，且必须保留一个启用的超管。不接受 null：停用请传 false，不想改就不要传这个字段 |

### `ValidationError`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `loc` | `(string \| number)[]` | 是 |  |
| `msg` | `string` | 是 |  |
| `type` | `string` | 是 |  |
| `input` | `unknown` | 否 |  |
| `ctx` | `Record<string, unknown>` | 否 |  |

### `VideoTemplateAttachSourceIn`

把样片登记后的 source 资产绑定到模板：绑定后它才是「全部模板」里可选的卡片。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sourceVideoAssetId` | `string` | 是 | 原片 id（资产库里的视频） |

### `VideoTemplateDraftIn`

封装一条新的视频模板。

`mediaOrder` 决定 mixedList 顺序（即 `{{Mixed N}}` 指向谁）：显式给出时按它拼，
旧模式必须原片第1位、其余图片与素材包同一批；用途绑定模式不传 mediaOrder，
服务端按 referenceBindings 生成，保留视频所在编号并完整附带辅助参考。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `string` | 是 | 模板名称 |
| `templateKey` | `string` | 否 | 稳定标识：同键同配置返回原模板，同键异配置409；留空自动生成。固定蓝本状态独立管理 |
| `description` | `string` | 否 | 模板说明 |
| `prompt` | `string` | 是 | 固定提示词原文，含 {{Mixed N}} 占位符 |
| `sourceVideoName` | `string` | 否 | 样片文件名 |
| `sourceVideoAssetId` | `string \| null` | 否 | 样片在资产库中的 asset id |
| `sourceVideoPath` | `string` | 否 | 样片本地路径（可选） |
| `sourceDurationSec` | `number` | 否 | 样片真实时长，用于提示与档位说明 |
| `aspectRatio` | `string` | 否 | 输出画幅 |
| `packId` | `string \| null` | 否 | 关联的已发布替换素材包；**可省略**——用途绑定模式下省略即「只声明槽位」，调用时按用途从用户自己选的包里取图 |
| `defaultKind` | `"character" \| "product" \| "scene"` | 否 | 选中这张原片卡片时，替换配置默认切到的替换类型 |
| `mediaOrder` | `VideoTemplateMedia[]` | 否 | 留空则由素材包顺序生成 |
| `generation` | `Record<string, unknown>` | 否 | 生成参数，覆盖默认值 |
| `rules` | `Record<string, unknown>` | 否 | 封装规则；referenceBindings 将连续 Mixed 编号绑定到 source_video/图片用途，服务端排列声明槽位并完整附带当前包其余图片，无需逐图引用 |
| `coverUrl` | `string` | 否 | 封面地址 |
| `pinned` | `boolean` | 否 | 是否设为固定蓝本（全库仅一个） |

### `VideoTemplateListOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `VideoTemplateOut[]` | 否 |  |
| `total` | `number` | 否 |  |
| `pinnedId` | `string \| null` | 否 | 当前固定蓝本的 id |

### `VideoTemplateMedia`

mixedList 里的一项；下标 +1 就是 {{Mixed N}} 编号。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `token` | `string` | 是 | 形如 {{Mixed 3}}，与所在下标严格对应 |
| `role` | `string` | 否 | source_video / front / multiview / detail 等用途 |
| `type` | `string` | 否 | video / image / audio |
| `assetId` | `string \| null` | 否 | 素材库 asset id |
| `name` | `string` | 否 | 素材显示名 |
| `file` | `string` | 否 | 本地文件相对路径（可选） |
| `inPrompt` | `boolean` | 否 | 是否被提示词显式引用 |

### `VideoTemplateOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `templateKey` | `string` | 是 |  |
| `name` | `string` | 是 |  |
| `description` | `string` | 是 |  |
| `prompt` | `string` | 是 |  |
| `promptSha256` | `string` | 是 | 提示词 sha256，防止文字被悄悄改掉 |
| `sourceVideoName` | `string` | 是 |  |
| `sourceVideoAssetId` | `string \| null` | 否 |  |
| `sourceVideoPath` | `string` | 是 |  |
| `sourceDurationSec` | `number` | 是 |  |
| `aspectRatio` | `string` | 是 |  |
| `packId` | `string \| null` | 否 |  |
| `packName` | `string` | 否 |  |
| `packVersion` | `number` | 否 |  |
| `defaultKind` | `"character" \| "product" \| "scene"` | 否 |  |
| `mediaOrder` | `VideoTemplateMedia[]` | 否 |  |
| `generation` | `Record<string, unknown>` | 否 |  |
| `rules` | `Record<string, unknown>` | 否 |  |
| `coverUrl` | `string` | 否 |  |
| `pinned` | `boolean` | 否 |  |
| `createdAt` | `string` | 是 |  |
| `updatedAt` | `string` | 是 |  |

### `VideoTemplatePackReferenceIn`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `token` | `string` | 是 |  |
| `packId` | `string` | 是 |  |
| `packVersion` | `number` | 是 |  |
| `assetId` | `string` | 是 |  |

### `VideoTemplatePromptIn`

原地保存固定提示词及跨封装图片引用，不改原片、档位或默认素材包。

既有用途引用按新原文重算；跨封装引用锁定包与版本，图片追加到基础媒体之后。
不再点名的图片仍可作为辅助输入；references 省略时保留，显式空数组清除固定引用。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `prompt` | `string` | 是 | 新的固定提示词原文，含 {{Mixed N}} 占位符 |
| `references` | `VideoTemplatePackReferenceIn[] \| null` | 否 | 跨封装固定图片引用；省略保留原绑定，空数组清除 |

### `core__response__ApiResponse_ModelListOut___1`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `code` | `number` | 否 | 0 表示成功，其余为业务错误码 |
| `message` | `string` | 否 |  |
| `data` | `modules__catalog__schema__ModelListOut \| null` | 否 |  |

### `core__response__ApiResponse_ModelListOut___2`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `code` | `number` | 否 | 0 表示成功，其余为业务错误码 |
| `message` | `string` | 否 |  |
| `data` | `modules__model_driver__schema__ModelListOut \| null` | 否 |  |

### `core__response__ApiResponse_TaskOut___1`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `code` | `number` | 否 | 0 表示成功，其余为业务错误码 |
| `message` | `string` | 否 |  |
| `data` | `modules__model_driver__schema__TaskOut \| null` | 否 |  |

### `core__response__ApiResponse_TaskOut___2`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `code` | `number` | 否 | 0 表示成功，其余为业务错误码 |
| `message` | `string` | 否 |  |
| `data` | `modules__task__schema__TaskOut \| null` | 否 |  |

### `modules__catalog__schema__ModelListOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `modules__catalog__schema__ModelOut[]` | 否 |  |
| `total` | `number` | 否 |  |

### `modules__catalog__schema__ModelOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `label` | `string` | 是 |  |
| `capability` | `Capability` | 是 |  |
| `provider` | `string` | 是 |  |
| `description` | `string` | 否 |  |
| `paramSchema` | `Record<string, unknown>` | 否 |  |
| `maxReferences` | `number` | 否 | 这个模型最多接受几张参考图（图生图 / 多图合成），`0` 表示纯文生图。参考图不是 `paramSchema` 里的参数：它的值是「一批图片」，提交时走 `referenceLibraryIds` 这条独立入参 |
| `isDefault` | `boolean` | 否 |  |

### `modules__model_driver__schema__ModelListOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `items` | `modules__model_driver__schema__ModelOut[]` | 否 |  |
| `total` | `number` | 否 |  |

### `modules__model_driver__schema__ModelOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `label` | `string` | 是 |  |
| `capability` | `string` | 是 |  |
| `provider` | `string` | 是 |  |
| `description` | `string` | 否 |  |
| `paramSchema` | `Record<string, unknown>` | 否 |  |
| `maxReferences` | `number` | 否 | 这个模型最多接受几张参考图（图生图 / 多图合成），`0` 表示纯文生图。参考图不是 `paramSchema` 里的参数：它的值是「一批图片」，提交时走 `referenceUrls` 这条独立入参 |

### `modules__model_driver__schema__TaskOut`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 |  |
| `name` | `string` | 否 |  |
| `model` | `string` | 是 |  |
| `capability` | `string` | 是 |  |
| `status` | `string` | 是 |  |
| `progress` | `number` | 否 |  |
| `outputUrl` | `string \| null` | 否 |  |
| `coverUrl` | `string \| null` | 否 |  |
| `error` | `string \| null` | 否 |  |
| `externalId` | `string \| null` | 否 |  |
| `ownerId` | `string` | 否 |  |
| `source` | `string` | 否 |  |
| `submitAttempts` | `number` | 否 |  |
| `createdAt` | `string \| null` | 否 |  |

### `modules__task__schema__TaskOut`

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
| `referenceLibraryIds` | `string[]` | 否 | 参考图资产 id（资产库 v2），**按顺序生效**；图生视频用。只接受 `image` 类型、当前账号可读、未在回收站或草稿里的资产 |
| `textLibraryIds` | `string[]` | 否 | 绑定的文本资产 id（顺序即 `{{Text N}}` 编号） |
| `textBindings` | `Record<string, unknown>[]` | 否 | 绑定的文案与版本快照（不含正文） |
| `authorId` | `string` | 是 | 作者用户 ID |
| `authorName` | `string` | 否 | 作者昵称快照（改名不回填历史） |
| `favorited` | `boolean` | 否 | 当前账号是否已收藏 |
| `downloadable` | `boolean` | 否 | 是否允许下载：已成功且有产物 |
| `errorMessage` | `string \| null` | 否 | 失败原因，成功时为 null |
| `createdAt` | `string` | 是 | 创建时间（UTC） |
| `updatedAt` | `string` | 是 | 更新时间（UTC） |
| `startedAt` | `string \| null` | 否 | 开始生成时间，未开始为 null |
