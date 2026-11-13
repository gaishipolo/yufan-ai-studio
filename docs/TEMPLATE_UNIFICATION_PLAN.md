# 模板库统一化改造计划

> 2026-11-12（**复核基线：已合并 PR #7，`b9dd24d`；开工前必读 §14**）。
> 范围：`api/src/modules/template/`（全仓第二大模块，约 30 文件 / 约 6900 行）
> + `replacement_runtime/`（Node 编译器）、`infra/ai/libtv_runtime/`（执行器快照）、
> `api/ratelimit_policy.py`、`studio/` 契约侧。
> 目标：把「自己写的一套」收敛回框架已有的能力——数据库、存储、权限、资产库、错误码。
> **本文只做计划，不含代码改动。**
>
> **第二轮修订**：用户要求「再看全一点，不止我说的那些」。§1–§9 是五个点名维度
> （框架/数据库/S3/权限/资产库）的结论；**§10 起是跨维度全面审计**，
> 覆盖提示词编译链、运行时快照、异常吞噬、可观测性、部署校验、测试真实性、
> 限流策略、契约出参。§11/§12/§13 是修订后的执行顺序、新增决策与验收标准。
>
> **第三轮修订（2026-11-12 晚，用户拍板「不用从头开始做，保留 v1 的计划并完善」）**：
> 路线定为 **保留 v1 就地改造，不新建 v2 模块**。§1–§14 的结论与六步顺序全部保留；
> **§15 起是第三轮补充**：路线决策、八条链路拆解、三条能力归属与六组自建副本、
> 新增工作项到 §11 六步的映射、硬约束与待拍板。新增工作项**并入原六步**
> （只插入一个「步 0.5」成片闭环），不另起并行计划。
>
> **第四轮修订（2026-11-13 用户确认「用到素材库 v1 的部分也需要迁移到 v2」）**：
> 核查结论是模板库**主链路大量使用 v1 `assets`**（约 25 文件 / 60+ 处），
> `core/ports/library_lookup.py:5` 甚至把「模板库 prepare 用它（旧素材库）」写进了端口注释。
> 因此 §19.7 原「输入侧不迁 v2」口径**作废**，改按新增的 **§21** 执行；
> §15.2 一句话口径、§16 链路 A、§17.1、§18 工作项同步更新，新增「步 2.5」。
>
> **第五轮修订（2026-11-13 用户给出「模板封装链路」产品口径）**：
> `category` 不是素材的固有属性，分类应由**封装时的一次绑定**表达。
> 目标是一个模板封装页面/实体，一次定义「参考视频 + 固定提示词 + 四类素材绑定」；
> 角色/场景/产品按 **v2 标签**筛候选、音频按 **`kind=audio`** 筛候选，选中后冻 id。
> 由此 §21.6 第 2 项**已拍板**：不做字段映射，删除素材侧分类判据。详见 **§22**。
> 同轮第二轮澄清：封装是**两段式**——封装者只声明槽位，调用者在 `prepare`
> 显式选具体产品并冻 id；这不是「按标签运行时解析」，§22.4 据此精确化。
>
> **第六轮修订（2026-11-13 用户拍板 §21.6 剩余三项并补充一条新链路）**：
> 第 1 项「模板库优化完成后，若没有其他地方用到 v1 素材库就直接下线」、
> 第 3 项「v2 删除保护补到完整守卫」、第 4 项「前端先保留旧 UI」均已定；
> 新链路是「资产库的**封装**（把同一产品的三视图、细节图打成包）也能被模板
> 调用者选作参考」。核查发现 **v1 仍有 4 类使用方**（含 `modules/library` 自己的
> legacy 链路），因此下线是「先清理再下线」的独立步 6，不是模板迁完自动发生。
> 详见 **§23**；§18 新增 W10 / W11，§21.3/§21.5/§21.6、§22.5/§22.6 同步收口。

---

## 0. 一句话结论

「模板库整个模块都是自己写的」这个判断**不成立**：`ModuleSpec`、权限点、生成流水线、
封装六张表、资产库端口都已经接好了。真正绕开框架的是**四块**，其中两块是硬伤：

| # | 问题 | 严重度 | 性质 |
|---|---|---|---|
| A | `video_template_store.py` 用 JSON 文件当数据库 | **P0** | 前提已失效的临时妥协 |
| B | 视频模板**零用户归属**、**零资产归属校验** | **P0** | 权限缺口 |
| C | `seed.py` 裸 SQL 查 `assets` 表 | P1 | 端口能力缺失 |
| D | 三个本地目录 + `runs_root()` 本地回落 | P1 | 存储语义不清 |

另有三处「结构性但不属于绕开」：两个超千行文件、错误码没落地、
`template_packager` 长在 `src/` 顶层。

### 0.1 第二轮新增（§10 的摘要）

第一版只看用户点名的五个维度，等于按图索骥。按全维度重扫后，**新增 6 个值得进计划的问题**，
其中三个是「不报错、但事后说不清」那一类：

| # | 问题 | 严重度 | 维度 |
|---|---|---|---|
| E | 提示词经 Node→Python 三层后处理，`compilation.json` 的哈希对不上实际发出的 prompt | **P1** | 可审计性 |
| F | Python 用字符串 `.replace()` 给 Node 编译器打补丁，模板一改就静默失效 | P2 | 跨语言耦合 |
| G | 冻结快照存的是模块版编译器，执行器读的是 vendor 版；档案里没记 vendor 版本 | P2 | 证据链 |
| H | **编译器缺独立 golden 断言**——决定已付费任务发出什么文案，却没有一条用例会因为它改了一个字而变红 | **P1** | 测试真实性 |
| I | `prepare` 的限流豁免理由写「只写元数据」，实际是全模块最重的 IO 端点 | **P1** | 策略腐烂 |
| J | 归档/投影/导入三处静默降级无指标；全模块只有 1 个 counter | **P1** | 可观测性 |
| K | 模板库没有 `doctor` 钩子；Node 版本、vendor 哈希、工作区可写性都不自检 | P2 | 部署校验 |
| L | TODO T10（HTTP 出参泄漏服务器绝对路径）至今未决，上一版计划漏了 | P2 | 契约 + 产品 |

**同时确认了 8 处「看着像问题其实是对的」**（§10.8），包括：限流不是漏挂、两份编译器
不是意外漂移、`AssetReferenceSource` 只声明一张表是有意的、Node 子进程没阻塞事件循环。
这些写进文档是为了**防止按报告误改**。

---

## 1. 已经接对的（改造时不要动）

审这几块是为了划清边界——它们的形态已经是正确的，改造中一旦被「顺手统一」掉就是倒退。

### 1.1 框架装配：`__init__.py`
`ModuleSpec` 声明了路由 / 5 个权限点 / `startup` / `recovery_kinds` /
`asset_reference_sources` / 3 个 `jobs` / `seed` / `cli`。这是全仓声明最完整的模块之一。

### 1.2 权限码：`dependencies.py` + 四个 router
`require("template:read")` 形态，5 个码注册进 `ModuleSpec.permissions` 且已写进内置角色
（`editor` / `viewer` / `asset_specialist`）。**改名等于让非超管账号静默失去权限**，
`dependencies.py:1-7` 的注释已经写明这一点。

### 1.3 生成流水线：`pipeline.py`
- `@register_pipeline` + `GenerationStore` 七方法 + `ModelRouter`
- `strict_model = True`（替换跑的是原任务那次冻结的渠道，不许兜底换供应商）
- `fail_on_model_gone = False`（已付费任务，判失败会诱导重复计费）
- `ReplacementStore._update()` 用 `state_version` 做 CAS，不是简单 update

这段是全模块质量最高的代码，**改造中一行都别碰**。

### 1.4 封装（packs）：六张表 + `PackRepository`
`template_packs` / `template_pack_references` / `template_pack_versions` /
`replacement_runs` / `replacement_run_inputs` / `replacement_reviews`，
仓储分层清晰，软删走 partial index。

### 1.5 资产库：`get_asset_lookup()` 端口
`pack_service._check_references()` / `_verify_bytes()` / `authorization.py` 全部走端口，
**没有一处直连 `assets` 表**，也**没有一处 import 兄弟模块**（架构守卫零违规）。

---

## 2. 问题 A（P0）：`video_template_store.py` 用 JSON 文件当数据库

### 2.1 事实

`api/src/modules/template/video_template_store.py`，766 行，把「视频模板目录」存成
`api/var/video_templates/catalog.json`，全量读 → 改 → 全量写。

self-built 的部分：

| 它自己实现的 | 本该由谁提供 |
|---|---|
| `threading.Lock()` 保护「读改写」 | 数据库事务 / 行锁 |
| `_backup_current()` 每次写前整份复制、滚动保留 20 份 | 表本身（每行独立，改一条不影响其他行） |
| `catalog_stamp()` 用文件 mtime 做进程内缓存失效 | 不需要缓存，或 `max(updated_at)` |
| `os.replace` 原子重命名保证不读到半个文件 | 数据库保证 |
| `_LOCK` 里「抢占固定蓝本」的互斥 | partial unique index `WHERE pinned` |

### 2.2 关键：它给出的理由已经不成立了

文件头写着理由：

> 当前部署的 PostgreSQL `alembic_version` 指向本地迁移链里不存在的 revision
> （`b4e2d8f1a630`），跑 `alembic upgrade head` 会失败。……后续迁移链对齐后把
> `_read/_write` 换成仓储即可，对外契约不变。

**已实测，这个前提消失了：**

```
$ ls migrations/versions | grep b4e2d8f1a630
b4e2d8f1a630_user_contact_fields.py          # ← 现在存在

$ alembic current
b7c4e2a18d63 (head)                          # ← 数据库就在 head
$ alembic check
No new upgrade operations detected.           # ← 零漂移
```

更强的一点：`df546efef239_template_rebuild_to_database.py` 在 `upgrade()` 里
**第 163 行 `op.drop_table('video_templates')`** —— 当年那张演示表已经被删掉了，
数据库实测只剩 `template_packs` / `template_pack_references` / `template_pack_versions`。

也就是说：这不是「要不要引入新东西」，而是**把当初写在注释里的 TODO 做掉**。

### 2.3 现在的实际风险

`_LOCK` 是**进程内锁**，注释自己承认「多 worker / 多机时目录会丢更新」。
当前 `worker_concurrency = 2`，但 HTTP 侧是多进程的话，两个封装并发提交 →
后写的整份覆盖前一条，**丢数据且不报错**。

这也是为什么它要写「写前备份 20 份」——本质是在没有数据库的情况下手搓版本控制。

### 2.4 改造方案

**建表**（新迁移）：

```
video_templates
  id                     varchar(32)   PK
  template_key           varchar(32)   UNIQUE        -- 幂等键，取代 _append_record 的 key 去重
  name                   varchar(120)
  description            text
  prompt                 text                        -- 固定提示词原文
  prompt_sha256          varchar(64)
  source_video_asset_id  varchar(32)   NULL
  source_video_name      varchar(255)
  source_duration_sec    float
  aspect_ratio           varchar(16)
  pack_id                varchar(32)   NULL
  pack_name              varchar(120)
  pack_version           int
  default_kind           varchar(32)
  media_order            jsonb                       -- [{token,role,type,asset_id,name,in_prompt}]
  generation             jsonb
  rules                  jsonb
  cover_url              varchar(512)
  pinned                 boolean       NOT NULL DEFAULT false
  created_at / updated_at
```

索引：

- `UNIQUE (template_key)`
- `UNIQUE (pinned) WHERE pinned` ← **这一条替代 `_LOCK` 里的「抢占蓝本」**，
  两个并发写入只有一个能成功，另一个自动失败（对应 `pin()` 的语义）
- `(source_video_asset_id, default_kind)` ← `find_for_run()` 的主查询路径
- `(created_at)`

**文件拆分**（顺手解决 766 行）：

| 新文件 | 内容 | 依赖 |
|---|---|---|
| `video_template_repository.py` | 表访问：列表/取/插/更新/pin/按原片查 | 只认 SQLAlchemy |
| `video_template_service.py` | `create` / `pin` / `update_prompt` / `attach_source` / `find_for_run` | repository + 端口 |
| `video_template_media.py` | `_prompt_tokens` / `_resolve_media` / `_validate_media` / `_cited_slots` | **纯函数，无 IO** |

最后一项值得单独说：这几个函数现在是混在 store 里的模块级函数，靠
`await asyncio.to_thread(...)` 硬扛同步 IO 才显得像业务代码。抽出来以后它们是纯计算，
可以直接单测，也不用再进线程池。

**一次性导入**：`app import-video-templates` —— 读 `catalog.json` 按 `id` 幂等插入。
与已有的 `import-template-legacy`（把老 JSON 导进六张表）同一个套路，可以直接复用其写法。

**契约不变**：`video_template_schema.py` 的出参不动（除非采纳决策 2 删字段），
前端 `videoTemplate.api.ts` 5 个调用零改动。

### 2.5 回滚

- 迁移 `downgrade()` 只 drop 新表，不碰其他表
- `catalog.json` 保留**只读**，不删；上线后观察一轮再清理
- 双读校验：切换前跑一次性脚本，把 JSON 与表的内容逐条 `diff`，不一致就停

### 2.6 门禁

改 `models.py` + 新增迁移 → `select_tests.py` 会判 **`full`**（迁移是硬触发），
全量 1570 条约 169 秒。这一阶段不要指望定向能省时间。

---

## 3. 问题 B（P0）：视频模板没有归属，也没校验资产归属

### 3.1 事实

`video_template_store.py` 与 `video_template_router.py` 里 **`owner_id` 出现 0 次**。
对比 `pack_service` 每个方法都带 `owner` 参数并校验。

具体三处：

**(1) `attach_source()` 不校验 owner**

```python
lookup = await get_asset_lookup().get_many(session, [asset_id])
asset = lookup.get(asset_id)
if asset is None or asset.deleted_at:
    raise NotFound("样片原片不存在或已删除")
if asset.category != "source" or asset.type != "video":
    raise BadRequest("请选择已归档的 source 视频原片")
```

只查「存在 + 类型」，**不查 `asset.owner_id == actor.id`**。

后果链条：任何人（有 `template:pack:manage`）按 id 绑别人的原片 → `asset.name` 与
`source_duration_sec` 被写进**全库共享**的模板 → `GET /video-templates` 对**所有**
持 `template:read` 的用户返回这个素材名。属于跨用户信息泄漏，虽然泄的是名字不是字节。

**(2) `create()` 对 `source_video_asset_id` 完全不校验**

`VideoTemplateDraftIn.source_video_asset_id` 直接落库，不查存在性、不查归属。
可以直接写一个不存在的 id，或别人的 id。

**(3) `source_video_path` 是死字段，但仍是客户端可控的「本地路径」**

- 定义：`video_template_schema.py:53`，`max_length=512`，客户端可传任意字符串
- 落库：`video_template_store.py:446`，`video_template_store.py:332` 还写进 `media.file`
- **消费：零**。`_media_in_mixed_order()` 只用 `item.type` / `item.role` / `item.asset_id`，
  从不读 `.file`
- 前端：`schema.ts` 两个类型里都有，但**所有赋值都是 `''`**（`VideoTemplateLibrary.tsx:58` 等）

它现在是无害的死数据，但形态上是个「看起来能读服务器文件」的入口。**建议直接删**。

### 3.2 方案

**这里需要先拍板一件事**（见 §7 决策 1）：视频模板是「全库共享的固定蓝本」还是「每人一份」？

- 如果**保持共享**（推荐，符合 `pin()` 注释里「切的是全库蓝本」的原意）：
  - 表结构不加 `owner_id`，但加 `created_by` / `updated_by` 两列，只用于审计
  - 校验改成「**你只能绑你自己能看见的素材**」：
    `asset.owner_id == actor.id or asset.shared`（与 `authorization.py` 里
    原片的公共池规则同一口径，不新造规则）
  - 写入动作（create / pin / update_prompt / attach_source）保留 `template:pack:manage`，
    但补审计（现在只有 `pin` 和 `update_prompt` 记了）

- 如果**改成每人一份**：
  - 加 `owner_id` + 新权限码 + 列表按 owner 过滤
  - **契约变更**：`GET /video-templates` 结果集按用户变化，前端要处理空列表
  - `find_for_run()` 要加 owner 维度，替换链路的命中逻辑跟着变
  - 成本明显更高，且与「固定蓝本全库唯一」的现有语义冲突

**通用（与决策无关）**：
- `attach_source` / `create` 补素材归属校验
- 删 `source_video_path`（字段 + `media.file` 写入 + 契约 + 前端类型）
- `create` 补「asset 必须存在且未删除」的前置校验

### 3.3 门禁

改 `video_template_schema.py` → 契约变更 → 必须重新生成四份生成物：

```
app export-openapi          # openapi.json / API.md / schema.ts / permissions.ts
npm run gen:routes --check
npm run gen:mock-coverage --check
```

`select_tests.py` 会判 **`contract`**。

---

## 4. 问题 C（P1）：`seed.py` 裸 SQL 查 `assets` 表

### 4.1 事实

`seed.py:116-129`：

```python
rows = await session.execute(
    text("SELECT id FROM assets WHERE owner_id = :owner AND category = :category "
         "AND type = 'image' AND deleted_at IS NULL ORDER BY created_at LIMIT :limit"),
    ...)
```

文件头自述：「**素材用 SQL 直查 `assets` 表**，不 import 素材模块的模型……seed 是运维工具，
读一条 `SELECT id` 不构成依赖。」

**动机是对的**（模块间不许互相 import 是红线，`test_modules_do_not_import_each_other`
会拦），但做法是绕开。真正的问题是：

### 4.2 根因：`AssetLookup` 端口缺「按条件列 id」

看 `authorization.py` / `pack_service.py` 用的是
`get_asset_lookup().get_many(session, ids)` —— 只能「已知 id 批量取」。
seed 需要的是「按 owner + category 找 id」，端口没这个能力，只能直查表。

### 4.3 方案

扩端口，而不是让模块继续直查：

> **第四轮修订（§21）**：下面写的是第三轮「扩 v1 `AssetLookup`」的原方案，
> 保留作分析记录；**执行时改为扩 v2 `LibraryLookup`**（v2 没有 `category`，
> 按 `kind` / 标签查），`seed.py` 与 `import_legacy.py` 随之切到 v2 id 空间。
> v1 端口不再扩能力，避免刚补完就随迁移废弃。守卫同理改扫
> `text("... FROM library_assets")`。

- 在 **`core/ports/media.py::AssetLookup`**（`media.py:44`）上加一个查询方法
  （如 `list_ids(session, *, owner_id, category, type, limit)`）。
  注意别改错文件：`core/ports/assets.py` 是「素材引用来源」的声明式注入口
  （`AssetReferenceSource` / `ask_asset_references()`），不是查询端口。
  返回最小投影（id + name + type），**不返回整行**——避免端口变成「绕过 assets 模块读全表」
- 实现放在 `modules/asset/` 侧（它才是 `assets` 表的拥有者）
- `seed.py` 改成调端口；`_asset_ids()` 那个 `text()` 删掉

