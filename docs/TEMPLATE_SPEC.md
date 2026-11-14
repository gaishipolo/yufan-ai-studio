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

## 2. 目标 / 非目标

**目标**

1. 同一套结构支持四种组合：纯提示词、提示词+多图、提示词+图片+视频、再加音频/文本参考。
2. 封装模块只管「定表」：槽位声明、提示词、模型与参数默认值。**不取字节、不查素材元数据、不物化**。
3. 模板库只管「填表」：把槽位填满并提交。不复制校验规则。
4. task 不知道「模板」存在。
5. 素材取用只剩两处：`core/ports/library_lookup`（元数据/可读性）与 task 的 `generic_inputs.materialize`（字节落地）。

**非目标（本期不做）**

- 把替换链并进 task（见 §11 P3：先保留，语义另议）；
- 槽位直接指向「资产库封装」（`binding: "pack"` 预留字段，见 §13）；
- 模板市场 / 跨工作台共享 / 版本回滚 UI。

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
| `binding` | `fixed \| user` | `fixed`：封装时绑定具体素材，模板库只读；`user`：模板库填 |
| `ref_mode` | `mixed \| attachment` | `mixed` 参与 `{{Mixed N}}` 编号；`attachment` 作为附加输入、不占号（音频通常用这个） |
| `min` / `max` | int | 数量范围。`min=0` 表示可选；`max` 上限受全局 10 与模型 `max_references` 双重约束 |
| `label` | str(1..40) | 槽位名的展示文案（"产品主图"/"背景音乐"） |
| `usage` | str(0..500) | 进提示词说明的用途描述（对应旧 `PACK_ROLES` 的说明与逐图 `description`） |
| `asset_id` | str \| null | `binding=fixed` 时必须给；`user` 时必须空 |
| `position` | int | 排序，**就是编号顺序**，见 §6 |

约束：

- `type=text` 不许 `ref_mode=mixed`（文本不占视觉编号）；
- `type=audio` 只允许 `ref_mode=attachment`；
- 版本内 `position` 连续唯一；`key` 唯一；
- `binding=fixed` 且 `max>1` 时，`asset_id` 表达单值，多值固定素材请拆成多个槽位（保持编号可预测）。

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
    { "key": "detail", "type": "image", "binding": "user",  "refMode": "mixed",
      "min": 0, "max": 2, "position": 2, "label": "细节图", "usage": "只补纹理与扣件" },
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

1. 校验填充：`fixed` 槽位取封装的 `asset_id`；`user` 槽位取入参 `slotValues[key]`；数量必须落在 `[min, max]`，类型必须匹配（用 `library_lookup` 校验存在与可读，不直查表）。
2. 按 `position` 升序，只对 `ref_mode=mixed` 的槽位累加计数，得到 `slot_key → [token…]`；`attachment` 槽位跳过（不占号）。
3. 把 `{{slot:…}}` 替换成对应 token；`attachment` 槽位被引用 → 400（它不该出现在提示词里）。
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
| `slot_key` / `type` / `binding` / `ref_mode` | |
| `min_count` / `max_count` | 不用 `min`/`max`：SQL 里是聚合函数名，写迁移时容易踩 |
| `label` / `usage_text` | |
| `asset_id` | `binding=fixed` 时非空 |
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
| `POST` | `/template-specs/{key}/compile` | **纯函数式**：入参 `{version?, slotValues}`，出参见 §6.5。不落库、不调 task |

`compile` 之所以存在：展开与校验是**模板语义**，不能下放前端（否则编号规则有两份），也不该让封装去调 task（跨模块）。它只做映射，不产生副作用，天然可缓存、可单测。

模板库拿到 `compile` 出参后，由前端直接 `POST /tasks`。这是一次纯转发，没有业务判断。

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
- 二级：对应类型的素材选择器（复用 `/library/selection`）；
- 从「资产」选 = 插入 `binding=fixed` 槽位（封装时定死）；从「图片/视频/音频/文本」插入 = `binding=user` 槽位，占位留空。

细节：

- 插入位置决定 `position`；拖动缩略图改序 → 重排 `position` → 角标实时重算（因为编号是派生的，这里天然一致）。
- 音频/文本槽位在提示词里**不出现 chip**，只在槽位区列出，且 `ref_mode=attachment` 时提示「作为附加输入，不参与编号」。
- 底部模型切换后，`params` 区按新模型的 `param_schema` 重绘（已有能力）；旧值不在新 schema 里就丢弃并提示。

## 10. 模板库填表页

同一份 spec 的**只读镜像**：

