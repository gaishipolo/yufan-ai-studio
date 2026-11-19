# 素材库 v2 · 任务逻辑说明（按实现口径整理）

> 依据：`docs/ASSET_LIBRARY_V2_DESIGN.md`、`docs/ASSET_LIBRARY_V2_PLAN.md` 与
> `api/src/modules/library/*`、`studio/src/{modules/library,features/library,entities/library}` 的**实际代码**。
> 设计文档写「应该是」，本文写「跑起来是什么」；两者不一致的地方在 §9 单独列出。
> 阅读顺序建议：§1 概念 → §3 可见性 → §4 任务清单（主体）→ §9 风险。

## 1. 一句话总览

素材库是一张统一资产表 `library_assets`，用 **`state`（生命周期）× `visibility`（可见范围）× `kind`（资产类型）**
三个正交维度表达全部业务；「草稿箱 / 回收站」是状态视图而不是可见范围；所有写操作都必须守住两条不变式
（草稿与回收站只能是个人私有），共享与撤销共享都要留申请单，删除分「软删 / 彻底删」两个动作。

```
                    ┌─ 有 library:share:direct ─→ 立即生效
共享请求 ───────────┤
                    └─ 否则 ─→ pending ─ approve / reject / cancel
撤销请求 ── reason 必填 ──→ 有 share:approve 则自审立即生效，否则 pending
```

## 2. 概念模型

| 维度 | 取值 | 说明 |
| --- | --- | --- |
| `state` | `draft` / `active` / `trash` | 草稿箱 / 在库 / 回收站，生命周期轴 |
| `visibility` | `personal` / `group` / `team` | 三个可见范围；小组的具体归属在 `library_asset_groups` |
| `kind` | `video` / `audio` / `image` / `pack` | 成片 / 音频 / 图片 / 封装；封装是一行无字节资产 |
| `origin` | `upload` / `generated` / `template_source` / `imported` / `pack` | 来源，决定详情页是否显示生成参数 |
| `trash_reason` | `deleted` / `rejected` / `forced` / `expired` | 进回收站的原因，决定文案与恢复去向 |

两条硬不变式（由 `lifecycle._to_trash()` 强制，不靠调用方记得）：

- **A**：`state ∈ {draft, trash}` ⇒ `visibility = personal` 且 `library_asset_groups` 无该资产的行。
- **B**：`visibility = team` ⇒ 组行必须为空（团队 ⊇ 小组，共享到团队时清空组行）。

推论：
- 「已共享的素材不能删」不是一条 if，而是数据模型上进不去回收站。
- 恢复只回 `personal`，绝不恢复共享，要重新走一次申请。
- `owner_id` 在任何共享动作里都不改写，「谁上传的」永远可追溯。

## 3. 读判定：五个入口各查什么

| 一级入口 | 查询条件 | 谁能看全量 |
| --- | --- | --- |
| 草稿箱 | `state=draft` + `owner_id=me` | 持 `library:draft:review` 可代审 |
| 个人 | `state=active` + `owner_id=me` + `visibility=personal` | — |
| 小组 | `state=active` + `visibility=group` + 组行命中我所在组 | — |
| 团队 | `state=active` + `visibility=team` | 所有启用账号 |
| 回收站 | `state=trash` + `owner_id=me` | 持 `library:delete:any` 可看全量 |
| 选择器 `/selection` | 强制 `state=active`，三库并集 | 草稿与回收站**永不出现** |

判定只有一份定义：`access.py` 的 `active_visible_clause`（SQL）、`tab_clause`（按入口）、
`can_read`（Python 单条）与 `load_visible`（看不见就 404）。四处改一处必漂，所以读代码时以 `access.py` 为准。

三个容易踩的口径：

1. **共享出去之后，个人库不再重复出现**——「我自己的」这一支只算 `visibility=personal`。
2. **封装成员在列表里不单列**（`hidden_by_pack_clause`），且必须带「封装对我可见」这一条件，
   否则我把共享到团队的素材装进自己的个人封装，团队成员会以为素材被删了。
