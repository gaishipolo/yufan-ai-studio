# 资产库 v2 · 并行开发与整体替换计划（决策总览）

> 状态：**设计已定稿**。本文记录 7 条拍板决策、现役基线、隔离方案与批次总览；
> 用户追加的功能描述已翻译成完整规格，见 **`ASSET_LIBRARY_V2_DESIGN.md`**（以那份为准）。
> 第 4 节保留为早期提案的过程记录，**不再作为功能清单**。
> **进度（2026-11-09）**：M0 骨架、M1 数据模型 / 上传 / 导入、M2 可见性 / 共享审批 /
> 删除矩阵 / 草稿与回收站、M3 封装 / 批量下载 / 生成入库已落地，现役素材库未动；
> 下一步是 M4 前端页面全套（列表 / 详情六区 / 上传 / 批量 / 回收站 / 选择器）。

## 0. 目标与边界

现役「素材资产库」正在被使用，**不允许原地改造**。做法是并行新建一个完全独立的资产库模块，
开发与验收期间两个库同时在线、互不干扰；验收通过后做一次性入口替换，确认无误再下线旧模块。

三条硬边界：

- **冻结**：并存期 `api/src/modules/asset/`、`studio/src/modules/assets/`、
  `studio/src/features/archive-library/`、`studio/src/entities/asset/` 一行不改。
- **隔离**：新模块有独立的表、接口、页面、权限码、任务名、CLI、投影前缀和注入口。
- **可回滚**：切换点收敛到前端注册表的 `load()` 一行，回滚就是改回去。

## 1. 已拍板决策（2026-11-09）

| 编号 | 议题 | 用户答复 | 落地含义 |
| --- | --- | --- | --- |
| D1 | 需求范围 | **重新定义功能清单** | 不以备份分支 `ASSET_LIBRARY_REDESIGN.md` / `ASSET_LIBRARY_PLAN.md` 为基线；blob/主体/配方/候选/槽位那一套概念**全部不继承**。功能清单由用户 6 条描述重新定义，见 `ASSET_LIBRARY_V2_DESIGN.md`。 |
| D2 | 「替换」的边界 | 默认：**一起换** | 页面入口、后端接口、数据一起切；旧接口随旧模块下线，不做长期双轨。 |
| D3 | 存量数据 | 默认：**一次性幂等导入** | 写一个可反复重跑、可 dry-run、可对账的导入命令；**不做双写**，避免两个真相源。 |
| D4 | 团队 / 可见域 | **不用备份的** | 不取回备份里的 `teams` / `subject` / `published_grants`；可见性直接接本轮上线的 **identity 三层授权**（团队 ∪ 小组 ∪ 个人 ∪ 角色，无 deny）。与 `USER_ACCESS_PLAN.md` D8 一致。 |
| D5 | 权限码 | 默认：**新前缀** | 不复用 `asset:*`，避免两个库的权限互相串；切换时统一改模板侧引用。 |
| D6 | 并存期可见范围 | 默认：**仅管理员 / 开发可见** | 新 URL 独立存在，不给全体用户多一个侧栏入口，避免误用。 |
| D7 | 生命周期与删除（2026-11-09 追加） | **草稿箱 / 回收站进一级类目**；AI 生成先进草稿箱，审核后进个人库；删除进回收站定期清理；普通用户只能删自己的；**已共享的不能删**；取消共享要审批；团队/小组删除权限收紧 | 底层用 `state ∈ draft\|active\|trash` 与 `visibility` 两个正交维度，不把草稿/回收站做成可见范围；「已共享即不可删」由不变式保证（`trash ⇒ personal`）；撤销共享复用申请单表加 `action=revoke`；新增 `library.trash.gc` 周期任务与 `library:delete:any` / `library:draft:review` 两个权限点。详见 DESIGN §0 / §3.3–3.5 / §3.4 权限矩阵。 |

## 2. 现役基线（冻结，只读参考）

### 2.1 后端 `modules/asset`

- 单表 `assets`（`api/src/modules/asset/models.py`），文件在对象存储，`storage_key` 是相对 key。
- `APIRouter(prefix="/assets")`，openapi 中 **11 条路径 / 12 个操作**：列表、上传、inbox 登记、
  文件/缩略图/兼容流预览、详情、受控公共预览、改名、分享、回收站、彻底删除。
- 6 个权限点：`asset:read / create / update / delete / share / reference:manage`。
- `ModuleSpec`：periodic `asset.gc` / `asset.mirror`；CLI `import-archive-index` /
  `migrate-blobs` / `prune-blobs` / `rebuild-mirrors`；`doctor`；`seed`（seed 钩子仍在，但**不执行**）。
