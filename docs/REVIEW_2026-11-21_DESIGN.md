# 2026-11-21 全量设计审查

> **这是一次快照评审，不是活文档**。行号以 `f45ba14f` 为准；代码改动后请重新核对证据。
> 本次只读审查，**未修改任何代码**。CI 在该提交上三个 job 全绿（后端 1690 passed）。

## 0. 一页结论

系统在「用机器守约定」上做得少见地好：模块边界、契约字节比对、权限单点求值、迁移多道闸都有真守卫。但设计层面的问题高度集中在两条主线：

| 主线 | 含义 | 典型例子 |
|---|---|---|
| **A. 同一概念多处真相** | 一个定义被复制成 N 份，靠人工同步；漂移后测试仍绿 | 终态词汇 4 份、模板语法正则 3 份、响应封套 3 份、契约四份里两份读全局单例 |
| **B. 约定写在注释里但没有机制执行** | 规范要求与代码形状脱节，守卫看不见违规 | 协议 + isinstance 被 8 处 getattr 绕过、权限字面量被 13 处属性访问绕过、service 必须 commit 只有反向守卫 |

这两条比任何单个 bug 都更值得先处理：前者让"改一处"变成"改五六处"，后者让全绿变成错误的安全感。

**建议处理顺序**：先收敛定义（P0），再补守卫（P1），再动数据层（P2），最后确认几个产品取舍（P3）。详见 §5。

---

## 1. 审查范围与方法

四路并行只读扫描，然后回到代码交叉核对关键结论：

| 路线 | 范围 |
|---|---|
| 后端架构 | `api/src/` 的 api / core / db / infra / generation / modules / scaffold / cli / workers，及模块发现与注入口 |
| 数据模型与权限 | `api/src/db/`、`api/migrations/`、`modules/identity/`、`core/permissions*`、`modules/library` 的标签与共享模型 |
| 核心业务链路 | library 入库、template_pack 编译、task 聚合、model_driver、generation 恢复、shot / storyboard |
| 前端与契约 | `studio/src/` 全量，`schema.ts` / `permissions.ts` 与后端契约的对齐 |

**判别标准**：只把「结构本身导致多处真相、职责错位、约定无法执行」的算作设计问题；单点写错、测试没覆盖但结构合理的，单独标注，不混进来充数。刻意取舍标注为【取舍】。

交叉核对过的证据包括：`getattr` 能力探测（8 处）、`has(access.*)`（13 处）、真实 FK/CheckConstraint（0 处）、`isStateChanged` 调用点（0 个）、`ApiResponse` 定义（3 份）、`c2b7d4e91a06` 的 no-op downgrade、生成入库的 sha256 短路分支、`UsagePage` 的占位文案。

---

## 2. 系统性主线 A：同一概念多处真相

| 概念 | 副本位置 | 后果 |
|---|---|---|
| 生成终态 | `generation/spec.py:22`（`succeeded`）、`modules/task/pipeline.py:42`（`completed`）、`modules/image/pipeline.py:50`、`modules/model_driver/models.py:21`（多一个 `cancelled`）；feed 侧再写两份归一化 | 新增一种生成能力要同步改 4–5 处；跨链路聚合必须先归一 |
| 模板槽位语法 | `core/template_spec/compile.py:47`、`core/template_spec/rules.py:54`、前端 `specDraft.slotRefPattern` | `rules.py:63` 注释自承"已经漂移过三次"；坏引用曾原样进供应商提示词 |
| HTTP 响应封套 | `studio/src/shared/api/schema.ts:14`（生成物）、`shared/api/types.ts:2`、`shared/api/rest.ts:19` | 真正解包走手写版；后端改封套字段时生成物更新而手写版零报错 |
| 契约四份 | `openapi_export.py:657,483` 的渲染函数读全局 `catalog`，依赖 `build_spec()` 先跑过；tag 顺序是含已下线模块的手写清单 | 单独调渲染函数产出残缺文件；新增模块分组必错位 |
| 反馈分组 | `image/feed_provider.py:38-43`（SQL）与 `:146-153`（Python）；`task/feed_service.py:161` 又写一份 | 改分组要改 3–4 处，视频侧缺"成功但无产物→attention"的对称规则 |
| 权限判定 | 集中求值（`core/authz.py`）是好的；但 `core.ports.library_lookup.py:79`、`library/schema.py:77,506` 三套同值枚举 + 大量裸字符串 | "两处判定必须同口径"的注释反复出现，用人力补类型缺失 |
| 标签深度 | `library/models.py:159,180` 仍写"最多三级"，`library/tags.py:5,89` 是 `MAX_TAG_DEPTH = 2`，迁移 `c2b7d4e91a06` 又写"压成两级" | 领域规则没有权威载体，照注释写的新代码会造出三级数据 |