3. **选择器不隐藏封装成员**（`hide_packed_members=False`）——它要挑的就是成员本身。

文件取件：`state=trash` 一律 404（「删除」必须真的拿不到文件）；草稿本人可预览可下载；
其余可见即可下载，本地驱动走 `FileResponse`（支持 Range），远端驱动 307 到预签名地址。

## 4. 任务清单

每个任务给出：触发 → 前置校验 → 写入 → 失败分支 → 代码位置。
权限点共 11 个：`read / create / update / delete / delete:any / draft:review / share / share:direct /
share:approve / tag:manage / generation:ingest`。

### T1 上传图片 / 音频 → 直接进个人库

- 触发：`POST /api/v1/library/assets/upload?filename=&kind=image|audio&sourceKey=`，`library:create`，限流 cost=3。
- 前置：
  - 扩展名必须在 `infra.media_probe.EXTENSIONS` 白名单内，且推断出的 mime 类型必须与 `kind` 一致；
  - `sourceKey` 服务端加前缀 `upload:<actorId>:`（按人隔离，防跨用户串号），长度上限 120。
- 步骤：
  1. 流式写入临时文件（`MAX_BYTES` 上限，超限 413，不进内存）；
  2. `inspect()` 探测宽高 / 时长 / 是否有音轨（阻塞调用放线程）；
  3. 计算 sha256 与实际大小，与接收大小不符 → 400（防截断）；
  4. `sourceKey` 命中已有行 → 直接返回原行（幂等）；
  5. 同 owner + 同 sha256 + 同 kind 且未删除 → 返回原行（**秒传**，不重复写字节）；
  6. 写字节到 `library/blobs/<sha前2位>/<sha><ext>`；
  7. 非音频生成封面 `library/previews/.../<sha>.jpg`，失败只记日志、`cover_key` 留空；
  8. 落库：`state=active`、`visibility=personal`、`origin=upload`、`state_version=1`。
- 并发：唯一索引 `uq_library_assets_owner_sha_kind` 兜底，`IntegrityError` 后回滚并按同口径再查一次；
  查不到则原样抛出，不伪装成功。
- 代码：`service.LibraryService.upload / _ingest / _find_duplicate`，`router.upload`。

### T2 模板库原片上传 → 直接进个人库

- 触发：`POST /library/source-videos`，`library:create`。
- 与 T1 的唯一差别：`kind=video`、`origin=template_source`。
- 为什么单列接口：来源语义不同，视频上传属于模板库流程，`origin` 决定模板封装能否继续吃它。
  模板库切换时只改 `archive.api.ts` 一个 URL 常量，界面不动。
- 代码：`router.upload_source_video`。

### T3 生成结果入库 → 固定落草稿箱

- 触发：`POST /library/assets/from-generation`（`library:generation:ingest`），
  或后端模块经 `library-ingest` 注入口调用 `ingest_generated()`。
- 入参：`sourceModule(image|template|task)` + `sourceTaskId` + `sourceKey` + `kind(image|video)` +
  字节三选一（`storageKey` / `sourceUrl` / 由注入口直接给）+ `generation` 参数快照。
- 强制：`state=draft`、`visibility=personal`、`origin=generated`；调用方给不了 state 与 visibility。
- 步骤：
  1. 校验 kind 只能是图片 / 视频；`ownerId` 取入参，缺省取当前请求主体，都没有 → 400；
  2. `sourceKey` 必填，先查幂等（生成侧轮询会重复回调）；
  3. 物化字节：`storageKey` 优先（本地驱动零拷贝），`sourceUrl` 走 `fetch_bytes`（逐跳 SSRF + 大小上限），
     **不接受只存外部签名 URL**；
  4. 探测 + 摘要 + 同 owner 同 sha 同 kind 去重（与上传同口径，第二次同字节不撞 500）；
  5. 写字节 + 封面；
  6. 落库 `state=draft`，并写一份**冻结的** `library_asset_generation` 快照（逐字段白名单映射，
     不联表、不补算）；
  7. 可选 `tagIds`。
