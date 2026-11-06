# 后端代码评审 · 第十二轮（R12）

> 时间：2026-11-06 10:40
> 承接：`CODE_REVIEW_2026-11-06_R11.md`
> 范围：只审后端；复核 R11 五条的修复，并检查修复**组合之后**产生的新交互面
> 方法：基线 → 增量比对 → 逐层读码 → 临时探针（真实 SQLite / runner / queue / 模型目录）实测 → 清理 → 全量回归

---

## 一、结论

### 增量

09:50 之后 **23 个源文件**改动，另有三份生成物与 `ARCHITECTURE.md` / `BACKEND_DELIVERY_IMAGE.md` / `EXTENSIBILITY_GAPS.md` 同步更新。新增：

- `generation/recovery.py` —— 框架级启动恢复器（三类未终态记录分流）
- `ModuleSpec.recovery_kinds` + `app.py` 调用 + 两条"模块 × 插槽"架构守卫
- `POST /images/{id}/retry` + `image:retry:any` 权限点 + `ImageRetryOut`
- `image/pipeline.py` 的落盘加固、终态守卫 `_guard()`、`list_unfinished()`
- `core/audit.py::ensure_audit_sink()` + `audit-sink` 注入口登记
- `tests/test_generation_recovery.py`（8 例）、`test_image_retry.py`（8 例）、`test_image_persist.py`（11 例）

### 质量基线（全部实测）

| 项 | 结果 |
|---|---|
| `pytest` | **325 tests collected / 全通过**（R11 为 292，+33） |
| `ruff check .` | `All checks passed!` |
| `mypy src` | `Success: no issues found in 144 source files` |
| `export-openapi --check` | 四份生成物全部无变化（新路由已进 `openapi.json` / `API.md` / `schema.ts` / `permissions.ts`） |

### R11 逐条复核

| 条目 | 修复 | 复核 |
|---|---|---|
| 🔴 R11-01 `running` 孤儿无人接管 | 新增框架恢复器 + `recovery_kinds` 声明 + 架构守卫；识别到同步供应商时显式置失败而非无脑 `resume` | ✅ **真修好**，且把我上轮点出的"Agnes 的 `poll()` 会判死"这个坑写进了实现 |
| 🔴 R11-02 `submit_unknown` 永久死锁 | 补 retry 路由、清 `submit_attempts`、终态保护 `_guard`、阈值按驱动超时推导、日志按分支分开、架构守卫 | ✅ 主体修好；⚠️ 但 `_guard` 引入 **R12-02** 回归 |
| 🟡 R11-03 落盘 SSRF | 逐跳 host 校验、`follow_redirects=False`、流式大小上限、content-type 白名单、文件魔数、12 条用例 | ⚠️ 大部分修好，默认兜底仍留 **R12-04** |
| 🟡 R11-04 全表扫描 | `cutoff` / `LIMIT 50` 下推到 SQL、排序以主键收尾、改用 `started_at` 判陈旧 | ✅ 修好；阈值来源有 **R12-03** |
| 🟡 R11-05 审计写入端静默失效 | `audit-sink` 进 `PORT_CATALOG`、扫描范围扩到 `core/`、`ensure_audit_sink()` 在 prod 启动期断言 | ✅ **真修好** |

这轮改法值得表扬的地方：`_guard()` 的注释准确指出"identity map 不刷新就读不到别的 worker 提交的终态"、`_stale_after_sec()` 尝试从驱动声明推导而非写死常量、`persist_output` 把每一跳重定向都重新过校验并验魔数、以及两条架构守卫确实把 R11 第八节的建议落成了 CI 可执行的断言。33 条新用例也不是摆设，覆盖了同步孤儿、终态覆盖、重定向、超限等边界。

### 本轮结果

**1 条 🔴 / 4 条 🟡 / 4 条 💭**。核心发现是一个之前没见过的形状：**R11-01 和 R11-02 两个修复各自都正确，但组合到同一条记录上时，破了一条框架反复强调的硬不变量——"绝不重复计费"**。

---

## 二、🔴 R12-01 同步供应商的 `running` 孤儿被判"请重试"后，一次点击 = 第二次计费 + 对账凭证丢失

