"""运行工作区的目录约定与提示词编译。

**为什么单独一个文件**：这两件事都不认识数据库，也不懂业务规则，只处理
「路径」与「调 Node 编译一段文字」。混进 `replacement_service` 会让那个文件
里同时出现 SQL、文件 IO 和子进程，而它们的失败模式完全无关。

## 工作区布局

```
RUNS_ROOT/
└── <run_id>/                 新档案：目录名就是 run_id
    ├── inputs/               参考图与原片（执行器要真实文件）
    ├── outputs/generated.mp4 成片
    ├── records/              执行回执、错误分类
    ├── review/review.json    评价
    └── code/runtime/         提示词编译器快照（冻结证据）
```

旧档案在 `2026/11/06/product-<id>/` 下，位置由指针决定，`run_path()` 照旧能打开。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from core.exceptions import BadRequest, UpstreamError
from infra.archive.base import read_json, write_json
from infra.archive.runs import check_id, lock_path, relative_key, runs_root

RUNTIME = Path(__file__).parent / "replacement_runtime"
# 执行器本体在 infra 层：它是「怎么提交到供应商」的实现，不属于业务模块。
BRIDGE = Path(__file__).parents[2] / "infra/ai/libtv_runtime/bridge.mjs"

FOLDERS = ("inputs", "outputs", "records", "review", "code")


class Compilation(BaseModel):
    model_config = ConfigDict(extra="allow")
    prompt: str
    compiler_version: str
    # **不是编译器算的**：编译器只交 `template_sha256`（哈希模板）和
    # `spec_sha256`（哈希输入），而 `prepare` 之后还会注入文本正文、改写引用写法。
    # 这一段字节的摘要由 `replacement_service` 在所有后处理之后填进来，
    # 再与 `prompt.txt`、`replacement_runs.prompt_sha256` 三处保持一致。
    # 留空默认值是为了让「还没算」和「算出来是空」可区分——空串永远不是合法摘要。
    prompt_sha256: str = ""


def run_path(run_id: str) -> Path:
    """工作区目录：由指针（`catalog/runs/<id>.json`）定位。

    **指针留着，业务状态不留**：指针只有一个 `{"path": ...}`，它回答「文件在哪」，
    是执行器协议的一部分（`bridge.mjs` 要真实目录）；而「这次替换到哪一步了」
    已经搬进 `replacement_runs`。两者分工明确，所以不重复存路径。
    """
    from infra.archive import get_archive_storage, run_dir

    return run_dir(get_archive_storage(), check_id(run_id))


def ensure_layout(path: Path) -> None:
    for folder in FOLDERS:
        (path / folder).mkdir(parents=True, exist_ok=True)


def create_workspace(run_id: str, state: dict[str, object]) -> Path:
    """新档案的工作区：目录、`run.json`、索引指针，三件必须一起建。

    **漏了指针的后果是整条链路 404**：`run_path()` 先读指针再校验 `run.json`，
    只建目录不算「档案存在」。所以创建动作集中在这里，谁新建 run 谁调一次，
    不要各自 `ensure_layout` 完事。
    """
    from infra.archive import get_archive_storage, save_pointer

    path = runs_root() / check_id(run_id)
    ensure_layout(path)
    write_state(path, state)
    save_pointer(get_archive_storage(), run_id, relative_key(path))
    return path


def run_lock(run_id: str):
    """与执行器互斥的锁：准备、提交、回执写入不能交叉。"""
    return lock_path(f"run-{check_id(run_id)}")


def write_state(path: Path, state: dict[str, object]) -> None:
    """把业务状态同步进 `run.json`——执行器只认这个文件。

    **为什么要有这一步**：执行器（`bridge.mjs` 与 `libtv_process`）不知道数据库，
    它读 `run.json` 的 `status` / `billing_mode` 决定「还能不能提交」。所以数据库
    是权威、`run.json` 是给执行器的投影，由业务层单向写下去，再把执行器的回执
    读回来（`read_state`）。不是双写——`run.json` 从不反推业务状态。
    """
    write_json(path / "run.json", state)


def read_state(path: Path) -> dict[str, object]:
    """读执行器写下的回执；文件不在（比如从没提交过）时返回空字典。

    调用方要能区分「没写过」和「写成空」：这里不造默认值，缺失就是缺失。
    """
    try:
        # 走 `read_json` 而不是自己 `read_text`：共享盘上 `rename` 不保证 POSIX 语义，
        # 桥接层每次 `update()` 重写 `run.json` 的瞬间路径可能不存在。自己读的话那个
        # 窗口会漏成 500（轮询期间真的见过），而调用方只想知道状态。
        value = read_json(path / "run.json")
    except (FileNotFoundError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}



def input_checksums(path: Path) -> dict[str, str]:
    """`inputs/input-map.json` → `{相对路径: sha256}`，执行器拿它验快照没被换过。

    从清单派生而不是重算一遍字节：素材 key 本身就是摘要，清单里那份就是当初
    物化出来的字节的摘要；而重算要把原片再读一遍（几百 MB）。
    """
    try:
        entries = read_json(path / "inputs" / "input-map.json")
    except (OSError, ValueError):
        return {}
    if not isinstance(entries, list):
        return {}
    return {
        str(entry["file"]): str(entry["sha256"])
        for entry in entries
        if isinstance(entry, dict) and entry.get("file") and entry.get("sha256")
    }


def compile_prompt(spec: dict[str, object], *, node: str) -> Compilation:
    """调 Node 编译器把结构化输入变成一段提示词。

    超时 30 秒：编译器是纯字符串拼装，正常耗时在百毫秒级；卡到 30 秒说明
    Node 环境坏了，继续等只会让整个请求挂在那里——用户看到的是一个不动的转圈，
    而不是「编译器不可用」。
    """
    try:
        result = subprocess.run(
            [node, str(RUNTIME / "workbench/compile.mjs")],
            input=json.dumps(spec),
            capture_output=True,
            encoding="utf-8",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpstreamError("提示词编译器不可用，请检查 Node 配置后继续") from exc
    if result.returncode:
        raise BadRequest("提示词编译失败，请检查参考素材和配置")
    return Compilation.model_validate_json(result.stdout)
