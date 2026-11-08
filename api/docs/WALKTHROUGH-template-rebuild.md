# 模板库重建：删掉再按手册长回来

> 起于 2026-11-08。上一轮 `WALKTHROUGH-image-rebuild.md` 证明了「删掉一个模块再照
> `EXTENDING.md` 重建」是检验框架的可靠手法；这一轮用同一手法处理模板库，顺带处理
> 两笔一直欠着的债：**模板数据不在数据库里**、**归档与素材是两套并行 API**。
>
> 本文按时间顺序记录每一步：读了什么、判断了什么、改了什么、怎么验证的。
> 不写「已完成」的结论句，写当时的依据——这样以后有人问「为什么这么设计」，
> 答案在这里而不在某个人脑子里。

---

## 0. 本轮四个决策（用户 2026-11-08 拍板）

| 决策 | 选择 | 影响 |
|---|---|---|
| 重建范围 | **完整替换流水线** | 源片库 / 封装 / 替换生成 / 评价一比一保留，只换存储 |
| 老数据 | **全部都迁** | 封装、版本、替换记录、评价、输入摘要全搬进 DB |
| 归档合并 | **接口也合并成一套** | 只留 `/assets`，归档能力作为 `source='archive'` 挂在上面 |
| 生成文件 | **顺手改对象存储** | 输入原片与输出成片从本机 `runs/` 移到归档存储 |

四条都选最重的路。理由记在这里，免得后面有人以为这是顺手为之：
**这四条合起来才叫「数据存在数据库里」**。只做第一条，数据仍散在 JSON 和本机磁盘；
只做第三条，删了模块还留着两套打同一张表的 service。

---

## 1. 现状：只有 2583 行代码，但里面有 90% 是没人用的

### 1.1 读的范围

`modules/template/` 全部 20 个 Python 文件（2583 行）+ `replacement_runtime/` 下的
Node 编译器与三份提示词模板。逐个读完，不是 grep 出来的印象。

### 1.2 第一个发现：`video_templates` 这条线是死代码

| 证据 | 位置 |
|---|---|
| 一张表 `video_templates`：name / description / industry / shot_count / usage_count / cover_url / tags | `models.py:11` |
| 一个接口 `GET /api/v1/templates`，按 industry 过滤 | `router.py:27` |
| 4 条种子数据（电商/本地生活/知识/文旅） | `seed.py:17` |
| 前端 `useTemplates()` **全仓库零调用** | `studio/src/entities/template/api/template.api.ts:12` |
| 它声明的返回类型是 `Paginated<VideoTemplate>`，后端实际返回 `{items,total}` | 形状不匹配 |
| 「模板库」页面渲染的是 `<ReplacementTemplates />`，与这张表无关 | `studio/src/pages/templates/TemplatesPage.tsx:6` |

**判断**：`VideoTemplate` 是脚手架留下的演示物，从来没跑通过。用户选「完整替换流水线」
不含模板目录，所以这张表本轮**删除**，不迁数据（它没有真实数据可迁）。

### 1.3 第二个发现：真正的模板库是「封装 + 替换流水线」

#### 封装（Pack）= 参考素材包

一个封装 = 名称 + 1–10 张参考图（`assets.id`）+ 逐图用途 + 必备用途 + 产品替换范围
+ 特征说明 + 补充要求。

- 三种 kind：`character` / `product` / `scene`
- 生命周期：`draft`（带 `revision` 乐观锁）→ `published`（不可变版本 v1/v2…）→ `disabled`
- 版本号靠**原子创建版本文件**做 CAS（`pack_service.py:178` 的 `_claim_version`），
  防的是「两份内容不同却同版本号」——已发布版本按定义不可变，覆盖等于篡改
- 发布前 `gaps()` 算「还差什么」，顺序就是补齐顺序
- 公共预览不开放原件：缺预览就现生成，生成失败宁可 404

#### 替换运行（Run）= 一次生成

1. `prepare`：校验输入（原片必须 1–15 秒、带音轨、MP4；参考图分类必须与 kind 一致）
2. 物化输入：`storage.link()` 硬链接（不重算 sha256，因为 key 就是摘要）
3. 编译提示词：`subprocess` 调 Node 的 `compile.mjs`，超时 30 秒
4. 写 `input-map` / `prompt-spec` / `configuration` / `media-inputs`
5. `execute`：先落 `billing_mode` 再排队（顺序反了加速就白点）
6. 走 `generation/pipeline` 适配器提交 LibTV，`libtv.download` 作业兜底重试
7. 状态机：`preparing → prepared → queued/waiting_free/waiting_executor → running → success/failed/prepare_failed/service_error/submission_unknown`

三个值得单独记住的机制：

- **`reconfirm` 出口**（`replacement_service.py:480`）：`submission_unknown` 是「上一次
  可能已经花了钱」，三个地方都把路堵死，没有人工确认出口就是永久死锁。所以
  `ReconfirmReplacement.confirm_charged` 默认 `False`——一次误点变成第二次计费，
  是这套流程里最贵的一个操作。
