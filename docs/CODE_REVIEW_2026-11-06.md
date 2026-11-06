# 代码评审 · 2026-11-06（素材库 T01–T07 / 替换任务打通 / 人工验收）

> 被评审范围：当前工作区未提交改动（60 个已跟踪文件 + 5286 行改动，另 65 个新增文件）
> 依据：[`CODE_STYLE.md`](./CODE_STYLE.md) ｜ [`REVIEW_CHECKLIST.md`](./REVIEW_CHECKLIST.md) ｜ [`ASSETS_REQUIREMENTS.md`](./ASSETS_REQUIREMENTS.md) ｜ [`ASSETS_INTEGRATION_PLAN.md`](./ASSETS_INTEGRATION_PLAN.md) ｜ [`REPLACEMENT_TEMPLATES.md`](./REPLACEMENT_TEMPLATES.md) ｜ [`TASKS_INTEGRATION.md`](./TASKS_INTEGRATION.md)

---

## 0. 结论

**不阻断合并，但有 9 项建议在合并前处理**，主要集中在三处：任务聚合接口被当成"轻量统计"用在每个页面、模板页原片选择存在两份真相、仓库卫生与提交粒度。

功能与文档的一致性总体良好：单视频选择、素材包主图绑定与版本强制、撤回/停用在四个边界复查、评价的独立原子文件与版本冲突、回收站不取消在途生成，都能在代码里找到对应实现，且都有失败路径测试。

---

## 1. 门禁实测（2026-11-06 15:2x）

| 检查 | 结果 | 备注 |
|---|---|---|
| `ruff check src tests scripts` | ✅ All checks passed | 仓库根目录 `api/` 下执行 |
| `mypy --platform win32 src` | ✅ 142 files, no issues | 与 CI 参数一致 |
| `pytest -q` | ✅ 280 passed | 默认临时目录被本机权限拦截（`PermissionError: AppData\Local\Temp\pytest-of-PC`），改用 `--basetemp=var/test-review-1106` 后全绿；属环境问题，非代码问题 |
| 前端 `npm run verify` | ✅ typecheck + lint(`--max-warnings 0`) + 50 files/369 tests + build | 与 `ASSETS_INTEGRATION_PLAN.md` 记录一致 |
| 权限目录前后端一致性 | ✅ 各 28 个权限点，无差异 | 后端 `discover_modules()` 实测 28；前端 `permission-catalog.ts` 28 |
| 契约三份生成物 | ✅ 随 pytest 通过（`test_openapi_contract.py`） | `API.md`/`openapi.json`/`schema.ts` 已同步 |

> 说明：`REPLACEMENT_TEMPLATES.md` 里"全量 1 项失败（权限目录不一致）"的记录已过期，实测已修（见 D2）。

---

## 2. 合并前建议处理

### 🟡 F1 任务聚合被当成"统计接口"用在每个页面

`studio/src/modules/task-center/RunningTaskBadge.tsx:8` 用 `useTaskFeed({scope:'mine', pageSize:1})` 取角标；
`studio/src/entities/task/api/task.queries.ts:33` 是**无条件** `refetchInterval: 5_000`。

**为什么**：`/tasks/feed` 不是轻量接口。它要 `list_runs()` 扫全量归档目录，并为每条替换档案组装 `input_summary`
（`api/src/modules/template/replacement_service.py:76-79` → `:66`），单条记录约 4 次文件读
（`run.json` / `configuration.json` / `reference-pack.json` / `input-map.json`，另加 review、management）。
100 条档案 ≈ 每次轮询 600 次文件读；而角标在每个页面的侧栏都挂载，后台标签页也不停。

**建议**（择一）：
1. 角标走轻量口径：`/tasks/feed?source=video&pageSize=1` 复用现有 stats，或恢复 `/tasks/stats` 并把替换计数并入；
2. `refetchInterval` 加条件：`data.stats.running > 0 && document.visibilityState === 'visible'` 才 5s，否则 `false`。

**注意**：`docs/TASKS_INTEGRATION.md:16` 把"每 5 秒同步、空闲时也能发现新任务"写成了需求。若坚持，请把"后台标签页 / 无进行中任务"排除，并在文档里说明取舍；否则与 `REVIEW_CHECKLIST` 反模式 #7、`FE-08` 直接冲突。

