# 改进与优化计划 · 2026-11-18

> **来源**：以 `D:/xx/AI/supabase-python/SUPABASE_PYTHON_REFERENCE.md`（Supabase Python v3 生态架构参考）
> 为对照样本，结合本仓库 `main` @ `9d9dd07` 的实测现状给出。
>
> **对照样本的定位**：它是一个 **SDK 仓库**（7 个包、发布到 PyPI、公开 API 受合规矩阵约束），
> 而本仓库是 **业务应用**（FastAPI 后端 + React 前端 + 一套工作流）。
> 两者形态差距很大，**不能整套照搬**——照搬会引入不必要的复杂度。
> 因此本计划只取两类东西：
>
> 1. **机制层面**（不依赖「我是 SDK」这个前提）：重试双闸门、原子落盘、路径穿越防护、
>    日志脱敏、幂等键生命周期、类型契约回归测试、Markdown 代码块类型检查。
> 2. **判断标准**（用来审计本仓库自己的设计）：协议是否兑现了文档承诺、
>    两个实现是否语义一致、失败路径是否被测到。
>
> 每条都落到**本仓库的具体文件与行号**，并标注「已实测复现」或「静态判断」。
> 与 `docs/REVIEW_2026-11-15.md` / `docs/REVIEW_2026-11-17.md` 的既有条目**不重复**——
> 那两份已列的 P1/P2/P3 不在本计划里重复出现。

---

## 0. 一眼结论

| 项 | 值 |
|---|---|
| 计划条目 | **24 条**：P0 ×6 · P1 ×9 · P2 ×9 |
| 预计工作量 | **P0 约 4h · P1 约 12h · P2 约 16h**（合计约 32h） |
| 与参考文档的关系 | 机制借用 **6 项**（§1），判断标准借用 **4 项**（§2），**不抄** **5 项**（§3） |
| 当前门禁状态 | 后端 `pytest` **1409 passed / 12 skipped / 3 failed**（复跑实测，非过期日志）；失败根因已逐条定位（P0-6） |
| 动手前必做 | **先修 P0-6 的 3 条红测试**（约 5 分钟放宽超时 + 一次 vendor 重新 checkout） |

---

## 1. 参考文档里值得抄的机制（以及本仓库的落点）

对照文档 §4 的 8 个专题，按「本仓库是否真的缺」逐个判定：

| 参考机制 | 参考文档位置 | 本仓库现状 | 结论 |
|---|---|---|---|
| **重试双闸门**（幂等性 × 可重放性） | §4.3 `utils/http/retry.py` | `infra/http.py` 有重试，但**只有 GET**、无「方法幂等 ∧ body 可重放」的双闸门表达 | ✅ **抄**，见 P0-2 |
| **原子落盘 + 权限竞态防护** | §4.6 `storage/download.py` | `storage/local.py` 已有临时文件 + `os.replace`，但**无 0600 → chmod 的权限竞态防护** | ⚠️ 部分抄，见 P2-6 |
| **路径穿越防护**（`relative_path_to_parts`） | §4.6 `file_api.py` | ⚠️ **归档侧做了，资产库侧没做** —— 实测见 P0-3 | ✅ **抄**（补齐） |
| **日志脱敏三件套** | §4.7 `realtime/_redact.py` | `core/logging.py` **无任何脱敏**；`db/session.py:79` 打了 db_url | ✅ **抄**，见 P1-7 |
| **幂等键直到终态才释放** | §4.3 / §2.1 | ⚠️ **db 驱动对，内存驱动错** —— 实测见 P0-4 | ✅ **抄**（补齐） |
| **类型契约变成可回归测试** | §4.8 `test_typecheck.py` | ❌ 没有。mypy `warn_unused_ignores = false` | ✅ **抄**，见 P1-9 |
| **Markdown 代码块类型检查** | §4.8 `check_markdown_snippets.py` | ❌ 没有。`docs/` 里有大量 Python 代码块 | ✅ **抄**，见 P2-7 |
| **合规矩阵强制双登记** | §4.8 `sdk-compliance.yaml` | ✅ **已有等价物**（四份契约 + 字节比对 + 权限目录） | ⛔ 不抄 |
| **不可变链式 builder + filter 安全阀** | §4.2 `request_builder.py` | 不适用（本仓库走 SQLAlchemy ORM，不是 PostgREST DSL） | ⛔ 不抄 |
| **生成器 + 描述符统一 sync/async** | §4.1 `http/io.py` | 不适用（本仓库是纯 async 服务端，无 sync 客户端） | ⛔ 不抄 |
| **Realtime 状态机 / 退避重连** | §4.5 | 不适用（本仓库无 WebSocket 推送） | ⛔ 不抄 |
| **服务端签名替代客户端 S3 签名** | §4.6 | ✅ 已是（`S3Storage.presigned_*`） | ⛔ 不抄 |

---

## 2. 从参考文档借来的「判断标准」

这四条不是代码，是**审计本仓库设计的尺子**。用它们扫出来的结果见 §4。

1. **协议是否兑现了文档承诺**（参考文档 §2.1 索引 A 的隐含要求）
   → 扫出 **P0-1**（`StorageError` 承诺 502，实际 500）、**P1-3**（内存队列 `close()` 违反协议「不许清空队列数据」）。

2. **同一语义的两个实现是否语义一致**（参考文档 §7「不要抄的部分」提示的坑）
   → 扫出 **P0-3**（两套 `blob_key`）、**P0-4**（两套队列重试语义）、**P1-5**（`Cache.incr` 契约与实现分叉）。

3. **失败路径是否被测到**（参考文档 §4.8「类型契约回归测试」的动机）
   → 扫出 **P1-1**（`_persist` 落在错误的预算里）、**P2-2**（`S3Storage.presigned_*` 未包异常）。

4. **文档/注释里的数字是否还成立**（参考文档 §4.8 用 CI 强制文档与代码同步）
   → 扫出 **P2-8**（`CODE_STYLE.md` 基线快照停留在 346 passed，实际 1409）。

---

## 3. 明确不抄的部分（连同理由）

| 参考文档的做法 | 为什么本仓库不抄 |
|---|---|
| `unasync` AST 生成同步测试树 | 参考文档 §7 自己也说「sync/async 差异大时反而难维护」。本仓库只有 async 一路，没有生成必要 |
| `sdk-compliance.yaml` 1761 行全量手写 | 参考文档 §7 明确列为「维护成本高，除非你也做多语言 SDK 矩阵」。本仓库的四份契约已覆盖同等保障 |
| `pyrefly` strict preset + 36 条 error 规则 | 本仓库已有 mypy + ruff，选型不同不必统一。但**「strict 程度」这件事要抄**（见 P1-9） |
| `POST /object/list-v2` 游标分页 + 游标不前进检测 | 本仓库存储层是自研 `ObjectStorage` Protocol，未接 PostgREST 语义。**但「游标不前进 → 无限循环」这个坑在本仓库的对象列表上同样成立**，列入 P2 观察 |
| `asyncio.shield` 单飞刷新 | ⚠️ 部分适用：本仓库后端 refresh 走**条件更新抢占**（`identity/service.py:568` `rowcount != 1`），**比单飞更严格**——单飞是"共享结果"，抢占是"只允许一个赢家"。前端已有 `inflight` 单飞（`shared/access/token-refresh.ts:24`）。**不抄** |