**这条改动的价值超出 seed**：端口缺查询能力，后面每个想「按条件筛素材」的模块都会
再写一次裸 SQL。

### 4.4 守卫

加一条测试：`modules/*/` 里出现 `text("... FROM assets")` 即失败。
现有 `test_asset_reference_sources.py` 已经在做类似的「引用来源必须声明」检查，
照着写就行。

---

## 5. 问题 D（P1）：三个本地目录 + `runs_root()` 本地回落

### 5.1 事实

模板库涉及三个「跑在本地盘上的目录」，由三个配置项决定：

| 配置项 | 默认位置 | 存什么 | 能否上对象存储 |
|---|---|---|---|
| `video_template_catalog` | `api/var/video_templates/catalog.json` | 视频模板目录 | 否（见问题 A → 落表后消失） |
| `template_packager_state_dir` | `api/var/template-packager` | 封装进度（幂等/断点续跑） | 否（需原子重命名） |
| `replacement_runs_root` | `<归档根>/runs` | 执行器工作区（inputs/outputs） | **不能**（ffmpeg/Node 要真实文件） |

前两个随问题 A / packager 归位一起消失。第三个是**真实约束**，不是设计缺陷——
`workspace.py:1-20` 的说明是准确的：执行器 `bridge.mjs` 要真实目录。

### 5.2 真正需要改的是 `runs_root()` 的回落

```python
def runs_root() -> Path:
    raw = settings.replacement_runs_root.strip()
    if raw:
        return Path(raw).resolve()
    if settings.archive_driver == "local":
        return get_archive_storage().root() / "runs"
    return _DEFAULT_LOCAL_RUNS      # ← 归档切 S3 时，工作区仍在 api/var/runs
```

`archive_driver != local` 时**静默回落本地盘**。这在多机部署下是错的：A 机建的
工作区，B 机读不到，而两边都不会报错，表现是「任务卡在 uploading」或「成片不存在」。

配置文件里的注释已经承认了（`core/config.py:214`：「归档切远端时落到 `api/var/runs`」），
但**这是把部署陷阱写进注释，而不是消掉它**。

### 5.3 方案（两种，选一种）

**方案 1（小）**：`archive_driver != local` 且 `replacement_runs_root` 为空时，
**启动即失败**，报「远端归档必须显式配置 `REPLACEMENT_RUNS_ROOT`（指向共享盘）」。
注意不是从零加：现有 `app doctor` / 启动日志 / `app runs-status` 已经有 WARN
（见 §10.5 更正），这一步是**把 WARN 升级成 FAIL**，并补一条 doctor 项。

**方案 2（大）**：把「工作区」也抽象成端口（`WorkspaceStore`：`create` / `materialize` /
`publish`），本地实现是目录，远端实现是「临时目录 + 从对象存储物化 + 用完清」。
执行器的真实文件约束由实现层承担，业务层只认 key。

方案 2 才是「统一到 S3」的正解，但它动的是 `workspace.py` + `worker.py` 的
`prepare_upload` / `download` 链路，**风险明显高于问题 A**，而且当前部署是单机共享盘，
收益暂时为零。

**建议：先做方案 1**（消除静默错误），把方案 2 记进 `EXTENSIBILITY_GAPS.md`，
等真的要多机再说。

---

## 6. 结构性但不算「绕开」的三处

### 6.1 两个超千行文件

- `replacement_service.py` 1345 行（合并 PR #7 后）
- `worker.py` 975 行

它们不是绕开框架，是内聚度问题：状态机、投影、归档、授权闸、提示词编译全在一个文件。
**不建议为拆而拆**——这两个文件有非常密集的注释解释「为什么这样」，拆散会丢上下文。

如果要动，优先级最低，且只在「改到某一段必须同时改另一段」时顺手拆。
`worker.py` 里有一组天然边界：`_sync_out` / `_write_projection` / `_absorb` /
`sync_projection`（投影层，约 230 行）可以整体挪成 `worker_projection.py`。

### 6.2 错误码没落地

`errors.py` 里只有**一个示例错误码** `TemplateBusy`，且**全模块无人使用**
（`grep TemplateBusy` 只有定义处）。业务代码里：

```
BadRequest(  96 处
NotFound(    24 处
errors.code(  1 处   ← 就是那个没人用的示例
```

后果：前端拿不到稳定的 `code`，只能匹配中文文案；文案一改前端就断。

**建议不做全量替换**（96 处改动、收益低）。只把「前端需要按原因分支」的挑出来
（例如「素材包已停用」→ 前端该跳去重新选包，而不是弹个 toast），其余保持字面量。
`TemplateBusy` 这个示例码应删掉——留着一个没人用的类会让人以为错误码体系已经启用。

### 6.3 `template_packager` 长在 `src/` 顶层

`api/src/template_packager/`，909 行，5 个文件。

事实：
- **零内部引用**（`src/` 里没有任何地方 import 它）
- 它是 `python -m template_packager manifest.json` 的**批处理工具**，
  通过 HTTP + token 调自己的 API（`client.py` 用 httpx + `YUFAN_API_TOKEN`）
- 只有测试 import 它（`test_packager_full_chain.py` 等 5 个文件）

所以它是**外部客户端工具**，走 HTTP 的设计是对的（等于用用户视角验证接口），
问题只是**位置**：放在 `src/` 里会被 mypy 全量类型检查、被当成服务端代码一起发布。

**建议**：挪到 `api/tools/template_packager/`（或仓库根 `tools/`），
测试里的 import 跟着改。低风险、纯搬运。也可以选择不动——它至少没有污染 `modules/`。

---

## 7. 需要你拍板的四个决策（第一版；新增决策见 §12）

### 决策 1（影响最大）：视频模板要不要按人隔离？

| 选项 | 含义 | 成本 |
|---|---|---|
| **A 保持全库共享**（推荐） | 加 `created_by`/`updated_by` 仅作审计；校验改成「只能绑自己可见的素材」 | 小，契约不变 |
| B 每人一份 | 加 `owner_id` + 新权限码 + 列表按人过滤 + `find_for_run` 加 owner 维度 | 大，契约变更，与「全库固定蓝本唯一」冲突 |

推荐 A：`pin()` 的现有语义就是「切全库蓝本」，B 会把它变成「每人一个蓝本」，
产品含义完全不同。

### 决策 2：`sourceVideoPath` 直接删？

前端所有赋值都是 `''`，后端只存不读。删掉是净收益。**代价**：契约变更
（`schema.ts` 两个类型、`apps export-openapi` 重新生成）。可以顺带在决策 1 的同一批做完。

### 决策 3：`runs_root()` 走「启动即失败」还是「抽象工作区端口」？

建议先做前者（5 行），后者记进 `EXTENSIBILITY_GAPS.md`。

### 决策 4：错误码要不要系统化？

建议只改「前端要分支」的那几个，不做 96 处全量替换。

---

## 8. 执行顺序（第一版四步；修订后的六步见 §11）

| 步 | 内容 | 风险 | 分级 | 依赖 |
|---|---|---|---|---|
| **1** | 扩 `LibraryLookup` 端口（v2，见 §21）+ `seed.py` 去裸 SQL + 加守卫 | 低 | targeted | 步 2.5b |
| **2** | 视频模板落表（问题 A）+ 766 行拆三份 + 导入脚本 | **中高** | **full** | 无 |
| **3** | 补归属校验 + 删 `sourceVideoPath` + 补审计（问题 B） | 中 | contract | 步 2 |
| **4** | `runs_root()` 启动校验 + 删 `TemplateBusy` + packager 归位 | 低 | targeted/framework | 无 |

**为什么 1 先做**：它最小、独立、且验证了「扩端口」这条路走得通——步 3 的归属校验
也要靠端口（`get_many` 已经够用，但口径要对齐）。

**为什么 3 必须在 2 之后**：落表后归属校验才是「一个 WHERE」的事；
在 JSON 上做校验要再手写一遍遍历。

**不做的事**（避免过度改造）：

- 不动 `pipeline.py` / `channels.py` / `authorization.py` —— 它们已经是正确形态
- 不为拆而拆 `replacement_service.py` / `worker.py`
- 不把 `replacement_runs_root` 强上对象存储（执行器要真文件，这是物理约束）
- 不重命名 5 个权限码（会让非超管账号静默失去权限）

---

## 9. 验收标准（第一版；补充项见 §13）

每一档都要能反向验证（这是项目一贯的口径）：

1. **落表后**：把 `catalog.json` 里删掉一条，`GET /video-templates` 不应变化（证明真读表）
2. **归属校验**：用 A 用户 token 绑 B 用户的 source 资产，必须 403/400；
   移除校验后守卫必须变红
3. **裸 SQL 守卫**：把 `seed.py` 的 `text()` 加回去，守卫必须变红
4. **蓝本唯一**：并发两次 `pin` 不同模板，必须恰好一条 `pinned=true`
   （单测里用两个 session 并发）
5. **`find_for_run` 顺序**：同原片多条模板时，`pinned` 优先、其次 `created_at` 最早
6. **契约**：`app export-openapi --check` 四份无差异（除决策 2 采纳后的那一次）

---

## 10. 第二轮：跨维度全面审计

> 方法：不再按「框架/数据库/S3/权限/资产库」分类，而是按**软件系统的完整维度**
> 逐项扫——编译与执行链、运行时依赖、异常吞噬、可观测性、部署校验、测试真实性、
> 限流策略、契约出参、时间源、路径安全、数据保留。
> 每条都写了「实测证据」和「要不要改」，避免把「看着可疑」当成「确认有问题」。

### 10.1 提示词编译链：三层后处理，冻结哈希对不上实际发出的文案（E / F，P1）

**链路实测**：

```
Python prepare()
  └─ asyncio.to_thread(_materialize_inputs)         # 线程池，不阻塞事件循环 ✓
       └─ compile_prompt()  → subprocess(node, workbench/compile.mjs, timeout=30)
            ├─ 模块版 prompt-compiler.mjs 编译
            ├─ 分支① video_scene_text：整段自己拼 prompt
            ├─ 分支② oneSecond：replace 改文案 + 重算 spec_sha256
            ├─ 分支③ wardrobe=reference：整段重写 prompt（只用 audio token）
            └─ 分支④ fixed_prompt：换掉整段 prompt，重算 template_sha256
  └─ Python 再改一次：
       ├─ .replace("；提供同片原音轨", ...) / .replace("声音：为完整原音轨", ...)
       │    → compiler_version += "+embedded-audio-v1"
       └─ translate_prompt()（bailian 渠道）→ compiler_version += "+bailian-references-v1"
  └─ inject_masked_text_refs() 把 {{Text N}} 换成正文
  └─ 写 inputs/prompt.txt、run.prompt、records/prompt-compilation.json
```

**E 的实质**：`compilation.json` 存的是**最终 prompt**，但里面的 `template_sha256`
和 `spec_sha256` 是 Node 前几步算的 —— 分支③只改了 `template_sha256`，分支④只改了
`template_sha256`，Python 两次改写**一个哈希都没动**。
结果是：**没有任何一个哈希能验证「实际发出去的这段字节」**。
`bridge.mjs:80` 用 `compiled.prompt !== p.prompt` 做运行时比对，那是同一份字符串自己比自己，
不是独立证据。对于「已付费任务，事后要自证当时发了什么」，现在只能靠 `run.prompt`
那一列，档案里的哈希不可用于复核。

**F 的实质**：Python 那两个 `.replace()` 是在给 Node 打补丁。
Node `compile.mjs` 的 wardrobe 分支用 `tokens('source_audio')` 拼「声音」段；
**没有 source_audio 参考时**（原片内嵌音轨）这个 token 是空串，会生成病句，
Python 再用字符串替换去修。模板文案一改，两个 replace 就**静默不命中**——不抛错，
只是提示词里留着病句，而 `compiler_version` 仍然标着 `+embedded-audio-v1`，
等于盖了个假章。

**改法（建议合并成一步，见 §11 步 4）**：

1. 把「音频内嵌」下沉成 Node `compile.mjs` 的显式分支（像它已有的 `oneSecond` 那样），
   Python 侧删掉两个 `.replace()`；补丁不命中时**抛错**，不许静默通过。
2. 在 Python 侧**所有后处理之后**统一算 `prompt_sha256 = sha256(compiled.prompt)`，
   写进 `compilation.json` 和 `replacement_runs` 新列；Node 侧各分支的哈希保留但
   **改名为 `template_sha256` 的语义要写清**（它哈希的是模板，不是最终 prompt）。
3. `records/prompt-compilation.json` 补 `vendor_release` / `vendor_commit`（见 §10.2）。
4. 老档案没有 `prompt_sha256` 时读成 `None`，不回填、不猜测。

### 10.2 运行时快照：冻的是一份执行器从不读的编译器（G，P2）

实测两份编译器：

| 位置 | 行数 | 性质 |
|---|---|---|
| `modules/template/replacement_runtime/prompt-compiler.mjs` | 91 | 模板库改造版，Python 编译走它 |
| `infra/ai/libtv_runtime/vendor/libtv/prompt-compiler.mjs` | 89 | 第三方只读快照，`vendor/manifest.json` 逐文件锁 sha256 |

**先纠正一个容易误判的点**：MD5 不同主要是 CRLF 差异。去掉 CR 后只有 **2 处**差异
（模块版多了 `ref.usage` 支持，让封装补充文案优先生效），`verifyMixedList()` 两版
**逐字相同**。所以这是**有意分叉**，不是意外漂移，运行时的 mixedList 校验口径一致。

**真正的问题在证据链**：`bridge.mjs:61` 的 `moduleAt()` 从 **vendor 目录**解析模块
（`verifyVendor()` 先校验 manifest），而 `replacement_service.py:1243` 的
`shutil.copytree(RUNTIME, code/runtime)` 只把**模块版**复制进每个 run 的归档快照。
也就是说：注释里写的「编译器快照：代码改了行为却没变时，靠它证明当时跑的是哪一版」，
**只覆盖了 Python 那一半**；执行器实际用的 vendor 版根本不在档案里，
`compiler_version` 也没记 vendor 的 `release` / `commit`。
vendor 侧有 manifest 锁哈希（这半边反而更可靠），但要复现老档案得倒查 git 历史。

**改法**：把 `manifest.json` 的 `source.release` / `source.commit`（或整份 manifest 的
sha256）写进 `records/prompt-compilation.json`；`app doctor` 加一项校验 vendor 哈希清单。
不改加载路径、不动 `verifyVendor()`。

### 10.3 异常吞噬：三处降级不报错，且没有指标（J，P1）

| 位置 | 吞掉什么 | 后果 |
|---|---|---|
| `worker.py:728` `publish_output` | 成片归档到对象存储失败 | `output_key` 不落库，任务显示**成功**；靠 `output()` 本地兜底能播 |
| `worker.py:142` `_sync_out` | 工作区不可达 / 抢不到锁 | 返回 `False`，投影下次再补，无计数 |
| `import_legacy.py:415` `_run_dir` | `except Exception` 一律 `return None` | **归档根没配**被当成「这条档案不在本机」，整次导入可能一条不导还报成功 |

`output()` 的注释自己写着「只是换台机器打不开」——这与问题 D（`runs_root()` 在
`archive_driver != local` 时静默回落 `api/var/runs`）**叠加**：多机部署下就是
「生成成功但播不了」，而且全程只有日志，没有告警。

`prepare` 那处 `except Exception`（`replacement_service.py:360`）处理是对的：
记 `PREPARATION_FAILED` + `last_error` 再 `raise`，有留痕，不要改。

**改法**：`import_legacy` 把「归档根不可用」与「单条档案不在本机」分开（前者直接失败）；
`publish_output` 失败时在详情里标「成片仅本机可见」；三处各加一个 counter。

### 10.4 可观测性：全模块只有 1 个指标（J，P1）

`grep Counter/Gauge/Histogram` 在 `modules/template/*.py` 里只命中
`worker.py:77` 的 `RECOVERY_LOCK_GIVEUPS`（`replacement_recovery_lock_giveups_total`）。
`api/instrumentation.py` 里没有任何 `template_*` / `replacement_*` 指标名。

也就是说：**成功率、各阶段耗时、编译器失败、归档失败、队列积压、Node 子进程耗时
全都没数**。这个模块跑的是全仓最贵、最长、最容易「成功却静默丢东西」的链路，
目前只能靠翻日志。

**改法（最小集，别一次铺开）**：

```
replacement_prepare_total{result=ok|failed}            # 准备成功/失败
replacement_compile_seconds                            # Node 编译耗时（含超时）
replacement_archive_publish_total{result=ok|failed|missing|unrecorded}
replacement_projection_skipped_total{reason=lock|io}   # 投影写不进去
```

**命名统一为 `replacement_`**（不是原稿的 `template_`）：本模块唯一的存量指标
`worker.RECOVERY_LOCK_GIVEUPS` 就是这一族，同一个模块出现两套前缀会让看板要对两次。
`archive_publish` 比原稿多两个枚举值，因为「成片文件不在」与「字节进桶但键没落库」
是两种不同的静默失败，合成一个 `failed` 就分不出该修哪边。

配合 §10.3，这四个能把「不报错的失败」变成可看的数。

### 10.5 部署前置校验：模板库一项自检都没有（K，P2）

事实：

- `ModuleSpec` **没有声明 `doctor` 钩子**（框架支持，见 `api/module.py:156`，
  `create_app()` 末尾逐个调用）。模板库在 `app doctor` 里零存在感。
- vendor manifest 写明 `verified_node: "22.14.0"`，但 `get_libtv_settings().node`
  默认是字符串 `"node"`（取 PATH），**没有任何版本校验**。Node 版本不对时，
  用户只看到「提示词编译器不可用」。
- **更正（合并后复核）**：工作区**不是零自检**。`api/diagnostics.py:258` 的
  `_check_workspace_and_blobs()` 已有 `archive.runs` 检查，`app.py:158` 启动时按
  `workspace_needs_attention()` 打 warning，`app runs-status` 能列出「只在本机可见」
  的档案。缺点是它按 orphan 指针给 **WARN，不拦启动**。
  真正没查的是 `video_template_catalog` / `template_packager_state_dir` 可写性、
  Node 版本、vendor 哈希清单 —— 以及**模板模块自己没有 `doctor` 钩子**。

**改法**：给模板库加 `doctor` 钩子，四项：Node 版本与 manifest 声明对齐、
vendor 哈希清单完好、远端归档时 `REPLACEMENT_RUNS_ROOT` 必须显式配置、
三个本地目录可写。上一轮决策 3 的「启动即失败」放这里最合适——它本来就是自检。

### 10.6 测试真实性：缺独立 golden 断言（H，P1）

**准确表述（合并 PR #7 后复核，别再说「零测试」）**：全仓搜
`prompt-compiler` / `compileWorkbench` / `compile_prompt` / `prompt-compilation`
在 `api/tests/*.py` 里零命中，**但编译链路并非完全无人跑**：

