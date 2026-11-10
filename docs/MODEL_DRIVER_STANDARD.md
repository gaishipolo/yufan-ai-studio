# AI 模型驱动开发标准

> 一句话：**在一个 Python 文件里定义「怎么调用 / 怎么查结果 / 怎么停止」，模块自动载入，其他模块通过统一生成接口调用。**
>
> 适用版本：2026-11-10 起。规范定义与校验实现在
> `api/src/modules/model_driver/spec.py` / `registry.py`。

## 0. 这套东西解决什么

以前接一个模型供应商，要同时改 `.env` 的 `AI_PROVIDERS`、写 `infra/ai/<name>.py`、
在模型目录里登记、给每个调用方各写一遍提交与轮询。现在拆成两层：

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

**可选的两个声明**：

```python
preferred_for = frozenset({Capability.IMAGE})   # 「我的模型是该能力的默认」
submit_timeout_sec = 180.0                     # 单次提交最多等多久（秒）
```

`preferred_for` 不写就沿用「注册顺序第一个」，而 `drivers/` 永远后于
`AI_PROVIDERS` 注册——所以想当默认必须显式声明，否则默认会落在老的 infra
供应商上（agnes 就是这么踩过一次：从 `AI_PROVIDERS` 搬进 `drivers/` 后，
默认生图模型差点悄悄变成 echo 的 `seedream-3`）。

`submit_timeout_sec` 是**提交动作**的预算，不是整条生成的预算（后者由
`generation_timeout_sec` 管）。框架默认 120 秒，会再加上 30 秒余量作为提交
job 的硬超时。同步出图的驱动（提交即等结果）必须声明它，而且要≥上游最长
耗时：框架先到点就会取消 handler，任务被重投时「有提交意图、没有任务号」，
直接判成「提交结果未知」——钱可能花了、图丢了、还不许重试。异步驱动提交
很快，一般不用写。

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
参考实现见 `drivers/mock_async.py`（异步）与 `drivers/mock_sync.py`（同步），
两个都能离线跑通；真实厂商的行文见 `drivers/agnes.py`（同步 + 图生图/多图合成）。

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
模型 id 与别的驱动撞车、或与 `AI_PROVIDERS` 同名但模型清单不同，同样只红那一条。

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
- **不改 `.env`**：这与 `infra/ai/<name>.py` + `AI_PROVIDERS` 是两条并存的接入方式，
  老驱动不受影响；名字撞车且模型清单不同会被报出来。
- **不做生成结果入草稿箱**：本模块目前不实现 `pipeline.archive_request`，
  产物只在 `output_url`；与资产库草稿箱的联动口径仍未拍板。