**验收**：停在"设置"页 10 分钟，DevTools 里 `/tasks/feed` 请求数应为 0；在任务中心有进行中任务时约每 5s 一次。

### 🟡 F2 列表接口按详情口径组装

`ReplacementRunOut` 带 `input_summary`（`replacement_schema.py:91`），而 `list_runs`（`replacement_service.py:76-79`）复用同一个 `run_out`。
目录页只需要"原片名"这一项，却为每条记录读 4 个文件（PERF-01 的 IO 版本）。

**建议**：列表只返回标题所需最小字段（如 `sourceName`），`input_summary` 仅 `detail` 返回；或在 prepare 时把摘要写进 `run.json` 一次成型。

**验收**：`GET /templates/replacement-catalog` 在 200 条档案下 P95 < 300ms（本地归档盘）。

### 🟡 F3 原片选择有两份真相

`studio/src/features/replacement-templates/useReplacementWorkbench.ts:18` 用 `useState(params.get('sourceId'))`，
`:48` 又用 `params.get('sourceId')` 做 `useArchiveSelection`，但 `setSourceId` **从不写回 URL**
（`ReplacementSourceLibrary.tsx:24` 的"清空选择"、`:28` 的点击切换都只改 state）。

**为什么**：清空后 URL 里仍是旧 `sourceId`，刷新会把旧选择带回来；"带入的原片不可用"提示（`linkedSource.error`）和当前选择可能不是同一条；
与 `docs/REPLACEMENT_TEMPLATES.md`「再次点击当前视频或移除/清空可取消选择」不符（FE-06/FE-07）。

**建议**：单一来源——选择写进 URL（`setParams` 克隆后 set/delete），组件只读 `params`；深链带入只消费一次。

**验收**：选 A → 选 B → 点"清空选择" → 刷新页面，应无任何选中项，且 `linkedSource` 不再发请求。

### 🟡 F4 同一条任务在两个页面显示不同名字

后端聚合标题：`api/src/modules/task/feed_service.py:61` = `原片 · 类型 · 目标`（无 `#短码`、不截断）。
前端命名规则：`studio/src/entities/template/model/replacementTaskIdentity.ts:11,18` = `原片前18字… · 类型 · 目标 · #1C916E7A`。

**为什么**：文档明确"列表、核对弹窗、档案详情和删除确认共用同一命名规则"；任务中心是第四条"列表"，却自成一套，长原片名还会撑破卡片。

**建议**：规则收敛一处——后端返回 `displayTitle`/`shortCode`，或前端在 TaskCenter 复用 `replacementTaskName`。

**验收**：同一条 run 在 `/templates` 与 `/tasks` 的标题逐字相同（含 `#` 短码）。

### 🟡 F5 归档列表的范围/状态用裸字符串

`api/src/modules/asset/archive.py:15-22`：`category: str`、`scope: str`、`state: str`，白名单校验在
`archive_service.py:60-62` 用元组兜。同模块其它接口都用枚举（`TaskScope`、`TaskStatusFilter`）。

**为什么**：OpenAPI 生成成 `string`，前端只能手写 `ArchiveSearch` 联合类型，两端口径靠人记；非法值变成 400 而不是契约层的 422（SEC-01）。

**建议**：`ArchiveScope = Literal["mine","public","trash"]`、`ArchiveState = Literal["all","private","shared"]`、`ArchiveCategory` 复用既有枚举。

**验收**：`openapi.json` 里这三个参数是 `enum`；传 `scope=xxx` 得 422。

### 🟡 F6 缓存键与失效范围没有统一工厂

- `replacement.queries.ts:22` 写死 `['tasks','feed']`
- `pack.queries.ts:6,13` 写死 `['packs', owner, ...]`
- `archive.queries.ts:26` 写死 `['archive-media', owner]`

**为什么**：`taskKeys` 工厂已存在（`task.queries.ts:18`），新增的 `feed` 却没进工厂；改前缀漏一处就是"数据变了界面不动"（FE-05）。

**建议**：补 `taskKeys.feeds()` / `taskKeys.feed(owner, params)`、`archiveKeys.mediaAll(owner)`、`packKeys.*`，调用点全部走工厂。