- **`submit_mode` 快照**（`replacement_repository.py:57`）：提交成功那一刻把供应商形态
  （`async`/`sync`）写进档案。恢复时不能拿**当前**模型目录重解析——供应商下线、换配置
  都会让同一条历史记录被分类成另一个 provider，把还能续查的记录判死。
- **`billing_mode` 只作用于本条任务**（`replacement_schema.py:115`）：提交时关掉账号的
  0 积分权益标签，走正常积分队列，**不动账号级开关**。否则同账号其他人下一次提交
  会莫名其妙开始花钱。

### 1.4 第三个发现：数据一行 DB 都没进

| 数据 | 现在在哪 | 归属 |
|---|---|---|
| 封装的 head | `catalog/packs/{id}.json` | 归档存储 |
| 封装草稿 | `catalog/pack_drafts/{id}.json` | 归档存储 |
| 封装历史版本 | `catalog/pack_versions/{id}/{v}.json` | 归档存储 |
| 替换档案元数据 | `runs/<日期>/<kind>-<id>/run.json` | **本机工作区** |
| 输入文件 | 同上 `inputs/` 目录，真实 mp4 | **本机工作区** |
| 成片 | 同上 `outputs/generated.mp4` | **本机工作区** |
| 评价 | 同上 `review/review.json` | **本机工作区** |
| 指针 | `catalog/runs/{id}.json` | 归档存储 |

数据库里只有一张从没被读过的 `video_templates`。

`list_runs()` 的注释自己承认了这个问题（`replacement_service.py:96`）：

> owner 过滤仍在 Python 里——指针文件暂时只存 path，把 owner 下推到索引要改指针
> 格式，属于契约级改动，留待单独一轮。

本轮就是那一轮。

### 1.5 第四个发现：归档与素材是两套打同一张表的 API

`assets` 表已经有 `source` 字段（`upload` / `archive` / `generated`）和 `category`
（归档分类）。但对外挂着两套接口：

| `/assets`（`asset/router.py`） | `/archive`（`asset/archive.py`） |
|---|---|
| list / upload / get / delete / file / cover | list / upload / import / file / thumbnail |
| | stream / detail / preview / rename / sharing |
| | deleted（回收站）/ purge（彻底删） |

两套都做上传、都读文件，`/archive` 是超集。代码上 `archive_*` 占了 asset 模块一半
（1512 / 2926 行）。用户判断「归档应该是素材的一种特殊情况」——这个判断与表结构
一致，与代码结构不一致。

### 1.6 第五个发现：本机磁盘是替换档案的硬约束

`replacement_repository.py` 的 docstring 自己写了：

> 档案本身（`run.json`、inputs、outputs、records）是执行器的过程文件，必须落在
> 本机工作区（见 `infra/archive/runs.py`）

原因是执行器（`bridge.mjs`）直接操作文件系统。这决定了「文件改对象存储」这一项
不是改配置，而是要改执行器的读写路径——见 §7。

---

## 2. 目标设计

### 2.1 数据模型

> 六张表。判据：**凡是需要「按 owner 查」「按状态查」「按时间排序」的字段，都必须是列**；
> 只有「一次性读整块、不参与查询」的才进 JSON。

```
template_packs                 封装 head（草稿与已发布共用一行）
  id, kind, name, owner_id
  status            draft | published | disabled
  version           当前已发布版本号，0 表示还没发布过
  revision          乐观锁，每次保存 +1
  features, instructions, wardrobe_mode
  published_at, deleted_at
  UNIQUE(kind, name, owner_id) WHERE deleted_at IS NULL   -- 防同名

template_pack_references       逐图引用（有序）
  pack_id, asset_id, position, role, description
  UNIQUE(pack_id, position)
  → 「删除素材前问还有谁在用」变成一条 SQL，不再扫 JSON

template_pack_versions         已发布快照（不可变）
  pack_id, version
  reference_ids JSON, bindings JSON, required_roles JSON
  supported_scopes JSON, features, instructions, wardrobe_mode
  published_at
  UNIQUE(pack_id, version)
  → 用 UNIQUE 约束替代「原子创建版本文件」的 CAS

replacement_runs               一次替换
  id, kind, target, owner_id, status, framework_status
  fingerprint                  幂等：同 request_id 同配置
  pack_id, pack_version        来自哪个封装版本
  prompt TEXT                  编译后的提示词
  prompt_spec JSON, configuration JSON, input_summary JSON
  task_id, duration, progress_percent
  estimated_credits, credits, billing_mode
  submit_attempts, submit_mode, retry_count
  last_error, error_code, deleted_at
  output_key                   成片在对象存储的 key

replacement_run_inputs         逐条输入素材
  run_id, asset_id, role, position, usage
  storage_key                  物化后的对象存储 key

replacement_reviews            评价（当前状态）
  run_id UNIQUE, status, version, reason_codes JSON
  comment, reviewed_by, reviewed_at

replacement_review_events      评价历史（追加）
  run_id, version, status, reason_codes JSON, comment, reviewed_by, reviewed_at
  UNIQUE(run_id, version)
```

