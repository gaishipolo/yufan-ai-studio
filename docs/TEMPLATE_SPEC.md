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
│  prompt{locked, editable}   │         │ 提交前调 compile 拿请求体  │             │ references +  │
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
| `type` | `image \| video \| audio \| text` | 决定提哪种素材选择器、以及能进哪个字段 |
| `source` | `asset \| pack` | 指向**单个资产**还是**资产库封装**（多成员、带角色与顺序） |
| `binding` | `fixed \| user` | `fixed`：封装时定死，模板库只读；`user`：模板库填 |
| `ref_mode` | `mixed \| attachment` | `mixed` 参与 `{{Mixed N}}` 编号；`attachment` 作为附加输入、不占号（音频通常用这个） |
| `min` / `max` | int | 数量范围。`min=0` 表示可选；`max` 上限受全局 10 与模型 `max_references` 双重约束 |
| `label` | str(1..40) | 槽位名的展示文案（"产品主图"/"背景音乐"） |
| `usage` | str(0..500) | 进提示词说明的用途描述（对应旧 `PACK_ROLES` 的说明与逐图 `description`） |
| `asset_id` | str \| null | `source=asset` 且 `binding=fixed` 时必填；其余为空 |
| `pack_id` | str \| null | `source=pack` 且 `binding=fixed` 时必填；其余为空 |
| `position` | int | 排序，**就是编号顺序**，见 §6 |

约束：

- `type=text` 不许 `ref_mode=mixed`（文本不占视觉编号）；
- `type=audio` 只允许 `ref_mode=attachment`；
- 版本内 `position` 连续唯一；`key` 唯一；
- `source=pack` 时 `type` 由成员推导（成员必须同类型），`min/max` 不参与（数量 = 成员数）；
- `source=asset` 且 `binding=fixed` 只能表达**一个**资产；需要固定多张时，要么用 `source=pack`
  指向一个资产库封装，要么拆成多个槽位（后者只在"几张互不相关的图"时才用）。

### 4.2 TemplateSpec

```jsonc
{
  "key": "product-swap-6036",          // 幂等边界，发布后不可改名
  "name": "6036 产品替换",
  "description": "",
  "model": "libtv-wan3-replacement",   // 必须是目录里存在的公开模型
  "params": { "aspectRatio": "9:16", "durationSec": 6 },  // 键必须在该模型 param_schema 内
  "prompt": {
    "locked": "8 秒 · 9:16 竖屏写实短片，眼平正面中景…严格跟随参考图生成。",  // 封装写死，模板库只读
    "editable": "把 {{slot:source}} 里的内衣换成 {{slot:hero}} 的同款。{{slot:detail*}} 只补细节。"  // 模板库可改
  },
  "slots": [
    { "key": "source", "type": "video", "binding": "fixed", "refMode": "mixed",
      "min": 1, "max": 1, "position": 0, "label": "原片", "assetId": "0a2062…" },
    { "key": "hero",   "type": "image", "binding": "user",  "refMode": "mixed",
      "min": 1, "max": 1, "position": 1, "label": "产品主图", "usage": "确定同款同色外观" },
    { "key": "detail", "type": "image", "source": "pack", "binding": "user", "refMode": "mixed",
      "position": 2, "label": "细节封装", "usage": "只补纹理与扣件" },   // 用户选一个资产库封装
    { "key": "look",   "type": "image", "source": "asset", "binding": "user", "refMode": "mixed",
      "min": 0, "max": 2, "position": 3, "label": "补充参考" },          // 用户挑散图
    { "key": "bgm",    "type": "audio", "binding": "user",  "refMode": "attachment",
      "min": 0, "max": 1, "position": 3, "label": "背景音乐" }
  ]
}
```

## 5. 提示词的两段

- `locked`：封装时写死，模板库只读、灰显。对应截图里的「定量（强制固定，严禁改劝）」块。
- `editable`：模板库可改，默认值由封装给；为空表示"整段固定"。

