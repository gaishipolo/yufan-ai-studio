# 资产库改造 · 开发计划

> 配套文档：`ASSET_LIBRARY_REDESIGN.md`（设计与决策理由）。
> 那份回答"为什么这么设计"，这份只回答三件事：**按什么顺序做、动哪些文件、怎么判完成**。
> 决策编号（A1–A8 / B1–B8 / C1–C6）沿用设计文档，不重新编号。

---

## 0. 三条贯穿全程的纪律

写在最前面，因为违反它们的代价不是"多写点代码"，而是**数据要重做**。

**① 扩展 → 双写 → 收缩，任何一个结构变更都不许一步到位。**
新增列/表先建、回填写满、双写校验一版、再删旧列。这条对 `blob` 拆分、`category` 表化、`shared` → `visibility` 三处都适用。直接 `ALTER TABLE ... DROP COLUMN` 的回滚成本是把备份恢复回来，而不是改一行代码。

**② 每批次独立可上线、独立可回滚，不留"半迁移"状态。**
批次之间的依赖只走接口和注入口，不走"等下一批一起发"。任何一批发不出去时，线上必须是上一批的完整状态，不能是"表建了但代码没读"。

**③ 前端改造是"换数据源"和"换壳"两件事，永远分开做。**
同一批次里既改查询字段又重写页面布局，出问题时无法判断是数据错还是渲染错。M1 只换数据源（壳不动），M2 才换壳。

---

## 1. 批次总览

```
M1 地基 ──┬──► M2 标签 ─────────────┬──► M4 向量
          │                          │
          ├──► M2.5 主体 ──┬─────────┘
          │                └──► M3.5 模板与槽位 ◄── M3 配方与候选
          └──► M3 配方与候选 ─┘
                                    M1 + M2.5 + M3 ──► M5 组织与生命周期
```

| 批次 | 交付物 | 依赖 | 可独立上线 | 风险 |
| --- | --- | --- | --- | --- |
| **M1** ✅ | team / blob / category / media_type / 配额口径 | 无 | 是 | **高**（动的是存量数据的形状） |
| **M2** ✅ | 受控标签 + 筛选 + 保存视图 + 五段式壳 | M1 | 是 | 中（纯新增，不动存量） |
| **M2.5** ✅ | 主体 / 成员 / 成员授权 / 版本 + 模板读路径切换 | M1 | 是 | **高**（模板 prepare 依赖它） |
| **M3** ✅ | 配方 / 候选与采纳 / 详情浮层 / 衍生三入口 | M1 | 是 | 中 |
| **M3.5** | 模板实体 / 槽位契约 / 两个编辑台 / 试跑 | M2.5 + M3 | 是 | 中（新能力，无存量迁移） |
| **M4** | 向量索引 / 语义分区 / 上下文推荐 | M1 + M2 | 是 | 中（外部依赖是模型成本） |
| **M5** | 复制进团队 / 待审区 / 提升 / 只读态 / 分桶过期 | M1 + M2.5 + M3 | 是 | 中（阻塞在组织流程，不在代码） |

**不做**（本期，与设计文档一致）：物理永久删除入口、多人实时协同编辑标签、跨团队素材市场、自动生成标签、可见水印。

---

## 2. 每批次都要过的闸门

单列一节，因为下面每批的"验收"只写本批特有的检查，这些是所有批次共同的前置。

| 闸门 | 命令 / 动作 | 不过会怎样 |
| --- | --- | --- |
| 后端单测 | `cd api && ./.venv/Scripts/python.exe -m pytest -q --basetemp=var/pytest-tmp` | 必须先确认 `var/pytest-tmp` 没有堆积上千残留子目录，否则本机批量删除守卫会把汇总行冲掉 |
| 契约四份 | `cd api && app.exe export-openapi`，提交 `api/docs/openapi.json`、`api/docs/API.md`、`studio/src/shared/api/schema.ts`、`studio/src/shared/api/permissions.ts` | 前端类型与权限清单与后端脱钩，只有上线后才会发现 |
| 权限点 | 新权限点登记进 `ModuleSpec.permissions`；路由上的 `require(...)` 与数据对象上的权限码（如导出源 `source.permission`）都要有引用侧守卫 | 漏了不报错，症状只有"超管能、别人 403" |
| 注入口 | 新注入口必须有 `register_* / reset_* / has_*` 三件套 + `PORT_CATALOG` 登记 | `test_port_modules_expose_the_full_shape` / `test_every_port_is_catalogued` 会红 |
| 装配物 | 新 job / periodic 必须在 `ModuleSpec.jobs` / `.periodic` 里声明并注册 | `create_app()` 直接拒绝启动，这是好事 |
| 静态检查 | `ruff check` + `mypy --platform win32 src` | — |
| 部署自检 | `cd api && app.exe doctor` | 新增的表/桶/配额口径要各加一条检查 |
| 迁移双向 | 本地 `upgrade` → `downgrade` → `upgrade`；CI 的 PG job 走 `upgrade → check → downgrade base → upgrade head` | 只跑 upgrade 的迁移，回滚路径从来没被执行过 |
| 前端 | `cd studio && npm run verify` | typecheck + lint + test + build |

> 迁移里改已有列时用 `op.batch_alter_table`（SQLite 不支持大多数 `ALTER`）；PG 侧建大索引要在 `autocommit_block()` 里用 `CONCURRENTLY`，否则会锁表。

---

## 3. M1 · 地基

七个子步骤，**顺序不可换**。M1-A 到 M1-F 是一批，M1-G 单独一批（观察一版后再做）。

### 3.0 执行记录（M1-A ~ M1-F 已完成）

| 步骤 | 状态 | 落点 |
| --- | --- | --- |
| M1-A team 实体 | 完成 | `modules/asset/library_models.py`、`migrations/a1c4e8b72d90`、`core/teams.py`、`GET /teams` |
| M1-B blob 表 + 回填 | 完成 | `blobs` 表、`modules/asset/blob_repository.py`（`backfill_blobs` 批 1000 行、可重入） |
| M1-C 读路径切 blob | 完成 | `ArchiveAssetRepository.to_media()` 优先 blob；`_ingest` 双写 |
| M1-D category 表 | 完成 | `categories` 表、`library_service.ensure_tree`（savepoint + `IntegrityError` 重查）、迁移回填 `category_id` |
| M1-E 媒体类型判定 | 完成 | `_sniff` 读 16 字节定 `(类型, MIME)`；`infra.media_probe.inspect(..., kind=)` 按内容分派 |
| M1-F 配额按可见域 | 完成 | `AssetQuota` 八字段、`distinct_used`（`UNION ALL` 去重）、`physical_used`（`blobs` 全量和） |
| M1-G 删旧列 | **未开始**（需观察 ≥1 版） | — |

