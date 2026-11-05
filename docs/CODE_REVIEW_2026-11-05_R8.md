# 代码评审报告（第八轮 · 仅后端 · R7 修复复核）

> 评审时间：2026-11-05 16:23 ｜ 范围：`998ba8c`（R7 的 6 项修复）及其直接影响面；前端不在范围内
> 关联：[`R7`](./CODE_REVIEW_2026-11-05_R7.md)｜[`R6`](./CODE_REVIEW_2026-11-05_R6.md)｜[`R5`](./CODE_REVIEW_2026-11-05_R5.md)｜[`R4`](./CODE_REVIEW_2026-11-05_R4.md)｜[`R3`](./CODE_REVIEW_2026-11-05_R3.md)｜[`R2`](./CODE_REVIEW_2026-11-05_R2.md)｜[`首轮`](./CODE_REVIEW_2026-11-05.md)｜[`CODE_STYLE.md`](./CODE_STYLE.md) / [`REVIEW_CHECKLIST.md`](./REVIEW_CHECKLIST.md)

---

## 一、结论

**R7 的 6 条全部落地，实测通过**。R7-01 的闸门写法比建议更严谨——它不是简单 `async with semaphore` 包一层，而是把「查库 + 验密」整段包进去，并且在失败分支里**主动 `rollback()` 把连接当场还池**，否则"闸门最多放 N 个"这句话在请求 teardown 之前根本不成立。这条注释值得留着。

这一轮的问题仍然不是"改错了"，而是**同一条修法只贴了一个门**：

R7-01 治的是"排队时攥着连接"，但只治了 `authenticate`。`change_password` 里**两次 scrypt（验旧口令 + 算新口令）既没有闸门、顺序也还是"先拿连接再排队"**。结果同一个症状换了个门就能复现，而且门槛更低——不需要猜任何人的口令，**只要有一个能登录的账号**（实习生、只读账号都行），20 个并发请求就能把连接池顶满，把一次 `select 1` 从 1 ms 拖到 224 ms。

这已经是连续第三轮出现同一个模式了（R5-03「只守撤权一个方向」、R6-04「只守停用一个方向」、R8-01「只守一个入口」）。建议把这条写进 `REVIEW_CHECKLIST.md`：**给某个入口加限制时，先数一遍"还有哪些路径做同一件事"**。

### R7 逐条复核（全部实测）

| 编号 | 结论 | 证据 |
|---|---|---|
| R7-01 登录排队持有连接 | ✅ 修好 | 40 并发失败登录 → 连接峰值 **4**（池 5+10）；失败分支在闸门内 `rollback`，连接当场回池 |
| R7-02 `GET /images` 无分页 | ✅ 修好 | 返回 `['items','page','pageSize','total']`；`page=1,pageSize=3` → 3 条、`total=4`（count 而非 `len`）；第 1/2 页 id 交集 **0**；`pageSize=1000` → **422**（`le=100` 生效） |
| R7-03 改他人邮箱无守卫 | 🟡 部分修好 | 非超管改超管邮箱 → **403 / 40322**；但同一次循环里的 `display_name`、`avatar_url` 仍是 **200**（见 R8-02） |
| R7-04 前端 retry 泛型 | ➖ 按分工未动 | 后端 `TaskRetryOut` 已在 `schema.ts`，属前端迁移债 |
| R7-05 数据范围/分页写进文档 | ✅ 落地 | `modules/asset/__init__.py` 写明"没有 `asset:read:all` 是决定不是遗漏"，`EXTENDING.md` §1.1 三模块对照表 |
| R7-06 哈希池无 shutdown | ✅ 修好 | 连续 5 轮「哈希 → `shutdown_hash_pool()` → sleep」：scrypt 线程数每轮回 **0**，无累积；关闭后再哈希自动重建，可用 |

### 质量基线（实测）

```
pytest         184 passed, 1 warning (42.85s)
ruff           All checks passed
mypy           Success: no issues found in 110 source files
export-openapi 无变化（json / md / ts 三件套）
```

---

## 二、🟡 R8-01 口令闸门只装了 login 一个入口，改密路径能原样复现 R7-01

**位置**：`src/modules/identity/service.py:272-277`（`change_password`）、`:633-646`（`set_password`）、`src/modules/identity/router.py:78-83`（路由只挂 `ActorDep`）

