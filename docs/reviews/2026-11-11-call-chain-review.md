# 替换生成调用链评审

日期：2026-11-11（Asia/Singapore）。对象：当前工作区代码，包含已有未提交修改。此次只评审和运行隔离验证，不修改业务实现、不重启服务、不重新提交生产任务。

后续用户已授权修复，实施记录见文末。下列发现和行号记录的是评审时状态，不代表修复后的代码仍存在相同问题。

## 结论

发现 4 项 P1、4 项 P2。当前实现对常规单实例路径有较多保护，但用户确认、数据库队列的后继投递、跨进程回执恢复、并发终态写入仍存在缺口。不能仅凭现有测试通过认定整条调用链可靠。

## 实际调用路径

1. 前端 `useReplacementWorkbench` 生成请求标识，调用 `POST /replacement-runs`。后端校验素材、冻结配置、物化输入、编译提示词，写入 `replacement_runs` 及共享工作区，返回 `prepared`；前端打开核对框。
2. 用户确认后，`POST /replacement-runs/{id}/execute` 经 `ReplacementService.execute`、`worker.schedule_state` 决定准备、继续轮询或结束。
3. `template.replacement.prepare` 上传并校验素材。LibTV 通过监督 Python → Node bridge → 固定版适配器 → 本机已登录浏览器；百炼使用 Python HTTP 适配器。
4. `GenerationRunner.handle_submit` 写提交意图、调用对应 provider、登记上游任务号，排入轮询。LibTV 还存在文件回执到数据库之间的交接窗口。
5. `handle_poll` 通过延迟新作业继续查询；LibTV 生成后单独派发下载，百炼在 poll 内下载。成片搬入归档存储后登记 `output_key`，再完成业务状态。
6. 人工“继续”与启动恢复重新调度现有任务；确认重提用于没有任务号且提交结果未知的情况。

数据库与工作区不是单向关系：业务层会把整行状态投影到 `run.json`，执行器也更新该文件，部分路径再吸收其字段。现有 `docs/CHAIN_REPLACEMENT.md` 仍描述文件主导及旧文件名，已不能作为当前实现的准确依据。

## 发现（按优先级）

### 1. [P1] 启动恢复会执行用户尚未确认的任务

位置：`api/src/modules/template/worker.py:623`，启动扫描集合见第 57–61、724–742 行。

触发：用户只准备输入、预览提示词，关闭核对框，没有点击确认；随后任一启用 worker 的服务实例启动。`prepared` 被扫描进入恢复，`recover_state` 返回 `prepare`，后续上传并 dispatch 生成。百炼渠道会产生实际费用。

前端 `useReplacementWorkbench.ts:278` 的准备动作只打开核对框，`ReplacementReviewModal.tsx:40` 才确认生成；后端路由也明确声明准备接口“不提交生成”。因此这不是恢复已授权工作的正常行为。

验证：直接调用真实 `recover_state`、以 repository mock 提供 `prepared/task_id=None/submit_attempts=0`，实际返回 `prepare`。现有 `test_replacement_recovery.py:229` 将 `prepared` 列为应恢复状态，测试反而固化了此缺陷。

建议：将尚未确认的准备态排除出自动执行恢复；用持久化确认记录区分“可预览”与“用户已授权调度”，恢复只能推进后者。

### 2. [P1] 迟到的失败查询可以覆盖已经成功的任务

位置：`api/src/generation/runner.py:383`，底层无条件更新见 `api/src/modules/template/pipeline.py:86`。

触发：两个实例或重复恢复同时查询一条任务。A 在调用上游前读到 running，B 随后成功并落库；A 最后返回失败，仍直接 `mark_failed`。终态检查只在请求开始时进行，落库没有版本或状态条件，事件也无条件发布。

影响：数据库和投影从 success 回退到 failed，用户先收到成功再收到失败。共享数据库上的独立内存队列不能跨实例去重，执行器文件锁也不能保护整个“读库→查询→写库”事务。

隔离验证使用真实 runner、ReplacementStore、两个异步查询及内存 SQLite，只替换上游和投影：

```text
after_success= success success 100 None
after_stale_failure= failed failed 100 stale failure
events= ['generation.succeeded', 'generation.failed']
```

建议：为一次执行/恢复引入代次或版本，通过数据库条件更新保护终态与较新结果；只有成功更新状态的执行者才发结算事件。仅追加一次查询前检查不足以消除竞争。

### 3. [P1] 数据库队列会吞掉正在执行任务的下一轮调度

位置：`api/src/infra/queue/db.py:214`；轮询固定键见 `api/src/generation/runner.py:541`。

