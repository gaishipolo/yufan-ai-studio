# 产品替换：从点击按钮到成片的完整链路

> 适用范围：模板库 → 替换配置 → 产品替换（`kind = product`）。
> 人物 / 场景替换走**完全同一条链路**，只有提示词模板与变量不同。
>
> 本文用「文件 + 函数 + 落盘」的方式描述一次真实调用。所有行号对应当前 `main`。

---

## 0. 一句话结论

前端只调 **3 个接口**（目录、准备、执行），后端用**一条队列 + 两个 handler + 一个生成编排器**，
把「上传参考图 → 0 积分估价 → 提交上游 → 轮询 → 下载」拆成 5 段异步，
每一段的中间状态都写在共享盘的 `run.json` 里，因此任何一段崩了都能从文件恢复，
不需要数据库参与。

---

## 1. 全景图

```
浏览器
  pages/templates/TemplatesPage.tsx
    └─ features/replacement-templates/ReplacementTemplates.tsx      ← 按钮在这里
         └─ entities/template/api/replacement.api.ts               ← 3 个接口
              └─ shared/api/http.ts                                ← 封套解包 / 401 续期
                   │  HTTP  /api/v1/templates/...
                   ▼
FastAPI
  modules/template/replacement_router.py                           ← 路由 + 权限
    └─ replacement_service.py                                      ← 同步业务（线程池）
         ├─ replacement_repository.py                              ← 路径索引 / 清单读写 / OS 租约
         ├─ infra/archive/（工厂 + local 驱动）                      ← 共享盘读、写、复制、校验
         └─ replacement_worker.py  ──enqueue──►  进程内队列
                                                   │
                                                   ▼
                                        workers.py（Worker 消费）
                                                   │
              ┌────────────────────────────────────┼──────────────────────────────┐
              ▼                                    ▼                              ▼
  replacement_worker.prepare_upload      generation/worker.handle_submit   generation/worker.handle_poll
  （上传 + 估价，非计费）                  （提交，计费点）                   （轮询；generated 时投 download）
              │                                    │                              │
              └──────────────► infra/ai/libtv.py（execute_stage / LibTVProvider）
                                                   │  subprocess
                                                   ▼
                                   infra/ai/libtv_process.py（监督进程，持 OS 锁）
                                                   │  subprocess
                                                   ▼
                                   infra/ai/libtv_runtime/bridge.mjs
                                                   │  import
                                                   ▼
                             libtv_runtime/vendor/ 里的固定版适配器 + 浏览器会话
                                                   │
                                                   ▼
                                             LibTV 上游（wanx3.0 / 720P）
```

---

## 2. 十二跳速查表

| # | 触发者 | 文件 | 关键函数 | 产出 |
|---|---|---|---|---|
| 1 | 用户打开模板库 | `studio/src/modules/templates/module.ts` | 路由声明 | 挂载 `/templates` |
| 2 | 页面渲染 | `pages/templates/TemplatesPage.tsx` → `features/replacement-templates/ReplacementTemplates.tsx` | `ReplacementTemplates()` | 拉目录 + 拉素材 |
| 3 | 拉目录 | `entities/template/api/replacement.api.ts` → `replacement_router.py` | `catalog()` | runs / packs / root |
| 4 | **点击「预览提示词并归档输入」** | `ReplacementTemplates.tsx` `prepareAll()` | 组装 config + `requestId` | — |
| 5 | 发请求 | `replacement.api.ts` → `shared/api/http.ts` | `prepare()` | `POST /replacement-runs` |
| 6 | 归档输入 | `replacement_service.py` | `prepare()` → `_validate_inputs()` → `_prepare_inputs()` | `inputs/*`、`run.json` |
| 7 | 编译提示词 | `replacement_service.py` `_compile()` → `replacement_runtime/workbench/compile.mjs` → `prompt-compiler.mjs` → `prompts/product.template.zh.txt` | 子进程 + 模板替换 | `inputs/prompt.txt` |
| 8 | **点击「确认并加入零积分生产队列」** | `ReplacementTemplates.tsx` `submitRuns()` | 逐个 `execute()` | `POST .../execute` |
| 9 | 入队 | `replacement_worker.py` | `schedule()` → `schedule_state()` → `enqueue()` | job `template.replacement.prepare` |
| 10 | 上传 + 估价 | `replacement_worker.py` `prepare_upload()` → `infra/ai/libtv.py` `execute_stage()` → `libtv_process.py` → `bridge.mjs` | 上传/审核/估价 | `records/uploads.json`、状态 `uploaded` |
| 11 | 提交 + 轮询 | `generation/runner.py` `handle_submit()` / `handle_poll()` → `libtv.py` → `bridge.mjs` | 提交、查进度 | `run.json` 的 `task_id` / `progress_percent` |
| 12 | 下载 + 收尾 | `replacement_worker.py` `download()` → `bridge.mjs` download 分支 | 落盘成片 | `outputs/generated.mp4`、状态 `success` |

---

## 3. 逐段展开

### 阶段 A｜页面就位（用户还没点）

**A1. 路由注册** `studio/src/modules/templates/module.ts:7-13`

```ts
{ path: 'templates', load: () => import('@/pages/templates/TemplatesPage'),
  permissions: ['template:read'] }
```

