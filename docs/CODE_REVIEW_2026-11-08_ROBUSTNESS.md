# 框架健壮性审查（第二轮）：变异测试驱动的守卫补齐

日期：2026-11-08
范围：`api/src` 框架层（core / api / db / infra）+ 门禁链路
方法：变异测试（mutation testing），不是读代码找 bug

上一轮（`CODE_REVIEW_2026-11-08_FRAMEWORK.md`）是**读**出来的：四处「文档写着不该这样、代码还这样」。
这一轮换判据 —— 不问我能不能看出问题，问**测试能不能发现问题**。

---

## 结论先说

| 指标 | 第一轮 | 第二轮（本文主体） | 现在 |
|---|---|---|---|
| 关键不变量注入缺陷后被发现 | 8 / 15 | 15 / 15 | **29 / 29** |
| 有测试的缺口 | 7 个 | 0 个 | **0 个**（又发现 3 个并补齐，§9） |
| 新增测试 | — | 54 条 | **86 条**（含 §9 的 32 条） |
| mypy | 220 files, **16 errors** | 220 files, no issues | 221 files, **no issues** |
| 全量测试 | 864 | 918 | **950 passed / 12 skipped** |
| CI 状态 | 连续 **8 次 failure** | 后端/前端全绿；PG 迁移根因已修（§5 补记） | 全绿（run 42/43） |

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
| `tests/test_migrations.py` | +2 | 迁移不得使用 sqlite 专有类型；每个迁移必须有 `downgrade()` |

两个模块此前是**零测试覆盖**：`core/serving.py`（所有下载接口的必经之路）和
`infra/loader.py`（`.env` 里的驱动名决定 import 什么模块）。

### 反向验证

补完测试后把实现改回去，确认新测试会红：

- `Served.response()` 的 `is_file()` 改 `if False:` → 2 红（`test_missing_local_file...`、`test_directory_is_not_served...`）
- 权限三条、注入口、错误码、事务、驱动名、请求 id → 见 §2 表格，全部转红

**没做反向验证的测试不算守卫**，只算「多跑了几行代码」。

---

## 四、修的 5 个缺陷

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

### 5. 下行迁移用了 sqlite 专有类型（`migrations/versions/df546efef239`）

`downgrade()` 里 `sa.Column('created_at', sa.DATETIME(), ...)` —— 这是
`sqlalchemy.dialects.sqlite` 专有类型，通用的是 `sa.DateTime`。PG 上执行报
`type "datetime" does not exist`。

`upgrade` 是通的（新表都用通用类型），断的是 `downgrade`：**回滚部署这条路是断的**。
sqlite 上一切正常，本地测不出来。详见 §5 补记。

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
| 42 | aa990d2 | **绿** | **绿** | 红（根因已定位并修复，见下） |

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

### 补记：run 42 与 PG 的真正根因

修完推上去，run 42 **整体 Success**（run 33 之后第一次）：后端 1m59s、
前端 1m29s，`mypy` 这一步在 CI 上**第一次真正执行并通过**。

但 PostgreSQL job 仍然红（`continue-on-error` 使整体不阻塞）。于是本地起了
`postgres:16-alpine` 复现，四步逐条跑：

```
alembic upgrade head        → 通过
alembic check               → No new upgrade operations detected
alembic downgrade base      → UndefinedObjectError: type "datetime" does not exist
alembic upgrade head        → 未执行
```

根因在 `df546efef239` 的 `downgrade()`：

```python
sa.Column('created_at', sa.DATETIME(), nullable=False)
```

`sa.DATETIME` 是 **sqlalchemy.dialects.sqlite 专有**类型，通用类型是 `sa.DateTime`。
它进迁移的唯一途径是 autogenerate 对着一个 **sqlite 库**做反射。

**这条的价值在于「哪条路断了」**：`upgrade` 能过（新表都用通用类型），所以
「新部署」是通的；断的是 `downgrade` —— 也就是「出事要回滚」的那一刻。
而 sqlite 上一切正常，本地永远测不出来，`test_migrations.py` 当时也只跑 sqlite。

修完 PG 四步全通。守卫是纯文本扫描（`test_migrations_do_not_use_sqlite_only_types`），
不需要数据库 —— 因为它要防的正是「本地 sqlite 测不出 PG 方言」。

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
| 6 | PG 迁移 job 是 `continue-on-error` 观察期 | **已修**：下行迁移的 sqlite 专有类型（§5 补记），PG 四步全通；建议观察一周后去掉 `continue-on-error` |
| 7 | 变异测试不进 CI | 29 个变异约 5 分钟，不适合每次 push 都跑；作为手动工具（§9 已从 15 增至 29） |
| 8 | `tests/test_storage_s3_live.py` 的 **12 条永远跳过** | S3 驱动只在 mock 层被验证过；真实的 multipart / 预签名 / 断点续传没跑过。**建议**：CI 加 MinIO service（`s3` 镜像，`S3_LIVE_ENDPOINT` 指过去），与 PG job 同形态。这是目前唯一「写进测试但从不执行」的整块区域 |

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

## 九、第二轮追加（2026-11-09）：守卫自己的盲区

第一轮补齐的 15 个不变量都有守卫了。这一轮换了个问法：**守卫自己会不会失效**。

答案是会，而且失效方式高度一致——**扫不到东西时不报错**。目录改名、`glob` 不匹配
当前布局、`include_router` 换实现、过滤条件写反，结果都是「零个问题」，与「全都
没问题」在输出上完全相同。这一类已经在本仓真实发生过 4 次，所以这轮把「守卫的
产出量」本身也变成了断言。

