# 本轮执行记录 · 未完成任务盘点与实施（2026-11-23）

> 本文件是**本轮任务的记忆文件**：上下文被压缩后，以此为准恢复进度。
> 盘点结论 → 执行顺序 → 逐项核实 → 疑点清单，全部记在这里。

## 0. 本轮要做的事

按 `REVIEW_2026-11-21_DESIGN.md` / `TODO.md` 的登记，执行建议顺序：
**H12 → H10 → H8 → H7 → T37 → M7**（用户可感知 → 守卫盲区 → 并发保护 → 静默截断）。

每项执行时的要求：
1. 先核实「该条发现的事实」在当前代码里是否属实；
2. 实施；
3. 跑相关测试 + 全量回归；
4. 在 §2 日志表里写清：**核实方式、结论、改动、验证结果**；
5. 无法确认 → 标 `待核实` + 列出需补充的信息；
6. 需要产品/用户拍板 → 标 `待确认` + **跳过**，继续下一项。

## 1. 盘点基线（2026-11-23 复核）

### 已完成，不在本轮范围
- `FIX_PLAN_2026-11-23` 的 12 条（含 P2-2 版本语义，今天完成并推送 `52ae524a`）
- `IMPROVEMENT_PLAN_2026-11-18` 的 15 条完整实施项（见 `VERIFY_IMPROVEMENT_PLAN_2026-11-18.md` §8.1）
- `T49`（`expected_version = 0` 双重语义）
- 代码里 23 处 `TODO` 注释 = 脚手架模板（`scaffold/templates.py`、driver 模板），**非待办**

### 未完成总量
| 来源 | 条数 | 状态 |
|---|---|---|
| `REVIEW_2026-11-21_DESIGN.md` | 46（高 13 / 中 23 / 低 10） | 几乎全部未处理；仅两条系统性主线被 `FIX_PLAN_2026-11-23` 吸收 |
| `IMPROVEMENT_PLAN_2026-11-18.md` | 24 中：15 完成、4 部分做、7 暂缓 | 残留 4 项部分 + 7 项暂缓 |
| `docs/TODO.md` | 14 条登记（T2/T8/T9/T22/T23/T24/T25/T26/T29/T30/T35/T37/T45/T48） | 未销账（其中 T35-4、T35-6 已核实实际已修）|
| `REVIEW_2026-11-23_CODE_QUALITY.md` | 12 条已全做；另有 2 条未排期（审查 #11 CSV 空值语义、#15 提交防重入） | 待办 |

### 优先级排序（本轮执行依据）
| 级别 | 项 | 理由 |
|---|---|---|
| P0 | H12 UsagePage 整页假交互 + 无权限门控 | 用户当天可见；空占导航位 |
| P0 | H10 409 语义只实现一半（`isStateChanged` 零调用） | 并发冲突只给裸文案，用户不知道该做什么 |
| P0 | H8 13 处 `has(access.*)` 绕过 AST 守卫 | 守卫盲区，含 `library:delete:any` 一类高危判定 |
| P0 | H7 9 处 `getattr` 绕过协议 + `isinstance` | 协议改名静默降级 |
| P0 | T37 生图无在途上限 | 队列可被顶满 |
| P0 | M7 批量删除 `_MAX_IDS=200` 静默截断 | 静默丢数据 |

## 2. 执行日志（每完成一项补一行）