---

## 3. 系统性主线 B：约定写在注释里但没有机制执行

| 约定 | 违反情况 | 守卫现状 |
|---|---|---|
| 可选能力用协议 + isinstance，别 getattr | `generation/pipeline.py:106,120`、`runner.py:829,901`、`model_driver/store.py:132` 等约 8 处；同文件注释还在主张反面 | 无扫描守卫；拼错钩子名静默降级、测试全绿 |
| 权限判定写字面量，不用 `access.XXX` | `modules/library/` 下 13 处 `has(access.PERM_*)` | `test_permission_coverage.py:141` 的 `_strings()` 不处理 `ast.Attribute`，双向检查全盲 |
| service 必须自己 commit | 约 104 处手工 `await commit()` 散落 15 个文件 | 只禁止 router 出现 commit/rollback/flush（`test_architecture.py:953`），正向一面空白 |
| 不可逆迁移要带 `irreversible = True` | 已落地且测试守着；但判据只认"downgrade 会抛"，`c2b7d4e91a06` 这种有损但可运行完的迁移被当作可逆 | `scripts/downgrade_anchor.py:27` 无法表达"数据不可还原" |
| feature 之间不许横向 import | 前端 ESLint 只生成"向上依赖"禁止项（`eslint.config.js:26`），7+ 处已发生跨 feature 引用 | 没有源码扫描守卫补相对路径的口子 |
| 删除前必须知道引用情况 | `LibraryDetail.tsx:442` 用"引用检查当前不可用"的文案替代能力 | 删除是不可逆动作，文案把风险判断推给用户 |

---

## 4. 分级发现

### 4.1 高优先级

#### H1 · 生成终态没有单一定义
- **证据**：`generation/spec.py:22-27`、`modules/task/pipeline.py:40-42`、`modules/image/pipeline.py:50`、`modules/model_driver/models.py:21-22`；`modules/task/enums.py:3` 明写"没有状态机表"。
- **为什么是设计问题**：框架提供了 `GenerationRecord.terminal` 想统一，但每个 store 为了做数据库条件更新必须再持一份 `_TERMINAL`，同一概念两个真相源靠人工同步。
- **影响**：新增能力要改 store 的 `_TERMINAL`、record 的 `terminal_statuses`、feed 分组、前端状态标签；漏一处即"同一状态两处不同义"。
- **建议**：终态词汇做成业务模块声明一次的 `LifecycleSpec`，store guard / record / feed / 契约全部从它派生；或全链路统一 `succeeded`，只在 API 出参映射成 `completed`。【取舍】task 用 `completed` 是对前端契约的刻意选择，问题在于它没被收敛到一层。

#### H2 · 模板小语言没有形式化定义
- **证据**：`core/template_spec/compile.py:47` 与 `rules.py:54` 各一份槽位正则，前端 `specDraft.slotRefPattern` 第三份；`rules.py:63-67` 注释自承三处已漂移过三次。
- **为什么是设计问题**：这是语言定义问题，不是某个正则写错——语法形状没有单一 source of truth。
- **建议**：建 `core/template_spec/grammar.py` 作为唯一语法定义（token 正则、normalize、modifier 语义、leftover 检查），编译、校验与前端共用或由后端生成。【取舍】单遍 `re.sub` + `empty_text` 禁引用是"结构性禁循环"，不是循环检测，属于刻意选择。

