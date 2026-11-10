# 资产库 v2 · 详细设计规格

> 状态：**功能清单已定稿**（用户 6 条描述 + 2026-11-09 追加的生命周期与删除规则）；
> 本文把它们翻译成数据模型、接口、权限、状态机、页面与框架装配。
> 总览与 7 条已拍板决策见 `ASSET_LIBRARY_V2_PLAN.md`。
> 本文写完后才进入 M0 骨架；**当前未新建模块、未写业务代码、未改动现役素材库**。
> 标注「我拍板」的条目是授权范围内由我定的实现方式，不阻塞开工；有不同意见随时改。

## 0. 术语纠正：「三级分类」是三个维度 + 一条生命周期轴

用户描述里的三层不是同一个字段的三级枚举，硬做成 `level1/level2/level3` 会让筛选、
共享和权限全部纠缠。拆成三个正交维度：

| 用户说法 | 实际语义 | 建模 | 交互 |
| --- | --- | --- | --- |
| 一级：团队 / 小组 / 个人素材库 | **可见范围**，不是分类 | `visibility` ∈ `personal \| group \| team` + `library_asset_groups`（共享到哪些组） | 一级导航切换「库」 |
| 二级：成片 / 音频 / 图片 | **资产类型** | `kind` ∈ `video \| audio \| image \| pack` | 类型筛选 + 分面统计 |
| 三级：标签（音乐、音效、场景…可新增） | **多对多标签** | `library_tags` + `library_asset_tags` | 标签筛选、批量打标、标签管理 |

**草稿箱与回收站不是第四、第五种可见范围**（2026-11-09 追加需求）。它们是**生命周期视图**，
与可见范围正交：

| 一级入口 | 实际查询 | 为什么不能做成 `visibility` 的额外值 |
| --- | --- | --- |
| 草稿箱 | `state=draft AND owner_id=me` | 草稿**永远只能本人可见**，共享、选择器、团队检索都必须排除它；把它塞进 `visibility` 会出现「草稿共享到团队」这种非法状态 |
| 全部素材 | `state=active` + `library=all`（个人 / 小组 / 团队三支可见集的并集） | 它是「范围之和」不是第四种归属，做成枚举值会让每条素材多存一个永远冗余的状态 |
| 个人 / 小组 / 团队素材库 | `state=active` + 对应可见范围 | 三者才是真正的可见范围 |
| 回收站 | `state=trash AND owner_id=me`（管理员看全量） | 回收站里的东西必须已经**退出所有共享**，否则「删了别人还在用」 |

因此底层是 `state × visibility` 两个维度，界面上排成六个一级入口（`全部素材` 是
2026-11-10 追加的总览页，见 §8）。这个拆分还带来两条
**不可违反的不变式**，服务端每条写路径都要守：

- **不变式 A**：`state = draft` ⇒ `visibility = personal` 且 `library_asset_groups` 无行。
- **不变式 B**：`state = trash` ⇒ `visibility = personal` 且 `library_asset_groups` 无行，
  即**只有完全私有化的资产才可能进回收站**。所以「已共享的素材无法删除」不是一条 if 判断，
  而是数据模型上根本进不去那个状态。

**「封装」不占二级分类的一格**：封装是把若干素材聚合成一个可整体选择/下载的对象，
我拍板把它做成 `kind=pack` 的**资产行**（没有自己的字节），与散素材共用同一套
可见性、共享、标签、回收站、选择器逻辑；成员关系放 `library_pack_items`。
这样第 5 条「选择素材和封装的素材」只需一个接口、一次查询。

**成片与视频的关系**：`成片 = 生成的视频`。用户明确「只能上传音频和图片」，因此：

- 通用上传入口只接受 `image` / `audio`；
- `video` 只能由两条路径产生：① 生成结果入库（`origin=generated`）；
  ② 模板库上传的**原片视频**（`origin=template_source`，单独接口）；
- `origin` ∈ `uploaded | generated | template_source | imported`，与 `kind` 正交，
  详情页据此决定要不要显示「生成参数」区。

## 1. 七条需求逐条落地

| # | 用户描述 | 设计落点 | 我拍板的关键点 |
| --- | --- | --- | --- |
| 1 | 三级分类；成片是生成的视频；只能传音频/图片；视频在模板库上传但存素材库；上传模板只暴露接口、不改前端 | §0 维度拆分；`POST /library/assets/upload` 白名单；`POST /library/source-videos` 专供模板库 | 模板库前端**并存期完全不动**（继续打旧 `/assets/upload`）；切换阶段只改 `archive.api.ts` 里一个 URL 常量，不改任何界面。理由：`/assets/upload` 是前端字面量，若 v2 硬做同名兼容路由会与旧模块撞路径、撞限流策略表、破坏隔离 |
| 2 | 一个上传入口、一个封装入口；单独/批量上传；封装可选已有素材或边传边封 | §4 上传与封装接口；两个按钮 + 批量工具栏 | 封装统一为「先把文件传完拿到 id，再 `POST /library/packs`」，两种来源走同一个建包接口，避免 multipart 与 JSON 混在一个请求里。批量上传 = 前端批次并发单文件接口 + 幂等键，服务端不新增批量上传端点 |
| 3 | 个人素材共享到小组/团队；无直接共享权限需审批；共享只改可见范围不改所有者 | §3 状态机；`library_share_requests`；`owner_id` 永不改写 | **审批资格按申请目标范围拆三个码**（2026-11-10，见 §12 第 21 条）：`library:share:approve` 全站（兼容旧配置）、`:approve:team` 管团队级、`:approve:group` 管「我所在小组」。**组长表不放进 identity 模块**（会动到刚上线、正在使用的三层授权），v2 自建 `library_group_leaders`，且这张表只服务删除窄口、不再发审批票 |
| 4 | 下载范围 = 可见范围；下载/打标签/封装都多选；多下载异步打包、流式传输 | §4 批量写 + §6 下载任务 | 现役是「仅 owner 可下载」（`download_allowed = editable`），v2 **改成可见即可下载**，这是明确的产品变更。批量下载走「创建任务 → 后台打包 zip → `Served` 流式/307 返回」，不在请求线程里压包 |
| 5 | 一个接口让模板库、生图、生成工作台选择权限内素材与封装素材 | `GET /library/selection` + `library-lookup` 注入口 | 服务端按 identity 三层算可见集，前端**不再传 `scope=mine/public`**；返回统一形状 `{id, kind, name, previewUrl, ...}`。模板库替换 2 处调用点；生图页 `/image` 与工作台 `/studio` 目前**没有选择器**，属于新增 UI（不是替换） |
| 6 | 一个接口让三处生成结果进入素材库；详情页除元数据外还要看生成参数 | `POST /library/assets/from-generation` + `library-ingest` 注入口 + `library_asset_generation`；§7 详情页 | 必须**把字节登记进 v2**（不能只存现签 URL，调研确认 `outputUrl` 是临时签名地址）。入库时按 `source_module/source_task_id` 冻结一份参数快照，运行时不跨模块联表查。全项目当前**没有 seed 字段**，v2 自己补一个可选 `seed` |
| 7 | 一级加草稿箱/回收站；AI 生成先入草稿箱、审核后进个人库；删除进回收站并定期清理；普通用户只能删自己的；已共享的不能删；取消共享要审批；团队/小组删除权限收紧 | §0 生命周期视图 + §3.3 状态机 + §3.4 删除权限矩阵 + §3.5 撤销共享审批 + `library.trash.gc` 周期任务 | 用 `state ∈ draft\|active\|trash` 表达，不新增 `visibility` 取值；**已共享即不可删**由不变式 B 保证；撤销共享复用 `library_share_requests` 增加 `action=revoke`；回收站保留 30 天、每日物理清理，有引用则跳过并标 `blocked` |

## 2. 数据模型（新表统一 `library_` 前缀，无外键，UUID 主键）

| 表 | 关键字段 | 作用 / 约束 |
| --- | --- | --- |
| `library_assets` | `id, name, kind, origin, mime_type, size_bytes, sha256, storage_key, cover_key, width, height, duration, has_audio, owner_id, visibility, state, trashed_from_state, trash_reason, trash_expires_at, deleted_at, deleted_by, reviewed_by, reviewed_at, reject_reason, state_version, ref_count, source_key, created_at/updated_at` | 统一资产行；`kind=pack` 时 `storage_key` 为空。`state ∈ draft\|active\|trash`（草稿 / 在库 / 回收站），`trashed_from_state ∈ draft\|active` 决定恢复去向，`trash_reason ∈ deleted\|rejected\|forced\|expired`。`source_key` 幂等（上传重传/生成回写重复调用不产生第二行）。`state_version` 乐观锁，批量写、审核、共享决定都带版本。索引：`(owner_id,state,kind,created_at)`、`(visibility,state,kind,created_at)`、`(state,trash_expires_at)`、`uq(owner_id,sha256,kind) where deleted_at is null` |
| `library_asset_groups` | `asset_id, group_id` | `visibility=group` 时列出可见小组；`team` 不需要行。唯一 `(asset_id,group_id)`。**不变式 B 的落点**：进回收站前必须删空该表 |
| `library_tags` | `id, name, created_by, created_at` | 标签词表，`name` 唯一（大小写不敏感按 DB collation，应用层先归一化）。示例：音乐、音效、场景、室内、室外 |
| `library_asset_tags` | `asset_id, tag_id` | 多对多，唯一约束 |
| `library_pack_items` | `pack_id, asset_id, position, role, description` | 封装成员；`position` 有序，`role` 预留给模板替换的用途（产品主图 / 多视结构图…），首版可不填。唯一 `(pack_id,position)` |
| `library_share_requests` | `id, asset_id, applicant_id, action, target, group_id, status, reviewer_id, note, reason, decided_at, expected_version, created_at` | `action ∈ share\|revoke`（申请共享 / 申请取消共享）；`target ∈ team/group`；`status ∈ pending/approved/rejected/cancelled`。同一资产同一目标同一 action 只许一条 pending（部分唯一索引）。`reason` 存撤销理由，审核人看得到「为什么要收回」 |
| `library_draft_reviews` | `id, asset_id, reviewer_id, decision, reason, snapshot(json), created_at` | 草稿审核轨迹（采纳 / 驳回 + 当时参数快照）。**不复用 `library_share_requests`**：那是可见性流转，这是生命周期流转，混在一张表会让「待我审核」列表语义含混。允许同一草稿多次驳回后重新提交 |
| `library_group_leaders` | `group_id, user_id` | 小组负责人映射，隔离 identity 模块。唯一 `(group_id,user_id)` |
| `library_asset_generation` | `asset_id, source_module, source_task_id, model, model_id, prompt, size, count, duration_sec, aspect_ratio, style_preset, seed, reference_asset_ids(json), pack_id, pack_version, prompt_spec(json), configuration(json), external_id, credits, generated_at` | 1:1 生成溯源快照。**冻结写入**，不跟业务表联查；来源对应 `image_jobs` / `video_tasks` / `replacement_runs` |
| `library_download_jobs` | `id, owner_id, asset_ids(json), status, storage_key, size_bytes, error, expires_at, created_at, finished_at` | 批量打包任务，`status` ∈ `queued/running/succeeded/failed/expired` |

**不建表**的东西：审计走框架 `audit.record`；可见性判定直接读 identity 的 `group_members`
（读的是别模块的表但通过 `library-lookup`/服务层注入，不在 `library` 里 import 业务模块）；
投影 `catalog/library/*.json` 是只读派生文件，不是真相源。

## 3. 可见性、共享与生命周期

### 3.1 读判定（谁能在哪个入口看到）

