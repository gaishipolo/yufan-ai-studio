# 可扩展框架的问题清单（来自 PR #1 合并）

> 记录时间：2026-11-06
> 来源：同事提交的 `feat/replacement-templates`（替换模板 / 素材归档）合并进 `main` 的全过程
> （`bf0e3c7` → `59513ba`，8 个提交），包括合并时的四处修正、前端用户体系并回、以及上线前排查的共享盘故障。
> 范围：**只记录后端骨架/扩展框架暴露的问题**，不包括同事代码本身的实现问题（那些单独给过他）。
> 目的：这份清单是框架的待修项，不是对人的评审。

---

## 一句话结论

这次合并暴露的不是"某个文件写错了"，而是**四类系统性缺口**：

1. 扩展点只覆盖了「代码」这一维，**没有覆盖「配置 / 运行时资产 / 跨模块查询」**；
2. 声明式契约只保证「注册成功」，**不保证「端到端生效」**；
3. 框架级参数被写成了**全局参数**，缺的是「由 pipeline 自己声明节奏与预算」；
4. 几乎所有错误都靠人眼发现，**缺机械守卫**（默认值、忽略规则、配置对齐、权限对齐）。

下面 13 条按这四类排列，每条给「现象 → 证据 → 根因 → 影响」。

---

## 修复进展（2026-11-06 更新）

接一次真实的新功能（生图，Agnes）做验收，边做边修；随后把「扩展点」这一类（A 组）
整体补齐，再按七维对账把剩下四维补完。**13 条里修了 10 条，另有 3 条是新发现的；
只剩 B1 / C1 / C2 三条（都不是扩展点，是运维与参数层级问题）。**

| 条目 | 状态 | 怎么修的 |
|---|---|---|
| A1 provider 私有配置无归属地 | **已修** | 新增 `infra/driver_config.py`：每个驱动一个 `XxxSettings`，前缀 = 驱动名大写。`core/config.py` 里的 `media_archive_root` / `libtv_*` 全部迁出，**环境变量键名一个没改** |
| B3 `generation_timeout_sec` 是死配置 | **已修** | `runner._timed_out()` 按任务创建时间算墙钟；`Settings._guard_generation_budget` 在启动期校验 `MAX_POLLS × INTERVAL ≥ TIMEOUT × 0.8`，默认值改成 900×2=1800 |
| D1 默认值指向盘符 | **已修** | 默认值改空串，使用处报错并带上配置项名；新增守卫测试「默认值只能是仓库内路径」 |
| D3 `.env.example` 人工对齐 | **已修** | 双向测试：字段→示例（含注释形式）、示例→字段（防止拼错的键静默失效） |
| **新** 同步供应商不被支持 | **已修** | `ModelProvider.submit` 可返回 `GenerationProgress`；框架拿到终态直接落结果、不再排轮询 |
| **新** `ModuleSpec.jobs` 声明了但无人消费 | **已修** | 删掉字段（`discovery` 与 `app modules` 同步）。宁可删，也不留一个只用来打印的假承诺 |
| **新** 周期任务没有扩展点（七维之一） | **已修** | 新增 `core/scheduler.py`（`@periodic` + 进程内单调度器 + `SCHEDULER_ENABLED`）；生图用它补 `image.reconcile`：捞回进程重启后卡在 `queued` 的作业 |
| **新** 模型能力声明未参与校验，靠供应商兜底 | **已修** | `param_schema` 原来只给前端渲染表单、受理时不看。后果是「声明支持 4 张、供应商只接受 1 张」——201 受理、几秒后异步失败。现在 `ImageService._validate_params` 按模型声明的 enum / min-max 在**受理时**拒绝，报错带可选值；`agnes` 的 `count` 上限从 4 改为 1 |
| **新** 尺寸字面量格式不统一（全角 `×` vs 半角 `x`） | **已修** | `echo.py` 的 `_IMAGE_SCHEMA` 用全角 `×` 而 `agnes.py` 用半角 `x`，同一套前端不可能同时满足；且 `size` 只校验长度，非法值穿透到供应商。已统一半角并把白名单校验接上（前端 `ImagePage` 的 `×` 仍待改，见 [`BACKEND_DELIVERY_IMAGE.md`](BACKEND_DELIVERY_IMAGE.md)） |
| A2 归档存储不走 driver loader | **已修** | 新增 `infra/archive/`（`base` 协议 / `local` 驱动 / `__init__` 工厂），`ARCHIVE_DRIVER` 选实现；provider 与子进程里 5 处直接 `new` 全部改走 `get_archive_storage()`，并加测试禁止再直接实例化 |
| A3 跨模块协作无成文规则 | **已修** | 新增 `api/ports.py` 的 `PORT_CATALOG`（协议 / 谁实现 / 未注册时的行为），注入口形状写进 `EXTENDING.md §5.1`；三个架构测试守住「漏登记就红」「缺 has 就红」；`app modules` 与启动日志都会列出未接上的注入口 |
| A4 外部进程位置参数 | **已修** | 参数改为 stdin 的 JSON + `version`（`LIBTV_PROCESS_PROTOCOL`），两侧各自校验；版本不认就退出码 2，启动方按「协议不一致」报错而不是干等结果文件 |
| B1 权限点不自动进内置角色 | 未修 | 已有 `app sync-roles`，但仍需人记得跑 |
| B2 前端权限清单手抄 | **已修** | `app export-openapi` 增加第四份生成物 `studio/src/shared/api/permissions.ts`（来源是 `ModuleSpec.permissions`），`--check` 与契约测试拦住忘记生成。前端改法见 [`BACKEND_DELIVERY_IMAGE.md` 第四节](BACKEND_DELIVERY_IMAGE.md)：不是「一行 re-export」——生成物是 `as const` 的 readonly 元组，`new Set()` 要显式 `Set<string>` 否则类型不过；那条解析 Python 源码的 vitest 比对可以一起删（它在单独检出前端时静默跳过，等于没有） |
| B4 任务不校验 owner 状态 | **已修** | 注入口从 `api/accounts.py` 下移到 `core/ports/accounts.py`（`generation` 也能用，不再反向依赖），`runner.handle_submit` 在**提交前**检查，停用即失败且不留提交意图 |
| C1 轮询节奏全局化 | 未修 | 仍读 `settings.generation_poll_interval_sec` |
| C2 重试是作业级不是任务级 | 未修 | 仍是 `Job.max_attempts` |
| D2 资产目录被 `.gitignore` 误伤 | **已修** | 生图成图落 `storage`（`pipeline.persist_output`）；`test_gitignore_does_not_swallow_sources` 拿忽略规则回头匹所有源码路径，被吞就红 |

