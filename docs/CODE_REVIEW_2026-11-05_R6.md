# 代码评审报告（第六轮 · 仅后端 · R5 修复复核）

> 评审时间：2026-11-05 15:42 ｜ 范围：`a8afbdd`（R5 的 7 项修复）及其直接影响面；前端不在范围内
> 关联：[`R5`](./CODE_REVIEW_2026-11-05_R5.md)｜[`R4`](./CODE_REVIEW_2026-11-05_R4.md)｜[`R3`](./CODE_REVIEW_2026-11-05_R3.md)｜[`R2`](./CODE_REVIEW_2026-11-05_R2.md)｜[`首轮`](./CODE_REVIEW_2026-11-05.md)｜[`CODE_STYLE.md`](./CODE_STYLE.md) / [`REVIEW_CHECKLIST.md`](./REVIEW_CHECKLIST.md)

---

## 一、结论

**R5 的 9 条里 7 条真的修好了，2 条按报告接受了取舍**，测试从 157 涨到 168，涨出来的用例都在断言"修完的行为"，不是凑数。

但这一轮最重要的结论不在修复清单里，而在**限流那条修复的方向**：

> R5-01 的问题是「锁能挡住正确密码」，R6-01 的问题是「锁根本锁不住」——因为限流键的 IP 段可以由请求者自己填。

`core/net.py` 把「可信代理」这件事抽象出来了，判断（对端是否可信）也对，**唯独取哪一跳取反了**。`X-Forwarded-For` 的语义是每一跳往右边追加，所以最左段永远是客户端自己写进去的，最右边那一段才是我们的代理亲眼看到的。取最左 = 取攻击者可控值。上一轮花 127 行改好的限流，被这一行抵消掉了。

其余四条（R6-03/04/05/06）是同一类问题的不同变体：**守卫只做了一个方向**，或者**字段的 null 没有定义**。都能在 20 行内改完。

### R5 逐条复核

| 编号 | 结论 | 证据 |
|---|---|---|
| R5-01 锁定挡住正确密码 / 表无上界 | 🟡 部分修好 | 逻辑侧成立（`[401×5, 429]`，且第 6 次输对密码仍 200）；代理侧取错值 → 见 **R6-01** |
| R5-02 `create-admin` 不作废旧令牌 | ✅ 修好 | 三条路径收敛到 `_set_password`；实测轮换后旧令牌 401 |
| R5-03 撤权没有对等守卫 | ✅ 修好 | 实测 `PUT /users/{强账号}/roles []` → 403 `40323`，且角色集合未被改动 |
| R5-04 CLI 密码策略与 API 不一致 | ✅ 修好 | `create-admin --password 1234567890` → `SystemExit(1)`；策略下沉到 `policy.py`，schema/CLI 共用 |
| R5-05 `retry:any` 单独授予无效 | ✅ 生效 | 只有 `retry:any`、没有 `read:all` 的账号重试 200；但副作用见 **R6-05** |
| R5-06 删角色没有子集检查 | ✅ 修好 | 比自己强的角色删除 → 403；`delete_role` 已接收 `actor` |
| R5-07 `/me` 每条请求解析两次 | ✅ 修好 | `me(actor)` 直接用请求期 `actor.permissions`，只补读用户行与角色摘要 |
| R5-08 `iat` 秒级窗口 | ⬜ 保持现状 | 代码注释写明了取舍，可接受 |
| R5-09 `403`/`401` 差异 | ⬜ 保持现状 | 同上 |

### 质量基线（实测）

| 项 | 结果 | 变化 |
|---|---|---|
| `pytest -q` | ✅ 168 passed | 原 156，+12 |
| `ruff check src tests` | ✅ All checks passed | — |
| `mypy src` | ✅ 109 files clean | 原 107，+2（`core/net.py`、`identity/policy.py`） |

---

## 二、🔴 R6-01 `X-Forwarded-For` 取最左段：限流键仍由请求者决定

**位置**：`core/net.py:62-66`

```python
    # 取最左边的第一个地址：那是「最初的客户端」。
    # 右侧的每一跳都可以由我们的代理追加/覆盖，左侧只有最外层能决定，
    # 所以前提始终是「直连对端可信」——它才可能帮我们挡住伪造的左侧值。
    first = forwarded.split(",", 1)[0].strip()
    return first or peer
```