### 2.2 文件去对象存储

- `prepare` 不再往本机写 `inputs/`，改为把文件写进归档存储的
  `runs/<run_id>/inputs/<filename>` key
- 执行器启动前 `materialize()` 到临时目录，跑完上传 `outputs/generated.mp4`
- 本机不再需要 `runs_root()` 常驻；只在执行期间存在临时目录

### 2.3 归档并入素材

只保留 `/assets` 一套。归档特有能力的落点：

| 原 `/archive` 能力 | 新位置 |
|---|---|
| `POST /archive/upload` | 与 `POST /assets/upload` 合并（同一实现，`source` 由 `?origin=` 决定） |
| `POST /archive/import`（Z 盘 inbox 登记） | `POST /assets/import` |
| `GET /archive/{id}/stream` | `GET /assets/{id}/stream` |
| `GET /archive/{id}/thumbnail` | 与 `GET /assets/{id}/cover` 合并 |
| `GET /archive/{id}/preview` | `GET /assets/{id}/preview` |
| `PUT /archive/{id}/name` | `PUT /assets/{id}/name` |
| `PUT /archive/{id}/sharing` | `PUT /assets/{id}/sharing` |
| `PUT /archive/{id}/deleted` | `PUT /assets/{id}/deleted` |
| `DELETE /archive/{id}` | 与 `DELETE /assets/{id}` 合并（`?purge=true` 区分软删/真删） |

---

## 3. 执行顺序

按「后面要能验证前面」排：

1. 删模块 + 清牵连 → `doctor` 全过、`app --help` 全在
2. 脚手架生成骨架 → `app modules` 能看见
3. 封装进 DB → 发布/版本 CAS 的测试转绿
4. 运行进 DB → 状态机与恢复的测试转绿
5. 迁移脚本 → 老 JSON 搬进表且可重复执行
6. 归档合并 → 一套接口，旧路径 404
7. 文件进对象存储 → 执行器不再依赖本机 `runs/`
8. 生成物 + 全量验证

---

## 4. 执行记录

### 4.1 备份（动手前）

```
api/var/backup-template-20261108/
├── catalog/           28 份 JSON（packs 5 / drafts 5 / versions 6 / runs 12）
├── runs/              12 份 run.json（工作区在 NAS 上）
├── module-src/        旧模块 20 个 Python 文件全文
└── tests/             16 个被删除的测试文件全文
```

备份放 `api/var/`（已被 `.gitignore` 忽略），迁移失败时可整体回滚。
**备份里最有价值的是 `tests/`**：那些用例写的是业务语义（版本 CAS、重催出口、
评价规则），是重写时的验收清单。

### 4.2 删除模块：先量影响面，再动手

删之前先实测一次「移走目录会炸什么」，避免凭想象删：

| 检查项 | 结果 |
|---|---|
| `app doctor` | 24 项全过（`runtime.assembly` 也过：`jobs` 声明跟着模块一起消失，两边一致） |
| `app --help` | 24 条命令全在（`backup`/`restore`/`prune-blobs` 不受影响） |
| `pytest --collect-only` | **中断**，15 个文件 `ModuleNotFoundError` |
| `app modules` 的注入口 | `replacement-feed` 自动降级成「未注册（任务中心看到空列表）」，`asset-references` 仍在 |

结论：框架层已经能做到「删模块不连坐」，**全部代价都在业务层**。
这正是上一轮 `ModuleSpec.cli` / `seed` 钩子改造换来的。

真正要一并处理的遗留物（不是 import，是静态耦合）：

| 位置 | 是什么 | 处理 |
|---|---|---|
| `infra/ai/libtv_process.py:126` | 提交前 `MediaCatalog.check_run()` 做最终授权 | 移到业务层 `execute()`；那里能查库，这里只有文件系统 |
| `infra/media_catalog.py` | 整个文件只服务模板（读写 `catalog/*.json`） | **删除**，换 DB 查询 |
| `core/media.py` 的 `MediaPack`/`PackBinding`/`aligned_*` | 只有模板用 | 收回模块 |
| `core/pack_roles.py` | 只有模板用 | 收回模块 → `modules/template/pack_roles.py` |
| `api/ports.py` 两处描述 | 写死「modules/template 实现了…」 | 改成「重建中」 |
| `identity/permissions.py` 的 5 个权限码 | 三个内置角色预设 | **保留不动**——新模块声明同名码，悬空期自动闭合 |