**位置**：`generation/recovery.py:76-93`（`_fail_unrecoverable`）、`:50-52`（`SYNC_PROVIDER_ORPHAN_MESSAGE`）、`modules/image/service.py:131-165`（`retry`）

### 完整链路（探针实测）

造一条真实中间态：Agnes 已经返回终态、`mark_submitted()` 已写入 `external_id`、进程在 `_settle()` 之前崩溃。

```
[probe2] recover counts = {'resubmit': 0, 'resume_poll': 0, 'needs_review': 0, 'sync_orphan': 1}
[probe2] after recover: status=failed external=agnes-task-REAL-123
[probe2] after retry:   status=queued external=None attempts=0
[probe2] runner calls after retry = [('dispatch', '2fff2512…')]
```

恢复器按 R11-01 的建议做对了：识别到 `supports_poll=False`，没有 `resume()`（那会被 `AgnesProvider.poll()` 判死），而是置为 `failed`，文案是「生成结果未能落库：该供应商是同步接口，不支持续查，**请重试**」。用户照做，走 R11-02 新加的 retry 出口——`service.retry()` 把 `external_id` 清成 `None`、`submit_attempts` 归零、重新 `dispatch()` → `provider.submit()` 再调一次 Agnes。

### 为什么是 🔴

`generation/runner.py:112-118` 的 `resume()` docstring 把这条不变量写得很清楚：

> 已有 `external_id` 意味着供应商侧任务已经在跑（**也可能已经扣费**），再走一遍 `dispatch` 会重复提交、重复计费，并生成两个不同的产物。

而 `submit_unknown` 三态防线（`runner.py:143-161`）存在的全部理由，就是宁可让一条任务失败等人核对，也不下第二份账单。现在这条记录**带着 `external_id`**（供应商侧的 `task_id`，说明图已经生成、钱已经花了）进入了 retry，且失败文案没有任何"可能已计费"的提示。更糟的是 `service.py:151` 把 `external_id` 直接清空——**连拿去供应商对账的凭证都没了**，事后无法证明这是第二次提交。

单独看两个修复都没错：恢复器不能续查所以置失败，retry 要清标记否则永远死锁。**错的是没有人负责"记录带着 `external_id` 进入 retry"这条组合路径。** 一次部署重启若恰好打断一批同步生成，会同时产生多条"请重试"，用户批量点就是多份账单。

### 建议

区分两种 `failed`，别让它们共用一个出口：

- `submit_unknown`（`attempts>0`、无 `external_id`）：确定没确认受理，可安全重试 → 现有 retry 适用；
- `charged_but_lost`（有 `external_id`、结果未落库）：可能已扣费 → 优先提供"按 `external_id` 找回结果"；做不到则在 retry 时**保留 `external_id` 到审计/历史字段**、要求显式二次确认或管理员权限，文案改成「该次生成可能已产生费用，确认后再提交一次」。

最小改动：`service.retry()` 遇到 `job.external_id` 非空时拒绝或走单独分支，并把该值写进 `audit.record("image.retry", detail={"previous_external_id": ...})`。这样即使重来一次，对账也有据可查。

---

## 三、🟡 R12-02 `_guard()` 的 `refresh` 冲掉了同会话未提交的 `clear_submit_attempt`，"确定没送达"分支形同失效

**位置**：`modules/image/pipeline.py:77-87`（`_guard`）、`generation/runner.py:227-232`

### 现象

R11-02 的终态保护给每个 `mark_*` 加了 `await session.refresh(job)`，本意是读到别的 worker 已提交的 `failed`。但 refresh 会把**本 session 尚未提交的改动一并丢弃**。`handle_submit` 的"确定没送达"分支正好是两次连续写：

```
runner.py:227  await store.clear_submit_attempt(record_id)   # 设 attempts=0，未 commit
runner.py:232  await self._fail(...)                          # → mark_failed → _guard → refresh
```

探针用 `httpx.ConnectError`（`_possibly_delivered` 判定"确定没送达"）走完这条分支：

```
[probe1] final status=failed attempts=1 error=提交生成失败：模拟连不上（确定没送达）
[probe1] expected attempts=0（确定没送达应已清标记）, actual= 1
```

### 影响

