# 对 `IMPROVEMENT_PLAN_2026-11-18.md` 的逐条核实

> **核实对象**：`docs/IMPROVEMENT_PLAN_2026-11-18.md`（写于 `main @ 9d9dd07`）
> **核实基准**：`main @ 7818b41`（比计划晚 14 笔前端改动，**均未改动后端**）
> **方法**：24 条逐条回到代码指到行；能实测的一律实测（临时脚本，不入仓）；关键因果用探针复现
> **结论**：**16 条完全成立 · 8 条部分成立**（其中 1 条改法不可行、1 条含明确事实错误）

---

## 0. 一眼结论

| 严重度 | 条数 | 完全成立 | 部分成立 | 需要重新设计 |
|---|---|---|---|---|
| P0 | 6 | 3（P0-1/4/5） | 3（P0-2/3/6） | — |
| P1 | 9 | 7 | 2（P1-5/7） | P1-4（改法不可行） |
| P2 | 9 | 6 | 3（P2-4/8/9） | — |
| 观察项 | 8 | 5 | 3（L1/6/7） | — |

**最该先做的四条**（事实清楚、改法小、后果实）：

1. **P0-4** 内存队列在 dequeue 就释放幂等键 —— 而 `QUEUE_DRIVER=memory` 是**默认配置**，这条保护在默认配置下失效。实测同键可重入，堆里出现两条。
2. **P1-7 的一半**（计划自己判错了这一半）：`db/session.py:79` 的 `split("///")[0]` **对 PG URL 无效**，启动日志会打印**含密码的完整 DSN**。计划把它标成「✅ 做对了」。
3. **P1-1**（计划最有价值的一条）：我上一轮加的 `GENERATION_PERSIST_TIMEOUT_SEC` **一次都不会生效** —— 它嵌在 48 秒的轮询预算里。
4. **P0-1 / P2-1**：`StorageError` 与 `presigned_*` 都会穿透到 500，把「上游故障」报成「代码 bug」。

**最需要重新设计的三条**：`P1-4`（改法不可行）、`P0-6` 第 2 步（幅度未复现）、`P2-4`（两条因果错）。

---

## 1. 计划本身需要修正的六处

### ❌ 1.1 P1-7 把 `db/session.py:79` 标成「已经做对了」

计划原文：「这一处**已经做了拆分**（`split("///")[0]` 去掉密码部分）——✅ 做对了。」

**实测**（本项目 `DB_URL` 是 PG）：

```
dialect = postgresql+asyncpg
包含 "///" ? False           ← PG 的 URL 只有 "://"，没有 "///"
split('///')[0] 结果 = 'postgresql+asyncpg://yufan:***@127.0.0.1:5433/yufan_studio'
```

`split` 找不到分隔符就返回原串 —— **密码原样进了启动日志**。只有 sqlite 的 `sqlite+aiosqlite:///path` 才带三个斜杠。

这一条的严重度**比计划说的更高**（不是「将来可能打 URL」，而是「现在就在打密码」），但修法不同：加 `RedactingFilter` 兜不住 `user:pass@host` 这种形态，要改成 `make_url(...).render_as_string(hide_password=True)`。

### ❌ 1.2 P2-4 的两条因果都不成立

计划说「`wait_for` 超时会 cancel 协程，但若 handler 内部是 `to_thread(subprocess.run)`，**线程不会被杀**——`wait_for` 会等它结束才真正超时返回」。

**实测**：handler = `await asyncio.to_thread(time.sleep, 3)`，`wait_for(timeout=0.2)` 在 **0.21 秒**返回，线程仍在跑。`wait_for` 只取消 awaitable，不会等线程。

计划又说「`lease_ttl` 精心设计的『租约比上限长』余量，恰恰因为这里串行等待而失效」。

**实测**：串行只**推迟开始**，不影响租约。真正发生的是 —— 超时后 `finally: await self._release(job.name)` **主动释放**了租约，所以 TTL 余量根本用不上；日志还写「租约已到期，别的实例可能已接管」（`scheduler.py:252-255`），与事实不符（是我们主动放的）。

真正会堵死同进程全部周期任务的，是 handler **直接在事件循环里做同步 IO / CPU**（此时 `wait_for` 连触发都做不到），不是 `to_thread` handler。

### ❌ 1.3 P1-4 的改法不可行

