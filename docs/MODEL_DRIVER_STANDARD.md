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
| 可选导出 | `EXTRA_ROUTES = (APIRouter, ...)` —— 供应商专属端点（见 §1.1） |
| 可选导出 | `EXTRA_JOBS = (JobSpec(...), ...)` —— 供应商专属异步作业（见 §1.1） |
| 可选导出 | `EXTRA_SCHEMAS = (BaseModel 子类, ...)` —— 供应商专属出参（见 §1.1） |
| 生效方式 | 重启进程即自动载入；**新增文件可热接上**（`POST /api/v1/model-driver/refresh`，见 §5.1），**改已加载的文件不热替换**，要重启才生效 |

### 1.1 供应商专属的接口面：自己声明（2026-11-23）

公共层不认识供应商：加一个供应商**不该改公共文件一行**。所以供应商自己的端点、
作业、出参由驱动文件自己声明：

```python
from fastapi import APIRouter
from modules.model_driver.spec import JobSpec

router = APIRouter(prefix="/my_vendor")          # 自己带路径前缀，不要带 tags
EXTRA_ROUTES = (router,)
EXTRA_JOBS = (JobSpec(name="my_vendor.download"),)
EXTRA_SCHEMAS = (MyAccountOut,)
```

- `EXTRA_ROUTES`：框架挂到模块路由上，对外是 `/model-driver/my_vendor/...`。
  子 router **不要带 tags** —— 标签由父 router 统一给，否则同一个接口在
  OpenAPI 里会出现两个重复的分组。
- `EXTRA_JOBS`：作业名必须与 `workers.register_handler(...)` 里用的是同一个；
  **声明了却没注册 handler，启动就失败**（不是静默少一个消费者）。
- `EXTRA_SCHEMAS`：供应商专属出参；公共出参不在这里 —— 那是框架自己的口径。

三个常量都必须是 tuple，元素类型不对 → **启动失败**（`spec.ExtrasError`）。

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

**可选能力也是协议**（`infra.ai.base`，运行时判定用 `isinstance`）：
`PollCapable`（`supports_poll`）、`PollPacing`（`poll_interval_sec`）、
`SubmitBudget`（`submit_timeout_sec`）、`InlineReferencesCapable`（`inline_references`）、
`ReferenceLimitsCapable`（`reference_limits`）。五个刻意分开 —— 声明了 `supports_poll`
但没写节奏的驱动不会被当成「不支持轮询」，只是按全局间隔问。
**不要**用 `getattr(provider, "...", None)` 探测：属性名写错时它只会安静地返回
`None`，然后退化成默认行为（轮询变 2 秒、预算变默认），日志里一句话都没有。

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

**素材上传是驱动内部的事，不是协议**。多数视频模型只接受**公网 URL** 作参考素材，
不认本地文件——这类驱动要在提交前把素材直传供应商自己的对象存储，再把直链拼进
请求体。这一步既不是 `submit` 也不是 `poll`，写法是驱动自己的私有方法，由它的
`submit()` 调用：

```python
def unavailable_reason(self) -> str: ...                  # 空串=可用；否则给用户看的原因
async def prepare_inputs(self, run_id: str, model: str = "") -> dict: ...   # 上传并返回请求体
```

**不要为它造可选协议**：曾经有一个 `InputPreparer` Protocol，唯一的调用方（替换链）
在 2026-11-14 就删了，剩下的 `isinstance` 判断全仓为零 —— 协议留着的效果只是让人
以为「不实现它就跑不起来」。范例见 `drivers/bailian.py`（`submit()` 里先
`prepare_inputs` 再落请求体）。

### 2.4.1 参考素材有两条通道，由驱动声明走哪条（2026-11-22）

参考素材不是「模型参数」，走 `GenerationRequest.reference_urls` 这条独立通道。
但**怎么把字节送到供应商面前**，各渠道差得很远，于是分成两条：

| 通道 | 声明 | 链路层做什么 | 适用 |
|---|---|---|---|
| 落盘物化 | 不声明 | `params["referenceKeys"]` → 物化器写进归档工作区，上传时才有地址 | LibTV / 百炼（执行器型） |
| 内联 | `inline_references = True` | 把存储里的字节读成 `data:<mime>;base64,...` 进请求体 | 直连型（Agnes 视频） |

