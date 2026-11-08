# 走查：删掉生图模块，再照《扩展指南》长回来

> 目的不是「证明手册好用」，而是**拿一次真实的模块删除与重建去压框架的扩展面**。
> 加功能只是手段：每一步都要问「这件事该不该由使用手册的人来做」，不该，就说明
> 框架把负担放错了地方。

- 时间：2026-11-08
- 起点提交：`7ba41e9`
- 走查对象：`modules/image`（删）→ 按 `docs/EXTENDING.md` 重建
- 判定标准（开工前定好，避免事后解释）：
  1. `app modules` 看到 image，**权限点 4 个、接口 3 个**（与删除前一致）；
  2. 54 条 `test_image_*.py` 用例全绿（用例即行为规格）；
  3. `app doctor` 24 项、失败 0、警告 0；
  4. 四份生成物 `--check` 无变化 —— **前端一行都不用改**；
  5. 全量 `pytest` 绿。

第 4 条是这个走查最有价值的一条：接口契约只要漂了一点，前端就得跟着动。重建之后
它必须**原地踏步**。

---

## P0 基线（动手删之前先记下来）

```bash
cd api
git rev-parse --short HEAD                    # 7ba41e9
app modules | grep image                      # image  3  image:create,image:read,image:read:all,image:retry:any
app doctor | tail -1                          # 共 24 项：失败 0、警告 0
python -c "…读 docs/openapi.json…"             # /api/v1/images(GET)、/api/v1/generations/image(POST)、
                                              # /api/v1/images/{job_id}/retry(POST) —— 3 条路径
pytest tests/test_image_*.py -q                # 54 passed
```

规模：`modules/image` 9 个文件 1345 行（其中 pipeline 386、maintenance 214、service 238）。

---

## P1 删除模块 —— 一次就把框架的三处反向依赖暴露出来

```bash
git rm -r src/modules/image
rm -rf src/modules/image/__pycache__           # 只删 .py 会留下空目录，架构测试会说"模块目录没导出 module"
```

**预期**：模块从清单里消失，`image.*` 权限点消失，其余一切照常。
**实际**：清单确实干净了（33 → 29 个权限点、流水线少一个 kind）……然后两个工具直接崩。

### 结果 1：`app doctor` 崩在半路

```bash
$ app doctor
  File "src/api/diagnostics.py", line 231, in _check_database
    from modules.image.models import ImageJob
ModuleNotFoundError: No module named 'modules.image.models'
$ echo $?          # 1
```

这一刀切得很准：doctor 有 24 项检查，它是**第 5 项**。崩在这里意味着后面 19 项
（schema head、缓存、队列、outbox、指标、配额、归档完整性……）一条都看不到。
一个「一次把所有问题列全」的工具，因为一条业务检查而整份报告失联。

### 结果 2：整个测试套件无法收集

```bash
$ pytest -q
ImportError while loading conftest 'tests/conftest.py':
  tests/conftest.py:50: from seed import seed
  src/seed.py:21:       from modules.image.models import ImageJob
ModuleNotFoundError: No module named 'modules.image.models'
$ echo $?          # 4（不是 1：根本没有用例跑起来）
```

**这才是真正的发现**：删掉一个业务模块，**816 条用例一条都跑不了**。扩展性在这里
是反的——模块不是插件，而是框架的一部分。

### 三处反向依赖，都违反手册第 0 节第 1 条

| 位置 | 干了什么 | 为什么必须由它干 |
|---|---|---|
| `api/diagnostics.py::_check_database` | import `modules.asset.models` + `modules.image.models`，跑「旧 /static 地址」「归档镜像」「配额」四条业务检查 | 不该。这四条只对各自模块成立 |
| `cli.py::_strip_legacy_urls` | import `ImageJob`，扫全表剥旧地址，挂在 `app migrate-storage` 尾巴上 | 不该。「库里存了本服务的绝对地址」是**跨模块数据纪律** |
| `seed.py` | 顶层 import 6 个模块的模型；`--reset` 手写删除清单 | 不该（清单漏一个 = 静默不清）；`seed` 钩子（§5.4①）才是正解 |