- `test_template_fixed_references.py` 的 prepare 集成用例（含 `inputMode:
  video_scene_text`）会真的走到 Node `compile.mjs`；
- `test_video_template_binding.py:194` 断言了 `compiler_version` 的后缀；
- `api/var/mtp-*/.../code/runtime/` 堆着大量测试生成的工作区副本。

缺的是**针对编译结果字节的独立 golden 断言**：没有任何用例固定住
`prompt` 的 sha256 / `bindings`，也没有断言「`compilation.json.prompt` ==
`run.prompt` == `inputs/prompt.txt`」。改一个中文模板的字，集成用例照样绿。

注意别把两个 `prompt_sha256` 混为一谈：`test_video_template_prompt.py` 里断言的是
**视频模板目录里存的那份提示词原文**（PR #7 为逐字保新加的，见
`video_template_store.py:446`），不是 `records/prompt-compilation.json` 的最终产物哈希。
§10.1 要补的是后者。

后果：Node 侧 4 个分支（PR #7 又给 `video_scene_text` 加了 `fixedReferenceTokens`
路径）、3 个中文 `.txt` 模板、Python 侧两次 `.replace()`，任何一处文案改一个字，
都不会有测试变红。而这些字节直接决定**已付费任务发出去的提示词**。
这跟项目里「文本引用宽容路径」「`library_asset_generation.prompt` 改回 String(4000)
会静默丢长文案」是同一类风险，只是落在 Node 侧，缺少独立守卫。

**改法（先钉住再改，顺序见 §11）**：

1. **先**加 golden test：固定 4 组 spec（character/product/scene，各覆盖固定提示词与
   文字换场景）→ 断言 `prompt` 的 sha256 与 `bindings`。可以把 Node 直接跑起来
   （`node replacement_runtime/workbench/compile.mjs`），不必进 pytest 的 HTTP 链路。
2. 加一条跨层守卫：`records/prompt-compilation.json` 的 `prompt` 必须等于 `run.prompt`
   且等于 `inputs/prompt.txt`。这条现在只靠 `bridge.mjs` 运行时校验，不在测试里。
3. **然后**才做 §10.1 的改动 —— 改完 golden test 必须按预期变红，更新指纹时
   要能说清「为什么变了」（这正是反向验证）。

### 10.7 限流策略腐烂：`prepare` 的理由与事实不符（I，P1）

先说机制是对的：`api/ratelimit_policy.py` 有 `EXEMPT_WRITE_ROUTES`，
`tests/test_route_policy.py::test_every_write_route_declares_rate_limit_policy`
要求每条写路由「挂了 `rate_limit` 或在豁免表里写清理由」，还会核理由里引用的
`.py` 路径和权限码真实存在、`review_by` 过期即红。**模板库 13 条写路由全部有条目，
不是漏挂。**

但 `ratelimit_policy.py:278` 这条：

```
"POST /templates/replacement-runs": Exemption(
    why="建草稿：只写元数据，不提交供应商（真正的提交由 `/execute` 限流）",
    review_by=NEAR_TERM,   # 2027-05-01
)
```

**与实现不符**。`service.prepare()` 实际做：

- `create_workspace()`：建目录 + `run.json` + 索引指针
- `_materialize_inputs()`：`storage.materialize()` 物化**每一份**输入素材副本、
  `subprocess` 跑 Node 编译器（30 秒上限）、`shutil.copytree()` 整个运行时、
  生成缩略图（`create_preview` 子进程）、写 5 份以上 JSON
- 数据库多行写入

它不提交供应商，但**是全模块最重的 IO 端点之一**，比 `execute`（只是入队）重得多。
策略表文档自己承认「守卫核不了『这个接口现在值多少额度』」，而 `review_by`
还有近半年 → 这句错误理由会一直绿着。一个账号高频打 prepare，能把线程池、
磁盘和 Node 子进程打满，唯一限流的 `execute` 在后面。

**改法**：给 `prepare` 挂 `rate_limit`（额度可低于 `execute`），并从豁免表移除；
或者保留豁免但把理由改成真实的成本说明 + 写清靠什么兜底（`request_id` 幂等、
素材数量/大小上限）。**不要只改文案**。

顺带：`video-templates` 那四条理由写「只写本地目录 / 单条更新」，
现在 `create` 实际是「整份 catalog.json 读改写 + 备份 20 份 + `os.replace`（锁内）」。
**落表（问题 A）之后这四条理由才名副其实** —— 这是步 2 的附带收益，记一笔即可。

### 10.8 已核对为「正确」，不要按报告误改

这一节和上面同等重要。以下 8 处看着可疑，逐条查过，**保持现状**：

| 项 | 结论 |
|---|---|
| 限流是不是漏挂 | **不是**。13 条写路由全在豁免表，`test_route_policy.py` 守卫齐全。问题只在 §10.7 的理由失真 |
| 两份编译器 MD5 不同 | **有意分叉**。去 CR 后仅 2 处差异（模块版加 `usage`），`verifyMixedList` 逐字相同，vendor 有 manifest + `verifyVendor()` |
| 5 个权限点有没有全被引用 | **全部有**。路由 + `identity/asset_roles.py` 都齐；`EXTENSIBILITY_GAPS §215` 那个 `template:use` 角色不同步的坑已修 |
| `AssetReferenceSource` 只声明 `template_pack_references.asset_id` | **有意**。`replacement_run_inputs` 是历史档案，注释写明「历史版本算引用会让老素材永远删不掉」；`input_thumbnail()` 从归档快照派生，不依赖 `assets` 行 |
| Node 子进程会不会阻塞事件循环 | **不会**。`prepare` 用 `asyncio.to_thread(_materialize_inputs)` 包住，30 秒超时也在线程里 |
| `sync_projection` 把 ORM 对象传进线程池 | **安全**。`_write_projection()` 只读已加载的列属性，`_sync_out()` 内部已吞 `Conflict`/`OSError`；没有惰性加载 |
| 四份契约生成物 | **同步**，git 状态干净；`schema.ts` 里 `sourceVideoPath` 与后端一致 |
| `pipeline.py` / `channels.py` / `authorization.py` | §1 已确认正确，本轮复核仍是对的。三处授权闸用同一个函数，不是三份规则 |

### 10.9 契约出参、时间源与小项（L / M）

**L：TODO T10 仍未决，且上一版计划漏了。**
`schema.py:179` 的 `archive_path` 字段描述明写「工作区绝对路径（排查用）」，
`_archive_path()` 返回 `str(run_path(run_id))`（可能是 `Z:\...`）；前端
`ArchivedResults.tsx:83`、`ReplacementDetailModal.tsx:44`、`ReplacementReviewModal.tsx`
三处原样展示。`docs/TODO.md:80-90` 标为「产品给结论：保留 → 在字段描述里写明
勿用作下载链接；改 → 只回 runId」，**至今没定**。
它与决策 2（`sourceVideoPath` 死字段）同属「路径字段」，建议**同一批**处理，
一次契约变更解决，别分两次重生成四份生成物。

**M：小项汇总**（都不单独占一步，跟着相邻步骤顺手做）：

- 时间源不统一：`pack_service.py:210,215` 用 `datetime.now(UTC)`，
  而 `db.base.utcnow` / `repository.py:_now()` / `run_clock.py` 各有口径；
  想给「发布时间」钉时钟会很别扭。跟随步 2 统一到 `utcnow`。
- `import_legacy.py:354` 把老档案 URL 硬拼成当前 `settings.api_prefix`，
  prefix 一改历史数据全错。跟随步 4 的导入脚本清理。
- `import_legacy._run_dir()` 的 `(root / relative).resolve()` 没校验 resolve 后
  仍在 root 下（路径来自 JSON 指针）。运维工具、只读 `run.json`，风险低，
  但顺手加一条 `is_relative_to(root)` 很便宜。
- 每个 run 的 `code/runtime/` 复制一份编译器 + 3 个中文模板，`api/var/` 下
  测试残留已经堆了很多副本（`du` 扫盘超时）。需要一条 GC / 保留策略，
  否则落表后工作区仍会无限增长。

---

## 11. 修订后的执行顺序

第一版的四步保留，但前后各插一步，并单独拆出编译链治理。
**核心原则：先钉住现状（golden test），再改行为。**

| 步 | 内容 | 风险 | 分级 | 依赖 |
|---|---|---|---|---|
| **0**（新） | `doctor` 钩子（Node 版本 / vendor 哈希 / `runs_root` / 目录可写）+ 4 个 counter + 修 `import_legacy` 静默跳过 + 修 `prepare` 限流理由 | 低 | targeted | 无 |
| **1** | 扩 `LibraryLookup` 端口（v2，见 §21）+ `seed.py` 去裸 SQL + 加守卫 | 低 | targeted | 步 2.5b |
| **2** | 视频模板落表（问题 A）+ 766 行拆三份 + 导入脚本 | **中高** | **full** | 无 |
| **3** | 补归属校验 + 删 `sourceVideoPath` + 补审计 + **定 T10 `archivePath` 口径**（问题 B / L） | 中 | contract | 步 2 |
| **4**（新） | **先加编译器 golden test**，再统一 `prompt_sha256`、下沉音频内嵌分支、补 vendor 版本记录（E/F/G/H） | 中 | framework | 无（可与 0/1 并行） |
| **5** | `runs_root()` 启动/自检校验 + 删 `TemplateBusy` + packager 归位 + 时间源统一 | 低 | targeted/framework | 步 0 |

**进度（2026-11-13 收官时更新）**：步 0 / 0.5 / 1 / 2 / 2.5a / 2.5b 已完成，
执行记录见 §24.1–§24.7；**下一步是步 2.5c**（后端读路径与三道授权闸切 v2）。

**为什么 0 在最前**：全是「加观测和校验」，不改任何行为，却能把后面每一步的
「成功却静默丢东西」变成可见。而且它最小、独立、可单独上线。

**为什么 4 要「先钉住再改」**：编译器只有集成用例间接覆盖、没有独立 golden 断言
（§10.6 已更正表述），直接改 §10.1 的行为没有安全网。
先加 golden test 锁住当前输出，改完按预期变红、再更新指纹 —— 这是项目一贯的
「还原改动后守卫必须变红」口径，在 Node 侧同样适用。

**并行关系**：步 0/1/4 互不依赖，可以三条独立提交；
步 2 是唯一需要全量测试的一档（动迁移）；步 3 依赖 2（落表后归属校验才是一个 WHERE）。

---

## 12. 新增决策（接着 §7 的 1–4）

### 决策 5：`archivePath` 保留绝对路径还是改？（L）

| 选项 | 含义 | 代价 |
|---|---|---|
| 保留 | 字段描述补「服务端路径，勿用作下载链接」，前端展示不变 | 零契约变更，但继续泄漏共享盘布局 |
| 改成只回 `runId` | 前端拼展示文案 | 契约变更 + 3 处前端改动，运维可能真的按这个路径找文件 |

`docs/TODO.md` 明确写这是**产品决定**，我不代拍。建议与决策 2 同批处理，
避免两次重生成四份生成物。

### 决策 6：编译链治理做到哪一档？（E/F/G/H）

| 选项 | 内容 |
|---|---|
| **A 完整**（推荐） | golden test + `prompt_sha256` + 音频分支下沉 + vendor 版本入档 + 删 Python `.replace()` |
| B 只补证据 | golden test + `prompt_sha256` + vendor 版本，不动编译逻辑（F 的补丁风险留着） |
| C 只加测试 | 只做 H，把现状钉住，行为一个字不改 |

推荐 A。B 会把「字符串补丁静默失效」这个定时炸弹留着；C 适合「这轮只想降低风险、
不想动生产提示词」的情形 —— 如果用户觉得近期提示词文案还会大改，选 C 更稳，
等文案稳定了再做 A。

---

## 13. 修订后的验收标准（接着 §9 的 1–6）

7. **编译结果可复核**：任一新档案的 `records/prompt-compilation.json.prompt_sha256`
   必须等于 `sha256(inputs/prompt.txt)` 且等于 `sha256(run.prompt)`；
   手工改一个字节，守卫必须变红。
8. **Python 补丁已移除**：把「音频内嵌」的 spec 跑一遍，prompt 里不得出现
   空的 `{{Mixed N}}` 或「提供同片原音轨」病句；把 Node 模板那句话改掉，
   golden test 必须变红（证明不是靠静默 replace 兜着）。
9. **vendor 版本入档**：新档案的 compilation.json 带 `vendor_release` / `vendor_commit`；
   篡改 `manifest.json` 里任一哈希，`app doctor` 必须报 FAIL。
10. **限流理由对齐实现**：`prepare` 要么挂了 `rate_limit`，要么豁免理由里
    明确写出「物化素材 + Node 编译 + 复制运行时」；`review_by` 不得晚于本轮改动。
11. **静默失败可见**：模拟归档存储抛异常，任务仍判成功但
    `template_archive_publish_total{result=failed}` 必须 +1，详情页标注「仅本机可见」。
12. **导入不再静默空跑**：把 `REPLACEMENT_RUNS_ROOT` 配成不可用，
    `app import-template-legacy` 必须失败退出，不能报「导入完成 0 条」。
13. **doctor 自检**：Node 版本与 manifest 不符时 `app doctor` 必须 WARN/FAIL；
    `ARCHIVE_DRIVER` 非 local 且未配 `REPLACEMENT_RUNS_ROOT` 时必须 FAIL
    （现状只有启动 warning 与 `archive.runs` 的 WARN，见 §10.5 更正）。

---

## 14. PR #7 合并后的复核（开工前必读）

> 本计划写于 PR #7 之前。2026-11-12 晚把 PR #7 合进 `main`（merge `b9dd24d`）后，
> 逐条复核了计划引用的事实。**结论：六步顺序与六个决策都还成立**，但有 5 处漂移，
> 其中第 1 条会直接决定步 2 会不会把刚上线的功能改坏。

### 14.1 最关键：步 2 落表必须带走「固定引用」（新增硬约束）

PR #7 给视频模板加了 `rules.fixedReferences` / `rules.fixedBaseMediaCount`
（`video_template_store.py:548-570`），保存端冻结 `packId + packVersion + assetId`，
准备/执行端 `authorization_gap()` 再复核。§2.4 的表结构用 `rules jsonb` 能装下，
**但一次性导入脚本必须是「rules 原样搬运」，不能按已知字段重建 dict** ——
否则落表后固定引用静默丢失，用户刚做好的跨包引用模板全部退回旧行为，且不报错。

步 2 因此新增两条：

- 导入脚本按 `id` 幂等插入时，`rules` / `media_order` / `generation` 三个 jsonb
  **整字段复制**，不做白名单过滤；
- 加一条回归：造一条带 2 个固定引用的模板 → 落表 → `GET` → `prepare`，
  断言 `fixedReferences` 与 `fixedBaseMediaCount` 逐字不变（并入 §9 验收 1）。

### 14.2 其余四处漂移

| 处 | 变化 | 计划怎么调整 |
|---|---|---|
| `video_template_store.py` | 736 → **766 行**；`replacement_service.py` 1307 → **1345 行** | 正文数字已更新，拆三份的边界不变 |
| 编译链 | PR 给 `compile.mjs` 的 `video_scene_text` 分支加了 `fixedReferenceTokens`；`spec["fixedPromptSha256"]` 在编译前注入 | §10.1 的分支图多一条路径；**步 4 的 golden test 必须覆盖「带固定引用的文字换场景」这组 spec**，否则新增分支仍无人看守 |
| 授权口径 | PR 在 `authorization_gap()` 用 `asset.owner_id == pack.owner_id` 复核固定引用 | §3 的「只能绑自己可见的素材」要**与这条对齐**，不要造第二套 owner 规则；否则保存端与执行端又会分叉 |
| 工作区自检 | 复核发现 `app doctor` / 启动日志 / `app runs-status` **已有** WARN | §5.3、§10.5、§13.13 已更正为「把 WARN 升级成 FAIL」，不是从零加 |

另外把 §10.6 的「编译器零断言」改成准确表述：**缺的是独立 golden 断言**，
集成用例其实会跑到 Node（PR 新增的 `test_template_fixed_references.py` 就是），
`test_video_template_binding.py:194` 还断言了 `compiler_version` 后缀。
同时区分两个 `prompt_sha256`：模板目录那份（PR #7 新增，已有断言）≠
`compilation.json` 里要补的最终产物哈希。开工时别把两者当一个。

### 14.3 复核后仍成立（不用改计划）

`owner_id` 在 store / router 里仍是 **0 次**；`sourceVideoPath` 前端赋值仍全是 `''`；
`seed.py:121` 裸 SQL 仍在；`ratelimit_policy.py:280` 的「只写元数据」理由仍失真
（prepare 现在还多了一步 `resolve_pack_references`，更重）；
`alembic check` 无漂移、契约四份同步；模板模块**确实没有声明 `doctor` 钩子**。

### 14.4 对决策 6 的影响

PR #7 刚改过编译器且加了一条分支，说明提示词文案近期还在动。
**如果下一轮还要调文案，决策 6 选 C（只加 golden test、不改行为）比 A 更稳**；
等文案稳定了再做 A（下沉音频分支 + 统一 `prompt_sha256`）。
选 A 的话，golden test 的指纹更新必须能说清「是按 PR #7 的固定引用分支变的」。

---

## 15. 第三轮：路线决策与计划边界

### 15.1 决策

2026-11-12 晚用户拍板「**不用从头开始做，保留 v1 的计划并完善**」。
即 §1–§14 的四块问题、六步顺序、六个决策、PR #7 后的复核约束全部成立；
本轮只补内容，不换载体、不重写模块。

此前评估过另一条路：参考模板库业务语义、按 `api/docs/EXTENDING.md` 新建 v2 模块
（六阶段：定名 → 骨架 → 数据 → 能力 → 生成链 → 契约收口；数据策略
A 全新表 / B 一次性迁移 / C 双写灰度）。**该方案不再执行**，但核查结论不丢：

- v2 的「目标形状」直接用作 v1 改造的验收标准（§16 / §17 两张表就是它）；
- 「不写 `startup`、用 `:read:all` 权限点、抢全局唯一命名空间、`--slot 7` 的示例码
  `40971` 会撞 `TemplateBusy`」等结论**只适用于新建模块**，照搬回 v1 会改错（见 §19）。

### 15.2 一句话口径

> 把 v1 从「一个模块自己干完八件事」改成「**八条链路各只留业务决策**」——
> 素材走 `LibraryLookup`（v2，第四轮修订，见 §21）、模型能力由 `model_driver` 提供目录与驱动并交给
> `GenerationRunner` 统一提交轮询、落盘／取件／引用／恢复分别走
> `infra/archive`＋`media_probe`、`core.serving.Served`、`core/prompt_refs`、
> `generation/recovery`；实体工作量只有**八个局部换实现 + 一个方法补齐**。

### 15.3 「拆到其他模块」的正确说法

