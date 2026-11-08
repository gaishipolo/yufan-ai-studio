# 走查记录：按 `EXTENDING.md` 从零做一个「导出」功能

> **目的**：把手册里的「加一个业务模块（5 分钟）」在真实项目上完整走一遍，
> 看它会不会卡住。卡住的每一处都当场修掉，并补上能拦住同类问题的守卫。
>
> **结论**：导出功能本身约 500 行、一次写完；**框架层暴露 9 处缺口**，全部已修。
> 其中 3 处是「静默失败」（不报错、不提示、症状出现在很远的地方），这类才是真问题。
>
> 环境：Windows 11 / Python 3.13 / sqlite / `QUEUE_DRIVER=memory` /
> `STORAGE_DRIVER=local` / `ARCHIVE_DRIVER=local`

## 0. 结果摘要

| 指标 | 走查前 | 走查后 |
|---|---|---|
| `pytest` | 789 passed / 1 failed（迁移红） | **807 passed / 12 skipped / 0 failed** |
| `mypy src` | 202 文件 | 209 文件，无问题 |
| `ruff check src tests` | 通过 | 通过 |
| `app doctor` | 23 项全绿 | 23 项全绿 |
| 四份契约 `--check` | 一致 | 一致 |
| 权限点 | 32 | 33（+`export:read:audit`） |
| 路由 | export 2 条（501 占位） | export 6 条 |

新增/修改文件：

```
新增  api/src/core/serving.py                      取件出参（从 asset 上移）
新增  api/src/core/ports/export_sources.py         导出源注入口
新增  api/src/modules/task/export_source.py        task 注册 `tasks` 源
新增  api/src/modules/export/{enums,sources,worker,maintenance}.py
新增  api/migrations/versions/1c19c2bf9bdf_export_init.py
新增  api/tests/test_export.py                     15 条端到端
改写  api/src/modules/export/{__init__,models,schema,service,dependencies,router,errors}.py
改写  api/migrations/script.py.mako                迁移模板（风格对齐）
改动  api/src/api/discovery.py                     +_load_errors（上一轮已做）
改动  api/src/core/exceptions.py                   +号段码值闸门（上一轮已做）
改动  api/src/api/ports.py                         +export-sources 登记
改动  api/tests/test_architecture.py               +2 条守卫
改动  api/src/core/config.py / .env.example        +3 个导出配置
```

---

## 1. 逐步记录

每一步都按同一格式：**命令 → 实际输出 → 预期 → 判断（达标 / 不达标）**

### 步骤 1｜重新生成模块骨架

```bash
rm -rf src/modules/export
app new-module export --label 导出 --perm export --slot 9 --group 内容生产
```

实际输出：

```
已创建模块 export：
  + src\modules\export\__init__.py
  + src\modules\export\router.py
  + src\modules\export\schema.py
  + src\modules\export\service.py
  + src\modules\export\dependencies.py
  + src\modules\export\models.py
  + src\modules\export\errors.py

下一步：
  1. 补全 service.py 的业务逻辑（当前接口返回 501）
  2. 执行 alembic revision --autogenerate -m 'export init'，然后 alembic upgrade head
     （漏了这步，测试环境照样绿，生产第一条查询就 no such column）
  3. 重启服务，`app modules` 应能看到该模块
  4. 权限点会自动并入内置角色 ...
```

**预期**：7 个文件、`--group` 生效、退出码 0。
**实际**：一致。`__init__.py` 里 `group="内容生产"` 正确写入。
**判断**：✅ 达标。

> 一处小瑕疵（不阻断）：`description="导出"` 与 `tags=["导出"]` 完全相同，
> `app modules` 的「说明」列因此没有信息量。脚手架没有 `--desc` 参数，
> 目前只能手改。**未修**，见第 3 节。

### 步骤 2｜静态检查与自动发现

```bash
ruff check src/modules/export/     # All checks passed!
app modules
```

实际（节选）：

```
export      2     export:create,export:delete,export:read,export:update   导出
```

**预期**：模块出现、2 条路由、权限点在「内容生产」分组下。
**实际**：一致。
**判断**：✅ 达标（权限点数量在下一步修正）。

### 步骤 3｜删掉业务上不存在的权限点

导出没有「改」——一份已生成的数据快照，改它只能是重新导一次（那是 create）。
所以删掉 `export:update`，同时改说明文案：