`clear_submit_attempt()` 的效果被抹掉，记录最终长成 `failed + attempts=1 + 无 external_id` —— 正是 `submit_unknown` 的形状，而错误文案却是「提交生成失败：连不上」（明确没送达、本可自动重试）。于是：

- 按 `submit_attempts>0` 筛"可能已计费、需人工核对"的对账会把这批误报进去；
- 框架"确定没送达 → 清标记 → 允许安全重试"的设计在这条分支上不再成立；
- 好在 `service.retry()` 会清标记，所以**不会重新死锁**，这也是定 🟡 而非 🔴 的原因。

### 建议

`refresh` 只应出现在跨会话/跨 worker 的读边界。两个方向：

1. 让 `_fail()` 和 `clear_submit_attempt()` 合并成**一次原子写**（runner 先决定终态与标记，再一次性落库），中间不插入第二次 `_guard`；
2. 或 `_guard` 在做 refresh 前先 `await session.flush()` 保住本会话改动，并只对"需要读别人提交的数据"的入口调用，而不是每个 `mark_*` 都刷。

补一条用例：模拟 `ConnectError` → 断言 `submit_attempts == 0`。现有 `test_image_retry.py` 覆盖了 retry 清标记，但没有覆盖 runner 这条失败分支。

---

## 四、🟡 R12-03 巡检阈值按"所有已登记驱动的最大 `timeout_sec`"聚合，与生图无关的配置能把阈值拖到 90 分钟

**位置**：`modules/image/maintenance.py:56-73`

```python
for cls in registered_driver_settings():
    value = getattr(cls(), "timeout_sec", None)
    ...
return max(_MIN_STALE_AFTER_SEC, max(budgets, default=0.0) * _TIMEOUT_MARGIN)
```

思路（从驱动声明推导，别写死 90 秒）是对的，但聚合口径错了：`registered_driver_settings()` 是**全部驱动配置的全局注册表**，按字段名 `timeout_sec` 猜语义。实测注册表里有 `ImageAssetSettings`（成图下载 60 秒）、`LibTvSettings`、`AgnesSettings`（提交 180 秒），默认阈值 270 秒；再登记一个与提交毫无关系的驱动配置后：

```
[probe3] _stale_after_sec() = 270.0
[probe3] registry now = ['ImageAssetSettings', 'LibTvSettings', 'AgnesSettings']
[probe3] after adding an unrelated driver with timeout_sec=3600: _stale_after_sec() = 5400.0
```

**5400 秒（90 分钟）**——生图卡住要等一个半小时才被巡检捞回。另外两处不稳：

- `_REGISTRY` 靠 import 期登记，探针第一次读时是**空列表**；`_stale_after_sec()` 只显式 import 了 `agnes`，别的驱动是否被 import 取决于启动顺序，阈值因此不确定；
- `:68` 的 `except Exception: continue` 会在某个驱动配置缺必填项时静默跳过，阈值悄悄回落到 90 秒兜底——**正好退回 R11-02 的误伤窗口**。

这其实是 `EXTENSIBILITY_GAPS.md` 里 C1「轮询节奏全局化」的镜像：上轮是"全局参数管所有 pipeline"，这轮是"所有驱动的参数管一个 pipeline"。

**建议**：只取本模块真正会用的 IMAGE 供应商超时——从 `get_model_router().models(Capability.IMAGE)` 拿到 provider 名，再读对应 `DriverSettings`；把"取不到就报警"替代静默 `continue`。至少把筛选条件限定到 `infra/ai/` 下声明的供应商配置类，别扫全局。

---

## 五、🟡 R12-04 落盘 URL 校验的默认兜底是黑名单，挡不住域名解析到内网

**位置**：`modules/image/pipeline.py:53-56, 68, 215-243`

R11-03 的主体修好了：`follow_redirects=False` + 逐跳 `_validate_asset_url`、流式累计 `max_bytes`、content-type 白名单、文件魔数校验，12 条用例覆盖到位。剩下的问题是**白名单默认不开**（`allowed_hosts: str = ""`），此时走黑名单兜底：只挡 IP 字面量里的私有/环回/链路本地地址，以及 `.internal` / `.localhost` 之类后缀。实测：