### 🟡 F7 `api/var/` 会被整体 `git add`

`.gitignore` 只忽略了 `api/var/*.db`、`api/var/storage/`、`*.tmp.json`。但 `git status` 里是 `?? api/var/`——
`assets-role-migration-2026-11-06.json` 与 10+ 个 `test-*` 目录（含测试媒体）都在待提交集合里，而文档明确说迁移快照"不提交仓库"。
另外 `ReplacementReferences.tsx` 处于 `AD` 状态（索引里有、工作区已删），`ReplacementFeedback.tsx` 是 `A`，索引与工作区不同步。

**建议**：`api/var/` 整体忽略（需要时放 `.gitkeep`）；提交前按 CR-02 过一遍 `git status`，确认无调试产物、无本地运行数据。

**验收**：`git status --porcelain | grep var/` 无输出。

### 🟡 F8 一次改动 5286 行 / 126 个文件 / 5 个主题

素材（T01–T05）、任务聚合、人工验收、回收站、预览画质全在一个工作区，没有提交可切分。规范要求 PR ≤ 400 行（CR-03），且"一次提交只做一件事"。

**建议**：按主题拆提交，每个自带测试：
`feat(asset): 个人/公共素材与分享回收站` → `feat(template): 封装发布与版本调用` → `feat(task): 统一任务聚合` →
`feat(template): 替换任务回收站与人工验收` → `perf(media): 高清派生预览` → `chore(scripts): 角色迁移`。
好处是出问题时能按主题回滚，评审也能分批进行。

### 🟡 F9 新增 `modules → api` 反向边，且无人守

`modules/template/__init__.py:4` 与 `modules/task/feed_service.py:8` import `api.replacement_feed`；
README 写的是"依赖单向 `api（装配）→ modules → …`"。现有 `tests/test_architecture.py` 只拦"底层→modules""modules↔modules"，这条新边不在守卫范围。

**建议**：把注册表放进已存在的 `core/feed.py`（`register_reader` / `list_runs`），`modules` 只依赖 `core`；
或在 `test_architecture.py` 增加一条"modules 不得 import `api.*`（`api.module`、`api.deps` 白名单除外）"，避免以后随手引用。

---

## 3. 建议（💭 nit，不阻塞）

| # | 位置 | 说明 |
|---|---|---|
| N1 | `modules/identity/asset_roles.py:62-67` | 函数体内 `import`（反模式 #4），签名用 `dict[str, Any]`；建议上提 + 定义 `MigrationReport` 类型 |
| N2 | `pack_service.py:133-160` | `available()`/`search()` 对每个包每次请求都做全量哈希（`verify`→`checksum`）；目录页随包数×图数线性变慢。建议缓存校验（mtime+size）或只在发布/准备时校验 |
| N3 | `pack_service.py:187-196` | 包内预览在缺 `preview_path` 时直接返回原件；文档说"公共预览不开放原件下载"，图片虽小仍建议强制生成预览 |
| N4 | `archive_service.py:37-47` | 非本人输出清了 `path`/`sha256`，但 `previewPath` 仍暴露内部存储键；公共接口建议一并置空 |
| N5 | `feed_service.py:50-56` | `KIND_LABELS[run.kind]` 下标取值，未知 kind 会 KeyError→500；改 `.get(...)` |
| N6 | `replacement_review.py:53-59` | `history` 每次保存追加、无上限；评价改 20 次就是 20 份。建议保留最近 N 条或分文件 |
| N7 | `ReplacementTaskHistory.tsx:17-19` | 历史区搜索/状态筛选在本地 state（同页其它筛选都在 URL），刷新丢失；且与顶部 `q` 两个搜索框语义不同 |
| N8 | `archive.types.ts:17` | 前端硬编码 `v=hd-v2`，与后端 `media_probe.PREVIEW_VERSION` 两处定义，升版时前端缓存不失效 |
| N9 | `archive.queries.ts:48` + `media_probe.py:153-166` | `staleTime: 0` + 预览最高 1920p/crf20：hover 每次挂载重拉高清流。建议 hover 用 720p 派生或提高 staleTime |
| N10 | `useReplacementWorkbench.ts:16,34` | 直接改 `previous`（URLSearchParams）再返回；同文件其它 setter 都 clone，保持一致 |
| N11 | `task.queries.ts:65` | `useTaskStats` 生产已无调用（仅 `Sidebar.test.tsx:16` mock 它），死代码；该 mock 也已失效（角标改用 feed） |
| N12 | `replacement_management.py:23-32` | `set_deleted` 无日志；回收站是业务动作，建议 `logger.info` 带 run_id/owner（BE-22） |