| 一级入口 | 返回 |
| --- | --- |
| 草稿箱 | `state = draft AND owner_id = me`（管理员可看全量，用于代审） |
| 个人素材库 | `owner_id = me AND state = active AND visibility = personal` |
| 小组素材库 | `state = active AND visibility = group AND group_id ∈ 我所在的小组`（含我共享出去的） |
| 团队素材库 | `state = active AND visibility = team`（所有启用账号） |
| 回收站 | `state = trash AND owner_id = me`（管理员可看全量） |
| 全部（检索/选择器） | 仅 `state = active` 的三库并集 ∪ identity 三层有效权限允许的额外范围。**草稿与回收站永不进入选择器** |

**可见即可下载**：`/file`、批量打包、预览都走同一判定，不再有「看得见但下不了」。
例外只有回收站：`state=trash` 的条目**不提供预览与下载**（只剩恢复 / 彻底删除），
否则「删除」就失去了语义；恢复后立刻可用。草稿箱里本人的条目可预览、可下载。

`owner_id` 在任何共享操作里都不改写，所以「谁上传的」永远可追溯；撤回共享（审批通过后）
只是把 `visibility` 调回 `personal` + 删 `library_asset_groups` 行，资产本身不动。

### 3.2 共享状态机

```
                    ┌─ 持 library:share:direct ─→ 直接写 visibility（无申请单）
发起共享 ───────────┤
                    └─ 否则 ─→ pending ─┬─ approve → 写 visibility + 记 reviewer/decided_at
                                        ├─ reject  → 记 reviewer/note，资产不变
                                        └─ cancel  → 申请人自己撤回
```

- 「共享至团队跟共享至小组一样」：同样走审核流，只是 `target=team`、由持
  `library:share:approve:team` 的人把关（没有「团队负责人」这个概念）。
  小组级由持 `:approve:group` **且是该组成员**的人把关；持旧的全站码
  `library:share:approve` 则两种目标都能审。
- 审核通过是一次事务：校验 `expected_version` → 改 `visibility` → 写/删
  `library_asset_groups` → 更新申请状态 → 写审计。并发重复审核靠版本挡住。
- 申请单列表分两个视角：「我提交的」与「待我审核」，后者按上述三个权限点与
  当前组成员关系过滤，不会让人看到全站申请；`library_group_leaders` 不参与。

### 3.3 生命周期状态机（草稿 / 在库 / 回收站）

```
 生成结果入库（origin=generated）
            │
            ▼
     ┌───────────┐   采纳：本人 / 持 draft:review 代审    ┌──────────┐
     │   draft   │ ───────────────────────────────────→ │  active  │
     │  草稿箱    │                                       │  在库中   │
     └───────────┘                                       └──────────┘
            │ 驳回                                            │ 删除
            │                                        （仅 personal 且本人）
            ▼                                                 ▼
     ┌───────────────────────────────────────────────────────────────┐
     │                          trash 回收站                          │
     │  恢复 → 回到 trashed_from_state，强制 personal，不恢复共享        │
     │  30 天到期 / 手动彻底删 → purged（先过 library-references 校验）  │
     └───────────────────────────────────────────────────────────────┘

 上传（image/audio）、模板库原片（template_source） ──→ active（直接进个人素材库）
```

| 流转 | 触发 | 守卫 |
| --- | --- | --- |
| `— → draft` | `POST /assets/from-generation`、`library-ingest.ingest_generated()` | `origin=generated` 的**唯一**落点。上传与模板原片**不进草稿箱**：文件是用户自己挑的，没有「AI 产物待确认」这层语义；模板原片若进草稿会导致模板库流程断裂 |
| `draft → active` | 本人采纳，或持 `library:draft:review` 的人代审 | 只允许 `owner_id = me` 或 `library:draft:review`；写 `reviewed_by/reviewed_at` + `library_draft_reviews` + 审计 |
| `draft → trash` | 驳回 | `trash_reason = rejected`，`trashed_from_state = draft`（恢复时回到草稿，不是直接进库） |
| `active → trash` | 本人删除 | 要求 `owner_id = me` 且 `visibility = personal`；`trash_reason = deleted`，`trashed_from_state = active` |
| `trash → active` | 恢复 | 只回到 `personal`，**绝不恢复共享**（要重新走一次共享申请）。`library:delete:any` 可恢复他人条目 |
| `trash → purged` | 手动彻底删 / `library.trash.gc` | 先跑 `library-references`，被模板/任务/封装引用则 409 并标 `blocked`，不静默跳过 |

**回收站保留期**：默认 30 天（`LIBRARY_TRASH_RETENTION_DAYS`，M1 落到 `.env.example`）。
入库时写死 `trash_expires_at = now + 保留期`，前端显示「剩余 N 天」。
`library.trash.gc` **每日 03:00** 跑一次，物理删除过期条目与字节、清投影、写审计汇总；
有引用的跳过并在 doctor 里计数（不阻断，避免一条被引用的资产让整个 GC 停摆）。

### 3.4 删除权限矩阵（「团队和小组的删除权限要收紧」的落地）

「删除」在本设计里分两个动作，权限完全不同，接口也不共用：

- **移入回收站**（软删，可恢复）：`DELETE /assets/{id}`
- **彻底删除**（物理，不可恢复）：`DELETE /trash/{id}`

| 资产所在 | 谁能移入回收站 | 谁能彻底删除 | 说明 |
| --- | --- | --- | --- |
| 个人库 · 本人所有 · `visibility=personal` | 本人（`library:delete`） | 本人（在回收站内） | 普通用户能删的**唯一**情形 |
| 个人库 · 他人所有 | ✗ 404（不可见） | ✗ | 连「是否存在」都不该暴露 |
| 小组库 · 本人所有 | ✗ 409 `SHARED_ASSET_NOT_DELETABLE`（**命中窄口则一步撤销+删除**，见 3.4.1） | ✗ | 必须先在小组里撤销共享并通过审批；窄口是唯一例外 |
| 小组库 · 他人所有 | ✗ 403 | ✗ | 小组负责人也不行 —— 负责人只能「移出小组」，见下 |
| 团队库 · 任何人所有 | ✗ 409 `TEAM_DELETE_FORBIDDEN` | ✗ | **收紧点**：团队库资产一律不可删，包括上传者本人；只能撤销团队共享后再删 |
| 任意 · 管理员（`library:delete:any`） | ✓（同一事务内自动补一条 `action=revoke`、`status=approved`、`reviewer=自己` 的申请单，再进回收站） | ✓ | 兜底通道，必须带 `reason`，写审计；不提供批量彻底删除。**共享撤销「必须留一条通过审批的记录」这条不变，超管只是自审** |

三条「收紧」的额外规则：

1. **小组负责人的权力边界**：可以「移出小组」（等价于对该组发起撤销共享，直接生效并记审计），
   但**不能删除别人的资产**，也不能移出别人的个人库。移出后资产回 `personal`，归 owner。
2. **团队库没有「负责人」概念**，所以团队共享的撤销只能由申请人本人发起 +
   `library:share:approve:team`（或全站码 `library:share:approve`）审核，或超管直接处理。
3. **批量删除逐条判定，不做整体回滚**：一批 10 条里 3 条已共享 → 返回 7 成功、3 条
   `SHARED_ASSET_NOT_DELETABLE` 明细，前端按条标红。整体回滚会让人误以为「一条都没删」。

#### 3.4.1 窄口规则：组长删「自己上传、只共享给本组」的素材（一步）

这是「已共享不可删」的唯一例外。组长把自己的素材共享给自建的小组后想删，按常规要他
先发撤销申请、再自己批（自审）、再回来删 —— 三个动作、两次事务。窄口把它压成**一次
事务、一个显式动作**，但「撤共享必须留审批记录」这条不变式不破。

**命中条件（四个全部满足，缺一即回落常规流程）**：

```
owner_id            = me
visibility          = group
共享行集合           = { 本组 }        —— library_asset_groups 恰好 1 行且 group_id = 我的组
                                        （没有 team 共享、没有其他组的行）
我在该组的身份        = leader         —— 取自 identity 的组成员关系，不看权限码
state               = active          —— 草稿/回收站不走这条
```

**动作**：`DELETE /assets/{id}` 携带 `?revokeOwnGroupShare=true`，服务层在同一事务内：

1. 插一条申请单 `library_share_requests`：`action=revoke`、`target=group`、
   `target_id=本组`、`status=approved`、`reviewer_id=me`、`reason` 取请求体 `reason`（必填）、
   `note` 标记 `self_leader_combined`；
2. 删 `library_asset_groups` 行、`visibility=personal`（申请单留着，就是「撤销存在过」的证据）；
3. 资产转 `state=trash`、`trash_reason=deleted`、`trashed_from_state=active`、写 `trash_expires_at`；
4. 审计写一条 `library.asset.revoke_and_delete`，payload 带申请单 id，可与申请单互查。

**为什么不默认就一步**：不传 `revokeOwnGroupShare` 时，接口仍返回 409
`SHARED_ASSET_NOT_DELETABLE`，并带 `canRevokeAndDelete: true`。前端据此把按钮换成
「撤销共享并删除」并弹二次确认 —— 显式意图，避免有人只想删、结果顺手把组里同事正在用的
素材撤了。判定与执行共用同一个 `_can_revoke_and_delete()`，前端不自己算条件。

**边界**：

- 共享给本组**和**别的组 / 团队 → 不命中，走常规撤销审批（因为会影响本组之外的同事）。
- 组长不是 owner → 不命中；这种情况走「移出小组」（3.4.1 之外，见收紧规则 1）。
- 超管走 `library:delete:any` 兜底，不受此窄口约束，同样补一条自审申请单。
- 批量删除里也按这条逐条判定，`action=delete` + `payload.revokeOwnGroupShare=true`。

### 3.5 取消共享：也要审批

用户明确要求「取消共享需要经过审批才能取消，取消后才能删除」。做法是把撤销**复用**
同一张申请单表，只加一个 `action=revoke`，审核人判定与共享申请完全一致：

```
owner 发起撤销 ─→ pending(action=revoke, reason) ─┬─ approve → 事务：删 library_asset_groups 行
                                                  │             / visibility=personal
                                                  │             → 审计 → 申请人现在可以删了
                                                  ├─ reject  → 共享保持原样，记 reviewer/note
                                                  └─ cancel  → 申请人自己撤回申请
```

- **不区分申请人有没有 `library:share:direct`**：能「直接共享」不等于能「直接收回」，
  因为收回会让正在用这个素材的同事突然拿不到文件，风险方向相反，所以一律审批。
- **自己就是审核人时自审**：超管 / 对**该申请的目标范围**持有对应审批权的人
  申请撤销自己的资产，系统直接 `approved by self` 并写审计（否则会出现「自己申请、
  等自己批」的死锁）。只有 `:approve:group` 的人撤销自己共享到团队的资产**不自审**——
  团队级把关不能由小组审批权替代。
- **部分撤销**：资产共享到 3 个小组时，撤销按 `group_id` 逐组进行；只撤 1 个组则
  `visibility` 仍是 `group`，其余两组不受影响；撤到最后一个组才回 `personal`。
  `target=team` 只能整撤。
- **撤销期间的并发**：pending 期间共享仍然有效（同事还能下载），但删除请求一律
  409 `SHARE_REVOKE_PENDING`，提示「撤销审批中」。审批通过后再删即可。
- **撤销被拒后**：资产保持共享，申请人可再次发起（需新理由），不限制次数但要留痕。
- **唯一例外是 3.4.1 的窄口**：`owner=me ∧ 只共享给本组 ∧ 我是组长` 时撤销与删除合并为
  一个动作，但仍先落一条 `status=approved` 的自审申请单再删共享行，审计与申请单一一对应。
  其它任何情况（多组共享、含 team、非 owner、非组长）一律走完整审批。

