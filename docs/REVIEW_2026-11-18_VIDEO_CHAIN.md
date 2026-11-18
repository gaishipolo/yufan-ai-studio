# 视频生成调用链评审 · 2026-11-18（重点：LibTV 免费队列 / 积分队列）

> 审查对象：`POST /tasks` → 物化 → 上传 → 估价 → 提交 → 轮询 → 下载 → 入库 这条链，
> 以及它在 LibTV 上的两个分叉（`billingMode=free` 与 `billingMode=credits`）。
> 方法：逐段读码 + 用真实账号/真实任务验证（证据见 §3）。只读评审，未改动业务代码。

## 0. 一眼结论

| 项 | 结论 |
|---|---|
| 主链是否完整 | **完整**：状态机、幂等键、账号绑定、失败分类都对得上，两条队列今天都跑通过 |
| 阻塞级问题 | **P1 ×0**（两条已在 2026-11-18 修掉，见 §5） |
| 次要问题 | P2 ×0 · P3 ×3 |
| 今天已修 | `project_id` 写死导致 30001（见 §4） |

严重度：**P0** 阻断上线 · **P1** 正确性 / 钱 / 数据一致性 · **P2** 健壮性 · **P3** 清理与文档。

## 1. 链路全貌

```
POST /tasks                      task/service.create
  ├─ resolve_billing_mode(spec)   ← 模型 schema 决定可不可以传；按行冻结
  ├─ 参考素材受理期校验            ← 存在/可读/类型，避免跑到上游才炸
  └─ TaskStore.load → GenerationRecord
        ↓ runner.dispatch  (idempotency_key = submit:video:<id>)
handle_submit
  ├─ 三态守卫：有 external_id → 跳过；submit_unknown → 判失败待人核对
  ├─ mark_submitting（先落「提交意图」）
  └─ provider.submit → drivers/libtv.py
        ├─ materialize（幂等：run.json 已存在就不再下载素材）
        ├─ 没有 inputs/request.json → execute_stage('upload')
        │     · 桥接层上传素材、估价、写 request.json、**绑定账号**到 run.json
        └─ execute_stage('submit')
              ├─ free：vendor submit.mjs（校验 infiniteSwitch===0、报价必须为 0）
              └─ credits：bridge submitCredits（calculator→verification→create）
        ↓ mark_submitted(external_id=`<run>:<上游 task_id>`)
poll（2 秒一跳，预算 = min(墙钟 1800s, max_polls×interval)）
  └─ provider.poll → 读 status.json → SUCCEEDED 时 enqueue libtv.download
        ↓ _persist：从运行工作区取 outputs/generated.mp4 → 对象存储 videos/<task>.mp4
mark_succeeded（封面、入库、downloadable）
```

## 2. 两条队列的分叉点

| 环节 | `free`（0 积分畅用） | `credits`（积分加速） |
|---|---|---|
| 选账号 | `LIBTV_BROWSER_URLS` 里第一个**在线**的（9411 优先） | 只认 `LIBTV_CREDITS_BROWSER_URL`（9412），**不降级** |
| 请求体 | `params.infiniteSwitch=0` | `infiniteSwitch=1` + 顶层/params 双份 `disableBenefitTags` |
| 估价 | 必须为 **0**，否则 `waiting_free` | 必须 **>0**，否则 `service_error` |
| 提交实现 | vendor `submit.mjs`（硬校验 count=1 / mixed2video / 720P） | `bridge.submitCredits`（同样校验，门禁换成积分队列） |
| 失败语义 | 报价非零 → 任务失败「当前报价非零，等待免费」 | 无有效权益标签 → 直接抛错 |
| 账号锁 | `account-executor-<host>-<port>`，两账号互不阻塞 | 同左 |

**两条路都要求「当前有生效的 0 积分权益标签」**——这是下面 P1-1 的根。

## 3. 已验证正常的部分（不是"读起来对"，是跑过）

