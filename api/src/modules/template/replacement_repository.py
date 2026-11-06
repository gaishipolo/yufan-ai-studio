"""替换调用仓储：类型化旧 JSON，并保持 Z 盘目录兼容。"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel

from core.exceptions import NotFound
from core.media import StoredMedia
from infra.ai.libtv_state import LibTVState
from infra.archive import ArchiveStorage, get_archive_storage, lease
from modules.template.replacement_schema import ReplacementKind, ReplacementPack


class RunPointer(BaseModel):
    path: str


class ReplacementManifest(LibTVState):
    kind: ReplacementKind
    target: str
    owner_id: str
    fingerprint: str
    review_status: str = "pending"
    model: str = "wanx3.0"
    resolution: str = "720P"
    duration: int = 0
    billing_mode: str = "free"
    max_credits: int = 0
    file_checksums: dict[str, str] = {}
    # 自动重试计数（执行器繁忙 / 成片下载失败）。旧的 run.json 没有这个字段，
    # 默认 0 即「还没重试过」；人工再点「继续」会清零，重新拿一份额度。
    retry_count: int = 0


class PublishedPack(ReplacementPack):
    owner_id: str


class ReplacementRepository:
    def __init__(self, storage: ArchiveStorage | None = None) -> None:
        self.storage = storage or get_archive_storage()

    def path(self, run_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise NotFound("调用档案不存在")
        pointer = self.storage.resolve(f"catalog/runs/{run_id}.json")
        if not pointer.is_file():
            raise NotFound("调用档案不存在")
        return self.storage.resolve(RunPointer.model_validate(self.storage.read(pointer)).path)

    def load(self, path: Path) -> ReplacementManifest:
        return ReplacementManifest.model_validate(self.storage.read(path / "run.json"))

    def save(self, path: Path, meta: ReplacementManifest) -> None:
        self.storage.write(path / "run.json", meta.model_dump(mode="json"))

    @contextmanager
    def edit(self, run_id: str) -> Iterator[ReplacementManifest]:
        """与独立监督进程互斥，禁止覆盖它刚写入的提交回执。"""
        path = self.path(run_id)
        with lease(path / "records/executor.lease"):
            meta = self.load(path)
            yield meta
            self.save(path, meta)

    def index(self, path: Path, run_id: str) -> None:
        self.storage.write(
            self.storage.resolve(f"catalog/runs/{run_id}.json"),
            {"path": path.relative_to(self.storage.root()).as_posix()},
        )

    def owned(self, run_id: str, owner: str) -> tuple[Path, ReplacementManifest]:
        path = self.path(run_id)
        meta = self.load(path)
        if meta.owner_id != owner:
            raise NotFound("调用档案不存在")
        return path, meta

    def media(self, asset_id: str, owner: str) -> StoredMedia:
        if not re.fullmatch(r"[a-f0-9]{32}", asset_id):
            raise NotFound("素材不存在")
        path = self.storage.resolve(f"catalog/assets/{asset_id}.json")
        if not path.is_file():
            raise NotFound("素材不存在")
        media = StoredMedia.model_validate(self.storage.read(path))
        if media.owner_id != owner:
            raise NotFound("素材不存在")
        return media

    def packs(self, owner: str) -> list[ReplacementPack]:
        rows = [
            PublishedPack.model_validate(self.storage.read(p))
            for p in (self.storage.root() / "catalog/packs").glob("*.json")
        ]
        return [ReplacementPack.model_validate(p.model_dump()) for p in rows if p.owner_id == owner]

    def all_paths(self) -> list[Path]:
        return [
            self.storage.resolve(RunPointer.model_validate(self.storage.read(p)).path)
            for p in (self.storage.root() / "catalog/runs").glob("*.json")
        ]

    def repair_indexes(self) -> None:
        """断连可能中断索引写入；仅补登记，不修改历史输入或上游任务。"""
        for file in (self.storage.root() / "runs").glob("*/*/*/*/run.json"):
            meta = self.load(file.parent)
            self.index(file.parent, meta.id)