---

## 4. 改进条目

### P0 — 会导致数据错误、任务卡死或安全漏洞（先做，约 4h）

---

#### P0-1 · `StorageError` 承诺 502，实际返回 500

**位置**：`api/src/infra/storage/base.py:16`（文档承诺）vs `api/src/api/errors.py:39-127`（无处理器）

`base.py` 的 docstring 写「存储层异常，由中间件转换为 502」，但 `errors.py` 注册的处理器只有
`AppError` / `RequestValidationError` / `StarletteHTTPException` / `IntegrityError` /
`PoolTimeoutError` / `StaleDataError` / `SQLAlchemyError` / `Exception`。
`StorageError` 继承 `Exception` 而非 `AppError` → 落到 `:126` 的兜底 → **HTTP 500 +「服务器内部错误」**。

**后果**：上游对象存储故障被报成「我们写错了」。生产告警分流会按 500 归到「代码 bug」，
而 `errors.py:74-79` 专门写了 500 与 503 的分流意义——这个分流在存储故障时被绕过了。

**改法**：让 `StorageError` 继承 `core/exceptions.py:228` 的 `UpstreamError`。
**注意**：`CacheError` 不要一起改——缓存故障应该**降级**而非报错，语义不同，保持现状。

**验收**：`StorageError` 抛出时响应码为 502，且带可读的 `code`。
**工作量**：15 分钟。

---

#### P0-2 · `fetch_bytes` / `fetch_to_file` 的重试缺「幂等性 × 可重放性」双闸门

**位置**：`api/src/infra/http.py:180-207`（`fetch_bytes`）、`:287-319`（`fetch_to_file`）

现状：两个函数都只有 GET，方法本身幂等，所以「不重试 POST」这条自动满足。但**缺的是另一半**：

- **退避无抖动**：`http.py:206` 是 `min(8.0, 0.5 * (2**attempt))`，固定序列。
  参考文档 §4.3 明确指出抖动的作用是**化解惊群**：一批 worker 同时失败时，
  固定退避会让它们在同一个时刻一起重试，把刚恢复的服务端再打垮。
  参考文档 §4.3 的注释还给了理由——「同批次任务本来就错峰投递，再加抖动只会让排查时的时间线更难对齐」，
  但那是**排查**的成本，代价是**惊群**的风险。
- **不处理 `Retry-After`**：服务端明确告诉客户端「等 N 秒再来」（429/503 常见），现在完全忽略。
- **不区分终止错误与可重试错误**：`RETRY_STATUS`（`:48`）含 500，而参考文档 §4.3 特意把 500 排除：
  「A 500 is left out, since PostgREST answers a failing query with it and a retry fails the same way」。
  同理，`400/401/403` 已在 `>= 400` 分支里正确地不重试，✅ 这条做对了。

**改法**：
1. 退避加 **full jitter**：`delay = min(8.0, 0.5 * 2**attempt) * (1 - 0.5 * random())`，
   并在注释里写清「为什么这次决定加抖动」——呼应参考文档的 AWS full jitter 推荐。
2. 读 `Retry-After` 头（支持秒数与 HTTP 日期两种格式），服务端给了就用它，且**它超过总预算时不重试**。
3. 把 `RETRY_STATUS` 里的 `500` 去掉，并在注释里说明理由（查询类错误重试同样会错）。

**验收**：新增用例断言「两次重试之间的等待不是固定值」；`Retry-After: 3` 时第一次重试等待 ≥3s。
**工作量**：1.5 小时（含用例）。

---

#### P0-3 · 资产库 `blob_key` 不做扩展名消毒，与归档版同名不同义

**位置**：`api/src/modules/library/service.py:127-128` vs `api/src/infra/archive/blobs.py:55-59`

```python
# modules/library/service.py:127
def blob_key(digest: str, extension: str) -> str:
    return f"{BLOB_PREFIX}/{digest[:2]}/{digest}{extension}"   # ← extension 原样拼

# infra/archive/blobs.py:55
def blob_key(sha256: str, suffix: str = "") -> str:
    return f"{BLOB_ROOT}/{sha256[:2]}/{sha256}{normalize_suffix(suffix)}"  # ← 消毒过
```

**实测**（同一 digest）：

```
archive.blob_key(digest, ".JPG")  -> blobs/aa/<sha>.jpg
library.blob_key(digest, ".JPG")  -> library/blobs/aa/<sha>.JPG
```

三处差异：前缀不同、扩展名大小写处理不同、预览版本后缀缺失。
两个函数**同名、都从 `infra/archive/__init__.py` 导出**（`:44`、`:46`），import 时极易拿错。

**参考文档 §4.6 的对应做法**：`relative_path_to_parts` 明确拒绝 `.` / `..` / 空段。
本仓库的归档版**已经抄对了**（`normalize_suffix` 把后缀限制在 `.<字母数字>`，`_SUFFIX` 正则），
资产库版**漏了**。

**实测路径穿越**：`blob_key(digest, "../../etc/passwd")` 在资产库版返回
`library/blobs/aa/<sha>../../etc/passwd`（未消毒），归档版正确返回空后缀。

**当前风险等级**：**尚不可利用**——`extension` 在上游已被约束：
- `service.py:677` 用 `Path(filename).suffix.lower()`，Python 的 `Path.suffix` 不会含 `/`（实测 `'../../etc/passwd'` → `''`）；
- `service.py:678` 还要求 `EXTENSIONS.get(extension)` 非空，白名单；
- `text_assets.py:143` 的 `payload.extension` 有 `pattern="^(md|txt)$"`。

所以这是**防御纵深缺失**而非可利用漏洞：今天安全是因为**调用方恰好挡住了**，
不是**函数本身挡住**。参考文档的价值正在于此——把「碰巧安全」变成「必然安全」。

**改法**：
1. `modules/library/service.py:127` 的 `blob_key` 复用 `infra/archive/blobs.py` 的 `normalize_suffix`，
   消除同名不同义。
2. 更彻底：把 `infra/archive/blobs.py` 的 `blob_key` / `preview_key` / `clip_key` 三个函数
   **只保留一份**（`BlobStore.ensure` / `adopt` 在 `src/` 下零调用，实测 `ensure`=0、`derive`=0、
   `adopt`=2 且两处都是 `trace.adopt` 同名不同义——基本是死代码）。
3. `verify_blobs` 扫的前缀（`integrity.py:53` 的 `"blobs"`）与资产库实际写入的前缀
   （`service.py:98` 的 `"library/blobs"`）**不一致**——见 P1-4，这是配套问题。

**验收**：`blob_key(digest, "../../etc/passwd")` 不再产生路径穿越；两套 key 函数合并为一套。
**工作量**：1.5 小时。

---

#### P0-4 · 内存队列在 dequeue 时释放幂等键，与 db 驱动语义相反

**位置**：`api/src/infra/queue/memory.py:60-61` vs `api/src/db/jobs.py:61-67`

```python
# memory.py:60
heapq.heappop(self._heap)
if job.idempotency_key:
    self._by_key.pop(job.idempotency_key, None)   # ← 立刻释放
```

