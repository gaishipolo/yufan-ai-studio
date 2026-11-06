# Assets 页面开发规划（阶段 A 实施版）

日期：2026-11-06  
对象：`/assets`「素材资产库」  
依据：[`ASSETS_REQUIREMENTS.md`](./ASSETS_REQUIREMENTS.md) 的 R01–R07、状态契约与验收场景  
性质：**实施规划**。本文把需求条目落到具体文件、状态机与验收用例；不修改业务代码，不改接口契约。本文新增的代码事实均来自当前工作区静态检查，未启动应用。

## 1. 实施范围

| 范围 | 内容 | 本文处理方式 |
| --- | --- | --- |
| 本轮必做（A） | R01–R07：可判断、可恢复 | 拆成 A1–A7 七个任务，逐项给出文件与判据 |
| 紧随其后（B） | 客户端搜索、规格补全、结果区任务信息 | 只给接口与文件落点，不排入 A |
| 明确不做 | 删除、共享、素材包创建、服务端分页、自动幂等 | 不设计，不预留半成品代码 |

阶段 A 的完成定义：**用户能分辨「成功了 / 失败了 / 还不知道」，并且每一种失败都有明确下一步。** 不是"页面不报错"。

## 2. 现状核对：代码事实与补充发现

需求文档第 3、4 节与代码一致。静态复查另外确认了 5 处会影响实现的细节，A 阶段必须一并处理：

| 编号 | 代码事实 | 对实现的影响 |
| --- | --- | --- |
| C01 | `ArchiveLibrary.tsx` 共 79 行，同时承担分类、上传循环、inbox 表单、列表三态、卡片、弹窗、结果区挂载 | 不改结构无法加入批次状态；必须先拆文件，否则所有改动都堆在一个组件里 |
| C02 | 模板页 `SourceHoverPreview.tsx` 已用 `archivePreviewUrl(archiveStreamUrl(video.url))` 播放视频，`SourceTemplateCard.tsx` 用 `archivePreviewUrl(previewUrl)` 取缩略图 | 兼容预览的写法在仓库内已有先例，Assets 直接对齐即可，不需要新造工具函数 |
| C03 | `ArchiveLibrary` 给缩略图传的是**未经 `?v=hd-v2` 的** `previewUrl`，模板页加了版本参数 | 同一张缩略图在两个页面走两套浏览器缓存，改图后可能一边新一边旧；A2 统一 |
| C04 | 后端 `ensure_preview` 只对能生成封面的媒体写 `previewUrl`，其余为 `null`；缩略图接口本身也可能 404 | 「没有封面」和「封面加载失败」是两种状态，卡片不能都显示"加载预览…" |
| C05 | `ReplacementTaskHistory.tsx` 已实现"有缓存时错误不遮列表 + 只在成功且无记录时空态" | 列表三态有现成的仓库内范式，照抄语义，不新立一套文案规则 |

另外两条环境事实，影响验收方式：

- `VITE_USE_MOCK` 的 mock 适配器**没有实现** `/assets/archive*`。A 阶段的联调必须连真实后端；mock 模式下本页必然失败，这反过来要求错误态本身必须准确（不能用空态兜住）。
- 模板页当前不支持 `?run=<id>` 深链，`ReplacementTemplates` 不读该参数。结果区只能链到 `/templates` 概览，精确跳转属 B 阶段。

## 3. 目标文件结构

只新增文件、不改骨架（`shared/ui` 现有组件的 props 不动）。

