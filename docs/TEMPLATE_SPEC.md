# 模板规格（Template Spec）

> 状态：**设计定稿，待实施**（2026-11-14）。
> 目标读者：改模板封装 / 模板库 / 生成链的人。
> 与 `TEMPLATE_LIBRARY_V2.md` 无关（那份是任务中心 v2 的旧计划，用户明确「先不用管」）。

## 1. 这份文档解决什么

现有的「封装 → 模板」只服务一种形态：**一支原片 + 一组图片做替换**。它把三件事写死在了结构里：

| 约束 | 现在的落点 | 后果 |
|---|---|---|
| 必须有且只有一支原片 | `ManifestDraftIn.video` 必填单值；`video_templates.source_video_asset_id` | 「只有提示词」「多图无视频」表达不出来 |
| 参考槽位只有图片，用途来自固定词表 | `packs[].references` + `PACK_ROLES`（product/character/scene 三套） | 音频、第二支视频、文本参考进不了槽位 |
| 封装自己存了一份素材清单 | `template_pack_references`（发布时把 asset id 复制一份） | 第二事实来源；封装侧自己物化素材 |

同时框架里**已经有**通用能力，只是没被用上：

- `modules/model_driver/generic_inputs.py`：按 `kind` 收窄后缀、probe 时长、写 `references/bindings`，支持 `image / video / audio`；
- `modules/task/service.py`：文本参考走 `freeze_text_refs` 冻结；
- `CreateTaskIn`：`prompt + model + referenceLibraryIds + textLibraryIds + durationSec/aspectRatio`，已经是通用形态；
- `ModelSpec.param_schema`：参数区由模型声明驱动，前端不用为每个模型写表单；
- 资产库：`video / audio / image / pack / text` 五类，`core/ports/library_lookup` 是唯一读取入口。

所以结论不是「给封装加字段」，而是**把模板降级为「表单元数据」，把执行让给 task**。

### 1.1 决策判据（先定判据，再选方案）

这份文档的取舍只依据下面五条框架原则，**不依据改动量或实现难度**：

| 原则 | 判据（违反即否决） |
|---|---|
| 数据归属唯一 | 每类数据只有一处权威源：素材与素材结构 → 资产库；模板声明 → spec；执行记录 → task。**不得为过渡复制第二份** |
| 能力只实现一次 | 取字节、查元数据、编译编号、校验 —— 各自只有一处实现，谁的第二处都不许长 |
| 派生结果不进数据 | 编号、展开顺序、可读性判定都是算出来的，不落库 |
| 模块不互相 import | 跨模块只经 `core/ports` 或纯 JSON 契约 |
| 发布即冻结 | 已发布内容不可改写；改模板 = 新版本 |

按这五条回看，先前两条结论**被否决**：

- ~~「槽位指向资产库封装本期不做，因为要扩资产库模型」~~ —— 拿成本否决了"数据归属唯一"；
  而且事实也错：资产库封装的成员表本来就有 `position` / `role` / `description`，成员允许
  `image / video / audio / text`（只禁嵌套 pack），`LibraryLookup.expand_packs()` 已经能按
  `position` 有序返回带 `role` 与 `readable` 的成员，其 docstring 明确写着"顺序即槽位，
  调用方不得重排"。**所以它进 P1，不是"预留"。**
- ~~「P2 期间由 spec 发布时投影回 `video_templates`，让替换链继续消费」~~ —— 让新写的
  spec 去喂旧表，等于把第二事实来源制度化。**改为：P1 起替换链的 runtime 端口直接读 spec，
  `video_templates` 只作为历史数据只读保留。**

## 2. 目标 / 非目标

**目标**

1. 同一套结构支持四种组合：纯提示词、提示词+多图、提示词+图片+视频、再加音频/文本参考。
2. 封装模块只管「定表」：槽位声明、提示词、模型与参数默认值。**不取字节、不查素材元数据、不物化**。
3. 模板库只管「填表」：把槽位填满并提交。不复制校验规则。
4. task 不知道「模板」存在。
5. 素材取用只剩两处：`core/ports/library_lookup`（元数据/可读性）与 task 的 `generic_inputs.materialize`（字节落地）。
6. **终态统一**（2026-11-14 用户拍板「统一」）：替换不再是平行链路，而是 task 的一个 capability；
   不留独立替换工作台，成片复核并入任务中心。**注意是删掉重复的编排，不是把封装并进 task** ——
   P3 之后项目里只有两个概念：模板（封装模块）与任务（task）。见 §11.1。
7. **素材结构不由模板复制**：一组"同一对象的多个视图"归资产库封装（`kind=pack`），
   模板只引用它的 id，成员与角色由库给（`LibraryLookup.expand_packs()`）。

**非目标（本期不做）**

- 模板市场 / 跨工作台共享 / 版本回滚 UI；
- 在 spec 里复制封装成员（见 §12 不变量 9）。

## 3. 三层职责

```
封装（编写侧，modules/template_pack）        模板库（消费侧，前端 + 只读接口）        task（执行）
┌─────────────────────────────┐  发布   ┌──────────────────────────┐   compile   ┌──────────────┐
│ TemplateSpec                │ ──────▶ │ 卡片 = 已发布版本快照      │ ─────────▶ │ /tasks       │
│  slots[]（槽位声明）         │  冻结   │ 渲染槽位表单（fixed 只读） │  填表结果    │ 只认 prompt + │
│  prompt（整段）             │         │ 提交前调 compile 拿请求体  │             │ references +  │
│  model + params             │         └──────────────────────────┘             │ params        │
└─────────────────────────────┘                                                  └──────────────┘
```

三条边界是硬的：

- 封装 **不 import** `modules/task`（`test_modules_do_not_import_each_other`）；
- 模板库 **不拼编译结果**：`{{slot:x}} → {{Mixed N}}` 的展开只在服务端一处（§6）；
- task **不读** spec 表，入参与模板无关。

## 4. 核心词汇

### 4.1 InputSlot（槽位）