db 驱动的部分唯一索引 `uq_queued_jobs_active_idempotency_key` 覆盖 `pending` **+ `running`**——
**运行中的 job 仍然占着键**。内存驱动没有这层保护。

**实测**：dequeue 后键已释放 → 重复 enqueue 成功 → 队列里出现 2 条同键 job。

**为什么是 P0**：`generation/runner.py:345-364` 的限流重投路径**显式依赖**幂等键来避免双计费，
注释写明「**必须带幂等键**…两条作业各调一次 `provider.submit()`…双计费」。
而 `QUEUE_DRIVER=memory` 是**默认配置**（`core/config.py:216`）——这条保护在默认配置下是失效的。

**改法**：内存驱动增加 `_running: dict[str, str]`（键 → job.id），在 `ack` / `nack` 时才释放；
或在 `enqueue` 时检查「键是否被 pending 或 running 占用」。
**顺带修 P1-3**（`close()` 不该清空队列）：`base.py:31` 的协议表已写明「不许清空队列数据」，
db 驱动的 `close()`（`db.py:561`）正确地什么都不做，内存驱动（`memory.py:100-101`）却 `clear()` 了。

**验收**：`QUEUE_DRIVER=memory` 下，同一键并发 enqueue 只产生一条；`close()` 后 `size()` 不为 0。
**工作量**：1 小时。

---

#### P0-5 · `recovery._fail_unrecoverable` 忽略 `mark_failed` 返回值

**位置**：`api/src/generation/recovery.py:113` vs 正确写法 `api/src/generation/runner.py:1004`

```python
# recovery.py:113 —— 不看返回值
await store.mark_failed(record.id, message)
await session.commit()
await get_event_bus().publish(EVT_GENERATION_FAILED, ...)

# runner.py:1004 —— 正确
if not await store.mark_failed(record_id, message):
    logger.info("记录已终态，跳过失败收尾 ...")
    return
```

`GenerationStore` 协议（`generation/store.py:22-27`）**明确规定**所有写方法返回 bool，
且「终态保护只有在调用方看返回值时才真正成立」。`recovery.py` 违反了这条约定。

**后果**：用户点了取消、紧接着启动恢复 → 用户先收到「已取消」，再收到「生成失败」，两个矛盾通知。

**改法**：`recovery.py:113` 改成 `if not await store.mark_failed(...): return`。
**工作量**：10 分钟。

---

#### P0-6 · 当前有 3 组测试是红的，根因各不相同（已逐条定位）

> ⚠️ **订正**：初次扫描 `var_pytest.log` 看到「5 failed」，那是**过期日志**（该文件早于最近的修复）。
> 实测复跑确认：**当前真正红的是 3 条**（`var_pytest.log` 里的 `test_config_guard` 与
> `test_library_tags` **单独跑与一起跑都是绿的**，24 passed）。下表是**复跑实测**的结果。

| # | 用例 | 实测根因 | 修法 |
|---|---|---|---|
| 1 | `test_libtv_vendor_manifest.py`（3 条） | **CRLF**。`.gitattributes:26` 的 `vendor/** -text` 规则**是对的**（`git check-attr` 确认 `text: unset`），但工作区里的文件仍是 CRLF——规则加上后没重新 checkout | `git rm --cached -r <vendor 目录> && git checkout -- <vendor 目录>`。**实测验证**：用 `git checkout-index` 检出一份到临时目录 → CRLF 0 个、sha256 `fbfe2c567b45` **与清单完全一致** |
| 2 | `test_db_queue.py::test_claim_cannot_return_pre_promotion_payload` | **超时预算太紧**。`dequeue(timeout=0.1)`，而该路径要先「claim 撞上 ack 提升 → 第一次拿不到 → 退避后重试」，实测耗时 0.093s，**紧贴 0.1s 上限**。pytest 环境里 DB 操作比独立脚本慢一个数量级，就翻车 | 把 `timeout=0.1` 放宽（实测 `timeout=2.0` **通过**）。同时这个用例的语义（不许返回过期 payload）是**对的**，代码的 fencing（`db.py:399-403` 注释「An ack may have promoted a different payload…retry rather than return it stale」）**行为正确**，只是测试给的时间不够 |
| 3 | `test_db_queue.py::test_real_worker_consumes_same_key_handler_chain` | **同一类**：3 步续接链，pytest 环境实测 **1.33s**（独立脚本只要 0.11s），但用例硬编码 `asyncio.wait_for(..., timeout=1)` | 放宽到 `timeout=5`（实测**通过**） |

**根因归纳（这条比三个用例本身更重要）**：

每个用例各自 `asyncio.run()` 一个**新事件循环**，而 `db/session.py` 的 `AsyncEngine` 是
**模块级单例**、连接池里的 aiosqlite 连接绑定在**创建它的那��循环**上。
于是同一个进程里反复换循环，DB 操作被迫走「连接已绑到旧循环 → 重新建连」的慢路径。

**实测证据**（同一份探针脚本，只差一次 `dispose_engine()`）：

```
不 dispose:  STEP 1 t=0.782  STEP 2 t=1.992  STEP 3 t=3.571   ← 每步 ~1.2s
dispose 后:  STEP 1 t=0.034  STEP 2 t=0.082  STEP 3 t=0.127   ← 每步 ~0.04s
```

**差 30 倍。** 这不只是 3 条用例的问题——**它是全量 `pytest` 跑 1798s（30 分钟）的
主要成因之一**，而 `api/scripts/select_tests.py:5` 的注释仍写「全量 `pytest -q` 要 169 秒」
（**实际劣化 10.6 倍**）。这条同时解释了为什么 CI 需要 `9d9dd07` 那个
「事件循环守卫改用峰值+累计双维度，消除 CI 调度抖动误报」的补丁——**症状被压住了，根因还在**。

**建议的三步改法**（按性价比）：
1. **放宽那 3 条用例的超时**（0.1→2.0、1→5）——立刻变绿，5 分钟。
2. **在 `conftest.py` 里让每个用例开始前丢弃连接池**（`asyncio.run(dispose_engine())`），
   或把 `DbJobQueue` 的测试改用 **NullPool**——让 DB 操作回到 ~0.04s。
   ⚠️ 实测：给 conftest 加这个 autouse 夹具后，**上述 3 条用例仍红**——
   说明「每个用例前 dispose」这个位置**不对**（`_clean_rate_limit_counters` 夹具也在每个用例前
   `asyncio.run()`，两个夹具的 loop 顺序会互相抵消）。**正确做法是治本**：
   让 `AsyncEngine` 与事件循环绑定，或让测试全程共用一个 loop（`pytest-asyncio` 的 session 级 loop）。
3. **把 `select_tests.py` 的分级注释改成实测值**（169s → 1800s），否则它的
   `FULL_TRIGGER_FILES` 触发条件是在拿一个差 10 倍的基线做判断。

> **这一条与 P1-6②（pytest 并行化）强相关**：xdist 是 per-worker 进程，
> 每个 worker 有自己的 engine，**不会踩这个跨循环复用问题**——
> 所以并行化不但能提速，还会**绕开**这个 30 倍的慢路径。