```python
# __init__.py
description="数据导出：按数据源异步生成 CSV，产物到期自动回收",
permissions=(
    PermissionSpec("export:read", "查看导出", group="内容生产"),
    PermissionSpec("export:create", "创建导出", group="内容生产"),
    PermissionSpec("export:delete", "删除导出", group="内容生产"),
),
```

连带删掉 `dependencies.py` 的 `ExportUpdateDep` 和 `schema.py` 的 `UpdateExportIn`
（删权限不删依赖，会留下一个永远 403 的死依赖）。

```bash
app modules
# export  2  export:create,export:delete,export:read   数据导出：按数据源异步生成 CSV，产物到期自动回收
# 权限点合计：32
```

**预期**：3 个权限点，说明列有信息量。
**实际**：一致。
**判断**：✅ 达标。

> **这里发现手册缺一句话**：脚手架固定生成 4 个 CRUD 权限，但手册没提醒
> 「用不到的权限必须删掉」。不删的后果不是报错，而是角色配置界面里多出
> 永远用不上的开关，管理员会以为配了就该生效。见问题 P9。

### 步骤 4｜定义表

`models.py` 按导出语义重写：`data_source` / `params(JSON)` / `status` /
`row_count` / `object_key` / `byte_size` / `error` / `attempts` / `requested_by` /
`started_at` / `finished_at` / `expires_at`，外加两个联合索引。

```bash
ruff check src/modules/export/
```

第一次结果：**3 条红**

```
UP042 Class ExportStatus inherits from both `str` and `enum.Enum`
F401   `sqlalchemy.Text` imported but unused
```

**预期**：生成即合规。
**实际**：不达标。
**判断**：❌ 我自己的代码问题，改 `StrEnum` + 删未用导入。

> 这是「生成即合规」这条承诺的反面教材：lint 能拦住，说明这类问题不需要靠人。

### 步骤 5｜生成迁移

```bash
alembic revision --autogenerate -m "export init"
```

实际输出：**空**。`echo $?` → **1**。

**预期**：打印 `Detected added table 'exports'` 并生成文件。
**实际**：失败，且**一个字都没输出**。
**判断**：❌ **不达标（问题 P1）**。

诊断过程：

```bash
alembic --help; echo $?          # 退出码 1，无输出  ← 连帮助都跑不了
python -m alembic --help; echo $? # 退出码 0，正常输出
```

结论：不是项目配置问题，是 venv 里 `alembic.exe` 这个 pip 启动器坏了。
修复：

```bash
pip install --force-reinstall --no-deps alembic   # 重建启动器，版本不变
alembic --help; echo $?                           # 0 ✅
```

重试 `alembic revision` 成功，生成了 `89cea5ed59ac_export_init.py`。

### 步骤 6｜对齐迁移模板

生成出来的迁移文件用旧式写法：

```python
from typing import Sequence, Union
down_revision: Union[str, Sequence[str], None] = 'b3d6f8a1c204'
```

而仓库里现有 17 份迁移全是：

```python
from collections.abc import Sequence
down_revision: str | Sequence[str] | None = "b3d6f8a1c204"
```

**预期**：生成物与现有文件同风格，不需要手改。
**实际**：不达标（每次 autogenerate 都要手工收拾一遍）。
**判断**：❌ 不达标（问题 P2）。修复：改 `migrations/script.py.mako`，
并在模板 docstring 里加一句「为什么改 / 旧数据怎么办 / 能不能回滚」。

删掉刚生成的文件，重跑：

```bash
alembic revision --autogenerate -m "export init"
# Generating ...\1c19c2bf9bdf_export_init.py ... done
ruff check migrations/versions/1c19c2bf9bdf_export_init.py   # All checks passed!
```

✅ 达标。

### 步骤 7｜执行迁移

```bash
alembic upgrade head
# INFO [alembic.runtime.migration] Running upgrade b3d6f8a1c204 -> 1c19c2bf9bdf, export init

alembic current      # 1c19c2bf9bdf (head)
pytest tests/test_migrations.py -q   # 5 passed
app doctor | grep schema.head        # [PASS] 数据库已到 head（1c19c2bf9bdf）
```

**预期**：head 前移、`test_models_match_migrations` 由红转绿（走查前它就是那条唯一的红）。
**判断**：✅ 达标，且**原本的红是正常的**——它正是在提醒「定义了表没迁移」。

### 步骤 8｜接跨模块数据源（发现最关键的架构缺口）