触发：`QUEUE_DRIVER=db`。poll handler 在当前作业 ack 前，以相同幂等键投递下一轮；数据库队列对 pending 和 running 都去重，因此返回当前运行作业的 ID，没有创建后继。handler 返回后 worker ack 删除该行，链路消失。

同一根因覆盖轮询、准备阶段执行器忙等待、下载重试及恢复锁退避。enqueue 没有抛异常，调度失败兜底不会触发。内存驱动领取时已经释放幂等键，现有测试因此看不到差异。

真实数据库队列 + 独立内存 SQLite 复现：

```text
successor_is_current_job= True
after_ack_size= 0
next_dequeue= None
```

当前本机配置是 memory，因此此项不是本次进程中断的直接原因，但切换持久队列后首轮未完成的轮询就会停摆。

建议：明确区分活动执行和待执行后继，设计可原子提交的“完成当前并安排下一轮”语义；不能只去掉去重约束，否则会引入另一种并发重复执行。

### 4. [P1] LibTV 的成功提交回执未参与恢复，已有任务仍可被放行重提

位置：`api/src/modules/template/worker.py:630`；结果未知判定第 617 行，确认重提第 318–327 行。

触发：上游已经受理，`records/submission.json` 甚至 `run.json` 已含任务号，但 API 在数据库 `mark_submitted` 完成之前退出。数据库仍是 `submit_attempts>0/task_id=None`。恢复只检查百炼回执，明确跳过 LibTV，于是有凭据的已提交任务仍被定格为 submission_unknown。

人工确认重提同样不检查 LibTV 回执，会清零次数；后续投影又用数据库的空 task_id 覆盖工作区，再次上传产生新 requestId，可产生第二笔费用。此处不是声称会自动重提，而是系统没有优先使用已持久化的成功凭据，错误地开放了收费重提路径。

隔离 mock 以当前真实函数验证：

```text
recover: action=done, status=submission_unknown, task_id=None, archive_reads=[]
reconfirm: allowed=True, status=queued, submit_attempts=0, archive_reads=[]
```

建议：继续、启动恢复和确认重提前统一核验各渠道成功回执；可信任务号存在时补齐数据库，只允许续查，禁止清除凭据后创建新任务。

### 5. [P2] 轮询作业超时耗尽后，任务留在 running 且没有后续查询

位置：`api/src/generation/runner.py:542`，取消兜底缺失见第 407 行，worker 超时处理见 `api/src/workers.py:248`。

poll 外层预算 60 秒、max_attempts=1，worker 超时取消 handler 后只 nack；runner 的 `except Exception` 捕获不到取消信号，无法安排下一轮，也没有落库说明需要恢复。百炼 poll 中查询与下载各自有 45 秒预算，合法的两个步骤合计就可能超过外层预算。

以真实 Worker、runner、store 缩放时间验证（60 秒缩为 0.02 秒，provider 为 0.2 秒）：

```text
timeout_attempt=1 queue_size=1
timeout_attempt=2 queue_size=0
after_timeout=running running 0 None
next_dequeue=None
```

内存队列实际额外尝试了一次，最终仍无后继；数据库队列则耗尽进入死信。若不重启或人工继续，页面会一直显示生成中。

建议：让超时/取消与业务状态恢复有明确交接；非计费查询可限次重新调度。百炼下载应独立成作业或统一计算完整预算。停机取消需与执行预算超时区分。

### 6. [P2] 业务投影原地截断 run.json，执行器可以读到半份状态

位置：`api/src/modules/template/workspace.py:92`；调用见 `worker.py:450`、`pipeline.py:98`。

该处直接 `write_text` 覆盖共享盘上的 run.json，没有临时文件原子替换，也不参与执行器租约。多个实例或投影与 Node 交错时，读取可能看到空文件/部分 JSON；全量旧投影也可能覆盖执行器较新字段。archive.base 已实现的原子 JSON 写与重试并没有覆盖这里。

影响是随机解析失败、状态回退及扩大提交回执丢失窗口。此项由代码路径确认，未在生产上注入文件竞争；不据此断言此前 fetch failed 就由它导致。

建议：统一 JSON 原子写入口，并让投影与执行器更新遵守同一互斥和版本协议。原子替换解决半份文件，版本/租约解决旧状态覆盖，两者都需要。

### 7. [P2] 新提交授权规则错误地阻断已付费任务的继续查询

位置：`api/src/modules/template/replacement_service.py:384`。

execute 对所有请求先检查输入和素材包是否仍可用，包括已存在 task_id、只需恢复查询的任务。已付费任务查询中断后，若运营停用素材包或删除原素材，用户点继续会先收到 400，无法进入已有任务的续查路径。后台 prepare_upload 第 430 行却明确先对已有 task_id 续查，前后规则不一致。

