"""模板发布闸的「全员可读」判据：可见性之外还必须看生命周期状态。

`state` 与 `visibility` 正交：回收站里的团队可见素材，按 `visibility` 看是
「所有人可读」，按 `can_read` 看却谁都读不到。发布闸漏掉 `state` 的症状是
「发布成功、别人复用时安静少一张图」，所以判据单独钉在这里。
"""

from __future__ import annotations

import pytest

from core.ports.library_lookup import LibraryAssetKind, LibraryAssetState, LibraryVisibility
from modules.template_pack.spec_gate import _is_publicly_readable


def _ref(*, state: LibraryAssetState, visibility: LibraryVisibility, purpose: str = ""):
    from core.ports.library_lookup import LibraryAssetRef

    return LibraryAssetRef(
        id="a" * 32,
        owner_id="u_001",
        kind=LibraryAssetKind.IMAGE,
        name="测试素材",
        state=state,
        visibility=visibility,
        purpose=purpose,
    )


@pytest.mark.parametrize(
    "state,visibility,purpose,expected",
    [
        (LibraryAssetState.ACTIVE, LibraryVisibility.TEAM, "", True),
        (LibraryAssetState.ACTIVE, LibraryVisibility.PERSONAL, "case", True),
        (LibraryAssetState.ACTIVE, LibraryVisibility.PERSONAL, "", False),
        (LibraryAssetState.ACTIVE, LibraryVisibility.GROUP, "", False),
        # 下面两条是这次补的：回收站 / 草稿里即便 case + team 也不能放行
        (LibraryAssetState.TRASH, LibraryVisibility.TEAM, "case", False),
        (LibraryAssetState.DRAFT, LibraryVisibility.TEAM, "case", False),
    ],
)
def test_publicly_readable_requires_active_state(state, visibility, purpose, expected) -> None:
    assert _is_publicly_readable(
        _ref(state=state, visibility=visibility, purpose=purpose)
    ) is expected