注释里的推论是错的：`X-Forwarded-For` 是**追加**语义，不是覆盖语义。最外层代理看到请求头里已经有值，标准做法（nginx 的 `$proxy_add_x_forwarded_for`、绝大多数 ingress 默认行为）是**在右边追加**自己看到的地址，而不是丢掉左边的值。所以：

```
客户端自己发:  X-Forwarded-For: 9.9.9.9
nginx 追加后:  X-Forwarded-For: 9.9.9.9, 198.51.100.7   ← 真实来源在右边
本实现读到的:  9.9.9.9                                   ← 攻击者随手写的那一段
```

实测（`TRUSTED_PROXIES=127.0.0.1`，直连对端固定 `127.0.0.1`，只改 XFF 最左段）：

```
12 次错误口令的状态码: [401, 401, ..., 401]     ← 12 次全过，没有一次 429
计数表条目数: 12
计数表键: ['admin|203.0.113.0', 'admin|203.0.113.1', 'admin|203.0.113.2', ...]

对照（不带 XFF，同一账号）: [401, 401, 401, 401, 401, 429]   ← 限流本身是好的

代理「追加」模式下 client_ip() 的取值：
  真实客户端 198.51.100.7  →  client_ip() = 9.9.9.9
```

也就是说：**只要部署在追加式反代后面、且按文档配了 `TRUSTED_PROXIES`，爆破者每个请求换一个假的 XFF 前缀，就能让限流完全不生效**——这恰好是引入 `TRUSTED_PROXIES` 想解决的问题。没配 `TRUSTED_PROXIES` 时反而是安全的（不读头），等于「配对了部署，反而打开了洞」。

**为什么测试没拦住**：`tests/test_net.py:37-40` 把这个行为写成了断言。

```python
def test_trusted_proxy_yields_the_original_client(...):
    """对端在名单里：取最左边的第一跳，而不是代理链里的任意一跳。"""
    monkeypatch.setattr(settings, "trusted_proxies", "127.0.0.1")
    assert net.client_ip(_request("127.0.0.1", "198.51.100.7, 10.0.0.1")) == "198.51.100.7"
```

这和 R2 里 `test_task_api.py:124-126` 把 `scope=all` 的漏洞写成断言是同一个模式：**测试忠实记录了实现，但没人问过这个实现对不对**。缺陷被固化进 CI，比缺陷本身更难发现——后面的人看到绿灯就不会再看这一段。

**建议**：从右往左走，跳过可信代理，取第一个不可信地址（uvicorn 的 `--proxy-headers` 就是这么做的，源码注释原文：*each proxy appends to the header list so check it in reverse order*）。

```python
def _is_trusted(host: str, nets: _Networks) -> bool:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False          # 不是合法地址：当作不可信，但也不要拿它当键
    return any(addr in net for net in nets)


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else ""
    nets = trusted_networks()
    forwarded = request.headers.get("x-forwarded-for", "")
    if not peer or not forwarded or not nets or not _is_trusted(peer, nets):
        return peer

    hops = [h.strip() for h in forwarded.split(",") if h.strip()]
    for hop in reversed(hops):
        if _is_trusted(hop, nets):
            continue
        return hop if _valid_ip(hop) else peer
    return hops[0] if hops else peer
```

配套改三处：

1. `tests/test_net.py` 那条断言改成「`198.51.100.7, 10.0.0.1` 且两者都可信时返回最左；只有右侧可信时返回右侧第一个不可信地址」，并补一条「客户端自带的伪造前缀被跳过」的用例。
2. 文档里补一句**不要**把 `TRUSTED_PROXIES` 配成 `0.0.0.0/0`：那时候"跳过可信"等价于"永远取最左"，又回到可伪造。
3. `core/net.py` 开头那段"两者选一个"的说法要收敛：如果运维同时开了 uvicorn `--proxy-headers`，`request.client` 已经被改写成真实客户端，本函数会走"对端不可信 → 直接用 peer"的分支，结果正确；但两条路径同时生效会让"到底谁在解析头"变得不可推理，建议文档里明确只保留一条。

---

## 三、🟡 R6-02 登录验密在事件循环里跑 scrypt：10 个并发就能让整个服务停 1.5 秒

**位置**：`modules/identity/service.py:162-164`（`verify_password` 是同步调用）、`core/security.py:28-41`

