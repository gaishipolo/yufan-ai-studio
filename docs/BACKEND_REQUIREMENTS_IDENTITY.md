# 身份与会话 · 给后端的需求清单

> **2026-11-11 状态**：本清单已全部落地（续期 `POST /identity/refresh`、会话列表/吊销、
> 登录记录、口令策略、审计），**保留作决策记录，不要当现状读**。
> 文中「三份生成物」是当时的说法，现在是**四份**（多了 `permissions.ts`）。

> 提出方：前端（`studio/`） · 日期：2026-11-05 · 对应后端模块：`api/src/modules/identity/`
>
> 背景：前端用户体系（登录、我的资料、用户管理、角色权限）已按当前后端接口全部实现并在真实后端上跑通。
> 下面这些是**前端无法自行补齐**、必须后端先动手的部分。每项都写了现状、影响、建议契约与验收口径，
> 可以直接当 issue 拆分。

## 优先级总览

| # | 需求 | 优先级 | 为什么现在就要 | 预估改动 |
|---|---|---|---|---|
| 1 | 令牌续期（refresh 或滑动过期） | P0 | 令牌只有 **2 小时**（`access_token_ttl_seconds=7200`），到点直接掉登录 | 中 |
| 2 | 登出 / 令牌吊销 | P0 | 现在「退出登录」只删本地令牌，旧令牌在过期前仍然可用 | 小 |
| 3 | 契约生成物重新生成 | P0 | `app export-openapi --check` **当前是失败的**，CI 会红 | 一条命令 |
| 4 | 登录审计（登录记录） | P1 | 只有 `lastLoginAt` 一个时间点，查不了「谁在什么时候从哪登录」 | 中 |
| 5 | 密码策略下发 | P1 | 前端硬编码「≥10 位、非纯数字」，后端改策略前端不会知道 | 小 |
| 6 | 品牌改名残留（天火 → 雨范） | P2 | 与 3 一起做，避免源码与生成物再次不一致 | 极小 |
| 7 | 自助找回密码 | P2 | 登录页现在写的是「忘记密码请联系管理员」 | 大 |
| 8 | 在线会话列表 / 退出所有设备 | P2 | 依赖 2 的吊销能力，可与其合并 | 中 |

> **状态（2026-11-05 傍晚）**：1、2、3 已由后端 `2fc0fc6` 交付，前端也已接线
> （`shared/access/token-refresh.ts` + `shared/access/end-session.ts`）。
> 这三节留档，用来解释「为什么当初要这条契约」，**不再需要后端动作**。
> 4、5、6 在后端也已交付（前端还没消费「登录记录」与「策略下发」两个接口）；7 仍缺。

---

## 已对齐，无需动作

前端已封装并实测通过的 16 个接口（`studio/src/entities/{user,role,session}/api/`）：

```
POST   /identity/login                     GET    /identity/permissions
GET    /identity/me                        GET    /identity/users
PATCH  /identity/me                        POST   /identity/users
POST   /identity/me/password               PATCH  /identity/users/{id}
GET    /identity/roles                     PUT    /identity/users/{id}/roles
POST   /identity/roles                     POST   /identity/users/{id}/reset-password
PATCH  /identity/roles/{id}                DELETE /identity/users/{id}
PUT    /identity/roles/{id}/permissions    DELETE /identity/roles/{id}
```

两点确认已经按后端实际行为校准过，不用再改：

- 重置密码字段是 `newPassword`（不是 `password`）；
- `GET /identity/roles` 返回**裸数组**，不是 `{items,total}`。

`GET /identity/users/{id}` 目前前端未使用（列表已返回全字段，没有详情页）。接口保留即可，不算缺口。

---

## 1. 令牌续期（P0）

> **已交付并已接线**（后端 `2fc0fc6` 的 `POST /identity/refresh`，前端 `token-refresh.ts`）。
> 实测：登录 `expiresIn=7200 / refreshExpiresIn=604800`，refresh 一次即轮换（旧 refresh 立刻 401，
> access 在本秒内相同、之后按新 `iat` 重签）。接线时发现原计划的「在到期钩子里加一个分支」
> 不够——并发与重放才是主体，见本节末「接线的实际形状」。

**现状**：`TokenOut.expiresIn` 固定 7200 秒，没有 refresh 接口。前端 `useSessionExpiryWatch`
按 `expiresAt` 定时清理，到点直接把用户送回登录页。

