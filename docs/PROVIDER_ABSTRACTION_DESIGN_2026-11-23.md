# 供应商抽象重构方案（设计稿 · 待评审）

> 状态：**设计稿，评审要点已沉淀，尚未实施**（方案文档已提交 `ebf76f4`）
> 日期：2026-11-23
> 目标：新增供应商只需改 1 处；改配置 / 加供应商**无需重启服务**即动态生效
> 拍板结论（2026-11-23）：注册采用**驱动文件内自描述**；配置采用 **mtime 指纹、改 `.env` 即时生效**；先出方案文档、后实施

---

## 修订记录

| 日期 | 变更 | 对应章节 |
|---|---|---|
| 2026-11-23 | 初稿：现状评估、三层方案、迁移步骤、测试清单 | 全文 |
| 2026-11-23 | 拍板 D1–D4：路径前缀 / 刷新触发 / 配置缓存 / 迁移范围 | §6.3 |
| 2026-11-23 | 调研 LiteLLM / AI SDK / Dify / OpenRouter，修正「错误分类权归框架」 | §3.1.1 |
| 2026-11-23 | 拍板 D5：提交幂等提炼为共享能力（依赖 D6） | §6.3 |
| 2026-11-23 | 拍板 D6：回执落 DB / 工作区降级为临时缓存，新增阶段 0.5 | §6.5、§4 |
| 2026-11-23 | 核查上游文档：百炼图片支持 Base64，改为「通道抽象 + 分层决策」 | §3.1.4 |
| 2026-11-23 | 全面自查：补 G6–G10、假设验证、回滚与并发、资源排期与指标 | §1.1、§6.6、§7 |
| 2026-11-23 | 排版整理：章节重排、过程标记移入修订记录 | 全文 |

> 原章节标题里的「自查新增」「评审新增」「定稿」等过程标记已移入本表，标题只保留身份。

### 实施状态（2026-11-23）

| 部分 | 状态 | 产物 |
|---|---|---|
| 回执表 + 迁移 | ✅ 已完成 | `modules/model_driver/models.py::SubmissionReceipt`、`migrations/versions/a1c5f8203d47_*` |
| 回执仓储（抢占/恢复/状态机/迁移函数） | ✅ 已完成 | `modules/model_driver/receipts.py`、8 条测试全绿 |
| **bailian 驱动接入 DB 回执** | ✅ 已完成 | `_submit()` 改为「只读恢复 → 本地校验 → DB 抢占 → POST → DB 结算」；文件锁保留管进程互斥；32 条既有测试全绿 |
| CLI 存量文件回执迁移命令 | ✅ 已完成 | `app migrate-receipts [--dry-run]`（模块 CLI 注册）；坏行/冲突非零退出，**执行需人工冻结提交通道** |
| 工作区 GC（30 天） | ✅ 已完成 | `modules/model_driver/maintenance.py`，`leader_only=True`、6h 一轮；四道安全判据（终态 / 有 DB 回执 / 超 30 天 / 无锁） |
| 运维权限点 + 独立页面 | ⏳ 未做 | 阶段 3 |
| 阶段 0（A1 验证 + kit 骨架） | ✅ 已完成 | 21 条黄金样本；kit/errors.py；生产代码零改动 |
| 阶段 1 首批（grsai/grsai_video 迁移） | ✅ 已完成 | 错误分类 + `_suffix` 委托 kit；配置 getter 切 mtime 指纹；47 条驱动测试全绿 |
| 通道决策层 | ✅ 已建、⏳ 未接线 | `infra/media_channel.py` + 9 条测试；接线需改 pipeline 按素材类型分组读字节，风险超出「行为等价迁移」，留待专门评审 |
| 全量回归 | ✅ 已通过 | **1905 passed, 14 skipped**；ruff 全仓 / mypy 276 文件全绿 |

> ⚠️ **当前仍不能上线**：代码已切 DB，但尚未执行**生产环境**的存量文件回执迁移。直接上线会让「旧任务的文件回执」读不到 → 恢复路径判为无回执 → 二次计费。上线 runbook：①冻结 bailian 提交（运维停入口/下线模型）→ ②`app migrate-receipts --dry-run` 核对 → ③正式迁移，校验 100% → ④解冻。**冻结机制需人工执行，代码不内置开关**（见 §3 冻结小节）。

### 实施拍板（2026-11-23）

| # | 事项 | 拍板 |
|---|---|---|
| P1 | 阶段 0.5 | **独立先行上线**，不等 kit/基类重构（止血优先） |
| P2 | 回执迁移 | **直接切 DB**，不保留文件/DB 双写；不存在替换链 worker 要读文件。存量文件回执**全量迁移并校验**，冻结提交通道直到校验通过 |
| P3 | 资源 | **直接开始**，按关键路径推进 |
| P4 | 运维页面 | **单独新建一个运维页面**，承载刷新入口与审计（不塞进现有模型驱动页） |
| P5 | 刷新间隔 | **300 秒**（与 `image.reconcile` 同节奏）；`@periodic("model_driver.refresh", every=300, immediate=False)`；**不要 leader_only**——registry 是进程内单例，leader-only 会让其他实例永不刷新 |
| P6 | 文件回执保留期 | **30 天**（与 `library_trash_retention_days` 同口径），只读安全网，到期由 GC 删除 |
| P7 | 最终目标 | 健壮、**高扩展**、**生产级**系统 —— 不是「能跑就行」的演示实现 |

> P2 同时更正了一个过时的架构前提：文档此前反复写的「替换链 worker 必须读文件回执 / 与替换链耦合不可动」**不成立**。核实见 §6.5。

---

## 0. 摘要

当前架构**已经有一半是声明式的**：`drivers/` 目录扫描自动发现、`preferred_for` 声明默认模型、`ModelSpec.param_schema` 驱动前端表单、`driver_config(prefix)` 统一配置工厂 —— 加一个供应商**不用改注册表**。

但「加一个供应商要改多处」依然成立，原因不在注册，而在两处没有抽象边界的地方：

1. **执行期样板没有进契约** —— 「怎么发一次 HTTP、怎么把上游状态翻成平台状态、怎么把错误码翻成异常」全在契约之外，各驱动各写一份，且已实际写错（见 §2.4 的错误分类事故）。
2. **供应商特有功能没有隔离边界** —— LibTV 是唯一带账号面板 / 排队查询 / 自投作业的供应商，它的代码被塞进**公共**的 `schema.py` / `router.py` / `__init__.py` / `core/config.py`。这导致：任何带专属能力的供应商必然要改公共文件，而 LibTV 的每次改动也波及公共文件。

本方案四层 + 一项前置修复：

| 层 | 做什么 | 消除什么 |
|---|---|---|
| **前置 D6** | 回执落 DB（唯一约束）+ 工作区 GC + 素材字节 S3 唯一副本 | 「工作区误删 → 二次计费」现网风险、磁盘无限增长 |
| **L1 `kit/`** | 供应商工具箱：HTTP、错误分类、模型 id、素材分类、配置、提交幂等；控制流统一到 final 基类 | 驱动内 7 类样板 × 2–5 份、控制流语义漂移 |
| **L1.5 通道抽象** | Base64 / OSS / 公网 URL 三种通道由框架实现，驱动只声明上游能力与偏好 | `inline_references: bool` 粒度不足、传输实现散落 |
| **L2 驱动内自描述** | 专属端点 / 作业 / schema 由驱动导出并自动挂载 | 4 处公共文件里的供应商硬编码 |
| **L3 运行时刷新** | `registry.refresh()` + 配置去 `lru_cache` | 「必须重启」这个约束本身 |

> 实施顺序不是「四层并行」：D6 是 D5 的前置，L1.5 随 L1 一起迁移，L2 依赖 L1 的驱动清理，L3 最后。详见 §4 与 §7。

---

## 1. 目标与验收标准

| # | 目标 | 可度量验收 |
|---|---|---|
| G1 | 新增供应商不改公共文件 | 加一个新供应商，git diff 只碰 `drivers/` + `tests/`；`core/`、`infra/`、`modules/*`（除 model_driver 自身）零改动 |
| G2 | 供应商特有功能自带 | 带专属端点 / 自投作业的供应商，公共 `router.py` / `schema.py` / `__init__.py` 零改动 |
| G3 | 改配置无需重启 | 改 `.env` 里的 `GRSAI_API_KEY` → 下一次请求即生效（无需重启进程） |
| G4 | 加驱动文件无需重启 | 往 `drivers/` 放一个文件 → 刷新后 `GET /models` 能看到它的模型 |
| G5 | 错误分类只有一处实现 | 「缺 key / 上游 4xx / 上游 5xx / 上游 200 但回报 failed」四种情形的平台状态码，全仓只有一处判定代码 |
| G6 | 控制流统一 | 6 个 HTTP 驱动的 `submit` / `poll` / `cancel` 由同一个 final 基类模板驱动；驱动内不再有独立的状态机副本 |
| G7 | 提交回执不丢 | 工作区被删除后，同一 `request_id` 的恢复路径仍能读到回执，**不产生第二次上游提交** |
| G8 | 通道按类型声明 | 同一驱动可对 `image` 声明 Base64、对 `audio` 声明 OSS；三种通道实现各只有 1 份；可行性错误在**启动期**暴露 |
| G9 | 磁盘不再无限增长 | 工作区 GC 可运行；一轮 GC 后磁盘占用下降且无任务失败 |
| G10 | 迁移不破坏存量 | 存量回执 100% 迁入 DB 并校验；迁移期间不产生重复提交；回滚演练通过 |