验证：迁移 `upgrade → downgrade → downgrade → upgrade` 双向通、`alembic check` 无差异、
`doctor` 27 项 0 FAIL、素材 118 条用例全绿、`ruff` + `mypy` 干净。

> **M1 期间做的两个决定**（都不阻塞后续批次，记在这里备查）：
>
> **① 团队权限码挂在 `asset` 名下**（`asset:team:read` / `asset:team:manage`），
> 不另立 `team` 模块。理由是 `test_architecture.py` 要求「权限码前缀 == 归属模块」，
> 而团队是**库的边界**、属于素材库这个产品（M5 的复制进团队 / 待审区 / 提升全长在素材库上），
> 不是独立的组织系统；`资源:子资源:动作` 也是既有语法（同 `asset:reference:manage`、
> `template:pack:manage`）。反方案「新建 team 模块」会因为 `ensure_all_team_trees`
> 要读 Team 表又写 Category 表而立刻需要一条跨模块注入口——出生即耦合。
> 若日后团队升级为平台级组织能力，改名是权限清单 + 角色快照的一次迁移，成本可接受。
>
> **② `asset:team:member:manage` 暂不声明**。`test_permission_coverage.py` 的规则是
> 「声明了就必须有判定点」，现在没有成员管理接口，先声明等于给角色配置界面加一个
> 勾不出效果的选项（该文件的 `ALLOWED_UNENFORCED` 刻意留空）。随 M5 成员管理接口
> 一起上线并同时授权给 `team_admin`。**这一条要在 M5 开工时第一件事做掉**，
> 否则团队管理员会以为自己能管人。

### M1-A · team 实体

| 项 | 内容 |
| --- | --- |
| 表 | `team(id, name, kind, status, created_at)`；`team_member(team_id, user_id, role, joined_at)`，唯一 `(team_id, user_id)` |
| 取值 | `kind` = content / ops / tech；`role` = member / admin；`status` = active / archived |
| seed | 3 个团队 + 6 个管理员账号（`tm.content.lead` / `.backup` 等，B7）。**账号 id 常量放 `core/`**，identity 的 seed 按它建账号，asset 的 seed 按它建 `team_member`——两边都不能 import 对方 |
| 接口 | `GET /teams`（只读，顶栏切换器数据源）。写接口留到 M5 |
| 权限点 | `asset:team:read`（默认授予所有登录用户，否则顶栏拿不到列表）、`asset:team:manage`；`asset:team:member:manage` 随 M5 成员管理接口上线（见 §3.0 决定②） |
| 新增列 | `asset.team_id`（可空）、`subject` 的 `team_id` 留到 M2.5 |
| 测试 | 唯一约束；seed 幂等（跑两遍不产生重复）；`asset:team:read` 不含成员明细（列表只给 id/name，成员要走 `asset:team:member:manage`） |
| 退出条件 | `app doctor` 有一条"团队数与 seed 账号数对账"；跨团队不可见的谓词单测先立起来（M1 就生效，不留到 M5） |

> **为什么账号常量要上移 `core/`**：asset 不能 import identity，identity 也不该知道素材团队。两边共用一份 id 表是最小的耦合面，比"asset 里硬编码 user_id"和"asset 调 identity 的私有服务"都干净。

### M1-B · blob 表（扩展阶段）

| 项 | 内容 |
| --- | --- |
| 表 | `blob(id, sha256, size_bytes, mime_type, storage_key, preview_key, tier, ref_count, created_at, expires_at)` |
| 唯一 | `UNIQUE(sha256) WHERE sha256 IS NOT NULL`（摘要算不出的行不参与，NULL 互不相同） |
| 新增列 | `asset.blob_id`（先可空） |
| 回填 | 按 `sha256` 分组，每组取最早一条的 `storage_key` / `preview_key` 建 blob；同一批内把组内所有 asset 的 `blob_id` 指过去；`sha256 IS NULL` 的行单独建 blob（sha256 存 NULL） |
| 回填方式 | 按 1000 行一批、`WHERE blob_id IS NULL` 可重入，走 CLI 命令（`app assets backfill-blobs`）而不是迁移里跑全量——迁移里跑大循环会让部署卡住，失败后还要靠手动清 |
| `ref_count` | 回填时按实际引用数算，之后由写路径维护。旧 `asset.ref_count` 保留展示，不作删除依据（沿用现有认知） |
| 测试 | 回填后 `blob` 条数 = distinct `sha256` 数 + 摘要为 NULL 的行数；每条 asset 都有 `blob_id`；字节总和与迁移前一致 |
| 退出条件 | 对账脚本输出 0 差异（写成 CLI 子命令，能进 doctor） |

### M1-C · 读路径切 blob（双写）

| 项 | 内容 |
| --- | --- |
| 读 | `ArchiveAssetRepository.to_media()` 从 `blob` 取 `storage_key` / `size_bytes` / `mime_type` / `preview_key`。**这个映射只保留一份**（现有注释里已经在强调，别新增第二处） |
| 写 | `_ingest` 改为：先按 `sha256` upsert `blob`（并发用唯一约束兜底，冲突则回读），再写 `asset.blob_id`；同时继续写旧的 `assets.storage_key/sha256/size_bytes` |
| 双写校验 | doctor 加一条"抽查 N 条 asset，`asset.storage_key == blob.storage_key`"，不一致直接 FAIL |
| 传输 | `Served.local/remote` 的出参形状不变；只换数据来源，不动响应 |
| 测试 | 同一文件传两次 → `blob` 1 条 / `asset` 2 条 / `ref_count=2`；软删一条 → `ref_count=1`；恢复 → 回到 2；彻底删除最后一条 → blob 不被删（留到 M5 的清理任务） |
| 退出条件 | 线上双写跑满一个版本周期、doctor 一致性 0 FAIL |

> **M1-C 之前不要删任何旧列。** 双写期的价值就是"改坏了能立刻退回读旧列"，退回动作是改一行代码，不是恢复备份。

### M1-D · category 表与分类树

| 项 | 内容 |
| --- | --- |
| 表 | `category(id, scope, team_id, name, kind, is_inbox, sort_order, archived_at)`；`scope` = personal / team / brand |
| 约束 | 一级分类唯一 `(scope, team_id, name) WHERE parent_id IS NULL AND archived_at IS NULL`。**不建 `parent_id` 列**——二级是媒体类型分面（C1），不是真实节点，建了就会有人往里插 |
| 系统分类 | 每个库一个"未分类收件箱"（`is_inbox=true`，不可改名/删除/归档） |
| 初始一级分类 | 参考素材 / 产品图 / 人物形象 / 场景背景 / 合成成片（公共库与三个团队库由 seed 建；个人库懒创建——用户首次上传或首次打开素材页时补） |
| 新增列 | `asset.category_id`（先可空）+ `asset.visibility`（`personal` / `team` / `brand`）+ `asset.owner_team_id` |
| 回填映射 | `source` → 参考素材 · `character` → 人物形象 · `product` → 产品图 · `scene` → 场景背景 · `audio` → **参考素材**（媒体类型分面为 audio，音频不是用途）· `NULL` → 收件箱 |
| `shared` 回填 | `shared=true` → `visibility=brand` + 一条"历史公开"的 `promotion_request` 记录（`reviewer` 记为 `system`，便于追溯） |
| 测试 | 映射覆盖率 100%（每条非删素材都有 `category_id`）；收件箱计数与"无分类"条数一致；删分类必须选去向（不允许级联删素材） |
| 退出条件 | `category_id` 收紧为 NOT NULL 的前置检查：0 条为空 |

