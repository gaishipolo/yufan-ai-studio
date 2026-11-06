"""操作留痕扩展点：谁在什么时候对什么做了什么。

## 为什么需要它（这是七维里最容易被忽略的一维）

生图上线后第一个问题是「这张图是谁提交的、用的哪个模型、花了多少张」——
接口日志里有请求，但没有**业务动作**。日志是给排障的，留痕是给对账与追责的：
两者的保留期、查询方式、能不能被业务改都不同，不能指望 `grep` 日志。

## 为什么做成注入点而不是直接写表

`core` 不能 import `db`（依赖方向：`generation → db / infra / core`），
所以这里只定义「记一条」的协议，真正的写入由 `db/audit_sink.py` 注册。
测试里也可以换成一个内存收集器，不必碰数据库。

## 两条硬规则

1. **审计失败绝不影响业务**：`record()` 吞掉异常只打日志。因为一次留痕写失败
   而让用户的重试按钮报错，比丢一条审计更糟；
2. **只记动作与参数，不记密钥**：`detail` 里不许塞 api_key、令牌、完整提示词之外的隐私。
   审计表是长期保留的，写进去就收不回来。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AuditEntry:
    action: str
    actor_id: str | None = None
    target_type: str = ""
    target_id: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))


AuditSink = Callable[[AuditEntry], Awaitable[None]]

_sink: AuditSink | None = None


def set_audit_sink(sink: AuditSink) -> None:
    """注册写入实现（由 `db/audit_sink.py` 在 import 期调用）。"""
    global _sink
    _sink = sink


def reset_audit_sink() -> None:
    """仅测试使用。"""
    global _sink
    _sink = None


def has_audit_sink() -> bool:
    return _sink is not None


def ensure_audit_sink(app_env: str) -> None:
    """启动自检：生产环境没接上写入端就炸，而不是静默丢光所有留痕。

    `record()` 按设计吞掉一切（审计失败不能拖垮业务），所以「没注册」这件事
    在 dev/test 里是允许的、无害的；但生产里它意味着对账时一张表都是空的，
    且日志里一句错都没有。抽成函数是为了让这条自检本身可被测试覆盖。
    """
    if app_env == "prod" and _sink is None:
        raise RuntimeError(
            "生产环境审计写入端未注册：确认 app.load_extension_points() 已被调用，"
            "且 db/audit_sink.py 能正常导入（其它环境允许静默跳过）"
        )


async def record(
    action: str,
    *,
    actor_id: str | None = None,
    target_type: str = "",
    target_id: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    """记一条操作留痕。没有注册写入端时静默跳过（可选依赖，不能拖垮业务）。"""
    if _sink is None:
        return
    entry = AuditEntry(
        action=action,
        actor_id=actor_id,
        target_type=target_type,
        target_id=target_id,
        detail=detail or {},
    )
    try:
        await _sink(entry)
    except Exception:  # noqa: BLE001 - 留痕失败不能影响业务结果
        logger.exception("审计写入失败 action=%s target=%s", action, target_id)


__all__ = [
    "AuditEntry",
    "AuditSink",
    "ensure_audit_sink",
    "has_audit_sink",
    "record",
    "reset_audit_sink",
    "set_audit_sink",
]