| 序 | 项 | 核实方式 | 核实结论 | 改动 | 验证 | 状态 |
|---|---|---|---|---|---|---|
| 1 | H12 UsagePage | 读 `pages/usage/UsagePage.tsx`、`modules/usage/module.ts`、`app/router.test.ts`、后端 `modules/usage/` | **前提不成立**：页面已接真实后端（`/usage/overview`、`/usage/records`）、已声明 `page:usage`、豁免名单已空、无占位与 API 路径 | 无需改动 | `git log` 确认由 `9c5f5e41`+`53256aa9`（11-22）修复 | **关闭（已过时）** |
| 2 | H10 409 语义 | 读 `core/exceptions.py`、`api/errors.py`、全库 `raise Conflict(`、前端 `types.ts`/`http.ts`/`query-client.ts`、`TextVersionHistory.tsx`、`TaskCenterPage.tsx` | **成立**：后端 6 类 409 只有 `StaleDataError` 带 `reason=state_changed`；素材库/模板的乐观锁 409 不带 → 前端判不出；`isStateChanged` 零调用；`TextVersionHistory` 私有 `isConflict` 只判状态码 | 后端：新增 `core/exceptions.py::StateChanged`（409 + `details.reason=state_changed`），替换 5 处版本落败（`library/service.py`、`text_assets.py`×2、`sharing.py`×2、`lifecycle.py`）；前端：`query-client.ts` 全局 `mutations.onError` → `invalidateQueries()` + warning 提示；`TextVersionHistory` 改用共享 `isStateChanged` | 后端 +4 用例（19 passed）、前端 +3 用例（3 passed）、ruff 全绿 | **完成** |
| 3 | H8 13 处 `has(access.*)` | 读 `tests/test_permission_coverage.py` 全文、grep 13 处调用点与 import 形式、查两个码的声明处、临时探针对比扩展前后扫描结果 | **成立，但审查的机理说错了**：守卫的 `_ENFORCEMENT_CALLS` **本含 `has`**（调用点被扫到）；盲区在**参数解析**——`access.PERM_X` 是 `ast.Attribute`，常量解析只认 `ast.Constant`/`ast.Name`。且码一直可见（`lifecycle.py` 有字面量写法），**看不见的是 13 个调用点** | 不改 13 处代码（保留常量：能防拼错，审查建议的「改回字面量」是倒退）；改守卫：`test_permission_coverage.py` 新增 `_resolve_module()` + `_strings()` 的 `ast.Attribute` 分支 + `ast.Import` 导入记录；新增两条棘轮：`test_cross_module_constant_codes_reach_the_scan`（钉位置）、`test_enforcement_attribute_arguments_are_resolvable`（静态常量引用必须可解析） | 临时探针：码总数 60→60（未引入新码），`library:delete:any` 4→14 处、`library:draft:review` 3→6 处，**+13 与审查数精确吻合**；`test_permission_coverage.py` 10 passed、ruff 全绿 | **完成** |
| 4 | H7 9 处 `getattr` | 读 `infra/ai/base.py`、`generation/pipeline.py`、`generation/runner.py`、`infra/queue/base.py`、两个 queue 驱动、三处调用点；grep 全部 `getattr(` | **成立（8 处违反点 + 1 处正当）**：审查建议的三个协议（`Persistable`/`ArchiveRequestable`/`StatsCapable`）确实缺失；`PollCapable` 已存在且恰好就是 `supports_poll: bool`。`db.py` 那句「stats 不在协议里（别的驱动没有这个概念）」**是错的**——memory 驱动也有 | 新增 5 个协议（`generation/pipeline.py` 4 个 + `infra/queue/base.py` 1 个）；8 处 `getattr` 探测改 `isinstance`；修 `db.py` 过时注释；新增 `api/tests/test_capability_protocols.py`（守卫 + 3 类自检） | 12 passed（含**故意违规的自检**：喂 3 种违规写法证明守卫会红）；后端全量 **1812 passed**；ruff 全绿 | **完成** |
| 5 | T37 生图在途上限 | 读 `docs/TODO.md` T37、`task/service.py::_assert_in_flight_room`、`image/models.py`、`image/service.py::generate`、`image/router.py`、`core/config.py`、`tests/conftest.py` | **成立**：任务侧有在途上限（queued + running，`0`=不限），生图侧只有**频率**限流（`image.generate` 60/分钟）——频率限流拦得住手抖、拦不住「一分钟点 60 次」，而每条生图占供应商账号的上游排队位次（账号全团队共享）。TODO 里「计数口在 task 模块」的顾虑**不成立**：`ImageJob` 就在 image 模块，带 `owner_id`+`status`，可自数 | 加 `image_max_in_flight_per_user: int = 5`；`image/service.py` 新增 `_assert_in_flight_room()` 并在 `generate()` 最前面调用（超限 429，消息带上限与在途数）；`.env.example` 补键；`conftest.py` 测试环境默认关掉；新增 `test_image_in_flight_cap.py` 6 条（含默认值棘轮 + AST 路径棘轮） | `test_image_in_flight_cap.py` 6 passed；后端全量 **1818 passed, 14 skipped**；ruff 全绿 | **完成** |
| 6 | M7 静默截断 | grep `_MAX_IDS` 全库；读 `core/ports/library_references.py` 全文、三个 checker、`library/bulk.py` 全文、grep 全部 `ask_library_references` 调用点、读 `library/access.py` 与 `lifecycle.py:113-128` | **部分成立**：3 处 `[:_MAX_IDS]` 静默切片是事实；但**当前不可达**（所有调用点只传一个 id），且审查的两条证据链都已过时——批量动作走 `delete`（进回收站），`purge` **刻意不在批量里**；「跨组保护走 JSON LIKE」也不对（它查的是**关联表** `LibraryAssetGroup`）。JSON LIKE 实际只用于**引用检查** | 3 处静默切片改**超限即抛 `Unavailable`（503，fail-closed）**；新增 `test_library_reference_limits.py` 9 条（超限报错×3、边界×3、AST 守卫「无静默切片」、守卫「purge 不在批量」、端到端引用拦截） | 9 passed；后端全量 **1827 passed, 14 skipped**；ruff 全绿 | **完成（主体）**；子项「拆关联表」另见 §3.7 |

### 本轮汇总（2026-11-23 收尾）

六项全部走完：**5 项完成、1 项关闭（已过时）**；另按用户拍板补做了 M7 的子项「拆关联表」（§3.7）。

| 项 | 结论一句话 | 关键验证 |
|---|---|---|
| H12 | **关闭**——页面已接真实后端、已声明权限、豁免表已空；审查结论已过时（`9c5f5e41`/`53256aa9` 修复） | `git log` |
| H10 | 完成——后端 6 类 409 里只有版本过期该带 `reason=state_changed`；前端全局消费它 → 刷新 + 提示 | 后端 19 passed / 前端 3 passed |
| H8 | 完成——盲区在**参数解析**不在「调用点」（`has` 本就在扫描清单里）；改守卫让常量引用可解析，**不动 13 处生产代码** | 临时探针：+13 处，与审查数精确吻合 |
| H7 | 完成——补 5 个协议，8 处 `getattr` 探测改 `isinstance`；顺带修掉 `db.py` 那句过时注释 | 12 passed（含故意违规的自检）/ 全量 1812 |
| T37 | 完成——生图加在途上限（默认 5，0=不限）；**image 模块可自数**（TODO 里「计数口在 task 模块」的顾虑不成立） | 6 passed / 全量 1818 |
| M7 | 完成（主体）——3 处静默截断改**超限即 503**；审查的两条证据链都已过时 | 9 passed / 全量 1827 |
| M7 子项 | 完成——下载任务资产清单拆成关联表（`library_download_job_assets`），引用检查从 **JSON 子串粗筛**改成**关联表 `IN` 查询** | 7 + 1 passed；全量 1835；手工复验 upgrade/downgrade |

**最终全量**：后端 **1835 passed, 14 skipped**（基线 1794 → 本轮 +41）；`ruff check src tests` All checks passed。