计划：「`verify_blobs` 接受显式 `prefix` 参数…或复用 `modules/library/service.py` 的 key 构造。」

**实测**：`verify_blobs(storage: ArchiveStorage)` —— 形参是**归档存储**。而：

- `ObjectStorage` 协议（`infra/storage/base.py:20-107`）**根本没有 `list_keys`**（只有 `put/get/delete/exists/url_for/presigned_*/put_file/local_path/iter_chunks`）；
- 默认 `ARCHIVE_DRIVER=local` / `STORAGE_DRIVER=local` 是**两个不同根**；本机 `.env` 两者都是 s3 但**桶不同**（`yufan-dev` vs `yufan-archive`）。

所以只把 `"blobs"` 改成 `"library/blobs"` **仍然扫错桶**，结果还是 0 个对象。真正要做的是新增一条走 `get_storage()` 的校验（按 `LibraryAsset.sha256` 逐条 `exists`/`get` 比对），或给 `ObjectStorage` 加枚举能力 —— 不是换一个字符串。

### ⚠️ 1.4 P0-6 的「30 倍」我没复现出来

计划用探针给出「不 dispose: 每步 ~1.2s / dispose 后: 每步 ~0.04s，差 30 倍」，并推断「这是全量 30 分钟的主要成因之一」。

我用同一套机制（`sqlite+aiosqlite` 临时库、反复 `asyncio.run()`、`dispose_engine()` 对照）测了三档负载：

| 每步负载 | 不 dispose | dispose 后 | 倍数 |
|---|---|---|---|
| 1 次 `select 1` | 32.8 / 3.4 / 1.8 ms | 4.5 / 3.2 / 4.9 ms | **3.0×**（首步 7×） |
| 5 轮 insert+count | 653 / 216 / 191 ms | 187 / 319 / 160 ms | **1.6×** |
| 60 轮 insert+count | 551 / 271 / 188 ms | 200 / 187 / 168 ms | **1.8×** |

**慢路径的方向确实存在**（首步从 4.5ms 涨到 32.8ms），但**幅度随负载摊薄**，我没测到 30 倍，也没测到计划里那种「逐步递增到 1.6 秒」的形状。计划自己的探针代码没给出，无法复现它的数值。

**结论不变的那部分**：那 3 条用例确实是「超时给太紧」而不是逻辑错 —— 我把 `timeout=0.1→2.0`、`1→5` 改完，**两条 db_queue 立刻变绿**（实测 2 passed），改回后重新变红。所以 P0-6 的第 1 步建议（放宽超时）**成立且立即可做**；第 2 步（治本跨循环）收益存疑，建议先做第 1 步和第 3 步。

### ⚠️ 1.5 P0-3 的「两个都从 `__init__` 导出」是误读

计划：「两个函数**同名、都从 `infra/archive/__init__.py` 导出**（`:44`、`:46`），import 时极易拿错。」

实际：`__init__.py` 的 `__all__` 里 `:44` 是 `"blob_key"`、`:46` 是 **`"clip_key"`** —— 计划把 `clip_key` 当成了第二个 `blob_key`。全仓没有任何模块同时导入两者，**不存在同名冲突**。（`normalize_suffix` 也**没有**在 `__all__` 里，复用要直接 `from infra.archive.blobs import ...`。）

另外一处要收窄：`LocalStorage._resolve`（`storage/local.py:93-102`）对含 `..` 的 key 直接抛 `StorageError` —— 所以今天那个未消毒的 key **连写盘都过不去**，不会静默穿越，只会变成 500。计划说「尚不可利用」是对的，但「只会在 `put()` 处变成 500」这层它没提到。

### ⚠️ 1.6 P2-8 的行号、处数、"4 倍"都要订正

| 计划写的 | 实际 |
|---|---|
| `CODE_STYLE.md:775-786` | **`:868-878`**（`bc6b3dd` 插入了 93 行，行号全漂） |
| 命令在 `:739`/`:749`/`:766` | **`:832`/`:842`/`:859`** |
| 「三份」3 处 | **4 处**（`openapi_export.py:1`、`:14`、`cli.py:227`、`README.md:353`） |
| 「差 4 倍」 | 只对 pytest 成立（346→1409，**4.07×**）；前端 314/45 → 796/101 约 **2.1×** |
| 「`migrations/versions/*` 的 per-file-ignores 是死代码」 | **今天不是**：文档写的 `ruff check .` 会 lint 它；只有把它改成 `check src tests scripts` 之后才变死代码 |

