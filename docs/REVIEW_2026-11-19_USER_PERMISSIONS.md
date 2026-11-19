# 用户与权限代码审查 · 2026-11-19

> 审查对象：`main` @ `640ca61`（PR #13 / #14 合并后）。
> 范围：`api/src/modules/identity/**`、`api/src/api/{authn,deps}.py`、`api/src/core/{security,permissions,context}.py`、
> `studio/src/shared/access/**`、`studio/src/features/{permission-layers,role-editor}/**`。
> 判据：`docs/REVIEW_CHECKLIST.md` §2-§4 + §6 高频反模式；机制层面参考
> `D:/xx/AI/supabase-python/SUPABASE_PYTHON_REFERENCE.md`（下称「参考文档」）。
> 状态：**只读审查**，未改动任何代码。

## 0. 一眼结论

| 项 | 值 |
|---|---|
| 发现 | **P0 × 0 · P1 × 1 · P2 × 5 · P3 × 4** |
| 复核为干净 | 提权面四入口、停用的唯一写点、refresh 轮换的条件更新、前端续期单飞、权限目录生成物 + 双守卫 |
| 最该先修 | **权限变更一条审计都没有**（§1）—— 系统有审计表、有查询命令、有导出源，唯独身份模块一次没写 |
| 整体评价 | 这套 RBAC 已经被审过十轮，注释里能查到 R4-08 → R10-12 的编号。**能提权的地方基本都堵死了**；剩下的问题集中在「出了事查不出来」和「权限点语义与路由不一致」，而不是「谁能绕过谁」 |

严重度：**P0** 阻断上线 · **P1** 正确性 / 追责 / 数据一致性 · **P2** 健壮性 · **P3** 清理与文档。

---

## 1. P1：权限变更没有任何业务留痕

**位置**：`modules/identity/service.py` 全文（无一处 `audit.record`）；对照 `modules/image/service.py:231,345`、`modules/library/**`。

审计这套东西在本仓库是**齐的**，只差身份模块接线：

| 环节 | 位置 | 状态 |
|---|---|---|
| 写入协议 | `core/audit.py:79` `record(action, actor_id, target_type, target_id, detail)` | ✅ 有，且「失败不影响业务」 |
| 落库 | `db/audit.py:32` `audit_events` 表 + `db/audit_sink.py` | ✅ 有 |
| 查询面 | `cli.py:423` `app audit --action/--target` | ✅ 有 |
| 导出源 | `modules/export/sources.py:114` | ✅ 有 |
| **身份模块写入** | `modules/identity/service.py` | ❌ **零调用** |

现状是：改权限、改角色、重置口令、停用账号，只写 `logger.info(...)`（`service.py:961,987,1005,1187...`）。
日志滚动即消失、不可按目标查询、不进导出。也就是说——

- 「3 月 2 日谁把 `editor` 的 `task:*` 加上了」：查不到；
- 「谁给这个离职账号重置过口令」：查不到（而重置口令是**最强提权面**，`service.py:964` 自己的 docstring 就这么写的）；
- 「上周那条 403 投诉，是权限被谁撤了」：只能靠猜。

登录事件表 `login_events` 存在，但它只记**登录**，不记**授权**。两者不是一回事：
前者回答「谁进来了」，后者回答「谁给了他钥匙」。

**修法**：在提权写点各加一条 `audit.record`，`detail` 用已经写好的 `_snapshot_detail`（`service.py:142`）记前后差集——这个函数目前只用于 `app_meta` 的快照说明（`service.py:1525`），等于把现成的能力闲置了。

