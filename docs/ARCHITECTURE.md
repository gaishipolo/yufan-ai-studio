# 架构说明 · 雨范 AI 短视频工作台

> 目标读者：**刚接手这个仓库的人**。读完你应当能回答三个问题：
> ① 一个请求从浏览器到数据库经过哪些文件；② 要加一个新功能该动哪些文件；③ 哪些文件是「别乱改」的骨架。
>
> 本文只讲结构与文件职责。写代码规范看 [`CODE_STYLE.md`](CODE_STYLE.md)，
> 前后端扩展流程看 [`FRONTEND_EXTENSION.md`](FRONTEND_EXTENSION.md)，替换模板业务看 [`REPLACEMENT_TEMPLATES.md`](REPLACEMENT_TEMPLATES.md)。

---

## 0. 怎么用这份文档

| 你的问题 | 看哪一节 |
|---|---|
| 这项目整体是什么形状 | §1 |
| 我该先读哪 10 个文件 | §2 |
| `api/src` 里某个文件是干嘛的 | §3（逐文件） |
| `studio/src` 里某个文件是干嘛的 | §4（逐文件） |
| 一个请求 / 一次登录 / 一条生成任务怎么走 | §5 |
| 我要加个新功能，动哪里 | §6 |
| 有哪些坑是别人踩过的 | §7 |

---

## 1. 全局形状

```
┌────────────────────────── studio/ （前端 · FSD 分层） ──────────────────────────┐
│  app/       装配：路由、Provider、会话引导         ← 骨架，改需评审              │
│  widgets/   外壳：顶栏、侧栏、导航、账号菜单       ← 骨架                        │
│  modules/   模块清单：路由 + 导航 + 权限 + 插槽   ← 一个功能一个目录，丢进去即注册 │
│  pages/     页面（组装 features + entities）                                     │
│  features/  交互块（弹窗、工具栏、业务动作）                                     │
│  entities/  领域实体：类型 + api + queries + ui                                   │
│  shared/    无业务的基座：UI 基件、请求内核、注册表、权限判定                     │
└────────────────────────────────────┬────────────────────────────────────────────┘
                    HTTP  {code,message,data} · camelCase · /api/v1
┌────────────────────────────────────┴────────────────────────────────────────────┐
│  api/       HTTP 装配：路由聚合、依赖、异常、中间件                              │
│  modules/   业务垂直切片：每个模块自带 router/schema/service/models/permissions  │
│  generation/ 生成编排框架：提交 / 轮询 / 重试 / 超时 / 事件                      │
│  db/        ORM 基座、会话、通用仓储                                             │
│  infra/     可替换实现：ai / queue / storage（Protocol + 注册表 + .env 选择）     │
│  core/      与业务无关：配置、异常、响应、权限、安全、日志                        │
└─────────────────────────────────────────────────────────────────────────────────┘
```

**三条不可违反的约束**（都由测试兜住，违反了 CI 就红）：

1. **后端依赖单向**：`api（装配）→ modules → generation → db / infra / core`。
   业务模块之间**禁止互相 import** —— `tests/test_architecture.py` 用 AST 静态检查。
   模块之间要互调，走 `api/authn.py`、`api/accounts.py` 这种**注入点 Protocol**，
   并登记进 `api/ports.py` 的 `PORT_CATALOG`（漏登记 = 架构测试红）。
2. **契约单一来源**：`api/docs/openapi.json`、`api/docs/API.md`、
   `studio/src/shared/api/schema.ts`、`studio/src/shared/api/permissions.ts` 四份都由
   `app export-openapi` 生成（权限点那份来自模块声明，不从 OpenAPI 抽）。
   手改任何一份，`tests/test_openapi_contract.py` 会字节比对失败。
3. **骨架不为业务开洞**：前端要往顶栏/侧栏加东西，用 `slots` / `nav` / `cards` 声明，
   不要去改 `app/` 和 `widgets/` 里的骨架文件。

---

## 2. 先读这 10 个文件（约 40 分钟）

按顺序读，读完对全貌就清楚了：

| 顺序 | 文件 | 为什么先读它 |
|---|---|---|
| 1 | `README.md` | 技术栈、启动命令、演示账号 |
| 2 | `api/src/app.py` | 后端所有装配都在这里，没有魔法 |
| 3 | `api/src/api/module.py` | `ModuleSpec` —— 一个模块对外暴露什么的契约 |
| 4 | `api/src/modules/health/` | 最小的完整模块，5 个文件看完就知道切片长什么样 |
| 5 | `api/src/api/deps.py` | 数据库会话、当前操作者、权限依赖都在这 |
| 6 | `api/src/core/response.py` + `core/schema.py` | 统一响应与 camelCase 契约基座 |
| 7 | `studio/src/shared/registry/types.ts` | 前端骨架对外的唯一扩展契约 |
| 8 | `studio/src/modules/templates/module.ts` | 一个模块清单的完整写法（路由 + 导航 + 权限） |
| 9 | `studio/src/shared/api/http.ts` | 请求内核：mock 切换、401 先换票再重放 |
| 10 | `studio/src/shared/access/session.ts` | 「当前用户」的唯一出口 |

---

## 3. 后端逐文件（`api/`）

### 3.1 `api/src/` —— HTTP 装配层

这一层只做装配与协议转换，**不放业务规则**。

| 文件 | 职责 |
|---|---|
| `api/__init__.py` | HTTP 层包：路由聚合、中间件、异常处理、依赖。 |
| `api/accounts.py` | 账号状态扩展点：给「没有请求上下文」的调用方（worker、脚本）查「这个人还能不能干活」。`AccountStatusChecker` Protocol + 三态判定。 |
| `api/authn.py` | 主体解析扩展点：把「令牌里声称的身份」换成「数据库里的权威身份」。 |
| `api/ports.py` | 注入口目录（`PORT_CATALOG`）：登记每个注入口的协议、**谁该实现它**、没接上时的行为。漏登记会被架构测试拦住。 |
| `api/deps.py` | 通用依赖：数据库会话、分页、当前操作者、基础设施、权限。 |
| `api/discovery.py` | 模块自动发现（`pkgutil` 扫 `modules/`，读每个包的 `ModuleSpec`）。 |
| `api/errors.py` | 全局异常处理器：把任何异常收敛成 `{code, message, data}`。 |
| `api/middleware.py` | HTTP 中间件：请求 ID、耗时、CORS 前置处理，并给每个请求打 `http_requests_total` / 耗时直方图（标签用路由模板）。 |
| `api/instrumentation.py` | 指标族声明（HTTP / 队列 / outbox / 构建信息）与抓取时的瞬时值采集（`refresh_runtime_gauges`）。 |
| `api/ops.py` | 运维端点 `/metrics`：独立于业务路由，`METRICS_TOKEN` 校验，不进 OpenAPI。 |
| `api/module.py` | 模块描述对象 `ModuleSpec`：router / permissions / **bootstrap（装配，人人执行）** / startup（恢复，仅消费实例）/ `recovery_kinds` / seed / public。 |
| `api/ratelimit_policy.py` | 写路由的限流策略：依赖树里查 `rate_limit` 标记，未挂的必须在豁免表里写理由（测试守卫）。 |
| `api/registry.py` | 模块注册表（自动发现版）：按名字查模块、汇总权限点、启动钩子。 |
| `api/v1/__init__.py` | API v1 版本包。 |
| `api/v1/router.py` | v1 路由聚合：把每个模块的 router 挂到 `/api/v1` 下。 |

### 3.2 顶层入口（`api/src/*.py`）