其余成立：基线快照 346/314/45 确实在文档里；`ruff check .` 与 CI 的 `ruff check src tests` 确实不一致。

---

## 2. P0 逐条

| # | 结论 | 关键证据 |
|---|---|---|
| **P0-1** `StorageError` 承诺 502 实际 500 | ✅ **成立** | `base.py:16` `class StorageError(Exception)`；`errors.py` 8 个处理器无它；实测 `StorageError → 500 {"code":50000}`、`UpstreamError → 502 {"code":50200}`。`core/exceptions.py:228` 的 `UpstreamError` 确实 `http_status=502`，归档层已在用它，只有 `infra/storage/*` 还在裸 `Exception` |
| **P0-2** 重试缺「幂等 × 可重放」双闸门 | ⚠️ **部分成立** | 退避无抖动 ✓（`min(8.0, 0.5 * 2**attempt)`，两处）；无 `Retry-After` 处理 ✓（全文件无此字符串）；**但「去掉 500」这条不该照抄** —— 本仓库注释写明 500 代表「网关抖动、上游重启」，而 PostgREST 的 500 是「查询失败」，语义不同。照抄会让真正的网关 500 不再重试 |
| **P0-3** 资产库 `blob_key` 不消毒 | ⚠️ **部分成立** | 实测 `library.blob_key(d, "../../etc/passwd")` → `library/blobs/2d/<sha>../../etc/passwd`；归档版 → 后缀被消成空 ✓。上游确实挡着（`service.py:677` 白名单、`text_assets.py:143` pattern）。但见 §1.5：无同名冲突、且 `_resolve` 已挡 `..` |
| **P0-4** 内存队列 dequeue 释放幂等键 | ✅ **成立** | `memory.py:59-62` `self._by_key.pop(...)`；`db/jobs.py:61` 索引 `WHERE status IN ('pending','running')`；`config.py:216` 默认 `memory`。实测：job1 在途时同键重入队成功（`dup? False`），堆里两条 |
| **P0-5** `_fail_unrecoverable` 忽略返回值 | ✅ **成立** | `recovery.py:113` 丢弃返回值后无条件 `commit` + `publish`；`store.py:22-27` 协议原文要求「终态保护只有在调用方看返回值时才真正成立」；`runner.py:1004` 是正确写法。**后果要收窄**：`:111` 的 `current.terminal` 已挡掉大多数，矛盾通知只在「load 与 mark_failed 之间」的窗口或双实例同时恢复时发生 |
| **P0-6** 3 组测试红 + 跨循环慢路径 | ⚠️ **部分成立** | 3 条红 ✓（我实测两条 db_queue 红；manifest 是 CRLF）；放宽超时后**立刻变绿** ✓（2 passed，改回又红）。**但「30 倍」未复现**（见 §1.4），`select_tests.py:5` 的「169 秒」确实过期（实际 1798s）✓ |

---

## 3. P1 逐条

