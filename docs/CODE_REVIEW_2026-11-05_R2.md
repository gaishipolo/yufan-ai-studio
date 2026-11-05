# 代码评审报告 R2 · 复审

> 复审时间：2026-11-05 12:35 ｜ 范围：12:03–12:12 那轮修复改动 + 上一轮未走到的路径
> 关联：[`CODE_REVIEW_2026-11-05.md`](./CODE_REVIEW_2026-11-05.md)（首轮）｜ [`CODE_STYLE.md`](./CODE_STYLE.md) ｜ [`REVIEW_CHECKLIST.md`](./REVIEW_CHECKLIST.md)

---

## 一、复审结论

首轮 4 个 🔴 里，**B-02 / B-03 / B-04 修得干净**，B-01 修了 `mine` 分支但**漏了 `all` 分支**，且新写的回归测试把这个漏洞**当成期望值断言了下来**。

新增代码（`catalog` 模块 + 模型目录）方向是对的，但只做了一半：目录能读，创建入口不校验，前端也只改了一个入口。

| 编号 | 级别 | 结论 | 一句话 |
|---|---|---|---|
| R2-01 | 🔴 | 新发现（B-01 残留） | `scope=all` 对任何持 `task:read` 的用户返回全站任务，且被测试固化 |
| R2-02 | 🔴 | 新发现（B-03 残留缺口） | 提交成功但落库失败 → 重试再提交一次，双份计费 |
| R2-03 | 🟡 | 新发现（S-04 半成品） | 创建任务完全不校验模型目录，非法参数一律 201 |
| R2-04 | 🟡 | 新发现 | `model` 字段混装 id 与 label，历史数据无法映射 |
| R2-05 | 🟡 | 新发现（S-04 半成品） | `StudioPage` 仍硬编码模型/时长，两个创建入口行为不一致 |
| R2-06 | 🟡 | 新发现 | `referenceAssetIds` / `stylePreset` 收了就丢，无回显无报错 |
| R2-07 | 🟡 | 新发现 | 轮询 job 重试 1 次即被丢弃，任务永久 `running`，无看门狗 |
| R2-08 | 🟡 | 新发现 | 非瞬时错误（未知模型/参数错）也退避重试 3 次 |
| R2-09 | 🟡 | 新发现 | 授权口径三处不一致：列表按 scope、详情按 author+硬编码 `admin` |
| R2-10 | 💭 | 细节 | `count_by_status` 全行子查询、模型别名冲突静默、前端目录兜底清单无守护 |

### 质量基线（实测）

| 检查项 | 结果 |
|---|---|
| `ruff check src tests` | ✅ All checks passed |
| `pytest -q` | ✅ 69 passed（1 warning：starlette testclient 弃用提示） |
| `tsc -b`（studio） | ✅ clean |
| 前端 ESLint / 测试 | ❌ 仍缺（首轮 S-09，未动） |
| 模块 / 权限点 | 8 个模块 / 13 个权限点，启动日志可核 |

---

## 二、🔴 必须修复

### 🔴 R2-01 `scope=all` 仍是全量泄漏，且测试把漏洞写成了断言

**位置**：`api/src/modules/task/repository.py:28-33`
**关联**：`api/tests/test_task_api.py:124-126`、`studio/src/features/task-filter/ui/TaskToolbar.tsx:4-8`

```python
scope = TaskScope(query.scope)
if scope is TaskScope.MINE:
    stmt = stmt.where(VideoTask.author_id == actor.id)
elif scope is TaskScope.TEAM:
    stmt = stmt.where(VideoTask.author_id == actor.id)
# scope is ALL → 不加任何过滤
```

首轮修的是「`is` 比较失效」，`mine` 分支现在正确了。但 `ALL` 分支**从来没有加过过滤条件，也没有 admin 判断**——它等价于「对全部用户开放」。

**实测**（bob / carol 的 token 只带 `task:read`，不带 `*`）：

