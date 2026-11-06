# 雨范工作台 · 代码规范

> 版本 **v2.0**（2026-11-06 按仓库现状更新，替代首日 v1.0）
> 适用：`api`（Python 3.11 / FastAPI 0.115+ / Pydantic v2 / SQLAlchemy 2.0 async）、
> `studio`（React 18.3 / TS 5.7 / Vite 5.4 / Vitest 2.1，CI 用 Node 20）
> 配套：[`REVIEW_CHECKLIST.md`](./REVIEW_CHECKLIST.md)（编号化检查项，评审评论引用它）
>
> 本次更新依据：R1–R12 后端评审、FE–FE5 前端评审，以及 `ARCHITECTURE.md`、
> `api/docs/EXTENDING.md`、`EXTENSIBILITY_GAPS.md`、`FRONTEND_EXTENSION.md` 的落地结果。
> v1.0 里「React 18 / 无 CI / 手抄 types.ts / 三份生成物 / 手写注册表」等描述均已失效。

---

## 这份文档怎么用

规范分两级，写代码前请先分清哪条能被 CI 拦住：

| 级别 | 含义 | 违反后果 |
|---|---|---|
| **硬性（MUST）** | 结构、契约、安全、事务边界、状态机 | CI 报错或评审直接打回 |
| **建议（SHOULD）** | 可读性、组织方式、性能习惯 | 评审提 🟡，不阻塞合并 |

已经被工具管住的事（缩进、引号、import 排序、行宽）本文不重复讲，交给 `ruff` / `tsc`。
本文只写**工具判断不了、但会让人踩坑**的部分；能机械化的规则已经尽量落到守卫里：

| 机械守卫 | 管什么 | 落点 |
|---|---|---|
| 契约字节比对 | 四份生成物与代码不同步就红 | `api/tests/test_openapi_contract.py` |
| 生成器合法性 | 生成物不能引用不存在的类型名（R10-01） | 同上，`test_every_endpoint_data_type_is_defined` |
| 架构矩阵 | 模块目录 / 权限点 / 注入口 / 恢复槽 / retry 出口 | `api/tests/test_architecture.py` |
| 跨组件状态机表 | 恢复 × 巡检 × retry 组合后的终态与计费凭证 | `api/tests/test_generation_state_machine.py` |
| 分层 zone | 前端反向 import、`http` 直连 | `studio/eslint.config.js` |
| 路由与权限目录 | 注册表不变量、权限码与后端逐条比对 | `studio/src/app/registry/*.test.ts`、`permission-catalog.test.ts` |

> 守卫覆盖不到的，仍然靠评审。尤其第 0 节的第 4、5 条——它们是从十几轮返工里总结出来的，
> 目前只能靠人和表驱动测试一起守。

**文档索引**（按「我要做什么」选）：

| 想做的事 | 读哪份 |
|---|---|
| 理解全仓逐文件职责 | [`ARCHITECTURE.md`](./ARCHITECTURE.md) |
| 后端加模块 / 供应商 / 生成能力 / 注入口 | [`api/docs/EXTENDING.md`](../api/docs/EXTENDING.md) |
| 前端加页面 / 插槽 / 权限 / 会话接入 | [`FRONTEND_EXTENSION.md`](./FRONTEND_EXTENSION.md) |
| 前端真实路由清单 | [`ROUTES.md`](./ROUTES.md) |
| 框架还剩哪些已知缺口 | [`EXTENSIBILITY_GAPS.md`](./EXTENSIBILITY_GAPS.md) |
| 提交前自检 | [`REVIEW_CHECKLIST.md`](./REVIEW_CHECKLIST.md) |

---

## 0. 五条底线

先记住这五条，它们覆盖了本仓库绝大多数返工。前三条是首日定下的，
后两条是 R5–R12 反复踩同一个坑之后补的。

1. **依赖只能单向流动**：`modules → generation / core / db / infra`，前端
   `pages → widgets → features → entities → shared`。反向 import 一律不允许，绕路也属于违规。
2. **契约只有一份定义**：后端 `schema.py` + `ModuleSpec.permissions` 是唯一真相；
   `app export-openapi` 产出**四份生成物**（`openapi.json`、`API.md`、`schema.ts`、`permissions.ts`）。
   前端类型与权限清单从生成物**派生**，禁止手抄、禁止手改生成物。
3. **一次业务操作 = 一次事务提交**，提交发生在 `service`，不在 `router`，也不在 `repository`。
4. **一条不变量只能有一个写点**。如果某条规则需要在两个地方各写一遍才能成立，
   它迟早只在一个地方成立。R5-03 / R6-04 / R8-01 / R10-02 / FE2-02 全是这个形状的变体：
   守卫只贴在「当时想到的那个入口」，换个入口就漏。正确做法是把不变量收进一个函数
   （如 `IdentityService._cut_access()` 统一处理停用 + 吊销会话），所有入口调它。
5. **新模块要按「模块 × 插槽」矩阵接全**。框架把恢复、重试、周期任务、审计做成可选插槽，
   于是「让功能跑起来」的都接了、「出事才用得上」的常常没人接，而全量 CI 照样绿（R11-01/02）。
   新增生成类模块时，逐格确认 `startup` / `recovery_kinds` / retry 出口 / 周期巡检 / 审计，
   **「我确实不需要」也要写成显式声明**，由架构测试兜底。

---

## 1. 工程结构与依赖方向

### 1.1 后端：垂直切片 + 可插拔框架