| # | 结论 | 关键证据 |
|---|---|---|
| **P1-1** `_persist` 落在轮询预算内，300 秒预算永不生效 | ✅ **成立（这是计划最有价值的一条）** | AST：`timeout` 块行 **501-659**，`_persist` 调用在 **601** → 在内部。`budget = (job.timeout_sec or 120) * 0.8`，而 `timeout_sec=60.0` → **48 秒**压制 300 秒。连带：`except Exception` 捕不到 `CancelledError`（实测 `issubclass(asyncio.CancelledError, Exception) == False`），所以「退回供应商直链」的兜底在取消语义下**也不执行** |
| **P1-2** `* 0.8` 无注释、`timeout_sec` 硬编码 | ✅ **成立** | `runner.py:488` `budget = (job.timeout_sec or 120.0) * 0.8`，紧邻注释讲的是「session 开在超时块外面」，与 0.8 无关；`:954` `timeout_sec=60.0` 字面量 |
| **P1-3** 内存/db 队列重试语义不一致 | ✅ **成立** | `memory.py:87-92` 先判 `exhausted` 再 `with_attempt()`；`db.py:517-521` 反之。实测 `max_attempts=3`：memory **执行 4 次**且后彻底消失（`size=0, snapshot=[]`），db **3 次**并留 `stats={'dead':1}`。补充：memory 的 `nack(requeue=False)` 连失败次数都不计 |
| **P1-4** `verify_blobs` 前缀不一致 | ✅ 事实成立 · **改法不可行** | `integrity.py:52-53` `storage.list_keys("blobs")` + `BlobStore(storage)`；实测把两个根设成同一目录后，`archive.list_keys('blobs')=[]`、`healthy=True, total=0`，而盘上有 `library/blobs/**`。改法问题见 §1.3 |
| **P1-5** `Cache.incr` 导致 fail-open | ⚠️ **部分成立** | 后果成立：`MemoryCache.incr` 抛 ValueError → `CacheCounter` 透传 → `ratelimit.py:274` `except Exception` → `degraded=True, allowed=True`（实测复现）。**两处过头**：①「该 scope 全部流量无限放行」不成立 —— key 含 subject，实测另一个 subject `degraded=False`，影响面是「单个 subject × scope × 窗口桶」；②「协议契约不一致」证据不足 —— `base.py:64-70` 只约定「键不存在从 0 起算」，从未承诺非整数不抛（Redis `INCR` 同样报错）。真问题是 `read` 与 `incr` 对同一错误条件策略不同 |
| **P1-6** CI 无 job 超时 | ✅ **成立** | `ci.yml` 全文无 `timeout-minutes`；三个 job（backend / postgres-migrations / frontend）；无 `-n auto`；`.github/dependabot.yml` 不存在 |
| **P1-7** 日志无脱敏 | ⚠️ **部分成立 + 一处明确错误** | `core/logging.py` 只有 `RequestIdFilter` + 两个 Formatter，**无任何 redact** ✓。但见 §1.1：`session.py:79` 对 PG **会打印含密码的 DSN**，计划判成「做对了」。泄露面比计划描述窄（预签名只在 `diagnostics.py:230` 打状态码，访问日志只打 path 不含 query）但**更实在** |
| **P1-8** 无 lockfile | ✅ **成立** | `git ls-files api` 只有 `pyproject.toml`（无 uv.lock/requirements*.txt/poetry.lock）；`ci.yml:57` `pip install -e ".[dev]"` 每次从 PyPI 解析。细节更正：`pydantic>=2.9,<3.0` **也有上限**，`certifi` **无任何约束** |
| **P1-9** mypy 不 strict | ✅ **成立** | `pyproject.toml:71-75` `warn_unused_ignores = false`；`grep -c "strict\|disallow_untyped_defs\|check_untyped_defs\|warn_return_any\|disallow_any"` = **0**；`ci.yml:105` `mypy --platform win32 src`。更正：「1400+ 行测试代码」实际 **3.5 万行**，所以 4h 估算偏低 |

---

## 4. P2 逐条