隔离 mock 验证：已有 `task_id='already-paid'` 时，授权错误导致 schedule 完全没有被调用。

建议：保留任务归属、账号访问及回收站规则；仅在尚未创建上游任务、将发生新提交时执行素材/素材包的新提交授权。已经付费的查询和取回结果不应依赖素材包继续上架。

### 8. [P2] 撤回公共原片共享后，最终授权仍允许新提交

位置：`api/src/modules/template/authorization.py:70`；准备时原片共享检查见 `replacement_service.py:651`。

别人共享原片→用户准备快照→主人撤回共享→用户确认执行。最终授权只检查输入是否存在、是否删除，没有复核 source.owner_id/shared，因而所有授权闸仍放行。准备阶段本身会拒绝别人的未共享原片，两阶段约束不一致；撤回共享接口也确实允许这一操作。

隔离 mock 验证：撤回共享的公共原片，authorization_gap 仍返回 None。

建议：明确共享撤回对尚未提交任务的语义，并在真正新提交前统一执行同一规则。此检查不应阻断已有上游任务的续查（见上一项）。

## 与这次生产报错的关系

查询中断对应 `56bd89bcc37047168a89546ac57d6a5d`，上游任务号 `20261111101403661947197`。其独立监督日志显示另一处部署 `C:\Users\PC\WorkBuddy\...` 在模块导入 Pillow 时 KeyboardInterrupt；Windows 返回 3221225786（0xC000013A，STATUS_CONTROL_C_EXIT）。日志不能证明具体是谁发送了中断信号，也不能据此认定生成失败。

本次异常发生在 libtv_process.main 的回执 finally 建立之前。当前启动方 Popen（libtv.py:162）没有设置独立进程组，不能保证服务控制台中断时监督进程继续存活。建议评估 Windows 控制台/服务生命周期隔离，并使用轻量监督入口尽早建立失败回执；不能把所有取消信号一律吞掉。

同一工作区 10:18:14 仍有上游排队回执，10:18:15 另一 poll 回执为 OK，而数据库记录的是 10:18:12 的查询中断失败。它证明停止的是本地查询，并说明多个查询可能交错；具体哪个部署调度了各次查询，现有回执缺少 host/instance/父进程关联，不能进一步确定。

建议在回执中补充实例标识、代码版本、父子 PID、operation ID、run ID、上游 task ID、执行代次；日志应保留底层连接 cause，但不得暴露登录凭证。前一次登录阶段 fetch failed 与本次 KeyboardInterrupt 是不同故障，不应混为同一根因。

## 验证和边界

- 后端第一组：LibTV 协议、执行器故障、计费、替换恢复/闸门/渠道、生成编排/恢复、数据库队列、输出与计时，共 162 项通过。
- 后端第二组：百炼 provider、替换渠道、管理与输入归档，共 65 项通过。两组包含重复的渠道测试，不能把 162+65 当成独立测试总数。
- 前端：ReplacementTemplates、PromptMediaView、templatePromptMedia、VideoTemplateLibrary 四个文件，共 79 项通过。
- 父评审者独立重跑了真实 runner/worker/store 的三项复现：并发成功覆盖、数据库后继丢失、超时后孤儿状态，输出与报告一致。
- 未运行真实新生成、未注入生产故障、未更改生产任务，未尝试取消上游。现有测试通过不能覆盖本报告新增的反例。
- 排除了“下载阶段临时覆盖 credits 就必然账单错误”的候选：下载后已有 submission 回执回填机制，不作为发现。

## 建议修复顺序及验收

1. 先阻断未确认任务的启动执行，并补“准备后重启不提交”的回归；恢复权限必须可追溯到用户确认。
2. 统一数据库原子状态迁移和执行代次，验证“成功后迟到失败不覆盖、不重复发事件”；同时设计数据库队列可靠后继投递，验证首次未完成后仍有下一轮。
3. 统一各渠道成功回执恢复，覆盖“上游已受理、DB 未提交”的中断点；已知任务号必须只续查，确认重提也不能擦掉它。
4. 修复 poll/download 预算与取消交接，原子化投影并统一写入租约；覆盖双实例、共享盘替换失败及进程导入期中断。
5. 分离新提交授权与已有任务续查，并定义共享撤回语义；最后更新链路文档，避免继续按旧文件架构排障。

## 修复实施记录（2026-11-11）

八项发现均已实施修复并加入回归测试，业务接口保持不变：

