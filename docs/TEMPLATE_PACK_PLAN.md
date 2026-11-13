# 方案：template_pack 模块（带筛选约束的封装模板）

> 状态：**已拍板，开始实施**，2026-11-13。
> 模块名：`template_pack`。与现有 `modules/template`（视频模板 / 素材包）**并行独立**，
> 不集成、不互相 import。

---

## 0. 需求与已拍板决策

**需求**：用户点「新增模板」进入编辑页 → 上传参考视频 → 编辑提示词 →
提示词里的槽位可以放**固定素材**，也可以是**筛选资产库 v2 素材**（如
「一级标签=产品 且 kind=image」）→ 发布后进模板库；使用时用户**只能**选符合
筛选条件的素材。

| 决策点 | 结论 |
|---|---|
| 模块名 | `template_pack` |
| 与现有模板的关系 | **不集成**。留一个 ports 出口给需要它的模块取用 |
| 筛选维度 | 一级标签 + 素材类别 kind |
| 槽位语义 | 每个槽位**二选一**：固定素材 或 筛选槽位 |
| 其余 | 按默认（详见 §9 落地口径） |

---

## 1. 与现有能力的边界

| 能力 | 现有实现 | 本模块 |
|---|---|---|
| 视频模板 | `modules/template/video_template_*.py` | 不复用，自有一套表 |
| 素材包 | `modules/template/pack_*.py` | 不复用。筛选槽位指向资产库 v2 的**单条素材** |
| 槽位映射 | `replacement_service._media_in_mixed_order` | 自己一套（禁 import 对方模块） |
| 标签筛选 | `modules/library/tags.py::expand_tag_ids` | **经 ports 调用**，不 import |

**关键区别**：现有模板运行时选的是**包**；本模块运行时选的是**单条素材**。

---

## 2. 关键发现：不需要新建筛选端口

`core/ports/library_lookup.py` 里 **已有 `LibraryLookup.find_ids()`**，签名正好覆盖需求：

```python
async def find_ids(
    self, session, actor, *,
    kind: LibraryAssetKind | None = None,
    tag_ids: Sequence[str] = (),
    owner_id: str | None = None,
    limit: int = 50,
) -> list[str]
```

实现（`modules/library/lookup.py`）已包含全部四条判据：
- `state=active` 硬过滤
- `kind` 精确匹配
- `tag_ids` 经 `expand_tag_ids` **OR 展开到二级**（一级标签 → 自身 + 直属子级）
- `readable_rows()` 可见性判定（含小组共享、封装成员入口）

**结论**：直接用它，**不写新的筛选端口**。用户说的「ports 已经有了就不要重复写」正落在这里。

---

## 3. 数据模型

### 3.1 表名冲突（必须避开）

现有已占用：`template_packs`、`template_pack_references`、`template_pack_versions`、
`library_pack_items`。

本模块用 `template_pack_*` 前缀但避开上述：

| 表 | 说明 |
|---|---|
| `template_pack_templates` | 主表 |
| `template_pack_slots` | 槽位表（模板 1:N） |

### 3.2 `template_pack_templates`

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | 32 hex | UUIDMixin |
| `template_key` | str(80) | 稳定标识；同键同配置返原行，同键异配置 409 |
| `name` | str(160) | 模板名称 |
| `description` | str(2000) | 说明 |
| `prompt` | text | 提示词原文 |
| `prompt_sha256` | str(64) | 防篡改 |
| `source_video_asset_id` | str(64) | 参考视频（资产库 v2 id） |
| `source_video_id_space` | str(8) | `v1`/`v2`，显式分流 |
| `source_duration_sec` | float | 原片时长 |
| `aspect_ratio` | str(16) | 默认 `9:16` |
| `status` | str(16) | `draft`/`published`/`disabled` |
| `owner_id` | str(32) | 发布者 |
| `published_at` | datetime | |
| `created_at`/`updated_at` | datetime | TimestampMixin |