**另外**：`test_config_guard` 要求 `.env.example` 列出每个配置键
（`tests/test_config_guard.py:239-250`），**加 `Settings` 字段必须同步改 `.env.example`**——
本计划若新增 `generation_poll_timeout_sec` 等配置项（P1-2），**必须一并改**，否则门禁立刻红。

---

### P1 — 健壮性与一致性（约 12h）

---

#### P1-1 · `GENERATION_PERSIST_TIMEOUT_SEC` 已加但**永远不生效**——`_persist` 落在轮询预算内

**位置**：`api/src/generation/runner.py:501`（`async with asyncio.timeout(budget)`）与 `:601`（`_persist` 调用）

> ⚠️ **这一条是「已修但没修对」的典型**。`2026-11-18` 的修复记录写着
> 「generation 落盘独立预算 GENERATION_PERSIST_TIMEOUT_SEC（否则无限重跳）」——
> 配置项（`config.py:398`，默认 300 秒）**确实加了**，`:705` 的内层 `asyncio.timeout` **也确实加了**，
> 但**加错了位置**，所以 300 秒这个数**一次都不会生效**。

AST 核验嵌套关系（`handle_poll` 函数体 480-680 行）：

```
timeout at 501 - 659 | contains line 601? True
```

即 `:501` 的 `async with asyncio.timeout(budget)` **覆盖到 `:659`**，
而 `_persist` 在 `:601` —— **在它内部**。

**实际预算**：`budget = (job.timeout_sec or 120.0) * 0.8`（`:488`），而轮询作业的
`job.timeout_sec` 硬编码 `60.0`（`:954`）→ **48 秒**。
内层的 300 秒被外层 48 秒完全压制，**形同虚设**。

**为什么这正是它要修的 bug**：`:690-697` 的注释自己写明了——
「几百 MB 的成片下载塞进去就把它耗光 —— 预算耗尽会把这一跳判成『没拿到结果』，
下一跳重新拉一遍，而它照样超时，于是**无限重跳**，产物永远进不了库。」
现在这个场景**依然成立**：48 秒打不下来的成片，永远进不了库。

**连带问题**：`:707` 的 `except Exception` **捕不到 `CancelledError`**
（Python 3.8+ 起 `CancelledError` 继承 `BaseException`，不是 `Exception`）。
所以「退回供应商直链」的兜底在取消语义下**也不执行**。

**实测复现**（复刻嵌套语义）：

```
外层 timeout(0.3s) 包住 内层 timeout(300s) 包住 sleep(10s)
结果: outer-timeout          ← 内层兜底分支从未执行
CancelledError 是 Exception 的子类吗? False
```

**改法**：
1. 把 `_persist` 移出 `asyncio.timeout(budget)` 块（放到 `:659` 之后、与 `_finish_success` 同层）。
   这样 300 秒预算才真正生效。
2. `:707` 改为 `except (Exception, asyncio.CancelledError)`，保证取消语义下兜底也执行。
3. `:488` 的 `budget` 语义随之调整——落盘不再从轮询预算里扣（见 P1-2）。

**验收**：新增集成用例「慢产物（耗时 > 48s）落盘成功」；
断言 `GENERATION_PERSIST_TIMEOUT_SEC` 改成 2 秒时落盘超时**且**走兜底（而不是外层先炸）。
**工作量**：2 小时（含集成测试）。

---

#### P1-2 · 轮询预算的 `* 0.8` 折扣无注释解释，`timeout_sec` 硬编码

**位置**：`api/src/generation/runner.py:488`、`:954`

`:954` 的 `timeout_sec=60.0` 硬编码在 `Job(...)` 构造里，`:488` 的 `budget = ... * 0.8` 也没有任何注释解释
「为什么是 80%」。同时 `settings.generation_poll_interval_sec`（`config.py:386`）
只用于 `interval`（下一次排程延迟），**不参与** `budget`——两个「时间」概念同名不同义，
读代码的人会以为 `poll_interval` 影响预算。

**改法**：`timeout_sec` 提到 `settings`（如 `generation_poll_timeout_sec`）；
`budget` 改成显式相加（poll 预算 + persist 预算）而不是 `* 0.8` 的隐式折扣，并写清折扣理由。
**注意**：`config.py:476-484` 的启动期守卫 `MAX_POLLS × INTERVAL ≥ TIMEOUT × 0.8` 要一并复核。

**工作量**：1 小时。

---

#### P1-3 · 内存队列与 db 队列的重试次数语义不一致，且内存驱动无死信

**位置**：`api/src/infra/queue/memory.py:87-92` vs `api/src/infra/queue/db.py:516-536`

```python
# memory.py:88 —— 先判 exhausted，再 +1
if not requeue or job.exhausted: return
job.with_attempt()

# db.py:517 —— 先 +1，再判 exhausted
job.with_attempt()
dead = not requeue or job.exhausted
```

**实测**（`max_attempts=3`）：内存驱动执行了 **4 次**，且失败后**彻底消失**——没有 dead 状态、
没有 `last_error`、没有日志。db 驱动则留一条 `status='dead'` 的行（`db/jobs.py:51`）。

**后果**：`QUEUE_DRIVER=memory` 是默认配置，dev/test 全跑内存驱动。
「跑 4 次就消失」与生产「跑 3 次留死信」的差异，意味着**在内存驱动下测过的重试逻辑在生产上表现不同**。

**改法**：内存驱动 `nack` 改成与 db 一致的顺序；耗尽时 `logger.error` + 保留一条可查记录（至少内存 list）。
**工作量**：1 小时。

---

#### P1-4 · `verify_blobs` 扫描的前缀与实际写入前缀不一致，完整性巡检恒为「健康」

**位置**：`api/src/infra/archive/integrity.py:53` vs `api/src/modules/library/service.py:98-99`

```python
# integrity.py:53
keys = sorted(storage.list_keys("blobs"))

# library/service.py:98-99 —— 真正写 blob 的地方
BLOB_PREFIX = "library/blobs"
PREVIEW_PREFIX = "library/previews"
```

**实测**：`verify_blobs` 扫归档存储的 `blobs/` 前缀，资产库的素材字节落在
**对象存储**的 `library/blobs/`。两者是不同驱动（`get_archive_storage()` vs `get_storage()`）——
这个巡检在生产上永远扫到 0 个对象，`IntegrityReport.total=0`、`healthy=True`。

**为什么是 P1 而不是 P0**：`app doctor --strict` 与 `app verify-blobs` 是运维信任的最后一道防线，
现在这道防线在**资产库这条线上是空转的**。

**改法**：`verify_blobs` 接受显式 `prefix` 参数，由调用方（`api/diagnostics.py:264`、`cli.py:607`）
传入实际前缀；或让 `verify_blobs` 复用 `modules/library/service.py` 的 key 构造（与 P0-3 合并做）。

**验收**：`app verify-blobs` 在有素材的库上 `total > 0`。
**工作量**：1 小时。

---

#### P1-5 · `Cache.incr` 的协议契约与 `MemoryCache` 实现不一致，导致单个脏键让整个限流作用域 fail-open

**位置**：`api/src/infra/cache/base.py:64-69`（协议）vs `api/src/infra/cache/memory.py:116-119`

