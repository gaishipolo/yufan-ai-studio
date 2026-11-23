# 2026-11-23 代码质量审查（合并稿）

> **这是一次快照评审，不是活文档**。行号以 `94153705`（2026-11-22）为准；代码改动后请重新核对证据。
> 本次为**只读审查，未修改任何代码**。
> 本稿合并两次审查的结论：**2026-11-21 全量设计审查**（结构层，见 `REVIEW_2026-11-21_DESIGN.md`）与 **2026-11-23 代码质量审查**（正确性层，本次）。
> 两次审查的分级口径不同（设计侧 H/M/L，代码侧 严重/重要/一般/建议），本稿统一为四档并保留原编号以便回溯。

## 0. 一页结论

设计侧（11-21）的结论是：系统「用机器守约定」做得少见地好，但问题集中在两条主线——**同一概念多处真相**、**约定写在注释里没有机制执行**。

代码侧（本次）跑完同一条主线，得到一个具体印证：**乐观锁这条横切契约在前端被系统性降级为 `0`**，而后端把 `0` 解释成「不校验」。于是审批、批量等跨人协作的写操作**看起来有并发保护，实际没有**。这是本次唯一的严重项，也是最该先修的一条。

| 维度 | 结论 |
|---|---|
| 架构与边界 | 良好。模块边界清晰、契约有字节比对守卫、权限单点求值、迁移有多道闸 |
| 注释质量 | 突出。大量注释记录「为什么」与踩坑历史，可直接当设计文档读 |
| 并发安全 | **有缺口**。乐观锁契约在前端多处失效，形成假安全感 |
| 错误边界 | **有缺口**。原始异常经 `error` 字段回显前端 |
| 输入安全 | **有缺口**。CSV 公式注入转义漏 `-` |
| 缓存一致性 | **有缺口**。查询键漏 `status` 维度（尚未触发） |
| 测试有效性 | 设计侧已指出「守卫测文案不测行为」；本次另发现 `specDraft` 等新增测试同样只断言参数 |

**发现统计**：严重 1、重要 5、一般 7、建议 5。

**建议处理顺序**：先修乐观锁契约（严重+重要#1），再修错误边界与注入转义（重要#2#3），再补缓存键与批量版本（重要#4#5），其余按「数据层 → 流程 → 可读性」排。详见 §6。

---

## 1. 审查范围与方法

### 1.1 覆盖范围

| 层 | 路径 |
|---|---|
| 后端业务模块 | `api/src/modules/export/`（worker / service / sources / schema / router / enums）、`api/src/modules/library/`（sharing / bulk / service / schema / text_service）、`api/src/modules/template_pack/`（case_router / case_service / case_schema） |
| 后端核心 | `api/src/core/config.py`、`api/src/workers.py`、`api/src/db/`（outbox / session） |
| 前端 | `studio/src/entities/template-case/`、`studio/src/features/library/`、`studio/src/features/template-library/`、`studio/src/features/template-specs/`、`studio/src/shared/ui/` |
| 契约 | `studio/src/shared/api/schema.ts`、`api/src/openapi_export.py` |

**未覆盖**：`var/`、`videos/`（运行时产物）、`node_modules` 依赖树、以及 `task` / `model_driver` / `generation` 等模块的逐行阅读（设计侧 11-21 已覆盖其结构层）。本次对这些模块的引用来自 11-21 的结论，未重复取证。

### 1.2 方法

读码 + 定向搜索取证，每条结论都回到源码核对行号；对「疑似缺陷」先查调用点确认是否真实可达，避免把「未使用的路径」当成 bug 上报。

**一处修正说明**：初稿曾判定「前端批量接口从不传 `expectedVersions`」。复核发现 `LibraryWorkspace.tsx:809-817` 的 `requestShare` 路径**确实**传了版本字典；而 `rename` / `approveDraft` / `addTag` 等路径（`LibraryWorkspace.tsx:603`）才没传。本稿已按实际实现改写（见 重要#5）。

---

## 2. 系统性主线（合并两次审查）

### 主线 A · 同一概念多处真相

设计侧列出的副本：生成终态 4 份、模板槽位语法 3 份、HTTP 响应封套 3 份、契约四份里两份读全局单例、标签深度 3 处。