**影响**：一个内容编辑正在填一张分镜表单，两小时一到就掉线，未保存的内容全丢。
这是目前用户反馈风险最高的一条。

**建议契约**（二选一，优先 A）：

A. 独立 refresh 令牌

```http
POST /identity/refresh
{ "refreshToken": "..." }

200 { "code": 0, "message": "OK", "data": {
      "accessToken": "...", "tokenType": "Bearer", "expiresIn": 7200, "user": { ... }
}}
401 { "code": 40122, "message": "刷新令牌已失效，请重新登录" }
```

- `POST /identity/login` 的 `data` 增加 `refreshToken` 与 `refreshExpiresIn`（建议 7 天）；
- refresh 令牌必须带 `typ: "refresh"` 并在校验时拒绝用于业务接口
  （`core/security.py:223` 的注释已经预留了这个约定，照它实现即可）；
- 刷新时应轮换 refresh 令牌（旧的立即失效）。

B. 滑动过期（改动更小）

- 任一请求带的令牌剩余有效期 < 阈值（如 30 分钟）时，在响应头回写
  `X-Token-Expires-At: <ISO8601>`，前端据此延长本地计时；或直接换发新 `Authorization` 令牌。

**前端落点**（已就位，后端一通就能接）：

- `shared/access/session.ts::signIn({token, expiresIn, refreshToken, refreshExpiresIn, subject})`；
- `shared/access/token-refresh.ts::refreshAccessToken()` —— 单飞续期；
- `shared/access/use-session-watch.ts::useSessionExpiryWatch` —— 到点先续期，失败才 `endSession('expired')`。

**验收**：

1. 令牌剩 5 分钟时触发续期，用户无感知，页面不刷新；
2. refresh 令牌过期 / 被吊销 → 回登录页并提示「登录状态已过期」；
3. 用 refresh 令牌直接访问 `GET /identity/me` 必须 401。

**接线的实际形状**（原计划只写了「在到期钩子里加一个分支」，实际是三件事）：

1. **401 要重放原请求**，不能把 401 抛给页面——页面不认识「令牌刚过期」，
   只会显示错误态，用户看到的是「列表加载失败」而不是「稍等，已自动续期」；
2. **并发要单飞**：一屏 6 个请求同时 401，第一个续期成功并轮换了 refresh，
   其余 5 个拿旧票必然失败。不单飞就是「明明还在用却突然退出」；
3. **多标签页要考虑**：两边同时到点，后到的那个手里一定是旧票。
   失败时先看 `localStorage` 有没有更新的票（`adoptStoredTokens`，会校验是同一个人），
   而不是直接清场。

---

## 2. 登出 / 令牌吊销（P0）

> **已交付并已接线**。实测：登出后同一张票 `/identity/me` → 401，重复登出仍 200（幂等）。
> 前端 `endSession('manual')` 现在会先清本地、再 fire-and-forget 调 `POST /identity/logout`；
> 另外三条结束路径（过期 / 401 / 他页退出）不调，因为那时会话已经不可用。

**现状**：没有 `/logout`，也没有黑名单或 `token_version`。前端「退出登录」只在本地删令牌。

**影响**：三处具体风险

1. 员工离职时在共用电脑上点了退出，令牌被复制走仍可用满 2 小时；
2. 管理员重置密码后，**旧令牌依然有效**（`POST /users/{id}/reset-password` 不吊销会话）——
   这会让「重置密码」看起来像没生效；
3. 无法实现「退出所有设备」。

**建议契约**：

```http
POST /identity/logout                 # 当前令牌作废（幂等，已失效也返回 200）

POST /identity/users/{id}/revoke-sessions   # 管理员踢下线（需 identity:update）
```

实现上最省事的是给 `users` 加一列 `token_version: int`，签发时写进 claim，
校验时比对：与管理端「重置密码 / 停用 / 踢下线」一起自增即可，
不用维护黑名单表，也不用引入 Redis。

**前端落点**：`shared/access/end-session.ts`（四条路径的唯一出口，只有 `manual` 调后端）、
`shared/api/auth.ts::requestLogout`（旁路通道，显式带清场前那张令牌）、
`shared/api/http.ts::finishUnauthorized`（401 已经改成「先续期再重放」）。

**验收**：

1. 登出后拿旧令牌调 `GET /identity/me` → 401；
2. 管理员重置某用户密码后，该用户旧令牌立即 401（前端会自动跳登录页并提示「已在别处被重置」——
   这条提示文案已经写好了，见 `pages/login/LoginPage.tsx::SESSION_ENDED_NOTICE.unauthorized`）。

