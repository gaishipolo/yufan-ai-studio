# Assets 迭代 1 实施计划：让页面说的每句话都可信

日期：2026-11-06  
关联：[`ASSETS_REQUIREMENTS.md`](./ASSETS_REQUIREMENTS.md)（R01/R04/R05/R06）、[`ASSETS_DEVELOPMENT_PLAN.md`](./ASSETS_DEVELOPMENT_PLAN.md)（A1/A2/A5/A6）  
规范依据：[`CODE_STYLE.md`](./CODE_STYLE.md)、[`REVIEW_CHECKLIST.md`](./REVIEW_CHECKLIST.md)、[`FRONTEND_EXTENSION.md`](./FRONTEND_EXTENSION.md)

## 1. 迭代目标

**一句话**：素材页不能再把"没读到"说成"没有"，把"播不了"说成"已归档"，把"没权限"说成"没结果"。

验收只看三件事：

1. 请求失败时，用户看到的是失败和重试入口，不是"0 个文件"；
2. 视频在浏览器里播不了时，用户知道这是解码问题，并且仍能下载原件核对；
3. 没有 `template:read` 的账号，页面不替他去请求模板目录，也不装作"暂无结果"。

这三个目标都不新增后端接口，不改变数据归属规则，不改归档盘布局。

## 2. 范围

| 纳入 | 内容 | 对应编号 |
| --- | --- | --- |
| ✅ | 列表五分支（首次加载 / 首次错误 / 刷新失败 / 成功空 / 正常）与统计口径 | A1、R01 |
| ✅ | 预览走 `/stream?v=hd-v2`、下载走 `/file`，缩略图缺失与失败分开 | A2、R04 |
| ✅ | 详情弹窗快照化、规格补全、归档信息折叠 | A1/A2 附带 |
| ✅ | 源片适用性标注（缺音轨 / 超时长），不拦截归档 | A6、R05 |
| ✅ | 结果区按权限请求 + 三态 + 生成/验收分开 | A5、R06 |
| ❌ | 上传批次状态机、逐条结果、分类锁定 | 迭代 2（A3/A4） |
| ❌ | 搜索、分页、素材包发布、删除 | 迭代 3 / 未立项 |
| ❌ | 任何后端改动 | 本迭代前端零契约变更 |

## 3. 交付物

新增：

```
studio/src/entities/asset/model/archive-fit.ts          适用性纯函数
studio/src/entities/asset/ui/ArchivedThumbnail.tsx      缩略图独立组件（缺失/失败可区分）
studio/src/entities/template/model/replacement.labels.ts 任务状态与验收标签（下沉）
studio/src/features/archive-library/ArchiveToolbar.tsx  分类 + 统计 + 上传入口
studio/src/features/archive-library/ArchiveImportForm.tsx inbox 登记（就地反馈）
studio/src/features/archive-library/ArchiveGrid.tsx     列表五分支
studio/src/features/archive-library/ArchiveCard.tsx     卡片 + 适用性徽标
studio/src/features/archive-library/ArchiveDetail.tsx   详情弹窗
```

修改：

```
entities/asset/api/archive.api.ts            + download(url)
entities/asset/api/archive.queries.ts        + useArchiveDownload()
entities/asset/ui/ArchivedMedia.tsx          预览/下载分离、解码失败提示
entities/template/api/replacement.queries.ts + enabled 开关
features/archive-library/ArchiveLibrary.tsx  改容器（原 79 行拆为编排）
features/archive-library/ArchivedResults.tsx 权限 + 三态 + 任务信息
features/replacement-templates/replacementOptions.ts  改为 re-export 标签
features/replacement-templates/SourceTemplateCard.tsx 缩略图 import 调整
```

新增测试：`archive-fit.test.ts`、`ArchiveLibrary.test.tsx`、`ArchivedResults.test.tsx`。

## 4. 行为契约（实施判据）