```
src/
├── core/          与业务无关：config / logging / context / exceptions / response / security
│   ├── ports/     跨模块查询协议（消费方在低层、答案在业务模块手里）
│   ├── audit.py   操作留痕的写入端（Callable 注入口）
│   └── scheduler.py  @periodic 周期任务 + 进程内单调度器
├── db/            数据访问基座：Base / session / 通用仓储 / audit_sink
├── infra/         可替换实现：storage / archive / queue / ai（全部面向 Protocol）
│   └── driver_config.py  驱动私有配置的归属地（前缀 = 驱动名大写）
├── generation/    生成任务框架：spec / pipeline 注册表 / store / runner / worker / recovery
├── api/           HTTP 层：deps / middleware / errors / module(ModuleSpec) / discovery / ports
├── modules/       业务：每个模块自带 models / schema / service / router（+ maintenance / worker）
├── scaffold/      新模块脚手架模板
├── openapi_export.py  四份契约生成物的唯一产出点
└── cli.py         app 命令入口（serve / seed / export-openapi / modules / new-module …）
```

单个模块内四层职责，边界要硬：

| 文件 | 可以做的事 | 绝对不做的事 |
|---|---|---|
| `models.py` | ORM 字段、索引、约束、关系 | 业务判断、`select` 查询、序列化逻辑 |
| `schema.py` | 出入参定义、字段级校验（`Field(ge=...)`） | 碰数据库、发起 IO |
| `service.py` | 业务规则、编排、事务提交、抛业务异常 | 读写 `Request`/`Response`、拼 SQL 字符串 |
| `router.py` | 声明路径与参数、调 service、包 `ApiResponse` | 任何 `if` 业务分支、任何 `commit()` |
| `worker.py` / `maintenance.py` | 消费队列、幂等重试、周期巡检 | 依赖请求上下文（`CurrentUser`） |

**判断口诀**：写在 `router` 里的 `if`，八成该下沉到 `service`；写在 `service` 里的 `select()`，
八成该上提到 `repository`。

#### 模块声明：只有 `ModuleSpec` 一个入口（MUST）

模块由 `api/discovery.py` **扫描目录自动发现**，没有手写注册表。
唯一约定是 `src/modules/<name>/__init__.py` 导出 `module: ModuleSpec`，
且 `ModuleSpec.name` 必须等于目录名。当前字段（新增字段前先读 `api/module.py`）：

| 字段 | 作用 | 漏了会怎样 |
|---|---|---|
| `name` / `router` / `tags` / `order` | 路由注册；有通配路径的模块把 `order` 调大 | 404 / 吃掉别人的静态路径 |
| `permissions` | 权限点唯一来源，进入生成物 `permissions.ts` | 前端手抄、权限码漂移（FE2-06） |
| `public` | 免登录模块（健康检查等），**默认 False** | 忘了加权限点 = 对所有已登录用户放行 |
| `startup` | 启动期恢复钩子，lifespan 内、worker 启动前调用 | 重启后 `running` 孤儿永久卡住（R11-01） |
| `recovery_kinds` | 声明本模块负责的 `generation.pipeline` kind，交给框架恢复器 | 同上；架构测试会红 |
| `seed` | 模块自带演示数据 | 演示数据全挤进 `seed.py`，变成框架债 |

**「模块 × 插槽」矩阵**（新增生成类模块时逐格确认，MUST）：

| 框架插槽 | 谁提供 | 不接的后果 |
|---|---|---|
| `pipeline` / `dispatch` | 模块 | 功能跑不起来（会立刻被发现） |
| `startup` 或 `recovery_kinds` | 模块 / `generation/recovery.py` | 重启后孤儿任务无人接管 |
| retry 出口（清零 `submit_attempts`） | 模块路由或 CLI | 「请手动重试」成空头支票（R11-02） |
| `@periodic` 巡检 | 模块 `maintenance.py` | `queued` 记录永久滞留 |
| `audit.record()` | 模块 service | 关键操作无对账凭据 |
| 注入口登记 `PORT_CATALOG` | `api/ports.py` | 架构测试红；未接上时静默丢数据 |

`tests/test_architecture.py` 已经把后半张表变成机械守卫：
`test_every_pipeline_kind_declares_a_recovery_slot`、`test_every_kind_has_a_user_retry_exit`。
**新增 kind 时不要绕过它们，也不要为了让测试过而写假声明。**

### 1.2 前端：分层单向 + 注册表驱动

```
app/         组装：providers、router（由注册表生成）、app/registry 不变量测试、SessionBootstrap
modules/     功能切片：一个功能一个目录，声明路由 / 导航 / 插槽 / 权限
pages/       路由级页面（被 module 的 load() 懒加载）
widgets/     跨页面布局块：app-shell、sidebar、top-bar、module-nav、user-menu
features/    用户动作：auth、user-security、user-roles、role-editor、create-task …
entities/    领域实体：user / session / role / task / asset / image / shot / template …
shared/      ui / api / access / registry / config / hooks / lib
             api/schema.ts、api/permissions.ts 是后端生成物（只读）
             access/ 是会话与授权的唯一出口
mocks/       仅开发期的假后端，按契约实现，生产构建动态剔除
```

- 依赖只能**向下**：`pages` 可以用 `features`，`features` 不能 import `pages`。
- `modules` 是最上层纵向切片，只能向下依赖；**不能 import `@/app/*`**（app 单向发现模块），
  **模块之间不能互相 import**（要复用就下沉）。
- 跨 `entities` 相互引用要谨慎：共性类型提到 `shared` 或从生成物派生。
- 一个实体对外只暴露三样东西：`model/types.ts`（类型）、`api/*.ts`（请求）、`ui/*`（展示组件）。
- **新增功能的标准姿势是「新增文件」，不是改骨架**：详见 `FRONTEND_EXTENSION.md`；
  路由由 `npm run gen:routes` 生成，模块骨架由 `npm run gen:module <name>` 生成。