---

## 4. 文档与实现需要对齐（3 处）

- **D1** `docs/REPLACEMENT_TEMPLATES.md` 写"已移除批量提示与逐条批量进度区""页面状态只保存单个 `sourceId`"，但页面仍渲染
  `ReplacementBatch`（批量勾选 + 逐条进度 + 逐条核对），而 `ASSETS_INTEGRATION_PLAN.md:215` 又明确"保留单原片入口，新增可展开的批量准备"。
  两份文档互相矛盾，请留一份为准（代码现状更接近后者）。
- **D2** 同一文档的"全量 362 项通过 / 1 项失败（前后端权限目录不一致）"已过期：实测前后端 28/28 一致、前端 369 项全绿。
- **D3** `REVIEW_CHECKLIST.md` 附录 A 的基线（pytest 87 passed、前端 46）早于当前代码（280 / 369），建议刷新——否则"这次有没有让仓库变差"没有参照。

---

## 5. 做得好的地方（建议保留这些做法）

1. **四个边界收敛到同一把租约**：撤回/停用、发布、准备快照、最终提交都过 `catalog_lease`
   （`infra/media_catalog.py:14`），执行器在 `supervise` 里落 `call-authorization.json` 后**立刻释放**租约再启上游
   （`libtv_process.py:71-76`）——正好对上清单里"提交成功+入队失败""读改写丢更新"两个反模式，也没有长持素材锁。
2. **幂等有三层**：`request_id` 复用、`expected_version` 校验、"同状态重试只返回事实、不改写时间与版本"（`archive_service.py:120-131`）。
3. **评价的原子性**：结论与历史放进同一个文件一次写入（`replacement_review.py:52-60`），避开"两份文件一成功一失败"。
4. **权限迁移不静默**：只读预览 → 差异报告 → 版本比对 → 独占创建快照 → 可回滚（`asset_roles.py:33-79`），符合 10.3/10.5。
5. **测试选在失败路径上**：越权、归属、停用、版本冲突、离线归档、"写失败不报假成功"都有用例
   （`test_replacement_review.py`、`test_assets_integration.py`、`test_task_feed.py`）。

---

## 6. 复验清单

- [ ] `/tasks/feed` 在无进行中任务、页面不可见时不再轮询
- [ ] 同一条 run 在 `/templates` 与 `/tasks` 标题一致
- [ ] 清空原片选择后刷新，选择与深链提示都不复现
- [ ] `git status` 中不再出现 `api/var/`；索引与工作区一致（无 `AD`）
- [ ] `openapi.json` 中素材列表的 `scope`/`state` 为 enum
- [ ] 提交按主题拆分后，单个提交 diff ≤ 400 行（生成物与测试可放宽）

---

## 7. 处理结果（2026-11-06 同日修复）

### 7.1 合并前建议项