```
bob   scope=mine : total=0    authors=[]
bob   scope=all  : total=12   authors=['老大']     ← 拿到别人的全部任务
carol scope=all  : total=12   authors=['老大']
bob   详情 u_001 : HTTP 403 无权查看他人的任务      ← 详情拦住了，列表没拦住
```

三个连带问题：

1. **前端是活路径**：`TaskToolbar` 明确提供「全部任务」选项，用户点一下就能看到别人所有任务，`prompt` 字段也一并返回（`TaskOut.prompt`）。
2. **测试把漏框死了**：`test_scope_mine_isolates_authors` 里断言
   ```python
   everyone = ...params={"scope": "all"}...
   assert everyone["total"] > mine["total"]
   assert "u_001" in {item["authorId"] for item in everyone["items"]}
   ```
   这两行等于「bob 必须能看到 u_001 的任务」。下次有人修这个洞，测试会先红。**修漏洞必须先改这两行断言**。
3. **口径分裂**：同一个用户在列表能看到所有任务，点进详情却 403；`stats()` 又只统计 `MINE`（`service.py:70-72`），侧边栏数字和列表条数对不上。

**建议**（按落地成本排序）：

- 最小改动：`ALL` 分支要求 `task:read:all` 权限点，没有则降级为 `author_id == actor.id`；`TEAM` 分支同理（当前它与 `mine` 完全等价，是半成品）。
- 顺带把 `get_task` 的 `"admin" not in actor.roles` 换成权限点判断，让「能不能看别人的」只有一个开关（见 R2-09）。
- 测试改成：bob 查 `scope=all` → 要么 403，要么结果集只含自己；再加一条「带 `task:read:all` 才能看到 u_001」。

**验收**：`task:read` 单权限用户请求 `scope=all`，返回的 `items` 中 `authorId` 集合 ⊆ `{自己}`。

---

### 🔴 R2-02 「提交成功 → 落库失败」窗口仍会重复提交、重复计费

**位置**：`api/src/generation/runner.py:100-121`

```python
external_id = await provider.submit(request)     # ← 钱在这里已经花掉
await store.mark_submitted(record_id, external_id)
await session.commit()                           # ← 崩在这之前，external_id 就丢了
```

B-03 的修复解决的是「进程重启后按 `external_id` 分流」，这个逻辑依赖 **`external_id` 已经写进库**。而上面这个窗口（供应商已受理、数据库还没提交）里，库里 `external_id` 为空，重启后 `resume_unfinished_tasks` 会判定为「提交前中断」→ `resubmit` → **再提交一次，两份账单、两个产物**。

`record.submitted` 的幂等注释（第 94 行）写的正是「上次提交其实成功了，只是没来得及写库」——但这行代码只在**库里已有 external_id** 时才生效，恰恰覆盖不了这种情况。

**建议**：

1. 把 `request_id` 当供应商侧幂等键传下去（`GenerationRequest.request_id` 已经存在，但目前没有任何 provider 用它——`grep request_id src/infra` 只有一个字段定义）。支持幂等键的厂商直接解决；不支持的厂商见下条。
2. 提交前先落一条 `submitting` 标记（或独立的 `submission_attempt` 表，带自增 attempt 号），重启时对「有提交中标记但无 external_id」的记录**不自动重提**，转为人工/定时对账：按 `prompt + 时间窗` 去供应商侧查一次。
3. 至少先加监控：这类记录命中时打 `WARN` 并计数，别让它在账单上静默出现。

**验收**：模拟 `mark_submitted` 抛错 → 重启 → 断言不会二次调用 `provider.submit`（可用 mock provider 计数）。

---

## 三、🟡 建议修复

### 🟡 R2-03 模型目录建好了，创建入口却完全不校验它

**位置**：`api/src/modules/task/service.py:81-105`、`api/src/modules/task/schema.py:72-73`

`CatalogService` 提供了 `paramSchema`（含 `durationSec.min/max`），但 `create_task` 从头到尾没碰过它。`CreateTaskIn.model` 只是个 `str`，`duration_sec` 只受 `ge=1, le=60` 约束。

**实测（4 条全部 201）**：