用户点名的三条方向都对，但**不能实现成模块间 import**：
`test_modules_do_not_import_each_other` 对 `modules/` 下横向 import 零容忍。
已复核 `modules/template/` 全目录，没有任何 `from modules.<别的模块>`。
正确形态是「**能力上移到框架层 / 经注入口取**」：模板只认 `core/ports/` 协议、
`ModuleSpec` 声明与 `generation.runner`，实现由 `modules/asset`、
`modules/model_driver`、`infra/*` 在装配期注入（`PORT_CATALOG` 登记）。

---

## 16. 八条链路拆解

> 「业务该留」= 留在 `modules/template/`；「框架已提供」= 直接调、不再自己写；
> 「缺口」= 本轮要补的那一跳。最后一列是并入 §11 的哪一步。

| # | 链路 | 业务该留 | 框架已提供 | 缺口 | 并入 |
|---|---|---|---|---|---|
| A | **素材封装**：`PUT /replacement-packs/draft` → `publish` → 按 `pack_id+pack_version` 冻结读 | 草稿/发布规则、`revision` 乐观锁、`fixedReferences` 语义 | 现状是 v1 `core/ports/media.py::AssetLookup` + `core/ports/assets.py::AssetReferenceSource`；**目标**是 `core/ports/library_lookup.py::LibraryLookup` + `core/ports/library_references.py::LibraryReferenceChecker` | **整条输入链仍是 v1，须迁 v2**（§21，约 25 文件）；不是第三轮说的「已对别动」 | 步 2.5 |
| B | **视频模板配置**：`create` / `update_prompt` / `pin` / `attach_source` / `find_for_run` | `{{Mixed N}}` 编排、`mediaOrder`、`fixedReferences`、全库唯一蓝本 | 素材解析现状在 `AssetLookup`（随 §21 迁 v2） | `catalog.json` 当库、`threading.Lock`、20 份备份、零 owner（问题 A/B）；另需随 §21 切 v2 id | 步 2、3 + 2.5 |
| C | **准备 `prepare`**：`POST /templates/replacement-runs`（全模块最重 IO） | 参数校验、渠道与范围判定、spec 组装、四类提示词改写规则 | `get_archive_storage().materialize/link`、`core/prompt_refs.py::translate_prompt:36`、`core/library_text.py::validate_text_refs:87` / `inject_text_refs:111` / `inject_masked_text_refs:202`、`infra/media_probe.py::encode_text:77`、`infra/archive/base.py::read_json:200` / `write_json:217` | Node 编译器没有端口（`replacement_service.py:1228` 直接 `get_libtv_settings().node`）、文本落盘绕开 `encode_text`（`:1251`）、JSON 读写各走一条路、投影散在三处 | 步 0、4、5 |
| D | **模型调用**：`execute` → `GenerationRunner` | 「冻结渠道 → model id」+ `strict_model` / `fail_on_model_gone` 两个声明 | `ModelRouter.resolve()` + `generation/runner.py`、`infra/ai/router.py::provider_for()`、driver 的 `submit/poll` | `params["archiveRunId"]` 隐式协议、`execute_stage()` 直连（见 §16.1） | 记为契约债 |
| E | **成片交付**：`publish_output` → `output_key` → `output()` | 状态机与终态闸门 | `generation/runner.py:595` 收尾逻辑调 `getattr(pipeline, "archive_request", None)`（`:615`）；`core/ports/library_ingest.py::IngestRequest.archive_key:81-87`；`modules/library/ingest.py:36` 注释明写为它预留 | **W1（新增 P0）**：`ReplacementPipeline` 无 `archive_request()`，成片只停在 `runs/{id}/outputs/generated.mp4`；`image/pipeline.py:406`、`task/pipeline.py:162` 已实现 | 步 0.5 |
| F | **取件／预览**：`input_file` / `input_preview` / pack preview / `output` | 取数是否成功的业务判断 | `core/serving.py::Served:70`、`infra/media_probe.py::inspect:116` | 三个端点裸 `Path` + `FileResponse`（`replacement_router.py:132,143`、`pack_router.py:104`），MIME 硬编码 `image/jpeg`（`pack_service.py:384,393`）；只有 `output()` 用 `Served`（`replacement_service.py:597`） | 步 3 |
| G | **权限与授权闸** | `authorization_gap()` 的业务规则（封装是否发布、固定引用是否仍有效）、`reconfirm(confirmCharged)` | `ModuleSpec.permissions` 五个码 + `api/deps.py::require` | 调用点分散（`replacement_service.py:780`、`worker.py:513` 等），收成单一授权服务；落表时补 `created_by` | 步 3 |
| H | **恢复／重试／启动** | `submission_unknown` 等状态与人工出口 | `generation/recovery.py::recover_kind:122` / `recover_declared:165`、`generation/runner.py::_backoff:875` | `worker.py:479/566` 用字符串码 `RETRY_EXHAUSTED` / `OWNER_DISABLED`，`resume()` 失败只写日志不发事件 | 步 0 |

### 16.1 模型调用链：模板一行都不直接调 `model_driver`

用户点名的「模型调用方式在 `model_driver` 实现」，准确说是**三段、模板只给一个 id**：

1. **目录与就绪**（`channels.py::channel_catalog()`）：只问 `ModelRouter.resolve()`、
   `provider_for(allow_fallback=False)`、`InputPreparer.unavailable_reason()`，
   回答「这个渠道现在能不能选」，**不提交**。
2. **提交／轮询/重试/超时/恢复**（`generation/runner.py`）：`pipeline.load()` 用
   `CHANNEL_MODELS[run_channel(configuration)]` 把冻结渠道换成 model id 塞进
   `GenerationRequest.model`（`pipeline.py:74`），再由 `runner._provider_for()` →
   `infra/ai/router.py::provider_for()` 找到 driver 实例调 `submit/poll`。
3. **模板侧三处碰供应商实例全是元数据**：`_snapshot_submit_mode()` 读
   `supports_poll`、`channel_catalog()` 问就绪、`_prepare_channel_inputs()` 调
   `prepare_inputs`。

所以结论是「**供应商实现 + 模型目录 + 就绪判断在 `model_driver`，调用编排在
`generation`，模板只提供冻结的 model id 和三个声明**」。

**明确不做**：不要把整条链改成 `get_model_generation().submit()`
（`core/ports/model_generation.py:136`）。那个门面落 `driver_jobs` 一行、出参四个状态；
替换链有 `waiting_free` / `needs_login` / `submission_unknown` / `reconfirm`、
三道授权闸与计费模式，换过去等于把防重复计费的命门丢掉。正确拆法是
**能力下沉、状态留在本模块**。

**两处残留耦合不是模型能力**：`replacement_service.py:1228` 的
`get_libtv_settings().node` 要的只是 `node` 可执行文件；`worker.py:612/681` 的
`execute_stage("upload"/"download")` 是 LibTV 执行器协议。
`pipeline.py:83` 的 `params["archiveRunId"]` 则是隐式协议——
`infra/ai/libtv.py:294`、`modules/model_driver/drivers/bailian.py:392` 校验后
回工作区读 `run.json`。前者抽到 `core/config`，后者见决策 9。

### 16.2 两个「必须留」的实现

- **`run.json` 投影**：`bridge.mjs` 是独立子进程、只认文件系统，数据库是权威但它读不到。
  除非重写执行器，否则投影必须留；能做的是收进一个 adapter，别散在
  `workspace.py` / `worker.py` / `pipeline.py` 三处。
- **防重复计费状态机**：`submission_unknown` / `reconfirm(confirmCharged)` /
  `strict_model=True` / `fail_on_model_gone=False`（§1.3 一行别碰）。

---

## 17. 能力归属核对：三条点名 + 六组自建副本

### 17.1 三条点名能力：一半已经是对的

| 用户提法 | 现状 | 处置 |
|---|---|---|
| 素材来源资产库 | **输入侧仍是 v1**：`AssetLookup` 多处调用、删除保护靠 v1 `AssetReferenceSource`；只有 `{{Text N}}` 与生图 `referenceLibraryIds` 已是 v2。输出侧另缺一跳（W1） | **输入侧整体迁 v2（§21）** + 补 `archive_request()`；原「不迁」口径作废 |
| AI 模型能力来源 `model_driver` | **已对**：目录/驱动/就绪判断都在，提交轮询在 `GenerationRunner`（§16.1） | 别换统一门面、别把模板改成拿供应商实例 |
| 模板只实现业务 | **部分**：状态机、编排规则、授权闸、评价是业务该留；`catalog.json`、裸 SQL、归档目录/锁/投影、`template_packager` 位置是自建 | 留 4 类、搬 4 类，对应 §11 步 1–5 |

### 17.2 三条之外，六组「只用了框架一半」的副本

| 能力域 | 模板侧副本（证据） | 框架已有 | 处置 |
|---|---|---|---|
| 提示词与引用 | `{{Mixed N}}` / `{{Text N}}` 的解析、校验、渲染散在 `video_template_store.py`、`video_template_bindings.py`、`replacement_service.py`、`prompt_media.py`、`manifest.py`，Node 侧另有正则 | `core/prompt_refs.py::translate_prompt:36`（模板已在 `:1244` 用）、`core/library_text.py::validate_text_refs:87` / `inject_text_refs:111` / `inject_masked_text_refs:202`（`:1250` 已用） | 收口调用点，不重写规则 |
| 文本与 JSON 落盘 | `replacement_service.py:1251` 自己 `write_text(..., newline="\n")`；`video_template_store.py:133` `json.load`、`workspace.py:104,119`、`worker.py:243/782/870` 自己 `read_text`；20 份备份轮转 | `infra/media_probe.py::encode_text:77`、`infra/archive/base.py::read_json:200` / `write_json:217` / `checksum` | W3，与步 4 同批 |
| 并发协调 | `catalog.json` 的进程内 `threading.Lock`（§2.1，注释自认「只在单实例成立」）、手写 CAS、`UNIQUE(pack_id, version)` 抢版号、`StaleDataError` 重试 | `infra/archive/base.py::lease:63`（跨进程文件锁，`worker.py` 自己已在用）、`core/coordination.py` | 落表后由数据库事务 + partial unique index 取代（§2.4） |
| 取件与媒体 | 三个裸 `Path` + `FileResponse`、硬编码 `image/jpeg`（见链路 F） | `core/serving.py::Served:70`、`infra/media_probe.py::inspect:116` | W2，步 3 |
| 错误与事件 | `worker.py:479/566` 字符串码、`pipeline.py:266` 用「`占用` in error」判状态；`resume()` 失败只写日志 | `core/exceptions.py::ModuleErrors:76`、`generation/events.py::emit:32`、`db/outbox.py` | W7，步 0；§6.2 的「只改前端要分支的几个」口径不变 |
| 恢复与退避 | 自有 `resume()`（`__init__.py:56`）、手写有界退避（`worker.py:72-76`）、业务态与 `framework_status` 双轨 | `generation/recovery.py::recover_kind:122` / `recover_declared:165`、`generation/runner.py::_backoff:875` | **v1 保留**（框架显式允许），只补事件 |

其中两条是**真实缺陷，不只是风格**：

- `infra/archive/base.py::read_json:200` 的 docstring 记录了「实测轮询期间读到
  `FileNotFoundError`，接口随之 500」，所以框架做了 3 次重试；模板自写的
  `read_text()` 没有，而 `workspace.py` 写侧已经用了框架 `write_json`——
  **读写各走一条路**。
- `infra/media_probe.py::encode_text:77` 拦 `U+0000` 的理由写得很明白：PG text 拒存
  NUL，而上传链是「先 put blob、再落库」，失败会在对象存储里留一份没人引用的字节。
  `inputs/prompt.txt` 绕开了这一关。

### 17.3 不算绕开，别按报告误改

- 自有 `resume()` 是框架显式例外（`generation/recovery.py:170`「有 `startup` 的模块
  沿用自己的恢复器，框架跳过」；`task/__init__.py` 同样），**v1 保留**。
- `TemplateBusy` 全仓零引用（§6.2）；HTTP 限流走的就是 `rate_limit()`（§10.7/§10.8）。
- 时间源、`datetime.now(UTC)`、日志入口、`uuid4`、`asyncio.to_thread` 只是口径不统一，
  框架实现与模块实现语义等价（§10.9 M）。
- 回收站／分享／可见性的对应物在 `modules/library/`，按模块隔离红线不能直接 import；
  要复用只能把状态机上移 `core/`，那是架构决策，不是顺手重构。
- 评价（review）没有框架对应物，属于纯业务，该留。
- `pack_service.py` 内存切片分页是「框架已有 `PageParams` / `paginate()` 却没用」，
  归到落表那批（步 2），不单列一步。
- 最讽刺的一条：`core/serving.py:44` 的注释写明这个类型当初正是从
  `modules/asset/serving.py` 上移的，理由就是「第二个需要下载的模块出现时，才发现
  要么违规 import、要么抄一份」。模板库是第三个模块，又抄回了
  `FileResponse(path, media_type=...)` 的旧形状——W2 正是补这个回归。

---

## 18. 新增工作项 → 并入 §11 六步

§11 表格保持不变。新增项按依赖插进原步骤，**只新增一个独立小步**：

| 项 | 动作 | 落点 | 分级 | 并入 |
|---|---|---|---|---|
| **W1** | 补 `ReplacementPipeline.archive_request()`，成片进 v2 草稿箱 | `template/pipeline.py` | targeted | **步 0.5（新）** |
| W2 | 三个取件端点改 `Served`，MIME 改 `media_probe` | `replacement_router.py` / `pack_router.py` / `pack_service.py` | contract | 步 3（与契约变更同批） |
| W3 | 文本/JSON 落盘改调 `encode_text` / `archive.base` | `replacement_service.py` / `workspace.py` / `worker.py` / `video_template_store.py` | targeted | 步 4（与编译链同批） |
| W4 | Node 编译器抽 `core/ports/prompt_compiler.py` 端口（先补 golden test） | `core/ports/` + `infra/` + `PORT_CATALOG` | framework | 步 4 |
| W5 | `node` 路径从 `core/config` 取，删 `get_libtv_settings()` 依赖 | `replacement_service.py:1228` | targeted/framework | 步 5 |
| W6 | 授权闸调用点收成单一授权服务（规则不变） | `replacement_service.py` / `worker.py` | targeted | 步 3 |
| W7 | 恢复失败改发事件，可选 `@periodic` 对账（**不删 `startup`**） | `worker.py` / `__init__.py` | targeted | 步 0（观测项） |
| W8 | 时间源统一 `utcnow`、`import_legacy` 路径校验、工作区 GC 保留策略 | 见 §10.9 M | targeted | 步 5 |
| **W9** | **v1 `assets` → v2 `library_assets` 全链迁移**（端口/删除保护/权限口径/契约与前端数据源/演示数据与导入/dual-read 兼容期） | 见 §21 | full（迁移 + 装配声明） | **步 2.5（新）** |
| **W10** | **资产库 v2 封装作为模板调用者的参考**：扩 `LibraryLookup` 按 pack id 展开成员 + `PrepareReplacement` 显式字段 + `role` 白名单 + pack/run 双向删除保护 | 见 §23.5 | framework（端口/声明）+ contract（契约） | 步 1 / 2.5a / 2.5c / 步 3 |
| **W11** | **v1 素材库整体下线**：先清完 §23.2 的四类使用方，再删端点 / 端口目录 / 守卫集合 / library legacy 链路 | 见 §23.2 | full（装配声明 + 删端口） | **步 6（新，步 2.5e 之后）** |

**步 0.5 单独说明**：它最小（一个方法）、零 schema 变更、立刻能让成片出现在草稿箱；
因为**改变可见行为**，不放进「只加观测、不改行为」的步 0，紧跟步 0 独立提交。

**步 2.5 单独说明**（第四轮新增，见 §21）：v1 → v2 素材库迁移是**本轮第二个 `full`**
（动迁移 + 装配声明），与步 2 的 `catalog.json` 落表**不合并提交**——两件事、各一次
迁移、各一份契约生成物。W1 不依赖它，可以先做；但 W9 完成前，模板模块的
`get_asset_lookup` 不会归零。

**步 6 单独说明**（第六轮新增，见 §23.2）：v1 素材库下线**不是**模板迁完的自动结果。
全仓核查确认还有 `modules/library` / `modules/task` / 前端旧入口 / `modules/asset`
自身四类使用方，必须先清理再下线；历史冻结快照靠 `ReplacementRunInput.storage_key`
回放，不改写。步 6 排在步 2.5e（dual-read 观察期结束）之后，独立提交。

**分级依据与验收**（沿用用户定的「不每次跑全量」约定）：

- 每步开工前跑 `scripts/select_tests.py --print`，以算出的级别为准，
  不「保险起见跑全量」；分级理由写进提交说明。
- 固定跑 `test_architecture.py` / `test_internal_models.py` /
  `test_openapi_contract.py` + `ruff check src tests` + `mypy --platform win32 src`。
- 步 2 是唯一 `full`（动迁移 + 装配声明）；步 3 / W2 判 `contract`，
  另跑 `app export-openapi --check` 与前端 `gen:routes --check`、
  `gen:mock-coverage --check`（`npm run verify` 不含这两个）。
- 新增守卫按「还原改动后必须变红」自查。改接口时四份生成物一起重生成并提交，
  `--check` 只比对不写盘。
- W1 反向验证：造一条成功 run，断言草稿箱新增一行且 `archive_key == run.output_key`；
  删掉 `archive_request()` 后守卫必须变红。
- W2 反向验证：把任一取件端点改回 `FileResponse` 守卫必须变红；XHR 取 blob 的端点
  必须 `stream()`，只有 `<img>` / `<video src>` 才可 `remote`（307）。

---

## 19. 硬约束与不做清单（本轮新增，与 §8「不做的事」并列）

1. 不新建 v2 模块、不删 v1、不双写灰度（两个事实来源违反 `EXTENDING.md §5.7 ②`）。
   **消歧（§23.1）**：这里的「v1」指**模板库模块本身**（不删掉重建，第三轮路线），
   不是 v1 `assets` 素材库；后者按用户第六轮拍板是**要下线的**，见 §23.2。
2. 不把能力「拆到其他业务模块」；只上移框架层 / 经端口注入。
3. **不删 `startup=resume`**（`template/__init__.py:56`）。v2 的「不写 startup」
   只适用新建模块；v1 是框架显式例外（`generation/recovery.py:170`）。
   要补的是失败事件与可选周期对账，不是换恢复器。
4. **不换 `get_model_generation()` 门面**（理由 §16.1）。
5. 不动 `pipeline.py` 的 `strict_model` / `fail_on_model_gone` / `state_version` CAS（§1.3）。
6. `run.json` 投影必须留（§16.2），只收口成一处 adapter。
7. **输入侧必须迁到 v2**（用户 2026-11-13 拍板，第三轮「不迁」口径作废，见 §21）：
   `assets`（v1）与 `library_assets`（v2）并存，`core/ports/library_lookup.py:3-10`
   要求「调用方必须显式选择问哪一个」——迁移期内**禁止把两个端口合并成一个隐式
   查询**（例如「先查 v2 再回退 v1」的兜底），dual-read 必须显式分流（§21.4）。
   W1 让成片进 v2 草稿箱，与输入侧迁移是两件事，但属同一目标形状。