| 字段 | 类型 | 说明 |
|---|---|---|
| `key` | `^[a-z][a-z0-9_]{0,39}$` | 槽位标识，提示词里用它引用；版本内唯一 |
| `type` | `image \| video \| audio \| text \| pack` | **就是资产库的 kind**：决定提哪种素材选择器、以及能进哪个字段。`pack` = 要一整个封装（多成员、带角色与顺序） |
| `binding` | `fixed \| user` | `fixed`：封装时定死，模板库只读；`user`：模板库填 |
| `ref_mode` | `mixed \| attachment` | `mixed` 参与 `{{Mixed N}}` 编号；`attachment` 作为附加输入、不占号（音频通常用这个） |
| `min` / `max` | int | 数量范围。`min=0` 表示可选；`max` 上限受全局 10 与模型 `max_references` 双重约束 |
| `label` | str(1..40) | 槽位名的展示文案（"产品主图"/"背景音乐"） |
| `usage` | str(0..500) | 进提示词说明的用途描述（对应旧 `PACK_ROLES` 的说明与逐图 `description`） |
| `asset_id` | str \| null | 非封装槽位且 `binding=fixed` 时必填；其余为空 |
| `pack_id` | str \| null | `type=pack` 且 `binding=fixed` 时必填；其余为空 |
| `tag_id` | str | **候选集范围**：一级标签 id，空 = 不限。固定与填写都只在这个标签（含下级）里挑；编译不看它 |
| `position` | int | 排序，**就是编号顺序**，见 §6 |

约束：

- **槽位数量 0 ~ `MAX_SLOTS`**：下不封底。没有槽位 = 纯提示词模板（去 §6 看它怎么编译），
  封装页也允许把槽位删到 0 —— "至少一个槽位"是曾经写死在页面上的假约束，服务端从来没有；
- **提示词里的引用必须指向存在的槽位**：`{{slot:gone}}` 在结构校验（预检 / 保存 / 发布）就 400，
  不拖到 `compile`。槽位能删到 0，这条判据因此比过去更有用；封装页删槽位时会顺手把
  它的引用清掉并告知清了几处（不静默改用户的文字，但也不留一条必然失败的引用）；
- `type=text` 不许 `ref_mode=mixed`（文本不占视觉编号）；
- `type=audio` 只允许 `ref_mode=attachment`；
- 版本内 `position` 连续唯一；`key` 唯一；
- `type=pack` 时 `min/max` 不参与（数量 = 成员数，恒为 1/1），且 `ref_mode` 必须是 `mixed`：
  成员要占 `{{Mixed N}}` 的编号；
- 非封装槽位且 `binding=fixed` 只能表达**一个**资产；需要固定多张时，要么用 `type=pack`
  指向一个资产库封装，要么拆成多个槽位（后者只在"几张互不相关的图"时才用）。

**「要什么」只有一根轴**（2026-11-14 合并，见 §11.4）：曾经 `type`（image/video/…）与
`source`（asset/pack）并列，两根轴表达同一件事时接口能收到 `type=image, source=pack` 这种
自相矛盾的组合。「要一个封装」现在就是 `type=pack`，`is_pack` 只有一处定义（`InputSlot` 上）。

### 4.2 TemplateSpec

```jsonc
{
  "key": "product-swap-6036",          // 幂等边界，发布后不可改名
  "name": "6036 产品替换",
  "description": "",
  "model": "libtv-wan3-replacement",   // 必须是目录里存在的公开模型
  "params": { "aspectRatio": "9:16", "durationSec": 6 },  // 键必须在该模型 param_schema 内
  // 整段提示词，只有一段：模板库不改文字，要可变内容就放 text 槽位（见 §5）
  "prompt": "8 秒 · 9:16 竖屏写实短片，眼平正面中景…把 {{slot:source}} 里的内衣换成 {{slot:hero}} 的同款。{{slot:detail*}} 只补细节。",
  "demoAssetId": "7179dd76…",          // 展示案例（图或视频）；纯展示，不进发布快照
  "slots": [
    { "key": "source", "type": "video", "binding": "fixed", "refMode": "mixed",
      "min": 1, "max": 1, "position": 0, "label": "原片", "assetId": "0a2062…" },
    { "key": "hero",   "type": "image", "binding": "user",  "refMode": "mixed",
      "min": 1, "max": 1, "position": 1, "label": "产品主图", "usage": "确定同款同色外观" },
    { "key": "detail", "type": "pack",  "binding": "user",  "refMode": "mixed",
      "position": 2, "label": "细节封装", "usage": "只补纹理与扣件" },   // 用户选一个资产库封装
    { "key": "look",   "type": "image", "binding": "user",  "refMode": "mixed",
      "tagId": "d3567812…",                                               // 一级标签：只在这个标签下挑
      "min": 0, "max": 2, "position": 3, "label": "补充参考" },          // 用户挑散图
    { "key": "bgm",    "type": "audio", "binding": "user",  "refMode": "attachment",
      "min": 0, "max": 1, "position": 4, "label": "背景音乐" },
    { "key": "brief",  "type": "text",  "binding": "user",  "refMode": "attachment",
      "min": 1, "max": 1, "position": 5, "label": "口播文案" }   // 提示词里唯一可变的地方
  ]
}
```

## 5. 提示词只有一段

`prompt` 是**一段字符串**（2026-11-14 合并，见 `c9a1f4e27b53`）。原来的
`locked`（封装写死）/ `editable`（模板库可改）两段，存在理由只有一个：
"模板库允许改那一半"。用户拍板取消这个入口之后，两段就没有区别了。

**要可变内容，用 `text` 槽位**：模板里声明 `type=text` 的槽位（填表时选一份文案），
编译时按槽位顺序拆成 `{{Text N}}`，执行层再按 `textLibraryIds` 把正文注入。
模板库因此**不改任何文字**，只选素材 —— 这也让"模板改一个字就换版本"的问题消失。

字数上限沿用 `MAX_PROMPT = 20000`（编写）、`EXECUTION_PROMPT_LIMIT = 2000`（执行，
按展开后的成品判）。

## 6. 编号与编译规则（唯一一处）

**spec 里不出现 `{{Mixed N}}`，只出现 `{{slot:key}}`**。原因：用户看到的是缩略图和 chip，编号取决于模板库实际填了几张；写死数字会让「少填一张细节图」把后面全部错位。

引用语法（三个形态）：

| 写法 | 含义 |
|---|---|
| `{{slot:hero}}` | 该槽位**第一张**的编号 |
| `{{slot:detail*}}` | 该槽位**全部**编号，按填充顺序用「、」连接 |
| `{{slot:detail.2}}` | 该槽位第 2 张（越界即 400） |

展开算法（`compile()`，纯函数、不写库）：

1. 解析填充：
   - 非封装槽位：`fixed` 取 spec 里的 `asset_id`；`user` 取入参 `slotValues[key]`（资产 id 列表），数量必须落在 `[min, max]`；
   - `type=pack`：取 spec 的 `pack_id`（fixed）或入参（user）里的封装 id，调
     `LibraryLookup.expand_packs()` 展开成员 —— **顺序由库给，编译不得重排**；成员必须同类型、
     全部 `readable`（有不可读成员就报"封装《X》的成员 Y 无权访问"，不静默少一张）；
   - 单资产的可读性与存在性一律经 `library_lookup`，不直查表。