**为什么守卫没拦住？** `test_architecture.py::test_lower_layers_do_not_import_modules`
只参数化了 `core / db / infra / generation`，**漏了 `api/` 和顶层脚本**。手册写了
「`api` 不 import 任何业务模块」，但没有任何一条测试在守它——文档里的约定，没进 CI
就是建议。

---

## P2 修框架（先止血，再继续按手册扩展）

### F1 模块自检变成插槽：`ModuleSpec.doctor`

`api/module.py` 新增字段 + `DoctorReportLike` 协议（只声明 `add`）；`api/diagnostics.py`
删掉 102 行业务耦合代码，换成通用执行器：

```python
async def _check_modules(report):
    for spec in active_modules():
        if spec.doctor is None: continue
        try:    await spec.doctor(report)
        except Exception as exc:                       # 一条失败不该吞掉其余 20 条
            report.add(f"module.{spec.name}", "FAIL", f"模块自检未完成：{...}")
```

素材模块的三条检查搬到 `modules/asset/health.py`，生图模块的一条搬到
`modules/image/health.py`（重建时补）。**没有只搬 image 那一条**——同一个病只治一个
入口，正是本项目历史上反复踩的坑（R5-03 / R6-04 / R8-01）。

反向验证：
```bash
$ app doctor            # 23 项、0 失败（image 已删，只剩 asset 那三条）
$ app doctor            # 重建后 24 项、0 失败 —— 与基线一致
```

### F2 `migrate-storage` 改成扫 schema，而不是扫一张表

剥旧地址的逻辑从「`ImageJob` 的 `output_url`/`cover_url`」改成「`Base.metadata` 里
所有以 `_url` 结尾的列」，前缀仍只认当前驱动的 `url_for("")` 与静态前缀
（供应商直链、`data:`、协议相对地址一律不动）。代价是多了几行遍历，收益是
**下一个犯同样毛病的模块不用回来改 CLI**。

### F3 `seed --reset` 改成 metadata 驱动

```python
for table in reversed(Base.metadata.sorted_tables):
    if table.name in FRAMEWORK_TABLES:   # app_meta / app_meta_history
        continue
    await session.execute(delete(table))
```

**这一步我自己踩了两次坑，都被现有用例抓住**（见 P8），细节留到那里说。

### F4 补两条守卫，把这一类钉死

| 新守卫 | 拦什么 |
|---|---|
| `test_api_layer_does_not_import_business_modules` | `src/api/**` 里出现任何 `import modules.*` 立即红（零容忍，手册第 0 节第 1 条第一次有了测试） |
| `test_top_level_scripts_only_import_known_modules` | `cli.py` / `seed.py` 的业务模块 import 只能来自一张**带退出条件**的清单；清单里过期的条目同样红（棘轮：不追求一次清零，但不许变坏） |

清单现在只剩 `cli.py`（identity / asset 的运维命令）和 `seed.py`（老模块演示数据
还没搬进 `ModuleSpec.seed`）——那是独立的一次改动，本轮不动，但**下次删模块时，
这张清单会直接告诉你哪里会崩**。

> **同日收尾（2026-11-08 下午）**：这条尾巴已经剪掉，清单现在是**空的**。
> `ModuleSpec.cli` 与 `ModuleSpec.seed` 两个钩子落地，identity / asset 的运维命令
> 搬进了 `modules/*/cli.py`，六个模块的演示数据搬进了 `modules/*/seed.py`，
> `cli.py` 从 1245 行降到 ~880 行、`seed.py` 从 243 行降到 ~80 行，两个文件都
> 一行业务 import 都没有。反向验证：移走 `modules/shot` 之后 `app doctor` 24 项全过、
> `app modules` exit=0、**835 条用例照常收集**。

---

## P3 按手册第 1 节重建骨架