`password_slot()` 目前的唯一调用点是 `authenticate`（`service.py:179`）。而所有 scrypt 调用点是这几个：

```
service.py:182   authenticate       ← 有闸门
service.py:274   change_password    ← 无闸门（验旧口令）
service.py:645   set_password       ← 无闸门（算新口令，reset_password / create_user / create_admin 都走它）
service.py:88    _dummy_hash        ← 在闸门内，无妨
```

`change_password` 的形状和 R7-01 修之前**一模一样**：

```python
user = await self._require_user(user_id)          # ← 先取走一条连接
if not await verify_password_async(...):          # ← 再在 4 个 worker 上排队
    raise OldPasswordMismatch()
await self.set_password(user, payload.new_password)  # ← 排第二次
```

而这条路由**只需要登录，不需要任何权限点**，也没有限流。旧口令传错时它只花 1 次 scrypt、**不改任何状态**，可以无限重放。

### 实测（同机、真实 scrypt 耗时 263 ms、池 5+10）

```
并发改密 n= 0 → 一次 select 1 耗时      1 ms，连接峰值  1
并发改密 n=20 → 一次 select 1 耗时    224 ms，连接峰值 15   ← 顶满
并发改密 n=60 → 一次 select 1 耗时   1579 ms，连接峰值 15

对照（R7-01 修好之后）：40 并发失败登录 → 连接峰值 4
```

也就是说：**匿名爆破者做不到的事，一个只读账号 20 个并发就做到了**。R7-01 把门槛从"匿名"抬到了"有账号"，但没堵住。

持续压测（4 个并发改密循环、持续 3 秒）对登录的影响是温和的——因为双方单位成本相同，攻击者并不放大：

```
无洪水              登录 22 次 / 均值 137 ms / p90 141 ms
1 个账号持续改密      登录 22 次 / 均值 140 ms / p90 144 ms
4 个并发改密         登录 18 次 / 均值 169 ms / p90 237 ms
```

真正的问题是**连接**，不是 CPU：等待哈希时连接已经借出去了，于是"查库的接口一起变慢"。这一点与 R7-01 完全同构。

### 建议

最小改法是把闸门挪到 `set_password` / `verify_password_async` 的更外层，而不是每个业务方法各包一次——现在这个形状注定会漏（漏的就是 `change_password`）。两个方向：

1. **在 service 层包**（改动小）：`change_password` 与 `reset_password`、`create_user` 统一包进 `password_slot()`，并且把 `_require_user` 挪到闸门**里面**——顺序必须是"先排队、再拿连接"，否则闸门白加。
2. **在 security 层兜底**（更彻底）：把闸门做进 `hash_password_async` / `verify_password_async` 本身，让"忘了包"这件事不可能发生。代价是 `authenticate` 想额外把查库也包进来时会形成嵌套——`asyncio.Semaphore` 不可重入，会自锁。所以要配一个"已在闸门内"的标记（`contextvars`），或者在 security 层只对**未持闸门**的调用取名额。

我倾向第 2 种 + `contextvars` 标记：这个坑（谁调用谁负责排队）已经踩了两次，靠约定守不住。

另外顺手加两条：`POST /me/password` 的失败计数并入现成的 `throttle`（键用 `user_id`，不要用 IP——这里已经是登录态，IP 没有意义）；`PasswordChangeIn` 之外的成功路径也留着，别把正常改密限死。

---

## 三、🟡 R8-02 资料字段守卫只覆盖了 email，同一循环里另外两个字段照旧放行

**位置**：`src/modules/identity/service.py:356-366`

```python
if "email" in fields and fields["email"] != user.email and user.id != actor.id:
    await self._guard_target_not_above(user, actor)   # ← 只守 email

for field in ("display_name", "email", "avatar_url"):  # ← 三个字段一起写
    if field in fields:
        setattr(user, field, fields[field])
```

R7-03 的理由写得很对——"这类『暂时无害』的写权限最容易在半年后变成漏洞"。但结论只落到了三个字段里的一个。

### 实测

`hr01`（自建角色 `hr`，只有 `identity:read` + `identity:update`，非超管）对超管账号：

```
email        → 403 40322 操作超级管理员账号 admin：只有超级管理员可以操作
displayName  → 200 OK
avatarUrl    → 200 OK
```

`displayName` 和 `avatarUrl` 三个后果都不是"暂时无害"：