| # | 结论 | 关键证据 |
|---|---|---|
| **P2-1** `presigned_*` 未包异常 | ✅ **成立** | `s3.py:199-208` 直接 `to_thread(generate_presigned_url)` 无 try；前 5 个方法都有 `except (ClientError, BotoCoreError) → StorageError`。实测无凭证时抛 `NoCredentialsError`，`isinstance(e, BotoCoreError) == True`（会被现有写法捕到）。`artifacts.py:75` 只捕 `StorageError` → 穿透 500 |
| **P2-2** `CacheError` 不该改 502 | ✅ 判断正确 | 属 P0-1 的附注，方向对：缓存故障应降级 |
| **P2-3** 归档层同步 IO 在 async 里 | ✅ **成立** | `ArchiveStorage` 13 个方法全同步 `def`；`task/pipeline.py:265`（`async persist_output` 内）、`library/ingest.py:331`（`async _materialize` 内）直接调；`base.py:213` `time.sleep(0.05 * (attempt+1))`。**改法注意**：`ingest.py:331` 的 `materialize` 是 `@contextmanager` 生成器，不能直接 `to_thread(materialize)` |
| **P2-4** 周期任务串行执行 | ⚠️ **部分成立（两条因果错）** | 串行属实；但见 §1.2：`wait_for` 不等线程、`lease_ttl` 不是「因串行而失效」而是「超时后主动释放」 |
| **P2-5** `reload_settings` 不失效 `parsed_overrides` | ✅ **成立** | `ratelimit.py:149-151` `@lru_cache` + 读 `get_settings().rate_limit_overrides`；`reload_settings` docstring 列 10 个单例，漏了它。实测：改 env 后 `parsed_overrides()` 仍返回旧值 |
| **P2-6** 缺 0600 → chmod | ✅ **成立** | `local.py:35` `temporary.write_bytes(data)`、`:62` `temporary.open("wb")`；全仓 `grep "0o600"` = **0**、`grep "chmod\|os.open"` = **0**。**改法注意**：CI 是 win32，`os.open` 的 mode 在 Windows 基本无效、`chmod` 只有只读位 → 必须按 `sys.platform` 分支 |
| **P2-7** Markdown 代码块类型检查 | — | 是新增脚本，无现成事实可核；思路与参考文档一致，可做 |
| **P2-8** 文档基线与命令不一致 | ⚠️ **部分成立** | 见 §1.6 |
| **P2-9** 前端 api/queries 未分离 | ⚠️ **部分成立** | 三个 entity（image/shot/storyboard）确实只有 `.api.ts`、hook 在其内 ✓；4 处直接 `useQuery/useMutation` ✓（`AssetPicker.tsx:121`、`SpecWorkspace.tsx:117`、`ImagePreviewModal.tsx:43`、`remoteBlob.ts:33/47`）。**但「越过 entity 直接调 http」只对 `remoteBlob.ts` 成立** —— 其余三处的 `queryFn` 都走 entity api（`imageApi.retry` 等），属「hook 位置违规」不是「数据层越界」。eslint `blockHttp` 范围、`createQueryKeys` 只 2 处在用、`from '../../'` 0 处，均准确 |

---

## 5. 观察项 L1-L8

| # | 结论 | 说明 |
|---|---|---|
| **L1** 选主用墙钟 | ⚠️ | `coordination.py:183` `time.time()` ✓，但**不构成时钟混用 bug** —— 写入端（`_claim`）与比较端（`acquire`）都用 `time.time()`，自身一致。对照位置也错：`infra/queue/*` **一处 `monotonic` 都没有**，真正的对照是 `infra/cache/memory.py:53-54` |
| **L2** `_evict()` 全量 stat | ✅ | `archive/s3.py:448-478`，调用点在 `:360` 的未命中分支，无节流/无时间戳判据 |
| **L3** `MemoryCache._evict` O(n) | ✅ | 但要补前提：**条目达到 `max_entries` 后**才每次写入全扫；未达上限时 `:69` 提前返回 |
| **L4** outbox prune 间隔写死 | ✅ | `db/outbox.py:189` `every=3600.0` 字面量；`config.py` 的 outbox 一组里**没有**该开关（对照 `library_trash_gc_interval_sec` 可见是缺项） |
| **L5** S3 `put_file` 读两遍 | ✅ | `s3.py:130` 先 `_digest(path)` 全文件，`:139` 再 `upload_file`；本地版 `local.py:63-65` 边写边算 |
| **L6** `_SKIP_DIRS` 与文档不符 | ⚠️ | 文本矛盾 ✓（1 个 vs 4 个）。**但 `runs/` 不会被搬走** —— `migrate-archive` 是**按前缀枚举**（`catalog/blobs/previews/library`），不走 `_scan`，`runs/` 天然不在其中。另：`inbox/`、`trash/` 在 `src` 里**没有任何写入点**，是文档提到、代码里不存在的目录 |
| **L7** metrics `Any` 与下标约定 | ⚠️ | 类型退化成 `Any` ✓；**但「无注释」不成立** —— `metrics.py:258` 有 `# 末尾两位：sum、count`；「无断言」成立（4 处 `data[-2]/data[-1]` 都没有长度/类型检查） |
| **L8** `JobQueue` 缺 `stats()` | ✅ | 协议 `base.py:126-151` 无 `stats()`；`instrumentation.py:142`、`health/checks.py:119` 用 `getattr` 探测；纪律出处 `infra/ai/base.py:156`。行号偏移 1-2 行 |

---

## 6. 按核实结果调整后的建议顺序

计划原顺序大体可用，但三处要动：

### 现在就能做（事实清楚、改法小、无争议）

