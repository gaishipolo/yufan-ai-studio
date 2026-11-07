"""写路由的限流策略：谁限、谁免、凭什么。

## 为什么需要一份策略表

`Depends(rate_limit(...))` 是「挂上去才生效」的东西，**漏挂不会报错**：接口照常
工作，只是没有任何上限。这种漏法在 review 里几乎看不出来——是少了一行，不是多了
一行，diff 里没有它的位置。上个模块上线时 `POST /tasks` 挂了限流，其余二十多个写
接口全凭自觉，靠人记是不可能的。

这里把「限不限流」变成可检查的声明，二选一：

1. 路由上挂了 `rate_limit(...)`（依赖树里有标记，见 `declared_scope`）；
2. 在 `EXEMPT_WRITE_ROUTES` 里写一行**理由**。

两条都不满足，`tests/test_route_policy.py` 直接红，并把该补的那一行打出来。
条目失效（路由改名/删除）同样会红——否则这张表会慢慢变成历史遗迹，下一个人照着
它判断「这里已经有人想过了」，而想的那件事早就不存在了。

## 为什么不是「中间件自动给写接口限流」

按 IP 的全局兜底已经存在（`RATE_LIMIT_GLOBAL_PER_MIN`，默认关，见 T27），但它和
这里的 per-user 额度是两件事：全局兜底防「一扫一大片」，这里防「一个账号把队列
或供应商额度打满」。自动挂依赖会让额度变成说不清的隐式值，还要在中间件里猜身份；
显式声明则把「这个接口值多少额度」留在接口旁边，改的人一眼能看到。

## 豁免不等于没保护

表里每一条都写清了它靠什么兜底（登录失败节流、幂等、权限点、配额）。写「无」
的条目是不允许的——那只是把问题挪进了表格里。
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Final

from fastapi.routing import APIRoute

# 会被限流策略检查的方法。GET/HEAD/OPTIONS 不计：读接口由缓存与分页兜底，
# DELETE 以外还有大量天然幂等的读，逐条挂限流只会把噪声堆到路由上。
WRITE_METHODS: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# 依赖函数上的标记名。用属性而不是「函数名匹配」：
# `rate_limit()` 每次返回一个新闭包，名字靠 `__name__` 猜会在重构后静默失效。
SCOPE_ATTR: Final = "__rate_limit_scope__"


def mark_rate_limited(dependency: Any, scope: str) -> Any:
    """给 `rate_limit()` 产出的依赖打标记（`api/deps.py` 调用）。"""
    setattr(dependency, SCOPE_ATTR, scope)
    return dependency


def declared_scope(route: APIRoute) -> str | None:
    """在依赖树里找 `rate_limit(...)` 的标记，找到就返回它的 scope。

    必须遍历整棵树：`rate_limit` 可以挂在路由上、挂在整个 router 的
    `dependencies=` 上，也可以被别的依赖包一层。只看 `route.dependant.call`
    会漏掉后两种，于是「明明限了却报没限」，逼人写一条假的豁免。
    """
    stack = [route.dependant]
    while stack:
        dependant = stack.pop()
        scope = getattr(getattr(dependant, "call", None), SCOPE_ATTR, None)
        if isinstance(scope, str) and scope:
            return scope
        stack.extend(getattr(dependant, "dependencies", ()) or ())
    return None


# --------------------------------------------------------------------------- #
# 豁免清单
# --------------------------------------------------------------------------- #
# 键是 `方法 路径`，路径**不含** `settings.api_prefix`（改前缀不会让这张表失效）。
# 值必须写清「靠什么兜底」，不接受「暂无限流」这种答案。
EXEMPT_WRITE_ROUTES: Mapping[str, str] = {
    # ---- 身份：登录态相关，已有更贴合语义的闸门 ----
    "POST /identity/login": (
        "匿名接口，`rate_limit()` 依赖 ActorDep 用不了；失败限流在 "
        "`modules/identity/throttle.py`（用户名|IP 滑动窗口 + 渐进延迟 + 429）"
    ),
    "POST /identity/refresh": (
        "匿名（凭刷新令牌续期），ActorDep 不可用；令牌是 256 位随机值，爆破不成立，"
        "泄漏靠轮换与 `/me/sessions` 吊销处理"
    ),
    "POST /identity/logout": "幂等收尾：调用多少次结果相同，且刻意不要求令牌有效",
    "PATCH /identity/me": "改自己的昵称/头像，低频轻量，且受 `identity:update` 自身范围约束",
    "POST /identity/me/password": (
        "自助改密：`service.change_password` 用 `pw:<user_id>` 做失败计数并通过 "
        "`password_slot()` 串行化，两次 scrypt 的成本已被闸门挡住"
    ),
    "DELETE /identity/me/sessions/{session_id}": "会话自助管理，写一行状态，低频",
    "DELETE /identity/me/sessions": "同上（退出其他设备），一次批量吊销，低频",
    # ---- 身份：管理动作，权限点已经把它限制在管理员手里 ----
    "POST /identity/users": "管理员建号：需要 `identity:create`，低频且天然受限",
    "PATCH /identity/users/{user_id}": "管理员编辑：需要 `identity:update`，低频",
    "PUT /identity/users/{user_id}/roles": "管理员授权：需要 `identity:role:manage`，低频",
    "POST /identity/users/{user_id}/reset-password": (
        "管理员重置口令：需要 `identity:update`，且目标权限不得高于自己"
    ),
    "POST /identity/users/{user_id}/revoke-sessions": "管理员踢下线：需要 `identity:update`，低频",
    "DELETE /identity/users/{user_id}": "停用账号：需要 `identity:delete`，低频",
    "POST /identity/roles": "角色管理：需要 `identity:role:manage`，低频",
    "PATCH /identity/roles/{role_id}": "角色管理：需要 `identity:role:manage`，低频",
    "PUT /identity/roles/{role_id}/permissions": "角色管理：需要 `identity:role:manage`，低频",
    "DELETE /identity/roles/{role_id}": "角色管理：需要 `identity:role:manage`，低频",
    # ---- 素材归档：单条记录的状态位 ----
    "PUT /assets/archive/{asset_id}/name": "只改自己那条记录的名字，单行更新",
    "PUT /assets/archive/{asset_id}/sharing": "分享开关，单行更新，进入公共原片池由权限点管",
    "PUT /assets/archive/{asset_id}/deleted": "回收站开关，单行更新，可逆",
    "DELETE /assets/archive/{asset_id}": (
        "彻底删除单条：前置要求记录已在回收站（二次确认），且受 `asset:delete` 限制"
    ),
    "DELETE /assets/{asset_id}": (
        "单条删除，前置有引用检查（被引用直接 409）与存储配额，实际删除量受数据约束"
    ),
    # ---- 任务 ----
    "PATCH /tasks/{task_id}": "改标题/收藏标记，单行更新，无外部调用",
    "POST /tasks/{task_id}/favorite": "幂等开关，单行更新，无外部调用",
    # ---- 替换模板：贵的在 execute 上，这里都是元数据 ----
    "POST /templates/replacement-runs": (
        "建草稿：只写元数据，不提交供应商（真正的提交由 `/execute` 限流）"
    ),
    "POST /templates/replacement-runs/{run_id}/reconfirm": (
        "只翻一个确认位；真正重提由 `/execute` 限流，这里加限流反而会挡住正常补救"
    ),
    "POST /templates/replacement-runs/{run_id}/restore": (
        "从回收站恢复单条记录，幂等（重复调用结果相同）"
    ),
    "DELETE /templates/replacement-runs/{run_id}": "软删单条（进回收站），不取消生成",
    "PUT /templates/replacement-runs/{run_id}/review": (
        "审核结论落库，单行更新，需要 `template:review`"
    ),
    "PUT /templates/replacement-packs/draft": (
        "保存整包草稿，整包覆盖写，需要 `template:pack:manage`"
    ),
    "POST /templates/replacement-packs/{pack_id}/publish": (
        "发布整包，需要 `template:pack:publish`，低频管理动作"
    ),
    "POST /templates/replacement-packs/{pack_id}/disable": "下架整包，同为低频管理动作",
}


def _relative(path: str, prefix: str) -> str:
    """去掉 `api_prefix`，让豁免表与部署前缀解耦。"""
    if prefix and (path == prefix or path.startswith(f"{prefix}/")):
        return path[len(prefix) :] or "/"
    return path


@dataclass(frozen=True, slots=True)
class WriteRoute:
    method: str
    path: str
    scope: str | None


def _included(route: Any) -> Any:
    """判断是不是 FastAPI 的「延迟包含」包装（不同版本形态不同）。

    0.162 起 `app.include_router()` 不再把子路由摊平进 `app.routes`，而是放一个
    `_IncludedRouter` 包着原 router 和 include 前缀。只看 `app.routes` 会以为
    整个应用只有两三条路由——**守卫会变成永远通过的空检查**，比没有守卫更糟。
    所以这里显式判型，并且不依赖私有类名。
    """
    return route if hasattr(route, "original_router") else None


def iter_api_routes(routes: Iterable[Any], prefix: str = "") -> Iterator[tuple[APIRoute, str]]:
    """递归展开被 include 的 router，产出 `(路由, 完整路径)`。"""
    for route in routes:
        wrapped = _included(route)
        if wrapped is not None:
            context = getattr(wrapped, "include_context", None)
            extra = getattr(context, "prefix", "") or ""
            yield from iter_api_routes(wrapped.original_router.routes, prefix + extra)
        elif isinstance(route, APIRoute):
            yield route, prefix + route.path


def iter_write_routes(routes: Iterable[Any], *, prefix: str = "") -> Iterator[WriteRoute]:
    """展开所有写方法路由，附上它声明的限流 scope（没声明就是 None）。"""
    for route, full_path in iter_api_routes(routes, prefix):
        methods = {m.upper() for m in (route.methods or ())}
        for method in sorted(methods & WRITE_METHODS):
            yield WriteRoute(method, full_path, declared_scope(route))


def policy_violations(
    routes: Iterable[Any], *, api_prefix: str = ""
) -> tuple[list[str], list[str]]:
    """返回 `(没有声明策略的写路由, 已失效的豁免条目)`，两个都该为空。

    `routes` 传 `app.routes`：include 前缀由 `iter_api_routes` 自己累加，
    这里不要再喂初始前缀（否则会出现 `/api/v1/api/v1/...` 这种双前缀）。
    `api_prefix` 只用于把结果归一成 `/identity/login` 这种与部署前缀无关的键。

    `stale` 也是错误而不是提示：条目失效意味着这张表正在提供过期信息，
    而它的全部价值就在于「被人信任」。
    """
    undeclared: list[str] = []
    seen: set[str] = set()
    for item in iter_write_routes(routes):
        key = f"{item.method} {_relative(item.path, api_prefix)}"
        seen.add(key)
        if item.scope is None and key not in EXEMPT_WRITE_ROUTES:
            undeclared.append(key)
    stale = sorted(set(EXEMPT_WRITE_ROUTES) - seen)
    return undeclared, stale


__all__ = [
    "EXEMPT_WRITE_ROUTES",
    "SCOPE_ATTR",
    "WRITE_METHODS",
    "WriteRoute",
    "declared_scope",
    "iter_api_routes",
    "iter_write_routes",
    "mark_rate_limited",
    "policy_violations",
]
