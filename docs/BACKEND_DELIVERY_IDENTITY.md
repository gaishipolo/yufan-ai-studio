# 身份与会话 · 后端交付说明（对 `BACKEND_REQUIREMENTS_IDENTITY.md`）

> 交付方：后端 · 日期：2026-11-05 · 提交：`api/` 与 `schema.ts`（契约三件套已重生成）

## 一、逐条结论

| # | 需求 | 状态 | 说明 |
|---|---|---|---|
| 1 | 令牌续期 | ✅ 按方案 A 实现 | `refreshToken` + `refreshExpiresIn`，`POST /identity/refresh`，**轮换**（旧 refresh 立即失效） |
| 2 | 登出 / 吊销 | ✅ 实现，且**比需求多做了两件** | `POST /identity/logout` 幂等；`POST /users/{id}/revoke-sessions` 踢下线；管理员重置口令时自动吊销目标全部会话 |
| 3 | 契约生成物重生成 | ✅ 本来就已一致 | 实测 `export-openapi --check` 退出码 0。这条是基于旧状态的判断，`/images` 分页当轮就已重新生成并提交 |
| 4 | 登录审计 | ✅ 实现 | `login_events` 表；`GET /users/{id}/logins`、`GET /me/logins`；`app prune-logins` 清理 90 天前 |
| 5 | 口令策略下发 | ✅ 实现 | `GET /identity/password-policy`（匿名）；**文案也由后端给**，前端不用再拼 |
| 6 | 品牌改名 | ✅ 6 处源码 + 生成物 | 先改源码再重生成，`API.md` / `openapi.json` 的标题已同步为「雨范」 |
| 7 | 自助找回密码 | ⏸ 本轮未做 | 缺发信基础设施（无 SMTP / 邮件驱动），需要独立一轮：Mailer 协议 + 一次性令牌表 + 找回限流。**急不来，也不该顺手做** |
| 8 | 在线会话列表 / 退出所有设备 | ✅ 实现 | `GET/DELETE /me/sessions`、`DELETE /me/sessions`（保留当前） |

**需求 2 里“重置密码后旧令牌仍有效”这一条不成立**：R4-13 起 `password_changed_at`
就写进去了，解析器每请求比对 `iat`，管理员重置后旧 access 下一次请求即 401。
本轮补的是“刷新令牌也一起作废”——只让 access 失效的话，攻击者手里的 refresh
还能换出新票，自救只做了一半。

## 二、契约变化（前端要跟的四件事）

1. **`TokenOut` 新增两个字段**：`refreshToken`、`refreshExpiresIn`。登录与刷新返回的都是它。
2. **新错误码 `40122`**（刷新令牌失效）。前端处理：清本地会话 → 回登录页 →
   提示“登录状态已过期”。不要按文案判断，按 `code`。
3. **新接口 7 个**（`API.md` 已生成）：`/refresh`、`/logout`、`/password-policy`、
   `/me/sessions`（GET / DELETE / DELETE 全部）、`/me/logins`、
   `/users/{id}/revoke-sessions`、`/users/{id}/logins`。
4. **`DELETE /me/sessions` 与 `/me/sessions/{id}` 的成功响应**：前者返回
   `{ "revoked": n }`，后者返回 `data: null`。

## 三、两处行为约定（不是接口，但会影响你的实现）

- **`POST /identity/logout` 不需要有效令牌也会 200**。它是幂等的：令牌过期、已经登出过、
  干脆没带令牌，都返回 200。前端可以在 `signOut()` 里 fire-and-forget，不用等它成功再清本地。
- **同一个账号在多标签页同时续期时，只有一个会成功**（refresh 轮换）。后到的那个会拿到
  40122。建议 `useSessionExpiryWatch` 里对 40122 加一次“重读 localStorage 的 token，
  如果已经被别的标签页换过就直接用新的”分支，否则用户会被莫名其妙踢下线。
  这是本轮唯一需要前端配合的边界情况。

## 四、验收脚本（对应需求文档的验收口径）

```
1. 剩 5 分钟触发续期 → POST /refresh → 200，页面不刷新           ✅ 实测
2. refresh 过期/被吊销 → 401 40122 → 回登录页                    ✅ 实测（轮换后复用旧票）
3. 用 refresh 访问 GET /me → 401                                 ✅ 实测
4. 登出后旧令牌 GET /me → 401；重复登出 → 200                     ✅ 实测
5. 管理员重置口令 → 该用户旧 access 401、旧 refresh 401、会话列表清空  ✅ 实测
6. 3 次密码错误后成功登录 → 4 条记录，失败原因 bad_password       ✅ 实测
7. 把后端 minLength 调成 12 → 前端重新请求策略即可看到新文案       ✅ 接口已按单一来源出参
```
