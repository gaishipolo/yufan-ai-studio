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

## 「写过」不等于「写对」

第一版只核了「这条路由有没有被想过」。可是这张表真正的风险不是漏写，是**写完之后
就不再有人看**：某个接口昨天只是改个名字，今天开始调供应商，理由里的「单行更新」
变成了假话，而表还是绿的——它提供的是一份看起来被审阅过的错误信息。

守卫能核的（确定性的，不猜）：

1. **理由里引用的东西必须真的存在**。文中反引号里的 `.py` 路径要能落到文件、
   `资源:动作` 形式的权限码要真的在权限目录里。文件被删、权限点被改名，
   那句话就成了谎言——这类腐烂只有对账能发现，review 看不出来。
2. **复核期限**。每条带 `review_by`，过期即红。写得太远（超过 `REVIEW_HORIZON_DAYS`）
   也红：那是拖延，不是判断。

守卫核不了的，写在这里免得下一个人以为它核了：**「这个接口现在值多少额度」**
是产品判断，只能靠人看指标（`docs/OPS.md` §3 的写路由 P95 与调用量）和读供应商
账单。守卫能做的是**按时把人叫回来**，不是替他做判断。
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Final

from fastapi.routing import APIRoute

# `iter_api_routes` 住在 `api/routing.py`：权限核对（`api/assembly.py`）也要用它，
# 放在这里会让「装配核对」依赖「限流策略」，方向不自然。
from api.routing import iter_api_routes

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
# 所有豁免的复核期限。写成一个常量而不是逐条写日期：这张表是一次**集体**判断，
# 半年后一起重新看一遍，比「各自记各自的日子」现实（没人会记得自己那条）。
# 到期那天 CI 会红，红的就是这张表本身。
NEAR_TERM = "2027-05-01"

# 复核期限离今天最远能有多远。留半年：比这更远的日期不是判断，是拖延。
REVIEW_HORIZON_DAYS = 180


@dataclass(frozen=True, slots=True)
class Exemption:
    """一条「这个写接口不需要限流」的判断。

    `why` 要能被后来者核实——引用的 `.py` 路径与权限码由守卫对账
    （`tests/test_route_policy.py::test_exempt_reasons_cite_real_code`）。
    `review_by` 是过期日：这张表最大的风险不是写错，是**一直没人再看**。
    """

    why: str
    review_by: str

    def deadline(self) -> date:
        """过期日。格式不对就让导入期直接炸——静默按「永不过期」处理最糟。"""
        return date.fromisoformat(self.review_by)


