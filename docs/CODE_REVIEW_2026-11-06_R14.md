# 后端代码评审 R14（2026-11-06 14:10）

> 范围：后端 `api/`，全局性代码、核心骨架与新增的扩展能力。
> 手法：基线 → 确认增量 → 逐层读码 → 临时探针（真实临时归档盘 + 真实 queue / manifest）实测 → 清理探针 → 全量回归。
> 本轮**只新增报告，未改动任何生产代码**。

---

## 一、结论

### 增量

11:50 之后后端无新改动（`find api/src api/tests api/migrations -newermt "2026-11-06 11:50"` 返回 0 个文件），工作区只有文档变更。R13 的四条（R13-01 ~ R13-04）**仍未修**，因此本轮不再重复它们，而是把范围挪到前十三轮**一次都没深挖过**的模块。

前十三轮的重心分布：

| 轮次 | 覆盖 |
|---|---|
| R1–R10 | `modules/identity`（鉴权、限流、口令、会话吊销） |
| R11–R13 | `generation/*`、`modules/image`、`infra/ai/*`、`core/audit`、`scheduler` |

按代码规模排，未审的正好是最大的三块：

| 模块 | 规模 | 前十三轮 |
|---|---|---|
| `modules/identity` | 3167 行 | R1–R10 反复审 |
| `modules/task` | 1288 行 | 仅被 R13 顺带看过 startup |
| **`modules/template`** | **1137 行** | **从未读码** |
| `modules/image` | 1023 行 | R11–R13 |
| **`modules/asset`** | **599 行** | **从未读码** |
| **`infra/archive` + `infra/storage`** | **243 行** | 只在 R11 看过 `persist_output` 一侧 |

本轮审这三个。

### 质量基线（全部实测）

| 检查 | 结果 |
|---|---|
| `pytest -q --no-header` | **346 passed**（80.74s） |
| `ruff check .` | `All checks passed!` |
| `mypy src` | `Success: no issues found in 144 source files` |
| `app export-openapi --check` | 四份生成物**全部「无变化」** |

基线全绿，而下面的 🔴 就藏在这片绿色里。

### 本轮结果

| 级别 | 编号 | 一句话 |
|---|---|---|
| 🔴 | R14-01 | 模板替换的「提交回执未知」是**永久死锁**，而专为防这个死锁写的架构守卫被 `retry_count` 这个无关关键字骗过 |
| 🟡 | R14-02 | `resume()` 的恢复集合**只增不减**：每条历史成功记录每次启动都被重投一次 |
| 🟡 | R14-03 | `catalog` / `list_runs` 全量 glob + 逐条读两遍，owner 过滤在 Python 里做，无分页 |
| 🟡 | R14-04 | 三处**服务端绝对路径**进了 HTTP 契约，前端零消费 |
| 💭 | ×6 | 见第六节 |

---

## 二、🔴 R14-01 模板替换的「提交回执未知」是永久死锁，而守卫被关键字骗过

### 现象

`modules/image` 在 R12 拿到了 `POST /images/{id}/retry` + `confirmCharged` 的出口。`modules/template` 里**同一个状态、同一句文案、没有出口**，并且模块自己的三个函数分别把它堵死：

| 位置 | 行为 |
|---|---|
| `replacement_worker.py:109-110` | `if not meta.task_id and (status == "submission_unknown" or submit_attempts > 0)` → `raise Conflict("提交回执未知，禁止重提，请先核对上游记录")` |
| `replacement_worker.py:164` | `prepare_upload` 遇到同样的条件**直接 return**，连 `execute_stage` 都不进 |
| `replacement_worker.py:258-259` | `recover_state` 把它定格成 `submission_unknown` + `framework_status=failed`，返回 `done` |

三条路都关掉之后，全后端唯一会清 `submit_attempts` 的地方是 `generation/runner.py:227` 的「确定没送达」分支——而它只在 `handle_submit` 里执行，前提是记录能被 `dispatch`。`schedule_state` 在 `dispatch` 之前就抛了，`prepare_upload` 在 `execute_stage` 之前就返回了。**这条分支对模板永远不可达。**

`replacement_pipeline.py:58` 那个 `clear_submit_attempt` 因此成了死代码——它存在，但没有任何调用方能到达它。

### 实测（探针 1）

造一条 `status=submission_unknown / submit_attempts=1 / task_id=None` 的档案（就是「进程在供应商已受理与写库之间被杀」的现场），然后模拟用户反复点「继续」：