| 想做的事 | 声明位置 |
|---|---|
| 新页面 / 新导航 / 顶部宫格 | `src/modules/<id>/module.ts` |
| 页面级权限、导航按权限隐藏 | `module.ts` 的 `permissions` |
| 按钮级权限 | `<Can permission="...">` |
| 新接口 | `entities/<id>/api/*.api.ts` + `*.queries.ts` |
| 契约类型 / 权限清单 | **从 `shared/api/schema.ts` / `permissions.ts` 派生**，不手写 |
| 结束会话 / 续期 / 当前用户 | **只走 `shared/access/`**（`endSession` / `refreshTokens` / `useSessionStore`） |
| 功能 mock | `src/modules/<id>/mock.ts`（按契约，不按「页面方便」） |
| 外壳插槽内容 | `module.ts` 的 `slots`，种类登记在 `shared/registry/types.ts` 的 `SlotName` |

---

## 2. 后端规范（Python）

### 2.1 命名

| 对象 | 规则 | 例子 |
|---|---|---|
| 模块目录 | 单数、小写 | `modules/task/`，不是 `tasks/` |
| Service 类 | `<实体>Service` | `TaskService` |
| 入参 Schema | `<动作><实体>In` | `CreateTaskIn`、`UpdateTaskIn` |
| 出参 Schema | `<实体>Out` | `TaskOut`、`TaskListOut` |
| 查询参数 | `<实体>ListQuery` / `<实体>ListParams` | `TaskListQuery` |
| service 方法 | 动词开头，命令式 | `create_task`、`retry_task`、`toggle_favorite` |
| 内部方法 | 单下划线前缀 | `_cut_access` |
| 常量 | 全大写 | `JOB_GENERATE`、`RECOVERY_BATCH` |
| 权限码 | `资源:动作[:范围]`，通配 `resource:*` / `*` | `image:retry:any` |

同一资源有多种形态时用不同 Schema 表达，不要给一个类加一堆 `Optional` 字段。
额外一条（MUST）：**响应封套的内层如果是匿名形状，生成器会内联成 `Record<…>`，
不要手改生成物去补一个不存在的类型名**（R10-01）。

### 2.2 类型注解

- 每个文件顶部 `from __future__ import annotations`。
- 所有函数**完整注解**（参数 + 返回值），包括 `-> None`。
- 禁止用 `Any` 逃逸。确实动态的地方收窄到 `object` 或定义 `Protocol`。
- 集合类型用内建泛型：`list[str]`、`dict[str, int]`；`Sequence` / `Mapping` 从 `collections.abc` 导入。

```python
# ✅
async def paginate(
    self, stmt: Select[Any], params: PageParams
) -> tuple[Sequence[ModelT], int]: ...
```

> Pydantic v2 的 `use_enum_values=True` 意味着 `is TaskScope.MINE` 这类恒等比较**永远是 False**
> （R2 的实现级缺陷）。比较枚举一律用 `==`，不要顺手写 `is`。

### 2.3 异步纪律

`async def` 内部**不允许出现同步阻塞调用**——它会卡住整个事件循环。

| 禁止 | 替代 |
|---|---|
| `requests.get()` | `httpx.AsyncClient` |
| `time.sleep()` | `asyncio.sleep()` |
| 大文件 `open().read()` | `anyio.to_thread.run_sync` / 异步库 |
| CPU 密集（scrypt、转码、大图） | 有界线程池，不在事件循环做 |
| 同步 ORM 调用 | `await session.execute()` |

**口令哈希是唯一例外路径，且必须走唯一入口（MUST）**：
`core/security.py` 的 `hash_password_async` / `verify_password_async`（内部是有界线程池
`_HASH_POOL`），同步代码不要直接调 `hash_password`。口令写入只有
`IdentityService.set_password` 一处（同时写 `password_changed_at`）。

并发保护用 `password_slot()` 闸门，并且**闸门要放在取数据库连接之前**（R7-01）、
**所有验密入口都要过**（R8-01 漏了 `change_password`、R9-01 把 `reset_password` 装在守卫之后）。
`_GATE_DEPTH` ContextVar 防嵌套，不要自己再造一套。

同一个请求内的独立 IO 用 `asyncio.gather` 并发，串行 `await` 是隐性性能债。

### 2.4 事务

- **提交点在 service**，一个方法最多一次 `commit()`。
- `repository` 只 `flush()`，不 `commit()`——它不知道业务边界。
- 读操作不需要 `commit()`。
- 副作用（入队、发消息、调第三方）放在 `commit()` **之后**；如果副作用失败会导致状态悬挂，
  必须写补偿。当前框架的补偿链是：`@periodic` 巡检捞 `queued`、`generation/recovery.py`
  启动期恢复 `running`、人工 retry 出口清 `submit_attempts`。**新模块必须接进这条链，
  不能只依赖「重启就好了」**（见 §1.1 矩阵）。
- `session.refresh()` 会丢弃同一会话里**尚未提交的改动**（R12-02）。
  需要「先清标记再写终态」时，把两步合成一次原子写，或在 `refresh` 之前 `flush` + 重新确认；
  不要在 `_guard()` 这类通用校验里悄悄刷新别人刚改过的对象。

### 2.5 错误处理与错误码

**业务代码不写 `try/except`**。可预期错误直接 `raise`，由全局处理器转成统一响应。
优先用语义化子类（`NotFound` / `Forbidden` / `InvalidState` / `RateLimited` / `UpstreamError`）。