**本轮新增的待确认项**：无（M7 的子项已按用户拍板做掉）。

**本轮发现的模式**（供后续轮次参考）：审查文档里的行号普遍已漂移，**引用行号当证据会指错地方**——三处（H8/H7/M7）都不得不用 grep + 读全文重新定位，且 M7 的两条证据直接指向了已被重构掉的代码。

## 3. 逐项核实详情

（每项：发现原文 → 核实方式 → 结论 → 处置）

### 3.1 H12 · UsagePage 是整页假交互，且无权限门控

**发现原文**（`REVIEW_2026-11-21_DESIGN.md:132-136`）：
- `pages/usage/UsagePage.tsx:50-64` 时间筛选是 span（注释「静态占位」）
- `:87-92` 两处「图表占位」
- `:116-133` 把 `/usage/summary` 等后端路径渲染给终端用户
- `modules/usage/module.ts:18-26` 是唯一不声明 `permissions` 的模块
- `app/router.test.ts:37` 显式豁免

**影响**：用户以为看板故障；任何登录用户都能看到未完成功能；空态没有出口。

**建议**（原文）：接口未就绪前从导航摘掉，或加权限门控；保留则改成「即将上线 + 去任务中心」的真出口，禁止渲染 API 路径。

**核实**：**不成立**。逐条比对：
- `UsagePage.tsx` 现状：有真实查询（`useUsageOverview` / `useUsageRecords`）、真实筛选控件、骨架屏、错误态、空数据 Notice、分页；**没有**「静态占位」；
- `modules/usage/module.ts:22` 已声明 `permissions: ['page:usage']`（原文说「唯一不声明 permissions 的模块」→ 已不成立）；
- `app/router.test.ts:37` 的豁免名单**已清空**；
- 全仓搜不到 `占位` / `/usage/summary` / `静态`（页面不再渲染后端 API 路径）；
- 后端 `modules/usage/` 提供 `/usage/overview`、`/usage/records` 真实接口。

**处置**：**关闭**（审查结论已过时）。`git log` 显示由 `9c5f5e41`、`53256aa9`（2026-11-22）修复。
**无需用户确认**。

### 3.2 H10 · 409 语义处置只实现一半（`isStateChanged` 零调用）

**发现原文**（`REVIEW_2026-11-21_DESIGN.md:120-124`）：
- 响应契约三份定义：`schema.ts:14`、`types.ts:2`、`rest.ts:19`
- `isStateChanged`（`types.ts:69-72`）全仓零调用点
- 后端 `api/src/api/errors.py:100-116` 要求「重读权威状态」
- `pages/task-center/TaskCenterPage.tsx:297-299` 只 toast
- `features/library/TextVersionHistory.tsx:22-25` 私有 `isConflict` 只判 409

**核实（方式）**：
1. 读 `studio/src/shared/api/types.ts`（`isStateChanged` 实现：判 `details.reason === 'state_changed'`）；
2. 全库搜 `state_changed` → **只有 1 处**（`api/src/core/exceptions.py` 的 `StaleDataError`）；
3. 全库搜 `raise Conflict(` → 逐个分类：
   - **版本落败**（应标 `state_changed`）：`library/service.py::_check_version`、`library/text_assets.py::_check_version` 与 `_version_conflict`、`library/sharing.py` 两处、`library/lifecycle.py` 草稿决策、`template_pack/spec_service.py` revision 冲突 —— **全部不带 `reason`**；
   - **业务冲突**（不该标）：重名、已结束、不是你的、幂等键占用、重复正文 —— 语义正确，不动；
4. 读 `studio/src/shared/api/http.ts`（`ApiError.details` 已能拿到后端 `details` ✓）、`query-client.ts`（**没有** `mutations.onError` → 无处消费 `isStateChanged`）；
5. 读 `TextVersionHistory.tsx`（私有 `isConflict` 只看状态码 → 把业务冲突也说成「版本冲突」）；
6. 读 `TaskCenterPage.tsx:297-299`（是**删除**失败的 catch，非并发冲突路径 → **不需要改**，它显示服务端 message 是正确呈现）。

**结论**：发现成立，且比原文更深一层——**前端唯一的判定入口漏掉了最常见的一类**（素材库/模板的乐观锁）。

**改动**：
- 后端 `core/exceptions.py`：新增 `StateChanged(Conflict)`，构造时把 `reason: 'state_changed'` 合并进 `details`（409 与业务冲突的分类只留这一个入口）；
- 6 处版本落败改抛 `StateChanged`（保留原有 `expectedVersion` / `stateVersion` / `textVersion` / `revision` 等 details）；
- 前端 `shared/api/query-client.ts`：`mutations.onError` 统一消费 `isStateChanged` → `invalidateQueries()`（重读权威状态）+ `toast('数据已被其他人修改，已为你刷新，请确认后重试', 'warning')`；
- 前端 `features/library/TextVersionHistory.tsx`：删掉私有 `isConflict`，改用共享 `isStateChanged`。

**验证**：后端 `test_library_versions.py` 19 passed（+4：三类版本冲突带 `reason`、业务冲突不带）；前端 `query-client.test.ts` 3 passed（版本过期→刷新+提示 / 业务冲突→不动 / 500→不动）；`ruff check src tests` All checks passed。

**未纳入本项**：响应契约三份定义（`schema.ts` / `types.ts` / `rest.ts`）的收敛 —— 那是 H3「契约四份非单一真相」的范围，属 P1，本轮不动。

### 3.3 H8 · 13 处权限判定绕过 AST 守卫