### 3.3 `template_pack_slots`

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | 32 hex | |
| `template_id` | str(32) | 索引 |
| `position` | int | 从 1 起；**决定 `{{Mixed N}}` 编号** |
| `slot_kind` | str(16) | `fixed` / `filtered` |
| `token` | str(32) | 占位符，如 `{{Mixed 2}}` |
| `label` | str(64) | 槽位显示名 |
| `asset_id` | str(64) | fixed：素材 id |
| `asset_id_space` | str(8) | fixed：id 空间 |
| `media_type` | str(16) | fixed：video/image/audio |
| `filter_kind` | str(16) | filtered：image/video/audio |
| `filter_tag_ids` | JSON(list) | filtered：一级标签 id 列表 |
| `filter_tag_match` | str(8) | filtered：`any`/`all`，默认 `any` |
| `required` | bool | 默认 true |

**position=1 固定是参考视频**（`{{Mixed 1}}`），其余按 position 顺序对应 `{{Mixed 2}}`…

---

## 4. 筛选语义

1. **一级标签展开**：选中一级 = 自身 + 直属子级，OR 语义（`find_ids` 已实现）
2. **多标签**：**只有 `any`**（与资产库列表筛选一致）。
   ~~`all`~~ —— **落地时去掉了**。`find_ids` 的标签语义固定是 OR
   （`tag_id.in_(expanded)`），没有「全部匹配」的实现。留着 `all` 会让用户
   选了一个说好更严、实际更松的条件 —— **筛选只收窄不放宽**，这条是硬约束。
3. **可见性叠加**：筛选只收窄不放宽 —— 可读集 ∩ 筛选条件 ∩ `active` ∩ 未删除
4. **kind 白名单**：只允许 `image`/`video`/`audio`。**`text` 排除**——文本资产不是参考图
5. **标签存 id 不存名字**：改名不断引用，展示时取名字

---

## 5. API 契约

前缀 `/template-packs`。

| 方法 | 路径 | 权限 | 限流 scope | 说明 |
|---|---|---|---|---|
| POST | `/template-packs` | `template_pack:create` | `template_pack.create` | 保存草稿（幂等） |
| GET | `/template-packs` | `template_pack:read` | — | 列表 |
| GET | `/template-packs/{id}` | `template_pack:read` | — | 详情（含槽位） |
| POST | `/template-packs/{id}/publish` | `template_pack:publish` | `template_pack.publish` | 发布 |
| POST | `/template-packs/{id}/disable` | `template_pack:publish` | `template_pack.disable` | 停用 |
| PATCH | `/template-packs/{id}/prompt` | `template_pack:update` | `template_pack.update` | 改提示词 |
| GET | `/template-packs/{id}/candidates` | `template_pack:use` | — | 运行时取合规素材池 |
| POST | `/template-packs/{id}/verify` | `template_pack:use` | `template_pack.verify` | 提交前复核选材 |

**每个写路由都要声明限流**（`api/ratelimit_policy.py` 的守卫会拦，漏挂直接红）。

### 出入参

```python
class TemplatePackSlotIn(CamelModel):
    token: str = Field(pattern=r"^\{\{Mixed [1-9]\d*\}\}$")
    slot_kind: Literal["fixed", "filtered"]
    label: str = ""
    asset_id: str | None = None
    asset_id_space: Literal["v1", "v2"] = "v2"
    media_type: Literal["video", "image", "audio"] = "image"
    filter_kind: Literal["image", "video", "audio"] | None = None
    filter_tag_ids: list[str] = Field(default_factory=list, max_length=20)
    filter_tag_match: Literal["any"] = "any"
    required: bool = True

class TemplatePackDraftIn(CamelModel):
    name: str = Field(min_length=1, max_length=160)
    template_key: str = Field(default="", max_length=80)
    description: str = Field(default="", max_length=2000)
    prompt: VerbatimText = Field(min_length=1, max_length=20000)
    source_video_asset_id: str = Field(min_length=1, max_length=64)
    source_video_id_space: Literal["v1", "v2"] = "v2"
    aspect_ratio: str = Field(default="9:16", max_length=16)
    slots: list[TemplatePackSlotIn] = Field(min_length=1, max_length=11)
    cover_url: str = ""
```

