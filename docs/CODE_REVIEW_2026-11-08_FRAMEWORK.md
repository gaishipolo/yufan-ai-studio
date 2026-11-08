# 框架代码全量审查 · 2026-11-08

> 范围：`api/src` 的框架层（`core/` `api/` `db/` `infra/` `generation/` `workers.py` `cli.py`）＋ 前端 `studio/src/shared/` 网络与访问层。
> 不评审业务实现（`modules/*` 的领域逻辑、`infra/<kind>/<driver>.py` 的驱动细节、studio 的 UI 组件）。
> 依据：`api/docs/EXTENDING.md`（框架约定）、`api/src/api/module.py`（模块契约）、`docs/CODE_STYLE.md`。

---

## 0. 结论

框架的分层与守卫总体是**可信的**：装配对账（声明 vs 注册）、注入口目录（fail-closed 例外写清楚了）、
错误码号段归属、限流策略豁免表、权限码启动核对，都是「漏了会红」而不是「漏了靠记」。
这批审查没有发现越权、SQL 注入、令牌伪造这一类安全问题。

但发现 **1 个会在特定配置下真实发生的运行时故障**（已修）、**3 处框架对业务模块的残留耦合与死代码**（已删）、
以及 **1 处门禁退化**（mypy 从 0 错误变成 16 个，因为没进 CI 所以无人发现）。

| 级别 | 数量 | 状态 |
|---|---|---|
| P0 运行时故障 | 1 | 已修 + 新增测试 + 反向验证 |
| P1 耦合 / 死代码 | 3 | 已修 |
| P2 注释撒谎 / 死符号 | 2 | 已修 |
| P3 门禁与约定缺口 | 5 | 记录，未改（见 §4） |

---

## 1. 门禁实测（2026-11-08）

| 检查 | 结果 | 备注 |
|---|---|---|
| `ruff check src/ tests/` | ✅ All checks passed | 与 CI 一致 |
| `pytest -q --basetemp=var/pytest-audit6` | ✅ **864 passed / 12 skipped**（128s） | 修复前基线 862，本次新增 2 条用例 |
| `mypy --platform win32 src` | ❌ **16 errors in 3 files**（220 files） | 见 §4.1：历史记录 2026-11-06 是「142 files, no issues」，重建模板库后变红 |
| `app doctor` | ✅ 24 项：失败 0、警告 0 | |
| `app modules` | ✅ 7 个注入口全部已注册；6 handler / 8 周期任务 / 33 权限点 | |
| `app export-openapi --check` | ✅ 四份生成物「无变化」 | json / API.md / schema.ts / permissions.ts |

---

## 2. 已修（P0）

### 🟥 F1 周期任务抢租约时阻塞整个事件循环

**位置**：`api/src/core/scheduler.py:189`（原写法）、`api/src/core/coordination.py:173`（`BackendLeaderElector.acquire`）。

`LeaderElector` 的两个方法都是同步的，底层是文件 `O_EXCL` 与 S3 `If-None-Match: *`。
`Scheduler.run_once` 在协程里直接 `self._elector.acquire(...)` —— `LEADER_ELECTOR=backend` 且
`ARCHIVE_DRIVER=s3` 时，这里每次要跑 1～3 次 boto3 往返（`head` / `put` / 接管路径的 `read`），
**期间整个事件循环停摆**：HTTP 请求、队列消费、其它周期任务全部排队，日志里没有任何异常。

后果的形态特别误导：表现为「服务偶尔卡一下」，而排查会先怀疑数据库或网络，
因为调用链上没有任何一处报错。这与 `core/security.py` 里对 scrypt 的处理（必须离开事件循环）
是同一条原则，只是换了个位置被违反。

**修法**：`Scheduler` 新增 `_acquire` / `_release`，用 `asyncio.to_thread` 调 elector；
`LeaderElector` 协议保持同步，并在文档里写明「实现允许阻塞、调用方负责 offload」——
改成 `async def` 反而会让下一个人在新调用点直接 `await` 而重新踩回去。

**验证**（`api/var/_verify_block.py` 的临时脚本，结论已沉淀为测试）：