模块是自动发现的：`studio/src/shared/registry/*` 收集 `modules/*/module.ts`，
`app/router.tsx` 统一展开。没有 `template:read` 的人根本进不来。

**A2. 页面只有 9 行** `studio/src/pages/templates/TemplatesPage.tsx`

```tsx
<PageHeader title="模板库" ... />
<div className="mt-5"><ReplacementTemplates /></div>
```

**A3. 主体组件** `studio/src/features/replacement-templates/ReplacementTemplates.tsx:29`

挂载时同时发两个查询（`:48-49`）：

- `useArchiveAssets()` → 归档素材（原片 / 参考图），走 `entities/asset/api/archive.queries.ts`
- `useReplacementCatalog()` → 调用档案列表，走 `entities/template/api/replacement.queries.ts:9`

**A4. 目录轮询规则** `replacement.queries.ts:7-13`

```ts
const ACTIVE = new Set(['queued','uploading','uploaded','submitting','submitted',
                        'running','generated','waiting_executor']);
refetchInterval: data?.runs.some(r => ACTIVE.has(r.status)) ? 5000 : false
```

**只要列表里还有活跃任务，就每 5 秒拉一次；全部终结后自动停。**

---

### 阶段 B｜第一次点击：归档输入 + 编译提示词（不花钱）

> 按钮文案：**「预览提示词并归档输入」**（`ReplacementTemplates.tsx:140-142`）
> 权限：`<Can permission="template:use">`

**B1. 前端组装配置** `ReplacementTemplates.tsx:68-86`

```ts
const config = { kind, target: target.trim(), sourceId, referenceIds: references,
  wardrobeMode, packId: packId || null,
  replacementScope: scopes.join('、'), notes, aspectRatio: 'adaptive' };
const key = JSON.stringify(config);
let requestId = requestIds.current.get(key);
if (!requestId) { requestId = crypto.randomUUID(); requestIds.current.set(key, requestId); }
prepared.push(await prepare.mutateAsync({...config, requestId}));
```

三个要点：

1. **`requestId` 按配置内容缓存**（`:45` 的 `useRef<Map>`）。同一份配置重复点，用的是同一个
   UUID，后端据此幂等，重复请求只会返回已有档案，不会建第二份。
2. 支持**批量原片**：`for` 循环逐条准备，任一条失败时，已成功的那些仍然弹窗（`:82-85`）。
3. 请求超时被单独放宽到 **120 秒**（`replacement.api.ts:6`），因为要复制视频和起 Node 子进程。

**B2. 接口层** `studio/src/entities/template/api/replacement.api.ts:4-8`

```ts
prepare: (payload) => api.post<ReplacementRun>('/templates/replacement-runs', payload, {timeout: 120_000})
```

`shared/api/http.ts` 做三件事：`baseURL = /api/v1`（`:14`）、解 `{code,message,data}` 封套（`:125-133`）、
401 先续期再重放（`:65-100`）。所以 `replacement.api.ts` 拿到的已经是 `data` 本体。

**B3. 路由与权限** `api/src/modules/template/replacement_router.py:30-34`

```python
@router.post("/replacement-runs", status_code=201)
async def prepare_run(payload: PrepareReplacement, actor: ReplacementWriteDep, ...):
    return ApiResponse.ok(await service.prepare(payload, actor.id, actor.has("asset:read")))
```

权限在 `replacement_dependencies.py:19-20`：读要 `template:read`，写要 `template:use`。
另外还有一道**业务权限**：`service.prepare` 里 `if not can_read_assets: raise Forbidden`
（`replacement_service.py:302-303`）——要替换，必须同时能读素材。

**B4. 幂等闸门** `replacement_service.py:76-119`

```python
uid = payload.request_id.hex
fingerprint = sha256(owner + payload.model_dump_json())
with lease(repo.storage.resolve(f"catalog/locks/{uid}.lock")):
    if pointer.exists():
        path, meta = repo.owned(uid, owner)
        if meta.fingerprint != fingerprint: raise Conflict("请求标识已被不同配置使用")
        if meta.status not in ("preparing","prepare_failed"): return run_out(path, repo)   # 已准备好，直接返回
    else:
        path = repo.storage.resolve(f"runs/{date}/{kind}-{uid}")
        ...
        repo.save(path, meta); repo.index(path, uid)
```

- `lease()`（`infra/archive/base.py`）是**跨进程 OS 文件锁**（Windows `msvcrt.locking`、
  Linux `flock`），锁文件放在共享盘上，所以两个 uvicorn 进程也互斥。
- 索引是一个指针文件：`catalog/runs/{uid}.json` → `{path: "runs/2026/11/06/product-<uid>"}`。
  所有后续环节都靠这个指针把 32 位 id 还原成目录（`replacement_repository.py:48-54`）。
- `if (path/"run.json").exists()` 那段（`:104-107`）防的是「索引丢了但目录还在」的半成品：
  只要档案里有 `task_id` 或指纹不一致，就拒绝覆盖。

**B5. 校验输入** `replacement_service.py:145-167`

