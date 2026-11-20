# 视频生成调用链评审 · 2026-11-19

> 审查对象：`POST /tasks` → 物化 → 上传 → 估价 → 提交 → 轮询 → 下载 → 落盘 → 入库。
> 与 11-18 那轮（`REVIEW_2026-11-18_VIDEO_CHAIN.md`）同一范围，本轮重读全部实现并**加入
> 运行时证据**（`/metrics`、`pg_stat_activity`、运行归档、任务行）。只读评审，除 §2 那一处已修外未改业务代码。

## 0. 一眼结论

| 项 | 结论 |
|---|---|
| 主链是否完整 | 完整。状态机、幂等键、计费纪律、失败分类都比 11-18 更严 |
| 阻塞级（P0） | 0 |
| 正确性 / 钱 / 数据（P1） | **5 条**：全部已修（含今日的 §2）。修复记录见 §9 |
| 健壮性（P2） | 7 条，其中 3 条已修（P2-3 / P2-4 / P2-6） |
| 清理（P3） | 4 条，其中 2 条已清理（P3-2 / P3-3） |
| 最要紧的一句 | **超时预算是分层的，但每一层的时间尺度互不匹配**：`60 秒` 的 job 超时包着 `300 秒` 的落盘、`1680 秒` 的提交占着只有 `2` 个的 worker 槽位。这类错误不会报错，只会让任务"莫名超时"或"成功但空白"。 |

严重度延续 11-18 的定义：**P0** 阻断上线 · **P1** 正确性 / 钱 / 数据一致性 · **P2** 健壮性 · **P3** 清理与文档。

## 1. 链路全貌与真实预算

```
POST /tasks ──► task.service.create（受理期校验：模型/参数/素材/文本快照/计费模式）
                    │  写 video_tasks(status=queued) + commit
                    ▼
              runner.dispatch ── idempotency_key=submit:video:<id> ──► [submit job]
                    ▼
              handle_submit
                 ├─ 三态守卫：有 external_id → 跳过；submit_unknown → 判失败待人核对
                 ├─ mark_submitting（先落「提交意图」，崩溃可识别）
                 └─ provider.submit（libtv）
                       ├─ materialize（幂等：run.json 已存在就不重复下载素材）
                       ├─ [upload]  桥接层传素材 + 估价 + 写 request.json + 绑定账号
                       └─ [submit]  free=vendor submit.mjs / credits=bridge.submitCredits
                    ▼  mark_submitted：写 external_id + **started_at = now（墙钟起点）**
              handle_poll（延迟投递，每跳 5 秒，同一幂等键续接）
                 ├─ 墙钟判定 _timed_out(started_at + 1800s)
                 ├─ 次数判定 polls >= 900
                 ├─ provider.poll → _poll_live（纯 HTTP，12 秒超时）→ progress
                 │     └─ 看到 generated → 投 [libtv.download]（独立 job，300 秒）
                 │            └─ 把 CDN 成片拉回共享盘 outputs/generated.mp4
                 │                 （下一跳 poll 才会看到 success）
                 └─ 看到 success：_persist（共享盘 → 对象存储）+ mark_succeeded + 入库 + 事件
```

注意 `poll` 与 `download` 是**并行的两条链**，不是串行：`download` 是独立 job，
`poll` 只负责发现 `generated` 并把下载"踢"出去，真正的完成信号来自下一跳看到 `success`。

### 1.1 时间尺度矩阵（本次评审的核心）

| 层次 | 数值 | 由谁决定 | 实测/结论 |
|---|---|---|---|
| 单跳轮询预算 | **48 秒**（`job.timeout_sec=60 × 0.8`） | `runner.handle_poll` | 只覆盖「问一次供应商 + 落库」 |
| poll job 硬超时 | **60 秒** | `runner._schedule_poll` | **外层，裁掉一切"预算外"的工作** |
| 落盘预算 | **300 秒** | `GENERATION_PERSIST_TIMEOUT_SEC` | 被上面的 60 秒压制（见 P1-2） |
| submit 硬超时 | **1680 秒**（驱动报 1650 + 30） | `LibTVProvider.submit_timeout_sec` | 真正在上传的那一个独占槽位，最长 25 分钟 |
| 墙钟 | **1800 秒**，排队可顺延，硬顶 10800 | `_timed_out` + `extend_deadline` | 从 `mark_submitted` 起算，**含本地排队** |
| 轮询次数 | 900 次 × 5 秒 = 4500 秒 | `GENERATION_MAX_POLLS` | 永远用不到，墙钟先到（见 P2-2） |
| worker 并发 | **2** | `WORKER_CONCURRENCY` | 与 submit 的 1680 秒相乘，是 P1-4 的根 |