| 文件 | 职责 |
|---|---|
| `app.py` | 应用工厂：所有装配都在这里，`main.py` 只引用它。 |
| `cli.py` | 命令行入口 `app <command>`：`serve` / `seed` / `export-openapi` / `import-archive-index` / `prune-sessions` / `sync-roles` / `meta`。 |
| `main.py` | ASGI 入口：`uvicorn main:app --reload`。 |
| `openapi_export.py` | 契约导出：`openapi.json` / `API.md` / `schema.ts` / `permissions.ts` 四份一致文件。 |
| `seed.py` | 演示数据装载（仅开发环境）：三个账号、示例素材、任务、模板。 |
| `workers.py` | 后台 worker：消费队列、分发到已注册的 handler。 |
| `scaffold/__init__.py` | 新模块脚手架（`app new-module` 的实现入口）。 |
| `scaffold/templates.py` | 脚手架模板：生成 router/schema/service/models/permissions 五件套。 |

### 3.3 `core/` —— 与业务无关的基础设施

| 文件 | 职责 |
|---|---|
| `core/config.py` | 应用配置：**部署策略**的唯一来源（`storage_driver`、`queue_driver`、`archive_driver`、`ai_provider` 等），全部可用环境变量覆盖；驱动私有参数在各自的 `XxxSettings`（见 `infra/driver_config.py`）。 |
| `core/context.py` | 请求级上下文：用 `contextvars` 贯穿一次调用链（请求 ID、操作者）。 |
| `core/enums.py` | 跨模块共享的枚举词汇。 |
| `core/events.py` | 进程内域事件总线。 |
| `core/exceptions.py` | 统一异常体系 + 错误码规则 `HTTP×100 + 模块号段×10 + 序号`。 |
| `core/logging.py` | 日志：标准库 logging + 结构化输出。 |
| `core/media.py` | 跨模块共用的媒体描述词汇（类型、尺寸、时长）；权限判断属于各自业务 service。 |
| `core/net.py` | 客户端地址解析（真实 IP，配合 `TRUSTED_PROXIES`）。 |
| `core/permissions.py` | 权限点目录：汇总各模块声明的权限，产出前端权限选择器用的树。 |
| `core/registry.py` | 命名注册表：进程内「按名字注册 → 按名字解析」的通用容器（供应商、驱动都靠它）。 |
| `core/audit.py` | 操作留痕扩展点：`record(action, actor, target, detail)`，写入端由 `db/audit_sink.py` 注册；**写失败不影响业务**。 |
| `core/ports/accounts.py` | 账号状态注入口（从 `api/` 下移）：低层（`generation`）也能问「发起人还在不在」，三态语义，未注册按放行。 |
| `core/scheduler.py` | 周期任务扩展点：`@periodic("模块.动作", every=秒)` 注册，进程内单调度器；模块自己说「多久问一次」，框架不认识任何具体任务。 |
| `core/response.py` | 统一响应包装 `{code,message,data}` 与分页原语 `Page`。 |
| `core/schema.py` | Pydantic 基座：统一 camelCase 契约，与前端 TypeScript 类型一一对应。 |
| `core/metrics.py` | 指标基元（counter / gauge / histogram）+ Prometheus 文本暴露，不引第三方库；标签基数有上限。 |
| `core/trace.py` | 异步链路的 trace 传递：入队/写 outbox 时快照 `request_id`，执行时恢复、结束还原。 |
| `core/flags.py` | 功能开关唯一出口 `is_on("领域.动作")`；不做灰度、不做热更新。 |
| `core/security.py` | 鉴权原语：JWT 签发/校验 + 口令哈希（`hash_password_async` / `verify_password_async` 是唯一入口）。 |

### 3.4 `db/` —— 数据访问层

| 文件 | 职责 |
|---|---|
| `db/base.py` | ORM 基座：声明基类 + 公共字段混入（id / 时间戳）。 |
| `db/registry.py` | 模型注册表：自动扫描所有业务模块的 ORM 实体。 |
| `db/repository.py` | 通用仓储基类：分页、排序、条件拼装的公共实现。 |
| `db/session.py` | 数据库会话与引擎管理（async session、事务边界）。 |
| `db/audit.py` | 通用操作留痕表 `audit_events`（谁/何时/对什么/参数），与 identity 的登录审计分开。 |
| `db/audit_sink.py` | `core.audit` 的数据库写入端：独立开会话，业务回滚了也要留得下。 |
| `db/jobs.py` | 持久化队列表 `queued_jobs`（含 `trace_id`、租约与领取凭据、死信）。 |
| `db/outbox.py` | 事件 outbox：`record()` 与业务同事务落库，周期任务至少一次投递。 |
| `db/backup.py` | 备份/恢复：sqlite 用 backup API 取一致快照、postgres 走 `pg_dump`，附 `manifest.json`。 |
| `db/app_meta.py` | 框架级键值表：存「上一次运行认定的事实」（如权限目录快照），不是缓存。 |

### 3.5 `generation/` —— 生成编排框架

业务（任务、替换模板）只实现几份协议，提交/轮询/重试/超时由这层统一处理。

| 文件 | 职责 |
|---|---|
| `generation/pipeline.py` | 生成流水线注册表。 |
| `generation/recovery.py` | 启动恢复器：按 `ModuleSpec.recovery_kinds` 捞未终态记录，分「续轮询 / 同步供应商孤儿 / 结果未知 / 重新投递」四类处置。分类优先读记录上的 `submit_mode` 快照，缺省 `supports_poll` 按 False（安全一边）；双声明模块跳过并告警。 |
| `generation/runner.py` | 生成编排执行器：把「提交 / 轮询 / 重试 / 超时 / 事件」标准化。 |
| `generation/spec.py` | 生成编排的核心数据类型（阶段、结果、状态）。 |
| `generation/store.py` | 业务侧需要实现的存储协议（读写 run 状态）。 |
| `generation/worker.py` | 框架自带的两个 job handler：轮询与超时。 |
| `generation/events.py` | 生成域事件名与发布辅助。 |

### 3.6 `infra/` —— 可替换基础设施

`infra/loader.py` 按 `.env` 里的名字动态加载实现，所以换驱动不改代码。
四个 kind：`storage` / `queue` / `ai` / `archive`，各自对应一个 `*_DRIVER` 配置项。

| 文件 | 职责 |
|---|---|
| `infra/loader.py` | 驱动解析：按 `infra.<kind>.<name>` 命名约定动态 import，未实现时启动即报错。 |
| `infra/driver_config.py` | 驱动私有配置基座（`DriverSettings`）：前缀 = 驱动名大写，`core/config.py` 只放共有策略。另提供 `submit_budget_for` / `prod_required_fields` 两个自描述声明，以及按能力筛预算、生产启动自检两个函数——消费方不按字段名猜语义。 |
| `infra/media_probe.py` | 媒体字节校验与元数据探测（ffmpeg 走 `imageio_ffmpeg.get_ffmpeg_exe()`，不写死路径）。 |
| `infra/ai/__init__.py` | 模型供应商标注册与路由单例。 |
| `infra/ai/base.py` | AI 模型供应商抽象（`ModelProvider` Protocol）。 |
| `infra/ai/echo.py` | Echo 供应商：本地开发占位，不调用任何外部 API。 |
| `infra/ai/agnes.py` | Agnes 生图供应商：OpenAI 兼容的**同步**接口，`submit` 直接返回终态快照，框架不再轮询。 |
| `infra/ai/router.py` | 模型路由：`model` 字符串 → 具体供应商。 |
| `infra/ai/libtv.py` | LibTV 供应商：只接收归档引用，提交与轮询交给 `GenerationRunner`。 |
| `infra/ai/libtv_process.py` | 独立监督进程：持有 OS 租约直至 Node 真正退出，API 重启不会释放活任务的锁。 |
| `infra/ai/libtv_state.py` | LibTV 适配器的持久化执行状态，不含模板业务。 |
| `infra/ai/libtv_runtime/` | 执行器运行时资源（Node 侧脚本）。 |
| `infra/queue/base.py` | 任务队列抽象。 |
| `infra/queue/memory.py` | 内存队列：开发 / 单测使用，进程重启即丢。 |
| `infra/storage/base.py` | 对象存储抽象。 |
| `infra/storage/local.py` | 本地磁盘存储：开发默认。 |
| `infra/archive/base.py` | 归档存储协议（`ArchiveStorage`）+ 跨实现共用的文件语义（`within` 越界校验、`checksum`、跨进程 `lease` 锁）。 |
| `infra/archive/local.py` | 本地 / 共享盘驱动（`ARCHIVE_DRIVER=local`）：`root()` 不可用直接抛 `UpstreamError`，**不静默回退本地**；`copy()` 带 sha256 校验。 |
| `infra/archive/__init__.py` | 归档存储工厂：`get_archive_storage()` 按 `ARCHIVE_DRIVER` 走 `load_driver("archive", ...)`，与 storage / queue 同形态。 |