- 错误码结构：`HTTP状态码 × 100 + slot × 10 + seq`，保证 `code // 100 == http_status`。
- 模块私有号段用 `ModuleErrors("voice", slot=3)` 申请，**不要改 `core/exceptions.py`**；
  号段分配表在 `api/docs/EXTENDING.md` §7，新增错误码必须登记进文档（契约测试会扫）。
- 前端分流用 `code // 100` 取状态段，**不要硬编码完整业务码**（如 identity 登录失败是 `40121`）。
- 需要模块私有错误码时，同时确认 `docs/API.md` 里出现了它——`app export-openapi` 会带上。

`except` 的三条禁令不变：不吞异常、不裸 `Exception` 后 `return None`、
捕获必须能真正处理并记录上下文。再加一条（MUST）：**注入口 / 审计 / 周期任务这类
「可选依赖」未接上时，可以静默跳过，但生产启动自检必须报错**——静默降级只允许发生在
明确标注的中性路径上（`ensure_audit_sink()` 就是这么做的，R11-05）。

### 2.6 接口契约

| 规则 | 说明 |
|---|---|
| 响应体 | 一律 `ApiResponse.ok(data, message)` → `{code, message, data}` |
| 字段命名 | Python 侧 snake_case，序列化自动 camelCase（`CamelModel`） |
| 时间 | 一律 `UtcDateTime`，输出 `...Z`；**禁止**直接返回 naive `datetime` |
| 分页 | 入参 `page` / `pageSize`，出参 `{items, total, page, pageSize}` |
| 资源不存在 | 404 + `NOT_FOUND`，不要返回 `200 + data:null` |
| 状态冲突 | 409 + `INVALID_STATE` |
| 未实现 | 501 + `NotImplementedFeature`，并留 `TODO(owner)` |

**新增/修改路由的合法流程（MUST，替代 v1.0 的「手写注册表 + 改 README」）**：

1. 在 `router.py` 声明，**返回类型写具体 `ApiResponse[T]`**（`data` 不要留 `dict`）。
2. 权限点在 `ModuleSpec.permissions` 登记；模块自动发现，不需要改任何注册表。
3. 跑 `cd api && app export-openapi` —— 同时产出并一起提交**四份生成物**：
   `api/docs/openapi.json`、`api/docs/API.md`、`studio/src/shared/api/schema.ts`、
   `studio/src/shared/api/permissions.ts`。`app export-openapi --check` 只比对不写盘（CI 用）。
4. 前端从生成物派生类型；如需同步文档，更新 `FRONTEND_EXTENSION.md` 的契约表与 `ROUTES.md`。

> 这四份文件**禁止手改**：`tests/test_openapi_contract.py` 做字节比对，
> 手改会红。生成器本身也要能产出合法 TS（R10-01 的教训：字节比对只能保证
> 「生成物与生成器一致」，保证不了「生成器写的是合法代码」）。

### 2.7 日志与留痕

```python
logger = get_logger(__name__)
logger.info("任务已创建并入队 id=%s author=%s", task.id, actor.id)   # 惰性格式化
```

- 禁止 `print`；禁止记录 `password`、`token`、`Authorization`、完整 `prompt`、完整手机号。
- 错误日志带 `exc_info=`；`request_id` 由 middleware 注入 context，不必手动传。
- **日志 ≠ 审计**：涉及计费、授权变更、人工重试的操作要调 `core/audit.record()`，
  日志是给排障的，审计是给对账与追责的。审计写入端未注册时 `record()` 会静默跳过，
  所以**关键凭证不能只写审计，还要留在业务记录上**（`previous_external_id` 就是这么设计的）。

### 2.8 配置

- 业务代码只通过 `core.config.settings` 读取，禁止 `os.environ.get(...)`。
- **驱动私有配置归 `infra/driver_config.py`**：每个驱动一个 `XxxSettings`，前缀 = 驱动名大写。
  不要往 `core/config.py` 里塞供应商参数（A1），也不要在 provider 里直接读全局配置。
- 新增配置项同步：`core/config.py` 或 `driver_config.py` → `api/.env.example` → README 部署清单。
  已有**双向测试**守住这两侧，拼错的键不会静默失效（D3）。
- 生产必填项用 `prod_required_fields` 声明（如 `ImageAssetSettings.allowed_hosts`），
  **不要让安全配置在 prod 走一个宽松默认值**。
- 阈值类配置不要从「全局所有驱动」取聚合值：按能力/模块过滤
  （`registered_submit_budgets(Capability.IMAGE.value)`），否则加一个无关的慢驱动就能把
  生图巡检阈值从 270 秒拖到 90 分钟（R12-03）；配置解析失败要报错，不要静默 `continue` 降级。

### 2.9 数据访问

- 禁止字符串拼接 SQL，一律参数绑定；排序字段来自枚举白名单。
- 列表接口先确认没有 N+1：关联属性用 `selectinload` / `joinedload` 或一次性聚合。
- **分页/巡检的 `LIMIT` 与 cutoff 必须下推到 SQL**，不允许「全表实例化后在 Python 里切 50 条」
  （R11-04）。排序要以主键收尾，保证同时间戳下顺序稳定（R8-06 同类）。
- 迁移用 Alembic；`app_env=prod` 时自动建表已关闭，这是刻意的。新增 migration 检查
  `down_revision` 不成环、链线性无分叉。

### 2.10 依赖注入与注入口

- 横切依赖从 `api/deps.py` 取（`SessionDep` / `ActorDep` / `PageDep` / `StorageDep`…）。
- 模块自己的 service 装配放 `modules/<name>/dependencies.py`，用 `Annotated[T, Depends(...)]`。
- 路由签名里不要出现 `Depends(get_xxx())` 内联写法。

