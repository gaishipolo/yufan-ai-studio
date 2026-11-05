# 代码评审报告（第十轮 · 仅后端 · 会话层新功能 + R9 复核）

> 评审时间：2026-11-05 17:23 ｜ 范围：`2fc0fc6`（刷新令牌 / 登出吊销 / 会话列表 / 登录审计 / 策略下发 / 品牌改名）与 `0a2d91b`（R9 修复）；前端不在范围内
> 关联：[`R9`](./CODE_REVIEW_2026-11-05_R9.md)｜[`R8`](./CODE_REVIEW_2026-11-05_R8.md)｜[`R7`](./CODE_REVIEW_2026-11-05_R7.md)｜[`R6`](./CODE_REVIEW_2026-11-05_R6.md)｜[`R5`](./CODE_REVIEW_2026-11-05_R5.md)｜[`R4`](./CODE_REVIEW_2026-11-05_R4.md)｜[`R3`](./CODE_REVIEW_2026-11-05_R3.md)｜[`R2`](./CODE_REVIEW_2026-11-05_R2.md)｜[`首轮`](./CODE_REVIEW_2026-11-05.md)｜[`CODE_STYLE.md`](./CODE_STYLE.md) / [`REVIEW_CHECKLIST.md`](./REVIEW_CHECKLIST.md)

---

## 一、结论

这一轮不是修 bug，是**补了一整层"令牌有状态"**：`user_sessions` 表、`sid` 每请求校验、refresh 轮换、登出吊销、登录审计。做法和注释质量都是十轮里最高的一次——它没有把"登出"实现成"前端删本地副本"，而是承认了「签名有效 ≠ 还被允许使用」，并把这句话写进了 `EXTENDING.md` §2.6 的"不要改的约定"。

实测这层是成立的：

```
登出 → /me 401，再次登出 200（幂等）
踢单台 → 被踢那台 401，当前这台 200
踢超管 → 403 40322
旧 refresh 二次使用 → 401 40122；新 refresh 可继续续期
refresh 当 access 用 → 401；access 当 refresh 用 → 401
重置口令后 refresh → 401 40122
20 / 40 并发失败登录 → 连接峰值 4~5，期间 select 1 延迟 3 ms
```

**但这轮有两类问题，一类是"新功能自己没长齐"，另一类是"新功能把别人的线踩断了"。**

先说要紧的那条：**契约三件套里的 `studio/src/shared/api/schema.ts` 编不过**。两个新接口返回 `dict[str, int]`，TS 生成器把 `ApiResponse_dict_str__int__` 的名字切成 `dict_str__int` 就直接当类型用，从不检查它是否存在：

```
$ git show HEAD:studio/src/shared/api/schema.ts > /tmp/x.ts && npx tsc --noEmit /tmp/x.ts
x.ts(678,42): error TS2304: Cannot find name 'dict_str__int'.
x.ts(714,60): error TS2304: Cannot find name 'dict_str__int'.
```

而这份文件在厂库里是**已经被手工改过**的（工作区 `M studio/src/shared/api/schema.ts`，17:28）：前端同学补了一个 `dict_str__int` 别名，注释写着"生成器为它产出了引用名却没有产出定义……所以这份文件一直编不过"。手工补完，`tsc -b` 绿了，但后端 `test_openapi_contract.py` 立刻红——因为它是字节比对。**两头堵死，而且不是前端的错。**

至于"新功能没长齐"：**吊销会话这条不变量只贴了三个入口（登出 / 踢下线 / 重置口令），漏掉了两个最容易被当成"切断"的动作：停用、自助改密。** 于是有一条在真实运维动作下走得通的路径：

```
管理员停用某账号（应急处置）→ 该账号的 access 与 refresh 同时 401
管理员重新启用（误操作纠正 / 临时冻结解除）
→ 同一张 access 200，同一张 refresh 也能换票 200
```

`refresh` 寿命 **7 天**且每次续期滑动延长。"停用"在产品语义上成了一次**暂停**——而它恰恰是管理员以为自己在"切断"的那个动作。

### R9 逐条复核（全部实测）