- **改名即社工**：把"老大"改成"系统管理员"或某个同事的名字，在这个界面里就是一个可信的冒充入口，尤其在审批/协作类流程里。
- **换头像即外带 IP**：`avatarUrl` 会渲染成 `<img src>`，指向攻击者域名就能记录**谁在什么时候打开了这个页面**（超管自己打开自己的资料页也算）。这是"暂时无害"字段里最典型的延迟爆炸项。
- **审计失真**：`display_name` 出现在任务作者、操作日志里，改名等于篡改历史归属的显示。

### 建议

这行守卫本来就该和 email 写在同一个条件里——现在这样写反而更费解（读者会以为另外两个字段有什么特殊豁免）。最省事的修法是：**"改别人的资料"整段过一个守卫**，而不是逐字段判断：

```python
touches_other = user.id != actor.id
if touches_other and any(f in fields for f in ("display_name", "email", "avatar_url")):
    await self._guard_target_not_above(user, actor)
```

顺带把 `email: null` 的漏洞一起收掉（见 R8-03）：当前 `fields["email"] != user.email` 在"对方本来就没有邮箱、这次传 null 清空"时是 `None != None` → `False`，守卫直接跳过。

---

## 四、💭 R8-03 `X-Request-Id` 无条件采信客户端值

**位置**：`src/api/middleware.py:33`

```python
rid = headers.get(REQUEST_ID_HEADER.lower().encode(), b"").decode() or uuid.uuid4().hex[:16]
```

三个问题，都很轻但都是"迟早要处理"：

1. **无长度上限**：一个 64 KB 的 `X-Request-Id` 会被原样写进该请求的每条日志行，并回写到响应头。日志系统的单行上限一旦被打爆，这条请求的所有上下文都会丢。
2. **无字符集限制**：`\r\n` 在 HTTP 层会被 uvicorn 拒掉，但 `\t`、控制字符、超长 Unicode 都能进日志——日志查看器里做转义，还是会被"看起来像另一条日志"骗过去。
3. **可伪造关联**：别人可以把你的 `X-Request-Id` 塞进自己的请求，让两条无关的链路在日志里粘在一起。排障时这比没有 id 更糟。

建议：只接受 `^[A-Za-z0-9_-]{8,64}$`，不合法就自己生成（**不是**报错——这是辅助头，不该让请求失败）。

---

## 五、💭 三条一致性与契约余留

**R8-04 `isActive` 与当前值相同时不过守卫，可当成存在性探测。** `service.py:352` 的 `if active != user.is_active:` 让"设成原值"变成免检操作：非超管对超管 `PATCH {"isActive": true}` → **200**，对不存在的 id → 404。两个状态码合起来就是一个廉价的 id 枚举器。同理 `email` 传原值、`displayName` 传原值都免检。这不是提权，但"守卫按值变化触发"这个写法会在每个字段上重复一遍这个洞——R8-02 建议的"整段过一个守卫"会顺手修掉它。

**R8-05 `GET /images` 的分页默认值会让前端静默截断。** 后端默认 `pageSize=24`，而 `studio/src/entities/image/api/image.api.ts:12` 是 `api.get<Paginated<ImageJob>>('/images')`——没传任何分页参数。改动前它拿到全部，改动后最多 24 条，而 UI 上没有翻页控件。这不是后端的错（不传就该有默认值），但**这是一次"后端修好了、用户历史少了一半"的静默回归**，只有第 25 条记录消失时才会有人报 bug。按分工我不改前端，但建议在这条契约变更的提交说明里点明"前端需要补 `pageSize` 或翻页"，否则下一轮它会被当成后端 bug 重新报回来。

**R8-06 `paginate` 的排序键不唯一。** `image/service.py:37` 是 `order_by(ImageJob.created_at.desc())`，`task` 的列表同样只有时间键。时间戳相同的行在翻页边界上顺序不确定，可能重复或漏掉（SQLite 实际按 rowid 兜底，所以现在测不出来；换 PG 或加了并发写入就会现形）。分页查询的排序键应该以主键收尾：`order_by(created_at.desc(), id.desc())`。`db/repository.py:57` 的 `paginate` 是公共入口，在这里补一条"排序键必须唯一"的注释比逐个调用点改更划算。

---

## 六、值得保留的做法（这轮新增）