**代码侧的印证**：`expected_version` 的「0 = 不校验」语义在两个模块各有一份约定——`library/service.py:1431-1437`（`_check_version`）与 `library/sharing.py:117,244`（各写一遍同样的 `if payload.expected_version and ...`）。而前端 `ShareRequestModal.tsx:46` 硬编码 `expectedVersion: 0`，等于把「0 = 不校验」这条约定当成默认值在用。**同一个数字在三个地方各有一层含义**，这正是主线 A 的典型形态。

### 主线 B · 约定写在注释里但没有机制执行

| 约定 | 违反情况 |
|---|---|
| 乐观锁：跨人写操作要带 `expected_version` | 注释明确要求（`sharing.py:113-116`），但前端传 0；`ShareRequestOut` 干脆没有 `state_version` 字段，前端**无法**遵守 |
| 可选能力用协议 + isinstance | 约 8 处被 `getattr` 绕过 |
| 权限判定写字面量 | 13 处用 `has(access.PERM_*)` 绕过 AST 守卫 |
| service 必须自己 commit | 只有反向守卫（router 不许 commit），正向空白 |
| feature 之间不许横向 import | ESLint 不覆盖该维度，已发生 7+ 处 |
| 删除前必须知道引用情况 | `LibraryDetail.tsx` 用文案替代能力 |

本次新增一条同类：**「批量写操作要带版本」这条约定没有守卫**。`bulk.py:89` 的 `payload.expected_versions.get(asset_id, 0)` 允许缺省，`LibraryWorkspace.tsx:603` 的调用点就不传，没有任何测试或静态检查会报错。

### 主线 C · 错误边界没有收口

后端对「面向用户的文案」其实有明确约定（`export/service.py:165` 的「任务入队失败，请重新导出」、`:275` 的「导出超时未完成，请重新导出」都是中文用户文案）。但 `export/worker.py:104` 直接 `record.error = str(exc)[:512]`，把开发者口吻的 traceback 片段写进同一个对外字段。**同一模块内两种错误风格并存**，属于约定未被统一执行。

---

## 3. 严重

### 严重#1 · 共享审批的乐观锁被静默绕过，审批人看到的是过期快照

- **位置**：`studio/src/features/library/ShareRequestsModal.tsx:46`；`api/src/modules/library/sharing.py:244`；`api/src/modules/library/schema.py:533-548`
- **描述**：前端审批固定传 `body: { expectedVersion: 0 }`。后端 `decide()` 的判定是：

  ```244:248:api/src/modules/library/sharing.py
  if payload.expected_version and asset.state_version != payload.expected_version:
      raise Conflict(
          "资产已被修改，请刷新后重试",
          details={"stateVersion": asset.state_version},
      )
  ```

  `0` 是 falsy，整个版本分支被跳过。
- **影响**：申请单是快照（代码注释自己也承认这一点，`sharing.py:250-251`）。若资产在申请后被改名/换标签/改了可见性，审批人点「通过」时后端只重跑 `_recheck_target`（校验共享目标是否仍有效），**不校验资产版本**——于是一份基于旧快照的申请照样生效了。同文件 `request()` 路径（`:117`）有版本校验，`decide()` 路径实际没有，两条路径口径不一致。
- **根因**：`ShareRequestOut`（`schema.py:533-548`）**没有 `state_version` 字段**，前端拿不到资产的当前版本，只能填 0。而 `LibraryAssetOut` 是带 `state_version` 的（`schema.py:213`），说明这个字段本来就在契约里，只是没被带到申请单的出参上。
- **改进方向**：
  1. 在 `ShareRequestOut` 补 `state_version`（申请创建时的资产版本快照）；
  2. 后端 `decide()` 改为 `expected = payload.expected_version or row.expected_version`——申请单入库时已经存了 `expected_version`（`sharing.py:160,214`），直接复用即可，让 0 不再等价于「放弃校验」；
  3. 前端回传申请单快照版本。

---

## 4. 重要

### 重要#2 · 案例列表的 React Query 缓存键缺少 `status` 维度