### 3.7 `modules/` —— 业务垂直切片

每个模块结构一致：`__init__.py`（`ModuleSpec`）+ `router.py` + `schema.py` + `service.py` + `models.py` + `permissions.py`。
**丢进目录即被发现**，`api/` 不需要任何注册代码。

**health · 健康检查（最小示例，建议先看这个）**

| 文件 | 职责 |
|---|---|
| `modules/health/router.py` | 存活 / 就绪探针（`/api/v1/health`）。 |

**identity · 身份与权限（最复杂的模块）**

| 文件 | 职责 |
|---|---|
| `modules/identity/models.py` | ORM 实体：用户、角色、角色→权限、用户→角色、登录事件、会话。 |
| `modules/identity/router.py` | 路由：登录、续期、登出、个人中心、用户与角色管理、权限目录。 |
| `modules/identity/schema.py` | 出入参（`TokenOut` / `MeOut` / `RoleOut` …）。 |
| `modules/identity/service.py` | 业务规则：认证、权限展开、用户与角色增删改、`_cut_access` 切断访问的唯一入口。 |
| `modules/identity/permissions.py` | 权限点与内置角色模板（`sync_builtin_role_permissions` 只加不减）。 |
| `modules/identity/repository.py` | 仓储：把 SQL 关在这一层。 |
| `modules/identity/dependencies.py` | 依赖装配：当前操作者、权限校验、目标校验。 |
| `modules/identity/errors.py` | 身份模块错误码（slot 2，号段见 `docs/EXTENDING.md` §7）。 |
| `modules/identity/account_status.py` | `AccountStatusChecker` 的身份模块实现（供 worker 消费）。 |
| `modules/identity/principal.py` | 把数据库里的账号状态接进请求链路（`api/authn.py` 的解析器实现）。 |
| `modules/identity/policy.py` | 口令策略：长度 + 「一眼能猜到」的形态。 |
| `modules/identity/throttle.py` | 登录失败限流（进程内 LRU + 滑动窗口）与审计去重。 |

**task · 视频生成任务**

| 文件 | 职责 |
|---|---|
| `modules/task/router.py` | 路由：只做 HTTP 协议转换，业务在 service。 |
| `modules/task/service.py` | 领域服务：业务规则都在这一层。 |
| `modules/task/repository.py` | 仓储：把 SQL 细节挡在 service 之外。 |
| `modules/task/models.py` | 任务 ORM 实体。 |
| `modules/task/schema.py` | 出入参。 |
| `modules/task/enums.py` | 枚举与状态机（queued → running → completed / failed）。 |
| `modules/task/permissions.py` | 权限点单一常量，避免权限码散落成字符串。 |
| `modules/task/params.py` | 「模型目录 → 创建入参」的校验：入口就把非法请求挡住。 |
| `modules/task/pipeline.py` | 把 `video_tasks` 表适配到通用生成框架。 |
| `modules/task/dependencies.py` | 依赖装配。 |
| `modules/task/worker.py` | 启动恢复：进程重启后把在飞任务接回去。 |

**asset · 素材资产（含归档库）**

`assets` 表是**全部素材的唯一索引**（网页上传 / 归档登记 / 生成产物），但字节分两套存储：
`source='upload'` 走对象存储（`STORAGE_DRIVER`，本地或 S3），`source='archive'` 走归档盘
（`MEDIA_ARCHIVE_ROOT`）。两套存储的 `storage_key` 不是同一命名空间，读文件时按 `source`
选择驱动；两个列表接口也各自限定来源，避免把对方的行渲染成 404 封面。
归档登记时仍写一份 `catalog/assets/{id}.json` 镜像——替换模板在 worker 里同步读它，
这是「索引换 DB、替换模板零改动」的桥；JSON 不是真相源，丢了不影响列表与取文件。

| 文件 | 职责 |
|---|---|
| `modules/asset/router.py` | 普通素材路由（`/assets`）。 |
| `modules/asset/service.py` / `schema.py` / `models.py` | 素材资产的服务、出入参、ORM 实体。 |
| `modules/asset/archive.py` | 归档路由：只声明 HTTP 参数与响应，业务在 `archive_service`。 |
| `modules/asset/archive_service.py` | 归档编排：流式落盘、摘要去重、缩略图、写 JSON 镜像；磁盘操作在线程池执行。 |
| `modules/asset/archive_repository.py` | 归档素材仓储：按 `source='archive'` 限定作用域查 `assets` 表，`CATEGORIES` 分类常量。 |
| `modules/asset/archive_import.py` | `app import-archive-index` 的实现：JSON 索引 → `assets` 表，幂等、不删 JSON。 |
| `modules/asset/archive_schema.py` | 归档契约（`ArchivedAssetOut` 等）。 |
| `modules/asset/archive_dependencies.py` | 归档 HTTP 依赖装配。 |

**template · 模板与替换模板（同事新增的那个功能）**

| 文件 | 职责 |
|---|---|
| `modules/template/router.py` / `service.py` / `schema.py` / `models.py` | 行业模板的常规四件套。 |
| `modules/template/replacement_router.py` | 替换模板 HTTP 契约：IO 与状态规则交给 service。 |
| `modules/template/replacement_service.py` | 业务编排：归档先登记后准备，失败可安全续做。 |
| `modules/template/replacement_repository.py` | 替换调用仓储：类型化旧 JSON，保持归档目录兼容。 |
| `modules/template/replacement_schema.py` | 契约；已准备与已生成是不同状态。 |
| `modules/template/replacement_pipeline.py` | 替换档案到生成框架（`GenerationStore` / `Pipeline`）的适配。 |
| `modules/template/replacement_worker.py` | 上传准备与下载为非计费阶段；提交/轮询/超时复用生成框架。停用账号闸门、重试退避也在这。 |
| `modules/template/replacement_dependencies.py` | 替换模板 HTTP 依赖装配。 |
| `modules/template/replacement_runtime/` | 执行器运行时：`prompt-compiler.mjs`（提示词编译）、`prompts/*.txt`（三类中文提示词模板）、`workbench/`（浏览器侧预览）。 |

**其余模块（结构同上，看文件名即可）**

| 文件 | 职责 |
|---|---|
| `modules/catalog/service.py` | 模型目录服务：把 `ModelRouter` 的模型清单翻译成前端可直接用的结构。 |
| `modules/catalog/router.py` | 模型目录路由：前端「模型选择器」的唯一数据源。 |
| `modules/catalog/schema.py` | 模型目录出参。 |
| `modules/image/router.py` / `service.py` / `pipeline.py` / `schema.py` / `models.py` / `permissions.py` | 生图：作业提交（建记录 + 投递）、历史与 `POST /images/{id}/retry`（带 `confirmCharged` 确认开关）、`image_jobs ↔ GenerationRecord` 映射与终态保护、`persist_output` 落盘（host 白名单 / 逐跳校验 / 大小上限 / 魔数）、`list_unfinished` 供框架恢复器调用、权限点（含 `image:retry:any`）。`models.py` 另存 `previous_external_id`（重试前的对账凭证）与 `submit_mode`（供应商形态快照）。 |
| `modules/image/maintenance.py` | 生图周期任务 `image.reconcile`：捞回卡在 `queued` / `running` 且无 `external_id` 的记录（已受理的不碰）。阈值只取声明了 IMAGE 提交预算的驱动的 `timeout_sec`、按 `coalesce(started_at, created_at)` 判陈旧、cutoff 按 dialect 选 aware/naive、`LIMIT` 下推到 SQL、日志按「重投 / 待人工核对」分分支。 |
| `modules/image/seed.py` | 生图演示数据：由 `ModuleSpec.seed` 调用，不再改 `src/seed.py`；演示记录必须是终态，否则会被巡检重投到真实供应商。 |
| `modules/shot/router.py` / `service.py` / `schema.py` / `models.py` | 镜头库：可复用的运镜指令词条。 |
| `modules/storyboard/router.py` / `service.py` / `schema.py` / `models.py` | 分镜模板：整段分镜结构与时长/比例预设。 |