```
model='Vidu Q1'      dur=30 → 201 任务已创建   ← 模型不存在 + 超上限
model='Seedance 2.0' dur=15 → 201 任务已创建   ← 模型不存在
model='Seedance 2.5' dur=30 → 201 任务已创建   ← 超目录上限（max=12）
model='不存在的模型'  dur=5  → 201 任务已创建
```

为什么现在看不出来：`provider_for()` 在「只有一个供应商」时**无条件兜底**到 echo（`infra/ai/router.py:82-84`），echo 又不校验参数。一旦 `AI_PROVIDERS=echo,volcengine`，这些请求会在 **worker 里**抛 `BadRequest`，重试 3 次后任务失败——而用户早在 201 时就被告知「任务已创建」。

**建议**：在 `create_task` 里先 `spec = router.spec_for(payload.model)`，拿不到直接 `BadRequest`；再按 `spec.param_schema["durationSec"]` 校验 `duration_sec`。默认值也别再硬编码 `"Seedance 2.5"`，用 `router.default_model(Capability.VIDEO)`。

**验收**：上表 4 条全部 422/400，且错误信息带上可用模型列表。

---

### 🟡 R2-04 `model` 字段混装 id 与 label，历史数据不可映射

**位置**：`api/src/modules/task/service.py:91`（直接存 `payload.model`）、`studio/src/features/create-task/CreateTaskModal.tsx:74`（提交的是 `label`）

同一个字段，实测会存进三种形态：

```
POST {"model": "seedance-2.5"}        → 库里 "seedance-2.5"
CreateTaskModal 提交 currentModel.label → 库里 "可灵 2.1"
StudioPage 提交 MODEL_OPTIONS[0].value → 库里 "Seedance 2.0"（后端不存在）
```

后果：按模型聚合/统计做不了；供应商改 label 后老数据没法映射；`/models` 返回的 `id` 失去意义（前端拿 id 对比 `task.model` 永远不相等）。

**建议**：入库只存 `spec.id`，`label` 在出参时由目录解析（或直接返回 `modelId` + `modelLabel` 两个字段）。`CreateTaskModal` 改提交 `currentModel?.id`。迁移时按 `ModelRouter.resolve()` 把历史 label 归一（`router.resolve` 已支持按 label 反查）。

**验收**：新老数据 `model` 全部是目录里存在的 id；`task.model` 与 `/models` 的 `id` 可 join。

---

### 🟡 R2-05 `StudioPage` 仍硬编码模型与时长，两个创建入口不一致

**位置**：`studio/src/pages/studio/StudioPage.tsx:11-18`

```tsx
const MODEL_OPTIONS = [
  { value: 'Seedance 2.5', ... },
  { value: 'Seedance 2.0', ... },   // ← 后端没有
  { value: '可灵 2.1', ... },
  { value: 'Vidu Q1', ... },        // ← 后端没有
];
const DURATION_OPTIONS = ['5', '10', '15', '30'] ...
```

`CreateTaskModal` 已经改成 `useModels('video')` + `durationOptionsFor()`，但首页这个更主要的入口没改。S-04 的目标是「新增模型前端不用发版」，现在只完成了一半，而且**两个入口对同一个任务给出不同的合法参数集**。

**建议**：`StudioPage` 同样改用 `useModels` / `durationOptionsFor`；`RATIOS` 也可以从 `paramSchema.aspectRatio.options` 生成（当前两处都还是常量，与「schema 驱动」的说法不符）。

**验收**：全局 `grep "Seedance\|Vidu"` 只在 mock 与测试里出现。

---

### 🟡 R2-06 契约字段收了就丢：`referenceAssetIds` / `stylePreset`

**位置**：`api/src/modules/task/schema.py:75-76`（唯一出现处，service 从未读取）

```
POST {"prompt":"带参考图","referenceAssetIds":["a1","a2"],"stylePreset":"电影感"}
→ 201，返回体里没有 referenceAssetIds，库里也没存
```