| 动作名 | 位置 | detail |
|---|---|---|
| `identity.user.roles` | `service.py:936` | 角色 code 前后集 |
| `identity.user.permissions` | `service.py:1281` | `_snapshot_detail` |
| `identity.role.permissions` | `service.py:1094` | `_snapshot_detail` |
| `identity.role.create/delete` | `service.py:1049,1107` | code + 权限集 |
| `identity.group.members/permissions` | `service.py:1228,1250` | 成员 id 差集 / 权限差集 |
| `identity.team.permissions` | `service.py:1265` | `_snapshot_detail` |
| `identity.user.password_reset` | `service.py:964` | 不带口令，只记 actor/target |
| `identity.user.deactivate` | `service.py:989` | 停用 + 吊销会话数 |

**判别力**：一条用例把 sink 换成内存收集器，调 `set_user_permissions` 前后各取一次，
断言 `detail.added == ["task:read"]`；再删掉那行 `audit.record` → 变红。

> 为什么归 P1 而不是 P2：这不是「少了点便利」。一个 RBAC 后台在出内部事件时拿不出授权流水，
> 等于把追责成本转嫁给 DBA 捞 binlog；而 `audit_events` 的写入端与查询面都已经付过成本了，
> 现在只是没接线。

---

## 2. P2

### 2.1 「超管」有两套判定口径，`_guard_has_superuser` 用错了那套

| 判定 | 依据 | 位置 |
|---|---|---|
| 鉴权侧 | **任意层出现 `*`** 即超管等价 | `service.py:447-459`、`service.py:216`（`ResolvedIdentity.is_superuser`）、`service.py:1654`（`_require_superuser` → `actor.has("*")`） |
| 兜底守卫 | **只认 `Role.is_superuser` 角色** | `repository.py:141-152`（`count_active_superusers` join `Role`） |

`resolve_identity` 的注释明确写了「任一层出现 `*` 都等价于超管：只认角色上的 `is_superuser`
会让『给某人单独授 `*`』这条合法操作失效」——**同一个文件里，另一处的补集恰是它**。

后果分两个方向：

- **过严（当前会撞到）**：唯一持有 `*` 的人是「个人授权 `*`」而不是 admin 角色时，
  `_guard_has_superuser` 数出 0，于是改角色 / 换角色 / 删角色 / 停用全部抛 `LastSuperuser`。
  管理面看起来「坏了」，实际只是口径不一致。`app create-admin` 能救（`service.py:1544`），
  但那要求人先想到「这不像是权限问题」。
- **过松（暂不可达）**：这类超管等价账号**不受**「最后一个超管」保护。当前不可达是因为
  `_cut_access` 与 `set_user_roles` 都把计数守住了、计数到 0 就拒，所以系统永远至少有一个
  *角色*超管；一旦有人为了「临时超管」先建个人 `*` 再撤 admin 角色，就会掉进上面那个坑。

**修法**：让 `count_active_superusers` 与鉴权侧同源——统计「持有 `is_superuser` 角色的启用账号
**或** 任意层 grant / 角色权限里含 `*` 的启用账号」。SQL 上就是一次 `UNION`；
或者反过来，禁止把 `*` 写进个人/小组层（只留角色），把 `resolve_identity` 的短路删掉。
两条路都行，**但必须只有一条**：现在两套口径共存，下一个人写守卫时一定会选错。

**判别力**：`test_identity` 里造一个只有个人 `*` 的账号 + 一个 admin 角色账号，停用后者，
断言成功（而不是 `LastSuperuser`）——这条测试在现有实现下会红。

### 2.2 `identity:delete` 不是停用的闸门，`identity:update` 才是

| 路由 | 依赖 | 位置 |
|---|---|---|
| `PATCH /identity/users/{id}`（可传 `isActive: false`） | `identity:update` | `router.py:240-247` |
| `DELETE /identity/users/{id}` | `identity:delete` | `router.py:292-297` |

`UserUpdateIn` 带 `is_active`（`schema.py:134`），`update_user` 里直接走 `_cut_access`（`service.py:915-924`）。
所以：