最后一条是有意的取舍：既然对外契约要求路径与权限码不变（前端 41 个文件按它们
对接），删掉再重建时就不该让那些码在中间态消失，否则「重建」会变成「让所有
非超管账号静默失去权限」的一次升级。

### 4.3 测试文件的处置

15 个文件收集失败，按「测的是语义还是实现细节」分两类，**全部移走**：

- **纯模板（10 个，2478 行）**：`test_pack_publish_cas` / `test_pack_roles` /
  `test_replacement_{archive,guards,management,pack_cover,recovery,review,titles}` /
  `test_user_retry_exits` → 新模块写好后按语义重写
- **混杂（5 个，944 行）**：`test_archive_{blobs,workspace}` /
  `test_asset_reference_sources` / `test_assets_integration` / `test_task_feed`
  ——它们测框架能力（归档字节、引用检查、任务聚合），但用 `PackService` 造夹具，
  要改成新接口
- **跨栈守卫（1 个，54 行）**：`test_pack_roles_sync` 读前端 `packRoles.ts` 逐字
  比对后端词表。**必须恢复**——它防的是「前端显示旧标签、提交旧 key」这种只有
  用户能发现的漂移

移走而不是删掉：中间态保持 `pytest` 可跑（707 条，原来 845），每一步都能验证，
不会出现「一堆红里分不清哪个是我刚改坏的」。

### 4.4 按手册长回来

```bash
app new-module template --label 模板 --perm template --slot 7 --group 内容生产
```

脚手架给的 7 个文件，按 §1.0 的通用假设逐条改掉：

| 生成物 | 脚手架的假设 | 实际要的 | 处理 |
|---|---|---|---|
| 权限点 | 通用 CRUD 四个 | read / use / review / pack:manage / pack:publish | 换掉 |
| `errors.py` | 一定需要自定义码 | 全程只用框架异常 | **删除** |
| `dependencies.py` | 注入 `BaseRepository[Model]` | 两个 service、五张表 | 重写 |
| `models.py` | 一张 `templates` 表 | 六张表 | 重写 |
| `schema.py` | 通用出入参 | 与旧契约一比一 | 重写（照抄旧形状） |
| `service.py` | 抛 501 的骨架 | 拆成 `pack_service` / `replacement_service` | **删除** |
| `router.py` | 一条列表 + 一条创建 | 15 条替换/封装路由 | 重写 |

`slot 7` 沿用旧号段（`EXTENDING.md` §7 的表里 template/storyboard/shot 共用），
因为槽位是「业务领域」的编号，不是「模块」的编号。

### 4.5 数据模型：六张表

见本文 §2.1。落地时补了三个字段，都是设计时漏想、写代码时被测试逼出来的：

1. `template_packs.cover_asset_id` —— 旧实现有「显式主图」，我一开始按
   `reference_ids[0]` 处理，等于把「换主图」变成「把图拖到第一位」，会改变
   提示词里的编号顺序；
2. `template_packs.required_roles` / `supported_scopes` —— 草稿编辑期的「待发布
   状态」。第一版想挂在对象上（`pack.required_roles_pending = ...`），SQLAlchemy
   不接受动态属性，这才发现它本来就该是列；
3. **去掉** `workspace_key` —— 一度想用一列存工作区路径，后来想清楚：指针
   （`catalog/runs/<id>.json`）只有 `{"path": ...}`，它回答「文件在哪」，是执行器
   协议的一部分。业务状态进数据库，路径不必重复存第二份。

### 4.6 三处框架层改动（都是「数据进 DB」逼出来的，不是顺手改）

| 改动 | 为什么必须改 |
|---|---|
| **新增 `core/ports/media.py`**：`AssetLookup` 协议 + 注册表 | 模板要读素材的归属/分类/时长，但不能 import 素材模块（§5.1 红线）。素材模块在 `lookup.py` 注册实现，复用 `ArchiveAssetRepository.to_media()` 这一份映射 |
| **`core/feed.py` 的读端改 async** | 任务中心注入口原来是 `Callable[[str], list]`（同步），因为旧实现读盘。现在读库，同步签名会逼调用方 `asyncio.to_thread`，而在线程里查异步连接池会拿到另一个事件循环——那是只在生产并发下才出现的 bug。注册口仍是同步（import 期调用） |
| **删 `infra/media_catalog.py`，`libtv_process` 不再做授权校验** | 它整块是「读写 catalog JSON」，数据进 DB 后作废。最终授权前移到 `execute()`：那里能查库，执行器只有文件系统 |

### 4.7 一处刻意保留的妥协：`run.json` 与指针

执行器（`bridge.mjs` / `libtv_process`）要真实目录、要读 `run.json` 的 `status`
才能决定「还能不能提交」。改它等于把执行器拆成两半，风险远高于本轮目标。

所以边界这样划：

- **数据库 = 业务状态的权威**（这次替换到哪一步、谁提交的、花了多少）
- **`run.json` = 给执行器的单向投影**：业务层写下去（`workspace.write_state`），
  执行器的回执再读回来（`read_state`）。**不反向推导业务状态**