8. 不重命名 5 个权限码（§1.2）、不为拆而拆两个超千行文件（§6.1）、
   不把 `replacement_runs_root` 强上对象存储（§5.1）。
9. 一次提交一件事、不按前后端目录切分；**不自动 push**。

---

## 20. 新增决策与开工前复核

### 决策 7：成片进草稿箱要不要开关？

建议**直接做、不加开关**。`core/ports/library_ingest.py:81-87` 的 `archive_key` 分支与
`modules/library/ingest.py:36` 的注释就是为 `ReplacementPipeline.archive_request()`
预留的，框架侧零改动；加开关反而多一个「生成成功但草稿箱里没有」的静默态。

### 决策 8：Node 编译器收口做到哪一档？

| 选项 | 内容 | 代价 |
|---|---|---|
| A | 新增 `core/ports/prompt_compiler.py` 端口 + 注入口三件套 + `PORT_CATALOG` 登记，实现放 `infra/`（含 30s 超时与 vendor 快照） | framework，动框架层 |
| **B 先行** | 只把 `node` 路径挪到 `core/config`，删掉 `get_libtv_settings()` 依赖，编译调用点不动 | targeted，最小 |
| C | 不动，只记契约债 | 零 |

推荐 **B 先行、A 与步 4 同批**（先有 golden test 再动编译链，§11 的原则不变）。
Node 编译器是提示词编排，**不进 `model_driver`**——它不是模型能力。

### 决策 9：`archiveRunId` / `execute_stage` 本轮收不收？

建议**不做，记为契约债**。`pipeline.py:83` 的 `params["archiveRunId"]` 被
`infra/ai/libtv.py:294` 与 `modules/model_driver/drivers/bailian.py:392` 校验，
`worker.py:612/681` 直接调 `execute_stage("upload"/"download")`：收成显式端口要同时动
`infra/ai` 与两个驱动，风险高、收益只是显式化。等替换链稳定后单独排期。

### 开工前复核清单

- `scripts/select_tests.py --print`、`app doctor`、`app export-openapi --check`；
- §14.1 的硬约束仍在：步 2 导入必须把 `rules` / `media_order` / `generation`
  **整字段搬运**，否则 PR #7 的 `fixedReferences` 静默丢失；
- 步 4 的 golden test 必须覆盖「带固定引用的文字换场景」这组 spec（§14.2）；
- 步 3 的归属校验口径与 `authorization_gap()` 里 `asset.owner_id == pack.owner_id`
  对齐，别造第二套 owner 规则（§14.2）；
- `runs_root()` 现状是 WARN 而非零自检，要做的是升级成 FAIL（§10.5 / §14.2）。

---

## 21. 第四轮：素材库 v1 → v2 迁移（用户 2026-11-13 拍板）

### 21.1 结论：有，而且是主链路，不是边角

核查范围：`api/src/modules/template/` 全目录、`replacement_runtime/`、
`studio/src/features/{archive-library, video-templates, replacement-templates}`。
约 **25 个文件 / 60+ 处**使用 v1 `assets`。最有说服力的证据在框架侧：
`core/ports/library_lookup.py:5` 的注释原文写着「`core.ports.media` 回答的是
`assets` 表里的旧素材（**模板库 prepare 用它**）」——这是设计上明确记录的现状。

按五类分布：

| 类别 | 证据（文件:行号） |
|---|---|
| **持久化列**（模板自己的表） | `models.py:83` `TemplatePack.cover_asset_id`、`:126` `TemplatePackReference.asset_id`（+ `:135` 索引）、`:156` `TemplatePackVersion.reference_ids`（冻结 JSON 数组）、`:287` `ReplacementRunInput.asset_id`（+ `:293` `storage_key`）。`models.py:8-10` 明确**全项目无 ForeignKey**，所以迁移是改语义、不是换外键约束 |
| **读路径**（端口调用） | `pack_service.py:261/294/346/379`、`template_references.py:29`、`authorization.py:69/86`、`replacement_service.py:719`、`video_template_store.py:268/325` 全是 `get_asset_lookup().get_many()`，返回 `core/media.py::StoredMedia`（`owner_id` / `shared` / `deleted_at` / `category` / `path` / `sha256`） |
| **删除保护** | `template/__init__.py:33-42,59` 的 `ModuleSpec.asset_reference_sources` 声明 `template_pack_references.asset_id`；`asset_references.py:26` `PackAssetReferences` → `repository.py:256-280 packs_referencing()`；走 v1 `core/ports/assets.py::AssetReferenceSource` + `ask_asset_references()` |
| **契约与前端** | `video_template_schema.py:32,57,89,109,118` 的 `asset_id` / `source_video_asset_id`；`video_template_router.py:111`；`replacement_router.py:67` 的 `actor.has("asset:read")`；前端选图器 `VideoTemplateLibrary.tsx:290 useArchiveAssets('source')`、`archive-library/PackEditor.tsx:19,42` 的 `referenceIds`、`entities/asset/api/archive.api.ts` 的 `/assets...` 端点 |
| **演示数据与导入** | `seed.py:116-129` 裸 SQL `SELECT id FROM assets WHERE category=...`；`import_legacy.py:308` 写 `asset_id`、`:352-354` 拼 `{api_prefix}/assets/{id}/file`、`:439-446` 读 `input-map.json` |

**已经是 v2、不用迁**（迁移时当参照实现，别重复造）：

- `{{Text N}}` 文本注入：`core/library_text.py::freeze_text_refs` →
  `get_library_lookup().read_text_many()`；`replacement_runs.text_library_ids`
  （`models.py:255`）、`schema.py:133,195` 已是 v2。
- 生图/视频工作台参考图 `referenceLibraryIds`（`studio/src/pages/studio/StudioPage.tsx:56`、
  `entities/library/api/library.api.ts` 的 `/library/assets...`）。
- 输出侧 `library-ingest` 草稿箱（W1 补上之后）。
- `library-lookup` / `library-ingest` / `library-references` 三个端口本身。

### 21.2 目标形状

迁移完成后，模板模块内 **`get_asset_lookup` / `AssetReferenceSource` / `assets` 字面量
零引用**；所有素材 id 都是 `library_assets.id`，请求字段统一为
`referenceLibraryIds` / `textLibraryIds` / `sourceVideoLibraryId`（命名以契约为准，
**不要改回 `referenceAssetIds`**——`api/tests/test_architecture.py:620` 的
`_REFERENCE_FIELDS` 与 `studio/src/shared/ui/ReferenceImages.tsx` 明确两套字段
校验规则完全不同）；读权限判据只有 `LibraryLookup.readable_ids()` 一份
（转发 `modules/library/access.py`）。

**分类口径见 §22**：v2 没有 `category`，也不补一个等价字段；「角色 / 场景 / 产品 /
音频」是**模板封装时的槽位绑定**，标签与 `kind` 只用于挑候选，落库的是冻结的 asset id。
所以步 2.5 不是「把分类列换个名字」，而是「把分类从素材行移到封装关系」。

### 21.3 五个必须同步改的层（缺一层就静默出错）

| 层 | 现在 | 改成 | 风险 |
|---|---|---|---|
| **数据／迁移** | 无 FK，列存 v1 id；`template_pack_versions.reference_ids` 是冻结 JSON | 新 alembic revision；新增来源标记列（v1/v2 id 空间），历史行不改值 | `full`；`alembic check` 零漂移 |
| **端口／删除保护** | `AssetLookup.get_many()`；v1 `AssetReferenceSource` + `ModuleSpec.asset_reference_sources` | `LibraryLookup.get_many()/readable_ids()`；v2 `LibraryReferenceChecker` | **框架能力缺口，见下** |
| **权限口径** | `StoredMedia.owner_id / shared / deleted_at / category / path` | `LibraryAssetRef`（`library_lookup.py:86-96`）：`kind` 替 `category`、`state != ACTIVE` 替 `deleted_at`、`visibility + readable_ids()` 替 `shared`、按 `storage_key` 走对象存储 `materialize` 替 `path` | `authorization.py:83,100`、`pack_service.py:267,381`、`template_references.py:49-50` 的 owner/共享校验必须重写，**判据只认 `readable_ids()`**；`library_lookup.py:138-146` 明说「重写必然漂移，方向总是多给权限」 |
| **契约／前端** | `referenceIds` / `assetIds` / `sourceVideoAssetId`、`asset:read`、四份生成物 | `referenceLibraryIds` 等 v2 字段名 + v2 权限码（具体码名以 `permissions.ts` 实际为准，**待确认**）；**按 §21.6 第 4 项已拍板「保留旧 UI」**：组件与页面布局不动，只把数据源从 `/assets...` 换成 `/library/selection` 或 `/library/assets...`，不引入六个入口那套新选择器 | 四份生成物一起重生成；`app export-openapi --check` + 前端 `gen:routes --check` / `gen:mock-coverage --check` |
| **演示数据／导入** | `seed.py` 裸 SQL、`import_legacy.py` 拼 v1 取件 URL | 扩 `LibraryLookup` 的「按条件列 id」能力（v2 无 `category`，按 `kind` / 标签查）；导入显式声明 id 空间 | 这正是 §4.3 说的端口能力缺口，只是对象从 v1 换成 v2 |

**框架能力缺口（本轮唯一的前置 framework 工作，必须先做）**：v2 的删除保护
`core/ports/library_references.py` 目前只有 `register_library_references()` 单例注册
（`owner="modules.library"`），`library_reference_gaps():80-82` 只查这一个来源；
文件头 `:17-21` 自己写着「跨模块来源由各自的 `ModuleSpec` 声明（**M2 起接入时补**）」。
也就是说 v2 **没有** v1 那套 `bind_asset_reference_sources()` +
`declare_expected_reference_sources()` + AST 静态守卫。模板接进来若只注册一个来源，
漏注册时 `get_library_references()` 仍会 fail-closed（这是好的），但**「新增一个写
`library_asset_id` 的模块忘了声明」不会被 CI 发现**——删除成功、模板裂图，静默且不可逆。
所以步 2.5a 要先补：`ModuleSpec` 的来源声明字段 + 期望集合对账 + AST 守卫，
并按「还原改动后守卫必须变红」自查。**§21.6 第 3 项已拍板按「完整守卫」档做**，
具体形状（含 pack/run 双向来源）见 §23.3。

### 21.4 冻结快照与兼容期（最容易出事的地方）

- `TemplatePackVersion.reference_ids`（`models.py:156`）、`ReplacementRunInput.storage_key`
  （`:293`）、`input-map.json` / `media-inputs.json` 是**已冻结的历史证据**；
  `asset_references.py:6-8` 明说历史版本不算「引用」。**绝不批量改写**成 v2 id，
  否则历史任务不可复现。
- 因此需要 **dual-read 兼容期**：同一列可能混有 v1/v2 id，靠新增的来源标记显式分流到
  `get_asset_lookup()` 或 `get_library_lookup()`。`core/ports/library_lookup.py:8-10`
  明确禁止「合并成一个素材查询」——**不能写一个「先查 v2、查不到回退 v1」的隐式兜底**，
  那会让切换那天才发现某一侧悄悄读到了另一半数据。
- `ReplacementRunInput.storage_key` 已物化字节，历史 run 回放不依赖 `assets` 表，
  可原样保留；这也是 v1 下线后历史任务仍可复核的依据。
- PR #7 的 `rules.fixedReferences` 里冻着 `assetId`，迁移时同样**整字段搬运**，
  并把「固定引用是否仍有效」的校验切到 v2 `readable_ids()`（§14.1 的约束继续生效）。

### 21.5 执行顺序（并入 §11，新增「步 2.5」）

| 子步 | 动作 | 分级 | 备注 |
|---|---|---|---|
| **2.5a** | 补 v2 删除保护的声明式对账（`ModuleSpec` 字段 + 期望集合 + AST 守卫） | framework | **前置**，先做；改框架层要跑 `tests/test_select_tests.py` 自查 |
| **2.5b** | 扩 `LibraryLookup` 查询能力 + 新增来源标记列 + alembic 迁移 | full | 本轮第二个 `full`；与步 2 **分开提交** |
| **2.5c** | 后端读路径与三道授权闸切 v2（`readable_ids()` 单一判据） | contract | 四份生成物一起重生成；`export-openapi --check` |
| **2.5d** | 前端选图器与字段名切换 + `seed.py` / `import_legacy.py` | contract | `npm run verify` + 两个 `--check` |
| **2.5e** | dual-read 观察期结束、移除 v1 分支 | full | **单独排期，不在本轮**；v1 是否下线**已拍板**（§21.6 第 1 项）：清完 §23.2 四类使用方后由**步 6** 整体下线 |

每步开工前跑 `scripts/select_tests.py --print`；固定跑三份守卫 +
`ruff check src tests` + `mypy --platform win32 src`；`full` 级每次换全新
`--basetemp`。步 2.5 与步 2 不合并：一次提交一件事。

### 21.6 四项拍板结论（2026-11-13，全部已定 → §23）

1. **已拍板（第六轮）**：模板库优化完成后，**若没有其他地方用到 v1 素材库就直接下线**。
   历史冻结快照靠 `storage_key` 回放、不改写。核查结论是**当前仍有 4 类使用方**，
   所以这是「先清理、再下线」的**步 6**，不是迁完自动发生 —— 清单见 §23.2。
2. **已拍板（第五轮，§22）**：问题是「缺了模板封装链路」，不是「缺一个映射字段」。
   分类由**封装时绑定**表达：角色/场景/产品按标签筛候选、音频按 `kind=audio`
   筛候选，选中后冻 asset id；素材侧 `category` 判据整条删除，不新增字段、
   不塞进 `role`、不按标签动态解析。
3. **已拍板（第六轮）**：v2 删除保护补到**完整守卫**档 —— 补齐 v1 那套
   「`ModuleSpec` 声明 + 装配期对账 + AST 守卫」，不是只给模板注册一个来源。
   具体形状见 §23.3。
4. **已拍板（第六轮）**：前端**先保留旧 UI** —— 只换数据源与字段名，不换选择
   组件与页面布局，不引入六个入口那套选择器。范围收窄见 §23.4。

### 21.7 与既有步骤／结论的关系

- §19.7 原「输入侧不迁」作废；§15.2 一句话口径已改为 `LibraryLookup`；
  §16 链路 A 的「已对别动」改为「迁 v2」；§17.1 与 §18 同步更新。
- **W1（步 0.5）不受影响**：输出侧进 v2 草稿箱，不依赖输入侧迁移，可先做。
- `modules/library/__init__.py:47` 自己仍用 v1 `AssetReferenceSource` 声明
  `LibraryAssetGeneration.reference_asset_ids`（生成溯源快照）——那是 v2 模块内部的
  历史快照，不在模板迁移范围，**别顺手改**。
- 迁移目标是「模板模块不再问 v1」，**不是**「把两个端口合并成一个」；
  `core/ports/library_lookup.py:3-10` 的并存期约束始终是硬约束。

---

## 22. 第五轮：模板封装链路（用户 2026-11-13 拍板，回答 §21.6 第 2 项）

### 22.1 口径

> 「有 `category` 映射这个问题，是因为**缺了一条模板封装链路**」。

目标形状是**一个**模板封装页面 / 实体，一次把下面四件事定义完；素材本身不带分类，
分类由「把它绑到哪个槽位」表达，绑完就是冻结的 id。

| 组成 | 来源 | 绑定方式 |
|---|---|---|
| 参考视频 | 资产库 v2 | 上传走 `POST /library/source-videos`（`origin=template_source`）；模板存 v2 原片 id |
| 固定提示词 | 封装页输入 | `prompt` + `prompt_sha256`，含 `{{Mixed N}}`；编辑走 PR #7 的 `PATCH .../prompt` |
| 角色 / 场景 / 产品图 | 资产库 v2 | **按标签筛选候选**，选中后冻 `asset id`（跨包装的再带 `packVersion`） |
| 音频 | 资产库 v2 | **按 `kind=audio` 筛选候选**，选中后冻 `asset id` |

**两段式封装（用户 2026-11-13 二次澄清，本节的准绳）**：

1. **用户 A（封装者）**上传参考视频、写提示词「把视频里的产品换成 {产品}」——
   `{产品}` 是**槽位声明 / 占位**，不指向任何具体素材。A 定义的是「这里需要一个产品」
   以及它的约束（标签 / 用途），不是「这里用这张图」。
2. **用户 B（调用者）**在模板库选**具体产品**（一个已发布素材包 + 版本）发起生成。
   具体 id 在 `prepare` 这一步才确定，并立刻冻进 `replacement_runs`。

这与 §22.4 的 A/B 二分不冲突：判据不是「什么时候绑」，而是**「有没有一次显式选定、
之后有没有把结果冻下来」**。两段式满足判据（B 显式选 + `prepare` 冻结），
所以归 A 一侧；B 的病根是「无人选定、系统按标签自动取当下集合」，这里没有。

`assets.category`（`source/character/product/scene/audio`）是 v1 的历史实现，
不是业务概念。**§21.6 第 2 项的答案：不做字段映射，删除素材侧分类判据**，
改成「封装侧声明槽位 + 调用侧显式绑定」。这样 §21.2 的目标形状才自洽：
v2 无 `category` 也不需要补。

### 22.2 现状已有的一半（不要重做）

- `VideoTemplateDraftIn`（`video_template_schema.py:38-79`）已经是「封装一条视频模板」：
  `name` / `prompt` / `source_video_asset_id` / `pack_id` / `default_kind` /
  `media_order` / `generation` / `rules` / `pinned`，只是存在 `catalog.json` 里。
- `rules.referenceBindings`（`video_template_bindings.py:13-41`）把 `{{Mixed N}}`
  绑到**用途**（`source_video` / `front` / `multiview` / `detail`），不是绑到素材 id。
- PR #7 的 `rules.fixedReferences`（`VideoTemplatePromptIn.references`，
  `video_template_schema.py:114-135`）冻的是 `packId + packVersion + assetId`
  —— **正是「选中后冻 id」的现成形状**，别推倒重来。
- 提示词校验：`VerbatimText`、`prompt_sha256`、`PATCH .../prompt` 原地保存。
- v2 筛选入口全部现成：`GET /library/assets?tagIds=...&kind=image`
  （`router.py:90-119`；多选 OR 展开见 `service.py:259-270`）、`UploadKind.AUDIO`
  （`schema.py:246`）、`POST /library/source-videos`（`router.py:215-251`）。
  `service.py:248-252` 的注释甚至已写明「模板库要同时取 `template_source` 与
  `imported`」——框架侧就是按这次切换设想的。

### 22.3 缺的那一半

1. **单一封装入口**：现在参考视频在 `VideoTemplateLibrary.tsx` 绑、参考图在
   `PackEditor.tsx` 挑、提示词在 `PATCH .../prompt` 改，三处分散且都按 v1
   `category` 筛；没有「一次提交 = 视频 + 提示词 + 四类绑定」的实体。