**跨模块协作用注入口（port），不要现场开文件（MUST）**。新增注入口照 `api/ports.py`
文档里的六件套形状：`Protocol` → 模块级单例 → `register_xxx`（重复注册报错）→
`reset_xxx`（仅测试）→ `has_xxx` → `ask_xxx`（**未注册必须返回中性值，不许抛**）。
并登记进 `PORT_CATALOG`（当前三个：`principal-resolver`、`account-status`、`audit-sink`），
写明「谁该实现」与「未接上时的行为」。架构测试会扫 `src/api/*.py`、`src/core/*.py`、
`src/core/ports/*.py`，清单之外的注入口直接红。

### 2.11 注释与 TODO

- 注释写 **why**，不写 **what**。模块顶部 docstring 用一两句话说明文件定位。
- TODO 必须带 owner 和触发条件：`# TODO(zhangsan): 接真实供应商后删除 echo 实现，见 TASK-142`。
- 语言：注释、docstring、错误提示统一中文；标识符、日志字段名统一英文。
- **注释里的行为断言要与代码同向**：FE4-02 的弹窗写「会话不受影响」，
  后端 `reset_password` 实际 `revoke_all`，方向正好相反；`end-session.ts` 曾出现
  「五条路径 / 四个入口」自相矛盾。改实现时顺手读一遍附近注释。

### 2.12 风格基线

以 `pyproject.toml` 为准，改动需团队确认：

```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "ASYNC"]
ignore = ["B008"]   # FastAPI 的 Depends() 默认值属于框架惯例

[tool.mypy]
python_version = "3.11"
plugins = ["pydantic.mypy"]
```

- `per-file-ignores` 已覆盖 `src/seed.py`（表格化种子数据）与 `migrations/versions/*`
  （Alembic 模板产物）。要治就改模板，别逐个文件手工修。
- 本机跑 `mypy src`；CI 跑 `mypy --platform win32 src`（CI 是 Linux，
  按 Linux typeshed 会把 `ctypes.WinDLL` 判为不存在）。两边都要过。
- 行宽 100；函数超过 ~50 行、嵌套超过 3 层，基本该拆了。导入顺序交给 `ruff --fix`。

### 2.13 生成任务状态机（新增能力必读）

`generation/` 是跨模块框架，状态语义比普通 CRUD 严格，以下为 MUST：

| 规则 | 原因 |
|---|---|
| `resume()` **只续轮询，绝不重新提交** | 已有 `external_id` 意味着可能已扣费，再 `dispatch` = 第二份账单 + 两个产物 |
| `submit_attempts > 0 且 external_id 为空` = 提交结果未知 | 三态而非二态；必须保留可清零的人工重试出口 |
| `failed + external_id`（`charged_but_lost`）默认**拒绝重投** | 参考 `ImageService.retry(confirm_charged=True)`：显式确认、把旧任务号搬进 `previous_external_id`、写审计 |
| 同步供应商显式声明 `supports_poll = False` | 未声明时恢复器按「不能续查」处理；靠默认值 `True` 会把记录判死（R12-05） |
| 能力快照落在**记录自身**（`submit_mode`），不要每次启动时重新解析模型目录 | 配置漂移会让历史记录分类变化 |
| `param_schema` 是契约，不是表单注释 | 受理时按声明的 enum / min-max 校验，不要让「声明支持 4 张、供应商只收 1 张」变成 201 后异步失败 |
| 新增 kind 补**表驱动的跨组件状态机用例** | `(status, external_id, submit_attempts)` × `(恢复 / 巡检 / retry / handle_submit)` 逐格断言；组件级用例各自绿，仍可能组合出重复计费（R12-01） |

一句话：**两个各自正确的修复，组合后仍可能破例**。改恢复器、巡检、重试三者之一时，
把另外两者一起跑一遍 `tests/test_generation_state_machine.py`。

### 2.14 外部 URL 与文件落盘（MUST）

供应商返回的图片/文件 URL 属于**不可信输入**：

- 逐跳校验 host；白名单为空时至少要解析后判断 IP 是否为私网 / 回环 / 链路本地 / 非公网，
  **不能只挡 IP 字面量**——`169-254-169-254.nip.io` 这类域名能绕过（R12-04）。
  （当前默认兜底仍不解析 DNS，**这条是尚未落地的规范**，见 §6 缺口；
  `tests/test_image_policy.py::test_default_fallback_boundary_is_explicit` 把这个边界显式记了下来。）
- 生产强制 `IMAGE_ASSET_ALLOWED_HOSTS`（`prod_required_fields`），不要靠黑名单兜底。
- 重定向要么禁用，要么每一跳重新校验；响应体流式读取并设大小上限；
  用魔数/内容类型确认是预期文件格式，`content-type` 不能单由对方决定。

---

## 3. 前端规范（TypeScript / React）

### 3.1 类型

- `tsconfig` 已开 `strict`。禁止 `any`；确需未知类型用 `unknown` + 收窄。
- `as` 断言只在收窄确实安全时用，并加一行理由注释。
- 对外契约用 `interface`，联合类型用 `type`；后端字段名照抄 camelCase，不要再套一层转换。
- **契约类型的唯一来源是 `shared/api/schema.ts`**（MUST）：用
  `import type { TokenOut, UserOut } from '@/shared/api/schema'` 派生，不要另写一份
  `interface User`。FE4-03 的教训：手写副本与生成物可选性不一致时**永远无法互相赋值**，
  编译器也就永远不会报错。需要「别退回手抄」的机械保证时，参考
  `entities/user/model/contract.test.ts` 的 `expectTypeOf` + 读源码断言双层守卫。