- **位置**：`studio/src/entities/template-case/api/case.queries.ts:15-24`；`case.api.ts:29-36`；`api/src/modules/template_pack/case_router.py:56`
- **描述**：`CaseListParams` 声明了 `status?: string`（`case.api.ts:31`），`caseApi.list` 原样透传，后端也支持 `status` 过滤（`case_router.py:56`：`status_filter: str = Query(default="", alias="status", max_length=16)`）。但缓存键只拼了 `scope / keyword / category / page / pageSize`：

  ```15:24:studio/src/entities/template-case/api/case.queries.ts
  list: (params: CaseListParams) =>
    [
      ...caseKeys.all(),
      'list',
      params.scope ?? 'library',
      params.keyword ?? '',
      params.category ?? '',
      params.page ?? 1,
      params.pageSize ?? 0,
    ] as const,
  ```
- **影响**：任何一处开始传 `status`（例如「已发布/已下架」筛选），不同 status 会命中同一缓存键 → 切换筛选不刷新，或显示上一档数据。当前三个调用点恰好都没传 status（`CaseLibrary.tsx:38`、`CasePickerDialog.tsx:25`、`SpecWorkspace.tsx:406`），所以是**尚未触发的缺陷**——但契约已经铺好，随时会被踩。
- **附带**：键里 `params.pageSize ?? 0` 与后端默认 `page_size=50`（`case_router.py:60`）不一致——不传时键是 `0`、实际返回 50 条；键与请求语义不对齐，将来排查缓存问题会很困惑。
- **改进方向**：`caseKeys.list` 补 `params.status ?? ''`，并把 `pageSize ?? 0` 改为与后端一致的默认值（`?? 50`）。

### 重要#3 · 导出失败原因把原始异常直接回显给前端

- **位置**：`api/src/modules/export/worker.py:100-110`；`api/src/modules/export/schema.py:49`
- **描述**：worker 捕获异常后写：

  ```103:105:api/src/modules/export/worker.py
  record.status = ExportStatus.FAILED.value
  record.error = str(exc)[:512] or type(exc).__name__
  record.attempts += 1
  ```

  而 `ExportOut.error` 是对外字段（`schema.py:49`），随列表/详情接口返回前端。
- **影响**：异常字符串可能含内部文件路径、SQL 片段、数据库连接串、上游依赖地址等敏感信息，等于给前端开了一条信息泄露通道；同时前端拿到的是开发者口吻的 traceback 片段，对用户不可读。
- **对照**：同模块的其它两处错误都用了面向用户的中文文案——`service.py:165`「任务入队失败，请重新导出」、`service.py:275`「导出超时未完成，请重新导出」。只有 worker 这一处用原始异常，口径不一致。
- **改进方向**：`record.error` 只写面向用户的中文文案；完整异常进 `logger.exception`（`:108` 已有）；另加稳定 `error_code` 供前端分流。

### 重要#4 · CSV 公式注入防护漏掉 `-` 前缀

- **位置**：`api/src/modules/export/sources.py:64-74`
- **描述**：

  ```71:74:api/src/modules/export/sources.py
  if isinstance(value, str):
      # CSV 公式注入：让 Excel 把这一格当文本，而不是执行它
      return "'" + value if value[:1] in ("=", "+", "@", "\t", "\r") else value
  ```

  转义集合含 `= + @ \t \r`，**唯独缺 `-`**。`-` 是 Excel / LibreOffice 公式注入的经典触发字符之一（`=cmd|...`、`+cmd|...`、`-2+3+cmd|...`）。
- **影响**：导出 CSV 被用户用 Excel 打开时，攻击者可控的单元格内容（素材名、标签名、审计 detail 等）可能被当公式执行。这是一条「用户自己点开的文件」的注入路径，跨用户边界。
- **改进方向**：转义集合补 `-`。若担心负数被误伤，可只在 `-` 后不跟数字/空白时转义（`-2` 保留，`-cmd` 转义）。

### 重要#5 · 批量写操作只有共享路径带版本，其余写动作无并发保护

