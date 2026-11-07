"""字节完整性巡检与 app doctor：把「静默退化」变成看得见的结论。

重点不是「哈希算得对」，而是三种结果必须分开：完好 / 损坏 / 没查（旧布局）。
把「没查」算成「通过」是最常见的自检自欺。
"""

from __future__ import annotations

import asyncio
import hashlib

from api.diagnostics import _CAS_PROBE, run_doctor
from infra.archive import BlobStore, blob_key, get_archive_storage
from infra.archive.integrity import verify_blobs


def _write_bytes(key: str, data: bytes) -> None:
    path = get_archive_storage().resolve(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_verify_accepts_matching_digest() -> None:
    data = b"yufan-integrity-ok"
    key = blob_key(hashlib.sha256(data).hexdigest(), ".png")
    _write_bytes(key, data)
    try:
        assert BlobStore(get_archive_storage()).verify(key) is True
    finally:
        get_archive_storage().remove(key)


def test_verify_detects_tampered_bytes() -> None:
    """key 没变、内容被换：内容寻址本身拦不住外部覆盖。"""
    data = b"original-bytes"
    key = blob_key(hashlib.sha256(data).hexdigest(), ".png")
    _write_bytes(key, b"tampered-bytes")
    try:
        assert BlobStore(get_archive_storage()).verify(key) is False
        report = verify_blobs(get_archive_storage(), sample=None)
        assert key in report.corrupted
        assert not report.healthy
    finally:
        get_archive_storage().remove(key)


def test_verify_skips_legacy_layout_without_claiming_success() -> None:
    assert BlobStore(get_archive_storage()).verify("library/source/2026/11/old.png") is None


def test_verify_blobs_sampling_is_deterministic() -> None:
    storage = get_archive_storage()
    first = verify_blobs(storage, sample=3)
    second = verify_blobs(storage, sample=3)
    assert first.total == second.total
    assert first.checked == second.checked <= 3


def test_doctor_reports_cas_and_cleans_up_probe() -> None:
    """条件写是发布 CAS 的唯一支点；探针不能留在归档里。"""
    storage = get_archive_storage()
    report = asyncio.run(run_doctor(sample=3))
    names = {check.name: check for check in report.checks}
    assert "archive.cas" in names
    assert names["archive.cas"].level == "PASS", names["archive.cas"].message
    assert "ports.references" in names
    assert "archive.integrity" in names
    assert not storage.exists(_CAS_PROBE)
    assert report.render().startswith("[")