剩下三条（B1 权限点进内置角色、C1 轮询节奏下放、C2 任务级重试）都不是「找不到地方放」，
而是「放错了层」或「需要一个运维动作」——它们不影响新功能能不能加出来，影响的是加完之后
运维与重试语义是否顺手，所以留到下一批。

A 组修完后的形状变化，一句话概括：**扩展点从「代码插槽」补齐成了「代码 / 配置 / 运行时
资产 / 跨模块查询」四类都有归属地**，而且每一类都有一个不看人脸的守卫测试。

---

## 七个维度的对账（用「从零加一个生图功能」当验收）

> 验收方式不是「读代码觉得行」，而是**真的从一个空目录加出一个功能**，看它每走一步
> 需不需要改框架。第一轮只通过了 2 维，暴露出「框架只设计了代码插槽」；
> 补完 A 组 + 这一轮的四个维度后是 7/7，**每一维都有一条不看人脸的守卫测试**。

| 维度 | 生图这条路怎么用它 | 扩展点 | 守卫测试 |
|---|---|---|---|
| 配置 | `AGNES_API_KEY` / `AGNES_BASE_URL` / `AGNES_MODEL` 全在 `AgnesSettings` | `infra/driver_config.py` | 字段必被引用 / `.env.example` 双向一致 / 默认值不许指向机器路径 |
| 运行时资产 | 成图下载后落 `storage`，`output_url` 换成自己的地址 | `pipeline.persist_output`（可选方法） | `test_image_persist.py` + `.gitignore` 不许吞源码 |
| 跨模块查询 | 提交前问一句「发起人还在吗」，停了就不花钱 | `core/ports/accounts.py` + `PORT_CATALOG` | `test_disabled_owner_stops_submit_before_spending` |
| 周期任务 | `image.reconcile` 每 5 分钟把卡在 `queued` 的作业捞回来 | `core/scheduler.py` 的 `@periodic` | `test_scheduler.py` + 命名前缀静态扫描 |
| 种子数据 | `app seed` 调模块自己的 `seed()`，不再改 `src/seed.py` | `ModuleSpec.seed` | 生图演示数据是终态（不会被巡检重投） |
| 审计留痕 | 提交生图写一条 `image.generate` | `core.audit.record` + `audit_events` 表 | `test_audit.py`（含「写入失败不影响业务」） |
| 前端配对 | 权限点随契约下发，前端不再手抄镜像 | `app export-openapi` 的第四份生成物 | `test_permission_catalog_is_up_to_date` |

