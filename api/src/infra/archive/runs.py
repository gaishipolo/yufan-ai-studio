"""运行工作区：执行器要真实目录，所以它不归归档存储管。

桥接层（`libtv_runtime/bridge.mjs`）要求 `run/inputs/*` 是真实文件、还要重算
sha256 对上快照，ffmpeg 派生预览也要真实路径。对象存储交不出路径，把 run 目录
硬搬进桶只会把执行器拆成两半。边界因此这样划：

- **归档存储（可远端）**：内容 —— 素材 blob、预览、封装元数据、运行索引指针
- **运行工作区（必须本地）**：过程 —— inputs / outputs / records / review

索引指针仍留在归档存储（`catalog/runs/<id>.json`），所以任务列表与启动恢复
不依赖本机目录；反过来，指针在、工作区不在时，列表跳过该条、单条返回 404 ——
这正是「换了台机器」该有的表现，而不是 500。

`REPLACEMENT_RUNS_ROOT` 指定工作区。本地驱动留空时沿用 `<归档根>/runs`，
存量档案不需要搬家；远端驱动没有本地根，留空则落到 `api/var/runs`。
"""

from __future__ import annotations

import re
from pathlib import Path

from core.config import settings
from core.exceptions import BadRequest, NotFound
from infra.archive.base import ArchiveStorage, within

# 索引指针前缀：留在归档存储里，枚举走 `list_keys`（远端驱动同样可用）
INDEX_PREFIX = "catalog/runs"

RUN_ID = re.compile(r"[a-f0-9]{32}")
_LOCK_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")

# api/src/infra/archive/runs.py → api/var/runs
_DEFAULT_LOCAL_RUNS = Path(__file__).parents[3] / "var" / "runs"

# 旧指针存的是「相对归档根」的路径（`runs/2026/11/06/...`），新指针存「相对工作区根」
# （`2026/11/06/...`）。读取端两种都认，省掉一次数据迁移。
_LEGACY_PREFIX = "runs/"


def runs_root() -> Path:
    """工作区根目录。每次读配置，不缓存 —— 测试会改它。"""
    raw = settings.replacement_runs_root.strip()
    if raw:
        return Path(raw).resolve()
    if settings.archive_driver == "local":
        from infra.archive import get_archive_storage  # 延迟导入：本模块被 __init__ 引用

        return get_archive_storage().root() / "runs"
    return _DEFAULT_LOCAL_RUNS


def index_key(run_id: str) -> str:
    return f"{INDEX_PREFIX}/{check_id(run_id)}.json"


def check_id(run_id: str) -> str:
    if not RUN_ID.fullmatch(run_id):
        raise NotFound("调用档案不存在")
    return run_id


def relative_key(path: Path) -> str:
    """工作区目录 → 存进指针的 key。"""
    try:
        return path.resolve().relative_to(runs_root()).as_posix()
    except ValueError as exc:
        raise BadRequest("运行目录不在工作区内，无法登记索引") from exc


def read_pointer(storage: ArchiveStorage, run_id: str) -> str:
    """读指针里的相对 key；不存在按「档案不存在」处理。"""
    key = index_key(run_id)
    if not storage.exists(key):
        raise NotFound("调用档案不存在")
    payload = storage.read(key)
    raw = payload.get("path") if isinstance(payload, dict) else None
    if not isinstance(raw, str) or not raw:
        raise NotFound("调用档案不存在")
    return raw


def save_pointer(storage: ArchiveStorage, run_id: str, key: str) -> None:
    storage.write(index_key(run_id), {"path": key})


def run_dir(storage: ArchiveStorage, run_id: str) -> Path:
    """指针 → 工作区目录；目录缺失时 404 而不是把空路径交给调用方。"""
    path = within(runs_root(), _strip_legacy(read_pointer(storage, run_id)))
    if not (path / "run.json").is_file():
        raise NotFound("调用档案不存在")
    return path


def _strip_legacy(raw: str) -> str:
    return raw[len(_LEGACY_PREFIX) :] if raw.startswith(_LEGACY_PREFIX) else raw


def existing_runs() -> list[Path]:
    """工作区里所有真实存在的运行目录。

    只用于「指针丢了要补登记」这种修复动作：正常发现走指针，不走目录。
    """
    root = runs_root()
    if not root.is_dir():
        return []
    return sorted(folder.parent for folder in root.rglob("run.json") if folder.is_file())


def lock_path(name: str) -> Path:
    """本机锁文件：执行器互斥、发布串行化都落在工作区旁边，不写进远端存储。"""
    if not _LOCK_NAME.fullmatch(name):
        raise BadRequest("锁名无效")
    return runs_root() / ".locks" / f"{name}.lock"