## 4. 接口清单（统一前缀 `/api/v1/library`）

| 组 | 方法 / 路径 | 权限 | 要点 |
| --- | --- | --- | --- |
| 浏览 | `GET /assets` | `library:read` | `library=team\|group\|personal\|all`、`state=draft\|active\|trash`（默认 `active`）、`kind`、`tagIds`、`keyword`、排序、`page/pageSize`；返回分面统计。`state=draft/trash` 时可见集强制收窄到本人（管理员可传 `mine=false` 看全量） |
| 浏览 | `GET /assets/{id}` | `library:read` | 详情含规格、标签、可见性、引用、**生成参数**（有才返回） |
| 浏览 | `GET /assets/{id}/file` `/preview` `/stream` `/thumbnail` | `library:read` | 复用 `core.serving.Served`；本地 `FileResponse`（支持 Range）/ 远端 307 |
| 上传 | `POST /assets/upload` | `library:create` | 单文件 octet-stream 流式；`kind` 只允许 `image/audio`；`sourceKey` 幂等 + sha256 秒传；挂 `rate_limit` |
| 上传 | `POST /source-videos` | `library:create` | **专供模板库**，只收 `video`，`origin=template_source`；等价于旧 `/assets/upload`，不改界面 |
| 封装 | `POST /packs` | `library:create` | `{name, tagIds, assetIds[], coverAssetId, items[role,description]}`；已有素材与「刚上传的一批」都走这里 |
| 封装 | `GET/PATCH/DELETE /packs/{id}` | `library:read` / `library:update` / `library:delete` | 改成员、改名、解散（成员资产不受影响） |
| 标签 | `GET /tags`、`POST /tags`、`PATCH/DELETE /tags/{id}` | `library:read` / `library:create` / `library:tag:manage` | 新增标签对有 `library:create` 的人开放；改名/合并/删除需 `library:tag:manage` |
| 批量 | `POST /assets/bulk` | 按 action | `{ids[], action, payload, expectedVersions?}`；action = `addTag/removeTag/rename/share/requestRevoke/delete/restore/purge/approveDraft/rejectDraft/pack`；**逐条判定**，返回逐条结果，部分失败不整体回滚 |
| 单条 | `PATCH /assets/{id}` | `library:update` | 改名、标签、可见性（直接共享时）；`state=draft` 时也可改 |
| 草稿 | `POST /drafts/{id}/approve`、`POST /drafts/{id}/reject` | `library:create`（本人）/ `library:draft:review`（他人） | 采纳 → `active`；驳回 → `trash(reason=rejected)` + `reject_reason`；写 `library_draft_reviews` |
| 生命周期 | `DELETE /assets/{id}?revokeOwnGroupShare=` | `library:delete` | **软删**，仅 `owner=me ∧ visibility=personal`；已共享 409 `SHARED_ASSET_NOT_DELETABLE`，响应带 `canRevokeAndDelete`；命中 3.4.1 窄口并显式传 `revokeOwnGroupShare=true` 时，同事务内自审撤销 + 软删 |
| 回收站 | `POST /trash/{id}/restore`、`DELETE /trash/{id}` | `library:delete`（本人）/ `library:delete:any` | restore 回 `trashed_from_state` 且强制 `personal`；purge 前 `library-references` 校验，被引用 409 |
| 共享 | `POST /share-requests`（`action=share\|revoke`）、`GET /share-requests`、`POST /share-requests/{id}/approve\|reject\|cancel` | `library:share` / 审批三码（全站 `library:share:approve`、团队 `:approve:team`、所在小组 `:approve:group`） | 见 §3.2 与 §3.5；`action=revoke` 必须带 `reason`。**审批路由只要求 `library:read`**，资格按申请目标范围在服务层判；`PUT /groups/{id}/leaders` 仍由全站码守护 |
| 下载 | `POST /downloads`、`GET /downloads/{id}`、`GET /downloads/{id}/file` | `library:read` | 见 §6 |
| 选择器 | `GET /selection` | `library:read` | 统一返回 `asset` + `pack`；`kind/tagIds/keyword/includePacks/page`；服务端算可见集，**硬过滤 `state=active`**（草稿与回收站永远不出现在选择器里） |
| 生成入库 | `POST /assets/from-generation` | `library:generation:ingest` | 见 §7；供 image/template/task 模块经注入口调用 |

**CLI**（`ModuleSpec.cli`）：`app import-library`（幂等导入现役 `assets` + S3 `yufan-archive`）、
`app verify-library`（对账）、`app rebuild-library-mirrors`。不是 HTTP 接口。

## 5. 注入口与框架装配

业务模块之间不许互相 import，所以第 5/6 条的后端侧不能让 template/image 直接调 `library`：

| 注入口 | 方向 | 协议要点 | 未注册时 |
| --- | --- | --- | --- |
| `library-lookup` | 读 | `get_many(session, ids) -> dict[id, StoredMedia]`；`resolve_visible(session, user, query)` 给选择器/prepare 用 | **fail-closed 503**（与现役 `asset-lookup` 同口径） |
| `library-ingest` | 写 | `ingest_generated(session, source_module, source_task_id, bytes_or_key, generation_snapshot) -> asset_id`，幂等键 `sourceKey`；**落库即 `state=draft`**（草稿箱），不直接进个人库 | **fail-closed 503**，生成成功但入库失败要能重试，不能静默丢 |
| `library-references` | 删前校验 | `references(asset_ids) -> Mapping[id, 人可读描述[]]`，聚合 `pack_items` / 任务引用 / 模板引用 | **fail-closed 503**，拒绝彻底删除 |

三者都用 `core.ports.host.Port`（`multi=False` 单例），各自导出**有且只有一个**
`register_*` + `reset_*` + `has_*`（三件套），并登记 `api/ports.py::PORT_CATALOG`
（漏登记 `test_every_port_is_catalogued` 红，形状不全 `test_port_modules_expose_the_full_shape` 红）。

**M2 实施时补了第四个（设计里原先没定的那个洞）**：`org-directory`（`core/ports/
org_directory.py`，由 `modules/identity` 注册）。小组素材库要问「我属于哪些组」
「这个组叫什么」「这个人是哪位」，而答案在身份模块的表里。三条约束决定了必须做成
注入口：不许跨模块 import、可见性判定必须与列表查询同一个事务快照、未接上时要能
退化成「不在任何小组」（少看见，不多看见）。它返回**中性值**而不是 fail-closed——
小组是可选能力，缺它就 503 会让「删掉身份模块」等于「资产库起不来」。

其它装配清单：

- **权限点**（`ModuleSpec.permissions`，全部新前缀 `library:*`，不进 `BUILTIN_ROLES`）：
  `read / create / update / delete / delete:any / draft:review / share / share:direct /
  share:approve / share:approve:team / share:approve:group / tag:manage /
  generation:ingest`，共 **13 个**。`delete` 与 `delete:any`
  必须分开：前者是「删自己的」，服务层仍要判 `owner_id`；后者是管理员兜底通道
  （删他人的、团队库的、彻底删除）。批量下载不单列权限（可见即可下载，§4 决策）。
  `share:approve*` 三码见 §12 第 21 条：旧码全站，两个新码按申请目标范围拆。

  **这 13 个是目标形态，不是一次性声明**：`tests/test_permission_coverage.py` 要求
  每个声明的码都有 `require(...)` / `actor.has(...)` 判定点，否则角色界面会出现
  「勾了没用」的装饰品。所以按批进入 `ModuleSpec`：M1 只声明已接线的
  `read` / `create`，M2 加 `update / delete / delete:any / draft:review / share /
  share:direct / share:approve / tag:manage`，M3 加 `generation:ingest`，
  2026-11-10 加 `share:approve:team` / `share:approve:group`。
  **两个新码同样不进 `BUILTIN_ROLES`**：审批权是收窄用的，不能让启动钩子
  自动发给 editor / viewer。
- **错误码**：`ModuleErrors("library", slot=8)`（调研确认 slot 8 全空，81 个码值可用，
  与旧 asset 的 slot 5 完全隔离）。编码 `status*100 + 8*10 + seq`。
  本次新增需求必须有的语义（`seq` 在 M0 建表时按 `ModuleErrors` 实际占用排号，
  同一状态码每模块只有 9 个槽位，排号时先看 `api/docs/API.md` 的既有清单）：

  | 语义 | HTTP | 触发 |
  | --- | --- | --- |
  | `SHARED_ASSET_NOT_DELETABLE` | 409 | 资产仍共享到小组/团队就调删除 |
  | `TEAM_DELETE_FORBIDDEN` | 409 | 删除团队库资产（含本人上传） |
  | `SHARE_REVOKE_PENDING` | 409 | 撤销审批未通过就想删 |
  | `REVOKE_REASON_REQUIRED` | 400 | `action=revoke` 没给理由 |
  | `DRAFT_REVIEW_FORBIDDEN` | 403 | 审他人草稿但无 `draft:review` |
  | `DRAFT_NOT_REVIEWABLE` | 409 | 草稿已被处理（并发重复采纳/驳回） |
  | `ASSET_NOT_IN_TRASH` | 409 | 对非回收站条目调 restore/purge |
  | `TRASH_ENTRY_REFERENCED` | 409 | 彻底删除但被模板/任务/封装引用 |

  `ASSET_NOT_IN_TRASH` 与 `TRASH_ENTRY_REFERENCED` 也可以合成一个 409，但分开更利于前端
  给出不同提示（「不在回收站」vs「还有人用着」），按分开实现。
- **装配物**：`jobs=("library.download.build",)`、
  `periodic=("library.download.gc","library.trash.gc")`，
  必须在 `ModuleSpec` 声明，否则 `assembly_gaps` 让启动直接失败。
  `library.gc` / `library.mirror`（孤儿 blob、投影对账）随投影一起在 M4/M5 落地，
  **没实现就不要先声明**：`create_app()` 会核对「声明了必须注册」，声明了没注册
  直接拒绝启动（见 §10.2）。
  `library.trash.gc` 是小时级扫描（调度器没有 cron）：删过期回收站条目
  （先过引用校验）、写汇总审计。
- **限流**：上传类接口**必须挂** `rate_limit`（`test_policy_covers_money_endpoints` 禁止豁免）；
  其余新增写路由在 `EXEMPT_WRITE_ROUTES` 登记 `why` + `review_by`，或同样挂限流。
- **引用守卫**：`library_pack_items` 等写 `asset_id` 的位置要声明
  `ModuleSpec.asset_reference_sources` 或新注入口对应的静态守卫，避免漏了不报错。
- **生成物**：`app export-openapi` 四份（`openapi.json` / `API.md` / `schema.ts` /
  `permissions.ts`）+ `npm run gen:routes` → `docs/ROUTES.md` +
  `npm run gen:mock-coverage` → `docs/MOCK_COVERAGE.md`。
- **前端路径守卫**：`endpoints.test.ts` 要求 URL 写成字面量，不能运行时拼模板串。
- **mock**：`modules/library-v2/mock.ts` 的正则必须精确锚定 `^/library...$`；模块级 mock
  字典序排在骨架 mock 之前，正则过宽会抢走旧 `/assets` 页请求。
- **doctor**：新增 `library.storage` / `library.downloads` / `library.share_requests` /
  `library.trash` / `library.drafts` 自检项；回收站里「已过期但被引用阻塞」的条目单独计数。

## 6. 批量下载：异步打包 + 流式传输