```
[probe1] 第1次点「继续」 -> Conflict: 提交回执未知，禁止重提，请先核对上游记录
[probe1] 第2次点「继续」 -> Conflict: 提交回执未知，禁止重提，请先核对上游记录
[probe1] 第3次点「继续」 -> Conflict: 提交回执未知，禁止重提，请先核对上游记录
[probe1] recover_state -> done
[probe1] 之后 status=submission_unknown attempts=1 framework_status=failed
[probe1] prepare_upload 后 status=submission_unknown attempts=1
```

点 1 次和点 100 次完全一样。恢复器认为「没事可做」，`prepare_upload` 认为「不该动」。这条记录此后**只能靠运维进共享盘手改 `run.json`**。

### 为什么是 🔴

1. **钱已经花了。** `LibTVProvider.submit` 的实际提交发生在 `execute_stage(value, "submit")`（`infra/ai/libtv.py:162`），`submission_unknown` 的定义就是「这一步可能已经成功」。记录卡住 = 已付费的成片永远拿不回来。
2. **文案是空头支票。** 与 R11-02 完全同一句话「请先核对上游记录」，而 R11 把这条定级为 🔴 并修了 `image`。同一个不变量在 `template` 上仍然破着。
3. **模块自己的注释是反的。** `test_replacement_recovery.py:239` 的参数化用例 `("submission_unknown", 1)` 断言记录停在该状态——只锁住了「停下」，没锁「怎么出去」。

### 更值得说的：守卫为什么没拦住

`tests/test_architecture.py:379` 有一条守卫，名字就叫 `test_every_kind_has_a_user_retry_exit`，docstring 写得很到位：

> runner 对 submit_unknown 的记录一律判死，此后 `handle_submit` 的终态早退让它再也动不了；没有 retry 出口时这条记录永久死锁（R11-02 现象 A）。

但它的判定是文本匹配（`test_architecture.py:365-376`）：

```python
return bool(
    re.search(r"@router\.post\([^)]*retry", text)
    or re.search(r"\b(?:async\s+)?def\s+retry\w*", text)
    or "retry_count" in text          # ← 为了给 template 开的口子
)
```

第三条是为 template 加的，注释写「template 用的是 run.json 的 `retry_count`，语义不同但同样是出口」。**它确实语义不同，而且不是出口**：

- `retry_count` 统计的是**自动**重试（执行器占用 / 成片下载失败），计数在 `_bump_retry`（`replacement_worker.py:85-104`）；
- 它与 `submit_attempts` 是两个字段，`schedule_state:122` 会在用户点「继续」时把 `retry_count` **清零**——正好发生在被 `submit_attempts` 拦下**之前**；
- 也就是说 `retry_count` 连「用户点得动」都证明不了，只证明「存在自动重试」。

实测这条守卫：

```
[guard] _module_has_retry_exit(template) = True
[guard] _module_has_retry_exit(image)    = True
```

两边都 True，一边真有出口，一边没有。这是本项目已经出现过三次的同一个形状——**守卫只验证了「形式满足」，没有验证「约束成立」**；只不过这次它连形式都只是同名不同义。

### 建议

1. **补出口**，照 `image` 的形状（`service.py:131-190`）：`POST /replacement-runs/{id}/reconfirm`，body `{confirmCharged: bool}`。默认 409；`confirmCharged=true` 时先写审计（保留旧的 `task_id` 若存在、记录操作人），再把 `submit_attempts` 清零、`status` 置 `queued`、`framework_status` 置 `queued`。权限点用现有的 `template:use` 即可，若要更严可加 `template:reconfirm:any`。
2. **守卫改成能分辨状态**，别再用纯文本。最小改法：把 `_module_has_retry_exit` 换成「对 `SUBMIT_UNKNOWN` 状态调用模块的 retry 入口，断言不抛 Conflict」的表驱动用例——和 R12/R13 建议的跨组件状态机表是同一张表，顺带把 `template_replacement` 加进 `test_generation_state_machine.py`（目前它只覆盖 `image`）。
3. **顺手**：`clear_submit_attempt` 在 `replacement_pipeline.py:58` 是死代码，补了出口后它才会被走到；在此之前它给守卫一种「已有清零能力」的错觉。

---

## 三、🟡 R14-02 `resume()` 的恢复集合只增不减，每次启动重投所有历史成功记录

### 现象

`replacement_worker.py:282-298`：

