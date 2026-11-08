"""归档容量配额：上传前拦截，列表能显示上限与本人实际用量。

配额按「本人未删除的归档素材」算，不能拿列表的 `usedBytes` 顶替——后者在
public / trash 分池里口径完全不同（B4）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from test_asset_api import png_bytes  # noqa: F401
from tests_archive_helpers import archive

from core.config import settings


def test_quota_blocks_upload_and_is_reported(
    client: TestClient, api: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "archive_quota_bytes_per_user", 1)
    response = client.post(
        f"{api}/assets/upload",
        params={"filename": "over-quota.png", "category": "product"},
        content=png_bytes((20, 20), "red"),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 413, response.text
    assert response.json()["details"]["quotaBytes"] == 1

    listing = client.get(f"{api}/assets", params={"scope": "mine"}).json()["data"]
    assert listing["quotaBytes"] == 1
    assert "ownerUsedBytes" in listing


def test_zero_quota_means_unlimited(
    client: TestClient, api: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "archive_quota_bytes_per_user", 0)
    asset = archive.upload(client, "unlimited.png", "product", png_bytes((22, 22), "blue"))
    assert asset["id"]
    listing = client.get(f"{api}/assets", params={"scope": "mine"}).json()["data"]
    assert listing["quotaBytes"] == 0
    assert listing["ownerUsedBytes"] >= asset["sizeBytes"]


def test_trash_does_not_count_toward_quota(
    client: TestClient, api: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "archive_quota_bytes_per_user", 0)
    asset = archive.upload(client, "to-trash.png", "product", png_bytes((20, 20), "green"))
    before = client.get(f"{api}/assets", params={"scope": "mine"}).json()["data"][
        "ownerUsedBytes"
    ]
    assert client.put(
        f"{api}/assets/{asset['id']}/deleted",
        json={"expectedVersion": asset["stateVersion"], "deleted": True},
    ).status_code == 200
    after = client.get(f"{api}/assets", params={"scope": "mine"}).json()["data"][
        "ownerUsedBytes"
    ]
    assert after == before - asset["sizeBytes"]
