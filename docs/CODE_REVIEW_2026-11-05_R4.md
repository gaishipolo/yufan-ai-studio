# 代码评审报告（第四轮 · 仅后端，含 14:22 增补复查）

> 评审时间：2026-11-05 14:22（初版 14:05）｜范围：13:42–13:57 的 `scaffold` / 提交状态机改动 **+ 14:08–14:20 新增的 `identity` 模块**，前端不在本轮范围内
> 编号约定：`R4-01 ~ R4-10` 为 14:05 首次评审提出（本轮逐项复核，**全部未改动**）；`R4-11` 起为 14:22 增补。原编号保持不变，便于对照之前的讨论。
> 关联：[`R3`](./CODE_REVIEW_2026-11-05_R3.md)｜[`R2`](./CODE_REVIEW_2026-11-05_R2.md)｜[`首轮`](./CODE_REVIEW_2026-11-05.md)｜[`CODE_STYLE.md`](./CODE_STYLE.md) / [`REVIEW_CHECKLIST.md`](./REVIEW_CHECKLIST.md)

---

## 一、结论

**14:05 提的 10 项里，0 项被修**；这轮实际做的是新增 `identity` 模块（用户/角色/权限 + 登录），测试从 93 涨到 124。

新模块的工程质量明显高于平均水平：`PrincipalResolver` 这个注入点解决了「令牌快照 vs 数据库权威」的老问题，`_guard_has_superuser` 用「先改、flush、再数一遍」替代逐操作推演，都是对的做法。

但**它最核心的那个承诺没有兑现**。模块特意把 `identity:role:manage` 从 `identity:update` 里拆出来，文档写的是「能改昵称不等于能给自己提权」；实测下来，拿到 `identity` 任意一个写权限的人都能在两次请求内变成超级管理员，拆分的实际防护效果为零（详见 §二）。

### 质量基线（实测）

| 项 | 结果 | 变化 |
|---|---|---|
| `pytest -q` | ✅ 124 passed | 原 93，+31（`test_identity_api.py`） |
| `ruff check src tests` | ✅ All checks passed | — |
| `mypy src` | ✅ 105 files clean | 原 94 |
| 业务模块数 | 9（新增 `identity`） | +1 |
| 已注册流水线 | `['video']` | — |

### 问题清单

| 编号 | 级别 | 状态 | 问题 |
|---|---|---|---|
| R4-11 | 🔴 | **新增** | 三条通往超管的路没有守卫：`role:manage` 写 `*`、`update` 重置超管密码、`create` 建号时挂超管角色 |
| R4-01 | 🔴 | 未修 | `_possibly_delivered` 把 5xx 当「确定没送达」，供应商 502 触发自动重提 3 次，且清零后库里没有任何提交痕迹 |
| R4-02 | 🟡 | 未修 | 脚手架 `--slot` 是死参数：三种取值生成结果逐字节相同，且不生成 `errors.py` |
| R4-03 | 🟡 | 未修 | 脚手架生成的 `mark_submitting` 占位抛 `NotImplementedError`，被归类为「可能已送达」，用户看到「为避免重复计费…请核对」而 submit 一次都没调用 |
| R4-04 | 🟡 | 未修 | 重启恢复把 `running` 置回 `queued`，进度却保留 62% |
| R4-05 | 🟡 | 未修 | 批量删除部分成功静默返回 200，无法区分「不存在」与「无权限」 |
| R4-06 | 🟡 | 未修 | `task:read:all` 顺带解锁了会重新计费的「重试」，但权限描述里只写了查看/删除 |
| R4-12 | 🟡 | **新增** | `PATCH /me` 能改 `isActive`，可自我停用且没有自助恢复入口 |
| R4-13 | 🟡 | **新增** | 改密码后旧令牌继续有效，与「停用/改权限即时生效」的核心卖点不一致 |
| R4-14 | 🟡 | **新增** | 密码策略只有长度 8：新旧可相同，纯数字可通过 |
| R4-15 | 🟡 | **新增** | 登录接口无速率限制/锁定，`RateLimited` 全仓零使用 |
| R4-07 | 💭 | 未修 | `image` 模块只按 `owner_id` 过滤，没有 `read:all`；两套数据范围语义并存 |
| R4-08 | 💭 | 未修 | `require_role` / `AdminDep` 全仓零使用、零测试，却是唯一「写死角色名」的入口 |
| R4-09 | 💭 | 未修 | `decode_access_token` 不校验 `typ`，也不要求 `sub` 非空 |
| R4-10 | 💭 | 未修 | 生成事件的 payload 与落库文案不一致；启动恢复的「结果未知」分支不发事件 |
| R4-16 | 💭 | **新增** | `email` 无格式校验；`seed` 演示口令与匿名开关叠加有风险 |

