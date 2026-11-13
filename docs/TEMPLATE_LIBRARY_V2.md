# 模板库 v2 · 设计与契约

> 2026-11-13 用户拍板：**不再优化现有模板库，直接重做 v2**。
> 本文是这条线上唯一的活文档，替代已删除的 `TEMPLATE_UNIFICATION_PLAN.md` 及一整批
> 迁移期计划/评审文档。只写「现在是什么、要做什么、契约是什么」；过程看 git 历史，
> 不在这里记流水账。

## 1. 决策与范围

- 实体只有两个：**Template**（只读，来自封装模板模块）与 **ReplacementConfig**（用户拥有）。
- Template **不自建**：来源是 `modules/template_pack`（封装模板功能模块），它已完整存在。
- **只留一个接口给任务中心**：模板库提交生成任务，不自己跑流水线。
- 老 `modules/template` 的概念一律不带进 v2：视频模板、蓝本、素材包版本、替换运行档案。

一句话：模板库从「生成工作台」退回到「模板目录 + 配置管理器」。

## 2. 现状（2026-11-13 核对过代码）

| 对象 | 位置 | 状态 |
|---|---|---|
| 封装模板模块 | `api/src/modules/template_pack/`（8 个端点）、`studio/src/pages/template-pack/` | 已完整：`prompt` + `slots` + 发布/停用 + 候选池 + 复核 |
| 对外窄端口 | `api/src/core/ports/template_pack.py` | 已存在，`grep` 确认**零消费方** —— 正是留给任务中心的位置 |
| 老模板库 | `api/src/modules/template/`（video_templates / packs / replacement_runs）、`studio/src/pages/templates/` | 与封装模块**不互相 import、不共享表/权限点**，两套页面都叫「模板库」 |
| 任务中心 | `api/src/modules/task/`、`studio/src/pages/task-center/` | `GET /tasks/feed` 目前靠 `core/feed.py::replacement_feed` 拉老替换档案 |
| 提示词翻译 | `api/src/core/prompt_refs.translate_prompt` | 全仓唯一实现，`{{Mixed N}}` → `视频1/图2/音频1` |

`TemplatePackPort` 未注册时返回 `None`（空模板库是合法状态），与「生成端口未注册即 503」刻意不同。

## 3. 参考图说的封装方式

截图是封装编辑器：`@` 引用浮层（图片 / 视频 / 音频 / 文本 / 资产）、已引用条目、
带编号的参考图、`定量（强制固定，严禁改动）` 提示词正文、底部 `Wan 3.0 · 9:16 · 720P · 8s · 1个`。

| 图上元素 | 现有落点 | 缺口 |
|---|---|---|
| 固定提示词正文 | `TemplatePackDraftIn.prompt`（`VerbatimText` ≤20000，含 `{{Mixed N}}`）+ `prompt_sha256`，仅未发布可 `PATCH` | 现在是**整条固定**；图上是**分段**（定量段固定、其余可填）。分段要新增编译能力 |
| `@` 引用与编号参考图 | `slots[].position`（position=1 固定是参考视频）；`MediaKind = video/image/audio` | 不含 `text`（`{{Text N}}` 走 `core/library_text.py`），也没有「资产（封装包）」这一类 |
| 已引用条目按标签挑 | `filtered` 槽位的 `filter_kind` + `filter_tag_ids`（一级标签，OR 展开到二级） | 候选池**服务端算**，前端不能自己筛 |
| 模型 / 分辨率 / 时长 / 数量 | 契约里只有 `aspect_ratio` | 其余靠提交时 `GET /models` 的 `paramSchema`，没写进模板 |

## 4. 五向 I/O 契约