**约束性目标（不可为达成 G1–G10 而牺牲）：**

- C-1 **不重复计费** —— 任何重构动作（刷新、回退、GC、迁移）不得让同一 `request_id` 产生第二次上游提交
- C-2 **在途任务不中断** —— 刷新/迁移期间已受理的任务必须继续跑完或明确失败，不能变成「无主任务」
- C-3 **不静默降级** —— 通道回退、配置覆盖、端点挂载失败都必须留痕；宁可启动失败，不可静默少能力

G5 单独强调：这不是洁癖。2026-11-23 就因为**语义在 `generation/runner.py` 一处、实现散在 4 个驱动**，导致「缺 API key」被抛成 502 → 框架判「可能已受理」→ 用户看到「提交结果未知，请核对上游任务」，而上游根本没收这次请求。

### 1.1 关键假设与验证计划

以下假设是方案成立的前提。**任一验证失败，对应阶段必须重新设计，不得带着假设硬上。**

| # | 假设 | 何时验证 | 验证方法 | 失败后果 / 处置 |
|---|---|---|---|---|
| A1 | 各驱动的 `_http_error` / `_json` 等样板**语义等价**，可以合成一份 | 阶段 0 | 逐行比对 4 个驱动的函数，产出差异清单 + 黄金样本测试 | **❌ 2026-11-23 验证失败**：4 组里仅两个 `_suffix` 逐字等价；HTTP 映射/剥前缀/素材分类都不可合并（差异是业务语义，如 `agnes_video` 把 `5xx+video_queue_full` 判 429、其他判 502；429 在 agnes 是 `RateLimited`、grsai 是 `BadRequest`）。**按预案处置**：G5 降级为「同语义同状态码」而非「同一函数」；kit 只抽确定等价件，其余保留驱动私有并以**声明式映射表**约束 |
| A2 | 存量文件回执**全部可读、可解析、可映射到 DB** | 阶段 0.5 前 | 只读扫描全部 `records/bailian-submission.json`，统计损坏/缺失/模型不一致 | 有损坏 → 先人工修复清单再迁移；**不得跳过损坏项**（跳过 = 二次计费风险） |
| A3 | 历史素材字节仍可从 S3 重新读取（D6 的重建前提） | 阶段 0.5 前 | 抽样 `referenceKeys` 对应对象，检查存在性与完整性 | 存在丢失对象 → D6 改为「仅新任务使用 S3 唯一副本」，存量档案保留磁盘副本并单独登记 |
| A4 | `inline_references` 声明与真正走哪条通道**一致**（尤其 agnes 生图链路无条件内联） | 阶段 1 前 | 按驱动 × 素材类型穷举，记录实际通道 | 声明与实现不符 → 先修声明，再迁 kit；否则通道抽象会把错的行为固化 |
| A5 | 驱动 import 无不可重复的副作用，新增文件热加载安全 | 阶段 3 前 | 同一驱动 import 两次 + `refresh()` 两轮，检查注册表与路由是否稳定 | 有副作用 → L3 降级为「仅管理端点手动刷新且需重启生效」，G4 撤回 |
| A6 | libtv 的工作区需求与通道选择**正交** | 阶段 1（libtv 迁移时） | 让 libtv 在通道抽象下跑一次完整任务，确认 `canvas-run.mjs` 的 sha256 校验不受影响 | 不正交 → libtv 保持独立通道实现，G8 对 libtv 例外 |
| A7 | 通道可行性错误能在**启动期** Detection（公网域名配置、OSS 凭据端点） | 阶段 1（通道抽象上线时） | 故意错配配置启动，确认启动失败而非运行时 400 | 无法启动期检测 → 增加受理期校验守卫，禁止进入提交阶段 |

---

## 2. 现状评估

### 2.1 已经做对的部分（本方案不重做）

| 机制 | 位置 | 作用 |
|---|---|---|
| 目录扫描自动发现驱动 | `registry.py:62` `_discover()` | 加驱动不用改注册表 |
| 驱动自愈注册（按模型清单区分同名实例） | `registry.py:124-148` | 路由被重置后能自己接回去 |
| `preferred_for` 声明默认模型 | `infra/ai/router.py:59-142` | 默认不再由 import 顺序决定 |
| `driver_config(prefix)` 统一配置工厂 + 自描述 ClassVar | `infra/driver_config.py:38-79` | 配置前缀 / 必填项 / 提交预算可声明 |
| `ModelSpec` 携带 `param_schema` / `internal` / `placeholder` | `infra/ai/base.py:82-115` | 前端表单与模型目录由声明驱动 |
| 参数与限制按模型 schema 解析 | `modules/task/service.py:333-346`（`resolve_*`） | 计费模式 / 风格预设 / 额外参数都走声明，不按供应商分支 |
| 加载期契约自检 | `modules/model_driver/spec.py` | 单个驱动不合格只红自己 |
| 前端零供应商分支 | `studio/src/**`：硬编码仅出现在测试 / mock，LibTV 独立在 `entities/libtv/` | 加供应商不动前端 |
| 数据库无 provider 枚举列 | `image_jobs` / `tasks` 存模型 id 字符串 | 加供应商不需迁移 |

### 2.2 重复改动的具体位置与范围

#### A. 驱动内部：同一份样板被复制 N 遍

按 `drivers/*.py` 全量函数清单比对：

| 样板职责 | 重复位置 | 份数 |
|---|---|---|
| HTTP 错误 → 平台异常映射 | `agnes.py:339`、`agnes_video.py:606`、`grsai.py:133`、`bailian.py:332-343`（内联在 `_json` 里） | 4 |
| JSON 解析兜底 | `agnes_video.py:596`、`grsai.py:152`、`bailian.py:338-340` | 3 |
| 剥模型 id 前缀 | `agnes_video.py:431`、`grsai.py:643`、`bailian.py:178` | 3 |
| 错误细节 / 错误码提取 | `agnes_video.py:653`、`:668`、`grsai.py:731`、`grsai_video.py:352` | **4** |
| 数值 / 时长 / 画幅解析 | `agnes_video.py:444`、`:463`、`grsai.py:662`、`:724`、`grsai_video.py:345` | **5** |
| 参考素材分类 | `agnes.py:312`、`agnes_video.py:473`、`grsai.py:163`+`:187`、`bailian.py:206`（`_validated_inputs`） | 4 |
| Settings 类 + `get_xxx_settings()` + `lru_cache` | `agnes.py:147`/`:169`、`agnes_video.py:191`/`:206`、`grsai.py:83`/`:97`、`bailian.py:143`/`:153` | **5** |
| **提交幂等 / 回执（防重复计费）** | **仅 `bailian.py:20-27, 479` 有**（`O_EXCL` 抢占 + 回执 + `submission_unknown` 禁止重提） | **独一份，该共享而未共享** |

> ⚠️ 最后一行是本表最重要的发现：**防重复计费这个能力，框架只有「分类」（`_possibly_delivered`），而 bailian 在驱动内部做了「机制」**（提交前 `O_EXCL` 抢占 `records/bailian-submission.json` + 锁文件；4xx 记 `rejected`，超时/5xx/畸形记 `submission_unknown` 且一律禁止自动重提）。2026-11-23 那次「提交结果未知」事故缺的正是这套机制，而它现在只在一个驱动里。
>
> 另外注意 `bailian.py:332-343`：它把「HTTP 错误映射」与「JSON 解析」合在同一个函数里，而 `agnes`/`grsai` 分成两个 —— **样板连形状都不统一**，这正是 kit 要解决的问题（而不是「bailian 不适用」）。

驱动体量（供应商名提及行数 / 文件总行数）：

| 驱动 | 提及 | 总行数 |
|---|---|---|
| `grsai.py` | 67 | 741 |
| `agnes_video.py` | 58 | 677 |
| `grsai_video.py` | 50 | 362 |
| `agnes.py` | 45 | 313 |
| `libtv.py` | 32 | 353 |
| `bailian.py` | 28 | 582 |
| `echo.py`（占位） | 1 | 135 |

> ⚠️ 上表「等价性」是按职责与函数名比对得出的，**未逐行核对每个同名函数的实现** —— `agnes_video._http_error` 与 `grsai_http_error` 的映射规则可能已有差异。阶段 0 的首要任务就是把这个差异查清并固化成契约测试（§5）。

#### B. 驱动目录之外：真正的「改多处」

| # | 位置 | 硬编码内容 | 性质 |
|---|---|---|---|
| B1 | `modules/model_driver/__init__.py:19-20, 36-38` | `libtv.download` 作业写死在模块装配（`jobs=(LIBTV_DOWNLOAD,)`） | **代码硬编码** |
| B2 | `modules/model_driver/schema.py:100-180` | 11 个 LibTV 专属输出 schema 混在公共 schema 文件 | **代码硬编码** |
| B3 | `modules/model_driver/router.py:19, 175-276` | LibTV 专属 3 个端点写进公共 router | **代码硬编码** |
| B4 | `core/config.py:80-82` | `node` 字段用 `AliasChoices("NODE_BINARY", "LIBTV_NODE")` —— 公共配置字段带着某供应商的私有别名 | **代码硬编码** |
| B5 | `core/param_schema.py:16,77`、`core/reference_limits.py:3`、`core/prompt_refs.py:30`、`infra/http.py`、`infra/archive/{s3,runs}.py` | 注释里的供应商口径举例（「Agnes 256–5760」「百炼 240–8000」「LibTV 1–15 秒」） | **文档式耦合**（改限制时容易照旧注释改错） |