---

## 二、🔴 R4-11 三条通往超管的路都没有守卫

**位置**：`modules/identity/service.py`

权限模型本身是对的：`PERM_ROLE_MANAGE` 单独拆出来，因为「能改昵称不等于能给自己提权」。守卫也确实写了——但只写在一处：

```python
# service.py:299-304  set_user_roles
touches_superuser = any(role.is_superuser for role in roles) or any(
    role.is_superuser for role in current
)
if touches_superuser and not actor.has(SUPER_PERMISSION):
    raise SuperuserRequired()
```

**`set_user_roles` 是唯一带这道守卫的入口。** 同样能拿到超管身份的三条路，一条都没守。下面每条都是实测结果，不是推演。

### 路径 A：`identity:role:manage` → 给自己的角色写 `*`，一次调用完成提权

```
起点权限: ['identity:read', 'identity:role:manage']
D1 PUT /roles/{自己的角色}/permissions {"permissions":["*"]}  → 200 OK
D2 GET /identity/me                                          → ['*']
D3 DELETE /tasks（需要 task:delete）                          → 200 已删除 0 个任务
```

`_validate_permissions` 把 `*` 判为「合法权限点」（`_known_permission` 里 `code == SUPER_PERMISSION` 直接返回 True，这个判断本身没错——超管角色就是这么表达的），但**没有任何一处限制「谁能写 `*`」**。`create_role` 守了 `is_superuser` 字段、`update_role` 也守了，唯独漏了与它完全等价的权限字符串：

```
E  POST /roles {"permissions":["*"]}  → 201 ['*']   （同样不拦）
```

这是最短的一条：`identity:role:manage` 的 description 是「新建/删除角色、调整角色权限」，读起来像配置界面的开关，实际等价于 `*`。（附带效应：拿到 `*` 之后，`PATCH /roles/{id} {"isSuperuser":true}` 的守卫也随之失效——因为此时 `actor.has("*")` 已经为真。实测该请求返回 200。）

### 路径 B：`identity:update` → 重置超管密码，然后以超管登录

```
A1 POST /users/{超管id}/reset-password  → 200 密码已重置
A2 POST /identity/login (admin, 新密码)  → 200 perms=['*']
```

`PERM_UPDATE` 的描述是「编辑用户（含停用、重置密码）」，并特意补了一句「不含分配角色——那是提权动作」。**重置密码是比分配角色更强的提权**：它直接接管对方身份，不需要对方再登录。把重置密码归类成普通编辑，是这份模块里唯一一处威胁模型判断错误。

### 路径 C：`identity:create` → 建号时直接挂超管角色

```
B1 POST /users {"username":"backdoor","roleIds":[<admin 角色 id>]} → 201 OK
B2 POST /identity/login (backdoor)                                 → 200 perms=['*']
```

`create_user` **连 `actor` 参数都没有**（`service.py:248`），而 `UserCreateIn.role_ids` 接受任意角色 id，`set_user_roles` 的守卫在这里被完整绕过。

顺带：即便不给超管角色，`identity:create` 也能直接发 `editor`（含 `task:*`、`asset:*`、`image:*`），已超出「创建用户」的授权范围。

### 为什么这不能被「默认只有 admin 才有 identity:\*」解释掉

内置角色里确实只有 `admin` 持有 `identity:*`，所以**当前部署不可利用**。但有两点：