```
修复后（to_thread）：最大间隔 22ms
修复前（同步调用）：最大间隔 315ms   ← 假阻塞 300ms
判据成立： True
```

新增用例 `tests/test_scheduler.py::test_leader_only_lease_call_does_not_block_the_event_loop`，
并做过反向验证（临时改回同步写法 → 用例红：`抢租约期间事件循环被按住 117ms（假阻塞 100ms）`）。

> 顺带记一个测量陷阱：ticker 用 `create_task` 之后必须**先 await 一次**让它在跑，
> 否则它的第一个时间戳取在阻塞之后，实测会得到「最大间隔 16ms」的全绿结果。

---

## 3. 已修（P1 / P2）

### 🟡 F2 框架层硬编码业务表 `image_jobs`

`api/src/app.py` 的 `_warn_stale_artifact_urls()` 用裸 SQL 查 `image_jobs`。

它辩解说是「不 import 业务模块只为数一行」，但那只是把依赖从 import 换成了**表名**：
删掉 `modules/image`，这段代码继续存在、继续静默失败（表不存在 → `except` → `return`）。
而 `api/module.py` 的 `DoctorHook` 注释已经把这类检查的归属写得很清楚 ——
「只对一个模块成立的检查」应该走 `ModuleSpec.doctor`。

而且 `modules/image/health.py::check` **已经在做同一件事**（ORM 写法、进 doctor 的 `image.urls`），
两处重复。已删除启动期那份，`app doctor` 的 `image.urls` 仍为 PASS。

### 🟡 F3 演示账号 id 写死 `u_001`，与 `DEMO_USER_ID` 脱钩

`api/src/api/deps.py` 里 `DEMO_USER` 是模块级常量 `id="u_001"`，
而 `app seed` 按 `settings.demo_user_id` 种数据（`SeedContext.demo_user_id` 同源）。
改了 `DEMO_USER_ID` 之后，匿名请求拿到的是一个库里不存在的身份：接口全 200、列表全空，
看起来像「演示数据没种上」，实际是两个人。

改为 `demo_user()` 函数，从 settings 取 id 与昵称。新增
`tests/test_identity_api.py::test_anonymous_identity_follows_demo_user_setting`（直接测构造函数，
不打 HTTP —— `/me` 会走 `resolve_principal`，账号不存在时 401，断言就变成在测查库了）。

### 🟡 F4 孤儿死代码 `modules/template/service.py`

模板库重建（`b6464ed`）后遗留的脚手架文件：`from modules.template.models import Template`
—— `Template` / `TemplateOut` 都**不存在**，全仓无人 import。任何人一旦 import 它就 `ImportError`；
mypy 也因此多报 2 条 `attr-defined`。已删除。

### 🟢 F5 `CamelORMModel` 的注释在撒谎

`api/src/core/schema.py` 原文：「从 ORM 实体读出的出参基类（**额外放开 lazy 属性访问开销**）」，
但它的 `model_config` 与父类 `CamelModel` **逐字相同**，没有多任何东西。
「读不读 lazy 关系」由查询里的 `selectinload` 决定，不是模型基类能管的。
已改为实话：一个语义标记，不是能力差异。

### 🟢 F6 `PageMeta` 死符号

`api/src/core/response.py` 的 `PageMeta` 全仓零引用（分页结果用的是 `Page`）。已删。

---

## 4. 记录但未改（P3）

### 4.1 mypy 门禁从绿变红（16 errors / 3 files）

全部集中在模板库：`pack_service.py`（`Sequence` 传进 `list` 形参，6 处）、
`replacement_service.py`（`_run_out` 的 review 参数声明成 schema 类型但传的是 ORM 实例，
4 处；`spec.get("duration")` 被推断成 `object`，2 处）、`repository.py`（`Select[T]` 与
`Select[tuple[T]]` 的赋值不兼容，2 处）。

它们都是**注解债，不是运行时 bug**（`int("300")` 也成立），但意义在于：
历史记录写着「142 files, no issues」，现在 220 files / 16 errors，没有人发现 ——
因为 mypy 不在 CI，也不在 `verify` 脚本里。建议：
1. 把 `python -m mypy --platform win32 src` 加进 CI；
2. 先把这 16 条修绿再加，否则新门禁一上线就是红的（`Sequence` → 形参改 `Sequence[...]`；
   `repository.py` 用 `Select[Any]`；`spec` 标注成 `dict[str, Any]`）。

