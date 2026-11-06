"""替换调用仓储：类型化旧 JSON，并保持 Z 盘目录兼容。"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel

from core.exceptions import NotFound
from core.media import StoredMedia
from infra.ai.libtv_state import RUN_LOCK, LibTVState
from infra.storage.archive import ArchiveStorage, get_archive_storage, lease
from modules.template.replacement_schema import (
    ReplacementInputSummary,
    ReplacementKind,
    ReplacementPack,
)


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
    # 输入在 prepare 时一次成型并快照，列表/任务中心直接复用，不再逐条回读
    # configuration / reference-pack / input-map。旧档案为 None，由读取方按需重算。
    input_summary: ReplacementInputSummary | None = None


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
        """与独立监督进程互斥，禁止覆盖它刚写入的提交回执。

        锁名沿用执行器的 RUN_LOCK（`.lock` 后缀）：桥接层的 update() 会遍历
        records 给每个文件算 sha256，只跳过 `.lock/.download/.tmp`。
        """
        path = self.path(run_id)
        with lease(path / RUN_LOCK):
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
        return [self._public_pack(p) for p in rows if p.owner_id == owner]

    def _public_pack(self, pack: PublishedPack) -> ReplacementPack:
        cover = pack.cover_reference_id
        if cover not in pack.reference_ids:
            # 迁移时 bindings 与新 reference_ids 按相同顺序写入；不能用旧 material_id。
            front = next(
                (i for i, binding in enumerate(pack.bindings) if binding.role == "front"), 0
            )
            cover = (
                pack.reference_ids[front]
                if front < len(pack.reference_ids)
                else next(iter(pack.reference_ids), None)
            )
        return ReplacementPack.model_validate(pack.model_dump()).model_copy(
            update={"cover_reference_id": cover}
        )

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