- 返回值：新建 201 / `created=true`；命中幂等 200 / `created=false`。
  第二种命中（同字节已存在）**不覆盖参数快照**，避免「这张图是谁生成的」由最后一次回调决定。
- 代码：`ingest.GenerationIngest.run`，`ingest._SNAPSHOT_FIELDS`。

### T4 草稿审核：采纳 / 驳回

- 采纳 `POST /drafts/{id}/approve`：
  - 权限：本人，或 `library:draft:review`（代审）；他人且无权限 → 403 `DRAFT_REVIEW_FORBIDDEN`；
  - `state` 必须是 `draft`，否则 409 `DRAFT_NOT_REVIEWABLE`；
  - `expectedVersion` 非 0 时校验 `state_version`，不一致 → 409；
  - 写入：`state=active`、`visibility=personal`、`reviewed_by/at`、`state_version+1`、
    `library_draft_reviews(decision=approved, snapshot)`、审计 `library.draft.approve`。
- 驳回 `POST /drafts/{id}/reject`：
  - 同上校验；写入 `reviewed_by/at`、`reject_reason`；
  - 进回收站：`trash_reason=rejected`、`trashed_from_state=draft`；
  - 从回收站恢复会回到**草稿箱**，不是直接进库（否则等于跳过审核）。
- 批量：`POST /assets/bulk` 的 `approveDraft` / `rejectDraft`，逐条判定。
- 代码：`lifecycle.approve_draft / reject_draft / _draft_for_review`。

### T5 封装（pack）

- 触发：`POST /library/packs`（`library:create`），或批量写 `action=pack`。
- 语义：一行 `kind=pack`、`origin=pack`、无自己的字节的资产 + `library_pack_items` 有序成员；
  成员行仍归各自 owner，封装只是引用，解散不影响成员。
- 前置：
  - 至少一个成员，否则 400 `PACK_EMPTY`；
  - 每个成员必须「我看得见且在库」，否则 400 `PACK_ITEM_INVALID` 带明细
    （不看可见性就等于拿封装当窥探探针）；
  - 成员不能是另一个封装（禁止嵌套）；
  - 单封装成员上限 100。
- 写入：自动打「封装」标签（`PACK_TAG_NAME`）；封面取法 = 优先图片成员缩略图 → 任意成员缩略图 → 空；
  `position` 全量替换（删干净 → flush → 重插，因为有 `(pack_id, position)` 唯一索引）。
- 相关：`GET/PATCH/DELETE /packs/{id}`；解散 = 软删封装行，成员不动。
- 列表口径：`state=active` 列表里，装进「对我可见的 active 封装」的成员不再单独出现；
  类型筛选里没有「封装」这一档（按「封装」标签筛）。
- 代码：`packs.PackService`，`access.hidden_by_pack_clause`。

### T6 共享到小组 / 团队

- 触发：`POST /library/share-requests`，`action=share`，`library:share`。
- 前置：
  - 只有 `owner` 能发起（管理员也不行，「替别人共享」没有这个口子）；
  - 资产必须 `state=active`；
  - `target=team`：已共享团队 → 409 `SHARE_TARGET_INVALID`；
  - `target=group`：`groupId` 必填、必须是我所在的小组、资产不能已共享团队、不能已在该组。
- 分支：
  - 已有同资产同目标同 action 的 `pending` → 直接返回原单，不重复建；
  - 有 `library:share:direct` → 申请单直接 `status=approved`（`reviewer=self`），
    并在同一事务内应用可见性：team 清空组行并置 team；group 加组行并置 group；
    `state_version+1`；
  - 否则 `status=pending`，等审批。