```
studio/src/features/archive-library/
├─ ArchiveLibrary.tsx          容器：URL 分类/关键词、查询编排、互斥域、把各块拼起来
├─ ArchiveToolbar.tsx          分类切换 + 搜索(B) + 统计 + 上传入口 + 批次分类提示
├─ ArchiveUploadPanel.tsx      批次逐条结果、重试/跳过/核对未知
├─ ArchiveImportForm.tsx       inbox 登记独立成块，自带进行中/错误态
├─ ArchiveGrid.tsx             列表三态（首次加载/首次错误/刷新失败）+ 卡片网格
├─ ArchiveCard.tsx             单卡：封面、规格、适用性徽标
├─ ArchiveDetail.tsx           弹窗内容：预览、规格、下载、归档信息折叠区
├─ ArchivedResults.tsx         改造现有文件：权限、三态、任务信息
└─ model/
   ├─ upload-batch.ts          纯函数：reducer、错误分类、汇总（无 React，可单测）
   └─ use-upload-batch.ts      zustand store + 串行 runner（跨路由保留进度）

studio/src/entities/asset/
├─ api/archive.api.ts          + download(url) 取原件字节
├─ api/archive.queries.ts      + useArchiveDownload() 或返回 {download, state}
├─ model/archive-fit.ts        + replacementFitness(asset) 纯函数
├─ model/archive.types.ts      + PREVIEW/KIND 文案常量（保持现有导出不变）
├─ ui/ArchivedMedia.tsx        改：显式接收 previewUrl，预览/下载分离，错误可重试
└─ ui/ArchivedThumbnail.tsx    从 ArchivedMedia 拆出，独立处理加载失败与 null 封面
```

`features/replacement-templates/replacementOptions.ts` 里的 `STATUS`、`REVIEW_LABELS` 需要下沉到 `entities/template/model/replacement.labels.ts` 再在原处 re-export —— `features/archive-library` 不能 import `features/replacement-templates`（`CODE_STYLE` 1.2 单向依赖）。这是 A5 的前置小改动。

## 4. 关键设计

### 4.1 列表：三态分离与统计口径

现在 `error ? … : isLoading ? … : !items.length ? 空态 : 网格` 的问题是：**错误优先于数据**，而且 `?? 0` / `?? 'Z:\\yufan_ai_studio'` 把"没拿到"伪装成"真的是零"。

判定顺序改为：

```ts
const { data, error, isPending, isFetching, refetch } = useArchiveAssets(category);
const firstLoad = isPending && !data;          // 无缓存首次
const firstError = !!error && !data;           // 无缓存失败
const staleError = !!error && !!data;          // 有缓存但更新失败
const hasData = !!data && !firstError;
```

| 分支 | 渲染 | 统计 |
| --- | --- | --- |
| `firstLoad` | `CardSkeletonGrid`（复用） | `—` |
| `firstError` | `ErrorState` + `onRetry=refetch`，标题「素材读取失败」 | `—` |
| `staleError` | 顶部细条「更新失败，以下为上次加载的素材」+ 重试；列表照常渲染 | 显示上次数值，并注明「上次」 |
| 正常空 | `EmptyState`，按权限分别给上传入口或联系维护人 | `0 项 · 0 B`（只有此时允许出现 0） |
| 正常 | 网格 | `当前分类 N 项 · 合计 X` |

规则：**`0` 和 `0 B` 只能出现在 `data` 成功返回且确实为空时**；`root` 同样改为 `data?.root ?? '—'`，不再用默认路径兜底。统计措辞固定为「当前分类 N 项 · 合计 X」，不写成磁盘占用。

刷新提示用 `aria-live="polite"` 的小字，不用 toast；后台刷新不清空列表（`staleTime: 30_000` 已够，不引入 `keepPreviousData`——切分类时保留旧分类的卡片会造成归属误读）。

### 4.2 上传批次状态机

这是本轮唯一需要新造机制的部分。核心是**把"整体 toast"换成"逐条事实"**。

```ts
type ItemStatus =
  | 'queued'     // 尚未处理
  | 'uploading'  // 请求在途
  | 'success'    // 服务端已确认，有 assetId
  | 'failed'     // 服务端明确拒绝，同一文件可安全重发
  | 'unknown'    // 超时/断连/5xx，可能已落盘，禁止直接重传
  | 'skipped';   // 用户主动跳过

interface BatchItem { key: string; file: File; status: ItemStatus; assetId?: string; error?: string }
interface Batch { id: string; category: ArchiveCategory; startedAt: number; items: BatchItem[]; phase: 'running' | 'paused' | 'done' }
```

