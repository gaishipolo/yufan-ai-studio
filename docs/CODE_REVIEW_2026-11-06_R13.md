# 后端代码评审 R13（2026-11-06 11:45）

> 范围：后端 `api/`，全局性代码、核心骨架与新增的扩展能力。
> 手法：基线 → 增量提交 → 逐层读码 → 临时探针（真实临时 SQLite + 真实 runner / lifespan）实测 → 清理探针 → 全量回归。
> 本轮**只新增报告，未改动任何生产代码**。

---

## 一、结论

### 增量

11:20 之后后端无新改动。本轮审的是上一轮只跑了基线、**没有深入读码**的提交：

| | |
|---|---|
| 提交 | `aa9c5e4` 「后端：修复 R12 复核的组合缺陷与可扩展框架七维补齐」 |
| 时间 | 2026-11-06 11:15:37 +0800 |
| 规模 | 79 files changed, 8181 insertions(+), 214 deletions(-) |
| 新文件 | `generation/recovery.py`、`test_image_policy.py`、`test_generation_state_machine.py`、迁移 `f4c2d8e91b70` 等 |
| 迁移链 | `844779d1b4c9 → b7f3a1c2d4e5 → 2e770625c6a3 → 9c1f4e2a7b83 → 4d2b8f6a1c90 → c8d5e2f7a910 → e6b1c9a4d230 → f4c2d8e91b70`，**线性单头，无分叉** |

提交信息里写「5 条主结论 + 4 条观察项全部修完」，本轮逐条复核。

### 质量基线（全部实测）

| 检查 | 结果 |
|---|---|
| `pytest -q --no-header` | **346 tests collected / 全通过**（R12 时 325） |
| `ruff check .` | `All checks passed!` |
| `mypy src` | `Success: no issues found in 144 source files` |
| `app export-openapi --check` | 四份生成物全部「无变化」：`openapi.json` / `API.md` / `schema.ts` / `permissions.ts` |
| 生成物字段 | `openapi.json` 与 `schema.ts` 的 `ImageJobOut` 均含 `externalId` / `previousExternalId`，**四份同步无误** |

### R12 逐条复核

| 问题 | 本轮修法 | 结论 |
|---|---|---|
| 🔴 R12-01 两个修复组合后重复计费 | `retry` 区分 `charged_but_lost`：带 `externalId` 默认 409、需 `confirmCharged=true`；旧任务号落 `previous_external_id` 新列 + 审计 detail；恢复失败文案改成费用提示 | ✅ **真修好**（探针 6 实测 `mark_failed` 保留凭证、默认重试被拦）；⚠️ 凭证只有**一个槽位**，见 R13-02 |
| 🟡 R12-02 `_guard.refresh` 冲掉未提交写 | `pipeline.py:100` 在 `refresh()` 前先 `flush()` | ✅ 修好 |
| 🟡 R12-03 阈值按全局驱动聚合 | `DriverSettings.submit_budget_for` 自描述；`registered_submit_budgets()` 只取声明 IMAGE 的、不吞异常、无声明返回 `None` 跳过本轮 | ✅ 修好 |
| 🟡 R12-04 默认兜底是黑名单 | `ImageAssetSettings.prod_required_fields=("allowed_hosts",)` + `ensure_prod_driver_settings()` prod 启动断言 | ✅ **prod 侧真修好**（探针 2 实测能 raise）；dev/test 仍走不解析 DNS 的兜底，是已知取舍（v2.0 规范已列未落地项） |
| 🟡 R12-05 `supports_poll` 默认 True | 默认改 `False`、echo/libtv 显式 `True`；`submit_mode` 快照落记录，恢复分类优先读快照、严格 `resolve()` | ✅ 修好 |
| 💭 R12-06 恢复是启动期一次性 | 巡检纳入 `running` | ⚠️ **半修**：排除条件恰好把最需要接的那类记录挡在外面，见 R13-04 |
| 💭 R12-07 双声明静默跳过 | `recovery.py:171` 补 `logger.warning` | ✅ 修好 |
| 💭 R12-08 `naive_cutoff` 跨库债 | `maintenance.py` 按 `settings.is_sqlite` 分支构造 cutoff | ✅ 修好 |
| 💭 R12-09 worker 关闭时恢复仍入队 | `app.py:80` 给 `recover_declared()` 加 `worker_enabled` 门控 | ⚠️ **半修，且漏掉的正是主要那一半**，见 R13-01 |

