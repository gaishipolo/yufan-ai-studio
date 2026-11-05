# 代码评审报告（第五轮 · 仅后端 · R4 修复复核）

> 评审时间：2026-11-05 15:20 ｜ 范围：14:30–15:12 那轮改动（R4 修复 + `identity` 加固），前端不在范围内
> 关联：[`R4`](./CODE_REVIEW_2026-11-05_R4.md)｜[`R3`](./CODE_REVIEW_2026-11-05_R3.md)｜[`R2`](./CODE_REVIEW_2026-11-05_R2.md)｜[`首轮`](./CODE_REVIEW_2026-11-05.md)｜[`CODE_STYLE.md`](./CODE_STYLE.md) / [`REVIEW_CHECKLIST.md`](./REVIEW_CHECKLIST.md)

---

## 一、结论

**R4 的 16 条，15 条真的修好了，而且几处比我建议的做法更彻底**；剩下 1 条（R4-07 `image` 数据范围）没有动，那条本来就是 💭。

这一轮的重点不在"改了什么"，而在**新加的那套守卫覆盖了哪个面、漏了哪个面**。`_guard_permission_grant` / `_guard_role_grant` / `_guard_target_not_above` / `_guard_has_superuser` 四个闸口的设计是对的（尤其"先改、flush、再数一遍"这个思路，不用为每种操作推演锁死路径），但落地时只覆盖了**授权**方向，**撤权**方向没有对等守卫，实测可复现。

另外两个新问题都在运维/暴露面上：登录限流可以被匿名利用来锁死任意账号，以及 `create-admin` 轮换口令不作废旧令牌。

### R4 逐条复核（全部实测）

| 编号 | 结论 | 证据 |
|---|---|---|
| R4-01 5xx 被重提 | ✅ 修好 | 桩供应商抛 `UpstreamError(502)`：`submit 调用 1 次 → failed / attempts=1 / 无 external_id / "提交结果未知"` |
| R4-02 `--slot` 死参数 | ✅ 修好 | `render(slot=5)` 生成 `errors.py` 且写入 `ModuleErrors("music", slot=5)`；`_used_slots(api/)` → `{2: 'identity'}`，冲突会抛错 |
| R4-03 占位实现误报计费 | ✅ 修好 | `mark_submitting` 已移出 `try`，落 `提交准备失败`；模板占位改用 `NotImplementedFeature(501)`，`_possibly_delivered` 对 501 返回 False |
| R4-04 恢复后 62% 排队中 | ✅ 修好 | `external_id` 在时强制保留 `running`，不再置 `queued` |
| R4-05 批量删除静默 | ✅ 修好 | `{"deleted": 1, "skipped": ["nope"]}` + "已删除 1 个任务，1 个未删除" |
| R4-06 `read:all` 顺带解锁重试 | ✅ 修好 | 新增 `task:retry:any`，`retry_task` 单独判；目录里已能看到该点 |
| R4-07 `image` 只按 owner 过滤 | ⬜ 未动 | `image/service.py:27` 仍是 `owner_id == actor.id` |
| R4-08 `require_role` 死代码 | ✅ 删除 | `api/deps.py` 留了说明性注释，`AdminDep` 一并消失 |
| R4-09 `typ` 未校验 | ✅ 修好 | `decode_access_token` 校验 `typ == "access"` 且 `sub` 非空 |
| R4-10 事件与落库文案不一致 | ✅ 修好 | `resume` 的"结果未知"分支补发 `EVT_GENERATION_FAILED` |
| R4-11 三条提权路 | ✅ 修好 | 见下 |
| R4-12 自助停用自己 | ✅ 修好 | `ProfileUpdateIn` 白名单，不再接 `UserUpdateIn` |
| R4-13 改密后旧令牌仍有效 | ✅ 修好 | 实测 `200 → 改密 200 → 旧令牌 401`（`iat` vs `password_changed_at`） |
| R4-14 弱口令 | ✅ 修好 | 纯数字 422；`PASSWORD_MIN_LENGTH=10` |
| R4-15 登录无速率限制 | ✅ 修好 | `[401×5, 429, 429]` |
| R4-16 生产可匿名超管启动 | ✅ 修好 | `_guard_production` 校验 5 项，配置错误直接启动失败 |

R4-11 的修法值得单说一句：不是"每条路加一个 if"，而是抽出 `_guard_permission_grant`（子集规则）让 `create_user` / `set_user_roles` / `create_role` / `set_role_permissions` 共用，`create_user` 也补上了 `actor` 参数。实测三条原路径全部 403：

