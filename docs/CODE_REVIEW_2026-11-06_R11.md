# 后端代码评审 · 第十一轮（R11）

> 时间：2026-11-06 09:40
> 承接：`CODE_REVIEW_2026-11-05_R10.md`（后端 R10）、`CODE_REVIEW_2026-11-05_FE5.md`（前端 FE5）
> 范围：`studio` 之外全部后端代码；重点复核 11-05 19:00 之后的增量，以及新增扩展点是否被"接全"
> 方法：基线 → 增量比对 → 逐层读码 → 临时探针（真实 SQLite + 真实 runner/queue + 本地 HTTP 服务）实测 → 清理探针 → 全量回归

---

## 一、结论

### 增量规模

11-05 19:00 之后后端有 **77 个文件**变动：`git diff` 41 个已跟踪文件（+1502 / -266）、22 个新增源文件、2 个 migration，另有多份新架构文档。这是一次框架级重构，不是一个功能补丁：

| 新增 | 内容 |
|---|---|
| `infra/archive/` | 归档盘协议 + local 驱动 + 工厂（A2） |
| `api/ports.py`、`core/ports/` | 跨模块注入口目录与账号状态协议（A3 / B4） |
| `core/scheduler.py` | `@periodic` + 进程内单调度器（七维之"周期任务"） |
| `core/audit.py`、`db/audit*.py` | 操作留痕协议与独立会话落库 |
| `infra/driver_config.py` | 驱动私有配置的归属地（A1） |
| `infra/ai/agnes.py` | 同步生图供应商 |
| `modules/image/*` | 全新生图能力（pipeline / service / reconcile / seed） |
| `modules/template/replacement_*` | 替换模板工作台 |
| 2 个 migration | `c8d5e2f7a910`（image_jobs 状态列）、`e6b1c9a4d230`（audit_events） |

### 质量基线（全部实测）

| 项 | 结果 |
|---|---|
| `pytest -q` | **292 tests collected / 全通过**（R10 时为 204） |
| `ruff check .` | `All checks passed!` |
| `mypy src` | `Success: no issues found in 143 source files`（R10 为 110） |
| `app export-openapi --check` | **四份生成物全部无变化**（`openapi.json` / `API.md` / `schema.ts` / `permissions.ts`） |
| Alembic 版本链 | `844779d1 → b7f3a1c2 → 2e770625 → 9c1f4e2a → 4d2b8f6a → c8d5e2f7 → e6b1c9a4`，**线性无分叉** |
| CI | `.github/workflows/ci.yml` 前后端两个 job，后端含 pytest / ruff / `mypy --platform win32` |

### R10 逐条复核：真修好了

- **R10-01**（TS 生成器给泛型响应编造不存在的类型名）：`openapi_export.py` 新增 `_inline_data_type()`（`:264`）与叶子名存在性校验（`:279-294`），生成物现在能通过 `tsc`；`--check` 与契约测试字节比对都覆盖到第四份 `permissions.ts`。
- **R10-02 / R10-03**（停用不吊销会话、自助改密后僵尸会话）：收敛成 `_cut_access()` **唯一写点**（`service.py:1105-1120`），`deactivate_user` / `update_user` / `change_password` / `reset_password` 全部经它。这比"在两个调用点各补一句"更彻底，注释也把"两个写点都漏了"的根因写清楚了。
- **R10-04 / R10-05 / R10-06** 相关路径读码无明显回退。

### 本轮结果

**2 条 🔴 / 3 条 🟡 / 5 条 💭**。两条 🔴 是同一个根因的两面：**新加的 `image` 模块只接了框架一半的插槽**——让它"跑起来"的插槽（pipeline、dispatch、reconcile）都接了，让它"出事后能恢复"的插槽（startup/resume、retry）一个都没接。而框架恰好把这两类插槽做成了可选，于是全绿测试也发现不了。

值得单独表扬几处：`docs/EXTENSIBILITY_GAPS.md` 的自我复盘（把 13 条按四类归因、逐条写根因）、`driver_config` 让驱动参数有归属地且环境变量键名一个没改、`_cut_access` 的唯一写点、`test_scheduler.py` 用假 `now` 测 `run_once` 而不是 `sleep`、以及 migration 链没有分叉。这些都是"不靠人记得同步"的正确方向。

---

## 二、🔴 R11-01 `image` 没有启动恢复钩子：卡在 `running` 的生图任务无人接管