`hashlib.scrypt(N=2**14)` 单次在本机约 **147 ms**，而且它是在 `async def authenticate` 里**同步**跑的——这段时间整个事件循环被占住，其它请求（包括静态接口、健康检查）一个都进不来。

实测（10 个并发登录，用户名各不相同，全部走"用户不存在"路径，因此没有任何限流延迟）：

```
单次 verify_password: 147.4 ms
10 个并发状态码: [401] × 10
10 个并发总耗时: 2992 ms（串行上限约 1474 ms）
心跳最大间隔: 1492 ms           ← 后台协程被卡住 1.5 秒
```

心跳那条才是要害：**不是"登录变慢"，是"进程内所有协程停摆 1.5 秒"**。登录是唯一匿名可达的接口，而且限流键里带用户名——换用户名就换键，每次尝试都只有 1 次失败记录，`penalty()` 一直是 0。所以单个攻击者用 10 条并发连接反复打，就能让整个 API 处于持续不可用状态，成本比 R5-01 那个"锁账号"低得多。

这不是这一轮引入的（scrypt 一直是同步的），但这一轮把"登录的 CPU 成本"当成一等公民来设计（渐进延迟就是在给 CPU 成本定价），却把最贵的那一段留在循环里，值得一起收掉。

**建议**：验密丢到线程池，并给并发设上限（`to_thread` 默认执行器上限是 32，32×147ms 仍然会挤爆 CPU）：

```python
_VERIFY_GATE = asyncio.Semaphore(2)   # 按核数定，2~4 之间

async def _verify(raw: str, encoded: str) -> bool:
    async with _VERIFY_GATE:
        return await asyncio.to_thread(verify_password, raw, encoded)
```

`_set_password` 里的 `hash_password` 同理（改密/重置/`create-admin` 三条路径都要哈希）。改完之后顺手补一条**并发**用例：N 个并发登录期间，一个 `GET /health`（或任意轻接口）的响应时间不超过 100ms——这条用例才是真正守住这个不变式的东西，单测并发数写 8 就够。

---

## 四、🟡 R6-03 `{"isActive": null}` 会静默停用账号

**位置**：`modules/identity/service.py:317-324`

```python
        if "is_active" in fields:
            active = bool(fields["is_active"])
```

`UserUpdateIn.is_active` 是 `bool | None = None`，`model_dump(exclude_unset=True)` 只排除"没传"，**不排除"传了 null"**。于是 `{"isActive": null}` 落进 `bool(None) = False`，账号被停用。

实测：

```
初始 isActive: True
PATCH {"isActive": null}      -> 200   isActive = False     ← 期望：不生效或 422
PATCH {"displayName": "改名"} -> 200   isActive = False     ← 确认已落库，不是回显问题
```

同一个文件里 `email` / `avatar_url` 的 `null` 语义是明确的（"传 null 清空"，schema 描述里写了），但 `is_active` 的 `null` 从来没人定义过。前端表单里"这个开关没填"发 null 是很常见的，而后果是**把一个正常账号停掉**，还带着"最后一个超管"的连带判定，排查时会往权限方向查很久。

顺带一个同类隐患：`fields.get("is_active") is False` 这个反向判定（`service.py:331`）依赖上面那个 `bool()` 转换，`null` 同样会被当成 False 走进 `_guard_has_superuser`——它不是漏洞（守卫多跑一次无害），但它说明这个字段的三种取值（缺省 / null / false）没有被区分过。

**建议**：`is_active` 显式不接受 `null`，两种写法任选：

```python
# A. 只认真正的布尔
if fields.get("is_active") is not None:
    ...
# B. schema 里就挡掉
@field_validator("is_active", mode="before")
def _no_null(cls, v): 
    if v is None: raise ValueError("isActive 不能为 null")
    return v
```

推荐 B + A 一起：B 给调用方一个明确的 422，A 让内部调用（脚本、CLI）也安全。补一条用例：`PATCH {"isActive": null}` → 422，且 `GET` 回来 `isActive` 仍为 `True`。

---

## 五、🟡 R6-04 停用超管要超管，恢复超管不要

**位置**：`modules/identity/service.py:316-324`

```python
        if "is_active" in fields:
            active = bool(fields["is_active"])
            if not active and user.id == actor.id:
                raise CannotTargetSelf()
            if not active:
                # 停用超管 = 让对方下不来台，与「重置超管密码」同级，需要超管
                await self._guard_target_not_above(user, actor)
            user.is_active = active
```