2. 按 `position` 升序累加计数，得到 `slot_key → [token…]`：
   - `mixed` 槽位用 `{{Mixed N}}`（视觉编号）；
   - `text` 槽位用**自己的** `{{Text N}}`（N = 文本素材的序号，与 `text_library_ids` 的顺序逐一对应）；
   - `audio` 槽位没有编号空间，跳过。
3. 把 `{{slot:…}}` 替换成对应 token。`audio` 槽位被引用 → 400；`text` 槽位可以引用
   （它就是"提示词里可变的那一处"）。
   `type=pack` 的槽位，其成员的 `role` 作为该 token 的角色写进执行请求（`expand_packs` 已带出）。
4. 校验总量：`mixed` 展开后总数 ≤ `min(10, 模型 max_references)`；音频/文本数量按模型声明另判（§13 开放问题）。
5. 产出执行请求：

```jsonc
{
  "model": "libtv-wan3-replacement",
  "prompt": "<整段提示词，token 已展开>",
  "params": { "aspectRatio": "9:16", "durationSec": 6 },
  "referenceLibraryIds": ["0a2062…", "454955…", "5987f1…"],   // mixed 按编号序 + attachment 追加在后
  "textLibraryIds": ["…"],                                      // text 槽位
  "slotTokens": { "source": ["{{Mixed 1}}"], "hero": ["{{Mixed 2}}"], "detail": ["{{Mixed 3}}"],
                  "brief": ["{{Text 1}}"] }
}
```

`slotTokens` 只回给前端做展示（角标编号），不参与提交。

**这份出参是「模板层」结果，不是任何端点的请求体。** `POST /tasks` 把时长与画幅放在
**顶层**（`durationSec` / `aspectRatio`），不接受自由 `params`；生图端点又是另一套摆法。
摊平由提交方按目标端点做 —— 模板库页面对照生成的 `CreateTaskIn` 类型把 `params` 展开，
类型检查就是那份映射的守卫。`compile` 刻意不假设端点：否则换一个执行端点就要改编译规则，
而那属于 P3 的 capability 声明。


## 7. 数据模型

三张表，全部在 `modules/template_pack/models.py`（`db/registry.py` 只扫 `modules.*.models`）。沿用全项目约定：**无外键**，靠字符串 id + 应用层校验；唯一性靠复合索引。

### `template_specs`（head：编辑态与生命周期）

| 列 | 说明 |
|---|---|
| `id` | 32 hex |
| `spec_key` | 唯一；发布后不可改（幂等边界） |
| `name` / `description` | |
| `owner_id` | |
| `status` | `draft / published / disabled`（`disabled` 是下架，不清历史） |
| `draft` | **JSON**：编辑中的整份 spec（未发布内容） |
| `revision` | 乐观锁，每次保存 +1 |
| `fingerprint` | draft 的结构摘要（幂等判定 + 冲突检测） |
| `current_version` | 指向最新已发布版本；0 = 从未发布 |
| `published_at` / `disabled_at` | |
| `deleted_at` | 软删 |

### `template_spec_versions`（发布快照，不可变）

| 列 | 说明 |
|---|---|
| `id` / `spec_id` | `unique(spec_id, version)` |
| `version` | 从 1 递增 |
| `model` / `params`(JSON) | 发布那一刻冻结 |
| `prompt` | `Text`（整段；2026-11-14 由两段合并） |
| `fingerprint` | 本次发布内容的摘要（回执与重发比对用） |
| `published_by` / `published_at` | |

### `template_spec_slots`（属于某个版本）

| 列 | 说明 |
|---|---|
| `id` / `version_id` | `unique(version_id, slot_key)`、`unique(version_id, position)` |
| `slot_key` / `type` / `source` / `binding` / `ref_mode` | |
| `min_count` / `max_count` | 不用 `min`/`max`：SQL 里是聚合函数名，写迁移时容易踩 |
| `label` / `usage_text` | |
| `asset_id` / `pack_id` | 按 `source` 二选一；`binding=user` 时都为空 |
| `position` | 编号顺序 |

**为什么 draft 用 JSON 而不是草稿行**：草稿只有一个人写、整份替换，查询需求为零；用行存反而要区分 `version=0` 与"发布中"两种半成品状态。发布时一次性把 draft 展开成 version + slots 行，之后不可变——与 `template_packs` 的"发布即冻结"是同一套口径。

## 8. 契约

编写侧（模块 `template_pack`，权限码沿用 `template_pack:read / create / publish`，不新增码）：

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/template-specs/validate` | 只预检不落库（**必须挂 `rate_limit`**，`test_route_policy` 按方法扫） |
| `POST` | `/template-specs` | 保存草稿；`key + fingerprint` 幂等，`revision` 冲突 409 |
| `GET` | `/template-specs` / `/template-specs/{key}` | 列表 / 详情（含 draft 与最新版本） |
| `POST` | `/template-specs/{key}/publish` | 冻结发布：draft → version + slots，回执含 `version` 与 `tokensPreview` |
| `POST` | `/template-specs/{key}/disable` | 下架（历史版本保留） |
| — | `demoAssetId`（`POST`/`GET` 出参） | 展示案例：图或视频的资产 id。**只存在草稿、不进发布快照** —— 改案例不用发新版 |
| `DELETE` | `/template-specs/{key}` | **软删**进回收站（`deleted_at`）。列表与模板库都看不到，可恢复 |
| `POST` | `/template-specs/{key}/restore` | 从回收站恢复 |
| `GET` | `/template-specs/trash` | 回收站列表（按删除时间倒序；不看 status / target） |

消费侧：

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/template-specs/published` | 模板库卡片：已发布版本的快照（slots + 两段 prompt + model + params），不返回 draft。`?target=replacement\|generation\|all` 按**模型声明**筛（见 §11.0），不按槽位形状猜 |
| `POST` | `/template-specs/{key}/compile` | **纯函数式**：入参 `{version?, slotValues}`，出参见 §6。不落库、不调 task |

`compile` 之所以存在：展开与校验是**模板语义**，不能下放前端（否则编号规则有两份），也不该让封装去调 task（跨模块）。它只做映射，不产生副作用，天然可缓存、可单测。

