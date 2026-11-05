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