守卫写在 `not active` 分支里，**只守了"关"这一个方向**。把账号重新打开是"恢复访问权"，属于授权方向，按 R5-03 定下的原则（授予方向必须有闸）它更需要守卫。

实测：一个只有 `identity:read` + `identity:update` 的运营账号，对一个**超级管理员**账号：

```
运营停用该超管账号: 403        ← 守卫生效
运营恢复该超管账号: 200        ← 同一个账号、同一个接口、方向反过来就放行
  → 返回体里 isActive = True，roles = [{code: "admin", isSuperuser: true}]
```

这一条单独看危害有限（运营不知道对方口令，恢复后也不是自己的号），但它和 R5-03 是**同一个形状的漏洞**：上一轮刚把"给权限有闸、撤权限没闸"补上，这里又留了一处"关有闸、开没闸"。安全事件后的处置流程通常是"先停用、排查、再启用"，而"启用"这一步恰好不需要超管——如果被停用的正是内鬼账号，一个中级管理员就能把它放回来。

顺带：`update_user` 也是"停用"的三条入口之一（另外两条是 `deactivate_user` 和 `DELETE /users/{id}`），三处都调了 `_guard_target_not_above`，唯独恢复没有对应入口——说明这条不变式现在是"按操作名"记的，而不是"按权限变化"记的。

**建议**：把守卫提到分支外，两个方向都过（`CannotTargetSelf` 只在"停用自己"时保留）：

```python
        if "is_active" in fields:
            active = fields["is_active"]
            if active is False and user.id == actor.id:
                raise CannotTargetSelf()
            if active is not user.is_active:      # 状态真的变了才校验
                await self._guard_target_not_above(user, actor)
            user.is_active = active
```

用例：`identity:update` 持有者恢复超管账号 → 403，与停用同一错误码（`40323`）。

---

## 六、🟡 R6-05 `retry:any` 顺带成了「读任意失败任务」的旁路

**位置**：`modules/task/service.py:143`、`modules/task/router.py:108-112`

R5-05 的修法本身是对的（`read:all` 与 `retry:any` 互不蕴含，不能先过可见性），但它把 `get_task()` 整个换成了 `repo.get_or_404()`，而这个方法只按主键取行、**没有任何 scope 过滤**（`db/repository.py:40`）。后果是响应体：

```python
task = await service.retry_task(task_id, actor)
return ApiResponse.ok(service.to_out(task))     # 完整 TaskOut：title / prompt / authorName / ...
```

实测：持有 `task:read` + `task:update` + `task:retry:any`（**没有** `read:all`）的客服账号：

```
GET  /tasks/{他人的任务}        → 403
GET  /tasks?scope=all 可见条数  → 0
POST /tasks/{他人的任务}/retry  → 200
  返回字段: [aspectRatio, authorId, authorName, coverUrl, createdAt, downloadable,
            durationSec, errorMessage, favorited, id, model, modelId, progress,
            prompt, referenceAssetIds, ...]
  title = 机密项目A   authorName = 老大
```

也就是"看不见但能拿到全字段"。影响面有两个限制（只对 `failed` 状态生效、会真的触发一次生成消费），所以不是 R4-06 那种级别的越权；但它是**意料之外的读通道**：客服本来只需要"帮着重跑"，现在顺带获得了"按 id 读任意失败任务的 prompt 与作者"。如果有"任务内容按项目隔离"的需求，这里就是缺口。

另外 `get_or_404` 把"不存在"和"不在你范围"合并成了同一个 404/403 差异，而 `retry` 又先判状态再判归属之外的条件，错误码会告诉调用方"这个 id 存在且失败了"——存在性同样泄露。

**建议**（按代价从小到大）：

1. **只改返回体**：`retry` 返回 `TaskRetryOut`（`id` + `status` + `queuedAt`），前端重试后本来就该刷新列表，不需要整份详情。这条最省事，也最符合"重试是一个动作，不是一次查询"。
2. **按可读性决定投影**：`actor.has(PERM_READ_ALL) or task.author_id == actor.id` 时才 `to_out`，否则返回最小投影。

判定的**顺序**是对的（先判归属再判状态，无 `retry:any` 的人不会从"仅失败的任务可以重试"这句里读到状态），这一点不用动。