## 2. 本次已修：轮询节奏在默认队列下完全失效

**现象**：任务提交成功、上游正常生成、进度已到 99，却在 **342~344 秒**被判「生成超时，请重试」，积分已扣。

**根因**：`QUEUE_DRIVER` 默认 `memory`（`core/config.py:216`），而 `MemoryJobQueue` 的**续接路径丢掉了延迟**：

```python
# 修前 src/infra/queue/memory.py
self._next[parent.id] = job          # delay 没有一起存
...
self._push(successor, delay=0)       # ack 时硬编码 0 → 下一跳立刻可领
```

生成轮询每一跳都用同一个幂等键排下一跳（走续接），于是每跳都立刻重排 → 变成忙循环：
单跳 0.378 秒，`GENERATION_MAX_POLLS=900` 在 **900 × 0.378 ≈ 340 秒**内烧完。

**证据**：

| 证据 | 数值 |
|---|---|
| `/metrics` `app_build_info{queue_driver}` | `memory` |
| `job_duration_seconds_count{name="generation.poll"}` | 4526 跳 / 累计 1710 秒 |
| 均值 | **0.378 秒/跳**（若 5 秒间隔生效，跑完这些要 6.3 小时，而进程只活了 83 分钟） |
| 两个失败任务耗时 | 342 秒、344 秒（≈ 900 × 0.378） |
| 同批成功任务 | 349 秒（赶在预算耗尽前出片） |
| 任务行 | `progress=99`、`finished_at - started_at = 344s`、`run.json` 停在 `submitted` 后再无更新 |

**修复**：`_next` 改存 `(job, 到期时刻)`，`ack` 按剩余时间入堆，与 db 驱动的 `next_job.run_at` 语义对齐；
新增 `tests/test_memory_queue.py::test_continuation_keeps_the_delay`（把修复回退后该用例失败，已验证）。

**为什么"反反复复"**：上限是 900 **次**而不是 30 **分钟**，能撑多久取决于单跳多快。
成片时间超过约 5.7 分钟就必挂；恰好更快的就成功——所以表现为"时好时坏"。

## 3. 已验证正常的部分（有证据，不是"读起来对"）

| 结论 | 证据 |
|---|---|
| 重试不会二次下单 | `submit()` 先读归档 `task_id`，有就直接返回；`_resolve_unknown` 未命中一律交人工 |
| 「提交结果未知」不会自动重提 | 三处守卫：runner 的 `submit_unknown` 分支、`resume_unfinished_tasks` 的 `submit_attempts>0` 分支、桥接层 `Refusing duplicate or uncertain submission` |
| 终态保护接住了返回值 | `mark_succeeded` / `mark_failed` 均条件更新，调用方检查 bool（`_settle`、`_archive`、`recovery` 都查了） |
| 排队不算进墙钟 | `GenerationProgress.queued` → `extend_deadline` + 3 小时硬顶，`test_generation_deadline.py` 覆盖 |
| 账号绑定不会被中途换掉 | `browser_for()` 先读 `run.json::browser_url` |
| 落盘不在 48 秒轮询预算内 | `test_persist_runs_outside_the_poll_budget`（AST 守卫，防位置回退）。注意它**仍在 60 秒 job 超时内**，见 P1-2 |
| 传入素材在受理期就校验 | `resolve_reference_images` + 删除保护，避免跑到上游才炸 |
| 计费模式按行冻结 | `service.create` 落 `billing_mode`，`retry_task` 不碰该列 |

## 4. P1（正确性 / 钱 / 数据）

### P1-1 ✅ 已修 · 轮询延迟在默认队列下丢失（见 §2）

### P1-2 ✅ 已修 · `300 秒` 的落盘预算被外层 `60 秒` 的 job 超时裁掉

**证据**：