**位置**：`modules/image/__init__.py:12-19`（`ModuleSpec` 无 `startup`）、`modules/image/maintenance.py:50-72`（只捞 `queued`）、`generation/runner.py:111`（`resume()` 无 image 调用点）

### 现象

`app.py:58-60` 会遍历所有模块执行 `spec.startup`。实测三个模块的接线情况：

```
[probe1] startup hooks = {'image': False, 'task': True, 'template': True}
```

造一条"进程在提交成功后、落结果前被 kill"的记录（`status=running`、`external_id` 有值、`created_at` 两小时前），模拟重启后让 `image.reconcile` 跑一遍：

```
[probe1] reconcile dispatched = []                 ← 一条都没捞
[probe1] queue after reconcile = []                ← 没有任何 poll job 被排上
[probe1] orphan status after reconcile = running   ← 永远停在 running
[probe1] queue after explicit resume = [{'name': 'generation.poll',
           'key': 'poll:image:949483e6…', 'attempts': 0}]   ← 框架明明有现成手段
```

也就是说：**只要显式调一次 `runner.resume(KIND_IMAGE, id)` 就能救回来，但生产代码里没有任何地方会调它。**

### 为什么

`maintenance.reconcile_stale_jobs()` 的查询条件是 `ImageJob.status == STATUS_QUEUED`（`:58`），它只覆盖"建了记录却没进队列"。而 `ImageJobStore.mark_submitted()`（`pipeline.py:82-90`）把状态改成 `running` 并写入 `external_id`，此后到 `_settle()` 落结果之间是一个真实存在的窗口：

- **同步供应商（Agnes）**：窗口是 `submit()` 返回后到 `mark_succeeded()` 之间的几行代码，进程被 kill / OOM / 部署重启就命中；
- **异步供应商**：`echo` 的 IMAGE 能力是异步的（`submit` 返回任务 ID、`poll` 返回进度），此时 `running` 会**持续多个轮询周期**，重启后轮询链整个丢失——这才是常态而非巧合。框架的 docstring 专门说过"轮询预算是本次进程生命周期内的计数，重启会归零"，说明作者知道要恢复，只是新模块没接。

对照 `modules/task/worker.py:75-105`：它有一套完整恢复器，把未完成任务分成 `resubmit` / `resume_poll` / `unknown` 三类分别处置，还补发 `EVT_GENERATION_FAILED` 事件；`modules/template/replacement_worker.py:282` 也挂了 `startup=resume`。**三个模块里只有新加的 image 没接。**

### 影响

用户看到一条永远"生成中"的记录，日志里没有任何错误；`image.reconcile` 每 5 分钟跑一次也不会碰它（它是 `running`）。只能人工改库或重新提交一次、再花一份钱。

### 建议

1. 给 `modules/image/__init__.py` 加 `startup`，扫 `status=running AND external_id IS NOT NULL` 的记录调 `runner.resume()`。
2. **但不要照抄 task 的恢复器**：`AgnesProvider.poll()`（`agnes.py:139-147`）对任何调用都返回 `FAILED / "不支持轮询"`，`handle_poll` 会据此把记录判死。所以恢复前必须按 `model_id` 判断该供应商是否支持轮询，或者在 `mark_submitted` 时给同步供应商直接进 `_settle`、不留 `running` 中间态。这一条容易被漏，报告里单独点出来。
3. 更彻底的做法：把"按 kind 恢复未终态记录"上移到 `generation/recovery.py`，让 `ModuleSpec` 只需声明 `recoverable=True`；再加一条架构测试（见第八节）。

---

## 三、🔴 R11-02 "提交结果未知"在生图上是**永久死锁**，且 `reconcile` 会把在途任务判死

**位置**：`generation/runner.py:137`（terminal 早退）、`:143-161`（`submit_unknown` 分支）、`generation/spec.py:70-79`、`modules/image/router.py`（只有 2 个路由）、`modules/image/maintenance.py:41,67`

### 现象 A：文案承诺的"手动重试"不存在

`SUBMIT_UNKNOWN_MESSAGE`（`runner.py:57`）写着「请核对后**手动重试**」，`GenerationRecord.submit_unknown` 的 docstring 更明确：

> 出路是人工核对后点「重试」——业务侧的 retry 命令会复位提交意图标记（见 `modules/task/service.py::retry_task`）。