- 想表达「可以编辑用户资料，但不能停用」——**做不到**，任何拿到 `identity:update` 的人都能停用；
- 权限目录里 `identity:delete` 写着「停用用户」（`permissions.py:56`），`identity:update` 写着「含停用」
  （`permissions.py:47-55`）——两个权限点承诺同一个能力，最小组件原则失效。

`test_permission_coverage.py` 只能证明「两个码都被判定过」，看不出「一个码的能力被另一个码覆盖」。

**修法**（两行）：`update_user` 在 `writing_active` 为真时补一道 `actor.has(PERM_DELETE)`，
或者把 `is_active` 从 `UserUpdateIn` 摘掉，停用只走 `DELETE`。
前者改动更小、对现有超管零影响。

### 2.3 团队层守卫在团队层上是空转的：一个委派管理员可以清空全站基础权限

`set_team_permissions` 有三道闸（`service.py:1265-1275`）：`_validate_team_grants`（禁 `*` 与 `identity:*`）、
`_guard_layer_not_above`、`_guard_permission_grant`。

问题在第二道：`_guard_layer_not_above` 判的是「**当前**团队授权是否 ⊆ 操作者」，
而每个人的有效权限**必然包含团队层**（`service.py:434`、`repository.py:392` 无条件并入）。
于是这条检查恒真——它不是空转，是结构上不可能失败。

结果：一个只有 `identity:read` + `identity:team:manage` 的委派管理员，可以把团队基础权限从
6 条改成 `[]`，**全站非超管立刻 403**（连 `task:read` 都没了）。这属于「一键全站降权」，
且没有二次确认、没有审计（§1）、没有下限保护。

**修法**：团队层加一条「不能清空到只剩管理类」的下限（例如禁止移除 `task:read` / `catalog:read`），
或者把「清空」单独要求超管（`len(permissions) == 0` 时 `_require_superuser`）。
两种都比现状好：现在是**破坏性操作没有任何摩擦**，正是 `docs/CODE_STYLE.md` 说的「静默丢弃比报错更坏」的同族。

### 2.4 refresh 令牌被重复使用时没有任何痕迹

`refresh` 在 `refresh_jti != jti` 时直接 401（`service.py:539-541`），注释里给了理由
（两个标签页同时续期，后到的必然拿旧 jti，当攻击处理会误伤）——**这个取舍是对的**。

但它漏了另一半：真被偷时，攻击者拿旧票先轮换 → 真实用户下一次续期 401 被踢 → 攻击者握着新票继续用。
整条链在系统里**只留下一条「某人 refresh 失败」的 401**，而 401 不进任何表。
告警、复盘、判断「这是不是同一条会话被两处使用」都无从下手。

**修法**：jti 不匹配时记一条 `audit.record("session.refresh_reuse", ...)`（带上 `sid`、`sub`、IP、UA），
但**仍然只 401、不吊销**——保住多标签页体验，同时留下可聚合的证据。
再加一个廉价计数器：同一条 `sid` 在 5 分钟内出现 ≥3 次复用 → 吊销该会话并记 `session.revoked_suspicious`。

### 2.5 `avatar_url` 的 scheme 校验只在前端

- 后端：`schema.py:125-127` / `166-168` 只有 `max_length=512`，**允许任意字符串**；
- 前端：`entities/user/model/validation.ts:108-113` 要求 `http(s)://` 或 `/`。

直接打 `PATCH /identity/me` 或 `PATCH /identity/users/{id}` 就能写入 `javascript:`、`data:`、`file://`。
今天渲染走 `<img src>`（`entities/user/ui/UserAvatar.tsx:46`），`javascript:` 不会执行，所以**现在不是 XSS**。
但这是典型的「等下一处用法」隐患：导出、审批流、邮件模板、`<a href>` 任何一处把它当链接，立刻变成存储型 XSS。
另外任意外链头像会把每个看到它的人的 IP / Referer 泄给第三方。