#### H3 · 契约四份并非单一真相
- **证据**：`api/src/openapi_export.py:657-669,699,720` 的 `render_permissions()` 读全局 `catalog` / `page_catalog`；`:382-385,483` 的 `render_markdown()` 同样；`tests/test_openapi_contract.py:71` 明写 `build_spec()  # 触发模块发现，填满 catalog`；`:516-529` 硬编码 tag 顺序，含已下线的"素材"/"模板"。
- **为什么是设计问题**：官方 CLI 流程碰巧顺序正确，但"四份文件同源"是假的——两份来自 spec，两份来自可变全局注册表。
- **建议**：`build_spec()` 返回 `Contract(spec, permissions, pages)` 快照，渲染函数显式接收；tag 顺序从 `ModuleSpec.order` 派生或删掉手写清单。

#### H4 · 幂等边界混用：事件身份 vs 字节身份
- **证据**：`modules/library/ingest.py:267-284` 先按 `source_key` 查，未命中再按 `sha256 + kind` 查；字节命中直接返回旧行且不写 `LibraryAssetGeneration` 快照（文件头 `:15-25` 有说明）。
- **为什么是设计问题**：把"上传秒传"的字节即身份语义套到"生成事件"上。生成侧明明用带 `external_id` 的 `source_key` 表达事件身份（`image/pipeline.py:443`、`task/pipeline.py:335`），却被对象身份判定抢先合并。
- **影响**：两次不同 prompt/模型/seed 生成相同字节时，库里只留一条、快照属于第一次，而任务中心显示两条已完成；第二次的 lineage 永久丢失，`created=False` 还让调用方以为已存好。
- **建议**：生成入库只用 `source_key` 做事件幂等，sha256 只用于 blob 物理去重；若产品坚持"相同图只存一条"，必须为每次生成保留独立 lineage 记录。【取舍】用户 2026-11-20 以"共享库 70 个产物中 0 例"判过不优化；此处作为边界定义问题保留。

#### H5 · 所有关键不变式只在 service / 测试层，数据库零约束
- **证据**：`modules/library/models.py:8-9,18-19,176-181`、`modules/template_pack/models.py:23`；全项目 0 个真实 `ForeignKey` / `CheckConstraint`（唯一 grep 命中是注释）。
- **为什么是设计问题**：这是贯穿全项目的一致策略，不是单点遗漏。sharing / lifecycle / tags / ingest / maintenance 多个服务都能写同一批表，测试只能钉住已知调用路径。`library/access.py:316-318` 的注释描述过"既删不掉也撤销不了，只能手工改库"的状态，说明真实发生过。
- **建议**：增量下沉而非全盘推翻——可枚举的局部不变式（`state` / `visibility` / `effect` 值域，`state∈{draft,trash} ⇒ visibility='personal'`）用 DB CHECK 固化；把合法写入口收敛到 repository 层。【取舍】无 FK 是为了切换期回滚不被级联带走、sqlite/pg 行为一致，不要为补约束破坏它。

#### H6 · 「不可逆」判据与真实数据损失脱节
- **证据**：`scripts/downgrade_anchor.py:27-28,99-106` 只认模块级 `irreversible = True`；`migrations/versions/c2b7d4e91a06_tags_two_levels.py:139-145` 的 `downgrade()` 空实现，而 `:60-64` 承认不承诺数据无损；`tests/test_downgrade_anchor.py:291-339` 只验证"标记了会抛"。
- **为什么是设计问题**：判据把"可逆"定义成"downgrade 不抛异常"，无法表达"降级后数据不可还原"这一类。
- **影响**：标签三级→两级这道最大的数据迁移（合并词、删行、转移绑定），降级后留下旧代码无法正确解释的库，CI 却报告"降级成功"。迁移 docstring 自己算过"筛选范围只会更宽、不会更窄"，属于已接受的债务，但守卫没承认这个决定。
- **建议**：给有损但可运行完的迁移加 `lossy_downgrade = True` 并计入锚点；或让这类 downgrade 直接抛错。