前端目前是把风格拼进 prompt 字符串（`StudioPage.tsx:51` `\`${prompt}，${style}风格\``），所以字段一直是空的——但契约里声明了、Swagger 上有、类型定义里有，客户端一旦使用就会「以为生效了」。

**建议**：要么接上（`VideoTask` 加 `style_preset` / 关联表），要么从 `CreateTaskIn` 删掉，别留「看起来支持」的字段。中间态最贵。

---

### 🟡 R2-07 轮询链断一次就永久 `running`，没有看门狗

**位置**：`api/src/generation/runner.py:232-243`（`max_attempts=1`）、`runner.py:166-176`（只捕获 `provider.poll` 的异常）、`infra/queue/memory.py:77-78`

`handle_poll` 只把 `provider.poll()` 包在 try 里。如果 `store.load` / `mark_progress` / `session.commit` 抛错（DB 抖动、锁等待），异常冒到 worker → `nack` → 因为是 `max_attempts=1`，**第一次 nack 重投、第二次直接丢弃**（`if not requeue or job.exhausted: return`）。

丢弃后没有任何补偿：任务停在 `running`，队列里没有它的 job，前端一直转圈，只有进程重启才会被 `resume_unfinished_tasks` 捞回来。

**建议**：

- `next_delay` 重投失败时不要依赖 worker 重试，在 `handle_poll` 内自己兜一层：`_schedule_poll` 失败则记 `WARN` + 缩短间隔重试。
- 加一个轻量看门狗：定期扫 `status=running AND updated_at < now - N`（N 取 `generation_timeout_sec`），重新 `resume` 或标失败。这个查询很便宜，且能把「所有断链场景」一次兜住。

**验收**：mock `store.mark_progress` 抛错一次，任务最终仍能到终态（而不是永久 `running`）。

---

### 🟡 R2-08 非瞬时错误也被退避重试 3 次

**位置**：`api/src/workers.py:90-94`、`api/src/generation/runner.py:104-105`

`except Exception` 一律 `nack`，`handle_submit` 也只看 `attempts`，不看异常类型。未知模型、参数非法这类 4xx 是**永久错误**，重试只会：

- 多等 2s + 4s 才把任务标失败（用户感知为「卡住」）；
- 真实供应商场景下可能被计费/限流；
- 最后一次的错误信息覆盖前两次，排查时看不到根因（目前是取最后一次 `str(exc)`）。

**建议**：定义 `RetryableError` / `PermanentError`（或用 `AppError.retryable` 属性），`handle_submit` 里 `if isinstance(exc, PermanentError): 直接 mark_failed`。这与 R2-03 配合——参数错误在入口就挡住后，这里主要兜供应商侧错误。

---

### 🟡 R2-09 授权口径三处不一致，且硬编码角色名

**位置**：`repository.py:28-33`（列表）、`service.py:74-78`（详情）、`service.py:149-157`（删除）

| 动作 | 判定依据 | 问题 |
|---|---|---|
| 列表 | `scope` 参数，`all` 不过滤 | 全量泄漏（R2-01） |
| 详情 | `task.author_id != actor.id and "admin" not in actor.roles` | 角色名硬编码，绕开 `PermissionSpec` 体系 |
| 删除 | 同上 | 同上 |

一个只被授予 `task:read:all` 的运营角色，永远看不到详情（403），却能在列表里看到全部——这是首轮 S-07「权限体系只落地四分之一」的延续：权限点补上了，但**判定逻辑还散落在业务代码里**。

**建议**：把「能否看他人数据」收敛成一个权限点（`task:read:all` / `task:manage:all`），列表、详情、删除共用同一个判定；`"admin"` 这类角色名只出现在种子数据里。架构测试 `test_permissions_are_declared_by_owners` 已经能守住「声明」，可以再加一条「路由不出现硬编码角色名」的 grep 测试。

---

## 四、💭 细节