```
POST /downloads {ids[]}  → 202 {id, status:"queued"}
   ↓ job "library.download.build"（@register_handler，ModuleSpec.jobs 声明）
后台逐条取字节（BlobStore.materialize / 远端预签名下载）→ 写 zip 到独立前缀 library/downloads/
   ↓ 成功写 storage_key + size_bytes + status=succeeded
GET /downloads/{id}   → 轮询状态（前端 React Query 条件轮询，页面隐藏时停止）
GET /downloads/{id}/file → Served.local(FileResponse，支持 Range) / Served.remote(307)
   ↓ periodic "library.download.gc" 清理过期 zip 与 expired 任务
```

约束（我拍板）：单次最多 100 项、解压后总量上限 2 GB（超限 413）、zip 内文件名用
`序号_原名` 去重、`pack` 展开成成员、任务 24h 过期、`failed` 保留 `error` 供重试。
**只接受 `state=active` 的条目**；草稿与回收站里的 id 不进包，逐条返回可读原因
（「草稿未入库」/「回收站内容不可下载」），不整单失败。
**不在请求内 `zipfile` 压包**：调研确认全仓没有 `StreamingResponse`/`zipfile` 实现，
请求线程压几十上百 MB 会拖住整个 worker；异步 job 与项目现有任务模型一致。

## 7. 生成溯源与详情页

### 7.1 入库契约

`POST /assets/from-generation`（也经 `library-ingest` 注入口供后端模块调用）入参：

```text
sourceModule     image | template | task        （生成来自哪个模块）
sourceTaskId     业务侧任务/作业/运行 id
sourceKey        幂等键 = sourceModule + ":" + sourceTaskId [+ ":" + index]
kind             video | image                 （生成结果的类型）
bytes / storageKey / sourceUrl               （三选一，服务端必须物化字节，不能只存 URL）
generation       { model, modelId, prompt, size, count, durationSec, aspectRatio,
                   stylePreset, seed, referenceAssetIds[], packId, packVersion,
                   promptSpec, configuration, externalId, credits, generatedAt }
name / tagIds / visibility                    （可选；visibility 必须是 personal，
                                               state 固定落 draft，共享一律由本人后续发起）
```

参数来源对照（调研确认）：`image_jobs` 给 `model/model_id/prompt/size/count`；
`video_tasks` 给 `duration_sec/aspect_ratio/style_preset/reference_asset_ids`；
`replacement_runs` 给 `prompt_spec/configuration/pack_id/pack_version/task_id/credits`。
**全项目当前没有 seed**，v2 自己留可空字段，供应商返回了就记，没有就显示「未记录」。

### 7.2 详情页信息架构（用户要求「好好设计」）

从上到下六个区，前四区常显、后两区折叠：

1. **媒体区**：图片/音频/视频预览（`video` 用兼容派生流）、原件下载、封装展开的成员缩略图。
2. **基础信息**：名称、类型（成片/音频/图片/封装）、来源（上传/生成/模板原片/导入）、
   **状态徽标**（草稿 / 在库 / 回收站；回收站额外显示「剩余 N 天」）、所有者、
   所属库（个人/小组/团队 + 具体小组名）、创建时间、标签（可就地编辑，回收站内禁用）。
   草稿状态额外显示「待审 / 已驳回 + 驳回原因」，并在顶部给出「入库 / 驳回」主按钮。
3. **媒体规格**：格式、体积、尺寸、时长、音轨；**未知值显示「未知」，不显示 0×0 / 0 B**。
4. **生成参数**（仅 `origin=generated`）：按来源分组的键值表 + 「最终提示词」全文 +
   「输入快照」（参考素材、封装版本、替换范围）+ 上游任务号与跳转链接；折叠的
   `promptSpec` / `configuration` 原始 JSON 放「查看完整参数」里，给排查用。
5. **使用与引用**：哪些模板/任务/封装用过它（决定能不能彻底删除）、可见性变更历史
   （含每次共享与撤销的申请人 / 审核人 / 时间 / 理由）。这一区是「为什么删不掉」的唯一解释来源，
   回收站里也要能看。
6. **归档信息**（仅本人）：存储键、SHA-256、幂等键、投影状态。

`pack` 类型详情页不显示 3/4 区，改显示「封装成员」列表（可跳转各成员详情）。

## 8. 前端形态

- 新模块 `modules/library-v2`，路由 `/library`，`permissions: ['library:read']`
  —— 注册表骨架已支持「一行权限同时管 URL 守卫 / 侧栏 / 宫格」，D6「仅管理员可见」
  就是只把这个权限点授给超管/开发角色，不引入 feature flag。
- 页面顶部**六个一级入口**（2026-11-10 按用户要求重排）：`全部素材` / `个人` /
  `小组` / `团队` / `草稿箱` / `回收站`，顺序、角标字段与 `state`/`library` 映射
  集中在 `VIEWS` / `viewQuery()` 一处定义。`全部素材` 是默认落地页，查
  `state=active&library=all`——本人未共享的 + 共享到我所在小组的 + 团队的，
  三支取并集、同一素材只出现一次；装进可见封装的成员不单列（与列表同口径）。
  **角标 `all` 可能小于三个范围角标之和**：跨入口可见的封装，其成员会在各自
  范围内出现、却在全量口径里被封装收起来，这不是漏数据。
  草稿箱与回收站虽是另一个维度，界面上不做解释，只放在末尾并带计数徽章。
  URL 统一 `/library?view=all|personal|group|team|drafts|trash`，旧的
  `?view=personal` 链接照常可用，刷新与分享可还原。
- 每个入口内部：二级类型筛选（成片 / 音频 / 图片 / 封装）→ 关键词 / 排序 / 分页，
  筛选条件写进 URL。标签筛选是**工具栏下方一整条常显的标签区**，不是下拉框：
  摊开全部标签、每个标签旁带「当前入口的素材数」，按标签添加时间先后排列，
  点一下切换筛选（可多选）、「全部」清空。卡片上则摊开该素材的全部标签，
  每个标签带 × 可就地去掉、行末一个 + 打开选择器就地添加（可一次多选、可新建词），
  两者共用 `PATCH /assets/{id}` 的 `tagIds` 整体替换；没有标签时 + 也要在，
  否则第一条素材无从打标。封装的标签自动继承**所有成员**的标签（见 §12 第 19 条）。
- 两个主按钮：**上传**（单个与批量，弹窗内可多选文件、选类型与标签、逐条进度）与
  **封装**（弹窗内「选择已有素材」+「上传新素材」两个来源，选完填名称与标签，提交）。
  这两个按钮只在 `全部素材` 与 `个人` 入口出现：上传写的是个人库、封装选择器一直是
  全量可见集，都与「当前在看哪个入口」无关；草稿箱与回收站没有上传/封装入口。
- **删除按钮的显示规则**（对应 §3.4，前端只做「少看到不该点的」）：
  - `个人` 入口：自己的资产显示「移入回收站」；他人资产在这个入口根本看不到。
  - `小组` / `团队` 入口：**不显示删除按钮**，只显示「取消共享」（走审批）。
    卡片上有共享徽标时给一句 `title`：「需先取消共享并通过审批才能删除」。
  - `回收站` 入口：显示「恢复」与「彻底删除」，彻底删除二次确认并提示不可恢复。
  - 管理员（`library:delete:any`）额外看到「强制移入回收站」，必须填理由。
- `草稿箱` 入口：卡片显示生成来源与参数摘要，操作是「入库」「驳回」「查看详情」；
  顶部有「全选 / 批量入库」，批量入库前弹确认（列出将采纳的条数与总体积）。
  驳回要求填原因（记 `reject_reason`，进回收站后仍可见）。
- `回收站` 入口：卡片显示「剩余 N 天」，过期条目灰色且不可恢复；顶部提示保留期。
- 列表每张卡片带 checkbox；选中后浮出批量工具栏，动作随入口变化：
  `个人` = 下载 / 打标签 / 封装 / 移入回收站；`草稿箱` = 批量入库 / 批量驳回；
  `回收站` = 批量恢复 / 批量彻底删除（后者需二次确认）。
  现役素材库没有多选/批量工具栏，这是全新组件（`shared/ui` 里新增列表多选能力）。
- 共享：卡片操作「共享到…」→ 选小组或团队 → 有 `library:share:direct` 立即生效，
  否则提示「已提交审核」；已共享的卡片操作变成「取消共享…」→ 填理由 → 提交审批；
  「待我审核」入口在页面右上，两类申请（共享 / 撤销）分两个 Tab 展示。
- 选择器组件 `LibraryPicker`（供模板库 / 生图 / 工作台复用）打 `GET /selection`；
  模板库替换 2 处调用点，生图页与工作台各新增一个「选择参考素材」入口。

## 9. 隔离清单（并存期必须全部成立）

| 项 | v2 取值 |
| --- | --- |
| 后端模块 / 表 / 路由 / 权限 | `modules/library` / `library_*` / `/api/v1/library` / `library:*` |
| 前端模块 / 路由 | `modules/library-v2` / `/library` |
| 字节与派生前缀 | `blobs/library/`、`previews/library/`、`clips/library/`、`library/downloads/` |
| 投影前缀 | `catalog/library/*.json`（只读、可重建、不参与写入判定） |
| GC / mirror | `library.gc` / `library.mirror` / `library.trash.gc` / `library.download.gc`，只扫自己的前缀，不碰旧库孤儿 |
| 注入口 | `library-lookup` / `library-ingest` / `library-references`，与 `asset-lookup` 并存不冲突 |
| 任务 / CLI / doctor | `library.*`、`import-library` 等、`library.*` 自检项 |
| 缓存 key | `['library-*', ...]`，不与 `archive-assets` / `packs` 串味 |
| seed | **不写任何 seed 钩子、不造演示数据**（用户明确「不要 seed 了，全部都不要了」） |

## 10. 批次（在 PLAN 的 M0–M4 上细化）

| 批次 | 交付 | 闸门 |
| --- | --- | --- |
| M0 | 模块骨架：ModuleSpec 全钩子、11 个权限点、slot=8 errors、三个注入口 + PORT_CATALOG、空路由、前端占位页与精确前缀 mock、四份生成物 + ROUTES/MOCK_COVERAGE | 启动对账 0 gap；`/assets` 与 `/library` 同时在线互不影响；架构/装配守卫全绿 |
| M1 | 10 张表 + 双向迁移、上传/秒传/幂等、`source-videos`、导入 CLI（dry-run + 对账报告） | 迁移双向、导入幂等、两库 blob 前缀互不误删 |
| M2 ✅ | 可见性与三层判定、共享审核 + **撤销共享审批**、**删除权限矩阵**、**草稿/回收站状态机与 `library.trash.gc`**、标签、批量写、选择器接口、`library-lookup`、`library-references` | 越权/越级守卫、审核并发版本、**不变式 A/B 用例**（草稿与回收站必须 personal）、引用 fail-closed |
| M3 ✅ | 封装、批量下载 job + 流式、生成入库 + 溯源快照、详情页六区 | 100 项/2GB 边界、失败重试、参数快照冻结 |
| M4 ✅ | 前端页面全套 + `LibraryPicker` + mock 全端点 | `npm run verify`、`gen:mock-coverage` |
| M5 ▶ | 入口切换 + 并行观察（**不下线旧模块**）、旧库数据追平对账 | `app library reconcile` 无缺口；两个入口同时可用 |

### 10.1 M2 落地记录（与设计的差异，都是实施时才暴露的）