- `install_lookup()` 往 `asset-lookup` 单例注入口注册，供模板库 prepare 读素材归属与分类。

### 2.2 前端

- `modules/assets/module.ts` → 路由 `assets`、导航「素材资产库」、卡片、`permissions: ['asset:read']`。
- `pages/assets/AssetsPage.tsx` → `features/archive-library/AssetWorkspace.tsx`。
- 四个 scope：公共素材 / 我的素材 / 参考素材维护 / 封装与发布；四个固定分类：
  原片 / 产品 / 人物 / 场景；三个视图：个人原片 / 回收站 / 历史素材。
- `entities/asset` 被模板库等多处复用，是替换时最大的迁移成本；**并存期只读复用、不改**。

### 2.3 共享底座（最危险，必须绕开）

| 资源 | 风险 | v2 对策 |
| --- | --- | --- |
| `catalog/assets/*.json` | 旧库写、模板 worker 读的派生投影；整文件覆盖无 CAS，两边都写会互相破坏 | 独立前缀，只读可重建，**不参与写入判定** |
| `blobs/` + `previews/` | 共享内容寻址空间，两边 GC 会把对方的「孤儿」删掉 | 独立前缀 + 独立 GC，作用范围按前缀严格隔离 |
| `asset-lookup` | 单例注入口，重复 `install()` 启动即报错 | 新名字 `library-lookup`，登记 `PORT_CATALOG`（三件套） |
| 前端模块级 mock | 注册顺序早于 `catalog.mock.ts`，正则过宽会抢走旧页请求 | v2 的 mock 用精确前缀，加隔离用例 |
| `ROUTES.md` / `MOCK_COVERAGE.md` / 四份 OpenAPI 生成物 | 新增模块会让它们过期 | 每次改接口/路由都重生成 |

### 2.4 现役已知缺口（提案的依据，不是照抄需求）

来自 `docs/ASSETS_REQUIREMENTS.md` §4 / §5 与 `docs/TODO.md` T22、T23：

- 两套真相：`assets` 表 + JSON 投影，靠 mirror 周期任务对账，存在漂移窗口。
- 检索弱：只有 category / scope / keyword / state，无排序、无时间/大小/分辨率筛选、无标签；
  4 个分类是写死的枚举；`pageSize` 上限 200 + offset 分页。
- 批量写弱：改名 / 分享 / 移入回收站都是单条 PUT。
- 上传无秒传（`_find_by_digest` 已能判重但没走「先传摘要」）、断线只能整段重传。
- 派生预览在请求内同步跑 ffmpeg，首个点开视频的用户要等转码，无预热与失败重试。
- 可见性只有 `shared` 布尔位（私人 / 公共二分），没有组织级共享粒度。
- 生成产物**不在资产库**（落在运行工作区），「素材库」不等于「所有用过的媒体文件」。

## 3. 命名与隔离方案（默认命名，可改）

| 项目 | 旧 | v2 默认 | 说明 |
| --- | --- | --- | --- |
| 后端模块 | `modules/asset` | `modules/library` | 模块名与目录全新 |
| 表 | `assets` | `library_assets` / `library_tags` / … | 统一前缀；不叫 `assets_v2`，避免工具模糊匹配 |
| 路由 | `/api/v1/assets` | `/api/v1/library` | 前端并存期用 `/library` |
| 权限 | `asset:*` | `library:*` | 独立 group「资产库」；写入 `ModuleSpec.permissions` |
| 周期任务 | `asset.gc` / `asset.mirror` | `library.gc` / `library.mirror` | 必须在 `ModuleSpec.periodic` 声明 |
| CLI | `import-archive-index` 等 | `import-library` / `verify-library` 等 | 走 `ModuleSpec.cli` |
| 投影 | `catalog/assets/*.json` | `catalog/library/*.json` | 独立前缀 |
| 前端模块 / 路由 | `assets` / `/assets` | `library-v2` / `/library` | 新导航项默认仅管理员可见 |
| 错误码 | status/slot/seq | 同号段规则，另择 slot | `code = status*100 + slot*10 + seq`，slot 1–9；落地前先核 `ModuleErrors` 占用表 |
| 生成物 | 四份 | 同源重生成 | `app export-openapi` + `npm run gen:mock-coverage` + `ROUTES.md` |

## 4. 早期功能清单提案（过程记录，已被用户 6 条描述取代）