1. **`count_by_status` 的聚合方式**（`repository.py:65-81`）：`stmt.subquery()` 会把所有列都塞进子查询再 `group by status`。数据量大时是全表宽行扫。直接 `select(VideoTask.status, func.count()).where(<同样的过滤>).group_by(...)` 更省，但需要把过滤条件从 `Select` 里拆出来——可以用 `stmt.whereclause`。
2. **模型别名冲突静默**（`infra/ai/router.py:56`）：`_alias.setdefault(spec.label, spec.id)`。两个供应商如果都叫「Seedance 2.5」，后者永远解析到前者，且没有任何提示。建议 alias 冲突时抛 `RegistryError`（和 `_models` 重复 id 的处理保持一致）。
3. **前端兜底清单无守护**（`studio/src/entities/model/api/model.queries.ts:15-40`）：`FALLBACK_MODELS` 目前和 echo 的声明一致，但它是**又一份手抄的目录**——正是 S-04 想消灭的东西。接口 500 时会静默展示这份清单并允许提交（把「后端挂了」变成「提交后 400」）。建议失败时禁用提交并提示，或至少给这份清单加一条与 mock 对齐的测试。
4. **`resume` 会把 `polls` 归零**（`worker.py:67-71` → `runner.resume(polls=0)`）：每次重启都重置轮询预算，反复重启的任务可以超过 `generation_timeout_sec` 一直活着。若在意，把已轮询次数持久化到记录上。

---

## 五、值得保留的做法（这轮新增的）

- **`tests/test_architecture.py`**：用 AST 守依赖方向、用发现结果守「每个模块必须导出 ModuleSpec / 必须有权限点 / 权限码必须带模块前缀」、用子类遍历守错误码与 HTTP 状态一致。这是**把架构约定变成 CI 能拦的东西**，比写在文档里强一个量级。建议继续加：`R2-09` 的「路由无硬编码角色名」、`R2-04` 的「model 必须是目录内 id」。
- **`config.py` 的生产守卫**：把「照抄 .env.example 上线」这种剧本挡在启动期，而不是运行期，判断非常对。
- **`storage/local.py` 的三层校验**：正则 + `..` 段 + `is_relative_to`，并且注释写清了为什么不能用 `startswith`。
- **`worker.py` 的重启分流**：注释直接点明「这一点直接关系到钱」，代码按 `external_id` 分流——这类注释比 JSDoc 有用得多。

---

## 六、建议的修复顺序

| 顺序 | 事项 | 预估 | 理由 |
|---|---|---|---|
| 1 | R2-01 + 改掉固化漏洞的两行断言 | 30 min | 已在泄漏真实数据，且测试会挡住修复 |
| 2 | R2-03 入口校验（顺便解决 R2-08 的主要来源） | 1 h | 接真实供应商前必须做，否则错误都发生在异步侧 |
| 3 | R2-04 + R2-05 统一 model 用 id、StudioPage 走目录 | 1.5 h | 一起改，避免两份数据形态继续扩散 |
| 4 | R2-07 看门狗 | 1 h | 生产必踩，且很便宜 |
| 5 | R2-02 幂等键 / 对账 | 2 h+ | 需要和供应商能力对齐，先加监控 |
| 6 | R2-06 / R2-09 | 按产品节奏 | 契约与授权模型收敛 |

---

## 七、本次验证方式（可复现）

所有结论都跑过临时库（`DB_URL` 指向 tmp、`WORKER_ENABLED=false`、`AI_PROVIDER=echo`），未改动任何业务代码：

```bash
# 1) 数据隔离
GET /api/v1/tasks?scope=all   （token 只含 task:read）→ total=12, authors=['老大']
GET /api/v1/tasks/{id}        （同上）              → 403

# 2) 创建校验
POST /api/v1/tasks {"model":"Vidu Q1","durationSec":30} → 201
POST /api/v1/tasks {"model":"不存在的模型","durationSec":5} → 201

# 3) 契约字段
POST /api/v1/tasks {"prompt":"x","referenceAssetIds":["a1"],"stylePreset":"电影感"} → 201，字段未回显

# 4) 默认值
POST /api/v1/tasks {"prompt":"默认"} → model="Seedance 2.5"(label), durationSec=15（目录上限 12）
```

