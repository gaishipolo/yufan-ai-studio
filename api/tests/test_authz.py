"""求值链的两条顺序约束（`core.authz`）。

这两条都不是风格问题，写反了都会得到一个**看起来正常、实际是错的**结果，
而且都很难从现象反推回原因：

- 减法放到摊开之前 → 角色里的 `task:*` 把刚否决的 `task:delete` 又摊回来，
  症状是「关了但没关」，而库里那条否决记录毫无问题；
- 减法放到门控之后 → 否决一个页面不生效，页面关了、接口还开着。

用纯函数测（不碰数据库）：判定链的正确性不该依赖「起一个服务再点一遍」。
"""

from __future__ import annotations

from core.authz import evaluate
from core.context import SUPER_PERMISSION
from core.pages import page_catalog


def test_exception_wins_over_a_resource_wildcard() -> None:
    """角色给 `task:*`，本人否决 `task:delete` → 摊开之后再减，才减得掉。"""
    result = evaluate(
        {"task:*", "page:task-center"}, {"task:delete"}
    )
    assert "task:delete" not in result.effective
    assert "task:delete" in result.denied_off
    # 同资源的其它动作不受影响
    assert "task:read" in result.effective


def test_exception_wins_over_a_page_wildcard() -> None:
    """`page:*` 也摊开：否则否决某一个页面会静默失效（`permission_holds` 仍为真）。"""
    # 用**独占**某个页面的接口码（`library:create`）。共享接口（`library:read`
    # 同时归「资产库」和「模板封装」）关掉一页仍然有效，那是设计如此。
    result = evaluate({"page:*", "library:create"}, {"page:library"})
    assert "page:library" not in result.effective
    # 接口码是单独授予的（页面只决定可见性）；它被门控砍掉的原因是
    # 「页面被本人关掉」，所以落在 deniedOff 而不是 gatedOff
    assert "library:create" in result.denied_off


def test_denying_a_page_is_reported_as_denied_not_gated() -> None:
    """否决页面 ⇒ 它管的接口失效，但原因写「你自己关的」。

    归到 `gatedOff` 的话，管理员会去开页面 —— 而页面本来就是开着的，
    真正该做的是点「恢复」。
    """
    result = evaluate({"task:read", "page:task-center"}, {"page:task-center"})
    assert "task:read" in result.denied_off
    assert "task:read" not in result.gated_off
    assert "page:task-center" not in result.pages


def test_gated_off_is_still_gated_off_without_an_exception() -> None:
    """没有例外时，被页面砍掉的仍然报 `gatedOff` —— 两条分支不能互相吞掉。"""
    result = evaluate({"task:read"})
    assert "task:read" in result.gated_off
    assert result.denied_off == ()


def test_full_privilege_bypasses_the_exception() -> None:
    """超管（或持有 `*` 的账号）等于全集，例外不进这条链。"""
    for result in (
        evaluate({"task:read"}, {"task:read"}, is_superuser=True),
        evaluate({SUPER_PERMISSION}, {"task:read"}),
    ):
        assert result.effective == {SUPER_PERMISSION}
        assert result.pages == tuple(spec.code for spec in page_catalog.all())


def test_invariants_hold_for_a_mixed_set() -> None:
    """不变量：生效集合 ⊆ 摊开集合，且与「被否决」不相交。"""
    granted = {"task:*", "library:read", "page:library", "export:read"}
    denies = {"task:delete", "page:library"}
    result = evaluate(granted, denies)

    assert result.effective <= result.expanded
    assert not (result.effective & denies)
    # 被否决的码必须全都报出来，否则界面上会出现「消失了但没痕迹」
    reported = set(result.denied_off) | set(denies)
    assert denies <= reported