| # | 处理 | 落点 |
|---|---|---|
| F1 | 角标改走统计口径：`/tasks/feed?statsOnly=true` 只算 stats、不组装条目；`useTaskFeed` 改为「有进行中任务 + 页面可见」才 5s 轮询 | `modules/task/{router,feed_service}.py`、`RunningTaskBadge.tsx`、`task.queries.ts` |
| F2 | 输入摘要一次成型：prepare 时写进 `run.json`，列表与任务中心直接复用；`run_out` / `input_summary` 支持传入已读 `meta`，`list_runs` 不再每条读两次 `run.json` | `replacement_repository.py`、`replacement_service.py`、`replacement_inputs.py` |
| F3 | 原片选择单一来源 = URL：选择/移除/深链都改同一个 `sourceId` 参数，`linkedSource` 只读 URL | `useReplacementWorkbench.ts` |
| F4 | 命名规则收敛到后端 `replacement_titles.task_name`：`sourceName · 类型 · 目标 · #短码`，原片名超 18 字截断；`ReplacementRunOut.displayTitle` 与任务中心 `title` 同源，前端只在缺字段时兜底 | `replacement_titles.py`、`replacement_schema.py`、`replacementTaskIdentity.ts` |
| F5 | `category` / `scope` / `state` 改 Literal，进契约；非法值由框架回 422 | `archive_schema.py`、`archive.py`、`archive_service.py` |
| F6 | 补齐 `taskKeys.feeds()/feed()`、`packKeys.*`、`archiveKeys.mediaAll()`，调用点不再写死数组 | `task.queries.ts`、`pack.queries.ts`、`archive.queries.ts`、`replacement.queries.ts` |
| F7 | `api/var/*` 整体忽略（保留 `.gitkeep`）；索引里的 `AD` 文件已 `git rm --cached` | `.gitignore` |
| F8 | **未处理**：5286 行拆成 5–6 个提交需要人工决定顺序与切分边界，工具不该替人重写历史 | — |
| F9 | 注册表移到 `core/feed.py`，`api/replacement_feed.py` 删除；新增架构守卫「模块只能引用 `api` 的四个装配点」 | `core/feed.py`、`test_architecture.py`、`api/README.md` |

顺带修掉的 nit：N1（`asset_roles` 函数体内 import 上提 + `MigrationReport` 类型）、N2（包内校验按 mtime+size 缓存摘要）、
N3（公共预览缺图时现生成，不再回原件）、N5（`KIND_LABELS` 下标 → 直接取后端标题）、N6（评价历史保留最近 20 条）、
N7（历史区搜索/状态/回收站筛选进 URL）、N9（预览 blob `staleTime` 0 → 5 分钟）、N10（URL 写入统一 clone）、
N11（删除死代码 `useTaskStats`）、N12（回收站动作补日志）。
N4 经复核不成立：`ArchivedAsset` 不含 `preview_path`，非本人素材只回 `/preview` 地址。
N8 保留双份定义，但把「必须与 `media_probe.PREVIEW_VERSION` 同名」写进了注释。

### 7.2 过程中发现的新问题（已修）

同一个事件里连续调用两次 `setSearchParams` 会基于同一份旧快照计算，**后一次会把前一次改动整条覆盖**。
F3/N7 把筛选搬进 URL 后立刻暴露：上传成功时的「选中新原片 + 切回我的素材」、回收站页签的「切换 + 清空状态」
都会丢掉一半更新。改为所有 URL 写入走同一个 `patchParams`，一次写全。

### 7.3 文档对齐

- `REPLACEMENT_TEMPLATES.md`：删掉「已移除批量区」的过期描述（代码仍在渲染可展开的批量入口），补「`sourceId` 只有 URL 一个来源」，测试数字刷新。
- `TASKS_INTEGRATION.md`：轮询规则改为「仅在存在进行中任务时 5 秒、页面隐藏即停、空闲不发请求」，补 `statsOnly` 与统一标题规则。
- `REVIEW_CHECKLIST.md` 附录 A：基线刷新为当前实测。

### 7.4 复验（本轮实跑）

| 检查 | 结果 |
|---|---|
| `ruff check src tests scripts` | ✅ All checks passed |
| `mypy --platform win32 src` | ✅ 142 files, no issues |
| `pytest -q --basetemp=...` | ✅ 287 passed |
| 前端 `npm run verify` | ✅ typecheck + lint 0 告警 + 50 文件 / 371 用例 + build |
| `openapi.json` 素材列表参数 | ✅ `category` / `scope` / `state` 均为单 enum |
| `git status --porcelain \| grep var/` | ✅ 仅 `api/var/.gitkeep` |

新增测试：`test_replacement_titles.py`（3 条命名规则）、`test_task_feed.py` 增 2 条（`statsOnly` 口径、标题含短码）、
`test_assets_integration.py` 增 1 条（非法筛选 422）、`ReplacementTemplates.test.tsx` 增 2 条（清空选择不写回 URL、深链成唯一选择）。

---

## 8. 第二轮复检（同日 15:49 起）

修复完成后重新逐行核对改动，重点是「修复本身有没有引入新口径」。结论：F1/F2/F3/F5/F7/F9 的实现成立，
但复检发现 4 类问题，已全部处理；另有 2 条 nit 留给人决定。