> **映射里唯一需要人复核的是 `audio → 参考素材`。** 原来 `category` 同时承担"业务类型"和"目录"两种语义，把音频塞进某个用途分类会造出一个不存在的概念。发布前导出一份"音频素材清单"给人看一眼，这是整个 M1 里唯一不能自动判对的地方。

### M1-E · 媒体类型系统判定

| 项 | 内容 |
| --- | --- |
| 行为 | 上传接口的 `category` 参数从必填改为**可选**；服务端按 magic bytes + mime 判定 `media_type`，与客户端传值不一致时**以服务端为准、记一条 warn、不返回 422** |
| 值域 | `image` / `video` / `audio` / `document`（新增 document，为后续脚本、PDF 留位） |
| 字段 | `assets.type` 改名 `media_type`（先加新列回填，M1-G 删旧列） |
| 前端 | M1 不动：仍可选，但选择器变成"可选筛选项"而非必填 |
| 测试 | 伪造扩展名（`.mp4` 实为 PNG）→ `media_type=image`；`document` 不被当成图片渲染；前端传错时响应里的 `mediaType` 是服务端判定值 |
| 退出条件 | 存量素材的 `media_type` 由探测重算（不信历史 `type`），差异清单人过一眼 |

> **为什么不返回 422**：存量前端和各处脚本都在传 `category`，改必填语义会一次打挂所有调用方。以服务端为准 + warn 是唯一能平滑过渡的形态；到 M2 前端去掉选择器后，这个参数自然退化成"提示"。

### M1-F · 配额按可见域聚合（A6）

| 项 | 内容 |
| --- | --- |
| 口径 | 个人额度 = `visibility=personal AND owner=me AND deleted_at IS NULL` 的 distinct `sha256` 字节和；团队额度 = `visibility=team AND team_id=t` 同理；`brand` 不计入任何个人/团队 |
| 出参 | 新结构 `AssetQuota { personalUsed, personalQuota, teamUsed, teamQuota, draftUsed, trashUsed, physicalUsed, physicalQuota }`，替换现在扁平的两个数字 |
| 配置 | `ARCHIVE_QUOTA_BYTES_PER_USER` 保留为个人默认；新增 `ARCHIVE_QUOTA_BYTES_PER_TEAM`；两者都为 0 时不限制（沿用现状） |
| 上传前置 | 双检查：账面（aggregate）与物理（`SUM(blob.size_bytes)`）都不过则拒；账面过但物理不过时提示"回收站占用 X，清空可释放" |
| doctor | 现有"配额水位"检查改为按库分别报（个人 / 团队 / 物理三条） |
| 测试 | 复制一条素材到另一分类 → 个人已用**不变**（同 `sha256` 去重）；交付到团队 → 个人不变、团队 +size、物理不变（跨主体各计一次）；软删 → 账面立刻回落、物理不变；候选不计 |
| 退出条件 | 发布前导出一份"改造前 / 改造后"的额度对比（同一个人、同一批数据），差异必须能被口径解释 |

> **M1 里最容易写错的一条断言是"复制后个人已用不变"。** 写成 `SUM(size_bytes)` 就会翻倍，而翻倍不会有任何报错——用户只会觉得"我明明只复制了一次"。这条要单独一个用例，且用例名里带上"dedupe"。

### M1-G · 收缩（单独一批）

**前置**：M1-A~F 上线观察 ≥1 个版本，doctor 一致性检查 0 FAIL，回填对账 0 差异。

| 动作 | 说明 |
| --- | --- |
| 删 `assets.storage_key` / `preview_key` 副本 | 读路径已全走 blob |
| 删 `assets.sha256` / `size_bytes` | 同上 |
| 删 `assets.category` / `type` / `shared` | 已由 `category_id` / `media_type` / `visibility` 承接 |
| 收紧 | `asset.category_id` NOT NULL、`asset.blob_id` NOT NULL、`asset.visibility` NOT NULL |
| 迁移写法 | `op.batch_alter_table`（SQLite）；PG 走普通 `ALTER`，但删列前先确认没有索引引用 |
| 回滚 | **不可逆**。降级只能重建空列，数据从 blob/category 反推——写进 migration 的 `downgrade` 注释里，别假装能退 |

> 这是整个改造里**唯一一个"做错就无法回头"的动作**，所以它单独一批、单独发版、单独通知。

---

## 4. M2 · 标签与五段式壳

### 4.0 执行记录（M2-A / M2-B / M2-C 已完成）

后端：

| 文件 | 内容 |
| --- | --- |
| `asset/tag_models.py` | `Tag` / `AssetTag` / `AssetView` 三张表；`(dimension,name)` 唯一、`(tag_id,asset_id)` 反查索引、`(owner_id,name)` 视图唯一 |
| `asset/tag_vocabulary.py` | 46 词 / 6 维度常量（`INITIAL_TAGS`、`TAG_TOTAL`） |
| `asset/tag_service.py` | `ensure_vocabulary`（bootstrap 补齐）、`catalog`（分组 + 使用数 + `pinned`）、`create`、`resolve`、`set_asset_tags`（整体替换）、视图 CRUD |
| `asset/tag_api.py` | `GET/POST /asset-tags`、`GET/POST/DELETE /asset-views`，写路由各带限流 |
| `asset/category_api.py` | `GET /asset-categories?library=`，返回分类树 + 每类计数 + `inboxCount` |
| `asset/api.py` | `GET /assets` 新增 `tagIds`（AND）、`sort`、`categoryId`、`mediaType`、`scope=team` |
| `asset/__init__.py` | 新增 `asset:tag:manage`；`asset:update` 描述扩到"名称、标签" |
| `migrations/c3f5a1d8e6b2_asset_tags.py` | 三张表 + 索引 |

标签 AND 的实现是 `IN + HAVING COUNT(*) = N` 而不是链 `EXISTS`：一次索引扫描 vs N 次。
`asset_tag` 的主键是 `(asset_id, tag_id)`，同一条素材不会重复命中同一个词，所以不必 `COUNT(DISTINCT)`。