编译顺序固定为 `locked + "\n\n" + editable`，两段都做同一套引用与上限校验。字数上限沿用 `MAX_PROMPT = 20000`，分别统计、总和校验。

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
   - `source=asset`：`fixed` 取 spec 里的 `asset_id`；`user` 取入参 `slotValues[key]`（资产 id 列表），数量必须落在 `[min, max]`；
   - `source=pack`：取 spec 的 `pack_id`（fixed）或入参（user）里的封装 id，调
     `LibraryLookup.expand_packs()` 展开成员 —— **顺序由库给，编译不得重排**；成员必须同类型、
     全部 `readable`（有不可读成员就报"封装《X》的成员 Y 无权访问"，不静默少一张）；
   - 单资产的可读性与存在性一律经 `library_lookup`，不直查表。
2. 按 `position` 升序，只对 `ref_mode=mixed` 的槽位累加计数，得到 `slot_key → [token…]`；`attachment` 槽位跳过（不占号）。
3. 把 `{{slot:…}}` 替换成对应 token；`attachment` 槽位被引用 → 400（它不该出现在提示词里）。
   `source=pack` 的槽位，其成员的 `role` 作为该 token 的角色写进执行请求（`expand_packs` 已带出）。
4. 校验总量：`mixed` 展开后总数 ≤ `min(10, 模型 max_references)`；音频/文本数量按模型声明另判（§13 开放问题）。
5. 产出执行请求：

```jsonc
{
  "model": "libtv-wan3-replacement",
  "prompt": "<locked>\n\n<editable，token 已展开>",
  "params": { "aspectRatio": "9:16", "durationSec": 6 },
  "referenceLibraryIds": ["0a2062…", "454955…", "5987f1…"],   // mixed 按编号序 + attachment 追加在后
  "textLibraryIds": ["…"],                                      // text 槽位
  "slotTokens": { "source": ["{{Mixed 1}}"], "hero": ["{{Mixed 2}}"], "detail": ["{{Mixed 3}}"] }
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
| `prompt_locked` / `prompt_editable` | `Text` |
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
| `POST` | `/template-specs/{key}/disable` | 下架 |

消费侧：

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/template-specs/published` | 模板库卡片：已发布版本的快照（slots + 两段 prompt + model + params），不返回 draft |
| `POST` | `/template-specs/{key}/compile` | **纯函数式**：入参 `{version?, slotValues, promptOverride?}`，出参见 §6.5。不落库、不调 task |

`compile` 之所以存在：展开与校验是**模板语义**，不能下放前端（否则编号规则有两份），也不该让封装去调 task（跨模块）。它只做映射，不产生副作用，天然可缓存、可单测。

模板库拿到 `compile` 出参后，由前端直接 `POST /tasks`。有一道**字段摊平**：`/tasks` 的
`durationSec` / `aspectRatio` / `stylePreset` 是顶层字段、没有自由 `params`，所以
`params` 里出现这三个以外的键（生图模型的 `size` / `count`）必须**报错而不是丢掉** ——
丢掉等于按模型默认值跑，而模板里明明写了。映射只在前端一处
（`features/template-library/taskPayload.ts`），单测直接断言它产出的形状。

`promptOverride` 是填表时改写的 `editable` 段，三条硬口径（服务端判，不信调用方）：
`locked` 段永不可改；模板的 `editable` 为空（作者关掉了入口）时传它 400；
override 里写 `{{Mixed N}}` 也 400（编号是派生的，写死会在少填时错位）。

## 9. 封装页 UI 规格（按参考截图）

单页编辑，三块区域：

```
┌─ 槽位区（左） ────────────┬─ 提示词区（中） ──────────────────────┐
│ [+ 上传] [从资产库选]      │  ┌ 已引用（= 槽位清单，可点回定位/删） │
│ ┌──┐┌──┐                  │  │  6036 / 米兰之夜6036-产品…          │
│ │1 ││2 │  ← 角标 = 编号    │  └────────────────────────────────────┘
│ └──┘└──┘                  │  [locked 段：灰底，只读]                │
│ 槽位卡片：类型/固定或待选/  │  [editable 段：可编辑，@ 唤起引用弹层]  │
│ min-max/用途说明            │  总字数 961 / 20000（分段计）           │
├───────────────────────────┴────────────────────────────────────────┤
│ [模型 ⌄] [模式/参数：9:16·720P·8s·1个 ⌄]              [预检][发布]   │
└────────────────────────────────────────────────────────────────────┘
```