### 3.8 测试（`api/tests/`，32 个文件）

| 文件 | 职责 |
|---|---|
| `conftest.py` | 测试夹具：临时库、应用、登录客户端。 |
| `test_architecture.py` | **架构守护**：业务模块禁止互相 import、注入口必须登记、周期任务命名、**模块 × 插槽矩阵**（每个 pipeline kind 有恢复插槽与重试出口）。 |
| `test_generation_recovery.py` | 框架恢复器四类分支、供应商形态快照、缺省安全与双声明告警、审计启动自检。 |
| `test_image_retry.py` | 生图重试出口（含 `confirmCharged` 与凭证留存）、终态保护、巡检阈值与 SQL 下推。 |
| `test_image_persist.py` | 落盘下载：host 白名单、逐跳重定向、大小上限、content-type 与魔数。 |
| `test_image_policy.py` | 驱动配置自描述：预算筛选口径、声明缺失响亮失败、prod 白名单断言、兜底边界。 |
| `test_generation_state_machine.py` | **跨组件状态机契约表**：把恢复 / 巡检 / retry 串到同一条记录上，锁住「带着 external_id 重投」的交接路径。 |
| `test_openapi_contract.py` | **契约守护**：四份生成物字节比对 + `$ref` 可解析 + TS 引用类型已定义。 |
| `test_config_guard.py` | 配置守护：危险默认值 / 生产环境开关。 |
| `test_health.py` | 健康检查。 |
| `test_identity_api.py` | 身份模块接口全集。 |
| `test_login_gate.py` | 登录闸门：停用、限流、审计。 |
| `test_sessions.py` | 会话：`sid` 校验、续期轮换、吊销、踢设备。 |
| `test_password_offload.py` | 口令哈希不阻塞事件循环。 |
| `test_task_api.py` | 任务接口。 |
| `test_generation.py` | 生成编排：提交/轮询/重试/超时。 |
| `test_resume.py` | 进程重启后的任务恢复。 |
| `test_catalog.py` | 模型目录。 |
| `test_image_scope.py` | 生图的可见范围（本人 / 团队）。 |
| `test_storage.py` | 存储驱动。 |
| `test_net.py` | 真实客户端 IP 解析。 |
| `test_archive_preview.py` | 归档预览与派生文件。 |
| `test_replacement_archive.py` | 替换模板的归档登记。 |
| `test_replacement_guards.py` | 替换模板的闸门：停用账号、重试退避与上限、权限补齐。 |
| `test_replacement_recovery.py` | 替换任务崩溃恢复。 |
| `test_scaffold.py` | `app new-module` 脚手架产物可跑。 |
| `tests_archive_helpers.py` | 归档测试的公共辅助（非 pytest 用例文件）。 |

### 3.9 其它

| 路径 | 职责 |
|---|---|
| `api/scripts/import_legacy_replacement_assets.py` | 只读旧库，复制已登记原片和已发布素材包；不移动历史资料、不触发生成。 |
| `api/scripts/refresh_archive_media.py` | 刷新已登记视频的时长元数据与浏览器预览，不改原片（修容器时长误判与 10-bit/HEVC 不可播）。 |
| `api/migrations/` | Alembic：`env.py`（async 迁移环境）、`versions/`（8 个线性版本脚本，链尾 `f4c2d8e91b70` 给 `image_jobs` 加 `previous_external_id` / `submit_mode`）、`script.py.mako`（模板）。 |
| `api/alembic.ini` | Alembic 配置。 |
| `api/var/` | 本地运行时目录（存储、归档、日志），已忽略。 |

---

## 4. 前端逐文件（`studio/src/`）

### 4.1 分层规矩（先看这张表再看文件）

| 层 | 允许 import | 放什么 | 不许做什么 |
|---|---|---|---|
| `app/` | 下面各层 | 装配：Provider、路由工厂、会话引导 | 放业务逻辑 |
| `widgets/` | `shared`、`entities` | 外壳：顶栏、侧栏、导航、账号菜单 | 认识具体业务模块 |
| `modules/` | `pages` | **模块清单**：路由 + 导航 + 权限 + 插槽 | 写 UI 实现 |
| `pages/` | `features`、`entities`、`shared` | 页面组装 | 直接写请求 |
| `features/` | `entities`、`shared` | 一个业务交互（弹窗、工具栏） | 跨 feature 复用 → 上提到 entities |
| `entities/` | `shared` | 领域类型 + api + queries + ui | 依赖 features |
| `shared/` | 无 | 基座：UI 基件、请求内核、注册表、权限 | 出现业务名词 |

### 4.2 根文件与 `app/`

| 文件 | 职责 |
|---|---|
| `main.tsx` | 挂载入口：`createRoot` → `<App />`。 |
| `index.css` | Tailwind 指令 + 设计令牌（颜色、圆角、阴影变量）。 |
| `vite-env.d.ts` | Vite 环境变量类型声明（`VITE_USE_MOCK`、`VITE_API_BASE_URL` 等）。 |
| `test/setup.ts` | 测试环境统一收尾（jsdom 补丁、清理）。 |
| `app/App.tsx` | 应用根：`AppProviders` + `RouterProvider` + `Toaster`。 |
| `app/providers.tsx` | Provider 装配：Query、注册表、访问控制、会话引导。**骨架文件，改动需评审。** |
| `app/router.tsx` | 路由工厂：把模块清单里的路由展开成 `createBrowserRouter`，按 `layout` 自动包外壳或裸布局。 |
| `app/router.test.ts` | 路由守护：每个 shell 路由必须声明权限（除豁免名单）、导航不能指向 `bare` 页。 |
| `app/RouteError.tsx` | 路由级兜底：lazy 页面加载失败或渲染抛错时接管整页。 |
| `app/SessionBootstrap.tsx` | 冷启动会话引导：只有刷新令牌时先换票，再拉身份；失败分三种结局。 |
| `app/SessionBootstrap.test.tsx` | 引导分支的单测（换票成功 / 401 / 网络故障）。 |
| `app/registry/index.ts` | 模块清单聚合：`import.meta.glob` 自动收集 `modules/*/module.ts`。 |
| `app/registry/index.test.ts` | 骨架级不变量：模块清单与注册表不能脱节。 |

### 4.3 `entities/` —— 领域实体

**asset · 素材**

| 文件 | 职责 |
|---|---|
| `entities/asset/api/asset.api.ts` | 普通素材接口（`/assets`）。 |
| `entities/asset/api/archive.api.ts` | 归档库接口：列表、上传、从 inbox 登记、文件/缩略图/流。 |
| `entities/asset/api/archive.queries.ts` | 归档查询键与 hooks（`useArchiveList`、上传 mutation）。 |
| `entities/asset/model/types.ts` | 素材资产类型。 |
| `entities/asset/model/archive.types.ts` | 归档素材类型 + 分类枚举（`source/character/product/scene/audio` 与后端 `CATEGORIES` 对应）。 |
| `entities/asset/ui/AssetCard.tsx` | 素材卡片（封面 + 名称 + 大小）。 |
| `entities/asset/ui/ArchivedMedia.tsx` | 归档媒体的渲染（图片 / 视频 / 音频 / 预览降级）。 |