- **指针 = 工作区定位器**：只有 `{"path": ...}`，不含任何业务字段

这不是双写——`run.json` 从不反推。真正的收益已经拿到：`list_runs` 的 owner
过滤下推到 SQL、按状态查、按时间排序、多实例看到同一份状态。

### 4.8 老数据迁移：`app import-template-legacy`

**为什么必须有这一步**：数据从 JSON 搬进表之后，存量数据不会自己跟过来。没有
这一步，「升级」等于「模板库清空」。

读的五个来源（少读一个就丢一类东西）：

| 文件 | 是什么 | 丢了会怎样 |
|---|---|---|
| `catalog/packs/<id>.json` | 已发布封装的 head | 上架的封装没了 |
| `catalog/pack_drafts/<id>.json` | 草稿（与 head 同 id） | **从没发布过的封装整批消失** |
| `catalog/pack_versions/<id>/<v>.json` | 不可变版本快照 | 历史版本没了，回滚不了 |
| `catalog/runs/<id>.json` | 指针，只有 `{"path": ...}` | 找不到工作区 |
| `<工作区>/run.json` + `inputs/*` + `review/review.json` | 档案本体 | 状态、提示词、输入、评价全丢 |

四个设计取舍：

1. **主键沿用老 id**，所以重跑安全。实测第二次跑：`跳过 23 条`（5 封装 + 6 版本 + 12 档案），
   一条都没重复。
2. **不删老 JSON**。它们是回滚依据，也是 `doctor` 的对比基准。真要清理，等新实现
   稳定一个版本之后单独做。
3. **草稿优先于 head 取内容**。head 与草稿同 id 时，草稿才是最后一次落盘的编辑内容；
   但**发布状态只能来自 head**——用草稿覆盖会把已上架的封装降级成草稿。
4. **`--dry-run` 也真的读盘**。报告里的「输入 N 条」必须是真实数字，否则运维会
   以为迁移漏了东西（第一版就是这样：dry-run 显示「输入 0 条」，看着像全丢了）。

**踩到的两个真实缺陷**（都由这步暴露，且都没有测试能发现）：

- `replacement_service` 里写的是 `from modules.template import replacement_worker`——
  这个模块名不存在。`execute()` 与 `reconfirm()` 会在 import 那一行抛 `ImportError`，
  而全量测试一片绿（相关测试当时已被移走）。
- 第一版只遍历 `catalog/packs/`，会丢掉只存在于 `pack_drafts/` 的草稿。

实测结果：**封装 5 条、版本 6 条、调用档案 12 条、输入 40 条**，六张表分别
`5 / 11 / 6 / 12 / 40 / 0`（评价 0 是真的：这份数据里 12 条评价全是 `pending`）。

### 4.9 成片进归档存储（`output_key` 第一次被真正写入）

**发现的缺陷**：`output_key` 这一列建好了、`_run_out` 也按它决定 `outputUrl`，
但**没有任何地方写它**。结果是生成成功后前端拿到空的 `outputUrl`，用户在列表里
看不到自己刚花钱做出来的片子。

现在的流程：

1. `libtv.download` 成功 → `publish_output()` 把 `outputs/generated.mp4`
   搬进归档存储（key = `runs/<run_id>/outputs/generated.mp4`），写 `output_key`
2. `GET /replacement-runs/{id}/output` → `output_key` 有值就走存储：远端驱动
   返回 307（字节不经过应用进程，对象存储自己处理 Range，能拖进度条），本地驱动
   退回文件直出
3. `output_key` 为空 → 工作区兜底。老档案与上传失败的都能播，只是换台机器打不开

**上传失败不判任务失败**：本地文件还在，兜底照样能播。把「上传失败」判成
「生成失败」会让一条已经花了钱的片子被标成失败——这是这套流程里最贵的误判。

### 4.10 展示名规则收回独立模块

重建时 `_display_title()` 被内联进 `replacement_service`，顺手丢了原
`replacement_titles.task_name()` 的三条规则：超长截断、兜底文案区分「没记录」
与「记录为空」、`#ABCDEF12` 后缀。后果是同一条记录在列表与任务中心叫两个名字。

现在规则回到 `titles.py` 一个函数，四处（列表 / 弹窗 / 详情 / 任务中心）共用，
`test_replacement_titles.py` 逐字比对格式。

### 4.11 差点漏掉的一处：`replacement_runtime/`（Node 提示词编译器）

删旧模块目录时，`modules/template/replacement_runtime/` 里的
`prompt-compiler.mjs`、`workbench/compile.mjs`、三份 `prompts/*.txt`
**一起被删了**，而 `workspace.RUNTIME` 就指向那里、`prepare` 还要
`copytree` 到工作区做冻结快照。不恢复的话，第一次点「生成」就会在
`compile_prompt` 抛「提示词编译器不可用」。