```
PUT /roles/{自己的角色}/permissions {"*"}  → 403 不能给角色 ops 授予自己不具备的权限
POST /users/{超管id}/reset-password        → 403 该操作需要超级管理员权限
POST /users {roleIds:[admin]}              → 403 该操作需要超级管理员权限
PUT /users/{自己}/roles [ops, admin]        → 403 该操作需要超级管理员权限
```

### 质量基线（实测）

| 项 | 结果 | 变化 |
|---|---|---|
| `pytest -q` | ✅ 156 passed | 原 124，+32 |
| `ruff check src tests` | ✅ All checks passed | — |
| `mypy src` | ✅ 107 files clean | 原 105，+2 |

---

## 二、🟡 R5-01 登录限流：能把任意账号锁死，且计数表没有上界

**位置**：`modules/identity/throttle.py`、`modules/identity/router.py:46`（`client_ip` 来源）

三处叠加起来构成一个问题。

**1）锁定发生在验密之前，正确密码也会被拒**

`guard()` 在 `authenticate` 第一行调用，`record_failure` 之后才走到 `raise CredentialInvalid`。所以第 6 次请求无论密码对不对都是 429：

```
正常登录: 200
5 次错误后，用【正确】密码登录: 429 密码错误次数过多，请 15 分钟后再试
```

没有解锁入口（管理员界面没有"解除锁定"），窗口 15 分钟不可调。用户唯一能做的是等。

**2）`client_ip` 取的是 `request.client.host`，全仓没有 `ProxyHeaders` / `X-Forwarded-For` 处理**

```python
client_ip = request.client.host if request.client else ""
key = f"{username}|{client_ip}"
```

直连时这个键能起到"同一出口 IP 不拖累别人"的作用。但**部署在 nginx / ingress 后面时，所有请求的 `client.host` 都是代理地址**——键退化成 `用户名|代理IP`，等价于"只按用户名计数"。此时任何一个匿名攻击者用 5 个请求就能把一个已知用户名锁 15 分钟，无限重复。

这条的严重度完全取决于部署形态，所以判 🟡；**如果这台服务对外网可达，这一条应直接按 🔴 处理**（未认证即可让全体用户无法登录，且无需猜中密码）。

**3）失败计数表没有上界，也没有过期回收**

```python
_failures: dict[str, deque[float]] = {}

def _recent(key):            # 只在"同一个 key 再次出现"时才裁剪
    bucket = _failures.setdefault(key, deque())
    cutoff = time.monotonic() - WINDOW_SEC
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    return bucket
```

`reset()` 只在登录成功时调用，`_recent` 只清理当前 key。**没有任何地方删除已过期的 key**，也没有容量上限。实测 300 次不同用户名的失败登录后：

```
Q3 失败计数表大小（300 个不同用户名后）: 300
```

也就是说匿名请求可以线性地往进程内存里塞键：换个用户名就是一条新记录，永不回收。作为对比，同一个模块里 `RateLimited` 已经定义得很好，说明限流的设计意图是到位的，只是存储层用了最简实现。

**建议**（三条一起做，改动都在 `throttle.py` 一个文件内）：

1. 用 `cachetools.TTLCache(maxsize=N, ttl=WINDOW_SEC)` 或自己维护 `OrderedDict` 做 LRU + TTL，超限时淘汰最旧；
2. 锁定改为**渐进延迟**（失败 5 次后每次额外 sleep 0.5–2s）而不是硬拒——延迟对爆破同样致命，但不会把真实用户挡在门外；
3. 客户端 IP 从可信代理头取：`uvicorn --proxy-headers --forwarded-allow-ips=<代理IP>` 或加 `ProxyHeadersMiddleware`；在没配置可信代理时**不要**读 `X-Forwarded-For`（否则攻击者伪造头即可绕过限流）。

---

## 三、🟡 R5-02 `create-admin` 轮换口令不作废旧令牌

**位置**：`modules/identity/service.py:573-583`（`ensure_superuser` 的 else 分支）

`change_password` 和 `reset_password` 都会写 `password_changed_at`（R4-13 就是靠它生效的），但 `ensure_superuser` 走的是第三条改密路径，**没有写这一列**：

```python
else:
    user.password_hash = hash_password(password)
    user.is_active = True
```

实测（等价于在服务器上跑 `app create-admin --username admin --password rotated@12345`）：

```
轮换前旧令牌: 200
create-admin 轮换口令后旧令牌: 200    ← 期望 401
新口令能否登录: 200
旧口令是否失效: 401
```

口令换了、旧口令立刻失效，但**已经签发出去的令牌还能用满 2 小时**。