```bash
$ app new-module image --label 生图 --perm image --slot 6 --group 内容生产 \
    --generation --capability image
已创建模块 image：
  + src/modules/image/{__init__,router,schema,service,dependencies,models,errors,pipeline}.py
```

**预期**：8 个文件（7 个基础 + `--generation` 的 pipeline），生成即能跑，能过 lint。
**实际**：文件齐了，但三处内容不对——**都不是「我要改业务」，是脚手架自己写错了**。

### G1 流水线的 `capability` 写死成 VIDEO

```python
kind = "image"
capability = Capability.VIDEO     # ← 生图模块，能力却是视频
```

`test_pipelines_implement_protocol` 照样绿（协议确实实现了），启动也不报错。它只在
**第一次真实提交**时把生图请求交给视频供应商——错在最贵的那一步。

修复：`ModulePlan` 加 `capability`，`--generation` 时**必填**、不给默认值（没有正确
的默认值），模板改成 `Capability.${capability}`。同时加两条用例：漏传 / 光传
`--capability` 不传 `--generation` 都要报错。

### G2 列表接口生成的是全量 `list`

```python
) -> ApiResponse[list[ImageOut]]:        # ← §1.1 明确说过这是错的写法
```

手册 §1.1 用两段话讲「全量返回在数据长起来之后是一进页面打满数据库」（R7-02），
脚手架却把这一版生成出来了。改成 `Page[ImageOut]` + `Page.create` + `sorted by (created_at desc, id desc)` 的注释提示。

### G3 `rate_limit(..., limit=30)` 写死数字

手册 §5.2.3 要求「写盘或提交供应商的入口用 `settings.rate_limit_default_per_min`」。
30 不是错，但它让「线上不改代码调额度」这句话对脚手架产物不成立。改成读配置。

### 生成物必须过 lint（顺带记一笔自己的失误）

改完模板第一次重新生成，`ruff` 报 `E501`：我把模板里的文档表格行改长了。修了两轮
才降到 100 字符以内。教训：**改模板 = 改所有未来的模块**，`tests/test_scaffold.py`
里那条「生成物能过 lint」的用例就是为此存在的，改完必须重新生成跑一遍。

---

## P4 把骨架改成真实业务（手册 §1 / §1.1）

| 脚手架给的 | 改成 | 依据 |
|---|---|---|
| `Image` / `images` / `title` | `ImageJob` / `image_jobs` / `name` + `prompt` + `size` + `count` | 领域 |
| 权限 4 个 CRUD | `read` / `create` / `read:all` / `retry:any` | §1.1 数据范围 + §5.6② 重试出口 |
| `/image` + `/image` POST | `/images`、`/generations/image`、`/images/{id}/retry` | 「生成」是动作不是资源子集 |
| `errors.py` + 示例码 `40961` | **删掉整个文件** | 生图只用框架级异常；留着等于让前端为一个永不出现的码写分支 |
| `dependencies.py` 的 repo 注入 | 只注入 session | 单表模块，repo 提到依赖层是纯噪音 |

权限点这一条是手册的空白：**生成类模块照抄 CRUD 会同时多两个没用的、少两个要用的**。
已写进 §1.0。

---

## P5 流水线（§4 + §5.4③ + §5.6①②）

七个 Store 方法 + `list_unfinished`。后者是**手册没写的方法**：

```python
# generation/recovery.py
pipeline = get_pipeline(kind)
if not isinstance(pipeline, RecoverablePipeline):
    raise RuntimeError(f"kind={kind!r} 声明了框架恢复但流水线没有 list_unfinished")
```

框架这边是**fail-loud**（好），但手册 §4 的方法清单只列了七个，照抄的人会在
「第一次重启带 worker 启动」时才撞上。已在 §4 补一段说明这第八个方法。

`persist_output` 的 SSRF 防护（host 白名单 / 每跳重定向重校验 / 流式大小上限 /
content-type + 魔数）按 §5.6③ 原样实现，`tests/test_image_persist.py` 269 行覆盖。

