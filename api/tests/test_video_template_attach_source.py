"""换样片原片：文件名、时长与生成档位都要跟着资产走。

只写 `assetId` 的后果是「卡片换了、说明没换」：封装管理里仍是上一支片的名字和
秒数，生成档位也还按旧时长取整。但**手调过的档位不能覆盖**——那是人做的决定，
不是时长算出来的，所以只在「档位 == 旧时长取整」时才跟着改。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_video_template_binding import record

from core.exceptions import NotFound
from modules.template import video_template_store as store

NEW_ASSET = "ffeeb073241c5e7d76bf46fc8544e887fe0da464"


def catalog(path: Path, **overrides: object) -> None:
    path.write_text(
        json.dumps({"version": 1, "items": [record(**overrides)]}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_rebinding_follows_the_new_asset_name_and_duration(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "CATALOG_PATH", tmp_path / "catalog.json")
    # 样片 5.312 秒 → 创建时档位取整成 5
    catalog(tmp_path / "catalog.json", generation={"duration": 5, "ratio": "9:16"})
    target = store._bind_source_asset("template-1", NEW_ASSET, "我穿的是米兰之夜3030.mp4", 6.0)

    assert target["sourceVideoAssetId"] == NEW_ASSET
    assert target["sourceVideoName"] == "我穿的是米兰之夜3030.mp4"
    assert target["sourceDurationSec"] == 6.0
    # 5 恰好是旧时长取整，说明它是算出来的，跟着新时长走
    assert target["generation"]["duration"] == 6
    assert target["generation"]["ratio"] == "9:16"


def test_hand_tuned_duration_survives_rebinding(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "CATALOG_PATH", tmp_path / "catalog.json")
    catalog(tmp_path / "catalog.json", generation={"duration": 8, "ratio": "9:16"})
    target = store._bind_source_asset("template-1", NEW_ASSET, "新片.mp4", 6.0)
    assert target["sourceDurationSec"] == 6.0
    assert target["generation"]["duration"] == 8


def test_missing_template_and_missing_duration(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "CATALOG_PATH", tmp_path / "catalog.json")
    catalog(tmp_path / "catalog.json", generation={"duration": 5, "ratio": "9:16"})
    with pytest.raises(NotFound):
        store._bind_source_asset("missing", NEW_ASSET, "新片.mp4", 6.0)
    # 探测不到时长时只换 id 和名字，不拿 0 覆盖已有档位
    target = store._bind_source_asset("template-1", NEW_ASSET, "新片.mp4", 0)
    assert target["generation"]["duration"] == 5
    assert target["sourceDurationSec"] == 5.312