模板库拿到 `compile` 出参后，由前端提交到**模型能力对应的接口**（2026-11-14 起）：

| 模型能力 | 接口 | 顶层字段 |
|---|---|---|
| `video` | `POST /tasks` | `durationSec` / `aspectRatio` / `stylePreset`，没有自由 `params` |
| `image` | `POST /generations/image` | `size` / `count`，其余声明的参数进 `params` |

**模板本身不声明「是图片还是视频」**：它只写模型，能力由 `GET /models` 决定。
判错方向的代价很具体 —— 图片模板走 `/tasks` 会被按 `Capability.VIDEO` 拒掉，
用户看到的是「这些参数无法提交给通用生成任务：count」，一句与模型无关的错。

两条映射都在前端一处（`features/template-library/taskPayload.ts`：
`toTaskPayload` / `toImagePayload` / `toSubmitPayload`）。共同口径是**参数不许静默丢**：
视频那边多出来的键没有落点 → 报错；图片那边除 `size`/`count` 外的键进 `params`，
由服务端按模型 `paramSchema` 校验（未声明即 400，也是看得见的失败）。

`compile` **不再有 `promptOverride`**（2026-11-14）：模板库不改文字，可变内容走 `text` 槽位。
旧字段会被 `extra="forbid"` 拒成 422，不是静默忽略。

## 9. 封装页 UI 规格（按参考截图）

单页编辑，三块区域：

```
┌─ 槽位区（左） ────────────┬─ 提示词区（中） ──────────────────────┐
│ [+ 上传] [从资产库选]      │  ┌ 已引用（= 槽位清单，可点回定位/删） │
│ ┌──┐┌──┐                  │  │  6036 / 米兰之夜6036-产品…          │
│ │1 ││2 │  ← 角标 = 编号    │  └────────────────────────────────────┘
│ └──┘└──┘                  │  [整段提示词：可写描述文字]              │
│ 槽位卡片：类型/固定或待选/  │  [引用用 chip 插入，手写大括号会被拦]    │
│ min-max/用途说明            │  总字数 961 / 20000（分段计）           │
├───────────────────────────┴────────────────────────────────────────┤
│ [模型 ⌄] [模式/参数：9:16·720P·8s·1个 ⌄]              [预检][发布]   │
└────────────────────────────────────────────────────────────────────┘
```

`@` 弹层两级（照截图）：

- 一级：`已引用`（当前槽位清单）+ `素材引用`：图片 / 视频 / 音频 / 文本 / 资产；
- 二级：
  - 图片 / 视频 / 音频 / 文本 → 插入 `type=<该 kind>, binding=user` 槽位（占位留空，模板库填）；
  - 资产 → 选**单个资产**则插入 `type=<该 kind>` 槽位，选**资产库封装**则插入
    `type=pack` 槽位（成员数与角色由封装带出）；
  - **类型下拉的选项就是资产库的 kind**（图片 / 成片 / 音频 / 文本 / 封装），前端不再写一份
    本地词表 —— 库里多一类，这里就多一档；
  - **槽位的「一级标签」**把候选集收窄到某个一级标签（含其下级）：封装时挑固定素材、
    模板库填写，两处看到的是同一批素材（`AssetPicker` 的 `tagIds` 从同一处传）；
  - 每个槽位卡片上有「封装时固定」开关：打开 = `binding=fixed`（模板库只读），关闭 = `user`。

细节：

- 插入位置决定 `position`；拖动缩略图改序 → 重排 `position` → 角标实时重算（因为编号是派生的，这里天然一致）。
- 音频/文本槽位在提示词里**不出现 chip**，只在槽位区列出，且 `ref_mode=attachment` 时提示「作为附加输入，不参与编号」。
- 底部模型切换后，`params` 区按新模型的 `param_schema` 重绘（已有能力）；旧值不在新 schema 里就丢弃并提示。

## 10. 模板库填表页

同一份 spec 的**只读镜像**：

- 提示词**整段只读**；要可变就用 `text` 槽位（填表时选文案）；
- `fixed` 槽位显示缩略图 + 「封装时固定」；`user` 槽位按 `type` 走不同的填法（见 §10.3）：
  图片/视频 → 按「一级标签 · 槽位名」分组、点二级标签看素材；文本/音频/封装 → 各自的
  列表或封装选择器；
- 提交按钮在 `min` 未满足时禁用，旁边说明缺哪个槽位（不写冗长引导语）；
- 提交 = `compile` → **按模型能力摊平** → `/tasks` 或 `/generations/image` → 提示 + 「去任务中心」。

### 10.1 已落地的形态（2026-11-14，`/templates/library`）

两栏常驻：左列卡片（名称 / 版本 / 槽位数 / 固定数），右栏就地填表。换模板只换右栏，
不跳页 —— 填到一半不会丢上下文。要点：

- **卡片不摆空封面**：spec 没有封图字段，就不放渐变块冒充缩略图（第一版放了
  `aspect-[16/10]` 的占位，截图一看就是大片空白）。卡片按「叫什么、第几版、要几样素材」
  排；选中的那一份才在右栏出图。
- 槽位卡片左上角是序号（= 编号顺序），`fixed` 显示缩略图 + 「封装固定」且**没有选择按钮**，
  `user` 显示「选择 / 更换」；封装槽位只显示「N 张」与首图，成员顺序不在这里展开。
- 提示词整段只读（`不可编辑`），下面一列「`{{slot:key}}` → 第几位 / 第几份文案」。
- 提示词就在原位展开（见 §10.2）：选过的位置显示缩略图 / 正文，没选的位置是一个
  「选择」按钮；不再用「`{{slot:x}}` → 第几位」的对照表 —— 用户不需要先学占位符语法。
- 底部按钮在必填未满足时禁用并说明缺几项 —— 这是提交前置条件，不是业务规则；
  数量上下限、可读性、编号展开仍然只有服务端一份。
- 模型与参数**只读展示**：模板定的是模板定的。要改参数回规格页改模板。

**浏览器实测（2026-11-14）**：Chrome headless 注入令牌打开 `/templates/library` →
点槽位 2「选择」→ 弹层挑「3030粉」→ 按钮从「还缺 1 项素材」变「生成」→ 点生成 →
提交接口 **201**（视频 `/tasks`、图片 `/generations/image`）、右栏出现「已提交：… 去任务中心」；控制台无 error、无失败请求、
右栏 420px 无横向溢出（`scrollWidth > clientWidth` 的元素为空）。任务随即 DELETE，
验证不留下扣费记录。

### 10.2 占位符在界面上怎么显示（2026-11-14）