这套代码在别处已经立过规矩：团队层禁止 `identity:*` 时明确写着「校验放在服务层而不是只靠前端隐藏选项：
直接打接口也要挡得住」（`service.py:1144-1150`）。头像这里是同一条规矩没执行。

**修法**：`policy.py` 加 `check_avatar_url()`（与 `check_password_strength` 同形态），
在 `ProfileUpdateIn` / `UserUpdateIn` / `UserCreateIn` 的 validator 里调用；前端那份保留（即时反馈）。

---

## 3. P3

| # | 位置 | 问题 | 建议 |
|---|---|---|---|
| 3.1 | `modules/identity/policy.py:49-62` | 口令策略只挡「太短 / 纯数字 / 重复字符」。`<用户名>1234`、`公司名2024` 全部通过；`describe()` 如实返回 `forbid_same_as_username: false`（`policy.py:38`），这份诚实值得保留，但能力本身缺 | 加一条：口令不得包含用户名（忽略大小写）；成本 3 行，挡掉最常见的手滑 |
| 3.2 | `shared/access/session.ts` + `shared/api/http.ts:152` | 权限快照在 `localStorage` 缓存 5 分钟；`http.ts` 只处理 401，**403 不触发 `/identity/me` 重取**。管理员撤权后，用户界面最长 5 分钟仍显示按钮，点下去才 403 | 403 时对该 query 做一次 `invalidate`（不是全局），让界面在第一次碰壁后自愈 |
| 3.3 | `modules/identity/throttle.py:54` | 登录爆破限流是**纯进程内** `OrderedDict`。多副本 / 多 worker 时阈值 × 副本数；`docs/OPS.md:427` 只写了通用限流的 `CACHE_DRIVER=memory` 注意事项，没点名这条 | 在 OPS 补一句，或把 `_failures` 迁到 `core.ratelimit` 的计数器（已经是可换驱动） |
| 3.4 | `core/security.py:253`、`286-298` | 单密钥、无 `kid`、无时钟偏移容忍。轮换 `JWT_SECRET` = 全员下线（可接受，但没写进 OPS 的轮换步骤）；多实例时钟漂移会让 `iat`/`exp` 判定抖动 | OPS 补「轮换密钥的后果与操作顺序」；`jwt.decode` 加 `leeway=30` |

另有一条不算缺陷、但值得记录：**每个已认证请求 5~6 次查询**
（`principal.py:45-74`：用户 / 角色 / 角色权限 / 小组 id / 三层 grant + 会话）。
在 2 秒轮询的任务页上放大 30 倍/分钟/人。作者已经明确写下「不为省这几次查询把权限新鲜度换掉」，
判断合理；真要优化，方向是 `users.permissions_version` 版本号 + 短 TTL 缓存，而不是简单加 TTL
（那会把「撤权即时生效」这个卖点弄丢）。

---

## 4. 与参考文档对照：抄对的、和不该抄的

| 参考文档的机制 | 本仓库 | 结论 |
|---|---|---|
| **单飞刷新**（`session.py:371`：并发共享一个 Task + `asyncio.shield`） | `shared/access/token-refresh.ts:26` 用模块级 `inflight` Promise 单飞，且失败时先 `adoptStoredTokens()` 判断「是不是别的标签页换过了」 | ✅ **已经做对**，而且多标签页那段比参考文档更细（它只处理单进程内并发） |
| **token 轮换的正确性**（读回存储、比 `scheduled_for`） | 服务端用**条件更新** `WHERE refresh_jti = jti` + `rowcount == 1` 抢占（`service.py:557-587`），比「读-比较-写」更强 | ✅ **已经做对**，并发下只有一张票能换 |
| **算法来自 key，不来自 header**（防 alg confusion） | 单密钥 HS256、算法白名单在启动期校验（`config.py:142-162`） | ⚪ **不适用**：没有 JWKS、没有多算法协商，alg confusion 的前提不存在。等接 SSO（RS256 + JWKS）时才需要 `_bind_algorithm` 那一套 |
| **PKCE / MFA / 会话单飞（客户端）** | 无三方 IdP | ⚪ **不适用**，别为了对齐而引入 |
| **终止错误 vs 可重试错误**（4xx 才登出） | 登录失败一律 401/429，没有「网络错误导致清场」的路径（前端只在 401 且非登录接口时清场，`http.ts:152`） | ✅ 精神已具备 |
| **日志脱敏** | `core/logging.py` 的 `redact()` + `RedactingFilter`（PR #14 刚补） | ✅ 已对齐 |