1. 拆分 `role:manage` 的全部意义，就是让管理员能把「管用户」交给运营而不交出超管。一旦这么做（这正是它的目标场景），路径 A/B/C 立刻全部打开——这个拆分现在提供的是心理安全感，不是实际边界。
2. `test_only_superuser_can_grant_superuser` 只测了 `set_user_roles`。**守卫写对的那一处，恰好就是被测试的那一处**，这不是巧合，是同一处盲区。

### 建议

把「提权面」收敛成一个判定函数，在四个入口统一调用：

```python
def _require_superuser(actor: CurrentUser, *, reason: str) -> None:
    if not actor.has(SUPER_PERMISSION):
        raise SuperuserRequired(reason)
```

| 入口 | 触发条件 |
|---|---|
| `set_user_roles` | 已有，保持 |
| `create_user` | `role_ids` 含 `is_superuser` 角色，或含调用者自身不具备的权限 |
| `create_role` / `set_role_permissions` | 权限列表含 `*` |
| `reset_password` | 目标用户是超管（或目标的权限集不是调用者权限集的子集） |

其中「`role_ids` 只能是调用者权限的子集」是一条更根本的规则，它同时解决 A/B/C 与「`identity:create` 能发 editor」的问题，代价是每次建号多一次集合比较。

另外建议把 `reset_password` 从 `PERM_UPDATE` 拆出去（如 `identity:password:reset`），与 `role:manage` 的拆法保持一致——现在这条权限的粒度明显偏粗。

**必须补的测试**（每条一个负向用例，否则这次的修复会重演「改了一处、漏了三处」）：

```
非超管 + identity:role:manage  → PUT  /roles/{self}/permissions ["*"]  应 403
非超管 + identity:role:manage  → POST /roles {"permissions":["*"]}     应 403
非超管 + identity:update       → POST /users/{超管}/reset-password     应 403
非超管 + identity:create       → POST /users {"roleIds":[admin]}       应 403
```

---

## 三、🟡 身份模块的四个专项问题（R4-12 ~ R4-15，新增）

### 🟡 R4-12 `PATCH /me` 可以停用自己，且没有自助恢复

**位置**：`service.py:204-209` + `schema.py:79-83`

`UserUpdateIn` 同时被 `/users/{id}`（管理员）和 `/me`（自助）复用，而 `update_me` 是缺白名单的整字段 `setattr`：

```python
for field, value in payload.model_dump(exclude_unset=True).items():
    setattr(user, field, value)
```

实测：

```
1a PATCH /me {"isActive": false}  → 200 isActive=False
1b 之后自己还能用令牌吗             → 401 账号已停用或不存在，请重新登录
1c 之后还能登录吗                   → 403 账号已停用，请联系管理员
```

管理员路径有 `CannotTargetSelf` 守卫（`update_user:278`），自助路径没有。而恢复只有一个入口——管理员 PATCH `/users/{id}`——所以用户误操作后**只能找人解锁**，还看不出是自己点的。

**建议**：给 `/me` 单独一个 `ProfileUpdateIn`（`display_name` / `email` / `avatar_url`），把 `is_active` 从自助可写字段里剔除；顺带把 `setattr` 换成显式赋值，与 `update_user:282` 保持一致。

### 🟡 R4-13 改密码后旧令牌继续有效

**位置**：`service.py:211-216`

```
2a 改密码            → 200 密码已更新
2b 改密码后旧令牌    → 200        ← 仍然可用
2c 新密码登录        → 200
```

这恰好是 `principal.py` 文档拿来当卖点的那类问题（「令牌快照 vs 数据库权威」），但解法只覆盖了 `is_active` 和角色变更，没覆盖凭证变更。实际后果：账号被盗后改密码，攻击者令牌能继续用满 2 小时。

`resolve()` 已经每请求都查一次 `User`，加一个字段即可根治，不需要白名单/黑名单存储：

```python
# 签发时带上 iat，User 增加 password_changed_at
if principal.issued_at < user.password_changed_at:
    return None
```

### 🟡 R4-14 密码策略只有长度