#### H7 · 协议 + isinstance 被 getattr 系统性绕过
- **证据**：主张在 `infra/ai/base.py:158-169`、`generation/pipeline.py:50`；违反在 `pipeline.py:106,120`、`runner.py:829,901`、`model_driver/store.py:132`、`api/diagnostics.py:360`、`api/instrumentation.py:142`、`modules/health/checks.py:119`。
- **为什么是设计问题**：可选能力的名字有两套权威来源；getattr 静默降级，协议改名立刻类型失配，两套机制互相抵消。
- **影响**：`persist_output` / `supports_poll` / `stats` 任一改名，分别导致产物不落盘、同步供应商被当异步续查、监控静默变空，且测试全绿。
- **建议**：补 `Persistable` / `ArchiveRequestable` / `StatsCapable` 协议，8 处改 `isinstance`；加一条源码扫描守卫拦 `getattr(x, "name", default)` 形式的能力探测。

#### H8 · 13 处权限判定绕过 AST 守卫
- **证据**：`modules/library/bulk.py:175`、`downloads.py:196`、`service.py:306,309,514,687,721,874,1402,1407`、`tags.py:521,523`、`text_assets.py:335`；守卫 `tests/test_permission_coverage.py:141-152` 只解析 `ast.Constant` / `ast.Name`。
- **为什么是设计问题**：守卫正确性依赖源码形状约定，13 个调用点违反后"僵尸码 + 未声明码"双向检查同时出现盲区，只是碰巧别的字面量调用点覆盖了同一个码。
- **影响**：`library:delete:any` 改名或删声明时，这 13 条跨人删除/审核判定不会被任何测试报错。
- **建议**：按约定改回字面量 `actor.has("library:delete:any")`，再补 `assert "has(access." not in source` 棘轮。

#### H9 · 前端同层横向 import 完全不设防
- **证据**：`studio/eslint.config.js:26-33` 的禁止列表不含 `@/features/*`，且 `:21` 自认相对路径也拦不住；已发生 `features/group-editor/GroupEditorModal.tsx:14-15`、`role-editor/RolePermissionsModal.tsx:8`、`permission-overview/UserPermissionsPanel.tsx:15`、`template-library/slotIndexRefs.ts:2` 等多处。
- **为什么是设计问题**：守卫存在却故意放过了整个同层维度。
- **影响**：`PermissionPicker`、`specDraft` 实质是共享资产却住在 feature 里，改一处连带 4 个 feature 回归。
- **建议**：把共性组件下沉到 `entities/permission/ui`、`entities/template-spec/lib`；补源码扫描守卫覆盖相对路径。

#### H10 · 响应契约三份定义 + 409 语义处置只实现一半
- **证据**：`schema.ts:14`、`types.ts:2`、`rest.ts:19`；`isStateChanged`（`types.ts:69-72`）全仓零调用点，零调用点已交叉核对；后端 `api/src/api/errors.py:100-116` 明确要求"重读权威状态"；`pages/task-center/TaskCenterPage.tsx:297-299` 只 toast；`features/library/TextVersionHistory.tsx:22-25` 私有 `isConflict` 只判 409。
- **为什么是设计问题**：错误模型在判定层收敛了，在处置层却分散且判定函数成了死代码——注释描述的机制并不存在。
- **影响**：并发删除/恢复版本时用户只看到裸错误文案，不知道该刷新；后端错误语义被降级。
- **建议**：`http.ts` / `auth.ts` 改从 `schema.ts` 引 `ApiResponse`，`PageResult` 统一为 `Page`；在统一 mutation 封装消费 `isStateChanged` 触发 `invalidateQueries` 并明确引导。

#### H11 · 供应商抽象名不副实
- **证据**：`infra/ai/base.py:1-7` 宣称"只保留一个 provider 协议"，实际还有四个可选 Protocol（`:173-214`）和四个 getattr 开关；LibTV 的账号/计费/排队 REST 长在通用 router（`modules/model_driver/router.py:192-289`），`modules/model_driver/libtv/` 独占 7 个文件；`model_driver/spec.py:37-38` 承诺"不需要改 .env、不需要注册、不需要写路由、不需要碰 generation/"。
- **为什么是设计问题**：供应商能力实际由"1 主 + 4 可选 + 4 开关"共同定义，厂商专属概念穿透了面向能力的抽象。
- **影响**：新增一家带自己计费/队列语义的供应商要改 router + 专属子包 + 前端，成本远高于文档承诺。
- **建议**：把计费/排队做成 `BillingCapable` / `QueueCapable` 可选协议统一注册，或把 LibTV 专属面拆到独立模块。【取舍】sync/async 双形态建模是有意的。