`key` 用 `crypto.randomUUID()` 生成，**不能用文件名**——同名多选是合法的，用文件名当 key 会让第二条覆盖第一条的状态。

转换表（`model/upload-batch.ts` 的纯 reducer）：

| 事件 | 前置 | 结果 |
| --- | --- | --- |
| `create(files, category)` | 无在途批次 | 全部 `queued`，`phase=running`，分类与 `startedAt` 快照 |
| `begin(key)` | `queued` | `uploading` |
| `succeed(key, assetId)` | `uploading` | `success`，继续下一条 |
| `reject(key, msg)` | `uploading` | `failed`，`phase=paused`，其余保持 `queued`（显示「尚未处理」） |
| `unknown(key, msg)` | `uploading` | `unknown`，`phase=paused` |
| `retry(key)` | `failed` | 回到 `queued`，`phase=running`，从该项继续 |
| `skip(key)` | `failed` / `unknown` | `skipped`，`phase=running`，继续后续 |
| `confirmArchived(key, assetId)` | `unknown` | `success`（由用户核对后确认，不自动判定） |
| `dismiss()` | 无 `uploading` | 清空批次 |

串行 runner 保持"首错暂停"，但**暂停不等于丢弃**：已成功项保留，失败项可单项重试，后续项明确标为未处理。

```ts
for (;;) {
  const item = nextQueued(batch);
  if (!item) return finish();
  markUploading(item.key);
  try {
    const asset = await archiveApi.upload({ file: item.file, category: batch.category });
    markSuccess(item.key, asset.id);
  } catch (error) {
    classifyUploadError(error) === 'failed'
      ? markFailed(item.key, messageOf(error))
      : markUnknown(item.key, messageOf(error));
    return; // 暂停，等用户决策
  }
}
```

**不逐条 invalidate。** 现在每调一次 `mutateAsync` 就 invalidate 整个 owner 前缀，5 个文件等于 5 次全量列表重取。批次进入 `paused` 或 `done` 时统一 invalidate 一次；归档成功与列表刷新失败是两件事，UI 分别报告（对应需求文档「归档成功但列表刷新失败」用例）。

**状态放 zustand，不放组件 state。** 放在 `use-upload-batch.ts` 的模块级 store：否则用户中途点侧栏再回来，"已成功 3 个、失败 1 个"全部消失，正好违反 R02。store 只存内存，不做持久化（刷新页面丢失是可接受的，因为服务端事实仍在，列表能查到）。

### 4.3 明确失败 vs 结果未知

现有 `ApiError.code` 已经是 HTTP 语义（`errorHttpStatus(code)` 取 `code // 100`），直接复用，不新增错误类型：

| 判定 | 状态段 | 依据 | 处理 |
| --- | --- | --- | --- |
| 明确失败 | 4xx（408 除外） | 请求已到达并被业务规则挡下（类型不符、超限、越权） | 允许「重试此项」，重发同一文件安全 |
| 结果未知 | 408 / -1 / 5xx | 超时、断连或服务端在落盘前后出错 | 只能「刷新并核对」，禁止直接重传 |

```ts
export function classifyUploadError(error: unknown): 'failed' | 'unknown' {
  if (!(error instanceof ApiError)) return 'unknown';
  const status = errorHttpStatus(error.code);
  return status >= 400 && status < 500 && status !== 408 ? 'failed' : 'unknown';
}
```

文案对齐仓库已有说法：模板任务的 `submission_unknown` 标签是「提交结果未知 · 禁止重提」，本页用「此文件归档结果待核对」，语义一致。

### 4.4 未知结果的核对流程

没有幂等标识，就不能承诺"重传不重复"。核对只提供证据，不自动下结论：