**task · 任务**

| 文件 | 职责 |
|---|---|
| `entities/task/api/task.api.ts` | 任务接口：列表、详情、创建、重试、删除。 |
| `entities/task/api/task.queries.ts` | 任务查询键与 hooks。 |
| `entities/task/model/types.ts` | 任务类型与状态机（queued → running → completed / failed）。 |
| `entities/task/model/types.test.ts` | 状态机与展示名兜底的用例。 |
| `entities/task/ui/TaskCard.tsx` | 任务卡片（状态、进度、操作）。 |
| `entities/task/ui/TaskCard.test.tsx` | 卡片渲染与状态文案。 |

**user · 用户**

| 文件 | 职责 |
|---|---|
| `entities/user/api/user.api.ts` | 用户接口：列表、详情、新建、编辑、分配角色、重置密码、踢下线。 |
| `entities/user/api/user.queries.ts` | 用户查询键与 hooks；角色「成员数」复用同一命名空间。 |
| `entities/user/model/types.ts` | 用户类型，从 `shared/api/schema.ts` 派生，**不要手抄**。 |
| `entities/user/model/contract.test.ts` | 契约测试：前端类型与生成物一致。 |
| `entities/user/model/validation.ts` | 口令 / 邮箱 / 头像地址校验 —— **唯一真相**（长度常量只在这里）。 |
| `entities/user/model/validation.test.ts` | 校验规则用例。 |
| `entities/user/ui/UserAvatar.tsx` | 头像：只要 id/昵称/头像地址三个字段就能画。 |
| `entities/user/ui/UserAvatar.test.tsx` | 头像兜底（首字母 / 默认色）。 |

**role · 角色**

| 文件 | 职责 |
|---|---|
| `entities/role/api/role.api.ts` | 角色接口（注意角色列表返回**裸数组**，不分页）。 |
| `entities/role/api/role.queries.ts` | 角色查询键与 hooks。 |
| `entities/role/model/types.ts` | 角色、权限目录、`cannotDeleteReason`。 |
| `entities/role/model/permission-catalog.ts` | 前端权限点清单（与后端各模块 `PermissionSpec` 逐条对齐）。 |
| `entities/role/model/permission-catalog.test.ts` | 与后端逐条比对；漂移即红。 |
| `entities/role/ui/RoleCheckboxList.tsx` | 角色多选（新建用户与分配角色共用）。 |

**session · 会话**

| 文件 | 职责 |
|---|---|
| `entities/session/api/session.api.ts` | 登录 / 续期 / 登出 / 本人资料接口（走旁路通道）。 |
| `entities/session/api/session.queries.ts` | `useLogin`（写会话）、`useMe`（刷身份）、`useUpdateProfile`。 |
| `entities/session/model/types.ts` | 由 `schema.ts` 的 `TokenOut` 派生的类型。 |

**其余实体（结构一致：`api` + `model/types`）**

| 文件 | 职责 |
|---|---|
| `entities/image/api/image.api.ts` | 生图记录接口与查询键。 |
| `entities/image/model/types.ts` | 生图任务记录类型。 |
| `entities/model/api/model.api.ts` | 模型目录接口 —— 前端「有哪些模型」的唯一入口。 |
| `entities/model/api/model.queries.ts` | 模型目录查询键与 hooks。 |
| `entities/model/api/model.queries.test.ts` | 目录取数与缓存行为。 |
| `entities/model/model/types.ts` | 模型目录资源形状。 |
| `entities/shot/api/shot.api.ts` / `entities/shot/model/types.ts` | 镜头库接口与类型。 |
| `entities/storyboard/api/storyboard.api.ts` / `entities/storyboard/model/types.ts` | 分镜模板接口与类型。 |
| `entities/template/api/template.api.ts` / `entities/template/model/types.ts` | 行业模板接口与类型。 |
| `entities/template/api/replacement.api.ts` | 替换模板接口（目录、准备、执行、轮询）。 |
| `entities/template/api/replacement.queries.ts` | 替换模板查询键与 hooks。 |
| `entities/template/model/replacement.types.ts` | 替换模板类型（`ReplacementKind` 由生成物派生）。 |

### 4.4 `features/` —— 交互块

| 文件 | 职责 |
|---|---|
| `features/asset-library/AssetLibrary.tsx` | 素材库主体：类型分面 tab、关键字搜索、分页、上传（带进度与去重提示）、删除（引用冲突原样回显）。 |
| `features/asset-library/AssetPreview.tsx` | 素材预览弹窗：按类型给原生播放器、元信息、下载与删除入口。 |
| `features/asset-library/AssetLibrary.test.tsx` | 列表筛选、上传去重提示、删除 409、无权限隐藏删除入口。 |
| `features/replacement-templates/ReplacementTemplates.tsx` | 替换模板主界面：选原片 → 选参考 → 准备 → 提交。 |
| `features/replacement-templates/SourceTemplateCard.tsx` | 原片卡片（选中态、时长、音轨标记）。 |
| `features/replacement-templates/ReplacementTemplates.test.tsx` | 主流程单测（取数层替换为固定数据）。 |
| `features/replacement-templates/replacement-templates.css` | 该功能的局部样式。 |
| `features/auth/LoginForm.tsx` | 登录表单：提交、错误分类、限流倒计时。 |
| `features/auth/login-feedback.ts` | 登录失败的分类与措辞（按 HTTP 状态段，不按完整业务码）。 |
| `features/auth/login-feedback.test.ts` / `features/auth/LoginForm.test.tsx` | 失败分类与表单行为。 |
| `features/auth/ProfileModal.tsx` | 改自己资料。 |
| `features/auth/ChangePasswordModal.tsx` | 改自己密码（成功后结束会话，后端已吊销全部会话）。 |
| `features/auth/ChangePasswordModal.test.tsx` | 改密流程用例。 |
| `features/auth/UserMenuItems.tsx` | 顶栏账号菜单里的菜单项（骨架通过 `user-menu.items` 插槽渲染）。 |
| `features/user-form/UserFormModal.tsx` | 新建 / 编辑用户。 |
| `features/user-form/UserFormModal.test.tsx` | 校验拦截与提交载荷。 |
| `features/user-roles/AssignRolesModal.tsx` | 分配角色（整组替换语义）。 |
| `features/user-roles/AssignRolesModal.test.tsx` | 自己改自己的角色时给降权提醒。 |
| `features/user-security/ResetPasswordModal.tsx` | 管理员重置密码（字段是 `newPassword`）。 |
| `features/user-security/ResetPasswordModal.test.tsx` | 重置流程用例。 |
| `features/user-security/DeactivateUserModal.tsx` | 停用用户（非物理删除，保留历史归属）。 |
| `features/role-editor/RoleFormModal.tsx` | 新建 / 编辑角色基本信息。 |
| `features/role-editor/RolePermissionsModal.tsx` | 勾选角色权限（整组替换）。 |
| `features/role-editor/RolePermissionsModal.test.tsx` | 「有效权限码」计算与对象引用一致性。 |
| `features/task-filter/ui/TaskSearchRow.tsx` | 任务搜索行（未知总数时传 `undefined`，不显示「共 0 条」）。 |
| `features/task-filter/ui/TaskToolbar.tsx` | 任务筛选工具条（状态、范围；`team` 范围待后端实现）。 |
| `features/task-preview/TaskPreviewModal.tsx` | 任务结果预览弹窗。 |
| `features/create-task/CreateTaskModal.tsx` | 创建生成任务（模型、时长、比例全部来自 `/models`）。 |

### 4.5 `modules/` —— 模块清单（**加功能先加这里**）