第一轮的原始结论值得留着：**三分之二的维度没通过，原因不是生图写得糙，而是框架只设计了
「代码插槽」**。生图第一版能一次跑通，恰恰因为它是最简单的形态——简单到几乎验证不到扩展点。
**下一个形态更复杂的功能（要读别人数据、要定时、要落盘）才是真正的验收。**

---

## A. 扩展点缺失：想扩展，找不到该往哪放

### A1. provider 的私有配置没有归属地

**现象**：`libtv` 需要 3 个部署参数（归档根、旧运行时根、node 路径），只能加进全局 `Settings`。

**证据**

```python
# api/src/core/config.py:93-102
# 刻意不使用 Literal：新增驱动/供应商不应该需要修改本文件。
storage_driver: str = "local"
...
media_archive_root: str = "Z:/yufan_ai_studio"
libtv_legacy_root: str = "E:/yufan-ai"
libtv_node: str = "node"
```

```python
# api/src/infra/ai/libtv.py:68-77   provider 把全局配置直接读进子进程参数
[sys.executable, "-m", "infra.ai.libtv_process", stage, str(path),
 settings.libtv_legacy_root, settings.libtv_node, str(resultfile)]
```

**根因**：`api/docs/EXTENDING.md:447` 与 `config.py:94` 两处都承诺「新增供应商不需要改动 `core/config.py`」。
这个承诺在 `echo`（零配置）身上成立，`libtv` 是第一个需要配置的 provider，一踩就破。

**影响**：承诺失真；provider 私有参数与全局参数混住，读者无法从文件结构看出「哪些是部署必须配的」。
**不严重，但它是 A2/A3 的同源问题：框架只设计了「代码插槽」，没设计「配置插槽」。**

---

### A2. 归档存储没有驱动注册机制（两套并存的获取方式）

**现象**：存储有两种拿法，一种可换、一种写死。

**证据**

```python
# api/src/infra/__init__.py:22-29   正规路径：load_driver 按配置选实现
def get_storage() -> Storage: ...   # settings.storage_driver
def get_queue() -> JobQueue: ...    # settings.queue_driver
```

```python
# api/src/infra/storage/archive.py:115-116   归档盘：写死
def get_archive_storage() -> ArchiveStorage:
    return LocalArchiveStorage()
```

```python
# 而且 provider 里绕过了工厂，直接 new（共 5 处）
api/src/infra/ai/libtv.py:45,51,55
api/src/infra/ai/libtv_process.py:58,109
```

**根因**：`infra/` 的驱动约定是「`infra/<kind>/<name>.py` 里有个 Driver，`settings.<kind>_driver` 选它」。
归档存储没有走这套，也没有对应的 `<kind>`，于是工厂函数退化成一个恒返回同类型的函数。

**影响**：
- `.env.example` 里「换实现只改这里」这句注释，对归档盘不成立；
- 归档盘要上 S3 / MinIO，得改 `libtv.py`、`libtv_process.py` 的 5 处 `new`；
- 单测里无法注入假实现，只能依赖真实临时目录（现有测试都是 `tmp_path` + 真文件）。

**修法（2026-11-06）**：把 `infra/storage/archive.py` 拆成 `infra/archive/{base,local,__init__}.py`，
kind 从「storage 包里的一个文件」升为独立的 `archive`：`ARCHIVE_DRIVER` → `infra/archive/<name>.py`
的 `Driver`。协议留在 `base.py`，`libtv.py` / `libtv_process.py` 里 5 处直接 `new` 全改走工厂。
`test_archive_storage_only_reached_through_factory` 用 AST 拦住「又在某处直接 new」。
顺带承认一个边界：协议用 `Path` 表达位置，所以它支持的是「有本地路径语义」的存储，
真上纯 HTTP 的 S3 要先改协议 —— 写在 `ArchiveStorage` 的 docstring 里，不靠口口相传。

---

### A3. 跨模块协作没有正规通道，只能每次临时开注入口

**现象**：`modules/template` 需要问「这条任务的发起人还有效吗」，而消息在 `modules/identity` 手里。

**证据**

```python
# api/tests/test_architecture.py:57
def test_modules_do_not_import_each_other() -> None:   # 业务模块互相 import 直接红
```

```python
# 目前全仓只有两个「注入口」，都是遇到问题才开的
api/src/api/authn.py      # 上一次：拿当前用户
api/src/api/accounts.py   # 这一次：查账号是否停用（本次新增 83 行）
```

