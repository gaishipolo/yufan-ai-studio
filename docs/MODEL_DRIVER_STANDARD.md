# AI 模型驱动开发标准

> 一句话：**在一个 Python 文件里定义「怎么调用 / 怎么查结果 / 怎么停止」，模块自动载入，其他模块通过统一生成接口调用。**
>
> 适用版本：2026-11-10 起。规范定义与校验实现在
> `api/src/modules/model_driver/spec.py` / `registry.py`。

## 0. 这套东西解决什么

以前接一个模型供应商，要同时改 `.env` 的 `AI_PROVIDERS`、写 `infra/ai/<name>.py`、
在模型目录里登记、给每个调用方各写一遍提交与轮询。现在拆成两层
（**2026-11-13 起老路径已整体下线，仓库里只剩下面这一条接入方式**）：

- **上游（驱动开发者）**：只写一个 Python 文件，放进 `drivers/`，不用改配置、不用注册。
- **下游（业务模块）**：只认 `core/ports/model_generation` 一个注入口，
  调 `submit / query / cancel / models`，任务表、队列、轮询、重试、超时、启动恢复
  全部复用 `generation.runner`，不需要自己建表或实现 `GenerationStore`。

```
drivers/<name>.py  ──自动扫描──▶ ModelRouter ──▶ generation.runner ──▶ driver_jobs
                                      ▲
                                      └── 其他模块经 core/ports/model_generation 提交
```

## 1. 文件放哪、叫什么

| 项 | 约定 |
|---|---|
| 位置 | `api/src/modules/model_driver/drivers/<name>.py` |
| 文件名 | ASCII 标识符，不能以下划线开头（`_` 开头的文件会被跳过，`_template.py` 就是这样放在目录里而不被载入的） |
| 起步模板 | `cp drivers/_template.py drivers/<name>.py`，只改 `META` / `MODELS` / `name` / 三个方法体 |
| 必须导出 | `Driver = <实现类>`（类、工厂函数、已实例化对象都行） |
| 可选导出 | `META = DriverMeta(label=..., description=..., version=..., author=...)` |
| 生效方式 | 重启进程即自动载入；**不做热重载**，避免已提交任务的 `external_id` 指向换掉的驱动 |

## 2. 必须实现的成员

| 成员 | 类型 | 含义 |
|---|---|---|
| `name` | `str` | 驱动唯一标识，ASCII 标识符；必须与每个 `ModelSpec.provider` 一致 |
| `capabilities` | `frozenset[Capability]` | 至少一种：`Capability.IMAGE` / `VIDEO` / `AUDIO` |
| `models` | `tuple[ModelSpec, ...]` | 至少一个；`ModelSpec` 的 `provider` 必须等于 `name` |
| `supports_poll` | `bool` | 异步 `True` / 同步 `False`；**不写按 `False` 处理**，恢复器会保守置失败 |
| `submit(request)` | `async` | 怎么调用：返回供应商任务号 `str`，或同步终态 `GenerationProgress` |
| `poll(external_id, *, model="")` | `async` | 怎么查询结果：返回 `GenerationProgress` |
| `cancel(external_id)` | `async` | 怎么停止；不支持时空实现即可 |

类型从 `infra.ai.base` 导入，也可从 `modules.model_driver.spec` 一次导入
（那里做了再导出）。`GenerationProgress` 会在构造时把 `state` 归一成枚举、
把 `progress` 夹到 0–100，所以别在驱动外自己拼字典。

**可选的声明式字段**：

```python
preferred_for = frozenset({Capability.IMAGE})   # 「我的模型是该能力的默认」
submit_timeout_sec = 180.0                     # 单次提交最多等多久（秒）
```

单条模型上的 `ModelSpec(internal=True)` 另算，见下面「只给自己的业务链路用的模型」。

`preferred_for` 不写就沿用「注册顺序第一个」，而 `drivers/` 按文件名字典序
扫描——增删一个驱动文件就会换掉默认，所以想当默认必须显式声明。历史上 agnes
从配置白名单搬进 `drivers/` 时，默认生图模型差点悄悄变成 echo 的假模型
`seedream-3`。配置白名单已下线，但「顺序不可靠」这条纪律不变。