测试：`test_asset_tags.py`（6 条）、`test_asset_categories.py`（7 条）。
后者有一条**对账用例**：分类角标之和必须等于同一个库的列表总数——「角标 149、点进去 12 条」
这种差异通常被当成缓存问题排查很久。

前端：

| 文件 | 内容 |
| --- | --- |
| `model/library.ts` | 库 id（`personal` / `brand` / `team:<id>`）、`resolveLocation`（新旧参数都在这里判定）、`listScopeOf`（含第四个域 `team`）、`MEDIA_TYPES` |
| `LibrarySwitcher.tsx` | 第①段，团队库带角色角标 |
| `CategoryBar.tsx` | 第②段，树从 `/asset-categories` 取（不从列表响应推），二级媒体类型分面 |
| `TagFilterBar.tsx` | 第③段，受控标签 + 保存视图；只能选词表、多选是 AND、状态放 URL |
| `StickyControls.tsx` + `model/controls.ts` | 第④段，数量 / 排序 / 密度 / 上传；枚举值独立成文件（`react-refresh` 要求一个文件只导出组件） |
| `SourceLibrary.tsx` | 第④⑤段，`toolbar` 三档（`full` / `control` / `none`）+ `density` 两档，**共用一次查询** |
| `AssetWorkspace.tsx` | 五段式装配 |
| `pages/assets/AssetReferencesPage.tsx` / `AssetPacksPage.tsx` | 两个维护页拆成**独立路由** `/assets/references`、`/assets/packs`（设计 §7.6） |
| `entities/asset/api/{tag,team,category}.api.ts` | 数据层 |
| `mocks/routes/library.mock.ts` + `mocks/library-tree.ts` | mock 端团队 / 分类 / 标签 / 视图；分类计数与列表筛选**共用同一个 `visibleInLibrary`** |

`density-contract.test.tsx` 断言「两种密度传给查询的参数逐键相等」而不是「卡片数一样」：
后者在固定 mock 下永远成立，抓不到漂移。

> **M2 期间做的三个决定**（不阻塞后续批次，记在这里备查）：
>
> **① 「我的素材」只显示 `visibility=personal` 的素材，团队库素材不重复出现。**
> 设计文档 §7.1 那句「同一素材在我的素材与团队库同时出现时是同一记录」是反重复，
> 不是要求两个入口都列。真按"都列"实现会立刻破坏计数：团队素材的 `category_id`
> 落在**团队**那棵树上，个人树统计不到它，症状是「个人库共 9 项、分类角标加起来 5」。
> 一条素材只有一个"家"；要拿回自己库里走 M5 的「取用」复制。
> **这一条如果业务另有预期，M5 开工前需要拍一次。**
>
> **② 分类条需要独立的 `/asset-categories` 接口**，计划里没写。原因：分类条要在
> 没有列表请求时也渲染（首屏、刚切库），从列表响应挤出来会先闪旧树再跳新树。
> 计数口径与列表分支**逐条对齐**（公共池也保留那条 `category=source` 的限制），
> 靠 `test_asset_categories.py` 的对账用例守住。
>
> **③ 两个维护页提前拆成独立路由**（原计划未规定时点）。它们原来靠
> `?scope=references|packs` 内联渲染，放进五段式壳会变成第 6 个 tab；
> 旧参数保留为重定向，书签不失效。

### M2-A · 词表与关联

| 项 | 内容 |
| --- | --- |
| 表 | `tag(id, dimension, name, status, created_by, use_count, created_at)`，唯一 `(dimension, name)`；`asset_tag(asset_id, tag_id)` 复合主键 + `(tag_id, asset_id)` 反查索引 |
| 词表 | 46 词 / 6 维度（B1 定稿，设计文档 §3.3） |
| 治理 | `tag:manage` 新增直接生效、不审批、留审计；`status=active` 但 `use_count < 5` 的不进筛选条常显区；90 天零使用提示 `retired`（不物理删，历史关联仍在） |
| 接口 | `GET /asset-tags`（按维度分组 + 每词使用数 + `pinned` 标志）；`POST /asset-tags`（管理员，校验同维度重名） |
| 素材筛选 | `GET /assets` 加 `tagIds`（**AND 语义**：多个标签是"同时具备"，不是"任一"） |
| 保存视图 | `asset_view(id, owner_id, name, query_json, sort, created_at)`，唯一 `(owner_id, name)`；`query_json` 存原始筛选参数，不存 SQL |
| 测试 | 两标签 AND 命中数 = 交集；`retired` 标签不出现在筛选条但历史素材仍带它；保存视图恢复后结果条数与原筛选一致 |
| 退出条件 | 筛选条能按 6 维度完整下拉；"＋更多"后第三行不再挤压内容区 |

### M2-B · 五段式壳

| 段 | 组件 | 要点 |
| --- | --- | --- |
| ① 顶栏库切换 | `LibrarySwitcher` | 5 个入口（个人 / 3 团队 / 公共），超过 3 个时分组成「我的 / 团队 / 公共」；当前库写进 URL |
| ② 分类条 | `CategoryBar` | 一级胶囊 + 二级媒体类型胶囊（可折叠、带计数）；计数在无筛选时是总数，有筛选时变"匹配 N / 共 M" |
| ③ 筛选条 | `FilterBar` | 受控标签下拉 + 已选条件 chip + 保存视图入口 |
| ④ 吸顶控制条 | `StickyControls` | 数量 / 排序 / 视图切换 / 上传；多选时整条替换为批量操作条 |
| ⑤ 内容区 | `AssetGrid` / `AssetList` | 单实现 + `density` 参数；卡片角标与悬停操作条按 `capabilities` 渲染 |

**旧 tab 的处理**：现在的 `?scope=public|mine|references|packs` 全部保留为**别名**，映射到新的库 + 分类组合，至少保留两个版本。用户书签和外部链接失效是最容易被忽略的回归。

**不做什么**（写下来防止实现时顺手加）：
- 不做第二套数据 hooks。`density` 只影响渲染，不影响查询。
- 不把筛选状态放 localStorage——放 URL，可分享、可后退。
- 不在卡片上做"只有 hover 才存在的操作"：键盘用户无法触达。hover 条要能通过 Tab 聚焦。

### M2-C · 契约测试

同一组筛选参数（10 组，覆盖标签 AND、媒体类型、时间区间、空结果）在 `density=full` 与 `density=compact` 下必须返回**相同条数与相同首条 ID**。这条做成测试而不是人工对比——两套实现的漂移正是这个仓已经付过一次代价的地方。

---

## 5. M2.5 · 主体

### M2.5-A · 表与迁移

