"""页面权限：把「前端能看见哪一页」与「后端准不准调这个接口」分成两层。

## 为什么要分开

在此之前，页面可见性和接口权限是**同一个码**：`task:read` 既决定侧栏有没有
「任务中心」，也决定 `GET /tasks` 是否 403。后果是两件本该独立的事被绑死了：

- 想让某人「能看任务，但先别让他进任务中心」—— 做不到，撤了 `task:read`
  他就连详情接口也用不了；
- 想关掉一个页面做灰度/下线 —— 只能靠前端删路由，而后端接口照旧全开。

所以这里引入**页面权限**（`page:<slug>`）作为独立的一层：

| 层 | 码 | 管什么 | 谁看 |
|---|---|---|---|
| 页面 | `page:task-center` | 侧栏/路由能不能进 | 前端路由守卫、菜单过滤 |
| 接口 | `task:read` / `task:delete` | 这个请求能不能发 | 后端 `require()` |

## 页面与接口怎么关联

每个页面在声明时列出它用到的接口权限（`requires`）。判定规则只有一条：

> **某个接口权限，只有在「至少一个声明它的页面被授予」时才有效。**
> 不被任何页面声明的接口（`/identity/me` 这类基础设施接口）不参与门控。

这样「禁用页面 ⇒ 该页面的接口自动禁用」成立，同时不会误伤共享接口：
`catalog:read` 同时被「生成工作台」和「生图」声明，只要还留着其中一页，
它依然是有效的 —— 否则关一个页面会把另一页也打残，而错误会出现在
一个**看起来无关**的接口上，极难排查。

## 为什么门控放在服务端而不是只放前端

前端路由守卫只能挡住「点进来」，挡不住「拿着令牌直接打接口」。而页面权限
本身也是一个普通权限点（走角色/小组/个人四层授予），所以把门控放在
`resolve_identity()` 里：**一次判定，令牌、`/me`、`effective-permissions`、
每个 `require()` 看到的都是同一个结果**。前端拿到的 `permissions` 里已经
包含页面码，菜单与路由据此工作，无需再算一遍。

## 超管旁路

`*` 直接放行所有页面与接口（`resolve_identity` 在门控之前就短路了）。
这不是漏洞而是必要：「新模块上线、页面还没配给任何角色」时，超管必须能进去
把它配好。门控只约束「被显式授予了部分页面」的账号。
"""

from __future__ import annotations

from dataclasses import dataclass

from core.context import permission_holds
from core.permissions import PermissionSpec
from core.registry import RegistryError

#: 页面权限码的固定前缀。写成常量是为了让守卫、文档、前端契约同源。
PAGE_PREFIX = "page:"