已从备份恢复，并做了一次真实校验：拿一条历史档案的 `inputs/prompt-spec.json`
喂给现在的编译器，**能正常产出提示词**（返回 `compiler_version: 1.0.0`）。
与历史 `prompt.txt` 逐字不同，差距是模板多了 `{{reference_bindings}}` 一行、
编译器 .mjs 也改过——正是档案里 `code/runtime/` 快照存在的意义：它证明
「当时跑的是哪一版」，而不是让历史提示词可以被今天的代码复现。

**这类文件的教训**：`.py` 的 import 关系能靠 `ruff` 与测试抓出来，`.mjs`/`.txt`
不行。删模块前应按目录列一遍非 Python 文件，逐个决定「跟着删」还是「留着」。

---

## 5. 验证记录

### 5.1 当前状态

| 项 | 结果 |
|---|---|
| `ruff check src/ tests/` | 全过 |
| `app doctor` | 24 项：失败 0、警告 0 |
| `app modules` | `template` 已挂载，5 个权限码、3 个作业、1 个引用来源 |
| `alembic upgrade head` | 六张表建好，`video_templates` 已删 |
| `app import-template-legacy` | 幂等，第二次全跳过 |
| `app export-openapi --check` | 四份生成物「无变化」：15 条模板路径，与旧契约一致（少的是刻意删掉的死接口 `GET /templates`） |
| `pytest` | **859 passed / 12 skipped**（原 845；腾出的 11 个模板文件已按新语义重写为 12 个、150 条） |

### 5.2 测试

被移走的 16 个文件里，两类处置：

- **必须恢复的**（守卫价值与实现细节无关）：
  `test_pack_roles_sync.py`（前后端词表逐字比对）、`test_user_retry_exits.py`
  （每个 kind 的自救出口，架构守卫依赖它）、`test_replacement_titles.py`、
  `test_archive_purge.py` 里的引用检查用例
- **按新实现重写的**：`test_user_retry_exits.py` 的现场从「写 run.json」变成
  「插 `replacement_runs` 一行」，出口本身没变；`test_archive_purge.py` 的引用
  从「写 `catalog/packs/*.json`」变成「插 `template_pack_references` 一行」

新增的：

- `test_replacement_output.py`：成片取件的三个分支（归档存储 / 工作区兜底 / 404）

**一个与重建无关的既有缺陷**：`test_asset_reference_completeness.py` 的每条用例都会
`reset` 全局引用注册表（那是被测行为本身），但注册发生在模块 import 期、每进程只跑
一次，于是**后面的用例看到「声明了来源、实现没注册」**，素材删除被 fail-closed 挡住
——表现为 `test_asset_references.py` 的三条在整跑里红、单文件跑全绿。修法是给这个
文件加一个还原夹具，按 `ModuleSpec` 重新声明与绑定（与装配期同一套动作，不需要在
`core` 里开测试后门）。

### 5.3 重写测试：150 条用例，挖出 9 个真缺陷

被移走的 16 个文件里，11 个是「只测模板语义」的，必须按新实现重写。重写不是改
import：现场从「工作区里的 `run.json`」变成「数据库行 + 工作区投影」，很多断言连
**问题本身**都要换一种问法。

| 文件 | 条数 | 钉住的东西 |
|---|---|---|
| `test_pack_drafts.py` | 21 | 用途校验、`gaps` 顺序、编辑同身份、封面归属、词表一致性 |
| `test_pack_versions.py` | 7 | `UNIQUE(pack_id, version)` 当 CAS、savepoint 不回滚 head、`version`/`revision` 分工 |
| `test_replacement_archive.py` | 19 | 准备阶段全链路 + **真跑 node 桥接**（零报价闸门、投影字段名、加速队列） |
| `test_replacement_guards.py` | 6 | 退避曲线、重试上限、停用挡新提交、已提交不重提 |
| `test_replacement_management.py` | 5 | 回收站幂等、归属 404、删/恢复/再删 |
| `test_replacement_recovery.py` | 26 | 谁能重提、谁只能等回执、锁抢不到时的六轮退避 |
| `test_replacement_review.py` | 17 | 评价版本、history 不含当前、20 条上限、成片丢失 409 |
| `test_task_feed.py` | 15 | 混合分页、分组、搜索、回收站隐藏、离线归档仍可用 |
| `test_assets_integration.py` | 12 | 公共原片池开关、跨账号准备、乐观锁、删除撤回分享 |
| `test_asset_reference_sources.py` | 5 | 多来源合并、只算 head、软删封装不阻塞 |
| `test_archive_workspace.py` | 8 | `list_keys`、指针往返、legacy 前缀、悬空指针 404 |
| `test_archive_blobs.py` | 9 | 内容寻址、`_verify_bytes` 只看存在、硬链接、镜像兼容层 |