#### H12 · UsagePage 是整页假交互，且无权限门控
- **证据**：`pages/usage/UsagePage.tsx:50-64` 时间筛选是 span（注释"静态占位"）、`:87-92` 两处"图表占位"、`:116-133` 把 `/usage/summary` 等后端路径渲染给终端用户；`modules/usage/module.ts:18-26` 是唯一不声明 `permissions` 的模块，`app/router.test.ts:37` 显式豁免。
- **为什么是设计问题**：占据导航位的页面对用户零价值，注释本身在替设计缺陷解释。
- **影响**：用户以为看板故障；任何登录用户都能看到未完成功能；空态没有出口。
- **建议**：接口未就绪前从导航摘掉，或加权限门控；保留则改成"即将上线 + 去任务中心"的真出口，禁止渲染 API 路径。【取舍】模块注释说明是等后端 `usage:read` 的 fail-closed 考虑，但代价被转嫁给用户。

#### H13 · 写操作测试守卫空转
- **证据**：`features/user-roles/AssignRolesModal.test.tsx:8-10,33-45` 把 `mutate` mock 成 `vi.fn()`，只断言提示文案与按钮 enabled，从未断言调用参数/成功关闭；`features/permission-layers/no-explanatory-copy.test.tsx:32-40,96-149` 同理；`features/task-preview/` 三个组件与 `group-editor/GroupEditorModal.tsx` 无行为测试。
- **为什么是设计问题**：守卫测的是"文案在不在"而不是"按钮点了会发生什么"，全绿给的是错误的安全感。
- **影响**：保存参数错误、成功后不关闭等回归无网可接；最高风险的授权/删除链路恰好没覆盖。
- **建议**：给每个 mutation 调用点补"点击 → 断言参数 → 成功/失败态"三段测试；文案守卫单独归类，不充当行为测试。

### 4.2 中优先级