导出要读 `video_tasks`，而 `tests/test_architecture.py::test_modules_do_not_import_each_other`
禁止 `modules.export` import `modules.task`。

手册 §5.1 给了注入口模式，但现有 5 个注入口全是「权威来源只能有一个」的单例形状，
没有「多个模块各贡献一份」的形状。

**判断**：❌ 框架缺口（问题 P5）。
修复：新增 `core/ports/export_sources.py`（注册表形状）+ 在 `api/ports.py`
的 `PORT_CATALOG` 登记 + 补 `has_export_source()`（守卫要求 register/reset/has 三件套）。
`modules/task/export_source.py` 在 import 期注册 `tasks` 源。

### 步骤 9｜取件出参（发现第二个「第二个模块才暴露」的问题）

下载要把存储里的字节交给浏览器。现成实现是 `modules/asset/serving.py` 的 `serve()`，
但 `modules.export` import 它同样违规。

**判断**：❌ 框架缺口（问题 P4）。
修复：上移到 `core/serving.py`（加 `filename` 参数），`modules/asset/serving.py`
改成 re-export 兼容入口，asset 的 43 条测试全绿。

### 步骤 10｜写业务与注册

写 `sources.py`（内置 `audit_events` 源 + CSV 渲染）、`service.py`、`router.py`、
`worker.py`、`maintenance.py`，然后在 `__init__.py` 里副作用 import：

```python
from modules.export import maintenance, worker  # noqa: E402,F401  isort: skip
```

```bash
python -c "...discover_modules(); print(periodic_jobs(), handler_names())"
# 周期任务: ['export.reclaim', 'export.stale']
# handler: ['export.build']
# 注册的导出源: ['tasks']

app modules
# export      6     export:create,export:delete,export:read,export:read:audit   数据导出：...
# Job handler：['export.build', ...]
# 周期任务：... export.reclaim(3600s), export.stale(600s), ...
```

**预期**：6 条路由、4 个权限点、1 个 handler、2 个周期任务、1 个注册源。
**判断**：✅ 达标。

> 但第一次跑到这里时 `export:read:audit` **没有出现在权限点里**——
> 我在 `sources.py` 里写了 `permission = "export:read:audit"`，却忘了在
> `ModuleSpec.permissions` 声明它。症状是「超管能导操作留痕，其他人永远 403」，
> 而且**没有任何检查会报错**（静态检查只扫 `require("...")` 字面量）。
> 见问题 P6。

### 步骤 11｜写测试（这一步抓到了 2 个真 bug）

```bash
pytest tests/test_export.py -q
# 10 failed, 5 passed
# AttributeError: 'str' object has no attribute 'value'
```

根因：`CamelModel` 开了 `use_enum_values=True`，`CreateExportIn.data_source`
到 service 时已经是 `str`，`payload.data_source.value` 必然崩。
修复：`source_name = str(payload.data_source)`（对 `StrEnum` 和 `str` 都成立）。

改完后仍有一批 500，原因是 `service.py` 里 `detail/download/delete` 都调用
`self._get_owned()`，而**这个方法我根本没写**（写文件时漏了整节）。
`ruff` 与 `mypy` 都抓不到 `self.xxx` 不存在——只有运行到才会 500。
补上 `_get_owned` 与 `_reject_duplicate` 后：

```bash
pytest tests/test_export.py -q
# 15 passed
```

**判断**：✅ 达标（15 条含真的跑 worker、真的读回文件、真的回收）。

### 步骤 12｜同步四份契约

```bash
app export-openapi
# 已写入 json / md / ts / perms（80 → 86 接口，9 → 10 模块）
app export-openapi --check
# 无变化 ×4
```

**预期**：四份一致，`test_openapi_contract.py` 的 4 条由红转绿。
**判断**：✅ 达标。

### 步骤 13｜全量验证

```bash
pytest -q                  # 807 passed, 12 skipped
mypy src                   # 209 files, no issues
ruff check src tests       # All checks passed
app doctor                 # 共 23 项：失败 0、警告 0
```

✅ 全绿。

---

## 2. 发现的问题（按严重度）

### P1 `alembic.exe` 静默失败：退出码 1、零输出 🔴

- **症状**：`alembic revision --autogenerate` 什么都不打印，`$?` 是 1。
  很容易被当成「跑完了」，然后发现迁移没生成，回去怀疑配置。
- **根因**：venv 里的 `alembic.exe` 是 pip 生成的启动器，venv 被复制/移动过之后
  会坏。`python -m alembic` 走的路径不同，正常。