**发现原文**（`REVIEW_2026-11-21_DESIGN.md:108-112`）：
- **证据**：`modules/library/bulk.py:175`、`downloads.py:196`、`service.py:306,309,514,687,721,874,1402,1407`、`tags.py:521,523`、`text_assets.py:335`；守卫 `tests/test_permission_coverage.py:141-152` 只解析 `ast.Constant` / `ast.Name`。
- **为什么是设计问题**：守卫正确性依赖源码形状约定，13 个调用点违反后「僵尸码 + 未声明码」双向检查同时出现盲区。
- **影响**：`library:delete:any` 改名或删声明时，这 13 条跨人删除/审核判定不会被任何测试报错。
- **建议**：按约定改回字面量 `actor.has("library:delete:any")`，再补 `assert "has(access." not in source` 棘轮。

**核实（方式）**：
1. 读 `tests/test_permission_coverage.py` 全文（411 行）：`_ENFORCEMENT_CALLS = {"require","has","has_all","has_any"}` —— **`has` 本来就在清单里**；
2. grep `has(access\.` 全库 → **13 处**（`bulk.py`×1、`downloads.py`×1、`service.py`×8、`tags.py`×2、`text_assets.py`×1），与审查数量一致（行号已随重构漂移）；
3. 查 import 形式：全部是 `from modules.library import access`；
4. 读 `_strings()`（只认 `ast.Constant` / `ast.Name` / 元组）→ **`ast.Attribute`（`access.PERM_X`）解不出**；
5. 查两个码的声明处：`modules/library/__init__.py:92,98`（`ModuleSpec.permissions`）→ `_declared()` 覆盖；
6. 查 `lifecycle.py:93,255,291,526` 用的是**字面量** → **码一直在扫描结果里，盲区在「调用点」而非「码」**。

**结论**：发现成立，但**机理与审查所述不同**——不是「调用点绕过守卫」，是「调用点被扫到但参数解析不出码」。审查给的证据（守卫只解析 Constant/Name）是对的，但它由此推出的「改回字面量」并不解决问题：**字面量只是把形状换成另一种形状**，守卫依然依赖源码形状约定，且丢掉常量防拼错的收益（13 处判定点已经用上了这个好处）。

**改动**（全部在 `api/tests/test_permission_coverage.py`，**不动 13 处生产代码**）：
- `_scan_module()`：新增 `ast.Import` 记录（`import a.b.c as x` / `import a.b.c`）；
- `_resolve_module()`：新增——把名字解析成**模块** dotted 名（`access` → `modules.library.access`），子模块路径优先于包本身；
- `_strings()`：新增 `ast.Attribute` 分支——`access.PERM_DELETE_ANY` → 顺着属性链找到目标模块顶层常量；
- 新增 `test_cross_module_constant_codes_reach_the_scan`：钉**位置**（每个码必须在用常量的那些文件里被看到），防止将来退化；
- 新增 `test_enforcement_attribute_arguments_are_resolvable`：静态常量引用（base 是模块）必须能解出码；**不拦运行时动态值**（`actor.has(source.permission)` —— 导出来源把权限码放在数据里，静态分析本来就读不出，这是它自己发现的第二类差异）。

**验证**：临时探针对比扩展前后扫描结果 —— 码总数 60→60（未引入新码，不影响 `orphan` / `unknown` 两条守卫）、`library:delete:any` 4→14 处、`library:draft:review` 3→6 处，**+13 处与审查数精确吻合**；`test_permission_coverage.py` 10 passed；`ruff check src tests` All checks passed。

**与审查建议的差异（如实记录）**：**不采纳**「改回字面量」与「禁 `has(access.` 的源码扫描棘轮」。前者是功能倒退（丢常量防拼错），后者会把常量写法本身禁掉——而常量是这 13 处判定点已有的、更强的写法。等价强度的棘轮由上面两条测试提供：常量改名/挪走 → 解析失败 → `test_enforcement_attribute_arguments_are_resolvable` 报错；常量调用点被删 → 位置数掉下来 → `test_cross_module_constant_codes_reach_the_scan` 报错。

### 3.4 H7 · 协议 + isinstance 被 getattr 系统性绕过

**发现原文**（`REVIEW_2026-11-21_DESIGN.md:102-106`）：
- **证据**：主张在 `infra/ai/base.py:158-169`、`generation/pipeline.py:50`；违反在 `pipeline.py:106,120`、`runner.py:829,901`、`model_driver/store.py:132`、`api/diagnostics.py:360`、`api/instrumentation.py:142`、`modules/health/checks.py:119`。
- **为什么是设计问题**：可选能力的名字有两套权威来源；getattr 静默降级，协议改名立刻类型失配，两套机制互相抵消。
- **影响**：`persist_output` / `supports_poll` / `stats` 任一改名，分别导致产物不落盘、同步供应商被当异步续查、监控静默变空，且测试全绿。
- **建议**：补 `Persistable` / `ArchiveRequestable` / `StatsCapable` 协议，8 处改 `isinstance`；加一条源码扫描守卫拦 `getattr(x, "name", default)` 形式的能力探测。