`task/service.py:139-166` 确实实现了：`status` 改回 `queued`、`external_id=None`、`submit_attempts=0`，然后重新 dispatch。**但 `image` 没有这个出口**。实测：

```
[probe2] image routes = ['/generations/image', '/images']     ← 没有 retry
[probe2] job dequeued = generation.submit
[probe2] after redispatch: status=failed attempts=1 error=提交结果未知：请核对后手动重试
[probe2] queue left = []
```

重新投递后 `handle_submit` 在 `if record.terminal: return`（`:137`）直接退出——状态不变、`submit_attempts` 仍是 1。**此后无论用户点多少次重试、进程重启多少次、`reconcile` 跑多少轮（`failed` 不在它的查询条件里），这条记录都再也动不了。** `ImageJobStore.clear_submit_attempt()` 方法写了，但在 image 模块里零调用方。

### 现象 B：`reconcile` 日志说"重新投递"，实际把记录判死

造一条真实中间态（`status=queued`、`submit_attempts=1`、两小时前创建——即"进程在供应商已受理但结果未落库之间崩过"），让 `reconcile` 完整跑一遍并消费它投出的 job：

```
[probe5] queue after reconcile = [{'name': 'generation.submit', 'key': 'submit:image:4676b51e…'}]
[probe5] final status=failed attempts=1 external=None error=提交结果未知：请求已发出但未确认供应商是
```

`maintenance.py:67` 打的是 `logger.warning("发现 %d 条生图任务停在 queued…重新投递")`，而实际结果是 `_fail()` 置为 `failed`。防重复计费本身是对的（宁可判死也不下第二份账单），但**日志和文案都在说另一件事**，而且判死之后紧接着就是现象 A 的死锁。

### 现象 C：误伤窗口是配置上就存在的，不是极端巧合

| 参数 | 值 | 位置 |
|---|---|---|
| `STALE_AFTER_SEC`（多久算卡住） | **90 秒** | `maintenance.py:41` |
| `AgnesSettings.timeout_sec`（同步生成允许的最长耗时） | **180 秒** | `agnes.py:80` |
| `reconcile` 周期 / `immediate` | 300 秒 / **True** | `maintenance.py:50` |

一次 Agnes 调用合法地跑到 100 秒，或 `WORKER_CONCURRENCY=2` 让任务在队列里多等一会儿，记录就可能"创建已过 90 秒、状态还是 `queued`、提交意图已落"。此时 `reconcile` 命中：

- `MemoryJobQueue` 在**出队时**就 `self._by_key.pop(idempotency_key)`（`infra/queue/memory.py:54`），幂等键只覆盖"排队期"，不覆盖"执行期"；
- 于是 `dispatch` 的 `submit:image:<id>` 去重失效，第二个 `generation.submit` job 入队；
- 第二个 worker 读到 `submit_unknown=True` → `_fail()` 判死并发出 `EVT_GENERATION_FAILED`；
- 第一个 worker 13 秒后拿到结果，`mark_submitted()`（`pipeline.py:84`）**无条件**把状态改回 `running`，再 `_settle()` 改成 `succeeded`——状态与事件顺序来回跳，通知渠道接上后用户会先收到"失败"再收到"成功"。

`maintenance.py` 的 docstring 写「即使上一轮的提交动作其实还在队列里，也只会被去重掉」，这句话只在 job **还没被 dequeue** 时成立。

### 建议

1. **补 `POST /images/{id}/retry`**（照 `task/service.py:139` 的形状）：限 `failed`、校验 owner 或加 `image:retry:any`、清 `submit_attempts` / `external_id` / `error_message` / `finished_at`、改回 `queued` 再 dispatch。这是文案已经承诺、用户唯一能自救的出口。
2. `STALE_AFTER_SEC` 应由模块的供应商超时推导（例如 `max(90, provider_timeout * 1.5)`），不要写死一个小于供应商超时上限的常量；或在 `mark_submitting` 时把 `started_at` 写上，巡检按"提交开始时间"而非"创建时间"判陈旧。
3. `reconcile` 的日志按实际分支分开打："重新投递 N 条 / 提交结果未知 M 条（置为待人工核对）"，别让运维按"重投"去理解一条被判死的记录。
4. `mark_submitted` / `_settle` 加终态保护：若记录已是 `failed` 且 `submit_attempts>0`，不要无条件覆盖回 `running`。

---

