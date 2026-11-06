"""素材包封面使用新素材 ID，不误取多视图或旧 material_id。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.config import settings
from modules.template.replacement_repository import ReplacementRepository


@pytest.mark.parametrize(
    "cover,bindings,expected",
    [
        (None, [{"role": "multiview"}, {"role": "front"}, {"role": "detail"}], "front"),
        ("detail", [{"role": "front"}], "detail"),
        ("old-material-id", [{"role": "multiview"}, {"role": "front"}], "front"),
        (None, [], "back"),
    ],
)
def test_pack_cover_resolves_owned_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    cover: str | None, bindings: list[dict[str, str]], expected: str,
) -> None:
    monkeypatch.setattr(settings, "media_archive_root", str(tmp_path))
    repo = ReplacementRepository()
    repo.storage.write(tmp_path / "catalog/packs/pack.json", {
        "id": "pack", "ownerId": "owner", "kind": "product", "name": "产品",
        "referenceIds": ["back", "front", "detail"],
        "coverReferenceId": cover, "bindings": bindings,
    })
    result = repo.packs("owner")[0]
    assert result.cover_reference_id == expected
    assert result.reference_ids == ["back", "front", "detail"]
    assert repo.packs("other") == []