- `locked` 段灰显不可编辑；`editable` 段可改，但 `{{slot:…}}` 引用不可手改（点击 chip 可定位到槽位）；
- `fixed` 槽位显示缩略图 + 「封装时固定」；`user` 槽位显示「选择图片/视频/音频/文本」占位，走 `/library/selection`；
- 提交按钮在 `min` 未满足时禁用，旁边说明缺哪个槽位（不写冗长引导语）；
- 提交 = `compile` → `POST /tasks` → 跳工作台或任务中心。

## 11. 迁移与分期

现有数据不丢、不重写：

| 旧 | 新 |
|---|---|
| `manifest.video.assetId` | 一个 `fixed / video / mixed` 槽位（`position=0`，key `source`） |
| `manifest.packs[].references[]` | 每张图一个 `fixed / image / mixed` 槽位；成员的 `description` → `usage` |
| `manifest.functions[]` | **每个 function 拆成一条 spec**（key = `manifest.key:function.key`）。一 spec = 一张卡片，与原"一功能一卡片"一致 |
| `manifest.functions[].prompt` | `prompt.editable`（原样）；`locked` 留空（旧模型本来整段只读，语义上更接近 locked，但为了让模板库可改，先放 editable 并在迁移报告里注明） |
| `manifest.functions[].scopeOptions` | 暂不迁（替换专属，见 P3） |
| `video_templates` | 不再作为编写源；P2 期间由 spec 发布时投影回旧表，供替换链继续消费 |

三期，每期可独立上线、独立全绿：

| 期 | 内容 | 验收 |
|---|---|---|
| **P1 定表** | `core/` 加 `InputSlot`/`TemplateSpec` 词表与编译纯函数；三张表 + 迁移；spec 六个接口（含 `compile`）；封装页改成 §9 单页 | 用现有 6036 素材配出「fixed 原片 + 2 个 user 图片槽」，`compile` 输出与旧模板的 `mediaOrder` **逐项等价**；预检能挡住所有非法组合（见 §12） |
| **P2 填表** | 模板库填表页；`compile → /tasks` 走通 | 四种组合各跑通一次：纯提示词（`agnes-video-2.5-flash`，maxRefs=0）、多图（`agnes-image-*`，maxRefs=4）、图+视频、加音频附件 |
| **P3 收编** | 替换链去留：把 `supportedScopes`/复核语义上移为 spec 的可选字段并作为 task 的一个 capability，或保留独立工作台 | 旧 `video_templates` 只读投影为 spec 视图，替换业务不中断；跑一轮历史任务复跑 |

## 12. 不变量与守卫（每条都要有测试）

1. `modules/template_pack` **不得** import `modules/task`（既有守卫已覆盖，新增代码别破）。
2. spec 相关代码**不得**读写 `video_templates` / `template_pack_references`（扫源码字符串 + 扫 import）。
3. 封装模块**不得**直接 `select(LibraryAsset)`：素材校验一律经 `library_lookup`（扫 import 与表名）。
4. 编号只由 `position` + 填充数量决定：给定同一份 spec 与同一组填充，`compile` 是纯函数（同一入参两次调用结果相同）。
5. `audio` 槽位永不拿到 `{{Mixed N}}`；`{{slot:音频槽}}` 引用 → 400。
6. 提示词里引用了不存在的槽位 → 400；`{{slot:x.2}}` 越界 → 400。
7. 发布后 `template_spec_versions` 与 `_slots` **只插入不更新**（改模板 = 新版本）。
8. `fixed` 槽位的 `asset_id` 在发布时校验存在且可读；素材被删只影响"新建任务"，不影响历史。

## 13. 开放问题（实施时再定，不阻塞 P1）

1. **模型能力声明不足**：`ModelSpec` 现在只有 `max_references`，分不出「接受几张图 / 几段音频 / 几支视频」。`attachment` 槽位（音频）到底能不能交，目前只能靠驱动自己 400。P2 前建议给 `ModelSpec` 加 `accepts: frozenset[str]`（`image/video/audio/text`）与分类型上限，`compile` 用它做受理前校验。
2. **槽位指向资产库封装**（`binding: "pack"`，预留）：需要资产库封装先有"成员角色/顺序"语义。字段留着，本期不实现；实现时是纯增量。
3. **`attributes` 归一**：旧 `supportedScopes`、`wardrobeMode` 这类替换专属开关，收编时统一放进 `spec.attributes`（`dict[str, str]`），避免每收编一个概念就加一列。
4. **`compile` 的缓存**：入参 `(version, slotValues)` 是纯输入，可加一层进程内 LRU；但先不上，等有性能证据。