| 编号 | 结论 | 证据 |
|---|---|---|
| R9-01 `reset_password` 闸门在守卫之后 | ✅ 修好 | `service.py:767` 是 `async with password_slot():` 打头，`_require_user`（:768）在其内 |
| R9-02 资料守卫用错比较基础 | ✅ 修好 | 委派角色（`identity:read`+`update`）改 admin 昵称 **403/40322**、改 low02 **200**；邮箱与停用两档同样按目标分级 |
| R9-03 用例只覆盖超管分支 | ✅ 修好 | `test_identity_api.py` 新增 4 处目标为普通账号的 `40323` 断言 |
| R9-04 峰值断言被限流兜住 | ✅ 修好 | 新增 `test_reset_password_does_not_exhaust_the_connection_pool`；注释写明"限流不介入，峰值只能由闸门给出" |

### 质量基线

```
pytest         204 passed（其中 1 条 test_openapi_contract 在工作区手改 schema.ts 后转红）
ruff           All checks passed
mypy           Success: no issues found in 110 source files
export-openapi 无变化（json / md / ts 三件套，与生成器一致）
npx tsc -b     工作区绿（靠手改 schema.ts 换来）；HEAD 版红，两处 TS2304
```

迁移 `4d2b8f6a1c90`：两张表、索引齐（`user_id` / `expires_at` / `created_at`），`downgrade` 与 `upgrade` 对称，不动存量数据。

---

## 二、🔴 R10-01 TS 生成器对"非命名模型的泛型参数"编造类型名，导致契约生成物编译不过

**位置**：`src/openapi_export.py:241-253`（`_response_data_type`）、`:57-70`（`_type_name`）
**受害接口**：`DELETE /identity/me/sessions`、`POST /identity/users/{id}/revoke-sessions`（都返回 `ApiResponse[dict[str, int]]`）

```python
if name.startswith("ApiResponse_"):
    return _type_name(name[len("ApiResponse_"):])   # ← 切完直接当类型名，不查存在性
```

`ApiResponse_dict_str__int__` → 去掉前缀、`rstrip("_")` → `dict_str__int` → 不匹配任何泛型前缀 → **原样返回**。而 `dict_str__int` 并不是 `components.schemas` 里的条目：

```
openapi.json:  'dict_str__int' in components.schemas  →  False
               ApiResponse_dict_str__int__.data      →  anyOf[{additionalProperties:{integer},type:object}, null]
```

真实形状在 `data` 里是完整内联的（`_ts_type` 完全有能力渲染成 `Record<string, number>`），只是 `_response_data_type` 走了"按名字引用"的捷径，没回头看这个名字存不存在。`_is_wrapper` 又把 `ApiResponse_*` 从"模型"区跳过了，所以那个名字连"恰好被定义出来"的机会都没有。

**为什么定🔴**：`schema.ts` 是前端唯一契约来源（`docs/EXTENDING.md` 与 `api/docs/API.md` 都这么写）。它编不过，等于这一轮新增的两个接口在前端**无法类型化使用**，而绕过去的方式只有两种，两种都错：手改生成物（现在这个状态，`pytest` 红）、或者不用类型（这轮新增的会话管理正好需要那个 `{revoked: number}`）。

**实测**：

```
# 1) 提交进库的版本
$ npx tsc -b --force
src/shared/api/schema.ts(678,42): error TS2304: Cannot find name 'dict_str__int'.
src/shared/api/schema.ts(714,60): error TS2304: Cannot find name 'dict_str__int'.

# 2) 手工补上别名之后
$ npx tsc -b            → 绿
$ pytest tests/test_openapi_contract.py
  test_typescript_schema_is_up_to_date → FAILED（字节比对不一致）
```

**建议修法**（两层，都要）：

1. **后端具名 schema（止血 + 契约更可读）**：

   ```python
   class RevokedSessionsOut(CamelModel):
       revoked: int = Field(description="本次吊销的会话数")
   ```

   两个接口改成 `ApiResponse[RevokedSessionsOut]`。`{"revoked": n}` 本来就该有名字——`dict[str, int]` 把"字段叫什么、代表什么"丢给了注释。