| 表 | 字段 |
| --- | --- |
| `subject` | `id / subject_type / category_id / visibility / team_id / attributes(JSON) / cover_asset_id / status(draft·published·archived) / requires(JSON) / current_version` |
| `subject_member` | `(subject_id, asset_id)` 唯一 · `role` · `sort_order` · `grants(JSON)` · `in_output(bool)` · `added_by / added_at` |
| `subject_version` | `subject_id / version / members(JSON: asset_id+sha256+role) / cover / attributes`，唯一 `(subject_id, version)` |
| `subject_legacy_pack` | `pack_id → subject_id`（B5，历史链接不断） |

**迁移来源**：`template_packs` + `template_pack_references` → subject + member。现有 pack 已经是 1–10 图 + 主图 + 固定参数 + 发布版本，映射是结构平移：
包 → 主体 · 包内图 → 成员（按现有 `position` 填 `sort_order`，`role` 先统一填"未标注"）· 主图 → `cover_asset_id` · 固定参数 → `attributes` · 已发布包 → `subject_version` v1。

`grants` / `in_output` **没有历史值要保真**（现有 pack 没这个维度）：主图给 `generate + download`，参考图与未标注只给 `generate`。

### M2.5-B · 能力与可达性

两条断言写成**纯函数**，输入是主体 + 成员 + 访问者，输出是布尔与缺失清单。纯函数才能被单测穷举，写在服务里就只能靠集成测试撞。

| 断言 | 形状 |
| --- | --- |
| 角色可达 | `∀ role ∈ required_roles, ∃ m ∈ members: m.role == role ∧ m.stage == adopted` |
| 能力可达 | `requires ⊆ ⋂{m.grants \| m 参与输出}` |

判定粒度必须分开：`generate` 要求全体成员授予（生成要读全部字节）；`download` / `derive` 逐成员判（木桶不压整体）。主体能力取**交集**——取并集会让"公开一个产品"等于"公开它全部参考原片"。

### M2.5-C · 读路径切换（本批最容易出事的地方）

template 模块现在直接读 `pack_id` 拿图。改造后它必须走注入口：

| 步骤 | 动作 | 校验 |
| --- | --- | --- |
| 1 | 在 `core/ports/media.py`（或同级）加 `SubjectLookup` 注入口，asset 模块实现并注册 | 三件套 + `PORT_CATALOG` |
| 2 | template 的 prepare 改为经注入口取成员与能力 | 注入口未注册时返回**中性值**并在 `PORT_CATALOG.when_unregistered` 写明原因，不要静默返回空图 |
| 3 | 灰度：同一请求同时走旧路径与新路径，比对成员 ID 列表与顺序 | 差异率 0 才切默认 |
| 4 | 旧路径保留一版，标 `deprecated` | M5 再删 |

> **M2.5 的验收不看页面，看 prepare。** 主体卡片做得再对，只要 prepare 拿到的图少一张或顺序变了，生成结果就会变——而这是"生成结果与上周不一样"这类最难归因的故障源头。灰度比对是唯一能在上线前发现的办法。

### M2.5-D · 接口与前端

| 接口 | 说明 |
| --- | --- |
| `GET /subjects` | 列表，带成员数、封面、可用性（三态） |
| `GET /subjects/{id}` | 详情 + 成员条带（每条带 role / grants / 缺图标记） |
| `POST/PUT/DELETE /subjects` | 组装与编辑。删除只删主体，不动成员 |
| `POST /subjects/{id}/publish` | 发布前置：至少 1 名成员、封面在成员内、角色可达 + 能力可达、成员合规全通过 |
| `GET /subjects/by-legacy-pack/{packId}` | B5 只读解析 |

前端本批只做**主体卡片（堆叠态）+ 详情成员条带**，组装台留到 M3.5——组装台需要模板槽位才知道"该按哪些角色摆"，提前做会白做一遍。

### M2.5-E · 测试重点

- 删主体不删成员（铁律①）
- 成员被软删 → 主体进"缺图"状态，禁止发布与新调用，历史版本仍可读
- 同一 asset 可同时在两个主体里（唯一约束只管组内）
- 角色可达 / 能力可达各自的边界：缺一个 required role / 缺一个 required cap 都必须拦在发布时，而不是运行时
- `subject_version` 冻结后改成员不影响已发布版本的成员列表

### 5.0 执行记录（M2.5-A ~ M2.5-E 已完成）

| 步骤 | 落点 | 用例 |
| --- | --- | --- |
| A 表与迁移 | `subject_models.py`（四表 + 两条部分索引）、`d1a7b3e5c924_subjects.py`（含 `_backfill_from_packs`，按 `pack_id` 可重入） | `test_migrations.py` |
| B 能力与可达性 | `subject_capabilities.py`（纯函数：`effective_capabilities` / `check_reachability`） | `test_subject_capabilities.py` 7 条 |
| C 读路径切换 | `core/ports/subjects.py`（fail-open，返回 `None` 不抛错）+ `template/subject_bridge.py`（off/shadow/on，默认 shadow） | `test_subject_bridge.py` 7 条 |
| D 接口与前端 | `subject_api.py` 四条路由 + `SubjectCard`（堆叠态）/ `SubjectDetailPanel`（成员条带）/ `/assets/subjects` 独立路由 | `test_subjects.py` 12 条、`subject-states.test.tsx` 6 条 |
| E 铁律 | 见下一段 | 已并入上面两份用例 |

四个决定（写在这里，免得下次重新纠结）：

1. **权限沿用 `asset:read` / `asset:update`，不为发布新造码**。发布改的是「本人组装的素材」，
   `asset:update` 的语义边界正好覆盖；新码只会让每个角色配置界面多一个看不出差别的勾选项。
2. **`GET /subjects` 不带成员明细，但带成员数与三态**；成员条带只在详情给。
   一页 24 个主体 × 10 个成员 = 240 条素材进一次列表响应，而其中绝大多数不会被看。
3. **演示主体在 asset 的 seed 里造**（`seed_order=20`），不接 `subject_legacy_packs`：
   旧包由模板模块的 seed 在下一步（`seed_order=30`）才建，那时还看不到它们的 id。
   「包还没有主体」正是双读期真实存在的状态，`shadow` 的差异日志本来就该把它数出来。
4. **`SubjectService.publish` 自己提交事务**：版本行与主体的 `current_version` 必须同事务落地，
   否则中途失败会留下「版本 3 存在、主体还指向 2」，而模板按 `current_version` 取图，
   读到的是老图——比直接报错更难查。

验证记录：`pytest` 全绿（1038 条 + 本批新增 12 条）、`ruff` / `mypy --platform win32`（243 文件）0 错、
迁移 `upgrade → downgrade -1 → upgrade` 双向通、`alembic check` 无差异、四份生成物已重生成、
`app doctor` 新增 `library.subjects` 之外的四条库检查全 PASS。

---

## 6. M3 · 配方与候选

### M3-A · 表