| 检查 | 失败文案 |
|---|---|
| 原片必须是 `source` 分类的视频 | 请选择已归档的原片 |
| 时长 1–15 秒且有音轨 | 原片需为带音轨的完整 MP4，时长 1–15 秒；不会自动截断 |
| 参考图不能重复 | 参考素材不能重复 |
| 参考图分类必须等于 `kind`、必须是图片 | 参考图分类需与替换类型一致 |
| 正文里不许出现 `{{ }}` | 请使用普通文字，不要手写素材引用标记 |
| 产品替换必须有替换范围 | 请填写目标名称并选择产品替换范围 |

**B6. 复制真实文件 + 生成绑定表** `replacement_service.py:170-259`

```python
for index, item in enumerate([*refs, source]):
    if checksum(original) != item.sha256: raise BadRequest("素材字节已变化，请重新登记")
    filename = "reference-video.mp4" if item is source else f"reference-{index+1}{suffix}"
    repo.storage.copy(original, path/"inputs"/filename, item.sha256)
    bindings.append({"id":..., "role": "target_product"|"source_video"|..., "type":..., "file":...})
repo.storage.copy(path/"inputs/reference-video.mp4", path/"inputs/source-original.mp4", ...)
```

- 先校验 sha256 再复制，复制时 `LocalArchiveStorage.copy`（`infra/archive/local.py`）会**边拷边校验**，
  不一致直接 `UpstreamError`。共享盘读写不稳时这一步就会拦下来。
- 原片被复制**两份**：`reference-video.mp4`（随绑定表上传）和 `source-original.mp4`（保真留档）。
- 然后拼 `variables`（产品替换是 `source_product / target_product / replacement_scope / state_constraints`），
  再组 `spec = {task, duration, aspect_ratio, wardrobe_mode, references, variables}`。

**B7. 编译提示词** `replacement_service.py:262-282`

```python
subprocess.run([settings.libtv_node, RUNTIME/"workbench/compile.mjs"],
               input=json.dumps(spec), capture_output=True, timeout=30)
```

三个文件依次生效：

1. `replacement_runtime/workbench/compile.mjs` — 入口，从 stdin 读 spec，补两处业务适配：
   1 秒素材按 2 秒编译后再改回（`:8-16`）、`character + wardrobe_mode=reference` 时整段替换提示词（`:17-23`）。
2. `replacement_runtime/prompt-compiler.mjs` — 真正的编译器：校验角色/类型/数量
   （图片 ≤10、视频 ≤5、音频 ≤5、必须有且仅有一个 `source_video`），
   生成 `{{Mixed 1}}` 形式的 token，替换模板变量，并**反向校验**
   「每个上传的参考都在提示词里出现过」（`:60`）。
3. `replacement_runtime/prompts/product.template.zh.txt` — 中文提示词骨架，
   含编辑边界、屏幕内商品独立替换、实物一致性、原声与结束边界四段。

编译产物写回三个文件（`:229-239`）：

```
inputs/prompt.txt                 最终提示词（纯文本，bridge 会逐字节比对）
inputs/prompt-spec.json           编译输入 spec
records/prompt-compilation.json   编译元数据（bindings/token/template_sha256/compiler_version）
```

**B8. 收尾** `replacement_service.py:120-134`

```python
meta.status = "prepared"
meta.file_checksums = {每个文件的相对路径: sha256}
```

`file_checksums` 是后面 bridge 的**防篡改基线**：上传前逐文件重算，不一致就不上传。

**B9. 弹窗** `ReplacementTemplates.tsx:166-172`

返回的 `run.prompt` 直接渲染在 `<pre>` 里，用户核对的就是 `inputs/prompt.txt` 的内容。
此阶段状态是 `prepared`（界面显示「待核对」），**上游一分钱没花**。

---

### 阶段 C｜第二次点击：入队（仍然没花钱）

> 按钮文案：**「确认并加入零积分生产队列」**（`ReplacementTemplates.tsx:170`）

**C1. 前端** `ReplacementTemplates.tsx:87-93` → `replacement.api.ts:7`

```ts
for (const run of runs) await execute.mutateAsync(run.id);
toast('已进入仅 0 积分生产队列', 'success');
```

**C2. 路由** `replacement_router.py:44-48` → `replacement_service.py:313-315`

```python
async def execute(self, run_id, owner):
    await schedule(run_id, owner)      # 只入队，不等结果
    return await self.detail(run_id, owner)   # 立刻返回当前状态（通常是 queued）
```

**接口是「投递」语义，不是「执行」语义**——所以前端拿到的 `status` 是 `queued`，
真正的推进靠 5 秒轮询。

**C3. 状态裁决** `replacement_worker.py:102-125`（`schedule_state`，在 `repo.edit()` 租约内执行）

| 当前状态 | 动作 |
|---|---|
| `success` | 直接返回 `done`，什么都不做 |
| 有 `task_id` | 置 `framework_status=running`，返回 `poll` → 走 `runner.resume()`（**只续轮询，绝不重提**） |
| `submission_unknown` 或 `submit_attempts > 0` 且无 `task_id` | `Conflict`：禁止重提，先核对上游 |
| `preparing` / `prepare_failed` | `Conflict`：输入没准备好 |
| 其它非可重试状态 | `Conflict`：该任务当前不能提交 |
| `prepared` / `failed` / `waiting_free` / `service_error` 等 | 置 `queued`、清 `last_error`、**`retry_count = 0`** → 返回 `prepare` |

