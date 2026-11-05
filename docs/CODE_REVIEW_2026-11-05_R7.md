# 代码评审报告（第七轮 · 仅后端 · R6 修复复核）

> 评审时间：2026-11-05 16:07 ｜ 范围：`182e223`（R6 的 8 项修复）及其直接影响面；前端不在范围内
> 关联：[`R6`](./CODE_REVIEW_2026-11-05_R6.md)｜[`R5`](./CODE_REVIEW_2026-11-05_R5.md)｜[`R4`](./CODE_REVIEW_2026-11-05_R4.md)｜[`R3`](./CODE_REVIEW_2026-11-05_R3.md)｜[`R2`](./CODE_REVIEW_2026-11-05_R2.md)｜[`首轮`](./CODE_REVIEW_2026-11-05.md)｜[`CODE_STYLE.md`](./CODE_STYLE.md) / [`REVIEW_CHECKLIST.md`](./REVIEW_CHECKLIST.md)

---

## 一、结论

**R6 的 8 条全部落地，逐条实测通过**，而且有三处比建议做得更彻底：

1. XFF 除了改方向，还处理了「链里出现非法段」——退回直连对端，而不是把一个字符串当限流键（那等于每请求换一个键，绕过方式换了个入口）。
2. 没有用 `asyncio.to_thread`，而是自建**有界**线程池，并在注释里写清了理由（默认执行器上限 32，32 个并发 scrypt 会把「事件循环不阻塞」换成「谁也别想算完」）。这是这轮最值钱的一句注释。
3. `set_password` 从 `_set_password` 变成 `async` 的公开方法，注释写的是「签名不同不是风格问题，而是『它会等待』这件事必须写在类型上」——这正是 R6-08 想要的收敛。

这一轮唯一的新问题不是"改错了"，而是**修好一段、露出了下一段**：CPU 不再是瓶颈之后，每个排在哈希队列里的登录请求**仍然占着一条数据库连接**。于是匿名登录洪水从「卡住事件循环」变成「占满连接池」——症状轻了，但方向仍然是让全站陪着登录一起慢。

### R6 逐条复核（全部实测）

| 编号 | 结论 | 证据 |
|---|---|---|
| R6-01 XFF 取最左段 | ✅ 修好 | 伪造前缀 + 代理追加真实值：12 次错误口令 → `[401×5, 429×7]`，计数表只剩 **1 条键** `admin\|198.51.100.7`；单段伪造（等价代理覆盖写）仍被采信，属正确语义 |
| R6-02 scrypt 阻塞事件循环 | ✅ 修好 | 10 并发登录 **712 ms**（原 2992 ms），心跳最大滞后 **15 ms**（原 1492 ms） |
| R6-03 `isActive: null` | ✅ 修好 | `null → 422`「停用请传 false，保持不动请不要传该字段」；`false → False`；`true → True`；只改昵称不动 `isActive` |
| R6-04 恢复超管无守卫 | ✅ 修好 | 运营恢复超管 **403**、运营停用超管 **403**（两个方向都过 `_guard_target_not_above`） |
| R6-05 `retry` 越权读 | ✅ 修好 | 客服 `GET /tasks/{id}` 403，`POST retry` 200 但返回字段只有 `['id','status','updatedAt']` |
| R6-06 `app seed` 无护栏 | ✅ 修好 | `APP_ENV=prod` + `main(["seed"])` → `SystemExit(1)`；`--force` 可越过 |
| R6-07 角色 N+1 | 🟡 部分修好 | `RoleRepository.get_many` 一条 SQL 取全量 + 本地校验缺失，N+1 消失；但 `_guard_target_not_above` 里仍有一次 `resolve_identity`（3 查询），写路径依旧是 8 次以上往返 |
| R6-08 「唯一写入点」 | ✅ 修好 | `seed.py` 也走 `set_password`；建号路径同样写 `password_changed_at` |
| R6-09 `image` 数据范围 | ✅ 修好 | 新增 `image:read:all`，列表按 `actor.has(...)` 决定是否加 `owner_id` 条件；`editor` 的 `image:*` 通配自然覆盖 |

### 质量基线（实测）

| 项 | 结果 | 变化 |
|---|---|---|
| `pytest -q` | ✅ 180 passed | 原 168，+12 |
| `ruff check src tests` | ✅ All checks passed | — |
| `mypy src` | ✅ 110 files clean | 原 109，+1 |
| `cli export-openapi --check` | ✅ 三件套无变化 | json / md / ts 均与代码一致 |