`submit_timeout_sec` 是**提交动作**的预算，不是整条生成的预算（后者由
`generation_timeout_sec` 管）。框架默认 120 秒，会再加上 30 秒余量作为提交
job 的硬超时。同步出图的驱动（提交即等结果）必须声明它，而且要≥上游最长
耗时：框架先到点就会取消 handler，任务被重投时「有提交意图、没有任务号」，
直接判成「提交结果未知」——钱可能花了、图丢了、还不许重试。异步驱动提交
很快，一般不用写；但如果提交里可能夹着一段素材上传（见下条），也必须声明。

**第三个可选能力：`InputPreparer`**（`infra/ai/base.py`）。多数视频模型只接受
**公网 URL** 作参考素材，不认本地文件——这类驱动要在提交前把素材直传供应商
自己的对象存储，再把直链拼进请求体。这一步既不是 `submit` 也不是 `poll`，所以
单列成可选协议：

```python
def unavailable_reason(self) -> str: ...                  # 空串=可用；否则给用户看的原因
async def prepare_inputs(self, run_id: str, model: str = "") -> dict: ...   # 上传并返回请求体
```

调用方（替换链）**通过路由拿到实例后 `isinstance` 判断**，有就调、没有就跳过，
它不会 import 你的驱动文件。`unavailable_reason` 让「没配密钥」在**选择渠道时**
就说清楚，而不是等用户点了开始才在上传阶段炸。范例见 `drivers/bailian.py`。

## 2.5 只给自己的业务链路用的模型：`internal=True`

写 `ModelSpec(..., internal=True)` 的模型不会出现在**给人挑**的地方：`GET /models`、
统一门面的模型清单、以及「未知模型，可用模型: …」这句报错里的候选列表。但
`resolve()` / `provider_for()` 仍然按 id 解析——某条业务链自己拿 id 直接提交时，
在解析层过滤等于把它打断。

两条配套规则：

- 新增「只有自己的入口才该用」的模型时**必须**带 `internal=True`；
- 过滤只发生在**目录**层，不要在 `router.models()` 里过滤。跨模块取模型清单请用
  `public_models()`，装配巡检、维护任务、按 id 找实现仍用 `models()`。

一个边角：若某个能力的默认模型恰好是 `internal`，目录接口**不会**把默认标记挪给
别的模型（前端会「以为默认是 A 所以不传 model」，后端却取到 B，直接 400）。宁可
一个默认都没有，让前端显式带上 id。

### 2.5.1 替换模型已经不靠 `internal` 藏了（2026-11-13）

`bailian-wan3-replacement` / `bailian-wan3-prime-replacement` /
`libtv-wan3-replacement` 原先标 `internal=True`，因为它们的 `submit` 只认替换链
写好的归档工作区（`params.archiveRunId` 必须等于任务 id），通用生成页选中必然 400。
现在三个模型**公开**，`GET /models` 能看到，生成工作台（`/tasks`）也能提交：

- `modules/model_driver/generic_inputs.py::materialize(request)` 把
  `prompt + params.referenceKeys + params.durationSec/aspectRatio` 物化成替换链同款
  工作区（`run.json` / `input-map.json` / `prompt-spec.json` / `media-inputs.json` /
  `prompt-compilation.json`）；驱动只在**没有** `archiveRunId` 时调它，模板库那条链
  一行没改。
- `modules/task/pipeline.py::VideoTaskStore.load()` 把受理期校验过的
  `referenceLibraryIds` 换成 `{id, key, kind, name}` 冻结进 `params`；查不到的资产
  **整条拒绝**，不许悄悄少一张图。
- LibTV 的 `submit` 还要先跑一次 `upload` 阶段（写 `inputs/request.json`）才轮到
  真正花钱的 `submit`；模板库那条链把 `upload` 排成独立的 prepare job，通用入口
  没有，所以在本跳里补上，并声明 `submit_timeout_sec = 1650`。

因此公开替换模型的守卫换了口径（`tests/test_internal_models.py`）：不再是「必须
标 internal」，而是「不能标 internal/placeholder、必须声明 `paramSchema` 与
`maxReferences`、且不能抢默认模型」。`internal` 这个开关本身保留，给将来真正
只服务某条业务链的模型用。

## 3. 两种供应商形态