### 8.1 已修：统计接口的 `total` 与列表不同口径

`statsOnly` 直接返回 `total=stats.all`，而 `stats` 按契约是**筛选前**统计（不受状态页签影响）。
列表的 `total` 却是**按状态页签过滤后**的条数，于是 `/tasks/feed?status=failed&statsOnly=true`
会给出「全部任务数」，与同一参数下的完整列表对不上。角标目前不传 `status`，所以线上不发作，
但这是个只要有人复用就会中的契约陷阱。

改法：`status=all` 时直接复用 `stats.all`（零额外查询），其它页签补一次只 COUNT 的
`repo.count_candidates`，不拉行。新增 6 条参数化用例逐一比对 `all/queued/running/completed/failed/attention`
两种模式的 `total`。

### 8.2 已修：任务中心里还留着第二套标题规则

`replacement_item` 仍是 `run.title or f"{run.source_name} · {run.kind} · {run.target}"`：
兜底分支用的是英文 kind（`character` 而不是「人物替换」）、没有 `#短码`、也不截断。
`feed_records` 现在总会写 `title`，所以这条分支实际不可达 —— 但不可达的第二套规则正是下次被复制粘贴的源头。

改法：`ReplacementFeedRecord.title` 去掉默认值改成必填，任务中心只读 `run.title`；提供方漏字段会在构造时报错，
而不是悄悄退化成另一种命名。

### 8.3 已修：并发改动带进来的 7 个 lint 错误 + 4 条测试回归

复检期间工作区里新增了「封装参考图用途」相关改动（`core/pack_roles.py`、`core/media.py` 的
`MediaPack` 校验、`pack_service` 的 `gaps/_check_roles`、前端 `packRoles.ts` / `PackEditor`）。
这些文件当时过不了门禁：

| 位置 | 问题 |
|---|---|
| `core/media.py:102`、`replacement_schema.py:120` | `UP037`：已开 `from __future__ import annotations`，返回注解不必加引号 |
| `core/pack_roles.py:54-56` | 3 处 `E501`：产品用途说明单行超 100 字符 |
| `pack_service.py:128` | `E501`：缺项列表推导单行超长 |
| `tests/test_pack_roles.py:3` | `I001`：import 未排序 |

同时 4 条集成测试按**旧契约**发布产品包（不声明用途与可替换范围），被新的 `gaps` 与 `_check_scope` 拒绝：
`test_publish_idempotent_version_and_disable`、`test_specialist_publication_and_preview_are_cross_account`、
`test_legacy_pack_is_not_automatically_public`、`test_shared_source_and_published_target_create_callers_snapshot`。
测试已补上 `bindings` / `supportedScopes` / `replacementScope`，没有放宽产品校验。

顺手去重：`pack_service.gaps` 里「缺哪个用途」的推导与 `core/pack_roles.missing_role_labels` 是同一份逻辑，
改为调用后者（`PACK_ROLES` 与 `ROLE_USAGE` 的顺序仍是唯一事实来源）。

### 8.4 已补：注释声称存在的同步测试其实不存在

`studio/src/entities/template/model/packRoles.ts` 开头写「两边不一致时 `api/tests/test_pack_roles.py` 会直接失败」，
但该文件里没有任何读取前端词表的断言 —— 又是「文档承诺了代码没做的事」。新增
`api/tests/test_pack_roles_sync.py`：直接读 TS 源，逐项比对 `PACK_ROLES`、`BASE_ROLES`、`PACK_SCOPES` 三张表。
词表漂移从「评审时肉眼核对」变成 CI 拦截。

### 8.5 未处理（需要产品或人工决定）

- `core/pack_roles.py` 的 `role_label`、`scope_label`、`ordered_roles` 目前无人调用（`missing_role_labels` 已接入）。
  保留是为后续生成链路预留词汇入口，但按仓库「不留死代码」的习惯，建议下轮删除或接入。
- `_check_roles` 允许两张参考图选同一个用途（例如两张都 `front`），发布后提示词会出现两段相同的用途说明。
  要不要在保存时拒绝重复用途，属于产品口径，未擅自改。

### 8.6 仓库卫生（仍未处理）