## 四、🟡 R11-03 成图落盘直连供应商返回的 URL：无 host 校验、跟随重定向、无大小上限

**位置**：`modules/image/pipeline.py:143-169`

```python
async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT_SEC, follow_redirects=True) as client:
    response = await client.get(url)
    response.raise_for_status()
suffix = Path(urlparse(url).path).suffix or ".png"
stored = await get_storage().put(key, response.content,
                                 response.headers.get("content-type", "image/png"))
```

`url` 完全来自 `AgnesProvider.submit()` 解析出的 `body["data"][0]["url"]`（`agnes.py:126`），也就是**外部响应**，而 `AGNES_BASE_URL` 本身是运维可配的环境变量。实测用本地 HTTP 服务冒充"供应商返回的图片地址"：

```
[probe4] paths hit = ['/secret.png', '/redirect', '/secret.png']   ← 302 也被跟随
[probe4] stored = {'key': 'images/probe-redirect.png',
                   'data': b'INTERNAL-ONLY-SECRET',
                   'content_type': 'image/png'}
```

服务端请求了 `127.0.0.1`，把内容当成图片存进了自己的存储，并且 `content-type` 由对方响应头决定。代码里没有 host 白名单、没有内网/环回地址拦截、没有 `max_bytes`、没有对重定向目标重新校验。`response.content` 一次性读入内存，60 秒超时只管时间不管体积。

**为什么值得修**：这类下载是典型的 SSRF 落点——即使初始 URL 看起来是供应商域名，一次 302 就能把请求带到元数据服务或内网；而供应商返回的 URL 不在本系统控制范围内（配置指向错误地址、上游被劫持、响应被篡改都能触发）。防御成本很低，收益是砍掉整个攻击面。

**建议**：按 `AGNES_BASE_URL` 的 host（或显式白名单）校验初始 URL 与每一跳；`follow_redirects=False` 后手动逐跳校验；改用 `client.stream()` + 累计字节上限（如 20 MB）并在超限时提前断开；`content-type` 用白名单（`image/png|jpeg|webp`）而不是信任响应头，最好再按文件魔数确认。`suffix` 也建议白名单化，避免把 `.php` 之类的路径后缀拼进对象 key。

---

## 五、🟡 R11-04 `reconcile` 的 `BATCH` 没进 SQL：先全表加载，再在 Python 里切 50 条

**位置**：`modules/image/maintenance.py:53-63`

```python
rows = (await session.scalars(
    select(ImageJob).where(ImageJob.status == STATUS_QUEUED)
           .order_by(ImageJob.created_at)          # 无 .limit()
)).all()
stale = [row for row in rows if _aware(row.created_at) < cutoff][:BATCH]
```

插 120 条陈旧 `queued` 后实测：

```
[probe3] dispatched count = 50 (BATCH=50)
[probe3] SQL has LIMIT = False | SELECT image_jobs.name, image_jobs.prompt, ... FROM image_jobs WHERE ... ORDER BY ...
```

注释说「一次最多补投这么多，防止供应商故障后一轮把队列灌爆」，但**防爆的是投递侧，不是读取侧**：供应商持续故障时 `queued` 会随每次用户提交线性堆积，调度器每 5 分钟把全表 `queued` 行全部实例化成 ORM 对象再丢掉多余的，这是最容易在事故中放大事故的写法。另外 `order_by(ImageJob.created_at)` 没有以主键收尾——R8-06 已经修过同一处形状（分页边界上顺序不定，sqlite 按 rowid 兜底、换 PG 才现形）。

**建议**：把 `created_at < cutoff` 和 `.limit(BATCH)` 一起下推到 SQL，`order_by(ImageJob.created_at, ImageJob.id)`；`_aware()` 只用于无法下推的场景（sqlite 读回 naive），查询条件里可直接用 UTC 的 `cutoff`。

---

## 六、🟡 R11-05 审计写入端没进启动自检：没接上时全部留痕静默丢失

**位置**：`core/audit.py:62-84`、`api/ports.py:77-100`、`tests/test_architecture.py:211,242`、`app.py:80-88`

`api/ports.py` 的文档写得很硬：「清单之外的注入口 = 不允许存在」，而 `app.py` 也确实为每个未注册的注入口打了启动日志。但机械守卫 `_API_DIR = SRC / "api"`（`test_architecture.py:211`）只扫 `src/api/*.py`，形状识别是"同时有 `Protocol` 子类与 `register_*` 函数"（`:220-232`）。`core/audit.py` 用的是 `AuditSink = Callable[...]` 类型别名 + `set_audit_sink()`，两项都不匹配，于是它既不在 `PORT_CATALOG`、也不在扫描范围、也不在启动日志里。实测：

