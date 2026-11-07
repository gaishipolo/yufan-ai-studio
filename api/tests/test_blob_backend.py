"""两条物理通路的共享后端：一个 client、可单桶可分桶。

以前 storage/s3.py 与 archive/s3.py 各建一份 boto3 client、各声明一份连接参数，
改一处连接配置要改两个文件；`S3_ARCHIVE_CACHE_DIR` 更因为字段名没带前缀，
实际环境变量是 `S3_CACHE_DIR`，文档与代码长期对不上。这里锁住这两个事实。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from core.exceptions import UpstreamError
from infra.archive.s3 import ArchiveS3Settings, S3ArchiveStorage
from infra.blob import client_cache_info, reset_client_cache, s3_client


def _fake_factory() -> Any:
    def make(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"args": args, "kwargs": kwargs}

    return make


def test_same_connection_reuses_one_client() -> None:
    reset_client_cache()
    factory = _fake_factory()
    kwargs = dict(
        endpoint="http://127.0.0.1:9000",
        access_key="key",
        secret_key="secret",
        region="us-east-1",
        use_ssl=False,
        verify=False,
        factory=factory,
    )
    first = s3_client(**kwargs)
    second = s3_client(**kwargs)
    assert first is second
    assert client_cache_info()["currsize"] == 1
    assert client_cache_info()["hits"] >= 1


def test_different_connection_gets_its_own_client() -> None:
    reset_client_cache()
    factory = _fake_factory()
    base = dict(
        endpoint="http://127.0.0.1:9000",
        access_key="key",
        secret_key="secret",
        region="us-east-1",
        use_ssl=False,
        verify=False,
        factory=factory,
    )
    first = s3_client(**base)
    second = s3_client(**{**base, "endpoint": "http://other:9000"})
    assert first is not second
    assert client_cache_info()["currsize"] == 2


def _settings(**overrides: Any) -> ArchiveS3Settings:
    return ArchiveS3Settings(
        endpoint="http://127.0.0.1:9000",
        access_key="key",
        secret_key="secret",
        use_ssl=False,
        **overrides,
    )


def test_separate_mode_requires_its_own_bucket() -> None:
    with pytest.raises(UpstreamError, match="S3_ARCHIVE_BUCKET"):
        S3ArchiveStorage(settings=_settings(bucket_mode="separate", archive_bucket=""))


def test_invalid_bucket_mode_is_rejected() -> None:
    with pytest.raises(UpstreamError, match="S3_BUCKET_MODE"):
        S3ArchiveStorage(settings=_settings(bucket_mode="whatever", archive_bucket="arc"))


def test_shared_mode_uses_material_bucket_with_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "infra.storage.s3.get_s3_settings", lambda: SimpleNamespace(bucket="media")
    )
    monkeypatch.setattr("infra.archive.s3.boto3.client", _fake_factory())
    reset_client_cache()
    storage = S3ArchiveStorage(settings=_settings(bucket_mode="shared"))
    assert storage._bucket == "media"
    assert storage._prefix.startswith("archive")
    assert storage._client is not None
    reset_client_cache()


def test_cache_dir_setting_matches_documented_env_key() -> None:
    """`S3_ARCHIVE_CACHE_DIR` 必须真能解析，别再出现「文档写了但代码不认」。"""
    from infra.driver_config import driver_config

    prefix = str(ArchiveS3Settings.model_config.get("env_prefix"))
    assert prefix == "S3_"
    assert "archive_cache_dir" in ArchiveS3Settings.model_fields
    assert "cache_dir" not in ArchiveS3Settings.model_fields
    assert driver_config("S3")["env_prefix"] == "S3_"