`retry_count` 清零这一句（`:123`）是有意的：自动重试有 5 次上限，人工再点一次等于重新发一份额度，
否则上一轮耗尽后，这次刚入队就会在第一次失败时被判「已重试 5 次」。

**C4. 入队** `replacement_worker.py:89-99`

```python
Job(name="template.replacement.prepare", payload={"id": run_id},
    max_attempts=1, idempotency_key=f"replacement:prepare:{run_id}", timeout_sec=1600)
```

`max_attempts=1` + 幂等键：同一个 run 在队列里最多一条，且失败不会自动重投
（重试逻辑由 handler 自己用 `backoff_delay` 控制，见 D4）。

队列实现是 `infra/queue/memory.py`：堆 + 幂等键索引 + 延迟投递，**进程内**。
换成 Redis 只需替换实现，业务代码不动（`infra/queue/base.py:1-13` 写明了这个约定）。

---

### 阶段 D｜worker 执行：上传 + 0 积分估价（非计费）

**D1. 消费** `workers.py:65-96`

`Worker._loop` 按 `job.name` 从 `HANDLERS` 取 handler，`asyncio.wait_for(handler, timeout=job.timeout_sec)`，
成功 `ack`，异常 `nack`。worker 在应用启动时拉起（`app.py:71-74`），并发数 `WORKER_CONCURRENCY`（默认 2）。

**D2. handler** `replacement_worker.py:155-206`

```python
if meta.task_id:                       → runner.resume()      # 已经提交过，只续轮询
if meta.status in ("submitting","submission_unknown") or meta.submit_attempts: return
if await owner_disabled(meta.owner_id): → service_error / OWNER_DISABLED
result = await execute_stage(run_id, "upload")
```

**停用闸门放在 `task_id` 判断之后**（`:161-173`）。顺序即语义：已经拿到 `task_id` 的任务继续跑完
（钱已经花了，停掉只会让成片烂在盘里），但**没提交过的不许再替他花钱**。
`owner_disabled` 走 `api/accounts.py` 这个 Protocol 注入点，而不是直接 import `modules.identity`——
业务模块之间禁止互相 import，由 `tests/test_architecture.py` 守着。

**D3. 起子进程** `infra/ai/libtv.py:64-96`

```python
subprocess.Popen([sys.executable, "-m", "infra.ai.libtv_process"], stdin=PIPE, ...)
process.communicate(json.dumps({"version": 2, "stage": stage, "run": str(path),
                                "node": node,
                                "result": str(resultfile)}).encode())
```

参数走 **stdin 的 JSON**（带 `version`），不再按位置对齐——加参数只要给默认值，
也不会出现「某个字段拿到隔壁字段的值」这种离原因很远的故障。v2 起 payload 不再带
适配器目录：固定版适配器随代码入库，监督进程按自身位置算出 `libtv_runtime/vendor/`
再交给桥接层，因此没有任何外部路径配置。

- 结果写进 `records/operation-<uuid>.json`，**通过文件回传**，不走 stdout（stdout 直接丢弃）。
- `asyncio.to_thread(process.communicate, payload)` 而不是 `asyncio.create_subprocess`：注释写明
  Windows 的 uvicorn reload 用 Selector loop，不支持 asyncio 子进程 API。

**D4. 监督进程** `infra/ai/libtv_process.py:55-103`

```python
with lease(run/"records/executor.lease"):
    clear_finished_lock(run, storage)          # 旧锁要确认进程真的死了才清
    # upload/submit 再抢工作区旁 .locks/account-executor.lock
    node bridge.mjs stage run vendor           # 超时: upload=1500s submit=100s poll=45s download=240s
```

为什么要有这一层：**它持有 OS 租约直到 Node 真的退出**。API 进程重启不会释放活任务的锁，
重启后 `clear_finished_lock` 会通过 `node_pid` 判断上一个 Node 是否还活着，
活着就返回 `EXECUTOR_BUSY`，而不是两个进程同时操作同一个 run。账号级串行改用归档盘上的
自有租约，与任何外部项目的进程单例锁无关。

**D5. bridge：upload 分支** `infra/ai/libtv_runtime/bridge.mjs:58-113`

这是整条链路唯一接触上游的地方，顺序很讲究：

1. **验固定版适配器**：按 `vendor/manifest.json` 逐文件核对 sha256，失败即 `RELEASE_INVALID`。
2. **接浏览器**（`:31`）：`CanvasApi.fromBrowser(process.env.JS_REVERSE_BROWSER_URL)`，
   复用已登录的浏览器会话；未登录 → 阶段码 `LOGIN_REQUIRED`。
3. **一致性三连**（`:59-66`）：提示词与 `prompt-compilation.json` 逐字节相同、绑定数量一致、
   读到 `media-inputs.json`。
4. **逐文件上传**（`:71-83`）：每个参考重算 sha256 → 对比 `meta.file_checksums`（防篡改）→
   `MediaUploader.upload()`（带审核）→ 结果写 `records/uploads.json`，**已上传且审核通过的会跳过**。
   所以重跑是增量的，不会重复上传。