2. **生成器校验存在性（根治）**：`_response_data_type` 切出内层名后，先查 `components.schemas`；不存在就回退到展开 `ApiResponse_X.properties.data` 的真实 schema。这样将来任何人写 `ApiResponse[dict[str, int]]`、`ApiResponse[list[str]]` 都不会再产出一个幽灵类型名。

3. **契约测试加一条"能编译"**：现在的 `test_openapi_contract.py` 只做字节比对——它保证"生成物和生成器一致"，不保证"生成器产出了合法代码"。这两件事这轮正好分开了。建议在 CI 里补 `npx tsc -b`（前端已有脚本，只是没进流水线，`FE2` 也提过零 CI）。

**附带一条流程问题**：`schema.ts` 头部写着"自动生成，请勿手工编辑"，但它是唯一能让前端编过的办法。生成物被手改，说明生成链在某处断了——**这类"只能手改生成物"的情况应当当作生成器的 bug 上报，而不是当作前端临时方案消化掉**，否则下一次 `export-openapi` 会把它冲掉，双方各红一次。

---

## 三、🟡 R10-02 停用不吊销会话：重新启用后，旧 access 与 refresh **双双复活**

**位置**：`src/modules/identity/service.py:778-792`（`deactivate_user`）、`:697-713`（`update_user` 的 `writing_active` 分支）、对照 `:774`（`reset_password` 的 `revoke_all`）
**解析侧**：`src/modules/identity/principal.py:47`（只判 `is_active`）、`:66`（`sid` 有效即放行）

两条停用路径（`DELETE /users/{id}` 和 `PATCH {"isActive": false}`）都只写 `is_active`，**不碰会话表**。会话的 `revoked_at` 仍是 `None`、`expires_at` 仍是登录时算的 7 天后——所以解析器在停用期间拦得住（`is_active` 为假），启用后再检查时**四个条件全部重新满足**。

实测（真实登录签发的两张票，全程复用）：

```
停用前 /me                      200
管理员停用                      200 用户已停用
停用期间 /me                    401            ← 看起来生效了
停用期间 refresh                401 40122
管理员重新启用                  200 OK
启用后同一张旧 access /me       200            ← 复活
启用后同一张旧 refresh          200 OK         ← 复活，且寿命还剩 7 天
当前会话数                      2（会话从未被吊销）
```

**为什么这条比"改密"更值得修**：`reset_password` 已经做了 `revoke_all`，注释说得很清楚——"改密本身已经让旧 access 失效，但会话列表里还挂着几台设备会让管理员以为没踢干净"。`deactivate` 缺的正是同一句话。区别在后果：改密靠 `password_changed_at` 兜住了 access，停用**只靠一个布尔字段**，而这个字段是可以被改回来的。

**组合风险**：假设某账号因可疑活动被停用，攻击者手里的 refresh（7 天、滑动续期）在此期间 401 但**不失效**。只要管理员在 7 天内启用一次（哪怕只是误判纠正），票立刻恢复，且不再需要知道口令。R10-04 让这个组合更容易成立。

**建议修法**：

1. `deactivate_user` 与 `update_user` 的 `is_active is False` 分支各自 `await self.sessions.revoke_all(user.id, reason="deactivate")`；
2. 更好的位置是**把"停用"收进一个私有方法**（`_cut_access(user, reason)`：置 `is_active` + `revoke_all`），让将来新增的停用入口不可能漏。这轮的问题本质就是"同一个不变量有两个写点，两个都漏了"。

并把这条写进 `EXTENDING.md` §2.6 的"不要改的约定"：**停用 = 置 `is_active` + 吊销会话，两件事必须一起做**。现在的列表有"登出 / 踢下线 / 改密让 refresh 失效"，唯独没有停用，读文档的人不会想到还差这一步。

---

## 四、🟡 R10-03 自助改密后，`/me/sessions` 把 4 条僵尸会话列成有效

**位置**：`src/modules/identity/service.py:598-621`（`change_password`）、`src/modules/identity/repository.py:208-218`（`list_for_user`）
**对照**：`service.py:774`（`reset_password` 的 `revoke_all`）

`change_password` 只 `commit`，不 `revoke_all`。它靠 `password_changed_at` 让访问票失效（这是对的，R8 验证过），但**会话表里的行没有任何标记**。而 `list_for_user` 的"有效"定义是 `revoked_at IS NULL AND expires_at > now`——僵尸会话全部满足。