`counter.py:13-18` 的设计意图写得很清楚：「读失败返回值是 0 而不是抛异常…只让这一个键上的历史用量归零，
**其余键照常计数**」「『少拦一次』和『全都不拦』是两种不同的失误，这里选前者」。

但 `MemoryCache.incr` 在值不是整数时**抛 ValueError**，`CacheCounter.incr`（`counter.py:36-37`）**原样透传**，
异常一路冒到 `ratelimit.py:275` 的 `except Exception` → **降级放行**。

**实测**（污染当前桶的键）：

```
hit -> degraded=True allowed=True used=0 limit=30 remaining=30
```

**一个脏键 = 该 scope 全部流量无限放行**，与设计意图完全相反。
`counter.py:44-47` 的 `read` 路径已经捕 `ValueError` 返回 0——**同一个类里两个方法对同一个错误条件处理不一致**。

**参考文档对应**：§4.3 的双闸门之所以有效，前提是**每个闸门只管自己那一件事**。
这里是「读」与「增」两件事被塞进同一个错误条件处理，边界糊了。

**改法**：`CacheCounter.incr` 捕 `ValueError` → 视作 0（与 `read` 对齐）；
并在 `base.py:64-69` 补上「键存在但值不可解析」的契约（建议：视为 0 并重置），两个驱动统一。
**验收**：污染一个键后，该 scope 其它键仍正常计数。
**工作量**：1 小时。

---

#### P1-6 · CI 无 job 超时，pytest 串行 30 分钟

**位置**：`D:/xx/AI/yufan_AI_studio/.github/workflows/ci.yml`（全文无 `timeout-minutes`）

**实测**：`var_pytest.log` 尾部 `1798.10s (0:29:58)`。而 `api/scripts/select_tests.py:5` 的注释
仍写「全量 `pytest -q` 要 169 秒」——**实际已劣化 10.6 倍**，选测脚本的分级逻辑仍基于旧假设。

> **这 10.6 倍里有一大块不是「测试变多了」，而是一个可修的慢路径**：
> P0-6 已实测出「每个用例跨事件循环复用同一个 AsyncEngine 连接池」会让 DB 操作慢 **30 倍**
> （0.04s → 1.2s/步）。**先修 P0-6 的第 2 步，这 30 分钟大概能掉一个量级**，
> 然后再上 xdist。

**改法**（按性价比排序）：
1. **加 `timeout-minutes`**（backend 45 / postgres-migrations 20 / frontend 20）—— 纯增益，5 分钟。
2. **加 `pytest -n auto`**（需 `pytest-xdist`）—— 通常压到 8-12 分钟。
   ⚠️ 中风险：xdist 改变执行顺序，需先本地跑一次对比结果数。
   **额外好处**：xdist 是 per-worker 进程，每个 worker 有自己的 engine，
   **天然绕开 P0-6 的跨循环慢路径**。
   `tests/conftest.py:16` 已用 `tempfile.mkdtemp` 给每个 session 建独立库，
   但 xdist 是 per-worker 的，需验证 worker 之间不共享同一个 `_TMP_DB`。
3. **加 `.github/dependabot.yml`**（npm / pip / github-actions 三条）—— 15 分钟。
4. **前端 CI 加「产物不含演示口令」检查**（`grep -r "admin@12345" dist/`）：
   mock 适配器是动态 import（`shared/api/http.ts:24-26` 特意为此优化），但**这只是设计意图，不是门禁**。
   一旦有人改成静态 import，演示口令就进生产 JS。10 分钟。

**验收**：CI 单次 PR 全绿时间 < 15 分钟。
**工作量**：2 小时（含验证）。

---

#### P1-7 · 日志无脱敏，`db_url` 直接进日志

**位置**：`api/src/core/logging.py`（全文无脱敏逻辑）、`api/src/db/session.py:79`

```python
logger.info("数据库引擎已创建: %s", settings.db_url.split("///")[0])
```

这一处**已经做了拆分**（`split("///")[0]` 去掉密码部分）——✅ 做对了。
但整个 `core/logging.py` **没有任何脱敏机制**：

- `core/logging.py` 只加 `RequestIdFilter` 与两个 Formatter，**日志内容原样输出**；
- 参考文档 §4.7 的「日志脱敏三件套」——URL query 剥离、握手头打码、payload 不落日志——**一条都没有**；
- 参考文档 §4.7 还强调「日志**永不含** key/token/header 值/query string/请求体」这条纪律。

**本仓库的泄露面**（实测 `grep` 结果）：

```
db/session.py:79                logger.info("数据库引擎已创建: %s", ...)
modules/export/service.py:112   logger.warning("导出产物丢失 id=%s key=%s", ...)
modules/library/downloads.py:338  logger.warning("打包时取不到对象 key=%s", key)
```

后三条打的是**对象存储 key**（不含凭证）——风险低。
真正的缺口是**没有统一的脱敏层**：将来任何一处打 URL（预签名地址、供应商回调地址）
就会带出 query string 里的签名。

**改法**：
1. 在 `core/logging.py` 加一个 `RedactingFilter`：正则剥离 URL query string、
   匹配 `apikey=` / `access_token=` / `token=` / `password=` / `secret=` 的值并替换为 `<redacted>`。
2. **不要**改 `DbUrlFilter` 之外的东西——把过滤做在 Formatter 里比做在每个调用点可靠。
3. 补一条用例：构造含 `?apikey=secret` 的日志消息，断言输出不含 `secret`。

**参考文档对应**：§4.7「日志脱敏三件套」★★★★★。

**验收**：日志中不出现任何预签名 URL 的 query、任何 token 值。
**工作量**：2 小时。

---

#### P1-8 · 后端无 lockfile，CI 不可复现

**位置**：`api/pyproject.toml:10-25`（依赖全为下限，只有 `fastapi` 有上限）

实测：`find api -maxdepth 2 -name "*.lock" -o -name "requirements*.txt" -o -name "uv.lock"` → **空**。

CI 里 `pip install -e ".[dev]"`（`ci.yml:57`）**每次都从 PyPI 解析最新版本** → CI 随时可能变红且无法二分。

**项目自己已经踩过**：`pyproject.toml:31-33` 的注释记录了真实事故——
「0.17.3 与 0.17.4 对『import 了不存在的模块』的 I001 判定不同，曾出现本地全绿、CI 红」，
并**只为 ruff 单独钉了 `==0.17.4`**。其余 12 个依赖（fastapi/uvicorn/pydantic-settings/sqlalchemy/pytest/mypy/alembic…）完全浮动。

**改法**：
- 用 `uv lock` 生成 `uv.lock`（或 `pip-compile` 生成 `requirements.lock`）。
- **建议只锁 dev 依赖**（pytest/ruff/mypy 等工具链），运行时依赖保持范围——
  运行时差异由真实部署环境另行管理。
- CI 改用 `uv sync --frozen` 或 `pip install -r requirements.lock`。

**验收**：连续 5 次 CI 的安装版本完全一致。
**工作量**：1.5 小时。

---

#### P1-9 · 类型契约不可回归：mypy 不 strict，且主动关掉了 `warn_unused_ignores`

**位置**：`api/pyproject.toml:71-75`