---

## 二、🟡 R7-01 登录排队时持有数据库连接：CPU 让开了，连接池顶上了

**位置**：`modules/identity/service.py:170-180`（`authenticate` 的取值顺序）、`core/security.py:50-56`（有界线程池）

现在的顺序是：

```python
user = await self.users.get_by_username(username)        # ① 这里取走一条数据库连接
encoded = user.password_hash if user is not None else await _dummy_hash()
ok = await verify_password_async(password, encoded)      # ② 在这里排队等线程池
```

①取到的连接**一直持有到请求结束**（`get_session` 依赖在响应返回后才关闭 session），而②在只有 2~4 个 worker 的池子里排队。所以「排队」这件事发生在**已经占着连接**之后：并发登录一旦超过连接池容量（实测本机 `AsyncAdaptedQueuePool` size=5 + overflow=10 = **15**），超出的请求会一边握着一条连接、一边等哈希。

实测（真实哈希耗时，不人为放大；20 ms 后发起一次普通业务请求 `GET /tasks`）：

```
哈希线程池 worker = 4，连接池 = 5 + 10

并发登录数 → 同一时刻 GET /tasks 的延迟
  n=  1 →      27 ms
  n=  5 →      11 ms
  n= 15 →     117 ms      ← 正好是池容量，开始有排队
  n= 30 →     545 ms
  n= 60 →    1649 ms
  n=120 →    3816 ms

连接持有区间 12 条，峰值同时占用 12 条（n=12 时）
```

也就是说：**一个匿名攻击者只要并行发几十个登录请求，就能让所有「要查库」的接口变慢，而他自己一个有效请求都不用发对**。登录是唯一匿名可达且成本极低的入口，这类放大在线上会被当成"数据库慢"排查很久。

按实测常量推算（4 worker × 150 ms ≈ 27 次/秒的哈希吞吐，即平均每 37 ms 释放一条连接），同形状的排队在 **800 个左右并发时会触到 SQLAlchemy 默认 30s 的 `pool_timeout`**，此后登录请求开始 500。这一段是算术推算、没有实测（要复现得压 800 个并发，测试机上跑不动），写出来是为了说明它不是"只会慢一点"。

**为什么会这样**：R6-02 的修法把「CPU 并发」收窄到 4，这本身是对的；但**收窄的是运算，没有收窄「拿着连接等运算」的请求数**。提交说明里那句「超出的请求在队列里等——登录是低频动作，排队比雪崩好」只在队列有界时成立，而这里的队列是无界的（每个排队请求都已经占用了连接与 HTTP 连接）。

**建议**：把闸门提到第一次查库之前。容量与哈希池对齐即可，不需要另外调参：

```python
_LOGIN_GATE = asyncio.Semaphore(_HASH_WORKERS)   # 或 workers + 1，给成功路径留一点余量

async def authenticate(self, username, password, *, client_ip=""):
    key = f"{username}|{client_ip}"
    wait = throttle.penalty(key)
    if wait:
        await asyncio.sleep(wait)

    async with _LOGIN_GATE:          # ← 排队在这里，此时还没有连接
        user = await self.users.get_by_username(username)
        encoded = user.password_hash if user is not None else await _dummy_hash()
        ok = await verify_password_async(password, encoded)
    ...
```

注意两点：闸门要包住「查库 + 验密」整段（只包验密没有意义，连接已经拿走了）；`throttle.penalty` 的 `sleep` 必须留在闸门外面，否则爆破者会用 5 秒的等待占满闸门。

**建议的用例**（我这次是用 `checkout` / `checkin` 事件统计出来的，可以直接改成断言）：

```python
@event.listens_for(engine.sync_engine, "checkout")
def _out(...): active.add(...)
```

断言「并发 40 个登录期间，连接池的 `checkedout()` 峰值不超过 `_HASH_WORKERS + 1`」。现在这个峰值是 `min(n, 15)`。

---

## 三、💭 R7-02 `GET /images` 在 `image:read:all` 之后没有分页

**位置**：`modules/image/service.py:25-33`、`modules/image/router.py:30-34`

R6-09 把数据范围打开了，但列表接口仍然是一次全表 `select`：