```python
async def resume() -> None:
    if settings.app_env == "test" or not settings.worker_enabled:
        return
    ...
    for path in paths:
        meta = await asyncio.to_thread(repo.load, path)
        if (
            meta.task_id                       # ← 命中所有历史成功记录
            or meta.submit_attempts
            or meta.status in ("queued", "uploading", "uploaded", "waiting_executor", "submitting")
        ):
            await enqueue_recovery(meta.id)
```

`meta.task_id` 这个条件让**每一条成功过的记录**都进恢复队列。虽然 `recover_state` 随后对 `status == "success"` 返回 `done`（不产生副作用），但代价已经付了：一次入队 + 两次磁盘读（`all_paths` 读指针、`repo.load` 读 `run.json`）。

更麻烦的是 `failed` 那一类：`recover_state:254` 的条件是 `meta.task_id and meta.status != "success"`，于是**本地已判定失败**的记录被翻回 `framework_status="running"` 并返回 `poll`，`recover()` 随后 `get_runner().resume()` 真的去上游再查一次。

### 实测（探针 2）

四条记录：成功（有 task_id）、失败（有 task_id）、排队中、仅准备完成。模拟两次启动：

```
[probe2] 第1次启动投递 3 条
[probe2]   10fa57:template.replacement.recover:0
[probe2]   b696a6:template.replacement.recover:0
[probe2]   e947a2:template.replacement.recover:0
[probe2] 第2次启动投递 3 条
[probe2]   10fa57:template.replacement.recover:0
[probe2]   b696a6:template.replacement.recover:0
[probe2]   e947a2:template.replacement.recover:0
[probe2] failed 记录 recover_state=poll status failed->failed framework_status None->running
[probe2] success 记录 recover_state=done
[probe2] prepared 记录 recover_state=done
```

两次启动投递的**完全同一批**。也就是：成功记录永远白投一次、失败记录永远被翻回 running 并再查一次上游，集合永不收敛。`repair_indexes` 之前已经全盘 glob 过一次，加上 `all_paths` 与逐条 `load`，启动成本随历史线性增长。

### 建议

- `success` 直接排除：`if meta.status == "success": continue`（它已有的 `task_id` 不再需要任何续查）。
- `failed` / `service_error` 要不要重查，应该是一个**显式决定**而不是副产品：如果确实想「本地失败但上游可能成功」的重查，就用 `retry_count` 给它一个上限（这个字段现在正好没被这条路径用上），并在 `last_error` 里留痕；否则一并排除。
- 排序上先读 `run.json` 再决定是否入队——现在为了决定要不要入队必须先读一遍，等于没有节省，真正要省的是入队本身。

---

## 四、🟡 R14-03 `catalog` / `list_runs` 全量 glob + 每条读两遍，owner 过滤在 Python 里

### 现象

`replacement_service.py:66-70`：

```python
def list_runs(owner: str) -> list[ReplacementRunOut]:
    repo = ReplacementRepository()
    rows = [run_out(path, repo) for path in repo.all_paths() if repo.load(path).owner_id == owner]
```

`repo.load(path)` 在列表推导里被调用**两次**（一次做 owner 过滤、一次在 `run_out` 内部）。而 `all_paths()`（`replacement_repository.py:102-106`）先 glob 全部指针、逐个读出来 `resolve`。

`catalog`（`:289-293`）把它和 `repo.packs()` 一起，在**每次打开页面**时跑一遍。`ReplacementCatalog.runs` 是 `list[ReplacementRunOut]`，**没有分页参数**，`GET /replacement-catalog` 也没有 `limit` / `offset`。

`modules/asset` 是同一个形状：`ArchiveAssetRepository.list()`（`archive_repository.py:30-34`）glob 整个 `catalog/assets`，`ArchiveAssetService.list_assets` 再在 Python 里按 `owner_id` 与 `category` 过滤，`ArchiveList` 也只有 `total` 没有分页。

### 实测（探针 3）

```
[probe3] 20 条记录 list_runs -> 20 行，磁盘读取 60 次
[probe3] 其中 run.json 40 次，指针 20 次
[probe3] list_runs 参数 = ['owner']；ReplacementCatalog 无分页字段
[probe3] 加一条他人记录后再查：返回 20 行，读取 62 次
```

20 条记录 = 60 次读（每条被读 3 次：1 次指针 + 2 次 `run.json`）。加一条**别人的**记录，返回行数不变但读取次数涨到 62——说明 owner 过滤发生在读完文件之后，别人的档案一样要读。