暂存区里 15 个文件停在旧版本（`git status` 显示 `AM` / `MM`）：`useReplacementWorkbench.ts`、
`ReplacementTemplates.tsx`、`ReplacementConfig.tsx` 等。此时直接 `git commit` 会提交**不含本轮修复**的半成品。
提交前需要 `git add -A`，或按主题重新分批暂存（与 §7.1 F8 是同一件事）。

### 8.7 复验（本轮实跑）

| 检查 | 结果 |
|---|---|
| `ruff check src tests scripts` | ✅ All checks passed |
| `mypy --platform win32 src` | ✅ 143 files, no issues |
| `pytest -q --basetemp=...` | ✅ 305 passed |
| 前端 `npm run verify` | ✅ typecheck + lint 0 告警 + 50 文件 / 371 用例 + build |
| `app export-openapi --check` | ✅ json / md / ts 三份产物均无变化 |
| 前后端用途词表 | ✅ 新增用例逐项比对通过 |

> 复检过程中有一次全量前端测试出现 2 条失败，单独复跑该文件 31/31 通过 —— 当时 `PackEditor.tsx` /
> `PackManager.tsx` 正在被并发修改，`tsc` 读到了写了一半的文件。这类失败以「文件稳定后单跑 + 全量重跑」为准。

---

## 9. 第三轮审查（同日 16:13 起，针对 16:00 后的新增改动）

这一轮的范围是第二轮之后新落地的代码：封装用途/范围链路（`core/pack_roles.py`、`PackService` 的
`save/publish/public/available`、`_check_scope`、`_pack_usage`、提示词 `usage` 注入）与两个迁移脚本
（`sync_legacy_packs.py`、`import_legacy_replacement_assets.py`）。结论：这几块的职责划分和失败路径是对的，
但有 **2 个会让用户直接撞上的功能缺陷**，已修；另有 6 条留意见。

### 9.1 已修（P1）：同一份草稿重复提交会被判成「草稿已变化」

`PackService.save` 的幂等分支用 `content == old` 比较：`content` 来自 `payload.model_dump()`（嵌套是 dict），
`old` 来自 `getattr(previous, key)`（嵌套是 `PackBinding` 模型）。两者永远不等；`required_roles` 还会被模型
归一化成 `["front"]`，于是 `payload` 里的 `[]` 也对不上。

后果：超时重试、双击「保存草稿」时走到 `previous.revision == expected_revision + 1` 分支却比不相等，
落到下一条 `previous.revision != payload.expected_revision`，返回 **409「草稿已变化，请刷新后核对」**——
用户没改任何东西，却被告知草稿变了。原有测试 `test_edit_draft_uses_same_identity` 传的是当前 revision，
不进这个分支，所以一直没暴露。

改法：`_same_content()` 把入参先构造一遍 `MediaPack`（走同一套归一化），再和存储对象逐字段比，
并排除 `id/owner_id/version/revision/status/published_at`；`draft()` 会补的默认主图也在此对齐。
新增 `test_repeat_save_with_same_revision_is_idempotent`（连提两次同一 payload，必须返回同一 revision）。

落点：`modules/template/pack_service.py:40-53`、`tests/test_pack_roles.py:232`。

### 9.2 已修（P1）：产品替换范围的默认值不在词表里，缺省必然失败

`PrepareReplacement.replacement_scope` 的默认值是 `"人物上身穿着、手持展示产品、盒内可见产品、屏幕商品"`，
而 `PACK_SCOPES.screen` 的文案是「手机详情页产品图」。`_check_scope` 拿标签比对，所以：

- 任何**不带** `replacementScope` 的产品准备请求，都会被 `_check_scope` 判为越界，
  返回「替换范围超出素材包已审核的支持范围」——明明是没填，却提示成填错了；
- 默认值还隐含「四个位置全都要」，本身就是越过素材包审核范围的承诺。

前端一直显式发送，所以页面看不出问题，只有脚本、旧客户端和 `prepare()` 直调会中。

改法：默认值改为空串，产品必须显式给范围；`_check_scope` 拆成两种错误——未选择时回
「请选择产品替换位置（素材包已审核：…）」并列出可选值，越界时才回「超出…」。
新增 `test_product_prepare_requires_explicit_scope`；`tests/test_replacement_archive.py` 的产品参数补上显式范围；
`openapi.json` / `API.md` / `schema.ts` 已重新导出（`default` 从旧串变为 `""`）。