| 结论 | 证据 |
|---|---|
| 积分队列能跑通全链 | 任务 7414901a → 上游 `20261118101503751524489` → 扣 160 积分 → 成片入库 `老大-2026-11-18-02-19-52.mp4` |
| 免费队列能跑通全链 | 任务 29dc66ae → 免费队列排队 29 分钟后出片，入库并抽封面 |
| `billingMode` 按行冻结、重试不换队列 | `service.create` 里 `billing_mode=resolve_billing_mode(...)`，`retry_task` 不碰该列 |
| 账号绑定不会被中途换掉 | `browser_for()` 先读 `run.json::browser_url`，只有没绑定时才按计费模式选 |
| 重试不会二次下单 | `submit()` 第一件事是看归档里的 `task_id`，有就直接返回它 |
| 提交/估价全程只用 1 个账号锁 | `ACCOUNT_STAGES = {upload, submit}`，poll/download 不进锁 |
| 失败原因能落到任务详情 | 今天补的 `detail` 透传（`RELEASE_INVALID`、`30001` 都能看到原话） |

## 4. 今天已修的一条（记录原因，避免再犯）

`bridge.mjs` 把 `metadata.project_id` 写死成 11 月初抓包时的画布项目。用户在画布里
切项目后，`create` 一律返回 **30001「账号状态已切换」**，而 `catch {}` 把原因吞了，
表现成「回执未知」。现改为提交前按浏览器**当前画布**对齐 `project_id`，并把上游
错误原文带出来。

## 5. P1（已修 · 2026-11-18；保留判断理由）

### P1-1 ✅ 已修 · 2026-11-30 之后，**两条队列同时失效**

权益接口现在给的是 `endTime: 2026-11-30T09:26:28`（7 条权益同一时刻到期）。到期后：

- **积分队列**：`billing.mjs::accountCreditTags()` 找不到有效标签 → 抛
  `No active zero-power benefit tag; credit queue needs manual review`；
  `applyBilling()` 再抛 `Credit queue requires active zero-power benefit tags`；
  桥接层 `submitCredits` 还有第三道同款校验。→ **提交 100% 失败**，且文案让人无从下手。
- **免费队列**：报价从 0 变成 80 → `waiting_free` → 驱动抛
  「当前报价非零，等待免费；未提交生成」→ 任务失败。

也就是说：**到期当天，默认队列（free）和付费队列（credits）都提交不了**，而报错里没有一个字
提示「去充积分 / 换队列」。

建议（按风险从低到高）：
1. `accountCreditTags()` 允许空标签：无有效收益时**不传** `disableBenefitTags`，
   `infiniteSwitch` 仍置 1，先按「正常积分队列」提交；只有上游明确拒绝时才失败。
2. `waiting_free` 的报错补一句下一步：「当前无 0 积分权益，报价 80 积分；可用积分队列提交，
   或等权益恢复」，并把 `estimated_credits` 带上。
3. 权益到期前给运营一条提醒（任务中心额度条已经有 `endTime`，只差一个阈值判断）。

### P1-2 ✅ 已修 · 排队时间被算进 30 分钟墙钟

`_timed_out()` 用 `started_at`（提交那一刻）算 `generation_timeout_sec`（默认 1800 秒，
**包含上游排队**）。免费队列是 `UNLIMITED_SERIAL` 串行，画布 10 个名额、本机经常排 20+ 分钟；
`RATE_LIMIT` 更不可控。昨天的 29dc66ae 就是这样被判「生成超时」——上游其实还在正常排队。

建议：poll 拿到 `queueReason` 非空时把墙钟起点顺延（`store.extend_deadline`），
另设硬上限（如 `created_at + 3h`）兜底，避免"永远排队"变成"永远不失败"。

## 6. P2（健壮性）

- **P2-1 ✅ 已修 · 免费队列失败文案没有出口**：`waiting_free` 直接判失败，不给「改用积分队列」的指引，
  也不带估价。默认队列就是它，踩中概率最高。