为什么直连型只能内联：上游只收**公网 URL** 或 `data:` URI，而我们的对象存储在
内网、供应商根本拉不到（实测内联 data URI 上游照收照出片，轮询回包里还有
`usage.input_image_count`）。引入了公网直传就要新增公网可达存储与合规口径，
成本远高，所以先走内联。

**声明出来的限制也要跟着走**（`ReferenceLimitsCapable`，属性 `reference_limits`）：
每类素材的份数上限（`max_count`）、图片的单边像素范围（`min_side` / `max_side`）、
单份与同类合计的体积（`max_bytes` / `max_total_bytes`）、音视频的单段与同类合计
时长（`min_seconds` / `max_seconds` / `max_total_seconds`）。所有字段 `0` 表示不限。
各渠道不一样（Agnes 256–5760、单张 15MB、音频 2–12 秒；百炼 240–8000），写成全局
常量就会把「百炼能用的图」在受理期拒掉，而用户不知道错在哪。受理期按资产库里的
宽高 / 体积 / 时长判一次（早失败、报错说人话），提交前按真实字节再判一次
（那三样都可能是 0，或与实际文件对不上）。

**合计口径必须单独声明**，不能靠逐份判推出来：供应商的表格同时写着「单张 15MB」
和「整单 50MB」—— 五张满额就是 75MB，每张都合法而整体超限；时长同理，
「总时长 2–12 秒」是合计，三段各 5 秒单看都合规。没有声明时就退回
`modules/task/service.py` 的兜底 1–15 秒（LibTV「原片」的口径），**别把兜底
当成 Agnes 的规矩**：它比 Agnes 宽，会把 1.5 秒的音频放过去，到上游才 400。

这份声明还会**经目录接口下发给前端**（`GET /models` 的 `referenceLimits`，
按类型分组）。必须下发，因为 `max_references` 只是**总数**：「5 张图 + 3 段音频
= 总数 8」的模型只给总数，用户加到第 6 张图时界面还说「还能再加」，提交才吃 400。
前端据此逐类算余量，没声明的供应商拿空对象、退回只守总数。

**提示词里的素材指代由驱动自己翻**：`{{Mixed N}}` 是本项目的模板语法，上游不认识。
`core.prompt_refs.translate_prompt_with()` 给的是**编号规则**（按类型各自从 1 开始，
与上游分数组收的形状一致），**标签**由驱动给 —— 生图与替换链用「图1」，
Agnes 视频用 `<Picture 1>`（上游只认这个写法）。两边各写一份编号规则，迟早出现
「档案里写 图1、实际发出去的是 图2」这种对不上的情况。

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

**`reference_urls` 在统一门面这一层只接内联 `data:` URI**（2026-11-15）：
http(s) 地址会被受理期拒掉。收地址等于让供应商替调用方去取一个我们判不了归属的
资源——我们自己的预签名链接一旦转发出去，拿到的人就能把那件私有素材复制成新图，
绕开共享审批。`task` / `image` 两条链一直是「按资产 id 校验可读性 → 本地展开成
data URI」，门面没有理由例外。

## 5. 自动载入与校验

`registry.ensure_drivers_registered()` 做三件事：扫描 `drivers/` 包 →
逐文件实例化并校验 → 把合规驱动注册进 `ModelRouter`。

**校验项**（`spec.validate_driver`）：name 合法性、capabilities 非空且是枚举、
models 非空且归属一致、三个方法可调用。

**失败处理**：单个驱动文件出错只记一条错误，不会让服务起不来。
错误会出现在 `GET /model-driver/drivers` 的 `errors`、
`app drivers check`（exit 1）与 `app doctor` 的 `model_driver.drivers` 条目里。
模型 id 与别的驱动撞车、或同名驱动模型清单不同，同样只红那一条。

**例外：自报内容不合法 = 启动失败**。`EXTRA_*`（§1.1）装配失败会抛
`spec.ExtrasError` 直接让服务起不来，**不会**被当成「这个驱动加载失败」吞掉。
两者后果不同：加载失败只影响一个供应商；装配失败意味着端点凭空消失 ——
前端只会看到一个没头没脑的 404，比启动失败难查得多。

**为什么不另起注册表**：`generation.runner.handle_submit()` 里是
`get_model_router().provider_for(record.model)`，这是整条编排链唯一找供应商的地方。
驱动进这个路由，才能一行框架代码都不改地复用提交/轮询/重试/超时/恢复。

