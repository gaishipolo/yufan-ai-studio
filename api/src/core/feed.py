"""跨模块只读任务摘要与读取注入点；各业务模块仅依赖共享契约。

注册表放在 `core` 而不是 `api`：`modules` 只允许向下依赖，
`api` 只负责装配（导入模块即触发自注册）。

形状照 `api/ports.py` 的六条：注册 / 复位 / 是否已接上 / 消费。少了
`has_` 与 `reset_`，`app modules` 看不到它，未注册时的行为也没被声明过。
"""

from collections.abc import Awaitable, Callable

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
#:
#: **读端是 async**：上一版实现读归档盘上的 JSON（同步 IO），现在读数据库。
#: 签名从 `Callable[[str], list[...]]` 改成 awaitable 不是风格偏好——同步签名
#: 逼着调用方 `asyncio.to_thread`，而在线程里查异步连接池会拿到另一个事件循环，
#: 结果是一个只在生产并发下才出现的「attached to a different loop」。
_feed: Port[Callable[[str], Awaitable[list[ReplacementFeedRecord]]]] = Port(
    "replacement-feed", multi=False
)


def register_replacement_feed(
    reader: Callable[[str], Awaitable[list[ReplacementFeedRecord]]],
) -> None:
    """注册口**必须保持同步**：它只在 import 期调用一次。

    形参是 async reader，但注册动作不 await 任何东西。写成 `async def` 会让
    import 期的调用点变成「未 await 的协程」——注册悄悄不生效，而 `app modules`
    显示「未注册（任务中心看到空列表）」，能查很久。

    这里**不是**「设置语义」：名字是 `register_`，契约就是「只有一个档案来源」。
    之前用 `replace=True` 让它悄悄让位给后来者，症状是两个模块都注册时，任务中心
    的列表由 import 顺序决定——而 import 顺序没人负责。
    """
    _feed.provide(reader, owner="modules.template")



def reset_replacement_feed() -> None:
    """仅测试用：清空注册，验证「未注册时返回空表」这条约定。"""
    _feed.reset()


def has_replacement_feed() -> bool:
    """是否已接上实现；`app modules` 与启动日志据此显示状态。"""
    return _feed.has()


async def replacement_feed(owner: str) -> list[ReplacementFeedRecord]:
    """未注册（例如只跑任务模块单测）时返回空表，不抛异常。"""
    reader = _feed.single()
    return await reader(owner) if reader else []
