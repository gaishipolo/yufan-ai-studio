"""框架级的键值表（`app_meta`）：**存状态，不存缓存**。

## 为什么需要它

有一类信息既不属于某个业务实体，也不能放配置：它是「上一次运行时系统认定的事实」。
第一个消费方是权限点目录的快照——见 `IdentityService.sync_new_permissions`：
启动时要把「新增的权限点」自动补进内置角色，但**不能**把管理员手工移除的旧权限
再塞回去。要区分「新」与「被删掉的」，就必须记住上一次看见过哪些。

放进程内存不行（多副本各记各的、重启就丢），放配置文件不行（它是运行期写出的），
放缓存也不行（缓存会过期，而这个事实不能过期）。所以一张两列表最合适。

## 为什么键是字符串而不是枚举

它刻意保持「无 schema」：写下 `permissions.catalog` 的人负责它的格式，读的人负责
解析。加一个键不需要迁移——这正是它相对「给每件事建一张表」的价值。
代价是没人管得住乱写，所以约定：**键带域名前缀**（`permissions.` / `flags.`），
值统一是 JSON（标量也 JSON 编码，读的时候不必猜类型）。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin, utcnow


class AppMeta(Base, TimestampMixin):
    __tablename__ = "app_meta"

    # 键就是主键：一个键只能有一个值，"最后写入者胜"是这张表唯一需要的并发语义
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="", nullable=False)


async def get_json(session: AsyncSession, key: str) -> Any | None:
    """读一个键并反序列化；键不存在返回 None（**不是** KeyError）。

    「没有这个键」和「值是 False」必须能区分，所以这里不用 `default=False` 那种写法。
    """
    raw = await session.scalar(select(AppMeta.value).where(AppMeta.key == key))
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        # 写坏的值当作不存在：调用方会按「首次运行」处理，比抛异常让整个启动失败安全
        return None


async def set_json(session: AsyncSession, key: str, value: Any) -> None:
    """写一个键（不 commit）。

    不 commit 是刻意的：调用方通常还有别的写入要在同一个事务里落地，
    这里自己提交会把「一次启动引导」拆成两个事务，中途失败就留下半份状态。
    """
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
    row = await session.get(AppMeta, key)
    if row is None:
        session.add(AppMeta(key=key, value=payload))
    else:
        row.value = payload
        row.updated_at = utcnow()
    await session.flush()


__all__ = ["AppMeta", "get_json", "set_json"]