`@` 弹层两级（照截图）：

- 一级：`已引用`（当前槽位清单）+ `素材引用`：图片 / 视频 / 音频 / 文本 / 资产；
- 二级：
  - 图片 / 视频 / 音频 / 文本 → 插入 `source=asset, binding=user` 槽位（占位留空，模板库填）；
  - 资产 → 选**单个资产**则插入 `source=asset` 槽位，选**资产库封装**（`kind=pack`）则插入
    `source=pack` 槽位，成员数与角色由封装带出、界面上只显示「N 张 · 角色列表」；
  - 每个槽位卡片上有「封装时固定」开关：打开 = `binding=fixed`（模板库只读），关闭 = `user`。

细节：

- 插入位置决定 `position`；拖动缩略图改序 → 重排 `position` → 角标实时重算（因为编号是派生的，这里天然一致）。
- 音频/文本槽位在提示词里**不出现 chip**，只在槽位区列出，且 `ref_mode=attachment` 时提示「作为附加输入，不参与编号」。
- 底部模型切换后，`params` 区按新模型的 `param_schema` 重绘（已有能力）；旧值不在新 schema 里就丢弃并提示。

## 10. 模板库填表页

同一份 spec 的**只读镜像**：

- `locked` 段灰显不可编辑；`editable` 段可改，但 `{{slot:…}}` 引用不可手改（点击 chip 可定位到槽位）；
- `fixed` 槽位显示缩略图 + 「封装时固定」；`user` 槽位显示占位，按 `source` 走不同选择器：
  `source=asset` → `/library/selection`（按 `type` 过滤，`min/max` 决定单选或多选）；
  `source=pack` → 资产库封装选择器（只列同 `type` 成员的封装），选中后展示「N 张」与角色列表；
- 提交按钮在 `min` 未满足时禁用，旁边说明缺哪个槽位（不写冗长引导语）；
- 提交 = `compile` → **字段摊平** → `POST /tasks` → 提示 + 「去任务中心」。

### 10.1 已落地的形态（2026-11-14，`/templates/library`）

两栏常驻：左列卡片（名称 / 版本 / 槽位数 / 固定数），右栏就地填表。换模板只换右栏，
不跳页 —— 填到一半不会丢上下文。要点：

- **卡片不伪造封面**：spec 没有封图字段，用 key 生成稳定渐变，不拿"第一张参考图"
  冒充模板示意图（那会让人以为模板长那样）。
- 槽位卡片左上角是序号（= 编号顺序），`fixed` 显示缩略图 + 「封装固定」且**没有选择按钮**，
  `user` 显示「选择 / 更换」；封装槽位只显示「N 张」与首图，成员顺序不在这里展开。
- 固定段灰显 + 「不可编辑」；可编辑段是一个 textarea，改动才随 `promptOverride` 发出
  （没改就不发，服务端用发布版本原文）。
- 底部按钮在必填未满足时禁用并说明缺几项 —— 这是提交前置条件，不是业务规则；
  数量上下限、可读性、编号展开仍然只有服务端一份。
- 模型与参数**只读展示**：模板定的是模板定的。要改参数回规格页改模板。

## 11. 迁移与分期

现有数据不丢、不重写：

| 旧 | 新 |
|---|---|
| `manifest.video.assetId` | 一个 `fixed / video / mixed` 槽位（`position=0`，key `source`） |
| `manifest.packs[]`（旧素材包） | **首选**：整体落成一个资产库封装（`kind=pack`，成员 `position`/`role`/`description` 原样带过去），spec 里用一个 `source=pack, binding=fixed` 槽位指向它 —— 这样"多视图属于同一对象"的结构归位到资产库。<br>**降级**（仅当这批图互不相关）：拆成多个 `source=asset, binding=fixed` 槽位。降级会让模板重新拥有一份结构，只在旧包本身没有对象语义时使用，并在迁移报告里逐条标出 |
| `manifest.functions[]` | **每个 function 拆成一条 spec**（key = `manifest.key:function.key`）。一 spec = 一张卡片，与原"一功能一卡片"一致 |
| `manifest.functions[].prompt` | `prompt.editable`（原样）；`locked` 留空（旧模型本来整段只读，语义上更接近 locked，但为了让模板库可改，先放 editable 并在迁移报告里注明） |
| `manifest.functions[].scopeOptions` | 暂不迁（替换专属，见 P3） |
| `video_templates` | **只读历史，不再写入**。替换链的 `template-runtime` 端口在 **P3** 改为读 spec 版本（见下方"接读为什么不在 P1"）；旧行保留到 P3 删除 |