**异步（主流视频厂商）**：`submit` 返回任务号，框架按 `generation_poll_interval_sec`
回来调 `poll`，直到终态或墙钟超时。

```python
async def submit(self, request: GenerationRequest) -> str:
    return await vendor.create_task(...)

async def poll(self, external_id: str, *, model: str = "") -> GenerationProgress:
    ...
```

**同步（多数图片厂商）**：`submit` 自己等出结果，直接返回终态
`GenerationProgress`，`poll` 不会被调用。

```python
async def submit(self, request: GenerationRequest) -> GenerationProgress:
    return GenerationProgress(
        external_id="...", state=GenerationState.SUCCEEDED,
        progress=100, output_url="https://...",
    )
```

同步形态会占住一个 worker 槽位直到出结果，这是厂商事实约束，不是框架缺陷。
参考实现见 `drivers/_mock_async.py`（异步）与 `drivers/_mock_sync.py`（同步），
两个都能离线跑通；真实厂商的行文见 `drivers/agnes.py`（同步 + 图生图/多图合成）。

> **文件名以下划线开头的驱动不会被载入。** `_iter_driver_modules()` 跳过它们，
> 所以 `_template.py` 这类骨架、以及两个示例驱动都不会进模型目录、不进
> `app drivers list`、也不会出现在用户可见的模型下拉里。
> 需要离线联调时显式调用
> `modules.model_driver.registry.register_example_drivers()`。
> （示例驱动以前叫 `mock_async.py` / `mock_sync.py`，于是真被扫进来了，
> 用户在生成工作台里能选中「示例同步生图」，提交后只拿到占位图。）

## 3.5 参数声明（param_schema）

`ModelSpec.param_schema` 是**唯一的参数清单**：受理校验、前端表单、发给供应商的
请求体三处都读它，写一次三处生效。

| `type` | 前端控件 | 受理校验 |
|---|---|---|
| `enum`（或只给 `options`） | 下拉 | 值必须在 `options` 里 |
| `int` / `integer` | 档位下拉；超过 12 档转数字输入 | 整数，受 `min` / `max` / `step` 约束 |
| `float` / `number` | 数字输入 | 数字，受 `min` / `max` 约束 |
| `bool` / `boolean` | 开关 | 布尔（也接受 `"true"` / `"1"` / `"false"`） |
| `string` / `text` | 文本框 | 字符串，可选 `maxLength` |

- `default` 必须真在可选集合里：不在的话下拉会显示空白（生成页会回落到第一个
  选项），而供应商收到的可能是另一个值。
- 没写 `type` 时按形状推断：有 `options` 当枚举、有 `min` / `max` 当整数、
  否则按自由文本。
- **键名是契约**：`size` / `count` 在生图接口上是顶层字段（历史列表与统计按列
  读），其余键统一进 `params`。你只要声明 `quality`，前端会自动多一个控件、
  受理侧会自动校验——不需要改任何前端或后端代码。
- 未声明的键会被 400 拒绝并回 `availableParams`：宁可当场报错，也不要让用户
  改的值被静默忽略（打错字 `szie` 是最常见的一种）。
- `label` / `description` 可选：前端优先用 `label`，没有才查内置中文词典
  （`size`→尺寸、`count`→数量、`quality`→质量…），再没有就用键名原文。

> 视频侧表单目前仍只按 `durationSec` / `aspectRatio` 两个键取
> （`CreateTaskModal`、`StudioPage`），新增视频参数要单独接线；生图页已经完全
> 通用（`entities/model/lib/paramForm.ts`）。

## 4. 最小可运行示例

不想手抄就先复制骨架：`cp drivers/_template.py drivers/my_vendor.py`
（`_template.py` 以 `_` 开头，不会被自动载入，改完另存才生效）。