1. **失败分支在闸门内 `rollback()`**（`service.py:187`）。这条不是"顺手清理"，而是闸门成立的前提——注释里写清了"留到请求 teardown 才还的话，这句话就不成立"。这是本轮最好的一处。
2. **`_hash_pool()` 惰性创建 + `shutdown_hash_pool()` 置 None**（`security.py:66-87`）。测试、`--reload`、正常停机走同一条路，不需要"只在测试里调用"的特例分支。实测 5 轮无累积。
3. **`_hash_gate()` 按事件循环取闸门**（`security.py:89-104`）。注释点明了真实约束：`Semaphore` 一旦在有竞争时被使用就绑定到当时的 loop，测试里每个 `TestClient` 都是新 loop。这个坑不写下来，下一个人一定会踩。
4. **`asset` 模块用文档写"没有 `read:all` 是决定"**（`asset/__init__.py`）。缺省和遗漏在代码里长得一模一样，唯一能区分的办法就是写下来——而且它给了"要打开它，先回答谁需要看别人的原始上传，再照 `modules/image` 的样子补分页"的具体路径，不是一句"暂不支持"。
5. **`EXTENDING.md` §1.1 把「数据范围 / 分页」变成列表接口的必答两问**。这是把连续几轮的教训沉淀成规范，比修单个 bug 值钱。

---

## 七、建议顺序

| 顺序 | 事项 | 理由 |
|---|---|---|
| 1 | **R8-01** 闸门下沉到 `security` 层（`contextvars` 标记防自锁）+ `change_password` 失败计入 `throttle` | 唯一能让"一个有账号的人拖垮全站"的项；改法要一次到位，别再逐个入口贴 |
| 2 | **R8-02** 资料字段整段守卫（顺带修 R8-04 的值相等免检） | 与 R8-01 同文件，一起改成本最低；`avatarUrl` 那条越早越好 |
| 3 | **R8-05** 前端补 `pageSize`/翻页（跨端，需协同） | 已经在影响用户，只是还没人发现 |
| 4 | **R8-03** `X-Request-Id` 校验；**R8-06** 排序键补主键 | 都是十分钟级，可并到任一次提交 |
| 5 | 把"加限制时先数一遍同类入口"写进 `REVIEW_CHECKLIST.md` | 这是连续三轮的同一个模式，规范比记性可靠 |

---

## 八、验证方式（可复现）

```bash
# 基线
cd api && ./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe -m ruff check src tests
./.venv/Scripts/python.exe -m mypy src
./.venv/Scripts/python.exe -m cli export-openapi --check
```

**R8-01 连接池**（服务层直调，避开 HTTP 层噪音；`old_password` 传错以模拟"不计入限流、不改状态"的免费路径）：

```python
# 关键点：change_password 收的是 user_id，不是 username
# （我第一次传 "admin" 得到 UserNotFound，3 秒"完成"4683 次改密——那是空转，不是加速）
async def bad_change():
    async with factory() as s:
        try:
            await IdentityService(s).change_password(admin_id, P)  # P.old_password = "wrong"
        except Exception:
            pass

# 结果
# n= 0 → select 1 耗时    1 ms，连接峰值  1
# n=20 → select 1 耗时  224 ms，连接峰值 15
# n=60 → select 1 耗时 1579 ms，连接峰值 15
```

**R8-01 打假提醒**：第一次测这组时我用了 8/24 个并发一次性打完就结束，登录只从 138 ms 涨到 172 ms，看起来"没什么影响"。那不是没问题，而是**测法太轻**——必须持续压（3 秒）+ 同时测连接峰值，症状才在连接维度上现形。

**R8-02 字段守卫**：需要"有 `identity:update` 但不是超管"的账号，seed 里没有，现建：

```python
role = POST /identity/roles {"code":"hr","permissions":["identity:read","identity:update"]}
usr  = POST /identity/users {"username":"hr01","roleIds":[role_id], ...}
PATCH /identity/users/{admin_id} {"email":"evil@example.com"}  # 403 / 40322
PATCH /identity/users/{admin_id} {"displayName":"改名"}         # 200  ← R8-02
PATCH /identity/users/{admin_id} {"avatarUrl":"http://evil/x.png"}  # 200  ← R8-02
```

**R7-02 分页**：`page=1,pageSize=3` → `total=4`（count 而非 `len(rows)`）、两页 id 交集 0；`pageSize=1000` → 422。

**R7-06 线程池**：连续 5 轮 `hash → shutdown_hash_pool() → sleep(0.15)`，`scrypt` 线程数每轮回 0。