### 4.2 `reload_settings()` 不清派生缓存

`core/config.py` 反复强调「一个进程只有一份配置」，但 `reload_settings()` 只原地刷新 Settings，
不动 `@lru_cache` 的派生值：`core/ratelimit.py::parsed_overrides`（额度覆盖）、
`get_rate_limiter`（key 前缀）、`core/coordination.py::get_leader_elector`（选主策略）。

后果是「改了环境变量，一半生效一半不生效」—— 正是该文件注释里想避免的那件事。
生产不调 `reload_settings()`，所以只在测试与热重载路径出现；每个消费方都有 `reset_*`，
但没人强制调用。建议在 `reload_settings()` 里显式清理（延迟 import 各 `reset_*`），
或在文档里把「重载配置 = 同时调这几个 reset」写成硬约定。

### 4.3 `VALIDATION_FAILED` 违反自述的编码不变量

`core/exceptions.py` 开头写着「`http_status = code // 100`」，但 `VALIDATION_FAILED = 40001`
在 `api/errors.py` 里配的是 **HTTP 422**（`422 * 100 = 42200 ≠ 40001`）。
前端只判 `code == 0`，所以无害；后端起约束作用的 `BadRequest` 子类仍是 400，也一致。
问题是这个不变量一旦有例外，下一个人就会照着算错。要改就改 `code`（40001 → 42201），
但那会动前端契约与 55 条断言，属于独立改动，不在本轮。

### 4.4 密码字段会被剥掉首尾空格

`CamelModel` 统一开了 `str_strip_whitespace=True`，`LoginIn.password` / 改密入参都继承它。
创建与校验**同样剥**，所以能登录；但用户以为密码里有空格，而实际没有 —— 静默改变用户输入。
密码这类字段应该显式不剥（单独一个 model_config，或用 `Field` 的 `json_schema_extra` 之外的手段）。

### 4.5 outbox 多实例是「并发即重复」，不是「崩溃才重复」

`db/outbox.py::dispatch_pending` 先 `select` 一批 pending，再逐条投递、最后批量置 delivered，
**没有领取 CAS**。`OUTBOX` 的注释接受「至少一次」，但实际语义更差一档：
两个实例同时在跑时，同一批事件会被各投一遍（不是崩溃窗口里的偶发，而是每轮稳定的 2×）。
要收窄需要 `UPDATE ... SET status='delivering', owner=? WHERE ... RETURNING` 那样的领取，
与「刻意不做 delivering 状态」的设计相冲突 —— 所以这里只记录，不建议改。

---

## 5. 覆盖范围（诚实清单）

**读过全文**：`core/` 的 `config` `security` `permissions` `registry` `scheduler` `coordination`
`audit` `events` `context` `serving` `response` `schema` `media` `ratelimit` `net` `exceptions`
`ports/host`；`api/` 的 `app` `errors` `middleware` `deps` `module` `discovery` `assembly`
`ports` `v1/router` `authn` `ops` `ratelimit_policy` `instrumentation`（关键段）`diagnostics`（关键段）；
`db/` 的 `session` `repository` `outbox` `backup`（关键段）；`infra/loader` `infra/archive/base`
`infra/queue/db`（关键段）；`generation/runner`；`workers.py`；`cli.py`（装配段）；
`studio/src/shared/api/http.ts`、`studio/src/shared/access/token-refresh.ts`。

**没读**：`modules/*` 的业务实现、`infra/` 各驱动实现细节、`scaffold/templates.py` 的模板正文、
`studio/src` 的 UI 与路由层。这些属于另一条线（业务正确性 / 交互），本次不下结论。

**抽查过但未单列的**：`core/metrics.py` 的自建 Registry、`api/instrumentation.py` 的
`route_template`（从右对齐替换参数位，逻辑正确）、`infra/archive/s3.py` 的条件写语义、
`db/queue` 的 fencing token（`lease_token` + CAS 领取，写法正确）。