**根因**：架构测试只规定了「不许互相 import」，没有规定「那就该这么办」。
`api/*.py` 是事实上的解耦通道，但没有成文规则：什么情况该开、命名怎么定、放多少逻辑、谁来实现。

**影响**：每遇到一次跨模块查询就新开一个文件，风格随人而异。
`accounts.py` 里的三态语义（启用 / 停用 / 查不到视为放行）是这次现场定的，没有写进任何规范。

**修法（2026-11-06）**：规范写进 `EXTENDING.md §5.1`（六件套形状 + 四条硬规则），
并把「谁实现它」变成数据：`api/ports.py` 的 `PORT_CATALOG` 登记协议名、实现方约定、
未注册时的行为。`app modules` 与启动日志都会列出「未接入」的注入口 —— 以前这件事只有
读代码或踩坑才能发现。三个架构测试把「漏登记」「缺 has_*」「重名」变成红灯。

---

### A4. 外部进程的调用契约是位置参数

**现象**：provider 通过 `subprocess` 起一个 Python 监督进程，参数靠位置传递。

**证据**

```python
# api/src/infra/ai/libtv.py:68-77
[sys.executable, "-m", "infra.ai.libtv_process",
 stage, str(path), settings.libtv_legacy_root, settings.libtv_node, str(resultfile)]
```

对应的接收侧靠 `sys.argv[1..6]` 顺序解析。

**根因**：这是最快能跑通的写法，但没有版本号、没有能力协商、没有可选参数。

**影响**：provider 每加一个参数 = 两侧同步改 + 老进程可能与新代码混跑（`compile.mjs`/`libtv_process` 是独立文件，热更新时不会自动同步）。
**建议**：参数改成一个 JSON 文件路径或 stdin 传 dict，一次到位。

**修法（2026-11-06）**：走 stdin 的 JSON，带 `version`（`libtv_state.LIBTV_PROCESS_PROTOCOL`，
两侧共用同一个常量）。接收侧用 pydantic 校验、可选参数给默认值；版本不认 → 退出码 2，
启动方发现结果文件不存在时直接报「协议不一致 + 退出码」，而不是抛一个看不出原因的
`FileNotFoundError`。`tests/test_libtv_protocol.py` 覆盖正常入参、默认值、错版本、垃圾输入。

---

## B. 声明与生效脱节：写了，但没真的生效

### B1. 模块声明了权限点，内置角色不会自动获得

**现象**：`template:use` 写进 `ModuleSpec.permissions` 后，库里的 `editor` 角色并没有它。
非超管上线即 403；而超管的权限是 `*`，全程自测发现不了。

**证据**

```python
# api/src/modules/identity/service.py:1037
async def sync_builtin_role_permissions(self) -> dict[str, list[str]]:
    """把 BUILTIN_ROLES 里**新增**的权限点补进已存在的内置角色……
    上一个模块就撞上了：template:use 写进了声明，库里的 editor 仍然没有，
    非超管上线即 403，而超管拿 * 全程自测发现不了。
    """
```

（这是本次补的 `app sync-roles` 命令，属于**事后补救**。）

**根因**：`ensure_builtin_roles` 刻意「不覆盖已有权限」（管理员手工调过的不该被重启改回去），
于是「新库初始化」和「老库加权限点」分成两条路径，**后者没有自动通路**。

**影响**：每加一个权限点就多一步人工操作，忘一步就是线上 403。
现在的解法是一个需要**记得跑**的命令，仍然依赖人。

---

### B2. 前端权限清单是手抄的，两边没有一致性检查

**现象**：角色编辑页授不了 `template:use`，因为前端目录里没有这一条。

**证据**

```ts
// studio/src/entities/role/model/permission-catalog.ts   人工维护的权限点列表
```

后端唯一事实在 `ModuleSpec.permissions`，前端这份是抄的，且没有测试比对。

**根因**：权限点没有走接口契约（`openapi.json` / `schema.ts`）下发，而是各自维护。
`ModuleSpec` 声明的权限点**没有出现在任何对外接口里**。

**影响**：每次加权限点都要改两处；漏了前端那处的表现是「后端允许、界面不给勾」，很难定位。
**这条与 B1 是同一个根因的两面：权限点的唯一事实没有被单点消费。**

---

### B3. `generation_timeout_sec` 是死配置，真实超时算错 6 倍

**现象**：配置项存在、有默认值、有注释，**但没有任何代码读它**。

**证据**

