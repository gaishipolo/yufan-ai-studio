# 代码审查报告 · 2026-11-17（本轮改动）

> 审查对象：**未提交的工作树**（不是某个 commit）。范围 = 今天这几轮改的东西：
> LibTV 发布自检/解封/账号接口、任务超时锚点、任务流出参、任务中心的排队与额度条。
> 方法：先跑门禁确认基线，再逐文件读码；涉及并发/权限/计数的怀疑都用临时探针跑过
> （探针已删，见 §4）。**这些 WIP 里还有另一个会话的改动**（`executor.py` / `process.py` /
> `bridge.mjs` / `state.py` / `.env.example` / `start-services.ps1`），本轮只评审与本次改动
> 相交的部分，那批未逐行过。
> 状态：**已修完可修项**（2026-11-18 09:xx）。剩下两条 P3 标了「不做」与理由，
> 其余各条已在代码里落地；修完一条删一条。

## 0. 一眼结论

| 项 | 值 |
|---|---|
| 基线（当前树） | ruff 0 · 契约四件套 `--check` 零差异 · `pytest`（task/contract/architecture/libtv 约 120 条）全绿 · 前端 typecheck / eslint / vitest 绿 |
| **CI 门禁会红** | `mypy --platform win32 src` **5 errors**（见 P1-1）——本地没跑 mypy，CI 的 backend job 会挂 |
| 剩余发现 | **P1 ×0 · P2 ×0 · P3 ×2**（均为「不做」并给理由） |
| 复核为干净 | 只读接口没有写路径、`externalId` 不加库列的取舍、`RateLimited` 复用既有排队机制 |
| 没能覆盖 | 他人 WIP 未逐行；前端未做真机视觉验收（无截图证据） |

严重度：**P0** 阻断上线 · **P1** 正确性 / 钱 / 数据一致性 · **P2** 健壮性 · **P3** 清理与文档。

---

## 1. P1（必须改）

### P1-1 ✅ 已修 · `mypy` 5 处报错 → CI backend job 必红

`api/src/modules/model_driver/cli.py:116` 与 `:126` 在同一个函数里两次 `for item in ...`：
第一处遍历 `QueueItem`，第二处遍历 `ConsumptionItem`。mypy 按首次赋值定型，于是后一处
报 `"QueueItem" has no attribute "seconds/amount/source_name/summary"`。

```
$ mypy --platform win32 src
src\modules\model_driver\cli.py:126: error: Incompatible types in assignment
... Found 5 errors in 1 file (checked 251 source files)
```

修法：把两个循环变量分开命名（已改成 `progress` / `spend`，`mypy` 现在 251 文件 0 错）。
这属于「本地没跑 mypy」的漏检，建议提 PR 前照 CI 的两条命令手跑一遍。

### P1-2 新加的排队/额度只看得到「超级管理员」

三条新接口都挂 `require("model_driver:read")`，而库里**没有角色拥有它**——
只有超级管理员那条 `*` 兜住。

```
超管       权限   1 条（*）
内容编辑   权限  10 条（catalog/image/library/shot/storyboard）→ 连 task:read 都没有
只读成员   权限   8 条（含 task:read）→ 能进任务中心，但没有 model_driver:read
素材专员   权限   1 条
```

后果有两层：

1. **功能不可见**：除超管外，任务中心顶部的额度条与卡片上的「排队 3/10」永远不出现——
   而这恰恰是本轮要交付的东西。
2. **无谓请求**：`useLibtvAccount` 每 60 秒、`useLibtvQueue` 每 5 秒各打一次 403，
   而 `throwOnError:false` 把它悄悄吞了，控制台之外没有任何线索。

修法二选一（建议前者）：
- **排队位次按任务算**，改用 `task:read`（它只回进度与位次，不含任务内容）；
  额度条保留 `model_driver:read`，并在前端用 `useCan()` 门控住——没权限就**不发**请求；
- 或者接受「只有运维看得到」，那也必须补 `useCan` 门控，别让每个只读成员每分钟收一个 403。

---

## 2. P2（健壮性）

### P2-1 `reconcile` 不检查执行器是否正在跑，可能与桥接层打架
`libtv/reconcile.py` 的 `RECONCILABLE` 收了 `submitting` —— 那正是**桥接层正在提交**的状态。
此时人工 `--not-submitted` 会把档案改回 `uploaded`，而桥接层随后写回 `task_id`，两边矛盾
（钱可能已经花了，档案却说「没提交」）。

现成判据就在 `process.clear_finished_lock()`：`records/worker.lock` 存在且
`executor-result.json::phase == starting/running`（或 `node_pid` 还活着）就该拒绝迁移。
建议在 `reconcile()` 之前加这一道，并把「等一下或先确认执行器已退出」写进报错。