---

## 5. 复核为干净（别重复审）

- **提权面四入口**：授出（`_guard_permission_grant`）、借刀（`_guard_role_grant` 的角色权限子集）、
  撤权（`_guard_layer_not_above` / `_guard_role_not_above` / `_guard_target_not_above`）、
  改标记（`update_role` 的 `is_superuser`）四条路都堵了，且 `*` 与 `is_superuser` 被当作等价物一起挡
  （`service.py:1057` 注释明说「只挡字段、不挡字符串等于没挡」）。
- **停用的唯一写点**：`_cut_access` 同时置 `is_active` + 吊销会话（`service.py:1581-1597`），
  PATCH 与 DELETE 两条路由都走它——R10-02 那个「停用即暂停」的洞确实补上了。
- **口令写入的唯一写点**：`set_password` 必带 `password_changed_at`（`service.py:1599-1612`），
  建号 / 自助改密 / 管理员重置 / CLI 轮换全走它，令牌失效判定因此只有一个依据。
- **登录路径的时序**：先付等待 → 停用早退 → 验密（线程池 + 闸门）→ 失败才计数 → 审计折叠，
  每一步的取舍都在注释里写了反例（R5-01 / R6-02 / R7-01 / R10-04 / R10-06）。
- **权限目录不可漂移**：前端 `permission-catalog.ts` 已改成薄壳，权威是后端生成物，
  CI 有字节比对；`test_permission_coverage.py` 做了双向守卫（声明必须被使用 / 使用必须被声明），
  且带元测试防止扫描器静默退化。
- **请求上下文复位**：`api/middleware.py:60` 每请求 `reset_request_context()`——
  `core/context.py:74` 那段注释描述的「上一个用户的身份串到下一个请求」不会发生。

## 6. 没覆盖到的

- MFA / WebAuthn / SSO / API Key 签发（都不存在，也就无从审）；
- 前端 mock 模式的权限面（`modules/user-access/mock.ts` 只在 `VITE_MOCK` 下生效，未逐页核）；
- 多租户隔离（本项目单租户，`permission_grants` 没有 tenant 维度）；
- 实际渗透测试（本次是静态走查，没有构造越权请求打接口）。

---

## 7. 修复记录（2026-11-19 下午）

全部 P1 + P2 已修，P3 未动（理由见 §7.4）。**真实库实测 19/19 通过**，测试数据已删净。

### 7.1 P1（1/1）

| 条目 | 落点 | 做法 |
|---|---|---|
| 权限变更无审计 | `modules/identity/service.py` 的 `_audit_change` | **16 个写点**接 `audit.record`：`user.create/roles/permissions/activate/deactivate/password_reset/password_change`、`role.create/update/permissions/delete`、`group.create/delete/members/permissions`、`team.permissions`（另有 `identity.permissions.prune` 由清理任务自己写）。`detail` 用新的 `_grant_diff()` 记前后差集（`{"added","removed","countFrom","countTo"}`），与给人读的 `_snapshot_detail()` 分开 |

**顺带**：`refresh` 的 jti 不匹配也记 `session.refresh_reuse`（P2.4，同一处改动）。

### 7.2 P2（5/5 + 真实库新发现 1 条）