2. **筛选条件换 v2**：`useArchiveAssets(form.kind, ...)` / `useArchiveAssets('source')`
   改成 `GET /library/assets` 的 `tagIds` + `kind`；`sourceVideoAssetId` 改 v2 id
   （字段名按 §21.2 的 `referenceLibraryIds` 一类口径，不要改回 `referenceAssetIds`）。
3. **删素材侧分类判据**：`replacement_service.py:728` 的 `category=="source"`、
   `:742` 的 `category==payload.kind`，`pack_service.py:268` /
   `template_references.py:50` / `authorization.py:83` 的 `category==pack.kind`，
   `video_template_store.py:727`、`seed.py:123` 裸 SQL、契约描述
   `video_template_schema.py:110`。判据统一为「槽位绑定的冻结 id +
   `LibraryLookup.readable_ids()`」，授权闸不再反推素材类型。

### 22.4 三条硬约束

- **标签只能当筛选条件，不能当运行时绑定；但「冻 id」有两个合法时点**：
  ① **封装时冻** —— PR #7 的 `fixedReferences`（跨包固定图，`packId+packVersion+assetId`）；
  ② **调用时冻** —— 用户 B 在 `prepare` 显式选 `packId + packVersion`，服务端取该
  不可变版本的 `frozen.reference_ids` 覆盖请求并写进 run（`replacement_service.py:261-275`），
  随后 `run.prompt`（`:390`）、`input-map.json`、`ReplacementRunInput` 全部冻结。
  **这是本项目主线**，所以上文「落进模板的是冻结 id」应读作「落进本次运行」。
  两者都满足「一次显式选定 → 结果冻结」，历史可复现。
  **禁止的是第三种**：模板只存标签、`prepare` 时按 `library_asset_tags` 自动取当前
  匹配集合。标签可多选、可就地解绑（`PATCH tagIds` 整体替换），自动解析会让历史任务
  随打/解标签漂移，`input-map.json` 与 `run.prompt` 对不上。别把 `referenceBindings`
  改成「按标签声明槽位」。
- **用途 ≠ 分类**。`PACK_ROLES`（`pack_roles.py:22-40`）的 `front/multiview/detail`
  回答「这张图在包里干什么」，与「角色 / 场景 / 产品」不是一个维度；
  标签只回答「候选池里有哪些图」，槽位用途仍走 `referenceBindings`。
  两者合并成一个字段后，多人换脸的 `front` 槽位与「人物」标签会互相污染。
- **音频按 kind 绑定 = 只从 `kind=audio` 里挑**，不是用 kind 区分音频种类
  （v2 里音频只有一种 kind）。`defaultKind` 保留为**模板级替换档位**，不由标签推导，
  否则同一个「这张模板是产品替换」出现两套分类源、两份真相。

### 22.5 对执行顺序的影响（并入原六步，不另起计划）

| 影响 | 归属 |
|---|---|
| 单一封装页面 + 契约字段（原片 id / 标签筛选 / 音频槽位） | 步 3（契约与归属）+ 步 2.5d（前端选图器） |
| 删素材侧 `category` 判据、改按冻结 id 授权 | 步 2.5c（后端读路径与三道授权闸） |
| `catalog.json` 落表时整字段搬运 `rules`（含 `referenceBindings` / `fixedReferences`） | 步 2（**PR #7 硬约束，不变**） |
| 老数据导入按 `type/kind` 回填 `origin`（原片 → `template_source`） | 步 2.5b |

§21.6 **四项全部已拍板**：第 2 项见本节，第 1 / 3 / 4 项见 **§23**。
按第 4 项，上表「步 2.5d（前端选图器）」应读作「**前端数据源与字段替换**」，
不换 UI；「单一封装页面」若涉及 `packId` 校验放宽，归步 3 单独提交（§23.4）。
W1（步 0.5 成片进草稿箱）不受影响，仍可先做。开工前仍需按
`scripts/select_tests.py --print` 判级，不因本节新增工作而「保险起见跑全量」。

### 22.6 现状核对：两段式已经成立，还差一步

**代码里已有，而且注释写得很直白**：

- `video_template_store.py:687-690` `find_for_run()`：「模板按**原片**绑定，`packId`
  只是『选中卡片时默认带出的那个包』：**换包照样命中同一条模板** … 于是提示词不用改，
  `{{Mixed 2}}` 永远是所选包的正面主图。」
- `replacement_service.py:216-218`：「视频模板按原片套用：**工作台里换产品包，
  模板照样生效 —— 提示词不动**，图片按用途（主图/三视图/细节）从当前素材包里取。」
- `replacement_service.py:261-275`：调用侧 `pack_id + pack_version` 必填，服务端取
  该已发布版本的 `frozen.reference_ids` 覆盖 `payload.reference_ids` —— 这就是
  「B 选具体产品」的落点，且选的是**不可变版本**，不是散图。
- `video_template_bindings.py:13-41` 的 `referenceBindings` 把 `{{Mixed N}}` 绑到
  **用途**，正是「A 只声明槽位」的现成表达。

**还差的一步（理想态 ≠ 现状）**：封装页 `VideoTemplateLibrary.tsx:114` 校验
「请选择已发布的替换素材包」，`:216-219` 强制选 `packId`，`:178-190` 按包内
`assetIds` 生成 `mediaOrder`；`video_template_store.py:246-267` 保存时也按包校验编号。
也就是 A 现在**必须**绑一个默认包，不是「只写占位」。目标是把 `packId` 从必填
降为**可选**：用途绑定模式下 A 只提交 `{{Mixed N}}` → 用途的槽位声明，B 调用时
再按用途从自己选的包里取图。后端模式已存在，主要改封装页与保存端校验。
这是**同一页面内的校验放宽、不是换 UI**，按 §21.6 第 4 项口径归 **步 3
（契约与归属）单独提交**，不并入步 2.5d 的字段替换（§23.4）。

**两个术语校正**：

- **`{产品}` 是业务概念，不是代码里的字面占位符**。项目只认 `{{Mixed N}}` /
  `{{ScenePrompt}}` / `{{ReplacementScope}}`：`promptMentions.ts:38` 对其它标记直接报
  「非法引用标记」，`video_template_bindings.py:29-36` 还要求每个 `{{...}}` 都在
  `bindings` 或 `fixedReferences` 里声明过。封装页靠 `@` mention 插入编号。
  若要让 A 直接写 `{产品}` 这类命名槽位，是**新增编译能力**，归步 4（编译链，
  先补 golden test 再动），不是本次迁移顺带能带的。
- **A 与 B 可以是不同人**。视频模板全库共享、零 `owner_id`，而 B 选的产品是 B 自己的
  资产；`authorization.py:83` 的 `asset.owner_id == pack.owner_id` 在这个场景不成立，
  授权闸必须只认 `LibraryLookup.readable_ids()` —— 与 §21.2 的迁移口径一致。

---

## 23. 第六轮：三项拍板 + 资产库封装作为参考（用户 2026-11-13）

> 用户原话：「1、模板库优化完成后，如果没有其他地方用到 v1 素材库，那就直接下线；
> 3、v2 删除保护补到完整守卫；4、前端先保留旧 UI。另外还有一点就是资产库有个动作
> 叫做封装，把多个素材封装成一个包，比如说同一个产品的三视图、细节图等，
> 用户 B 也能选择封装的产品作为参考。」

### 23.1 三项拍板与一处消歧

| 项 | 拍板 | 对计划的直接影响 |
|---|---|---|
| **1** | 模板库优化完成后，**若没有其他地方用到 v1 素材库就直接下线** | 步 2.5e 不再是「待定」；但全仓核查发现**当前仍有 4 类使用方**（§23.2），所以下线是「先清理、再下线」的**步 6**，不是模板迁完自动发生 |
| **3** | v2 删除保护补到**完整守卫**（即推荐档） | 步 2.5a 范围钉死为 v1 那套「声明 + 对账 + AST 守卫」（§23.3） |
| **4** | 前端**先保留旧 UI** | 步 2.5d 只换数据源与字段名，不换选择组件与页面布局；六个入口那套选择器不进本轮（§23.4） |

**消歧（必须记住）**：§19.1「不删 v1」说的是**模板库模块本身**不删掉重建
（第三轮路线），不是 v1 `assets` 素材库；后者按本轮拍板是**要下线的**。
两个「v1」指的不是一回事，§19.1 已就地加注。

### 23.2 第 1 项：「没有其他地方用到」当前不成立

全仓核查（`api/src` + `studio/src` + `api/tests`）：模板模块 13 处
`get_asset_lookup()` 迁走后，v1 仍有 **4 类使用方**：

| 使用方 | 证据（`文件:行号`） | 下线要动 |
|---|---|---|
| **`modules/library` 自己**（最容易被漏） | `__init__.py:43-53,108` 的 `ASSET_REFERENCE_SOURCES` 声明 `LibraryAssetGeneration.reference_asset_ids`；`asset_references.py:29-64`；`import_archive.py:54-55` 跨模块裸 SQL `FROM assets`；`cli.py:3,5,33-38,66-74` 的 `library import-archive` / `reconcile`；`observation.py:29,40-61` + `__init__.py:105` 的每日周期 `library.legacy.sync` | 生成溯源快照改存 `storage_key` / v2 id；legacy 导入与对账链路整体退役；裸 SQL 删除 |
| **`modules/task`** | `__init__.py:10,28-36,56` 声明 `video_tasks.reference_asset_ids` | 同上，或随 v1 端点一并下线 |
| **前端旧入口** | `studio/src/entities/asset/api/archive.api.ts:19-40`、`pages/assets/AssetsPage.tsx:7-23`、`modules/assets/module.ts:14-29`、`shared/api/schema.ts:1945-1967` | 路由与契约表删除。注意第 4 项「保留旧 UI」只约束**模板工作台**，不保护旧素材库页面 |
| **`modules/asset` 自身** | `api.py:37,40-149` 的 12 个端点、`seed.py:29-40`、`cli.py:217-265` 四个命令、`api/ports.py:135-157` 两条 `PORT_CATALOG`（asset-references / asset-lookup）、`asset/cli.py:4,21` 的 `app backup` 对 `Asset` 模型的 import | 端点 / CLI / 端口目录 / 声明集合（`test_architecture.py:657,692-693`、`test_archive_gc_schedule.py:11,23-24`）一起删 |

**三条硬约束**：

- 历史冻结快照**绝不改写**（§21.4）：`TemplatePackVersion.reference_ids`、
  `ReplacementRunInput.storage_key`、`input-map.json` / `media-inputs.json`
  原样保留；v1 下线后历史任务仍靠 `storage_key` 回放到字节，`assets` 行不再需要。
- `library.legacy.sync` 这条每日对账必须在下线**之前**停掉，否则 v1 表删掉后
  服务启动就挂着一个指向不存在表的周期任务（`ModuleSpec.periodic` 装配期对账
  会拒启动）。
- 下线判据要固化为守卫，不靠人工 grep：全仓 `get_asset_lookup` /
  `AssetReferenceSource` / `assets` 字面量零引用（历史快照注释除外）。
  **步 6 = 一次独立提交**，排在步 2.5e（dual-read 观察期结束）之后。

### 23.3 第 3 项：完整守卫的具体形状

现状：`core/ports/library_references.py:59` 是 `Port("library-references", multi=False)`
单例；`:80-82` 的 `library_reference_gaps()` 只回答「注册了没」；`:17-21` 文件头
明写「跨模块来源由各自的 `ModuleSpec` 声明（**M2 起接入时补**）」。
`modules/library/references.py:11-19` 只查 v2 自己两张表
（`library_pack_items` / `library_download_jobs`）。删除动作**已经接了这道闸**
（`lifecycle.py:282` 的 `purge()`、`:317` 的 `purge_expired()` → `get_library_references()`
→ `TrashEntryReferenced` 409，`errors.py:82-90`）—— 缺的是「新增引用方忘了声明」
的静态发现能力，以及跨模块来源的聚合。

照 `core/ports/assets.py` 的形状补齐，步 2.5a 的范围固定为：

| 新增 | 类比 v1 的对应物 |
|---|---|
| `ModuleSpec.library_reference_sources` 声明字段 | `api/module.py:139-143` 的 `asset_reference_sources` |
| `LibraryReferenceSource` / `bind_library_reference_sources()` / `declare_expected_library_reference_sources()` / `ask_library_references()` | `core/ports/assets.py:62-73,109-137,172-198` |
| `library_reference_gaps()` 由「单例是否注册」升级为**声明 vs 实际注册**的缺口聚合 | `assets.py:145-153` |
| `api/ports.py:215` 的 `is_registered` 改走 gaps 判定 | `:145` 的 `lambda: not reference_source_gaps()` |
| 装配期对账（`create_app()` / `discover_modules()`）调 `declare_expected_*` | `assets.py:123-137` |
| 两条 AST 守卫 + `_LIBRARY_REFERENCE_FIELDS`：`test_every_library_reference_writer_declares_a_source()` / `test_library_reference_sources_are_complete_after_discovery()` | `test_architecture.py:620,623-641,644-680,683-697` |

**实现选择（步 2.5a 开工时确认，不在计划里拍死）**：`get_library_references()`
返回单个 checker、`purge()` 只调一次，所以倾向**保留单一聚合器**
（`Port(multi=False)` 不动），把多来源放到「声明 + 对账」层，由
`SqlLibraryReferences` 聚合已登记来源 —— 不动调用侧契约，也比改 `multi=True`
更容易保持 fail-closed（`library_references.py:6-11`）。

**两个必须排除的误报**：`referenceLibraryIds` / `textLibraryIds` 是 v2 字段名，
新守卫要显式把它们与 v1 `_REFERENCE_FIELDS`（`reference_asset_ids` /
`reference_ids` / `referenceIds`）分开，命名规则见 `image/schema.py:101-106`。
跨模块来源登记必须包含 **§23.5 新链路**（模板引用 v2 封装），否则 B 选中的包
被删掉时裂图静默发生。按「还原改动后守卫必须变红」自查；改框架层先跑
`tests/test_select_tests.py`。

### 23.4 第 4 项：前端范围收窄

- **保留**：`VideoTemplateLibrary.tsx` / `PackEditor.tsx` / `useArchiveAssets`
  这一套组件与页面布局、交互形态。不引入资产库「六个入口」的新选择器。
- **只换**：组件背后的数据源（`/assets...` → `/library/selection` 或
  `/library/assets...`）、请求/响应字段名（`referenceLibraryIds` 一类）、权限码。
- 据此 §21.3 契约行、步 2.5d、§22.3 第 2 项、§22.5 归属表的「换选图器」措辞
  统一收窄为「换数据源与字段」；`gen:routes --check` /
  `gen:mock-coverage --check` 仍必跑（`npm run verify` 不含这两步）。
- **不算换 UI、但不进本轮**：§22.6 的「封装页 `packId` 从必填降为可选」是
  **同一页面内的校验放宽**，属两段式封装的功能变更，归**步 3** 单独提交，
  与步 2.5d 的字段替换分开。若希望本轮连这一步一起做，需另行确认。

### 23.5 新增链路：v2 封装（pack）作为模板调用者的参考

#### 现状已有的大半（不要重做）

| 用户描述 | 现成落点 |
|---|---|
| 「把多个素材封装成一个包」 | `modules/library/packs.py:1-23`：`library_assets` 一行 `kind=pack` / `origin=pack` **无字节空壳** + `library_pack_items` 有序成员（`asset_id` / `position` / `role` / `description`，`models.py:193-207`）；`POST /library/packs`（`router.py:545`）、`PATCH`（`:568`）、`DELETE`（`:582` 解散）；成员字段是 **`items`**（+ 简写 `assetIds`），`PackItemIn{assetId,role,description}`（`schema.py:569-574,577-588`）。**无 publish 端点** |
| 「同一产品的三视图、细节图」 | `PackItemIn.role` 注释原文「**预留给模板替换（产品主图 / 多视结构图…）**」（`schema.py:570`），字面正对 `template/pack_roles.py:22-40` 的 `front / multiview / detail` 词表。但 v2 `role` 目前 `default=""`、无枚举、无词表校验（`models.py:201`） |
| 「按标签能挑到封装的产品」 | `packs.py:20-22` + `tags.sync_pack_tags`：建/换封装时把**成员标签并集**并到封装上、成员从列表隐身，理由就是不这样「按『产品』筛只能筛到散件、筛不到装着产品图的封装」；`GET /library/selection?tagIds=...&kind=...&includePacks=true`（`router.py:129-155`，`includePacks` 默认 true、硬过滤 `state=active`）。**这就是「B 按标签选中一个封装」的现成入口** |
| 「选中后作为参考」 | §22.4 两段式冻结形状已定：调用者显式选定 → `prepare` 冻结 |

#### 缺的一跳

- `core/ports/library_lookup.py:119-162` 的 `LibraryLookup` 只有
  `get_many()` / `readable_ids()` / `read_text_many()`，**没有「按 pack id 展开成员
  + 取 `storage_key`」的方法**（`LibraryAssetRef` 有 `storage_key`，`:93`）。
- `PrepareReplacement`（`template/schema.py:121-157`）的 `pack_id + pack_version`
  查的是 `template_packs` / `template_pack_versions`
  （`replacement_service.py:263-265` → `repository.py:38-45,121-126`）；
  `extra_pack_ids` / `extra_pack_versions` 也是模板包。**全仓无一处接受 v2 pack id**
  （`modules/template/` 内检索 `LibraryPackItem` / `library_pack_items` /
  `get_library_lookup` / `libraryPack` 零匹配）。
- 所以「B 选资产库封装」= 新增一个 **pack 源适配器**：新显式字段
  （如 `libraryPackId`）→ 展开成员 → 按 `role` 对齐 `{{Mixed N}}` 用途 →
  物化字节 → 写入 `ReplacementRunInput` / `input-map.json`。
  **禁止把 v2 pack id 塞进 `pack_id` 再靠 `PackRepository` 查不到时回退** ——
  §21.4 的隐式兜底禁令同样适用，两套 id 必须显式分流。

#### 三条硬约束

1. **不给 v2 封装加发布版本，冻结时点在 `prepare`**。v2 pack 只有
   `state_version`（`packs.py:100,125-129`，并发乐观锁），**没有**
   `TemplatePackVersion` 那样的不可变发布快照；`PATCH /library/packs/{id}`
   的 `items` 是**整体替换**（`schema.py:591-599`）。若模板直接引用「当前 pack」，
   A 改一次成员，同一模板的历史任务就指向不同图片。正确做法与 §22.4 一脉相承：
   **prepare 展开时把成员 id + `storage_key` 冻进本次 run**，包可变、run 不可变；
   不新增 `pack_version` 语义、不造代理版本行（第二事实来源，违反 §5.7 ②）。
2. **pack 可读 ≠ 成员可读**。`library_lookup.py:138-146` 与 `packs.py:13-14`
   都强调「不能拿封装当窥探别处资产的探针」；`readable_ids()` 注释里「含
   **封装成员**这条读入口」是 `modules/library/access.py` 的判据，模板侧不得
   另写一套。B 选的是 A 的封装时，必须**逐个成员**过 `readable_ids()` 再物化，
   不能只判 pack 那一行就放行 —— 否则等于绕过共享审批把别人的私有图交给
   供应商（`library_lookup.py:144-146` 原话）。