# 键是 `方法 路径`，路径**不含** `settings.api_prefix`（改前缀不会让这张表失效）。
# 值必须写清「靠什么兜底」，不接受「暂无限流」这种答案。
EXEMPT_WRITE_ROUTES: Mapping[str, Exemption] = {
    # ---- 身份：登录态相关，已有更贴合语义的闸门 ----
    "POST /identity/login": Exemption(
        why=(
            "匿名接口，`rate_limit()` 依赖 ActorDep 用不了；失败限流在 "
            "`modules/identity/throttle.py`（用户名|IP 滑动窗口 + 渐进延迟 "
            "+ 429）"
        ),
        review_by=NEAR_TERM,
    ),
    "POST /identity/refresh": Exemption(
        why=(
            "匿名（凭刷新令牌续期），ActorDep 不可用；令牌是 256 位随机值，"
            "爆破不成立，泄漏靠轮换与 `/me/sessions` 吊销处理"
        ),
        review_by=NEAR_TERM,
    ),
    "POST /identity/logout": Exemption(
        why="幂等收尾：调用多少次结果相同，且刻意不要求令牌有效",
        review_by=NEAR_TERM,
    ),
    "PATCH /identity/me": Exemption(
        why=(
            "改自己的昵称/头像，低频轻量，且受 `identity:update` 自身范围约"
            "束"
        ),
        review_by=NEAR_TERM,
    ),
    "POST /identity/me/password": Exemption(
        why=(
            "自助改密：`service.change_password` 用 `pw:<user_id>` 做失败计"
            "数并通过 `password_slot()` 串行化，两次 scrypt 的成本已被闸门"
            "挡住"
        ),
        review_by=NEAR_TERM,
    ),
    "DELETE /identity/me/sessions/{session_id}": Exemption(
        why="会话自助管理，写一行状态，低频",
        review_by=NEAR_TERM,
    ),
    "DELETE /identity/me/sessions": Exemption(
        why="同上（退出其他设备），一次批量吊销，低频",
        review_by=NEAR_TERM,
    ),
    # ---- 身份：管理动作，权限点已经把它限制在管理员手里 ----
    "POST /identity/users": Exemption(
        why="管理员建号：需要 `identity:create`，低频且天然受限",
        review_by=NEAR_TERM,
    ),
    "PATCH /identity/users/{user_id}": Exemption(
        why="管理员编辑：需要 `identity:update`，低频",
        review_by=NEAR_TERM,
    ),
    "PUT /identity/users/{user_id}/roles": Exemption(
        why="管理员授权：需要 `identity:role:manage`，低频",
        review_by=NEAR_TERM,
    ),
    "POST /identity/users/{user_id}/reset-password": Exemption(
        why="管理员重置口令：需要 `identity:update`，且目标权限不得高于自己",
        review_by=NEAR_TERM,
    ),
    "POST /identity/users/{user_id}/revoke-sessions": Exemption(
        why="管理员踢下线：需要 `identity:update`，低频",
        review_by=NEAR_TERM,
    ),
    "DELETE /identity/users/{user_id}": Exemption(
        why="停用账号：需要 `identity:delete`，低频",
        review_by=NEAR_TERM,
    ),
    "POST /identity/roles": Exemption(
        why="角色管理：需要 `identity:role:manage`，低频",
        review_by=NEAR_TERM,
    ),
    "PATCH /identity/roles/{role_id}": Exemption(
        why="角色管理：需要 `identity:role:manage`，低频",
        review_by=NEAR_TERM,
    ),
    "PUT /identity/roles/{role_id}/permissions": Exemption(
        why="角色管理：需要 `identity:role:manage`，低频",
        review_by=NEAR_TERM,
    ),
    "DELETE /identity/roles/{role_id}": Exemption(
        why="角色管理：需要 `identity:role:manage`，低频",
        review_by=NEAR_TERM,
    ),
    # ---- 素材归档：单条记录的状态位 ----
    "PUT /assets/{asset_id}/name": Exemption(
        why="只改自己那条记录的名字，单行更新",
        review_by=NEAR_TERM,
    ),
    "PUT /assets/{asset_id}/sharing": Exemption(
        why="分享开关，单行更新，进入公共原片池由权限点管",
        review_by=NEAR_TERM,
    ),
    "PUT /assets/{asset_id}/deleted": Exemption(
        why="回收站开关，单行更新，可逆",
        review_by=NEAR_TERM,
    ),
    "DELETE /assets/{asset_id}": Exemption(
        why=(
            "彻底删除单条：前置要求记录已在回收站（二次确认）、引用检查通过（被引用"
            "直接 409），且受 `asset:delete` 限制；实际删除量受数据约束。"
            "两套素材接口合并前这里是两条同名豁免，合并时必须并成一条，"
            "否则后写的那条会静默覆盖前一条的理由"
        ),
        review_by=NEAR_TERM,
    ),
    # ---- 任务 ----
    "PATCH /tasks/{task_id}": Exemption(
        why="改标题/收藏标记，单行更新，无外部调用",
        review_by=NEAR_TERM,
    ),
    "POST /tasks/{task_id}/favorite": Exemption(
        why="幂等开关，单行更新，无外部调用",
        review_by=NEAR_TERM,
    ),
    # ---- 替换模板：贵的在 execute 上，这里都是元数据 ----
    "POST /templates/replacement-runs": Exemption(
        why=(
            "建草稿：只写元数据，不提交供应商（真正的提交由 `/execute` 限流"
            "）"
        ),
        review_by=NEAR_TERM,
    ),
    "POST /templates/replacement-runs/{run_id}/reconfirm": Exemption(
        why=(
            "只翻一个确认位；真正重提由 `/execute` 限流，这里加限流反而会挡"
            "住正常补救"
        ),
        review_by=NEAR_TERM,
    ),
    "POST /templates/replacement-runs/{run_id}/restore": Exemption(
        why="从回收站恢复单条记录，幂等（重复调用结果相同）",
        review_by=NEAR_TERM,
    ),
    "DELETE /templates/replacement-runs/{run_id}": Exemption(
        why="软删单条（进回收站），不取消生成",
        review_by=NEAR_TERM,
    ),
    "PUT /templates/replacement-runs/{run_id}/review": Exemption(
        why="审核结论落库，单行更新，需要 `template:review`",
        review_by=NEAR_TERM,
    ),
    "PUT /templates/replacement-packs/draft": Exemption(
        why="保存整包草稿，整包覆盖写，需要 `template:pack:manage`",
        review_by=NEAR_TERM,
    ),
    "POST /templates/replacement-packs/{pack_id}/publish": Exemption(
        why="发布整包，需要 `template:pack:publish`，低频管理动作",
        review_by=NEAR_TERM,
    ),
    "POST /templates/replacement-packs/{pack_id}/disable": Exemption(
        why="下架整包，同为低频管理动作",
        review_by=NEAR_TERM,
    ),
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
    "NEAR_TERM",
    "REVIEW_HORIZON_DAYS",
    "SCOPE_ATTR",
    "WRITE_METHODS",
    "Exemption",
    "WriteRoute",
    "declared_scope",
    "iter_api_routes",
    "iter_write_routes",
    "mark_rate_limited",
    "policy_violations",
]