| 表 / 列 | 说明 |
| --- | --- |
| `generation_recipe` | `model / model_version / prompt / negative_prompt / params(JSON) / seed / reference_asset_ids(JSON 仅快照) / task_id / template_id / template_version / subject_version / content_label / parent_recipe_id / cost_units / created_by / created_at` |
| `asset.recipe_id` | 可空。生成型必须有 |
| `asset.origin` | `upload` / `generate`，**不可改** |
| `asset.stage` | `candidate` / `adopted` / `discarded` / `expired` |
| `asset.adopted_at` | 不建 `adopted_by`（A4：恒等于生成者） |
| `asset.compliance_status` | `pending` / `approved` / `rejected` / `review_needed`（B2 四档） |
| `asset.compliance_note` / `reviewed_by` / `reviewed_at` | 驳回理由走枚举 + 自由文本 |

**参考图关联落表，不落 JSON。** 配方里的 `reference_asset_ids` 只在 `subject_version` 快照里用（冻结证据），血缘反查走 `asset_ref` 关联表——存 URL 数组会让"反向查血缘"退化成全表扫，删图即 404。

### M3-B · 候选生命周期

```
生成完成 ─► candidate ──采纳──► adopted ──回收站──► deleted
                ├─弃用──► discarded ──过期──► 回收
                └─超时──► expired（不复活）
```

| 约束 | 实现位置 |
| --- | --- |
| 只有 `adopted` 能被主体引用、被模板消费、打标签、进向量索引 | 四条写路径各加一次判定（不是一处），因为漏一处就漏一个口子 |
| 候选默认不出现在 `GET /assets` | 列表默认 `stage=adopted`，候选走 `GET /assets/candidates`（按 `recipe_id` 分组） |
| 候选 7 天过期，不复活 | `@periodic("asset.candidate.gc")`，过期判定"自然日且至少跨一个工作日"（B3） |
| 候选不计配额 | 配额聚合里 `stage=adopted` 才算 |
| 采纳后 `blob.tier` 改为 `final` | tier 是**派生值**（取引用它的 asset 最高 stage），采纳不搬字节 |

### M3-C · 接口

| 接口 | 说明 |
| --- | --- |
| `POST /assets/{id}/adopt` | 仅 `generated_by == 当前用户`；补分类 / 可选标签；写 `adopted_at` |
| `POST /assets/{id}/discard` | 同上权限 |
| `POST /assets/batch/adopt` | 一次 8 张的批量采纳，"全选"是主要用法 |
| `GET /assets/{id}/lineage` | 配方 + 父配方链 + 被引用关系（谁在用它） |
| `POST /assets/{id}/derive` | `mode` = `image_ref` / `video_first_frame` / `prompt_extract` |
| `GET /assets/{id}/file` | 增加 `download_allowed` 判定（能力受限态的基础） |

### M3-D · 前端

**候选确认区是"待办"，不是"分类"**：顶部横幅，按 `recipe_id` 分组（同批 8 张必须挨着），`A` 采纳 / `D` 弃用 / 空格切换。候选为 0 时**横幅消失**（与"未分类收件箱"常驻不同——空待办只是噪音）。

详情浮层：右侧滑出，含大图、生成血缘（配方全文可复制）、标签与合规、引用关系、操作按钮。**不做侧栏**——侧栏会挤压网格宽度，而这个页面的主任务是浏览。

### M3-E · 测试重点

候选三条"不许"要各有一个用例：不进列表、不进主体、不进配额。过期不复活单独一条（用可控时钟，不要 `sleep`）。

### 6.0 执行记录（M3-A ~ M3-D 已完成）

| 步骤 | 落点 | 用例 |
| --- | --- | --- |
| A 表与迁移 | `recipe_models.py`（`generation_recipe` + `asset_refs`）、`e5b2c8d4f731_recipes_and_candidates.py`（8 列 + 显式回填 `upload/adopted/approved` 后摘掉 `server_default`） | `test_migrations.py` |
| B 候选生命周期 | `lifecycle.py`（纯函数）、`candidate_service.py`（入库 / 分组 / 采纳 / 批量 / 弃用 / 过期）、`subject_grants.py`（逐成员授权） | `test_asset_candidates.py` 12 条 |
| C 接口 | `GET /assets/candidates`、`POST /assets/{id}/adopt|discard`、`POST /assets/batch/adopt`、`GET /assets/{id}/lineage`、`POST /assets/{id}/derive`、`GET /assets/{id}/tags` | `test_asset_lineage.py` 9 条 |
| D 前端 | `CandidateBanner`（分组 / A·D·空格 / 为 0 消失）、`ArchiveDetail` 改右侧滑出 + `AssetLineage`、标签编辑、衍生入口；mock 补七条路由 | `candidate.mock.test.ts` 15 条、`ImagePage.test.tsx` 预填 1 条 |

四个决定（写在这里，免得下次重新纠结）：

1. **配方是独立实体，不是 asset 的列**。一条素材可被复制到三个库（3 条 asset），但它们说的
   是**同一次生成事件**。配方是事实（存一份），归属是视角（存三份）；拍平进 asset 会让
   复制变成"复制一份配方"，之后互相矛盾。
2. **参考图同时存两处**：`reference_asset_ids`（JSON，冻结的输入列表，供复现）+
   `asset_refs`（关联表，带 role/order，供反查）。只留 JSON 则反向查血缘要全表扫。
3. **采纳不搬字节**。`blob.tier` 是派生值：有 `adopted` → `final`，否则 `draft`。
   几十 MB 视频挪桶就是一次失败点，采纳只改一个字符串 + 清 `expires_at`。
4. **`derive` 只回填、不建任务**。生成按次扣费，把"点了衍生"顺手变成一次提交，
   等于让一个浏览动作产生费用。

**已知缺口（M3 交付说明要点）**：

- **合规没有审核接口**。`compliance_status` 已经做成硬前置（`subject_service.compliance_blocked`
  只认 `approved`，发布时 400），但目前只能由后台改库或后续管理页写入。**B2 的审核岗位必须在 M3 上线前定人**，
  否则生成件永远停在"待审"，进不了主体。
- **生成产物入库路径尚未接上**：`CandidateService.register_generated()` 目前只有 seed 与测试调用，
  真正的接入点是生成流水线收尾（`modules/image/pipeline.py::mark_succeeded`），需单独立项。
- **衍生的两条入口先禁用**：`image_ref` / `video_first_frame` 的契约（`DeriveIntent`）已经在了，
  但 `POST /images` 只收 `prompt / size / count / model`，没有参考图入参；`promote` 的入口
  等生成侧接上再开放。当前只有「提取提示词微调」端到端可用（跳 `/image?prompt=…`，只填不提交）。