```
[probe6] sink registered before = True
[probe6] sink registered now = False
[probe6] entries written = 0                       ← record() 静默 return，无异常、无日志
[probe6] port_status names = ['principal-resolver', 'account-status']   ← 看不到 audit-sink
```

`db/audit_sink.py` 靠 `app.load_extension_points()` 在 import 期注册。这层接线一旦被改动（或未来某个入口不经过 `create_app`，例如独立 CLI / worker 进程），`record()` 会**按设计**静默跳过，而所有守卫都不会红、日志里一句都没有——这正是 `app.py` 给其他注入口打"未注册"日志想避免的情况。

另外，`grep` 显示全后端 `audit.record()` 只有 **1 个调用点**（`modules/image/service.py:120`）。`db/audit.py` 的注释说这张表是「所有模块共有的需求」，但目前只有生图接了；用户管理、角色授权、素材删除、替换模板这些更该留痕的动作仍无记录。这不算 bug，但"框架级能力只接了一个消费者"值得在扩展文档里标成待办，否则下一个模块作者会以为留痕已经全局生效。

**建议**：把 `has_audit_sink` 纳入启动自检（可以直接扩 `PORT_CATALOG`，或加一条 `if settings.app_env == "prod" and not has_audit_sink(): raise`）；把架构守卫的扫描范围从 `src/api/` 扩到 `src/core/` 并放宽形状识别；补一条契约测试断言"每个声明了 pipeline 的 kind 至少有一条 `record()` 调用"。

---

## 七、💭 观察项

| 编号 | 观察 | 说明 |
|---|---|---|
| R11-06 | `get_agnes_settings()` 没有 `@lru_cache` | `driver_config.py:17-19` 的约定第 2 条要求"一个 `@lru_cache` 的 `get_xxx_settings()`"，`get_archive_settings()` 照做了，Agnes 每次 `submit` 都重新实例化 `AgnesSettings()` 并读一遍 `.env`。功能无误，但同一份约定两个实现 |
| R11-07 | `_validate_params()` 只认 `size` / `count` 两个键 | `image/service.py:31-49`。`param_schema` 是通用字典，新模型声明别的 enum / 数值范围不会被受理时校验——正是 `EXTENSIBILITY_GAPS.md` 里"模型能力声明未参与校验"那一类问题的缩小版复发。建议按 `type` 分派通用校验 |
| R11-08 | `generate()` 先 `commit()` 再 `audit.record()` 再 `dispatch()` | `service.py:117-128`。`dispatch` 抛异常时接口 500，但记录已落库为 `queued`、留痕也已写，用户会看到"失败了但列表里多一条"。靠 `reconcile` 兜底是可行的，但建议把 dispatch 失败显式转成"已受理，等待调度"而不是 500 |
| R11-09 | `Scheduler` 关闭时 `stop.set()` 与 `task.cancel()` 重复 | `app.py:97-101`。`set()` 已让 `serve()` 自然退出，`cancel()` 会在 handler 执行中途强杀它。文档要求 handler 幂等所以可接受，但 `reconcile` 这类"投递到一半"的任务被砍会留下半批已投、半批未投（下一轮能补，无害） |
| R11-10 | 驱动配置的 `.env` 是相对路径 | `driver_config.py:34` 与 `core/config.py` 一致，都依赖 cwd 在 `api/`。从仓库根启动 `uvicorn` 时驱动参数会静默回退默认值（`AGNES_API_KEY=""` 到运行期才报错）。建议像 `DB_URL` 一样在启动时校验并提示 cwd |

---

## 八、一个反复出现的形状：插槽造齐了，但"每个模块接全"没有守卫

`docs/EXTENSIBILITY_GAPS.md` 的七维对账做得很好，13 条里修了 10 条，剩下 B1 / C1 / C2 明确标了未修。但本轮两条 🔴 **一条都不在那 13 条里**——因为那份对账是「按维度数插槽是否造出来」，没有做「按模块 × 插槽」的矩阵：

