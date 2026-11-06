"""替换调用档案仓储：档案在运行工作区，索引指针在归档存储。

档案本身（`run.json`、inputs、outputs、records）是执行器的过程文件，必须落在
本机工作区（见 `infra/archive/runs.py`）；`catalog/runs/<id>.json` 只是一行指针，
留在归档存储里，所以列表与启动恢复不依赖「有本地根目录」。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from core.exceptions import NotFound
from core.media import StoredMedia
from infra.ai.libtv_state import RUN_LOCK, LibTVState
from infra.archive import (
    ArchiveStorage,
    existing_runs,
    get_archive_storage,
    lease,
    read_pointer,
    run_dir,
    save_pointer,
)
from infra.archive.runs import check_id, relative_key
from modules.template.replacement_schema import (
    ReplacementInputSummary,
    ReplacementKind,
    ReplacementPack,
)


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
        return run_dir(self.storage, run_id)

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
        save_pointer(self.storage, run_id, relative_key(path))

    def owned(self, run_id: str, owner: str) -> tuple[Path, ReplacementManifest]:
        path = self.path(run_id)
        meta = self.load(path)
        if meta.owner_id != owner:
            raise NotFound("调用档案不存在")
        return path, meta

    def media(self, asset_id: str, owner: str) -> StoredMedia:
        if not re.fullmatch(r"[a-f0-9]{32}", asset_id):
            raise NotFound("素材不存在")
        key = f"catalog/assets/{asset_id}.json"
        if not self.storage.exists(key):
            raise NotFound("素材不存在")
        media = StoredMedia.model_validate(self.storage.read(key))
        if media.owner_id != owner:
            raise NotFound("素材不存在")
        return media

    def packs(self, owner: str) -> list[ReplacementPack]:
        rows = [
            PublishedPack.model_validate(self.storage.read(key))
            for key in self.storage.list_keys("catalog/packs", suffix=".json")
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
        """所有还读得到的档案。

        指针坏了/工作区不在这台机器上时不抛错，直接跳过：一条脏指针不该让
        整个任务列表 500（以前的 `catalog/runs` 枚举没有这个容错，因为指针
        与目录同盘，缺一个就是盘掉了，本来就该报错）。
        """
        paths: list[Path] = []
        for key in self.storage.list_keys("catalog/runs", suffix=".json"):
            run_id = key.rsplit("/", 1)[-1][: -len(".json")]
            try:
                paths.append(run_dir(self.storage, check_id(run_id)))
            except (NotFound, OSError):
                continue
        return paths

    def repair_indexes(self) -> None:
        """断连可能中断索引写入；仅补登记，不修改历史输入或上游任务。"""
        for path in existing_runs():
            try:
                meta = self.load(path)
            except (OSError, ValueError):
                continue
            self.index(path, meta.id)

    def pointer_of(self, run_id: str) -> str:
        return read_pointer(self.storage, run_id)