修复质量整体很高：`_guard` 的 flush 时机、`submit_mode` 快照优先、`ImageRetryOut` 只回 id/status（不泄漏他人 prompt）、表驱动状态机契约表都比报告建议的更细。**R12-01 那条资金风险是真正堵住了。**

### 本轮结果

| 编号 | 级别 | 一句话 |
|---|---|---|
| R13-01 | 🔴 | `worker_enabled` 门控只贴了框架恢复器，模块自带 `startup` 无条件执行：无 worker 的实例启动会改写记录状态、派发无人消费的 job |
| R13-02 | 🟡 | `previous_external_id` 是单槽，连续两次「确认已扣费」重试会覆盖掉第一份对账凭证 |
| R13-03 | 🟡 | 两道 prod 启动断言放在 lifespan 末尾：启动失败前已执行全部恢复副作用，`finally` 还不会执行 |
| R13-04 | 🟡 | 巡检纳入了 `running` 却排除有 `external_id` 的，轮询链断掉仍无人接管（R12-06 半修） |
| R13-05~08 | 💭 | 驱动注册表靠 import 顺序 / 阈值异常只会每 5 分钟刷日志 / 先 commit 后 dispatch / 任务号未进前端页面 |

---

## 二、🔴 R13-01 `worker_enabled` 门控只贴了一个入口，模块自带恢复全程无门控

### 现象

`app.py` 的启动顺序：

```python
# app.py:71-73  —— 无条件
modules = active_modules()
for spec in modules:
    if spec.startup is not None:
        await spec.startup()

# app.py:80-87  —— R12-09 新加的门控
if settings.worker_enabled:
    await recover_declared(modules)
else:
    logger.warning("worker 未启用，跳过框架启动恢复…")

# app.py:89-90
if settings.worker_enabled:
    await worker.start()
```

门控只包住了**框架恢复器**。而模块清单里恰好是这三个形状：

```
image    startup=None    recovery_kinds=('image',)               ← 被门控覆盖 ✅
task     startup=_startup recovery_kinds=('video',)              ← 双声明，框架跳过，startup 无门控 ❌
template startup=resume   recovery_kinds=('template_replacement',) ← 双声明，框架跳过，startup 无门控 ❌
```

`recover_declared()`（`recovery.py:169-173`）对双声明模块只打一条 warning 就 `continue`——**两个真正自带恢复器的模块，正好完全不在门控范围内**。R12-09 原文写的是「`app.py:79` 的 `recover_declared()` 在 `worker.start()` 之前且无条件执行」，修法也只动了这一行；但同一段代码里 `spec.startup()` 比它更早、更无条件。

### 实测（探针 1）

在 `WORKER_ENABLED=false` 下插一条 `queued` 的视频任务，走真实 `lifespan` 启动：

```
[probe1] worker_enabled=False startup-called=True
         dispatch=[('dispatch','video','84800ef6…'), ('dispatch','video','f28d857b…'),
                   ('dispatch','video','a5698e6f…'), ('dispatch','video','5102eb6b…')]
```

四条 job 被投进内存队列，而 `worker.start()` 因为门控**没有执行**——这些 job 没有任何消费者，且日志会照常报「恢复未完成任务 N 个」。

### 为什么是 🔴

不只是「白投几个 job」。`task/worker.py:resume_unfinished_tasks` 在派发前会**先写库并提交**：

- 有 `external_id` → `task.status = RUNNING`（改写）
- `submit_attempts>0` 且无 `external_id` → **置 `FAILED`、写 `SUBMIT_UNKNOWN_MESSAGE`、发 `EVT_GENERATION_FAILED` 失败通知**
- 无 attempts → 重置 `progress` / `started_at`

于是一个配了 `WORKER_ENABLED=false` 的**只读 API 实例**（多实例部署里很常见：只有一组实例跑 worker，其余只对外提供 HTTP），每次启动都会和真 worker 实例抢着恢复同一批记录：把「提交结果未知」判死、发失败通知、把 `started_at` 清零。这些改动不可逆，只能靠人工 retry 兜回。

`conftest.py` 恰恰把 `WORKER_ENABLED=false` 写成默认并注释「关闭后台 worker，保证断言可预期」——**整个测试套件每次 session 启动都在做这件事**，只是库里通常没有未终态记录，所以没人察觉。

这又是「不变量只守一个入口」：`worker_enabled` 表达的是「本实例不消费队列、不该做恢复」，但只有框架那一条路径认它。v2.0 `CODE_STYLE.md` 刚把这条写成底线，本轮就在同一份提交里复发了。