这条的危险在于它命中的正是最需要它的场景：`create-admin` 是生产环境唯一的超管初始化/修复入口，也是"超管账号疑似泄漏、运维紧急轮换口令"时唯一能用的命令。轮换之后攻击者手里的令牌不受影响，等于自救动作只做了一半。而 `reset_password` 走 API 是同模块里唯一会被自动校验的路径——**修改口令的三条路径，只有这条没接上同一个不变式**。

**建议**：把"改口令"收敛成一个内部方法（如 `_set_password(user, raw, *, bump_changed_at=True)`），三条路径共用；顺手补一条测试断言"任何改密入口都必须让旧令牌 401"，避免下次再加第四条路径时又漏。

---

## 四、🟡 R5-03 撤权方向没有对等守卫

**位置**：`modules/identity/service.py:300-330`（`set_user_roles`）

R4-11 修完之后，**授予**方向有两道闸：`_guard_role_grant`（新角色的权限必须是自己的子集）+ `touches_superuser`（碰超管角色必须超管）。

**撤销**方向一道都没有：`set_user_roles` 不检查目标账号当前的权限是否高于操作者。而同一个文件里，`reset_password` / `deactivate_user` 都调了 `_guard_target_not_above`——也就是说"不能操作权限高于自己的账号"这条红线，写在了改密和停用上，唯独没写在"改角色"上。

实测：一个**只有** `identity:read` + `identity:role:manage` 的运营账号

```
Q1 角色管理员清空 editor 的角色: 200 OK → 结果角色: []
Q2 角色管理员把 editor 降为 viewer: 403 不能给角色 viewer 授予自己不具备的权限
```

同一对（操作者, 目标）上，**给**权限被挡、**撤**权限放行。对照上一轮：

```
P1 运营清空超管角色: 403 该操作需要超级管理员权限      ← 超管被 touches_superuser 挡住
```

所以影响面是"非超管的高权限账号"：`editor`（含 `task:*` / `asset:*`）、将来的"审核""财务"等角色，都能被一个只负责角色管理的人一键清空。这不是提权，是**越权变更他人账号**——而且被降级的人自己没法恢复，也看不出是谁干的（`logger.info` 记了操作者，但账号本身没有变更历史）。

**建议**：`set_user_roles` 在 `_guard_role_grant` 之前补一次 `await self._guard_target_not_above(user, actor)`。注意顺序——放在前面，否则"清空角色"会先通过子集检查（空集永远是子集）再落到目标校验上，虽然结果一样，但错误信息会指错方向。补一条负向测试："能管角色的人不能撤掉比自己权限高的账号的角色"。

---

## 五、💭 细节（六条）

**R5-04 `create-admin` 的密码策略与 API 不一致**：`cli.py:57` 只校验长度：

```python
if len(password) < PASSWORD_MIN_LENGTH: ...
```

而 API 侧的 `_check_password_strength` 还会拒绝纯数字与"重复字符过多"。实测 `UserCreateIn(password="1234567890")` → 422 "密码不能是纯数字"，但 `create-admin --password 1234567890` 会通过——**生产环境第一个超管的密码，恰好是唯一不受完整策略约束的那个**。建议把 `_check_password_strength` 提到 `permissions.py` 或独立 `policy.py`，CLI 与 schema 共用。

**R5-05 `task:retry:any` 单独授予无效**：`retry_task` 第一行是 `get_task(task_id, actor)`，没有 `task:read:all` 时先 403 "无权查看他人的任务"，`retry:any` 根本没机会参与判断。实测：

```
持有 task:read + task:update + task:retry:any，无 read:all → 403 无权查看他人的任务
```

权限点描述写的是"重试他人任务"，管理员照着勾选会得到"勾了没反应"。要么在描述里注明"需同时具备 `task:read:all`"，要么把判定改成 `owns or has(RETRY_ANY)` 再做可见性检查。前者是一行文档，后者是一行判定——建议后者，因为"能重试但看不见"本来就是合理组合（例如客服代跑）。

**R5-06 角色没有归属与层级**：`delete_role(role_id)` 连 `actor` 都不收，实测一个 `role:manage` 持有者删除他人建的角色返回 200。角色是全局对象，当前没有任何"这个角色归哪个团队/不能高于自己"的概念。短期可以在 `delete_role` 里加一条"角色权限必须是自己的子集"（与 `_guard_permission_grant` 同源），长期再考虑 owner 字段。