| 项 | 设计原本 | 落地做法 | 原因 |
| --- | --- | --- | --- |
| 组归属从哪来 | 只说「取自 identity 的组成员关系」 | 新增注入口 `org-directory`（组 id / 组名 / 用户显示名） | 不许跨模块 import；且可见性判定要和列表查询同一个会话快照 |
| 组长映射谁写 | 只说表 `library_group_leaders` 放在 v2 | `PUT /library/groups/{groupId}/leaders`（`library:share:approve`，整体替换） | 没有写入入口时窄口与「待我审核」永远空着 |
| `library.trash.gc` 时刻 | 「每日 03:00」 | `every=3600` 小时级扫描，`leader_only=True`，配置默认**关闭** | 调度器只有间隔、没有 cron；保留期 30 天，早一小时无差别，漏跑一天差别很大 |
| 审批路由的权限 | 「`library:share` / `library:share:approve`」 | `POST /share-requests/{id}/approve\|reject` 只要求 `library:read`，判定在服务层 | 组长不一定持有 `share:approve`（那是管理员级码），照抄 `require` 会让组长点不动 |
| 批量动作集合 | 列出 `addTag/…/purge/pack` | 不含 `purge`（不可逆）与 `pack`（M3 才有） | 十个勾选一次不可逆删除太危险；`pack` 现在没有实现可调 |
| 错误码 | 8 个语义 | 再加 `SHARE_TARGET_INVALID`(409-7)、`REQUEST_NOT_PENDING`(409-8) | 目标与状态不符、并发重复审批都要有独立文案；「已在回收站」用通用 409，把最后一个槽位留给将来 |
| `PATCH /assets/{id}` | 「改名、标签、可见性（直接共享时）」 | **不含可见性** | 可见性变更一律走申请单，否则「`share:direct` 可以顺手改可见性」会绕开撤销审批 |

> **2026-11-10 注**：上表「组长映射谁写」「审批路由的权限」两行里关于
> 「组长 / 待我审核」的口径已被 §12 第 21 条取代——审批资格改由三个审批权限点
> 按申请目标判定，组长表只服务删除窄口。这里保留 M2 当时的落地记录，不是现行约束。

### 10.2 M3 落地记录（同样都是实施时才暴露的）

| 项 | 设计原本 | 落地做法 | 原因 |
| --- | --- | --- | --- |
| 入库归属人 | 入库协议里只写了来源模块 / 任务号 / 幂等键 | `IngestRequest` 增加 `owner_id`，空则退回当前请求主体，两者都没有就 400 | worker 里没有请求上下文；归属是权限的基础，不能猜一个默认账号 |
| 内容相同的第二次生成 | 只用 `sourceKey` 幂等 | 再加一条「同 owner + 同 sha256 + 同 kind 且未删除 → 返回原行」 | 建表时就有 `uq_library_assets_owner_sha_kind` 唯一索引，不按它判就会在第二次生成同样字节时撞 500 |
| 批量下载的跳过去向 | 「逐条返回可读原因」 | 原因只在 `POST /downloads` 的响应里返回，落库只存可打包的 id | 落库要存解释就得加一列或一张明细表，而它只是「发起那一刻的选择结果」；轮询状态不该重复这段解释 |
| 打包压缩方式 | 未指定 | `ZIP_STORED`（不 deflate） | 图片 / 视频 / 音频本身已压缩，deflate 只省几个百分点，却要在 2GB 上白烧 CPU |
| 周期任务清单 | `library.download.gc` + `library.gc` + `library.mirror` 四个 | 只落地 `library.trash.gc` + `library.download.gc` | 投影（`catalog/library/*.json`）与孤儿 blob 清理还没实现，先声明未注册的任务会让 `create_app()` 直接拒绝启动 |
| 封装成员的上限 | 未写 | 单封装 100 项（`PackIn/items` 的 `max_length`），成员不能是另一个封装 | 嵌套会让「下载解包」变成递归、「这个封装多大」变成图遍历；需求里没有这一条 |
| 批量写里的 `pack` | 设计列在 action 里 | 在 `run()` 里**分流**，整批建一个封装，返回一条结果（`id` 是新封装） | 别的 action 是逐条改，`pack` 是「用这批建一个东西」；照抄逐条循环会建出 N 个单成员封装 |
| 打包任务的归属 | 未写 | 只有 owner（或 `library:delete:any`）能看能取，其余一律 404 | 既不说「存在但没权限」，也不泄露「这个人下过什么」 |
| 彻底删除封装 | 未写 | `_purge_row` 同时清 `pack_items.asset_id` 与 `pack_items.pack_id` | 成员关系挂在 `pack_id` 上，漏了会留下孤儿行；而引用检查只按 `asset_id` 查，谁也不会发现 |

### 10.3 M4 落地记录

| 项 | 设计原本 | 落地做法 | 原因 |
| --- | --- | --- | --- |
| `LibraryPicker` 接入点 | 「模板库 2 处 + 生图 + 工作台」 | 工作台与建任务弹窗两处（都用 `referenceAssetIds`） | 生图接口 `GenerateImageIn` 没有参考素材字段；模板替换读的是旧 `archive` 资产。硬接等于让前端传一个后端不认的 id，等 M5 切换时再一起改 |
| 六个入口的筛选维度 | 二级类型 → 三级标签 → 关键词 / 排序 / 分页 | 全部同名参数压进 URL（`view`/`kind`/`tags`/`q`/`sort`/`page`） | 刷新、分享、前进后退都要能还原；页面里不存第二份筛选状态 |
| 批量彻底删除 | 「回收站 = 批量恢复 / 批量彻底删除（二次确认）」 | 回收站只给批量恢复，彻底删除逐条确认 | 与后端一致：`BulkAction` 刻意不含 `purge` |
| 撤销共享的入口 | 卡片「取消共享…」→ 填理由 | 同左，且详情页底部同一入口 | 撤销一律走申请单（后端强制），界面明写「审批通过后才生效」 |
| 窄口的界面呈现 | 只说返回 409 | 删除撞 409 且 `details.canRevokeAndDelete` 为真时，直接弹「撤销共享并删除」（理由必填） | 后端已判定，前端再算一遍条件迟早与后端漂移；`ApiError` 因此新增 `details` 透传 |
| mock 的数据量 | 「mock 全端点」 | 9 条资产 + 1 个封装 + 3 标签 + 2 小组 + 2 张申请单，写操作真的改内存 | 空 mock 只能验证「不报错」，看不出可见性 / 状态机是否对；这不是后端 seed，`VITE_USE_MOCK=false` 时整块不进产物 |
| M0 骨架 mock 的包装 | 处理器里 `return ok(...)` | 全部改成返回裸数据 | 适配器已经统一包一层 `{code,message,data}`，处理器再包一次会让列表拿到 `data.data`，页面表现为「永远空」 |
| 卡片上的删除按钮 | 「团队库任何人不可删」 | 本人共享中的资产显示「取消共享」；管理员显示「强制移入」，理由必填 | 前端只做「少看到不该点的」，真正的矩阵在后端；两边措辞一致 |
| 标签的创建入口 | 只写了「上传弹窗内选类型与标签」 | 三处打标签的下拉（上传 / 批量栏 / 详情）都支持**就地新建**（`MultiSelect.onCreate`）；没有 `library:create` 的人不出现这一行 | M4 只做了「从已有词表里挑」，词表为空时界面上建不了第一个标签，四处全是「还没有标签」的死路。新建走 `library:create`（对上传者开放），改名 / 删除词表仍要 `tag:manage` |

### 10.4 M5 落地记录：切换入口，但不下线旧库

设计原写「M5 = 切换 + 下线旧模块」。按实际要求改成**只切换入口、并行观察**，
旧模块一个文件都不删。原因不是省事，而是观察期需要一个「点一下就能回去」的
回退路径：真正的回滚要是「改一行代码 + 重启 + 等构建」,那没人敢在观察期做
有风险的验证。

| 项 | 设计原本 | 落地做法 | 原因 |
| --- | --- | --- | --- |
| 入口切换 | 把 `/assets` 的 `load()` 指到新页面 | 两条路由各自保留，新库的 `nav.order=1` / `card.order=3`，旧库改成 `2` / `5` 并标「素材资产库（旧版）」 | 改 `load()` 等于旧页面从产品里消失，`asset:*` 权限的账号会直接看不到入口；并存期要的是「用户走新库、旧库仍可达」 |
| 旧模块下线 | 观察后删除 `modules/asset` 与 `entities/asset` | 本批不删；`modules/assets/module.ts` 里写了分两步的下线顺序（先撤 nav/card、再删模块） | 两步之间留一个「URL 还能打开」的缓冲期，日志能看出还有谁在用旧链接 |
| 顶部提示 | 无 | 旧页顶部一条指向新库的提示（可跳转、不拦截操作）；新库标题栏给一条「旧版素材库」反向链接，仅在 `asset:read` 时出现 | 单向往返不算回退路径；但不该给只配了新权限的账号显示一个点进去 403 的链接 |
| 数据追平 | 「并行观察」一句话 | 新增 `app library reconcile`（只读对账）与 doctor 的 `library.legacy_sync` | 观察期两边都有人写，「新库列表正常」不等于「旧库增量已追平」。对账要分三个桶报，处置完全不同 |
| 对账口径 | 无 | 复用 `LEGACY_PREFIX` 常量（导入与对账同一个），`assets` 表用原生 SQL 读 | 两处各写 `"legacy:"` 时，改一处就是「导进去了、对账永远说缺」 |
| 反向漂移 | 无 | `orphan` 桶：v2 有 `legacy:<id>` 而旧行已删 | 这个方向没人会主动核对，而那条 v2 行可能已经共享出去了。不自动清理——删不删是人的决定 |
| 格式不支持的旧行 | 无 | 单独一个桶（`missing_unsupported`） | 导入会**永远跳过**它们（早期归档行没有扩展名，脚本不猜格式）；混进「待补」会让值班的人反复跑一条跑不动命令 |

观察期怎么用这三条命令：

```
app library reconcile                  # 每天一次：有缺口退出码非 0
app library import-archive --dry-run   # 先看能补多少
app library import-archive             # 追平可补的部分（幂等，只增不改）
app doctor | grep library.legacy_sync  # 部署自检里也有一份同样的结论
```

**本机已经跑过一轮**：Postgres 开发库 19 条旧素材 → 迁 19 条、复制字节 72.7 MB，
`reconcile` 报「对账一致」，doctor 从「1 条警告」回到「0 失败 0 警告」。
这次导入只往 v2 写，旧 `assets` 表与旧归档盘一个字节都没动。

### 10.5 封装的口径调整：一个封装占一个位置

试用时发现的问题：封了 3 个素材，列表里变成 4 条（1 个封装 + 3 个原素材），
看起来像封装「又复制了一份」；而类型筛选里「封装」和成片 / 图片 / 音频并列，
像是第四种素材。三条调整：

| 项 | 原设计 | 现在 | 原因 |
| --- | --- | --- | --- |
| 成员是否单列 | 成员仍在列表里，封装只是引用 | `state=active` 列表里，装进「我能看到的封装」的成员**不再单独出现** | 用户要的是「封完只剩一条」。参考 M2 对共享的处理：一条素材共享出去后不在个人库重复出现，同一个原则 |
| 隐藏的判定 | — | `access.hidden_by_pack_clause`：成员行 + 存在一个 **active 且对我可见** 的封装包含它 | 只判 `library_pack_items` 有行会误伤：我把共享到团队的素材装进**自己的**个人封装，团队成员会平白看不到它，以为被删了 |
| 类型筛选的「封装」 | 二级筛选里有一档「封装」 | 去掉这一档；封装创建时**自动打上「封装」标签**（`PACK_TAG_NAME`），按标签筛 | 封装是容器不是类型。摆在类型筛选里会让人以为是第四种素材；标签本来就可筛、可组合、可改名 |
| 选择器 | 与列表同一个查询 | `list_assets(hide_packed_members=False)`：选择器**仍返回**成员 | 选择器挑的正是要被使用的那条素材。套用列表口径会让那 3 个原素材在生成页里彻底消失 |

