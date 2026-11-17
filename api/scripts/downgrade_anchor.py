"""算出「降级测试能回退到哪一道迁移」——CI 迁移观察期 job 用它决定锚点。

## 为什么需要这个脚本

CI 的「迁移 · PostgreSQL（观察期）」job 会跑一次降级往返：

    alembic upgrade head
    alembic check
    alembic downgrade <锚点>
    alembic upgrade head

锚点的含义是：**「从这里往下的迁移都不可逆，回退到这儿为止」**。

这份锚点过去是**硬编码在 `ci.yml` 里**的一个 revision id。硬编码的必然腐烂：

- 2026-11-15：唯一不可逆迁移是 `b8e4c1f90d27`（下线替换链，删 11 张表），
  CI 锚点被改成它。
- 2026-11-17：新增 `c3d8e1f04a7b`（素材封装下线，同样不可逆），位置在
  `b8e4c1f90d27` **之后** —— 降级到旧锚点时照样会撞上它的 `NotImplementedError`，
  CI 又红了。而 CI 注释里那句「哪天又出现不可逆迁移，把这里的锚点换成那道」
  就是为这种情况写的，但**没人会记得去改**。

所以锚点不能手写，要**从迁移链自己算出来**。

## 判据：模块级常量 `IRREVERSIBLE = True`

一条迁移「不可逆」= 它的 `downgrade()` 会抛异常（数据或表随能力一起下线，
重建空表只会把「降级成功」变成谎话）。

判据必须是**机器可读**的，不能靠读 `downgrade()` 的函数体去猜「它是不是
真的抛异常」—— 那既脆弱又误判（`d41c7a9e5f28` 的 `downgrade()` 里也有 `raise`，
但那是**业务前置检查**：引用找不到对应行时抛错，不是「这条迁移不可逆」）。

于是约定：不可逆迁移在模块里声明一个常量：

    irreversible = True

（大小写随意，脚本两种都认；推荐小写 `irreversible = True`，与
`revision` / `down_revision` 同风格。）

脚本读迁移链，按**拓扑顺序**找出所有 `irreversible = True` 的迁移，
取**离 head 最近的那一道**作为锚点。

## 为什么锚点是「离 head 最近的不可逆迁移」

降级目标必须满足两个条件：

1. **不能低于任何不可逆迁移** —— 否则回退时必经那些不可逆迁移的 `downgrade()`，
   它们会抛异常，测试直接失败。
2. **尽量靠近 head** —— 降级测试的价值在于「把尽可能多的可逆迁移的下行路径
   走一遍」。锚点越靠近 head，覆盖到的可逆迁移越多。

从 head 往回走，第一道遇到的不可逆迁移正好同时满足这两条。

## 用法

    python scripts/downgrade_anchor.py            # 打印锚点 revision id
    python scripts/downgrade_anchor.py --explain  # 顺带说明为什么是它

CI 里这么用（`downgrade` 的目标就是脚本的输出）：

    ANCHOR=$(python scripts/downgrade_anchor.py)
    alembic downgrade "$ANCHOR"
    alembic upgrade head

脚本**只读**迁移链，不碰数据库，可以在本机随手跑。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]

#: 不可逆迁移的声明方式。两种写法都认：`irreversible = True` 与
#: `IRREVERSIBLE = True`。存成一组是为了让「写错大小写」不致于让脚本静默
#: 找不到任何不可逆迁移（那会让锚点退化成 head，降级测试变成空转）。
_IRREVERSIBLE_NAMES = ("irreversible", "IRREVERSIBLE")


def _load_module(path: Path):
    """按文件路径导入一个迁移模块。

    迁移文件的名字（`c3d8e1f04a7b_drop_library_packs.py`）不是合法标识符，
    不能直接 `import`。用 `importlib` 按路径加载。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(f"_migration_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载迁移模块：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _is_irreversible(path: Path) -> bool:
    """这条迁移是不是不可逆的。

    **只认模块级常量**，不看 `downgrade()` 的函数体 —— 里面出现 `raise`
    有两种含义，只有一种是「这条迁移不可逆」。
    """
    module = _load_module(path)
    return any(getattr(module, name, False) is True for name in _IRREVERSIBLE_NAMES)


def _migration_files(versions_dir: Path | None = None) -> list[Path]:
    root = versions_dir if versions_dir is not None else API_ROOT / "migrations" / "versions"
    return sorted(root.glob("*.py"))


def _revision_of(path: Path) -> str:
    module = _load_module(path)
    revision = getattr(module, "revision", None)
    if not isinstance(revision, str) or not revision:
        raise RuntimeError(f"迁移文件缺少 revision：{path}")
    return revision


def _down_revision_of(path: Path) -> str | None:
    module = _load_module(path)
    down = getattr(module, "down_revision", None)
    return down if isinstance(down, str) else None


def _chain(versions_dir: Path | None = None) -> list[tuple[str, Path]]:
    """按**从 base 到 head** 的顺序返回 (revision, 文件路径)。

    不用 alembic 的 `ScriptDirectory` —— 它要求 `alembic.ini` 里的
    `script_location` 指向对位置，而脚本要在任何工作目录下都能跑。
    这里自己按 `down_revision` 串链：迁移链是单 head 的线性链，
    从「没有 down_revision 的那道」往后走就是完整顺序。

    `versions_dir` 是给测试用的：让用例能在临时目录里造一条迷你链，
    而不必往仓库的迁移目录里塞文件（那会污染工作区）。
    """
    files = _migration_files(versions_dir)
    if not files:
        root = versions_dir if versions_dir is not None else API_ROOT / "migrations" / "versions"
        raise RuntimeError(f"没找到迁移文件：{root}")

    by_down: dict[str | None, Path] = {}
    for path in files:
        down = _down_revision_of(path)
        if down in by_down:
            raise RuntimeError(
                f"迁移链分叉：{by_down[down].name} 与 {path.name} 的 down_revision 都是 {down}"
            )
        by_down[down] = path

    if None not in by_down:
        raise RuntimeError("迁移链找不到 base（没有 down_revision 为空的那道迁移）")

    ordered: list[tuple[str, Path]] = []
    cursor: str | None = None
    while cursor in by_down:
        path = by_down[cursor]
        revision = _revision_of(path)
        ordered.append((revision, path))
        cursor = revision
    if len(ordered) != len(files):
        missing = sorted(p.name for p in files if p not in {q for _, q in ordered})
        raise RuntimeError("迁移链断了（有些迁移不在链上）：" + ", ".join(missing))
    return ordered


def find_anchor(versions_dir: Path | None = None) -> tuple[str, list[str]]:
    """算出锚点。

    返回 `(锚点 revision, 全部不可逆迁移的 revision 列表)`。
    没有不可逆迁移时锚点取 head —— 那种情况下可以一路降到 base。
    """
    chain = _chain(versions_dir)
    irreversible = [revision for revision, path in chain if _is_irreversible(path)]
    if not irreversible:
        return chain[-1][0], []
    # 链是从 base 到 head 排的，最后一个不可逆的离 head 最近。
    return irreversible[-1], irreversible


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="算出迁移降级测试的锚点")
    parser.add_argument(
        "--explain",
        action="store_true",
        help="顺带打印链上所有不可逆迁移与理由",
    )
    args = parser.parse_args(argv)

    try:
        anchor, irreversible = find_anchor()
    except Exception as exc:  # noqa: BLE001 - 脚本入口要把原因讲清楚
        print(f"算不出降级锚点：{exc}", file=sys.stderr)
        return 1

    if args.explain:
        chain = _chain()
        for revision, path in chain:
            mark = "  ← 锚点" if revision == anchor else ""
            flag = "不可逆" if revision in irreversible else ""
            print(f"{revision}  {flag:6}  {path.name}{mark}")
        print()
        if irreversible:
            print(f"不可逆迁移共 {len(irreversible)} 道，锚点取离 head 最近的那一道。")
            print("降级只回到锚点：再往下走没有意义，只会得到一句「降级成功但数据没了」的谎话。")
        else:
            print("链上没有不可逆迁移：可以一路降到 base。")

    print(anchor)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