```
改密前会话数                        4
改密                                200 密码已更新
改密后同一张 access /me             200          ← 当前设备保持登录，符合预期
改密后用旧口令登录                  401
改密后重新登录，会话列表条数        5
  516f7df5 current=True
  fe70ba6b current=False
  09bc579a current=False     ← 这四条实际都换不出票了
  65450f18 current=False
  eb0a9667 current=False
```

用户打开"我的登录设备"会看到 4 台"在线"，而它们已经不可用，要等 7 天 `expires_at` 才从列表消失。这正是 `reset_password` 注释里点出的那种"看起来没踢干净"。

顺带一个秒级窗口：`password_changed_at` 与 `iat` 都是秒精度，`principal.py` 用 `<` 比较。同一秒内签发再改密的令牌不会被判失效——测试里改密后立刻用同一张 access 访问 `/me` 得到的就是 200。真实攻击者很难命中这一秒，但前端若依赖"改密后立刻换票"，会在开发环境里偶发对不上。

**建议**：`change_password` 里同样 `revoke_all(reason="password_change")`。当前设备会因此 401，所以前端要么"先 `/refresh` 再改密"、要么"改密后回登录页"——这个交互决定属于前端，但**后端应该先给出确定的状态**，而不是让会话列表说谎。

---

## 五、🟡 R10-04 停用账号的登录尝试泄漏口令正确性

**位置**：`src/modules/identity/service.py:274-285`（`throttle.reset(key)` 在 `is_active` 判断之前）

```python
ok = await verify_password_async(password, encoded)
if user is None or not ok:
    ... record_failure / _audit_login / reject → 401     # 错口令
throttle.reset(key)                                      # ← 先清零
if not user.is_active:
    ... _audit_login(disabled) → 403                      # 对口令
```

```
停用账号 + 错口令       → 401 用户名或密码错误
停用账号 + 对口令       → 403 账号已停用，请联系管理员      ← 可区分
停用账号 10 次错口令    → [401×5, 429×5]                  ← 限流生效
停用账号 10 次对口令    → [403×10]                        ← 每次 reset，永不限流
不存在用户名            → 401                              ← 与错口令一致，不能枚举存在性
```

两点说清楚：

- **不能枚举账号存在性**（这点比预想的好）：停用账号错口令也是 401，与不存在用户名一致。要拿到 403 必须先猜对口令。
- **但口令正确性泄漏了**：403 与 401 的差异就是"你猜对了"。攻击者对**已知/疑似停用**的账号（离职员工、被冻结的可疑账号）可以慢速验证口令。

而且对口令路径每次 `reset` 计数，所以**它的 403 永远不会被限流**——一个停用账号可以被无限次验证（成本是攻击者自己的 CPU）。

**建议**：把 `is_active` 判断挪到 `verify_password_async` **之前**。收益三重——不泄漏口令正确性、每个停用账号的尝试少一次 150ms scrypt（DoS 面也变好）、审计里仍记 `disabled`。代价是"账号存在且被停用"仍可见（403 vs 401），这点和产品确认即可；如果连这个也不想露，就统一 401 并把原因只写进审计。

---

## 六、🟡 R10-05 `identity:read` 能读超管的登录记录，没有层级守卫

**位置**：`src/modules/identity/service.py:528-530`（`logins_of` 只有 `_require_user`）、`src/modules/identity/router.py:242-250`（只挂 `IdentityReadDep`）
**对照**：`service.py:510-518`（`revoke_user_sessions` 有 `_guard_target_not_above`）

```
审计员 auditor01（仅 identity:read）读超管登录记录 → 200
  拿到 1 条：ip='…' ua='…'
对超管踢下线（对照）                              → 403 缺少权限: identity:update
```

这是"守卫只贴写操作"的又一次。读登录记录不是写操作，但拿到的是 **IP + User-Agent + 失败原因**——足以推断超管的常用网络、设备、作息，以及"凌晨三点有一次失败登录"这种安全事件。R9 把 `identity:read` 的语义定为"能看用户管理页面"，而用户列表里没有 IP。