两个必须一起改的地方，改一处就会不一致：

- **角标**：`_facets` 与列表同口径（否则「个人 4」点进去只看到 1 条）；
- **mock**：`listOf` 默认隐藏成员、`selection` 传 `hidePacked=false`、建封装带「封装」标签。

隐藏是**按关系算出来的**，不给成员打标记：封装一进回收站（解散），成员立刻回到
列表——不需要「解封时把标记擦掉」这种会在中途失败的第二处状态。

## 11. 我拍板的细节与仍需你知道的点

**已按授权自行决定**（不至于影响你验收的部分）：统一资产表用 `kind=pack` 表达封装；
负责人映射放 v2 不动 identity；批量打包阈值 100 项 / 2GB / 24h；标签新增对上传者开放、
改名合并需 `tag:manage`；上传接口单文件流式 + 前端批次并发；生成参数冻结快照不联表查；
错误码 slot=8；三个注入口全部 fail-closed。

生命周期相关的自行决定（2026-11-09 追加需求之后）：

- **草稿箱只收 AI 生成**，上传与模板库原片直接进个人库 —— 否则模板库流程会卡在「等审核」。
- **回收站保留 30 天**、每日 03:00 清理、可配置；有引用的跳过不硬删。
- **撤销共享一律审批**，不因为申请人有 `library:share:direct` 就免审；申请人是审核人时自审并记审计。
- **驳回草稿 = 进回收站**（`trash_reason=rejected`，可从回收站恢复回草稿），不留第四种状态。
- **小组负责人可移出小组但不可删他人资产**，团队库资产任何人不可删（含本人），只能先撤共享。
- **组长删自己上传、只共享给本组**的素材合并为「撤销 + 删除」一步（3.4.1 窄口）：
  必须显式传 `revokeOwnGroupShare=true`，同一事务内先写自审申请单再删共享行，审计带申请单 id。
  默认删除仍 409 + `canRevokeAndDelete`，由前端换成「撤销共享并删除」二次确认。

**五点需要你知道、但按默认继续、不阻塞**：

- **Q-A（模板库「不改前端」的边界）**：并存期模板库前端一行不动，继续走旧库；
  切换时改 `archive.api.ts` 里 `'/assets/upload'` 一个字符串常量指向 `/library/source-videos`。
  这是「不改界面」但必须改一处接口地址。若你要求连这个常量都不动，我得做同名兼容路由，
  会与旧模块撞路径和限流策略表 —— 不推荐。
- **Q-B（生图/工作台要新增 UI）**：这两处现在没有素材选择器，第 5 条的「一个接口」
  我能立刻给，但要让用户在界面上真的能选，需要各加一个入口组件。我按 M4 一起做。
- **Q-C（生成参数缺 seed、且旧生成结果没有回填参数）**：存量生成产物导入时只能拿到
  `outputUrl` 与各业务表已有字段，参数不完整会在详情页显示「未记录」；
  新产生的生成结果从接入 `library-ingest` 起才是完整快照。
- **Q-D（草稿审核要不要双人）**：你说的「审核后再进个人素材库」，我按**本人确认入库**
  实现（生成给自己用，自己挑最合适），管理员/持 `library:draft:review` 的人可代审、可批量审。
  若你要「提交人 ≠ 审核人」的双人复核（比如 AI 产物必须由组长点头才能入库），
  只需在 `approve` 路径加一条 `reviewer_id != owner_id` 判断，记在 M2 即可。

**Q-E 已按你的口径定稿**（2026-11-09）：`owner=me ∧ 唯一共享目标=本组 ∧ 我是组长`
→ 撤销与删除合并为一个动作，仍写审计与自审申请单。细节见 3.4.1，不再作为待确认项。

**回滚**：切换点仍是 `studio/src/modules/assets/module.ts` 的 `load()` 一行；
v2 的表、字节、投影、权限、注入口全部独立，删掉模块不影响现役库。

## 12. 上线前审查修掉的十处 + 上线后十一处（2026-11-10）

十处都不是新功能，而是**同一条规则写了两遍、其中一处漏了一半**，或者**一条脏数据
能让整批停下**。前七条先写成会失败的用例再修，用例留在
`api/tests/test_library_regressions.py`（7 例）。

1. **批量打标签越权**（`bulk.py`）：`addTag` / `removeTag` 直接写关联表，没有
   `load_visible` + owner 判定。任何持 `library:update` 的人拿到一个 id 就能改
   **别人的**标签，返回里还显示成功。补 `_require_writable()`，与改名同一条线。
2. **彻底删除误删共享字节**（`lifecycle.py`）：`others` 计数带了
   `deleted_at IS NULL`。两个用户的同内容资产都在回收站时，先 purge 的那个判定
   「没人引用」把文件删了，后恢复的那个取件 404。上游去重问的是「这条还在不在」，
   这里问的是「还有谁引用这份字节」，两个问题不同，条件不能抄。
3. **角标与列表口径不一致**（`service.py`）：四个入口原先共用一次
   `active_visible_clause` 的 group by。把共享到小组的素材装进**自己的个人封装**时，
   小组列表不隐藏它、角标却隐藏——显示 0，点进去 1 条。改成按入口各算一次
   （`tab_clause` + `hidden_by_pack_clause(tab)`）。
4. **选择器 total 虚高**（`service.py` / `router.py`）：`includePacks=false` 原先在
   Python 里滤 items，`total` 仍是含封装的数，前端会算出多一页，最后一页点进去为空。
   改成 `exclude_kinds` 落在 SQL 里。
5. **回收站里的封装还能打包**（`downloads.py`）：`kind=pack` 分支写在
   `_skip_reason` 之前，跳过了「回收站内容不可下载」。解散的封装照样展开成员进包——
   等于解散没生效。拆出 `_state_skip_reason`（封装天生没有 `storage_key`，
   不能套用那条「没有可下载的原文件」）。
6. **`sourceKey` 跨用户串号**（`service.py` / `router.py`）：`?sourceKey=` 是客户端
   可传的，而 `uq_library_assets_source_key` 是全局唯一、查询也不带 owner。别人用
   同一个 key 上传会命中**你的**行：他的文件被静默丢弃，返回的 id 还是你的。
   上传侧加 `upload:<actorId>:` 前缀按人隔离（生成侧的 `module:taskId` 不走这条
   路径），参数上限相应从 160 降到 120（列宽 160，前缀 40）。
7. **解散封装仍「保护」成员**（`references.py` / `lifecycle.py`）：引用检查不看封装
   state，解散后成员彻底删除被挡在「封装 X 的成员」上——X 正是用户刚解散的那个，
   界面上已经找不到。改成**只排除在回收站的封装**（不是「只算在库封装」：
   指向不存在封装的孤儿行也要算引用，不可逆删除的最后一道闸查不清就当有人在用）。
   同时补上「封装封面借用成员 `cover_key` / `storage_key`」这一层：字节真被删掉时
   把指向它的封装封面置空，留占位图而不是裂图。

8. **导入被一条脏数据打断**（`import_archive.py`）：`session.add` + `commit` 不在
   try 里。旧库里某条字节恰好也被同一个人在 v2 里传过时，`uq_library_assets_owner_sha_kind`
   会让整条命令中断——而观察期是每天跑一次的，每天都停在同一处，剩下的永远补不完。
   捕 `IntegrityError` 后回滚这一条、记进 `skipped_existing`，继续下一条。
9. **连续勾标签必然 409**（`LibraryDetail.tsx`）：标签选择器每勾一次提交一次，
   而 `expectedVersion` 取自尚未刷新的 `asset`。第一个请求已经把它 +1，第二个
   还带旧版本 → 「资产已被修改」，用户看到的是「打两个标签，第二个没生效」。
   标签改动改为不带版本（整体替换的集合操作），改名仍带版本。
10. **彻底删除封装清掉了别人的封面**（`lifecycle.py`，第 7 条的补丁打过头）：
    第 7 条那段「字节真被删掉时把借用这张图的封装封面置空」默认了「被删的行
    拥有这份字节」。封装没有自己的字节（`storage_key` 空、`sha256` 空，
    `others` 恒为 0），它的 `cover_key` 是从成员借来的，而成员还活着。两个封装
    收录同一个成员时，彻底删掉其中一个，另一个的封面被置空——图根本没人动过。
    改成只有 `storage_key` 非空且 `others == 0`（字节确实要删）才清理借用封面。
    用例：`test_library_packs.py::test_purge_pack_keeps_other_packs_cover`。
11. **封装点「下载」报 404，还怪在缩略图上**（`service.py` / `LibraryCard.tsx` /
    `LibraryWorkspace.tsx`，上线后用户报的）：卡片上的下载图标对封装也显示，
    点下去走的是单条取件 `/assets/{id}/file`。封装 `storage_key` 为空，落到
    「取不到键」那条分支，报 404 +「此资产没有缩略图」——用户下的是原文件，
    提示却在说缩略图；而 404 还让他以为记录被删了。
    两处一起改：
    - **后端**：`file()` 对 `kind=pack` 且非预览时抛 `PackHasNoOriginal`
      （400 `40087`，「请使用『下载』打包其中素材」）。**不能报 404**：封装是存在的，
      错的是「这个接口不适用于它」。同时把「没有键」的提示按 `preview` 分开
      （缩略图 / 原文件），两种取件共用一个函数，提示混用会把排查带偏。
      409 号段的 seq 1-9 已被占满，这里用 400 段的 7 号。
    - **前端**：封装改走打包下载（`POST /library/downloads`，服务端展开成员压成一个
      zip），卡片图标文案变「打包下载其中素材」，详情页对封装也给「打包下载」。
      详情页原来直接隐藏了下载按钮，与卡片不一致；顺手把「回收站里也显示下载原件」
      一并收掉——后端在回收站一律拒绝取件，那个按钮点了必然失败。
    用例：`test_library_packs.py::test_pack_file_endpoint_points_to_batch_download`。
12. **下载 zip 报 CORS**（`downloads.py` / `service.py` / `core/serving.py` /
    `infra/storage/*`，用户报的第二处）：`GET /library/downloads/{id}/file` 在远端
    驱动下 `307` 到对象存储的签名地址。前端是 XHR 取 blob 再 `saveBlob`，浏览器跟随
    跨域重定向时要求**目标域**回 CORS 头；MinIO 的桶级 `PutBucketCors` 在当前版本
    直接回 `NotImplemented`，桶上没有任何规则，于是控制台报
    `blocked by CORS policy`（这一次文件仍然下成了，属于运气，不是设计）。
    改法是「同源转发」而不是「去配桶」：
    - `ObjectStorage` 加 `iter_chunks()`：写方向 `put_file` 早就是「实现方自己决定
      流式还是分片」，读方向补上同一层（本地按块读盘、S3 按块读 `StreamingBody`）。
      顺带为 `_stage` 的 2GB OOM 待办铺好了接口。
    - `Served.stream()` 是第三种交法（前两种：本地路径、`307`），带 `Content-Length`
      与 RFC 5987 文件名——走 `307` 时用户存下来的名字是 key 的最后一段（一串 uuid）。
    - `downloads.file()` 与 `LibraryService._serve(proxy=True)` 改走它。
    **缩略图仍然 `307`**：`<img>` 不受 CORS 限制，交给桶能省一次中转、也保留 Range。
    **不给桶配 CORS 的理由**：那是环境配置，换部署就复发；而且本机 MinIO 的容器数据
    落在容器内（507 MB，没有 volume），重建容器等于丢数据，不该再往它身上加依赖。
    用例：`test_library_downloads.py::test_remote_download_file_streams_instead_of_redirecting`
    （假远端驱动下断言 200 而不是 307，把 `stream` 换回 `remote` 会失败在
    `404 == 200` 上）、`test_serving_stream.py`（分块读、响应头、原文件/缩略图分支）。