**只有封装页显示 `{{slot:key}}` 本身**：作者要看见引用才能把它插进提示词、才能判断
哪一处会变。其余三处一律渲染成真实数据：

| 位置 | 数据来源 | 显示成 |
|---|---|---|
| 模板库 | 用户本次填的 `values` | 文本槽位 → 正文；图 / 视频 → 与文字同高的缩略块（**点开看大图 / 直接播**）；封装 → 前两张成员 + 「N 张」；没选 → 「选择…」按钮（就地开选择器） |
| 任务中心 | 任务的 `textLibraryIds` / `referenceLibraryIds` | `{{Text N}}` → 第 N 份文案正文；`{{Mixed N}}` → 第 N 张参考素材缩略图 |
| 资产库 | 快照的 `prompt` + `reference_library_ids` | 同上（快照存的是**注入正文之后**的提示词） |

实现只有一处：`entities/library/ui/PromptRefs.tsx`，三处传不同的 resolver。

**缩略块可点**：提示词里没有别的地方能看一眼原图，所以行内缩略块点开是弹层大图 /
可播放视频；封装里的单张点开看那一张，「N 张」点开看整组（封装没有「一张代表图」）。
单选槽位选完即关选择器 —— 留着弹层等于让用户再点一次右上角，而缩略图早该出来了。
解析不到就**原样保留占位符**（灰底小字）—— 历史任务缺快照时，看到 `{{Text 1}}`
比看到空白更接近真相。

配套的三处数据改动：

- `ImageFeedItem` 补 `referenceLibraryIds` / `textLibraryIds`：只给 prompt，前端无法
  还原正文与缩略图。它是**只读投影**，在 `test_architecture.py` 的
  `_REFERENCE_PROJECTIONS` 里显式豁免（真正写入方 `modules/image` 已声明来源）；
- 视频入库快照的 `prompt` 改成 `inject_text_refs(...)`：资产详情是给人看的，
  `{{Text 1}}` 在那里没有上下文可解析（快照不带文案 id）；
- 生图作业名也从「提示词前缀」改成**注入正文之后**的文案：模板生成的图取名
  `{{Text 1}}`，列表里谁也不知道那是什么。

**生成产物命名**：`用户名-年-月-日-时-分-秒`（`core/naming.py` / `format.generatedFileName`）。
标题是一句描述、同一句话能生成十次，落在磁盘上就是十个同名文件；名字里放作者与时间，
下载后能分清哪个是新的。时间按**本地时区**格式化，后缀只认真实产物。

### 10.3 素材填写按一级标签分组（2026-11-14）

用户要的形态：「显示这个模板所有需要输入的一级标签，一级标签下显示二级标签，点二级标签
选择素材。比如名称产品1的槽位、类型图片、一级标签产品，就显示 `产品.产品1`，下面是图片类型
一级标签为产品的二级标签，点二级标签显示具体素材」。

```
填入素材                                      还缺 1 项
┌──────────────────────────────────────────────────┐
│ 产品 · 产品1        ＊        图片 · 已选 1/2 · 共 9 │
│ [T35✓] [3030]                                     │
│ 点一个标签，下面是它的素材。                       │
└──────────────────────────────────────────────────┘
2. 文案            文本                    选择文本
```

- 标题 = **`一级标签 · 槽位名`**；一级标签为空时只写槽位名，并提示去封装页补一个；
- 二级标签一行**只有子标签、没有「全部」**（用户定稿：一行摊开本来就长，再挂全量入口更挤）；
- **默认不列素材**，点哪一档才拉那一档（`/library/selection?kind=<type>&tagIds=<id>`），
  未点时不发请求 —— 所以二级标签只挂一级、没挂二级的素材不会出现在这里（要看它们去资产库）；
- 一级标签下没有二级标签（`音乐` 这种）→ **没有可点的，直接列一级标签下的素材**；
  标签接口读取失败时也走这一支，不把选择器锁死；
- 点素材即选中：单选槽位替换、多选槽位累加、已满则禁用其余（`slotPick.pickWithin`）；
- 胶囊**不带计数**：标签接口的数字是「列表口径」（被装进封装的成员不单列），
  而这里用「选择器口径」（成员也要能挑），两者对不上；真实条数放在右上角「共 N」。

**发布闸门**（同一条改动的另一半）：要让用户填的**图片/视频槽位必须有 `tag_id`**，
否则发布 400 并指名是哪几个槽位。理由很直接 —— 没有标签时这一段只能退化成"全部素材"，
素材一多就没法挑，而这正是要消灭的形态。只要求 `binding=user`：固定槽位作者已经选死，
模板使用者不挑；文本/音频/封装各走各的选择器，没有"按标签分组"的概念。

## 11. 迁移与分期

现有数据不丢、不重写：

| 旧 | 新 |
|---|---|
| `manifest.video.assetId` | 一个 `fixed / video / mixed` 槽位（`position=0`，key `source`） |
| `manifest.packs[]`（旧素材包） | **首选**：整体落成一个资产库封装（`kind=pack`，成员 `position`/`role`/`description` 原样带过去），spec 里用一个 `type=pack, binding=fixed` 槽位指向它 —— 这样"多视图属于同一对象"的结构归位到资产库。<br>**降级**（仅当这批图互不相关）：拆成多个 `type=<kind>, binding=fixed` 槽位。降级会让模板重新拥有一份结构，只在旧包本身没有对象语义时使用，并在迁移报告里逐条标出 |
| `manifest.functions[]` | **每个 function 拆成一条 spec**（key = `manifest.key:function.key`）。一 spec = 一张卡片，与原"一功能一卡片"一致 |
| `manifest.functions[].prompt` | 整段进 `prompt`（现在只有一段） |
| `manifest.functions[].scopeOptions` | 暂不迁（替换专属，见 P3） |
| `video_templates` | **只读历史，不再写入**。替换链的 `template-runtime` 端口在 **P3** 改为读 spec 版本（见下方"接读为什么不在 P1"）；旧行保留到 P3 删除 |

三期，每期可独立上线、独立全绿：