- 权限清单同理，从 `shared/api/permissions.ts` 派生；它是 `as const` readonly 元组，
  `new Set()` 要显式写 `Set<string>`。

### 3.2 状态边界

| 数据来源 | 放哪 | 例子 |
|---|---|---|
| 服务端数据（列表、详情、统计） | **React Query** | `useTaskList`、`useTaskStats` |
| 纯客户端 UI 状态（弹窗开关、选中项、筛选草稿） | `zustand` / 组件 state | `CreateTaskModal` 的输入 |
| URL 可表达的状态（分页、筛选、tab） | **URL searchParams** | 任务中心的分页 |
| 当前用户 / 令牌 / 会话状态 | **`shared/access/session.ts` 的 store** | 全应用唯一出口 |

**禁止**把服务端数据 `set` 进 zustand 或 `useEffect` 里镜像一份——那是两份真相。
`queryKey` 一律从工厂派生；轮询要有停止条件。

### 3.3 API 层

- 组件里禁止直接 `import { http }`。请求只写在 `entities/*/api/*.api.ts`，hook 写在 `*.queries.ts`。
- 响应解包、错误转换已在 `shared/api/http.ts` 拦截器统一处理，业务层只拿 `data`。
- 新增接口：先确认生成物里已有类型 → 再加 `api/xxx.api.ts` → 最后写 `queries.ts`。
- **续期 / 登出走 `shared/api/auth.ts` 的独立请求通道**（MUST）：它带独立拦截器，
  用清场**之前**取下的令牌。共用主 `http` 实例会递归续期，且 store 清空后注不进鉴权头。
- Mock 与真实后端只靠 `VITE_USE_MOCK` 一个开关切换，业务代码不得感知数据源；
  mock 严格按契约返回，不要返回「方便页面」的结构。生产构建用动态 import 剔除 mock，
  提交前用 `grep` 确认 `dist/` 里没有演示口令。

### 3.4 组件

- 一个文件一个组件，文件名 = 组件名 = `PascalCase.tsx`。
- Props 用 `interface XxxProps`；超过 200 行或 JSX 嵌套超过 4 层就拆。
- 派生数据在渲染期算，不要用 `useEffect` + `setState` 同步。
- `useEffect` 只用于「与外部系统同步」，依赖数组必须完整。
- 列表 `key` 用稳定 ID；分页组件的页码越界要有回收逻辑（`Pager` 已修）。
- **行内操作的 loading 只作用于该行**（MUST）：不要把 `mutation.isPending` 传给整张表的
  每一行，否则启用一个用户会让所有按钮一起转圈（FE4 💭）。

### 3.5 样式

- Tailwind 原子类 + `cn()` 合并；条件类名一律走 `cn`，不要字符串拼 `+`。
- 颜色、圆角、阴影从 `tailwind.config.js` 的 token 取，禁止新增裸十六进制色值。
- 禁止内联 `style`，除非是运行时计算值。同一视觉效果出现第三次，抽到 `shared/ui`。

### 3.6 性能

- 路由级页面用 `lazy()` + `Suspense`；`useMemo` / `useCallback` 只加在确实需要处。
- 列表图片加 `loading="lazy"`、固定宽高比占位；输入搜索走 `useDebouncedValue`。
- 构建产物是基线的一部分：当前入口 `index` 约 80 kB / gzip 27.5 kB，
  新增依赖前后对比一次体积，别让 mock 或大库漏进生产包。

### 3.7 可访问性

- 可点击元素用 `<button>` / `<a>`；仅图标按钮必须有 `aria-label`。
- 弹窗：`role="dialog"` + `aria-modal`，打开时焦点进入，`Esc` 关闭，关闭后焦点归还。
- 表单 `label` 与输入框用 `htmlFor` / `id` 关联；错误提示用 `aria-describedby`。
- 焦点样式不得 `outline: none` 后不给替代。

### 3.8 会话与令牌生命周期（MUST）

`shared/access/` 是全应用唯一的身份出口，五条结束路径（manual / expired /
unauthorized / external / password）**全部走 `endSession(reason)`**：

| 规则 | 原因 |
|---|---|
| 不自写 `signOut()`、不直接 `localStorage.setItem('sf_token')` | 换账号后旧账号缓存会串号（FE3-02、FE2-02） |
| `endSession` 必须原子地清 store + `queryClient.clear()` | 被动结束路径（401 / 他页退出）用户不会想到清缓存 |
| 续期用 `token-refresh.ts` 的单飞 Promise | 一屏 6 个请求同时 401，会打出 5 个互相打架的续期 |
| 跳转只由路由守卫读 `status` 决定，拦截器不碰路由 | 两处跳转会互相打架 |
| 冷启动 access 过期时**只清 access，保留 refresh** | 连 refresh 一起删 = 7 天会话退化成 2 小时（FE4-01，已修） |
| 跨标签页「他页退出」的信号用 `sf_refresh_token` 被删，不用 `sf_token` | 冷启动会删 `sf_token`，用它当信号会把他页连带踢下线并级联销毁共享 refresh（FE5-01，**当前代码未落地，见 §6 缺口**） |
| manual 路径先清本地、再 fire-and-forget 调 `POST /identity/logout` | 网络断了也能退出；请求用清场前取下的令牌 |
| `setSubject` 校验 token / subject.id，别把已退出的会话「复活」 | FE3-02 |

**测试要覆盖组合路径，不是只覆盖分支**：FE4-01 之所以漏掉，是因为
`session.test.ts` 把错误行为写成了预期、且用例里没塞 refresh。写会话测试时，
先列出 `(access 是否过期, refresh 是否存在, 是否多标签页)` 的组合再断言。