**建议**：`logins_of` 也过 `_guard_target_not_above`（与踢下线同级）。如果业务上确实需要"审计员能查所有人"，就单独开 `identity:audit:read` 并写进权限说明——**但不要让它悄悄跟着 `identity:read` 一起来**。

---

## 七、🟡 R10-06 登录审计表可被匿名流量线性填充

**位置**：`src/modules/identity/service.py:297-329`（`_audit_login`，每次失败都 `insert + commit`）

限流键是 `用户名|客户端 IP`（`service.py:243`，`throttle.py` 模块注释解释了为什么不是只按 IP 或只按用户名）。副作用：**换用户名就等于换键**，新键的桶是空的，`penalty()` 返回 0。

```
同一用户名 8 次（editor）      [401×5, 429×3]   4.2 s   ← 有惩罚
换 8 个用户名（scan0..7）      [401×8]          1.3 s   ← 零惩罚
24 个不同用户名                全部 401          3.5 s   ← 24 行审计
```

再加上"被 `rate_limited` 的那次也写审计"（`service.py:281` 的 `reason="rate_limited"` 是刻意的，为了留痕），实际是：

```
12 次错误口令 → 审计新增 12 行：{'bad_password': 5, 'rate_limited': 7}
```

即**限流完全没有减少写入成本**——被拒的请求照样 insert + commit 一行。匿名方以约 7 次/秒（受 4 个 scrypt worker 限制）写表，一天 60 万行量级。`prune-logins` 只按 90 天清理，且要人工跑。

**建议**（按性价比）：

1. `rate_limited` 的尝试不写审计，改为"该键每窗口写一条汇总"。第一次 429 已经说明问题，第 2~N 次的边际信息为零。
2. 给 `login_events` 加一个进程内"同 username 每 N 秒最多一条失败"的节流（复用 `throttle` 的 LRU），把爆破流量折叠成一行。

不建议把审计挪到闸门外——那会把峰值连接重新耦合到审计写入（R7-01 的形状）。

---

## 八、💭 观察项

| 编号 | 事项 | 说明 |
|---|---|---|
| R10-07 | 会话表只增不减，`/me/sessions` 无上限 | `list_for_user` 走 `self.list()`（`db/repository.py:49` 不截断）；没有 `prune-sessions`，过期会话永久留在表里（`prune-logins` 只清 `login_events`）。建议加清理命令并给列表加 limit |
| R10-08 | 无 `sid` 令牌的迁移窗口没有终点 | `principal.py:66` 对空 `sid` 跳过会话校验，`EXTENDING.md` §2.6 明确"不要改"。代价是升级后 2 小时内存在**既不能登出也不能踢下线**的令牌。建议文档写明窗口，或发布完成后加开关拒绝无 `sid` 令牌 |
| R10-09 | refresh 并发无乐观锁 | 两个标签页同时续期，若都读到旧 `jti`，两个都返回 200，后提交的覆盖先提交的 → 先返回的新 refresh 立即失效。只影响体验；注释承认了"后到的拿旧 jti 只拒绝"，但没覆盖"两个都读到旧值" |
| R10-10 | 没人能踢超管下线 | `revoke_user_sessions` 有 `_guard_target_not_above` → 超管互相也踢不动。若只有一个超管且会话被盗，没有第二个人能切断。建议允许"超管踢超管"，或把应急通道写进运维文档 |
| R10-11 | 针对不存在用户名的审计写了但查不到 | `login_events.user_id` 为 NULL，而 `latest_for_user` 按 `user_id` 查 → "扫描不存在账号"的记录只能进 DB 看。建议加按 `username` 查的管理端点，或运维文档写明直接查表 |
| R10-12 | 两个审计端点 `limit` 上限不同 | `/me/logins` 是 `le=100/default=10`，`/users/{id}/logins` 是 `le=200/default=20`。契约里是对的（不是 bug），但同一语义两个口径，前端容易记错。统一成一套常量即可 |

---

## 九、一个反复出现的形状

十轮下来，同一类问题出现了六次：