```
3  新旧密码相同     → 200 密码已更新
4  纯数字 8 位      → 200 密码已更新
```

`PASSWORD_MIN_LENGTH = 8` 是唯一约束。配合演示口令 `admin@123`（`seed.py:33`）的强度，策略层基本等于没有。建议至少三条：新密码不得与旧密码相同、拒绝纯数字、`PASSWORD_MIN_LENGTH` 提到 10~12。都在 `PasswordChangeIn` 的 validator 里做，不影响存量账号。

### 🟡 R4-15 登录接口无速率限制

```
5  连续 12 次错误密码 → {401}
```

`core/exceptions.py:190` 定义了 `RateLimited`（429），**全仓没有任何地方使用它**——又一个「能力建好了、没接线」的扩展点。登录是唯一匿名可达且需要口令校验的接口，没有限流/锁定/验证码，配合 R4-14 的缺口，爆破成本很低。

最小实现：按 `(username, client_ip)` 计失败次数，5 次后指数退避回 429（`RateLimited` 现成），成功登录清零。内存实现即可，与 `MemoryJobQueue` 同一阶段，切 Redis 时一起换。注意计数要放在 `verify_password` **之后**、抛 `CredentialInvalid` **之前**，否则账号枚举的时序侧信道又漏出来了。

---

## 四、🔴 R4-01 供应商 5xx 会被自动重提三次（14:22 复核：未修，`_possibly_delivered` 一字未动）

**位置**：`generation/runner.py:57-74`（判定）、`runner.py:158-193`（处置）

`_possibly_delivered` 的策略是：

```python
if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
    return False          # 连接都没建立 → 确实没送达
return not isinstance(exc, AppError)   # AppError → 「供应商明确知道结果」
```

注释写的是「拿到了 4xx/5xx 响应，传输层没有不确定性」，但 **5xx 恰恰不是「明确知道结果」**：网关 502 的典型成因就是「上游已经受理，回程失败」。把它归到「确定没送达」，等于用一次重提去赌对方没收到。

紧接着的处置又放大了后果：

```python
await store.clear_submit_attempt(record_id)   # 177：抹掉痕迹
if self._retryable(exc, job):                 # 178：UpstreamError(502) → retryable=True
    await session.commit()
    raise                                     # 交给 worker 退避重试 → 重新 submit
```

而 `test_generation.py:216` 恰好把「502 应该重试」写成了断言——对**轮询**来说这是对的，对**提交**来说就是第二份账单。

### 实测

用一个 `submit()` 抛 `UpstreamError("供应商网关 502")` 的桩供应商，走真实的 `handle_submit`（模拟 worker 的 3 次尝试）：

```
[供应商 502] submit 调用 3 次 -> status=failed attempts=0 ext=None
                               err='提交生成失败：供应商网关 502'
```

三次提交，最终库里 `submit_attempts=0`、没有 `external_id`，**看不出曾经提交过三次**，只有日志里有。对比同一条链路的正确表现：

```
[脚手架未实现 mark_submitting] submit 调用 0 次 -> status=failed
                               err='提交结果未知：请求已发出但未确认供应商是否受理…'
```

后者的文案是准的（确实没提交），但两者的分类逻辑正好相反。

### 建议

按 **HTTP 语义**分类，而不是按异常类：

```python
def _possibly_delivered(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return False                      # 握手前失败，必然没送达
    if isinstance(exc, AppError):
        # 4xx 是供应商明确拒绝（429 也算拒绝），5xx/408 可能已受理
        return exc.http_status >= 500 or exc.http_status == 408
    return True                           # 未知异常：按可能已送达处理
```

配套两点：

1. `UpstreamError` 属于「可能已送达」，**不能再走 `clear_submit_attempt`**，应保留标记并置为「结果未知」等人工核对；
2. 根治仍是把 `GenerationRequest.request_id`（`record.id`，已经填好了）当幂等键传给供应商——`infra/ai/base.py:47` 的注释写着「供应商可忽略」，但真正接火山/可灵时应要求对方按它去重。