```
$ grep -rn "generation_timeout_sec" api/src
core/config.py:116:    generation_timeout_sec: int = 1800
generation/runner.py:112:   ← 只出现在 docstring 里
```

真实超时 = `max_polls × poll_interval` = **150 × 2.0 = 300 秒**，而设计意图是 1800 秒。

**影响**：默认配置下，一个 5 分钟的视频会在第 5 分钟被判「生成超时，请重试」。
同事把 `.env.example` 改成 `POLL_INTERVAL=20.0 / MAX_POLLS=90`（恰好 1800）——**他不是想改全局，是在用手工拼出来的语义替框架补这个洞。**

这条是本次清单里唯一的**真 bug**，不只是设计问题。

---

### B4. 生成任务不校验发起人是否还有效

**现象**：账号被停用后，他名下的生成任务继续跑、继续调用付费供应商。

**证据**

```python
# api/src/generation/runner.py   全流程只透传 owner_id，从不判断账号状态
owner_id=record.owner_id      # 出现 10 次，全部是透传给事件/日志
```

本次在 `modules/template/replacement_worker.py` 的 `prepare_upload` 里补了闸门
（`api/accounts.py` + `account_is_active()` → `service_error(OWNER_DISABLED)`），
但 `modules/task` 的生成**没有这道闸**。

**根因**：`generation` 层不知道"用户体系"的存在（分层约束不允许它 import `modules.identity`），
而框架也没有定义「提交前可以插一个 owner 有效性检查」这个钩子。

**影响**：这是同一个业务规则需要每个生成模块各写一遍，且写不写全凭自觉。
从"停用即切断"的产品语义（`identity` 的 `_cut_access` 会吊销全部会话）来看，**任务侧漏了半条**。

---

## C. 语义错位：参数放错了层

### C1. 轮询节奏是供应商特性，却做成了全局配置

**现象**：为了 libtv 调慢轮询，所有业务模块的进度条一起变慢 10 倍。

**证据**

```python
# api/src/generation/runner.py   不分 kind 地读同一组全局值
229:  delay=settings.generation_poll_interval_sec
251:  if polls >= settings.generation_max_polls:
301:  if result.retryable and polls + 1 < settings.generation_max_polls:
327:  next_delay = settings.generation_poll_interval_sec
425:  settings.generation_poll_interval_sec * (2 ** min(polls, 4))
```

libtv 每一跳的代价是真实的：起一个 node 子进程、复用浏览器会话（45 秒超时）、两把 OS 锁。
2 秒一跳撑不住；但 `modules/task` 的视频生成没有这些代价，被一起拖慢了。

**根因**：节奏属于「供应商/流水线」的属性，框架却把它放在了「部署」这一层。

**建议**：`GenerationPipeline` 上加 `poll_interval_sec: float | None = None`（`None` 回落全局），
约 10 行 + 1 个测试。**这属于骨架变更，需要评审。**

---

### C2. 重试预算是「作业级」，业务要的是「任务级」

**现象**：每个带外部进程的模块都得自己再写一套重试。

**证据**

```python
# api/src/infra/queue/base.py:31-46
max_attempts: int = 3
def retry_delay(self, *, base: float = 2.0, cap: float = 60.0) -> float: ...
```

这是**作业级**的：进程重启即丢、退避封顶 60 秒、最多 3 次。
而 `replacement` 需要的是**跨重启**的预算（`run.json` 落 `retry_count`）：30 秒起、翻倍、封顶 10 分钟、上限 5 次 → `RETRY_EXHAUSTED`。

**根因**：两种语义都真实存在，但框架只提供了前一种，且没有说清「后一种该放哪」。

**影响**：`replacement_worker.py` 里多出约 40 行重试逻辑；下个模块会再抄一遍。

---

## D. 没有守卫：错了没人拦

### D1. 配置默认值是某台机器的路径，且没有测试拦

**现象**：换一台没映射 `Z:` 的机器，归档接口直接失败。

**证据**

```python
# api/src/core/config.py:100-101
media_archive_root: str = "Z:/yufan_ai_studio"
libtv_legacy_root: str = "E:/yufan-ai"
```

```python
# api/src/infra/storage/archive.py:71   设计上明确不回退
if not path.is_absolute() or not path.is_dir():
    raise UpstreamError("素材归档盘不可用，请检查 Z 盘连接；不会回退本地保存")
```

```python
# api/tests/test_config_guard.py:59   只检查了绝对路径，没检查「不许指向具体盘符」
def test_default_runtime_paths_do_not_depend_on_cwd() -> None: ...
```