```
[probe4] http://169.254.169.254/latest/meta-data/ -> BLOCK
[probe4] http://127.0.0.1/secret.png              -> BLOCK
[probe4] http://8.8.8.8/secret.png                -> PASS
[probe4] http://attacker.example.com/secret.png    -> PASS
[probe4] http://169-254-169-254.nip.io/secret.png -> PASS   ← DNS 解析到 169.254.169.254
```

`_validate_asset_url` 只检查 URL 字符串里的 hostname，**不解析 DNS**。任意公网域名（包括 `nip.io` 这类把 IP 编进域名的服务）都能通过，httpx 随后把请求发到解析出的内网/元数据地址。文件头注释写的「默认不放行任何 IP 字面量」也与实现不符——公网 IP 字面量是放行的（这一条本身风险较低，但注释会误导后来人）。

**建议**：生产环境强制要求配置 `IMAGE_ASSET_ALLOWED_HOSTS`（可以照 `ensure_audit_sink()` 的做法，在 prod 启动期断言非空）；或在校验时解析 hostname、对全部 A/AAAA 记录做同样的公网判定并把连接固定到已校验的 IP。二选一即可把这条路封死。

---

## 六、🟡 R12-05 `supports_poll` 默认 `True`，且恢复分类靠"启动时重新解析模型目录"而不是记录自身的快照

**位置**：`generation/recovery.py:62-73`、`infra/ai/base.py:120-123`、`infra/ai/agnes.py:95`

```python
return bool(getattr(provider, "supports_poll", True))
```

`base.py` 里 `supports_poll` 是**注释掉的可选属性**，只有 Agnes 显式设了 `False`。探针用一个不带该属性的假同步供应商：

```
[probe5] provider with no supports_poll -> _supports_poll = True
```

也就是说：**下一个同步供应商只要忘了声明这个可选属性，恢复器就会 `resume()` 它**，`handle_poll` 拿到 `poll()` 的 FAILED 后把记录判死——正是 R11-01 建议里专门提醒要避开的坑。默认值应选"更安全的一边"。

更根本的是分类依据：`_supports_poll(record)` 用 `get_model_router().provider_for(record.model)` **按当前配置重新解析**。探针 2 第一次运行时（测试环境只加载了 echo）把一条 `model_id="agnes-image-2.5-flash"` 的记录判成了 `resume_poll` 而不是 `sync_orphan`——因为 `router.provider_for` 对未声明模型有"只有一个供应商时兜底"的行为（`infra/ai/router.py:103-105`），返回了 echo。**用现在的模型目录解释历史记录，供应商下线或换配置时分类就会错。**

**建议**：把能力快照写进记录（`mark_submitted` 时落一列 `supports_poll`，或复用 `model_id` 到 `ModelSpec` 的持久映射），恢复时读记录而不是重新解析；`supports_poll` 提升为 `ModelProvider` 协议的必填属性，或把 `getattr` 默认值改成 `False`。

---

## 七、💭 观察项

| 编号 | 观察 | 说明 |
|---|---|---|
| R12-06 | 恢复是启动期一次性动作 | `recover_kind()` 的 `RECOVERY_BATCH=100`（`recovery.py:48`），而 `image.reconcile` 只捞 `queued`。若 `resume()` 排的轮询链之后再断、或残留记录超过 100 条，没有第二次恢复机会（除非重启）。建议把 `running` 也纳入周期巡检，或让恢复可重入 |
| R12-07 | `recover_declared` 静默跳过双声明模块 | `recovery.py:145`：`startup` 非空时直接 `continue`，即使模块也声明了 `recovery_kinds`。作者以为框架接管了，实际没有。建议此时告警或断言 |
| R12-08 | `naive_cutoff` 的跨库债被写进注释 | `maintenance.py:90-91` 自己承认「换 PG 时要改回 aware cutoff」。项目同时支持 sqlite / postgres，建议按 dialect 分支或用数据库侧时间函数消掉这段 |
| R12-09 | `worker_enabled=false` 时恢复仍会入队 | `app.py:79` 的 `recover_declared()` 在 `worker.start()`（`:83`）之前且无条件执行，worker 关闭时恢复的 job 无人消费，启动日志却报"恢复成功"。建议按 `settings.worker_enabled` 决定是否执行，或在日志里说明 |