顺带：`AppError.retryable` 是 `http_status == 429 or http_status >= 500`，于是 `NotImplementedFeature`（501）也被判为可重试。这一条现在无害，但会让「功能未实现」白白重试三次。

---

## 五、🟡 R4-02 ~ R4-06（14:22 逐项复核，均未改动）

### 🟡 R4-02 脚手架的 `--slot` 是死参数

**位置**：`scaffold/__init__.py:78-79`（校验）、`scaffold/templates.py`（全文）、`cli.py:139`（默认值）

`--slot` 有三处「承诺」：CLI help 写着「错误码号段 1-9（见文档分配表）」、`validate()` 会拒绝越界值、`docs/EXTENDING.md` §1/§7 明确要求「先在这张表里占一个空格，再写进 `app new-module --slot n`」。

但 6 个模板里**没有任何一个引用 `${slot}`**：

```
slot=3 -> 生成的 6 个文件里提到 slot 的: []
slot=4 -> 生成的 6 个文件里提到 slot 的: []
slot=9 -> 生成的 6 个文件里提到 slot 的: []
errors.py 是否生成: False
```

也就是说这个参数只参与校验，不产生任何效果。更糟的是它的默认值是 `3`，而文档的号段表把 3 分给了 `task`——即便将来接上，默认值就是一次撞车。

> 14:22 补充：新增的 `identity` 模块手写了 `errors.py` 并用上 `ModuleErrors(slot=2)`，证明这套机制本身是好的——**恰恰说明脚手架没接上它是个遗漏，而不是「这个机制不需要」**。目前全仓 9 个模块里只有 `identity` 有 `errors.py`，其余仍全部使用框架级的 `BadRequest` / `NotFound`。

**建议**：`--generation` 之外再加一个 `errors.py` 模板：

```python
# modules/music/errors.py
from core.exceptions import AppError, ModuleErrors

errors = ModuleErrors("music", slot=4)

class TrackBusy(AppError):
    code = errors.code(409, 1)
    message = "该音轨正在生成中"
    http_status = 409
```

同时把 `slot` 默认值改成 `None` 并设为必填，或在 `create_module()` 里扫一遍现有模块的 `errors.py` 做冲突检测。别忘了 `test_scaffold.py` 现在只校验「能编译」「方法齐全」，加一条「`--slot` 影响产物」的测试才能防止它再退化。

### 🟡 R4-03 脚手架的占位实现被误判成「可能已计费」

**位置**：`scaffold/templates.py:244-247` + `runner.py:150-157`

模板让 `mark_submitting` 抛 `NotImplementedError("TODO: submit_attempts += 1")`，而它位于 runner 的 `try` 块内：

```python
try:
    provider = get_model_router().provider_for(record.model)
    request = await pipeline.build_request(record)
    await store.mark_submitting(record_id)   # ← 还没实现，在这里抛
    ...
except Exception as exc:
    if _possibly_delivered(exc):             # NotImplementedError → True
        await store.mark_failed(record_id, SUBMIT_UNKNOWN_MESSAGE)
```

结果：新人按文档生成模块，跑通的第一条任务就显示「提交结果未知：请求已发出但未确认供应商是否受理，为避免重复计费…请核对后手动重试」。实测**供应商提交次数为 0**，这句文案是假的，而且指向「计费」这种最需要准确的地方。

**建议**（两处都改，成本都很低）：

1. 把 `await store.mark_submitting(record_id)` 移到 `try` **之前**——它是「记录意图」，不是「调用供应商」，实现错误应该表现为普通的 job 失败，而不是被当成计费不确定；
2. 模板占位改为抛 `NotImplementedFeature`（`AppError` 子类，501），让 `_possibly_delivered` 返回 False，文案变成「提交生成失败：该功能尚未实现」。注意 501 目前仍会被 `retryable` 判为可重试，见 R4-01 末尾。

### 🟡 R4-04 重启恢复把 `running` 降级成 `queued`，进度却保留

**位置**：`modules/task/worker.py:49-53`