```python
# src/generation/runner.py:1003-1011
job = Job(name=JOB_POLL, ..., max_attempts=1, timeout_sec=60.0)
# src/workers.py:243-247
asyncio.wait_for(handler(job), timeout=job.timeout_sec or DEFAULT_JOB_TIMEOUT_SEC)
```

`_persist` 与 `_finish_success`（入库、抽帧、发事件）确实被移出了 `asyncio.timeout(budget)`，
但**它们仍在 handler 函数体里**，而 handler 整体被 `wait_for(60)` 包着。
于是 `GENERATION_PERSIST_TIMEOUT_SEC=300` 一次都不会生效——**11-18 修的是内层，这次暴露的是外层**。

**后果链**：共享盘（`//Z4S-XI5Y`，SMB）读一个大视频 + 传 MinIO + ffmpeg 抽帧 > 60 秒
→ `wait_for` 取消 handler → `_persist` 的 `except CancelledError: raise` 继续冒泡
→ `wait_for` 转成 `TimeoutError` → `workers._handle` 走 `except TimeoutError` → `nack`
→ poll job `max_attempts=1` → **死信，且任务停在 running（进度 99）**
→ memory 队列没有巡检，也没有下一跳，**只能靠重启进程或人工点重试**。

而 metrics 显示历史 4526 跳全部 ≤ 5 秒，所以这是**潜在**问题（小文件 + 内网 S3 时不会触发），
一旦换成 100MB 以上的成片就会稳定复现。

**建议**：poll job 的 `timeout_sec` 改成「轮询预算 + 落盘预算 + 收尾余量」（如 48/0.8 + 300 + 120），
并把「单跳轮询预算」从 `job.timeout_sec × 0.8` 改成独立常量 `GENERATION_POLL_BUDGET_SEC`，
避免"一个 job 超时同时表达三件事"。补一条用例：让 `_persist` 睡 65 秒，断言 handler 不被腰斩。

### P1-3 ✅ 已修 · 落盘失败会退回一个**已不存在的路由**，任务显示成功但没片

`_persist` 的设计是"落盘失败退回供应商直链，不把成功改成失败"。这对返回公网 https 的供应商成立，
但 LibTV 回的是相对地址：

```python
# drivers/libtv.py:339
output_url=f"/api/v1/templates/replacement-runs/{run_id}/output"
```

**该路由在 `src` 里没有任何注册**（全仓只有 3 处引用：两个驱动在拼字符串，一个正则在做匹配）。
也就是说 `persist_output` 一旦失败（共享盘上文件不见了 / S3 抖动 / 超时）：

1. `_persist` 返回这个死链 → `mark_succeeded` 把死链写进 `output_url`；
2. 任务状态「已完成」，前端播放 404；
3. `_archive` 拿到非 http 地址 → 当成对象 key 去存储里找 → `GenerationSourceInvalid` → 草稿箱空。

**症状与 11-18 记录的「任务成功但页面空白」一模一样，但根因不同**（那条是 S3 换端点没迁对象）。
这两种死法现在没有任何区分手段。

**建议**：`persist_output` 抛错时按「原地址形态」分流——
`http(s)` 才允许退回；相对地址必须 `mark_failed("产物未落库：…")`，
或至少写 `output_url=""` + `error_message`，让页面显示「失败」而不是「已完成但打不开」。
补一条用例覆盖相对地址落盘失败。

### 原 P1-4 → 复核后降级为 **P2-7**（初稿判据有误，此处更正）

初稿写的是「submit 独占槽位 28 分钟 × 并发 2 → 批量提交必踩」。复核 `process.py` 后更正：

```python
# process.py:274-287  lease() 是**非阻塞**的
with lease(run / RUN_LOCK, ..., error=ExecutorBusy):          # 抢不到立即抛
    with lease(lock_path(account_lock_name(browser)), ..., error=ExecutorBusy):
```

抢不到账号锁的 submit 会**立刻**返回 `EXECUTOR_BUSY` → 驱动抛 `RateLimited`
→ 框架按 `GENERATION_THROTTLE_DELAYS`（10/20/30/60/90/120/180/240 秒）重投一条新 job。
那条 job 只需几十毫秒就结束，**不占槽位**。所以「批量提交时后面的 submit 排队 28 分钟」不成立。

**真实边界**：只有**正在上传的那一个** submit 占 1 个槽位（最长 25 分钟）。当前单账号（9412）
→ 最多 1 个 upload 在跑，另 1 个槽位仍然可用，已提交任务的 poll 不会被饿死。