> **以 `ASSET_LIBRARY_V2_DESIGN.md` 为准。** 2026-11-09 用户补充了 6 条功能描述
> （三级分类 / 上传与封装 / 共享审核 / 批量下载 / 统一选择器 / 生成结果入库与详情页），
> 明确「重新定义功能清单」「不用备份的」「细节由实现方完善」。本节保留当初的推导过程，
> 便于对照哪些缺口被新需求覆盖、哪些被重新定级；**不再作为待办或验收依据**。
>
> 原设计取向：一个统一的资产管理层，不引入生成工作流概念（配方 / 候选 / 主体 / 槽位）。
> 每条都对应现役缺口，P0 = 首版必须，P1 = 二期，不做 = 明确划出边界。

### 4.1 P0 · 首版必须

| 编号 | 能力 | 解决的现役问题 |
| --- | --- | --- |
| F0-1 | **单一资产模型**：原片 / 生成产物 / 模板参考素材统一为一条资产记录，用 `kind` 区分，不再有第二套 ID | 消除「两套真相」；生成产物终于进库 |
| F0-2 | **唯一真相 + 只读投影**：DB 为唯一事实来源，投影由写入后重建、只供外部读，不参与鉴权与列表 | 避免 JSON 覆盖无 CAS 的并发破坏 |
| F0-3 | **入库**：网页流式上传、inbox 登记、生成产物回写；先传摘要命中即秒传；批次状态逐条可查 | G03/G04/G12、T22 秒传缺失 |
| F0-4 | **可见性接 identity 三层**：个人 / 小组 / 团队，按 `团队 ∪ 小组 ∪ 个人 ∪ 角色` 判定，无 deny、只增不减 | `shared` 布尔位粒度太粗（D4） |
| F0-5 | **检索**：关键词 + 类型 + 时间 + 大小 + 分辨率 + 排序 + 分页 + 分面统计，查询条件写进 URL | G09、T22 检索弱 |
| F0-6 | **详情与预览**：统一详情（规格 / 来源 / 引用数 / 生成适用门禁）、视频兼容派生流、缩略图降级、原件独立下载 | G06/G07/G08 |
| F0-7 | **生命周期**：改名、移动分类、回收站、恢复、彻底删除；删除前多来源引用校验且 fail-closed | 现役已有但绑在旧表，需在新表重建 |
| F0-8 | **批量写**：批量改名 / 分类 / 标签 / 可见性 / 删除，批次级互斥 + 逐条结果 | T22 批量写缺失 |
| F0-9 | **与模板库衔接**：模板 prepare 经新 `library-lookup` 注入口取参考素材与归属；切换时统一改引用 | 替换 `asset-lookup`，D5 |
| F0-10 | **权限与审计**：细分 `library:*` 权限点、写操作审计、按用户配额 | 旧 6 个权限粒度过粗 |

### 4.2 P1 · 二期（首版不做，但模型要留位置）

- 标签与**自定义分类**，替代写死的 4 类枚举（`kind` / `category` 先留可扩展字段）。
- 异步派生队列：预热、失败重试、进度可查，替代请求内同步 ffmpeg。
- 分片上传 / 断点续传（大文件，300MB 级）。
- 收藏、批量下载、使用统计、引用关系图谱（哪个模板 / 任务用过）。

### 4.3 明确不做

- 备份分支的 blob 分离 / 主体(Subject) / 配方 / 候选 / 模板槽位 / `published_grants`（D1、D4）。
- 双写、运行时 feature flag（框架没有，用两个 URL 并存代替）。
- **任何 seed 演示数据**：用户已明确「不要 seed 了，全部都不要了」；新模块不写 seed 钩子，
  也不把 `app seed --reset` 当初始化步骤（它会清空新表且不回填）。
- 版本历史、多人协同编辑、跨模块统一产物库。

## 5. 数据迁移与切换

1. **导入**：`app import-library`，读现役 `assets` 表 + S3 `yufan-archive` 归档索引，
   幂等（按内容摘要去重）、支持 `--dry-run`、输出对账报告（条数 / 摘要 / 字节数 / 失败明细）。
   可反复重跑，切换前再跑一次追平增量。
2. **不双写**：并存期旧库继续独自服务生产；v2 只承接导入与新开发验证数据。
3. **切换**：改 `studio/src/modules/assets/module.ts` 的 `load()` 指向 v2 页面，
   导航文案同步；后端 `/assets` 先保留，观察期结束再下线。
4. **回滚**：把 `load()` 改回 `AssetsPage`，一行完成；数据未破坏，旧库仍在跑。
5. **收尾**：删旧模块 → 重生成 `ROUTES.md` / `MOCK_COVERAGE.md` / 四份 OpenAPI 生成物 →
   `entities/asset` 保留给模板库，避免连带返工。