13. **共享出去的封装是个空壳**（`access.py` / `downloads.py` / `LibraryDetail.tsx`，
    用户报的第三处）：封装共享到团队后，别人点开封装里任一成员都是「资产不存在」；
    下载这个封装则直接 400「选中的条目都不在可下载范围内」。
    根因是读判定只看**成员行自己的字段**：成员的 `visibility` 仍是 `personal`
    （共享的是容器，不是逐个成员再走一次审批），而 `load_visible` / 下载展开都只问
    `can_read`，于是「封装可见」这件事在成员身上一点没剩下。
    补第三支口径「**封装是成员的读入口**」：
    - `access.readable_via_pack_clause` / `readable_via_pack_ids`：我是某个
      **对我可见的 `active` 封装**的成员 → 读得到。判关系而不是判字段，所以
      **不能并进 `active_visible_clause`**：进了列表，成员的素材会在封装旁边再列一遍
      （与 §10.5「一个封装占一个位置」冲突），也会让「猜 id」变成翻别人库的探针。
      批量路径一次查完一批，不在循环里按 id 打点。
    - `can_read` **故意不含**这一支（它是纯 Python 的，判不了关系）：读入口统一走
      `load_visible`（详情 / 取件 / 下载）；写路径（`packs._validate`、批量
      `_require_writable`）继续只认 `can_read`——「看得到」不等于「能改能再封装」。
    - `state != active` 的成员不适用：草稿与回收站永远 personal（不变式 A），
      封装授予的是读取，不是状态机豁免。
    - 下载展开成员时用同一个读入口（`can_read` 或 `readable_via_pack_ids`）。
      只按成员行判，别人下我的封装会拿到**空 zip**——比报错更难解释。
    - 前端顺手对齐：改名 / 打标签的入口跟着后端的 owner 判定走
      （`canEdit = canUpdate ∧ (owner ∨ delete:any)`）。别人的素材本来也改不了，
      但按钮挂在那就必然点出一个「资产不存在」。
    用例：`test_library_packs.py::test_member_of_shared_pack_is_readable_by_teammate`、
    `::test_member_of_private_pack_stays_invisible`（没共享的封装一条都不许漏）、
    `::test_trashed_member_is_not_readable_through_shared_pack`、
    `test_library_downloads.py::test_shared_pack_downloads_its_members_for_a_teammate`
    （把 `readable_via_pack` 从展开里去掉会失败在 400「选中的条目都不在可下载范围内」上：
    成员全被跳过，等于一个空包）。
14. **原图在、封面没了，界面显示裂图**（`maintenance.py` / `cli.py` / `LibraryDetail.tsx`）：
    用户看「T35黑色三视图.png」「3030三视图粉产品-1.png」的详情，`<img>` 是裂的，
    问「源图是不是丢了」。实测**原图 5.7 MB / 1.2 MB 都在**，缺的是
    `library/previews/<sha>.jpg`（MinIO `NoSuchKey`）——**两套字节一条记录**：
    原图按摘要落 `blobs/`，封面按同一摘要派生到 `previews/`，而 `cover_key`
    只是个 key，没有任何东西证明它的对象还在。
    全库扫了一遍：27 条里原图缺失 **0**、封面缺失 **3**（含用户没提的「年轻女人.jpg」）。
    三条都是 `origin=imported`，成因定位不到单一路径（没有同摘要行、没有 purge 痕迹），
    所以不再猜，改成「可复跑的对账 + 界面兜底」：
    - `ObjectStorage.exists()`：`head_object` / `Path.is_file`。不能拿 `get()` 探，
      那会把整个对象拉进内存；403 与 404 必须分开（「问不到」≠「不存在」，
      否则对账会把一批好好的对象重建一遍）。
    - `library repreview`（`maintenance.run_preview_repair`）：扫 `image`/`video`
      里 `cover_key` 为空或对象不在的行，下载原图 → `create_preview` → 写回
      `preview_key(sha256)`，**key 不变**（按摘要派生）。默认试运行。
      不扫音频（没封面）与封装（封面是借成员的，自己 `sha256` 为空）。
    - `LibraryDetail` 的媒体区：封面加载失败就退回原图（同源流式端点），
      原图也取不到才显示「暂时取不到可预览的文件」。列表卡片本来就用
      `CoverImage` 的渐变占位兜底，所以只有详情页会露出裂图标。
    本次已跑 `library repreview --no-dry-run`：扫描 25、正常 22、重建 3、失败 0，
    复扫 0 缺失，两个封面 URL 由 `NoSuchKey` 变 `200 image/jpeg`。
    用例：`test_library_previews.py`（先真删封面再修、试运行不写盘、幂等、不扫封装）。
15. **组员把别人的素材共享到团队 → 404「资产不存在」**（`sharing.py` / `errors.py` /
    `LibraryShareModal.tsx`，用户报的第四处）：editor 在小组里看到 admin 共享进来的
    素材，点「共享到…」选团队，提交后 404。
    根因是发起入口只认所有者（`asset.owner_id != actor.id → NotFound`）。这条规则
    把共享审批写成了「只能处置自己的东西」，可审批流把关的从来不是所有权，而是
    「范围该不该扩大」——上传者不在（离职、休假、只是懒得点）时，一份该全员复用的
    素材在组里没有任何合法上浮路径。
    按**风险方向**重划：
    - **共享**（把自己的东西给别人看，风险在所有者可控范围）：读得到就能申请。
      范围由审批人把关，申请人只需够得着这条资产。
    - **撤销**（让别人突然拿不到在用的文件）：仍只认所有者，新码
      `ShareNotOwned`（403 `40382`）。非所有者看到「取消共享」按钮却没有资格，
      是下一类误报的来源。
    - **`library:share:direct` 只对自己的资产成立**：替别人扩大范围一律留 pending。
      审批记录是「谁把谁的私货推上了团队库」的唯一载体，跳过它等于管理员能悄悄
      处置任何人的可见性。
    - 「读得到」要再分一层：只因为「我是某个可见封装的成员」才读到的素材，不能
      单独调共享范围，新码 `PackMemberShareBlocked`（403 `40383`）。成员可见性跟
      封装走，允许单条放宽就出现「整体只给这个组看，但其中一张全团队可见」的裂缝。
    - 不可见的 id 仍是一律 404：`request()` 改走 `load_visible`，别把申请入口变成
      「猜 id 探库」的探针。
    - 前端弹窗的「立即生效 / 需审批」按「有 direct **且** 是自己的」算，非所有者
      那句文案直接说「这是别人上传的素材，通过后才会扩大可见范围」。
    用例：`test_library_sharing.py::test_group_member_can_ask_to_widen_a_shared_asset_to_team`
    （改回去会失败在 `404 == 201`）、`::test_member_request_never_applies_directly_even_with_direct_permission`、
    `::test_member_cannot_revoke_someone_elses_share`、
    `::test_pack_member_cannot_be_shared_on_its_own`、
    `::test_outsider_cannot_ask_to_share_an_invisible_asset`。
    真实 dev 库上复核（`内容工作台素材页面设计.png`，owner=u_001、组员 u_002）：
    revoke → 40382、共享到同一小组 → 409「已经共享给这个小组」（说明已越过 owner 检查、
    进入目标校验）、共享到团队 → `pending` 且资产 `visibility` 仍是 `group`。

顺带对齐 `mock.ts`：`visible()` 的「全部」口径原先写成
`ownerId === ME || visibility !== 'personal'`，会把别人共享到**我不在的小组**的资产
也放进来；现在与 `active_visible_clause` 同构，`facets()` 也改成按入口逐个算。

16. **改名能改格式，卡片还摆着后缀**（`service.py` / `schema.py` / `LibraryCard.tsx` /
    `LibraryDetail.tsx`，用户问的第五处）：卡片标题显示 `封面.png`，详情页改名输入框也
    带着 `.png`，用户问「把扩展名改成 `.mp4` 是不是就变视频了」。
    答案是**差点是**：`kind` / `mime_type` 在入库时由真实文件定死，改名改不动它们，
    但 `name` 是自由文本——`a.png` 改成 `a.mp4` 之后，详情徽标还写着「图片」，
    下载下来的文件却叫 `.mp4`，播放器一打开就是坏的。名字与字节对不上，比不能改更糟。
    - 后端 `keep_extension()`：改名只留主体，后缀拼回**记录自己的**那个（先看 `name`，
      再退到 `storage_key`——导入的历史数据名字可能被清过）。封装没有后缀就不加，
      不硬塞 `.png`。只剥「像扩展名」的尾段（带字母、1–5 位），`v1.2` 的 `.2` 留着。
      作用点只有 `patch`，批量改名走它、上传走 `EXTENSIONS`，两条路径口径一致。
    - `LibraryAssetOut` 增加 `tags`（一次 `IN (...)` 查完整页）：卡片要在体积下方显示
      标签，让每张卡再发一次详情请求不划算。详情继承这个字段，不再重复声明
      （原来详情单独传 `tags=` 给构造器，加完父类会撞关键字）。
    - 前端 `displayName()`：卡片标题、详情弹窗标题、`aria-label`、选择器、打包弹窗、
      删除确认文案统一剥后缀；`title` 与下载文件名仍用完整 `name`（下载下来要能双击打开）。
    - 改名输入框预填主体名，编辑态下方一行「只改名称主体，格式后缀由文件本身决定」——
      这句话必须在编辑时说，否则用户改完发现后缀还在，会当成 bug 来报。
    用例：`test_library_naming.py::test_rename_cannot_turn_an_image_into_a_video`
    （退化验证：把 `keep_extension` 换回 `payload.name` 会失败在
    `'换个名字.mp4' == '换个名字.png'`）、`::test_rename_keeps_dots_that_are_part_of_the_name`、
    `::test_pack_rename_gets_no_suffix`、`::test_list_items_carry_their_tags`。
    真实 dev 库上复核（u_001）：卡片文本依次是 `图片 · T35黑色三视图 · 导入 · 老大 ·
    5.5 MB · T35 · 产品`，详情弹窗标题不带后缀，改名输入框值为 `T35黑色三视图`。

