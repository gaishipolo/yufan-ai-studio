"""旧布局回归夹具：测试用字典接口不进入生产代码。"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from infra.archive import checksum, within
from infra.archive.local import LocalArchiveStorage
from modules.asset.archive_repository import ArchiveAssetRepository
from modules.asset.archive_service import ArchiveAssetService


class ArchiveFixtures:
    checksum = staticmethod(checksum)
    within = staticmethod(within)

    def root(self) -> Path:
        return LocalArchiveStorage().root()

    def read(self, path: Path) -> dict:
        value = LocalArchiveStorage().read(path)
        assert isinstance(value, dict)
        return value

    def save(self, path: Path, value: object) -> None:
        LocalArchiveStorage().write(path, value)

    def register(self, path: Path, filename: str,
                 category: Literal["source", "character", "product", "scene", "audio"],
                 owner: str) -> dict:
        return ArchiveAssetRepository().register(path, filename, category, owner).model_dump(
            mode="json", by_alias=True)

    def asset(self, asset_id: str, owner: str) -> dict:
        return ArchiveAssetService().owned(asset_id, owner).model_dump(mode="json", by_alias=True)

    def assets(self, owner: str) -> list:
        return [m for m in ArchiveAssetRepository().list() if m.owner_id == owner]


archive = ArchiveFixtures()