| 轮次 | 不变量 | 守住的入口 | 漏掉的入口 |
|---|---|---|---|
| R5-03 | 不能操作比自己高的账号 | 重置口令 | 改角色 |
| R6-04 | 停用超管要拦 | 停用 | 启用 |
| R8-01 | 闸门在查库之前 | `authenticate` | `change_password` |
| R9-02 | 目标不高于自己 | 写权限类字段 | 写展示字段（守过头） |
| **R10-02** | **停用 = 切断访问** | **登出 / 踢下线 / 重置口令** | **停用、自助改密** |
| **R10-05** | **不能读比自己高的账号** | **写操作** | **读审计** |

共同点：**不变量写在注释和文档里，实现只在"当时想到的那个入口"落地。** 前五轮都靠"记得下次也补上"在修，于是下一轮换个入口又出现。

`REVIEW_CHECKLIST.md` 已经有"加限制时先数一遍同类入口"，但这一轮说明"数一遍"不够——`deactivate_user` 和 `reset_password` 挨在同一个文件里、相隔 20 行，写法却不同。更有效的是**把不变量收进一个唯一写点**：

- 停用 → `_cut_access(user, reason)`（置 `is_active` + `revoke_all`）
- 目标层级 → 一个守卫入口，内部按"写访问权还是写展示字段"选档（R9 已经这么做，效果好）

判断标准可以写成一句话：**如果一条规则需要在两个地方各写一遍才能成立，那它迟早只在一个地方成立。**

---

## 十、建议顺序

1. **R10-01 契约生成物编不过**（后端加 `RevokedSessionsOut` + 生成器校验存在性，约 30 行）——这是唯一"现在就在阻塞别人"的一条：前端已经只能靠手改生成物工作，而手改会让后端测试红。
2. **R10-02 停用不动会话**（约 15 行 + 1 条用例"停用→启用后旧票必须 401"）——唯一会在真实运维动作下被踩到的安全项。
3. **R10-04 停用分支顺序**（挪 5 行）——与 R10-02 同文件、同一条路径，一起做完省一次回归。
4. **R10-03 自助改密吊销会话**（1 行 + 会话列表断言）——顺带解决"列表说谎"。
5. **R10-05 读审计的层级守卫**（1 行）——与 R10-02 同一次提交。
6. **R10-06 审计写入节流**（`rate_limited` 不写 + 同键折叠）——纯新增逻辑，可单独一轮。
7. R10-07 / R10-08 写进文档与运维手册，R10-09~R10-12 随时。

---

## 附录：复现方式

所有探针都跑在临时 sqlite + `TestClient` + 真实登录签发的令牌上。

**R10-01 契约编不过**：

```bash
cd studio && git show HEAD:src/shared/api/schema.ts > /tmp/x.ts
npx tsc --noEmit --strict /tmp/x.ts        # TS2304 ×2
npx tsc -b --force                          # 直接改回工作区版本则是这两行
```

**R10-02 停用后启用**：

```python
DELETE /identity/users/{editor_id}                     → 200
GET    /identity/me  (旧 access)                       → 401
POST   /identity/refresh (旧 refresh)                  → 401 40122
PATCH  /identity/users/{editor_id} {"isActive": true}  → 200
GET    /identity/me  (同一张旧 access)                 → 200   ← R10-02
POST   /identity/refresh (同一张旧 refresh)            → 200   ← R10-02
```

**R10-03 僵尸会话**：登录 4 次 → `POST /identity/me/password` → 重新登录 → `GET /identity/me/sessions` 返回 5 条，其中 4 条 `current=false` 且已不可用。

**R10-04 口令正确性**：停用账号后用错口令 → 401；用对口令 → 403。10 次对口令全 403 不触发 429。

**R10-05 读超管审计**：建 `auditor01`（角色权限仅 `identity:read`）→ `GET /identity/users/{admin_id}/logins` → 200 且含 `ip` / `userAgent`。

**R10-06 审计填充**：同一用户名 8 次 → 4.2s（第 6 次起 429）；换 8 个用户名 → 1.3s 全 401；审计行数按次数线性增长（含 `rate_limited` 那几次）。

**闸门约束（正向）**：20 / 40 并发失败登录（不同用户名，避开限流）→ checkout/checkin 峰值 **4~5**，期间 `select 1` **3 ms**。审计的 `commit` 没有抬高连接峰值，`EXTENDING.md` §2.6 的说法成立。