| 区域 | 条件 | 必须呈现 | 禁止 |
| --- | --- | --- | --- |
| 列表首次加载 | `isPending && !data` | 骨架 + 「正在读取素材…」，统计 `—` | 空态、`0` |
| 列表首次错误 | `error && !data` | 错误标题 + 后端 message + 重试 | 空态、`0`、默认路径当连接凭证 |
| 后台刷新 | `isFetching && data` | 保留列表 + 轻量「更新中」 | 清空列表 |
| 刷新失败 | `error && data` | 「更新失败，以下为上次加载的素材」+ 重试 | 显示成功更新 |
| 成功为空 | `data && !items.length && !error` | 分类化空态；有创建权限给上传，无权限给维护提示 | 在错误时出现 |
| 统计 | 仅 `data` 存在时 | 「当前分类 N 项 · 合计 X」 | 称作磁盘占用 |
| 缩略图缺失 | `previewUrl == null` | 类型图标 | 「加载预览…」 |
| 缩略图失败 | 请求/解码失败 | 类型图标 + 「预览不可用」 | 永久加载态 |
| 视频预览 | 详情打开 | `/stream?v=hd-v2` | 用原件地址播 |
| 下载 | 点击 | `/file` 取原件字节 | 下载派生预览 |
| 解码失败 | `onError` | 「无法播放/解码，原件仍可下载」 | 「文件已损坏」「归档失败」 |
| 详情失效 | 刷新后该 id 不在列表 | 关闭弹窗 + 页面说明 | 静默消失 |
| 结果无权限 | 无 `template:read` | 权限说明，请求数为 0 | 空态 |
| 结果三态 | 各自独立 | 加载 / 错误+重试 / 成功且空 | 错误显示成空 |

## 5. 规范符合性自查点

按 `CODE_STYLE.md` 与 `REVIEW_CHECKLIST.md` 逐条对： 

| 条款 | 本迭代做法 |
| --- | --- |
| CODE_STYLE 1.2 单向依赖 | 拆出的组件都在 `features/archive-library`；标签下沉到 `entities/template/model`，避免 `features → features` |
| CODE_STYLE 3.1 类型 | 不用 `any`；可选字段用 `?? 0` / `?? '未知'` 收窄，不做 `as` 断言 |
| CODE_STYLE 3.2 状态边界 | 服务端数据只在 React Query；分类在 URL；批次状态留给迭代 2 的 zustand |
| CODE_STYLE 3.4 组件 | 一文件一组件，容器只做编排；单文件均 < 200 行 |
| CODE_STYLE 3.5 样式 | 条件类走 `cn()`，颜色取 `text-ink*` / `Chip` token，无新增裸十六进制 |
| CODE_STYLE 3.7 可访问性 | 分类按钮 `aria-pressed`；统计 `aria-live`；批次/结果状态不只靠颜色 |
| FE-03 请求位置 | 组件只调 hook 与 `archiveApi`，不 import `http` |
| FE-05 queryKey | 复用 `archiveKeys` / `replacementKeys` 工厂，失效范围仍按 owner 前缀 |
| FE-11 派生 | 详情失效用渲染期比较，不写 `useEffect + setState` 镜像 |
| FE-14 三态 | 列表、结果区、媒体预览分别补齐 |
| FE-21 防重复 | 上传/登记在途时禁用，登记成功才清空输入 |
| CR-05 测试 | 纯函数 5 例 + 页面状态 6 例 + 权限 2 例 |
| CR-03 diff 规模 | 按 A1 / A2 / A5 分三次提交，单次 < 400 行 |

## 6. 实施顺序与提交拆分

| 提交 | 内容 | 校验 |
| --- | --- | --- |
| 1 | `archive-fit` + `ArchivedThumbnail` + `ArchivedMedia` + 下载接口 | `npm run test -- archive-fit`；详情不再用原件播视频 |
| 2 | `ArchiveToolbar` / `ArchiveImportForm` / `ArchiveGrid` / `ArchiveCard` + 容器重写 | 五分支手测 + 单测 |
| 3 | 结果区权限与三态、标签下沉 | 无权限账号手测 + 单测 |
| 4 | 测试补齐与文档同步 | `npm run verify` 全绿 |

提交信息遵循 Conventional Commits：`feat(asset): ...`、`fix(asset): ...`。

## 7. 完成判据（实施后回填）

- [x] `cd studio && npm run verify` 全绿（typecheck + lint + 44 个测试文件 / 318 条用例 + build）
- [x] 断网/停后端时：列表显示错误与重试，统计为 `—`（单测覆盖，`pending: !data`）
- [x] 有缓存时刷新失败：卡片仍在，出现「更新失败」条
- [x] 打开一条 HEVC 视频：预览失败有说明，"下载原始文件"仍取 `/file`
- [x] 空分类：有创建权限见上传按钮，只读账号见维护提示且无上传入口
- [x] 刷新列表后，已消失素材的详情关闭并给出说明
- [x] 只读账号（无 `template:read`）：结果区不发请求，显示权限说明
- [x] 源片缺音轨/超时长：卡片标注具体原因，仍可归档
- [x] 新增代码无 `any`、无 `as`、无内联 `style`、无裸色值

