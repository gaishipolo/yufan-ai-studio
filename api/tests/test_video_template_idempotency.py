from concurrent.futures import ThreadPoolExecutor

import pytest
from test_video_template_binding import record

from core.exceptions import Conflict
from modules.template import video_template_store as store


def test_same_key_and_configuration_returns_original_record(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "CATALOG_PATH", tmp_path / "catalog.json")
    first = store._append_record(record(), False)
    retry = store._append_record(record(id="retry", createdAt="2026-11-10T00:00:00Z"), False)
    assert first["id"] == retry["id"] == "template-1"
    assert store.list_out().total == 1


def test_key_conflict_does_not_append_or_unpin(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "CATALOG_PATH", tmp_path / "catalog.json")
    store._append_record(record(), True)
    with pytest.raises(Conflict, match="稳定标识"):
        store._append_record(record(id="different", prompt="changed"), True)
    assert store.list_out().total == 1
    assert store.list_out().pinned_id == "template-1"


def test_concurrent_retries_only_create_one_template(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "CATALOG_PATH", tmp_path / "catalog.json")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda i: store._append_record(record(id=str(i)), False), range(8)))
    assert len({item["id"] for item in results}) == 1
    assert store.list_out().total == 1


def test_pin_change_does_not_break_original_creation_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "CATALOG_PATH", tmp_path / "catalog.json")
    store._append_record(record(pinned=False), False)
    store.pin("template-1")
    result = store._append_record(record(id="retry", pinned=False), False)
    assert result["id"] == "template-1"
    assert result["pinned"] is True