- 审批 `POST /share-requests/{id}/approve|reject`：
  - 只要求 `library:read`，判定在服务层：持 `library:share:approve`，或
    `target=group` 且我是该组组长（`library_group_leaders`）；
  - `status` 必须 `pending`；`expectedVersion` 非 0 时校验资产版本；
  - 用 `UPDATE ... WHERE status='pending'` 抢占，`rowcount != 1` → 409 `REQUEST_NOT_PENDING`
    （不先查再改，否则两个人同时点「通过」会写两次）；
  - 通过才应用可见性并 `state_version+1`；拒绝只记 reviewer/note，资产不动。
- `cancel` 仅申请人、仅 pending。
- 列表 `GET /share-requests?scope=mine|pending`：`pending` 按权限点与组长身份收窄，
  不是全站待办（否则等于泄露别人在共享什么）。
- 组长映射：`PUT /library/groups/{groupId}/leaders`（`library:share:approve`，整体替换）。
- 代码：`sharing.ShareService`。

### T7 撤销共享（也要审批）

- 触发：`POST /library/share-requests`，`action=revoke`。
- 与 T6 的关键差异：
  - `reason` 必填，否则 400 `REVOKE_REASON_REQUIRED`；
  - **不看 `share:direct`**：能直接共享不等于能直接收回（风险落在正在用文件的同事身上）；
  - 申请人是审核人时**自审**（`share:approve`）：直接 `approved` + `note=self_approved`，避免「等自己批」死锁；
- 应用撤销：
  - `target=team`：清空组行 + `visibility=personal`；
  - `target=group`：只删该组行；还有别的组 → 保持 `group`（支持部分撤销），撤到最后一组才回 `personal`。
- 并发与删除的交互：存在 `action=revoke` 的 pending 单时，删除请求一律 409 `SHARE_REVOKE_PENDING`；
  撤销期间共享仍有效（同事还能下载）。
- 撤销被拒后共享保持原样，可以再次发起（新理由），不限制次数但要留痕。
- 代码：`sharing._request_revoke / _apply_revoke`，`lifecycle._pending_revoke`。

### T8 删除（软删，移入回收站）

触发：`DELETE /library/assets/{id}?revokeOwnGroupShare=&reason=`，`library:delete`，限流 60/min。

判定顺序（顺序本身是语义的一部分：先判「是不是我的」再判共享，否则 409 会确认 id 存在）：

| 情形 | 非管理员 | 管理员（`library:delete:any`） |
| --- | --- | --- |
| 不是我的资产 | 404（不暴露存在） | 允许，但必须带 `reason`，补自审撤销单 |
| 已在回收站 | 409「已在回收站」 | 同左 |
| 草稿 | 直接进回收站（`trashed_from_state=draft`） | 同左 |
| 个人库 · `visibility=personal` | 允许，直接软删 | 本人 + personal 直接软删；否则带 reason 走兜底 |
| 小组库 · 本人所有 | 409 `SHARED_ASSET_NOT_DELETABLE`，带 `canRevokeAndDelete`；命中窄口并显式传 `revokeOwnGroupShare=true` + `reason` 时一步撤销并删除 | 逐组补自审撤销单后软删 |
| 小组库 · 他人所有 | 404（不可见） | 允许，补自审撤销单 |
| 团队库 · 任何人（含本人） | 409 `TEAM_DELETE_FORBIDDEN` | 补 1 条 `target=team` 自审撤销单后软删 |

**窄口（唯一例外）**，四个条件全满足才算命中：

```
owner_id = me
visibility = group
library_asset_groups 恰好 1 行且该组 ∈ 我所在的组
library_group_leaders 里我是该组组长
state = active
```

命中后在同一事务内：插一条 `revoke` + `approved` + `reviewer=me` + `note=self_leader_combined` 的申请单 →
删组行 + `visibility=personal` → 转 `trash` → 审计 `library.asset.revoke_and_delete`（带申请单 id）。
「一步」只省点击，不省记录。