落点：`modules/template/replacement_schema.py:72`、`modules/template/replacement_service.py:230-240`。

### 9.3 文档与实测对齐

- `REPLACEMENT_TEMPLATES.md`：补「缺省范围不再由服务端补默认值」；把「底层准备接口保留历史手动参考图调用的兼容性」
  改成符合实际的说法（HTTP 强制 `packId`+`packVersion`，只有 service 层 `require_pack=False` 分支保留）；
  验证数字刷新为后端 310。
- `REVIEW_CHECKLIST.md` 附录 A：`pytest` 305→310、前端 50/371→51/375。

### 9.4 未改，留待判断

1. **`prepare` 不校验 `pack.available`**：目录里已经标 `available: false` 的包（内容不全/素材变更），
   绕过页面直接调接口仍能准备，最后卡在提示词编译或字节校验，错误发生在更难解释的位置。
   建议在 `_prepare` 解析出 `pack` 后加一条 `if pack.available is False: raise BadRequest("素材包内容不完整…")`。
2. **已顺手改掉：`prepare` 第三个位置参数的两层含义**。HTTP 层是 `can_read_assets`，模块层是 `require_pack`，
   两个 `True` 位置相邻，纯靠记忆区分。模块层已改成关键字专用（`*, require_pack`），
   `ReplacementService` 与测试调用点都改为 `require_pack=True`。
3. **`sync_legacy_packs` 用 `SystemExit` 表达数据不一致**（缺素材、哈希不符、索引冲突）。
   它被 `import_legacy_replacement_assets` 直接调用，`SystemExit` 会连复制流程一起打断且没有结构化错误，
   建议换成自定义异常并在 `main()` 统一捕获成退出码。
4. **同一份 `catalog/packs/*.json` 有两套投影**：`ReplacementRepository.packs/_public_pack`（旧，带 front 回退）
   与 `PackService.public`（新，目录实际使用）。生产路径只剩后者，前者只有测试引用。
   两套读法对「缺主图」的处理不同，长期会漂移，建议删旧或让测试改读新投影。
5. **编辑历史包会另存成新包**：`PackEditor.initialForm` 只认 `/^[a-f0-9]{32}$/`，带连字符的旧 uuid 会走 `uuid()` 分支，
   正好是注释里警告的「另存出一份新草稿」。实测 Z 盘现有 5 个包都是 32 位 hex，未发作，但迁移旧库前需要处理。
6. **已顺手改掉：`_pack_usage` 的 `zip` 改成 `strict=True`**。原本依赖 `ReplacementPack` 的校验器把
   `bindings` 对齐到 `reference_ids`，一旦有人构造未校验的包，`usage` 会静默挂到别的图上
   （提示词里说错图，不报错）。改为 strict 后错配当场失败；已校验的包不受影响。

### 9.5 已确认成立的部分

- 目录从 `repo.packs(owner)` 切到 `PackService().available()` 后，**真实数据没有退化**：
  Z 盘 5 个已发布包全部 `available=true`、`cover_url` 非空、用途与范围齐全（实跑核对）。
- 三份提示词模板现在都含 `{{reference_bindings}}`（此前只有产品/场景有），人物封装写的
  「同一人多视拼图」不会再整段丢失；`usage` 从 `PackService` → `_prepare_inputs` → `spec.references` → 编译器
  全链路带得下去，收尾标点也做了去重。
- 范围越界在服务端闭环（`_check_scope`），不只靠前端隐藏；`publish` 前 `gaps`+`verify` 双闸。

### 9.6 复验（本轮实跑）

| 检查 | 结果 |
|---|---|
| `ruff check src tests scripts` | ✅ All checks passed |
| `mypy --platform win32 src` | ✅ 143 files, no issues |
| `pytest -q --basetemp=...` | ✅ 310 passed |
| 前端 `npm run verify` | ✅ typecheck + lint 0 告警 + 51 文件 / 375 用例 + build |
| `export-openapi` | ✅ 已重导（`replacementScope` 默认值变更同步进 json/md/ts） |
