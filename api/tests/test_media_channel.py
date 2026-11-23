"""素材通道决策：按素材类型分别选、可行性前置校验、超限回退。"""

from __future__ import annotations

import pytest

from core.exceptions import BadRequest
from infra.media_channel import (
    MediaChannel,
    choose_channel,
    validate_declaration,
)

# 百炼的真实口径（§3.1.4 核查上游文档后）
BAILIAN_ACCEPTED = {
    "image": frozenset({MediaChannel.BASE64, MediaChannel.OSS, MediaChannel.PUBLIC_URL}),
    "audio": frozenset({MediaChannel.OSS, MediaChannel.PUBLIC_URL}),
}


def test_bailian_image_prefers_base64_audio_falls_to_oss() -> None:
    """同一驱动混合通道：图片 Base64、音频 OSS —— bool 声明表达不了这个。"""
    assert choose_channel(kind="image", accepted=BAILIAN_ACCEPTED) == MediaChannel.BASE64
    assert choose_channel(kind="audio", accepted=BAILIAN_ACCEPTED) == MediaChannel.OSS


def test_preferred_channel_overrides_default() -> None:
    assert (
        choose_channel(
            kind="image",
            accepted=BAILIAN_ACCEPTED,
            preferred={"image": MediaChannel.OSS},
        )
        == MediaChannel.OSS
    )


def test_oversized_image_falls_back_to_oss() -> None:
    """15MB 超项目 12MB 上限 → 自动回退 OSS。"""
    assert (
        choose_channel(
            kind="image",
            accepted=BAILIAN_ACCEPTED,
            size_bytes=15 * 1024 * 1024,
        )
        == MediaChannel.OSS
    )


def test_all_channels_exhausted_raises() -> None:
    with pytest.raises(BadRequest, match="超出所有可用通道"):
        choose_channel(
            kind="image",
            accepted={"image": frozenset({MediaChannel.BASE64})},
            size_bytes=20 * 1024 * 1024,
        )


def test_public_url_declared_without_domain_fails_startup() -> None:
    with pytest.raises(BadRequest, match="未配置公网域名"):
        validate_declaration(provider="bailian", accepted=BAILIAN_ACCEPTED)


def test_public_url_with_domain_passes() -> None:
    validate_declaration(
        provider="bailian",
        accepted=BAILIAN_ACCEPTED,
        public_base_url="https://cdn.example.com",
        oss_upload_supported=True,
    )


def test_oss_without_upload_credential_fails_startup() -> None:
    with pytest.raises(BadRequest, match="上传凭据"):
        validate_declaration(
            provider="bailian",
            accepted={"image": frozenset({MediaChannel.OSS})},
            public_base_url="https://cdn.example.com",
            oss_upload_supported=False,
        )


def test_empty_declaration_fails() -> None:
    with pytest.raises(BadRequest, match="没有声明任何可用通道"):
        validate_declaration(provider="bad", accepted={"image": frozenset()})


def test_agnes_only_base64() -> None:
    """Agnes 上游只收 data URI / 公网 URL，且我们给不出公网 URL → 只有 Base64。"""
    accepted = {"image": frozenset({MediaChannel.BASE64})}
    assert choose_channel(kind="image", accepted=accepted) == MediaChannel.BASE64
    validate_declaration(provider="agnes", accepted=accepted)  # 没声明 PUBLIC_URL，校验通过