| 编号 | 发现 | 证据 | 建议方向 |
|---|---|---|---|
| M1 | 模块声明与副作用注册双轨，`ModuleSpec` 一处填完的叙事不成立；`assembly.py` 专门对账 | `api/src/api/module.py:91-172`、`api/assembly.py:1-30,52-57`、各模块 `__init__.py` | 让 `jobs` / `periodic` 直接承载可调用对象，由框架遍历注册 |
| M2 | `api/registry.py` 零引用死代码；`PORT_CATALOG` 手维护元信息与 port docstring 重复 | `api/src/api/registry.py:1-23`、`api/ports.py:106-205` | 删除死文件；用装饰器把元信息挂在 Port 实例上生成目录 |
| M3 | 事务边界只守"router 不许 commit"，没守"service 必须 commit" | `tests/test_architecture.py:953-977`、`tests/test_session_scope.py:1-79` | 统一 unit-of-work 或补正向静态守卫（静态扫描属实现缺口，根因是手工提交的设计） |
| M4 | 同一个东西两套开关：`backup_dir` 双默认值（`BACKUP_DIR` 对手动备份无效）、reload 双判据 | `core/config.py:351`、`cli.py:896,631,775-779`、`main.py:16-21`、`test_config_guard.py:106-123` | `app serve` 成唯一入口；`--out` 默认回落 `settings.backup_dir` 并纳入 cwd 守卫 |
| M5 | 两套并行启动恢复状态机，分类逻辑写两遍且被写成永久例外 | `generation/recovery.py:139-208`、`modules/task/worker.py:31-128`、`tests/test_architecture.py:687-710` | 分类提为纯函数 `classify(record, supports_poll)`，两处共用 |
| M6 | 标签深度规则无权威载体，模型注释仍写三级；`MAX_TAG_DEPTH` 只在 service 内存里 | `library/models.py:159,180`、`library/tags.py:5,89,132`、`migrations/versions/c2b7d4e91a06:1` | 单一领域常量 + 模型注释和出参都从它派生；CHECK 固化结构 |
| M7 | 跨组引用/删除保护走 JSON LIKE + `_MAX_IDS=200` 静默截断，与共享的关联表模型无法统一解释 | `library/references.py:40,48,61-73`、`library/access.py:122-132`、`models.py:334-337` | 下载任务的资产引用拆成关联表；批量删除不静默截断 |
| M8 | 同一概念三套 StrEnum + 大量裸字符串，`group/team/personal` 多义；"同口径"靠注释 | `library/schema.py:77-80,506-508`、`core/ports/library_lookup.py:79-81`、约 100 处字面量 | 用 NewType / 带语义前缀的枚举区分；同口径谓词复用 |
| M9 | 申请单 `note` 列混合用户输入与系统控制标记（`self_approved` 等） | `library/sharing.py:211,275`、`lifecycle.py:68,188,233`、`schema.py:526-529` | 拆出 `system_flag` 枚举列，`note` 只留用户输入 |
| M10 | 标签"是不是维度"靠树形状推导，无显式废弃状态 | `library/tags.py:288-290,307-320,659-703`、`migrations/versions/c2b7d4e91a06:52-58` | 加显式 `level` / `is_dimension` 列与状态列，可打标收敛为列值比较 |
| M11 | feed 分组映射 SQL / Python 各一份，两个来源各写一份、统计结构体两份 | `image/feed_provider.py:38-43,146-153`、`task/feed_service.py:161`、`feed_schema.py:41-47`、`core/ports/generation_feed.py:85-92` | 下沉为 core 纯函数/表，两个来源与统计共用 |
| M12 | 供应商级 `cancelled` 跨层语义不同：业务层独立终态，provider 边界折叠成 failed | `infra/ai/base.py:26-34`、`model_driver/models.py:21-22`、`drivers/agnes_video.py:289-296` | `GenerationState` 增加 `CANCELLED` 或独立 terminal-reason；runner 约定其不触发失败事件 |
| M13 | 幂等键命名空间分散三层、格式无规范（队列键 / `source_key` / DB 唯一索引） | `downloads.py:603`、`export/service.py:157`、`library/service.py` scoped_key、`task/service.py:329-330` | 定义"前缀 + 作用域 + 语义"规范并集中生成，区分事件去重与内容去重 |
| M14 | 任务中心是纯只读视图，但收藏/删除只覆盖视频一路，视图 API 语义不对称 | `task/feed_service.py:1,143-146`、`task/service.py:325-399`、`feed_schema.py:33-38` | 要么动作全留各源页面，要么定义 source-agnostic 卡片动作端口 |
| M15 | `core.ports` 是运行期全局服务定位器，11 个端口粒度两极、未注册策略散落 | `api/ports.py:106-205`、`core/ports/host.py:51-139`、`generation_feed.py:31-105` | 跨模块查询改构造期注入；同 owner 端口合并成 facade；统一未注册行为 |
| M16 | 编译前 override 是三条并行旁路，顺序是隐式契约 | `template_pack/spec_service.py:880-909,767-770` | 收敛成显式 pipeline，顺序与冲突规则在数据里声明 |
| M17 | `shared/ui` 收了一个自认的业务控件，开了先例 | `shared/ui/index.ts:39-42`、`shared/ui/ReferenceImages.tsx` | 增设 `entities/common/ui` 承接跨实体业务 UI【取舍：权衡已记录】 |
| M18 | `entities/asset` 是下线残留死代码，空目录若干 | `modules/library/module.ts:7-9`、`entities/asset/model/archive.types.ts`、`entities/asset/ui/*` | 删除目录；仍在用的 archive 字段迁入 `entities/library/model` |
| M19 | token 体系存在却被绕过：body 底色与 canvas token 差一档，全仓 270 处任意像素值 | `studio/src/index.css:17,26`、`tailwind.config.js:9,60`、`PackGroupPicker.tsx:167`、`TopBar.tsx:17` | `index.css` 改 `@apply bg-canvas text-ink text-body`；7/9px 纳入 token |
| M20 | Escape / 临时浮层有 4 套键盘机制，`Modal` 的栈只管 Modal | `shared/ui/Modal.tsx:6-11,57-78`、`MultiSelect.tsx:95-105`、`features/template-specs/DemoMenu.tsx:32-40`、`RichPromptEditor.tsx:449` | 抽象统一 overlay 栈（Escape 优先级、焦点归还、z-index）【当前用法未嵌套，暂无故障】 |
| M21 | 前端权限判定语义与后端不同源，数组用 AND、资源通配分支是死逻辑 | `shared/access/evaluate.ts:19-23`、`Can.tsx:22`、`core/permissions.py:107-157`、`identity/service.py:562` | 前端只保留精确匹配 + 超管；`Can` 单码化或明确改名 `useCanAny` |
| M22 | mock 与真实 API 两套错误码语义，无行为级对拍 | `mocks/adapter.ts:75-80`、`shared/api/types.ts:29-35`、`app/registry/mock-coverage.test.ts:10-12` | 关键端点加"mock 响应对 schema 形状"测试；mock 也发 `status*100` 码【覆盖率豁免是刻意取舍】 |
| M23 | 删除前引用检查缺失，用说明文字兜底 | `features/library/LibraryDetail.tsx:442-445,475` | 接上注入口；未接上之前对删除加显式风险二次确认 |