5. **组请求体**（`:84-90`）：`model=wanx3.0`、`modeType=mixed2video`、`resolution=720P`、
   `enableSound=on`、`duration` 来自原片，写 `inputs/request.json`。
6. **回填 URL**（`:91-93`）：`inputs/input-map.json` 里每个条目补上 `field=params.mixedList[i].url`。
7. **快照旧代码**（`:94-99`）：把用到的 4 个 legacy 文件 + `release.json` 拷进 `run/code/`，
   保证「这次出片用的是哪版代码」可追溯。
8. **改状态 + 估价**（`:100-110`）：`status=uploaded`，然后调
   `/api/task/generation/power/calculator`；**只有 `power === 0` 才继续**，
   非零就置 `waiting_free` 并记 `estimated_credits`，本次到此为止。
9. `:111` 一行注释是设计声明：**真正的提交不在这里，交给 GenerationRunner**。

**D6. 回到 handler 裁决** `replacement_worker.py:174-206`

| 子进程结果 | 处理 |
|---|---|
| `code == EXECUTOR_BUSY` | `waiting_executor` + 记一次重试；`_bump_retry` 给出 30s→600s 退避，重新入队 |
| `code != OK` 或状态既不是 `uploaded` 也不是 `waiting_free` | `service_error`，写 `error_code` |
| `status == waiting_free` | 保存后**直接返回**（等用户手动继续 / 等上游变免费） |
| `status == uploaded` | `preparation_ready=True`、`retry_count=0` → `runner.dispatch()` |

---

### 阶段 E｜提交（真正的计费点）

**E1. dispatch** `generation/runner.py:91-103`

```python
Job(name="generation.submit", payload={"kind":"template_replacement","record_id":run_id},
    idempotency_key=f"submit:{kind}:{record_id}", timeout_sec=120)
```

**E2. handler → runner** `generation/worker.py:14-16` → `runner.py:120-230`

```python
record = await store.load(record_id)     # ReplacementStore.load
if record.terminal: return               # success / failed 不再动
if record.submitted:      external_id = record.external_id      # 幂等跳过
elif record.submit_unknown: → 直接失败（可能已计费，绝不自动重提）
else:
    await store.mark_submitting(record_id)   # 先落「提交意图」
    external_id = await provider.submit(request)
```

`ReplacementStore`（`replacement_pipeline.py:19-101`）是文件档案到框架 `GenerationRecord` 的适配层：

| 框架方法 | 落到 run.json |
|---|---|
| `mark_submitting` | `submit_attempts += 1` |
| `mark_submitted` | `task_id`、`status=running`、`submit_attempts=0` |
| `mark_progress` | `progress_percent`（只增不减，封顶 99） |
| `mark_succeeded` | `status=success`、`progress_percent=100` |
| `mark_failed` | 按有无 `task_id` 区分 `failed` / `submission_unknown` / `waiting_executor` |

**先 `mark_submitting` 再调用上游**，是为了让「进程在两步之间崩掉」这种情况可判定——
有意图标记、无 `task_id`，重启后一律按「结果未知」处理，绝不重提。

**E3. provider.submit** `infra/ai/libtv.py:112-134`

```python
if request.params["archiveRunId"] != request.request_id: raise BadRequest(...)  # 只能从替换模板入口进来
if previous.task_id: return f"{value}:{previous.task_id}"
if previous.status == "submission_unknown": raise UpstreamError("提交回执未知，禁止重提")
if previous.status != "uploaded": raise BadRequest("输入尚未完成上传审核，请先继续准备")
result = await execute_stage(value, "submit")
```

也就是说：**provider 自己会再校验一遍状态**，不依赖 runner 的判断。
`external_id` 的格式是 `"{run_id}:{task_id}"`，轮询时用它拆回两段。

**E4. bridge：submit 分支** `bridge.mjs:50-57`

```js
if (meta.task_id) return;                      // 已有 task_id 直接返回
if (meta.status !== 'uploaded') throw Error('Refusing duplicate or uncertain submission');
stage = 'submit';
const result = await submit(api, run, meta.id, async () => true);
if (result.state === 'waiting_free') await update(run, {status:'waiting_free'});
```

实际提交由 legacy `libtv/workbench/submit.mjs` 完成，它把 `task_id` 写进 `run.json`。
提交阶段超时只有 **100 秒**（`libtv_process.py:17`），比上传短得多——提交是一个请求。

**E5. 提交成功后** `runner.py:217-230`

```python
await store.mark_submitted(record_id, external_id)   # run.json: status=running, task_id
await session.commit()
emit(EVT_GENERATION_SUBMITTED)
await self._schedule_poll(kind, record_id, polls=0, delay=settings.generation_poll_interval_sec)  # 2s
```

---

### 阶段 F｜轮询（每 2 秒一跳，最多 150 跳）

**F1. 轮询不是 sleep，是延迟重投** `runner.py:342-361`

```python
Job(name="generation.poll", payload={kind, record_id, polls},
    idempotency_key=f"poll:{kind}:{record_id}", max_attempts=1, timeout_sec=60)
```

文件头注释（`:15-18`）解释了原因：handler 里 sleep 会占住并发槽位，一个 3 分钟的视频
就让 2 并发变成实际 2 并发；延迟重投下槽位只在「真正调 API 的几十毫秒」被占用。

