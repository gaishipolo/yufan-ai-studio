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

**唯一例外：判定结果会放行不可逆操作的注入口**（`core.ports.library_references` 的
引用检查）。那里「中性值 = 没人引用 = 可以删」，一次漏注册就是永久丢数据，所以必须
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
from core.ports.export_sources import export_sources
from core.ports.generation_feed import has_generation_feed
from core.ports.library_ingest import has_library_ingest
from core.ports.library_lookup import has_library_lookup
from core.ports.library_references import library_references_ready
from core.ports.model_generation import has_model_generation
from core.ports.org_directory import has_org_directory
from core.ports.template_catalog import has_template_catalog
from core.ports.template_runtime import has_template_runtime

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
        name="generation-feed",
        module="core.ports.generation_feed",
        protocol="",  # 类型是 Callable 别名，没有具名协议类
        implementer="modules/image 的 `feed_provider.read_generation_feed`（读 "
        "`image_jobs` 表，筛选/统计/现签 URL 都在来源方完成）",
        when_unregistered="任务中心不显示生图记录：统计与列表里都不计入这一路，"
        "视频与替换档案照常显示。只跑任务模块的单测就是这个状态",
        is_registered=has_generation_feed,
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
    # ---- 资产库 v2：三个注入口刻意与旧 asset 的注入口分开 ----
    # 并存期两套库同时对在线，共用注入口会让「问的是哪一边」变成隐式行为。
    PortSpec(
        name="org-directory",
        module="core.ports.org_directory",
        protocol="OrgDirectory",
        implementer="拥有组织模型的模块（当前是 modules/identity 的 user_groups / group_members）",
        when_unregistered="返回空集：小组素材库为空、组名显示为空串。"
        "这是中性方向——少看见不会多看见；抛异常会让「删掉身份模块」变成"
        "「资产库起不来」",
        is_registered=has_org_directory,
    ),
    PortSpec(
        name="library-lookup",
        module="core.ports.library_lookup",
        protocol="LibraryLookup",
        implementer="modules/library（v2 资产库，import 期注册 `SqlLibraryLookup`）",
        when_unregistered="fail-closed（见本文件开头的例外说明）：抛 503，不返回空映射。"
        "退化成「查不到 = 不存在」会把装配故障报成用户数据问题",
        is_registered=has_library_lookup,
    ),
    PortSpec(
        name="library-ingest",
        module="core.ports.library_ingest",
        protocol="LibraryIngest",
        implementer="modules/library（`ingest_generated()`，生成侧只能经它落草稿箱）",
        when_unregistered="fail-closed：抛 503。生成成功但没入库是最难发现的一类问题，"
        "宁可让生成侧显式失败一次",
        is_registered=has_library_ingest,
    ),
    PortSpec(
        name="library-references",
        module="core.ports.library_references",
        protocol="LibraryReferenceChecker",
        implementer="modules/library（v2 封装成员、下载任务）、modules/image"
        "（参考图快照，purge 后重试读不到字节）、modules/task"
        "（`video_tasks.reference_library_ids`）",
        when_unregistered="fail-closed：抛 503，v2 资产一律不可彻底删除。"
        "「查不到 = 没人引用」在这里等于静默放行不可逆删除",
        # 判「声明 ⊆ 实现」而不是「有没有人注册」：注册了却漏声明时，某个来源会
        # 被静默忽略，删除照样放行——这正是已下线的 v1 `asset-references` 的教训。
        is_registered=library_references_ready,
    ),
    PortSpec(
        name="model-generation",
        module="core.ports.model_generation",
        protocol="ModelGeneration",
        implementer="modules/model_driver（统一生成门面，import 期注册）",
        when_unregistered="fail-closed：抛 503。退化成「空模型列表」会把装配故障"
        "报成用户没配模型，是最难排查的一类问题",
        is_registered=has_model_generation,
    ),
    PortSpec(
        name="template-catalog",
        module="core.ports.template_catalog",
        protocol="TemplateCatalogPort",
        implementer="modules/template_pack 的 `catalog_port_impl`（读已发布的 "
        "manifest / packs / functions，组装成 core 层不可变快照）",
        when_unregistered="返回空结果：库里还没有已发布封装是合法状态，"
        "抛异常会把「模块没装配」和「用户没配置」混成一件事。"
        "只跑消费方单测时就是这个状态",
        is_registered=has_template_catalog,
    ),
    PortSpec(
        name="template-runtime",
        module="core.ports.template_runtime",
        protocol="TemplateRuntimePort",
        implementer="现在由 modules/template 的 `runtime_port_impl` 注册（读它自己的 "
        "template_packs / _versions / video_templates）；C2 把这三张表搬进 "
        "modules/template_pack 后，注册点跟着搬，消费方不改",
        when_unregistered="fail-closed：抛 RegistryError（500）。替换执行链拿不到"
        "冻结版本与模板原文就根本准备不了任务，退化成「封装不存在」的 400 会把"
        "装配故障说成用户选错了包。与 template-catalog 的「空结果」刻意相反",
        is_registered=has_template_runtime,
    ),
)


def port_status() -> list[tuple[PortSpec, bool]]:
    """当前进程内每个注入口是否已接上实现。"""
    return [(spec, spec.is_registered()) for spec in PORT_CATALOG]