| 文件 | 职责 |
|---|---|
| `modules/assets/module.ts` | 素材资产库：路由 `/assets` + 侧栏项 + 权限 `asset:read`。 |
| `modules/templates/module.ts` | 模板库：路由 `/templates` + 权限 `template:read/use`。 |
| `modules/shots/module.ts` | 镜头库：路由 + 侧栏项 + 权限。 |
| `modules/storyboards/module.ts` | 分镜模板：路由 + 侧栏项 + 权限。 |
| `modules/image/module.ts` | 生图：路由 + 侧栏项 + 权限。 |
| `modules/studio/module.ts` | 创作台：**模块清单示例**（路由 + 侧栏 + 顶部宫格卡片，三处一次声明）。 |
| `modules/task-center/module.ts` | 任务中心：路由 + 导航 + 角标声明。 |
| `modules/task-center/RunningTaskBadge.tsx` | 导航角标实现（模块自己取数，骨架不关心）。 |
| `modules/usage/module.ts` | 用量看板：只声明路由与导航，页面是占位骨架。 |
| `modules/usage/mock.ts` | 用量看板的 mock。 |
| `modules/user-access/module.ts` | 用户与权限：路由 `/system/user-access` + 权限。 |
| `modules/user-access/mock.ts` / `modules/user-access/mock.test.ts` | 用户权限功能的 mock 与断言。 |
| `modules/auth/module.ts` | 登录：`layout: 'bare'` 路由 + 顶栏菜单插槽。 |
| `modules/auth/mock.ts` / `modules/auth/mock.test.ts` | 登录 mock（同账号同密码，含限流复现）。 |

### 4.6 `pages/` —— 页面组装

| 文件 | 职责 |
|---|---|
| `pages/assets/AssetsPage.tsx` | 素材资产库页：页头 + `<AssetLibrary />`。 |
| `pages/templates/TemplatesPage.tsx` | 模板库页：页头 + `<ReplacementTemplates />`。 |
| `pages/shots/ShotsPage.tsx` | 镜头库页（词条列表与增删改）。 |
| `pages/storyboards/StoryboardsPage.tsx` | 分镜模板页。 |
| `pages/image/ImagePage.tsx` | 生图历史页（分页器在「总数未知」时不回退页码）。 |
| `pages/studio/StudioPage.tsx` | 创作台：模型/时长/比例全部来自 `/models`，加模型不改这里。 |
| `pages/task-center/TaskCenterPage.tsx` | 任务中心：筛选条件全部由 URL 承载，可分享、可回退。 |
| `pages/usage/UsagePage.tsx` | 用量看板 · 骨架占位页。 |
| `pages/login/LoginPage.tsx` | 登录页（裸布局）+ 会话结束原因文案。 |
| `pages/not-found/NotFoundPage.tsx` | 404。 |
| `pages/user-access/UserAccessPage.tsx` | 用户与权限页：两个页签（用户 / 角色）。 |
| `pages/user-access/UsersSection.tsx` | 用户列表：搜索、筛选、分页、行内动作。 |
| `pages/user-access/UsersSection.test.tsx` | 用户列表行为（含停用自己时按钮禁用）。 |
| `pages/user-access/RolesSection.tsx` | 角色卡片：成员数、失效权限、删除保护。 |

### 4.7 `shared/` —— 无业务基座

**`shared/api/` 请求内核**

| 文件 | 职责 |
|---|---|
| `shared/api/http.ts` | axios 实例：mock 切换（`VITE_USE_MOCK`）、鉴权头、401 **先换票再重放**、错误归一。 |
| `shared/api/http.test.ts` | 拦截器行为：换票与清场的边界。 |
| `shared/api/auth.ts` | 认证旁路通道：续期与登出走不带拦截器的实例（否则递归 / 注不进鉴权头）。 |
| `shared/api/rest.ts` | 标准 RESTful 资源工厂（list/detail/create/update/remove）。 |
| `shared/api/retry.ts` | 查询重试策略：只重试「下一次可能就好」的失败。 |
| `shared/api/query-client.ts` | 全局 Query 客户端（`staleTime`、错误重试挂载点）。 |
| `shared/api/types.ts` | 统一响应包装 `{code,message,data}` 与 `Page`。 |
| `shared/api/schema.ts` | 后端接口类型，**自动生成，请勿手工编辑**。 |
| `shared/api/mock-adapter-loader.ts` | mock 适配器的按需加载（生产构建摇掉）。 |
| `shared/api/mock-mode.test.ts` | mock 模式下的认证链路（集成级）。 |

**`shared/access/` 访问控制与会话**

| 文件 | 职责 |
|---|---|
| `shared/access/session.ts` | 会话状态 store —— **骨架里唯一的「当前用户」出口**（`token` / `refreshToken` / `subject` / 三态 `status`）。 |
| `shared/access/types.ts` | 访问控制骨架的类型（`Subject`、会话状态）。 |
| `shared/access/evaluate.ts` | 权限判定纯函数：`task:read` / `task:*` / `*` 三种写法。 |
| `shared/access/evaluate.test.ts` | 判定用例。 |
| `shared/access/Can.tsx` | 按钮级门禁组件。 |
| `shared/access/RequireAuth.tsx` | 路由级登录守卫：未登录回登录页，并带上 `from` 与 `reason`。 |
| `shared/access/RequireAuth.test.tsx` | 原路返回与过期提示。 |
| `shared/access/RequirePermission.tsx` | 路由级权限守卫（403 走 `ForbiddenState`）。 |
| `shared/access/access-context.ts` | 身份 Context（默认 `GUEST_SUBJECT`）。 |
| `shared/access/AccessProvider.tsx` | 身份注入点：把 session store 的 subject 下发。 |
| `shared/access/use-access.ts` | 读当前身份 / 权限判定便捷 hook。 |
| `shared/access/index.ts` | 访问控制层的统一出口（组件、hook、纯函数）。 |
| `shared/access/use-session.ts` | 会话状态的细粒度读取器。 |
| `shared/access/session.test.ts` | 会话 store 的启动分支（防回归）。 |
| `shared/access/end-session.ts` | **结束会话的唯一出口**：清会话 + 清查询缓存，只有 `manual` 才调 `POST /identity/logout`。 |
| `shared/access/end-session.test.ts` | 五条结束路径的用例。 |
| `shared/access/token-refresh.ts` | 续期：全应用唯一的「换票」入口，单飞 + 跟票 + 不改身份。 |
| `shared/access/token-refresh.test.ts` | 并发续期、跨标签页跟票。 |
| `shared/access/use-session-watch.ts` | 两个无人值守生命周期：到点续期、跨标签页同步。 |
| `shared/access/use-session-watch.test.tsx` | 定时与 storage 事件。 |
| `shared/access/SessionRecovery.tsx` | 引导失败的第三种结局：重试 / 重新登录，不假装还在 loading。 |

**`shared/registry/` 模块注册表**

| 文件 | 职责 |
|---|---|
| `shared/registry/types.ts` | 模块注册表的公共类型 —— **骨架对外的唯一扩展契约**。 |
| `shared/registry/define-module.ts` | 模块声明入口：只做类型收窄与恒等返回。 |
| `shared/registry/create-registry.ts` | 注册表实现：排序、按权限过滤、校验（路径冲突、`bare` 不进导航）。 |
| `shared/registry/registry-context.ts` / `shared/registry/RegistryProvider.tsx` | 注册表用 context 下发，避免 widgets 直接 import `@/app/registry`。 |
| `shared/registry/use-registry.ts` | 读注册表（必须在 Provider 内）。 |
| `shared/registry/Slot.tsx` | 插槽渲染器：按 `order` 渲染模块声明的内容。 |
| `shared/registry/index.ts` | 对外导出集合。 |
| `shared/registry/create-registry.test.ts` / `shared/registry/Slot.test.tsx` | 注册与插槽的用例。 |

**`shared/ui/` UI 基件**（无业务，任何地方可用）