**F2. 一跳做什么** `runner.py:233-339`

1. 载入记录；无 `external_id` → 重新 dispatch（状态被重置过的情况）
2. `polls >= GENERATION_MAX_POLLS(150)` → 判超时失败，文案「生成超时，请重试」
3. `provider.poll(external_id)`

**F3. provider.poll** `infra/ai/libtv.py:136-173` → `execute_stage(run_id, "poll")` → `bridge.mjs:38-49`

```js
const response = await api.request('/api/task/generation/progress', {taskIds:[meta.task_id]});
await save(run + '/records/status.json', response);        // 原始响应留档
const status = {0:'queued',1:'running',2:'generated',3:'failed',4:'timeout'}[progress.status];
await update(run, {status, progress_percent: progress.progressPercent,
                   credits: progress.power, failed_reason: progress.failedReason});
```

上游状态码 0–4 的映射就这一处。`credits` 也在这里回填。

**F4. 状态翻译回框架** `libtv.py:147-173`

```python
if meta.status == "generated":
    await get_queue().enqueue(Job(name="libtv.download", ...))   # 下载独立成 job
status = SUCCEEDED if meta.status=="success" else FAILED if meta.status in ("failed","timeout") else RUNNING
```

注意：`generated` 被翻译成 **RUNNING**，同时**投一个 download job**。
下载是非计费工作，不占「每次查询 60 秒」的预算，也不影响轮询节奏。

**F5. 回到 runner**

| 上游状态 | runner 动作 |
|---|---|
| `RUNNING` 且进度变了 | `mark_progress` → commit → `emit(PROGRESS)` → 2 秒后再跳 |
| `SUCCEEDED` | `mark_succeeded` → commit → `emit(SUCCEEDED)` → **停** |
| `FAILED` 且可重试且未到上限 | 指数退避（2s→4s→8s→16s，封顶 30s）后再跳 |
| `FAILED` 且不可重试 | `mark_failed` → commit → `emit(FAILED)` → 停 |

`handle_poll` 外层还有一层 `except`（`:328-336`）自己兜住：轮询 job 是 `max_attempts=1`，
异常冒到 worker 会被直接丢弃，任务就永久停在 `running`——所以这里把「这一跳失败」
降级成「继续下一跳」。

---

### 阶段 G｜下载 + 收尾

**G1. download handler** `replacement_worker.py:209-237`

```python
result = await execute_stage(run_id, "download")
if result.code != "OK":
    delay = _bump_retry(repo, path, meta, reason="成片下载失败")
    if delay is None: return              # 5 次用完 → service_error / RETRY_EXHAUSTED
    await get_queue().enqueue(Job(name="libtv.download", ...), delay=delay)
```

档案被删掉时（`NotFound`）直接放弃，否则每 30 秒报一次同样的错。

**G2. bridge：download 分支** `bridge.mjs:32-37`

```js
if (!meta.task_id) throw Error('No task ID');
stage = 'download';
await poll(api, run);      // legacy canvas-run.mjs 的 poll()
```

下载超时 **240 秒**（`libtv_process.py:17`），是所有阶段里最长的。

> 边界说明：固定版适配器已随代码入库在 `libtv_runtime/vendor/`，`poll()` 的落盘细节
> （`canvas-run.mjs`）现在**就在本仓库内**，可继续追溯。可确认的契约是：
> 该函数返回后 `run.json` 的 `status` 变成 `success`、`outputs/` 下出现成片——
> 下一跳 `provider.poll`（`libtv.py:142`）读到 `success` 就不再调上游，直接判成功。

**G3. 下一跳轮询看到 success** `libtv.py:142-146`

```python
if meta.status != "success":
    result = await execute_stage(run_id, "poll")   # 已 success 就不再调上游
```

于是 `provider.poll` 返回 `SUCCEEDED` → `runner.mark_succeeded` → `run.json` 变成
`status=success, progress_percent=100`，并发 `generation.succeeded` 事件。

> 这里有两个状态字段，别混：`status` 是**给人看的业务状态**（`prepared/uploading/running/success`…，
> 前端渲染的就是它，见 `run_out()`）；`framework_status` 是**给编排器看的框架状态**
> （`queued/running/success/failed`）。`ReplacementStore.load()` 把 `framework_status` 映射成
> `GenerationRecord.status`，`terminal` 也按它判定（`replacement_pipeline.py:32,40`）。
> 所以下载把业务状态改成 `success` 之后，还要等下一跳把 `framework_status` 也置成 `success`，
> 编排器才会真正停下来——两个字段的推进时机不同，是这套文件存储与框架解耦的代价。

**G4. 前端看到结果** `replacement.queries.ts:12` 的 5 秒轮询拿到 `success`，
`run_out()`（`replacement_service.py:58-62`）检测到 `outputs/generated.mp4` 存在，
于是填上 `outputUrl = /api/v1/templates/replacement-runs/{id}/output`。

`ReplacementTemplates.tsx:179` 用 `<ArchivedMedia url type="video" />` 直接播放；
点「查看档案」弹窗（`:173-184`）还能看到 `taskId / estimatedCredits / credits / prompt`。

