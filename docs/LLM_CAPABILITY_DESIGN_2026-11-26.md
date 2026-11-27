# LLM 调用能力设计方案（设计稿 · 待实施）

> 状态：**设计已闭环，待实施**（2026-11-26 讨论产出）
> 日期：2026-11-26
> 目标：接入大语言模型（LLM），**单次调用**产生结果供其他模块使用；为将来的 agent（长会话）预留兼容边界
> 拍板结论（2026-11-26）：协议用 **OpenAI 兼容一家**（base_url + model 可配）；V1 **只做能力层**，不接业务场景；**同步调用、产物只作返回值**（不落库、不进队列）

---

## 0. 修订记录

| 日期 | 变更 | 对应章节 |
|---|---|---|
| 2026-11-26 | 初稿：现状核实、三层方案、供应商接入方式、工作包与守卫清单 | 全文 |
| 2026-11-26 | 修正：删掉 `core/ports/llm.py` + `PORT_CATALOG` 登记（客户端在 infra 时业务模块直接 import 即可，注入口是自找的） | §3.2、§4 |
| 2026-11-26 | 修正：配置层改为**按供应商分组**形状（V1 即按多家并存设计），避免将来改配置层 | §5.2 |
| 2026-11-26 | 补：agent 必然是业务模块 → 循环归 `generation/`、业务归 `modules/agent/`；据此定出三条硬约束 | §6 |
| 2026-11-26 | 实施前复核：`LLMSettings` 改成 `DriverSettings` 子类（§5.2）；`get_http()` 不复用、LLM 自建 client（§2.2/§7）；`LlmMessage` 补 `tool_call_id`、`params` 改 `default_factory`（§4） | §2.2、§4、§5.2、§7 |
| 2026-11-27 | 审查修断链：多供应商页的 `default` 直接写 `LLM_*`（与运行时 LLMSettings 同前缀），不再"迁移"到 `LLM_DEFAULT_*`；`LLM_MODELS` 映射表由运行时解析 | §5.2、§8 |

---

## 1. 目标与非目标

### 目标

1. 提供一个**单次 LLM 调用**的能力：输入消息序列，输出一段文本。
2. **协议层从第一天就用「消息数组」**，而不是「一段 prompt 字符串」——这是唯一一处「现在省事、以后要推倒重来」的地方。
3. 为将来的 **agent（长会话）** 预留兼容边界，但 **V1 不实现** agent。
4. **换一个供应商不写代码**（同协议前提下）。

### 非目标（V1 明确不做）

| 不做 | 理由 |
|---|---|
| 不进 `generation/` 框架、不加 `Capability.TEXT` | 现有 `ModelProvider` 是 submit/poll/cancel 的**生成范式**（产物是文件/URL）；LLM 是一问一答同步返回（产物是文本）。硬套要凭空造任务记录、轮询、恢复语义，而 agent 需要的是「会话 + 消息序列 + 工具循环」，与任务范式正交 |
| 不建业务模块、不开路由、不建表 | V1 是能力层，没有业务场景要消费它 |
| 不做服务端会话状态 | 会话历史是**业务数据**（有归属、有保留期、可能要审计），归将来的 agent 模块 |
| 不做流式 | 将来协议层**新增** `astream()`，`complete()` 签名不变，调用方无感 |
| 不做工具执行 | `tool_calls` **解析但丢弃**，不执行 |
| 不接业务场景 | 按拍板：V1 只交客户端 + 协议 + 守卫 |

---

## 2. 现状核实（2026-11-26，读代码确认，非推测）

### 2.1 仓库里没有 LLM 的痕迹

- `infra/ai/base.py::Capability` 只有 `VIDEO` / `IMAGE` / `AUDIO`；
- 全仓无 `chat/completions`、无 `messages` 型请求；
- 现有 6 个驱动 / 16 个模型全是生成类。

### 2.2 可复用的件