**一个必须知道的副作用**：`provider_for()` 对「未声明的模型名」只在
**路由里只有一个供应商时**兜底（本地 echo 联调）。`drivers/` 里每多一个驱动，
这个兜底就可能失效——一旦路由里有 2 个以上供应商，传一个没声明过的 model
名会直接 400，而不是悄悄落到 echo。写驱动时请让 `models` 列全，调用方也必须
用真实 model id（旧代码里那些随手编的名字会在这里现形）。

### 5.1 新增驱动文件：热接上（2026-11-23）

`registry.refresh()` 重新扫描 `drivers/`，把**新增**的文件热接上：import → 校验 →
注册进路由 → 挂上它的 `EXTRA_ROUTES`（§1.1）。不用重启。

```bash
curl -X POST /api/v1/model-driver/refresh   # 权限：model_driver:refresh
```

还有一条周期兜底（`model_driver.drivers.refresh`，5 分钟一轮）跑同一套逻辑。

**边界是刻意选的**：

| 情况 | 结果 |
|---|---|
| **加文件** | 热生效（`added`） |
| **改已加载的文件** | **不热替换**，只出现在 `changedFiles` 里提醒重启 |
| **删文件** | 不动（已注册的驱动继续用） |
| **多实例部署** | 进程级：只影响本实例，其余实例靠各自的周期刷新收敛 |

**为什么改文件不热替换**：`importlib.reload` 会重新执行模块级代码、丢掉正在跑的
实例状态 —— 手里还攥着供应商任务号的任务会被续查到另一套实现上。
「假装生效」比「明确说要重启」危险得多，所以这里只报不改。

**刷新会连带重跑必填项与提交预算预检**：配置被改坏要在这里就暴露，而不是等
下次重启（那时坏配置已经生效过一整轮了）。巡检失败**不抛** —— 运行期没有
「启动失败」这个选项，问题进日志也进结果，两件事分开报。

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

上面画的是 `model_driver` 端口这条**旁路**。业务页面（生成工作台 / 生图 / 模板库）
走的是另外两条提交端点，最后同样汇进 `generation.runner` —— 三条链路从按钮到
结果的逐节点输入输出见 **§12**。

## 7. REST 管理面与权限

| 方法 | 路径 | 权限 | 用途 |
|---|---|---|---|
| GET | `/api/v1/model-driver/drivers` | `model_driver:read` | 驱动清单、加载错误 |
| GET | `/api/v1/model-driver/models` | `model_driver:read` | 可用模型（含 `paramSchema`） |
| POST | `/api/v1/model-driver/tasks` | `model_driver:create` | 提交统一生成任务 |
| GET | `/api/v1/model-driver/tasks/{id}` | `model_driver:read` | 查询任务 |
| POST | `/api/v1/model-driver/tasks/{id}/cancel` | `model_driver:cancel` | 取消（会调驱动 `cancel`） |
| POST | `/api/v1/model-driver/tasks/{id}/retry?confirmCharged=true` | `model_driver:create` | 人工重试失败任务 |
| POST | `/api/v1/model-driver/refresh` | `model_driver:refresh` | 刷新驱动目录，热接上新增的驱动文件（见 §5.1） |

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

- **不做热替换**：只热接上**新增**的驱动文件（§5.1）；**改已加载的文件要重启**。
  运行中的任务持有供应商任务号，热替换会让续查打到另一套实现。
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
**替换链需要的能力抽成协议**：`InputPreparer`（上传素材 + 自报可用性）当时放进了
`infra/ai/base.py`（该协议随替换链下线已于 2026-11-15 删除，见 §2），另把提示词引用重编号
下移到 `core/prompt_refs.py`。驱动仍读写 run 工作区里的档案（那是替换链编排期写下的
格式），但它不再被任何业务模块 import。**新供应商照这条路径走，不要照抄「豁免」**。

模型 id 归属：三个 `*-replacement` 现在**同时**服务替换模板链与通用生成入口
（见 §2.5.1）；其余模型仍按各自声明的能力走。`bailian` / `libtv` 都不声明
`preferred_for`，默认视频模型一直是 `agnes-video-2.5-flash`。

## 12. 三条生成链路全景：从前端按钮到结果回显（2026-11-19）

§6 画的是 `model_driver` 端口这条旁路。业务页面走的不是它，而是下面两条提交端点。
这一节记录三条链路的完整调用链与**每个节点的输入输出**，供后续优化（统一编排、
减少重复校验、缩短回显路径）时对照现状。行号是记录当时的锚点，改代码后以代码为准。