共享盘上的目录读延迟不是内存级别，这条在记录上千之后会直接反映成页面打开时间。

### 建议

- `run_out` 内部不要再 `repo.load`，把已读到的 `meta` 传进去（一次改掉 40 次读里的 20 次）。
- 指针文件 `catalog/runs/{uid}.json` 现在只存 `path`。让它顺带存 `ownerId` / `status` / `createdAt`，owner 过滤与排序就能在指针层完成，`run.json` 只在真正命中时读——这是把过滤**下推到索引**，和 R11-04 把 `LIMIT` 下推到 SQL 是同一件事。
- `GET /replacement-catalog` 与 `GET /assets/archive` 加分页（`ArchiveList.total` 已经在了，缺的是 `limit`/`offset` 和前端的分页组件）。

---

## 五、🟡 R14-04 三处服务端绝对路径进了 HTTP 契约

`openapi.json` 里实测：

```
ReplacementRunOut  -> {'archivePath': ('string', None)}
ReplacementCatalog -> {'root': ('string', None)}
ArchiveList        -> {'root': ('string', None)}
```

来源分别是 `replacement_service.py:49`（`archive_path=str(path)`）、`:292`（`root=str(self.repo.storage.root())`）、`archive_service.py:45`（`root=str(self.storage.root())`）。

这三处输出的是**部署机的真实绝对路径**——共享盘 UNC（`\\\\fileserver\\...`）或本地盘（`Z:\\...`），里面通常带机器名、共享名、甚至用户名。它们已经随四份生成物同步进了 `schema.ts`，属于契约的一部分。

前端 `grep` 结果是**零消费**：

```
$ grep -rn "archivePath\|root" studio/src/entities/asset studio/src/entities/replacement
(无输出)
```

即三个字段是纯粹的泄漏，没有一处 UI 需要它。测试里用 `Path(service.prepare(...).archive_path)` 取路径，是把它当调试信息用。

### 建议

从契约里删掉这三个字段（`ReplacementRunOut.archive_path`、`ReplacementCatalog.root`、`ArchiveList.root`），需要排障时走日志。若确实想在页面上显示「归档位置」，改成相对路径（`runs/2026/11/06/character-xxx`）——`repo.index` 里已经是这么存的（`path.relative_to(self.storage.root()).as_posix()`）。删字段要同步重跑 `app export-openapi`，四份一起提交。

---

## 六、💭 观察项

1. **`schedule()` 用 502 表达配置状态**（`replacement_worker.py:129`）：`worker_enabled=false` 时 `raise UpstreamError("后台执行器未启用")`，前端会显示成「上游服务异常」，运维第一反应是去查 LibTV。这属于配置/容量问题，503 或 409 更准确。
2. **`error_code` 可能是字符串 `"OK"`**（`replacement_worker.py:188`）：`error_code = result.message or result.code`，而 `ExecutorResult.code` 默认就是 `"OK"`、`message` 默认空。当 `execute_stage` 返回 OK 但档案状态不是 `uploaded`/`waiting_free` 时，页面会显示「上传准备未完成：OK」。
3. **`recover()` 遇持续 Conflict 会无限重投**（`:268-280`）：`except Conflict: await enqueue_recovery(run_id, delay=30); return`，没有次数上限。`_bump_retry` 只管 prepare / download 两条线，这条没有。如果租约被一个僵死的进程长期占住（`clear_finished_lock` 需要确认退出才清），这里会每 30 秒重投一次，永不停止。
4. **`resume()` 里的 `app_env == "test"` 早退让整条恢复链在测试中永不执行**（`:283`）。本轮三条问题（🔴 + 两条 🟡）全部落在被这个早退遮住的函数里——测试套件结构上不可能发现它们。同理 `task` 的 `resume_unfinished_tasks` 没有这个早退，所以 R13-01 才被探针抓到了。
5. **`ReplacementStore` 不写 `submit_mode` 快照**（`replacement_pipeline.py:22-45`）。R12-05 新增这个字段的理由是「恢复必须解释记录自身的形态，而不是拿当前模型目录重新解析」。模板的记录一律 `submit_mode=None`，现在靠「不经过框架恢复器」侥幸无事；一旦按 R13/框架的方向删掉模块自有 `startup`，`_supports_poll` 就会退回目录解析——而 `LibTVProvider.supports_poll = True`（`libtv.py:139`），在 echo-only 的环境里仍会被误分类。
6. **两处文档/脚本与代码相反**：
   - `infra/storage/base.py:4` 写「在 `infra/__init__.py` 的工厂里加分支」，而 `infra/__init__.py:6` 明写「本文件**不再包含任何 if/elif**」，真实机制是 `infra/loader.py`。
   - `scripts/import_legacy_replacement_assets.py:18` 与 `scripts/refresh_archive_media.py:20` 仍直接 `from infra.archive.local import LocalArchiveStorage` 并自行实例化，绕过 `ARCHIVE_DRIVER`。`infra/archive/__init__.py` 的 docstring 说「以前 provider 里还有 5 处直接 `LocalArchiveStorage()`，要上 S3 得逐个改」——脚本这一层还没改完。这两处在 `ruff` 里是干净的，属于「以正确的方式写了错误的依赖」。

