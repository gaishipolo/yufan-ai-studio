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

内部实现一律走 `core.ports.host.Port`，不要再手写模块级字典：

```python
_sink: Port[AuditSink] = Port("audit-sink", multi=False)   # 内部：宿主负责重名/复位

def set_audit_sink(sink: AuditSink) -> AuditSink:         # 1. 注册：模块对外仍暴露
    return _sink.provide(sink, replace=True)              #    （PORT_CATALOG 要用）

def reset_audit_sink() -> None:                           # 2. 复位：仅测试用
    _sink.reset()

def has_audit_sink() -> bool:                             # 3. 是否已接上：CLI / 启动日志
    return _sink.has()

async def record(...) -> None:                            # 4. 消费函数：**未注册时必须
    ...                                                   #    返回中性值**，不许抛
```

`Port(multi=False)` 是「权威来源只能有一个」，重复注册直接报错；
`Port(multi=True)` 是「多模块各贡献一份」，此时 `provide()` 必须带 `key`
（`export-sources` 就是这么装的）。两种形态的差别只在基数，库逻辑只有一份。

第 4 条是硬要求：注入口的意义是「可选依赖」。未注册就抛异常，等于把可选依赖
变成必选依赖 —— 删掉 identity 模块整个系统就起不来，那还不如直接 import。

**`set_*` vs `register_*` 是两种语义，别混用**：`set_*`（配 `replace=True`）是
「后写覆盖」，本来就有权威来源会变（`core.audit`、`core.feed`）；`register_*`
是「不许两个实现抢同一个位置」（`api.authn`、`core.ports.accounts`）。改造前这
两种语义在五个文件里各写各的，谁也没发现它们不一致。

**唯一例外：判定结果会放行不可逆操作的注入口**（`core.ports.assets` 的引用检查）。
那里「中性值 = 没人引用 = 可以删」，一次漏注册就是永久丢数据，所以必须
fail-closed：来源不齐抛 `Unavailable`(503)，宁可暂时删不掉。新增这类注入口时
要在 `PORT_CATALOG` 的 `when_unregistered` 里写明这是刻意的，别照抄「返回中性值」。

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
from core.ports.assets import reference_source_gaps
from core.ports.export_sources import export_sources
from core.ports.media import has_asset_lookup
from core.ports.subjects import has_subject_lookup

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
        implementer="由 `ModuleSpec.asset_reference_sources` 声明。当前实现者："
        "modules/task（`video_tasks.reference_asset_ids`）、"
        "modules/template（`template_pack_references.asset_id`，只算封装 head，"
        "已发布的历史版本不算引用）",
        when_unregistered="fail-closed（见本文件开头的例外说明）：来源不齐抛 503，"
        "删除一律拒绝。这里返回中性值等于静默放行不可逆删除",
        is_registered=lambda: not reference_source_gaps(),
    ),
    PortSpec(
        name="asset-lookup",
        module="core.ports.media",
        protocol="AssetLookup",
        implementer="modules/asset 的 `lookup.SqlAssetLookup`（模块 import 期 "
        "`install()` 注册）。与 `asset-references` 分工：那个回答「谁还在引用它」，"
        "这个回答「它本身是什么」",
        when_unregistered="fail-closed（见本文件开头的例外说明）：抛 503，不返回 "
        "空映射。这里退化成「查不到就算素材不存在」会把配置故障报成用户数据问题",
        is_registered=has_asset_lookup,
    ),
    PortSpec(
        name="subject-lookup",
        module="core.ports.subjects",
        protocol="SubjectLookup",
        implementer="modules/asset 的 `subject_lookup.SqlSubjectLookup`（import 期 "
        "`install()` 注册）。回答「这一版由哪几张图组成、什么顺序、有哪些能力」"
        "——`asset-lookup` 回答的是单张素材的事实，这个是组合的事实",
        when_unregistered="fail-open 且**回落旧路径**：返回 `None`（不是空元组），"
        "模板侧据此读 `template_pack_versions.reference_ids`，结果与改造前逐字节一致。"
        "返回空元组会被当成「这个包确实没有图」，生成结果少参考而无人察觉",
        is_registered=has_subject_lookup,
    ),
    PortSpec(
        name="replacement-feed",
        module="core.feed",
        protocol="",  # 类型是 Callable 别名，没有具名协议类
        implementer="modules/template 的 `feed_provider.read_feed`，读 "
        "`replacement_runs` 表（不再扫归档盘上的指针）",
        when_unregistered="任务中心看到空列表：替换档案不计入待办与统计，"
        "但页面本身仍可用",
        is_registered=has_replacement_feed,
    ),
    PortSpec(
        name="export-sources",
        module="core.ports.export_sources",
        protocol="ExportSource",
        implementer="任何拥有可导出表的业务模块（当前 modules/task 注册 `tasks`）。"
        "框架表（audit_events）由 modules/export 自己实现，不走这里",
        when_unregistered="导出功能仍可用，只是可选数据源为空："
        "`GET /exports/sources` 只剩内置的 audit_events，创建别的源返回 400",
        is_registered=lambda: bool(export_sources()),
    ),
)


def port_status() -> list[tuple[PortSpec, bool]]:
    """当前进程内每个注入口是否已接上实现。"""
    return [(spec, spec.is_registered()) for spec in PORT_CATALOG]