3. **`role` 词表要对齐且 fail-closed**。v2 `role` 是自由字符串、默认空，历史封装
   可能全空；模板侧只认 `PACK_ROLES[pack_kind]` 白名单。无法映射时**报错并让用户
   在封装页补用途**，不要按 `position` 静默猜 `front/multiview/detail` ——
   `{{Mixed N}}` 编号与用途绑死，猜错会生成「拿细节图当主图」的成片且不报错。

#### 删除保护的双向变化（衔接第 3 项）

现有 `SqlLibraryReferences.references()` 查的是「某素材被哪些封装收录」（**成员方向**，
`references.py:57-68`）。模板引用 v2 封装后还多两个方向：**pack 行本身被模板 /
未完成的 run 引用**、**pack 成员被已冻结的 run 引用**。完整守卫的跨模块来源声明
要把这两条都登记；但已冻结的历史 run 引用的是 `storage_key`，
`asset_references.py:6-8` 明说历史版本不算引用 —— **别把历史 run 算进去**，
否则删一个成员永远 409。

#### 并入原六步（不另起计划）

| 动作 | 归属 | 分级 |
|---|---|---|
| 扩 `LibraryLookup`：按 pack id 展开成员（返回 id / `role` / `storage_key`） | 步 1（与「扩端口」同批，§11 已指向 §21） | framework（改 `core/ports/`） |
| `PrepareReplacement` 新显式字段 + pack 源适配器 + 三道授权闸 | 步 2.5c / 步 3 | contract（四份生成物一起重生成） |
| `role` 白名单映射与缺角色报错 | 步 3 | targeted |
| 跨模块引用来源登记（pack / run 两方向） | 步 2.5a | framework |
| 前端封装选择入口（第 4 项口径下：复用现组件、只加一个数据源） | 步 3，**2.5d 不扩范围** | contract |

### 23.6 结论与开工前仍需定的两个实现口径

- §21.6 **四项全部拍板**；本轮没有新增「要不要做」的产品决策，只多了一个
  **能力缺口 W10** 与一个**收尾步 W11 / 步 6**。
- 两个**实现口径**（不是产品决策，开工前定即可）：
  ① `libraryPackId` 的最终字段名，以及它与 `packId` / `packVersion` 的互斥规则
  （同一任务只能有一个参考来源，别让两路同时填）；
  ② v2 `role` 词表是与 `PACK_ROLES` 共用一份，还是保持两份 + 类似
  `test_pack_roles_sync.py` 的逐字比对守卫。按 `pack_roles.py:9-12` 自己写的判据
  「放在 core 的条件是两个以上互不认识的模块都要用」，现在资产库成了第二个
  消费者，**倾向按它自己的规则上移 `core/`**；但这会动框架层，分级随之升到
  framework，别顺手做。
- W1（步 0.5 成片进草稿箱）仍不受影响，可先做；每步按
  `scripts/select_tests.py --print` 判级，不「保险起见跑全量」；**不自动 push**。

---

## 24. 执行记录与偏差（开工后持续追加）

> 计划是计划，落地时会遇到计划没预料到的事。本节只记「与 §1–§23 不一致的地方」
> 和「做完了但结论变了的地方」，不重复抄代码。每条按「做了什么 / 与计划的差异 /
> 待确认」三段。

### 24.1 步 0：观测与部署自检（2026-11-13 完成）

**做了什么**

| 项 | 落点 |
|---|---|
| 模块自检钩子 | 新建 `modules/template/health.py`，`ModuleSpec.doctor` 指向它（四项：`template.node` / `template.vendor` / `template.runs_root` / `template.dirs`） |
| 4 个指标 | `replacement_prepare_total{result}`、`replacement_compile_seconds`、`replacement_archive_publish_total{result}`、`replacement_projection_skipped_total{reason}` |
| `prepare` 限流 | `replacement_router.py` 挂 `rate_limit("template.replacement.prepare", limit=default//4)`，并从 `ratelimit_policy.py` 豁免表移除 |
| `import_legacy` 静默跳过 | `import_legacy()` 前置探测 `runs_root()` 并抛 `Unavailable`；`_run_dir()` 不再吞异常，另加 `is_relative_to(root)`；CLI 捕获 `AppError` 后 exit 1 |
| `prepare` 成功率 | `prepare()` 拆成「计数壳 + `_prepare()`」，壳里的 `try/except` 覆盖全部提前 raise 的分支 |

**与计划的差异**

1. **指标前缀**：原稿写 `template_*`，落地用 `replacement_*`（理由见 §10.4 改后文字）。
2. **`archive_publish` 多两个枚举值**：`missing`（成片文件不在）/ `unrecorded`
   （字节进桶但 `output_key` 没落库）。原稿只有 `ok|failed`，那两种静默失败会被压成一个数。
3. **`_sync_out` 只在两条异常分支计数**：函数里还有多处 `return False` 表示
   「本轮不需要写」（文件版本更新、task_id 不一致），那不是降级，计进去反而失义。
4. **doctor 只报不拦**：`template.runs_root` 在「远端驱动 + 未配根」时报 **FAIL**，
   但 `create_app()` 不据此拒启动；「启动即失败」仍归步 5，步 0 不改行为。

**待确认**

- **本机 Node 22.22.2 与 `vendor/manifest.json` 的 `verified_node: 22.14.0` 不一致**，
  `app doctor` 现在会红这一条。处理方式二选一：① 把部署环境统一到 22.14.0；
  ② 确认 22.22.2 下编译器输出与 golden 一致后更新清单。
  **步 4 的 golden test 落地前不要改清单**，否则等于用未验证的版本换掉已验证的基线。
- `template.dirs` 会真建目录并写一个瞬时临时文件（`delete=True`）：对只读挂载是
  准确探测，代价是共享盘上短暂出现 `.doctor-*` 文件。

**验证**：`select_tests.py` 判 `framework`（8 个文件，含 `api/ratelimit_policy.py`）
→ 43 个测试文件 **565 passed / 67.96s**；`ruff check src tests` 通过；
`mypy --platform win32 src` 289 files 无问题；`app doctor` 37 项，模板四项全部出现。

### 24.2 步 0.5 / W1：成片进 v2 草稿箱（2026-11-13 完成）

**做了什么**：`ReplacementPipeline.archive_request()`（`template/pipeline.py`）把一条成功的
`replacement_runs` 行翻译成 `IngestRequest`，走 **`archive_key`**（不是 `storage_key`，
也不是 `source_url`）。

**计划里没写、落地才确认的四件事**

1. **`stored` 在替换链上不是字节来源**。`libtv.poll()` 返回的是站内取件地址
   `/api/v1/templates/replacement-runs/{id}/output`（`infra/ai/libtv.py:365`），
   而模板没实现 `persist_output`，`_persist()` 会原样返回它。真正的 key 是
   `run.output_key`。照抄 image / task 的 `storage_key` / `source_url` 写法会把站内
   地址存进草稿箱，run 一删就裂。
2. **时序上 `output_key` 已就绪**。libtv：`libtv.download` 作业先 `publish_output`
   再让 `run.json` 变 `success`，而 `poll()` 只在 `meta.status == "success"` 时返回
   SUCCEEDED；其它渠道：`ReplacementStore.mark_succeeded` 先 `publish_output`
   再改状态。两条路都保证 `_finish_success` 触发时 key 已写。
3. **`session.get(..., populate_existing=True)` 是必须的**：`output_key` 由
   `publish_output` 另起事务写，沿用 session 里的旧快照会读到 `None` 而误判「还没搬完」。
4. **幂等键取数据库行的 `task_id`**（`mark_submitted` 写的），不是调用方手里的
   `record.external_id`。测试里只改后者测不出「重提换新行」这条判据。

**与计划的差异**

- 计划说「补一个方法即闭环」，基本成立；但**新增了「`output_key` 为空就返回 `None`」**
  这个分支——计划没讨论「成片还没搬完」的情形，而它在 bailian 渠道与 libtv 的作业
  顺序里都会命中。返回 `None` 后 `_archive()` 直接 return，**没有补偿路径**；
  真正的失败由 `replacement_archive_publish_total{result=failed}` 暴露，不在这里
  造半条指向站内地址的草稿。
- `tests/test_library_ingest_archive.py` 的 docstring 原写「不实现 `archive_request`，
  等口径再接」，已同步改掉——文档与代码互相打脸比没有文档更糟。

**验证**：新增 `tests/test_replacement_ingest.py`（3 条：archiveKey 映射与快照键、
无 `output_key` 时返回 None、task_id 变化换幂等键）；**反向验证**把方法改名后 3 条全红；
`select_tests.py` 判 `targeted`（3 个文件）→ 11 个测试文件 **202 passed / 17.16s**；
`ruff check src tests` 通过；`mypy --platform win32 src` 289 files 无问题。

### 24.3 步 2.5a：v2 删除保护补到完整守卫（2026-11-13 完成）

**做了什么**（照 `core/ports/assets.py` 的形状整套搬过来，两套 id 空间各自独立）

| 层 | 落点 |
|---|---|
| 端口 | `core/ports/library_references.py` 重写为「声明 + 对账 + 聚合」：`LibraryReferenceSource` / `bind_library_reference_sources()` / `declare_expected_library_reference_sources()` / `library_reference_gaps()` / `library_references_ready()` / `require_library_references()` / `ask_library_references()`。`get_library_references()` 单例 getter 删除 |
| 声明 | `ModuleSpec.library_reference_sources`（`api/module.py`，与 `asset_reference_sources` **并列不合并**） |
| 装配对账 | `api/discovery.py` 在 `discover_modules()` 与 `active_modules()` 各调一次 `declare_expected_library_reference_sources(specs)`（幂等） |
| 来源实现 | `library`：`library_pack_items.asset_id`（`PackMemberReferences`）+ `library_download_jobs.asset_ids`（`DownloadJobReferences`），原 `SqlLibraryReferences` 拆成两个类，一个来源一个类；`image`：`image_jobs.reference_json`（`ImageJobReferences`） |
| 调用点 | `lifecycle.purge()` / `purge_expired()` → `ask_library_references()`；`service._references()` → `library_references_ready()` + `require_complete=False`；`health.py` 与 `api/ports.py` 改走 `library_references_ready()` |
| 框架自检 | `api/diagnostics.py` 新增 `ports.library_references`。**原来只有 v1 的 `ports.references`**，而 `_check_ports` 的 docstring 早就写着「核 `library-references`」——文档与实现一直对不上，这次补上 |
| AST 守卫 | `test_architecture.py`：`_LIBRARY_REFERENCE_FIELDS` + `test_every_library_reference_writer_declares_a_source` + `test_library_reference_sources_are_complete_after_discovery` |

**四个计划没写、落地才定的口径**

1. **`text_library_ids` / `textLibraryIds` 不进 v2 引用字段集**。计划 §23.3 只说要「与 v1
   字段分开」，没说清这两个算不算。落地时按「purge 之后会不会坏」判：
   `text_json` 快照冻结的是**正文本身**，删掉文本资产既不影响历史展示，也不影响已经注入
   过的提示词 → 不是引用。算成引用会让「用过一次的文案永远删不掉」，而过度保护的真实
   后果是用户要求关掉整道闸，比漏保护更糟。反例是参考图：`reference_json` 只冻结
   `storageKey`，重试要按 key 去存储读字节 → 真引用。
2. **`pack_id` / `extra_pack_ids` 不是 v2 字段**。`modules/template/schema.py` 里那两个
   指的是模板库自己的 `TemplatePackVersion` 主键。真正指向 v2 封装的字段按 §23.5 定名
   `library_pack_id` / `libraryPackId`，已进字段集（当前无人使用，等步 3 接入）。
3. **`require_library_references()` 单独抽出来**。`test_library_skeleton.py` 的
   `test_v2_ports_are_fail_closed` 需要一个「未装配就抛 503」的同步探针，而
   `ask_library_references()` 是 async、还要 session。抽一个同步前置断言既给了探针，
   也让 fail-closed 的文案只有一份。
4. **teardown 要按「全部模块的声明」补装，不能只补 library**。
   `reset_library_references()` 清的是全部 `_sources` 与 `_expected`，image 在 import 期
   登记的那条会一起没掉；只补自己那份会让后面所有删除用例 503，而报错读起来像
   「purge 坏了」。`test_library_skeleton.py` 的 autouse fixture 改成遍历
   `active_modules()`。

**验证**

- `select_tests.py --print` 判 **full**（动了 `api/module.py`、`api/discovery.py`、
  `api/ports.py`、`core/ports/`——装配声明层），全量 **158 个测试文件，exit 0**（2m54s）。
- `ruff check src tests` 通过；`mypy --platform win32 src` 290 files 无问题。
- **反向验证 1**：把 `modules/image/__init__.py` 的
  `library_reference_sources=LIBRARY_REFERENCE_SOURCES` 删掉 →
  `test_every_library_reference_writer_declares_a_source` 红。
- **反向验证 2**：保留声明、只删掉 import 期的 `bind_library_reference_sources("image", …)`
  → `app doctor` 的 `ports.library_references` FAIL →
  `test_doctor_ports.py::test_real_assembly_passes_the_port_checks` 红。
  （第一次跑这条是**绿**的：当时 `ports.*` 只核 v1，`library.ports` 那条模块自检虽然
  FAIL 了，但没有任何守卫断言它。「补了检查却没补断言」——已一并补上。）

**留给后续步的**：`template` / `task` 目前**没有** v2 引用来源，因为它们还没把
v2 资产 id 持久化到「purge 之后会坏」的位置（只存冻结正文的 `text_json`）。步 3 接
§23.5 的 `libraryPackId` 时，AST 守卫会自动把它们要红，届时补 checker。

### 24.4 步 2.5b：扩 `LibraryLookup` + 来源标记列 + 迁移（2026-11-13 完成）

**端口扩了两条**

| 方法 | 用途 | 关键契约 |
|---|---|---|
| `expand_packs(session, actor, pack_ids)` | v2 封装 → 有序成员（§23.5「B 选封装当参考」） | 不可读的封装**不出现在键里**（空列表的含义是「这个封装是空的」，两件事不能混）；成员**全给**、用 `readable` 标出来 |
| `find_ids(session, actor, *, kind, tag_ids, limit)` | 按条件列 id（`seed.py` / 导入器） | `state=active` 硬过滤；标签多选按 OR 展开到二级（与 `GET /library/selection` 同口径） |

新增 `LibraryPackMember`（`id/name/kind/state/position/role/description/readable/mime_type/storage_key/size_bytes`）。

**三个计划没写、落地才定的口径**

1. **不可读成员不丢弃，只标 `readable=False`**。计划 §23.5 只说「逐个 `readable_ids()`」。
   落地时的判断是：静默少一张图 → 槽位顺序整体前移 → 错误一路传到成片的提示词编号，
   而界面上只看到「生成结果不对」。带布尔值，调用方才能报出「封装《X》的第 2 张已不存在」。
   孤儿成员（`library_pack_items` 指向已彻底删除的资产）走同一条路：占位 + `readable=False`。
2. **`role` 不做枚举校验**。词表在 `modules/template/pack_roles.py`，`core` 不该反向依赖它；
   白名单在调用方 fail-closed 地判（这条计划里有，但没说清「端口层一律不管」）。
3. **来源标记列不是布尔，是 `String(4)` 空间名**（`v1` / `v2`）。`is_v2` / `legacy` 会诱导出
   「`not is_v2` 就是 v1」的推断，而导入器将来可能带来第三套外部 id。

**为什么标记列必须有**：v1 `assets.id` 与 v2 `library_assets.id` 都是 32 位 uuid hex
（`db/base.py::UUIDMixin`），**形状上完全不可区分**。没有标记就只能写「先查 v2、查不到回退 v1」
的隐式兜底，而 `core/ports/library_lookup.py:8-10` 明令禁止——兜底会让某一侧悄悄读到另一半数据，
且只在删除之后才暴露成裂图。

**默认值取「当前空间」而不是硬编码 `v1`**（`modules/template/id_space.py`）：
`CURRENT_ASSET_ID_SPACE` 一个常量，步 2.5c 切换读写路径时**只改这一行**，不用逐个 INSERT 点
改口径。`mapped_column(default=asset_id_space_default)` 传的是**函数**而不是常量——常量在
import 时就固化了，那样「改一行常量」的承诺不成立。历史行的值由迁移显式回填成 `v1`。

**四列**（都是「一列 asset id + 一个空间标记」的同一形状）：

| 表 | 列 |
|---|---|
| `template_packs` | `cover_asset_id_space` |
| `template_pack_references` | `asset_id_space` |
| `template_pack_versions` | `reference_ids_space` |
| `replacement_run_inputs` | `asset_id_space` |

**`template_pack_versions` 也要标记**：已经发布的版本是 `v1` 冻结证据，切换后新发布的版本是
`v2`。同一张表混着两套，不标就没法回放。**绝不批量改写 `reference_ids` 本身**（改了就
不可复现，§21.4）。

**迁移写法**：`nullable=True` → `UPDATE ... = 'v1'` → `nullable=False`，三步分开。
`tests/test_migrations.py:153` 的注释记着本仓库踩过的坑：加列与去默认值写在同一个 batch 块里，
sqlite 重建表时 `INSERT ... SELECT` 只带原有列 → `NOT NULL constraint failed`。
downgrade 直接丢列：标记可重建（`v1` 是唯一初始值），而把 `v2` 行改回 `v1` 会让它们指向不存在的旧素材。

**验证**

- `select_tests.py --print` 判 **full**（动迁移 + 框架端口）。
- `tests/test_migrations.py` **9 passed**，含新增
  `test_template_asset_id_space_is_backfilled_as_v1`：升到上一版 → 塞三条历史行
  （参考行 / 冻结版本 / 运行输入）→ 升到 head → 逐字段比对 id 与标记。
- **反向验证**：把迁移的回填值改成 `'v2'` → 该用例红（`[('asset_v1_a', 'v2')]`），已还原。
- 新增 `tests/test_library_lookup_packs.py` **7 passed**：位置顺序与 `role`/`description`、
  不可读封装不出现在键里、孤儿成员占位且 `readable=False`、非封装 id 不被当空封装、
  空封装返回空列表、`find_ids` 排除草稿/回收站/他人私有、父标签展开到二级。
- `ruff check src tests` 通过；`mypy --platform win32 src` 291 files 无问题。

**踩到的两个小坑（记下来免得下次再查）**：`library_pack_items.position` 从 **1** 开始
（不是 0）；换标签走 `PATCH /library/assets/{id}` 的 `tagIds`，没有 `/assets/{id}/tags` 子资源。

### 24.5 步 1：`seed.py` 去裸 SQL + 加守卫（2026-11-13 完成）

`modules/template/seed.py` 原来是一条
`SELECT id FROM assets WHERE owner_id = ... AND category = ... AND type = 'image'`。
换成 `LibraryLookup.find_ids(session, actor, kind=IMAGE, limit=5)`。