`GET .../output` 最终由 `replacement_router.py:51-55` 的 `FileResponse` 把
共享盘上的 `outputs/generated.mp4` 流回去。

---

## 4. 状态机（`run.json` 里的 `status`）

```
                     ┌──────────── prepare（同步）
                     ▼
                 preparing ──异常──► prepare_failed ──┐
                     │                              │ 用同一配置再点
                     ▼                              ▼
                  prepared ────── execute ──────► queued
                                                   │
                                    ┌──────────────┼───────────────┐
                                    ▼              ▼               ▼
                              waiting_executor  uploading      waiting_free
                                    │（退避重投）    │            （报价非零，停）
                                    └──────────────┘               │
                                                   ▼               │
                                               uploaded ◄──────────┘（变免费后人工继续）
                                                   │ submit
                                                   ▼
                                               submitting
                                                   │
                                    ┌──────────────┴──────────────┐
                                    ▼                             ▼
                                 running                    submission_unknown（禁止重提）
                                    │
                          ┌─────────┼─────────┐
                          ▼         ▼         ▼
                      generated   failed    timeout
                          │（下载）  │
                          ▼         ▼
                       success   service_error
```

| 状态 | 界面文案 | 谁能推进它 |
|---|---|---|
| `preparing` | 正在准备输入 | prepare（同步） |
| `prepare_failed` | 准备失败 · 可继续 | 人工再点准备 |
| `prepared` | 待核对 | 人工点执行 |
| `queued` | 排队中 | worker |
| `uploading` | 上传及审核中 | bridge upload |
| `uploaded` | 已上传 | bridge upload（估价 0） |
| `waiting_free` | 报价非零 · 等待免费 | 人工 / 上游变价 |
| `waiting_executor` | 账号执行器忙 / 同一 run 已有执行器 | 退避重投 |
| `submitting` | 提交中 | bridge submit |
| `running` | 生成中 | bridge poll |
| `generated` | 下载中 | download job |
| `success` | 已完成 · 待验收 | — |
| `failed` | 生成失败 | 人工重试 |
| `timeout` | 上游超时 | 人工重试 |
| `submission_unknown` | 提交结果未知 · 禁止重提 | 只能人工核对 |
| `service_error` | 连接待恢复 | 人工继续 |

---

## 5. 一次 run 在共享盘上的产物

```
$MEDIA_ARCHIVE_ROOT/
├── catalog/
│   ├── runs/{run_id}.json          ← 索引指针 {path: "runs/2026/11/06/product-<id>"}
│   └── locks/{run_id}.lock         ← prepare 的幂等锁
└── runs/2026/11/06/product-<run_id>/
    ├── run.json                    ← 清单：状态、指纹、task_id、进度、积分、校验和
    ├── 00_本次调用说明.md
    ├── inputs/
    │   ├── reference-1.png ...      ← 目标产品参考图（实际字节副本）
    │   ├── reference-video.mp4      ← 原片（随绑定表上传的那份）
    │   ├── source-original.mp4      ← 原片保真留档
    │   ├── prompt.txt               ← 最终提示词（用户核对的就是它）
    │   ├── prompt-spec.json         ← 编译器输入
    │   ├── configuration.json       ← 原始请求体
    │   ├── input-map.json           ← 每个输入 → 上游 URL 的映射
    │   ├── reference-pack.json      ← 仅素材包路径才有
    │   └── request.json             ← bridge 组的上游请求体
    ├── records/
    │   ├── prompt-compilation.json  ← 编译元数据 + token 绑定
    │   ├── media-inputs.json        ← 每张图的宽高时长
    │   ├── uploads.json             ← 上传结果（url + 审核状态），重跑时复用
    │   ├── estimate-preflight.json  ← 估价原始响应
    │   ├── status.json              ← 每跳上游进度原始响应
    │   ├── operation-<uuid>.json    ← 每次子进程的 ExecutorResult
    │   ├── executor-result.json     ← 当前阶段的执行记录（含 node_pid）
    │   ├── executor.lease           ← OS 锁，防并发
    │   └── worker.lock              ← 旧版遗留锁，退出时清理
    ├── outputs/generated.mp4        ← 成片（前端播放的就是它）
    ├── review/review.json           ← 人工验收 {"status": "pending"}
    └── code/
        ├── runtime/                 ← 本次编译用的编译器快照
        ├── runtime-v2/bridge.mjs    ← 本次执行用的 bridge 快照
        ├── canvas-transport.mjs / canvas-run.mjs / canvas-media.mjs / submit.mjs
        └── vendor-manifest.json     ← 本次用的固定版适配器哈希清单
```

`code/` 这一组是**可追溯性设计**：任何一次出片，都能查到当时用的是哪版编译器、
哪版 bridge、哪版 legacy。出问题不需要猜。

---

## 6. 失败与恢复