| 框架插槽 | `task` | `template` | `image`（新） |
|---|---|---|---|
| `pipeline` / `dispatch` | ✅ | ✅ | ✅ |
| `ModuleSpec.startup` 恢复 | ✅ `worker.py:75` | ✅ `replacement_worker.py:282` | ❌ **R11-01** |
| `runner.resume()` 续轮询 | ✅ | ✅ | ❌ |
| `retry` 清 `submit_attempts` | ✅ `service.py:139` | 自有 `retry_count` 体系 | ❌ **R11-02** |
| `audit.record()` | 无 | 无 | ✅ 唯一一处 |
| 启动自检 `port_status()` | — | — | — |

规律和前几轮是同一个：**不变量只在"新功能能跑起来"的路径上接一次，"出事才用得上"的路径靠人记得接**。R5-03 / R6-04 / R8-01 / R10-02 / FE2-02 都是它的变体。区别是这次框架把恢复与重试做成了**可选**插槽（为了不给所有能力强加语义），于是 292 个用例、ruff、mypy、契约检查全部照常通过。

建议把这条矩阵变成机械守卫，而不是再写进文档：

1. 遍历 `generation.pipeline` 注册表，对每个 `kind` 断言对应 `ModuleSpec.startup is not None`，或在 `ModuleSpec` 上加显式的 `needs_recovery=False` 声明（让"我确实不需要"成为要写下来的决定）；
2. 对每个 `GenerationStore` 实现了 `submit_attempts` 的 kind，断言模块存在能清零该标记的 retry 入口（扫描路由或 CLI 命令），否则 `SUBMIT_UNKNOWN_MESSAGE` 里的"手动重试"就是空头支票；
3. 把这两条加进 `tests/test_architecture.py`——它在 CI 里跑，且已经承担了"漏登记就红"的角色。

---

## 九、建议顺序

1. **R11-02 的 retry 出口**（`POST /images/{id}/retry`，约 30 行 + 2 条用例）——文案已经向用户承诺、且是唯一自救路径；顺手把 `STALE_AFTER_SEC` 与供应商超时对齐、`reconcile` 日志分分支。
2. **R11-01 的 startup**（约 20 行 + 1 条"重启后 running 必须恢复"用例）——注意**同步供应商不能无脑 `resume`**，否则 `AgnesProvider.poll()` 会把记录判死，见第二节建议 2。
3. **R11-03 落盘加固**（host 白名单 + 禁/校验重定向 + 流式大小上限），安全项，改动集中在 `pipeline.py` 一个方法。
4. **R11-04 把 cutoff 与 LIMIT 下推到 SQL**，顺带补主键收尾排序。
5. **R11-05 审计写入端纳入启动自检**，并补第八节那两条矩阵架构测试——它们是防止 R12 再出现同一形状的真正解法。

---

## 附录：复现方式

所有探针写在 `api/tests/test___probe_r11.py`（临时文件，跑完已删除），用 `conftest.py` 的独立临时 SQLite、`MemoryJobQueue`、真实 `GenerationRunner`，以及一个 `ThreadingHTTPServer` 起的本地服务。`pytest -q -s tests/test___probe_r11.py` 输出：

```
[probe1] reconcile dispatched = []
[probe1] queue after reconcile = []
[probe1] queue after explicit resume = [{'name': 'generation.poll', 'key': 'poll:image:…', 'attempts': 0}]
[probe1] startup hooks = {'image': False, 'task': True, 'template': True}
[probe1] orphan status after reconcile = running

[probe2] image routes = ['/generations/image', '/images']
[probe2] after redispatch: status=failed attempts=1 error=提交结果未知：请核对后手动重试

[probe3] dispatched count = 50 (BATCH=50)
[probe3] SQL has LIMIT = False

[probe5] queue after reconcile = [{'name': 'generation.submit', 'key': 'submit:image:…'}]
[probe5] final status=failed attempts=1 external=None error=提交结果未知：请求已发出但未确认供应商是

[probe6] sink registered now = False / entries written = 0
[probe6] port_status names = ['principal-resolver', 'account-status']

[probe4] paths hit = ['/secret.png', '/redirect', '/secret.png']
[probe4] stored = {'key': 'images/probe-redirect.png',
                   'data': b'INTERNAL-ONLY-SECRET', 'content_type': 'image/png'}

6 passed
```

清理探针后全量回归：`pytest` 292 全通过、`ruff` All checks passed、`mypy` 143 files clean、`export-openapi --check` 四份生成物无变化。探针未改动任何生产代码。