**核实（方式）**：
1. 读 `generation/pipeline.py` 全文 —— **文件里已经有 4 个协议**（`CoverAttachable` / `DeadlineExtendable` / `ClaimedExternalIdQueryable` / `GenerationPipeline`），注释还引用了 `docs/HANDOFF.md §5.5「可选能力别用 getattr 探测」`；**而同一文件的 `strict_model()` / `fail_on_model_gone()` 自己就在用 getattr**（自相矛盾）；
2. 读 `infra/ai/base.py` —— 已有 `PollCapable`（含 `supports_poll: bool`）/ `PollPacing` / `SubmitBudget` / `PromptTranslation` / `InlineReferencesCapable`；**审查建议的三个协议确实缺失**；
3. 读 `infra/queue/base.py` —— `JobQueue` 协议旁写着「`stats` 不在协议里（别的驱动没有这个概念）」；但 grep 发现 **memory（`memory.py:232`）与 db（`db.py:596`）两个驱动都实现了 `stats()`** → **那句注释是错的**；
4. grep 全库 `getattr(` → 8 处能力探测 + 若干正当用法（读 `route.path`、读 ORM `rowcount`、读 `ModuleSpec` 可选字段）；`api/instrumentation.py:88` 的 `getattr(route, "path", None)` **不是能力探测**（读路由数据）→ 不改；
5. 查实现侧签名：`modules/image/pipeline.py` 与 `modules/task/pipeline.py` 都实现了 `persist_output(record_id, url) -> str` 与 `archive_request(session, record, stored)`；`task/pipeline.py:335` 的 docstring 自己写着「没有这个方法时 `generation.runner._archive()` 会在 `getattr` 那一步**静默返回**」；
6. 核对 `supports_poll` 语义：`base.py:289-294` 注释说「同步供应商设 `supports_poll = False`，异步显式设 `True`，缺省按 False 处理」→ `isinstance(provider, PollCapable) and provider.supports_poll` 与原 `getattr(provider, "supports_poll", False)` **完全等价**。

**结论**：成立（8 处违反点）。审查的机理描述（「协议 + isinstance 被 getattr 绕过」）准确，**协议缺失也是真的**——只有 `Persistable` / `ArchiveRequestable` / `StatsCapable` 三个需要新建（`supports_poll` 有现成的 `PollCapable`）。

**改动**：
- `generation/pipeline.py`：新增 `StrictModelDeclared` / `FailOnModelGoneDeclared` / `Persistable` / `ArchiveRequestable` 四个 `runtime_checkable` 协议；`strict_model()` / `fail_on_model_gone()` 改 `isinstance`（后者保持「未声明 = True」的默认语义）；
- `generation/runner.py`：`_persist()` / `_archive()` 改 `isinstance` 判定；
- `modules/model_driver/store.py`：改用现成的 `PollCapable`；
- `infra/queue/base.py`：新增 `StatsCapable` 协议（**刻意不进 `JobQueue` 主协议**——主协议是「每个驱动都必须实现」的契约，自报计数是运维增强，新驱动不实现时退回 `size()` 即可）；
- `api/diagnostics.py` / `api/instrumentation.py` / `modules/health/checks.py`：三处 `queue.stats` 探测改 `isinstance`；
- `infra/queue/db.py`：**修掉那句过时注释**（它把调用方推向了 getattr 探测，正是盲区的成因之一）；
- 新增 `api/tests/test_capability_protocols.py`：
  - 守卫 1：源码扫描，**已协议化的 6 个能力名**不许再出现在 `getattr(x, "<name>", ...)` 里（不禁止 `getattr` 本身——读 `route.path` 之类正当）；
  - **故意违规的自检**：喂 3 种违规写法证明守卫会红 + 3 种正当写法证明不误报（否则「守卫是绿的」分不清是真干净还是规则写错了）；
  - 自检 2：协议判定行为（只认「有没有声明」，不认值）；
  - 自检 3：真实实现与协议对得上（两个 queue 驱动满足 `StatsCapable`；已注册 pipeline 声明了可选能力就必满足对应协议）；
  - 自检 4：白名单里的每个能力名在源码里确实存在（守卫不是空转）。

**验证**：`test_capability_protocols.py` 12 passed；后端全量 **1812 passed, 14 skipped**（= 1794 + H10 4 + H8 2 + H7 12）；`ruff check src tests` All checks passed。

**与审查建议的差异**：审查建议「8 处改 isinstance」——照做；但「加一条源码扫描守卫拦 `getattr(x, "name", default)` 形式的能力探测」**未照原样做**：按那个写法会误报所有正当 getattr（读 `route.path`、读 ORM `rowcount`、读 `ModuleSpec` 可选字段等）。改为**白名单式**——只盯已协议化的那 6 个能力名，误报面为零，覆盖面不减。

### 3.5 T37 · 生图无在途上限

**发现原文**（`docs/TODO.md` T37）：待补

**核实（方式）**：
1. 读 `docs/TODO.md` T37 —— 诉求是「生图也加一条跟任务一样的在途上限」，并记着「计数口在 task 模块，image 不许 import task」；
2. 读 `modules/task/service.py::_assert_in_flight_room` + `task/repo.count_in_flight` —— 任务侧口径是 **queued + running 的条数**，上限来自 `TASK_MAX_IN_FLIGHT_PER_USER`，`0` = 不限，超限抛 `RateLimited`；
3. 读 `modules/image/models.py` —— **生图有独立的 `ImageJob` 表**，带 `owner_id` 与 `status` → **image 模块可以自数自己的在途**，TODO 里「计数口在 task 模块」的顾虑不成立（它源于「生图记录不在 image 模块」的旧认知）；
4. 读 `modules/image/service.py::generate` —— 模型在受理时校验、记录落库后投递队列；插入点选在**最前面**（这一步不依赖 payload，被挡住时不该先花时间做模型解析与参考图解析）；
5. 读 `modules/image/router.py` —— 生图入口是 `POST /generations/image`（`/images` 是历史列表与批量删除）；
6. 读 `core/config.py` —— 加 `image_max_in_flight_per_user: int = 5`（与任务同值）；
7. 读 `tests/conftest.py` —— **必须**在测试环境把新闸门默认关掉：既有生图用例会连着造好几条记录，限着会莫名拿 429（与 `TASK_MAX_IN_FLIGHT_PER_USER=0` 同一处理）。