---

## P6 巡检、演示数据、模块自检

- `@periodic("image.reconcile", every=300, immediate=True)`：两类「卡住」分开处置
  （无任务号 → 重投；有任务号 + 快照 async → 续轮询；sync/无快照 → 交人工）。
  这是 §5.3 + §5.6④ 的合体，`ModuleSpec.periodic=("image.reconcile",)` 声明是义务。
- `ModuleSpec.seed`：演示数据是终态（`succeeded`），否则巡检会拿演示记录去调真实供应商。
- `ModuleSpec.doctor`：历史行里还有没有旧 `/static` 地址 —— **用上 P2 新加的插槽**，
  否则那个插槽只是理论。

---

## P7 契约比对：零 diff

```bash
$ app export-openapi
已写入     json  api/docs/openapi.json
已写入     md    api/docs/API.md
无变化     ts    studio/src/shared/api/schema.ts
无变化     perms studio/src/shared/api/permissions.ts

$ git diff --stat api/docs/
 api/docs/API.md       | 2 +-
 api/docs/openapi.json | 2 +-
```

**唯一的差异是我在重试接口 docstring 里加了一句「/ 手册 §5.6②」**——路径、方法、
字段、权限码、tag 顺序、schema 全部与删除前逐字节一致。

四份生成物里前端用的那两份（`schema.ts` / `permissions.ts`）**完全没动**，所以
「前端不动」不是妥协，是这次重建本来就该有的结果。

---

## P8 全量回归：我自己在 F3 上踩的两次坑

### 坑一：`--reset` 开始清 `app_meta`，权限快照被抹掉

```
FAILED tests/test_role_auto_sync.py::test_startup_writes_catalog_snapshot
  assert snapshot is not None
```

原因：`bootstrap` 在**启动时**写快照，`seed --reset` 在**之后**执行。手写清单没包含
`app_meta`，所以旧代码"意外地"保护了它；换成 metadata 驱动后，快照被删掉，而这一轮
没有任何代码会重写 → `sync_new_permissions` 退化成「没有快照 → 全量补齐」，管理员
手工去掉的旧权限会在下次启动被加回来。

修：`FRAMEWORK_TABLES = {"app_meta", "app_meta_history"}`，并在注释里写清时序原因。

### 坑二：只留主表、不留历史表

```
FAILED tests/test_cli.py::test_meta_command_shows_history_for_a_key
  assert '最近写入' in stdout          # 实际输出只有 "v1 最后写入 ..."
```

`app_meta_history` 被清了，于是出现「有一条 v1、却没有任何写入轨迹」的不一致状态。
两张表是一个单元，要么都留要么都清。修：历史表一起进 `FRAMEWORK_TABLES`。

两个坑都不是新 bug，是**我的改动让原本被隐式保护的行为暴露出来**。值得记的是：
两次都是现有用例抓到的，而且报错信息（`snapshot is not None` / `'最近写入' in stdout`）
离根因都只有一步——这就是把断言写在「行为」上而不是「实现」上的回报。

---

## P9 最终结果

| 判定标准 | 结果 |
|---|---|
| `app modules` | `image 3 image:create,image:read,image:read:all,image:retry:any` ✅ 与基线一致 |
| `test_image_*.py` | 54 passed ✅ |
| `app doctor` | 24 项、失败 0、警告 0 ✅ |
| 四份生成物 | `schema.ts` / `permissions.ts` 无变化；`openapi.json` / `API.md` 仅 1 行 docstring ✅ |
| 全量 pytest | 823 passed / 12 skipped ✅ |
| ruff / mypy | 全绿（213 文件） ✅ |

---

## P10 问题清单

**A 类：框架真缺东西（已修 + 有反向验证）**