基线：`pytest 69 passed`、`ruff All checks passed`、`tsc -b` clean。

---

## 八、复核结论（当日 12:45–12:55，以代码与实跑结果为准）

我逐条对着代码核了一遍，能跑的都跑了。**9 条属实或部分属实，没有凭空捏造的条目**，
但有 4 条的性质/成因与原文描述不同，下面单独标注——这几处正是「没有会话上下文」造成的偏差。

### 8.1 逐条结论

| 编号 | 结论 | 复核方式与证据 | 处理 |
|---|---|---|---|
| R2-01 | ✅ 属实 | 自签只带 `task:read` 的令牌：`scope=mine → total=0`，`scope=all → total=13, authors=['老大']`；同一令牌取他人详情 → 403。`TaskOut` 含 `prompt` | 已修：ALL/TEAM 统一要求 `task:read:all`，缺失时降级为本人；改掉固化漏洞的两行断言 |
| R2-02 | ✅ 属实，且**比原文更严重** | 不止「重启」：`mark_submitted` 或 `commit` 抛错时异常冒到 worker → nack → 重试时库里仍无 `external_id` → **同一进程内**就重复提交 | 已修：调供应商前落 `submit_attempts` 意图；重启时「有意图无 external_id」直接置失败等人核对，绝不自动重提 |
| R2-03 | ✅ 属实 | 4 条非法请求实测全部 201：`Vidu Q1/30s`、`不存在的模型/5s`、`Seedance 2.5/30s`（超上限 12） | 已修：入口按 `paramSchema` 校验模型/时长/比例，错误信息直接给可用值 |
| R2-04 | ✅ 属实 | 弹窗提交 `label`、首页提交硬编码值、curl 提交 id → 库里 `model` 三种形态并存（实测 `Seedance 2.5`/`Kling 1.6`/`Vidu Q1`） | 已修：写库归一成 `model`（可读名）+ `model_id`（目录 id），出参补 `modelId`；历史行读取时按 label 反查，不做数据迁移 |
| R2-05 | ✅ 属实 | `StudioPage` 的 4 个模型里两个后端不存在；默认 15s 超目录上限 12 | 已修：模型/时长/比例全部来自 `useModels` + `durationOptionsFor` + `aspectOptionsFor` |
| R2-06 | ✅ 属实（性质是「契约先行、实现没跟上」） | `POST` 带 `referenceAssetIds/stylePreset` → 201 但回显为空、库里没有 | 已修：两列落库 + 出参回显；`stylePreset` 进 `GenerationRecord.params`，供应商接入时直接用 |
| R2-07 | ✅ 属实 | `handle_poll` 只把 `provider.poll` 包在 try 里，`mark_progress`/`commit` 抛错会冒到 worker；poll job `max_attempts=1`，第二次失败即丢弃 | 已修（按不同思路）：整个轮询体兜住异常并继续下一跳；入队失败重试一次，仍失败就把任务显式置失败 |
| R2-08 | ✅ 属实 | `except Exception` 一律 nack，`provider_for` 也在 try 之外 | 已修：`AppError.retryable` 按状态码判定，4xx 直接失败；`provider_for` 移入同一 try |
| R2-09 | ✅ 属实 | `get_task` / `delete_tasks` 用 `"admin" in actor.roles`，与 `PermissionSpec` 体系并存 | 已修：与 R2-01 合并为 `_can_access()` 单一判定 |
| R2-10 | ✅ 全部属实（4 条细节） | 子查询、别名静默、兜底清单、`resume` 重置 polls 均可在代码中定位 | 见 8.3：修 2 条，2 条保留并写明理由 |

### 8.2 需要修正原文判断的四处

1. **R2-02 的触发条件不止重启**。原文把它写成「进程重启 + 窗口期崩溃」，实际同一进程内
   一次 `nack` 重试就够了：`provider.submit` 成功 → `mark_submitted`/`commit` 抛错 →
   worker 重投 → `record.submitted` 仍为 False → 再提交。修复必须挡在「重试」这一层，
   光靠重启分流不够（原来的 `record.submitted` 幂等注释也覆盖不到这个窗口）。

