# 框架健壮性审查（第二轮）：变异测试驱动的守卫补齐

日期：2026-11-08
范围：`api/src` 框架层（core / api / db / infra）+ 门禁链路
方法：变异测试（mutation testing），不是读代码找 bug

上一轮（`CODE_REVIEW_2026-11-08_FRAMEWORK.md`）是**读**出来的：四处「文档写着不该这样、代码还这样」。
这一轮换判据 —— 不问我能不能看出问题，问**测试能不能发现问题**。

---

## 结论先说

| 指标 | 第一轮 | 现在 |
|---|---|---|
| 关键不变量注入缺陷后被发现 | 8 / 15 | **15 / 15** |
| 有测试的缺口 | 7 个 | **0 个** |
| 新增测试 | — | **52 条**（49 个用例，6 个新文件 + 2 处追加） |
| mypy | 220 files, **16 errors** | 220 files, **no issues** |
| CI 状态 | 连续 **8 次 failure** | 后端/前端/迁移三道全绿（见 §5） |

「7 个缺口」的意思是：**把框架的关键安全判定改坏，840+ 条测试没有一条会红**。
它们不是「覆盖率不够」，是「守卫不存在」——最典型的一个：把权限判定 `has_all`
改成 `any`（只要有一个权限就放行），全套测试**全绿**。

---

## 一、方法：为什么要用变异测试

「测试全绿」这个事实本身不能证明健壮：它只证明「现有测试没抓到问题」。
两者之间的差距，用**故意制造缺陷**来测量：

1. 选一批框架**必须恒成立**的性质（超管豁免、`resource:*` 通配、单例注入口不许抢位置…）；
2. 逐个把实现改坏（`if not granted:` → `if False:`）；
3. 跑该领域相关的测试文件；
4. **变红 = 这条性质被守着；全绿 = 守卫不存在**。

判据只有一条：**测试能不能发现我把实现改坏了**。

### 两个陷阱（都踩过）

**陷阱一：夹具报错被当成「守卫有效」。**
第一版用退出码判定。`--basetemp=var/mut-tmp/<n>`（两层目录）会让 `tmp_path`
夹具直接 `FileNotFoundError`，输出 `12 passed, 2 errors`，退出码非零 —— 扫描器
把它记成「已守住」。**环境坏掉之后，扫描器报告一切正常。**

修法：只认 `failed`，出现 `error` 一律标 `invalid` 并要求人工看。

**陷阱二：扫描器自己被改动的源码覆盖。**
扫描期间我改了 `src/core/serving.py`，而扫描器在变异前已经把原文件读进内存，
`finally` 里写回去 —— 我的修改会被静默抹掉。这一轮靠「每个变异只读一次、改完立刻校验
sha256」规避，但正确做法是扫描期间不碰源码。

工具已入仓：`api/scripts/mutation_check.py`（跑单个：`python scripts/mutation_check.py perm-wildcard`）。

---

## 二、第一轮的 7 个缺口

| # | 变异（把什么改坏） | 后果 | 第一轮 |
|---|---|---|---|
| 1 | `resource:*` 通配恒 `False` | 有 `task:*` 却过不了 `task:read` | **全绿** |
| 2 | `has_all` 退化成 `has_any` | `require("a","b")` 只要有一个就放行 | **全绿** |
| 3 | 错误码 `status * 100` → `* 10` | 前端按 `code // 100` 推状态全部错位 | **全绿** |
| 4 | 单例注入口重复注册不报错 | 两个实现静默互相覆盖，谁生效看 import 顺序 | **全绿** |
| 5 | 请求事务异常后不回滚 | 异常被吞，客户端拿到 200 | **全绿** |
| 6 | 驱动名不再校验 | 中文名通过 `isalnum()`，给出「请创建 中文.py」的假提示 | **全绿** |
| 7 | 请求 id 不写入上下文 | 日志全成 `-`，按请求串联排查的能力静默消失 | **全绿** |

第 5 条要说明：`async with factory() as session` 退出时 `close()` 本来就会丢弃
未提交事务，所以「忘记 rollback 导致脏数据落库」并不成立。**真正会出事的是异常被吞**：
少了 `raise`，FastAPI 认为依赖正常结束，继续返回响应。所以测试断的是「异常必须穿出去」，
而不是「查库查不到脏数据」（后者测不出区别）。

第 6 条是**修了代码**才发现测试缺口的：原校验 `name.replace("_","").isalnum()`
对中文返回 `True`（Unicode 字母算 alnum），于是校验通过、import 失败，最后给出
「请创建 `src/infra/storage/中文驱动.py`」这种永远不可能成立的提示。

---

## 三、补的测试（49 条）

