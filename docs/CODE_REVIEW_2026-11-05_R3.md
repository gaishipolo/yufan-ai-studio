# 代码评审报告 R3 · 第三轮

> 评审时间：2026-11-05 13:34 ｜ 范围：12:45–12:51 那轮改动（`params.py` / `permissions.py` / `submit_attempts` 状态机 / 前端目录驱动）
> 关联：[`R2`](./CODE_REVIEW_2026-11-05_R2.md)｜[`首轮`](./CODE_REVIEW_2026-11-05.md)｜[`CODE_STYLE.md`](./CODE_STYLE.md) / [`REVIEW_CHECKLIST.md`](./REVIEW_CHECKLIST.md)

---

## 一、结论

R2 提的 9 条，**9 条都真的改了，而且改法比我建议的更完整**：`count_by_status` 直接搬 `whereclause` 而不是子查询、`to_out` 给历史数据反查 `modelId`、轮询异常自己兜住 + 入队失败两次后标失败、`_retryable` 按 `AppError.retryable` 分流、`task:read:all` 收敛成单一开关。测试 69 → 81，mypy 0 error。

代价是引入了一个**新的状态机漏洞**：`submit_attempts` 只在「提交明确失败」时清零，提交成功后不复位，而 `retry_task` 恰好构造出「attempts>0 且无 external_id」——于是**所有在提交成功之后失败的任务，重试必然二次失败**，还附赠一句「请核对后手动重试」的提示，而手动重试就是刚点的那个按钮。

| 编号 | 级别 | 结论 |
|---|---|---|
| R3-01 | 🔴 | 重试入口被 `submit_attempts` 锁死，实测复现；「结果未知」的任务没有任何人工恢复路径 |
| R3-02 | 🟡 | 提交超时/网络中断被当成「明确失败」清零意图标记，重复计费窗口仍在（方向与 R3-01 相反） |
| R3-03 | 🟡 | 前端「全部任务」对无权限用户静默降级为「我的任务」，下拉在说谎 |
| R3-04 | 💭 | `permissions.py` 的常量化只做了一半：5 个常量里 4 个没人用，`dependencies.py` 仍写裸字符串 |
| R3-05 | 💭 | `resume` 的 `polls` 归零、`mark_submitted` 不复位 `submit_attempts`（R3-01 的根因） |

### 质量基线（实测）

| 检查项 | 结果 |
|---|---|
| `ruff check src tests` | ✅ All checks passed |
| `pytest -q` | ✅ 81 passed |
| `mypy src` | ✅ Success: no issues found in 94 source files |
| `tsc -b`（studio） | ✅ clean |
| 迁移 | ✅ `b7f3a1c2d4e5` 补齐 `model_id` / `style_preset` / `reference_asset_ids` / `submit_attempts` 四列，含 `server_default` |
| 测试缺口 | ❌ 「提交成功后再失败 → 重试」这条路径无覆盖（正是 R3-01 所在） |

---

## 二、🔴 R3-01 重试入口被 `submit_attempts` 锁死

**位置**：`api/src/modules/task/service.py:132-148`（`retry_task`）、`api/src/generation/spec.py:76`（`submit_unknown`）、`api/src/modules/task/pipeline.py:77-84`（`mark_submitted` 不复位）

```python
# spec.py
@property
def submit_unknown(self) -> bool:
    return self.submit_attempts > 0 and not self.external_id

# service.retry_task：清了 external_id，没清 submit_attempts
task.external_id = None     # ← 这一行让 submit_unknown 立刻变 True
# task.submit_attempts 仍然 = 1
```

`submit_attempts` 的唯一清零点是 `clear_submit_attempt()`，而它**只在 `provider.submit()` 抛异常时**被调用。提交成功后（`mark_submitted`）该字段保持 1，此后只要有任何一个任务失败，重试就会踩中：

**实测**（起真 worker、真队列，把任务置成 `handle_poll` 的失败现场：`external_id` 在、`submit_attempts=1`）：

```
重试前: status=failed  error='生成超时，请重试'
POST /retry -> 200  "已重新排队"          ← 用户以为重试成功了
重试后: status=failed  error='提交结果未知：进程在供应商受理后中断，
                              为避免重复计费未自动重提，请核对后手动重试'
```

三个连带后果：

1. **自然失败路径全部命中**：`handle_poll` 的超时（`polls >= max_polls`）、供应商返回 `FAILED`、`result.retryable` 用尽——这三条都保留 `external_id` 且 `submit_attempts >= 1`。其中超时那条的错误文案写的是「生成超时，**请重试**」，等于把用户直接引到陷阱里。
2. **启动恢复的任务同样命中**：`resume_unfinished_tasks` 的 `unknown` 分支把记录标失败并写入「请核对后手动重试」，但那个「手动重试」不存在——重试会被同一条规则再挡一次。
3. **唯一出路是改库**：`submit_attempts` 对用户完全不可见（`TaskOut` 里没有这个字段），也没有对账接口。一旦落到这个状态，这条任务就永久废了。