**已排除的两处误判**（核实后降级，列出以免后续重复排查）：

- `modules/task/service.py:338` 的「当前只有 LibTV 有免费/积分两条队列」**只是注释**；真实逻辑是 `resolve_billing_mode(spec, ...)` —— 已按模型 schema 声明 ✓，不是硬编码。
- `core/config.py` 的默认组合（LibTV + Agnes）由 `preferred_for` 决定，不是配置里的顺序 ✓。

#### C. 「无需重启即可动态生效」的三处缺口

| # | 位置 | 现状 | 后果 |
|---|---|---|---|
| C1 | `registry.py:114-117` | `ensure_drivers_registered()` 只在 `_state is None` 时才 `_discover()` | 新增驱动文件 → **必须重启** |
| C2 | 各驱动 `get_xxx_settings()` 的 `@lru_cache` | 配置是进程内永久快照 | 改 `.env` → **必须重启**（2026-11-23 的 `GRSAI_API_KEY` 空值事故即源于此） |
| C3 | `infra/driver_config.py:75-79` | `DriverSettings` 靠 `__init_subclass__` 的 import 副作用登记 | 未被 import 的驱动，其配置不进生产巡检 |

**已存在、可复用的能力**（不要重复造）：

- `registry._discover()` 本身是幂等重扫（`registry.py:62`）
- `registry.reset_drivers()` 清空发现结果（`registry.py:179-187`，注释写「仅测试使用」）
- `registry.py:124-148` 已能区分「同名等价实例」与「真重名」
- `ModelRouter.unregister()`（`infra/ai/router.py:253`）

所以 L3 的真实缺口不是「实现重扫」，而是**没有生产可调用的入口**，以及**配置读取那一层**。

### 2.3 根本原因

**三条，按重要性排序：**

1. **契约只描述「能力」，不描述「执行模板」。**
   `ModelProvider` / `ModelSpec` 说清了「我有什么模型、什么参数、什么限制」，但「怎么发一次 HTTP、怎么把上游状态翻成 `GenerationState`、怎么把错误码翻成平台异常」**不在契约里** → 必然各写一份 → 复制粘贴式演化 → 语义漂移（`grsai` 的 `_suffix` 与 `agnes_video` 的 `_error_detail` 就是同源分叉）。
   **症状**：同一个语义（"缺 key = 请求没发出"）在不同驱动实现成不同状态码。

2. **供应商特有功能没有隔离边界。**
   LibTV 是唯一带账号面板 / 排队查询 / 自投作业的供应商，于是它的代码被塞进**公共**文件。这造成双向成本：加新供应商必然改公共文件；LibTV 每次改动也波及公共文件。**这是重构成本的主项**，比驱动内样板复制更重要。

3. **配置是「代码里声明 + 进程内缓存」的双重绑定。**
   新增供应商必须照抄 5 行 `Settings` 样板 + 写一个 `lru_cache` getter；而 `lru_cache` 天然是启动期快照，与 G3 直接冲突。

### 2.4 一条真实事故（抽象不足的代价）

2026-11-23，`grsai` 驱动接入后首个任务 `77e420e3…` 立即失败：

```
error_message = 提交结果未知：请求已发出但未确认供应商是否受理，为避免重复计费未自动重试
external_id = None, submit_attempts = 1
```

真实原因：`GRSAI_API_KEY` 为空，请求**根本没发出去**。

成因链：框架在 `generation/runner.py` 用 `_possibly_delivered()` 按 HTTP 语义分类（4xx / 连接失败 = 确定没送达 → 允许安全重试并给真实原因；5xx / 408 = 可能已受理 → 改写成上面那句）。但「缺 key 抛哪种异常」由**每个驱动自己决定** —— `grsai` 抛了 `UpstreamError`（502）→ 落进「可能已受理」那一侧 → 措辞误导，用户被引导去核对一个上游根本不存在的任务。

**这不是「有人写错了」，是「同一个语义有两处真相，其中一处必然漂移」。** 抽象的目标就是把这类判断收进一处。

### 2.5 与既有文档的关系

| 既有文档 | 关系 |
|---|---|
| `docs/EXTENSIBILITY_GAPS.md` A1「provider 的私有配置没有归属地」、D3「`.env.example` 与 `Settings` 靠人工对齐」 | 本方案是这两条的**落地实现**，不重复列出，实施后回填其「修复进展」 |
| `docs/EXTENSIBILITY_GAPS.md:106` 记录的承诺 | 「新增供应商不需要改动 `core/config.py`」—— 本方案让这句承诺**对所有供应商成立**（今天只对零配置的 `echo` 成立） |
| `docs/MODEL_DRIVER_STANDARD.md`（驱动开发标准） | 实施后需同步：§2 成员表、§3 供应商形态、§10 明确不做的事 |
| `api/docs/EXTENDING.md`「加一个供应商」章节 | 实施后需同步：清单从「改 6 处」缩到「改 1 处」，这本身就是 G1 的验收证据 |

---

## 3. 方案设计

### 3.1 L1：供应商工具箱 `modules/model_driver/kit/`

目标：驱动只写**供应商特有**逻辑，通用样板由 kit 承担。

| 模块 | 职责 | 收敛的样板 |
|---|---|---|
> ⚠️ **A1 验证后收缩（2026-11-23）**：原表按「函数名/职责」估计的收敛份数**不成立**。逐行比对证明 4 组里仅两个 `_suffix` 逐字等价；HTTP 映射、剥前缀、素材分类各驱动的差异是**业务语义**（详见 §1.1 A1）。下表是收缩后的实际范围 —— **只抽确定等价件**，其余靠「声明式映射表 + 框架统一执行」约束，不强行合成函数。

| 模块 | 职责（收缩后） | 依据 |
|---|---|---|
| `kit/errors.py` | **框架持有唯一分类实现**：按驱动声明的 `status_map` / `error_map` 执行；统一 `suffix(error)`（两个 `_suffix` 逐字等价） | A1 验证 + 调研 LiteLLM |
| `kit/settings.py` | `vendor_settings(prefix, cls)` 工厂 + mtime 指纹缓存 | Settings 样板 ×5 结构等价（未逐行比对，风格一致） |
| `kit/submission_guard.py` | 提交前**DB 唯一约束抢占** + 「可能已受理」禁止自动重提 | **阶段 0.5 已实现**（`receipts.py`，作为件迁入） |
| ~~`kit/http.py`~~ | ❌ 不抽 —— 4 份映射规则互异，抽了只能靠参数开关还原差异 | A1 验证 |
| ~~`kit/models.py::strip_prefix`~~ | ❌ 不抽 —— agnes_video 是双别名归一、grsai 才剥前缀、bailian 是精确查表 | A1 验证 |
| ~~`kit/media.py::classify`~~ | ❌ 不抽分类 —— 无分类/MIME+协议/MIME+后缀三种维度，失败行为从「不报错」到「严格报错」 | A1 验证；但「认不出就报错」的默认仍在 grsai 侧 |

#### 3.1.1 错误分类：语义收进一处（G5）

**决定权分层（调研后修正，对应 LiteLLM 的做法）**：驱动**不实现分类函数**，只**声明映射数据**；框架持有唯一分类实现并按表执行。若把 `map_error` 做成子类钩子，决定权就又下放——2026-11-23 那类「同语义不同状态码」必然复发。

```python
# 驱动声明（数据，不是代码）—— 映射表按 HTTP 状态码，不按函数名：
http_status_map: Mapping[int, type[AppError]]  # 如 {401: Unauthorized, 429: RateLimited}
# 例外：agnes_video 的 5xx+video_queue_full → RateLimited 属业务码例外，
# 用 error_code_overrides: Mapping[tuple[int, str], type[AppError]] 声明，不写死在框架
error_code_overrides: Mapping[tuple[int, str], type[AppError]] = {}

# 框架执行（唯一实现）：kit/errors.py::classify_http_error() 按表分类并抛异常
def classify_http_error(
    response: httpx.Response,
    *,
    status_map: Mapping[int, type[AppError]],
    code_overrides: Mapping[tuple[int, str], type[AppError]] | None = None,
    vendor: str,
) -> AppError: ...
```

> **A1 验证后的关键调整**：原设计写的是「驱动提供供应商名，框架按固定口径分类」—— 逐行比对证明**口径本身各驱动不同**（429 在 agnes 是 `RateLimited`、grsai 是 `BadRequest`；401/403 同理；agnes_video 有业务码例外）。现在改为「驱动声明映射表，框架执行」：状态码→异常的**决定权仍在驱动**（尊重业务语义），但**分类实现只有一份**（消除 4 份代码漂移）。G5 据此降级为「同语义同状态码」—— 不是「所有驱动用同一张表」。

**唯一判定点**，驱动只提供映射表和错误细节：

```python
# kit/errors.py（唯一实现）
def http_error(response: httpx.Response, *, status_map, code_overrides=None, vendor: str) -> AppError:
    """上游 HTTP 错误 → 平台异常。分类口径由驱动的映射表给定，本函数只执行。"""
```

