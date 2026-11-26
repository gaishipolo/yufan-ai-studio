"""运维页运行时配置：脱敏、原子写、热生效、来源只读。

守卫的是这次事故的根源：白名单漏配后任务成功却进不了草稿箱，而改 .env 又要重启。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from infra.env_settings import clear_cache
from modules.model_driver import runtime_config as rc


@pytest.fixture
def env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把配置读写指向临时 .env，与真实部署隔离。"""
    path = tmp_path / "prod.env"
    path.write_text(
        "# 部署说明：这一行必须原样保留\n"
        "\n"
        "STORAGE_DRIVER=local\n"
        "IMAGE_ASSET_ALLOWED_HOSTS=old.example.com\n"
        "GRSAI_API_KEY=super-secret-value\n"
        "GRSAI_BASE_URL=https://grsaiapi.com\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(rc, "_ENV_PATH", path)
    from infra import env_settings

    monkeypatch.setattr(env_settings, "_ENV_PATH", path)
    # pydantic-settings 的 env_file 在类定义时就固定了，测试里要一起指向临时文件
    from modules.library.ingest import IngestDownloadSettings

    monkeypatch.setitem(IngestDownloadSettings.model_config, "env_file", (path,))
    clear_cache()
    return path


def test_secrets_never_appear_in_listing(env_file: Path) -> None:
    items = {item.key: item for item in rc.list_items()}
    key = items["GRSAI_API_KEY"]

    assert key.secret is True
    assert key.value == "", "密钥明文不能出现在出参"
    assert key.configured is True
    assert key.fingerprint and "super-secret-value" not in key.fingerprint
    assert key.masked == "supe***alue", "只显示前 4 后 4"
    assert "secret" not in key.masked, "掩码不能泄露中间内容"
    assert "super-secret-value" not in str(items), "任何一项都不能带出完整明文"


def test_short_secret_is_fully_masked(env_file: Path) -> None:
    """短到不足以遮住中间时全遮，否则掩码等于明文。"""
    from modules.model_driver.runtime_config import _mask

    assert _mask("short") == "*****"
    assert _mask("") == ""
    assert _mask("123456789") == "1234***6789"


def test_write_keeps_other_lines_and_comments(env_file: Path) -> None:
    result = rc.apply_updates({"IMAGE_ASSET_ALLOWED_HOSTS": "new.example.com, cdn.example.com"})

    text = env_file.read_text(encoding="utf-8")
    assert "updated" in result.__repr__()  # 改动确实发生了
    assert "IMAGE_ASSET_ALLOWED_HOSTS=new.example.com,cdn.example.com" in text
    assert "# 部署说明：这一行必须原样保留" in text
    assert "STORAGE_DRIVER=local" in text
    assert "GRSAI_API_KEY=super-secret-value" in text


def test_write_appends_missing_key(env_file: Path) -> None:
    rc.apply_updates({"LIBRARY_INGEST_ALLOWED_HOSTS": "ingest.example.com"})

    text = env_file.read_text(encoding="utf-8")
    assert "LIBRARY_INGEST_ALLOWED_HOSTS=ingest.example.com" in text
    assert text.count("STORAGE_DRIVER=local") == 1


def test_host_whitelist_rejects_urls_and_paths(env_file: Path) -> None:
    with pytest.raises(ValueError, match="不能带 http"):
        rc.apply_updates({"IMAGE_ASSET_ALLOWED_HOSTS": "https://evil.example.com/path"})

    text = env_file.read_text(encoding="utf-8")
    assert "IMAGE_ASSET_ALLOWED_HOSTS=old.example.com" in text, "校验失败不能改文件"


def test_unknown_key_is_rejected(env_file: Path) -> None:
    with pytest.raises(ValueError, match="不支持编辑"):
        rc.apply_updates({"STORAGE_DRIVER": "s3"})


def test_environment_variable_source_is_readonly(
    env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IMAGE_ASSET_ALLOWED_HOSTS", "from-process.example.com")
    item = next(i for i in rc.list_items() if i.key == "IMAGE_ASSET_ALLOWED_HOSTS")

    assert item.source == "environment"
    assert item.editable is False
    assert item.value == "from-process.example.com"

    result = rc.apply_updates({"IMAGE_ASSET_ALLOWED_HOSTS": "ignored.example.com"})
    assert "IMAGE_ASSET_ALLOWED_HOSTS" in result.skipped
    assert "ignored.example.com" not in env_file.read_text(encoding="utf-8")


def test_secret_empty_value_means_no_change(env_file: Path) -> None:
    result = rc.apply_updates({"GRSAI_API_KEY": ""})
    assert not result.updated

    text = env_file.read_text(encoding="utf-8")
    assert "GRSAI_API_KEY=super-secret-value" in text


def test_whitelist_change_takes_effect_without_restart(env_file: Path) -> None:
    """白名单 getter 走 mtime 指纹缓存：写文件后下一次读就是新值。"""
    from modules.library.ingest import get_ingest_download_settings

    before = get_ingest_download_settings().allowed_hosts
    rc.apply_updates({"LIBRARY_INGEST_ALLOWED_HOSTS": "file2.example.com"})
    after = get_ingest_download_settings().allowed_hosts

    assert before != after
    assert after == "file2.example.com"


def test_discovered_fields_cover_vendor_connections(env_file: Path) -> None:
    """新增供应商只要声明 api_key / base_url，就自动进运维页。"""
    keys = {field.key for field in rc.FIELDS}
    assert "IMAGE_ASSET_ALLOWED_HOSTS" in keys
    assert "LIBRARY_INGEST_ALLOWED_HOSTS" in keys
    # 至少一家供应商的密钥与地址在清单里（由 DriverSettings 自动发现）
    assert any(key.endswith("_API_KEY") for key in keys)
    assert any(key.endswith("_BASE_URL") for key in keys)