只有下面三种情形才会退化（记为 P2-7，见 §5）：

1. `WORKER_CONCURRENCY=1`（1 个 upload 独占全部槽位，poll 要等 25 分钟）；
2. 双账号时代两个 upload 同时跑（2 槽全占）；
3. upload（25 分钟）与 `libtv.download`（300 秒）同时占满槽位。

即使发生，墙钟 1800 秒内也不至于第一跳就超时（最长等待 25 分钟略小于 1800 秒），
但轮询间隔会从 5 秒退化到分钟级，进度条看起来"卡住"。

**仍然建议拆 upload / submit**（理由改成可维护性而非紧急度）：upload 不花钱、可长跑；
submit 只是下单（几十秒），却背着 1500 秒的预算，一旦它超时被 nack，
`submit_attempts` 已经落库，会被判成「可能已计费」——预算给得太宽反而放大了这个误判窗口。

### P1-5 ✅ 已修（代码侧） · 三个实例共享一个库，但队列是本机的

`pg_stat_activity` 显示 `.24`（本机）、`.19`、`.144` 三个客户端连同一个 `yufan_studio`；
`.144:8080` 上有同款后端（openapi 99 条与本机完全一致）。

`QUEUE_DRIVER=memory` + 共享 PostgreSQL + 共享 runs 盘（`//Z4S-XI5Y/...`）+ **本机 CDP 9412** 这个组合是自相矛盾的：

- 任务只在**创建它的进程**里被消费，重启即丢；
- 但 `modules/task/worker.py::resume_unfinished_tasks` 启动时会按 DB 状态把所有 `queued/running`
  捞进**自己的**内存队列，其中 `external_id` 存在的走 `resume()` 续轮询；
- 别的机器上的 9412 没有 Chrome → `_poll_live` 拿不到 headers → 回退 `execute_stage("poll")` → 也失败
  → 反复 backoff 直到墙钟 → `mark_failed` 写库 → **真正在跑的任务被别人判死**。

这也解释了历史报错「执行器浏览器未连接（CDP 127.0.0.1:9226）」——9226 是代码默认值，说明那个实例连 `run.json` 都没读到。

**建议**：要么只保留一个实例，要么全体切 `QUEUE_DRIVER=db`（并让每个实例只能绑定本机可用的账号）。
另外应加一条启动守卫：`queue_driver=memory` 且检测到多实例（或 `app_env=prod`）时**拒启或告警**。

### P1-6 ✅ 已修 · video 侧没有巡检，轮询链一断就永久停在 running

`image` 模块有 `image.reconcile`（每 300 秒，`immediate=True`，分「提交没落库」与「轮询链断」两类形状捞），
**video 侧没有对应物**——`generation.recovery` 只在进程启动时跑一次，`modules/task` 也没有 `@periodic`。

于是任何一次链路断裂都会变成永久卡住：

| 断点 | 现状 |
|---|---|
| poll job 死信（P1-2 的超时 nack、EXECUTOR_BLOCKED 之后没人重投） | 任务停在 running，进度 99，前端一直转 |
| memory 队列重启丢 job | 只能等下次重启触发 `resume_unfinished_tasks` |
| 进程被 kill 在 poll 提交之后、ack 之前 | 同上 |

用户能做的只有"重启服务"或"点重试"，而重试还要先确认是否已计费（带 `external_id` 时默认拒绝）。
**这是把 P1-2 从"偶发"放大成"必须人工介入"的那一环。**

**建议**：照搬 `image.reconcile` 的形状，对 `video_tasks` 做两类扫描——
`queued/running 且 external_id 为空` → `runner.dispatch`；`running 且 external_id 非空`
且超过 N 个轮询周期没推进 → `runner.resume`。两条都是幂等的，不会重复下单。
实现上可直接复用 `generation.recovery.recover_kind("video")`，不必再写一套状态机。

## 5. P2（健壮性）

- **P2-1 墙钟包含「本地队列排队」**：`_timed_out` 只看 `started_at`，无法区分「上游在排队」（会顺延）
  和「本机没轮到」（不会顺延）。正常并发下不致命，`concurrency=1` 时会被放大（见 P2-7）。