**结论**：成立。**改动**：
- `core/config.py`：新增 `image_max_in_flight_per_user: int = 5`（含残留说明：与任务**分开计数**）；
- `modules/image/service.py`：新增 `_assert_in_flight_room()`（口径同任务：queued + running，`0` = 不限，超限抛 `RateLimited`，消息里带**上限**与**当前在途数**两个数），并在 `generate()` 最前面调用；
- `.env.example`：补 `IMAGE_MAX_IN_FLIGHT_PER_USER=5`（配置守卫 `test_config_guard.py` 要求每个键都出现在示例里，第一次跑全量回归时正是它红了）；
- `tests/conftest.py`：默认把 `IMAGE_MAX_IN_FLIGHT_PER_USER` 设成 0（关掉）；
- 新增 `api/tests/test_image_in_flight_cap.py`（6 条）：达到上限被拒且消息带两个数 / 上限 0 = 不限 / 跑完一条即释放名额 / 别人的在途不影响我 / **默认上限必须是开着的**（防止有人把默认值改成 0 → 闸门在所有部署里静默失效）/ **闸门接在提交路径上**（AST 扫描 `generate()` 里必须调用 `_assert_in_flight_room`——删掉它没有任何症状）。

**验证**：`test_image_in_flight_cap.py` 6 passed；后端全量 **1818 passed, 14 skipped**（1812 + 6）；`ruff check src tests` All checks passed。

**残留（2026-11-23 晚逐条核实后的结论）**：这条与 `task_max_in_flight_per_user` **分开计数**——一个人可以同时挂满 5 条任务 + 5 条生图。**核实后确认：这是对的，不是待修的漏洞。**两条链路默认**不共用**同一个上游资源：

- 视频默认 LibTV：占**共享账号**的上游排队位次，且每 2-5 秒一跳 poll；
- 生图默认 Agnes：**同步**提交（`image/pipeline.py`：submit 直接返回终态、框架不轮询），既不产生 poll 负载、也不占本机 worker 槽位（等出片不占槽，见 `docs/CAPACITY.md` §1.1）。

真正会撞的只有「**同一个供应商被两条链路同时用**」（百炼视频 + 百炼生图吃同一份配额）。那时该封的是「一人在**同一供应商**上的在途总数」，而不是「视频 + 生图之和」。

⚠️ **更正本节初稿写的方向**：「要封住这个口子得让两条链路共用一个在途计数口」——共用一个口把两链路上限**合并**，正是会误伤的那种做法（默认组合 LibTV 视频 + Agnes 生图本来就不争抢，用「视频用户不许同时生图」去防「抢供应商」防错了对象）。触发条件与做法已写进 `core/config.py` 里 `image_max_in_flight_per_user` 上方的注释；同步更正了 `docs/CAPACITY.md` §2.3 / §3 与 `docs/TODO.md` T37 的过时表述。

### 3.6 M7 · 跨组引用/删除保护走 JSON LIKE + `_MAX_IDS=200` 静默截断

**发现原文**（`REVIEW_2026-11-21_DESIGN.md:154`）：
- **证据**：`library/references.py:40,48,61-73`、`library/access.py:122-132`、`models.py:334-337`
- **建议方向**：下载任务的资产引用拆成关联表；批量删除不静默截断

**核实（方式）**：
1. grep `_MAX_IDS` 全库 → **3 处静默切片**：`library/references.py:40`（=200，`:48` 是切片点）、`task/library_references.py:30`（=50）、`image/library_references.py:28`（=50）→ **「静默截断」这个事实成立**；
2. 读 `core/ports/library_references.py::ask_library_references` 全文 → 三个 checker **只**通过 `ModuleSpec.library_reference_sources` 注册 → 只被聚合入口调用，无绕过路径；
3. grep `ask_library_references` 调用点 → **3 处，全部只传一个 id**：`library/service.py:474`（详情展示）、`library/lifecycle.py:298`（`purge`）、`library/lifecycle.py:333`（`purge_expired`）→ **截断在当前调用链上不可达**；
4. 读 `modules/library/bulk.py` 全文 + 文件头注释 → 批量动作 `BulkAction.DELETE` 走 `LifecycleService.delete`（**进回收站，可恢复**），而 `purge`（彻底删除）**刻意不在批量动作里**：「不可逆操作一次点错十条就没救了」→ **审查的因果链（「批量删除 → 静默截断」）不成立**；
5. 读 `library/access.py:100-160` → 审查引用的 `:122-132` 现在是 visibility 逻辑（`active_visible_clause` / `tab_clause`）；**跨组删除保护现在在 `lifecycle.py:113-128`**（`can_revoke_and_delete` / `SharedAssetNotDeletable`），且它查的是**关联表** `LibraryAssetGroup`（`access.py::active_visible_clause` 里的 `group_branch` 子查询）——**不是 JSON LIKE**，本来就与共享模型统一 → **审查这半句已过时**；
6. 查 JSON LIKE 实际用在哪 → 只在**引用检查**（下载任务 `asset_ids`、生图 `reference_json`、视频 `reference_library_ids`）→「模型不统一」（共享关系用关联表、引用关系用 JSON 列）这半句**成立**。

**结论**：**部分成立**。「静默截断」的代码事实成立；但它**当前不可达**（所有调用点只传一个 id），且审查给的两条证据链（批量删除、跨组保护走 JSON LIKE）**都已过时或不成立**。真正的风险不是「正在丢数据」，而是「**机制上留着丢数据的口子**：一旦将来有人加批量 `purge`（一次传几十个 id），超限部分被静默丢掉 → 漏查 → 引用检查判成「没人引用」→ 不可逆删除放行，且没有任何痕迹」。