| 件 | 位置 | 用途 |
|---|---|---|
| `DriverSettings` + `driver_config(prefix)` | `infra/driver_config.py` | 供应商私有配置基座，前缀即环境变量前缀 |
| `vendor_settings(cls, env_prefix)` | `infra/env_settings.py` | mtime 指纹缓存，**改 `.env` 下次请求即生效**（无需重启） |
| ~~`get_http()`~~ | `infra/http.py` | **LLM 不复用**：它是为「产物下载」造的（GET stream + 逐跳 SSRF 校验 + 大小上限），只有 `fetch_bytes`/`fetch_file` 两处调用；供应商 POST 一律自建 client，理由见 §7 |
| `require_api_key` | `modules/model_driver/kit/credentials.py` | 缺密钥 = 4xx（不是 5xx）——**必须复用，不能另写** |
| `classify_http_error` | `modules/model_driver/kit/errors.py` | HTTP 错误分类的唯一实现 |
| 运维页供应商项自动发现 | `modules/model_driver/runtime_config.py::_discover_vendor_fields` | 扫 `infra/**` 的 `DriverSettings` 子类，按字段名 `api_key`/`base_url` 自动出现在运维页 |

### 2.3 架构约束（决定落点）

| 守卫 | 含义 |
|---|---|
| `test_lower_layers_do_not_import_modules` | core / db / infra / generation **不能**反向依赖业务模块 |
| `test_modules_do_not_import_each_other` | 业务模块之间禁止横向 import |
| `CODE_STYLE.md` §0 第 1 条 | 依赖只能单向：`modules → generation / core / db / infra` |
| `test_port_modules_expose_the_full_shape` + `api/ports.py` | 新注入口必须登记 `PORT_CATALOG`，否则架构测试红 |

实测：**49 个 `modules/*` 文件直接 import `infra.*`** —— 业务模块可以直接用 infra 层能力，不需要注入口。

### 2.4 外部事实（阿里云官方文档，2026-11-26 核实）

- 百炼提供 OpenAI 兼容端点：`POST https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions`；
- 现有百炼视频驱动 base_url 是 `https://llm-cz0qm1j8cl2weo19.cn-beijing.maas.aliyuncs.com/api/v1` —— **同域同 workspace**，只差路径；
- 同端点可调 Qwen / DeepSeek / Kimi / GLM（百炼直供或三方直供）；
- **API Key 按地域绑定**：跨地域调用会 401（`invalid_api_key`），换地域必须连 key 一起换；
- `Qwen-Audio` 不支持 OpenAI 兼容协议（仅 DashScope 协议）。

---

## 3. 分层与落点

### 3.1 三层

| 层 | 位置 | 职责 |
|---|---|---|
| L1 协议 | `infra/llm/base.py` | `LlmMessage` / `LlmRequest` / `LlmResponse` / `LlmUsage` / `LlmToolCall` |
| L2 客户端 | `infra/llm/client.py` | OpenAI 兼容 wire format（协议唯一实现） |
| L3 供应商 | `infra/llm/providers/<name>.py` | 配置 + 平台模型 id → 上游 model 名 的解析表 |

### 3.2 为什么整个放 `infra/`，而不是 `modules/`

**`modules/` = 有业务语义的东西**（模板封装 / 资产库 / 任务 / 分镜 / 导出），每个都有表、路由、权限点、页面，**要能被删掉**。`infra/` = 可替换基础设施（storage / queue / archive / ai）。

LLM 调用与 `infra/ai/`（生图视频的供应商抽象）是**同一类东西**：按能力选供应商、调上游、解析结果，只是协议不同。放进 `modules/` 有三个具体问题：

1. 造出一个**没有业务语义却占模块位**的东西——没路由、没权限点、没页面，`app modules` 里显示一个只有后端能力的模块，`doctor` 还要为它写自检钩子；
2. 真接业务场景时（调用发生在 `modules/template_pack` 里）**必须拆**——横向依赖守卫会红，只能改走注入口，等于把「将来要拆」提前到今天；
3. 「AI 供应商」这个概念被劈成两半（`infra/ai/` 管生图视频、`modules/` 里再有个管 LLM）。

**连带修正**：`core/ports/llm.py` + `PORT_CATALOG` 登记在 V1 是**多余的**。客户端在 `infra/` 时业务模块直接 import 即可；`core/ports` 那套注入口机制是为「实现方在业务模块里」准备的。**V1 不建注入口。**