> `asset_id_space` 默认 **`v2`**：本模块只面向资产库 v2。v1 与 v2 的 id
> 都是 32 位 hex，形状不可区分，默认给 v1 会让「不显式指定空间」的调用
> 静默落到空的 v1 空间。

---

## 6. 架构约束与解法

业务模块之间**禁止互相 import**（AST 静态守卫）。本模块需要：

| 需要的能力 | 合法通道 | 状态 |
|---|---|---|
| 按 id 读 v2 素材元数据 | `core/ports/library_lookup.py` 的 `get_library_lookup()` | **已有** |
| 判定 actor 可读哪些 id | `readable_ids()` | **已有** |
| 按条件列候选素材 | `find_ids(kind, tag_ids)` | **已有，不重复写** |
| 供外部模块取用已封装模块 | **新增端口** `core/ports/template_pack.py` | 待建 |

### 6.1 新增端口：`core/ports/template_pack.py`

用户要求「留一个出口给模板获取已经封装的模块」。现有 ports 里没有等价物
（`library-lookup` 是「查 v2 资产」，不是「查封装模块」），所以**需要新建**。

```python
@dataclass(frozen=True, slots=True)
class TemplatePackSummary:
    id: str
    name: str
    template_key: str
    prompt: str
    prompt_sha256: str
    status: str
    owner_id: str
    source_video_asset_id: str
    source_duration_sec: float
    aspect_ratio: str
    slots: tuple[TemplatePackSlotSummary, ...]
    published_at: datetime | None

@runtime_checkable
class TemplatePackPort(Protocol):
    async def list_published(self, session, *, owner_id: str = "") -> list[TemplatePackSummary]: ...
    async def get_published(self, session, pack_id: str) -> TemplatePackSummary | None: ...
    async def candidates(self, session, actor, pack_id, token) -> list[...]: ...
```

按 `api/ports.py` 的规矩：三件套（`register_*`/`reset_*`/`has_*`）+ 登记 `PORT_CATALOG`。
未注册时的行为：**返回空列表**（中性方向 —— 少看见不会多看见），
不抛异常（`model-generation` 那种 fail-closed 是因为「空模型列表」会把装配故障
报成「用户没配模型」；这里「没有已封装模块」是合法状态）。

---

## 7. 运行时链路

```
GET /template-packs/{id}/candidates   → 每个筛选槽位的合规素材池
  ↓
用户选素材（选择器只显示合规项）
  ↓
POST /template-packs/{id}/verify      → 服务端复核选材
  ↓
提交生成（携带 packId + 各槽位选中的 assetId）
  ↓
按 position 组装 mixedList → translate_prompt 重编号 → 提交生成
```

**服务端必须复核**。前端过滤只是 UI 便利，接口可被直接调用；筛选条件是模板的
业务契约，越权提交等于用错素材生成。

### 复核为什么逐条判，而不是「把整个池子捞出来再查表里有没有」

`LibraryLookup.find_ids` 内部有 `_MAX_CANDIDATES = 500` 的翻页上限（它要判
可见性，可读性压不进一条 SQL）。拿它做复核的话：合规素材超过 500 条时，排在
窗口外的**合法选择会被误判为不合规** —— 用户明明选的是合规素材，却被告知
「不符合要求」。这是把「池子太大」当成「素材不对」。

所以反过来做：**先看这条素材本身**（类别、在库、可读），再看**它挂的标签**
是否满足筛选条件。判的是「这一条」，与池子大小无关。

### 生成侧接入（本次不做）

「按 position 组装 mixedList → 提交生成」那一步属于 `modules/task` 的改动。
本次只把**出口**留好：其他模块通过 `core/ports/template_pack.py` 的
`TemplatePackPort` 取已发布的封装模板与合规候选池，不 import 本模块。

---

## 8. 文件清单

### 后端（新增）