**根因**：`test_config_guard.py` 的思路是对的（默认值必须是绝对路径、不能用 `./var`），但只覆盖了 `db_url` 和 `storage_local_root`，漏了这两个。

**影响**：这是本次真实发生的故障（新机器、无 Z 盘映射）。
"不回退、直接报错"的设计本身是对的，**问题在默认值不应该指向某台机器的盘符**。

---

### D2. 新模块的运行时资产目录会被 `.gitignore` 误伤

**现象**：PR 第一次提交缺 `replacement_runtime/workbench/compile.mjs`，15 条测试红。

**证据**

```gitignore
# .gitignore:50-51（这是修完之后的注释）
# 不加前导 / 会连 api/src/modules/template/replacement_runtime/workbench/ 一起吞掉。
/workbench/
```

**根因**：脚手架不生成「带运行时资产」的目录，所以也没有对应的忽略规则；一条为根目录草稿写的 `workbench/` 把模块内的同名目录一起吞了。

**影响**：这类错误的唯一发现方式是 CI 红。**"文件少了一个"不应该靠测试来发现。**

---

### D3. `.env.example` 与 `Settings` 靠人工对齐

**现象**：这次 31 个字段是对齐的（我核过：30 个有实际读取，唯一没人读的是 B3 的 `generation_timeout_sec`），**但没有任何测试保证**。

**根因**：配置项的唯一事实是 `Settings`，`.env.example` 是人工镜像。

**影响**：加字段忘写示例 → 部署时不知道该配；示例里写了不存在的字段 → 配了没效果，且不报错（`extra="ignore"`）。

---

## 四类缺口的共同形状

| 类别 | 共同形状 | 修复方向 |
|---|---|---|
| A 扩展点缺失 | 只为「代码」设计了插槽 | 补「配置」「资产」「跨模块查询」三种插槽，且都要有注册表 |
| B 声明与生效脱节 | 注册成功 ≠ 端到端生效 | 声明式的东西必须有一个「消费点」和一条「对齐测试」 |
| C 语义错位 | 把「流水线的属性」当成「部署的属性」 | 把节奏/预算下放到 `GenerationPipeline` |
| D 无守卫 | 靠人眼、靠 CI 红 | 默认值正则、忽略规则白名单、`.env.example` 双向比对 |

---

## 优先级

| 级别 | 条目 | 理由 |
|---|---|---|
| **P0 立刻** | B3 超时死配置 | 唯一的功能性 bug，默认配置下 5 分钟就误判超时 |
| **P0 立刻** | D1 默认值守卫 | 今晚真实故障；加一条正则测试 + 默认值改空串即可，改动最小 |
| **P1 本次迭代** | B1 + B2 权限点单点化 | 已经踩过一次 403；建议权限点随接口下发，前端不再手抄 |
| **P1 本次迭代** | C1 轮询节奏下放 | 骨架小改动，能解开"为了一个 provider 拖慢所有模块" |
| **P1 本次迭代** | A2 归档驱动走 loader | 消除"两套存储获取方式"，为换 S3/MinIO 留路 |
| **P2 排期** | B4 owner 校验上移为框架钩子 | 语义上应该框架兜底，不该每个模块自己写 |
| **P2 排期** | D3 配置对齐测试 / D2 资产目录检查 | 都是机械守卫，成本低但可以等 |
| **P3 观察** | A3 注入口规范、A4 进程参数结构化、C2 任务级重试预算 | 出现第三个实例时再抽象，现在抽象容易抽错 |

---

## 做得对的地方（不是客套，是下次照样抄）

这几条经过这次合并验证是有效的，改动它们要谨慎：

1. **`app new-module` 脚手架是真的能用**：一次生成 7 个文件，含 `errors.py`（错误码号段）、
   `dependencies.py`（权限依赖不下沉到 `api/deps.py`）、`pipeline.py`（Store 七方法齐全）。
   `tests/test_scaffold.py` 甚至校验「生成的 Store 实现了完整协议」——这次没有因为脚手架漏东西而返工。
2. **架构测试抓住了一次真实越界**：`modules/template` 想 import `modules.identity` 时被 `test_modules_do_not_import_each_other` 拦下，
   才促成了 `api/accounts.py` 这个（正确的）方向。
3. **作者本人没有碰 `generation/`**：`bf0e3c7..1fdaf3f` 的骨架层改动只有 `core/config.py`(+4)、`core/media.py`(+32)，
   编排层 0 行、`infra/ai/` 是设计内的扩展点。**说明扩展指南在"新增 provider"这条路上是走得通的。**