### 建议

把门控提到循环外面，覆盖**两类**恢复：

```python
if settings.worker_enabled:
    for spec in modules:
        if spec.startup is not None:
            await spec.startup()
    await recover_declared(modules)
else:
    logger.warning("worker 未启用，跳过全部启动恢复（模块 startup + 框架恢复）…")
```

并补一条架构守卫：`ModuleSpec` 里凡声明 `startup` 的模块，其恢复动作必须能在 `worker_enabled=false` 时被跳过；或更直接——在 `resume_unfinished_tasks` / `template.resume` 入口各加一次 `worker_enabled` 判断，让「不变量有两个写点」至少两处都认。测试上，把探针 1 固化成一条用例（插 queued 记录 → 关 worker 启动 → 断言记录状态未被改写、队列为空）。

---

## 三、🟡 R13-02 `previous_external_id` 只有一个槽位，第二次确认会抹掉第一份凭证

`service.py:158-170`：

```python
previous_external_id = job.external_id
if previous_external_id and not confirm_charged:
    raise InvalidState("…请先用任务号核对…")
if previous_external_id:
    job.previous_external_id = previous_external_id   # ← 直接覆盖
```

R12-01 的全部价值是「**别把对账凭证弄丢**」。但记录只允许留一个 `previous_external_id`：一条任务第一次带 `confirmCharged` 重试后留下 `EXT-FIRST`，第二次提交又拿到 `EXT-SECOND` 并再次失败，用户再确认一次，列里就只剩 `EXT-SECOND`——**第一份账单的凭证被覆盖**。

### 实测（探针 4）

```
[probe4] 第一次确认后 previous_external_id='EXT-FIRST'
[probe4] 第二次确认后 previous_external_id='EXT-SECOND'
[probe4] EXT-FIRST 是否还在记录上 = False
```

审计 detail 里虽有 `previousExternalId`，但 `core/audit.py:record()` 按设计吞掉一切异常、未注册时直接 `return`；`service.py` 的注释自己也写「审计写入按设计会吞异常，所以不能只靠它」。于是**主凭证和备份凭证会在同一轮里一起丢**。

### 建议

低成本改法：把列改成追加语义（逗号分隔或 JSON 数组，或新增一张 `image_job_external_ids` 明细表），出参 `previousExternalId` 保持返回最近一个、另加 `externalIdHistory`。若暂时不改表，至少在覆盖前把旧值拼进新值，并在 `image.retry` 的审计 detail 里同时带 `previousExternalId` 与 `replacedExternalId`。补一条「连续两次 charged 重试」用例。

---

## 四、🟡 R13-03 两道 prod 断言在启动流程末尾：失败前已产生副作用，且 `finally` 不执行

`app.py` 的顺序是：

```
:71  spec.startup()          ← 写库、入队、发失败事件
:80  recover_declared()      ← 写库、入队
:90  worker.start()          ← 起后台任务
:114 ensure_audit_sink()
:119 ensure_prod_driver_settings()   ← 「启动期炸掉」在这里
:135 try: yield … finally: worker.stop() / dispose_engine() / shutdown_hash_pool()
```

两个问题叠在一起：

1. **断言太晚**。「生产环境必须配白名单，否则不许启动」本意是*在任何动作之前*拦住，实际是先跑完恢复（可能已判死几条记录、发几条失败通知）、起完 worker，才检查配置。运维补上配置重启后，上一轮「失败的启动」留下的状态改写不会回滚。
2. **清理不执行**。`try: yield` 在 `:135`，断言在它之前抛异常时 `finally` 根本进不去，已启动的 `worker` 后台任务与 engine 不会被 `stop()` / `dispose_engine()`。

### 实测（探针 3）

```
[probe3] 启动失败 order=['module.startup (写库/入队)', 'ensure_prod_driver_settings -> RuntimeError']
```

### 建议

把 `ensure_audit_sink()` 与 `ensure_prod_driver_settings()` 提到 `lifespan` 开头（紧跟 `get_settings()` / `setup_logging()`，在 `init_db()` 与任何模块钩子之前），让「配置不合法」成为一个零副作用的快速失败。若担心驱动注册表还没 import 全（见 R13-05），显式调用一次 `discover_modules()` 即可，不需要靠启动流程后半段的 import 副作用。

---