### P2-2 冷缓存一次快照起 3 个 node；接口也没有速率限制
探针实测（§4）：

```
冷缓存：node 启动 3 次，耗时 624ms   ← asset / freePower / benefits 三个并发各起一次
热缓存：node 启动 0 次，耗时 367ms
```

`_headers()` 没有锁，`asyncio.gather` 三路同时穿透缓存。修法：per-account
`asyncio.Lock` + 双检；`snapshot()` 冷启动时先取一次 header 再并发。
另外这三条读接口每调一次就是「起 node + 打上游」，仓库已有 `rate_limit(...)` 依赖，
建议照 `submit_task` 那样挂一个。

### P2-3 `POST /model-driver/libtv/progress` 不校验任务归属
传任意 `taskIds` 都能拿到进度与位次（含别人的）。上游任务是时间戳型 id，枚举成本不高。
它只回进度/位次、不含提示词，敏感度有限，但仍是跨租户可见面。
修法：要么按调用者可见范围过滤（需要 join 任务表），要么在 `LIBTV_API.md` 里明确
「运维接口，仅 `model_driver:read`」并接受这个前提——现在文档里没写这一句。

### P2-4 `quote()` 把 0 积分权益标签写死了
`libtv/account.py::quote` 里 `disableBenefitTags=["rateLimit_powerZero_20261111"]` 是常量。
活动 2026-11-30 到期后这个 tag 失效，传了也不生效，而**调用方以为自己在问「积分队列的价」**。
标签本来就能从 `zero_power_benefits()` 拿到（`tags[]`），改为运行时取，取不到就不传。

### P2-5 额度条泄露内网地址，且失败是静默的
`LibTvAccountStrip` 直接把 `127.0.0.1:9412` 画在页面上：局域网演示无所谓，部署到客户机房
就是信息泄露。同时 403/上游故障都被 `throwOnError:false` 吃掉，用户只看到「没有额度条」。
建议：地址只在 `import.meta.env.DEV` 下显示；失败时在条位留一句 hover 文案（或干脆不渲染但
在控制台 `console.info` 一次），别让人以为「本来就没有这个功能」。

---

## 3. P3（清理与文档）

- **P3-1** `queueLabel()` 在 `queueLimit` 缺失时会渲染成「3/0」；缺省应回退 10（`types.ts`）。
- **P3-2** `TaskCard` 新加的 `queueHint` 没有渲染测试：`libtv` 单测覆盖了拼串，
  但「有 hint 时多一行、没有时不占位」这条没有断言。
- **P3-3** `cli.py` 两个新命令各写了一遍 `except AppError → print + exit 1`，可抽一个小工具函数。
- **P3-4** `process.vendor_problem()` 每个阶段都算 10 个文件的 sha256：poll 每 2 秒一次、
  在跑几个任务就乘几。文件很小（本地盘），但这是纯白算——按 `(mtime,size)` 缓存即可。
- **P3-5** `account._request()` 每次新建 `httpx.AsyncClient`，快照一次就是 3 次 TLS 握手。
  模块级共享 client（或 `AsyncClient` 单例 + 显式关闭）更合适。
- **P3-6** `LIBTV_API.md` 的端点表用 `...` 省略了 `api2.liblib.art/api/www` 前缀，
  第一次读的人要自己补；建议直接给完整 base URL 两行（`API_TV` / `API_ART`）。

---

## 4. 探针与证据

| 结论 | 证据 |
|---|---|
| mypy 5 errors | `mypy --platform win32 src` 实跑输出（§P1-1） |
| 只有超管有 `model_driver:read` | `select permission from role_permissions join roles ...` 实查：只读成员 8 条、内容编辑 10 条，均无该权限 |
| 冷缓存 3 次 node | 临时探针：包 `asyncio.create_subprocess_exec` 计数，跑完即删（§P2-2 输出） |
| 契约一致 | `app export-openapi --check` 四份文件「无变化」 |
| feed 已带 `externalId` | `GET /tasks/feed` 实测：`29dc66ae…:20261117193603599744489` |

## 5. 做得对的地方（该被复制）

- **不给排队状态加数据库列**：排队是上游瞬时状态，存快照必然过期；前端按 `externalId`
  一次性批量问，口径只有一处。
- **`EXECUTOR_BUSY → RateLimited`** 不是新造一套重试，而是复用既有的
  `GENERATION_THROTTLE_DELAYS` 排队机制——「账号被占」和「上游限流」本来就是同一类事。
- **`release.py` 一处实现、三处复用**（pytest / 监督进程 / 桥接层同源），
  并且把「只差换行符」单独识别出来给了对症修法——这正是这个坑连踩三次的原因。
- **只读接口不碰写路径**：花钱仍然只有桥接层一条路，新增的三条接口里没有任何
  create/cancel/改账号状态。
