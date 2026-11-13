"""清掉 `api/var/` 下的测试与演示残留（§24.23 候选 C）。

`var/` 是开发/CI 的临时目录，跑一遍测试就多几个 `pytest-*` 基准目录，手工调试
又留下 `dbg-*` / `template-*` 之类。它们不是任何功能的一部分，但**越跑越胖**，
而且盘满了之后排查现场很容易以为是「归档盘出问题」。

三条约束：

1. **默认不动手**。不加 `--yes` 只打印计划；名字里带 `--yes` 才真的删。
2. **只认 `var`**。目录名不是 `var` 一律拒绝——这个脚本的写法（把不在保留清单里的
   全部当残留）放到别处就是灾难。
3. **保留清单是白名单而非黑名单**。开发库 `app.db`、素材落盘 `storage/`、备份
   `backups/`、运行工作区 `runs/` 这些「看着像临时文件」的东西都在里面：工作区是
   验收与复现依据（§24.23），要清它得走盘点命令，不是这里。

用法：

    ./scripts/../.venv/Scripts/python.exe scripts/clean_var.py          # 看计划
    ... clean_var.py --yes                                            # 执行
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = API_ROOT / "var"

# 精确保留（目录或文件）
KEEP_NAMES = frozenset(
    {
        "storage",  # 本地素材落盘：真实字节
        "backups",  # 手工备份
        "video_templates",  # 模板目录的本地缓存/导出
        "runs",  # 运行工作区：验收与复现依据，清理要走 template-workspaces
        "archive-cache",  # 归档存储的本地缓存，删了会让归档读不到
        "ui-shots",  # 截图证据
        "scratch-staging",  # 手工搭的对照目录，不清
        ".gitkeep",  # 唯一纳入版本控制的文件：删了 `var/` 就空了
    }
)
# 前缀保留
KEEP_PREFIXES = (
    "app.db",  # 开发库本体与所有 .bak-*
    "chrome-",  # 截图工具的浏览器 profile
    "library-storage-",  # 素材库导出快照
)


def is_kept(name: str) -> bool:
    return name in KEEP_NAMES or name.startswith(KEEP_PREFIXES)


def size_of(path: Path) -> int:
    """目录递归求和；单文件直接 stat。指向不存在的符号链接按 0 算。"""
    try:
        if path.is_symlink() or path.is_file():
            return path.lstat().st_size
    except OSError:
        return 0
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file() and not child.is_symlink():
                total += child.stat().st_size
        except OSError:
            continue
    return total


def human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


def plan(root: Path) -> tuple[list[Path], list[Path]]:
    """分成「保留」与「残留」两组。不跟随符号链接：删到链接外面去是另一个事故。"""
    kept: list[Path] = []
    residue: list[Path] = []
    for child in sorted(root.iterdir()):
        (kept if is_kept(child.name) else residue).append(child)
    return kept, residue


def _guard(root: Path) -> None:
    if root.is_symlink():
        raise SystemExit(f"拒绝：{root} 是符号链接，清它等于清链接目标")
    if not root.is_dir():
        raise SystemExit(f"拒绝：{root} 不是目录")
    if root.name != "var":
        raise SystemExit(
            f"拒绝：{root} 不叫 var。这个脚本把「不在保留清单里的」全当残留，"
            "只能指向 api/var 这类临时目录。"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="清掉 api/var 下的测试/演示残留")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="默认 api/var")
    parser.add_argument("--yes", action="store_true", help="真的删（不加只打印计划）")
    parser.add_argument("--top", type=int, default=15, help="列出最大的前几个（默认 15）")
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    _guard(root)
    kept, residue = plan(root)
    total = sum(size_of(item) for item in residue)

    print(f"扫描 {root}")
    print(f"残留 {len(residue)} 项，合计 {human(total)}；保留 {len(kept)} 项")
    if not residue:
        print("没有需要清理的东西。")
        return 0
    print("将删除：")
    for item in sorted(residue, key=size_of, reverse=True)[: args.top]:
        kind = "目录" if item.is_dir() and not item.is_symlink() else "文件"
        print(f"  {human(size_of(item)):>9}  [{kind}] {item.name}")
    if len(residue) > args.top:
        print(f"  ……还有 {len(residue) - args.top} 项（用 --top 调大）")

    if not args.yes:
        print("（这是 dry-run：以上内容没有被删除。确认后加 --yes）")
        return 0

    removed, freed = 0, 0
    for item in residue:
        # 逐项算体积再删：中途失败时「已经释放了多少」仍然是真的
        freed += size_of(item)
        try:
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)
            else:
                item.unlink()
            removed += 1
        except OSError as exc:
            print(f"  删不掉 {item.name}：{exc}", file=sys.stderr)
    print(f"已删除 {removed}/{len(residue)} 项，释放 {human(freed)}")
    return 0 if removed == len(residue) else 1


if __name__ == "__main__":
    raise SystemExit(main())