| 文件 | 条数 | 守住什么 |
|---|---|---|
| `tests/test_permissions.py` | 12 | `has` / `has_all` / `has_any` 三条语义，含**不该通过**的 5 种情况 |
| `tests/test_ports.py` | 13 | `Port` 单例与多实现两种形态：重复注册、`replace`、`require` 缺失、`items()` 快照 |
| `tests/test_serving.py` | 7 | 取件四形态：本地 200（Range）、远端 307、文件缺失 404、两者皆空 404、中文名 RFC 5987 |
| `tests/test_error_envelope.py` | 6 | 任何异常都收敛成 `{code,message,data}`；`code // 100` 能反推状态 |
| `tests/test_driver_loader.py` | 6 | 非法名 / 非 ASCII / 数字开头 → `RegistryError`；未实现 → `NotImplementedError`（**不降级**） |
| `tests/test_session_scope.py` | 3 | 异常穿出依赖、`rollback` 在异常离开前执行、正常结束不回滚 |
| `tests/test_trace.py` | +4 | 中间件把 id 写进上下文、两个出口同一个 id、非法 id 被替换、**上个请求的用户不串号** |
| `tests/test_archive_serving.py` | +1 | 本地驱动下字节消失 → 404（此前只测了远端那一半） |

两个模块此前是**零测试覆盖**：`core/serving.py`（所有下载接口的必经之路）和
`infra/loader.py`（`.env` 里的驱动名决定 import 什么模块）。

### 反向验证

补完测试后把实现改回去，确认新测试会红：

- `Served.response()` 的 `is_file()` 改 `if False:` → 2 红（`test_missing_local_file...`、`test_directory_is_not_served...`）
- 权限三条、注入口、错误码、事务、驱动名、请求 id → 见 §2 表格，全部转红

**没做反向验证的测试不算守卫**，只算「多跑了几行代码」。

---

## 四、修的 4 个缺陷

### 1. 本地产物文件被清掉时返回 500（`core/serving.py`）

```
GET /api/v1/assets/<id>/file      盘上文件已删 → 500 Internal Server Error（纯文本，无错误码）
```

`FileResponse` 在发送前 `stat` 失败抛 `RuntimeError`，被兜底处理器收成 500。
「库里还有记录、盘上文件没了」是必然会发生的（GC、迁移中断、备份只还原数据库），
这是一次正常的「产物不在了」，应该 404。

修法：`response()` 里检查 `path.is_file()`，否则 `NotFound`。**一处修复覆盖 5 个调用点**
（素材原件、缩略图、导出包、成片、归档兜底）。

`archive_service._serve()` 的本地分支此前**完全没有存在性检查**，而远端分支有
（`storage.exists()` 才返回 307）—— 同一个函数的两条分支，只有一条做了检查。

### 2. 驱动名校验用 `isalnum()`（`infra/loader.py`）

见 §2 第 6 条。改成 `name.isidentifier() and name.isascii()`：`中文`、`3d` 在入口就被拒，
错误消息不再是误导性的「请创建 xxx.py」。

### 3. `scripts/sync_legacy_packs.py` 是死代码（删）

它 import 的 `core.media.MediaPack`、`infra.media_catalog.MediaCatalog` 已经不存在，
`PackService.save` 的签名也变了 —— **一执行就 `ImportError`**。能力已由
`modules/template/import_legacy.py` 承接（那个有测试）。

它是 CI 长期失败的后端唯一原因，见 §5。

### 4. mypy：16 errors → 0

三类，全在模板模块：

| 类型 | 处数 | 根因 |
|---|---|---|
| `Sequence[X]` 传给 `list[X]` 形参 | 7 | `repository` 返回 `Sequence`，`pack_service` 只声明 `list` |
| `_materialize_inputs` 的编译产物声明成 `object` | 2 | 返回注解写 `object`，于是 `compiled.prompt` 全部报「object 无此属性」 |
| `_run_out` 收 model 却标注成 schema | 4 | `runs.review()` 返回 ORM 行，`_review_map` 返回出参，两种混用 |
| `Select[tuple[Model]]` | 2 | 这一版 SQLAlchemy 里 `select(Model)` 是 `Select[Model]` |
| `summary and _source_name(summary)` | 1 | `and` 短路把类型拓宽成联合类型 |

修法都是**让注解说实话**，没有加 `type: ignore`（唯一用 `ignore` 的是原本就有的
`run.kind  # type: ignore[arg-type]`，那是枚举与字符串的已知边界）。

---

## 五、CI 的真相：连续 8 次 failure

这一轮最意外的发现，也是「为什么之前所有本地结论都不可信」：

| run | 提交 | 后端 | 前端 | PG 迁移 |
|---|---|---|---|---|
| 33 | 9551ff8c | 绿 | 绿 | 绿 |
| 34 | 4cd0393b | 绿 | **红** | 绿 |
| 37 | 7ba41e9b | 绿 | **红** | 绿 |
| 38 | b6464ed5 | — | — | — |
| 40 | b83a696c | **红（ruff）** | 绿 | **红** |
| 41 | e90923c | **红（ruff）** | 绿 | — |