---

## 七、模式：第四种形状——守卫验证的是「有没有」，不是「通不通」

前几轮归纳过的三种：

1. 不变量只守一个入口（R5/R6/R8/R10）；
2. 两个正确的修复组合后破例（R12）；
3. 门控/条件贴错了抽象层（R13）。

本轮是第四种：**机械守卫把「存在某个东西」当成了「那个东西能解决问题」**。

`test_every_kind_has_a_user_retry_exit` 的意图完全正确，docstring 就写着「不能是空头支票」，实现却是对模块目录做三个正则之一命中。第三个正则为了给 template 开绿灯而加，而 `retry_count` 恰好是最不该算作出口的字段——它计的是自动重试，还在被拦下的那一刻被清零。

这和 `CODE_STYLE.md` §6「机械守卫」那一节想防的是同一件事，只是这次守卫本身成了被绕过的对象。往后的守卫，判据应该落在**行为**上而不是**文本**上：

- 「有 retry 出口」→ 对 `SUBMIT_UNKNOWN` 调一次，断言不抛 Conflict；
- 「模块 × 插槽按矩阵接全」→ 已经有 `test_every_pipeline_kind_declares_a_recovery_slot` 这种形态，可以照抄；
- 「恢复集合会收敛」→ 连跑两次 `resume()`，断言第二次投递数为 0（本轮探针 2 就是这个断言，可以直接提成用例）。

---

## 八、建议顺序

| 顺序 | 事项 | 规模 |
|---|---|---|
| 1 | R14-01 出口 + 把守卫换成行为断言 | `replacement_router/service/worker` 约 50 行，+2 条用例 +1 条架构守卫 |
| 2 | R14-02 `resume()` 收敛（排除 success，失败类显式决定） | `replacement_worker.py` 约 10 行，+1 条用例（连跑两次断言 0） |
| 3 | R14-04 删三个绝对路径字段 | 3 处 schema + 重跑 `export-openapi` 四份 |
| 4 | R14-03 去重复 load + 指针存 owner/status + 分页 | 影响契约，建议单独一次提交 |
| 5 | R13-01 / R13-03（仍未修，两条都在 `app.py`，可合并一次） | 约 15 行 |
| 6 | 💭 六条 | 随手带 |

R13 的四条**本轮复核仍未修**，其中 R13-01（`worker_enabled=false` 时模块 startup 照跑并写库）与 R14-02 会叠加：前者在只读实例上跑恢复、后者让恢复集合永不收敛，两者相乘是「只读实例每次启动都往无人消费的队列里投一批历史记录，还顺手改库」。

---

## 附录：复现方式

本轮探针 `api/tests/test___probe_r14.py` 已删除。三段的骨架：

1. **R14-01**：在临时归档根下手工造一条 `runs/2026/11/06/character-<uid>/run.json`（`status="submission_unknown"`、`submit_attempts=1`、无 `task_id`）+ `catalog/runs/<uid>.json` 指针，然后连调三次 `schedule_state(uid, owner)`、一次 `recover_state(uid)`、一次 `prepare_upload(Job(...))`。
2. **R14-02**：造四条不同状态的记录，把 `modules.template.replacement_worker.get_queue` 换成记录调用的假队列，`settings.app_env="prod"` / `worker_enabled=True`，连调两次 `resume()`，比较两次投递集合。
3. **R14-03**：`monkeypatch.setattr(LocalArchiveStorage, "read", 计数包装)`，调 `list_runs("u_001")`，统计读盘次数；再加一条他人记录重跑，对比返回行数与读取次数。

守卫复现：`test_architecture._module_has_retry_exit(Path("src/modules/template"))` 返回 `True`。