`_to_trash()` 统一做的写入：清组行 + 强制 personal（不变式 A/B）、`trashed_from_state` 记来源状态、
`trash_reason`、`trash_expires_at = now + 30 天`（`library_trash_retention_days`）、`deleted_at/deleted_by`、
`state_version+1`、审计 `library.asset.delete`。

批量删除：`POST /assets/bulk` `action=delete`，逐条判定，3 条已共享就返回 7 成功 + 3 条明细，不整体回滚。

代码：`lifecycle.LifecycleService.delete / can_revoke_and_delete / revoke_and_delete / _admin_delete / _to_trash`。

### T9 恢复

- 触发：`POST /library/trash/{id}/restore`，本人或 `library:delete:any`。
- 前置：`state` 必须 `trash`，否则 409 `ASSET_NOT_IN_TRASH`。
- 写入：`state = trashed_from_state`（草稿驳回的回到草稿箱）、`visibility` 强制 `personal`（不恢复共享）、
  清空 `trashed_from_state / trash_reason / trash_expires_at / deleted_at / deleted_by`、`state_version+1`、审计。
- 批量：`bulk` `action=restore`。
- 代码：`lifecycle.restore`。

### T10 彻底删除（不可逆）与回收站 GC

- 手动：`DELETE /library/trash/{id}`，本人或 `library:delete:any`；必须已 `trash`。
- 前置：`library-references` 注入口查引用，**fail-closed**（注入口没接上就 503，不把「查不到」当「没人引用」）。
  被引用 → 409 `TRASH_ENTRY_REFERENCED`，`details.references` 给出「封装 X 的成员 / 下载任务 Y」。
- 引用口径：
  - `library_pack_items` 里收录了它——但**排除已在回收站的封装**（刚解散的封装不该继续挡住成员）；
    指向不存在封装的孤儿行仍然算引用（最后一道闸，查不清就当有人在用）；
  - `library_download_jobs`（queued/running/succeeded）里已打包过它。
- 物理删除顺序（`_purge_row`）：
  1. 统计同 sha256 的**其他行**（刻意不带 `deleted_at IS NULL`——问的是「还有谁引用这份字节」，
     不是「有没有未删除的重复行」）；
  2. 删标签关联、组行、`pack_items`（`asset_id` 与 `pack_id` 两个方向）、生成快照；
  3. 删资产行，`WHERE state='trash'` 作为并发恢复的最后一道闸；
  4. 只有 `others == 0` 才删物理字节；同时把借用该 `cover_key/storage_key` 的封装封面置空（避免裂图）；
  5. 审计 `library.asset.purge`，记 `bytesFreed`。
- GC：`library.trash.gc` 小时级扫描（默认 `LIBRARY_TRASH_GC_ENABLED=false`，需手动开），
  每批 200，只扫 `state=trash AND trash_expires_at <= now`；被引用则跳过并计入 `blocked`，不阻断整轮。
- 代码：`lifecycle.purge / purge_expired / _purge_row`，`references.SqlLibraryReferences`，`maintenance.run_trash_gc`。

### T11 批量写

- 触发：`POST /library/assets/bulk`（限流 20，cost=2），`{ids[], action, payload, expectedVersions{}}`。
- 动作与权限：`addTag/removeTag/rename → library:update`，`delete/restore → library:delete`，
  `approveDraft/rejectDraft → library:create`，`requestRevoke → library:share`，`pack → library:create`。
- 规则：
  - 权限按 action 判一次，不逐条判（同批差异只来自资产状态）；
  - 逐条复用单条服务方法，**每条成功立刻提交**；失败 `rollback()` 只清该条半截写入并记明细；
  - 返回 `{succeeded, failed, results[]}`，前端按条标红；
  - `pack` 在 `run()` 里分流：整批建**一个**封装，返回一条结果；成员有一个不可用则整批失败；
  - `purge` 刻意不在批量动作里（不可逆，一次点错十条没救）；
  - 打标签前显式 `_require_writable()`（看得见 + 是我的 / `delete:any`），否则拿到 id 就能改别人的标签。