| 条目 | 修法 | 判别测试 |
|---|---|---|
| 2.1 超管两套口径 | `UserRepository.count_active_superusers` 改由新的 `active_superuser_ids()` 计算：**角色 `is_superuser` ∪ 角色权限 `*` ∪ 个人授权 `*` ∪ 小组授权 `*`**，与 `resolve_identity` 同源 | `test_superuser_count_covers_star_grants` |
| 2.2 `identity:delete` 被绕过 | `update_user` 在写 `is_active` 时要求 `actor.has(PERM_DELETE)`（启用与停用两端），新错误码 `40324 AccessSwitchForbidden` | `test_patch_is_active_requires_the_delete_permission` |
| 2.3 团队层可被委派者清空 | 新增 `TEAM_FLOOR_CODES = (catalog:read, task:read, library:read)`；**移除地板项 = 超管动作**，非地板项仍可委派 | `test_team_floor_cannot_be_removed_by_a_delegate` |
| 2.4 refresh 复用无痕 | jti 不匹配时 `audit.record("session.refresh_reuse")`（带 sid / IP / UA），**仍只 401、不吊销** | `test_refresh_token_reuse_is_audited` |
| 2.5 头像 scheme 只在前端 | `policy.check_avatar_url()`（`http(s)://` 或 `/`），接进 `UserCreateIn` / `UserUpdateIn` / `ProfileUpdateIn` | `test_avatar_url_rejects_non_http_schemes`（4 个 scheme） |

**真实库实测额外抓到的第 6 条（原报告没有，因为静态走查看不出来）**

> 线上团队层里躺着 `asset:read`（v1 素材库下线）与 `template:read`（改名前），
> 而 `set_grants` 是**整组替换**、写入口拒绝未知码 —— 于是团队层**永远保存不了**：
> 前端把现有码原样提交 → `400 / 40022`。而 doctor 那句提示恰恰是
> 「可在『用户与权限』页重新保存一次清理」，在数据脏的时候是做不到的，形成死锁。
> 同样的问题对角色权限、小组授权、个人授权都成立。

修法：`IdentityService.prune_stale_permissions()`，在 `bootstrap()` 里紧跟
`sync_new_permissions()`（只加）之后执行（只减）。清理是安全的：未知码在本仓库里
可证明惰性（`test_architecture` 保证 `require()` 用到的码必须有人声明），
它唯一的作用就是把配置面卡住。删除动作本身写一条 `identity.permissions.prune` 审计。

### 7.3 新增测试

| 文件 | 条数 | 覆盖 |
|---|---|---|
| `tests/test_identity_audit.py` | 7 | 差集形状、撤权方向、角色换绑 from/to、重置口令**不含明文**、PATCH 停用留痕、refresh 复用留痕 |
| `tests/test_identity_guardrails.py` | 9 | 40324 闸门（含「闸门很窄」对照）、地板守卫三态、头像 4 种坏 scheme + 2 种好写法、超管口径、下线码死锁与 prune 幂等 |

`api/docs/API.md` 补 `40324` 一行（该表由 `export-openapi` 生成，`--check` 确认无差异）。

### 7.3.1 审计的位置有硬约束：闸门内、commit 之后

第一版把 `_audit_change` 放在 `password_slot()` **外面**，全量回归立刻红了两条——
`test_login_gate.py` 的「改密 / 重置口令不顶爆连接池」：审计走独立会话要再借一条连接，
放到闸门外就是 40 个并发一起借，正是 R8-01 描述的形状（峰值 5 > 闸门容量 4）。

改法：审计移进闸门、并放在 `self.session.commit()` **之后**——commit 已经把本请求的
连接还回池，审计再借一条，每个名额的峰值仍是 1 条。`create_user` 同处理。

> 这条值得单独记下来：**审计是旁路，但旁路也要遵守连接预算**。
> 「审计失败不能拖垮业务」和「审计不能放大并发」是两条独立的要求，
> 只做到前者会在高并发下变成「业务没挂，池先满了」。

