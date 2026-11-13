"""工作区盘点：占了多少、最老最新、哪些已经没有档案认领。

**为什么先做「看得见」而不是「自动删」**（§24.23 / §24.24）：工作区不是缓存，
`inputs/` 是验收依据（素材被删了评价页还要能打开当初喂给模型的图）、`records/`
是证据、`code/runtime/` 是复现依据。删了没有第二份，所以「留多久」是产品口径。
这一步只回答三个问题，**不删任何东西**：

1. 一共占多少盘、最老与最新分别是哪天；
2. 哪些目录的档案行已经不在库里（被硬删过）——它们不会出现在任务列表里，
   留着只是占盘，是最没有争议的清理候选；
3. 最大的几个是谁。

`known_ids` 由调用方给（数据库里现存的档案 id 集合）。本模块不认识数据库，
与 `workspace.py` 的分工一致：那里只管路径约定，这里只管统计。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from infra.archive.base import read_json

RUN_JSON = "run.json"
# 与 `infra.archive.runs.RUN_ID` 同形。这里不 import 它：那会把「归档根怎么解析」
# 一起拽进来，而盘点要能被指向任意目录（含 `var/` 下的测试残留）。
RUN_ID = re.compile(r"[a-f0-9]{32}")
_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def human_size(size: int) -> str:
    """1024 进制，保留一位小数：盘占用只需要一眼看懂，不需要精确到字节。"""
    value = float(size)
    for unit in _UNITS:
        if value < 1024 or unit == _UNITS[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


def _stamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%d %H:%M")


@dataclass(frozen=True)
class Workspace:
    """一个工作区目录。`run_id` 为空串表示「认不出归属」。"""

    path: Path
    run_id: str
    status: str
    size: int
    files: int
    modified: float

    @property
    def name(self) -> str:
        return self.path.name


@dataclass(frozen=True)
class Report:
    root: Path
    workspaces: tuple[Workspace, ...]
    # `None` = 没读到档案表。**不能退化成空集合**：那会让每个目录都显示成孤儿，
    # 而孤儿是「可以删」的候选——报错的时候给人一份「全都删得」的清单是最坏的结果。
    known_ids: frozenset[str] | None

    @property
    def total_size(self) -> int:
        return sum(item.size for item in self.workspaces)

    @property
    def orphans(self) -> tuple[Workspace, ...]:
        """档案行已经不在库里的目录。

        `run_id` 认不出来（老布局的 `product-<id>` 之类）也算孤儿：没有 id 就没有
        任何一行能认领它，与「档案被硬删」在清理这件事上等价。
        """
        if self.known_ids is None:
            return ()
        return tuple(item for item in self.workspaces if item.run_id not in self.known_ids)

    def lines(self, *, top: int = 5, orphans_only: bool = False) -> list[str]:
        if not self.workspaces:
            return [f"工作区根：{self.root}", "没有找到任何工作区目录（空盘或路径不对）"]

        oldest = min(self.workspaces, key=lambda item: item.modified)
        newest = max(self.workspaces, key=lambda item: item.modified)
        lines = [
            f"工作区根：{self.root}",
            f"共 {len(self.workspaces)} 个，合计 {human_size(self.total_size)}；"
            f"最老 {_stamp(oldest.modified)}，最新 {_stamp(newest.modified)}",
        ]
        if self.known_ids is None:
            lines.append("  归属未判断：读不到档案表（体积统计不受影响）")
            listed: tuple[Workspace, ...] = ()
            orphans: tuple[Workspace, ...] = ()
        else:
            orphans = self.orphans
            kept = len(self.workspaces) - len(orphans)
            lines.append(
                f"  在档案里：{kept} 个"
                f"（{human_size(self.total_size - sum(o.size for o in orphans))}）"
            )
            lines.append(
                f"  档案已删：{len(orphans)} 个（{human_size(sum(o.size for o in orphans))}）"
                + ("，这些只占盘、不会出现在任务列表里" if orphans else "")
            )
            listed = orphans if orphans_only else self.workspaces
        if not listed:
            return lines
        heading = "档案已删的目录" if orphans_only else f"最大的 {min(top, len(listed))} 个"
        lines.append(heading + "：")
        # 孤儿按体积降序（清理时先捡大的），全量清单按体积降序同理
        for item in sorted(listed, key=lambda entry: entry.size, reverse=True)[:top]:
            label = item.status or "状态未知"
            lines.append(
                f"  {human_size(item.size):>9}  {item.name}  {label}  {_stamp(item.modified)}"
            )
        if len(listed) > top:
            lines.append(f"  ……还有 {len(listed) - top} 个（用 --top 调大）")
        return lines


def _read_meta(path: Path) -> tuple[str, str]:
    """从 `run.json` 取 run_id 与状态。读不出来就当没有（盘点不该被脏文件打断）。"""
    try:
        payload = read_json(path / RUN_JSON)
    except (FileNotFoundError, ValueError):
        return "", ""
    if not isinstance(payload, dict):
        return "", ""
    raw_id = payload.get("id")
    run_id = raw_id if isinstance(raw_id, str) and RUN_ID.fullmatch(raw_id) else ""
    status = payload.get("status")
    return run_id, status if isinstance(status, str) else ""


def _measure(path: Path) -> tuple[int, int]:
    total, files = 0, 0
    for child in path.rglob("*"):
        try:
            if child.is_file():
                total += child.stat().st_size
                files += 1
        except OSError:
            # 共享盘上正被改写的文件会在 stat 前消失。少算一个文件不影响判断，
            # 抛出去会让整次盘点白跑。
            continue
    return total, files


def inspect(
    root: Path, known_ids: set[str] | frozenset[str] | None, *, measure: bool = True
) -> Report:
    """扫一遍工作区根。

    `known_ids=None` 表示「没读到档案表」，此时不判断哪些目录是孤儿。

    `measure=False` 只数目录不统计体积：共享盘上逐个 `stat` 是分钟级的，
    启动期或只想看清单时可以关掉（那三行合计会显示 0 B）。
    """
    known = frozenset(known_ids) if known_ids is not None else None
    if not root.is_dir():
        return Report(root=root, workspaces=(), known_ids=known)
    found: list[Workspace] = []
    for run_json in root.rglob(RUN_JSON):
        path = run_json.parent
        run_id, status = _read_meta(path)
        if not run_id and RUN_ID.fullmatch(path.name):
            # `run.json` 坏了但目录名就是 run_id：仍然认得出归属，
            # 否则这条会在盘点上变成「孤儿」，误导人删掉一份真实档案。
            run_id = path.name
        size, files = _measure(path) if measure else (0, 0)
        try:
            modified = path.stat().st_mtime
        except OSError:
            modified = 0.0
        found.append(
            Workspace(
                path=path,
                run_id=run_id,
                status=status,
                size=size,
                files=files,
                modified=modified,
            )
        )
    return Report(root=root, workspaces=tuple(found), known_ids=known)