**根因**：`submit_attempts` 被当成「计数器」在用，但它实际表达的是**三态语义**——「没提交过 / 正在提交（结果未知） / 已提交成功」。用 `int` 表达三态，就只能靠 `external_id` 是否为空来反推，而 `retry_task` 主动清空 `external_id` 正好破坏了这个推断。

**建议**（按推荐度排序）：

1. **换成语义化状态列**：`submit_state: idle | submitting | submitted | unknown`。`retry_task` 里显式置回 `idle`（人工重试是明确的「我要再来一次」，风险由用户承担），`mark_submitted` 置 `submitted`，崩溃恢复时只有 `submitting` 才进 `unknown`。判断不再依赖 `external_id` 是否为空。
2. 最小改动：`retry_task` 里加 `task.submit_attempts = 0`，并在 `mark_submitted` 里也归零。代价是丢掉「这条记录曾提交过几次」的信息，对账时有损。
3. 不论选哪种，**补一条测试**：`提交成功 → 轮询失败 → 重试 → 必须真的重新提交`。这条测试能同时挡住 R3-01 和后续任何对状态机的改动。

**验收**：上表那个现场点重试，任务应重新进入 `queued → running`，而不是立刻 `failed`。

---

## 三、🟡 建议修复

### 🟡 R3-02 提交超时被当作「明确失败」，重复计费窗口仍在

**位置**：`api/src/generation/runner.py:128-142`

```python
try:
    await store.mark_submitting(record_id)   # 意图标记 +1，已 commit
    await session.commit()
    external_id = await provider.submit(request)   # ← 这里超时/断连
except Exception as exc:
    await store.clear_submit_attempt(record_id)    # ← 无条件清零
```

`except Exception` 把三类性质完全不同的失败混成一类：

| 异常 | 供应商是否可能已受理 | 清零标记是否正确 |
|---|---|---|
| `BadRequest`（4xx 明确拒绝） | 否 | ✅ 正确 |
| `ConnectError`（连不上） | 否 | ✅ 正确 |
| `ReadTimeout` / `WriteTimeout` / 连接被重置 | **是**（请求已发出） | ❌ 会重复计费 |

R2-02 用 `submit_attempts` 封住了「进程崩溃」这个窗口，但**网络超时是同一个窗口的另一种形态**，而且发生频率远高于崩溃。清零后 worker 会按 `_retryable` 判定为可重试（非 `AppError` → `True`），下一轮 `provider.submit` 再发一次。

**建议**：

- 只对「确定没送到」的情况清零：`AppError`（4xx）、`ConnectError`。超时与写失败保留标记，走 `submit_unknown` 的失败分支等人核对。
- 更彻底的做法是让幂等键真正生效：`GenerationRequest.request_id` 已经存在（`infra/ai/base.py:47`），传给供应商的 `request_id` / `client_token` 字段。支持幂等键的厂商（火山、可灵多数字号都支持）这一整类问题就消失了。这才是把「不能重复计费」从业务约束变成协议保证。
- 短期至少要有监控：`submit_unknown` 命中时计数 + 告警，别让它在账单上静默出现。

**说明**：这条我按 🟡 列，但按这个仓库自己的标准（多处注释把「重复计费」列为最贵的一类 bug），建议当成 🔴 排期。

---

### 🟡 R3-03 「全部任务」对无权限用户静默降级，下拉在选择说谎

**位置**：`api/src/modules/task/repository.py:36-41`、`studio/src/features/task-filter/ui/TaskToolbar.tsx:4-8`

后端现在的降级策略很合理——缺 `task:read:all` 时 `all` / `team` 按本人过滤，不会 403（保证下拉永远可用）。**实测确认有效**：

```
仅 task:read   scope=all : total=0   authors=[]      ← 降级成功
仅 task:read   scope=team: total=0   authors=[]
带 read:all    scope=all : total=12  authors=['老大']
```

但前端**并不知道自己被降级了**：下拉里仍然显示「全部任务」，选中后列出的是自己的任务。用户会以为「系统里只有我这几条」，或者以为权限丢了——这是「静默改变语义」，比直接 403 更难排查。

**建议**：

- `TaskListOut` 加一个 `canReadAll: bool`（或 `appliedScope`，返回**实际生效**的范围），前端据此隐藏/禁用「团队」「全部」两项，或在选中时提示「当前账号仅可见本人任务」。
- 顺带解决另一个口径问题：`GET /tasks/stats` 固定按 `MINE` 统计（`service.py:76-78`），而列表的 `stats` 跟随 `scope`。管理员看「全部」时，侧边栏角标和列表头部的数字会对不上。既然 `canReadAll` 要加，不如让 `/tasks/stats` 也接受 `scope`，前端传同一个值。