17. **草稿箱是个空架子：生成侧从来没调过入库**（`generation/runner.py` /
    `image/pipeline.py`，用户问的第六处）：「我尝试生图结果怎么没进入草稿箱」。
    查下来不是权限、不是筛选，而是**这条链路只有两端、没有中间那一段**：
    `POST /assets/from-generation`、`library-ingest` 端口、`GenerationIngest`
    实现、`test_library_ingest.py` 全都在，但 `grep ingest_generated` 除了
    定义处没有任何调用点——生成成功之后，谁也没把结果送去资产库。
    - `runner._archive()`：两条成功路径（`handle_poll` 的 `SUCCEEDED` 与同步供应商
      的 `_settle`）都补上，位置在 `mark_succeeded` **commit 之后**——生成已经是
      既成事实，入库失败不许把用户看到的结果改回失败，只留一条带 `kind/id` 的
      error 日志（静默跳过与「没接链路」在日志里长得一样，就白排查一次）。
    - 端口未注册（资产库还没上线的部署）时只 warning 不中断生成：为此让用户连图都
      生成不了，是更坏的选择。
    - 字段映射放各模块的可选钩子 `pipeline.archive_request(session, record, stored)`：
      框架连 `image_jobs.size` / `video_tasks.aspect_ratio` 都不认识，硬写一张映射表
      等于把三个模块的知识塞回 `generation/`。模块不实现钩子 = 它的产物不进草稿箱。
    - **幂等键要带供应商任务号**（`image:{job}:{externalId}`）：重试复用同一个作业行
      重投，只用作业 id 的话第二张图会被判成「已经入过库」，草稿箱永远停着上一版。
    - 名字带真实后缀（`_with_suffix`）：卡片显示时剥掉，打包下载拿 `name` 当条目名，
      缺后缀的文件双击打不开。
    用例：`test_generation_ingest.py`（落点 + 草稿箱接口可见、重试出第二条草稿、
    入库失败不反噬生成）。三条都做过退化验证：去掉 `_archive` 调用失败在「草稿箱里
    必须有这一条」；幂等键退回 `image:{id}` 失败在 `1 == 2`（重试后仍只有一条）；
    去掉 try/except 会让轮询抛出、生成被标失败。
    真实 dev 环境（Postgres + MinIO）跑了一次完整轮询：作业 `succeeded`，草稿箱多出
    `探针产品图.png`（`draft/personal/generated`，181 B，`library/blobs/…` 有字节、
    有缩略图）；草稿箱页面截图确认卡片带「AI 生成 · 老大 · 181 B · 入库 / 驳回」。
    **仍未接**：生视频（`task`）与换装（`template_replacement`）——前者没有
    `persist_output`，产物是供应商直链，入库要现取现存（300MB 上限）；后者有自己的
    `replacement_reviews` 审核链，再接一层草稿箱要先定「哪一层说了算」。两处都只差
    一个 `archive_request` 实现，等产品口径。

18. **标签筛选是个看不见词表的下拉框，卡片还只露两枚标签**（`tags.py` / `router.py` /
    `LibraryToolbar.tsx` / `LibraryTagBar.tsx` / `LibraryCard.tsx`，用户提的第七处）：
    下拉不点开就不知道有哪些标签、每个标签下有多少素材；卡片用「两枚 +N」把后面的
    标签藏起来，去标签只能进详情弹窗。
    - `GET /tags` 新增 `state` / `library` 参数，返回 `LibraryTagStatOut`
      （`assetCount` + `createdAt`），按 `library_tags.created_at` 升序。
      计数用 `LibraryService.list_assets` 同一套可见条件（`tab_clause` +
      `hidden_by_pack_clause`、草稿/回收站按权限点收窄），一次 `group_by` 查完，
      **不受已选标签/关键词影响**——否则每点一次筛选，其余标签的数字都会跳。
      卡片上的 `tags` 仍是 `LibraryTagOut`，不背这两个与卡片无关的字段。
    - 前端标签条常显全部标签（含 0 条的）与计数，点击切换 URL 里的 `tags`；
      卡片标签全部换行摊开，每个带 ×，`library:update` + owner/`delete:any`
      才显示，走 PATCH 整体替换，成功后列表 / 分面 / 详情 / 标签计数一起失效。
    - `mock.ts` 的 `tagStats()` 与 `listOf()` 同口径，PATCH 去标后计数立刻变。
    用例：`test_library_tags.py`（按入口计数、他人个人库不串数、按添加时间排序、
    去标与移入回收站后计数变化）、`LibraryTagBar.test.tsx`、`LibraryCard.test.tsx`、
    `mock.test.ts` 的「标签栏计数」。

19. **封装只有「封装」一个标签，卡片上也没有加标签的入口**（`tags.py` / `packs.py` /
    `maintenance.py` / `cli.py` / `LibraryTagPicker.tsx`，用户提的第八处）：成员被装进
    封装后从列表隐身，按「产品」筛只能筛到散件、筛不到装着产品图的封装；而卡片只有 ×
    没有 +，想给素材补个标签必须进详情或批量栏。
    - `sync_pack_tags()` 在**建封装 / 替换成员**时把成员标签并集合并进封装：
      `create` 以「封装 + 手选标签」为基准整体替换，`update` 不传 `tagIds` 时只增不减。
      标签是真绑定而不是读时拼的，所以卡片上的 × / + 对封装同样有效，按标签筛选
      不需要另写一套 SQL。**成员后来单独打标不回写封装**：那要反查所有引用它的封装，
      还会把用户刚删掉的标签又塞回来；改一次成员时按当前成员并集重新补，是这条口径的
      代价，已写进函数注释。
    - `app library sync-pack-tags` 回填存量封装，默认试运行、`--no-dry-run` 才写，
      可反复跑（第二次 `pending=0`）。`--owner` / `--batch` 可收窄范围。
    - 前端 `LibraryTagPicker` 走 `Modal`（卡片根节点 `overflow-hidden` 会裁掉内联浮层），
      只列还没挂上的标签、可就地新建（`library:create` 才显示输入框），提交时把现有 id
      与本次选中合成完整 `tagIds`。权限与 × 一致：`library:update` + owner/`delete:any`。
    - `mock.ts` 的 `packTags()` 与后端同口径。用例：`test_library_pack_tags.py`
      （继承并集 / 按成员标签筛出封装 / 换成员只增不减 / 回填幂等）、
      `LibraryCard.test.tsx`、`LibraryTagPicker.test.tsx`、`mock.test.ts` 的封装用例。

20. **没有「全部素材」总览，入口顺序也把草稿箱摆在第一位**（`types.ts` /
    `LibraryToolbar.tsx` / `LibraryWorkspace.tsx`，用户提的第九处）：后端
    `LibraryTab.ALL` 与 `facets.all` 一直存在（选择器与分面在用），只是没做成
    一级入口；用户要的是「当前账号能见的个人 + 小组 + 团队」一次看完。
    - `LibraryView` 增加 `all`，`VIEWS` 重排成 `全部素材 / 个人 / 小组 / 团队 /
      草稿箱 / 回收站`，`viewQuery('all')` 显式传 `state=active&library=all`。
      **列表与标签栏共用这一个 scope**：默认页的「标签 · N」与「共 N 条」
      不会各算一套。`parseView(null)` 落到总览，旧 `?view=personal` 链接照常还原。
    - 角标取 `facets.all`，与 `library=all` 的 `total` 同源（`_facets` 里
      `visible_count(None)` 与列表的 `active_visible_clause` 同一支）。
      `all` **可能小于三个范围角标之和**：跨入口可见的封装，其成员在各自范围内
      出现、在全量口径里被封装收起来；这是去重，不是漏数据，已写进 `types.ts` 注释。
    - 上传 / 封装按钮跟着默认页走：`viewAllowsCreate` 对 `all` 与 `personal`
      都放行（上传写个人库、封装选择器本就是全量可见集），草稿箱与回收站仍隐藏。
    - 后端**接口零改动**：`library=all` 是既有参数，四份生成物无需重生成。
      新增 `test_library_all_scope.py` 钉住并集口径、他人个人库/他组共享不串、
      草稿与回收站不混入、共享后标签计数在 `all` 下不变、`facets.all == total`；
      前端 `types.test.ts` / `LibraryToolbar.test.tsx` / `mock.test.ts` 覆盖
      入口顺序、映射、默认视图、按钮显隐与 mock 同口径。

21. **审批权只有一个「全站」开关，组长还能靠组长表绕过它**（`__init__.py` /
    `sharing.py` / `dependencies.py` / `permissions.ts`，用户提的第十一处）：
    角色配置里想给「只审团队」或「只审本组」的人授权时，只能把能审所有申请的
    旧码整包发出去；而 `library_group_leaders` 又是一条不看权限点的旁路。
    - 按**申请单的目标范围**拆成三个码，共享与撤销共用一份口径：
      `library:share:approve` 保留为**全站审批**（兼容码，等同两个新码都勾，
      已有角色升级不丢能力）；`:approve:team` 管 `target=team`；
      `:approve:group` 管 `target=group` **且该组是我所在小组**（范围取自
      `org-directory`，不是组长表）。三个码都不进 `BUILTIN_ROLES`——
      启动钩子会自动把声明的新码补进内置角色，写进去等于给 editor / viewer
      也发了审批票，正好与「收窄」相反。
    - `ShareService._can_approve_target()` 是**唯一判据**，`decide()`、
      `list_requests(scope=pending)`、撤销申请的「自审」三处都调它；
      后两者曾经各写一套（一个查组长表、一个查权限点），拆分时最容易漏一处。
      「待我审核」用 `or_(team 分支, group 分支)` 拼 SQL，与 Python 判定同口径，
      不会出现「看得到却点不动」。
    - **自审按目标收紧**：只有 `:approve:group` 的人撤销自己共享到团队的资产
      不再自审（否则小组审批权能顺手跳过团队级把关）；全站码与对应的目标码
      才解锁自审，避免「自己申请、等自己批」的死锁。
    - **组长表按产品口径退回去只服务删除窄口**（§3.4.1）与 `/groups` 的
      `isLeader` 展示，不再单独发审批票；`PUT /groups/{id}/leaders` 仍由旧的全站码
      守护（它是全站级配置，不是审批动作）。窄口本身**保持不变**，一次改动不动到
      删除矩阵。
    - 路由 `approve|reject` 仍只要求 `library:read`：资格要按申请目标 + 当前组
      成员关系算，`require()` 表达不了。前端 `ShareRequestsModal` 也继续按「服务端
      返回的列表」呈现，不按权限点藏按钮。
    - 四份生成物重生成（权限表多两行、`GroupLeadersIn` / `LibraryGroupOut`
      的注释口径、`permissions.ts` 的码表）；新增
      `test_library_share_approval_split.py` 钉住三码边界、他组/无组越权、
      旧码兼容、组长表不再授权、自审按目标、负责人表写入口；
      `test_library_sharing.py` 的组长用例改成「不登记负责人也能审」。

**审查里看到、但这次没动的**（记录在这，避免下次重新发现一遍）：

- `modules/asset/archive_service.py` 的归档原片取件仍是 `307` 到对象存储。
  `<video>` 播放没问题，但如果哪天前端也改成 XHR 取 blob（下载归档原片），
  会踩与第 12 条完全相同的 CORS 坑。旧模块在观察期内不动，先记着。
- `downloads._stage` 对远端驱动是 `storage.get()` 全量进内存，2GB 单文件会 OOM。
  第 12 条已经补上 `iter_chunks()`，改成流式写盘只差一行，但这属于另一件事
  （打包链路的改动要连带验证「压缩中途断流」），没有顺手做。
- 被移出小组后，自己共享给该组的素材在列表里看不到（`active_visible_clause` 的
  owner 分支只认 `visibility=personal`），但详情按 owner 仍可读。这是产品口径问题，
  改它会让「共享出去的素材是否还在个人库出现」重新变成议题，不擅自定。
- `ensure_tag` 并发建同名标签会撞唯一索引返回 500（两个用户同时建封装）。
  根治要用 `begin_nested()` savepoint（直接 rollback 会把外层已 flush 的封装行一起回滚），
  概率低、重试即恢复，先记着。
- 审批通过时不校验申请单自带的 `expected_version`，一张等了很久的旧申请仍能落地。
- 选择器里队友只挑得到共享出去的**封装本身**，挑不到里面的成员（第 13 条只放宽了
  读入口，列表与选择器仍按 `active_visible_clause`）。要么让生成侧认「封装展开」，
  要么把成员也放进选择器——两条都改产品口径，不在修 bug 的范围里。
  实际只写 `visibility`，被改名/换标签不影响语义，危害小。

