"""从归档快照读取核对摘要，旧档案缺失时不以当前素材冒充实际输入。"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, TypeAdapter

from core.exceptions import NotFound
from infra.archive import within
from modules.template.replacement_repository import ReplacementManifest, ReplacementRepository
from modules.template.replacement_schema import (
    PrepareReplacement,
    ReplacementInputAsset,
    ReplacementInputSummary,
    ReplacementPack,
)


class InputBinding(BaseModel):
    id: str
    name: str
    role: str
    type: str
    file: str


def bindings(path: Path, repo: ReplacementRepository) -> list[InputBinding]:
    file = path / "inputs/input-map.json"
    if not file.is_file():
        return []
    return TypeAdapter(list[InputBinding]).validate_python(repo.storage.read(file))


def input_summary(
    path: Path,
    repo: ReplacementRepository,
    meta: ReplacementManifest | None = None,
) -> ReplacementInputSummary | None:
    """优先复用 prepare 时写进 run.json 的快照，避免列表逐条回读输入文件。"""
    meta = meta or repo.load(path)
    if meta.input_summary is not None:
        return meta.input_summary
    configuration = path / "inputs/configuration.json"
    if not configuration.is_file():
        return None
    config = PrepareReplacement.model_validate(repo.storage.read(configuration))
    pack_file = path / "inputs/reference-pack.json"
    cover = None
    if pack_file.is_file():
        pack = ReplacementPack.model_validate(repo.storage.read(pack_file))
        cover = pack.cover_reference_id or next(iter(pack.reference_ids), None)
    return ReplacementInputSummary(
        cover_reference_id=cover,
        wardrobe_mode=config.wardrobe_mode,
        replacement_scope=config.replacement_scope,
        notes=config.notes,
        assets=[
            ReplacementInputAsset(
                id=item.id,
                name=item.name,
                role=item.role,
                type=item.type,
                url=f"/api/v1/templates/replacement-runs/{meta.id}/inputs/{item.id}",
            )
            for item in bindings(path, repo)
        ],
    )


def input_file(run_id: str, asset_id: str, owner: str) -> Path:
    repo = ReplacementRepository()
    path, _ = repo.owned(run_id, owner)
    binding = next((item for item in bindings(path, repo) if item.id == asset_id), None)
    if binding is None:
        raise NotFound("归档输入不存在")
    result = within(path, binding.file)
    if not result.is_relative_to(path / "inputs") or not result.is_file():
        raise NotFound("归档输入不存在")
    return result