```python
for task in rows:
    task.status = TaskStatus.QUEUED.value     # 无条件先置 queued
    if task.external_id:
        resume_poll.append(task.id)           # 但这里不恢复 running
```

实测：

```
[2] 恢复后续轮询: status=queued progress=62
```

任务在供应商侧还在跑、进度 62%，重启后前端会显示「排队中 · 62%」——一个自相矛盾的状态，而且要等到下一次轮询（默认 2s，退避后可能更久）才会被 `mark_progress` 拉回 `running`。

**建议**：`resume_poll` 分支保留原状态（本来就是 `running` 的就别改），只对 `resubmit` 分支置 `queued`。另外这个分支目前只打日志、不发 `generation.failed` 事件——接入 SSE/通知后，重启期间失败的这批任务不会有任何人知道。

### 🟡 R4-05 批量删除「部分成功」静默返回 200

**位置**：`modules/task/service.py:184-192`

```python
owned = [t for t in tasks if self._can_access(t, actor)]
...
if len(owned) != len(ids):
    logger.warning(...)          # 只写日志
return len(owned)
```

实测混删（1 条别人的 + 1 条自己的，调用者无 `read:all`）：

```
[3c] 混删(他人的+自己的): HTTP 200 deleted=1 请求 2 条
     他人的任务还在吗: 200
```

前端拿到 `deleted: 1` 和「已删除 1 个任务」，无法区分「本来就不存在」和「没权限」，用户会以为是自己少选了。

**建议**：返回 `DeleteTasksOut(deleted=n, skipped=[ids…])`，或对「存在但无权限」的条目直接 403（与详情接口的语义保持一致——详情是 403，删除却静默跳过，这是同一条链路上的不一致）。`ids` 上限 200，逐个判定不构成性能问题。

### 🟡 R4-06 `task:read:all` 顺带解锁了会重新计费的重试

**位置**：`modules/task/service.py:99`（`_can_access`）+ `service.py:138-158`（`retry_task`）

`retry_task` 的准入是 `get_task()` → `_can_access()`，也就是「本人 或 有 `read:all`」。实测：

```
[3a] 运营重试他人任务: HTTP 200 '已重新排队'
[3b] 运营删除他人任务: HTTP 200 {'deleted': 1}
```

删除在 `permissions.py` 的描述里是写明的（「查看并管理他人任务…删除」），但**重试没写**，而重试的后果是「用别人的额度再跑一次生成」。一个叫 `read:all` 的权限解锁了花钱的动作，这个耦合值得显式化。

**建议**：给重试单独判定——要么要求 `task:update` 且（本人 或 显式 `task:retry:any`），要么在 `PERM_READ_ALL` 的 description 里补上「含重试」，让它成为一次有意识的决定而不是顺带。

---

## 六、💭 细节

**R4-07 `image` 与 `task` 的数据范围语义不一致**：`image/service.py:26-31` 写死 `where(ImageJob.owner_id == actor.id)`，没有 `read:all` 概念。现在 `identity` 上线后这个问题更明显了——`editor` 角色持有 `task:*` 和 `image:*`，但两个模块对「能看谁的」给出不同答案。建议把「数据范围」抽成一个共用依赖（如 `scope_clause(model.owner_col, actor)`），并在模板里留一行注释说明有这两种选择。

**R4-08 `require_role` / `AdminDep` 已成死代码**：`grep -rn "AdminDep\|require_role" src tests` 除定义处之外零命中，也没有测试。它是全仓唯一「按角色名判断」的入口，与 `permissions.py` 里「不认角色名」的原则相反。`identity` 模块已经把角色展开成权限码落库了，这个函数连过渡期的理由都没有了，建议直接删掉（`CurrentUser.roles` 字段可留作展示与日志）。

**R4-09 `decode_access_token` 不校验用途**：`core/security.py:86-92` 只验签名与过期，不检查 `claims["typ"] == "access"`，也不要求 `sub` 非空。当前只发 access token，所以不可利用；一旦加 refresh token（同样用 `JWT_SECRET` 签），refresh token 会被直接接受为访问凭证。**这条现在比上一轮更值得做**——`identity` 上线后 `sub` 已经是载荷了，若将来支持「服务账号 / API Key」，`sub` 为空或非用户 id 的令牌会静默变成「查不到用户 → 401」，排查成本不低。成本仍是一行。

