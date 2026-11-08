"""权限判定：`CurrentUser.has / has_all / has_any` 的三条语义。

**这三个方法此前没有任何直接测试。** 变异扫描发现：把 `resource:*` 通配改成
恒 `False`、把 `has_all` 改成 `any`，840+ 条测试全绿——因为现有用例都在验证
「某个接口返回 403」，而演示账号是超管（`*`），一次都不会走到这两个分支。

权限判定是全部越权风险的唯一收口，它错了不会报错，只会「该拒的放行」或
「该放的拒掉」，两者都是生产事故。所以这里逐条钉死语义，包括**不该通过的情况**。
"""

from __future__ import annotations

import pytest

from core.context import CurrentUser


def _user(*permissions: str) -> CurrentUser:
    return CurrentUser(id="u_1", name="测试", permissions=frozenset(permissions))


def test_exact_code_matches() -> None:
    assert _user("task:read").has("task:read")


def test_super_permission_grants_everything() -> None:
    superuser = _user("*")
    assert superuser.has("task:read")
    assert superuser.has("anything:at:all")
    assert superuser.has_all(("a:b", "c:d"))


def test_resource_wildcard_covers_every_action_of_that_resource() -> None:
    """`task:*` 是「这个资源的全部动作」，不是「某个叫 task:* 的动作」。"""
    user = _user("task:*")
    assert user.has("task:read")
    assert user.has("task:delete")
    assert user.has("task:retry:any")


def test_resource_wildcard_does_not_leak_across_resources() -> None:
    """通配只在同一个资源内生效。曾经把前缀当子串匹配就会让它串到别的资源。"""
    user = _user("task:*")
    assert not user.has("asset:read")
    assert not user.has("taskx:read")


def test_code_without_action_is_treated_as_the_resource_itself() -> None:
    """`task`（漏写动作）会被 `task:*` 放行。

    这不是漏洞：路由守卫都是 `require("task:read")` 这种带动作的码，没有
    `require("task")`。钉在这里是为了让「哪天有人写出无动作权限码」时，
    行为是**已知**的而不是碰运气。
    """
    assert _user("task:*").has("task")
    assert not _user("task:read").has("task")


def test_related_resource_prefix_is_not_a_wildcard() -> None:
    """`task:*` 不能覆盖 `task_item:read`——资源名按整段比较，不是前缀。"""
    assert not _user("task:*").has("task_item:read")


def test_has_all_requires_every_code() -> None:
    """变异扫描的缺口之一：改成 `any` 之后 840 条测试全绿。

    `mode="all"` 是最常用的守卫写法（`require("a", "b")`），退化成 any 等于
    只要有一个权限就能过——这是最容易发生、也最难被发现的一类越权。
    """
    user = _user("task:read")
    assert not user.has_all(("task:read", "task:write"))
    assert user.has_all(("task:read",))
    assert user.has_all(())


def test_has_any_accepts_one_of() -> None:
    user = _user("task:read")
    assert user.has_any(("task:read", "task:write"))
    assert not user.has_any(("task:write", "asset:read"))
    assert not user.has_any(())


def test_has_all_and_has_any_agree_on_single_code() -> None:
    """两个方法在单码时结论必须一致：不一致说明其中一个实现写错了。"""
    for permissions, code in (
        (("task:read",), "task:read"),
        (("task:*",), "task:read"),
        (("*",), "task:read"),
        ((), "task:read"),
        (("asset:read",), "task:read"),
    ):
        user = _user(*permissions)
        assert user.has_all((code,)) == user.has_any((code,)) == user.has(code)


@pytest.mark.parametrize(
    ("permissions", "code"),
    [
        (("task:*",), "task:read"),
        (("*",), "asset:delete"),
        (("asset:read", "asset:write"), "asset:write"),
    ],
)
def test_require_style_guards_pass(permissions: tuple[str, ...], code: str) -> None:
    """`require(code)` 的判据就是 `has_all((code,))`：这里把放行的一侧钉住。"""
    assert _user(*permissions).has_all((code,))