- **位置**：`api/src/modules/library/bulk.py:88-89`；`studio/src/features/library/LibraryWorkspace.tsx:603`、`:809-817`；`studio/src/features/library/LibraryBatchShareModal.tsx`
- **描述**：后端逐条取版本，取不到即 0：

  ```88:89:api/src/modules/library/bulk.py
  async def _apply(self, asset_id: str, payload: BulkIn, actor: CurrentUser) -> None:
      version = payload.expected_versions.get(asset_id, 0)
  ```

  `0` 传给单条服务方法后，`_check_version`（`library/service.py:1431-1437`）同样按「不校验」处理。
  前端两条路径行为不同：
  - `requestShare`（`LibraryWorkspace.tsx:809-817`）**正确地**组装并传了 `expectedVersions`；
  - 其余写动作（`rename` / `approveDraft` / `addTag` / `delete` / `restore`，统一走 `LibraryWorkspace.tsx:603`）**不传**。
- **影响**：`BulkAction` 枚举共 9 个（`schema.py:562-570`），其中 8 个走无版本路径。多人协作的团队库里，A 打开列表后 B 改了名，A 执行批量改名会把 B 的修改冲掉且无提示。
- **文档与实现不符**：`LibraryBatchShareModal.tsx` 的注释声称「版本过期的…会在 `BulkOut.results` 里带明细」，但只有共享路径能产生这种明细。
- **改进方向**：前端在 `LibraryWorkspace.tsx:603` 一并组装 `expectedVersions`；后端把 `expected_versions` 对写类动作改为必填或默认启用校验；同步修正 `LibraryBatchShareModal` 的注释。

---

## 5. 一般

### 一般#6 · 批量改名覆盖他人修改的具体路径

- **位置**：`api/src/modules/library/bulk.py:113-117` → `api/src/modules/library/service.py:703-705`
- **描述**：`rename` 走 `AssetPatchIn(name=..., expected_version=version)`，`version` 恒为 0（重要#5），`row.name` 被直接覆盖。
- **影响**：见重要#5。修复后此条自动消解。

### 一般#7 · 导出产物在内存中整体拼装，峰值内存翻倍

- **位置**：`api/src/modules/export/sources.py:77-84`；`api/src/modules/export/worker.py:84-91`；`api/src/core/config.py:388-391`
- **描述**：`render_csv` 用 `io.StringIO` 攒下全部行，最后 `("\ufeff" + buffer.getvalue()).encode("utf-8")`。上限 10 万行（`export_max_rows`），中文场景下 CSV 可达数十 MB，构造过程中同时存在 str 缓冲、拼接后的 str、编码后的 bytes 三份副本。
- **影响**：`config.py:389-390` 明确指出「worker 是共享进程，会连带同一进程里其他模块的 job 一起 OOM」——这正是本条风险的放大器。
- **改进方向**：改用 storage 的流式写入接口（若支持），或分块写临时文件后转存；`render_csv` 增加行数/字节数熔断。

### 一般#8 · 审计写入失败会让接口返回 500，但业务已提交

- **位置**：`api/src/modules/export/service.py:148-176`
- **描述**：`commit()` → 入队 → `audit.record(...)`，审计在最后且未包 try。
- **影响**：审计异常时接口 500，用户以为失败而重试 → 可能产生重复导出记录（`_reject_duplicate` 只拦 ACTIVE 状态的行，失败态不拦）。
- **改进方向**：审计失败只记日志不阻断业务，或改用与业务同一次 commit 的可靠事件表（同项目 `db.outbox` 已有此模式）。

### 一般#9 · 资产详情标签回滚回的是本地缓存而非服务端值

- **位置**：`studio/src/features/library/LibraryDetail.tsx:190-198`
- **描述**：

  ```195:197:studio/src/features/library/LibraryDetail.tsx
  if (variables.body.tagIds) {
    setTagIds(asset.tags?.map((tag) => tag.id) ?? []);
  }
  ```

  取的是组件里 `asset` 快照的标签；失败后没有重新拉取服务端资产。
- **影响**：注释声称「把本地态拉回服务端」，实际只是回到一次成功 invalidate 时的旧快照。若失败原因是标签已被删除，本地仍显示该标签，下一次保存还会把这枚坏标签一起提交——正是注释想避免的情况。
- **改进方向**：`onError` 中先 `refetch`（或 invalidate）再回填，或直接回填服务端返回的冲突详情。

### 一般#10 · CSV 空值与空字符串不可区分