| 方向 | 内容 |
|---|---|
| **① 封装模块 ← 作者 A** | 名称/说明/封面；参考视频（资产库 v2 原片 id，`POST /library/source-videos`）；提示词原文；槽位表（每个 `{{Mixed N}}` 声明 `fixed` 钉死 assetId，或 `filtered` 声明 kind+标签+required+label）；生成参数（待定是否纳入）；动作：存草稿 → 发布（发布时校验原片仍在、槽位非空） |
| **② 封装模块 → 模板库** | **只出已发布的只读 Template 快照**：id / templateKey / name / description / coverUrl / prompt / promptSha256 / 原片 id 与时长 / 画幅 / slots[] / publishedAt / ownerId。草稿与停用不外露；只有 `disable` 没有 `delete`。附带 `candidates(packId, token)` 与 `verify_picks(packId, picks)` —— **这是模板的业务约束，提交前必须服务端复核**，不是 UI 便利 |
| **③ 模板库 ← 调用者 B** | `templateId`；`picks = {token: assetId}`（只填 `filtered` 槽位，`fixed` 不可改）；可选生成参数覆盖；配置可命名保存。保存时**冻结模板快照**（promptSha256 + 槽位定义 + 素材 id/名称/storage_key），模板改版不影响已存配置 |
| **④ 模板库 → 用户** | 模板卡片列表 / 详情（含每个槽位的候选池：assetId、name、kind、coverUrl、尺寸）；把 picks 注入后的**最终提示词预览**；「我的替换配置」列表与回读；提交后的 taskId 与状态 |
| **⑤ 模板库 → 其他模块** | **唯一显式接口给任务中心**：`templateId + configId/picks → taskId`，feed 里带模板来源。当前 `CreateTaskIn` 缺 `templateId` 与 `picks`，要补。另向资产库**声明删除保护**（不是接口）：钉死的素材被彻底删除前要拦。export / image / shot 一律不接 |

## 5. 代码层：复用、删除、新增

**复用**：`modules/template_pack` 整个模块、`core/ports/template_pack.py`、`core/prompt_refs`、
`core/library_text.py`、资产库 v2、模型目录（`paramSchema`）、任务中心 `POST /tasks`。

**删除**（重做 v2 不带走）：`video_templates`（catalog.json、pinned 蓝本、attach-source、
media_order、referenceBindings / fixedReferences）、`modules/template/pack_*` 草稿与不可变版本、
`replacement_runs` 的 prepare / execute / review / output、`libraryPackId` / `extraPackIds` /
`referenceIds` 等别名、`/templates` 四步工作台、`feed_provider` 老路径。
删之前要处理 `modules/task` 的 feed、`export` 导出源、权限码与一批既有测试。

**新增**：v2 模块（Template 只读投影 + `ReplacementConfig` 表 + 一个提交口）、任务中心的
`templateId` / `picks` 入参、封装模板侧的 `library_reference_sources` 声明。

## 6. 两个现在就有的洞

- `modules/template_pack/__init__.py` **没有登记 `library_reference_sources`**：`fixed` 槽位
  与参考视频钉死的 assetId 被资产库「彻底删除」时无人拦，模板卡片会裂图且不可逆。
- 模板库走端口读（`None` = 空库）、任务中心走路由写（404 = 不存在），两边对
  「模板存不存在」要保持同一口径，否则会出现「列表里有、提交时找不到」。

## 7. 待拍板（确认后才动代码）

1. `ReplacementConfig` 落库持久化（「我的配置」可复用，多一张表），还是一次性提交？
2. 模板库**只提交不生成**（推荐），还是自己跑流水线？
3. `modules/template` 整块删，还是新建 `template_v2` 并存、老的只下线？
4. 模型 / 分辨率 / 时长 / 数量要不要写进模板定义？

## 8. 必须继承的不变量

- **冻结语义**：一次显式选定 → 结果落库冻结；禁止「模板只存标签、运行时按当前标签集合
  自动取素材」，那会让历史任务随打/解标签漂移。
- **服务端复核**：筛选条件是业务契约，前端过滤可以被接口绕过；`verify_picks` 必须在
  提交前调，不满足直接 400。
- **模块之间不互相 import**：跨模块取数走 `core/ports/*` 注入口；`api/` 层不 import 业务模块。
- **契约四份**：改接口后 `app.exe export-openapi` 重新生成 `openapi.json` / `API.md` /
  `schema.ts` / `permissions.ts`，四份一起提交，只重新生成不手工解冲突。
- **删除保护**：任何写入资产库 id 的新表都要登记 `library_reference_sources`（AST 守卫会扫）。
- 测试范围按 `scripts/select_tests.py --print` 定级，不因为「改动大」就默认跑全量。