**从 run 34 到 run 41，8 次提交没有一次全绿，没有人看。**

后果不是「少跑了几条测试」，而是**三道门禁从未真正执行过**：

1. `ruff check scripts`（后端 job 的第 9 步）—— 它是唯一红的那步，后面全部不执行；
2. `alembic upgrade head && alembic check`（第 10 步）；
3. `mypy --platform win32 src`（第 11 步）。

所以「mypy 220 files, no issues」这句话，在 CI 上是**从未验证过**的。同一类事故历史上有过
一次（`bcf73db 修复 CI ruff 红：scripts 仍 import 已删除的 infra.storage.archive`）——**这是第二次**。

本地复现：`ruff check src tests scripts` 只报 `scripts/sync_legacy_packs.py:24 I001`，
删掉后 `All checks passed`。sqlite 迁移检查本地补跑：`No new upgrade operations detected`。

**这一节的教训比任何一条代码缺陷都贵**：门禁的价值不在「配了」，在「红了有人看」。
现在的语义是「CI 红了 → 大概又是那个老问题 → 不管」，等于没有门禁。

---

## 六、测试自己的健壮性：一次顺序依赖

补请求 id 的那 3 条测试，初版用 `caplog` 断言「日志里带上了这个 id」。

- 单独跑 `pytest tests/test_trace.py` → 9 passed
- 全量跑 `pytest` → **2 failed**（`assert stamped` 失败，caplog 里没有那条记录）

原因：`caplog` 依赖 logger 的 `propagate`，而前面某个用例动过 logging 配置。

**一条测试如果在「单独跑」和「全量跑」下结论不同，它守不住任何东西**，只会让 CI 变成随机数。
改成直接驱动 `RequestContextMiddleware`（构造 ASGI scope，在内部读 `get_request_id()`），
与日志配置彻底解耦，顺带多测了一条「上一个请求的用户不串到下一个请求」。

这次全量：**916 passed / 12 skipped / 0 failed**（改动前 864，新增 52）。

---

## 七、仍然存在的限制（诚实清单）

| # | 限制 | 现在的实际风险 |
|---|---|---|
| 1 | outbox 是**至少一次**，多实例会重复投递 | **零**：全仓没有真实订阅者（`grep` 只匹配到 docstring 示例）。加订阅者时必须幂等 |
| 2 | `reload_settings()` 不重建 10 个 `@lru_cache` 派生单例 | 运行期无影响（生产不热改配置）；已写进 docstring，测试请 monkeypatch 单例属性 |
| 3 | `VALIDATION_FAILED = 40001` 配 HTTP 422，违反自述的 `code // 100` | 已用测试钉住，前端按 `code` 而非状态判断，当前不发作 |
| 4 | 成功响应有两种形状（裸数据 / `{code:0,...}` 信封） | 前端 `http.ts` 有兼容层；风险是业务对象恰好带 `code` 字段时被误判成信封 |
| 5 | `dispatch → submit` 之间仍有秒级授权窗口 | 已评估：归零需要注入口回调，收益不划算（上一轮结论，不变） |
| 6 | PG 迁移 job 是 `continue-on-error` 观察期，且现在红 | 阻塞上线的是它，不是 sqlite 那条。需要真实 PG 才能定位 |
| 7 | 变异测试不进 CI | 15 个变异约 3 分钟，不适合每次 push 都跑；作为手动工具 |

---

## 八、覆盖范围的诚实清单

**逐行读过**：`core/serving.py`、`core/context.py`、`core/ports/host.py`、`core/scheduler.py`、
`core/config.py`（校验与重载段）、`core/security.py`、`api/errors.py`、`api/middleware.py`、
`api/assembly.py`、`api/deps.py`、`db/session.py`、`db/outbox.py`、`db/app_meta.py`、`infra/loader.py`。

**只读了签名与关键分支**：`infra/archive/*`、`infra/queue/*`、`core/ratelimit.py`、
`core/events.py`、`db/repository.py`。

**没读**：`modules/*` 的业务实现（除为修 mypy 而读的 `modules/template` 三个文件）、
`studio/` 的 UI 层、`migrations/versions/*`。

所以本报告**不覆盖业务正确性**，只覆盖「框架的不变量是否被测试守着」。

---

## 附：怎么复查这份结论

```bash
cd api
# 1) 全量测试
python -m pytest -q --basetemp=var/pt

# 2) 变异扫描（15 个不变量，约 3 分钟）
python scripts/mutation_check.py

# 3) 类型与静态检查（与 CI 完全一致）
python -m ruff check src tests scripts && python -m mypy --platform win32 src
```

扫描输出 `缺口 0` 是这份报告唯一的核心论据。任何一个变异变成 `GAP`，
都意味着那条不变量重新失去守卫 —— 那比多一条失败用例严重得多。