验证记录：`pytest` 1057 passed / 12 skipped（3 条生成物滞后已重导出修复）、`ruff` / `mypy --platform win32`（250 文件）0 错、
迁移 `upgrade → check → downgrade -1 → upgrade → check` 双向通、四份生成物已重生成、
`app doctor` **30 项全 PASS**（新增 `library.candidates`）；前端 `npm run verify` 全绿（449 条用例）。

**顺手修掉的 mock 缺陷**：`MockContext.params` 只装 query 参数，捕获组要自己切路径。
主体详情 / 发布 / 旧包映射 / 删除保存视图四处原先都用 `ctx.params[0]`，等于永远 404
——症状是"点进主体也打不开"，而那个 404 与真实的不存在长得一模一样。已改成按路径段取。

---

## 7. M3.5 · 模板与槽位

### M3.5-A · 表与契约

| 表 | 字段 |
| --- | --- |
| `asset_template` | `id / name / kind / reference_video_asset_id / prompt_template / params(JSON) / slots(JSON) / status / current_version / published_at` |
| `asset_template_version` | 不可变快照，唯一 `(template_id, version)` |
| `template_try_run` | `template_id / subject_id / result_asset_ids / verdict(pass·fail) / created_by / created_at` |

槽位声明：`required_roles` / `optional_roles` / `min_images` / `max_images` / `required_caps` / `preferred_cover_role`。`required_caps` 默认 `{generate}`，要取原片另存时为 `{generate, download}`。

### M3.5-B · 可用性三态（必须在列表阶段算完）

`GET /asset-templates/{id}/subjects` 返回每条主体带 `availability`（`available` / `degraded` / `unavailable`）与 `missingRoles` / `missingCaps`。

| 态 | 条件 | 呈现 |
| --- | --- | --- |
| available | required 角色与能力全满足 | 正常可选 |
| degraded | 缺 optional 角色 | 可选，标"缺细节图，效果可能偏差" |
| unavailable | 缺 required 角色 / 缺 required 能力 / 张数越界 | 不进可选列表 |

### M3.5-C · 试跑与发布

试跑走**团队额度**（B4），产物进 `draft` 桶，独立额度子项，不写业务用量。发布前置：参考视频有效、角色词表内、必需角色能被至少一个已发布主体满足、**至少一次试跑标记 pass**。

### M3.5-D · 两个编辑台

组装台（左散素材池 / 中按 role 分槽的成员画布 / 右属性与常驻校验清单）与模板编辑器（参考视频时间轴标运动段落 + 提示词占位符 chip + 槽位声明 + 试跑样例）**各自独立路由**，不塞进五段式管理页。编辑态要三栏和常驻校验，浏览态要吸顶和批量，硬合并会把两个都做坏。

### M3.5-E · 契约失效检测

| 变化 | 必须暴露的位置 |
| --- | --- |
| 主体成员被删 | 组合列表标"不可用：主体缺 X" |
| 模板发新版本 | 引用方跟随（live），已提交任务用旧快照 |
| 参考视频被删 | 模板卡标"参考视频已失效"，可换绑 |
| 主体属性变更 | 组合仍可用，详情标"属性已更新" |

这些不是"以后再说"的健壮性优化：引用式复用的必然副产品就是契约会失效，检测漏一处，症状就是"上个月还好用，今天点生成直接报错"。

### 7.0 执行记录（M3.5-A 已完成，B ~ E 待做）

| 步骤 | 落点 | 用例 |
| --- | --- | --- |
| A 表与契约 | `modules/template/template_models.py`（三表：head / 版本快照 / 试跑）、`modules/template/slots.py`（槽位纯函数契约）、`f4c1a9e7d2b3_asset_templates.py` | `test_asset_templates.py` 15 条 |

三个决定：

1. **模板实体放在 `modules/template`，不放在 `asset`**——表名带 `asset_` 前缀说的是
   **产品归属**（它属于素材资产库），代码归属看的是"谁维护它"：角色词表、编辑器、
   发布校验都在模板模块。两者答案不同是正常的，写在这里免得下次搬来搬去。
2. **`CAPABILITIES` 下移到 `core/asset_capabilities.py`**。它原本只在
   `asset/subject_capabilities.py`，而模板的槽位声明也要用它，两个业务模块不许互相
   import（`test_modules_do_not_import_each_other`）。这是文档里写过的搬移判据
   "两个以上互不认识的模块都要用" 第一次真正触发；原位置保留 re-export，调用方零改动。
3. **槽位校验是纯函数 + 冻结 dataclass**，`generate` 强制出现在 `required_caps` 里。
   允许配一个不含 generate 的槽位，等于允许配一个"永远跑不起来"的模板，而失败会
   出现在生成流水线（那里只报"参考图读取失败"）。

**待做（B ~ E）的入口条件**：B 需要一个新的跨模块注入口——模板要问"这个类型下有哪些
已发布的可用主体"，而主体表在 `asset` 模块。现有 `core/ports/subjects.py` 只按
`pack_id + version` 取冻结视图，答不了这个问题；不能直接 import 主体模型。
建议新增 `subject-options` 注入口（`register_/reset_/has_` 三件套 + `PORT_CATALOG` 登记），
与 `subject-lookup` 并列而不是塞进同一个 Protocol——两者回答的问题不同
（"有哪些主体可用" vs "这一版由哪几张图组成"）。

顺手修掉的类型问题：`asset/seed.py` 里 `for team in teams` 与 `_ASSETS` 元组解包出的
`team`（字符串 id）撞名，mypy 因此把后者当成 `Team` 对象（报在 `team_trees.get(team)`）。
已改名为 `team_row`。

---

## 8. M4 · 语义检索

| 决策 | 内容 |
| --- | --- |
| 存储 | PG 走 `pgvector`；SQLite 走落表的 float32 BLOB + 应用层暴力 KNN（当前数据量下可接受），接口形状一致 |
| 查询形状 | **先过滤后 KNN**：`WHERE 可见域/分类/媒体类型 ORDER BY distance LIMIT k`。先取 topK 再过滤会让结果数随可见性变化，能反推出隐藏资产规模 |
| 索引对象 | 单张素材一条向量；主体聚合在查询期做 **max**（不建主体向量，也不取平均——正背面的平均是一个不存在的视角） |
| 接口 | `GET /assets/search/exact` 与 `GET /assets/search/semantic` **两个请求**，前端先渲染精确结果、再补语义分区。合成一次请求会让最慢的部分决定整体体验 |
| 补算 | `@periodic("asset.embedding.backfill")` + 队列，候选不入索引 |
| 上下文推荐 | 与编辑流程的场景/主题字段打通：输入是当前场景 + 主体类型，输出是同可见域内的素材，命中时标注"命中：侧面-02" |
| 降级 | 向量服务不可用时语义分区显示"语义检索暂不可用"，精确结果不受影响 |

---

## 9. M5 · 组织与生命周期