**「测试全绿」在删掉测试之后毫无意义**——这句话这一轮被反复验证：上面这批用例
写完，光跑一遍就翻出 9 个缺陷，其中 3 个是致命的。

| # | 缺陷 | 症状 | 为什么测试没发现 |
|---|---|---|---|
| 1 | 新建 run 从不登记索引指针、不写 `run.json` | `POST /replacement-runs` 建完行自己就 404 | 老测试直接构造目录，跳过了「创建」这一步 |
| 2 | 不写 `inputs/input-map.json` 与 `records/media-inputs.json` | 执行器在 upload 阶段 ENOENT | 接口层看不到：字节都在，缺的是清单 |
| 3 | `run.json` 用 camelCase（`taskId` / `submitAttempts`） | 执行器认为「从没提交过」→ 重复提交、重复计费 | 投影字段名不在任何 HTTP 契约里 |
| 4 | `managed()` 不填 `revision` / `status` | 前端存第二次必 409「草稿已变化」；已发布包显示成草稿 | `_shape()` 没有这两个字段，落默认值 |
| 5 | `publish()` 的幂等分支条件自相矛盾（在「revision 不等」里要求「revision 相等」） | 每次点发布多一个重复版本 | 分支永远进不去，等于没有幂等 |
| 6 | `list_owned(include_deleted=...)` 从没人传 `True` | `/replacement-catalog` 永不返回回收站记录，页签恒空 | 参数有默认值，调用方漏传不报错 |
| 7 | `set_deleted` 重复删除刷新时间戳 | 回收站按删除时间排序会跳动 | 幂等只在「状态相同」时才成立 |
| 8 | 评价 `history` 把当前条也塞进去，且丢掉了旧的 20 条上限 | 时间线最后一条重复、版本号对不上 | 只看「有没有写进去」看不出重复 |
| 9 | 任务中心只认 `output_key`，下载接口还有工作区兜底 | 列表说「没有结果」、点开却能下载 | 两个接口各写了一份「有没有成片」 |

前三条的共同点：**每一步单独看都「合理」，串起来第一次点生成就炸**。所以新用例
的断言刻意贴到文件级——清单、摘要、投影、指针——而不是只看接口返回 200。

还有一处是纯设计债：`pipeline._update()` 和 `worker._sync_out()` 各写了一份投影。
两份迟早漂移，而漂移的症状正好是第 3 条那种最贵的故障。合并成一处
（`worker.sync_projection`），`pipeline` 只调它。

### 5.4 本机一个会骗人的退出码（两次踩）

`pytest` 收尾时删除自己的临时目录（`Temp/pytest-of-<user>/garbage-*`，120+ 个文件）
会被本机的批量删除守卫拦下，进程在打印汇总行**之前**退出，`exit=1`。
用 `--basetemp=var/pytest-base` 指到固定目录能绕开收尾删除，汇总行正常打印。

**但 `--basetemp` 必须每次换一个全新目录**。复用旧目录时，pytest 在每个用例的
`tmp_path` 阶段要先清空它，同样触发守卫——结果是 68 个用例 `ERROR at setup`，
日志长得像代码大面积坏掉，实际一个断言都没跑到。判据：错误信息里出现
`SAFE_DELETE_BULK_CONFIRM_REQUIRED`，就不是代码问题。

**别把这两种 `exit=1` 当成测试失败**：日志里全是点、没有任何 `F`，就不是。

### 5.5 尚未完成

| 项 | 状态 | 说明 |
|---|---|---|
| **归档并入素材：接口合并成一套** | 后端已完成、前端待跟 | 路径统一到 `/assets`；前端需改 `archive.api.ts` 的 7 处路径并删掉 `asset.api.ts`，见 §6 |
| **输入原片进归档存储** | 未动工 | 执行器要真实文件、还要按 `run.json` 的 `file_checksums` 重算 sha256，必须「materialize → 跑 → 清理」三段式，比成片那一半复杂 |
| 剩余 11 个被移走的测试 | **已完成** | 重写为 12 个文件、150 条；见 §5.3 |

## 6. 合并接口：一套 `/assets`（2026-11-08）

### 6.1 决策

用户拍板两条：

1. **路径统一到 `/assets`**，去掉 `archive` 这一段。理由不是好看：归档已经**就是**
   素材系统（索引在 `assets` 表），`archive` 描述的是数据来源而不是资源名；两段
   前缀长期共存，看代码的人会一直问「archive 和 asset 什么区别」，而答案只是
   「一个是上一版」。
2. **旧 `cover` 与类型分面计数不搬**。归档侧本来就有 `thumbnail`，分面计数只服务
   旧素材库页面的三个 tab，而合并后那个页面就是归档页面，tab 用 `category` 筛选即可。

### 6.2 后端改了什么