```python
"""my_vendor.py —— 就这么一个文件，不需要别的改动。"""
from infra.ai.base import (
    Capability, GenerationProgress, GenerationRequest,
    GenerationState, ModelSpec,
)
from modules.model_driver.spec import DriverMeta

META = DriverMeta(label="示例厂商", version="1.0.0")

class MyDriver:
    name = "my_vendor"
    capabilities = frozenset({Capability.IMAGE})
    models = (
        ModelSpec(
            id="my-image-1", label="示例生图",
            capability=Capability.IMAGE, provider="my_vendor",
            param_schema={"size": {"type": "enum",
                                   "options": ["1024x1024"], "default": "1024x1024"}},
        ),
    )
    supports_poll = True

    async def submit(self, request: GenerationRequest) -> str:
        return await my_client.create(prompt=request.prompt, **request.params)

    async def poll(self, external_id: str, *, model: str = "") -> GenerationProgress:
        state = await my_client.query(external_id)
        return GenerationProgress(
            external_id=external_id,
            state=GenerationState.SUCCEEDED if state.done else GenerationState.RUNNING,
            progress=state.percent,
            output_url=state.url or None,
            retryable=state.rate_limited,
        )

    async def cancel(self, external_id: str) -> None:
        await my_client.cancel(external_id)

Driver = MyDriver
```

`request` 上能拿到：`capability` / `model` / `prompt` / `params`（自由参数字典）/
`reference_urls` / `request_id`（本模块的任务 id，用于账单对账）。

## 5. 自动载入与校验

`registry.ensure_drivers_registered()` 做三件事：扫描 `drivers/` 包 →
逐文件实例化并校验 → 把合规驱动注册进 `ModelRouter`。

**校验项**（`spec.validate_driver`）：name 合法性、capabilities 非空且是枚举、
models 非空且归属一致、三个方法可调用。

**失败处理**：单个驱动文件出错只记一条错误，不会让服务起不来。
错误会出现在 `GET /model-driver/drivers` 的 `errors`、
`app drivers check`（exit 1）与 `app doctor` 的 `model_driver.drivers` 条目里。
模型 id 与别的驱动撞车、或同名驱动模型清单不同，同样只红那一条。

**为什么不另起注册表**：`generation.runner.handle_submit()` 里是
`get_model_router().provider_for(record.model)`，这是整条编排链唯一找供应商的地方。
驱动进这个路由，才能一行框架代码都不改地复用提交/轮询/重试/超时/恢复。

**一个必须知道的副作用**：`provider_for()` 对「未声明的模型名」只在
**路由里只有一个供应商时**兜底（本地 echo 联调）。`drivers/` 里每多一个驱动，
这个兜底就可能失效——一旦路由里有 2 个以上供应商，传一个没声明过的 model
名会直接 400，而不是悄悄落到 echo。写驱动时请让 `models` 列全，调用方也必须
用真实 model id（旧代码里那些随手编的名字会在这里现形）。

## 6. 下游怎么调用

```python
from core.ports.model_generation import SubmitSpec, get_model_generation

ticket = await get_model_generation().submit(
    SubmitSpec(
        model="my-image-1",
        prompt="一只戴帽子的猫",
        owner_id=actor.id,
        params={"size": "1024x1024"},
        source="storyboard",          # 谁发的，便于在任务列表里追溯
    )
)
# ticket.task_id / status / progress / output_url ...

ticket = await get_model_generation().query(ticket.task_id)
await get_model_generation().cancel(ticket.task_id)
models = get_model_generation().models("image")
drivers = get_model_generation().drivers()
```

`submit` 是**编排入口**，不是「同步等结果」：落一行 `driver_jobs` 并入队后立即返回。
后续由 `generation.runner` 接管：

```
submit() ─▶ driver_jobs(queued) ─▶ generation.submit ─▶ provider.submit
                                        │
                                        ├─ 同步终态 → 直接结算
                                        └─ 任务号 → generation.poll → provider.poll
                                                          │
                                              succeeded / failed / 超时 / 重试
```

因此**启动恢复、幂等键、退避重试、`submit_unknown` 判定、墙钟超时**与生图/视频
任务完全同一套，不需要在驱动里重复实现。未注册时端口 fail-closed 抛 503，
不会静默返回空模型列表。

## 7. REST 管理面与权限

| 方法 | 路径 | 权限 | 用途 |
|---|---|---|---|
| GET | `/api/v1/model-driver/drivers` | `model_driver:read` | 驱动清单、加载错误 |
| GET | `/api/v1/model-driver/models` | `model_driver:read` | 可用模型（含 `paramSchema`） |
| POST | `/api/v1/model-driver/tasks` | `model_driver:create` | 提交统一生成任务 |
| GET | `/api/v1/model-driver/tasks/{id}` | `model_driver:read` | 查询任务 |
| POST | `/api/v1/model-driver/tasks/{id}/cancel` | `model_driver:cancel` | 取消（会调驱动 `cancel`） |
| POST | `/api/v1/model-driver/tasks/{id}/retry?confirmCharged=true` | `model_driver:create` | 人工重试失败任务 |

