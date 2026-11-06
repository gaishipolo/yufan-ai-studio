"""跨模块注入口（port）目录。

## 为什么需要这个文件

架构测试只规定了「业务模块不许互相 import」，但没规定**那该怎么做**。
结果是每遇到一次跨模块查询就现场开一个文件：上一次是 `api/authn.py`（拿当前用户），
这一次是「查账号是否停用」（先写成 `api/accounts.py`，后因 `generation` 也要用而下移到
`core/ports/accounts.py`）。文件开出来了，但没人知道：

- 一共有几个注入口？
- 谁该实现它？实现没接上是什么行为？
- 新写一个注入口要满足什么形状？

本文件把这三件事变成**可被机器检查的清单**：`tests/test_architecture.py` 会扫描
`src/api/*.py`、`src/core/*.py` 与 `src/core/ports/*.py`，与 `PORT_CATALOG` 对比，
漏登记就直接红。清单之外的注入口 = 不允许存在。

## 注入口的形状（新增时照抄）

```python
class XxxPort(Protocol):            # 1. 协议：只放消费方真正需要的方法
    async def query(...) -> ...: ...

_port: XxxPort | None = None        # 2. 模块级单例

def register_xxx(port: XxxPort) -> None:   # 3. 注册：重复注册直接报错（权威来源只能有一个）
def reset_xxx() -> None:                   # 4. 复位：仅测试用
def has_xxx() -> bool:                     # 5. 是否已接上：供 CLI / 启动日志 / 测试判断
async def ask_xxx(...) -> ...:             # 6. 消费函数：**未注册时必须返回中性值**，不许抛
```

两种等价形状都收：具名 `Protocol` 子类 + `register_*`（`api.authn`、
`core.ports.accounts`），或 `Callable` 类型别名 + `set_*`（`core.audit`）。
守卫按「注册 / 复位 / 是否已接上」这三个动作识别，不依赖协议类的具体写法。

第 6 条是硬要求：注入口的意义是「可选依赖」。未注册就抛异常，等于把可选依赖
变成必选依赖 —— 删掉 identity 模块整个系统就起不来，那还不如直接 import。

## 什么该做成注入口

- 消费方属于低层（`generation/`、`workers/`、CLI），而答案在业务模块手里；
- 同一份能力将来可能有第二个来源（本地账号 / SSO / 企业微信）；
- 没有请求上下文的场景（后台任务、子进程）也要能问。

**不该**做成注入口的：纯数据查询（那就把数据下移到 `db/` 模型或 DAO），
以及只有一个调用点、且永远只可能有一个实现的东西 —— 直接传参更诚实。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from api.authn import has_principal_resolver
from core.audit import has_audit_sink
from core.feed import has_replacement_feed
from core.ports.accounts import has_account_status_checker
from core.ports.assets import has_asset_reference_checker

__all__ = ["PORT_CATALOG", "PortSpec", "port_status"]


@dataclass(frozen=True, slots=True)
class PortSpec:
    """一个注入口的元信息。"""

    name: str
    """短横线命名，出现在 `app modules` 与启动日志里。"""

    module: str
    """定义它的模块，例如 `api.authn`。"""

    protocol: str
    """协议类名，必须在该模块里存在；留空表示用 `Callable` 类型别名（无具名协议类）。"""

    implementer: str
    """**谁应该**实现它（约定，不是强制）。出现第二个实现时要先改这里再改代码。"""

    when_unregistered: str
    """没接上实现时的行为。必须是无害的中性行为，不能是报错。"""

    is_registered: Callable[[], bool]


PORT_CATALOG: tuple[PortSpec, ...] = (
    PortSpec(
        name="principal-resolver",
        module="api.authn",
        protocol="PrincipalResolver",
        implementer="任意身份来源：modules/identity 的本地账号、SSO、API Key",
        when_unregistered="原样返回令牌里的主体，与未接入真实账号时行为一致",
        is_registered=has_principal_resolver,
    ),
    PortSpec(
        name="account-status",
        module="core.ports.accounts",
        protocol="AccountStatusChecker",
        implementer="拥有账号体系的模块（当前是 modules/identity）",
        when_unregistered="返回 None，调用方按「放行」处理（只关掉离职后继续花钱，"
        "不给历史档案补会误伤的闸门）",
        is_registered=has_account_status_checker,
    ),
    PortSpec(
        name="audit-sink",
        module="core.audit",
        protocol="",  # AuditSink 是 Callable 别名，不是 Protocol 子类
        implementer="db/audit_sink.py（app.load_extension_points 在 import 期注册）",
        when_unregistered="record() 静默跳过：留痕是可选依赖，不能拖垮业务；"
        "但生产环境启动自检会直接报错",
        is_registered=has_audit_sink,
    ),
    PortSpec(
        name="asset-references",
        module="core.ports.assets",
        protocol="AssetReferenceChecker",
        implementer="modules/task（`video_tasks.reference_asset_ids`，导入模块时自注册），"
        "将来把素材 id 写进自己表的模块（分镜 / 镜头）接上第二个来源时要先改这里",
        when_unregistered="返回空映射，删除一律放行。默认「可能有引用就拒绝」"
        "会让删除功能在实现接上之前完全不可用，而用户只看到「删不掉」",
        is_registered=has_asset_reference_checker,
    ),
    PortSpec(
        name="replacement-feed",
        module="core.feed",
        protocol="",  # 类型是 Callable 别名，没有具名协议类
        implementer="modules/template（导入模块时自注册 replacement_feed 读取器）",
        when_unregistered="任务中心看到空列表：替换档案不计入待办与统计，"
        "但页面本身仍可用",
        is_registered=has_replacement_feed,
    ),
)


def port_status() -> list[tuple[PortSpec, bool]]:
    """当前进程内每个注入口是否已接上实现。"""
    return [(spec, spec.is_registered()) for spec in PORT_CATALOG]