- **修复**：`pip install --force-reinstall --no-deps alembic` 重建；手册 §1 补了
  判断命令和替代写法。
- **守卫**：无法自动化（环境级）。文档是唯一防线。

### P2 迁移模板与仓库风格不一致 🟡

- **症状**：`autogenerate` 生成的迁移用 `typing.Union`，现有 17 份全是 `X | None`。
- **根因**：`migrations/script.py.mako` 是 alembic 出厂模板，从没对齐过。
- **修复**：改模板（现代 typing + `collections.abc`），并在 docstring 里加了
  「为什么改 / 旧数据怎么办」的写作提示。
- **守卫**：`ruff` 已覆盖语法层面；风格层面靠模板本身。

### P3 `require()` 拼错权限码无任何检查 🔴

- **症状**：把 `asset:read` 写成 `assets:read`，只有普通用户永远 403，
  超管（`*`）一切正常；启动、doctor、契约生成、既有测试全都不报错。
- **根因**：三条权限测试都只检查「声明侧」，没有一条检查「路由实际用了什么」。
- **修复**：`tests/test_architecture.py` 新增 `test_required_codes_are_declared_somewhere`，
  AST 扫所有 `require("...")` 字面量，逐个对 `catalog.codes()`。
- **反向验证**：把 `export:read` 改成 `export:readx`，
  报 `export: require('export:readx') 没有任何模块声明`，改回即绿。

### P4 取件出参埋在业务模块里 🔴

- **症状**：第二个需要下载的模块，只能违规 import `modules.asset.serving` 或抄一份。
- **根因**：横切能力放在了业务模块里，第一个模块写的时候没人觉得有问题。
- **修复**：上移 `core/serving.py`，asset 保留 re-export（不制造改名风暴）。
- **验证**：asset 43 条测试全绿；导出下载走同一个 `serve()`。

### P5 缺「多实现」形状的注入口 🔴

- **症状**：导出必须读 `video_tasks`，但业务模块之间禁止互相 import，
  现有注入口全是单例形状，装不下「很多模块各贡献一份」。
- **修复**：新增 `core/ports/export_sources.py`，注册表形状（register / items /
  has / reset），并在 `PORT_CATALOG` 登记（守卫 `test_port_modules_expose_the_full_shape`
  立刻要求补 `has_*`，说明守卫是有效的）。
- **附带价值**：`modules/task` 从此**不需要知道**导出功能存在。

### P6 权限码藏在数据里，静态检查覆盖不到 🔴

- **症状**：`ExportSource.permission = "export:read:audit"` 忘声明，
  只有超管能导操作留痕，无任何报错。
- **根因**：P3 的守卫只扫 `require("...")` 字面量，扫不到挂在对象属性上的权限码。
- **修复**：新增 `test_export_sources_declare_known_permissions`，
  遍历注册表 + 内置源，逐个对 `catalog.codes()`。

### P7 `use_enum_values=True` 让枚举字段退化 🟡

- **症状**：`payload.data_source.value` → `AttributeError: 'str' object has no attribute 'value'`。
- **根因**：`core/schema.py` 的 `CamelModel` 开了 `use_enum_values=True`，
  枚举在校验阶段就被替换成值。这是正确的设计（序列化友好），但**隐式**。
- **修复**：用 `str(payload.data_source)`（`StrEnum` 与 `str` 都成立），
  并在 service 里写明原因。

### P8 漏写方法，我以为静态检查抓不到（结论已更正）🟡

- **症状**：`detail/download/delete` 调用 `self._get_owned()`，方法不存在 → 运行到才 500。
- **当时的结论（错）**：`ruff` / `mypy` 不做 `self` 属性存在性检查。
- **实测**：mypy 明确报
  `error: "A" has no attribute "_missing_helper"  [attr-defined]`。
  真实原因是我写完 service 先跑了测试、后补方法，那一次 mypy 是在补完之后才跑的。
- **修复**：补上方法。
- **教训**：把「我没跑到」写成「工具做不到」，会让后面的判断建立在错的前提上
  （这条错误结论当时推出了「只能靠端到端测试发现」——测试该写，但不是因为这个理由）。
  写结论前先花十秒验证。

### P9 脚手架与手册不覆盖「后台任务」这一段 🟡

