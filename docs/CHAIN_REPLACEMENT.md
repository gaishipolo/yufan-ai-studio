# 替换生成：从用户确认到成片的调用链

更新：2026-11-11。适用于人物、产品、场景和文字换场景；LibTV 与百炼共享业务编排，供应商适配不同。

## 状态与事实来源

- replacement_runs 数据库行决定业务状态、归属、上游任务号和计费模式。
- state_version 是数据库单调版本。ORM 更新自动比较并递增版本；生成编排使用数据库条件更新，同时验证执行起点和上游任务号。迟到结果不能覆盖终态或人工恢复后的新执行。
- run.json 是给文件执行器的投影，执行器也会更新它。业务层在明确的回执吸收点读回结果；因此不能把它当作与数据库无关的独立状态机。
- records/submission.json（LibTV）与 records/bailian-submission.json（百炼）是成功提交的恢复凭据。上游已受理但数据库尚未登记时，优先恢复任务号，不能当成无回执重提。
- 输出归档的 object key 存在 output_key；URL 由取件接口按当前存储驱动生成。

## 调用顺序

| 步骤 | 入口与实现 | 关键约定 |
|---|---|---|
| 1 准备与预览 | useReplacementWorkbench → replacement.api → POST /templates/replacement-runs → ReplacementService.prepare | 校验原片/素材包/渠道，冻结配置，归档输入，编译提示词；只返回 prepared |
| 2 用户确认 | ReplacementReviewModal → POST /replacement-runs/{id}/execute | owner/回收站检查，恢复已知回执；无上游任务号才校验新提交素材授权 |
| 3 调度 | worker.schedule_state / schedule | 有任务号只 poll；未知提交禁止自动重提；其余已确认任务进入 prepare 队列 |
| 4 上传准备 | template.replacement.prepare → worker.prepare_upload | 检查发起人和素材授权，上传/审核/估价；LibTV 经独立执行器，百炼经 HTTP provider |
| 5 提交 | GenerationRunner.handle_submit → ReplacementStore → provider.submit | 先持久化提交意图，条件更新抢占提交；已有任务号不重提 |
| 6 轮询 | generation.poll → GenerationRunner.handle_poll → provider.poll | 短作业与延迟后继；查询错误退避，超时前交接下一轮 |
| 7 下载 | LibTV: libtv.download；百炼: provider.poll 内下载 | 下载为非计费工作；LibTV 下载后从提交回执回填扣费 |
| 8 归档与结束 | worker.publish_output → ReplacementStore.mark_succeeded | 归档输出，条件更新终态，只有实际生效的更新才发布成功/失败事件 |

准备接口的 request_id 是创建档案的幂等边界，不代表用户已同意生成。仅处于 prepared 的任务不会因服务启动而自动执行；用户仍需确认。

## LibTV 执行器

    API/worker
      → infra.ai.libtv.execute_stage
      → Python supervisor（infra.ai.libtv_process）
      → Node bridge.mjs
      → 固定版本 vendor adapters
      → 本机浏览器 CDP 登录状态
      → LibTV API

浏览器端点默认 http://127.0.0.1:9226。上传、提交、查询、下载分段执行。浏览器连接失败与登录凭证缺失不是同一根因，排查时同时查看 executor-error.json 的 stage/detail。

登录阶段（适配器 stage=login）的两种失败在业务层已经分开，不再共用一个码：

| 执行器回执 | 业务码 / 状态 | 用户要做的事 |
|---|---|---|
| `detail` 含 `fetch failed` / `CDP discovery` / `CDP connection` | `EXECUTOR_BROWSER_UNAVAILABLE` + `service_error` | 在**自己的终端**运行 `start-services.ps1 -WithBrowser` 拉起浏览器，再点继续 |
| `detail` 含 `No LibTV login` | `EXECUTOR_LOGIN_REQUIRED` + `needs_login` | 在那个浏览器里重新登录 LibTV，再点继续 |

判断「浏览器在不在」最快的一条：`curl --noproxy '*' http://127.0.0.1:9226/json/version`。
注意 `--noproxy`：本机若开着 HTTP 代理，普通 curl 会得到代理的 502 而不是真实的连接拒绝，
看起来像「端口活着但返回错误」。

浏览器与后端都**不要从 AI 会话里启动**：会话结束会连同子进程一起回收，正在生成的任务
会停在 `running`（已扣费）。`start-services.ps1` 的头部注释就是为此写的。