三期，每期可独立上线、独立全绿：

| 期 | 内容 | 验收 |
|---|---|---|
| **P1 定表** | `core/` 加 `InputSlot`/`TemplateSpec` 词表与编译纯函数；三张表（迁移 `d4a9f1c73b20`）；六个接口（含 `compile`）；封装页改成 §9 单页 | `compile` 输出与旧模板的 `mediaOrder` **逐项等价**（`tests/test_template_spec_equivalence.py`，已用"注入重排"验过会红）；预检挡住所有非法组合（§12）；**不写 `video_templates`、也不假装能读**（见下方"接读为什么不在 P1"） |
| **P2 填表**（页面已落地） | 模板库填表页 `/templates/library`（两栏：左选模板 / 右填素材；`source=pack` 走封装选择器；`promptOverride` 只在改动时发）；`compile → 摊平 → /tasks` 已用真实素材跑通（`queued`、6 秒、4 张参考） | 四种组合各跑通一次：纯提示词（`agnes-video-2.5-flash`，maxRefs=0）、多图（`agnes-image-*`，maxRefs=4）、图+视频、加音频附件；另加一条"选一个资产库封装填一个槽位"。**已完成**：固定原片 + 封装槽位（真实素材，任务 `queued`）；其余三种未跑 |
| **P3 接读 + 迁移 + 删表 + capability 化** | 替换链的 `template-runtime` 端口改读 spec 版本（旧 `video_templates` 只读）；**旧数据迁移**（上表那套映射，含旧素材包 → 资产库封装）；删 `video_templates` / `template_packs*`；替换能力作为 task 的一个 capability；`supportedScopes`/`wardrobeMode` → `spec.attributes`；成片复核 → 任务中心；`modules/template` 的编排与物化下线。细则见 §11.1 | §11.1 的三条判据全绿；替换业务与历史档案不中断；`prepare` 用 spec 驱动的模板跑通；迁移出的 spec 与旧模板 `mediaOrder` 逐项等价 |

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

### 11.1 P3 终局：统一到 task（2026-11-14 拍板）

**不留独立替换工作台。** 替换不再是一条平行链路，而是"某个模型 + 某组槽位"的普通生成：

| 现在的替换专属物 | P3 之后 |
|---|---|
| `video_templates` 表 + `/templates/video-templates` 路由 | **P1 起只读**（runtime 改读 spec），P3 删除。模板只在 `template_specs` |
| `template_packs` / `_references` / `_versions` | 删。多图语义由 `source=pack` 槽位指向资产库封装表达（见 §4.1）；旧包在 P1 迁成资产库封装 |
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
5. **提示词上限两处不一致**（P2 提交前必须定）：spec 的 `MAX_PROMPT` 是 20000，
   而 `POST /tasks` 的 `CreateTaskIn.prompt` 是 `max_length=2000`。于是 3000 字的规格
   能发布、能在 `compile` 里成功，提交时才 422 —— 正是"晚失败"。上限其实是**目标能力**
   的属性（替换链接受长提示词，通用视频端点不接受），所以要随 §13.1 的 `accepts` 一起
   由 `ModelSpec` 声明，在发布或 `compile` 时挡。别把 spec 的上限直接降到 2000：
   旧模板里有 20000 字级别的提示词，P3 迁移会被一刀切拒掉。
6. **历史替换档案的归宿**（P3 前必须定）：`replacement_runs` 是只读归档、还是整表导出成对象存储后删表？
   它是"重跑与复核的唯一依据"，因此至少要保住只读可查；具体形态等 P3 开工时拍。