```toml
[tool.mypy]
python_version = "3.11"
plugins = ["pydantic.mypy"]
ignore_missing_imports = true
warn_unused_ignores = false     # ← 主动关掉
```

实测 `grep -c "strict\|disallow_untyped_defs\|check_untyped_defs\|warn_return_any\|disallow_any"` = **0**。
加上 CI 只查 `src`（`ci.yml:105` `mypy --platform win32 src`），**1400+ 行测试代码零类型检查**。

**参考文档 §4.8 的对应做法**（**这是本计划最该抄的一条工程基建**）：
用 `# error:` 注释标记「必须被类型检查器拒绝的行」，然后让 **mypy / pyright / pyrefly 三个检查器都跑**，
要求**报错的行集合完全一致**——把类型契约变成可回归的测试。

参考文档给出的动机正是本仓库的问题：
> 类型化 API 的正确性无法用运行时测试保证（错误的类型在运行时是合法的）。

**改法**（**分三步，不要一次全开**）：
1. `warn_unused_ignores = true` —— 打开后过期的 `# type: ignore` 会被发现，
   这是「类型信息腐坏」的早期信号。预期会红一批，逐个处理。
2. CI 增加 `mypy --platform win32 tests` —— 测试代码是契约的第一道防线
   （`test_openapi_contract.py` 用了大量 `type: ignore`），它的类型错误目前无人发现。
3. `check_untyped_defs = true` + `warn_return_any = true` —— 工作量最大，放最后。

**另**：CI 的 `--platform win32` 让类型检查结果与真实 Linux 部署环境不同。
建议加一条 `mypy --platform linux` 的**允许失败**检查，或把 Win32 依赖隔离到一个模块。
**这条是「已知取舍，加监控」而不是立即重构**（参考文档 §4.8 也提到类似的平台 hack）。

**工作量**：4 小时（三步合计）。

---

### P2 — 一致性、可维护性与文档（约 16h）

---

#### P2-1 · `S3Storage` 的两个 `presigned_*` 未包异常，签名失败会让整个历史列表 500

**位置**：`api/src/infra/storage/s3.py:195-217`

实测 AST 扫描：

```
put                     try/except=True
put_file                try/except=True
delete                  try/except=True
exists                  try/except=True
get                     try/except=True
presigned_upload_url    try/except=False   ←
presigned_download_url  try/except=False   ←
```

其余 5 个方法都把 `ClientError` / `BotoCoreError` 转成 `StorageError`。
而 `infra/artifacts.py:75` 的兜底**只捕 `StorageError`**：

```python
try:
    urls.append(await storage.presigned_download_url(value, expires_in=expires_in))
except StorageError as exc:
    logger.warning("产物地址签名失败，暂不给地址：%s（%s）", value, exc)
```

**后果**：S3 签名失败时 botocore 异常穿透 `artifact_urls` → **整个历史列表接口 500**，
而不是「这一张图没地址」。这与 `artifacts.py:57-58` 的设计意图（「一张图的地址算不出来，
不该让整个历史列表 500」）**直接冲突**。

**改法**：两个 `presigned_*` 各加 `try/except (ClientError, BotoCoreError) → raise StorageError(...)`。
**验收**：模拟签名失败时，历史列表仍返回 200，只有那一张图的地址为空串。
**工作量**：30 分钟。

---

#### P2-2 · `StorageError` / `CacheError` 无全局处理器（P0-1 的另一半，此处只补 CacheError 的判断）

见 P0-1。**补充判断**：`CacheError` **不应**改成 502——缓存故障的正确行为是**降级**而非报错，
建议保持现状并补一条注释说明「为什么缓存错误不进错误处理器」。

---

#### P2-3 · 归档层的同步 IO 在 async 上下文里直接执行

**位置**：`api/src/modules/task/pipeline.py:265`、`api/src/modules/library/ingest.py:331`

`ArchiveStorage` 协议（`infra/archive/base.py:95-185`）的**全部方法都是同步 `def`**。
AST 确认两个调用点都在 **async 函数**内：

```
modules/task/pipeline.py    persist_output (async=True) lines 245-270
modules/library/ingest.py   _materialize  (async=True) lines 273-363
```

S3 驱动下（`archive/s3.py:203`）是同步 boto3 调用——**网络 IO 直接卡住事件循环**。
更糟的是 `archive/base.py:213` 的 `time.sleep(0.05 * (attempt+1))` 重试退避——
在 SMB 共享盘上这正是最常触发的路径，一次抖动会在事件循环里卡住 150ms。

**同一个仓库里已有正确写法**：`modules/model_driver/libtv/executor.py:306` 用了
`await asyncio.to_thread(run_path, run_id)`——只是没普及。

**改法**：两处调用改成 `asyncio.to_thread(...)`；`base.py:213` 的 `time.sleep` 随之离开事件循环。
（或给 `ArchiveStorage` 协议补 async 变体——改动更大，先走 `to_thread`。）

**注意**：`to_thread` 不自动传播 contextvar，若该路径用到 `trace` 上下文需确认。
（实测 `read_pointer` / `materialize` 当前不用。）

**工作量**：1.5 小时。

---

#### P2-4 · `core/scheduler.py` 的周期任务串行执行，一个卡住的任务堵住全部

**位置**：`api/src/core/scheduler.py:233-264`

```python
for job in self.due(now):
    ...
    await asyncio.wait_for(job.handler(), timeout=timeout)
```

`asyncio.wait_for` 超时会 cancel 协程，但若 handler 内部是 `asyncio.to_thread(subprocess.run)`，
**线程不会被杀**——`wait_for` 会等它结束才真正超时返回。`scheduler.py:39-42` 的注释承认了这点
（「取消」不等于「停止」），但 `run_once` 仍串行等它。

**后果**：一个卡住的周期任务（`pg_dump`、SMB 上的大文件读）会堵住**同进程内所有周期任务**——
outbox 停止投递、GC 停止执行。而 `lease_ttl`（`scheduler.py:82-102`）精心设计的
「租约比上限长」余量，恰恰因为这里串行等待而失效。

**改法**：周期任务改并发执行（`asyncio.gather` + 各自独立 timeout），或给 `run_once` 一个本轮总时长上限。
**风险**：中——改并发会改变多个周期任务的相对时序，需确认无隐含顺序依赖
（`core.outbox` 与 `core.outbox.prune` 之间目前无依赖）。

**工作量**：3 小时。

---

#### P2-5 · `reload_settings()` 不失效 `parsed_overrides` 缓存

**位置**：`api/src/core/config.py:527-550` vs `api/src/core/ratelimit.py:149-151`

**实测复现**：

```
1st call   -> parsed_overrides() -> {}
env changed -> settings: {"task.create": 222}
parsed_overrides() -> {}          ← STALE
```

`reload_settings` 的 docstring 明确列出了它**不**重建的 10 个 `lru_cache` 单例，
但 `core.ratelimit.parsed_overrides` 不在清单里——它却是**从 settings 派生**的
（`ratelimit.py:152` 读 `get_settings().rate_limit_overrides`）。

**改法**：`reload_settings()` 里加 `parsed_overrides.cache_clear()`。
注意 `config.py:548-549` 是逐字段 `setattr` 原地刷新，缓存清理要在其后。
**工作量**：15 分钟。