**R4-10 事件 payload 与落库文案不一致**：`runner.py:237` 超时分支发 `error="timeout"`，而库里写的是「生成超时，请重试」；`worker.py:71-72` 的「结果未知」分支干脆不发事件。等事件真正接上通知渠道时，这两个分支会给出与任务详情页不同的说法。

**R4-16 `email` 无格式校验**：`UserCreateIn.email` / `UserUpdateIn.email` 都是 `str | None` + `max_length=128`，没有 `EmailStr`。现在只用于展示所以不疼；一旦做「邀请链接」「密码找回」，无效邮箱配上「发送成功」的提示就是标准的坑。

**R4-16（续）演示口令与匿名开关的叠加**：`seed.py:33` 的 `admin@123` 只在演示库，但 `ALLOW_ANONYMOUS` 在 `dev/test` 默认放开且直接给 `*`。两件事单独看都合理，叠在一起意味着：一个照 README 启动、监听 `0.0.0.0:8080` 的开发机，在内网里就是一个免登录的超管接口。建议 `anonymous_allowed` 为真时在启动日志里显式打一行警告，比写在文档里有效。

---

## 七、值得保留的做法

上一轮（`scaffold` / 状态机）：

- **`scaffold` 抓的约束很准**：`test_scaffold.py` 检查的不是文案，而是「每个文件能编译」「Store 实现了 `GenerationStore` 全部方法」。后者尤其有价值——少一个 `mark_submitting`，运行期就是 `AttributeError`，而人工 review 极难发现。
- **`--generation` 的副作用导入注释**（`scaffold/__init__.py:61-67`）把「漏了这一行 dispatch 会报未注册」写在生成物里，比写在文档里有效。
- **`mark_submitted` 复位 `submit_attempts`**（`pipeline.py:85-87`）是 R3-01 的正确修法，注释也把「不复位会误判」讲清楚了。
- **`_retryable` 不做无谓重试**（`runner.py:358-369`）：`BadRequest` 不占重试次数，避免「多等 2s+4s 再用最后一条覆盖真错误」。

本轮（`identity`）：

- **`api/authn.py` 的注入点设计**：把「主体从哪来」做成与 `get_storage` / `get_model_router` 同形态的扩展点，并明确「没有注册解析器时行为完全不变」的降级语义。注释里还写清了「为什么不能直接 import 业务模块」的循环导入坑——这是能挡住下一次重构的注释。
- **`_guard_has_superuser` 的「先改、flush、再数一遍」**：不推演每种操作会不会锁死，让数据库在事务内给出答案。改角色标记、换角色、停用账号、删角色四条路径共用一处判断，比四个 if 可靠得多。
- **登录的恒定时间处理**：用户不存在时也跑一次 `scrypt`，把「响应快 = 用户名不存在」这条侧信道堵掉；`CredentialInvalid` 把两种失败合并成同一个错误码。两件事一起做才算完整，不少实现只做一半。
- **`_known_permission` 区分「孤儿权限」与「通配意图」**：`task:*` 不被当成拼写错误，而是「未来新增动作也一起授予」的合法意图，真正的失效码通过 `orphaned_permissions` 标出来让管理员清理。这个区分很细致。
- **`identity/errors.py` 用 `ModuleErrors(slot=2)`**：第一个真正用上号段机制的模块，与 `EXTENDING.md` §7 的表一致。
- **测试密度**：`test_identity_api.py` 31 条，覆盖「停用即时生效」「改角色免重登」「不能撤掉最后一个超管」「内置角色不可删」「无解析器时退回令牌」等边界，明显高于其他模块。

---

## 八、建议顺序