| 文件 | 职责 |
|---|---|
| `shared/ui/index.ts` | 统一出口（新增基件记得在这里导出）。 |
| `shared/ui/Button.tsx` / `shared/ui/IconButton.tsx` | 按钮与图标按钮（variant / size / loading）。 |
| `shared/ui/Modal.tsx` | 弹窗：焦点陷阱、滚动引用计数、关闭后焦点归还。 |
| `shared/ui/Modal.test.tsx` | 焦点归还用例。 |
| `shared/ui/Toaster.tsx` | 全局 toast 渲染器。 |
| `shared/ui/Tabs.tsx` / `shared/ui/Switch.tsx` / `shared/ui/Select.tsx` / `shared/ui/SearchInput.tsx` / `shared/ui/FormField.tsx` | 表单与筛选基元。 |
| `shared/ui/Pager.tsx` | 分页器（总数未知时不回退页码）。 |
| `shared/ui/Pager.test.tsx` | 删完末页最后一条的边界。 |
| `shared/ui/EmptyState.tsx` / `shared/ui/ErrorState.tsx` / `shared/ui/ForbiddenState.tsx` / `shared/ui/FullPageLoader.tsx` | 四种状态展示：空 / 失败可重试 / 403 / 整页加载。 |
| `shared/ui/ErrorState.test.tsx` | 失败态与重试。 |
| `shared/ui/PageHeader.tsx` | 页头（标题、描述、右侧动作）。 |
| `shared/ui/BrandMark.tsx` | 品牌图形标（「YF」组合字）。 |
| `shared/ui/CoverImage.tsx` | 封面图（无图时按种子生成兜底渐变）。 |
| `shared/ui/Chip.tsx` / `CardSkeleton*` | 标签与骨架屏。 |

**`shared/` 其余**

| 文件 | 职责 |
|---|---|
| `shared/config/app.ts` | 应用级常量（`HOME_PATH`、`APP_NAME` 等）。骨架配置，改需评审。 |
| `shared/config/app.test.ts` | 产品名只有两个落点且必须一致。 |
| `shared/lib/cn.ts` | 合并 className（后者覆盖前者冲突项）。 |
| `shared/lib/format.ts` | 秒 → `11s` / `1m20s`、字节 → `1.2 MB` 等格式化。 |
| `shared/lib/format.test.ts` | 格式化用例（本地时区构造，避免时差断言）。 |
| `shared/lib/gradient.ts` | 无图封面兜底渐变（按索引轮换）。 |
| `shared/lib/path.ts` | 路径匹配：导航高亮与分组定位共用。 |
| `shared/lib/toast.ts` | `toast()` 命令式提示（写入 `Toaster` 的队列）。 |
| `shared/hooks/useDebouncedValue.ts` | 防抖值（搜索输入）。 |
| `shared/hooks/useDebouncedValue.test.ts` | 防抖时序。 |

### 4.8 `widgets/` —— 外壳

| 文件 | 职责 |
|---|---|
| `widgets/app-shell/AppShell.tsx` | 应用外壳：布局 + 插槽，不认识任何业务模块。 |
| `widgets/top-bar/TopBar.tsx` | 顶栏：品牌标、插槽动作、账号菜单。 |
| `widgets/module-nav/ModuleNav.tsx` | 顶部宫格导航：由模块 `cards` 声明生成。 |
| `widgets/sidebar/Sidebar.tsx` | 侧栏：按分组渲染导航项，角标由模块提供。 |
| `widgets/sidebar/Sidebar.test.tsx` | 角标渲染（取数层替换）。 |
| `widgets/sidebar/use-collapsed-groups.ts` | 分组折叠状态（本地持久化）。 |
| `widgets/user-menu/UserMenu.tsx` | 顶栏账号菜单：容器与身份块归骨架，菜单项来自插槽。 |
| `widgets/user-menu/UserMenu.test.tsx` | 菜单项来自 auth 模块的真实实现。 |
| `widgets/user-menu/use-sign-out.ts` | 登出：`endSession('manual')` → 回登录页。 |

### 4.9 `mocks/` —— 浏览器内假后端

| 文件 | 职责 |
|---|---|
| `mocks/adapter.ts` | axios 适配器层拦截请求，模拟真实后端。 |
| `mocks/adapter.test.ts` | 直接喂 axios 配置给 adapter，绕过网络层。 |
| `mocks/kernel.ts` | 内核：请求解析、延迟、错误、响应包装。 |
| `mocks/db.ts` | 内存数据库：模拟后端持久层。 |
| `mocks/registry.ts` | mock 路由注册表。 |
| `mocks/routes/index.ts` | mock 文件自动发现（`modules/<id>/mock.ts` 由各模块自带）。 |
| `mocks/routes/catalog.mock.ts` | 资产 / 模板 / 分镜 / 镜头 / 生图记录的 mock。 |
| `mocks/routes/generation.mock.ts` | 生成（同步返回任务 / 生图记录）的 mock。 |
| `mocks/routes/models.mock.ts` | 模型目录 mock（与后端 echo 供应商声明一致）。 |
| `mocks/routes/task.mock.ts` | 任务 mock。 |

### 4.10 构建配置与脚本

| 文件 | 职责 |
|---|---|
| `studio/scripts/gen-module.mjs` | `npm run gen:module`：生成模块骨架（清单 + 页面 + mock）。 |
| `studio/vite.config.ts` | Vite：`/api` 代理、别名 `@`、测试配置。 |
| `studio/tailwind.config.js` / `postcss.config.js` | 设计令牌与 Tailwind 扫描范围。 |
| `studio/tsconfig*.json` | TS 项目引用（app / node 两套）。 |
| `studio/eslint.config.js` | ESLint 规则（含 FSD 分层 import 约束）。 |
| `studio/index.html` | HTML 模板。 |
| `studio/public/favicon.svg` | 站点图标。 |

---

## 5. 四条主线：一个请求是怎么走完的

### 5.1 普通查询（以「用户列表」为例）

```
浏览器
 └─ pages/user-access/UsersSection.tsx        页面取数
     └─ entities/user/api/user.queries.ts     useUserList（React Query 键 + hook）
         └─ entities/user/api/user.api.ts     userApi.list → GET /identity/users
             └─ shared/api/http.ts            axios 实例：加 Authorization、401 换票重放
                 └─ vite proxy /api → 127.0.0.1:8080
                     └─ api/middleware.py     请求 ID、耗时
                         └─ api/errors.py     异常 → {code,message,data}
                             └─ api/v1/router.py  build_v1_router() 聚合的模块 router
                                 └─ modules/identity/router.py   只做协议转换
                                     └─ modules/identity/dependencies.py  鉴权 + 权限
                                         └─ modules/identity/service.py   业务规则
                                             └─ modules/identity/repository.py  SQL
                                                 └─ db/session.py  async session
```

要点：**每一层只做一件事**。路由里不写业务，service 里不写 SQL，repository 里不判权限。

### 5.2 登录与会话（前端最容易写错的一段）

```
LoginForm ──► useLogin ──► POST /identity/login ──► TokenOut{accessToken, refreshToken, ...}
                │
                ├─ queryClient.setQueryData(sessionKeys.me, user)
                └─ signIn({token, expiresIn, refreshToken, refreshExpiresIn, subject})
                        │
                        └─ session.ts（唯一出口）→ AccessProvider → 导航过滤 / 守卫 / <Can> 全部自动跟随

之后：
  令牌快过期 → useSessionExpiryWatch → token-refresh.ts（单飞）→ POST /identity/refresh（轮换）
  任意 401   → http.ts 拦截器 → 先换票再重放 → 换不到 → endSession('unauthorized')
  别的标签页退出 → storage 事件 → endSession('external')
  用户点退出 → use-sign-out → endSession('manual') → 顺手 POST /identity/logout
```

**五条会话结束路径统一走 `endSession(reason)`**（清会话 + 清查询缓存写在一处）。
自己另写一个 `signOut()` 会导致「换账号后，上一个账号的列表数据还在缓存里」。

### 5.3 生成任务