将来 agent 出现时，若其它业务模块要触发 agent 运行，那时才在 `core/ports/agent.py` 建注入口——那时它是真的需要。

### 3.3 infra 层的自我约束

`infra/` 的规矩是「不认识业务，只做可替换的基础设施」。所以落在 `infra/llm/` 的东西必须：

- ❌ 不含「起草文案」这类业务语义 → 客户端只管「把 messages 发出去、把响应解析回来」，**提示词模板永远由调用方拼**（否则换供应商就得改业务代码）；
- ❌ 不含业务表 → V1 不落库；
- ❌ 不含权限点 → 「LLM 密钥」是**配置项不是权限**，由 `runtime_config` 从 `DriverSettings` 自动发现。

---

## 4. 协议形状（给 agent 留的关键）

```python
@dataclass(frozen=True, slots=True)
class LlmMessage:
    role: str          # system | user | assistant | tool
    content: str
    name: str = ""          # role=tool 时的工具名
    tool_call_id: str = ""  # role=tool 必填：agent 把工具结果喂回去时上游靠它配对

@dataclass(frozen=True, slots=True)
class LlmRequest:
    model: str                              # 平台内 id（如 bailian/qwen-plus），不是上游 model 名
    messages: tuple[LlmMessage, ...]        # ← 数组，不是 prompt 字符串
    # 必须是 default_factory：写 `= ...` 时 `LlmRequest(...).params is Ellipsis`，
    # 不传就透给上游一个省略号（实测）。
    params: Mapping[str, Any] = field(default_factory=dict)
    request_id: str = ""

@dataclass(frozen=True, slots=True)
class LlmUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

@dataclass(frozen=True, slots=True)
class LlmToolCall:
    id: str
    name: str
    arguments: str      # JSON 字符串（OpenAI 原样），不是 dict

@dataclass(frozen=True, slots=True)
class LlmResponse:
    text: str
    model: str
    finish_reason: str                      # stop | length | tool_calls
    usage: LlmUsage
    tool_calls: tuple[LlmToolCall, ...] = ()
    raw_id: str = ""
```

### 四处为 agent 预留、V1 不实现

| 预留 | 将来怎么用 | V1 做什么 |
|---|---|---|
| `messages` 是数组 | 多轮历史直接往后追加 | 单次调用传 `((LlmMessage("user", prompt),),)` |
| `params` 是自由字典 | 放 `tools` 做函数调用 | 透传给上游 |
| `tool_calls` + `finish_reason` | agent 判断要不要执行工具 | **解析但丢弃**，不执行 |
| `LlmMessage.tool_call_id` | agent 把工具结果按 id 喂回去 | 解析 tool_calls 时带出 id，V1 不拼 tool 消息 |
| `usage` token 计数 | 接 `usage_metrics` 记账 | 随响应返回 |

### 两条硬约定

1. **`params` 里放 `tools` 时，工具定义用 OpenAI 的形状**（`type/function/name/description/parameters`），不自创一套。百炼 compatible-mode 就是 OpenAI 格式，零翻译成本；agent 将来把「业务工具」翻译成这个形状时少一层永远对不齐的映射。
2. **`tool_calls.arguments` 是 JSON 字符串**（OpenAI 原样），不是 dict——直接透传给上游，将来 agent 只 `json.loads` 一次。

### `params` 的口径（已按仓库既有做法定）

**透传，不做白名单。** 与生图的 `params` 透传一致（`GenerationRequest.params` 是自由字典）。理由：将来「起草文案」需要低温、需要限制长度，硬编码参数表反而是债。

代价：调用方能传出上游不认的参数，上游会 400。这与仓库现有做法一致，**有意接受**。

---

## 5. 供应商接入

### 5.1 两种情形

**情形 A：同协议供应商**（DeepSeek / Kimi / GLM / 百炼上的第三方模型）——**改配置 + 加一行映射表，零代码**：

| 做什么 | 在哪 |
|---|---|
| 改网关地址 | `LLM_<NAME>_BASE_URL`（运维页直接改，热生效；或 `.env`） |
| 加密钥 | `LLM_<NAME>_API_KEY`（运维页直接填，不进代码） |
| 加模型映射 | `providers/<name>.py` 的解析表添一行 |