### 12.1 总体：三个入口、两条端点、一个编排层

```
生成工作台 ────────────────▶ POST /tasks ──────────┐
模板库(视频) ─先 compile─▶ POST /tasks ────────────┤
生图 ─────────────────────▶ POST /generations/image ├─▶ generation.runner
模板库(图片) ─先 compile─▶ POST /generations/image ─┘        │
                                                            ▼
                              submit job → provider.submit → (poll job) → provider.poll
                                                            ▼
                                   落库 → 前端条件轮询列表 → 拿 outputUrl → 预览/下载
```

- **没有 SSE**。三条链路都是 POST 返回一条 `queued` 记录，前端靠列表 query 的
  `refetchInterval` 等结果回填；mutation 的 `onSuccess` 里 `invalidateQueries`
  只是立刻重拉一次，那时异步结果通常还没出来。
- 视频的两条入口**共用** `POST /tasks` 和同一套轮询；图片的两条**共用**
  `POST /generations/image`。模板库独有一步 compile。
- 三条链路最终都进 `generation.runner`，共用 `ModelRouter`、幂等键、退避、
  `submit_unknown` 判定和墙钟超时；差异只在 pipeline 的 `build_request` 形状
  和落库表。

### 12.2 链路一：生成工作台 → `POST /tasks`（视频）

| # | 节点 | 输入 | 输出 |
|---|---|---|---|
| 1 | `StudioPage.submit`<br>`StudioPage.tsx:170` | 提示词、参数控件值、参考图 id | 手拼一个 `SpecCompileOut` 形状对象（`version=0`、`slots=[]`），不调 compile |
| 2 | `toTaskPayload`<br>`taskPayload.ts:123` | 上一步 + 模型 `paramSchema` 默认值 | `CreateTaskIn`：`title/prompt/model/durationSec/aspectRatio/referenceLibraryIds/textLibraryIds/params` |
| 3 | `POST /tasks`<br>`task/router.py:118` | `CreateTaskIn` | `201 ApiResponse[TaskOut]`；`TaskOut` 刻意**不含 outputUrl** |
| 4 | `TaskService.create_task`<br>`service.py:171` | 上一步 + actor | 校验模型/参数、冻结素材引用 → `video_tasks` 一行：`status=queued`、`reference_library_ids`、`text_json`、空 `output_url` |
| 5 | `_dispatch`<br>`service.py:467` | VideoTask | 入队 `generation.submit`（幂等键 `submit:video:{id}`） |
| 6 | `VideoTaskStore.load`<br>`task/pipeline.py:75` | DB 行 | `GenerationRecord`；`params.referenceKeys` = 资产 id → 存储 key（驱动可直接取字节） |
| 7 | `build_request`<br>`task/pipeline.py:257` | GenerationRecord | `GenerationRequest(capability=VIDEO, model, prompt, params, request_id)`，**不填 reference_urls** |
| 8 | `provider.submit`<br>`runner.py:332` | GenerationRequest | `str` 任务号（异步）或终态 `GenerationProgress`（同步）→ `status=running`、`external_id` |
| 9 | `provider.poll`<br>`runner.py:600` | external_id | `GenerationProgress(state/progress/output_url/queued/retryable)` → `mark_progress` |
| 10 | `mark_succeeded`<br>`task/pipeline.py:211` | 终态结果 | `status=completed`、`progress=100`、`output_url`、`downloadable=True`；成片经 `_persist` 落到对象存储 |

### 12.3 链路二：生图 → `POST /generations/image`

| # | 节点 | 输入 | 输出 |
|---|---|---|---|
| 1 | `ImagePage.submit`<br>`ImagePage.tsx:123` | prompt、size/count、参考图 id | `GenerateImageIn`（默认模型时 `model` 留空交后端） |
| 2 | `POST /generations/image`<br>`image/router.py:41` | `GenerateImageIn` | `201 ApiResponse[ImageJobOut]` |
| 3 | `ImageService.generate`<br>`image/service.py:169` | 上一步 + actor | 校验、参考图快照、**文本冻结** → `image_jobs` 一行 `status=queued`，然后 `dispatch(KIND_IMAGE, id)` |
| 4 | `ImageJobStore._to_record`<br>`image/pipeline.py:136` | DB 行 | `GenerationRecord`；`prompt` 已展开 `{{Text N}}`、`reference_urls` 是对象 key |
| 5 | `build_request`<br>`image/pipeline.py:400` | GenerationRecord | `GenerationRequest`；`reference_urls` = 逐张读字节转 `data:mime;base64,...`（供应商访问不到私签链接） |
| 6 | `agnes.submit`<br>`drivers/agnes.py:199` | GenerationRequest | **同步**：直接返回终态 `GenerationProgress(SUCCEEDED, output_url=图片直链)` |
| 7 | `_settle`<br>`runner.py:915` | 同步终态 | 跳过 poll job，`persist_output` 落盘 → `mark_succeeded`，`status=succeeded` |