### 3.9 文件命名速查

| 类型 | 规则 | 例 |
|---|---|---|
| 组件 | `PascalCase.tsx` | `TaskCard.tsx` |
| hook | `useXxx.ts` | `useDebouncedValue.ts` |
| API 方法 / Query hook | `<entity>.api.ts` / `<entity>.queries.ts` | `task.api.ts` |
| 类型 | 从 `shared/api/schema.ts` 派生 | `entities/user/model/types.ts` |
| 工具 / 常量表 | `camelCase.ts` / `UPPER_SNAKE` | `format.ts` |
| 生成物（只读） | `schema.ts` / `permissions.ts` | `shared/api/` |

> 同名不同义的工具函数（两份 `displayNameOf`、两个 `useSignOut`）已经在评审里出现过两次。
> 抽公共函数前先 `grep` 全仓有没有同名的；确实要保留两份时，名字必须体现语义差异。

---

## 4. 跨端契约（两边都要遵守）

后端 `schema.py` + `ModuleSpec.permissions` 是唯一真相。改契约的**唯一合法流程**（MUST）：

1. 先改后端 `schema.py`（含 `Field` 校验与默认值）/ `ModuleSpec.permissions`。
2. 跑 `cd api && app export-openapi`，四份生成物**同一次提交**一起入库。
3. 前端从生成物派生类型与权限清单；mock 数据按生成物对齐。
4. 如需更新说明文档，同步 `FRONTEND_EXTENSION.md` 契约表、`ROUTES.md`；
   `docs/API.md` 由生成器负责，不要手改。
5. `app export-openapi --check` 与 `pytest`（含契约测试）都绿再提交。

**破坏性变更清单**（必须评审显式确认）：字段改名/删除、枚举值增删、必填变可选或反之、
状态机新增状态、错误码语义变化、分页结构变化、权限点删改、`public` 标记变化。

**服务端下发的策略不要硬编码（MUST）**：`GET /identity/password-policy` 这类匿名接口
存在的意义就是「策略只有一个来源」（FE5-02）。前端要么调用它、要么用解析后端源码的
测试守住，**不要用本地常量当期望值**——后端改了数字测试不会红，等于没有守卫。
（当前 `GET /identity/password-policy` 前端零调用、`validation.ts` 仍是硬编码，
**这条尚未落地**，见 §6 缺口。）

---

## 5. Git 与提交

**分支**：`feat/<模块>-<短描述>`、`fix/<问题>-<短描述>`、`chore/...`、`docs/...`

**Commit**（Conventional Commits，中文正文可以）：

```
feat(image): 同步供应商孤儿任务支持显式确认重试
fix(identity): 停用账号时统一吊销全部会话
refactor(api): 把分页逻辑收敛到 BaseRepository
docs(readme): 补充生产部署清单
```

- 一次提交只做一件事；出现「顺便改了格式」的 diff，说明该拆。
- PR 控制在 **400 行以内**（不含 lock 文件与生成物）。超过就拆。
- 禁止提交：`.env`、`var/`、`node_modules`、`dist/`、调试用 `console.log`、注释掉的死代码、
  **临时探针测试**（评审用的 `__probe_*.test.ts` / `test___probe_*.py` 跑完必须删）。
- **分工边界**：后端改动只提交 `api/` 与四份生成物，同事在工作区的前端改动原样保留，
  不代改前端的 `types.ts` / `*.api.ts`；反之亦然。提交粒度按此切分。

---

## 6. 让规范自动生效（2026-11-06 现状）

### 已落地的机械守卫

| 检查 | 命令 / 落点 | 阻断条件 |
|---|---|---|
| 后端测试 | `cd api && pytest -q` | 任一失败（含契约字节比对、架构矩阵、状态机表） |
| 后端静态 | `ruff check .` | 任一 error |
| 后端类型 | `mypy src`（CI 用 `mypy --platform win32 src`） | 任一错误 |
| 契约生成物 | `app export-openapi --check` | 四份中任一与代码不一致 |
| 前端类型 | `npm run typecheck`（`tsc -b`） | 任一 error |
| 前端静态 | `npm run lint`（`eslint . --max-warnings 0`） | 任一 error / warning |
| 前端测试 | `npm run test`（Vitest） | 任一失败 |
| 前端构建 | `npm run build` | 失败 |
| CI | `.github/workflows/ci.yml`：`backend` job（pytest/ruff/mypy）+ `frontend` job（`npm run verify`） | 任一 job 红 |

前端四条用 `npm run verify` 一次跑完；后端四条 CI 会跑，本机建议至少
`pytest -q && ruff check . && mypy src && python -m src.cli export-openapi --check`。

**ESLint 分层 zone 的已知边界**：只拦 `@/xxx` 别名，相对路径 `../../features/...`
仍能绕过——评审时看到跨层相对路径要按违规处理。

### 常用命令速查

```bash
# 后端（cd api）
app serve --reload                  # 127.0.0.1:8080/docs
app seed --reset                    # 建表 + 演示数据（生产有护栏）
app export-openapi                  # 四份生成物；--check 只比对
app modules                         # 列出已发现模块、扩展点是否接上
app models                          # 模型目录
app sync-roles                      # 内置角色补齐缺失权限（只加不减）
app prune-sessions --days 30
app new-module <name>               # 新模块脚手架
pytest -q / ruff check . / mypy src

# 前端（cd studio）
npm run verify                      # typecheck + lint + test + build
npm run gen:module <name>
npm run gen:routes
npm run test:coverage
```

### 当前基线快照（2026-11-06 11:15 实测）