配套约定：

- **缺配置**（没配 key / 没配 base_url）属于「请求没发出」→ 一律 `BadRequest`（4xx）。这是 2026-11-23 事故的直接修法，且**一次修复、四个驱动同时受益**。
- **上游 200 但响应体回报失败**（如 `status: failed`）→ 上游明确拒绝 = 没受理 = 不会计费 → 4xx 侧。
- 框架侧（`generation/runner.py` 的 `_possibly_delivered`）**不改**：它是对的，问题在驱动的实现散落。

#### 3.1.2 素材分类：默认严格（G5 同源）

`kit/media.py` 的 `split_reference_media()` **默认对认不出格式的素材报错**，而不是跳过。理由：提示词里的 `{{Mixed N}}` 编号按**上传顺序**算，跳过一个素材，后面每个编号都指错对象 —— **出片照样成功，只是内容不对**（最难查的一类错）。

现状：`grsai.py` 已手动修过两处（`grsai.py:163-187`），`agnes.py` 同类问题仍在。抽象后这类规则只有一份，且默认从宽变严。

#### 3.1.3 配置：mtime 指纹缓存（G3）

```python
# kit/settings.py
def vendor_settings(prefix: str, cls: type[DriverSettings]) -> DriverSettings:
    """按前缀读配置，缓存以 .env 文件 mtime + 进程内环境变量快照为键。

    改 .env → 下次调用即重建配置（无需重启）。
    """
```

- 缓存键：`(.env` 的 mtime_ns + 进程内环境变量快照指纹`)。
- 每请求 1 次 `stat()` —— IO 可忽略。
- 读配置失败**不缓存**（否则坏配置会被粘住）。
- 迁移期兼容：保留 `lru_cache` 版入口给未迁移的驱动，两者行为一致（后者不热生效）。

#### 3.1.4 素材通道：抽象实现，分层决策

**事实基础**（核查阿里云官方文档后修正，先前「data URI vs `oss://` 由上游能力决定」的说法不准确）：

| 素材 | 公网 URL | OSS 临时 URL | Base64 data URI | 上限 |
|---|---|---|---|---|
| 百炼图片 `img_url` | ✓ | ✓ | **✓** `data:{MIME};base64,…` | 20MB（wan2.6/2.5）/ 10MB（wan2.2/wanx2.1） |
| 百炼音频 `audio_url` | ✓ | ✓ | **✗**（文档未列，不按支持处理） | 15MB、3–30 秒 |
| Agnes / Grsai 图片 | ✓ | — | ✓ | `image/pipeline.py:333`；Grsai OpenAPI「支持 base64 与 url 链接」 |

**真实约束有三条，只有一条来自上游：**

1. **我们给不出公网 URL** —— `image/pipeline.py:330-332`：取件链接是私签、几分钟过期、上游访问不到。**我们这侧**的限制。
2. **百炼音频不支持 Base64** —— 唯一真正的**上游**限制 → 音频必须走 OSS 临时 URL。
3. **libtv 的 Node 桥接要真实路径**（`runs.py:1-12`）—— **我们这侧**的执行器限制。

**抽象形态：三种通道都实现，决策权分层**

| 角色 | 拥有什么 | 不拥有什么 |
|---|---|---|
| **框架** | 三种通道的实现、可行性校验、回退决策、审计事件 | 不猜供应商的上游能力 |
| **驱动** | 声明「上游接受哪些格式」（**事实**）+ 可选首选通道（**偏好**，需注明理由） | 不自己实现传输、不做「三选一自由选」 |

```python
class MediaChannel(StrEnum):
    BASE64 = "base64"          # S3 字节 → data URI
    OSS = "oss"                # S3 字节 → 上传上游签发的临时目录 → oss://
    PUBLIC_URL = "public_url"  # 仅当素材本身有公网可访问地址

# 驱动声明上游能力（事实，按素材类型）:
accepted_media_formats: Mapping[MediaKind, frozenset[MediaChannel]]
# 百炼：image={BASE64, OSS, PUBLIC_URL}, audio={OSS, PUBLIC_URL}
# Agnes/Grsai：image={BASE64, PUBLIC_URL}

# 可选：首选通道（偏好，必须注明理由）
preferred_channel: Mapping[MediaKind, MediaChannel] | None = None
```

**框架决策顺序：**

1. **按素材类型分别选通道** —— 同一驱动可混合（百炼图片 Base64 + 音频 OSS），这是 `inline_references: bool` 表达不了的粒度
2. **启动期可行性校验**：
   - 声明 `PUBLIC_URL` 但未配置公网域名 → **启动失败**（当前私签 S3 给不出公网 URL）
   - 声明 `OSS` 但上游无上传凭据端点 → 启动失败
   - 声明的格式与上游能力矩阵冲突（如 `audio` 只声明 BASE64）→ 启动失败
3. **运行时按优先级回退**：首选不可用（素材超通道上限 / 凭据失效）→ 选次选，并记**审计事件**（含原通道/实际通道/原因）
4. **通道实现统一在框架**：`BASE64` 复用 `infra/reference_inline.py::inline_data_uris()`；`OSS` 提炼 `bailian.prepare_inputs` 的上传逻辑为通用件；`PUBLIC_URL` 仅当配置了公网域名才启用

**为什么不做成「三选一自由选」**：公网 URL 在当前架构下**实际不可用**，暴露为对等选项会误导驱动作者 → 运行时拿到上游 400「图片无法访问」，而根因在我们这侧。把可行性校验前置到启动期，符合项目「配置错误要早炸」的纪律。

**推论**：图片全部可走 Base64（含 bailian）→ 不再落盘、不再 OSS 上传；bailian 音频走 OSS（唯一上游特例）；libtv 的真实目录是执行器约束，与通道选择**正交**，仍保留但可临时化（D6）。

> ⚠️ `bailian.py:11` 的注释「Wan 只接受公网 URL 做参考素材，不认本地文件」与上游文档不符 —— 这是「通道被误判为上游能力约束」的根源。迁移前先更正该断言，并复核所有依赖它的设计。
>
> ⚠️ **边界**：`kit/media.py` 只做分类与严格性；通道的**实现**放 `infra/` 层而不是 kit —— 否则 kit 会退化成半个框架，并把执行器工作区的档案格式变成隐式契约。

### 3.2 L2：驱动内自描述（G1 / G2）

拍板结论：**不引入独立 YAML 清单**。配置与专属能力都写在驱动文件里，目录扫描自动生效 —— 避免第二个真相来源。

#### 3.2.1 驱动可导出「专属面」

```python
# drivers/xxx.py
class Driver:
    ...

# 可选：供应商专有的 HTTP 端点（LibTV 的账号 / 排队面板）
EXTRA_ROUTES: tuple[APIRouter, ...] = ()
# 可选：供应商自己投的作业（LibTV 的 download）
EXTRA_JOBS: tuple[JobSpec, ...] = ()
# 可选：供应商专有的输出 schema
EXTRA_SCHEMAS: tuple[type[BaseModel], ...] = ()
```

`registry` 扫描时收集并自动挂载。**需要改的公共代码只有这一处（一次性）**：

- `modules/model_driver/__init__.py:19-20, 36-38`：删掉写死的 `LIBTV_DOWNLOAD`，改为从扫描结果汇总 `jobs`
- `modules/model_driver/router.py:19, 175-276`：删掉 LibTV 的 3 个端点，改为遍历 `EXTRA_ROUTES` 挂载
- `modules/model_driver/schema.py:100-180`：LibTV 专属 schema 迁进 `drivers/` 侧或 `modules/model_driver/libtv/` 侧

> ⚠️ 注意 `__init__.py:36-37` 的注释写着「声明是义务：漏了这句会启动失败，而不是静默少一个 handler」—— 这条纪律要保留到新机制里：**挂载失败必须启动失败**，不能静默少一个端点。

#### 3.2.2 配置归属

- `core/config.py:80-82` 的 `node` 字段（带 `LIBTV_NODE` 别名）迁进 libtv 自己的 Settings：

  ```python
  # drivers/libtv.py（或 modules/model_driver/libtv/settings.py）
  class LibtvSettings(DriverSettings):
      node: str = Field(default="node", validation_alias=AliasChoices("NODE_BINARY", "LIBTV_NODE"))
  ```

  部署侧**不用改 `.env`**（`AliasChoices` 继续认旧名）。
- 公共 `core/config.py` 只保留与供应商无关的项（`storage_driver`、`queue_driver` 等）。

#### 3.2.3 新增供应商的新工作流

| | 现状 | 目标 |
|---|---|---|
| 驱动文件 | `drivers/xxx.py` | `drivers/xxx.py`（不变） |
| 配置声明 | 驱动内 Settings 类 + `get_xxx_settings()` + `lru_cache` | 驱动内 Settings 类（样板由 `kit/settings.py` 承担） |
| 注册 | 无（自动扫描） | 无（自动扫描） |
| 专属端点 / 作业 | 改公共 `router.py` / `__init__.py` / `schema.py` | 驱动内 `EXTRA_*` |
| 私有部署参数 | 加进公共 `core/config.py` | 驱动自己的 Settings |
| `.env` | 手写一段 | 手写一段（**必须手写**，不能自动 —— 见 §6） |
| 前端 | 无需改（若用 `param_schema`） | 无需改 |
| 数据库 | 无需改（无 provider 枚举列） | **新增提交回执表 + 存量迁移（D6）** —— 这是本方案唯一的 schema 变更 |