@dataclass(frozen=True, slots=True)
class PageSpec:
    """一个前端页面，以及它依赖的接口权限。

    `code` 是页面权限码（`page:<slug>`），授予它 = 这个人能看见这一页。
    `requires` 是这一页会调用的接口权限码；判定规则见模块文档（交集门控）。

    `requires` 刻意允许为空：占位页（如用量看板）还没有后端接口，
    但「能不能看见这一页」仍然是需要配置的。
    """

    code: str
    name: str
    route: str
    requires: tuple[str, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        if not self.code.startswith(PAGE_PREFIX):
            raise ValueError(
                f"页面权限码必须以 {PAGE_PREFIX!r} 开头，收到 {self.code!r}"
            )
        if len(self.code) == len(PAGE_PREFIX):
            raise ValueError("页面权限码不能只有前缀")
        if not self.route.startswith("/"):
            raise ValueError(f"页面路由必须以 / 开头，收到 {self.route!r}")
        for code in self.requires:
            if code.startswith(PAGE_PREFIX):
                raise ValueError(
                    f"页面 {self.code} 的 requires 里不能再嵌页面（{code!r}）："
                    "页面之间不建立依赖，否则会形成看不见的级联"
                )
            if code == "*":
                raise ValueError(
                    f"页面 {self.code} 的 requires 不许写 `*`：那等于把整站接口绑到一页上"
                )
            if ":" not in code:
                raise ValueError(f"接口权限码必须形如 '资源:动作'，收到 {code!r}")

    @property
    def permission(self) -> PermissionSpec:
        """页面权限码在**权限目录**里的样子。

        为什么要进同一个目录：不然管理员无处勾选它。授权体系（角色 / 小组 /
        团队 / 个人）只认 `PermissionSpec`，页面权限不该另起一套授权入口 ——
        两套入口意味着两套「谁能改」的规则，迟早对不上。

        描述里写清关联接口：管理员在角色编辑界面就能看到「关掉这一页会连带
        关掉哪些接口」，不必去翻代码。
        """
        if self.description:
            detail = self.description
        elif self.requires:
            detail = f"关联接口：{', '.join(self.requires)}"
        else:
            detail = "该页面暂无关联接口"
        return PermissionSpec(
            code=self.code,
            name=self.name,
            group="页面",
            description=detail,
        )


class PageCatalog:
    """全量页面目录。启动时由模块自动发现填充。

    与 `PermissionCatalog` 分开：两者的键空间不同（页面码 vs 接口码），
    混在一起会让「哪些码是页面」变成一个需要字符串前缀判断的隐式规则；
    而门控需要按「接口码 → 声明它的页面」反查，那是个页面目录才有的索引。
    """

    def __init__(self) -> None:
        self._specs: dict[str, PageSpec] = {}
        self._by_route: dict[str, str] = {}
        self._by_api: dict[str, tuple[str, ...]] = {}

    def declare(self, *specs: PageSpec) -> None:
        for spec in specs:
            existing = self._specs.get(spec.code)
            if existing is not None:
                if existing != spec:
                    raise RegistryError(
                        f"页面权限码冲突 {spec.code!r}: "
                        f"{existing.route} vs {spec.route}"
                    )
                continue
            if spec.route in self._by_route:
                # 两个页面抢同一条路由 = 侧栏会出现两个入口点同一页，
                # 而且「关掉哪一个」没有正确答案
                raise RegistryError(
                    f"页面路由重复 {spec.route!r}: "
                    f"{self._by_route[spec.route]} vs {spec.code}"
                )
            self._specs[spec.code] = spec
            self._by_route[spec.route] = spec.code

        # 反查索引整份重建：declare 可能分多批调用（逐模块装配），
        # 增量维护容易在「后声明的页面也要出现在旧接口的索引里」这一步漏掉。
        by_api: dict[str, list[str]] = {}
        for spec in self._specs.values():
            for code in spec.requires:
                by_api.setdefault(code, []).append(spec.code)
        self._by_api = {
            code: tuple(sorted(pages)) for code, pages in by_api.items()
        }

    # ------------------------------------------------------------------ 查询
    def all(self) -> tuple[PageSpec, ...]:
        return tuple(sorted(self._specs.values(), key=lambda spec: spec.code))

    def codes(self) -> frozenset[str]:
        return frozenset(self._specs)

    def get(self, code: str) -> PageSpec | None:
        return self._specs.get(code)

    def is_page(self, code: str) -> bool:
        return code in self._specs

    def pages_for(self, api_code: str) -> tuple[str, ...]:
        """声明了这个接口权限的页面码。空元组 = 没有页面声明它。"""
        return self._by_api.get(api_code, ())

    def permission_specs(self) -> tuple[PermissionSpec, ...]:
        """给权限目录用的页面权限点（分组「页面」）。"""
        return tuple(spec.permission for spec in self.all())

    def clear(self) -> None:
        """仅测试使用。"""
        self._specs.clear()
        self._by_route.clear()
        self._by_api.clear()

    # ------------------------------------------------------------------ 门控
    def gate(self, granted: frozenset[str] | set[str]) -> set[str]:
        """把一组接口权限过一遍页面门控，返回**有效**的那部分。

        规则（只有一条，见模块文档）：
        - 页面码本身原样保留（它就是「能进哪一页」的答案）；
        - 没有被任何页面声明的接口码原样保留（基础设施接口不受页面影响）；
        - 被声明过的接口码，只有当至少一个声明它的页面被授予时才保留。

        通配由 `permission_holds` 处理：授予 `page:*` 等价于所有页面都开了，
        授予 `task:*` 等价于持有该资源下所有具体动作 —— 两处判定必须同源，
        否则「`page:*` 的账号反而什么都点不了」这种怪事会出现。
        """

        enabled = {
            spec.code for spec in self._specs.values() if permission_holds(granted, spec.code)
        }
        effective: set[str] = set()
        for code in granted:
            if self.is_page(code) or not self.pages_for(code):
                effective.add(code)
                continue
            if any(page in enabled for page in self.pages_for(code)):
                effective.add(code)
        return effective


#: 进程内单例。与 `core.permissions.catalog` 同形态：启动期填充，运行期只读。
page_catalog = PageCatalog()

__all__ = ["PAGE_PREFIX", "PageCatalog", "PageSpec", "page_catalog"]