**改动（已做，低风险部分）**：
- 三个 checker 的 `[:_MAX_IDS]` 静默切片 → **超限即抛 `Unavailable`（503，fail-closed）**：`library/references.py`（新增 `_wanted()` 集中判断）、`task/library_references.py`、`image/library_references.py`；
- 抛 503 而不是 400：这是**服务端调用方式**的问题，不是用户输入问题；且删除路径必须 fail-closed —— 宁可这次不删，也不要在可能漏查的前提下删掉；
- 新增 `api/tests/test_library_reference_limits.py`（9 条）：超限报错 ×3（三个 checker 各一，消息带上限与实收数）/ 恰好等于上限不报错 ×3（上限是边界不是陷阱）/ **源码守卫**：三个 checker 里不许再出现 `[:上限]` 形状的切片（AST 扫 `Subscript`+`Slice`，钉形状不钉变量名）/ **守卫**：`purge` 不得出现在批量动作里（钉住原始设计）/ 端到端：资产被视频任务引用时彻底删除仍被拦（409 且说清是哪个任务）。

**验证**：`test_library_reference_limits.py` 9 passed；后端全量 **1827 passed, 14 skipped**（1818 + 9）；`ruff check src tests` All checks passed。

**待确认（本轮按指示跳过）**：「下载任务的资产引用拆成关联表」——涉及迁移 + 模型改写 + 引用检查改写 + 旧数据兼容，属一次独立的大重构，本轮未做。需用户拍板是否要排期。

**→ 已于 2026-11-23 晚补做（用户拍板「直接做 1」），见 §3.7。**

### 3.7 M7 补做 · 下载任务的资产引用拆成关联表

**触发**：用户拍板「直接做 1」——把 §3.6 标为待确认的「下载任务资产引用拆成关联表」做掉。

**核实（方式）**：
1. grep `asset_ids` → 该列只有 **4 个使用点**（模型定义、写入点 `downloads.py:128`、读取点 `_to_out`、打包处），无隐藏引用；
2. 读 `downloads.py` 全文 → **`asset_ids` 的顺序有语义**：包内文件按 `001_名`、`002_名` 命名，序号来自用户勾选顺序 → 拆表必须保住顺序，不能只保集合；
3. 读 `models.py` 的 `LibraryPackItem` → 同款关联表参照（`pack_id` / `asset_id` / `position`，**刻意没加外键**）→ 新表照它的形状；
4. 读 `core/ports/library_references.py` 全文 + 三个 checker → 引用检查的三个来源都走同一个注入端口，下载任务那支原本是 `LIKE '%"id"%'` 的**子串粗筛**（外加二次精确比对兜底）；粗筛的正确性依赖 JSON 文本形状，而它的落点是**不可逆删除**；
5. 读 `library/__init__.py` 的 `ModuleSpec` → 来源声明的位置字符串要一并改（静态守卫对账 `location` 指向的文件/符号是否存在）；
6. 读 `tests/test_architecture.py` 的引用字段守卫 → 它只盯 `reference_library_ids` 字段名，本改动**不触发**；
7. 读 `migrations/versions/e5b91c4d7a26_*`（数据搬迁型迁移先例）+ `db/base.py` 的 `TimestampMixin`/`UUIDMixin` → 迁移写法与列定义照抄；
8. 读 `tests/test_migrations.py` → 已有守卫：`compare_metadata` 零差异、空库能升 head、每个迁移必须有 `downgrade`、**带数据的库也要能升上来**（这正是本次改动的风险点）；
9. `alembic heads` → 迁移链当前 head 为 `c4f1a82e7b60`，新迁移挂在它后面。

**结论**：成立，且形状可照 `LibraryPackItem`。

**改动**：
- **模型**（`modules/library/models.py`）：新增 `LibraryDownloadJobAsset(job_id, asset_id, position)`；`LibraryDownloadJob.asset_ids`（JSON）→ `asset_count`（冗余计数，与既有的 `size_bytes` 同一做法——任务的资产集合创建后不可变）；
- **引用检查**（`modules/library/references.py`）：`DownloadJobReferences` 从「JSON 文本粗筛 + 二次比对」改成关联表上的**一次 `IN` 查询**；`_escape_like` 一并删掉；
- **写入/读取/清理**（`modules/library/downloads.py`）：建任务时写关联行（`position` = 下标）；打包时按 `position` 读回顺序；GC 删历史行时**一并删关联行**（无外键，级联得自己写）；
- **来源声明**（`modules/library/__init__.py`）：`name` / `location` 改指新表；
- **迁移**（`migrations/versions/b7c4e91f2a58_download_job_assets_table.py`）：建表 + 索引 → **把存量 `asset_ids` 逐行搬进关联表并回填 `asset_count`** → 删 JSON 列（sqlite 走 `batch_alter_table`）；`downgrade()` 反向搬回并删表；
- **测试**（`tests/library_support.py`、`tests/test_library_downloads.py`）：helper 造任务时写关联行；`fetch_download_job` 改从关联表取清单；
- **新增** `tests/test_download_job_assets_table.py`（7 条）：清单在关联表且 JSON 列没了（**模型层 + 库层都查**）/ `position` 保住顺序 / 端到端包内序号 001-002-003 / 引用检查只报真正被引用的资产 / 前缀相似的两个 id 不互相误报 / **源码守卫**（`references.py` 不许再出现 `.like(`）/ GC 清历史行后关联行不残留；
- **新增** `tests/test_migrations.py::test_download_job_manifest_moves_to_the_link_table`：带数据的旧库升上来 → 关联表 3 行且**顺序不变**、`asset_count` 回填、JSON 列消失。