---

## 3. 契约生成物重新生成（P0，工程项）

> **已交付**（后端 R10-01）。`app export-openapi --check` 现在退出码 0，
> `schema.ts` 用修好的生成器重新产出了 749 行，前端那 13 行手改已撤掉。
> 这条的收益已经体现在 CI 里：`.github/workflows/ci.yml` 有一条专门跑 `--check`。

**现状**：`cd api && ./.venv/Scripts/app.exe export-openapi --check` **失败**，三个文件与代码不一致：

```
api/docs/openapi.json
api/docs/API.md
studio/src/shared/api/schema.ts
```

**已定位到的漂移**：`GET /api/v1/images` 的代码已经改成**分页**（新增 `page` / `pageSize` 查询参数，
响应从 `ImageJobListOut` 换成 `Page[ImageJobOut]`），但生成物还是旧版。

前端核对过：`Paginated<ImageJob>`（`items/total/page/pageSize`）与新的 `Page[ImageJobOut]` **完全一致**，
页面不受影响。所以这只是文档与 CI 的问题，不是线上故障。

**请做**：跑一次 `app export-openapi`，把三份生成物与本批改动一起提交。
如果同期还有其他后端改动，请在提交信息里注明，便于前端判断要不要跟进类型。

**验收**：`app export-openapi --check` 退出码 0；`pytest tests/test_openapi_contract.py` 通过。

### 3.1 补一个 `RevokedSessionsOut`（生成物里有一个只有名字没有定义的类型）

**现状**：`2fc0fc6` 重新生成三份生成物之后，`studio/src/shared/api/schema.ts` **编译不过**：

```
src/shared/api/schema.ts(678,42): error TS2304: Cannot find name 'dict_str__int'
src/shared/api/schema.ts(714,60): error TS2304: Cannot find name 'dict_str__int'
```

`api/docs/API.md` 里同样只写了 `ApiResponse<dict_str__int>` 这个名字，没有定义。
来源是两个返回 `dict[str, int]` 的内联响应（都是 `{"revoked": n}`）：

- `DELETE /identity/me/sessions`（退出所有其他设备）
- `POST /identity/users/{user_id}/revoke-sessions`（踢下线）

生成器把 `dict[str, int]` 拼成了类型名，但没有产出对应定义 —— 共享 schema 里出现"引用不存在"，
`tsc -b` 直接失败，前端所有构建都被挡住。

**请做**：给这两个接口定义一个具名 Schema（名字建议 `RevokedSessionsOut`，字段
`revoked: int`），把路由签名从 `ApiResponse[dict[str, int]]` 改过去，再跑一次 `app export-openapi`。

**前端临时处理**：`schema.ts` 里加了一行 `export type dict_str__int = { revoked: number }` 并标了注释。
后端出正式 Schema 后，这一行会随生成物一起被覆盖，届时确认不再需要即可。

**验收**：`cd studio && npx tsc -b` 静默；`grep dict_str__int studio/src/shared/api/schema.ts` 无输出。

---

## 4. 登录审计（P1）

**现状**：`users.last_login_at` 只存最后一次成功登录的时间，没有历史，也没有失败记录、IP、UA。

**影响**：安全事件无法追溯（「这个账号上周三凌晨是不是别人登的」）；也无法做「异常登录提醒」。
目前界面上「最近登录」这一列的全部数据只有一个时间点。

**建议契约（最小可用）**：

```http
GET /identity/users/{id}/logins?limit=20      # 需 identity:read

200 { "code": 0, "data": { "items": [ {
      "at": "2026-11-05T02:11:03Z",
      "success": true,
      "ip": "10.2.3.4",
      "userAgent": "Mozilla/5.0 ...",
      "reason": null            // 失败时为 "bad_password" / "disabled" / "rate_limited"
} ] } }
```

另加 `GET /identity/me/logins?limit=10`（本人看自己的，不需要 `identity:read`）。

- 落库建议单独一张 `login_events` 表（user_id, at, success, ip, ua, reason），按时间索引；
- 只保留 90 天，或定期归档——不要把审计写进 `users` 表；
- 写入点复用现有 `IdentityService` 的登录分支，与限流器（`modules/identity/throttle.py`）共用一次请求上下文。

**验收**：连续 3 次密码错误后再成功登录，能查到 4 条记录且失败原因正确。

---

## 5. 密码策略下发（P1）