**目标：公共文件改动 = 0。**

### 3.3 L3：运行时刷新与配置热生效（G3 / G4）

#### 3.3.1 配置热生效

由 §3.1.3 的 mtime 指纹缓存承担 —— 改 `.env` → 下次请求生效，无需重启。

#### 3.3.2 驱动热加载

已有能力：`registry._discover()` 幂等重扫、`reset_drivers()`、`registry.py:124-148` 的同名自愈、`ModelRouter.unregister()`。

要补的：

1. `registry.refresh()` —— 生产可调用：
   - 清 `_state` → 重扫目录 → 重新 import 新增模块
   - 路由侧：先 `unregister()` 旧实例再 `register()` 新实例（`register` 同名会抛 `RegistryError`，见 `infra/ai/router.py:82`）
   - **已注册的驱动文件内容变更 → 不热替换**（`importlib.reload` 会丢 in-flight 实例状态，收益不抵风险）。只有**新增文件**才热生效。
2. 入口：管理端点 `POST /model-driver/refresh`（管理员权限）或挂到已有的 reconcile scheduler。

> 边界必须写进 `MODEL_DRIVER_STANDARD.md`：**加文件 = 热生效；改已加载的文件 = 需重启**。

---

## 4. 迁移步骤

每个阶段独立可发布、可回滚。

### 阶段 0：固化行为（不动生产代码）—— ✅ 2026-11-23 完成

**实际产出（与原文计划的差异已按 A1 验证结果调整）：**

| 项 | 结果 |
|---|---|
| A1 逐行比对 | ✅ 完成：4 组里仅两个 `_suffix` 逐字等价，其余不可合并（详见 §1.1 A1） |
| 黄金样本测试 | ✅ 21 条（`tests/test_driver_kit_errors.py`），逐条还原 4 个驱动各自的 HTTP 状态口径 |
| `kit/errors.py` | ✅ 框架唯一分类实现 + `suffix()`；驱动用 `status_map` / `code_overrides` 声明口径 |
| 生产代码 | ✅ 零改动（驱动仍在用自己的函数；kit 尚未被引用） |
| 408 口径修正 | ✅ kit 默认归 5xx 侧（可能已受理），grsai 用 `status_map={408: BadRequest}` 显式覆盖 |

> 原计划「建全部 kit 骨架」按 A1 结果收缩为只建 `errors.py`（唯一确定等价件）+ `settings.py`（阶段 1 随配置迁移建）。其余不抽（见 §3.1 收缩表）。
>
> **阶段 1 首批迁移结果（2026-11-23）**：`grsai` / `grsai_video` 的 HTTP 错误分类与 `_suffix` 已委托 kit（47 条测试全绿，行为等价）。
> `agnes` / `agnes_video` **保留私有**：404 有定制文案（「当前密钥分组未开通…可用模型见 GET /api/v1/models」），401/403/429 也各有专门提示；`agnes_video` 还有业务码例外。kit 的 `status_map`/`code_overrides` 只表达异常类型，表达不了逐状态的定制文案 —— 强迁会破坏行为等价。这正是 A1 预案「其余保留驱动私有」的落地。

### 原阶段 0 计划（留档）

1. 把各驱动的样板实现**逐行比对**，查清差异（重点：`agnes_video._http_error` vs `grsai_http_error` 的映射规则差异）。
2. 为**已存在的行为**写契约测试（§5.1–§5.3 的错误分类、素材分类、参数解析）—— 用现有驱动的真实行为做黄金样本。
3. 建 `kit/` 骨架，函数先只被测试引用，**生产代码一行不动**。

> ⚠️ **时序修正（自查）**：§5.4–§5.7 的配置 mtime、刷新、GC、DB 回执、通道选择测试**依赖尚不存在的功能**，无法在阶段 0 写「黄金样本」。它们分别在阶段 0.5 / 1 / 3 随实现一起写。阶段 0 只固化**现有行为**，不自造未来行为的测试。

**验收**：契约测试全绿；黄金样本与现状逐项一致；A1 假设的差异清单已产出。

### 阶段 0.5：回执落库 + 工作区 GC（D6，必须先于阶段 1）—— 已拍板单独先行上线

1. 新建提交回执表（`request_id` 或 `(provider, run_id)` 唯一约束），字段含 `status` / `task_id` / `model` / `channel_used` / 时间戳。
2. 把 `records/bailian-submission.json` 与锁文件**全量迁入 DB**；迁移并校验通过前**冻结 bailian 提交通道**（不做双写 —— 2026-11-23 拍板：不存在替换链 worker，无需兼容文件读）。
3. `_submit()` 改为读 DB 回执，`claim()` 改为 DB 唯一约束；**冻结窗口结束后不再读文件**。
4. 加工作区 GC：只清理**已迁 DB 且终态**的 run 目录；libtv 桥接所需目录保留但可重建。
5. 文件回执**只读保留 30 天**（与 `library_trash_retention_days` 同口径）后由 GC 删除；30 天是回滚安全网，不是第二真相源。
6. `runs.py` 的部署自检话术同步更新（当前「换机器显示档案不存在」将不再适用于回执）。

**验收**：删掉工作区后 bailian 任务仍能凭 DB 回执继续轮询；同一 `request_id` 重复提交不产生第二次上游请求；迁移校验 100% 通过；GC 跑一轮后磁盘占用下降且无任务失败。

> ✅ **2026-11-23 拍板：本阶段独立先行上线**（不等 kit/基类重构）。它修的是现网二次计费风险，且与其他阶段无依赖。

### 阶段 1：L1 迁移（逐个驱动，不合并）

迁移顺序（按收益/风险比）：

1. `grsai` / `grsai_video`（本次刚改过，测试现成；错误分类刚被修正过，正好固化为契约）
2. `agnes` / `agnes_video`（样板重复最重，收益最大）
3. `bailian`（**分层迁移，见下**；顺带验证「图片改内联、音频保留 OSS」这条路）
4. `libtv`（最后迁，顺带验证 L2 的 extras 机制）
5. `echo` —— **不迁**（零 HTTP、零配置，kit 对它零收益；它保持原样作为「零配置供应商」的活样板）

**`bailian` 的分层边界**（它同时含可收敛样板与不可动资产）：

| 层 | 内容 | 处理 |
|---|---|---|
| 可迁 | `_headers`（324）、`_json`（332）、`_real_model`（178）、`get_bailian_settings` + `lru_cache`（153） | 换成 kit 对应件 |
| **不迁** | `_validated_inputs`（206）的尺寸/时长/帧率校验 | 百炼特有约束，不是通用样板 |
| **可收敛** | `prepare_inputs`（345）的**图片**素材 | 上游支持 Base64 → 改走 `inline_data_uris()`，不再下载到磁盘 + OSS 上传 |
| **不迁** | `prepare_inputs` 的**音频** OSS 直传与 `oss://` 拼装 | 百炼 `audio_url` 不支持 Base64，音频仍需临时 OSS URL |
| **先修后迁** | 回执抢占与 `records/bailian-submission.json` | 现状回执落本地文件、误删会二次计费 → **先迁 DB 唯一约束（D6）**，再由 kit 统一封装；不能原样提炼（D5 依赖 D6） |
| **待修正** | `outputs/generated.mp4` 落盘与 `output_url` 指向 `/api/v1/templates/replacement-runs/...` | 该路由**已不存在**（`generation/runner.py:824` 自注「没有路由」；`modules/template` 已删），靠 `task/pipeline.py:39` 的正则识别并从工作区取字节搬到对象存储。属遗留约定，单独修正，不作为「不可动契约」 |

**每个驱动迁完**：该驱动的既有测试全绿 + 全量 `pytest` 全绿 + `ruff` / `mypy` 干净。

> ⚠️ 迁移期**默认行为等价**：同一个错误在迁移前后应得到**同样的平台状态码**。若某驱动的现状确实错了（如 `agnes` 缺 key 抛 502），单独提一个「行为修正」补丁并写清理由 —— 不要混在重构里悄悄改。
>
> **三处明确的行为变更例外**（不是等价迁移，须单独评审 + 单独测试，不计入「行为等价」验收）：
> 1. `bailian` 图片改内联（不再落盘 / 不再 OSS 上传）—— 实测验证上游接受 Base64 且素材在上限内（A4/A7）
> 2. 缺 key / 上游 200+failed 的错误分类修正 —— 修 2026-11-23 事故
> 3. `echo` 不迁 —— 保持零配置占位形态
>
> 除此之外任何行为变化都视为迁移缺陷，回滚该次迁移。

### 阶段 2：L2（消除公共硬编码）

1. `registry` 收集 `EXTRA_ROUTES` / `EXTRA_JOBS` / `EXTRA_SCHEMAS` 并挂载
2. 删 `__init__.py:19-20,36-38`、`router.py:19,175-276`、`schema.py:100-180` 的 LibTV 条目
3. `core/config.py:80-82` 的 `node` 迁进 libtv Settings
4. 公共文件的供应商名出现次数 → 0（可用一条守卫测试钉住，见 §5）

### 阶段 3：L3（动态生效）

1. `kit/settings.py` 的 mtime 指纹缓存上线，四个驱动的 `lru_cache` getter 全部切过去
2. `registry.refresh()` + 管理端点
3. 文档更新（§8）