---

#### P2-6 · 原子落盘缺少「0600 创建 → chmod」的权限竞态防护

**位置**：`api/src/infra/storage/local.py:32-38`、`:59-69`

本仓库的原子落盘**已经做对了核心部分**——临时文件 + `os.replace`，
且 `_temporary_name`（`:20-30`）的注释解释了「必须同目录」（Windows 上跨目录 `replace` 失败）
与「必须 uuid4」（同进程两个协程经 `to_thread` 是两条线程，pid 会撞）——这两点参考文档没有覆盖，
本仓库**比参考文档更细**。

**缺的**是参考文档 §4.6 的权限竞态防护：

```python
# 参考文档 storage/download.py::_open_new
descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    mode = stat.S_IMODE(destination.stat().st_mode) if destination.exists() else 0o666 & ~_umask()
    os.fchmod(descriptor, mode)
```

即：**先以 0600 创建**（避免竞态窗口内被他人以更大权限打开），**再 chmod 成目标权限**。
本仓库的 `temporary.write_bytes(data)` 用默认 umask 创建，窗口内权限偏宽。

**改法**：`local.py` 的两处临时文件创建改为 `os.open(..., 0o600)` + `fchmod`。
**工作量**：1 小时。

---

#### P2-7 · Markdown 里的 Python 代码块不参与类型检查

**位置**：`D:/xx/AI/yufan_AI_studio/docs/`（19 个文档）、`api/docs/`（多个）

参考文档 §4.8 的方案（**成本极低收益极高，五点设计值得整套抄**）：
1. `git ls-files` 精确取文件（排除 CHANGELOG 与 symlink）
2. 每个代码块包成 `async def _snippet()` 以支持 `await`
3. `PRELUDES` 声明共享名字，**类型以私有别名导入**（`_Prelude0`）——
   这样「用了类但没 import」的代码块仍会报错