---

## 四、💭 细节

1. **`permissions.py` 的常量化只做了一半**（`modules/task/permissions.py` vs `dependencies.py:43-46`）：文件头写「单一常量，避免权限码在业务代码里散落成字符串」，但 `PERM_READ` / `PERM_CREATE` / `PERM_UPDATE` / `PERM_DELETE` **没有任何引用**，`dependencies.py` 里仍是 `require("task:read")` 这样的裸字符串。只有 `PERM_READ_ALL` 被真正用了。建议 `dependencies.py` 改成 `require(PERM_READ)`，否则这个文件的存在反而会让人以为改了常量就生效。
2. **`mark_submitted` 不复位 `submit_attempts`**：这是 R3-01 的直接根因，单独修 `retry_task` 也能绕开，但状态机语义仍然含混（「提交成功」和「提交过但结果未知」用同一个计数表达）。
3. **`resume` 把 `polls` 归零**（`runner.py:73-85`）：注释里已经承认并解释了这个取舍（重启是异常事件、供应商侧有自己的超时），我认同不为此持久化计数。只是「轮询预算按进程而非按墙钟」这件事最好也写进 `EXTENDING.md`，否则接手的人会以为是 bug。
4. **`_can_access` 与 `scope` 的语义重复**：详情/删除走 `task:read:all`，列表走同一个权限点，已经一致了。但 `TEAM` 分支目前恒等于 `ALL`（`repository.py:42` 注释说「团队概念接入后替换」），前端却已经暴露了「团队任务」选项——建议在真正实现前先把它从下拉里拿掉，或者标注「即将支持」。

---

## 五、值得保留的做法（这轮新增）

- **`params.py` 的设计**：校验规则全部来自 `spec.param_schema`，文件里没有一行写死时长上限或比例选项。错误信息给出「可用模型列表」和 `details.availableModels`，前端拿到的是「该怎么改」而不是「参数有误」。这是把「目录」从展示层提升成了约束层——R2-03 建议的就是这个，实现得比我说的更好。
- **`submit_attempts` 的思路**：在调用供应商**之前**落一次意图，commit 完再发请求。用「先写后发」换一个可判定的崩溃窗口，这是处理「不可回滚的外部副作用」的标准姿势，方向完全正确（只是三态用 int 表达，见 R3-01）。
- **`_schedule_poll` 的双层兜底**：入队失败重试一次，再失败就 `_fail_orphan` 用新会话标失败。断链不再静默，`_fail_orphan` 自己失败时也记日志并说明「只能等重启恢复」。
- **`test_poll_keeps_chain_alive_when_store_raises`**：把 R2-07 的断链场景写成了回归测试，而不是改完就完。
- **架构测试继续生效**：新增 `task:read:all` 后 `test_permission_codes_are_namespaced_by_module` 自动覆盖，没有出现「加了权限点忘了前缀」。

---

## 六、建议顺序

| 顺序 | 事项 | 预估 |
|---|---|---|
| 1 | R3-01 重试路径（`retry_task` 复位标记 + 一条「提交成功→失败→重试」的测试） | 30 min |
| 2 | R3-02 异常分类（超时不清零）+ `request_id` 作幂等键传给供应商 | 1.5 h |
| 3 | R3-03 `canReadAll` 标志 + `/tasks/stats` 接受 scope | 1 h |
| 4 | R3-04 常量收口 | 10 min |
| 5 | 看门狗（R2-07 的第二条建议，仍未做）：扫 `running AND updated_at < now - N` | 1 h |

R3-01 和 R3-02 建议同一轮改完：它们都在 `submit_attempts` 这一处状态上，语义化状态列（`idle/submitting/submitted/unknown`）能一次解决两个方向的问题。

---

## 七、验证方式（可复现）

```bash
# R3-01：重试被锁死（起真 worker + 真队列）
创建任务 → 置 status=failed, external_id=<echo-...>, submit_attempts=1
POST /api/v1/tasks/{id}/retry   → 200 "已重新排队"
GET  /api/v1/tasks/{id}         → failed / "提交结果未知：…请核对后手动重试"

# R3-03：降级生效但前端不知情
仅 task:read  scope=all  → total=0（降级成功）
带 read:all   scope=all  → total=12

# 参数校验（R2-03 已修，确认）
model=Vidu Q1        → 400 未知模型 … 可用模型: kling-2.1（可灵 2.1）, seedance-2.5（Seedance 2.5）
durationSec=30       → 400 时长 30s 超出模型 Seedance 2.5 的支持范围（5-12s）
aspectRatio=4:3      → 422
```

基线：`pytest 81 passed`、`ruff` clean、`mypy` 94 files clean、`tsc -b` clean。
