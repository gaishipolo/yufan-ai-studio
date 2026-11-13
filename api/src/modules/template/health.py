"""模板库的部署自检：`ModuleSpec.doctor` 指向这里。

四项检查，各对应一种「不报错但会慢慢坏」的部署状态：

- `template.node`：`node` 在不在 PATH、版本与 `vendor/manifest.json` 的
  `verified_node` 是否一致。版本不对时用户只看到「提示词编译器不可用」，
  真正原因在 Node 侧，日志里不会有版本号。
- `template.vendor`：按清单逐个重算 sha256。适配器是只读快照，被改一个字
  运行时不会立刻报错，只会在某条分支上给出不同文案。
- `template.runs_root`：归档驱动不是 local、却没配 `REPLACEMENT_RUNS_ROOT`。
  `runs_root()` 这时会**静默回落到本机**（`infra/archive/runs.py:50`），
  多实例下表现为「生成成功但换台机器打不开」。本节只把它报成 FAIL，
  **不拦启动**；「启动即失败」是计划步 5 的事，别在这里改行为。
- `template.dirs`：打包器进度目录、运行工作区两个位置可写。
  它们默认在 `api/var` 下，多机部署要指向共享盘；不可写时要等到第一次
  封装 / 准备才发现，而那时用户已经填完整个表单。
  （视频模板目录**已不在列表里**：它随问题 A 落表，`catalog.json` 只剩
  导入用的只读副本，不再是运行时目录。）

纪律与其它模块自检一致：只往报告里加自己那几条，不改别人的结论。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from api.module import DoctorReportLike
from core.config import settings
from infra.ai.libtv_process import VENDOR
from infra.archive.runs import runs_root, workspace_needs_attention

__all__ = ["check"]

_NODE_VERSION = re.compile(r"v?(\d+\.\d+\.\d+)")
# .../api/src/modules/template/health.py -> parents[3] = .../api
_API_ROOT = Path(__file__).resolve().parents[3]


def _vendor_manifest() -> dict:
    try:
        raw = (VENDOR / "manifest.json").read_text(encoding="utf-8")
        return json.loads(raw)
    except (OSError, ValueError):
        return {}


def _vendor_gaps() -> tuple[int, list[str]]:
    """返回 (清单文件数, 缺口描述)。缺文件与哈希不符都算缺口。"""
    files = _vendor_manifest().get("files") or {}
    gaps: list[str] = []
    for rel, expected in files.items():
        try:
            actual = hashlib.sha256((VENDOR / rel).read_bytes()).hexdigest()
        except OSError:
            gaps.append(f"{rel} 缺失")
            continue
        if actual != expected:
            gaps.append(f"{rel} 哈希不符")
    return len(files), gaps


def _node_version() -> tuple[str, str]:
    """返回 (版本号, 失败原因)。版本号为空时第二项说明为什么拿不到。"""
    node = settings.node_binary
    executable = shutil.which(node)
    if executable is None:
        return "", f"PATH 里找不到 {node!r}（配 NODE_BINARY，或把 node 加进 PATH）"
    try:
        proc = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "", f"执行 {node!r} 失败：{type(exc).__name__}"
    match = _NODE_VERSION.search(proc.stdout.strip())
    if match is None:
        return "", f"{node!r} --version 输出无法识别：{proc.stdout.strip()[:40]!r}"
    return match.group(1), ""


def _packager_dir() -> Path:
    """与 `template_packager/__main__.py::_state_dir` 同一口径，这里只读不算。"""
    configured = settings.template_packager_state_dir.strip()
    if configured:
        return Path(configured).expanduser()
    return _API_ROOT / "var" / "template-packager"


def _writable_problem(path: Path) -> str:
    """真的写一个临时文件，不用 `os.access`（Windows 上对目录不可靠）。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".doctor-", delete=True):
            pass
    except OSError as exc:
        return f"{path} 不可写（{type(exc).__name__}）"
    return ""


async def check(report: DoctorReportLike) -> None:
    manifest = _vendor_manifest()
    expected_node = str((manifest.get("source") or {}).get("verified_node") or "")

    version, node_problem = _node_version()
    if not version:
        report.add("template.node", "FAIL", f"提示词编译器起不来：{node_problem}")
    elif expected_node and version != expected_node:
        report.add(
            "template.node",
            "FAIL",
            f"Node {version} 与快照声明的 {expected_node} 不一致："
            "编译器行为可能偏离 golden 基线，提示词文案会静默变化",
        )
    else:
        report.add(
            "template.node",
            "PASS",
            f"Node {version}（快照声明 {expected_node or '未声明'}）",
        )

    total, gaps = _vendor_gaps()
    if gaps:
        more = f"，另有 {len(gaps) - 5} 个" if len(gaps) > 5 else ""
        report.add(
            "template.vendor",
            "FAIL",
            f"适配器快照与清单不一致（{len(gaps)}/{total}）："
            + "；".join(gaps[:5])
            + more,
        )
    elif total == 0:
        report.add("template.vendor", "FAIL", f"清单为空或不可读：{VENDOR / 'manifest.json'}")
    else:
        report.add("template.vendor", "PASS", f"{total} 个适配器文件哈希与清单一致")

    root: Path | None = None
    try:
        root = runs_root()
    except Exception as exc:  # noqa: BLE001 - 自检要报告而不是崩掉
        report.add(
            "template.runs_root",
            "FAIL",
            f"运行工作区无法确定：{type(exc).__name__}: {exc}",
        )
    else:
        if workspace_needs_attention():
            report.add(
                "template.runs_root",
                "FAIL",
                "ARCHIVE_DRIVER 不是 local 且 REPLACEMENT_RUNS_ROOT 未配置："
                f"runs_root() 已静默回落到本机 {root}，多实例下"
                "「生成成功但换台机器打不开」",
            )
        else:
            report.add("template.runs_root", "PASS", f"运行工作区 {root}")

    targets: list[tuple[str, Path]] = [
        ("打包器进度目录", _packager_dir()),
    ]
    if root is not None:
        targets.append(("运行工作区", root))
    problems = [msg for msg in (_writable_problem(path) for _, path in targets) if msg]
    if problems:
        report.add("template.dirs", "FAIL", "；".join(problems))
    else:
        report.add(
            "template.dirs",
            "PASS",
            "、".join(name for name, _ in targets) + " 均可写",
        )