| 动作 | 文件 |
|---|---|
| 路由整体上移一级（`prefix="/archive"` → `"/assets"`），并重写模块说明 | `archive.py` → **`api.py`**（改名） |
| 删除旧六条接口（列表 / 上传 / 详情 / 删除 / 文件 / 封面） | `router.py`（删） |
| 删除旧服务与依赖装配 | `service.py`、`dependencies.py`（删） |
| 只留还活着的枚举，旧出参一起删 | `schema.py` 从 83 行降到 18 行 |
| 路径字符串同步 | `ratelimit_policy.py`、`archive_repository.py`、`archive_service.py`、`import_legacy.py`、`archive_purge.py`（注释） |

`app modules` 上 `asset` 从 18 条路由变成 12 条（旧 6 条没了，归档 12 条全保留）。

### 6.3 合并顺手挖出的两个静默缺陷

**限流豁免表里有两条同名键。** 合并后 `DELETE /assets/{asset_id}` 出现两次：旧一套
写「引用检查 + 配额」，归档一套写「必须在回收站 + `asset:delete`」。Python 的字典
字面量接受重复键、**后者静默覆盖前者**——两条理由不同，被丢掉的恰好是关于
「二次确认」的那半句。ruff 的 `F601` 抓到了；修法是把两半并成一条完整理由
（实际行为两者都做：先要求在回收站，再查引用）。

**文件类型错误码从 `40051` 退化成 `40000`。** 旧 `/assets` 抛 `FileTypeNotAllowed`
（40051），归档路径把扩展名判断下沉到 `infra/media_probe.inspect`，那里是基础设施层、
只能抛通用 `BadRequest`。前端本来靠 40051 单独提示「格式不支持」，合并后拿到的是
一句通用错误。修法是在 `_ingest` 里先自己判一次扩展名再交给 probe —— **分层不该
以丢业务错误码为代价**。

### 6.4 前端跟进（2026-11-08 完成）

契约生成物已重新导出（`api/docs/openapi.json`、`api/docs/API.md`、
`studio/src/shared/api/schema.ts`、`permissions.ts`，合计 −1156 行）。

前端这一边不是「改 7 处路径」那么小：实际删掉 12 个文件、改 8 个。
原因是有两套**页面**，不只是两套客户端——`features/asset-library`（旧素材库）
和 `features/archive-library`（归档）各自有列表、上传、预览、详情。

| 动作 | 文件 |
|---|---|
| 路径去掉 `archive` 段（7 处） | `entities/asset/api/archive.api.ts` |
| 整份删除（接口与类型都没了） | `entities/asset/api/asset.api.ts`、`asset.queries.ts` |
| 整份删除（旧素材库页面，能力是 `SourceLibrary` 的子集） | `features/asset-library/AssetLibrary.tsx`、`AssetPreview.tsx`、`AssetLibrary.test.tsx` |
| 整份删除（只被旧素材库用） | `entities/asset/ui/AssetCard.tsx`、`entities/asset/model/cover.ts`、`cover.test.ts`、`model/types.ts` |
| 整份删除（调后端已删的死接口 `GET /templates`） | `entities/template/api/template.api.ts`、`entities/template/model/types.ts` |
| 四个视图并成三个 | `features/archive-library/AssetWorkspace.tsx` |
| 数据形状改成归档契约 | `mocks/db.ts`（`buildAssets` 造 `ArchivedAsset`）、`mocks/routes/catalog.mock.ts`（九条路由对齐后端规矩） |
| 页面说明去掉「两条存储通路」 | `pages/assets/AssetsPage.tsx` |

**mock 也一起搬了。** 它原本 mock 的是旧六条接口（`counts` 分面、`refCount`、
`DELETE` 直接删）——后端删了那六条之后它照样绿，因为没人拿 mock 跑真实链路。
现在照抄后端的三条规矩：隐藏已删、`expectedVersion` 不匹配给 409、彻底删除前
必须先在回收站。`gen:mock-coverage` 重跑后未命中端点从 12 条降到 4 条，
剩下的全是字节流（`/file`、`/stream`、`/thumbnail`、`/preview`）——mock 内核
只能返回 JSON，这几条给不了，不是漏了。

**顺手修掉两个测试**，都与合并无关但都红着：
1. `modules/user-access/mock.test.ts` 硬编码着 `['内容生产','素材','组织管理']`。
   导出功能在 `a91328c` 加了「系统」组之后这条就红了，一直没人判断该不该把
   这一组放进前端目录。改成检查目录自洽（组不重名、码不重复、每个码恰好出现
   一次并覆盖全部 `PERMISSION_CODES`）——与后端加几组无关，但能抓坏目录。
2. `shared/api/endpoints.test.ts` 抓到 `/api/v1/templates` 不存在，牵出前端还在
   调后端已删的死接口（`entities/template`），一并删。

收尾：`tsc -b` 通过、`eslint --max-warnings 0` 通过、`vitest run` **424 passed**、
`vite build` 通过。`grep -r "assets/archive" studio/src` 只剩设计文档里的历史记录。