### 9.1 找到的真问题（按严重度）

**① 启动期权限核对漏扫了 `template` 的全部 16 条路由（已修）**

`api/assembly.py::permission_gaps` 遍历 `spec.router.routes`。而 `template/router.py`
只是两个 `include_router`，所以 `routes` 里是两个 `_IncludedRouter` **占位对象**，
既没有 `.dependant` 也没有 `.path` —— 循环体一次都没执行。

后果：`require("template:reviewx")` 这种拼写错误，启动期核对返回「无缺口」。
超管（`*`）自测一切正常，只有恰好持有 `template:read` 的人会一直 403。

讽刺的是，同一个坑 `api/ratelimit_policy.py` 早就踩过并写了 `iter_api_routes` 来
展开（它的 docstring 里明确写着「守卫会变成永远通过的空检查」）——权限核对没复用它。
修法：抽出 `api/routing.py::iter_api_routes`，两边共用；三条测试钉住
（含一条全量断言「每个模块的路由都必须能展开出东西」）。

**② 声明了却没人用的权限点，没有任何人管（已补守卫）**

已有两条守卫只看一个方向（用了的码必须有声明）。反方向没人管：一个权限码声明后
没人 `require`、没人 `actor.has`，它就是个**什么也不控制的开关**，却会出现在角色
配置界面里，让管理员以为配好了。

补 `tests/test_permission_coverage.py`：AST 扫全部 `src/**`，认出三种判定形状
（`require(...)` / `actor.has|has_all|has_any(...)` / 导出源的 `permission` 属性），
并且**能跨文件解析常量**——33 个权限点里有 12 个写成 `actor.has(PERM_READ_ALL)`，
只扫字面量会得到 12 条假阳性，那等于没有守卫。

当前结果：**33 个声明，33 个都有判定点，豁免表为空**。同一条守卫顺带覆盖了
服务层的拼写错误（`actor.has("task:raed:all")` 以前谁都看不见）。

**③ 内置角色模板的权限码没人核对（已补守卫）**

`BUILTIN_ROLES` 里手写的 `"catalog:read"` / `"template:use"` 一旦写错，角色建出来
就少一个能力，超管自测完全看不出来（`*` 直接放行）。`identity/service.py` 里那句
「上一个模块就撞上了：`template:use` 写进了声明，库里的 editor 仍然没有」就是
这个事故的现场记录。补：模板里的每个码（含 `资源:*` 通配）都必须在权限目录里存在。

**④ 令牌缺声明也能过（已收紧）**

变异「不校验 `sub`」跑全套 1000+ 用例 **全绿** —— 即「没有主体的令牌」从来没被
测过。继续追下去发现更硬的：`jwt.decode` 默认只在校验 `exp` 存在**时**判有效期，
所以

- 不带 `exp` 的票**永不过期**（签发路径漏写一行即可产生，没有任何现象）；
- 不带 `iat` 的票被解析器读成 `issued_at=0`，于是**静默跳过「改密让旧票失效」**
  （`principal.py` 的判断条件就是 `if principal.issued_at and ...`）。

已把 `exp`/`iat` 改成 `options={"require": [...]}`，并补 18 条逐声明用例
（`tests/test_token_claims.py`）：用途互换、空/缺 `sub`、缺 `sid`、缺 `jti`、
缺 `exp`、缺 `iat`、过期、伪造、篡改、TTL 上下界。

### 9.2 顺带修的一致性问题

| 位置 | 问题 | 影响 |
|---|---|---|
| `cli.py::_cmd_modules` | `len(spec.router.routes)` | `app modules` 把 template 报成 2 条路由，实际 16 条（运维据此误判模块规模）|
| `test_architecture.py::_required_codes` | `glob("*.py")` | 模块内再分子包时整份漏扫 |
| `test_architecture.py::test_every_port_is_catalogued` | `glob("*.py")` + 无产出量断言 | 同上；注入口挪进子包就静默失效 |
| `scripts/mutation_check.py` | 锚点不唯一时**静默跳过** | 汇总只显示一个 `??`，看不出「这条不变量其实没测」|

### 9.3 数字

| 项 | 之前 | 现在 |
|---|---|---|
| 变异数 | 15 | **29**（新增 4 组权限 + 7 组鉴权/调度 + 各 1 条）|
| 全量测试 | 918 | **950 passed / 12 skipped** |
| 变异结论 | 15/15 | **29/29（缺口 0、无效 0）** |

这一轮自己也踩了一次「守卫失效」：改 `test_permission_coverage.py` 修 ruff 时，把
`getattr(node, "target", None)` 改成了 `node.target`，于是 `ast.walk` 走到任何非
赋值节点都会 `AttributeError`，4 个用例 error。**是变异扫描把它标成 `invalid` 才
发现的**（只看「有没有 failed」会当成「守住了」）。这正是 §1 陷阱一的复用价值。

---

## 附：怎么复查这份结论

```bash
cd api
# 1) 全量测试
python -m pytest -q --basetemp=var/pt

# 2) 变异扫描（29 个不变量，约 5 分钟）
python scripts/mutation_check.py

# 3) 类型与静态检查（与 CI 完全一致）
python -m ruff check src tests scripts && python -m mypy --platform win32 src
```

扫描输出 `缺口 0` 是这份报告唯一的核心论据。任何一个变异变成 `GAP`，
都意味着那条不变量重新失去守卫 —— 那比多一条失败用例严重得多。