这组接口是给运维/页面看状态用的；**模块间调用不走 HTTP**，走注入口。

## 8. 状态与重试口径

- 状态：`queued → running → succeeded / failed / cancelled`；
  `cancelled` 也算终态，`runner.handle_poll` 会早退，不会把取消掉的任务改回 running。
- 「提交结果未知」（请求已发出但未确认受理）一律判死，绝不自动重提，避免二次计费。
- 人工重试必须清 `submit_attempts` 与 `external_id`；带 `external_id` 的失败
  默认拒绝，需显式 `confirmCharged=true`。这个出口登记在
  `tests/test_user_retry_exits.py::EXIT_CASES`，新增 kind 时守卫会强制对账。

## 9. 自检命令

```bash
cd api
./.venv/Scripts/app.exe drivers list     # 列出自动载入的驱动、能力、模型数
./.venv/Scripts/app.exe drivers check    # 契约校验，有问题 exit 1（适合 CI / 部署）
./.venv/Scripts/app.exe doctor           # model_driver.drivers 条目
```

## 10. 明确不做的事

- **不做热重载**：驱动改动重启生效。运行中的任务持有供应商任务号，热替换会让续查打到另一套实现。
- **不复制编排逻辑**：不自己写队列/轮询/状态机，全部交给 `generation.runner`。
- **不改 `.env`**：所有供应商都由 `drivers/` 自动载入；名字撞车且模型清单不同会被报出来。
- **不做生成结果入草稿箱**：本模块目前不实现 `pipeline.archive_request`，
  产物只在 `output_url`；与资产库草稿箱的联动口径仍未拍板。

## 11. 整体迁移已完成（2026-11-13）

`AI_PROVIDERS` 白名单、`infra/ai/<name>.py` 这条老路径**已彻底下线**：

- `echo`（占位模型）迁进 `drivers/echo.py`，同样自动载入，仍靠 `placeholder=True`
  挡在选择器外；离线联调按 id 提交照常能解析得到。
- `libtv` 的 Provider 在 `drivers/libtv.py`，执行器监督层与固定版 Node 适配器
  整体搬进 `modules/model_driver/libtv/`（`executor.py` / `process.py` /
  `state.py` / `runtime/`），子进程以 `-m modules.model_driver.libtv.process`
  启动，`runtime/vendor` 按模块自身位置定位。
- 模板库不许直接 import 业务模块，经 `core/ports/model_generation` 的
  `libtv_paths()` / `libtv_execute_stage()` 问执行器。
- `core/config.py` 的 `ai_provider` / `ai_providers` / `ai_provider_list` 与
  `.env` 的 `AI_PROVIDERS` 一并删除；`get_model_router()` 只持有单例，
  注册统一由 `ensure_drivers_registered()` 完成。

**接入纪律**：新供应商一律在 `drivers/` 放一个 py 文件；没有启用清单可改。
`infra/ai/` 只剩协议类型（`base.py`）与路由（`router.py`）。

**已迁走的先例：`bailian`（2026-11-11，T32）**。它当初也在这张表里，理由是「依赖
run 工作区、归档租约、计费回执」。真正搬的时候并不需要把这些搬进驱动，而是把
**替换链需要的能力抽成协议**：`InputPreparer`（上传素材 + 自报可用性）放进
`infra/ai/base.py`，替换链改成「从路由拿实例、按类型判断」，另把提示词引用重编号
下移到 `core/prompt_refs.py`。驱动仍读写 run 工作区里的档案（那是替换链编排期写下的
格式），但它不再被任何业务模块 import。**新供应商照这条路径走，不要照抄「豁免」**。

模型 id 归属：三个 `*-replacement` 现在**同时**服务替换模板链与通用生成入口
（见 §2.5.1）；其余模型仍按各自声明的能力走。`bailian` / `libtv` 都不声明
`preferred_for`，默认视频模型一直是 `agnes-video-2.5-flash`。