1. 点「刷新并核对」→ `invalidateQueries` + `refetch`。
2. 在最新列表里按 `name === file.name && sizeBytes === file.size && createdAt >= batch.startedAt` 找候选，展示「疑似已归档：名称 · 时间」。
3. 用户二选一：**「确认是这条」**（`confirmArchived`，标成功，不重传）或 **「不是，重新上传」**（显式转 `queued`）。
4. 找不到候选时只给「未找到匹配记录，可重新上传」，不写"未保存"——服务端索引可能延迟。

同名同大小是启发式，所以必须由人确认；这一点写进按钮文案，而不是只写在文档里。

### 4.5 互斥与分类锁定

`phase !== 'done'` 期间：

- 分类按钮 `disabled` + `aria-disabled`，旁边固定显示「本批次归档到：原片」，与 URL 当前分类分开呈现；
- 上传按钮、inbox 表单、文件输入全部禁用（不只是 `loading`）；
- 批次面板显示「正在归档第 X / N 个：文件名」，不用百分比（无法从 axios 拿到可靠进度）；
- 批次结束后才允许新的分类切换与登记。

浏览器后退可能改变 URL 的 `category`。此时**以批次快照为准**渲染批次面板，不因 URL 变化改写正在进行的归档目标；批次结束再把视图对齐回 URL，并提示「已切换到 X 分类」。

inbox 登记与上传共用一个互斥域：任一在途时另一个不可发起。登记成功才清空输入，失败保留路径。

### 4.6 预览、缩略图与下载

三件事必须分开，现在它们共用一个 blob：

| 用途 | 地址 | 失败时 |
| --- | --- | --- |
| 视频预览 | `archivePreviewUrl(archiveStreamUrl(asset.url))`（`?v=hd-v2`） | 提示「浏览器无法播放此预览（编码不兼容）」，原件下载仍可用 |
| 图片/音频预览 | 原件 `asset.url` | 区分读取失败与解码失败 |
| 缩略图 | `archivePreviewUrl(asset.previewUrl)` | `previewUrl == null` → 类型占位图标，**不显示"加载中"**；有地址但失败 → 占位 + 「预览不可用」 |
| 下载 | 新增 `archiveApi.download(asset.url)` → Blob → `<a download>` | 独立错误，不影响预览 |

关键约束：

- 下载**必须**走 `/file`（原件），预览走 `/stream`（派生）。派生文件绝不能作为"下载原始文件"的来源。
- 缩略图组件不能包可点击的重试按钮：卡片本身已经是 `<button>`，嵌套交互元素是非法的 HTML，也会让键盘焦点错乱。缩略图失败只降级为占位，重试入口放在详情弹窗。
- `onError` 只说明解码失败，不能推断文件丢失；文案不写"文件已损坏"。
- C03 的缓存版本不一致顺手修掉：Assets 的缩略图也走 `archivePreviewUrl`。

### 4.7 详情弹窗

现在 `selected = data?.items.find(...)`，列表一刷新、对象引用变了或该项消失，弹窗内容就凭空消失且没有解释。改为**点击时快照**：

```ts
const [selected, setSelected] = useState<ArchivedAsset | null>(null);
```

- 弹窗内容与列表刷新解耦，后台刷新不打断正在看的内容；
- 每次列表更新后检查快照 id 是否仍存在：不存在则关闭弹窗并在页面顶部提示「该素材已不在当前分类列表中」，而不是静默消失；
- 弹窗内分区：预览 → 规格（分类/格式/尺寸/时长/音轨/创建时间，未知显示「未知」，不写 `0×0`）→ 适用性 → 「下载原件」→ `<details>` 折叠的归档路径与 SHA-256。

`Modal` 已实现焦点接管与 Esc 关闭（`shared/ui/Modal.tsx`），直接复用，不重复实现。

### 4.8 生成结果区