**为什么不能只是「换条 SQL」**：v2 没有 `category`（§22：分类从素材行移到封装时的槽位
绑定），所以「按 category 取 3 张人物图 / 2 张产品图」这件事在新模型里**没有对应表达**。
落地的口径是：取 5 张图，前 3 张归人物包、后 2 张归产品包。演示数据要证明的是
「有两种封装形态」，不是「这些图属于某个产品」——为了演示去给素材硬塞分类标签，
等于把刚删掉的概念用标签重建一遍。

**演示封装的 `asset_id_space` 必须显式写 `v2`**。默认值是 `CURRENT_ASSET_ID_SPACE`
（此刻还是 `v1`），所以**知道自己在用 v2 资产的地方必须显式声明**。漏写的话，
等 2.5c 把默认值翻成 v2 之前，这批演示数据会拿 v2 的 id 去旧表里找。这条由
`tests/test_template_seed.py` 钉住。

**新增守卫 `test_seed_hooks_do_not_query_other_modules_tables`**：扫所有
`modules/*/seed.py` 里的字符串字面量，按 `FROM|JOIN|UPDATE|INTO <表名>` 提取表名，
再按 ORM 类所在包判定「这张表归谁」，跨模块即红。

三个实现细节值得记：
1. **必须排除 docstring**。本仓库的 docstring 会把旧写法当反例引用
   （`seed.py` 自己就写着 `SELECT id FROM assets ...` 解释为什么删掉它），
   不排除会把「解释为什么不该这么写」判成「正在这么写」。
2. **表归属按 `mapper.class_.__module__` 判，不按表名前缀猜**。
   `template_packs`→`template`、`video_tasks`→`task` 前缀猜法恰好都对，
   但 `library_asset_generation` 一类跨概念命名会立刻猜错。
3. 断言前先校验 `owners` 非空——ORM 没加载时映射为空，这条守卫会永远绿。

**⚠️ 留了一个已知行为变化，必须在步 2.5d 补掉**：`modules/library` **没有 seed 钩子**，
所以 `app seed` 跑完 v2 `library_assets` 是空的，模板演示数据只会种出草稿封装
（原来能种出「已发布的人物包 + 待补范围的产品草稿」）。这不是 bug 而是阶段错位：

- 修法不是「回退去读 v1」，而是给 `modules/library` 加一个 `seed_order=25` 的 seed 钩子；
- 但它需要一个**设计决定**：v2 的演示素材要不要落真实字节。`core/demo.py` 明确写着
  「演示数据不落真实字节：`app seed` 不该产生存储写入」，而 v2 的 `LibraryAsset`
  没有「远端占位地址」字段（远端封面不进 v2，见 `models.py:55`）。没有字节的行
  在列表 / 详情里是裂图，比没有行更糟。
- 所以这件事归 2.5d（与前端数据源切换同批），不在步 1 里临时造一个半成品。
- 过渡期不是静默的：`_asset_ids()` 取到空列表时会打
  `资产库 v2 没有可用的演示图片，模板演示数据只种草稿封装` 的 WARNING。

**验证**

- `select_tests.py --print` 判 **targeted**（3 个文件：`seed.py` / `test_architecture.py` /
  新增 `test_template_seed.py`）→ 39 个测试文件 **450 passed / 56.17s**。
- 新增 `tests/test_template_seed.py` **2 passed**：5 张 v2 图 → 两个封装、5 条参考行
  全标 `v2`、发布版本也标 `v2`；空库只种草稿且不抛。
- **反向验证 1**：往 `seed.py` 塞一条 `SELECT id FROM library_assets` → seed 守卫红
  （`modules/template/seed.py 直接查了模块 library 的表 library_assets`）。
- **反向验证 2**：删掉 `_bind` 里的 `asset_id_space=ASSET_ID_SPACE_V2` →
  `assert ['v1',...] == ['v2',...]` 红。
- `ruff check src tests` 通过；`mypy --platform win32 src` 291 files 无问题。

**踩到的坑（写下来免得下次再查）**：`find_ids` 的排序是 `(created_at, id)`，同一瞬间
插入的行按随机 hex id 决胜——**不能断言「前 3 张就是我先插的那 3 张」**。测试账号也要
每次 `suffix()` 唯一，否则第二次跑会挑到上一次的数据。

### 24.6 CI 抓到的回归：`find_ids` 的 `limit` 名不副实（已修）

`e319f03` 的 CI 在 pytest 步红了：`test_template_seed.py` 两条都错
（期望种 2 个封装，实际 1 个；空库用例期望 0，实际 1）。本地跑 targeted 39 个文件
是绿的 —— **这是我这一轮唯一一次「分级判断错了」**。

**根因**：`find_ids` 的可读性判定（`readable_rows`，要查小组）没法压进一条 SQL，
第一版写成「取 `limit` 条候选 → 逐条过滤」。库里别人先有 20 张私有图排在时间序前面时，
候选全被过滤掉，返回的条数取决于**别人的库有多大**。CI 跑全量、库里有上千条历史资产，
于是只拿回 1 张；本地 targeted 那 39 个文件里没有那么多人的资产，就凑巧够了。

**修法**（两层）：
1. `find_ids` 改成「取一页 → 过滤 → 不够再翻页」，翻页上限 `_MAX_CANDIDATES = 500`
   兜住「整库都不可读」的极端情况，避免扫全表；单页 `max(limit*5, 50)`。
2. 加 `owner_id` 参数。它不是筛选糖，而是**确定性开关**——演示数据要的是「就是这几张」，
   不能随别人的库大小漂。`seed.py` 现在传 `owner_id=demo_user_id`（老 SQL 里本来就有
   `owner_id = :owner`，这一步把它补回来而不是丢掉）。

**回归用例**：`test_find_ids_fills_the_limit_even_with_unreadable_rows_ahead` ——
先塞 20 张别人的私有图占满时间序前排，再让自己有 3 张，断言 `len(found) == 3` 且
foreign 一个都没混进来。**反向验证**：把循环条件改成恒假（退回「只取一页」）→
`assert 0 == 3` 红。

**另一个坑**：这条用例第一版用同一个默认颜色上传 3 张图 —— 内容寻址去重，三张落成
同一个资产 id，「凑满 3 条」永远做不到。测试里凡是要「N 张不同图」的，颜色必须各不相同。

**教训（写进判断流程）**：新增/修改**端口**时，「targeted 够不够」的判据不能只看
「谁 import 了它」，还要看**行为是否依赖全局库状态**。这次的分级错误代价是一次 CI 往返；
以后动 `core/ports/` 里带查询语义的方法，直接跑全量。

### 24.7 步 2：`catalog.json` 落表（问题 A，2026-11-13 完成）

**做了什么**

`video_template_store.py` 766 行整份删除，按「纯函数 / 存取 / 业务 / 一次性运维」拆成四份：

| 文件 | 职责 | 关键约束 |
|---|---|---|
| `video_template_media.py` | 槽位与 token 解析（`slot_number` / `prompt_tokens` / `cited_slots` / `apply_pack_roles` / `validate_media`） | **零 IO、零数据库**，所以能被 router、packager、脚本同时 import 而不引入循环 |
| `video_template_repository.py` | 行级读写（`get` / `get_by_key` / `list_all` / `bound_for_source` / `unpin_others` / `names`） | 只拼 SQL，不做校验、不抛业务错；排序在这里定义一次（`pinned desc, created_at desc, id`） |
| `video_template_service.py` | 出参投影 + 业务规则（`create` / `update_prompt` / `pin` / `find_for_run` / `attach_source` / `template_name`） | 事务边界在这一层（`get_session()` 不自动提交），所以每个写方法都显式 `commit()` |
| `video_template_import.py` | 老 JSON → 表的一次性导入（`app import-video-templates`） | 幂等、`--dry-run` 不写库、不删老文件（回滚依据） |

**与计划的差异（三处，都是落地才看清的）**

1. **`_LOCK` 换成部分唯一索引，不只是「换个地方存」**。原来的「抢占固定蓝本」
   靠进程内 `threading.Lock`，多 worker 下等于没有；现在是
   `uq_video_templates_pinned`（`WHERE pinned`），两个并发写入只有一个能成功。
   代价是**写入顺序变成契约**：必须先 `unpin_others()` 再设 `pinned=True`，
   因为部分唯一索引不是延迟约束、同一事务里先后顺序照样判。这条写进了
   `repository.unpin_others()` 的 docstring，并由
   `test_pinned_is_unique_at_the_database_level` 在数据库层钉住。
2. **`catalog_stamp()`（拿文件 mtime 当缓存代次）没了，换成 `_NAME_CACHE` 显式预热**。
   缓存不再是「文件变了就失效」，而是「谁拼运行记录谁先 `prefetch_names()` 一次」。
   两个原因：表里没有 mtime 可比；`_run_out()` 是纯同步投影，不能在里面查库。
   `reset_name_cache()` 留给测试。
3. **`ensure_no_subtitle_prompt.py` 从「读文件」改成「读表」**，并且**空表返回 2**。
   一个连错库的盘点最不该给的结论就是「全部提示词都合规」。同理 `health.py` 的
   `template.dirs` 不再检查 `catalog.json`（它已经只是导入源），只查打包器进度目录
   与运行工作区。

**导入这一步的两个非显然口径**

- **多条 `pinned` 收敛成 `createdAt` 最新那条**，其余清标记并记 note。不收敛的话
  导入会撞唯一索引，而那时的报错指向「约束冲突」，不会告诉你「老目录里有三个蓝本」。
- **`template_key` 重复跳过后来的**。旧实现把它当「脚本重试的稳定标识」
  （`_append_record`：同键同配置复用、同键不同配置 409），所以真实目录里不该重复；
  但 `catalog.json` 是人工可编辑文件，真重复时按先到先得跳过并在报告里点名两条 id，
  避免一条坏数据让整份导入失败。

**测试基础设施：造数入口从「临时 JSON」改成「表里的行」**

新增 `tests/video_template_support.py`（`record` / `put` / `aput` / `remove` /
`put_template` / `update_prompt` / `unpin_all`）。隔离口径随之变化且**必须记住**：

- 以前每个用例把 `CATALOG_PATH` 指向自己的 `tmp_path`，天然隔离；
- 现在 `tests/conftest.py` 是**整场共用一个 sqlite 文件**，所以隔离只能靠
  「每个用例用自己的 id」+「只断言自己那几行」；
- 凡是会写 `pinned=True` 的造数（包括导入、迁移这类绕过 service 的路径）都要先让位
  （`put()` 内置、导入用例显式 `unpin_all()`），否则撞的是别人的蓝本。

`row_from()` 复用 `apply_out()` 而不是再写一份字段映射 —— 两份映射迟早漂移，
漂移的表现是「测试通过、线上少一列」。

**全量跑抓到两个本地局部跑没抓到的回归**

1. `test_replacement_archive.py::test_selected_video_function_controls_prompt_and_scope`
   在 `async def` 用例里调了同步 `put()` → `asyncio.run() cannot be called from a
   running event loop`，而 pytest 把报错打在协程回收的位置，读起来像 flaky。
   → `video_template_support` 补 `aput()` / `aremove()`，同步版改成
   `run(aput(...))` 的薄包装。
2. 同一个用例里两条模板**共用一个 `templateKey`**。旧测试直写 `catalog.json`，
   绕过了 `_append_record` 的同键去重（真实路径上同键不同配置是 409），所以
   一直绿。落表后变成 `UNIQUE constraint failed` → 改为各用各的 key，并在注释里
   写清「为什么以前能过」。
3. 顺带清理：`ruff` 报的 `ASYNC240`（async 函数里做 pathlib IO）拆成
   `_exists()` / `_read_catalog()` 两个同步小函数，比加 `noqa` 干净 ——
   这条规则在请求路径上有价值，在一次性运维脚本上只是噪音。

**验证**

- `scripts/select_tests.py --print` → **full**（25 个文件，触发了迁移）→
  **1614 passed / 12 skipped / 196.46s**（全新 `--basetemp`）。
- 新增 `tests/test_video_template_import.py` **10 passed**：建行 + 默认值、snake_case、
  幂等（第二次 `unchanged=N`）、只更新改动行、多条 pinned 收敛、重复 key 跳过、
  `--dry-run` 不写库、文件不存在不报错、坏文件抛 `BadRequest`、`VIDEO_TEMPLATE_CATALOG`
  仍决定导入源位置。
- `pytest tests/test_migrations.py` **9 passed**（`compare_metadata` 零漂移）。
- `ruff check src tests` 通过；`mypy --platform win32 src` **294 files** 无问题。
- **反向验证 1**：把 `uq_video_templates_pinned` 改成 `unique=False` →
  `test_pinned_is_unique_at_the_database_level` 红（`DID NOT RAISE IntegrityError`）。
  *注*：同一次验证里 `test_import_keeps_only_the_newest_pinned` 仍绿 —— 说明它测的是
  **导入的收敛逻辑**、不是索引本身，两者职责没有重合（这是预期的，不是漏测）。
- **反向验证 2**：把收敛逻辑从 `pinned[-1]` 改成 `pinned[0]`（留最旧）→
  `assert ... and False is True` 红。

**回滚**

`downgrade()` 只 drop 三个索引与新表，不碰老 `catalog.json`；`app import-video-templates
--dry-run` 可先看会写多少条。老文件在导入完成后仍保留在 `api/var/video_templates/`。

### 24.8 步 2.5c 开工：先补一处被漏掉的 id 空间标记（2026-11-13）

计划里 2.5b「新增来源标记列」列了四列：`template_packs.cover_asset_id_space`、
`template_pack_references.asset_id_space`、`template_pack_versions.reference_ids_space`、
`replacement_run_inputs.asset_id_space`。**`video_templates` 不在里面** —— 不是因为
它不需要，而是那时这张表还不存在（它是步 2 才建的）。

步 2 的导入把 `catalog.json` 里的原片 id 原样写进 `source_video_asset_id`，
这些是 v1 id；切换后新封装的参考视频是 v2 id —— **同一列必然混装两套 id，而两者
都是 32 位 hex、形状不可区分**。没有标记，`_resolve_media` / `find_for_run` 只能靠
「查不到再回退另一侧」的隐式兜底，正是 `core/ports/library_lookup.py:8-10` 明令禁止的。

所以 2.5c 的第一个提交不是「切读路径」，而是补这一列：

| 动作 | 落点 |
|---|---|
| 新列 `source_video_asset_id_space` | `models.py`（默认值仍是 `asset_id_space_default()`，**不硬编码 v1**） |
| 迁移 `f2d7a91c4b83` | 照 `c3a8f1d62b74` 的三步：`nullable=True` → 回填 `v1` → `nullable=False` |
| 导入显式写 `v1` | `video_template_import._row_from_record()`，理由见下 |
| 用例 | `test_import_marks_the_id_space_explicitly` |

**为什么导入要显式写 v1 而不是靠列默认值**：默认值会在 2.5c 把
`CURRENT_ASSET_ID_SPACE` 翻成 `v2` 时一起翻，而 `catalog.json` 的内容不会跟着变。
漏写的话，切换之后重跑导入会把历史模板的原片标成 v2 → 运行时报「原片不可用」。
用例的做法是把 `CURRENT_ASSET_ID_SPACE` 打补丁成 v2 再导入，断言落库仍是 v1 ——
**验证的是「不随默认值漂移」，不是「等于某常量」**。

**反向验证**：删掉那行 `source_video_asset_id_space=ASSET_ID_SPACE_V1` →
`assert 'v2' == 'v1'` 红。

**2.5c 剩下的三块（按风险从低到高，各自独立提交）**

1. **名字解析适配层**：`video_template_service` 里三处 `get_asset_lookup().get_many()`
   只用来取 `name`，可以整体换成一个按空间分流的适配器（v2 走
   `get_library_lookup().get_many()`），语义损失为零，是切读路径的第一刀。
2. **三道授权闸**（`authorization.py` / `pack_service._check_references` /
   `template_references.resolve_pack_references`）：判据换成
   `readable_ids()`，同时删掉 `category` 反推（§22.3 第 3 项）。这一块不能只换端口：
   `pack_service` 的 `_verify_bytes` / `preview` 用的是 v1 的
   `path` / `sha256` / `preview_path`，v2 对应 `storage_key` + 库自己的预览链路，
   **得逐个确认字节从哪来**，否则会变成「封装能保存、发布时 404」。
3. **契约字段**（`referenceLibraryIds` / `sourceVideoLibraryId`）：必须与 2.5d 的
   前端切换同批，否则前端的 `/assets` 选择器会把 v1 id 填进 v2 字段。
   按 §21.4 双读口径，**不能靠请求里猜空间**，只认字段名。

**留一个问题给 2.5c 第 3 块**：`rules.fixedReferences[].assetId` 是 JSON，没有空间
标记列可比。要么在元素里加 `assetIdSpace`，要么整块跟随 `template_pack_versions`
的空间（它冻的是同一次发布的图）。**倾向后者**（少一个字段、且天然与包版本一致），
但需要确认「固定引用能不能跨包版本混空间」，确认前不动。

### 24.9 步 2.5c（二）：名字解析的分流适配层（2026-11-13）

`video_template_service` 里有两处 `get_asset_lookup().get_many()` 只用来给媒体清单
填 `name`，改成 `asset_source.resolve_names(session, [(assetId, space)])`：

| 旧写法 | 新写法 |
|---|---|
| `lookup = await get_asset_lookup().get_many(session, ids)` → `getattr(row, "name", "")` | 按 `(asset_id, asset_id_space)` 分流，v1 问 `AssetLookup`、v2 问 `LibraryLookup`，拿 `{id: name}` |

**为什么只做「名字」**：把 `StoredMedia` 与 `LibraryAssetRef` 揉成一个统一快照，
等于在这一层重新定义「什么算已删除 / 谁有权限」——那是 `readable_ids()` 的职责。
两份判据必然漂移，而漂移方向总是「多给权限」。所以适配层只回答展示名，
授权仍在原处；`asset_source.py` 的 docstring 写清了这条边界。

**两条口径**（都用例钉住）：
- 未知空间名忽略、`None` id 忽略 —— 「取不到名字」在界面上是可见的（显示裸 id），
  为一条坏标记挡住整次保存没有收益；
- **不做隐式兜底**：标了 v2 的 v1 id 必须取不到。这条是「禁止先查 v2 再回退 v1」
  的可执行版本 —— 名字回退错了只是显示问题，同一写法搬到授权闸就是把别人的
  私有图交给供应商。

**验证**：`select_tests` 判 **targeted**（4 个文件）→ 81 passed；另外单独跑了
10 个会走 `create()` 的模板用例（packager 全链路 / 固定引用 / 绑定 / 提示词…）
**81 passed**；ruff 通过；mypy 295 files 无问题。
**反向验证**：在 `resolve_names` 末尾加「v2 没找到就回退问 v1」→
`assert {'v1-…': '只在 v1 里.jpg'} == {}` 红。