4. **错误码号段冲突在导入期就炸**：`ModuleErrors` 的 `(状态码, seq)` 重复会直接报错，
   不需要等到线上出现两个 409 才知道撞了。

---

## 附：本次讨论后**暂缓**的提案 —— 配置分层

**提案**（2026-11-06 讨论）：`core` 只保留全局配置，新增功能时由脚手架生成一个**模块级 config 文件**（如 `modules/voice/config.py`）。

**结论：暂缓，不做。** 现在的规模不需要，且会引入新问题。记录在此，避免以后重复讨论。

**理由**

1. **本次故障不是"配置集中"造成的，是"默认值指向具体盘符"**。搬到模块里默认值还是 `E:/yufan-ai`，照样炸。
2. **现有配置卫生状况良好**：31 字段 / 30 个有实际读取 / 192 行 / 8 个注释分组；唯一没人读的是 B3 那条 bug。
   同事的 PR 改 4 行，**零冲突**。"集中"目前没有产生实际成本。
3. **真正的代价是语义会糊**：给了模块一个 `config.py`，业务规则容易被写成环境变量。
   现成例子就是 `resolution="720P"` / `provider="LibTV · Wan 3.0 · 720P"`（同一事实已写三遍）——
   有了模块 config，它很可能变成第四处 `TEMPLATE_RESOLUTION=720P`，而且看起来更"正规"、更容易过评审。
   判定标准只有一条：**运维改 → 环境变量；产品改 → 数据库/配置下发；开发改 → 代码常量。**

**真正需要独立配置的是"驱动"，不是"业务模块"**（对应 A1）。这条已按原计划落地，
且 `.env` 键名一个没改：

```
env_prefix="LIBTV_"          + field legacy_root → LIBTV_LEGACY_ROOT   ✓ 键名不变
env_prefix="MEDIA_ARCHIVE_"  + field root        → MEDIA_ARCHIVE_ROOT  ✓ 键名不变
env_prefix="AGNES_"          + field api_key     → AGNES_API_KEY       ✓ 新驱动
```

实现见 `api/src/infra/driver_config.py`；**模块级 config 仍然不做**（暂缓）。

**触发重议的条件**（任一满足再动手）

- `config.py` 超过 300 行或 60 个字段
- 同一迭代内有 2 个以上 PR 改 `config.py` 且产生冲突
- 出现「删除模块后留下孤儿配置」两次以上
- 出现第一个**确实需要部署级参数**的业务模块（目前 9 个模块，0 个有）

**如果将来要做，采用 `ModuleSpec.config` 而不是约定文件名**：

```python
module = ModuleSpec(name="voice", router=router, config=VoiceSettings)
```

注册点唯一（`ModuleSpec` 已是模块对外唯一契约），`api/discovery.py` 天然负责实例化，
`app modules` 能打印，D3 的对齐测试能自动枚举。**且脚手架默认不生成**，避免批量制造 `TEMPLATE_RESOLUTION`。

**替代动作（2026-11-06 已全部完成）**

1. ✅ 三条守护测试：字段必须被引用 / 字段与 `.env.example` 双向一致 / 默认值不得含盘符
   —— `api/tests/test_config_guard.py`
2. ✅ `MEDIA_ARCHIVE_ROOT`、`LIBTV_LEGACY_ROOT` 默认改空串；改用 `DriverSettings` 承载，
   未配置时在使用处报错并点名配置项（`archive.py` / `libtv.py`）
3. ✅ `generation_timeout_sec` 在 `runner._timed_out()` 真正生效，并加启动期一致性校验
4. ✅ `EXTENDING.md` 的「新增供应商」一节改成准确表述，并补了「同步供应商怎么写」与
   「驱动配置写在驱动自己的文件里」

---

## R11 复核后的补充修复（2026-11-06）

`docs/CODE_REVIEW_2026-11-06_R11.md` 用真实探针指出：这份 13 条对账是
**「按维度数插槽是否造出来」**，没有做**「按模块 × 插槽」**的矩阵，
所以两条 🔴 一条都不在原清单里——新加的 `image` 接齐了「让它跑起来」的插槽，
「出事后能恢复」的一个没接，而 292 个用例、ruff、mypy、契约检查全部照常通过。

本轮按报告第九节顺序全部修完，并把矩阵升级成机械守卫：