- **P2-2 `GENERATION_MAX_POLLS=900` 是个用不到的数字**：900 × 5 秒 = 4500 秒 > 1800 秒墙钟，
  真正的上限永远是墙钟。配置守卫只校验 `budget >= timeout × 0.8`，所以两者长期不一致也不会被发现。
  排障时容易误判成"次数用尽"（本次就差点走错），建议两者取同一个来源，或在日志里写明是哪个先到。
- **P2-3 ✅ 已修 · 超时文案不带任何判据**：`生成超时，请重试` 同时用于「墙钟到」与「次数到」，
  且不含 `polls / elapsed / interval / 最后进度`。本次定位完全靠 `/metrics` 反推。
  建议文案与事件里带上这三个数（或至少写进结构化日志）。
- **P2-4 ✅ 已修 · `libtv.download` 的 `max_attempts=1` 与注释矛盾**：handler 注释写「交给队列重试」，
  实际一次失败就是死信。目前靠"下一跳 poll 再投一次"自愈（死信会释放幂等键），
  但语义是"每 5 秒重试一次直到成功"，与 `max_attempts=3` 的预期不符。建议显式改成 `max_attempts=3`。
- **P2-5 两套恢复逻辑并存**：`task.startup`（自带）与 `generation.recovery`（框架）口径不同
  （框架有 `_supports_poll` / `sync_orphan`，task 自己有 `submit_attempts` 分支）。
  task 声明了 `startup` 后框架会跳过 `kind=video`，新增供应商/新增 kind 时容易只改一边。
- **P2-6 队列驱动没有部署守卫**：`queue_driver` 默认 `memory`（`core/config.py:216`），
  `.env.example` 里也是 `memory`。生产多实例部署时这会造成 P1-5，但启动期没有任何提示。
- **P2-7 长短 job 共用槽位**：`WORKER_CONCURRENCY=2` 时，一个 upload（最长 25 分钟）
  会占掉一半槽位；`concurrency=1`、双账号同时上传、或 upload + download 叠加时，
  poll 会被压到分钟级（详见 §4 的「原 P1-4」）。当前不是紧急问题，但拆 upload/submit
  仍是正确方向。

## 6. P3（清理）

- **P3-1** `video_tasks` 的 `cancelled` 终态语义存在（`_TERMINAL`、`mark_failed` 的注释都提到"用户点了取消"），
  但模块里没有取消接口——这个分支实际不可达，读代码时容易误判有一条取消链路。
- **P3-2 ✅ 已清** `queued_jobs` 里还留着一条 11-18 的 `test.continuation.chain`（pending，`last_error` 为空），
  是 `test_db_queue` 的残留。切到 db 驱动后会被 worker 领走并因未知 handler 丢弃，建议清掉。
- **P3-3 ✅ 已补** `docs/EXTENDING.md` 新增「超时预算有三个层次」一节：**新增 kind 时，poll job 的 `timeout_sec` 必须覆盖
  该流水线 `persist_output` + 入库的最坏耗时**（P1-2 的教训）。
- **P3-4** `_poll_live` 没有"同任务并发去重"：同一 `task_id` 若被两条 poll 链同时查（重试后旧链未死），
  会对上游发两次请求。当前靠幂等键挡着，风险低。

## 7. 与 11-18 评审的衔接

| 11-18 的项 | 本轮状态 |
|---|---|
| P1-1 权益到期两条队列同时坏 | 代码已按建议改（空标签不抛错）。**11-30 之后的真实行为仍未验证**——这是唯一的外部依赖风险 |
| P1-2 排队算进墙钟 | 已修（`queued` → `extend_deadline`）。但**只覆盖"上游排队"，没覆盖"本地排队"** → 本轮 P2-1 / P2-7 |
| P2-1/P2-3 文案与画布回退 | 已修，回归未见异常 |
| P2-2 自动对账 | 已按"只正向接回"收窄，与当前代码一致 |
| 今天新发现 | §2（已修）、P1-2、P1-3、P1-5、P1-6、P2-2、P2-3、P2-4、P2-7 |

一句话：**11-18 修的是"上游侧"的时间语义，这一轮暴露的是"本机侧"的时间语义**——
预算、槽位、队列驱动三者从未放在一起对齐过。

## 8. 建议的修复顺序

