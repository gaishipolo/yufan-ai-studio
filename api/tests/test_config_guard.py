"""生产环境配置守卫：不安全配置必须在启动期炸掉。"""

from __future__ import annotations

import pytest

from core.config import Settings

_SAFE_PROD = {
    "app_env": "prod",
    "debug": False,
    "allow_anonymous": False,
    "jwt_secret": "9f" * 32,
    "cors_origins": ["https://studio.example.com"],
}


def _prod(**overrides: object) -> Settings:
    return Settings(**(_SAFE_PROD | overrides))  # type: ignore[arg-type]


def test_safe_prod_boots() -> None:
    settings = _prod()
    assert settings.app_env == "prod"
    assert settings.anonymous_allowed is False


def test_prod_rejects_anonymous() -> None:
    with pytest.raises(ValueError, match="ALLOW_ANONYMOUS"):
        _prod(allow_anonymous=True)


def test_prod_rejects_sample_secret() -> None:
    with pytest.raises(ValueError, match="JWT_SECRET"):
        _prod(jwt_secret="change-me-in-production-please-use-openssl-rand-hex-32")


def test_prod_rejects_short_secret() -> None:
    with pytest.raises(ValueError, match="长度不足"):
        _prod(jwt_secret="short")


def test_prod_rejects_wildcard_cors() -> None:
    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        _prod(cors_origins=["*"])


def test_prod_rejects_debug() -> None:
    with pytest.raises(ValueError, match="DEBUG"):
        _prod(debug=True)


def test_dev_stays_lenient() -> None:
    """dev 不做生产校验，否则本地开发会被卡死。"""
    settings = Settings(app_env="dev", debug=True, allow_anonymous=True)
    assert settings.anonymous_allowed is True


def test_default_runtime_paths_do_not_depend_on_cwd() -> None:
    """默认 sqlite / 存储路径必须是绝对路径（锚定 api/）。

    相对路径 `./var/app.db` 会随 cwd 变化：在 api/ 下启动读到的是正式开发库，
    在 api/src 下启动则新建一个空库——登录 401、列表为空，但数据其实还在。
    """
    from pathlib import Path

    db_default = str(Settings.model_fields["db_url"].default)
    storage_default = str(Settings.model_fields["storage_local_root"].default)

    db_needle = db_default.replace("\\", "/")
    assert "var/app.db" in db_needle
    db_path = Path(db_default.split("///", 1)[1]) if "///" in db_default else Path(db_default)
    assert db_path.is_absolute(), f"db_url 默认值不是绝对路径: {db_default}"

    assert "var/storage" in storage_default.replace("\\", "/")
    assert Path(storage_default).is_absolute(), f"storage 默认值不是绝对路径: {storage_default}"