监督进程持 records/executor.lock 的 OS 租约，直到 Node 真正结束。Node 另有 worker.lock；锁文件存在不等于仍被持有，不可凭文件存在与否直接删除。上传与提交还受账号级执行器租约约束。

Windows 监督进程使用独立进程组，降低服务控制台 Ctrl+C 广播导致导入期退出的风险。系统强制终止仍可能阻止 finally 和回执写入，不能承诺任意终止都有回执；也不会吞掉终止信号或删除守卫。

## 工作区投影

工作区路径由 catalog/runs/{id}.json 指针定位，REPLACEMENT_RUNS_ROOT 决定真实目录。典型内容：

    <run_id>/
      inputs/                  原片、参考图、提示词、请求体及输入清单
      records/                 上传/报价/提交/查询回执、执行器状态与租约
      logs/                    supervisor-operation-*.log、executor-stderr.log
      outputs/generated.mp4    原始成片
      review/                  兼容归档资料
      code/                    编译器及执行器快照
      run.json                 执行器投影

workspace.write_state 统一使用原子 JSON 写：先写临时文件再替换，短暂 SMB 冲突有界重试。投影在与监督进程相同的 executor.lock 内非阻塞执行，忙时跳过或退避，不能等待后写旧快照。

投影携带 state_version，旧版本不能覆盖新文件；不能擦除已受理的 task_id，不能把本地 generated/success 回退成旧 running。回执吸收也拒绝比数据库更旧的版本，历史无版本文件保留兼容。

## 队列续接

memory 驱动在 dequeue 时释放待执行幂等键。db 驱动保留 running 行和租约，因此不能把 handler 的下一轮 enqueue 当成普通外部重复请求。

worker 在 handler task 创建前进入 job_execution 上下文。db enqueue 对当前 handler 的同键后继，将载荷、delay、超时、attempts、trace 等写入当前行的 next_job。ack 在凭据匹配时原子提升后继为 pending；其他外部重复 enqueue 仍只命中当前活动行。

nack 或崩溃接管丢弃失败执行留下的暂存后继，由重试的 handler 重新决定；旧租约持有者不能安排后继或删除新持有者的作业。领取时还比较读取到的状态和 token，防止 ack 刚提升后继却返回旧 payload。

轮询作业外层预算为 120 秒，内部使用其 80% 预算，给事务收尾和后继调度留下时间。百炼的一次查询与下载各最多 45 秒，完整正常路径可容纳于预算内。内部执行预算超时进入普通退避续查，不会仅耗尽队列重试而永久留下 running。

## 恢复与重提

| 状态/凭据 | 行为 |
|---|---|
| prepared | 保持待确认，启动不执行 |
| queued/uploading/uploaded/waiting_executor 且无未知提交 | 恢复已授权准备 |
| 有 task_id | 只续查，不再上传创建任务 |
| 数据库无 task_id，但可信成功回执存在 | 补数据库任务号后续查；确认重提也必须拒绝 |
| 无 task_id 且 submit_attempts > 0 / submission_unknown | 不自动重提，先核对上游 |
| success | 不重新执行 |
| failed/service_error 且已有 task_id | 人工继续可重开查询预算 |

继续查询保留 owner、身份、回收站访问控制，但不因素材包下架或原素材删除而阻断已付费任务取回结果。新提交仍检查素材有效性，以及他人原片是否继续共享。

计费模式只在未提交阶段可变，进入提交意图或已有上游任务号后不能切换到另一队列。模式变更和成功回执复核位于同一非阻塞执行器租约，避免受理中的任务被旧快照改变模式。

## 部署与排障

本次增加两项迁移：f7a9b2c4d610（queued_jobs.next_job）和 a9c2e5f8b310（replacement_runs.state_version）。需更新所有访问该数据库的部署：

1. 停止旧 worker，等待/核对仍存活的监督进程，保留现有任务与回执。
2. 备份数据库，并在 api 目录执行 alembic upgrade head。
3. 启动新版 API/worker。旧版与新版 worker 不应混跑：旧 ack 不认识 next_job，旧状态写也不参与版本控制。
4. 对已经提交的失败任务使用原档案继续查询；不新建生成任务代替续查。

排障时串联 run ID、operation ID、上游 task ID、部署路径、日志时间和执行器 PID。共享数据库/工作区并不意味着所有机器共享同一个内存队列；应核对所有启用 worker 的实例。

修复范围与反例见 reviews/2026-11-11-call-chain-review.md；实现与验证记录见 superpowers/plans/2026-11-11-call-chain-reliability.md。