```python
stmt = select(ImageJob).order_by(ImageJob.created_at.desc())
if not actor.has(PERM_READ_ALL):
    stmt = stmt.where(ImageJob.owner_id == actor.id)
rows = list(await self.repo.list(stmt))     # 没有 limit / offset
```

`ImageJobListOut` 里已经有 `total` 字段，但没有分页参数——这个形状让人以为它是分页的。改之前只有「自己的历史」，量级可控；改之后 `editor`（`image:*`）一进生图历史页就是**全表扫描 + 全量序列化**，任务列表那边的 `page/pageSize`（`TaskListQuery`）是现成的参照。

**建议**：`list_images` 接收 `page` / `page_size`（默认 20，上限 100），与 `GET /tasks` 保持同一套入参命名；`total` 用 `count(*)` 而不是 `len(rows)`。这是一次**破坏性契约变更**（前端要传参），可以在三件套里一起生成。

---

## 四、💭 R7-03 资料字段没有「目标不高于自己」守卫

**位置**：`modules/identity/service.py:342-345`

实测：一个只有 `identity:read` + `identity:update` 的运营账号，可以改**超级管理员**的昵称：

```
运营改超管昵称: 200 OK
```

改昵称本身不是提权，`_guard_target_not_above` 现在只拦「停用 / 改角色 / 重置口令」这三类会改变权限的动作，这个取舍是对的。但同一段代码里 `email` 也在可写白名单内——**一旦将来接上「邮箱找回密码」或「邮箱收验证码」**，改邮箱就是接管账号的第一步，而这条路径现在是任何 `identity:update` 持有者都能走的。

眼下的处理有两种，都不必现在做：一是把 `email` 从「无需守卫」的字段里摘出来（改他人邮箱要过 `_guard_target_not_above`）；二是在接邮箱流程之前，先在文档里给这类字段加一条"待接找回密码时需重新评估"的注解。**不建议**什么都不写——这类"暂时无害"的写权限最容易在半年后变成漏洞。

---

## 五、💭 R7-04 前端把 `retry` 的返回值标成了 `VideoTask`

**位置**：`studio/src/entities/task/api/task.api.ts:17`

```ts
retry: (id: string) => api.post<VideoTask>(`/tasks/${id}/retry`),
```

后端已经改成 `TaskRetryOut`（`id / status / updatedAt`），前端泛型还停在 `VideoTask`。运行时**不会炸**——`useRetryTask` 的 `onSuccess` 只 `invalidateQueries` 加一个 toast，没有读返回值——所以这是"类型在说谎"，而不是一个待爆的 bug。

之所以还是列出来：这类漂移在 TypeScript 里是最难发现的一种，`tsc` 绿灯、运行时正常，直到某天有人顺手 `queryClient.setQueryData(taskKeys.detail(id), data)` 把一次重试的返回值塞进详情缓存，UI 上的标题、进度、封面会一起变空。按分工我不动前端，建议前端在迁移时把泛型改成 `TaskRetryResult`（`schema.ts` 里已经生成好了）。

---

## 六、💭 两条一致性与卫生

**R7-05 数据范围现在有三个答案**：`task`（`task:read:all`）、`image`（`image:read:all`，R6-09 刚对齐）、`asset`（仍只有 `Asset.owner_id == actor.id`，没有 `read:all`）。R6-09 的理由是「同一份工作台两套答案，用户只会以为是 bug」——那条理由对素材同样成立，但素材是个人上传的媒体，保持 owner-only 也说得通。**至少该在 `asset/permissions.py` 或 `EXTENDING` 里写一句"这是刻意的"**，否则下一个人会照着 image 的样子再提一个 `asset:read:all`，然后没人记得当初为什么不做。

**R7-06 线程池的生命周期**：`_HASH_POOL` 是 import 期创建的模块级 `ThreadPoolExecutor`，没有 `shutdown()` 钩子。进程退出时 Python 的 `threading._register_atexit` 会 join，正常部署无影响；但 `uvicorn --reload` 每次重载会留下一组线程（worker 数是 2~4，泄漏量小、不影响正确性）。如果在意，把 `shutdown` 挂到 `lifespan` 的关闭段即可。另外 `tests/test_password_offload.py` 直接 `from core.security import _HASH_WORKERS`——测试依赖私有常量，改实现时会一起红，属于可接受的耦合，提一句留个记号。

---

## 七、值得保留的做法（这轮新增）