**验证**：
- `tests/test_download_job_assets_table.py` 7 passed；`tests/test_migrations.py` 10 passed（含 `compare_metadata` 零差异）；
- **手工复验迁移**（临时 sqlite 库，跑完即删）：upgrade → 关联表行/计数/列结构全对；downgrade → JSON 列回来、计数列与关联表消失；
- 后端全量 **1835 passed, 14 skipped**（1827 + 8）；`ruff check src tests` All checks passed。

**过程中修掉的一处隐患**：迁移里一开始把 Python `datetime` 直接绑给 sqlite（走 sqlite3 的**默认 datetime adapter**）——Python 3.12 起已弃用，且写出的格式带 `+00:00` 与 ORM 写入的格式（无时区后缀）不一致。改为**按方言给不同形状**：sqlite 传与 ORM 逐字一致的字符串，PG 传 aware datetime（`timestamptz` 需要时区）。改后回归里那条弃用警告消失。

**残留（2026-11-23 晚逐条核实后的结论）**：

1. 关联表**刻意没加外键** → **不需要再做什么**。核实：全库删 `LibraryDownloadJob` 的地方**只有一处**（`downloads.py` 的 GC purge），且已在同一段里先删关联行；`DownloadJobReferences` 走 **INNER JOIN**（关联行必须对得上任务行）→ 孤儿行**不会**被误报成引用，只是没人清理的垃圾数据。
   ⚠️ **更正**：本节初稿（以及 `downloads.py` 的注释、测试 docstring）写的是「孤儿行会让引用检查报出『还被下载任务 xxx 引用』，于是这条资产永远删不掉」——**这是错的**，INNER JOIN 会把孤儿行过滤掉。三处已改为「垃圾数据」的准确说法。结论不变（级联删除仍要自己写），但**理由**从「防误判」改成「数据卫生」——后者也顺带说明了「别为防误判而加外键」。
2. `asset_count` 是**冗余计数** → **前提成立，不需要做**。核实：`selected`（建任务时实际入包的那批）同时喂给 `asset_count` 与关联行，两者**恒等**；此后清单从不被改（两处 `update` 只改 `status` / 时间 / 大小 / `storage_key`）；打包时若有资产失效只记 `notes` 并跳过，**不改清单**（与旧版 `len(asset_ids)` 行为一致）。
3. 生图 `reference_json`、视频 `reference_library_ids` **仍是 JSON 列** → **暂时不做**。核实：
   - 生图那支**不划算**：`reference_json` 里除 `assetId` 外还有**快照字段**，拆 id 出来会变成「id 在表里、快照在 JSON 里」的**双写**（两个真相）；
   - 视频那支**是纯 id 列表**、形状与下载任务同款，将来要统一模型应该拆它——但它现在的正确性由「粗筛 + 二次精确比对」兜住（粗筛只多捞不漏），收益仅**性能**与「不依赖文本形状」，成本却是 1 张表 + 1 个迁移 + 改写入点 + 改读取点；
   - 更要紧的「形状变了导致**漏报**」风险，已有端到端用例覆盖（`test_task_library_references.py`、本轮的 `test_library_reference_limits.py`：「资产被视频任务引用时彻底删除仍被拦」）。
   → 触发条件：**数据量大到粗筛变慢**，或 JSON 形状真的要改时，再拆视频那支。
4. `assetCount` 前端展示 → **不存在这个问题（撤回）**。API 字段名与语义都没变（列表页读的仍是 `assetCount`，只是值来自冗余列而不是 `len(asset_ids)`），UI 无需改动。

## 4. 待核实 / 待确认 汇总（滚动更新）

### 待核实
| 项 | 缺什么 | 怎么补 |
|---|---|---|
| T35-5 | 原函数 `template/worker.publish_output` 已随模板库重构消失，「失败仍记 success」在新 `template_pack` 链路是否仍存在？ | 读 `template_pack` 的发布/产物落盘路径，确认失败分支是否被吞 |
| T35-4 | 文档登记「resume 全扫无 limit」，但 `resume_unfinished_tasks` 已带 `limit` 参数 → 登记与代码不一致 | 核对调用方是否真的传了 limit |
| T35-6 | 文档登记「libtv 监督无超时」，但 `communicate()` 已带 `timeout` → 同上 | 核对超时值与超时后的行为 |

### 待确认
| 项 | 需要谁拍板 | 问题 |
|---|---|---|
| H12 | 产品 / 用户 | 用量页：摘导航还是加权限门控？无权限用户看到什么？是否删除页面里渲染的后端 API 路径？ |
| T22 | 产品 | 归档检索与批量写要不要做、做成什么样 |
| T23 | 产品 | 上传秒传 / 断点续传 / 视频派生预热的排期 |
| 审查 #11 | 产品 | CSV 空值语义：空单元格 / 空字符串 / 缺失列如何区分 |
| H4 / M13 / M16 | 架构 | 幂等键格式规范、事件去重 vs 内容去重的边界、override 顺序规则 |
| H11 | 架构 | LibTV 专属面：可选协议化还是拆独立模块 |
| T30 | 已拍板 | PG 迁移闸观察期——维持暂不动 |
| M7 子项 | 已拍板并完成 | 「下载任务的资产引用拆成关联表」——用户拍板「直接做 1」，已实现（§3.7） |

## 5. 提交与验证口径

- 后端：`api\.venv\Scripts\python.exe -m pytest -q`、`-m ruff check src tests`
- 前端：`npm run typecheck`、`npm run lint`、`npm test -- --run`
- 契约：改后端入参必须 `python -m cli export-openapi` 重生成四份生成物
  （`api/docs/openapi.json`、`api/docs/API.md`、`studio/src/shared/api/schema.ts`、`studio/src/shared/api/permissions.ts`）
- 提交切分：后端 + 契约一份、前端一份、文档一份