| 期 | 内容 | 验收 |
|---|---|---|
| **P1 定表** | `core/` 加 `InputSlot`/`TemplateSpec` 词表与编译纯函数；三张表（迁移 `d4a9f1c73b20`）；六个接口（含 `compile`）；封装页改成 §9 单页 | `compile` 输出与旧模板的 `mediaOrder` **逐项等价**（`tests/test_template_spec_equivalence.py`，已用"注入重排"验过会红）；预检挡住所有非法组合（§12）；**不写 `video_templates`、也不假装能读**（见下方"接读为什么不在 P1"） |
| **P2 填表**（已完成） | 模板库填表页 `/templates/library`（两栏：左选模板 / 右填素材；`source=pack` 走封装选择器）；`compile → 摊平 → /tasks` 走通 | 五种形状各跑一次（真实素材，每条 `POST /tasks` 201 后删掉）：纯提示词（0 槽）/ 多图（2 张）/ 图+视频 / 图+音频附件（`attachment` 不占编号）/ 图+文本（正文进 `textLibraryIds`）；另加"选一个资产库封装填一个槽位"（更早已验） |
| **P3 接读 + 迁移 + 删表 + capability 化**（已全部落地，见 §11.0 / §11.2 / §11.3；「接读」按 §11.3 的结论改为**直接下线**） | 替换链的 `template-runtime` 端口改读 spec 版本（旧 `video_templates` 只读）；~~旧数据迁移~~ ✅；删 `video_templates` / `template_packs*`；替换能力作为 task 的一个 capability；`supportedScopes`/`wardrobeMode` → `spec.attributes`；成片复核 → 任务中心；`modules/template` 的编排与物化下线。细则见 §11.1 | §11.1 的三条判据全绿；替换业务与历史档案不中断；`prepare` 用 spec 驱动的模板跑通；迁移出的 spec 与旧模板 `mediaOrder` 逐项等价 |

#### 接读为什么不在 P1（2026-11-14 实现时确认）

原稿把「runtime 改读 spec」放在 P1，理由是"不许投影回旧表"。真做时撞上一个事实：
替换链按「原片 + 素材包」找模板（`find_template_for_run(source_asset_id, pack_id, template_id)`），
而一条 spec 可能根本没有视频槽位、也没有包槽位 —— 要让它读 spec，先得有一条判据说
"哪条 spec 是可替换模板"（模型属于替换通道？有 fixed video 槽？有 fixed pack 槽？）。

这条判据本质属于 P3「替换变成 task 的一个 capability」，P1 硬定就是猜。所以：

- **P1 只定表**，不写 `video_templates`（不产生第二份数据），替换链继续读自己的旧表；
- **P3 一次性接读**，并把判据写进 capability 声明（模型侧声明"我是替换能力"，
  spec 侧只按槽位组合校验），避免用启发式推断。

#### 数据迁移为什么也不在 P1（同日，同一根因）

上表那套映射是**目标形状**，但它的验收标准依赖一个 P1 还不存在的东西："
哪条 spec 与旧模板等价"。而 `compile` 产出的是 `POST /tasks` 的请求体，不是替换任务 ——
把一条 `video_templates` 迁成 spec，运行行为就换了（没有审核范围校验、没有成片复核、
不再走替换链）。在 P3 定清 capability 之前做这件事，会得到一个"看起来等价、
跑起来不同"的模板集，比不迁更糟。

所以 P1 只交**可机检的那一半**：`tests/test_template_spec_equivalence.py` 用旧
`media_order` 与 spec `compile` 对同一组素材断言「编号 → 素材 → 角色」逐项相等。
P3 补上 capability 判据后，迁移只需要让这份守卫继续绿，就算没换语义。

迁移形态也一并定下（P3 照此实现，别重新推导）：

- **位置**：`api/scripts/`（一次性运维脚本，不是模块）。它要同时写资产库（建 `kind=pack`）
  和规格表，而业务模块之间不许互相 import —— 脚本在 `modules/` 之外，正是为此存在的
  （先例 `scripts/migrate_asset_roles.py`）。
- **只产草稿**：迁出来的 spec `status=draft`，不自动发布。原模板的模型/参数组合要人来确认
  （旧数据里"用哪个模型跑替换"是运行时按渠道定的，不是模板里的一列）。
- **幂等**：以 `spec.attributes.migratedFrom = "template_pack:<id>"` 为标记，重跑跳过；
  资产库封装按 `(owner, 成员 asset 序列)` 去重，避免第二次跑就多出一批同内容的封装。

### 11.0 P3 第一刀已落地：替换能力由模型声明（2026-11-14）

替换链要按「原片 + 素材包」找回对应模板。判据**不能**从槽位组合推断
（"有 fixed video 槽 + 有 pack 槽就是替换模板"）—— 普通多图模板会被误判，
然后在替换链路里以「没有绑定档案」失败，那是个和用户操作毫无关系的错。

所以判据落在模型侧：

- `ModelSpec.replacement: bool = False`（`infra/ai/base.py`）—— 驱动声明「我能跑替换」；
  三个 `*-replacement` 模型置 `True`，其余默认 `False`；
- 经 `ModelSummary`（core 端口）→ `GET /models` 与 `/model-driver/models` 出参；
- `GET /template-specs/published?target=replacement|generation|all` 按它筛，
  只跟**当前发布版本** join（草稿里的模型不算数）。

实测（dev 库 6 个模板）：`/models` 三个替换模型 `replacement=true`；`?target=replacement`
拿到 5 个（全是 libtv），`?target=generation` 拿到 1 个（纯提示词，agnes 模型）。

**这一刀不动表、不动替换链**：下一步才是"runtime 端口读 spec"。先有稳定判据，
接读时才不用回头改筛选口径。

### 11.2 旧数据迁移已落地：`scripts/migrate_templates_to_specs.py`（2026-11-14）

把旧 `video_templates`（连同它的素材包）迁成 **spec 草稿**。只增不删，跑错删新行即可回滚。

| 旧 | 新 |
|---|---|
| `source_video_asset_id` | 一个 `type=video, binding=fixed` 的槽位（`position=0`） |
| 素材包（`template_packs` + 引用） | **先落成资产库封装**（`kind=pack`，`position`/`role`/`description` 原样带），再用**一个** `type=pack, binding=fixed` 槽位引用它 |
| 无素材包时的参考图 | 降级：每张一个 `type=<kind>, binding=fixed` 槽位（模板重新拥有一份结构，报告里逐条标出） |
| `prompt` | `editable` 段；**`{{Mixed N}}` 逐个翻成槽位引用**（见下） |
| `aspect_ratio` / `generation.duration` | `params.aspectRatio` / `params.durationSec` |
| — | `attributes.migratedFrom = "video_template:<id>"`（描述里也带一份，人可读） |