- **XFF 的五条用例里，`test_client_supplied_prefix_is_skipped` 是真正的回归用例**：它测的不是"函数返回什么"，而是"上一轮被绕过的那个具体姿势现在不成立了"。上一轮 `test_net.py` 把错误行为写成断言（R6-01 的教训），这一轮把正确的边界一条条写成断言，两次的差别值得记下来。
- **「为什么不用 `asyncio.to_thread`」这句注释**：默认执行器是 `min(32, cpu+4)`，32 个并发 scrypt 会把 CPU 打满——这解释了"有界"的必要性，而不是只留下一个魔法数字。
- **`set_password` 的 async 化理由**（"签名不同不是风格问题，而是『它会等待』这件事必须写在类型上"）：这是把 R6-08 那种"收敛写入点"的要求，真正落到了类型签名上。
- **`_reject_null_active` 的报错文案**：不只说"不能为 null"，而是告诉调用方"停用请传 false，保持不动请不要传该字段"。一个 422 文案能省掉一次对接沟通。
- **`test_password_offload.py` 断言的是心跳滞后，不是请求耗时**：请求耗时受并发数和池容量影响，会随着实现细节飘；事件循环的停顿才是那个不变式。这个选型比用例本身更有价值。
- **`TaskRetryOut` + `to_retry_out`**：把"重试是一个动作，不是一次查询"落成了类型，顺带让 `task:retry:any` 与 `task:read:all` 彻底分开。
- **`cli seed` 的拒绝文案给了出路**（"确需写入请显式加 --force"）：硬拒加一条明确的后门，比"静默跳过"和"无条件允许"都好。

---

## 八、建议顺序

| # | 动作 | 预估 | 理由 |
|---|---|---|---|
| 1 | R7-01 闸门前置到第一次查库之前 + 「连接占用峰值」用例 | 25 min | 匿名登录现在能把所有要库的接口拖慢一个数量级，修法只有一个 `async with` |
| 2 | R7-02 `GET /images` 补分页（顺带改三件套） | 30 min | `read:all` 之后这是全表返回，前端一进页面就吃满 |
| 3 | R7-03 给 `email` 加一句「接找回密码前需重新评估」的注解 | 5 min | 现在无害，半年后是接管面 |
| 4 | R7-04 前端 retry 泛型（前端同学顺手） | 5 min | 类型在说谎，运行时不炸 |
| 5 | R7-05 / R7-06 一致性说明与线程池收尾 | 20 min | 不急 |

第 1 项要注意：闸门必须包住「查库 + 验密」，且 `throttle.penalty()` 的等待要留在闸门外——把 5 秒的惩罚性等待放进闸门，等于让爆破者用等待占满闸门，比现在更糟。

---

## 九、验证方式（可复现）

```bash
cd api

# 基线
./.venv/Scripts/python.exe -m pytest -q            # 180 passed
./.venv/Scripts/python.exe -m ruff check src tests # All checks passed
./.venv/Scripts/python.exe -m mypy src             # 110 files clean
./.venv/Scripts/python.exe -m cli export-openapi --check   # 三件套无变化
```

R6-01 的正确复现姿势（**必须是两段**：客户端伪造前缀 + 代理追加真实值）：

```python
# TRUSTED_PROXIES=127.0.0.1，TestClient(client=("127.0.0.1", 5555))
headers={"X-Forwarded-For": f"203.0.113.{i}, 198.51.100.7"}
# 现在：12 次 → [401×5, 429×7]，计数表 1 条键 admin|198.51.100.7
# 只有一段（"203.0.113.i"）时会被采信——那是"代理覆盖写"的语义，正确
```

R7-01 的连接占用用 SQLAlchemy 事件统计，峰值比用眼睛看日志可靠：

```python
@event.listens_for(engine.sync_engine, "checkout")
def _out(dbapi_conn, rec, proxy): opened[id(dbapi_conn)] = time.perf_counter()

@event.listens_for(engine.sync_engine, "checkin")
def _in(dbapi_conn, rec): spans.append([opened.pop(id(dbapi_conn)), time.perf_counter()])
# 12 个并发登录 → 12 段区间互相重叠，峰值 12
```

R7-01 的业务侧影响：并发 N 个登录（用户名各异 → 限流键各异，无延迟），20 ms 后发一次 `GET /tasks?pageSize=1`，记录延迟。n=15 起开始排队（117 ms），n=60 时 1649 ms。
