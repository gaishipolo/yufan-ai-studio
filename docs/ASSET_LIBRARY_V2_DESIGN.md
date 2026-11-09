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

| 一级入口 | 实际查询 | 为什么不能做成 `visibility` 的第 4/5 个值 |
| --- | --- | --- |
| 草稿箱 | `state=draft AND owner_id=me` | 草稿**永远只能本人可见**，共享、选择器、团队检索都必须排除它；把它塞进 `visibility` 会出现「草稿共享到团队」这种非法状态 |
| 个人 / 小组 / 团队素材库 | `state=active` + 对应可见范围 | 三者才是真正的可见范围 |
| 回收站 | `state=trash AND owner_id=me`（管理员看全量） | 回收站里的东西必须已经**退出所有共享**，否则「删了别人还在用」 |

因此底层是 `state × visibility` 两个维度，界面上排成五个一级入口。这个拆分还带来两条
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
| 3 | 个人素材共享到小组/团队；无直接共享权限需小组负责人审核；共享只改可见范围不改所有者 | §3 状态机；`library_share_requests`；`owner_id` 永不改写 | **小组负责人不放进 identity 模块**（会动到刚上线、正在使用的三层授权）。v2 自建 `library_group_leaders(group_id,user_id)`，审核人 = 超管 ∪ 持 `library:share:approve` ∪ 该组登记的负责人。团队级申请由超管/持审核权的人处理 |
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

- 「共享至团队跟共享至小组一样」：同样走审核流，只是 `target=team`、审核人变成
  超管 / `library:share:approve` 持有者（没有「团队负责人」这个概念）。
- 审核通过是一次事务：校验 `expected_version` → 改 `visibility` → 写/删
  `library_asset_groups` → 更新申请状态 → 写审计。并发重复审核靠版本挡住。
- 申请单列表分两个视角：「我提交的」与「待我审核」，后者按 `library_group_leaders`
  与权限点过滤，不会让人看到全站申请。

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
2. **团队库没有「负责人」概念**，所以团队共享的撤销只能由申请人本人发起 + `library:share:approve`
   审核，或超管直接处理。
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
- **自己就是审核人时自审**：超管 / 持 `library:share:approve` 的人申请撤销自己的资产，
  系统直接 `approved by self` 并写审计（否则会出现「自己申请、等自己批」的死锁）。
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
| 共享 | `POST /share-requests`（`action=share\|revoke`）、`GET /share-requests`、`POST /share-requests/{id}/approve\|reject\|cancel` | `library:share` / `library:share:approve` | 见 §3.2 与 §3.5；`action=revoke` 必须带 `reason` |
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
  share:approve / tag:manage / generation:ingest`，共 **11 个**。`delete` 与 `delete:any`
  必须分开：前者是「删自己的」，服务层仍要判 `owner_id`；后者是管理员兜底通道
  （删他人的、团队库的、彻底删除）。批量下载不单列权限（可见即可下载，§4 决策）。

  **这 11 个是目标形态，不是一次性声明**：`tests/test_permission_coverage.py` 要求
  每个声明的码都有 `require(...)` / `actor.has(...)` 判定点，否则角色界面会出现
  「勾了没用」的装饰品。所以按批进入 `ModuleSpec`：M1 只声明已接线的
  `read` / `create`，M2 加 `update / delete / delete:any / draft:review / share /
  share:direct / share:approve / tag:manage`，M3 加 `generation:ingest`。
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
- 页面顶部**五个一级入口**（按用户要求）：`草稿箱` / `个人` / `小组` / `团队` / `回收站`。
  前三后二虽是两个维度，界面上不做解释，只把「草稿箱」「回收站」放两端并带计数徽章
  （待审 N / 回收站 N）。URL 统一 `/library?view=drafts|personal|group|team|trash`，
  刷新与分享可还原。
- 每个入口内部：二级类型筛选（成片 / 音频 / 图片 / 封装）→ 三级标签筛选 → 关键词 /
  排序 / 分页。筛选条件写进 URL。
- 两个主按钮：**上传**（单个与批量，弹窗内可多选文件、选类型与标签、逐条进度）与
  **封装**（弹窗内「选择已有素材」+「上传新素材」两个来源，选完填名称与标签，提交）。
  这两个按钮只在 `个人` 入口出现；草稿箱与回收站没有上传/封装入口。
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
| M4 | 前端页面全套 + `LibraryPicker` 接入三处 + mock 全端点 | `npm run verify`、`gen:mock-coverage`、隔离用例 |
| M5 | 切换演练（改 `load()` 一行）、并行观察、下线旧模块、重生成全部生成物 | 一键回滚演练通过；旧库数据已对账追平 |

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