## 五、🟡 R13-04 巡检纳入了 `running`，却把最需要接的那类排除了

`maintenance.py:110-115`：

```python
ImageJob.status.in_((STATUS_QUEUED, STATUS_RUNNING)),
ImageJob.external_id.is_(None),          # ← 排除已受理
started < cutoff_expr,
```

注释（`:108-111`）写的是「`running` 也要捞：恢复器只在启动跑一次且上限 100 条，**轮询链断掉后卡在 running 的记录本来没人管**（R12-06）」。但「轮询链断掉」的记录**必然有 `external_id`**（`mark_submitted` 才进 running 并写任务号），而这个 `is_(None)` 恰好把它们全挡在外面。也就是说，R12-06 描述的场景一条都没被覆盖，被纳入的只是「提交动作还在跑、没拿到任务号」的那一类——那类原本就在启动恢复器的处置范围内。

### 实测（探针 5）

构造一条 `running + external_id=EXT-ACCEPTED + submit_mode=async + started_at=3 小时前`：

```
[probe5] 巡检超期 running(有 external_id) 派发 = []
[probe5] 启动恢复对同一条 = {'resubmit': 1, 'resume_poll': 1, ...}
         sink=[('dispatch',…), ('resume','image','54681993…')]   ← resume 的正是这条
```

启动恢复器能捞到并 `resume()`，但它**只在进程启动时跑一次**（`RECOVERY_BATCH=100`）。轮询链在两次重启之间断掉，记录就一直挂在 `running`，周期巡检每 5 分钟扫过它、每次都跳过。

### 建议

巡检按形态分流，而不是一刀切排除：

- `external_id is None` → 维持现在的 `dispatch()`（提交前中断）；
- `external_id is not None` 且 `submit_mode='async'` 且 `running` 超期 → 调 `runner.resume(kind, id)` 续轮询，**绝不 `dispatch()`**（这是 R12-01 的计费红线）；
- `submit_mode='sync'` 或快照缺失 → 不动，交人工核对。

`runner.resume()` 自带幂等（记录终态早退），重复调用安全。补一条用例锁住「有 external_id 的 running 只被 resume、从不被 dispatch」。

---

## 六、💭 观察项

| 编号 | 观察 | 位置 / 依据 |
|---|---|---|
| R13-05 | **驱动注册表靠 import 顺序**。`_REGISTRY` 只在类被 import 时追加，`ensure_prod_driver_settings()` 与 `registered_submit_budgets()` 都只扫当前已登记的。探针 2 在 `AI_PROVIDERS=echo` 环境实测 `registered=['ImageAssetSettings','LibTvSettings']`——`AgnesSettings` 不在。`_stale_after_sec()` 靠函数内 `from infra.ai import agnes`（`maintenance.py:69`）兜住了，但**启动期 prod 断言没有同样的兜底**：将来给 `AgnesSettings` 加 `prod_required_fields`，在只加载 echo 的部署里不会被检查。建议提供显式的 `load_all_driver_settings()` 或在断言里 import 全部驱动模块。 | `driver_config.py:67`、`app.py:117-119` |
| R13-06 | R12-03 把「静默降级」改成「配置坏了直接抛」，方向对；但 `registered_submit_budgets()` 的异常会被 `Scheduler.run_once`（`scheduler.py:128`）捕获记日志后 `continue`，**每 300 秒刷一条错、巡检永久停摆**。从「悄悄用错阈值」变成「响亮地什么都不做」，都没人能保证被发现。建议在启动期做一次 `registered_submit_budgets(Capability.IMAGE.value)` 预检。 | `maintenance.py:58-78`、`scheduler.py:128-130` |
| R13-07 | `retry` 先 `commit()`（`:181`）再 `audit.record()` 再 `dispatch()`（`:193`）。dispatch 抛异常时 HTTP 500，但记录已是 `queued`、旧凭证已搬走、用户再点 retry 只会得到「仅失败的生图任务可以重试」，只能等巡检 270 秒后重投。 | `service.py:181-193` |
| R13-08 | 409 与恢复失败文案都让用户「凭供应商任务号核对结果」，`schema.ts:209/211` 也同步了 `externalId` / `previousExternalId`，但 `studio/src` 除生成物外**零引用**。页面目前不展示任务号，用户拿不到核对所需的凭证。属前端迁移债（按项目约定不代改前端），但接口契约的影响要记一笔。 | `service.py:159-165`、`recovery.py:51-54` |