| 条目 | 改动 | 为什么先做 |
|---|---|---|
| P0-4 + P1-3 | 内存队列幂等键改到 ack/nack 释放；`nack` 顺序对齐 db | **默认配置**下保护失效，且改动局部 |
| P1-7 的会话那半 | `session.py:79` 改 `render_as_string(hide_password=True)` | 计划判错了，但问题真实 —— 现在就在打密码 |
| P1-1 | `_persist` 移出 `asyncio.timeout(budget)`；`except` 加 `CancelledError` | 我上轮的修复无效，且改法明确 |
| P0-1 + P2-1 | `StorageError` 继承 `UpstreamError`；两个 `presigned_*` 包异常 | 15-30 分钟，消除「上游故障报成 500」 |
| P0-5 | `if not await store.mark_failed(...): return` | 10 分钟 |
| P2-5 | `reload_settings()` 里函数级 import 后 `parsed_overrides.cache_clear()` | 15 分钟，注意别造成循环 import |
| P0-6 第 1 步 | 放宽那两条用例超时（0.1→2.0、1→5） | 实测立刻变绿；第 2 步再说 |
| P1-6 第 1、3、4 步 | CI `timeout-minutes` / dependabot / 产物扫演示口令 | 纯增益 |

### 要先改设计再做

| 条目 | 为什么 |
|---|---|
| **P1-4** | 只改前缀仍扫错桶；需新增走 `get_storage()` 的校验，或给 `ObjectStorage` 加枚举 |
| **P0-6 第 2 步** | 「30 倍」未复现；建议先做第 1 步止血，再单独测「跨循环慢路径」到底值多少，别按 30 倍的收益立预期 |
| **P2-4** | 真问题是「超时后主动释放租约还打日志说已到期」和「handler 里的同步阻塞」；改并发解决不了这两个 |
| **P0-3** | 「合并两套 `blob_key`」的前提（`BlobStore.ensure/adopt` 是死代码）要单独核；前缀语义不同，只能合并「消毒」不能合并函数 |
| **P1-9** | 测试代码 3.5 万行，4h 估算不现实，建议只做 `warn_unused_ignores = true` 这一步 |

### 建议暂缓

- **P1-8（lockfile）**：需要改 CI 安装方式与缓存路径，影响所有 PR；建议单独一个 PR、先只锁 dev 依赖。
- **P2-7（文档代码块类型检查）**：新增脚本 + CI 接入，收益在长期，可以等前面稳定。
- **P2-9（前端分层）**：改动面 80+ 文件，且与本轮 14 笔前端改动（`Notice` 统一、`Segmented` 等）有重叠，建议等前端那波稳定后单独做。

---

## 8. 已实施（2026-11-18 晚）

按 §6 的「现在就能做」清单逐条落地。**24 条里做了 15 条，4 条部分做，5 条按 §6 的理由暂缓。**

### 8.1 完整做了

| 条目 | 落点 | 关键点 |
|---|---|---|
| **P0-1** | `infra/storage/base.py` | `StorageError` 继承 `UpstreamError` → 502。实测响应码从 500 变 502 |
| **P0-4 + P1-3** | `infra/queue/memory.py` | 幂等键占到 ack/nack；`nack` 先 `with_attempt()` 再判耗尽；耗尽留死信 + 释放键；`close()` 不再清空；`size()` 含在途；补 `stats()`。**连带补了「同键续接」**：db 驱动靠 `next_job` 支持 handler 排下一跳，内存驱动原来会把下一跳吞掉（`enqueue` 命中自己的键就返回旧 id）—— 那会让生成轮询在默认配置下断链 |
| **P0-5** | `generation/recovery.py` | `_fail_unrecoverable` 接 `mark_failed` 返回值，落败就跳过（不再补发矛盾事件）；`sync_orphan` 计数跟着真实写入 |
| **P0-6①** | `tests/test_db_queue.py` | 两处超时放宽（0.1→2.0、1→5）并写下理由。实测两条从红转绿 |
| **P1-1** | `generation/runner.py` | `_persist` 移出 `asyncio.timeout(budget)`（预算块 509-655，落盘在 665）；`_persist` 的 `except` 单独处理 `CancelledError`（留痕后**重新抛出**，不吞取消） |
| **P1-5** | `infra/cache/counter.py` | `incr` 遇脏值删键重来（与 `read` 对齐），不再把 `ValueError` 抛给限流器 |
| **P1-7** | `core/logging.py`、`db/session.py` | 新增 `redact()` + `RedactingFilter`（URL query / DSN / Bearer 三条规则，幂等）；DSN 改用 `make_url(...).render_as_string(hide_password=True)` |
| **P2-1** | `infra/storage/s3.py` | 两个 `presigned_*` 包 `ClientError/BotoCoreError` → `StorageError` |
| **P2-5** | `core/config.py` | `reload_settings()` 清 `parsed_overrides` 缓存；docstring 补「纯函数例外」的判据 |
| **P2-6** | `infra/storage/local.py` | 临时文件以 0600 创建，写完 `chmod` 到 `0o666 & ~umask`；Windows 走平台分支并在注释里说明 |
| **P2-8** | `docs/CODE_STYLE.md`、`api/README.md`、`openapi_export.py`、`cli.py` | 命令统一成 `ruff check src tests scripts migrations`；「三份」→「四份」4 处并补上 `permissions.ts`；基线数字刷新 |
| **P1-6①③④** | `.github/workflows/ci.yml`、`.github/dependabot.yml` | 三个 job 加 `timeout-minutes`（45/20/20）；dependabot 三条生态；前端 job 加「产物不含演示口令」检查 |