测试补一条：客服重试 200 后，响应体里**不含** `prompt` / `authorName`；并且 `GET` 同一个 id 仍然 403。

---

## 七、🟡 R6-06 `app seed` 没有环境护栏，生产可写入固定口令的超管

**位置**：`cli.py:40-44`、`seed.py:32-35`（`_USERS`）、`seed.py:128-133`

`seed.py` 的注释写着"仅演示库，生产用 `app create-admin`"，但那是注释，不是判断：

```python
def _cmd_seed(args):
    from seed import seed
    counts = asyncio.run(seed(reset=args.reset))    # 没有 app_env 判断
```

而 `seed()` 建号时用的是 `hash_password(password)` 直接写库，口令来自 `_USERS` 的固定字符串（`admin` / `admin@12345`）。`app.py:61` 只在启动时判了 `if settings.app_env != "prod": await init_db()`，**CLI 这条路完全没判**。

后果取决于运维习惯，但这条链是常见的：Docker entrypoint / 部署脚本里习惯性写一句 `python -m cli seed`（为了初始化数据），到了生产环境就把 `admin/admin@12345` 这个已知口令的**超级管理员**写进了库。而且 `ensure_superuser`（`create-admin`）走的是"存在则重置口令"，`seed` 走的是"直接插入"——如果生产的超管叫别的名字，`admin` 会被顺利插入，不会有任何冲突报错。

**建议**：

```python
def _cmd_seed(args):
    from core.config import get_settings
    if get_settings().app_env == "prod" and not args.force:
        print("生产环境拒绝写入演示数据；确需写入请加 --force", file=sys.stderr)
        raise SystemExit(1)
```

用例：`APP_ENV=prod` + `main(["seed"])` → `SystemExit(1)`。顺手把 `seed.py` 里的 `hash_password` 换成 `identity._set_password`（见 R6-08），让"改口令必须写 `password_changed_at`"这条不变式没有第四个例外。

---

## 八、💭 细节（三条）

**R6-07 写操作的查询数翻了一倍**：`_guard_target_not_above` 内部调 `resolve_identity(target.id)`（用户 1 次 + 角色 1 次 + 角色权限 1 次），而 `set_user_roles` 在这之前已经 `_require_user`、在这之后还要 `users.roles_for` 与 `set_roles`——一次"改角色"现在有 8 次以上往返。`_roles_by_ids`（`service.py:618`）也是逐 id `await`，5 个角色就是 5 次查询。建议：`_require_role` 批量改成 `select(Role).where(Role.id.in_(...))`（一条 SQL，顺带天然保序校验缺哪个 id）；`_guard_target_not_above` 接受一个可选的"已解析身份"参数，让 `set_user_roles` 复用。这不是性能瓶颈（管理接口频率低），但它让"每次加一个守卫 = 每次多三层查询"变成习惯，模块一多就不好收。

**R6-08「唯一的口令写入点」这句话不成立**：`_set_password` 的 docstring 写"**唯一**的口令写入点……三条改密路径共用这一个方法"，但 `seed.py:132` 还有第四处 `password_hash=hash_password(password)`。语义上可以辩（演示数据没有"旧令牌需要作废"这回事），但注释里的"唯一"会让人下次加第五条路径时不再检查。要么把 seed 也接进来，要么把注释改成"运行期唯一；seed 是演示数据、不写 `password_changed_at`，语义是'从未改过密码'"。

**R6-09 R4-07 仍然没动**：`image/service.py:24-30` 的 `list_jobs` 还是 `ImageJob.owner_id == actor.id`，没有 `read:all` 分支，与 `task` 模块的范围模型不一致。R4 定为 💭，第五轮没动、这轮也没动——列在这里只是避免它从清单里悄悄消失。

---

## 九、值得保留的做法（这轮新增）