---

## 八、一个之前没出现过的形状：两个各自正确的修复，组合后破例

前十一轮反复出现的是「不变量只守一个入口」。这轮不同：**R11-01（恢复器）和 R11-02（retry 出口）分开看都实现正确、测试也都覆盖了各自的边界**，但没人拥有它们交汇的那条路径——

```
running + external_id（已扣费）
   └─ 恢复器：不能续查 → 置 failed「请重试」      ← R11-01 的修法
        └─ retry：清 external_id + 重投            ← R11-02 的修法
             └─ 第二次 provider.submit()           ← 破例：绝不重复计费
```

`test_generation_recovery.py` 测了"同步孤儿被置失败"，`test_image_retry.py` 测了"failed 记录能重投"，两条用例各自都绿；**没有任何用例把这两个动作串起来跑一遍**。组件级测试天然覆盖不到跨组件的状态机交接，而这恰恰是这类框架最容易漏的地方。

建议补一张**表驱动的跨组件状态机契约测试**：枚举 `(status, external_id, submit_attempts)` 的所有合法组合 × `(恢复 / 巡检 / retry / handle_submit)` 四个动作，断言每格的终态、是否允许重新提交、以及 `external_id` 是否保留。这张表同时能一次性锁住 R12-01、R12-02 和 R11 那两条死锁，比继续加分支用例更省维护。

另外 R12-02 / R12-03 也提醒一件事：**"加一道守卫"本身会改变写入顺序**（`refresh`）、**"从配置推导"本身会引入聚合口径**（全局注册表）。修复的副作用往往不在被改的那行代码里，而在它邻近的第二次写、第二个调用者身上。

---

## 九、建议顺序

1. **R12-01**（`service.retry` 区分 `charged_but_lost`，约 15 行 + 1 条串联用例）——唯一涉及真实资金损失与对账凭证丢失的一条。
2. **R12-02**（把 `clear_attempt` 与 `_fail` 合成一次原子写，或修 `_guard` 的 flush 时机，约 10 行 + 1 条用例）——修复引入的回归，改动小。
3. **R12-04**（prod 强制 `IMAGE_ASSET_ALLOWED_HOSTS` 或解析后校验 IP）——安全项，集中在一个函数。
4. **R12-03**（阈值只取 IMAGE 供应商 + 去掉静默 `continue`）、**R12-05**（`supports_poll` 必填/默认 False + 能力快照落记录）。
5. 补第八节那张跨组件状态机表；R12-06~09 随手处理。

---

## 附录：复现方式

临时探针 `api/tests/test___probe_r12.py`（跑完已删，未改任何生产代码），使用 `conftest.py` 的独立临时 SQLite、真实 `GenerationRunner` / `MemoryJobQueue` / `ImageService`，以及 `monkeypatch` 替换模型目录。`pytest -q -s` 输出：

```
[probe1] final status=failed attempts=1 error=提交生成失败：模拟连不上（确定没送达）
[probe1] expected attempts=0（确定没送达应已清标记）, actual= 1

[probe2] recover counts = {'resubmit': 0, 'resume_poll': 0, 'needs_review': 0, 'sync_orphan': 1}
[probe2] after recover: status=failed external=agnes-task-REAL-123
[probe2] after retry:   status=queued external=None attempts=0
[probe2] runner calls after retry = [('dispatch', '2fff2512…')]

[probe3] _stale_after_sec() = 270.0
[probe3] registry now = ['ImageAssetSettings', 'LibTvSettings', 'AgnesSettings']
[probe3] after adding an unrelated driver with timeout_sec=3600: _stale_after_sec() = 5400.0

[probe4] http://169.254.169.254/latest/meta-data/ -> BLOCK
[probe4] http://8.8.8.8/secret.png                -> PASS
[probe4] http://169-254-169-254.nip.io/secret.png -> PASS

[probe5] provider with no supports_poll -> _supports_poll = True
5 passed
```

清理探针后全量回归：`pytest` 325 全通过、`ruff` All checks passed、`mypy` 144 files clean、`export-openapi --check` 四份生成物无变化。
