"""`scripts/downgrade_anchor.py`：算迁移降级测试的锚点。

这个脚本决定 CI 的降级往返能回退到哪一道迁移。它错了的后果分两种，
都很隐蔽：

- **算得比实际不可逆点更靠前** → 降级撞上不可逆迁移的 `NotImplementedError`，
  CI 的迁移观察期 job 直接红（2026-11-17 就是这么红的：锚点手写的
  `b8e4c1f90d27` 后面新加了一道更靠后的不可逆迁移 `c3d8e1f04a7b`）。
- **算不出任何不可逆迁移** → 锚点退化成 head，降级变成空转，
  CI 全绿但**什么都没测**。这种更坏：绿灯掩盖了没测。

所以这里不只钉「算对当前这条链」，还要钉「漏标记会被抓住」——
后者靠**临时目录里造的迷你链**来保证守卫有判别力。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = API_ROOT / "scripts" / "downgrade_anchor.py"
VERSIONS = API_ROOT / "migrations" / "versions"


def _load() -> object:
    """把脚本当模块加载（它不在 `src/` 下，不在 import 路径里）。"""
    spec = importlib.util.spec_from_file_location("_downgrade_anchor", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_downgrade_anchor"] = module
    spec.loader.exec_module(module)
    return module


anchor_mod = _load()


def _mini_migration(
    directory: Path,
    *,
    revision: str,
    down_revision: str | None,
    irreversible: bool = False,
) -> Path:
    """在临时目录里造一道迁移文件。

    **不往仓库的迁移目录里塞文件** —— 那会污染工作区，而且用例失败时
    残留文件会留在仓库里（曾经就这么脏过一次）。
    """
    path = directory / f"{revision}_mini.py"
    lines = [
        "from __future__ import annotations",
        "",
        "from alembic import op",
        "",
        f"revision = {revision!r}",
        f"down_revision = {down_revision!r}",
        "branch_labels = None",
        "depends_on = None",
    ]
    if irreversible:
        lines += ["", "irreversible = True"]
    lines += [
        "",
        "",
        "def upgrade() -> None:",
        "    pass",
        "",
        "",
        "def downgrade() -> None:",
    ]
    if irreversible:
        lines.append("    raise NotImplementedError('这条迁移不可逆')")
    else:
        lines.append("    pass")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# 当前这条链
# --------------------------------------------------------------------------- #


def test_anchor_is_the_latest_irreversible_revision() -> None:
    """锚点 = 离 head 最近的那道不可逆迁移。

    当前链上有两道不可逆迁移（`b8e4c1f90d27` 下线替换链、`c3d8e1f04a7b`
    下线素材封装），后者在链上更靠后，所以锚点必须是它。
    """
    anchor, irreversible = anchor_mod.find_anchor()  # type: ignore[attr-defined]

    assert "c3d8e1f04a7b" in irreversible, (
        "c3d8e1f04a7b 应当被认成不可逆迁移 —— 它少了 `irreversible = True` 标记？"
    )
    assert "b8e4c1f90d27" in irreversible, (
        "b8e4c1f90d27 应当被认成不可逆迁移 —— 它少了 `irreversible = True` 标记？"
    )
    assert anchor == "c3d8e1f04a7b", (
        f"锚点应取离 head 最近的那道不可逆迁移，实际是 {anchor}"
    )


def test_anchor_is_never_below_an_irreversible_revision() -> None:
    """锚点不能低于任何不可逆迁移。

    低了 = 降级时必经那道不可逆迁移的 `downgrade()` → 它抛异常 → CI 红。
    """
    anchor, irreversible = anchor_mod.find_anchor()  # type: ignore[attr-defined]
    chain = anchor_mod._chain()  # type: ignore[attr-defined]
    order = [revision for revision, _ in chain]

    assert anchor in order, "锚点不在迁移链上"
    anchor_index = order.index(anchor)
    for revision in irreversible:
        assert order.index(revision) <= anchor_index, (
            f"不可逆迁移 {revision} 在锚点 {anchor} 之后 —— 降级必然撞上它"
        )


def test_anchor_covers_every_reversible_migration_above_it() -> None:
    """锚点之后（离 head 更近的）那些可逆迁移都要被降级往返覆盖到。

    这是降级测试的**全部价值所在**：锚点离 head 越近，可逆迁移的下行路径
    走得越多。锚点算错（太靠前）时这条会红。
    """
    anchor, _ = anchor_mod.find_anchor()  # type: ignore[attr-defined]
    chain = anchor_mod._chain()  # type: ignore[attr-defined]
    order = [revision for revision, _ in chain]
    anchor_index = order.index(anchor)

    reversible_above = [
        revision
        for revision, path in chain[anchor_index + 1 :]
        if not anchor_mod._is_irreversible(path)  # type: ignore[attr-defined]
    ]
    assert reversible_above, "锚点之后一道可逆迁移都没有 —— 降级测试在空转"
    # 锚点之后的每道可逆迁移，其 downgrade() 都得真的存在
    for revision in reversible_above:
        path = next(p for r, p in chain if r == revision)
        module = anchor_mod._load_module(path)  # type: ignore[attr-defined]
        assert hasattr(module, "downgrade"), f"{revision} 缺少 downgrade()"


def test_chain_covers_every_migration_file() -> None:
    """链上不能漏文件 —— 漏一道 = 那道迁移的降级路径从来没被测过。"""
    chain = anchor_mod._chain()  # type: ignore[attr-defined]
    files = sorted(VERSIONS.glob("*.py"))
    assert len(chain) == len(files), (
        f"链上有 {len(chain)} 道，目录里有 {len(files)} 个文件 —— 迁移链断了"
    )


# --------------------------------------------------------------------------- #
# 判据本身（迷你链，不碰仓库）
# --------------------------------------------------------------------------- #


def test_marker_decides_not_position(tmp_path: Path) -> None:
    """**标记**决定不可逆，不是「碰巧链上顺序」。

    迷你链：base → a → b → c。`c` 的 `downgrade()` 明明会抛，但**没标记** →
    不算不可逆 → 锚点不是它。给它加上标记 → 锚点立刻变成它。

    这条是守卫的**判别力来源**：没有它，「脚本只是碰巧对」也全绿。
    """
    directory = tmp_path / "versions"
    directory.mkdir()
    _mini_migration(directory, revision="aaa_base", down_revision=None)
    _mini_migration(directory, revision="bbb_mid", down_revision="aaa_base")
    _mini_migration(directory, revision="ccc_head", down_revision="bbb_mid")

    anchor, irreversible = anchor_mod.find_anchor(directory)  # type: ignore[attr-defined]
    assert irreversible == [], "没有标记就不该有不可逆迁移"
    # 没有不可逆迁移时锚点取 head（可以一路降到 base）
    assert anchor == "ccc_head"

    # 现在给 head 加上标记
    head_path = directory / "ccc_head_mini.py"
    head_path.write_text(
        head_path.read_text(encoding="utf-8").replace(
            "depends_on = None", "depends_on = None\n\nirreversible = True"
        ),
        encoding="utf-8",
    )
    anchor2, irreversible2 = anchor_mod.find_anchor(directory)  # type: ignore[attr-defined]
    assert irreversible2 == ["ccc_head"]
    assert anchor2 == "ccc_head"


def test_latest_irreversible_wins_in_mini_chain(tmp_path: Path) -> None:
    """链上有两道不可逆迁移时，锚点取**离 head 最近**的那道。

    这正是本次 CI 事故的形状：早期锚点指向 `b8e4c1f90d27`，后来又加了
    更靠后的 `c3d8e1f04a7b`，锚点必须跟着往后挪。
    """
    directory = tmp_path / "versions"
    directory.mkdir()
    _mini_migration(directory, revision="aaa_base", down_revision=None)
    _mini_migration(directory, revision="bbb_irr", down_revision="aaa_base", irreversible=True)
    _mini_migration(directory, revision="ccc_rev", down_revision="bbb_irr")
    _mini_migration(directory, revision="ddd_irr", down_revision="ccc_rev", irreversible=True)
    _mini_migration(directory, revision="eee_head", down_revision="ddd_irr")

    anchor, irreversible = anchor_mod.find_anchor(directory)  # type: ignore[attr-defined]
    assert irreversible == ["bbb_irr", "ddd_irr"]
    assert anchor == "ddd_irr", f"锚点应取离 head 最近的那道，实际 {anchor}"


def test_unmarked_but_raising_downgrade_is_not_treated_as_irreversible(
    tmp_path: Path,
) -> None:
    """`downgrade()` 里出现 `raise` **不等于**不可逆。

    真实例子：`d41c7a9e5f28` 的 `downgrade()` 里有 `raise RuntimeError`，
    但那是**业务前置检查**（引用找不到对应行时抛错），不是「这条迁移不可逆」。
    若按「函数体里有没有 raise」来判，这条迁移会被误标成不可逆 → 锚点算得过前
    → 白白丢掉它上面所有可逆迁移的覆盖。

    判据必须是显式标记。
    """
    directory = tmp_path / "versions"
    directory.mkdir()
    _mini_migration(directory, revision="aaa_base", down_revision=None)
    # 一道「会抛但不是不可逆」的迁移：downgrade 里有业务检查
    path = directory / "bbb_business_check_mini.py"
    path.write_text(
        "from __future__ import annotations\n"
        "\n"
        "from alembic import op\n"
        "\n"
        "revision = 'bbb_business_check'\n"
        "down_revision = 'aaa_base'\n"
        "branch_labels = None\n"
        "depends_on = None\n"
        "\n"
        "\n"
        "def upgrade() -> None:\n"
        "    pass\n"
        "\n"
        "\n"
        "def downgrade() -> None:\n"
        "    raise RuntimeError('引用找不到对应行')\n",
        encoding="utf-8",
    )

    _, irreversible = anchor_mod.find_anchor(directory)  # type: ignore[attr-defined]
    assert irreversible == [], "带业务检查的 raise 被误判成不可逆了"


def test_broken_chain_is_reported(tmp_path: Path) -> None:
    """链断了（有两道迁移的 down_revision 相同）要报错，不能静默算个锚点。"""
    directory = tmp_path / "versions"
    directory.mkdir()
    _mini_migration(directory, revision="aaa_base", down_revision=None)
    _mini_migration(directory, revision="bbb_fork1", down_revision="aaa_base")
    _mini_migration(directory, revision="ccc_fork2", down_revision="aaa_base")

    with pytest.raises(RuntimeError, match="分叉"):
        anchor_mod.find_anchor(directory)  # type: ignore[attr-defined]


def test_orphan_migration_is_reported(tmp_path: Path) -> None:
    """**孤儿迁移**（down_revision 指向一道不存在的迁移）也要报错。

    这与「分叉」是两种不同的坏法：分叉是两头撞车，孤儿是链走不到头。
    两者都会让「链上的迁移数 ≠ 目录里的文件数」—— 也就是**有迁移的降级
    路径从来没被测过**。静默跳过的话，脚本会拿一个只覆盖了半条链的锚点
    去骗 CI 说「降级测试通过了」。
    """
    directory = tmp_path / "versions"
    directory.mkdir()
    _mini_migration(directory, revision="aaa_base", down_revision=None)
    _mini_migration(directory, revision="bbb_head", down_revision="aaa_base")
    # 孤儿：它的父迁移根本不存在
    _mini_migration(directory, revision="ccc_orphan", down_revision="nonexistent_revision")

    with pytest.raises(RuntimeError, match="断了"):
        anchor_mod.find_anchor(directory)  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# 标记与实际行为的一致性
# --------------------------------------------------------------------------- #


def test_marked_migrations_actually_raise_on_downgrade() -> None:
    """被标成不可逆的迁移，它的 `downgrade()` 必须真的抛。

    反过来就说明标记说谎了：标记说「不可逆」，脚本据此把锚点定在这里，
    而真降级时它不抛 —— 锚点算得偏保守，浪费了可逆迁移的覆盖范围。
    """
    chain = anchor_mod._chain()  # type: ignore[attr-defined]
    irreversible_paths = [
        path
        for _, path in chain
        if anchor_mod._is_irreversible(path)  # type: ignore[attr-defined]
    ]
    assert irreversible_paths, "链上没有任何不可逆迁移 —— 判据本身坏了"

    for path in irreversible_paths:
        module = anchor_mod._load_module(path)  # type: ignore[attr-defined]
        with pytest.raises(Exception):  # noqa: B017 - 迁移的不可逆异常类型不统一
            module.downgrade()


def test_every_irreversible_migration_carries_the_marker() -> None:
    """链上**每一道**会抛「不可逆」异常的迁移都必须带标记。

    漏标记的后果是静默的：脚本算出的锚点偏前，CI 的降级往返在撞上那道
    迁移时抛 `NotImplementedError` —— 失败发生在 CI 上，而不是在本地。

    判据用「`downgrade()` 的一行实现里出现了 `raise`」这种粗口径也不可靠
    （业务前置检查也算），所以这里改成**逐条人工确认**：把链上两道已知的
    不可逆迁移点名，要求它们都带标记；同时反向检查「带标记的确实在名单里」。
    """
    chain = anchor_mod._chain()  # type: ignore[attr-defined]
    marked = {
        revision
        for revision, path in chain
        if anchor_mod._is_irreversible(path)  # type: ignore[attr-defined]
    }

    known_irreversible = {
        "b8e4c1f90d27",  # 下线替换链：11 张表随能力一起下线
        "c3d8e1f04a7b",  # 下线素材封装(pack)：表、资产行与封装槽位一并退场
    }
    missing = known_irreversible - marked
    assert not missing, f"这些不可逆迁移漏了 `irreversible = True` 标记：{sorted(missing)}"

    # 反向：带标记的必须是真不可逆（上面那条用例已验证它会抛）
    assert marked == known_irreversible, (
        f"带标记的迁移集合变了：{sorted(marked)}。新增不可逆迁移时，"
        "把它加进这里的名单，并确认它的 downgrade() 真的抛"
    )