### 8.2 部分做了（附原因）

| 条目 | 做了 | 没做 |
|---|---|---|
| **P0-2** | 退避加 full jitter（0.5~1.0 倍）；支持 `Retry-After`（秒数 + HTTP-date）；超过 `MAX_RETRY_WAIT_SEC=30` 不重试 | **没去掉 `RETRY_STATUS` 里的 500** —— 本仓库的 500 是「网关抖动/上游重启」，与 PostgREST 的「查询失败」语义不同，照抄反而有害（已在注释里写明理由） |
| **P0-3** | `library.blob_key` 复用 `normalize_suffix`（消毒统一：`.PNG`→`.png`、路径穿越被清空） | **没合并两套 `blob_key`** —— 前缀语义不同（`library/blobs` vs `blobs`），只能合并「消毒」不能合并函数；「`BlobStore.ensure/adopt` 是死代码」这个前提还需单独核 |
| **P1-2** | `budget` 的 `* 0.8` 补上理由，并写明「落盘不在这个预算里」 | **没把 `timeout_sec` 提到 settings** —— 它现在硬编码 `60.0`，但与 `GENERATION_PERSIST_TIMEOUT_SEC` 拆开后语义已清楚，提配置项可以下一轮做 |
| **P2-3** | `task/pipeline.py` 的 `run_dir` + `is_file` 走 `asyncio.to_thread`（一次往返做完两件事） | **`library/ingest.py` 那处没动** —— `materialize` 是 `@contextmanager`，不能直接 `to_thread`，`__enter__` 跨线程不安全；要改得先把协议改成 async CM，属独立一轮 |

### 8.3 按 §6 暂缓（未动）

`P1-4`（改法不可行，需给 `ObjectStorage` 加枚举或新增走 `get_storage()` 的校验）、`P0-6②`（30 倍未复现，先止血再测）、`P2-4`（两条因果错，真问题是「超时后主动释放租约」与「handler 里的同步阻塞」）、`P1-8`（lockfile，会影响所有 PR 的安装方式）、`P1-9`（mypy 三步，测试代码 3.5 万行，4h 估算不现实）、`P2-7`、`P2-9`（与刚合的 14 笔前端改动有重叠）。

### 8.4 新增测试

`test_memory_queue.py`（**新文件，8 条**：幂等键生命周期、续接、重试次数、死信、`close` 语义、`stats`）·
`test_log_redaction.py`（**新文件，11 条**：三条规则 + 幂等 + filter 挂载 + DSN 渲染）·
`test_generation.py`（+2：`_persist` 不在预算块的 **AST 守卫**、超时退回直链）·
`test_storage.py`（+2，POSIX-only：0600 窗口、最终 umask 权限）·
`test_http_client.py`（+6：抖动、`Retry-After` 两种格式、超上限不重试、按提示等待）·
`test_cache.py`（+1：脏键不再 fail-open）· `test_config_guard.py`（+1：缓存失效）·
`test_error_envelope.py`（+1：`StorageError`→502）· `test_storage_s3.py`（+1：签名失败包装）·
`test_library_ingest.py`（+1：`blob_key` 消毒）。