- 代码：`bulk.BulkService`。

### T12 批量下载（异步打包 + 流式取件）

```
POST /downloads {ids[]}      → 202 {id, status:"queued"}（跳过项只在这次响应里给原因）
  ↓ job "library.download.build"
逐条取字节 → 写 zip 到 library/downloads/ → status=succeeded
GET /downloads/{id}          → 轮询（前端条件轮询，页面隐藏即停）
GET /downloads/{id}/file     → 本地 FileResponse / 远端 307
  ↓ periodic "library.download.gc"
```

- 只打包 `state=active`：草稿与回收站的 id 被跳过并给可读原因，不整单失败。
- 闸门在**展开之后**：请求最多 200 个 id（封装要展开），限制的是展开后的条目数（100）与
  解压后总量（2GB，按 `size_bytes` 求和而不是 zip 实际大小）。
- `ZIP_STORED`（不 deflate）：媒体本身已压缩；zip 内文件名 `序号_原名` 去重。
- 状态机：`queued → running → succeeded / failed → expired`；TTL 24h；
  卡在 queued/running 超 2h 判 `failed`；过期字节删、行留 7 天（用户看到「已过期」而不是 404）。
- 归属：只有 owner（或 `library:delete:any`）能看能取，其余 404。
- 重试 = 新建任务，不把旧任务改回 queued（保留失败次数）。
- 代码：`downloads.DownloadService / build_download / run_download_gc`。

### T13 选择器（统一读接口）

- `GET /library/selection?kind=&tagIds=&q=&includePacks=&page=&pageSize=`，`library:read`。
- 服务端自己算可见集，前端不传 `scope=mine/public`；硬过滤 `state=active`。
- 返回 `asset` 与 `pack` 的统一形状；`includePacks=false` 时**在 SQL 里**排除 `kind=pack`
  （在 Python 里滤 items 会让 `total` 虚高，前端多算一页）。
- 接入点：工作台与建任务弹窗（`referenceAssetIds`）；模板库替换与生图页等 M5 切换时再改。
- 代码：`router.selection`，`service.list_assets(hide_packed_members=False)`。

### T14 标签

- `GET /tags`（read）、`POST /tags`（create，上传者即可建第一个标签）、`PATCH/DELETE /tags/{id}`（`tag:manage`）。
- 资产打标走 `PATCH /assets/{id}`（整体替换 `tagIds`）或批量 `addTag/removeTag`；
  标签改动**不校验版本**（集合操作），改名仍带版本。
- 封装创建自动打「封装」标签。
- 代码：`tags.py`。

### T15 存量导入与观察期对账

- `app library import-archive [--dry-run]`：读旧 `assets` 表 + S3 归档，按 `legacy:<id>` 幂等；
  单条 `IntegrityError` 回滚并记 `skipped_existing`，不打断整批（观察期每天要跑）。
- `app library reconcile`：只读对账，分三桶——`missing`（该有没导）、`orphan`（v2 有旧行已删）、
  `missing_unsupported`（旧行格式不支持，永远跳过）；有缺口退出码非 0。
- `app doctor` 的 `library.legacy_sync` 给同一结论。
- 代码：`import_archive.py`、`cli.py`、`health.py`。

## 5. 生命周期状态机（全图）

