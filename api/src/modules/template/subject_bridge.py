"""模板取参考图的**双读**桥：旧包版本 vs 主体版本（M2.5-C）。

## 为什么需要一层桥，而不是直接改读主体

prepare 拿到的参考图决定了生成结果。少一张、顺序变一位，产出就会不同——而症状
只是「这次出来的图和上周不太一样」，没有栈、没有报错、日志里一行异常都没有。
这类故障唯一能在上线前发现的办法是**同时跑两条路再比对**，也就是这里做的事。

三段模式（`SUBJECT_LOOKUP_MODE`）：

| 模式 | 行为 | 用在哪 |
| --- | --- | --- |
| `off` | 只读旧包版本 | 主体注入口没装配、或要立刻退回旧路径 |
| `shadow` | 两条都读，**比对并记录**，仍返回旧路径结果 | 默认；观察差异率 |
| `on` | 读主体版本 | 差异率为 0 之后 |

## 为什么默认 `shadow` 而不是 `on`

迁移已经把 `template_pack_versions` 平移成 `subject_versions`，理论上两边逐条相等。
但"理论上"正是影子模式要验证的东西：生产里的旧数据可能有不连续 `position`、
指向已删素材的 id、或重复 id——这些在迁移时不会报错，只会在比对里显形。

## 差异只记 WARNING，不改行为

影子模式下返回的永远是旧路径结果，所以**任何差异都不会影响产出**。
把差异也一并返回/抛错会让"观察期"变成"故障期"。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.logging import get_logger
from core.ports.subjects import get_subject_lookup

__all__ = ["MODES", "resolve_reference_ids"]

logger = get_logger(__name__)

MODES = ("off", "shadow", "on")


async def resolve_reference_ids(
    session: AsyncSession,
    *,
    pack_id: str,
    version: int,
    fallback: Sequence[str],
) -> list[str]:
    """取这一版该用的参考图 id 列表。

    `fallback` 是旧路径的结果（`template_pack_versions.reference_ids`）。
    **任何异常情况都返回它**：模板拿不到主体时不该比现在更差。
    """
    mode = (settings.subject_lookup_mode or "shadow").strip().lower()
    if mode not in MODES:  # 配置写错时按 shadow 处理，不静默变成 on
        logger.warning("SUBJECT_LOOKUP_MODE 取值无效（%s），按 shadow 处理", mode)
        mode = "shadow"
    if mode == "off":
        return list(fallback)

    lookup = get_subject_lookup()
    if lookup is None:
        # 注入口没装配：这是装配问题，不是数据问题。记一次 info 就够，
        # 每次 prepare 都 WARNING 会把日志冲掉。
        logger.info("主体注入口未装配，参考图走旧包版本 pack=%s", pack_id)
        return list(fallback)

    snapshot = await lookup.snapshot(session, pack_id, version)
    if snapshot is None:
        # 没有映射或这一版不存在。`on` 模式下这是真的缺数据，要说清楚；
        # `shadow` 下属于正常范围（迁移之前创建的包）。
        level = logger.warning if mode == "on" else logger.debug
        level("主体里没有这一版，回落旧包版本 pack=%s v=%s", pack_id, version)
        return list(fallback)

    if mode == "shadow":
        old = list(fallback)
        new = list(snapshot.asset_ids)
        if old != new:
            # 用 `%s` 打全量而不是只打长度：差异通常是"顺序不同"或"少一个 id"，
            # 只给长度的话排查要重新跑一遍才知道差在哪。
            logger.warning(
                "主体与旧包版本不一致 pack=%s v=%s 旧=%s 新=%s",
                pack_id,
                version,
                old,
                new,
            )
        return old

    return list(snapshot.asset_ids)