## 8. 风险

| 风险 | 应对 |
| --- | --- |
| 拆文件后行为漂移 | 先补测试再改结构；容器只搬运，不改查询参数 |
| `Modal` 快照与列表刷新不一致 | 用渲染期比较判定失效，不引入 effect 同步 |
| 缩略图组件被模板页复用 | props 保持 `url?/name/type`，模板页只改 import 路径 |
| mock 无 archive 接口 | 单测用模块 mock，联调连真实后端；不为此改 mock 适配器 |
| 迭代范围膨胀 | 批次上传、搜索一律不进本迭代；遇到顺带发现记入下一迭代 |

## 9. 下一迭代预告（不在本次实施）

迭代 2 只做一件事：**上传批次可判断**。六态状态机 + 逐条反馈 + 分类锁定 + 未知结果核对（A3/A4）。因为它要新增 zustand store 与 reducer 测试，体量与本次相当，不合并。

## 10. 实施记录（2026-11-06）

已完成 A1 / A2 / A5 / A6。`npm run verify` 通过：typecheck 无错误、lint 无告警、44 个测试文件 318 条用例全过、生产构建成功。

| 计划项 | 实际落点 | 说明 |
| --- | --- | --- |
| 列表五分支 | `ArchiveGrid.tsx` | 先判「有无可用数据」，再判错误，最后才是空态；`pending: !data` 保证失败时统计为 `—` |
| 统计口径 | `ArchiveToolbar.tsx` | 固定「当前分类 N 项 · 合计 X」，不再出现 `0 B` 冒充真实容量 |
| 预览/下载分离 | `ArchivedMedia.tsx`、`archive.api.download`、`shared/lib/download.ts` | 预览 `/stream?v=hd-v2`，下载 `/file`；解码失败不影响下载 |
| 缩略图三态 | `ArchivedThumbnail.tsx`（新文件） | 无封面 / 读取失败 / 加载中分开；卡片内不放嵌套按钮 |
| 详情快照 | `ArchiveLibrary.tsx` + `ArchiveDetail.tsx` | 渲染期比较判断失效，不用 effect 镜像列表 |
| 适用性 | `entities/asset/model/archive-fit.ts` | 卡片与详情共用；同时替换模板页原先的内联判定 |
| 结果区 | `ArchivedResults.tsx` | `enabled: canRead` 不发无权请求；补时间、生成状态、人工验收、任务号 |
| 标签下沉 | `entities/template/model/replacement.labels.ts` | 解决 `features → features` 的跨域引用 |

实施中发现并修正的两处偏差：

1. **统计口径的初版实现是错的。** 最初用 `isPending && !data` 判断"未知"，但请求失败时 `isPending` 已经是 `false`，页面仍会显示 `0 项 · 0 B` —— 正是需求文档 G02 描述的问题。单测直接暴露，已改为 `!data`。这条说明「统计只能在成功后出现」不能靠直觉写。
2. **模板页的合规判定是重复实现。** 原以为模板页不筛合规性，实际 `SourceTemplateCard` 用内联条件禁用了不合规原片；问题在于规则有两份。已改为共用 `replacementFitness()`，避免两处漂移。

未做（按计划留给迭代 2）：上传仍是「整体 toast + 首错中断」，没有逐条结果与分类锁定；inbox 登记暂无批次互斥。

### 10.1 评审轮修正（同日）

对本次改动逐文件复审后发现 7 处问题，已全部修复并补用例。门禁从 318 条增至 **324 条**。

