"""素材通道决策：按素材类型分别选、可行性前置校验、超限回退。"""

from __future__ import annotations

import pytest

from core.exceptions import BadRequest
from infra.media_channel import (
    ChannelPlan,
    MediaChannel,
    MediaKind,
    choose_channel,
    summarize_channels,
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


# libtv 的真实口径：素材落盘到归档工作区，由**本地执行器**读文件。
# 它不是「上游收什么格式」，而是「素材要落到哪里执行器才读得到」。
LIBTV_ACCEPTED = {
    "image": frozenset({MediaChannel.WORKSPACE}),
    "video": frozenset({MediaChannel.WORKSPACE}),
    "audio": frozenset({MediaChannel.WORKSPACE}),
}


def test_workspace_is_chosen_when_it_is_the_only_option() -> None:
    """libtv 只声明工作区 → 就走工作区（不编码、不上传）。"""
    for kind in ("image", "video", "audio"):
        assert choose_channel(kind=kind, accepted=LIBTV_ACCEPTED) == MediaChannel.WORKSPACE


def test_workspace_has_no_base64_size_limit() -> None:
    """工作区是本地盘，不受 Base64 的 12MB 上限约束。"""
    huge = 50 * 1024 * 1024
    assert (
        choose_channel(kind="image", accepted=LIBTV_ACCEPTED, size_bytes=huge)
        == MediaChannel.WORKSPACE
    )


def test_workspace_cannot_be_mixed_with_other_channels() -> None:
    """WORKSPACE 与其它档正交，混声明必须报错 —— 不能静默挑一个。

    静默挑错的后果：选路挑了 BASE64 → 执行器读不到工作区里的文件 →
    任务在提交阶段才炸，且报错与用户输入无关。
    """
    with pytest.raises(BadRequest) as exc:
        validate_declaration(
            provider="libtv",
            accepted={"image": frozenset({MediaChannel.WORKSPACE, MediaChannel.BASE64})},
        )

    assert "WORKSPACE" in str(exc.value)


def test_workspace_declaration_needs_no_credentials() -> None:
    """工作区不涉及公网域名、不涉及 OSS 上传凭据。"""
    validate_declaration(provider="libtv", accepted=LIBTV_ACCEPTED)


def test_libtv_provider_declares_workspace_for_all_kinds() -> None:
    """libtv 驱动的声明与通道决策层的口径一致（防止「驱动改了、这边没改」）。"""
    from modules.model_driver.drivers.libtv import LibTVProvider

    declared = {
        kind: frozenset(channels)
        for kind, channels in LibTVProvider.accepted_media_formats.items()
    }

    assert declared == LIBTV_ACCEPTED


def test_libtv_never_declares_inline_references() -> None:
    """libtv **不得**声明 `inline_references` —— 那会让框架把素材转成 Base64。

    执行器读的是工作区里的真实文件（还要过 sha256 校验），转成 Base64
    就等于把文件抽走。这是设计文档 A6「工作区需求与通道选择正交」的红线。
    """
    from modules.model_driver.drivers.libtv import LibTVProvider

    assert not hasattr(LibTVProvider, "inline_references"), (
        "libtv 依赖工作区里的真实文件，声明 inline_references 会让框架改走内联"
    )


# --- 通道汇总（提交台账落 channel_used 用）---


def test_summarize_channels_collects_unique_channels() -> None:
    """多条素材可能走不同通道 → 汇总成去重后的集合（排序后拼接）。"""
    plans = {
        "a.png": ChannelPlan(kind=MediaKind.IMAGE, channel=MediaChannel.BASE64),
        "b.png": ChannelPlan(kind=MediaKind.IMAGE, channel=MediaChannel.BASE64),
        "c.wav": ChannelPlan(kind=MediaKind.AUDIO, channel=MediaChannel.OSS),
    }
    assert summarize_channels(plans) == "base64,oss"


def test_summarize_channels_is_empty_without_materials() -> None:
    """没有素材就没有通道 → 空串。编一个值会让「重试复用原通道」走错通道。"""
    assert summarize_channels({}) == ""