```
CreateTaskModal → POST /tasks
   └─ modules/task/service.py      建记录（status=queued）
       └─ modules/task/pipeline.py 适配到 generation 框架
           └─ generation/runner.py 提交 → 轮询 → 重试 → 超时 → 发事件
               └─ infra/queue + workers.py  @register_handler 的 job 被消费
                   └─ infra/ai/<provider>.py  真正调上游

进程重启：
   modules/task/worker.py 按「供应商侧有没有 external_id」分流 ——
   有：只续轮询（不重复提交，否则重复扣费）；无：重置现场重新排队
```

### 5.4 替换模板（同事新增的链路，细节见 `REPLACEMENT_TEMPLATES.md`）

```
① 上传 / 登记    POST /assets/archive/upload | /import   → 落归档盘 + 索引（非计费）
② 准备          POST /templates/replacement-runs          → 归档校验、算 sha256、写清单
③ 提交          POST /templates/replacement-runs/{id}/execute
                    └─ 仅当估价 = 0 积分才提交（花额度前必须过权限 template:use）
④ 轮询 / 恢复     generation 框架 + replacement_worker（准备与下载不计费）
⑤ 成片          library/ 下，索引进 runs/，前端 ArchivedResults 展示
```

停用账号的闸门在 `prepare_upload` 里，位置在 `task_id` 分支**之后** ——
意思是「已经提交给上游的任务」不因为账号被停用而中断（钱已经花了，让它跑完）。

---

## 6. 加一个功能，动哪些文件

**后端（`app new-module <name>` 能生成前 5 个文件）**

1. `api/src/modules/<name>/__init__.py` —— 写 `ModuleSpec`（router、permissions、可选 startup）
2. `api/src/modules/<name>/router.py` —— HTTP 参数与响应，不写业务
3. `api/src/modules/<name>/schema.py` —— 出入参
4. `api/src/modules/<name>/service.py` —— 业务规则
5. `api/src/modules/<name>/models.py` —— ORM（如需建表，`alembic revision --autogenerate`）
6. 权限点登记进 `permissions.py` 的 `ModuleSpec.permissions`
7. `cd api && app export-openapi` —— **四份生成物一起提交**
8. 需要被别的模块调用？加注入点 Protocol（照 `api/authn.py`、`api/accounts.py`），
   并在 `api/ports.py` 的 `PORT_CATALOG` 里登记一行

**前端（`npm run gen:module <name>` 能生成前 3 个文件）**

1. `studio/src/modules/<name>/module.ts` —— 路由 + 导航 + 权限 + 插槽
2. `studio/src/pages/<name>/XxxPage.tsx` —— 页面组装
3. `studio/src/entities/<x>/`（`model/types.ts` 从 `schema.ts` 派生、`api/*.api.ts`、`api/*.queries.ts`）
4. `studio/src/features/<x>/` —— 交互块（弹窗、工具栏）
5. 新权限点补 `entities/role/model/permission-catalog.ts`，否则角色编辑页授不了
6. `npm run verify` —— tsc + eslint + vitest + build 四关

---

## 7. 别踩的坑（都真实发生过）

| 坑 | 后果 | 怎么避免 |
|---|---|---|
| 改了接口没重新导出契约 | CI 字节比对失败；前端拿到过期类型 | `app export-openapi`，四份一起提交 |
| 页面忘了写 `permissions` | 对**所有已登录用户**放行，界面看起来完全正常 | `router.test.ts` 会红；确实无权限点时显式加进 `NO_PERMISSION_PATHS` 并写理由 |
| 权限码拼错 | 入口对所有人永久隐藏，不报错 | `permission-catalog.test.ts` 与后端逐条比对 |
| 业务模块互相 import | 架构测试红 | 用 `api/authn.py` / `api/accounts.py` 注入点 |
| 表单字段名照抄旧文档 | `password` vs `newPassword` 这种只在联调 422 时暴露 | 字段以 `schema.ts` 生成物为准 |
| 前端自己 `localStorage.setItem('sf_token')` | 「已登出，请求还带旧令牌」 | 令牌唯一真相是 session store |
| 自己写 `signOut()` | 换账号后旧账号缓存数据串号 | 一律走 `endSession(reason)` |
| 并发续期不单飞 | 一屏 6 个请求同时 401，5 个续期失败 → 用户莫名掉线 | `token-refresh.ts` 共享同一个 Promise |
| 拦截器里做跳转 | 与守卫打架，两个地址互相跳 | 跳转只由守卫读 `status` 决定 |
| 按完整业务码写分支 | identity 登录失败是 `40121` 不是 `40100` | 用 `code // 100` 取状态段 |
| mock 返回「方便页面」的结构 | 关 mock 接真后端时全面返工 | mock 严格按契约 |
| 归档盘路径写死 `Z:` | 换台机器直接 500 | `MEDIA_ARCHIVE_ROOT` 配 `.env`；本机用 UNC `//Z4S-XI5Y/内容中心素材盘/yufan_ai_studio` |

**连带改动清单**（改一处必须同步另一处）：

| 你改了 | 必须同步 |
|---|---|
| 任何接口的请求/响应 | `openapi.json` + `API.md` + `schema.ts`（权限点看 `permissions.ts`） |
| 新增权限点 | 后端 `ModuleSpec.permissions` + 前端 `permission-catalog.ts` |
| 新增插槽种类 | `shared/registry/types.ts` 的 `SlotName` + `FRONTEND_EXTENSION.md` |
| 新增模块错误码号段 | `docs/EXTENDING.md` §7 的号段分配表 |
| 新增配置项 | `core/config.py` + `api/.env.example` + README |

---

## 8. 命令与文档速查

```bash
# 后端
cd api
app serve --reload                  # http://127.0.0.1:8080/docs
app seed --reset                    # 建表 + 演示数据
app export-openapi                  # 生成四份契约（--check 只比对）
app import-archive-index            # 归档 JSON 索引 → assets 表（幂等，--dry-run 预演）
app sync-roles                      # 内置角色补齐缺失权限（只加不减）
app sync-roles --prune-snapshot     # 顺带把权限目录快照收缩成当前声明（自动路径只增不减）
app meta permissions.catalog        # 看这份状态的版本与写入轨迹
app prune-sessions --days 30        # 清理过期会话
pytest -q                           # 全量测试
ruff check . && mypy --platform win32 src

# 前端
cd studio
npm run dev                         # 5173，/api 代理到 8080
npm run verify                      # typecheck + lint + test + build
npm run gen:module <name>           # 生成模块骨架
```

| 文档 | 内容 |
|---|---|
| [`README.md`](../README.md) | 技术栈、启动、演示账号 |
| [`CODE_STYLE.md`](CODE_STYLE.md) | 命名、分层、错误码、注释要求 |
| [`FRONTEND_EXTENSION.md`](FRONTEND_EXTENSION.md) | 前端怎么加模块、插槽、权限、登录态接入 |
| [`REPLACEMENT_TEMPLATES.md`](REPLACEMENT_TEMPLATES.md) | 替换模板业务全流程 |
| [`REVIEW_CHECKLIST.md`](REVIEW_CHECKLIST.md) | 提交前自检清单 |
| [`BACKEND_REQUIREMENTS_IDENTITY.md`](BACKEND_REQUIREMENTS_IDENTITY.md) | 身份模块需求与接口清单 |
| `CODE_REVIEW_2026-11-05_*.md` | 历次评审记录（含每条问题的根因，排查同类问题时很好用） |

---

## 9. 最后一句话

这个仓库的设计目标是「**加功能时知道该改哪个文件**」：

- 后端加功能 = 加一个目录（`modules/<name>/`），不改装配代码
- 前端加功能 = 加一个清单（`modules/<name>/module.ts`）和它引用的页面，不改骨架
- 接口变了 = 跑一条命令生成四份契约（含权限点清单），前端自动跟上

如果你发现自己在改 `api/src/api/`、`app/`、`widgets/` 才能加业务，先停下来 ——
大概率是姿势不对，去看 `FRONTEND_EXTENSION.md` 或问一句，别硬塞。