**R5-07 `/identity/me` 每条请求解析两次**：`get_actor` 已经调过 `resolve_principal`（3 次查询），`me()` 又调一次 `resolve_identity`。`return self._to_me(...)` 完全可以用 `actor` 身上已有的数据。目前 admin 控制台一次页面切换会打十几次 `/me`，翻倍的查询量不值得。

**R5-08 `iat` 是秒级**：`issued_at < int(changed_at.timestamp())` 用整数比较，改密与签发落在同一秒时旧令牌不会被作废（窗口 ≤1s）。理论上可利用（攻击者在同一秒内抢先登录），实际概率极低；如果在意，可以给 `password_changed_at` 减 1 秒再比较，代价是"改密后 1 秒内新登录的令牌也会被踢"。

**R5-09 `AccountDisabled`(403) 与 `CredentialInvalid`(401) 状态码不同**：只有密码**正确**时才会看到 403，所以泄露面很小，但它确实告诉攻击者"这个密码是对的，只是账号停了"。如果哪天加了"停用不告知"的合规要求，这里要一起改。

---

## 六、值得保留的做法（这轮新增）

- **`api/authn.py` 这个扩展点选得对**：`PrincipalResolver` 与 `get_storage` / `get_queue` 同形态，核心不认识 identity 模块，删掉账号体系就退回"令牌即权威"。注释里把"为什么不在 deps.py 里直接 import"和循环导入的具体路径都写了，这比只写结论有用。
- **"先改、flush、再数一遍"**（`_guard_has_superuser`）：不用为停用/换角色/改角色标记分别推演"会不会把超管清零"，一条 `count_active_superusers() == 0` 覆盖全部间接路径。
- **`_missing_grants` 复用 `CurrentUser.has`**，并在注释里点明"另写一套通配匹配，迟早会和鉴权主路径对不上"——这正是上一轮 `_possibly_delivered` 踩过的坑。
- **`orphaned_permissions`**：角色界面能看到"这个角色挂了已下线的权限码"，模块删改后不会静默残留。
- **`_possibly_delivered` 的注释写了"已知边界"**：只识别 httpx 异常类别，其它客户端落到"可能已送达"这个安全默认值。这种把取舍写进代码的做法，比"以后再说"强得多。
- **`tests/test_architecture.py` 继续守住命名空间**：`identity:` 前缀、每个模块必须声明权限点、错误码与 HTTP 状态一致，这些都是 CI 能拦住的。

---

## 七、建议顺序

| # | 动作 | 预估 | 理由 |
|---|---|---|---|
| 1 | R5-02 收敛 `_set_password` + 改密即失效的测试 | 30 min | 超管轮换口令是应急动作，只有它没接上不变式 |
| 2 | R5-01 限流：TTL/LRU + 渐进延迟 + 可信代理头 | 1.5 h | 未认证即可锁号；计数表可被匿名撑爆 |
| 3 | R5-03 `set_user_roles` 补 `_guard_target_not_above` + 负向测试 | 30 min | 与同文件其它两条入口对齐，改动最小、语义最一致 |
| 4 | R5-05 重试判定改为 `owns or has(RETRY_ANY)` | 15 min | 否则新加的权限点"勾了没反应" |
| 5 | R5-04 密码策略下沉到 `policy.py`，CLI 复用 | 20 min | 生产首个超管是唯一绕过完整策略的账号 |
| 6 | R5-07 `/me` 复用 `actor`；R5-06 删角色的子集检查 | 30 min | 性能与一致性 |

第 1、3 项都只改 `identity/service.py`，可以同一个 PR；第 2 项独立，但要先定部署形态（有没有反代、能不能拿到真实 IP），否则改完仍然是"按用户名锁"。

---

## 八、验证方式（可复现）

```bash
# R5-01 正确密码被锁
5 次错误密码后，用正确密码登录 → 429 密码错误次数过多，请 15 分钟后再试
len(throttle._failures)  # 300 个不同用户名后 = 300，无回收

# R5-02 运维轮换口令不作废旧令牌
login admin → GET /me 200
ensure_superuser("admin", "rotated@12345")   # 等价 create-admin
GET /me（旧令牌） → 200    # 期望 401
POST /login admin@12345 → 401（旧口令确实失效）
POST /login rotated@12345 → 200

# R5-03 撤权无守卫（操作者只有 identity:read + identity:role:manage）
PUT /identity/users/{editor}/roles {"roleIds": []}     → 200，角色被清空
PUT /identity/users/{editor}/roles {"roleIds":[viewer]} → 403（授予方向有守卫）

# R5-05 retry:any 单独授予
持有 task:read+task:update+task:retry:any，无 read:all
POST /tasks/{别人的失败任务}/retry → 403 无权查看他人的任务
```