1. **P1-3**（数据一致性：成功但没片，用户无法自救）——按地址形态分流，相对地址一律判失败。
2. **P1-2**（潜在但一旦触发就永久卡 running）——拆出独立的单跳预算常量 + 用例。
3. **P1-6**（把"卡住"从"必须重启"变成"自动恢复"）——照搬 `image.reconcile`，成本最低、收益立竿见影。
4. **P1-5**（部署）——收敛实例或切 db 驱动，并加启动守卫。
5. P2-2 → P2-3（先让排障能看清是哪个上限先到）→ P2-4 → P2-7 → P2-5 → P2-6。

前三条都在 `generation.runner` / `drivers/libtv.py` / 一个新增的 maintenance 文件里，
可以一起回归（`pytest tests/test_generation*.py tests/test_task_api.py`）。

前三条都在 `generation.runner` / `drivers/libtv.py`，可以一起回归（`pytest tests/test_generation*.py tests/test_task_api.py`）。

## 9. 修复记录（2026-11-19）

| 项 | 落地 | 验证 |
|---|---|---|
| §2 轮询延迟 | `infra/queue/memory.py`：`_next` 改存 `(job, 到期时刻)`，`ack` 按剩余时间入堆（对齐 db 驱动的 `next_job.run_at`） | `test_memory_queue.py::test_continuation_keeps_the_delay`（把修复回退后必失败） |
| P1-2 | `runner`：拆出 `_POLL_QUERY_BUDGET_SEC=48` / `_POLL_FINISH_MARGIN_SEC=120` / `_poll_job_timeout()`；poll job 硬超时 60 → 48+300+120=468 秒 | `test_generation.py::test_poll_job_timeout_covers_persist_and_finish` |
| P1-3 | `runner`：新增 `OutputNotPersisted` 与 `_is_public_url`；相对地址落盘失败先重试 3 次（1s/2s 退避挡对象存储抖动），仍失败则**判失败**并保留 `external_id`；公网直链维持「退回直链」 | `test_generation.py::test_persist_rejects_relative_url_that_cannot_be_stored`、`test_persist_falls_back_to_supplier_url_on_timeout` |
| P1-5 | `infra/driver_config.py::ensure_prod_queue_driver`：`app_env=prod` + `QUEUE_DRIVER=memory` 启动期拒启；dev 下打一条说明代价的 warning（`app.py`） | `test_config_guard.py::test_prod_rejects_the_in_memory_queue` |
| P1-6 | 新增 `modules/task/maintenance.py`：`task.reconcile` 每 300 秒扫描「900 秒没有任何写入」的 queued/running 任务，接回 `runner.resume` / `dispatch`；`resume_unfinished_tasks` 增加 `stale_after_sec` / `limit` | `test_resume.py::test_reconcile_only_picks_tasks_that_stopped_moving` |
| P2-3 | 两个超时分支都带判据：`生成超时，请重试（墙钟判据；已轮询 N 次、耗时 M 分钟、最后进度 X%）`，同时打 warning 日志（`polls` / `elapsed` / `budget` / `progress`） | `test_generation.py::test_poll_times_out_by_wall_clock` 断言文案含判据 |
| P2-4 | `drivers/libtv.py`：`libtv.download` 的 `max_attempts` 1 → 3 | 现有下载/轮询用例 |
| P2-6 | 与 P1-5 同一条守卫（队列驱动的部署守卫） | 同上 |
| P3-2 | 清掉 `queued_jobs` 里的 `test.continuation.chain` | `queued_jobs` 现为空表 |
| P3-3 | `docs/EXTENDING.md` 新增「超时预算有三个层次，别混用」与 `persist_output` 的地址约定 | 文档 |

回归：`pytest` **1575 passed / 14 skipped**（235 秒）；`ruff check src tests` 干净。

**尚未做的两件事**（都需要人工决定，不是代码能定的）：

1. **实例收敛**：`.19` / `.144` 上那两份实例是干什么的，要不要留？P1-5 的代码守卫只能挡住
   `app_env=prod`，当前是 `dev`，所以那两份实例仍会共用一个库。
2. **upload / submit 拆分**（P2-7）：不紧急，但它是"长任务占槽位"的唯一根治办法。

**生效条件**：`api/src` 下的改动要重启后端才起作用。当前内存队列里没有在途业务任务，
重启不会丢东西（`video_tasks` 里没有 running 记录）。
