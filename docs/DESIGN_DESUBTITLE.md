# 智能去字幕 · 工作台集成方案（2026-11-20）

> 前置：接口已逆向完成并通过脚本验证（`api/scripts/libtv_desubtitle.mjs`，
> 上游模型 `volcano-subtitle-eraser`，实测 **0 积分**、4 秒源片约 70–90 秒出片）。
> 本文只讲「怎么接进工作台」，不含代码。

## 0. 需求拆成四条可验收的行为

1. 任务卡详情里有第 4 个按钮「智能去字幕」，与「下载成片 / 重新生成 / 收藏」并排；
2. 点击后**派生任务绑定同一张卡**：卡片上能看到它的进度，完成后结果回到这张卡；
3. 卡片播放器上方可在**原片 / 去字幕版**之间切换；
4. 默认选中**去字幕后**的版本，**资产库里的引用**也一样。

## 1. 数据模型：派生任务复用 `video_tasks`

不建议为「去字幕版」新建表。它和普通任务在生命周期上完全同构（提交 → 排队 → 生成 →
下载 → 入库 → 可重试），复用任务表可以白拿 runner 的轮询、重试、失败分类、事件、
封面、入库、收藏——新建表等于把 `runner` 再写一遍，而且两套状态机会立刻开始漂移。

`video_tasks` 加两列（一次迁移）：

| 列 | 类型 | 说明 |
|---|---|---|
| `derived_from_task_id` | UUID null，`index` | 派生自哪条任务；`NULL` = 用户主动创建的任务 |
| `variant` | String(24)，默认 `original` | `original` / `subtitle_erased` |

约束：

- **唯一索引 `(derived_from_task_id, variant)`**（`derived_from_task_id IS NOT NULL` 的部分索引）。
  这是「点两次不会下两单」的硬保证——靠应用层 `if exists` 早晚会被并发绕过去。
- **列表必须排除派生任务**：`/tasks`、`/tasks/feed` 加 `derived_from_task_id IS NULL`。
  否则任务中心会多出一张「去字幕」卡，用户以为是两条任务。
- 父任务删除时派生任务一起删（同一事务里按 `derived_from_task_id` 级联），否则会留下
  一条看得见但点不开的孤儿。

## 2. 驱动：新增模型 `libtv-subtitle-eraser`，复用整条执行链

上游请求形状与文生视频不同（无 prompt、`mode=Subtitle`、`videoListV2` 带宽高时长），
所以**不能**塞进现有的 `submit` 阶段（那段对 `wanx3.0/mixed2video/count=1` 有硬校验，
硬改会把两条链路的风险绑在一起）。

做法：`runtime/bridge.mjs` 加一个并列分支 `command === 'desubtitle'`：

```
inputs/request.json  {sourceUrl, width, height, duration}
  → power/calculator（报价，0 也要记）
  → client-verification-token
  → create（model=volcano-subtitle-eraser）
  → 写 run.json: status=submitted, task_id
```

`poll` / `download` 两个阶段**原样复用**：桥接的 poll 只按 `task_id` 查 progress，
download 只认 `taskResults[].previewPath`——两者都与模型无关。

Python 侧只需：

- `executor.STAGES` 增加 `desubtitle`（超时与 submit 同级，1650s 足够，实测 90s）；
- `drivers/libtv.py::MODELS` 增加一条 `ModelSpec(id="libtv-subtitle-eraser", …)`，
  `param_schema` **不暴露给用户**（源视频由父任务决定，用户没有可填的参数）；
- `submit()` 里按 `model_id` 分流到 `desubtitle` 阶段，`poll/download` 不动。

> 为什么不在驱动里直接 `httpx` 打上游：账号锁、日志、错误码映射、重试语义都在
> executor 里，绕过去等于再造一条没有这些保护的路径（和之前"两条能花钱的路"是同一类坑）。

## 3. 后端接口

### 3.1 创建（幂等）

```
POST /api/v1/tasks/{task_id}/desubtitle
→ 200 {id, status, progress, estimatedPower, reused: bool}
```

- 前置：父任务 `completed` 且 `outputUrl` 可用；否则 400（文案说清缺什么）；
- 幂等：已有同 `(parent, subtitle_erased)` 的任务 → 直接返回（`reused: true`），
  **不新建、不重新下单**；
- 首次创建：复制 `title/model/model_id/duration/aspect_ratio/author/参考资产`，
  `variant='subtitle_erased'`、`derived_from_task_id=父`、`billing_mode='free'`；
- 报价写进任务（`estimatedPower` 出参给前端展示），当前是 0，但不写死。

### 3.2 查询

`GET /api/v1/tasks/{id}` 出参新增：

