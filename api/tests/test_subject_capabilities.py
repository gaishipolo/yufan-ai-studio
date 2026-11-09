"""主体可达性判定的边界用例。

这些用例的价值在**边界**，不在happy path：缺一个角色、缺一种能力、成员被软删、
参考图不进输出——每一条都对应一次真实的发布事故，而它们都只需要几行数据。

断言里刻意区分「主体级能力」与「逐成员缺口」：前者决定能不能发布，
后者决定发布失败时用户看到什么。只做前者的话，报错会是
「缺少能力：download」，而用户完全不知道该去掉哪张图。
"""

from __future__ import annotations

from modules.asset.subject_capabilities import (
    MemberView,
    check_reachability,
    effective_capabilities,
)


def _member(asset_id: str, *, role: str = "", grants=(), in_output=True, deleted=False):
    return MemberView(
        asset_id=asset_id, role=role, grants=grants, in_output=in_output, deleted=deleted
    )


def test_all_members_granting_everything_is_reachable() -> None:
    members = [
        _member("a", role="front", grants=["generate", "download"]),
        _member("b", role="side", grants=["generate", "download"]),
    ]
    result = check_reachability(
        members, requires=["generate", "download"], required_roles=["front"]
    )
    assert result.ok, result.why()
    assert result.member_gaps == {}
    assert result.broken_members == ()


def test_capability_intersection_not_union() -> None:
    """只要有一张图不给 `download`，整个主体就不能下载。

    这是铁律②的实现：取并集的话，往主体里多塞一张"允许下载"的图就能解锁，
    而那正是 `公开一个产品 = 公开它全部参考原片` 的路径。
    """
    members = [
        _member("cover", role="front", grants=["generate", "download"]),
        _member("ref", role="detail", grants=["generate"]),
    ]
    assert effective_capabilities(members) == {"generate"}
    result = check_reachability(members, requires=["download"], required_roles=[])
    assert not result.ok
    assert result.missing_capabilities == ("download",)
    # 逐成员缺口要能指出是谁：否则用户只能一张张点开看
    assert result.member_gaps == {"ref": ("download",)}


def test_member_out_of_output_does_not_limit_the_subject() -> None:
    """不进输出的参考图不构成对外承诺，因此不压主体能力。

    反过来会让「多挂一张只给模型看的图」意外降级整个主体，而界面上完全
    看不出是这个原因——那是最难解释的一类回归。
    """
    members = [
        _member("cover", role="front", grants=["generate", "download"]),
        _member("ref", grants=["generate"], in_output=False),
    ]
    result = check_reachability(members, requires=["download"], required_roles=["front"])
    assert result.ok, result.why()


def test_soft_deleted_member_blocks_publish_and_is_reported() -> None:
    """成员被软删 → 主体进"缺图"状态：发布被拦，且能说出缺哪一张。

    注意它是 `broken_members` 而不是直接从列表里消失：行还在，
    历史版本仍要能读到"当时有这张图"。
    """
    members = [
        _member("cover", role="front", grants=["generate", "download"]),
        _member("gone", role="detail", grants=["generate"], deleted=True),
    ]
    result = check_reachability(members, requires=["download"], required_roles=["detail"])
    assert not result.ok
    assert result.missing_roles == ("detail",)
    assert result.broken_members == ("gone",)


def test_missing_role_is_caught_even_when_capabilities_pass() -> None:
    """两条断言独立：能力够但角色缺，仍然不能发布。

    角色决定"模板按角色取图能不能取到"，能力决定"取到之后能不能用"。
    合成一条会让报错只指向其中一类，而修错需要知道是哪一类。
    """
    members = [_member("a", role="front", grants=["generate", "download"])]
    result = check_reachability(members, requires=[], required_roles=["front", "side"])
    assert not result.ok
    assert result.missing_roles == ("side",)
    assert result.missing_capabilities == ()
    assert "side" in result.why()


def test_empty_members_promises_nothing() -> None:
    """空主体不承诺任何能力——不是"什么都能"。

    `intersection` 对空集合返回全集是个常见陷阱：那样空主体会被判成
    "可下载可生成"，而它连一张图都没有。
    """
    assert effective_capabilities([]) == set()
    result = check_reachability([], requires=["generate"], required_roles=[])
    assert not result.ok
    assert result.missing_capabilities == ("generate",)


def test_no_requirements_is_an_explicit_pass() -> None:
    """不填要求 = 不要求。这是显式选择，不是"配置漏填"。

    与"随便填个空就通过"的区别在于：要求是发布动作的一部分，空表示
    "这个主体只供内部组装，暂不对外承诺能力"。
    """
    members = [_member("a", grants=["generate"])]
    result = check_reachability(members, requires=[], required_roles=[])
    assert result.ok
    assert result.why() == ""