| 场景 | 谁兜住 | 结果 |
|---|---|---|
| 上传时执行器被占用 | `prepare_upload` 的 `EXECUTOR_BUSY` 分支 | `waiting_executor`，30s 起翻倍，5 次后 `RETRY_EXHAUSTED` |
| 下载失败 | `download` handler | 同上，状态 `service_error` |
| API 进程重启 | `modules/template/__init__.py` 的 `startup=resume` | 扫描所有档案，`task_id` 在的续轮询，`queued/uploaded` 的重投上传 |
| 重启时正好在提交 | `recover_state` | 有 `submit_attempts` 无 `task_id` → `submission_unknown`，**禁止自动重提** |
| 提交超时 / 5xx | `runner._possibly_delivered` | 按「可能已送达」处理，保留意图标记，人工核对 |
| 提交被 4xx 拒绝 | 清 `submit_attempts` 后重试 | 安全重投 |
| 共享盘断连 | `infra/archive/local.py` `LocalArchiveStorage.root()` | `UpstreamError`，**明确不回退本地保存** |
| 发起人被停用 | `prepare_upload` 的 `owner_disabled` | 未提交的停掉（`OWNER_DISABLED`），已提交的继续跑完 |
| 轮询入队失败 | `_schedule_poll` / `_fail_orphan` | 重试一次，仍失败则显式置失败，不留 `running` 僵尸 |

---

## 7. 三个关键不变量

1. **提示词快照不可变**。`bridge.mjs:61` 会在每次上传前比对
   `inputs/prompt.txt` 与 `records/prompt-compilation.json` 里的 prompt，
   不一致直接 `Prompt snapshot changed`。用户核对的提示词 = 实际上传的提示词。
2. **只有 0 积分才提交**。估价在 `bridge.mjs:102-110` 做一次预检，
   `LibTVProvider.submit` 又用 `previous.status != "uploaded"` 兜一次。
   两道闸，没有「先花钱再报错」的路径。
3. **提交一次，绝不自动重提**。`task_id` 一旦存在，所有入口都走 `runner.resume()`；
   没有 `task_id` 但有 `submit_attempts`，一律 `submission_unknown`。

---

## 8. 运行前置条件

| 条件 | 配置项 / 环境变量 | 缺失时的表现 |
|---|---|---|
| 共享盘可读写 | `MEDIA_ARCHIVE_ROOT` | 「素材归档盘不可用，请检查 Z 盘连接」 |
| Node 可用 | `NODE_BINARY`（旧名 `LIBTV_NODE` 仍生效，默认 `node`） | 「提示词编译器不可用，请检查 Node 配置」 |
| 固定版适配器 | `libtv_runtime/vendor/`（随代码入库，无需配置） | manifest 哈希不符 → `RELEASE_INVALID` |
| 已登录的浏览器会话 | 环境变量 `JS_REVERSE_BROWSER_URL` | `LOGIN_REQUIRED`（状态显示「需要登录」） |
| worker 开启 | `WORKER_ENABLED=true` | 「后台执行器未启用」 |
| 权限 | `template:use` + `asset:read` | 403 |

`JS_REVERSE_BROWSER_URL` 只从**进程环境变量**读取（`bridge.mjs:31,69`），
没有进 `core/config.py`，也没有写进 `.env.example`——这是当前链路里最容易漏配的一项。

---

## 9. 队列续接与恢复（合并 PR #5 后的口径）

> 这一节来自 PR #5 的调用链评审：续接（continuation）与恢复矩阵原来散在代码注释里，
> 集中记下来，改动时先看这张表。

### 队列续接

memory 驱动在 dequeue 时释放待执行幂等键。db 驱动保留 running 行和租约，因此不能把 handler 的下一轮 enqueue 当成普通外部重复请求。

worker 在 handler task 创建前进入 job_execution 上下文。db enqueue 对当前 handler 的同键后继，将载荷、delay、超时、attempts、trace 等写入当前行的 next_job。ack 在凭据匹配时原子提升后继为 pending；其他外部重复 enqueue 仍只命中当前活动行。

nack 或崩溃接管丢弃失败执行留下的暂存后继，由重试的 handler 重新决定；旧租约持有者不能安排后继或删除新持有者的作业。领取时还比较读取到的状态和 token，防止 ack 刚提升后继却返回旧 payload。

轮询作业外层预算为 120 秒，内部使用其 80% 预算，给事务收尾和后继调度留下时间。百炼的一次查询与下载各最多 45 秒，完整正常路径可容纳于预算内。内部执行预算超时进入普通退避续查，不会仅耗尽队列重试而永久留下 running。

### 恢复与重提

| 状态/凭据 | 行为 |
|---|---|
| prepared | 保持待确认，启动不执行 |
| queued/uploading/uploaded/waiting_executor 且无未知提交 | 恢复已授权准备 |
| 有 task_id | 只续查，不再上传创建任务 |
| 数据库无 task_id，但可信成功回执存在 | 补数据库任务号后续查；确认重提也必须拒绝 |
| 无 task_id 且 submit_attempts > 0 / submission_unknown | 不自动重提，先核对上游 |
| success | 不重新执行 |
| failed/service_error 且已有 task_id | 人工继续可重开查询预算 |

继续查询保留 owner、身份、回收站访问控制，但不因素材包下架或原素材删除而阻断已付费任务取回结果。新提交仍检查素材有效性，以及他人原片是否继续共享。

计费模式只在未提交阶段可变，进入提交意图或已有上游任务号后不能切换到另一队列。模式变更和成功回执复核位于同一非阻塞执行器租约，避免受理中的任务被旧快照改变模式。