现在 `const {data} = useReplacementCatalog()`，无权限、加载中、失败全都落到「暂无已下载的生成结果」。改造点：

1. `useReplacementCatalog(options?: { enabled?: boolean })` 增加开关；`enabled: useCan('template:read')`，无权限时**不发请求**，渲染 `ForbiddenState` 或等价说明。
2. 三态：`isPending && !data` → 骨架；`error && !data` → `ErrorState` + 重试；`error && data` → 保留旧结果 + 更新失败提示；空态只在 `data && results.length === 0` 时出现。
3. 卡片补 `createdAt`、`STATUS[run.status]`、`人工验收：{REVIEW_LABELS[run.reviewStatus ?? 'pending']}`、`taskId`。文案沿用下沉后的标签表，避免第二套状态词。
4. 「成片已保存」≠「验收通过」：状态与验收分两行显示，颜色不承担唯一语义。
5. 任务入口：当前模板页不读 `?run=`，本轮链到 `/templates`，按钮文案写「前往模板库查看任务」，不承诺直达某条任务。

### 4.9 替换适用性

`entities/asset/model/archive-fit.ts` 纯函数，只用已有字段：

```ts
export function replacementFitness(asset: ArchivedAsset):
  { ok: true } | { ok: false; reason: string } {
  if (asset.category !== 'source') return { ok: false, reason: '仅原片分类参与替换' };
  if (asset.type !== 'video') return { ok: false, reason: '替换需要视频原片' };
  if (!asset.hasAudio) return { ok: false, reason: '缺少音轨' };
  if (asset.duration < 1 || asset.duration > 15) return { ok: false, reason: '时长需在 1–15 秒' };
  return { ok: true };
}
```

用于卡片徽标（「可用于替换」/ 具体原因）和详情说明。**不拦截上传**，也不改后端校验：模板准备阶段仍是最终门禁，页面文案只做提前告知。页面顶部那句「原片要求带音轨的 1–15 秒 MP4」同时改掉——它把归档条件说成了生成条件。

### 4.10 可访问性

| 项 | 要求 |
| --- | --- |
| 分类切换 | 现有 Button 加 `aria-pressed`；是否有「全部」入口属 B 阶段决策，A 不加 |
| 统计与刷新提示 | `aria-live="polite"`，避免整块列表被反复播报 |
| 批次状态 | `role="list"`/`listitem`，每条文字含状态词（不只靠颜色/图标） |
| 错误 | 复用 `ErrorState`（已带 `role="alert"`） |
| 上传按钮 loading | `aria-busy` + 保持可聚焦，不因 `disabled` 丢失焦点位置 |
| 弹窗 | 复用 `Modal` 的焦点进入/归还；关闭后焦点回到触发卡片 |

## 5. 接口与契约影响

| 项 | A 阶段 | B 阶段 |
| --- | --- | --- |
| 归档列表 | 不改，`category` 参数够用 | 需 `keyword`/`page`/`pageSize` 与匹配总数；同步 `archive_schema.py`、`schema.ts`、`api/docs/API.md` |
| 归档上传/登记 | 不改，无幂等标识（不假装有） | 若做可靠自动恢复，另立请求标识与重复返回语义 |
| 兼容预览 | 不改，复用 `/stream` 与 `?v=hd-v2` | 无 |
| 生成结果 | 前端加 `enabled` 开关，不改后端 | 模板页支持 `?run=` 才算真正可追溯 |
| 下载 | 复用 `/file`，只改前端取流方式 | 无 |

A 阶段后端零改动，因此验收只需 `npm run verify`（typecheck + lint + test + build），不需要跑 pytest。若后续动 `archive_schema.py`，必须同一次提交更新前端类型与 OpenAPI。

### 5.1 模板库对本页的隐含依赖（改接口前必读）

模板库不是通过接口"订阅"素材，而是在浏览器里对 `useArchiveAssets('all')` 的全量结果做 join：