| # | 症状 | 根因 | 修复 | 反向验证 |
|---|---|---|---|---|
| F1 | 删模块后 `app doctor` 崩在第 5 项，后 19 项失联 | 诊断层反向 import 业务模块跑 4 条业务检查 | `ModuleSpec.doctor` 插槽 + `_check_modules` 单条兜异常；4 条检查搬回各自模块 | 删掉 image 后 doctor 仍出 23 项；单条自检抛错只红一条（`test_doctor_module_hooks`） |
| F2 | 删模块后 `app migrate-storage` 崩 | CLI 认识 `ImageJob` 的两列 | 改成扫 schema 里所有 `*_url` 列 | 用例仍绿（同一函数的行为没变，覆盖面变大） |
| F3 | 删模块后**整套用例无法收集** | `conftest → seed → modules.image.models` | `--reset` 改 metadata 驱动 + 框架记账表例外 | 删 image 后 conftest 仍可导入；快照两条用例绿 |
| F4 | 手册的「api 不 import 业务模块」没人守 | 架构测试参数化漏了 `api/` 与顶层脚本 | 加零容忍守卫 + 顶层脚本棘轮清单 | 在 `api/` 里写一行 `import modules.x` 立即红 |

**B 类：脚手架 bug（已修）**

| # | 症状 | 修复 |
|---|---|---|
| G1 | `--generation` 生成的流水线 capability 写死 VIDEO | `--capability` 必填；漏传报错 |
| G2 | 列表接口生成全量 `list`（§1.1 明确判定为错） | 生成 `Page[...]` |
| G3 | `rate_limit` 写死 30 | 读 `settings.rate_limit_default_per_min` |

**C 类：手册空白（已补）**

| # | 空白 | 补在哪 |
|---|---|---|
| M1 | 生成类模块照抄 CRUD 权限会多两个没用的、少两个要用的 | §1.0 |
| M2 | `errors.py` / `dependencies.py` / 路由路径为什么可以改、什么时候该删 | §1.0 |
| M3 | `--generation` 必须带 `--capability`，且没有默认值 | §4 |
| M4 | §4 的方法清单少了第八个 `list_unfinished`（漏了启动期抛错） | §4 |
| M5 | 模块自检该写在哪（`ModuleSpec.doctor`），以及为什么不能写进 `api/diagnostics.py` | §5.4④ + §10 + §9 清单 |
| M6 | `seed --reset` 清哪些表、为什么框架记账表例外 | `src/seed.py` 注释 + 本文 P8 |

---

## P11 前端：规划，但这一轮不动代码

重建之后 `schema.ts` / `permissions.ts` 没有 diff，所以**前端不缺任何东西，本轮
一行不改**。留给下一轮的规划（按优先级）：

1. **生图历史分页**：后端 `GET /images` 已经是 `Page`（`total` 是真全量），前端如果
   还在「一次拉完再本地切片」，数据过千就会卡。这是唯一一个**后端已就绪、前端没跟上**
   的地方。
2. **`confirmCharged` 的确认弹窗**：重试接口对「可能已扣费」的记录默认 409。前端需要
   把 409 + 文案里的任务号渲染成一个明确的二次确认，而不是笼统的「重试失败」。
   契约里 `previousExternalIds` 就是给这个弹窗用的。
3. **`/static/` 老地址兜底可以删**：`image.urls` 自检报 0 条之后，前端针对旧地址的
   兼容分支就没有存在理由了。
4. **图片走 `<img>` 还是 `next/image`**：接口返回的是按当前驱动现签的地址、会过期。
   如果前端做了 URL 缓存或 service worker 缓存，需要知道这点。

---

## 一句话结论

**手册是准的，框架不是。** 按手册写出来的模块，行为和删除前逐字节一致（54 条用例
全绿、契约零 diff）；但「删掉一个模块」这件事本身，会把三处「框架认识业务模块」的
反向依赖、四条漏掉的守卫、三个脚手架 bug 一起翻出来。

扩展性的真正判据不是「加模块要多久」，而是 **「删模块要改几处框架」** ——这一轮之前
的答案是三处，现在是零。