- **P2-2 ✅ 已修 · 提交失败后不复盘**：现在有 `app libtv-reconcile --auto`，但**要人手动跑**。
  建议在驱动里做成一步：提交失败 → 自动对账（消费明细命中就接回，没有就解封）→ 最多自动重排一次。
  注意这会在失败时自动花钱，需要产品确认。
- **P2-3 ✅ 已修 · 当前画布取不到时会退回旧项目**：`alignCanvasProject()` 在浏览器里没有画布标签页时
  返回 null，upload 阶段会回退到写死的旧 `project_id` → 又变成 30001。
  建议：取不到就**明确失败**并提示「请在该账号浏览器里打开画布」，别用旧值蒙。

## 7. P3（清理）

- **P3-1** `requestId` 在 upload 阶段生成：只重跑 submit 会复用（幂等，安全），
  重跑 upload 则会换新 → 若上游其实已建任务，就变成第二笔。当前 upload 仅在
  `request.json` 缺失时触发，风险低，但应写进 `EXTENDING.md` 的约束里。
- **P3-2** 两条队列的措辞不统一（free 说「等待免费」、credits 说「报价仍为 0」），
  排障时要分别记两套说法。
- **P3-3** `submitCredits` 里 `estimate` 与 `submission` 写在 `records/`，但**没有**
  记录 `verification` 回执；出问题只能靠上游日志。

## 8. 下一步（建议顺序）

1. **P1-1**（到期即坏，且影响付费通道）—— 改 `billing.mjs` 的空标签回退 + 免费队列文案。
2. **P1-2**（已有真实误判案例）—— 排队顺延 + 硬上限，需要 `store` 加一个 `extend_deadline`。
3. P2-3 → P2-1 → P2-2（自动闭环，需产品点头）。

前两条都要改 vendor/框架，各自需要一轮回归（vendor 有 manifest 哈希校验，按流程刷新）。


## 9. 修复记录（2026-11-18）

| 项 | 落地 |
|---|---|
| P1-1 | `billing.mjs`：无有效权益标签不再抛错（**返回空标签**，`applyBilling` 删掉顶层与 params 里的 `disableBenefitTags`，仍 `infiniteSwitch=1`）；`bridge.mjs` 去掉「必须有标签」的硬校验；免费队列文案补上估价与「改用积分队列」的指引。vendor 变更已按流程刷新 manifest。 |
| P1-2 | `GenerationProgress.queued` + `DeadlineExtendable` 协议：poll 看到归档 `queued` 就顺延墙钟起点；硬上限 `GENERATION_MAX_WALL_CLOCK_SEC`（默认 3 小时，按建单时间算）。两个 store 各实现一个 `extend_deadline`。 |
| P2-3 | `alignCanvasProject()` 读不到画布时**明确失败**（提示打开 liblib.tv/canvas），不再退回写死的旧 `project_id`。 |
| P2-1 | 见 P1-1 的文案部分：`waiting_free` 现在带估价 + 「改用积分队列」的下一步。 |
| P2-2 | **已修（范围收窄）**：只在用户点重试的那一刻自动对账——命中就接回（不花钱），确认没扣费就当场解封继续提交，判断不了才回落到命令。不做「框架自己重排」，所以不存在无人值守的自动扣费。 |

新增测试：`tests/test_libtv_billing_fallback.py`（node 真跑 `applyBilling`）、
`tests/test_generation_deadline.py`（顺延 + 硬上限 + 协议探测）、
`tests/test_libtv_auto_recover.py`（接回 / 解封 / 窗口没覆盖时不猜）。

**P2-2 的口径**：自动对账接在 `submit()` 的「归档已是 submission_unknown」分支上，
只有用户**主动点重试**才会走到；不会由 ticker/恢复器触发。判定还用一条额外的保险：
消费明细必须翻到提交时刻**之前**（`auto_verdict` 的 `unknown` 分支），
否则「没命中」只说明没看到，不足以断言没扣费。