2. **R2-01 的性质不是「忘了加过滤」，而是「产品语义未定 + 判定口径分裂」**。
   `scope` 在原型里是筛选器（我的 / 团队 / 全部），当时全站只有一个人在用，
   ALL 没过滤是「还没到那一步」；真正的问题是与详情页的 403 自相矛盾，
   且一旦接入多用户就是越权。所以修复选了「权限点 + 安全降级」而不是 403：
   前端下拉在任何角色下都还能用，不会点一下就报错。

3. **R2-07 的「定时看门狗」不是首选解**。看门狗是兜底，不是修复：断链的根因是
   handler 把 DB 异常当成了致命错误、而 poll job 又只有一次重试。先把链路改成
   「单跳失败不影响下一跳」，剩下的只有进程崩溃——而进程崩溃已经被启动时的
   `resume_unfinished_tasks` 覆盖。所以本轮不加定时扫描任务（多一个后台循环、
   多一处与并发相关的不确定性），只在链真的断掉时显式置失败，不留静默的 running。

4. **R2-06 的字段不是「随手写的」**，是照着前端表单（参考图上传 + 风格预设）
   先把契约摆好了。但原文的结论我同意：**中间态最贵**，收了不落库就是「看起来支持」。
   所以选择落地而不是删字段。

### 8.3 未处理项与理由

| 项 | 处理 | 理由 |
|---|---|---|
| R2-02 的「按 prompt + 时间窗向供应商对账」 | 未做 | 需要真实供应商的查询能力（按业务 id 反查）。当前只做到「不自动重提 + 明确失败文案」，对账逻辑等接入第一个真实厂商时补，`GenerationRequest.request_id` 已经在传 |
| R2-07 定时看门狗 | 未做 | 见 8.2-3。若将来引入多副本 / Redis 队列，再补一个「扫描 N 分钟未更新的 running」的定时任务 |
| R2-10.1 `count_by_status` | 已修 | 直接用 `stmt.whereclause` 聚合，不再 `subquery()` 套全行 |
| R2-10.2 别名冲突 | 已修（warn 而非 raise） | 冲突时打 WARN 并保留先注册者。直接抛 `RegistryError` 会让「两个供应商恰好同名」变成启动失败，这个风险大于收益 |
| R2-10.3 前端 `FALLBACK_MODELS` | 保留 | 它的用途是「接口挂掉时选择器仍有值」+ mock 模式。真正该做的是失败时禁用提交，属于前端交互改造，建议与 ESLint/测试脚手架（S-09）一起做 |
| R2-10.4 `resume` 重置 polls | 保留 + 注释说明 | 严格按墙钟算超时要把 polls 持久化；重启本身是异常事件，且供应商侧也有自己的超时，暂不为此加列 |

### 8.4 修复后的实测对比

```
                        修复前                          修复后
bob(task:read) all   total=13 authors=['老大']      total=0（仅本人）
非法模型             201 任务已创建                   400 未知模型…可用模型: kling-2.1, seedance-2.5
时长 30s             201                             400 时长 30s 超出范围（5-12s）
不传模型/时长        Seedance 2.5 / 15s（超上限）      Seedance 2.5 / 5s（取自目录）
参考图 + 风格        201，字段丢失                    201，referenceAssetIds/stylePreset 回显
```

基线：

```
pytest        81 passed（+12：scope 权限两向、详情/删除同权限、创建校验 4 条、
                          契约字段回显、提交未知态、轮询容错、重试策略）
ruff check    All checks passed
mypy src      Success: no issues found in 94 source files
tsc -b        clean ｜ npm run build ok
alembic       新增 b7f3a1c2d4e5（空库 upgrade head 已验证）
```

真机验证：`POST` 合法任务 `queued → running → completed(100)`，`modelId=seedance-2.5`；
`scope=all` 的降级、详情 403/200、批量删除的计数都符合预期；历史 13 条数据未改动。