### 7.4 有意未修（P3）

| 条目 | 为什么不动 |
|---|---|
| 3.1 口令不得含用户名 | 会**立刻废掉**种子账号 `admin/admin@12345` 与全部测试夹具的口令，属于破坏性策略变更，应当单独一轮 + 迁移说明，不能夹在权限修复里 |
| 3.2 403 触发 `/me` 重取 | 纯前端体验优化，与本次服务端改动无耦合；且要改 query 失效策略，需单独验证缓存一致性 |
| 3.3 登录限流进程内 | 迁移到 `core.ratelimit` 会改变故障半径（限流存储挂了会影响登录），需要单独的降级设计 |
| 3.4 `kid` / `leeway` | 单密钥下 `kid` 无收益；`leeway=30` 只需一行但会放松过期判定，等有多实例时钟证据再加 |

### 7.5 真实数据实测（2026-11-19 14:35 / 15:10 各一轮）

临时实例（`127.0.0.1:8099`，`WORKER_ENABLED=false` / `SCHEDULER_ENABLED=false`，连共享 PG）
跑 `.local/live_probe.py`，**两轮都是 19/19 通过**（第二轮跑在「审计挪进闸门」之后，验的是最终代码）：

- 只有 `identity:update` 的账号停用他人 → `403 / 40324`；同一请求不带 `isActive` → 200（闸门很窄）
- 委派管理员移除 `task:read` → `403 / 40322`；清空 → 403；移除 `image:read` → 200；超管恢复 → 200
- `javascript:` / `data:` 头像 → 422；`https://` 正常
- 个人 `*` 仍可授出，且被算进「超管等价」集合
- 审计真的落库（`audit_events`）：`user.create` / `role.create` / `user.permissions` / `team.permissions`，且不含口令明文

清理：探针用户 3、角色 3、绑定 3、会话 2、登录事件 2 全部删除并复核归零；
探针产生的审计记录删除，**保留** `identity.permissions.prune` 一条（那是真实数据变更的痕迹）。

**顺带被真实数据改掉的东西**（重要，需要知道）：启动时的 `prune_stale_permissions()`
把共享库里三层授权与角色权限中的**下线码**清掉了 —— 实测删除的是团队层的
`asset:read`、`template:read`（v1 素材库与旧命名的遗留）。这些码在代码里早已不可判定，
清理只是让 doctor 的那条 WARN 消失、让「重新保存」重新可用；删除动作本身留了一条审计。

> 注意：团队层现在仍缺 `library:read` / `template_pack:read`。这不是本次清理造成的
> （历史快照标记让 `ensure_team_permissions` 只播种一次），当前非超管的可读性来自各自角色。
> 若希望「团队层」也覆盖素材库，管理员在界面上勾一次即可 —— 那是配置决定，不是代码缺陷。

### 7.6 门禁（2026-11-19 16:10）

| 项 | 结果 |
|---|---|
| `pytest -q` | **1562 passed / 14 skipped / 0 failed**（1521 → 1576 条，新增 16 条） |
| `ruff check src tests` | 0 |
| `mypy --platform win32 src` | 251 files clean |
| `export-openapi --check` | 四份生成物无差异（`40324` 由错误码表自动收录） |
| `app doctor`（真实库） | 29 项、失败 0、**警告 2 → 1**；`identity.permission_layers` 从 WARN 转 PASS |
| 真实库验收 | 19/19（两轮） |

> 全量跑的过程中我改了一次错误文案（`40324` 的 message），
> `test_api_markdown_is_up_to_date` 因此红了一次 —— 那个进程在收集阶段已经
> import 了旧的 `errors.py`，重新生成出来的 md 与磁盘上的新文件当然对不上。
> 单独复跑 `tests/test_openapi_contract.py` 是 **18 passed**。这不是代码问题，
> 但值得记一笔：**别在跑全量的同时改源码**，否则红的那条会指向错的地方。