```
api/src/modules/template_pack/
├── __init__.py       # ModuleSpec：路由 + 权限点（末尾 import port_impl 完成注册）
├── models.py         # 两张表 ORM
├── schema.py         # 出入参
├── service.py        # 业务规则
├── slot_rules.py     # 筛选条件校验（新模块私有）
├── router.py         # HTTP 契约
├── dependencies.py   # 权限依赖
├── errors.py         # ModuleErrors(slot=3)
├── repository.py     # 表访问
└── port_impl.py      # 端口实现（import 期注册）
api/src/core/ports/template_pack.py   # 新端口（供外部取用）
api/tests/test_template_pack.py       # 27 个用例
```

### 后端（改动）

```
api/src/api/ports.py                 # PORT_CATALOG 登记新端口
```

### 生成物

```
api/migrations/versions/<hash>_template_pack_tables.py
api/docs/openapi.json
api/docs/API.md
studio/src/shared/api/schema.ts
studio/src/shared/api/permissions.ts
docs/ROUTES.md
docs/MOCK_COVERAGE.md
```

### 前端

```
studio/src/modules/template-pack/module.ts        # 路由注册（4 条）
studio/src/modules/template-pack/mock.ts          # mock（筛选语义真生效）
studio/src/pages/template-pack/
├── TemplatePacksPage.tsx        # 列表（模板库 / 我的草稿）
├── TemplatePackEditorPage.tsx   # 编辑页（参考视频 + 提示词 + 槽位）
└── TemplatePackDetailPage.tsx   # 详情页（已发布可「用」）
studio/src/features/template-pack/
├── TemplatePackSlotEditor.tsx       # 槽位编辑器（二选一）
└── TemplatePackRuntimePicker.tsx   # 运行时选材（候选池由服务端算）
studio/src/entities/template-pack/
├── api/templatePack.api.ts
├── api/templatePack.queries.ts
└── model/templatePackSlots.ts      # 槽位编辑态纯逻辑（15 个用例）
```

---

## 9. 落地口径（默认项）

- 一槽位一素材（不支持一个占位符选多张）
- 筛选条件实时查询（不做发布时冻结候选集）
- **发布后内容全锁**：改提示词也要重新发布（已发布的模板可能已被别人引用）
- 标签存 id；**只有 `any`**（`find_ids` 的唯一口径就是 OR，见 §4）
- 参考图上限 10（本模块自定义常量，不动 `modules/template` 的那份）
- **幂等按状态分三态**（不是二态）：

  | 已有行的状态 | 同键同配置 | 同键异配置 |
  |---|---|---|
  | `draft`（自己的） | 返回原行 | **允许改**（草稿是私有工作区） |
  | `published` / `disabled` | 返回原行 | 409 `TemplatePackConflict` |
  | 别人的（任何状态） | — | 409，**不确认存在**（避免命名空间探测） |

  > `modules/template` 是**一次性创建**（没有草稿态），所以它同键异配置一律 409。
  > 本模块有草稿→发布生命周期，照抄它的口径会让用户改一个字就存不了。

---

## 10. 与现有不变量的一致性

| 现有不变量 | 怎么遵守 |
|---|---|
| 提示词引用只有一份实现（`core/prompt_refs.translate_prompt`） | 直接用，不另写 |
| 模块之间不许互相 import | 走 `core/ports/` 装配期端口 |
| 注入口三件套 + PORT_CATALOG 登记 | 新端口照抄 `core/ports/host.py` 的 `Port` 模式 |
| 契约四份一起生成 | 改 router/schema/permissions 就重跑 `cli export-openapi` |
| 错误码 `status*100+slot*10+seq` | slot=3（1/3/4/6 空闲），码值不撞 |
| 状态机终态保护 | draft→published→disabled；终态不可再改内容 |
| `{{Text N}}` 注入只有一条链 | 本模块不支持文本槽位 |
| 选择器默认排除文本 | `filter_kind` 不含 `text` |
| 每个写路由都要声明限流 | 6 个写路由各挂一个 scope（`template_pack.*`） |
| `alembic check` 与 ORM 一致 | 只索引实际查询用到的列（`updated_at` 不建索引，与现有表一致） |
| 错误码必须真有判定点 | `TemplatePackConflict` / `TemplatePackStateError` 各有 raise 点 |
| 权限点必须有判定点 | 5 个权限点（**不声明 `:delete`** —— 没有删除接口） |
