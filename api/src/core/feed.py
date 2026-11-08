"""跨模块只读任务摘要与读取注入点；各业务模块仅依赖共享契约。

注册表放在 `core` 而不是 `api`：`modules` 只允许向下依赖，
`api` 只负责装配（导入模块即触发自注册）。

形状照 `api/ports.py` 的六条：注册 / 复位 / 是否已接上 / 消费。少了
`has_` 与 `reset_`，`app modules` 看不到它，未注册时的行为也没被声明过。
"""

from collections.abc import Callable

from core.ports.host import Port
from core.schema import CamelModel, UtcDateTime


class ReplacementFeedRecord(CamelModel):
    """任务中心需要的替换档案镜像，只含列表与统计字段。"""

    id: str
    kind: str
    target: str
    status: str
    created_at: UtcDateTime
    title: str
    source_name: str = ""
    progress: int = 0
    task_id: str | None = None
    output_url: str | None = None
    last_error: str | None = None
    deleted_at: UtcDateTime | None = None


#: 替换档案的读取注入口。单例：全局只有一个「档案来源」。
_feed: Port[Callable[[str], list[ReplacementFeedRecord]]] = Port(
    "replacement-feed", multi=False
)


def register_replacement_feed(reader: Callable[[str], list[ReplacementFeedRecord]]) -> None:
    # 与 `set_audit_sink` 同属「设置」语义：改造前就是后写覆盖，
    # 收紧成唯一会让测试里的换实现变成错误。
    _feed.provide(reader, replace=True)


def reset_replacement_feed() -> None:
    """仅测试用：清空注册，验证「未注册时返回空表」这条约定。"""
    _feed.reset()


def has_replacement_feed() -> bool:
    """是否已接上实现；`app modules` 与启动日志据此显示状态。"""
    return _feed.has()


def replacement_feed(owner: str) -> list[ReplacementFeedRecord]:
    """未注册（例如只跑任务模块单测）时返回空表，不抛异常。"""
    reader = _feed.single()
    return reader(owner) if reader else []