---

## 5. 契约测试清单

**阶段 0 的核心产物。** 每一项都对应一个「此前散落在 N 个驱动、且已经漂移过」的语义。

### 5.1 错误分类矩阵（`kit/errors.py`）

| 输入 | 期望平台状态码 | 期望副作用 | 备注 |
|---|---|---|---|
| 未配置 API key | 4xx | 允许安全重试、给真实原因 | **2026-11-23 事故的回归测试** |
| 未配置 base_url | 4xx | 同上 | |
| 连接失败 / 超时 | 4xx | 同上 | 确定没送达 |
| 上游 400 / 422 | 4xx | 透传上游原因 | |
| 上游 429 | 4xx | 透传原因（可另加退避） | |
| 上游 500 / 503 | **5xx** | **不自动重试** | 避免重复计费 |
| 上游 408 | **5xx** | 不自动重试 | 与 5xx 同侧 |
| 上游 200 + `status: failed` | 4xx | 允许重试 | 明确拒绝 = 没受理 |

> 关键断言：`4xx` 与 `5xx` 两侧**不得互换**。5xx 被降成 4xx → 框架自动重试 → 同一张图计费两次。

### 5.2 素材分类与通道选择（`kit/media.py` + 通道决策器）

**分类：**

| 输入 | 期望 |
|---|---|
| 图片 / 音频 / 视频 URL 与 data URI | 分类正确 |
| 认不出的格式 | **报错**（不静默跳过） |
| 混合顺序 | 顺序保持（与提示词 `{{Mixed N}}` 编号一致） |
| URL 带查询串（`a.png?v=1`） | 仍能识别类型（后缀取自 path） |
| 空列表 | 返回空，不报错 |

**通道选择：**

| 场景 | 期望 |
|---|---|
| 驱动声明 `image={BASE64,OSS}`、`audio={OSS}`，素材含图片+音频 | 图片走 Base64、音频走 OSS，**同一请求混合** |
| 声明 `PUBLIC_URL` 但未配公网域名 | **启动失败**，不是运行时 400 |
| 声明 `OSS` 但上游无上传凭据端点 | 启动失败 |
| 声明 `audio={BASE64}`（与上游能力矩阵冲突） | 启动失败 |
| 首选 BASE64，素材 15MB（超项目 12MB 上限） | 自动回退 OSS，并记审计事件 |
| 回退发生 | 审计事件含：原通道 / 实际通道 / 原因 |
| 同一 `request_id` 重试 | 通道选择结果一致，不因配置瞬时状态变化而漂移 |
| libtv（真实目录需求） | 通道决策与工作区声明**正交**，互不覆盖 |

### 5.3 模型 id 与参数解析（`kit/models.py`）

- 剥前缀：`grsai-nano-banana-2` → `nano-banana-2`；无前缀原样返回
- `parse_int` / `parse_enum` / `parse_duration` 的兜底与非法输入
- 非法枚举 → 4xx（不是 5xx）

### 5.4 配置（`kit/settings.py`）

- 首次读取拿到 `.env` 值
- **改 `.env` → 下次调用拿到新值**（核心：mtime 指纹生效，缓存被打破）
- 坏配置**不被缓存**（改好立即可用）
- 环境变量优先于 `.env`
- 缺必填项 → 生产巡检报出（`DriverSettings.prod_required_fields`）

### 5.5 刷新（`registry.refresh()`）

- 新增驱动文件 → 刷新后 `GET /models` 可见、路由可解析
- 删除驱动文件 → 刷新后不可见
- 已有驱动内容变更 → **不热替换**（需重启），行为明确
- 挂载失败（`EXTRA_ROUTES` 里有坏路由）→ **启动/刷新失败并报出**，不静默少端点
- 重名驱动（同名不同模型清单）→ 报冲突，不覆盖

### 5.6 架构守卫（新增，防止退化）

- `modules/model_driver/` 之外的公共文件**不得出现供应商名**（B1–B4 的回归守卫；B5 注释类豁免）
- `drivers/` 内的驱动**不得直接抛 `UpstreamError`**（必须走 kit 的分类函数）—— 这条能直接防住 2026-11-23 那类事故复发
- 驱动不得自建 `lru_cache` 配置 getter（必须走 `kit/settings.py`）
- 驱动不得自建**文件回执**或 `O_EXCL` 抢占（必须走 DB 唯一约束 + `kit/submission_guard.py`）—— 直接防住「工作区误删 → 二次计费」复发
- 驱动不得自建**素材传输实现**（Base64 / OSS 上传 / 公网 URL 拼装必须走 `infra/media_channel.py`）—— 防止三种通道再次散落成 N 份

### 5.7 提交幂等与防重复计费（`kit/submission_guard.py`，D5 + D6）

| 场景 | 期望 |
|---|---|
| 首次提交 | **DB 唯一约束抢占成功**（替代 `O_EXCL` 文件锁），写回执，请求发出一次 |
| 同一 `request_id` 重复调用 | 唯一约束冲突 → **复用已有回执，不重复发请求** |
| 上游 4xx（明确拒绝） | 回执记 `rejected` → 允许人工重提 |
| 超时 / 5xx / 响应畸形 | 回执记 `submission_unknown` → **禁止自动重提**，接口层必须拒绝并提示核对上游 |
| **回执已存在但内容损坏** | **明确报错，不静默当新任务提交** —— 最危险的分支：当成新任务 = 二次计费 |
| 进程崩溃 | DB 事务保证抢占原子性；不依赖文件锁残留与 TTL 清理 |
| **工作区被删除 / 换机器** | 回执仍可从 DB 读到 → **继续轮询，不重新提交**（这正是文件回执做不到的） |
| 存量迁移 | 旧 `records/bailian-submission.json` 全量迁入 DB 后才允许启用 guard；迁移前禁止清空工作区 |
| 工作区 GC | GC **不得删除元数据与回执**（已迁 DB 的才可删工作区目录）；测试要覆盖「GC 后任务仍可恢复」 |

---

## 6. 风险、边界与明确不做的事

### 6.1 明确不做

| 不做 | 理由 |
|---|---|
| 引入 YAML / TOML 供应商清单 | 拍板结论；且会制造第二个真相来源 |
| 远程加载插件 / 从网上拉供应商代码 | 执行任意代码的风险，与收益不成比例 |
| 修改已加载驱动文件的热替换 | `importlib.reload` 会丢 in-flight 状态 |
| 自动生成 `.env` 段 | `.env` 是部署侧事实，自动写会覆盖运维的手工安排（含多节点差异化） |
| 给 provider 加枚举列 / 改既有表结构 | 已确认无 provider 枚举列，不需要；D6 只**新增**一张提交回执表，与「不改既有结构」不矛盾 |
| 改前端运行时 | 前端已无供应商分支，无需动 |
| 改变现有驱动的错误状态码语义 | 迁移期必须行为等价；语义修正单独提补丁并说明 |

### 6.2 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| 迁移中某个驱动的行为悄悄变了 | 上线后异常分类错乱 | 阶段 0 契约测试 + 逐驱动迁移 + 每步全量回归 |
| `EXTRA_ROUTES` 自动挂载引入挂载顺序/前缀冲突 | 路由冲突或 404 | 启动时校验前缀唯一；冲突即启动失败 |
| 配置去 `lru_cache` 后每请求多一次 `stat` | 极小 | 高 QPS 场景可换 TTL 缓存，接口预留 |
| 刷新后路由里残留旧实例 | 模型 id 指向旧配置 | 刷新时先 `unregister` 再 `register`；加测试钉住 |
| `AliasChoices` 迁移改坏部署 | 老 `.env` 读不到值 | 保留旧别名；加一条「旧名仍生效」的测试 |
| 存量回执校验失败 / 历史素材字节已丢失 | 迁移中断或被跳过 → 二次计费风险 | A2/A3 前置验证；有损坏先修复，**不得跳过**；详见 §1.1 |
| refresh / GC 与在途任务冲突 | 任务失联或工作区被删 | 并发边界与回滚方案见 §6.6 |

### 6.3 决策记录（2026-11-23 拍板）