| 检查项 | 现状 |
|---|---|
| `pytest -q` | ✅ **346 passed**（346 collected，1 warning） |
| `ruff check .` | ✅ All checks passed |
| `mypy src` | ✅ 144 source files clean |
| `export-openapi --check` | ✅ 四份生成物全部「无变化」 |
| CI | ✅ 已接入，两个 job |
| 前端 `tsc -b` / ESLint | ✅ 静默 / 零告警 |
| 前端 Vitest | ✅ **314 passed / 45 files** |
| `vite build` | ✅ 入口 `index` 80.09 kB（gzip 27.48 kB），产物无演示口令 |

> 这张表是「评估这次改动有没有让仓库变差」的起点，每轮评审刷新一次；
> 历史值见 `REVIEW_CHECKLIST.md` 附录 A 与各轮 `CODE_REVIEW_*.md`。

### 仍然靠人守的缺口

**文档 / 流程欠债**

- `.github/workflows/ci.yml` 里那句注释仍写「三份生成物」，实际是四份；
  `ARCHITECTURE.md` 的连带改动清单也漏了 `permissions.ts`。**改文档时顺手带上。**
- `EXTENSIBILITY_GAPS.md` 剩余 B1（权限点自动进内置角色，需人记得跑 `app sync-roles`）、
  C1（轮询节奏全局化）、C2（重试是作业级不是任务级）。
- pre-commit 仍未接入；目前靠 CI 与提交前手动跑命令。
- 后端 dev server 重启若出现「模块缺导出 / 0 字节响应」，先查 Vite 日志的
  `SAFE_DELETE_BULK_CONFIRM_REQUIRED`（预构建临时目录被批量删除守卫拦下），
  逐个删除 `node_modules/.vite/deps_temp_*` 后再启动。

**规范已写、代码尚未落地**（本文相关条目是目标状态，不要当成现状读）

| 条目 | 规范位置 | 当前代码 |
|---|---|---|
| 跨标签页「他页退出」信号应看 `sf_refresh_token` | §3.8 / `REVIEW_CHECKLIST` FE-30 | `use-session-watch.ts:84` 仍以 `TOKEN_STORAGE_KEY` 为判据（FE5-01，🔴 未修） |
| 前端应消费服务端下发的口令策略 | §4 / FE-33 | `GET /identity/password-policy` 零调用，`validation.ts` 硬编码 10/4，测试用本地常量做期望（FE5-02） |
| 白名单为空时应解析 DNS 后判断 IP | §2.14 / SEC-11 | `pipeline._validate_asset_url` 只挡 IP 字面量与已知内网后缀；prod 已强制 `allowed_hosts`，但 dev 兜底仍能过 `nip.io`（R12-04，部分修） |
| `useMe` 的 5 分钟 `staleTime` | — | 会让降权/改密后的权限刷新延迟可见（`session.queries.ts:31`） |

---

## 附录 A：反例对照表

| ❌ 反例 | ✅ 写法 | 原因 |
|---|---|---|
| `router.py` 里判断 `if task.status != "failed"` | `service.retry_task()` 里 `raise InvalidState` | 规则换入口（worker/CLI）就失效 |
| `repository` 里 `commit()` | `service` 里 `commit()` | 仓储不知道事务边界 |
| `except Exception: pass` | 让异常冒泡，由全局处理器兜底 | 静默故障比崩溃更难查 |
| `logger.info(f"...{x}")` | `logger.info("...%s", x)` | 惰性求值 |
| 组件里 `await http.get(...)` | `useQuery` + `taskApi.list()` | 缓存、重试、轮询、卸载竞态全丢 |
| `useEffect(() => setList(data), [data])` | 直接渲染期派生 | 多一次渲染 + 易死循环 |
| `style={{ color: '#2b7cff' }}` | `className="text-primary"` | 主题不可换、暗色模式失效 |
| `as any` 让报错消失 | 补类型或 `unknown` + 收窄 | 把编译期错误推迟成线上错误 |
| 前端手抄一份 `interface User` | `import type { UserOut } from '@/shared/api/schema'` | 双份真相必然漂移，且编译器比不出来 |
| 手写 `api/registry.py` 注册路由 | `ModuleSpec` + 自动发现 | 两人各加一行必冲突，漏一行是 404 |
| 停用只置 `is_active`，改密只换 hash | 统一走 `_cut_access(user, reason)` | 不变量贴单个入口，换个入口就漏（R10-02） |
| 新模块只接 `pipeline` / `dispatch` | 按 §1.1 矩阵接全恢复 / retry / 巡检 | 「出事才用得上」的插槽最容易漏（R11） |
| `running + external_id` 直接重投 | 保留 `previous_external_id` + `confirmCharged` 显式确认 | 绝不重复计费，且不能丢对账凭证（R12-01） |
| 冷启动 `persistToken(null)` | 只清 access，保留 refresh，交给 `SessionBootstrap` 换票 | 否则 7 天会话只有 2 小时（FE4-01） |
| 每个结束路径各写一份 `signOut` | 全部走 `endSession(reason)` | 被动路径不会有人记得清缓存（FE2-02） |
| 测试期望值写成本地常量 `MIN_LEN = 10` | 解析后端源码或调用策略接口 | 后端改了数字测试不会红（FE5-02） |
| `BATCH=50` 在 Python 里切片 | `stmt.limit(BATCH)` + cutoff 下推 | 全表实例化，数据量一大就炸（R11-04） |
| 全表所有驱动取 `max(timeout_sec)` | 按 `Capability.IMAGE` 过滤后取 | 无关配置能把阈值拖到 90 分钟（R12-03） |