**编号必须逐个翻译，不能整组替换**：旧提示词的 `{{Mixed N}}` 是 `media_order` 下标
（1 = 原片，2 起是参考图）。新模型里封装槽位占 N 个号，所以 `{{Mixed 2}}` →
`{{slot:views.1}}`、`{{Mixed 3}}` → `{{slot:views.2}}`。用整组 `{{slot:views*}}` 会把
「以 {{Mixed 3}} 三视图为准」变成「三张都为准」——语义变了却不报错。
某个编号找不到落点 → **整条跳过**并在报告里点名，不写一份引用不到的草稿。

**只有草稿、不自动发布**：旧数据里"用哪个模型跑替换"不是模板的一列（运行时按渠道定），
所以 `--model` 必填且必须声明 `replacement`（见 §11.0）。默认只读打印计划，`--apply` 才写。

**实测（dev 库 3 条旧模板）**：迁移 → 发布 → `compile`，三条的提示词与素材顺序与旧模板
**逐字/逐项相同**（旧 `{{Mixed N}}` 与新派生编号完全对齐）。重跑一次全部跳过。

### 11.3 P3 收尾已落地：替换链整体下线（2026-11-14）

「接读」没有做，改成了**直接下线** —— 接读的唯一目的是让旧工作台继续活着读新表，
而终局（§11.1）本来就不留独立工作台。先接读再删表等于给一条要拆的桥加固。

**一次删掉的（提交 `b8e4c1f90d27` 的迁移 + 同批代码）**

| 删掉的东西 | 理由 |
|---|---|
| `modules/template` 整个模块（5664 行：执行链 / 物化 / 归档工作区 / 发布回执 / 运维命令） | 替换不再是平行链路；物化只由 `generic_inputs.materialize` 做 |
| `video_templates`、`template_packs` / `_references` / `_versions` | 模板只存在于 `template_specs`；素材包语义归资产库封装 |
| `template_manifests` 四表 + `/template-manifests` 前后端 | 它是 C1 的过渡态，产物是 `video_templates`；发布快照已经是唯一事实 |
| `replacement_runs` / `_run_inputs` / `_reviews` | **用户拍板直接删**：形状与 `tasks` 不对应，迁不成新记录；留只读视图反而让人以为旧链路还活着 |
| `core.feed`、`core.ports.template_runtime`、`core.ports.template_catalog`、`core/template_pack_view`、`core/template_rules` | 存在的理由都是「编写侧与运行侧是两套表」；现在只有一套 |
| 权限码 `template:read/use/review`、`template:pack:manage/publish` | 随接口消失。内置角色改用 `template_pack:read`（填表）与 `template_pack:create/publish`（封装） |
| 任务中心第三源（`source=replacement`） | 替换任务就是 `tasks` 里的一行，第一路已经能查到 |
| 前端「替换工作台」`/templates` 与「模板封装」`/templates/manifests` | 只剩 `/templates/library`（填表）与 `/templates/specs`（封装） |

**留下的守卫都还在，且换成不依赖旧表的形式**

- 编号等价性（`test_template_spec_equivalence.py`）：旧 `media_order` 的语义冻结成常量，
  继续钉「编号 → 素材」逐项相等；
- `test_task_feed.py` 改成两路（视频 / 生图）；
- `test_user_retry_exits.py` 去掉 `template_replacement` 这一 kind —— 替换走 `/tasks`，
  自救出口与视频链同一条；
- 「模型下线当场判失败」不再有例外（`test_generation.py`），替换链曾是唯一
  `fail_on_model_gone=False` 的那条。

**判据复核**（§11.1 的三条）：①②③ 都成立 —— 删掉旧表后替换业务由 `POST /tasks` +
`replacement` 模型承载；`modules/template` 不存在了，字节只经 `generic_inputs`；
`/tasks` 入参一字未改。

**数据处置**：开发库 49 条历史调用档案随表删除（升级前 `pg_dump` 备份在
`api/var/db-backup-before-p3-*.dump`）。这是明确接受的代价，不是遗漏。

### 11.4 槽位「要什么」合成一根轴 + 一级标签（2026-11-14，迁移 `a7d3f0c85e21`）

封装页的槽位卡片原来有两列要填：**类型**（图片/视频/音频/文本）与**来源**（单个资产/资产库封装）。
用户看完直说：「类型这里应该是获取资产库的种类」「来源改成一**级标签**，获取资产库的所有一级标签，
封装时固定选择素材只能选类型筛选、一级标签筛选后的素材；用户选择也一样」。

**改法**

| 原来 | 现在 |
|---|---|
| `type` ∈ image/video/audio/text | `type` ∈ **资产库的 kind**（多一个 `pack`） |
| `source` ∈ asset/pack | **删掉**：`type=pack` 就是"要一个封装"（`InputSlot.is_pack` 唯一一处定义） |
| （无） | `tag_id`：一级标签，**候选集范围**，空 = 不限 |

为什么 `source` 要真的删、而不是留着不用：两根轴表达同一件事时，接口能收到
`type=image, source=pack` 这种自相矛盾的组合，而代码里每一个 `if slot.source == "pack"`
都得再回答一次"那 type 算什么"。合并之后判据只剩 `type`，`compile` / 校验 / 选择器 / 前端
全部同一口径。`ref_mode` 显然也是派生的（audio/text → attachment，其余 → mixed），但它
属于另一根轴、这轮不动 —— 记在这里免得下次误以为"漏了一个"。

**候选集与内容分开**：`tag_id` 只决定"能选什么"，`compile` 完全不看它 —— 标签被改名、
被删掉，已发布版本照旧能编译。它是作者写的声明，所以跟着槽位一起进发布快照（存在
`template_spec_slots` 行里），而不是"编译时再查一次库"。

**两处选择器同一批素材**：封装时挑固定素材（`SpecWorkspace`）与模板库填写（`SpecLibrary`）
都把 `slot.tagId` 传给 `AssetPicker.tagIds` → `/library/selection` 的 `tagIds`（一级标签按
`expand_tag_ids` 展开成"自身 + 直属子级"）。两处各筛各的，就会出现"作者选的图用户根本挑不到"。

**迁移**：`template_spec_slots.source='pack'` 的行 → `type='pack'`；草稿 JSON 里的
`"source"` 并进 `type` 后删键（漏了这一步，草稿一打开就 422 —— `extra="forbid"`）。
降级能把 `source` 算回来，但**原来的 `type` 找不回来**（已被 `pack` 覆盖，按 `image` 写回）。

### 11.1 P3 终局：统一到 task（2026-11-14 拍板）

**不留独立替换工作台。** 替换不再是一条平行链路，而是"某个模型 + 某组槽位"的普通生成：