| # | 议题 | 决策 | 关键依据 |
|---|---|---|---|
| D1 | `EXTRA_ROUTES` 的路径约定 | **强制前缀 `/model-driver/{provider}/...`，启动期校验，违规即启动失败**；现有 LibTV 三个端点 URL **不动** | 现状已是 `/model-driver/libtv/{account,consumption,progress}`（`studio/src/entities/libtv/api/libtv.api.ts:12,16`），符合约定 → 零迁移成本；自由前缀会让路径分配随供应商数线性增长，且两个供应商声明同一路径时只能静默覆盖 |
| D2 | 刷新触发方式 | **管理端点为主 + 周期巡检兜底**，共用一个 `registry.refresh()`；端点需**新增运维级权限点** | `core/permissions.py` 现有 6 个权限点全是业务动作向，**无 admin/运维级** → 刷新能 import/挂载代码，不能挂在 `model_driver:read` 上 |
| D3 | 配置缓存策略 | **mtime 指纹**，键 = `(mtime_ns, size)` + 进程内 env 快照；**不引入 TTL** | pydantic-settings 每次实例化都**整读 `.env` 并解析**（`driver_config.py:41,58`）→ 无缓存的成本是每次请求一次文件读；TTL 的「最长延迟 N 秒」在改 key 止血场景里意味着继续用坏 key 提交 |
| D4 | `bailian` / `echo` 是否迁移 | **`echo` 永不迁移；`bailian` 纳入迁移，样板层迁，供应商特有层不迁；输出地址遗留约定单独修正** | `echo.py` 零 HTTP、零配置、无素材与错误分类 → kit 对它零收益，且它是「零配置供应商」的活样板；`bailian` **是 HTTP 驱动**（`httpx` 66、`_headers` 324、`_json` 332、`submit` 406），kit 四个模块全部适用，且它是唯一带参考音频的驱动（百炼 `audio_url` 不支持 Base64 → 音频仍需 OSS 临时 URL；**图片可改内联，不必物化**）。**不存在独立替换链**（`modules/template` 已删、`replacement_runs` 表已删、`output_url` 指向的路由已不存在）→ 所谓「耦合」实为「执行器工作区产物约定」，不作为不可动边界 |
| D5 | 防重复计费机制归谁 | **把 bailian 的提交幂等提炼为 `kit/submission_guard.py` 供所有驱动共用；但回执必须落数据库（唯一约束抢占），不落工作区文件** | 框架只有「分类」（`_possibly_delivered`），bailian 在驱动内做了「机制」（`O_EXCL` 抢占 + 回执 + `submission_unknown` 禁止重提）→ 该能力只服务了一个供应商。但现有回执存在 `records/bailian-submission.json`（`bailian.py:443`），与工作区同生共死 → 工作区误删会让「恢复动作」变成「当作新任务重提 = 二次计费」。**提炼前必须先修正回执介质（D6），否则统一的是错误做法** |
| D6 | 工作区 / S3 / 数据库的职责边界 | **素材字节以 S3 为唯一副本；档案元数据与提交回执落数据库；本地工作区降级为「可重建的临时执行目录」并加 GC** | `materialize()` 把 S3 字节下载一份到 `inputs/*`（`generic_inputs.py:315,323-325`），工作区**无清理机制**（`cli.py` 只有 `runs-status`）；`runs.py:138-141` 已文档化「换机器显示调用档案不存在」；回执同在工作区 → 误删后二次计费（详见 §6.5） |

**每项的替代条件**（不推荐但保留触发条件）：

- **D1 例外**：仅当某驱动需**原样透出上游官方路径**（网关代理场景）时，允许声明「外部路径透传」模式；此时该端点应置于独立网关层，且不参与命名空间分配。
- **D2 例外**：单机开发环境可只留端点（定时刷新反而会在改驱动文件时意外热加载）。
- **D3 例外**：配置以环境变量注入（无 `.env` 文件）时，缓存键退化为「env 快照指纹」——本方案的键已包含 env 快照，天然覆盖；若用 ConfigMap symlink 切换，mtime 会变，但可加 TTL 作为上限。
- **D4 例外**：出现**第二个执行器型供应商**且样板开始重复时，再单独抽 `kit/executor.py`；而不是现在把 HTTP 型 kit 撑成万能抽象。
- **D5 过渡例外 —— 已排除（2026-11-23 拍板）**：不存在独立替换链 worker（本质是供应商调用 + 执行器工作区），**直接切 DB，存量文件回执全量迁移**，不保留双写。文件回执只读保留 30 天后由 GC 删除。
- **D6 例外**：libtv 的 Node 桥接要求真实路径并重算 sha256 快照（`runs.py:1-12`）→ 本地目录**不可取消**；但可以做成「任务级临时 + 可重建 + 有 GC」，而不是持久真相源。素材字节也不该存数据库（项目已有纪律：`core/ports/library_ingest.py:14-20`「字节必须物化，不接受只存外部链接」），DB 只放元数据与回执。

### 6.4 三条必须在实施中处理的约束

1. **刷新是进程级的。** `registry` 是进程内单例 → 多实例部署下，命中 A 实例的端点刷新**只影响 A 实例**。因此周期刷新必须每实例都跑（作为收敛机制），端点只作即时手段；文档需写明「刷新不跨进程」。
2. **必填项巡检要跟刷新同路径。** `ensure_prod_driver_settings` 是**启动期**检查（`driver_config.py:217-239`）→ 改 `.env` 去掉必填项后，已运行的进程不会重新巡检，会出现「配置已改坏但进程还在跑」。刷新必须**连带重跑必填项与提交预算预检**（`precheck_submit_budgets`），否则热生效引入的是「更晚才发现的坏配置」。
3. **回执介质必须先修，再提炼（D5 依赖 D6）。** 当前回执落本地工作区文件，工作区误删后恢复动作会二次计费。在回执迁数据库之前，不得把现有文件回执模式提炼为共享能力——那会把缺陷扩散到所有驱动。

### 6.5 存储真相源：物化的边界

三个已核实的事实：

| 事实 | 证据 |
|---|---|
| S3 字节被下载一份到磁盘（一份变两份） | `generic_inputs.py:315` `storage.get(key)` → `:323-325` `_write_inputs` |
| 工作区**无清理机制**，磁盘只增不减 | `cli.py` 只有 `runs-status`（664-672）；`infra/archive` 里的 `unlink` 全是临时文件清理 |
| 回执与工作区同生共死，误删后果是**二次计费** | `bailian.py:443` 回执在工作区；`materialize()` 见不到 `run.json` 会重建（305-306）；`_submit()` 见不到回执当作新任务提交（479-512） |

`runs.py:138-141` 已文档化远端归档下的后果：「run.json 与成片在本地——换机器或多实例会显示『调用档案不存在』」；`orphan_pointers()`（172-187）专门列出「指针在桶里、工作区不在本机」的档案。**最严重的不是「任务无法恢复」，而是恢复动作本身会重新提交、产生二次计费。**

**要区分两个「物化」：**

- **执行期真实目录**：libtv 的 Node 桥接要 `run/inputs/*` 真实路径、重算 sha256 快照、ffmpeg 派生预览（`runs.py:1-12`）→ **不可取消**，「对象存储交不出路径，把 run 目录硬搬进桶只会把执行器拆成两半」。
- **持久工作区当真相源**：`run.json` / 回执 / 素材副本长期留磁盘 → **不必要，且有风险**。

**目标职责边界：**

| 数据 | 现状 | 目标 |
|---|---|---|
| 素材字节 | S3 + 磁盘副本 | **S3 唯一副本**（项目纪律：`core/ports/library_ingest.py:14-20`「字节必须物化，不接受只存外部链接」——这里的「物化」指落我们自己的存储，S3 已满足） |
| 档案元数据 | 磁盘工作区 | **数据库，或可从 DB 重建**（`GenerationRecord.params` + `referenceKeys` 已足以重建 `input-map.json` / `prompt-spec.json`） |
| 提交回执 | 磁盘文件 + `O_EXCL` | **数据库唯一约束**（文件会丢，抢占语义会失效） |
| 执行期工作区 | 持久、无 GC | **任务级临时目录 + 可重建 + GC** |

**不是物化器写错了**：`generic_inputs.py:11-15` 写明它是**为执行器型供应商补出归档形状**（libtv 的 Node 桥接要真实路径；百炼的 `prepare_inputs` 读 `input-map.json` 等），解决「这些驱动能不能在通用页用」的问题，但把「工作区即真相源」这个假设一并继承下来。

**「替换链」是过时概念（2026-11-23 核实）**：`modules/template` 目录已不存在，`replacement_runs` 三表已按拍板删除（`task/feed_service.py:9`），`bailian.py:534` / `libtv.py:343` 的 `output_url`（`/api/v1/templates/replacement-runs/...`）指向一个**没有路由**的地址（`generation/runner.py:824` 自注），靠 `task/pipeline.py:39` 的正则识别后从工作区取字节搬进对象存储。所以本质上**都是调供应商 + 执行器工作区**，不是一套独立的替换编排链。这条更正放宽了原方案里好几处「不可动契约」的边界。

**图片内联后还能再缩一圈**：核查上游文档后确认百炼 `img_url` 支持 Base64（§3.1.4），因此 bailian 的**图片素材不需要落盘、也不需要 OSS 上传**，可直接内联；只有参考音频（`audio_url` 不支持 Base64）与 libtv 的 Node 桥接仍需要文件。也就是说「物化」的适用范围比原方案估计的更小 —— 它既不是上游能力要求，也只对一小部分素材成立。

**迁移代价**：`bailian._validated_inputs` 改为从 DB 元数据重建；回执迁 DB 表 + 唯一约束替代 `O_EXCL` + 存量档案迁移；libtv 仍需真实路径（但可临时化 + GC）；`output_url` 的遗留路径契约需单独修正为平台自己的产物地址（**不需要兼容层**——没有替换链 worker 在读它）。

**结论**：不是取消物化，而是**把物化从「持久状态」降级为「临时缓存」**。且必须在 D5 提炼前完成，否则统一的是错误做法。

### 6.6 回滚方案与并发边界

**回滚原则**：每个阶段必须能在不丢数据的前提下退回。原方案只说「可回滚」却没写怎么退，现补齐。