另：`image:retry:any` 已由 `editor` 的 `image:*` 通配覆盖、超管 `is_superuser=True`，**重试他人生图任务的入口可用**（本轮特意核对，不是问题）。

---

## 七、模式：本轮两条半修，都是「门控/条件贴在了错误的那一层」

R11 的形状是「新模块只接了框架一半插槽」，R12 是「两个各自正确的修复组合后破例」，R13 出现了第三种：

> **一个约束被加在了「谁来实现」的分类上，而不是加在「约束本身」上。**

- R12-09 的 `worker_enabled` 表达的是「本实例不做恢复」，却只贴给「框架恢复器」这一种实现；模块自带 `startup` 是另一种实现，就被漏掉（R13-01）。
- R12-06 的「巡检要接管 running」表达的是「启动恢复之后的漏网记录也要有人管」，却贴成了「status in (queued, running)」这一层，再用 `external_id is None` 把真正漏网的那类排除（R13-04）。

两者都不是新写的逻辑有错，而是**新条件放错了抽象层**：一个按「恢复动作由谁实现」分类，一个按「记录状态」分类，都没按约束真正要保护的语义（不消费队列 / 轮询链断掉）来切。

这也解释了为什么 346 个用例、ruff、mypy、四份契约检查全绿：组件级用例各自守住了「框架恢复在 worker 关闭时跳过」「巡检会捞 running」，**没有一条用例把 `WORKER_ENABLED=false` 和 `ModuleSpec.startup` 放在同一个场景里**，也没有一条把「有 external_id 的 running 超期」放进巡检。v2.0 `REVIEW_CHECKLIST.md` 的 §6 反模式表已有「组件各自绿、组合破例」，本轮建议再加一条：**新加的门控/排除条件，要逐项对照它想约束的语义集合，而不是只覆盖触发这次修复的那一种实现**。

最省维护的补法仍是表驱动：把 `(worker_enabled, 恢复实现=框架/模块/无)` × `(startup 是否执行, 队列是否为空, 记录是否被改写)` 列成一张表；再把 `(status, external_id, submit_mode, 超期)` × `(启动恢复 / 周期巡检 / retry)` 扩到现有 `test_generation_state_machine.py` 里。两张表能一次锁住 R13-01、R13-04 与 R11/R12 那几条死锁。

---

## 八、建议顺序

1. **R13-01**（`app.py` 门控上提，约 8 行 + 1 条启动用例）——唯一会改写数据库状态、且是上轮明确要求修却漏掉一半的项。
2. **R13-03**（两道断言提到 `lifespan` 开头，约 6 行）——让配置失败成为零副作用快速失败，顺带修掉 worker 泄漏。
3. **R13-04**（巡检按 `submit_mode` 分流，约 20 行 + 1 条「只 resume 不 dispatch」用例）——注意别碰 `dispatch()` 红线。
4. **R13-02**（凭证改追加语义，需要小迁移）——R12-01 的价值在多轮重试下才完整。
5. R13-05 / 06 随手处理；R13-07 / 08 记进缺口表。

---

## 附录：复现方式

本轮 6 条临时探针写在 `api/tests/test___probe_r13.py`，用真实临时 SQLite、`FastAPI.lifespan`、真实 `resume_unfinished_tasks` / `reconcile_stale_jobs` / `recover_kind`，只把 runner 换成记录调用的 spy，不改动全局队列单例。运行：

```bash
cd api && ./.venv/Scripts/python.exe -m pytest -q -s tests/test___probe_r13.py
```

关键输出：

```
[probe1] worker_enabled=False startup-called=True dispatch=[('dispatch','video',…)×4]
[probe2] registered=['ImageAssetSettings','LibTvSettings']
[probe2] prod guard = RAISE: 生产环境驱动配置缺失…ImageAssetSettings.allowed_hosts
[probe3] 启动失败 order=['module.startup (写库/入队)', 'ensure_prod_driver_settings -> RuntimeError']
[probe4] 第一次确认后 previous_external_id='EXT-FIRST'
[probe4] 第二次确认后 previous_external_id='EXT-SECOND'
[probe5] 巡检超期 running(有 external_id) 派发 = []      ← 启动恢复却能 resume 同一条
[probe6] mark_failed 后 external_id='EXT-CHARGED' status='failed'
[probe6] 默认 retry = InvalidState: …可能已产生费用…
```

探针跑完已删除，删除后重跑全量回归确认 346 用例、ruff、mypy、四份生成物均未受影响。