```
            生成结果入库(origin=generated)
                      │
                      ▼
              ┌──────────────┐   采纳：本人 / draft:review      ┌──────────────┐
              │    draft     │ ───────────────────────────────→ │    active    │
              │   草稿箱      │                                    │    在库      │
              └──────────────┘                                    └──────────────┘
                      │ 驳回(rejected)                    删除(deleted) │   ↑ 恢复
                      ▼                                                  ▼   │
              ┌───────────────────────────────────────────────────────────────┐
              │                            trash                              │
              │  恢复 → trashed_from_state，强制 personal，不恢复共享            │
              │  30 天到期 / 手动 purge → 物理删除（先过引用校验，被引用则 409）   │
              └───────────────────────────────────────────────────────────────┘

上传(image/audio)、模板原片(template_source) ──→ active（直接进个人库）
```

## 6. 并发与一致性手段

| 场景 | 手段 |
| --- | --- |
| 改名 / 审批 / 批量写 | `state_version` 乐观锁；`expected=0` 表示不校验 |
| 审批重复点击 | `UPDATE ... WHERE status='pending'` 抢占，`rowcount != 1` → 409 |
| 上传重复 / 生成重复回调 | `sourceKey` 幂等 + `(owner, sha256, kind)` 唯一索引 |
| 同字节多行 | 内容寻址，purge 时只有「最后一个引用者」删物理文件 |
| 并发恢复 vs 物理删除 | 删行 `WHERE state='trash'` |
| 批量部分失败 | 逐条提交，失败只回滚该条 |
| 引用检查故障 | fail-closed 503，不允许「查不到=没人用」 |

## 7. 错误码（slot=8，`code = HTTP×100 + 80 + seq`）

| 语义 | HTTP | 触发 |
| --- | --- | --- |
| `SHARED_ASSET_NOT_DELETABLE` | 409 | 资产仍共享到小组就删，响应带 `canRevokeAndDelete` |
| `TEAM_DELETE_FORBIDDEN` | 409 | 删团队库资产（含本人上传） |
| `SHARE_REVOKE_PENDING` | 409 | 撤销审批未通过就想删 |
| `DRAFT_NOT_REVIEWABLE` | 409 | 草稿已被处理（重复采纳/驳回） |
| `ASSET_NOT_IN_TRASH` | 409 | 对非回收站条目调 restore/purge |
| `TRASH_ENTRY_REFERENCED` | 409 | 彻底删除但被封装 / 下载任务引用 |
| `REVOKE_REASON_REQUIRED` | 400 | `action=revoke`、窄口删除、管理员强删没给理由 |
| `DRAFT_REVIEW_FORBIDDEN` | 403 | 审他人草稿但无 `draft:review` |
| `LIBRARY_FILE_TYPE_NOT_ALLOWED` | 400 | 扩展名不在白名单 / 与 kind 不符 / 空文件 |
| `LIBRARY_FILE_TOO_LARGE` | 413 | 超过 `MAX_BYTES` |
| `SHARE_TARGET_INVALID` | 409 | 共享目标不成立（不在该组、已共享、已在团队） |
| `REQUEST_NOT_PENDING` | 409 | 并发重复审批 / 已处理 |
| `DOWNLOAD_NOT_READY` | 409 | 打包任务未完成或已过期 |
| `PACK_ITEM_INVALID` / `PACK_EMPTY` | 400 | 成员不可见 / 嵌套封装 / 空封装 |
| `GENERATION_SOURCE_INVALID` | 400 | 生成入库缺归属人 / 缺 sourceKey / 取不到字节 |
| `DOWNLOAD_EMPTY` / `DOWNLOAD_TOO_LARGE` | 400 / 413 | 无可打包条目 / 超 100 项或 2GB |

## 8. 前后端职责边界

- 后端是唯一判定方：删除矩阵、可见性、审批资格、引用检查全在服务层；
- 前端只做「少看到不该点的」：`deleteOffer()` 按 `ownerId / visibility / state` 给出
  `trash | revoke | force | none`；撞 409 且 `details.canRevokeAndDelete` 为真时，
  才把按钮换成「撤销共享并删除」（理由必填），**不自己重算窄口条件**；