## 6. 批次计划（总览；细化后的 M0–M5 见 DESIGN §10）

| 批次 | 内容 | 闸门 |
| --- | --- | --- |
| M0 | 空模块骨架：`ModuleSpec` 全钩子、隔离命名、权限点、生成物、mock 骨架 | 启动对账 0 gap，两个库同时在线互不影响 · **已完成** |
| M1 | 数据模型 + 迁移 + 入库 / 秒传 / 导入 CLI | 迁移双向、导入幂等与对账用例 · **已完成**（10 表 / 上传 / 秒传 / `sourceKey` 幂等 / `import-archive`） |
| M2 | 三层可见性、共享审核 + 撤销审批、删除矩阵与窄口、草稿/回收站状态机、标签、批量写、选择器 | 越权 / 越级守卫、审核并发版本、不变式 A/B、引用 fail-closed · **已完成** |
| M3 | 封装、批量下载 job + 流式、生成入库与溯源快照、详情页六区 | 100 项 / 2GB 边界、失败重试、参数快照冻结 · **已完成**（封装 CRUD / 异步 zip / `from-generation` / 详情六区数据源） |
| M4 | 前端页面（列表 / 详情 / 上传 / 批量 / 回收站 / 选择器） | `npm run verify`、mock coverage |
| M5 | 切换演练、并行观察、下线旧模块、重生成全部生成物 | 一键回滚演练通过 |

## 7. 验收闸门（沿用项目现有门禁）

- 后端：`pytest -q --basetemp=var/pytest-xxx`、`ruff check src tests`、
  `mypy --platform win32 src`、`alembic check` + 迁移双向、`app doctor`、`export-openapi --check`。
- 前端：`cd studio && npm run verify`、`npm run gen:mock-coverage`，`ROUTES.md` 重生成。
- **隔离专项**（新增）：`/assets` 与 `/library` 同时在线时请求互不串；
  两个 GC 各扫各的前缀、不误删对方 blob；投影目录互不覆盖；
  `asset-lookup` 与 `library-lookup` 两个注入口都在 `PORT_CATALOG` 且启动不冲突。
- 框架守卫参考 `docs/EXTENDING.md`：架构零容忍、权限声明对账、`ModuleSpec.jobs/periodic`
  对账、`PORT_CATALOG` 形状、CLI 顶层 import 棘轮。

## 8. 待确认（不阻塞，按默认继续）

| 编号 | 问题 | 默认 |
| --- | --- | --- |
| Q1 | ~~第 4 节功能清单是否按提案定稿~~ | **已关闭**：用户 2026-11-09 追加 6 条功能描述重新定义清单，详见 `ASSET_LIBRARY_V2_DESIGN.md` |
| Q2 | 命名用 `library` / `/library` / `library:*` 还是别的 | 按第 3 节默认，已在 DESIGN 全文采用 |
| Q3 | ~~生成产物是否纳入统一资产~~ | **已关闭**：用户第 6 条明确要求生成结果入库并展示生成参数，见 DESIGN §7 |
| Q4 | ~~标签是否从 P1 提到首版~~ | **已关闭**：用户第 1 条把标签定为三级分类，首版必做，见 DESIGN §2 |
| Q5 | 切换观察期多长、旧接口何时下线 | 观察期由用户定，先不下线 |
| Q6 | ~~「仅管理员可见」用什么判定~~ | **已关闭**：`permissions: ['library:read']` 一行同时管 URL 守卫 / 侧栏 / 宫格，权限点只授超管与开发角色，见 DESIGN §8 |
| Q7 | 草稿「审核」是本人确认，还是必须换人复核 | 默认**本人确认入库**，管理员/`library:draft:review` 可代审与批量审；若要求提交人 ≠ 审核人，M2 加一条判断，见 DESIGN §11 Q-D |
| Q8 | 组长删自己上传的「仅本组共享」素材是否要合并成一步 | 默认不合并（先撤销再删，两步且自身自审）；若嫌绕，加窄口规则，见 DESIGN §11 Q-E |

**DESIGN 的知会项**（不阻塞）：Q-A 模板库切换时需改一个 URL 常量、Q-B 生图与工作台需新增选择器 UI、Q-C 存量生成结果参数不全会在详情页显示「未记录」、Q-D 草稿审核默认本人确认、Q-E 组长删本组共享素材需两步。生命周期与删除规则见 D7 与 DESIGN §3.3–3.5。