| 问题 | 性质 | 处理 |
| --- | --- | --- |
| `Object.keys(ARCHIVE_LABELS) as ArchiveCategory[]` | 违反「`as` 需加安全理由」 | 改为显式 `ARCHIVE_CATEGORIES` 常量，导航顺序不再依赖对象键序 |
| 未知时长与越界时长共用一句「时长不在 1–15 秒」 | 文案说谎 | `duration == null` 单独返回「时长未知，需重新探测」 |
| `hasAudio` 缺省被当作「缺少音轨」 | 同上 | 缺信息 →「音轨信息缺失」；确为 false 才说「缺少音轨」 |
| `saveBlob` 用 `setTimeout(0)` 回收对象 URL | 与自身注释矛盾 | 改为 1 秒；`setTimeout(0)` 仍在点击后的最近宏任务，大文件不够 |
| 两处缩略图未带 `?v=hd-v2` | 同一张图两套缓存键 | `ReplacementSelectedSources` 改用 `archivePreviewUrl` |
| 刷新失败且上次读到 0 条时仍说「以下为上次加载的素材」 | 文案与内容不符 | 按 `assets.length` 分两句 |
| 结果区刷新按钮绑 `isFetching` | 任务在跑时每 5 秒轮询导致按钮常转 | 改为局部 `refreshing`，只反映手动刷新 |
| 批量上传中途失败整批报「上传失败」 | 会让用户重传已归档的文件 | 提示改为「已归档 N 个；「文件名」未完成：原因」 |

评审中明确**不修**的两项：`archiveApi.download` 与 `media` 实现完全相同（保留是为区分语义，后端若加 `Accept` 头需同步）；结果区播放成片仍整文件拉 Blob，因为 `/templates/replacement-runs/{id}/output` 没有派生流端点，需后端支持 Range 才能改。


## 11. 迭代 2 实施记录（2026-11-06）

目标：**上传批次可判断**——5 个文件在第 3 个失败时，用户能看清哪几个已归档、哪一个失败、哪几个没发出去。

`npm run verify` 通过：typecheck 无错误、lint 无告警、47 个测试文件 / 352 条用例、生产构建成功。

### 11.1 新增文件

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| `model/upload-batch.ts` | 173 | 六态 reducer、错误分类、候选匹配（纯函数，无 React） |
| `model/use-upload-batch.ts` | 90 | zustand store + 串行 runner + 批次结束统一刷新 |
| `ArchiveUploadPanel.tsx` | 58 | 批次汇总、进度、关闭出口 |
| `ArchiveBatchItem.tsx` | 96 | 单条状态与可用动作（重试 / 跳过 / 核对） |

容器从 94 行降到 82 行：上传循环整体移出，容器只把 `start(files, category)` 交给 store。

### 11.2 关键判定

| 情况 | 判定 | 界面动作 |
| --- | --- | --- |
| 4xx（408 除外） | 明确失败 | 「重试此项」重发同一文件 |
| 408 / 断连（-1）/ 5xx | 结果未知 | **无重传按钮**，只能「刷新并核对」 |
| 核对后找到同名同大小 | 建议而非结论 | 「确认是这条」或「不是，重新上传」 |
| 核对后没找到 | 不写"未保存" | 只说「没找到匹配记录，索引可能有延迟」 |

`retry` 与 `requeue` 是两个事件而不是一个：明确失败走 `retry`，结果未知的重传必须由用户核对后显式 `requeue`，reducer 层就拒绝「未知项直接重试」这个组合。

### 11.3 互斥与锁定

- 批次 `phase !== 'done'` 时禁用分类按钮、上传按钮、文件输入、inbox 登记表单；
- 分类以 `batch.category` 快照为准，URL 变化不改写在途批次；
- **暂停时保留「关闭批次」出口**：首版按 `locked` 隐藏，导致分类被锁又关不掉面板，用户只能刷新页面。store 只拒绝"请求在途时清空"。
- 批次结束（含暂停、关闭）统一 `invalidateQueries` 一次，不再逐条刷新。

### 11.4 同时修掉的三项遗留

1. **重试按钮没有进行中反馈**：`ErrorState` 新增 `retrying`，按钮转圈并显示「重试中…」，列表错误态与结果区一起受益。
2. **`Chip` 写死 10 个十六进制色值**：改为 token，`ok/warn/danger` 补齐 `-deep/-soft/-line` 三段。注意 `ok` 等主键**必须保持字符串**——既有 CSS 用 `theme('colors.ok / 8%')`，改成嵌套对象会让构建直接失败（本次踩到并回退）。
3. **`archiveApi.download` 与 `media` 逐字重复**：合并为单一 `fetchBlob`，保留两个命名导出表达不同语义（预览传派生流、下载只传 `/file`）。

### 11.5 仍未解决

结果区播放成片仍整文件拉 Blob：`/templates/replacement-runs/{id}/output` 是 `FileResponse`，鉴权走请求头，无法直接给 `<video src>`。要改成流式播放需要后端提供带签名/令牌的地址或 Cookie 鉴权，前端单改无效。