- **位置**：`api/src/modules/export/sources.py:64-66`
- **描述**：`None` → `""`，与真实的空字符串同形。
- **影响**：导出数据无法区分「字段无值」与「字段是空串」，影响下游对账/清洗。
- **改进方向**：按源定义决定 None 的呈现（统一输出 `NULL`，或留空但配独立空值列）。

### 一般#11 · `ShareDecisionIn.expected_version` 的 0 语义与其它模块不一致

- **位置**：`api/src/modules/library/schema.py:526-530`；`library/service.py:1431-1437`
- **描述**：`ShareDecisionIn.expected_version: int = 0`，与 `AssetPatchIn` 等同构，但两处对 0 的解释不同：`service._check_version` 里 0 = 不校验（注释明写「单页操作没必要为了一个数字失败一次」），而共享审批场景需要 0 = 用申请单快照。
- **影响**：同一模块内同一个字段名承载两套语义，是 严重#1 的深层成因。
- **改进方向**：审批路径单独定义语义（见严重#1 方案），或把「不校验」表达为 `Optional[int] = None` 并在审批路径显式回落快照。

### 一般#12 · 后端案例列表的 `status` 是自由字符串，未与枚举对齐

- **位置**：`api/src/modules/template_pack/case_router.py:56`；`studio/src/entities/template-case/api/case.api.ts:31`
- **描述**：后端 `status_filter: str = Query(default="", alias="status", max_length=16)` 是自由字符串；前端 `status?: string` 同样是自由字符串。后端 `scope` 用了 `Literal["library", "mine", "trash"]` 收窄，`status` 没有同等约束。
- **影响**：非法 status 会被当作「无过滤」静默处理（需看 `case_service.py` 的实现确认是否报错），前端收窄为字面量联合类型可提前拦截，减少走到 422/静默忽略的非法值。

---

## 6. 建议

### 建议#13 · `LibraryDetail.tsx:208` 代码格式损坏

```208:208:studio/src/features/library/LibraryDetail.tsx
        onError: (error) => toast(error.message, 'error'),      },
```

`},` 被挤到同一行且缩进错乱，明显是合并/格式化事故。属可读性问题，但会让 review 噪音变大，建议跑一次格式化。

### 建议#14 · `ShareRequestOut` 补申请单自身的快照版本

- **位置**：`api/src/modules/library/schema.py:533-548`
- 与严重#1 同一处修复：在出参里带上 `stateVersion`，审批人也能据此判断申请是否过期。

### 建议#15 · 提交类操作的防重入建议复核

- **位置**：`studio/src/features/template-library/SpecLibrary.tsx:603`、`:693-750`、`:1149-1160`
- 已核实：`busy` 定义为 `compile.isPending || createTask.isPending || generateImage.isPending`（`:603`），三段 mutation 都被覆盖；`ConfirmDialog` 的确认按钮 `loading={busy}`、取消按钮 `disabled={busy}`（`ConfirmDialog.tsx:114,117`），`Button` 的 `loading` 会禁用按钮。
- **残留风险**：`submit()` 是 `compile.mutateAsync` → `createTask.mutateAsync` 串行（`:719-736`），两段之间存在一帧 `busy` 可能短暂为 false 的窗口。窗口极窄、实际难以触发，**列为建议而非缺陷**。
- **改进方向**：若要彻底封死，在 `submit()` 开头加本地 `submitting` 标志做同步防重入（React state 的 `isPending` 有一帧延迟，不能单独承担防重入职责）。

### 建议#16 · 前端类型收窄，与后端枚举对齐

- **位置**：`studio/src/entities/template-case/api/case.api.ts:31`
- `status?: string` 收窄为字面量联合类型，与 `scope` 的处理方式保持一致（见一般#12）。

### 建议#17 · 导出 worker 的 `render_csv` 应移出事件循环关键路径

- **位置**：`api/src/modules/export/worker.py:84`
- `payload = render_csv(source.columns, rows)` 是同步 CPU 密集调用，当前在 worker 的异步处理函数里直接执行。10 万行时该调用会阻塞事件循环。
- **改进方向**：用 `asyncio.to_thread` 包装；与一般#7 的流式改造一并做。

---

## 7. 优先修复建议