差异点：Agnes 是同步驱动（`supports_poll=False`），所以这条链路**没有 poll job 这一跳**。
异步生图驱动（如 echo 占位）才会走 poll。注意「同步」是驱动的属性，不是 `ImageService`
自己同步跑——受理永远只是建记录 + dispatch，生成都在 worker 里。

### 12.4 链路三：模板库 → compile + 二选一提交

| # | 节点 | 输入 | 输出 |
|---|---|---|---|
| 1 | 填表页点「生成图片/视频」→ 二次确认 | `slotValues`、模型、运行时参数 | `SpecCompileIn` |
| 2 | `POST /template-specs/{key}/compile`<br>`spec_router.py:198` | `SpecCompileIn` | `SpecCompileOut`：`version/model/prompt/referenceLibraryIds/textLibraryIds/slots` |
| 3 | `compile_for`<br>`spec_service.py:731` | 上一步 | 读已发布版本 + 槽位 → `_fills` 查库校验素材 → `compile_spec` 纯函数算编号 → 成品 ≤7000 字闸门。**全程只读，不建任务** |
| 4 | `toSubmitPayload`<br>`taskPayload.ts:197` | `SpecCompileOut` + 模型能力 | 图片→`GenerateImageIn`，视频→`CreateTaskIn` |
| 5 | 提交 | 上一步 | 图片接链路二第 2 步起，视频接链路一第 3 步起 |

compile 不落库的原因见 `spec_service.py` 模块 docstring 与 `core/template_spec/compile.py`：
编号是派生物、编译是纯函数（无 session / 资产库 / 网络），业务模块之间不许互相 import，
所以「把结果转发给 `/tasks`」这一步交给前端做——那一步没有业务判断。

### 12.5 结果回显

三条链路提交成功后，mutation 只失效列表 query，不在原地等结果：

- 视频：`useTaskList` 每 2.5s（`task.queries.ts:28`）、feed 每 5s；
  `queued/running` 继续，终态 `completed/failed` 停。
- 生图：`useImages` 每 3s（`image/model/status.ts:8`）；`queued/running` 继续，
  `succeeded/failed` 停；老记录无 `status` 按 `succeeded` 处理，避免轮询不停。
- 模板库自身不轮询，靠「去任务中心」跳转；完成提醒由 `TaskCompletionWatcher`
  基于 feed 差集弹 toast（用 feed 是为了让生图也有完成提醒）。

`output_url` 在库里存的是**对象 key**，出参时用 `artifact_urls()` 现签成 24 小时
有效的 URL（`infra/artifacts.py:52`）。列表 `TaskOut` 只给 `downloadable` 布尔，
feed item 才带签好名的 `output_url`——这是前端唯一拿到成片地址的地方。
下载走 `shared/lib/download.ts` 的 `fetch` → `blob:` URL，失败回退打开新标签。

### 12.6 记录现状时特别核对过的两点

1. **`POST /tasks` 走的是 `KIND_VIDEO → VideoGenerationPipeline`，不是
   `KIND_MODEL_DRIVER`**。后者是 `POST /model-driver/tasks` 的旁路（§6）。两者在
   `generation.runner` 汇合，共用编排与 `ModelRouter`，差异只在 `build_request`
   形状（视频把 `referenceKeys` 塞 `params`；旁路用 `reference_urls` 独立通道）
   和落库表（`video_tasks` vs `driver_jobs`）。
2. **计费纪律集中在 runner**：提交前先写 `submit_attempts` 落「提交意图」；一旦
   「有意图但无 external_id」= `submit_unknown`，绝不自动重提，避免二次扣费。
   限流重投必须复用原 `idempotency_key`，否则父子作业并发提交会双计费。