- **症状**：新模块要写 worker / periodic 时，脚手架不生成这两个文件，
  也没提醒要在 `__init__.py` 里副作用 import。漏了不报错：
  任务永远 queued，周期任务在 `app modules` 里看不见。
- **修复**：手册 §1 补了完整示例 + 「`app modules` 是唯一检查点」。
- **未修**：脚手架仍不生成这两个文件（按需创建比预生成空壳更合理）。

---

## 3. 刻意没做的事

| 项 | 为什么 |
|---|---|
| 给脚手架加 `--desc` 参数 | `description` 就一行字符串，手写比参数化更直接。已在手册 §1 提示「说明列没信息量就手改」 |
| 导出源支持「按权限导全量」 | 导出是「我自己那个列表的快照」。有 `read:all` 能看别人的，不等于能一键带走全公司数据 |
| 失败自动重试 | 导出是用户主动操作，失败让他再点一次，比在数据源故障时反复打库合理。`max_attempts=1` |
| 前端代码 | 按约定只做规划，见第 4 节 |

---

## 4. 前端接入规划（只规划，不动代码）

### 4.1 接口映射

| 场景 | 接口 | 说明 |
|---|---|---|
| 列表页 | `GET /api/v1/exports?page&pageSize&status&dataSource` | 只返回自己的；`total` 是全量 |
| 数据源下拉 | `GET /api/v1/exports/sources` | **不要硬编码**，来自运行时注册表 |
| 新建 | `POST /api/v1/exports` → **202** | 202 不是 201：此刻还没有文件 |
| 详情/轮询 | `GET /api/v1/exports/{id}` | 多一个 `downloadable`，前端不用自己算 |
| 下载 | `GET /api/v1/exports/{id}/download` | 本地驱动 200 直出；远端驱动 307 |
| 删除 | `DELETE /api/v1/exports/{id}` → 204 | 生成中删除返回 409 |

### 4.2 类型与权限

类型直接从 `studio/src/shared/api/schema.ts` 取，不要手写：
`ExportOut`、`ExportDetailOut`、`CreateExportIn`、`ExportParams`、`ExportSourceOut`。

| 权限 | 控制什么 |
|---|---|
| `export:read` | 菜单项 / 页面可达 |
| `export:create` | 「新建导出」按钮 |
| `export:delete` | 行内删除 |
| `export:read:audit` | 数据源下拉里是否出现「操作留痕」（后端也会 403，不能只靠前端隐藏） |

### 4.3 三个必须和后端对齐的点

1. **下载不能直接 `window.open(url)`**：接口要 `Authorization` 头，
   新窗口带不上。用 `fetch(url, { headers })` → `blob()` → `URL.createObjectURL`
   触发下载，用完 `revokeObjectURL`。CSV 上限 10 万行（约 20MB），内存可接受。
2. **轮询要有终止条件**：只在 `status ∈ {queued, running}` 时轮询（3s），
   到 `succeeded/failed/expired` 立即停；`document.hidden` 时暂停。
3. **不要自己推断能不能下载**：用详情里的 `downloadable`。
   前端各写一遍 `status === "succeeded" && expiresAt > now` 必然和回收任务对不上，
   用户会看到「按钮亮着，点进去 410」。

### 4.4 状态展示

| status | 文案 | 可否下载 |
|---|---|---|
| `queued` | 排队中 | 否 |
| `running` | 生成中 | 否 |
| `succeeded` | 已完成 + 行数/体积 | 是（`downloadable`） |
| `failed` | 失败 + `error` 文案（可直接展示） | 否（可重导） |
| `expired` | 已过期，文件已回收 | 否（可重导） |

---

## 5. 复现命令

```bash
cd api

# 骨架
app new-module <name> --label <中文> --perm <前缀> --slot <1-9> --group <分组>
ruff check src/modules/<name>/
app modules

# 迁移（注意 alembic.exe 可能坏掉，见 P1）
alembic revision --autogenerate -m "<name> init"
alembic upgrade head
pytest tests/test_migrations.py -q

# 契约
app export-openapi && app export-openapi --check

# 全量
pytest -q && mypy src && ruff check src tests && app doctor
```

---

## 6. 给下一次的检查项（已并入 `docs/REVIEW_CHECKLIST.md`）

- `BE-44` 唯一性检查覆盖的域要和它声称的一致（局部去重 ≠ 全局唯一）
- 新增：路由里 `require` 的码必须有人声明（AST 守卫）
- 新增：挂在数据上的权限码（导出源这类）必须有人声明（注册表守卫）