| 优先级 | 条目 | 理由 |
|---|---|---|
| **P0 收敛语义** | 严重#1、重要#5、一般#11 | 乐观锁的「0」在三处含义不一。先把语义定死（0 = 用快照 / 不校验改用 `None`），再改调用点。改完这三条，「看起来有保护实际没有」的问题才真正消失 |
| **P1 补边界** | 重要#3、重要#4 | 一处是信息泄露、一处是注入路径，都影响安全边界；改动量小、收益直接 |
| **P2 补一致性** | 重要#2 | 缓存键漏维度；改动两行，防止后续加筛选时踩坑 |
| **P3 数据与流程** | 一般#7、一般#8、一般#9 | 内存、审计阻断、标签回滚；涉及线上行为，各自独立回归 |
| **P4 可读性** | 一般#6、一般#10、建议#13~17 | 跟随上面的修复顺带处理 |

### 建议随 P0 一并做的两件事

1. **补一条守卫**：批量写操作必须带 `expected_versions`（源码扫描或类型层强制），否则修完前端，后端那个 `.get(asset_id, 0)` 的宽松缺省仍会被下一个调用点绕过——这正是设计侧主线 B 的典型形态。
2. **补一条守卫**：对外错误字段不得写入原始异常字符串（可扫描 `record.error = str(exc)` 这类写法）。

---

## 8. 与现有文档的对账

| 已有文档 | 已记录的内容 | 本次新增的角度 |
|---|---|---|
| `REVIEW_2026-11-21_DESIGN.md` | 设计层主线 A/B，H1–H13、M1–M23、L1–L10 | 本稿是同一主线的**正确性层**落地：主线 A 的新实例（`expected_version` 语义三份）、主线 B 的新实例（批量版本无守卫）、新增主线 C（错误边界无收口） |
| `REVIEW_2026-11-19_*` | 视频链、用户权限的具体评审 | 本次不重复，聚焦 export / library / template-case 三条链 |
| `REVIEW_2026-11-15.md` / `REVIEW_2026-11-17.md` | 早期轮次 | 本次不重复 |
| `TODO.md` / `IMPROVEMENT_PLAN_2026-11-18.md` | 已排期的健壮性改进 | 本次新增项均未在上述文档中出现 |
| `LIBRARY_REVIEW_2026-11-16.md` | 素材库专项 | 共享审批的乐观锁缺口（严重#1）是该文档的延续问题 |

---

## 9. 明确判定为合理、不要顺手改的地方

- **权限判定单点且可追溯**：`core/authz.py` 是唯一求值入口，`effective_permissions` 能回答「谁有这个权限、为什么」。
- **他人资源返回 404 而非 403**：不泄露 id 存在性，是正确的安全默认。
- **导出产物下载只认 `object_key` 并二次 `exists()`**（`service.py:106-113`）：产物可能被运维删掉而行还停在 succeeded，处理正确。
- **导出入队必须在 commit 之后**（`service.py:20-21` 的模块注释）：顺序正确，且入队失败已正确置 failed（`service.py:161-168`），不是「永远 queued」。
- **判重不在 SQL 层比较 JSON 列**（`service.py:22-24`）：sqlite 与 postgres 行为差异的规避是对的。
- **`fail_stale` 按 queued/running 分两支判据**（`service.py:245-271`）：避免了「刚被接手的导出被判卡死」的误伤。
- **无 FK 的取舍有书面论证**：切换期回滚不被级联带走，不要为补约束破坏它。
- **确认框的 `busy` 三段覆盖**（`SpecLibrary.tsx:603`）：已覆盖 compile / createTask / generateImage，不是缺陷。

---

## 10. 附：本次未做的事

- 未修改任何代码、未提交、未推送。
- 未对每条发现做完整调用链追踪；各条关键证据已回到源码核对，落地前请按行号复核（尤其严重#1 的修复涉及前后端契约改动）。
- 未做前端真机 / 后端真机验证；判据是源码结构与已有守卫。
- 未重复审查 `task` / `model_driver` / `generation` 模块的逐行实现——这些由 11-21 设计审查覆盖结构层，代码层细节留待专项。
- 一般#12 的「非法 status 是否被静默忽略」依赖 `case_service.py` 的过滤实现，本次未展开确认；前端类型收窄这一改进方向无论如何都成立。