**情形 B：非 OpenAI 兼容协议**——加一个 `providers/<name>.py`，实现请求拼装与响应解析。**L1 协议层与调用方仍不动**（L1 定的是 `LlmRequest`/`LlmResponse`，不是 wire format）。

选 OpenAI 兼容作为唯一实现的原因：情形 A 覆盖了国内几乎所有实际选择，情形 B 留给真需要原生特性（如某家的 thinking 字段、特殊 tool 格式）时再开。

### 5.2 配置层：按供应商分组

**不要**做成单个全局 `LLM_BASE_URL` + 单张模型表——那样两家并存会打架（base_url 只有一个、key 只有一个、模型 id 会撞）。

形状（与仓库既有 `BAILIAN_*` / `GRSAI_*` / `AGNES_*` 并存一致）：

```python
# infra/llm/providers/openai_compatible.py
class LLMSettings(DriverSettings):
    """任意一个 OpenAI 兼容端点：base_url + api_key 即可，不绑死某家。"""
    model_config = driver_config("LLM")
    base_url: str = ""    # 必须显式配，空 → 400（不猜默认网关）
    api_key: str = ""
    timeout_sec: float = 120.0
    model: str = ""       # 上游模型名，如 qwen-plus / deepseek-v3

get_llm_settings = vendor_settings(LLMSettings, env_prefix="LLM")
```

**为什么必须是 `DriverSettings` 子类、而不是模块级常量**：运维页的
`runtime_config._discover_vendor_fields()` 走 `import_all_driver_settings()` 的
`infra/**` rglob，只认 `DriverSettings` 子类；`.env.example` 双向对齐守卫
（`tests/test_config_guard.py`）同样只认它。写成 `LLM_BASE_URL = "..."` 这种
模块常量，运维页不会出现、守卫巡检不到、`.env` 改了也不热生效。

V1 只实现一个通用 OpenAI 兼容供应商：随便一个满足协议的 `base_url` + `api_key`
都能接。平台模型 id 用 `llm/<上游名>` 前缀（`llm/qwen-plus`），天然全局唯一；
将来要并存第二家，再各建一个 `providers/<name>.py`，换前缀即可，不改已有文件。

**为什么 V1 就这么做**：一家时只多一层文件与一段前缀；不这么做，将来要并存时是「改配置层 + 改映射表 + 改 id 解析」，而那时已经有业务在跑，风险高得多。而 `DriverSettings` 子类 + 前缀的机制**本来就是为多供应商设计的**，照抄它的形状不额外背债。

### 5.3 模型 id 的翻译

客户端收到 `model="bailian/qwen-plus"` → 查该供应商的解析表 → 得上游 `"qwen-plus"` → 发给上游。

照百炼驱动的 `_real_model()` 做法：**未知 id 直接 400**，不静默回落。这样「换供应商不改业务代码」才成立——业务侧永远只认平台 id。

---

## 6. 与将来的 agent 的关系

**agent 必然是业务模块**——它有会话表（谁跟谁聊的、每轮消息、花了多少 token）、权限点、页面。

但要拆成两半：

| | 归属 | 内容 |
|---|---|---|
| 业务部分 | `modules/agent/` | 会话表、工具清单、权限、路由、页面、「这个 agent 能干什么」 |
| 运行循环 | `generation/agent_loop.py` | 多轮推进、解析 tool_calls、执行工具、喂回结果 |

循环放 `generation/` 的理由：`generation/runner.py` 现在做的正是同一类事（submit → poll → persist → recovery），**业务无关的编排机制**。而循环里「执行工具」必须回调业务能力，所以它只能通过 `core/ports/` 拿。

**agent 的「工具」，基本上就是现有注入口的投影**：

- `core/ports/library_lookup.py` — 查素材
- `core/ports/model_generation.py` — 提交生成
- `core/ports/usage_metrics.py` — 记 token 用量

不用为 agent 造新的能力通道。

### 由此定出的三条硬约束