| # | 动作 | 预估 |
|---|---|---|
| 1 | **R4-11** 收敛提权判定 + 拆 `identity:password:reset` + 补 4 条负向测试 | 2 h |
| 2 | **R4-01** `_possibly_delivered` 按 HTTP 语义分类 + 一条「502 不得重提」的测试 | 40 min |
| 3 | **R4-13** 改密码失效旧令牌（`password_changed_at` + `iat`） | 40 min |
| 4 | **R4-12** `/me` 独立 schema；**R4-14** 口令策略三条 | 40 min |
| 5 | **R4-03** `mark_submitting` 移出 `try` + 模板占位换 `NotImplementedFeature`；**R4-02** `--slot` 接上模板 | 1 h |
| 6 | **R4-15** 登录限流；**R4-04** 恢复保留 `running`；**R4-05** 删除返回 `skipped` | 1.5 h |
| 7 | **R4-09** `typ` 校验、**R4-08** 删死代码 | 15 min |
| 8 | **R4-06** 重试的独立准入（需要产品先确认「运营能否代跑」） | 与产品对齐后 |

**R4-11 优先于其他所有项**：它是唯一一条「设计意图明确、实现没跟上」的问题，而且修复成本随接入模块数增长——现在只有 `identity` 一个模块在发权限，等 `asset` / `image` / `template` 都开始发权限时，这个洞会变成系统性的。

第 2 项仍建议紧随其后：它不依赖任何设计决策，改一个函数 + 一条测试就闭环，而且是唯一一条会直接产生账单问题的。

---

## 九、验证方式（可复现）

```bash
# ── R4-11 路径 A：role:manage → 写 "*" → 立刻是超管 ──
起点 ['identity:read','identity:role:manage']
PUT    /identity/roles/{self}/permissions {"permissions":["*"]}  → 200
GET    /identity/me                                              → ['*']
DELETE /tasks {"ids":["nope"]}                                   → 200（本应 403）
POST   /identity/roles {"permissions":["*"]}                      → 201 ['*']
PATCH  /identity/roles/{self} {"isSuperuser":true}                → 200（守卫随之失效）

# ── R4-11 路径 B：update → 重置超管密码 → 登录拿 "*" ──
POST /identity/users/{superuser_id}/reset-password  → 200
POST /identity/login  (admin, 新密码)                → 200 perms=['*']

# ── R4-11 路径 C：create → 建号即超管 ──
POST /identity/users {"roleIds":[<admin_role_id>]}  → 201
POST /identity/login (backdoor)                     → 200 perms=['*']
# 对照：set_user_roles 有守卫
PUT  /identity/users/{self}/roles {"roleIds":[...,admin]} → 403 缺少权限: identity:role:manage

# ── R4-12 自助改 isActive ──
PATCH /identity/me {"isActive": false} → 200；随后令牌 401、登录 403

# ── R4-13 改密码不失效 ──
POST /identity/me/password → 200；旧令牌 GET /identity/me → 200

# ── R4-14 口令策略 ──
新旧密码相同 → 200；纯数字 8 位 → 200

# ── R4-15 无速率限制 ──
连续 12 次错误密码 → 全部 401（无 429）

# R4-01：5xx 被自动重提（桩供应商 submit 抛 UpstreamError）
[供应商 502] submit 调用 3 次 -> status=failed attempts=0 err='提交生成失败：供应商网关 502'
# 期望：submit 只调用 1 次，状态为「结果未知」并保留 submit_attempts

# R4-02：--slot 无效果
render(ModulePlan(name='music', label='音乐', perm='music', slot=3/4/9))  # 三者产物相同
errors.py in render(...)  # False

# R4-03：脚手架首跑提示
[脚手架未实现 mark_submitting] submit 调用 0 次 -> err='提交结果未知：请求已发出但未确认…'

# R4-04：重启恢复
running + progress=62 → resume_unfinished_tasks() → status=queued progress=62

# R4-05：批量删除
DELETE /tasks {ids:[他人的, 自己的]} → 200 deleted=1（请求 2 条）

# R4-06：read:all 解锁重试
仅 task:read:all + task:update → POST /tasks/{他人任务}/retry → 200 '已重新排队'
```

基线（14:22 复测）：`pytest 124 passed`、`ruff` clean、`mypy` 105 files clean。