- 原片列表：`videos = items.filter(category === 'source')`（`useReplacementWorkbench.ts:47`），只筛分类与名称，**不筛时长和音轨**；
- 素材包可用性：`isReplacementPackAvailable(pack, items)` 要求 `referenceIds` 里每个 id 都能在 `items` 中找到，且分类、类型匹配；
- 素材包主图：`replacementPackCoverId(pack)` → 再回 `items` 找对应素材。

因此：

1. **归档列表一旦改服务端分页，模板库会立刻误判。** `items` 只剩当前页时，多数素材包会显示「素材待完善」，封面也会变成占位。B 阶段做分页必须同步提供「按 id 批量取素材」的接口（或让后端在 pack 内联素材摘要），不能只改 `/assets/archive`。
2. **模板库的合规提示是第二份实现。** `SourceTemplateCard` 已用内联条件（`duration` + `hasAudio`）禁用不合规原片并给出原因，功能上是对的，但规则与后端 `_validate_inputs` 各写一遍。迭代 1 已改为共用 `replacementFitness()`，素材库与模板库从此同一条判定。
3. **素材包没有发布入口。** 全仓库只有 `api/scripts/import_legacy_replacement_assets.py` 写 `catalog/packs`，无 API、无 UI。这正是「上传图片 ≠ 发布素材包」的根因，也是 C 阶段要补的能力，不能靠 Assets 页文案解决。
4. **每次 `prepare` 都复制原片与参考图字节**到 `runs/…/inputs/`，而 `usedBytes` 只统计索引条目。向用户解释"占用空间"时不能拿页面数字当磁盘用量。

若要新增「从素材去模板」的入口，需要模板页支持 `?source=<assetId>`：读取后校验该 id 在 `videos` 中且 `replacementFitness().ok`，命中则预选，不命中给出原因而非静默忽略；切换 `kind` 时还要重新校验分类。

## 6. 任务拆分

按可独立合并、可独立验收切分，不按"前端/后端"切。

| ID | 任务 | 主要文件 | 完成判据 | 规模 |
| --- | --- | --- | --- | --- |
| A1 | 列表三态与统计口径 | `ArchiveGrid.tsx`、`ArchiveLibrary.tsx`、`ArchiveToolbar.tsx` | 首次错误不显示空态与 0；有缓存时错误不遮列表；统计只在上限成功后出现 | M |
| A2 | 预览/缩略图/下载分离 | `ArchivedMedia.tsx`、`ArchivedThumbnail.tsx`、`archive.api.ts` | 视频走 `/stream`，下载走 `/file`；封面 null 与失败两种情况可区分 | M |
| A3 | 批次状态机（纯逻辑） | `model/upload-batch.ts`、`model/use-upload-batch.ts` | reducer 单测覆盖 9 个事件；错误分类 4xx/408/-1/5xx 判定正确 | M |
| A4 | 批次 UI、互斥与核对 | `ArchiveUploadPanel.tsx`、`ArchiveImportForm.tsx`、`ArchiveToolbar.tsx` | 三文件第二个失败 → 1 成功 / 1 失败 / 1 未处理；续传只发 1 次请求；未知项无直接重传 | L |
| A5 | 结果区权限与三态 | `ArchivedResults.tsx`、`replacement.queries.ts`、`replacement.labels.ts` | 无 `template:read` 时不发请求；空态不与错误混淆 | S |
| A6 | 适用性标注 | `archive-fit.ts`、`ArchiveCard.tsx`、`ArchiveDetail.tsx` | 缺音轨/超时长给出具体原因；归档仍可成功 | S |
| A7 | 可访问性与回归 | 上述组件 + 新增测试 | 键盘可达全部操作；7 条用例通过 | M |

依赖关系：A1 前置（决定容器结构）；A3 → A4；A2 与 A3 可并行；A5 独立；A6 依赖 A2 的卡片结构；A7 收口。

## 7. 验收用例

需求文档第 9 节的 13 个场景照搬，落到可执行的验证手段：