1. **客户端必须无状态**——会话历史归 agent 的表。客户端收到 messages、吐出 text，**什么都不记**。理由比「infra 不该持有业务数据」更硬：会话历史就是业务数据。
2. **协议是 messages 数组**——agent 循环每轮往数组尾部追加，不用改客户端。
3. **`tool_calls` 原样带出工具名 + JSON 参数**。

### V1 完全不用为 agent 做的事

不建会话表；不实现流式；不实现 tool 循环；不改任何已有文件。四个文件交付后，agent 模块来了直接就能用。

---

## 7. 三条不能省的纪律

1. **不自动重试**——`infra/http.py` 注释写死「POST 重复提交等于重复计费」，LLM chat 同理。只在 4xx 明确拒收时可重试。
2. **超时单独给 —— LLM 自建 client，不复用 `get_http()`**。
   这不是"给共享 client 换个超时"的问题，查代码后的实情：
   - `get_http()` 的 `timeout=HttpSettings.timeout_sec`（默认 30s）是 **client 级默认值**，
     httpx 本来就支持 per-request `timeout=` 覆盖（下载链路一直在这么做），
     所以它并非"定死 30s 改不了"。
   - 但它**只为产物下载而生**：`a2605c73` 引入时同步做的是「手动跟重定向 + 逐跳
     `validate_public_url` + 流式大小上限」；全仓只有 `infra/http.py` 里两处
     `stream("GET", ...)` 用它，没有任何 POST 调用点。
   - 供应商驱动全部自建 client，是四个语义决定的：① 每个驱动自己的
     `timeout_sec` 还参与 `submit_budget_for` 提交预算推导；② 测试要注入
     `transport=MockTransport` 打真实请求构造路径；③ `trust_env=False` 不吃系统代理；
     ④ 超时异常必须**原样抛出**给 runner 的 `_possibly_delivered` 判「可能已受理」，
     包一层就变成"确定没送达"→ 重复计费。
   - LLM 是同步单次调用，无任务/轮询/预算推导，但**保留 ②③ 两条**（测试注入、
     不吃代理）。所以客户端自建 `httpx.AsyncClient`，超时读 `LLMSettings.timeout_sec`
     （默认 120s），测试注入 MockTransport。复用了 `get_http()` 反而要为 MockTransport
     开全局后门，把测试的口子留在生产里。
3. **错误分类用现成映射**（`kit/errors.py`）——401/403 → Unauthorized/Forbidden，429 → RateLimited，5xx → UpstreamError，其余 4xx → BadRequest。缺密钥 → 4xx（`kit/credentials.py`）。

---

## 8. 工作包

每个包可独立提交、独立验证。**W1 与供应商数量无关，两条路都要先做。**

### W1：kit 件下沉 `infra/`（LLM 与供应商数量无关，先做）

- `modules/model_driver/kit/errors.py` → `infra/http_errors.py`
- `modules/model_driver/kit/credentials.py` → `infra/credentials.py`
- 原路径保留 re-export（**照 `kit/settings.py` → `infra/env_settings.py` 的既有先例**）

理由：这两件现在在 `modules/model_driver/kit/` 下，而 infra 不能反向依赖 modules、业务模块之间禁止横向 import——LLM 客户端要用「缺密钥=4xx」「按状态码分类」这两条现成纪律，就得把它们下沉。

**验收**：现有驱动测试全绿（**行为零变化**）。

### W2：L1 协议

- `infra/llm/base.py`：五个 dataclass
- **不建 `core/ports/llm.py`、不登记 `PORT_CATALOG`**（见 §3.2）

**验收**：ruff + mypy 绿；架构测试绿（不应有新增注入口）。

### W3：客户端 + 供应商

- `infra/llm/client.py`（OpenAI 兼容唯一实现，自建 client + 测试可注入 transport）
- `infra/llm/providers/openai_compatible.py`（`LLMSettings(DriverSettings)` + 模型表）
- `infra/llm/__init__.py`（导出 `complete()` 与模型表查询）

**验收**：httpx `MockTransport` 单测，钉死四件事：