### 4.3 低优先级

| 编号 | 发现 | 证据 |
|---|---|---|
| L1 | 四个同名 `registry.py` 职责完全不同，其中一个是死代码；按名 grep 给虚假的安全感 | `api/registry.py`、`core/registry.py`、`db/registry.py`、`modules/model_driver/registry.py` |
| L2 | `_CODE_OWNER` 是进程内可变全局，测试用假模块名污染，断言依赖执行顺序 | `core/exceptions.py:73,125-133`、`tests/test_module_errors.py:51-102`（同文件 `105-127` 有子进程验证的正确范式） |
| L3 | `var/` 下留有多套契约快照副本，grep / 补全可能命中过期版本 | `var/pr12-check/api/docs/openapi.json`、`var/pr22/api/docs/API.md` 等 |
| L4 | JSON 列 + strict Pydantic 使 schema 演进与数据清洗强耦合，字段增删必须配清洗迁移 | `migrations/versions/667e0540179b:30-40`、`c2b7d4e91a06:97-102,271-295`、`spec_service.py:154,517`【已接受取舍】 |
| L5 | 生命周期概念反复"下线—重加—再下线"积累迁移债务与残留死代码 | `3ebfdacfbcca`、`67b2a1d5dfb3`、`9c1e4f7a2b58`、`c4f7a2b81d63`、`c2b7d4e91a06:298` 的未使用 `_new_id()`【产品决策反复】 |
| L6 | 注释与代码漂移：`user-access` 注释说写接口码，实际是页面码 | `studio/src/modules/user-access/module.ts:7,18` |
| L7 | 内部权限码进入用户文案（`page:user-access` 直接展示） | `shared/ui/ForbiddenState.tsx:23-25` |
| L8 | 空态出口不一致：有的只有文案无按钮 | `features/permission-overview/PermissionOverview.tsx:257` 对照 `features/template-specs/SpecWorkspace.tsx:767-779` |
| L9 | REST 工厂假设所有 delete 返回 `{deleted:number}`，但后端各资源不一致 | `shared/api/rest.ts:42`、`schema.ts:1996`（group delete 是 null）；目前只有 user 用该工厂 |
| L10 | 约 10 余个端点 `EndpointData` 为 `unknown`（文件流/导出） | `schema.ts:1979-1983,2099-2129`【二进制流不走 JSON 合理，但"创建导出 202"应有类型】 |

---

## 5. 建议处理顺序