```jsonc
"subtitleErase": {            // 没有派生任务时为 null
  "id": "...", "status": "queued|running|completed|failed",
  "progress": 42, "outputUrl": "...", "errorMessage": null
}
```

任务流 `TaskFeedItem` 增加同名可选字段（列表页一次 `IN` 查询批量带出，别在卡片里
逐个请求——这是仓库里已经踩过的 N+1）。

## 4. 前端

### 4.1 弹窗按钮（`features/task-preview/TaskPreviewModal.tsx`）

按钮组的第 4 个，状态机四种：

| 派生任务状态 | 按钮 | 点击 |
|---|---|---|
| 不存在 | 「智能去字幕」（`Wand2`） | 调创建接口 |
| `queued/running` | loading + 「去字幕 42%」 | 禁用（防重复） |
| `completed` | 「已去字幕」+ 勾 | 禁用（版本切换在播放器上） |
| `failed` | 「重试去字幕」 | 再次创建（`reused` 会回 false，需重试接口） |

进度复用现有任务轮询（弹窗已订阅 `useTaskDetail`，2.5s 一跳），不新增定时器。

### 4.2 播放器版本切换

播放器上方两个 pill：`原片` / `去字幕版`，**仅当派生任务 `completed` 时出现**：

- 默认选 `去字幕版`（localStorage 记住上一次选择，按任务 id 存，避免每次跳回原片）；
- 切换只改 `<video src>` 并保留 `poster`；`key={version}` 强制重建解码器，
  否则部分浏览器会继续播旧 buffer；
- 下载按钮跟随当前选中版本（下载的就是用户正在看的那一版），文件后缀区分：
  `xxx-去字幕.mp4`。

### 4.3 卡片（`entities/task/ui/TaskCard.tsx`）

- 派生任务 `queued/running`：在排队行位置显示「去字幕 42%」（复用 `queueHint` 那一行）；
- `completed`：封面右上角一个 `去字幕` 角标，不新增卡片。

### 4.4 资产库引用（默认选中去字幕版）

`library_assets` 加两列：`variant`（`original|subtitle_erased`）、`source_asset_id`。
派生任务入库时带上这两列（入库钩子已经能拿到父任务的资产 id）。

选择器（模板填表 / 镜头参考 / 图生视频参考）在列出资产时按 `source_asset_id` 归组：
**同组优先取 `subtitle_erased`**，组内没有才用 `original`；UI 上给一个「原片 / 去字幕」
小开关，默认「去字幕」，切换后回到原片版本——用户永远看得见自己引用的是哪一版。

## 5. 风险与要拍板的点

| # | 问题 | 建议 |
|---|---|---|
| 1 | 派生任务要不要单独出现在任务中心 | **不出现**（只在父卡内切换），否则同一段素材两张卡 |
| 2 | 失败重试是否允许 | 允许，但要走显式「重试去字幕」，不要自动重排（可能重复扣费） |
| 3 | 资产库保留几版 | **两版都留**，默认去字幕；原片是唯一可回溯的底片 |
| 4 | 父任务删除时 | 级联删派生任务与派生资产，但**先提示**（"会连去字幕版一起删除"） |
| 5 | 目前 0 积分，将来收费怎么办 | 创建接口返回 `estimatedPower`，前端在按钮上先展示价格；接口层不做"静默扣费" |
| 6 | 一次给多张卡批量去字幕 | 本期不做；接口天然支持（循环调），但要先有并发上限与配额 |

## 6. 工作量与拆分

| 阶段 | 内容 | 预估 |
|---|---|---|
| P0 | 迁移（2 列 + 唯一索引）+ bridge `desubtitle` 分支 + 驱动模型 + 创建/查询接口 + 弹窗按钮与进度 + 版本切换与默认选中 | 1 天 |
| P1 | 列表排除派生任务 + 卡片角标 + 资产库 `variant/source_asset_id` + 引用默认去字幕 | 半天 |
| P2 | 批量、失败重试入口、报价展示、删除级联提示 | 半天 |

## 7. 验收清单

- [ ] 点一次「智能去字幕」→ 卡片出现进度 → 完成后弹窗可在两版之间切换，默认去字幕版；
- [ ] 再点一次不会产生第二条任务（唯一索引 + `reused: true`）；
- [ ] 任务中心列表不出现「去字幕」的重复卡片；
- [ ] 资产库出现「去字幕版」资产，模板/镜头引用时默认选中它，且能切回原片；
- [ ] 失败时父卡显示原因，可点「重试去字幕」；
- [ ] 父任务删除时，派生任务与派生资产一并处理；
- [ ] 契约四件套用 `app export-openapi` 重新生成，CI 绿。