| 场景 | 验证方式 | 通过标准 |
| --- | --- | --- |
| 首次失败 / 有缓存刷新失败 | 单测（mock 两态） | 不出现 0 与空态；缓存列表保留；重试可恢复 |
| 三文件第二个失败 | 单测（mock 三次调用，第二次 reject 400） | 第 3 次请求未发出；重试只发 1 次 |
| 超时且可能已保存 | 单测（reject 408） | 标 `unknown`；无「重传」按钮；有「刷新并核对」 |
| 归档成功但列表刷新失败 | 单测 | 归档成功与列表更新失败分别呈现，不提示重传 |
| 上传期间切换分类 | 单测 + 手测 | 分类按钮禁用；批次面板显示快照分类 |
| 视频预览与下载 | 单测（断言请求 URL） | 预览含 `/stream?v=hd-v2`，下载命中 `/file` |
| 无 `template:read` | 单测 | catalog 请求数为 0，显示权限说明 |
| 封面缺失 / 解码失败 | 手测（真实文件） | 不出现永久「加载预览…」；不出现"文件损坏"结论 |
| 只读用户 | 手测（无 `asset:create` 账号） | 上传、登记入口均不渲染，空态不引导上传 |
| 归档盘不可用 | 手测（断 Z 盘） | 明确报不可用，不出现归档成功 |

测试沿用仓库习惯，与组件同目录：`features/archive-library/upload-batch.test.ts`、`ArchiveLibrary.test.tsx`，用 `vitest` + `@testing-library/react`（已在 devDependencies）。

## 8. 风险与对策

| 风险 | 影响 | 对策 |
| --- | --- | --- |
| 批次状态放组件内，切路由即丢 | 用户重新进来无法判断哪些已归档 | A3 用 zustand 模块级 store |
| 逐条 invalidate 造成刷新风暴 | 大文件批次下反复拉全量列表 | 批次暂停/结束时统一 invalidate |
| 未知结果靠 name+size 匹配 | 可能误判为已归档 | 只作建议，必须用户确认；不自动改状态 |
| 600s 超时 + 无进度事件 | 大文件时界面像卡住 | 逐条状态 + 已用时提示；不编造百分比 |
| mock 无 archive 接口 | 在 mock 模式开发必然见错误态 | 本页联调连真实后端；错误态本身按 A1 验收 |
| 模板页不支持 `?run=` | 结果区无法精确定位任务 | 本轮只链到 `/templates`，B 阶段再补深链 |

## 9. 待确认决策

进入编码前只需确认以下 4 项，其余按本文默认执行：

| 决策 | 本文默认 | 若改变的影响 |
| --- | --- | --- |
| 原片是否只接受可替换素材 | 允许归档合法 MP4，另标适用性 | 改为硬门禁需同时改上传、登记、历史素材提示与后端校验，超出 A 范围 |
| 批次进度是否跨路由保留 | 保留（zustand） | 改为组件内 state 可少一个文件，但用户切页即丢失进度 |
| 是否把客户端搜索并入 A | 不并入，A 只做闭环 | 并入需处理"搜索空"与"分类空"两套空态，A 体量上升 |
| 素材包入口 | 仅文案说明"上传不等于发布" | 若要页内发布流程，属独立需求（C 阶段） |

## 10. 建议实施顺序

1. **A1 + A5**（一周内可合并的最小闭环）：先把"错误不再伪装成空/零"和"无权限不再伪装成没有结果"修掉，这两处是当前最容易误导用户的点。
2. **A2**：预览与下载分离，解决 HEVC 归档成功却播不了的问题。
3. **A3 → A4**：批次状态机先行落地并单测，再挂 UI；这是本轮唯一的结构性新增。
4. **A6 → A7**：适用性文案与可访问性收口。

B 阶段（搜索、规格、任务追溯）在 A 合并、真实素材规模确认后再评估是否动后端契约。