**现状**：前端在 `studio/src/entities/user/model/validation.ts` 里硬编码
`PASSWORD_MIN_LENGTH = 10` 与「不能是纯数字」，并在 4 个入口使用
（建号、管理员重置、本人改密、两个 mock）。

**影响**：后端哪天把策略改成 12 位 + 必须含字母，前端四处提示仍然是「至少 10 位」，
用户按提示输入却被 422 拒绝。这正是本次重构刚统一掉的重复（原来散在 5 个文件里）。

**建议契约**：

```http
GET /identity/password-policy

200 { "code": 0, "data": {
      "minLength": 10,
      "maxLength": 128,
      "forbidAllNumeric": true,
      "forbidSameAsUsername": false,
      "message": "至少 10 位，不能是纯数字"     // 直接可展示的一句话，前端不再拼文案
} }
```

接口可匿名访问（登录页 / 重置页都要用），也可以挂在 `GET /identity/me` 的响应里
（前提是登录前也能拿到，所以还是独立接口更合适）。

**验收**：把后端 `minLength` 调成 12，前端不重新构建即可在表单提示里看到「至少 12 位」。

---

## 6. 品牌改名残留（P2）

前端与文档已全部改为「雨范」（`index.html`、`TopBar`、登录页、`.env.*`、README、`docs/*.md`）。
后端还有 6 处「天火」，建议**和第 3 条一起**改，否则源码与生成物会再次不一致：

| 文件 | 位置 |
|---|---|
| `api/src/core/config.py` | `app_name: str = "天火 AI 短视频工作台 API"` |
| `api/src/app.py` | 模块 docstring 第 36 行 |
| `api/src/cli.py` | `argparse(description=...)` 第 187 行 |
| `api/pyproject.toml` | `description` |
| `api/README.md` | 标题 |
| `api/docs/openapi.json`、`api/docs/API.md` | 生成物（随第 3 条重生成） |

注意：**先改源码再重生成**，不要只改生成物（会被下次导出覆盖）。

---

## 7. 自助找回密码（P2）

**现状**：登录页文案是「使用工作台账号登录；忘记密码请联系管理员重置」，只有管理员重置一条路径，
且新密码要线下口头告知（`ResetPasswordModal` 的提示语就是这么写的）。

**建议**（若要做）：`POST /identity/password/forgot`（发邮件验证码）→
`POST /identity/password/reset-by-token`。前端登录页已经预留了位置，
加一个「忘记密码」链接即可，不需要改骨架。

**风险提示**：这会引入「凭邮箱接管账号」的攻击面。当前后端在
`update_user` 里已经按「改他人邮箱需要目标不高于自己」做了限制（`service.py` 注释 R7-03），
做找回功能时请沿用同一套判断，别绕过。

---

## 8. 在线会话列表 / 退出所有设备（P2）

依赖第 2 条的吊销能力。建议合并实现：

```http
GET    /identity/me/sessions          # 当前账号所有有效会话
DELETE /identity/me/sessions/{sid}    # 踢掉指定会话
DELETE /identity/me/sessions          # 退出所有设备（保留当前）
```

需要 `token_version` 之外记录会话 id 才能「只踢某台设备」。如果只做「退出所有设备」，
单靠 `token_version` 自增就够了，前端加一个按钮即可。

---

## 附：前端已经自行消化掉的，后端不用重复做

为避免后端重复建设，说明一下这些事目前在前端是怎么处理的：

| 事情 | 前端做法 | 位置 |
|---|---|---|
| 会话到期 / 401 / 跨标签页退出 | 三态 store + 定时清理 + `storage` 事件 | `shared/access/session.ts`、`use-session-watch.ts` |
| 登录失败分类（401/403/429/超时/断网/5xx） | 按 `code // 100` 取状态段，不枚举业务码 | `features/auth/login-feedback.ts` |
| 登录限流倒计时 | 从 429 文案里解析分钟数，解析不到兜底 60 秒 | 同上 |
| 权限门禁 | `Subject.permissions` + `<Can>` / `RequirePermission`；`'*'` 为超管 | `shared/access/` |
| 操作自己 | 前端禁用「停用自己」并给出说明，不依赖后端 409 提示 | `pages/user-access/UsersSection.tsx` |
| 头像加载失败 | 自动退回首字母，不显示碎图 | `entities/user/ui/UserAvatar.tsx` |

**唯一需要后端兜底的**：上面这些只是 UI 层的「少一次往返」，
真正的判定（谁有权做什么、令牌是否有效）必须仍然在后端做。