- 上传 / 封装两个主按钮只在「个人」入口出现；小组 / 团队入口不显示删除按钮，只显示「取消共享」；
- 筛选条件全部写进 URL（`view/kind/tags/q/sort/page`），页面不存第二份状态。

## 9. 实现与设计的差异 / 需要留意的点

已确认的偏差（按影响排序）：

1. **通用上传接口接受视频，且前端已放开**（2026-11-19 定案）：`UploadKind` 含 `VIDEO`，
   原先设计与接口注释都说「只收图片与音频」，前端也刻意不给入口，导致「实现能收、
   文档说不能收」两边分裂。**选择承认这条路径**而不是收紧白名单：上传弹窗现在收 `.mp4`，
   落库 `origin=upload`，与「成片」（`origin=generated`）、「模板原片」
   （`origin=template_source`，仍只走 `/source-videos`）三者在来源上可区分。
   格式只 `.mp4`、上限沿用 `MAX_BYTES`（300 MB），与模板库原片同一口径。
   注意：视频与图片走同一条 `_ingest`，首帧封面不依赖来源，上传的视频有封面。
2. **列表隐藏封装成员，但详情仍可直接访问**：`can_read` 不看 `hidden_by_pack_clause`，
   拿到 id 仍能打开被隐藏的成员。这是有意的「隐藏是列表口径，不是权限」，但容易被误当成越权。
3. **审批通过不校验申请单自带的 `expectedVersion`**（只校验资产当前版本）：一张等了很久的旧申请仍能落地。
   因只写 `visibility`，危害小，已记录在 DESIGN §12 末尾。
4. **被移出小组后，自己曾共享给该组的素材在个人库列表看不到**（`owner` 分支只认 `personal`），
   但详情按 owner 仍可读。产品口径未定，未擅自改。
5. **回收站 GC 默认关闭**（`LIBRARY_TRASH_GC_ENABLED=false`）：上线后若忘记开启，
   「30 天自动清理」不会发生，回收站会无限增长。部署清单里要单独确认。
6. **`ensure_tag` 并发建同名标签**会撞唯一索引返回 500（两人同时建封装），概率低、重试即恢复，已记录未修。
7. **远端存储的打包会把整个文件读进内存**（`storage.get()`），2GB 单文件有 OOM 风险；
   当前用本地驱动（零拷贝）暂无实际影响，换 S3 前必须给 `ObjectStorage` 加流式下载。

## 10. 代码地图

| 关注点 | 文件 |
| --- | --- |
| 数据模型（10 表） | `api/src/modules/library/models.py` |
| 可见性 / 读判定（唯一来源） | `library/access.py` |
| 列表 / 分面 / 详情 / 取件 / 上传 | `library/service.py` |
| 共享与撤销审批、组长映射 | `library/sharing.py` |
| 草稿 / 回收站 / 删除矩阵 / 窄口 / purge | `library/lifecycle.py` |
| 批量写 | `library/bulk.py` |
| 封装 | `library/packs.py` |
| 批量下载 job + GC | `library/downloads.py` |
| 生成入库与参数快照 | `library/ingest.py` |
| 引用检查（purge 前置） | `library/references.py` + `asset_references.py` |
| 回收站 GC 周期任务 | `library/maintenance.py` |
| 存量导入 / 对账 / doctor | `library/import_archive.py`、`cli.py`、`health.py`、`observation.py` |
| 路由与权限依赖 | `library/router.py`、`dependencies.py`；权限声明在 `library/__init__.py` 的 `ModuleSpec` |
| 错误码 | `library/errors.py` |
| 前端页面与交互 | `studio/src/features/library/LibraryWorkspace.tsx` 及同目录组件 |
| 前端视图模型（删除按钮、入口） | `studio/src/entities/library/model/types.ts` |
| 前端数据访问 | `studio/src/entities/library/api/library.queries.ts` |
| 模块注册与 mock | `studio/src/modules/library/{module.ts,mock.ts}` |