| 项 | 内容 |
| --- | --- |
| 待审区 | `copy_request(id, source_asset_id, target_team_id, submitter, batch_id, status, reviewer, decided_at, note)`。提交后**不进团队库**，只进待审区 |
| 批次审核 | 审核单位是批次（`batch_id`），不是单条。200 张一提交，逐条审等于逼审核人全通过 |
| 复制 | 新建 asset + 共享 blob（`ref_count+1`）+ 复制配方指针 + 可见域重置为目标库。任务引用**不跟随** |
| 提升到公共 | 改 `visibility`，新建 0 条记录，`owner` 不变 |
| `maintainer` / `origin_owner` | 存量两者都回填 `owner`；此后 `owner` 不变，维护权用 `maintainer` 转交（管理员可接管） |
| 只读态 / 能力受限态 | 出参带 `capabilities` 数组；前端据此置灰并给原因，不给"点了才报错" |
| 分桶过期 | `blob.tier` 派生 + 清理任务；`draft` 到期回收，`ref_count=0` 才物理删 |
| 主体版本发布 | 发布时冻结成员 sha256；历史版本可追溯 |
| 团队管理员 | 6 个 seed 账号（B7），四项能做 / 四项不能做按设计文档 §6.1 |

---

## 10. 前端改造路线（与批次解耦）

| 阶段 | 对应批次 | 改动 | 不动 |
| --- | --- | --- | --- |
| P1 | M1 | `SourceLibrary` 的查询参数从 category 枚举改 `categoryId`；顶栏出现库切换（只读） | 页面布局、tab 结构 |
| P2 | M2 | 五段式壳 + 分类条 + 筛选条 + 吸顶控制条 + 网格 | 详情仍是现有形态 |
| P3 | M3 | 详情浮层 + 候选确认横幅 + 衍生的三个入口 | 管理页骨架 |
| P4 | M3.5 | 抽屉形态（同一组件 `density="compact"`）+ 两个编辑台路由 | 管理页与抽屉的查询逻辑（必须共用） |
| P5 | M5 | 只读态、能力受限态、待审区入口 | — |

**文件落点**（现有结构，不新造一套）：

- `studio/src/features/archive-library/` → 拆成 `shell/`（五段式骨架）、`grid/`（卡片与网格）、`detail/`（浮层）、`filters/`
- `studio/src/entities/asset/` → 保留 api / model / ui 三层，新增 `model/category.types.ts`、`model/tag.types.ts`、`model/subject.types.ts`
- `studio/src/pages/assets/AssetsPage.tsx` → 只留外壳与 PageHeader，逻辑下沉

---

## 11. 回滚矩阵

| 批次 | 回滚动作 | 可逆 | 数据损失 |
| --- | --- | --- | --- |
| M1-A team | 停用（`status=archived`），不删表 | 是 | 无 |
| M1-B/C blob | 读路径切回旧列（一行代码），保留 blob 表 | 是 | 无 |
| M1-D category | 读路径切回旧 `category` 列 | 是 | 无（双写期） |
| M1-E media_type | 读回旧 `type` 列 | 是 | 无 |
| M1-F 配额 | 配置项切回旧的单人额度口径 | 是 | 无（数字会变，需公告） |
| **M1-G 删旧列** | **不可逆** | 否 | 靠备份 |
| M2 标签 | 停用筛选条，关联表保留 | 是 | 无 |
| M2.5 主体 | 读路径切回 pack_id | 是 | 无（双跑期） |
| M3 候选 | 停止过滤 `stage`，候选直接进列表 | 是 | 无 |
| M3.5 模板 | 下线两个编辑台，旧 pack 仍可读 | 是 | 无（新数据闲置） |
| M4 向量 | 关 `searchMode=semantic`，精确检索不受影响 | 是 | 无 |
| M5 组织 | 待审区记录保留，前端下线入口 | 是 | 无 |

---

## 12. 风险登记

| # | 风险 | 触发信号 | 处置 |
| --- | --- | --- | --- |
| R1 | blob 回填期间双写漂移 | doctor 一致性抽查 FAIL | 立刻切回读旧列，重跑回填（可重入） |
| R2 | `audio → 参考素材` 映射错 | 用户找不到音频 | 发布前人工过清单；错则补一条迁移改映射 |
| R3 | 配额数字跳变引发信任问题 | 用户报"我明明删了" | 发布说明写清三种口径差异；提供"回收站占用"独立一行 |
| R4 | PG 建索引锁表 | 部署期间 5xx | `autocommit_block` + `CONCURRENTLY` |
| R5 | 壳重写期间功能回退 | 旧 URL 404 | 旧 `?scope=` 保留为别名两个版本 |
| R6 | 主体读路径切换导致 prepare 少图 | 生成结果与上周不同 | 双跑比对，差异率 0 才切；注入口未注册时返回中性值 |
| R7 | 候选默认过滤让用户以为"东西丢了" | 生成后列表没变化 | 横幅 + 计数 + 一次性引导；候选区不做隐藏入口 |
| R8 | 两套实现（管理页 / 抽屉）漂移 | 同一筛选两种结果 | M2-C 的契约测试做成 CI 门禁 |

---

## 13. 第一个可执行任务：M1-A 的开工清单

按顺序做，每条都能独立验证。

1. `core/` 新增团队与 seed 账号常量表（3 团队 / 6 账号 id）
2. `identity` 的 seed 按常量表建 6 个账号（`seed_order=10`，必须先于 asset）
3. `asset`（或新建 `team` 模块）建 `team` / `team_member` 表 + 迁移 + `downgrade`
4. `asset` 的表建好后，`seed_order=20` 里建 3 团队与 6 条成员关系
5. 权限点 `team:read` / `team:manage` / `team:member:manage` 登记进 `ModuleSpec.permissions`
6. `GET /teams` 只读接口（顶栏用），出参只有 id / name / kind
7. `app doctor` 加"团队数与管理员账号数对账"
8. 单测：唯一约束、seed 幂等、`team:read` 不含成员明细
9. 跑闸门（契约四份 + ruff + mypy + doctor + 迁移双向 + 前端 verify）
10. 提交：一次提交只做这一件事

---

## 14. 与现有 TODO 的关系

`docs/TODO.md` 里的 T22（归档检索与批量写太弱）与 T23（上传秒传 / 断点、视频派生预热）与本计划重叠：

- **T22** 会被 M2 的筛选条与 M5 的批量操作消化掉大半，建议并入 M2/M5，不单独立项。
- **T23** 的"秒传"依赖 M1 的 blob（同 `sha256` 已有 blob 即可直接返回记录），所以它天然排在 M1-G 之后；"视频派生预热"与 M4 无关，可独立做。

其余 TODO（T2 前端展示 `externalId`、T8/T9 轮询与重试语义、T25/T26/T29/T30 架构项）与资产库改造无依赖，可并行。