| 阶段 | 回滚动作 | 关键前提 | 不可回滚点 |
|---|---|---|---|
| 0 | 删除 `kit/` 与契约测试 | 无 | 无 |
| 0.5 | 回执表保留但停止写入；恢复读文件回执 | **过渡期文件回执必须继续写**（不能只读 DB） | 存量文件回执被物理删除之后 |
| 1（单驱动） | 该驱动切回旧实现，`kit/` 保留 | 旧实现在版本历史可恢复 | 已产生的新格式回执需保持兼容 |
| 2 | 恢复公共文件里的 LibTV 条目 | `EXTRA_*` 挂载与新路由可短暂并存 | 无 |
| 3 | 关闭 refresh 入口，恢复 `lru_cache` getter | 配置缓存接口保持兼容 | 无 |

> ⚠️ **阶段 0.5 的不可回滚点**：一旦删除存量文件回执，就无法退回文件模式。规定：文件回执**只读保留至少一个完整发布周期**；GC 默认不删 `records/bailian-submission.json`。

**并发边界（原方案未覆盖）：**

| 场景 | 风险 | 约束 |
|---|---|---|
| `refresh()` 与 in-flight 任务 | 实例被替换，正在 poll 的任务失联 | refresh 只替换注册表与路由；已发出的请求由原实例跑完，新请求才用新实例 |
| 工作区 GC 与执行中任务 | 删掉正在用的目录 | GC 只处理终态且 `updated_at < cutoff` 的 run；有锁文件则跳过 |
| DB 迁移期文件/DB 双写 | 两边状态不一致 → 误判是否已提交 | **已拍板不双写**：直接切 DB，存量文件回执全量迁移并校验；迁移完成前冻结提交通道（见 §4 阶段 0.5） |
| 通道回退与历史任务重试 | 同一素材两次重试走不同通道 | 通道选择结果随回执落库（`channel_used` 字段），重试复用原通道 |
| `.env` 热生效与在途请求 | 同一请求内前后读到不同配置 | 请求开始时快照一次配置，请求内复用；下次请求才用新值 |
| 多实例 + refresh | A 实例刷新后 B 实例仍是旧代码 | 每实例各自跑周期刷新；记录 `refreshed_at`，运维可核对各实例 |

**`.env` 变更审计（G3 的安全缺口）**：配置热生效让「改 `.env`」变成一次**运行时行为变更**，但当前无留痕。要求：refresh 端点记录操作人与时间；`.env` 改动由部署系统审计（不在应用内实现）；`app doctor` 输出当前生效配置的**指纹**（不含密钥值）。

---

## 7. 资源、排期与可衡量指标

### 7.1 关键路径与工作量估算

> ⚠️ 以下是**依赖关系与规模判断，不是工期承诺**。正式排期须在阶段 0 产出 A1 差异清单后重估。

| 阶段 | 主要工作 | 依赖 | 粗估规模 |
|---|---|---|---|
| 0 | 逐行比对 4 个驱动 + 现有行为契约测试 + `kit/` 骨架 | 无 | 中 |
| 0.5 | 回执表 + 迁移脚本 + 双写过渡 + 工作区 GC + 存量校验 | A2 / A3 验证 | **大（应视作独立项目）** |
| 1 | 6 个驱动逐个迁移 + 通道抽象 + 全量回归 | 0、0.5、A4 / A7 | **大（关键路径）** |
| 2 | `EXTRA_*` 挂载 + 3 处公共文件清理 | 阶段 1 的 libtv 迁移 | 中 |
| 3 | mtime 配置 + `refresh()` + 运维权限点 | 阶段 1、2、A5 | 中 |

**关键路径**：`A1 → 阶段 0 → 阶段 0.5 → 阶段 1（含通道抽象）→ 阶段 2 → 阶段 3`。

阶段 0 与 0.5 互不依赖、**可并行**；但两者都必须在阶段 1 之前完成。阶段 0.5 常被低估——它包含一个数据迁移、一个 GC 机制和一个双写过渡期，不是「加张表」那么简单。

### 7.2 人力与环境前提

- 需要 1 名熟悉 `model_driver` 的开发者 + 1 名熟悉 DB 迁移的开发者；阶段 0.5 的回执语义需**第二人独立复核**（二次计费不可逆）
- 需要可回滚的部署窗口（阶段 0.5 涉及存量数据迁移）
- 需要 staging 环境具备：真实 S3 对象样本（验证 A3）、百炼测试额度与大小边界素材（验证 A4/A7）、多实例部署（验证 §6.6 的刷新与审计）

### 7.3 可衡量指标（补充 §1 的 G1–G10）

| 类别 | 指标 | 目标值 | 采集方式 |
|---|---|---|---|
| 结构 | 公共文件中供应商名出现次数 | 0（B5 注释类豁免） | 守卫测试 |
| 结构 | 错误分类实现点数量 | 1 | 守卫测试 |
| 结构 | 通道实现份数 | Base64 / OSS / 公网 URL 各 1 份 | 守卫测试 |
| 可靠性 | 工作区删除后恢复成功率 | 100%（G7） | 故障注入测试 |
| 可靠性 | 同一 `request_id` 的上游提交次数 | 恒为 1 | 审计事件 |
| 可靠性 | 迁移期回执一致性比对通过率 | 100% | 迁移脚本报告 |
| 性能 | mtime 命中时配置读取开销 | ≤ 1 次 `stat` / 请求 | 基准测试 |
| 性能 | DB 回执较文件回执的延迟增量 | **记录基线，不设硬阈值**（DB 网络往返不可避免） | 压测报告 |
| 运维 | GC 一轮释放的磁盘占比 | > 0 且无任务失败 | GC 报告 |
| 质量 | 每阶段全量 `pytest` | 全绿 | CI |
| 质量 | 迁移引入的回归缺陷 | 0 | 阶段验收 |

**明确不设的指标**：不为「统一程度」设百分比目标（如「90% 代码复用」）—— 为达标而抽象只会制造逃生舱，与 D4 的教训相悖。统一看 §1 的结构性目标达成与否，不看比例。

---

## 8. 文档更新清单（实施时同步）

| 文档 | 更新点 |
|---|---|
| `docs/MODEL_DRIVER_STANDARD.md` | §2 成员表（新增 `EXTRA_*`）、§3 供应商形态（专属面自描述）、§10 明确不做的事（热加载边界） |
| `api/docs/EXTENDING.md` | 「加一个供应商」清单从 6 处缩到 1 处（G1 的验收证据） |
| `docs/EXTENSIBILITY_GAPS.md` | A1 / D3 标注修复，回填「修复进展」 |
| `docs/ARCHITECTURE.md` | §3.7 模块清单（`kit/` 的定位）、§6「加一个功能要动哪些文件」、存储边界（S3 / DB / 本地工作区的职责，D6） |
| `api/docs/openapi.json` + `studio/src/shared/api/schema.ts` | 仅当公开接口面变化（阶段 2 的端点挂载方式可能影响）→ 走既有生成流程 |

---

## 9. 附录 A：预计变更文件清单

### 新增

```
api/src/modules/model_driver/kit/__init__.py
api/src/modules/model_driver/kit/http.py
api/src/modules/model_driver/kit/errors.py
api/src/modules/model_driver/kit/models.py
api/src/modules/model_driver/kit/media.py
api/src/modules/model_driver/kit/settings.py
api/src/modules/model_driver/kit/submission_guard.py   ← D5，依赖 D6
api/src/infra/media_channel.py                          ← 三种通道实现 + 可行性校验 + 回退决策（§3.1.4）
api/src/db/submission_receipts.py                       ← 提交回执表（唯一约束）
api/tests/test_driver_kit_errors.py
api/tests/test_driver_kit_media.py                      ← 分类 + 通道选择
api/tests/test_driver_kit_settings.py
api/tests/test_driver_submission_guard.py               ← DB 回执 / 二次计费防护
api/tests/test_driver_refresh.py
```

### 修改（公共文件 —— 共 10 处，其中 3 处由 D6 引入）

> 此清单需在每次评审时复查；**不能假定「改完不再增长」** —— D5/D6 与通道抽象都是在评审中新增的公共文件改动。

```
api/src/modules/model_driver/registry.py     ← 收集 EXTRA_*、refresh()
api/src/modules/model_driver/__init__.py      ← 删 LibTV 作业硬编码
api/src/modules/model_driver/router.py        ← 删 LibTV 端点硬编码
api/src/modules/model_driver/schema.py        ← 删 LibTV schema 硬编码
api/src/core/config.py                        ← 删 node/LIBTV_NODE 别名；加工作区 GC 保留期
api/src/db/migrations.py                      ← 回执表 + 存量文件回执迁移（D6）
api/src/db/registry.py                        ← 登记表模型（如果项目按此惯例）
api/src/cli.py                                ← 工作区 GC 命令 + runs-status 话术更新
api/src/infra/archive/runs.py                 ← 自检话术：「回执在 DB，工作区丢失不再等于档案丢失」
api/src/generation/runner.py                  ← 仅当需要给 kit 分类结果加钩子（原则上不改）
```

### 修改（驱动，逐个迁移，行为等价）

```
api/src/modules/model_driver/drivers/{bailian,grsai,grsai_video,agnes,agnes_video,libtv}.py
# echo.py 不迁（D4）—— 零 HTTP、零配置，kit 对它零收益
```

---

> **本文档只是设计稿，未改动任何生产代码。** 评审通过后按阶段 0 → 0.5 → 1 → 2 → 3 实施，每阶段独立可发布；其中**阶段 0.5 可独立上线**，它修的是现网的二次计费风险。