| 发现 | 修复 | 主要回归 |
|---|---|---|
| 1 未确认自动执行 | prepared 从启动恢复集合及推进分支移除，准备 handler 也拒绝自动推进 | test_replacement_recovery.py |
| 2 迟到结果覆盖 | state_version 条件更新 + 执行起点/task_id 校验，只有生效更新才发事件；同执行的元数据冲突有限重试 | test_replacement_poll_concurrency.py |
| 3 DB 后继丢失 | handler 上下文区分同键续接，持久 next_job，带 token 的 ack 原子提升；领取也比较旧状态和 token | test_db_queue.py |
| 4 LibTV 回执未恢复 | schedule/recover/reconfirm/execute 统一恢复可信成功回执；已知任务号不能确认重提 | test_replacement_recovery.py、test_replacement_authorization.py |
| 5 查询超时断链 | poll 外层 120 秒、内部 80% 预算，内部超时进入退避续查 | test_replacement_poll_concurrency.py |
| 6 状态投影不可靠 | 原子 JSON 写、非阻塞执行器租约、单调版本保护投影与吸收；拒绝擦除已有任务号或回退完成状态 | test_workspace_projection.py |
| 7 付费续查被授权阻断 | 先恢复回执，再仅对新提交做素材授权；保留 owner/回收站及计费锁 | test_replacement_authorization.py |
| 8 撤共享仍能提交 | 最终授权复核 source_video owner/shared，包内私有参考图不受误伤 | test_replacement_authorization.py |

交叉评审还补齐了同键后继领取时返回旧载荷、提交回执落库后刷新误认领新执行、元数据并发更新丢轮询、已进入提交后仍可切换计费模式的回归。Windows supervisor 使用独立进程组，降低控制台 Ctrl+C 广播影响；没有吞终止信号或改变守卫逻辑。

新增迁移 f7a9b2c4d610（queued_jobs.next_job）及 a9c2e5f8b310（replacement_runs.state_version）。迁移测试验证旧有已付费任务号和排队作业保持不变。上线顺序为停止旧 worker、备份/迁移数据库、启动所有新版实例；旧新版不能混跑。

当前实施没有重启服务、迁移生产库、重提或取消任何生产任务。最新架构、恢复和部署说明已更新到 ../CHAIN_REPLACEMENT.md。

## 复核与补修（2026-11-11 第二轮）

对八项修复逐条复核：机制与回归都在，结论仍然成立。但引进 `state_version` 作为 ORM
`version_id_col` 之后，"并发写入落败" 这条路径换了一种形态出现 —— 它不再覆盖数据，
而是**变成一个 500**。这是本轮补修的主线。

| 补修 | 原因 | 位置 | 回归 |
|---|---|---|---|
| 乐观锁落败映射 409 | `StaleDataError` 继承 `SQLAlchemyError`，落进通用处理器变成 500「数据库操作失败」+ `logger.exception`：一次「点删除时轮询正在写进度」被报成服务端故障 | `api/src/api/errors.py` | test_replacement_concurrent_writes.py |
| 回收站写入重试一次 | 该列是「用户随手一点」的写，轮询每 2 秒改同一行；重读一次几乎总能成功，不该让用户先失败再手动重来 | `replacement_service.set_deleted` | 同上 |
| 投影失败不否决已提交的状态 | 提交成功后投影只吞 `OSError`，`run_path()` 的 `NotFound`（旧行无指针）与归档根 `UpstreamError` 仍会冒泡，把一次已落库的成功/失败顶成异常 | `pipeline.ReplacementStore._update` | 同上 |
| 投影写不进去按「忙」处理 | `lease()` 的 `mkdir` 在 try 之外，共享盘不可达时抛 `OSError`；`_sync_out` 只接 `Conflict`，会把一条未提交任务记成 `service_error` | `worker._sync_out` | 同上 |
| 补列写入容忍撞车 | 输出键、真实扣费都是「补一列」，却正好发生在轮询推进度的时刻；撞车让下载作业进死信 → 有片子没地址、账单永远待核对 | `worker._update_column`（`publish_output` / `sync_credit_charge`） | 同上 |
| 重试计数不因撞车断链 | 计数只是防无限重试的保险；写不进去最多多跑一轮，而异常冒到队列会让任务直接死信 | `worker._bump_retry` | 同上 |

补修后按同一口径重跑：后端 `1295 passed, 12 skipped`，`ruff check src tests` 干净。
没有再改业务接口、没有重启服务、没有迁移或重提生产任务。

一处**已知残留**（本轮不改，理由写在这里）：`prepare_upload` 在 upload 之后整行回写，
理论上仍可能与另一实例的元数据写撞车并让这个作业退出队列。实际不可达 —— 期间唯一的
并发写入口 `execute`/`set_billing_mode` 都要先抢执行器租约，而 upload 全程持锁（会得到
409 而不是写库）；回收站写入已加重试。真要收敛，方向是让 worker 侧也只做列级 CAS 写入，
而不是每次 `repo.save` 整行。