| 守卫 | 内容 |
|---|---|
| G1 | `messages` 原样按序发出去（role / content / name 都不丢） |
| G2 | 平台 id 正确翻译成上游 model 名；未知 id → 400 |
| G3 | **5xx 不自动重试**（防止将来有人「顺手加个重试」导致重复计费） |
| G4 | 错误分类映射正确（401/403/429/5xx/其它 4xx） |

另需：响应 `tool_calls` 被解析进 `LlmResponse`（即使不执行）；`usage` 三项被解析。

### W4：运维页可配 + 自检

- `LLMSettings(DriverSettings)` 声明 `base_url` / `api_key` → 运维页**自动**出现
  （`runtime_config._discover_vendor_fields` 经 `import_all_driver_settings` 扫 `infra/**`），
  无需改公共文件
- `.env.example` 补 `LLM_*` 键（`tests/test_config_guard.py` 双向对齐）
- 连通性入口做成**独立命令 `app llm-check`**，不放进 doctor：`diagnostics._check_http_channel`
  明确「只报配置，不发真实请求」（doctor 要在没外网的机器上能跑），连通性是部署验收，
  由人显式触发。因此**不需要改 README 的「部署自检（23 项）」**。

**验收**：`app doctor` 输出可读；配置守卫测试绿。

---

## 9. 交付物清单（最终形状）

```
api/src/infra/llm/__init__.py                     导出 complete() / 模型表查询
api/src/infra/llm/base.py                         L1 协议：五个 dataclass
api/src/infra/llm/client.py                       OpenAI 兼容客户端
api/src/infra/llm/providers/openai_compatible.py   LLMSettings + 模型表（base_url/api_key 可配）
api/src/infra/llm/providers/<name>.py              （真要并存第二家时再加，不改已有文件）
api/src/infra/http_errors.py             ← W1：从 kit/errors.py 下沉
api/src/infra/credentials.py             ← W1：从 kit/credentials.py 下沉
api/src/modules/model_driver/kit/errors.py      ← W1：变 re-export
api/src/modules/model_driver/kit/credentials.py ← W1：变 re-export
api/tests/test_llm_client.py             ← W3：G1–G4 守卫
```

**消费方长这样**（将来接业务场景时）：

```python
# modules/template_pack/xxx.py —— 直接 import，不需要注入口
from infra.llm import complete, LlmMessage, LlmRequest

text = await complete(LlmRequest(model="bailian/qwen-plus", messages=(LlmMessage("user", prompt),)))
```

---

## 10. 待拍板（不阻塞开工）

| 项 | 建议 | 说明 |
|---|---|---|
| ~~A. `LLM_API_KEY` 是否独立于 `BAILIAN_API_KEY`~~ | 已定：独立 | 实施改为通用 `LLM_API_KEY`，不绑百炼；填任意 OpenAI 兼容端点的 key。V1 `prod_required_fields` 留空（见 C） |
| ~~B. V1 要不要人工试的入口~~ | 已定：`app llm-check` 独立命令 | 不改 doctor（它不发外网请求）；README 的「23 项」无需动 |
| **C. 生产是否把 LLM 密钥列为启动必填** | 否（V1） | V1 没有业务场景，生产不配也应当能起；等真接业务再列入 `prod_required_fields` |

---

## 11. 与本轮其它待办的边界

本方案**不涉及**已有的「默认描述允许引用必填/固定槽位」修复（另一条线，见主会话待办）。两者无依赖。

---

## 12. 实施时的注意事项

1. **不跑全量测试 / CI**（除非单独要求）。照 `api/scripts/select_tests.py` 定级定向用例；每次改动照跑轻量公共面：`test_architecture` + `test_internal_models` + `test_openapi_contract` + `ruff check src tests` + `mypy --platform win32 src`。
2. **W1 是纯搬家**，验收标准是「行为零变化」——不要顺手改任何驱动的错误文案或状态判定。
3. **判别力自检照做**：守卫写完要「还原改动后守卫必须变红」，否则那条守卫可能一直在空转。
4. **推远端前必须征求同意**；可本地提交。
5. 新增配置项同步：`infra/driver_config.py` → `api/.env.example` → README 部署清单（`tests/test_config_guard.py` 双向对齐）。
