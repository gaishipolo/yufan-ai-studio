"""权限求值：**唯一**的判定入口。

## 为什么单独一个模块

`core.permissions` 管目录（有哪些码），`core.pages` 管页面（哪一页管哪些码），
本模块管**求值**（这个人最终能用哪些码）。三者互相引用会成环：
`pages` 要用 `permissions.PermissionSpec`，而求值要同时用两者。

于是求值放在这里，只被 `identity.service` 引用 —— 判定链上只有一处，
任何地方再拼一遍就是第二份真相。

## 求值顺序（每一步的位置都有理由）

```
granted   = 团队 ∪ 小组 ∪ 角色 ∪ 个人
if 全量(*)        → 全集（不进下面任何一步）
expanded  = 摊开 资源:*
allowed   = expanded − 个人否决
effective = 页面门控(allowed)
```

- **摊开在减法之前**：否则角色里的 `task:*` 会把刚被否决的 `task:delete` 又摊回来，
  症状是「关了但没关」，而库里那条否决记录看起来完全正常。
- **减法在门控之前**：否决的对象可以是**页面**，而「进不去那一页」是靠门控把它管的
  接口一并关掉实现的。顺序反了，页面被否决、接口还开着。
- **全量在最前面**：`*` 等于全集，不给它留「可以被逐条削减」的口子 ——
  否则「他到底是不是全量」会变成要算出来的事。

## 否决只出现在个人层

不是实现限制，是刻意的：只有最具体的一层能否决，**加新层（部门 / 外协）时才不用
重新回答「谁压谁」**。规则一句话，不随层数增长。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from core.context import SUPER_PERMISSION, permission_holds
from core.pages import page_catalog
from core.permissions import expand_grants


@dataclass(frozen=True, slots=True)
class Evaluation:
    """一次求值的完整结果。

    `denied_off` 与 `gated_off` **互斥**：一个码不生效只有两种原因 ——
    被本人否决，或它所属的页面一页都没开。混在一起，界面就只能说
    「你有这个权限但用不了」，而管理员需要知道该去改哪一处。
    """

    #: 摊开之后、减否决之前的集合。`sources` 的键就是它 —— 界面上每一行
    #: 都来自这里，所以「授权了但没生效」的码也有来源可查。
    expanded: frozenset[str]
    effective: frozenset[str]
    denied_off: tuple[str, ...]
    gated_off: tuple[str, ...]
    pages: tuple[str, ...]


def _full(pages: tuple[str, ...]) -> Evaluation:
    return Evaluation(
        expanded=frozenset({SUPER_PERMISSION}),
        effective=frozenset({SUPER_PERMISSION}),
        denied_off=(),
        gated_off=(),
        pages=pages,
    )


def evaluate(
    granted: Iterable[str],
    denies: Iterable[str] = (),
    *,
    is_superuser: bool = False,
) -> Evaluation:
    """把「授予」和「否决」算成最终权限。

    纯函数（只读全局目录，不碰数据库），因此判定链的正确性可以用普通单测覆盖，
    不需要起服务。
    """
    all_pages = tuple(spec.code for spec in page_catalog.all())
    if is_superuser:
        return _full(all_pages)

    denied = frozenset(denies)
    expanded = frozenset(expand_grants(granted))
    if SUPER_PERMISSION in expanded:
        # 通过授权路径拿到 `*`（存量数据，或别人显式授出）。语义等同超管：
        # 全量，且不参与否决 —— 写入口已经拒绝给这种账号配例外，所以这里
        # 不会与「否决了却不生效」并存。
        return _full(all_pages)

    allowed = expanded - denied
    effective = frozenset(page_catalog.gate(allowed))

    denied_off: list[str] = []
    gated_off: list[str] = []
    for code in sorted(expanded - effective):
        owners = page_catalog.pages_for(code)
        # 页面被否决时，它管的接口是被门控砍掉的 —— 但对用户来说原因是
        # 「你自己把这页关了」，所以归到 deniedOff，不归 gatedOff。
        if code in denied or any(page in denied for page in owners):
            denied_off.append(code)
        else:
            gated_off.append(code)

    pages = tuple(page for page in all_pages if permission_holds(allowed, page))
    return Evaluation(
        expanded=expanded,
        effective=effective,
        denied_off=tuple(denied_off),
        gated_off=tuple(gated_off),
        pages=pages,
    )