- **`_set_password` 收敛三条改密路径**：这是这轮质量最高的一处。它不是"修一个 if"，而是把"改口令"变成一个只有一种写法的动作——`password_changed_at` 这种不变式只有收敛写入点才守得住。附带的 `test_cli_password_rotation_invalidates_tokens` 还刻意 `sleep(1.1)` 避开秒级边界，说明作者知道 `iat` 是秒级的（R5-08），测试写得很诚实。
- **`policy.py` 把口令策略从"权限点声明"里拆出来**：`permissions.py` 的职责因此变纯了（只有权限码与内置角色），而 CLI 与 schema 用同一份规则这件事，是靠"只有一个实现"保证的，不是靠"记得同步改两处"。
- **`_guard_role_not_above` 与 `_guard_target_not_above` 成对存在，注释互相指认**：`service.py:697-703` 明确写了"同一件事换个入口就绕过去了"。这种"守卫之间互相注释"的习惯，比在每个守卫里各写一段原理更耐用。
- **渐进延迟的键设计**：`penalty` 只惩罚"猜错"这个动作，而键是 `用户名|IP`，所以攻击者制造的延迟只落在他自己的键上——正常用户从自己的 IP 登录不受影响。这个细节比"加个 sleep"要难想到，是对的。
- **`record_failure` 里的 `_sweep` + `MAX_KEYS` 双上界**：`_existing()` 刻意"只读不创建"，注释直说"否则随便编个用户名也能往表里塞一条空记录"。这种"读路径不能有副作用"的意识在限流实现里很少见。
- **`trusted_proxies` 的 field_validator 选择启动期炸掉**：把"配错了但看起来能用"转成"配错了起不来"，这类配置错误的排查成本本来就该在启动期付。

---

## 十、建议顺序

| # | 动作 | 预估 | 理由 |
|---|---|---|---|
| 1 | R6-01 XFF 改从右往左取 + 换掉那条固化错误的断言 | 40 min | 它让上一轮的限流修复在真实部署下失效，且测试正在保护这个错误 |
| 2 | R6-03 + R6-04 `is_active` 的 null 与双向守卫 | 30 min | 同一个字段的两处语义缺口，一个函数内改完 |
| 3 | R6-02 验密/哈希丢线程池 + 并发用例 | 40 min | 10 个并发匿名请求就能让整个进程停 1.5s，改起来只有几行 |
| 4 | R6-05 `retry` 返回最小投影 | 20 min | 越权读通道，且顺手让"重试"的语义更干净 |
| 5 | R6-06 `app seed` 生产护栏 | 10 min | 一行判断，挡住"部署脚本顺手 seed"这种最常见的踩法 |
| 6 | R6-07 / R6-08 / R6-09 批量查询、注释口径、image 范围 | 1 h | 不急，但别让"唯一写入点"这种说法继续误导下一个人 |

第 2、3、4 项都只改 `identity/service.py` 与 `task/service.py`，可以一个 PR；第 1 项独立，且**改动前后必须各跑一遍 `tests/test_net.py`**——那条断言现在保护的是错误行为，改实现不改断言会红，改了断言才算真的修完。

---

## 十一、验证方式（可复现）

```bash
cd api

# 基线
./.venv/Scripts/python.exe -m pytest -q            # 168 passed
./.venv/Scripts/python.exe -m ruff check src tests # All checks passed
./.venv/Scripts/python.exe -m mypy src             # 109 files clean
```

R6-01 的端到端复现（`TRUSTED_PROXIES` 必须在 import 前设好，`TestClient` 的 `client=` 指定直连对端）：

```python
import os, tempfile, asyncio
os.environ.update({
    "APP_ENV": "test", "WORKER_ENABLED": "false", "AI_PROVIDER": "echo",
    "DB_URL": "sqlite+aiosqlite:///" + tempfile.mkdtemp() + "/p.db",
    "TRUSTED_PROXIES": "127.0.0.1",
})
from fastapi.testclient import TestClient
from app import create_app
from seed import seed
from modules.identity import throttle

app = create_app(); asyncio.run(seed(reset=True))
with TestClient(app, client=("127.0.0.1", 5555)) as c:
    throttle.clear()
    codes = [c.post("/api/v1/identity/login",
                    json={"username": "admin", "password": f"bad-{i}"},
                    headers={"X-Forwarded-For": f"203.0.113.{i}"}).status_code
             for i in range(12)]
    print(codes, len(throttle._failures))   # [401]*12, 12  ← 应为 [401]*5 + [429]*7
```

R6-02 的事件循环停顿用"心跳协程 + 并发登录"测，关键指标是心跳的**最大间隔**（正常 5~10ms，实测 1492ms），不是请求耗时。

R6-03 / R6-04 / R6-05 直接打接口即可，上文各节给了请求与期望码；R6-06 用 `main(["seed"])` + `APP_ENV=prod` 断言 `SystemExit(1)`。