| 阶段 | 动作 | 理由 |
|---|---|---|
| **P0 收敛定义** | ① 终态词汇表单点定义并从 store/record/feed/契约派生；② `core/template_spec/grammar.py` 统一语法；③ `build_spec()` 返回显式契约快照；④ 前端响应封套只留 `schema.ts` 一份；⑤ 标签深度单一常量 | 这五处是"改一处漏五处"的根源，收敛后守卫才有意义 |
| **P1 补守卫** | ⑥ 13 处 `access.` 改字面量 + AST 属性分支；⑦ 8 处 getattr 改协议 + 扫描守卫；⑧ feature 横向 import 守卫；⑨ mutation 调用点行为测试 | 让"全绿"重新可信；P0 未完成前补守卫会固化旧形状 |
| **P2 数据层** | ⑩ 增量 DB CHECK（保留无 FK 取舍）；⑪ `lossy_downgrade` 标记纳入锚点；⑫ 下载引用拆关联表、去掉 200 截断；⑬ `note` 拆系统标记列 | 涉及迁移与存量数据，独立排期、独立回归 |
| **P3 取舍确认** | ⑭ 生成 lineage 是否保留（上次判"不优化"，需重新拍板）；⑮ UsagePage 去留；⑯ LibTV 专属面是否拆模块；⑰ 幂等键规范 | 需要产品决定，不适合工程侧单方面改 |

---

## 6. 与现有文档的对账

| 已有文档 | 已记录的内容 | 本次新增的角度 |
|---|---|---|
| `TODO.md` | T25 多实例硬边界、T26 队列唤醒、T29 单进程指标、T30 迁移漂移只覆盖 sqlite、T36 九条 P2 等 | 本次不重复这些，聚焦"定义多处真相"与"约定无机制"两条结构主线 |
| `EXTENSIBILITY_GAPS.md` | A1 私有配置、A2 归档存储、A3 跨模块通道、B1 权限未自动授予、C1 轮询节奏、C2 重试预算等 | 补 M1 双轨注册、M2 死代码、M15 端口粒度两极 |
| `IMPROVEMENT_PLAN_2026-11-18.md` | P0/P1/P2 共二十余条健壮性改进（多数已修） | 本次发现偏"结构/约定"而非"健壮性"，与它互补 |
| `PERMISSION_PAGES.md` / `EXCEPTIONS.md` | 页面门控、四层并集、个人例外的设计依据 | 权限模型本身判定合理；问题在执行侧（H8、M21） |
| `TEMPLATE_SPEC.md` | 槽位 + 两段提示词 + 模型绑定的定稿 | 补 H2 语法三份定义、M16 override 隐式顺序 |
| `REVIEW_2026-11-19_*` | 视频链、用户权限的具体评审 | 本次是全系统横切，不是单链路复审 |

---

## 7. 明确判定为合理、不要顺手改的地方

- **权限判定单点且可追溯**：`core/authz.py:1-10,73-117` 是唯一求值入口；`identity/service.py:1717-1785` 的 `effective_permissions` 给出 `sources` / `deniedOff` / `gatedOff`，能回答"谁有这个权限、为什么"。
- **owner 不可变**：全仓无 `owner_id` 更新路径，从根上消除"换 owner 后共享归属"的歧义；移出小组回退个人库的判据在 `library/access.py:73-102` 双分支同源。
- **无 FK 的取舍有书面论证**：`library/models.py:66-77`、`template_pack/models.py:23` 解释了切换期与方言一致性，不要为补约束破坏它。
- **后端下发 effective、前端只做成员判断**：`core/pages.py:227-250` 与 `authz.py:73-117` 同源，页面/接口两层权限没有第二份真相。
- **契约与路由的生成物守卫**：`endpoints.test.ts:16-21`、`test_openapi_contract.py:60-63` 的字节比对；`app/registry/create-registry.ts:99-111` 把"导航指向真实路由"做成注册期断言。
- **实体类型从生成物派生**：`entities/user/model/types.ts:13-27` + `contract.test.ts:44-50` 有"防退回手抄"的源码守卫。
- **源码扫描型守卫本身**：`shared/ui/notice-usage.test.ts:8-16` 用扫描守配色漂移，抽象层级正确；本次多个建议沿用同一手法。

---

## 8. 附：本次未做的事

- 未修改任何代码、未提交、未推送。
- 未对每条发现做完整调用链追踪；H1–H13 的关键证据已回到源码核对，M/L 级条目以扫描结果为准，落地前请按行号复核。
- 未做前端真机 / 后端真机验证；本报告的判据是源码结构与已有守卫。