| 编号 | 修复 | 落点 |
|---|---|---|
| R11-01 | 恢复上移到框架：`ModuleSpec.recovery_kinds` 声明 kind，`generation/recovery.py` 统一分类处置；同步供应商 `supports_poll=False` 不无脑 `resume()` | `api/module.py`、`generation/recovery.py`、`infra/ai/agnes.py`、`app.py` |
| R11-02 | `POST /images/{id}/retry` + `image:retry:any`；`_settle` 与 Store 双终态保护；巡检阈值由供应商超时推导、按 `started_at` 判陈旧、日志按分支分开 | `modules/image/*`、`generation/runner.py` |
| R11-03 | 落盘下载 host 白名单 / 每跳重定向重新校验 / 流式字节上限 / content-type 白名单 + 文件魔数 | `modules/image/pipeline.py`、`tests/test_image_persist.py` |
| R11-04 | cutoff 与 `LIMIT` 下推到 SQL，`order_by` 以主键收尾 | `modules/image/maintenance.py` |
| R11-05 | `has_audit_sink` 纳入 `PORT_CATALOG` 与 prod 启动自检；架构守卫扫描范围扩到 `src/core/` 并放宽形状识别 | `api/ports.py`、`app.py`、`tests/test_architecture.py` |
| 第八节 | 两条矩阵守卫：每个 pipeline kind 必须有人声明恢复插槽、归属模块必须有重试出口 | `tests/test_architecture.py` |

规律与前几轮相同：**不变量只在「新功能能跑起来」的路径上接一次，
「出事才用得上」的路径靠人记得接**。区别是这次不再写进文档了事，
而是变成 `ModuleSpec` 上的必填声明 + CI 里会红的守卫。

剩余待办不变：B1（权限点进内置角色）、C1（轮询节奏下放）、C2（任务级重试），
这三条都不是扩展点问题，是运维与参数层级问题。

---

## R12 复核后的补充修复（2026-11-06）

`docs/CODE_REVIEW_2026-11-06_R12.md` 复核了 R11 的修复，并指出一个新形状：
**两个各自正确、各自有测试的修复，组合到同一条记录上破了一条硬不变量**
——恢复器把「同步供应商的 running 孤儿」置成 failed「请重试」，retry 清掉
`external_id` 重新 dispatch，于是「绝不重复计费」被绕过，且对账凭证同时丢失。
组件级用例天然覆盖不到跨组件的状态交接。

本轮按报告第九节顺序全部修完：

| 编号 | 修复 | 落点 |
|---|---|---|
| R12-01 🔴 | retry 区分 `submit_unknown` / `charged_but_lost`：带 `external_id` 默认 409，需 `confirmCharged=true`；旧任务号落 `previous_external_id` + 审计 detail；恢复文案改成费用提示 | `modules/image/service.py` / `router.py` / `schema.py`、`generation/recovery.py`、migration |
| R12-02 | `_guard` 在 `refresh()` 前先 `flush()`，保住同会话未提交的 `clear_submit_attempt` | `modules/image/pipeline.py` |
| R12-03 | 驱动自描述 `submit_budget_for`：阈值只取声明 IMAGE 提交预算的驱动；去掉静默 `continue`；无声明时巡检跳过并告警 | `infra/driver_config.py`、`modules/image/maintenance.py` |
| R12-04 | prod 启动期断言 `IMAGE_ASSET_ALLOWED_HOSTS` 非空（`prod_required_fields`），修正与实现不符的注释 | `app.py`、`modules/image/pipeline.py`、`.env.example` |
| R12-05 | `supports_poll` 缺省改 False、异步供应商显式声明；`submit_mode` 快照落记录，恢复分类优先读快照、严格解析、不走单供应商兜底 | `infra/ai/*`、`generation/spec.py` / `recovery.py`、`modules/image/pipeline.py`、migration |
| R12-06~09 | 巡检纳入超期 `running`（排除已受理）；双声明告警；cutoff 按 dialect 分支；worker 关闭时跳过恢复 | `maintenance.py`、`recovery.py`、`app.py` |
| 第八节 | 新增跨组件状态机契约表与驱动策略守卫 | `test_generation_state_machine.py`、`test_image_policy.py` |

规律升级了一层：前几轮是「不变量只守一个入口」，R11 是「插槽造了没接」，
R12 是**「修复之间没人负责交汇路径」**，以及**「加一道守卫会改变写入顺序、
从配置推导会引入聚合口径」**。对应手段也不再只是补插槽：把能力/预算/必填项
变成驱动自己的声明，再用一张表把组件串起来跑。

B1 / C1 / C2 仍待办；R11-06~10 与 R12 报告里的建议性观察（如任务级重试、
DNS 解析后校验 IP）未纳入本轮。