4. ` ```python skip ` 逃生舱
5. 行号映射回 md + GitHub Actions 注解

**本仓库的适配点**：`api/docs/EXTENDING.md`（加模块/加供应商的标准做法）与
`api/docs/API.md`（接口清单）里有大量 Python 代码块，前端 `docs/FRONTEND_EXTENSION.md`
里有 TS 代码块（可另做一套）。

**改法**：新增 `api/scripts/check_doc_snippets.py`，把 `docs/**/*.md` 与 `api/docs/**/*.md` 的
Python 代码块喂给 mypy，出错时用 GitHub Actions 注解报出行号。

**工作量**：3 小时。

---

#### P2-8 · 文档里的基线数字与实际差 4 倍，`ruff check .` 与 CI 范围不一致

**位置**：`docs/CODE_STYLE.md:775-786`（基线快照）、`:739`/`:749`/`:766`（ruff/mypy 命令）、
`api/src/openapi_export.py:1`、`api/src/cli.py:227`、`api/README.md:353`（「三份」）

**实测**：`CODE_STYLE.md:786` 的基线快照写「`pytest -q` ✅ 346 passed」（2026-11-06），
实际是 **1409 passed**；「前端 Vitest ✅ 314 passed / 45 files」实际 95 files。
`CODE_STYLE.md:788` 说这张表的用途是「**评估这次改动有没有让仓库变差**的起点」——
**参照系本身失效了**。

**更实际的问题**：`CODE_STYLE.md` 写 `ruff check .`（含 migrations/、var/、docs/），
CI 实际跑 `ruff check src tests` + `scripts`——
**本地绿不等于 CI 绿**（或反之），这直接破坏 README.md:199-201 承诺的「自证清白」。
另外 `pyproject.toml:66-68` 为 `migrations/versions/*` 写的 per-file-ignores 是**死代码**——
CI 从不 lint 那个目录。

**改法**：
1. 基线快照改成实测值（跑一次 `pytest -q` / `npm run verify` 取真值）。
2. `ruff check .` → `ruff check src tests scripts`，与 CI 一致。
3. `mypy src` → `mypy --platform win32 src`（`CODE_STYLE.md:766` 与 README.md:203 都强调过这个 flag，
   文档内部自相矛盾）。
4. 「三份」→「四份」（3 处）。
5. `migrations/versions/*` 的 per-file-ignores：要么让 CI 真的检查它，要么删掉那条死规则并在注释里说明原因。

**验收**：按 `CODE_STYLE.md` 里写的命令跑一遍，CI 也绿。
**工作量**：1 小时。

---

#### P2-9 · 前端 entities 的 api/queries 分离未落地，shared 层直接调 http

**位置**：`studio/src/entities/{image,shot,storyboard}/api/*.api.ts`（**有 `.api.ts` 无 `.queries.ts`**）

实测确认这三个 entity 的 hook 写在 `.api.ts` 里，违反 `CODE_STYLE.md:428-430` 的约定。
另有 4 处越过 entity 层直接调 react-query：

```
features/template-specs/AssetPicker.tsx:120      useQuery
features/template-specs/SpecWorkspace.tsx:107     useMutation
features/task-preview/ImagePreviewModal.tsx:43    useMutation
shared/lib/remoteBlob.ts:33,47                   useQuery/useMutation + 直接 import http
```

**最严重的是 `shared/lib/remoteBlob.ts`**：它在 `shared/` 层，既直接
`import { api } from '@/shared/api/http'`（违反 `CODE_STYLE` 3.3），
又自己定义 query key（`mediaKeys`）与 staleTime。
eslint 的 `blockHttp` 规则**只对 features/widgets/pages/entities-ui 生效**
（`eslint.config.js:136-157`），**shared 层没有禁 http**——所以它合法但违背架构意图。

**连带**：query key 工厂 `createQueryKeys`（`shared/api/rest.ts:71`）只有 2 个实体在用，
image/shot/storyboard/session 各自手写裸数组字面量。

**另**：`eslint.config.js:21-22` 自己承认「只拦 `@/xxx` 别名路径，相对路径 `../../features/...` 仍可绕过」。
实测当前仓里 `from '../../`（非测试）= **0 处**，所以现状干净，但**规则形同虚设**。

**改法**：
1. 新建 3 个 `.queries.ts`，把 hook 从 `.api.ts` 挪出去。
2. 4 处越过 entity 的调用下沉到对应 entity。
3. eslint 补两条规则：① 相对路径模式也拦；② `shared/**` 禁直接调 http。
   ⚠️ 加规则会立刻红，先跑一遍拿违规清单再逐个修。
4. 裸 query key 全改用 `createQueryKeys`。

**工作量**：4 小时。

---

### 附：低优先级观察项（记录，不排期）

| # | 位置 | 观察 |
|---|---|---|
| L1 | `api/src/core/coordination.py:183` | 选主租约用 `time.time()`（墙钟），队列缓存 TTL 用 `time.monotonic()`。NTP 校时会让租约判断错乱。跨实例语义，**改造成本高**，建议加监控 |
| L2 | `api/src/infra/archive/s3.py:448-483` | `_evict()` 每次 materialize 未命中都全量 `stat` 扫描缓存目录（8GB / 800 文件 → 每次 800 次系统调用）。加最小间隔即可 |
| L3 | `api/src/infra/cache/memory.py:67-81` | `_evict()` 每次写入做 O(n) 全量扫描。实测新键 0.487ms vs 已有键 0.001ms |
| L4 | `api/src/db/outbox.py:189` | `prune_loop` 的 `every=3600.0` 写死，`.env` 里没有开关能改。装饰器参数在 import 期求值 |
| L5 | `api/src/infra/storage/s3.py:130-140` | `put_file` 把文件读两遍（先算摘要，再上传）。本地实现是单遍流式。300MB 成片多 300MB 磁盘 IO |
| L6 | `api/src/infra/storage/migration.py:44` | `_SKIP_DIRS = ("incoming",)`，但 `cli.py:375-376` 的文档说四个目录不搬（`runs/`、`inbox/`、`incoming/`、`trash/`）。`runs/` 可能几十 GB |
| L7 | `api/src/core/metrics.py:105` | `_Metric._samples` 声明为 `dict[tuple[str, ...], Any]`，丢失 Counter/Gauge/Histogram 的值类型。`Histogram` 的 `data[-2]`/`data[-1]` 下标约定**无注释无断言保护** |
| L8 | `api/src/infra/queue/base.py:126-151` | `JobQueue` 协议缺 `stats()`，两个消费方（`api/instrumentation.py:141`、`modules/health/checks.py:117`）各自用 `getattr` 探测——与 `infra/ai/base.py:155-167` 明写的纪律「不要用 getattr 探测」矛盾。同一指标名在不同部署下语义不同 |

---

## 5. 实施顺序建议

### 第一波（半天，零风险）— 拿到最大收益

| 条目 | 工作量 | 收益 |
|---|---|---|
| P0-5 `recovery` 检查返回值 | 10 min | 消除矛盾通知 |
| P0-6① vendor 重新 checkout + 放宽 3 条用例超时 | 15 min | 门禁变绿 |
| P2-5 `parsed_overrides` 缓存 | 15 min | 配置改了不起作用 |
| P2-1 `presigned_*` 包异常 | 30 min | 消除整页 500 |
| P0-1 `StorageError` → 502 | 15 min | 告警分流正确 |
| P1-6① CI 加 `timeout-minutes` | 5 min | 防挂死 |
| P1-6③ dependabot | 15 min | 漏洞可见 |
| P1-6④ 产物无演示口令 | 10 min | 安全门禁 |
| P2-8 文档基线与命令对齐 | 1 h | 参照系恢复有效 |

**合计约 2.5 小时**，覆盖 8 条、零行为变更风险。

### 第二波（1-2 天）— 数据正确性

P0-3 + P1-4（blob_key 合并 + 巡检前缀）→ P0-4 + P1-3（队列语义对齐）→ P1-5（限流 fail-open）→ P0-2（重试双闸门 + jitter）

> **P0-6 的第 2 步（治本跨循环慢路径）也归这一波**——它值 30 倍的测试提速，
> 且**必须在 P1-6②（xdist）之前做**，否则 xdist 只是掩盖了慢路径而不是修好它。

### 第三波（3-5 天）— 可靠性与可复现

P1-1（persist 预算）→ P1-2（预算语义）→ P1-6②（pytest 并行）→ P1-7（日志脱敏）→ P1-8（lockfile）→ P2-3 / P2-6（IO 与权限）→ P2-4（调度并发）

### 第四波（4-6 天）— 类型与文档基建

P1-9（mypy 三步）→ P2-7（文档代码块类型检查）→ P2-9（前端分层）

---

## 6. 复核后确认「已经做对了」的部分（不必改）

对照参考文档逐条扫过后，有几处**本仓库已经比参考文档做得更好**，明确记下来避免重复劳动：

| 项 | 位置 | 说明 |
|---|---|---|
| 原子落盘的临时文件命名 | `infra/storage/local.py:20-30` | 注释解释了「必须同目录」（Windows 跨目录 `replace` 失败）与「必须 uuid4」（同进程两个协程经 `to_thread` 是两条线程，pid 会撞）。**参考文档没有这一层** |
| 队列 fencing token | `db/jobs.py` / `infra/queue/db.py:462-473` | 凭据不匹配就写不进，ack/nack 都带 `where(*self._fence(job))`。**这是正确的防重入设计**，参考文档的对应实现更弱 |
| `_claim` 的 CAS 语义 | `infra/queue/db.py:394-417` | 条件更新 + `rowcount` 校验，抢输的 worker 回退重来。注释（`:399-403`）明确说明「ack 可能在读与写之间提升过 payload —— 要重试而不是返回过期的」，**语义正确**（P0-6 的失败是测试超时太紧，不是代码错） |
| JWT 用途隔离 | `core/security.py:284-336` | `typ` 显式校验（access/refresh 互不接受）、`exp`/`iat` **必须存在**才校验、`iat_ms` 毫秒精度解决「改密同秒旧票仍可用」。**与参考文档 §4.4「算法来自 key 而非 header」同一精神**，且更严 |
| 续接（continuation）代替新建行 | `infra/queue/db.py:214-253` | 下一跳写进父任务的 `next_job`，由 ack 原地转 pending——既不新建行也不占幂等键。`fc522fd` 还修了「续接落空时不再假装已经排上了」 |
| 密码哈希的闸门前置 | `core/security.py:133-157` | `password_slot()` 把「先排队、再拿连接」写进代码；`_GATE_DEPTH` 处理 Semaphore 不可重入。**比参考文档更细**——它防的是「占满连接池」这一类具体攻击 |
| `db/session.py` 的池参数 | `db/session.py:37-61` | 注释写明「默认 `pool_size=5` 跟线上完全不是一回事」，测试与线上的池行为显式对齐 |
| 限流 fail-open 的方向选择 | `infra/cache/counter.py:13-18` | 「读失败返回值是 0 而不是抛异常…『少拦一次』和『全都不拦』是两种不同的失误，这里选前者」——**设计意图正确**，P1-5 是实现没兑现这个意图，不是意图错了 |
| 契约四份 + 字节比对 | `tests/test_openapi_contract.py` | 比参考文档的 `sdk-compliance.yaml` 更强：不仅比对生成物，还校验「生成物引用的类型必须在 `components.schemas` 里存在」「服务实际吐的 spec == 落盘快照」 |

---

## 7. 与参考文档的对应关系速查

| 参考文档章节 | 本计划用到的部分 | 对应条目 |
|---|---|---|
| §4.3 重试与退避 | 双闸门、full jitter、`Retry-After`、幂等键生命周期 | P0-2 · P0-4 · P1-5 |
| §4.6 存储 | 路径穿越防护、原子落盘 + 权限竞态防护、签名 URL 双写 | P0-3 · P2-6 |
| §4.7 安全边界 | 日志脱敏三件套、loopback 绕过防护 | P1-7 |
| §4.8 工程基建 | 类型契约回归测试、Markdown 代码块检查、CI 矩阵裁剪 | P1-9 · P2-7 · P1-6 |
| §7 不要抄的部分 | 明确排除 `unasync` / 全量合规矩阵 / 强制 sync-async 统一 | §3 |
| §2.1 索引 A | 「协议是否兑现承诺」作为审计尺子 | P0-1 · P1-3 |