| 现在的替换专属物 | P3 之后 |
|---|---|
| `video_templates` 表 + `/templates/video-templates` 路由 | **P1 起只读**（runtime 改读 spec），P3 删除。模板只在 `template_specs` |
| `template_packs` / `_references` / `_versions` | 删。多图语义由 `type=pack` 槽位指向资产库封装表达（见 §4.1）；旧包在 P1 迁成资产库封装 |
| `replacement_runs` / `_run_inputs` / `_reviews` | 删除或**只读归档**；新任务一律落 task 自己的记录 |
| `supportedScopes` / `wardrobeMode` / `scopeOptions` | `spec.attributes: dict[str, str]`（`scope` / `wardrobe` 两个键），由 `compile` 读、进提示词变量 |
| 成片复核（review / reconfirm） | 任务中心的评价能力；不再在模板模块里长第二套 |
| `modules/template` 的 `_materialize_inputs` / `stage_library_refs` / `pipeline.py` | 删。字节只由 `generic_inputs.materialize` 落地 |

**保留但换位置的**

- 发布即冻结（`template_spec_versions`）不变量不变；
- 「审核范围不能临时扩大」这条规则保留 —— 只是载体从素材包表变成 `spec.attributes.scope`，由 `compile` 校验；
- 历史档案（`replacement_runs`）不迁移成 task 记录（形状不同、字段不对应），保留只读视图供查历史，写入口关闭。

**三条到达判据**（比看文件树可靠，缺一条就是"又长出一套平行的东西"）

1. 删掉 `video_templates` 与 `template_pack_references` 后，替换业务仍完整；
2. `modules/template` 里不再有物化素材的代码，字节只由 `generic_inputs` 落地；
3. `/tasks` 入参与今天完全一致 —— 模板没有污染执行层。

**为什么不合并模块**：封装仍只管"定表"、task 仍只管"执行"，`modules/template` 的下线是**删掉重复的编排**，不是把封装并进 task。P3 之后项目里只有两个概念：模板（在封装模块）与任务（在 task）。

## 12. 不变量与守卫（每条都要有测试）

1. `modules/template_pack` **不得** import `modules/task`（既有守卫已覆盖，新增代码别破）。
2. spec 相关代码**不得**读写 `video_templates` / `template_pack_references`（扫源码字符串 + 扫 import）。
3. 封装模块**不得**直接 `select(LibraryAsset)`：素材校验一律经 `library_lookup`（扫 import 与表名）。
4. 编号只由 `position` + 填充数量决定：给定同一份 spec 与同一组填充，`compile` 是纯函数（同一入参两次调用结果相同）。
5. `audio` 槽位永不拿到 `{{Mixed N}}`；`{{slot:音频槽}}` 引用 → 400。
6. 提示词里引用了不存在的槽位 → 400；`{{slot:x.2}}` 越界 → 400。
7. 发布后 `template_spec_versions` 与 `_slots` **只插入不更新**（改模板 = 新版本）。
8. `fixed` 槽位绑定的素材（`asset_id` / `pack_id`）在发布时校验存在且可读；素材被删只影响"新建任务"，不影响历史。
9. **spec 不得复制封装成员**：`{{slot:…}}` 展开一律现场调 `LibraryLookup.expand_packs()`，
   不落库、不缓存成员列表 —— 库改了成员，模板立刻跟着变，这才是"结构归库"的含义。
10. `compile` 不得重排 `expand_packs()` 给出的成员顺序（库的 `position` 即槽位顺序）。

## 13. 开放问题（实施时再定，不阻塞 P1）

1. **模型能力声明不足**：`ModelSpec` 现在只有 `max_references`，分不出「接受几张图 / 几段音频 / 几支视频」。`attachment` 槽位（音频）到底能不能交，目前只能靠驱动自己 400。P2 前建议给 `ModelSpec` 加 `accepts: frozenset[str]`（`image/video/audio/text`）与分类型上限，`compile` 用它做受理前校验。
2. **槽位指向资产库封装**（`source: "pack"`）：**进 P1**（原先以"要扩资产库模型"为由推迟，被 §1.1 判据否决）。
   事实核查：`library_pack_items` 已有 `position` / `role` / `description`；成员允许
   `image / video / audio / text`（只禁嵌套 `pack`）；`LibraryLookup.expand_packs()` 已能按
   `position` 有序返回带 `role` 与 `readable` 的成员，且契约写明"顺序即槽位，调用方不得重排"。
   `compile` 只需调用它，不需要给资产库加任何字段。
3. **`attributes` 归一**：旧 `supportedScopes`、`wardrobeMode` 统一放进 `spec.attributes`（`dict[str, str]`），避免每收编一个概念就加一列。键名先定 `scope` / `wardrobe`，其余按需加。
4. **`compile` 的缓存**：入参 `(version, slotValues)` 是纯输入，可加一层进程内 LRU；但先不上，等有性能证据。
5. **提示词上限两处不一致** —— **已定（2026-11-14）**：两个数不是一个东西，别合并。
   - `MAX_PROMPT = 20000` 是**编写**上限（草稿能写多长），沿用旧 manifest 口径；
   - `EXECUTION_PROMPT_LIMIT = 2000` 是**执行**上限，即 `CreateTaskIn.prompt` 的
     `max_length`，放在 `core/template_spec`。
   判据分三处：保存草稿**不判**（`validate_draft(executable=False)`，否则旧模板里
   2 万字级别的提示词连草稿都存不进来，P3 迁移会被一刀切）；预检与发布按两段原文之和
   **粗判**（运营正看着屏幕，这里说清最省事）；`compile` 按**展开后的成品**精判
   （`{{slot:key*}}` 一次可能多出几十字）。漂移由
   `test_execution_prompt_limit_matches_task_schema` 钉住 —— 它直接读 `CreateTaskIn`
   的 `MaxLen`，改哪边都会先红。
   等第二个执行端点进来，这个数应随**该能力**声明（届时按模型取），而不是继续全局一个。
6. **历史替换档案的归宿** —— **已定（2026-11-14，用户拍板「直接删」）**：`replacement_runs`
   / `_run_inputs` / `_reviews` 随替换链一起删表，不导出、不留只读视图。理由：那批行记的是
   旧链路的中间态（`prompt_sha256`、归档工作区指针、渠道快照），形状与 `tasks` 不对应，
   迁不成新记录；留一份只读视图反而让人以为旧链路还活着。开发库 49 行历史记录随之消失，
   升级前的 `pg_dump` 备份是唯一回溯途径。见 §11.3。
