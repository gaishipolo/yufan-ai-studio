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

  > **前端已按这个建议落地**（2026-11-05）：不在到期钩子里特判 40122，而是收在
  > `shared/access/token-refresh.ts` 的失败分支里——`adoptStoredTokens()` 会重读存储、
  > 校验是**同一个人**（换账号不跟，否则会带着本页的查询缓存），成功就继续用新票。
  > 401 拦截器与到期钩子共用这一处，所以两条路径都覆盖到了。

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

---

## 五、R10 复审修复（对上面交付的补充）

第十轮复审看的正是这一轮的提交，两类问题：契约生成物编不过、新功能的不变量只贴了一半入口。

| 编号 | 修复 | 前端要跟的 |
|---|---|---|
| R10-01 | 两个「吊销会话」接口改返回具名 `RevokedSessionsOut`；生成器对「内层不是具名模型」的封套改为内联渲染 `data` 的真实结构，不再编造类型名 | `schema.ts` 里手工补的 `dict_str__int` 别名已随重生成删除，**不要再加回去**；`EndpointData` 里两处是 `RevokedSessionsOut` |
| R10-02 | 停用 = 置 `isActive=false` + 吊销全部会话（唯一入口 `_cut_access`） | 重新启用后旧令牌仍然 401，用户必须重新登录；不要指望「启用即恢复」 |
| R10-03 | 自助改密吊销全部会话（含发起请求的这台） | `POST /me/password` 成功后所有令牌作废：请提示「密码已更新，请重新登录」并回登录页 |
| R10-04 | 停用账号在验密**之前**拒绝 | 停用账号固定 `403 / 40321`，不再随口令对错变化（原来错口令 401、对口令 403） |
| R10-05 | 读别人的登录审计加层级守卫（只挡超管） | 审计员（仅 `identity:read`）读普通账号 200、读超管 `403 / 40322` |
| R10-06 | 失败审计折叠写入 | 同一（原因, 用户名, IP）60 秒一条，`rate_limited` 15 分钟一条——「3 次错口令 = 3 条记录」不再成立 |
| R10-12 | 两个审计端点 `limit` 统一为 `default=10 / max=100` | `/users/{id}/logins` 的默认条数由 20 改为 10，需要更多就显式传 `limit` |

另外两条运维侧的变更（不影响前端）：`app prune-sessions --days 7` 清理过期会话行；
`GET /me/sessions` 截断到最近 50 条。

### 验收脚本（补充）

```
8. 停用 → 启用 → 同一张 access / refresh 均 401，重新登录 200        ✅ 实测
9. 自助改密 → 当前设备与其它设备的 access / refresh 全部 401          ✅ 实测
10. 会话列表在改密后只剩「重新登录」的那一条，不再有僵尸会话          ✅ 实测
11. 停用账号：错口令与对口令都 403 40321，连试 10 次不出 429          ✅ 实测
12. 审计员读超管登录记录 → 403 40322；读普通账号 → 200                ✅ 实测
13. 12 次错口令 → 审计新增 2 行（bad_password 1 + rate_limited 1）    ✅ 实测
```

**已知未做**（与需求 7 同类，需要单独一轮）：针对不存在用户名的审计查询端点
（`login_events.user_id` 为 NULL，`latest_for_user` 按 `user_id` 查，扫描记录只能直接查表）；
超管之间互相踢下线的应急通道（`_guard_target_not_above` 对超管一视同仁地拒绝）。
