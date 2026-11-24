"""素材通道声明：解析、旧开关兼容、启动期可行性校验、回退留痕。"""

from __future__ import annotations

from typing import Any

import pytest

from core.exceptions import BadRequest
from infra.media_channel import (
    MediaChannel,
    declared_channels,
    plan_channel,
    preferred_channels,
    record_channel_fallbacks,
)


class _Provider:
    """最小驱动形态：只有声明，没有实现。"""

    name = "p"

    def __init__(self, **attrs: Any) -> None:
        for key, value in attrs.items():
            setattr(self, key, value)


def test_structured_declaration_is_read_as_is() -> None:
    provider = _Provider(
        accepted_media_formats={"image": frozenset({MediaChannel.BASE64})}
    )
    assert declared_channels(provider) == {"image": frozenset({MediaChannel.BASE64})}


def test_legacy_inline_switch_is_translated_to_base64_for_every_kind() -> None:
    """旧开关是「所有类型一个开关」→ 兼容推导成「全部类型走 Base64」。"""
    provider = _Provider(inline_references=True)
    assert declared_channels(provider) == {
        "image": frozenset({MediaChannel.BASE64}),
        "audio": frozenset({MediaChannel.BASE64}),
        "video": frozenset({MediaChannel.BASE64}),
    }


def test_no_declaration_means_no_channels() -> None:
    assert declared_channels(_Provider()) == {}


def test_unknown_channel_name_is_rejected() -> None:
    provider = _Provider(accepted_media_formats={"image": frozenset({"telepathy"})})
    with pytest.raises(BadRequest, match="未知通道"):
        declared_channels(provider)


def test_preferred_channels_are_read() -> None:
    provider = _Provider(preferred_media_channels={"image": MediaChannel.OSS})
    assert preferred_channels(provider) == {"image": MediaChannel.OSS}


def test_preferred_channels_absent_is_empty() -> None:
    assert preferred_channels(_Provider()) == {}


def test_plan_marks_fallback_when_base64_cannot_fit() -> None:
    plan = plan_channel(
        kind="image",
        accepted={"image": frozenset({MediaChannel.BASE64, MediaChannel.OSS})},
        size_bytes=15 * 1024 * 1024,
        base64_max_bytes=12 * 1024 * 1024,
    )

    assert plan.channel is MediaChannel.OSS
    assert plan.preferred is MediaChannel.BASE64
    assert plan.fell_back
    assert "Base64" in plan.fallback_reason


def test_plan_without_fallback_when_it_fits() -> None:
    plan = plan_channel(
        kind="image",
        accepted={"image": frozenset({MediaChannel.BASE64, MediaChannel.OSS})},
        size_bytes=1024,
    )

    assert plan.channel is MediaChannel.BASE64
    assert not plan.fell_back


async def test_fallback_is_recorded_as_audit_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """回退必须留痕：素材换了通道会改变请求体大小与失败点。"""
    from core import audit

    events: list[tuple[str, dict[str, Any]]] = []

    async def _record(action: str, **kwargs: Any) -> None:
        events.append((action, kwargs))

    monkeypatch.setattr(audit, "record", _record)

    plan = plan_channel(
        kind="image",
        accepted={"image": frozenset({MediaChannel.BASE64, MediaChannel.OSS})},
        size_bytes=15 * 1024 * 1024,
        base64_max_bytes=12 * 1024 * 1024,
    )
    await record_channel_fallbacks({"refs/a.png": plan}, target_id="gen-1", actor_id="u-1")

    assert len(events) == 1
    action, kwargs = events[0]
    assert action == "media.channel_fallback"
    assert kwargs["target_id"] == "gen-1"
    assert kwargs["detail"]["preferred"] == "base64"
    assert kwargs["detail"]["actual"] == "oss"


async def test_no_audit_event_without_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from core import audit

    events: list[tuple[str, dict[str, Any]]] = []

    async def _record(action: str, **kwargs: Any) -> None:
        events.append((action, kwargs))

    monkeypatch.setattr(audit, "record", _record)

    plan = plan_channel(kind="image", accepted={"image": frozenset({MediaChannel.BASE64})})
    await record_channel_fallbacks({"refs/a.png": plan}, target_id="gen-1")

    assert events == []


def test_oss_declaration_without_upload_credentials_fails_startup_check() -> None:
    """声明 OSS 却没有供应商签发的上传端点 → 空头支票，启动期就该拒。"""
    from modules.model_driver.registry import (
        ChannelDeclarationError,
        _check_channel_declaration,
    )

    provider = _Provider(
        accepted_media_formats={"image": frozenset({MediaChannel.OSS})},
        oss_upload_supported=False,
    )
    with pytest.raises(ChannelDeclarationError, match="上传凭据"):
        _check_channel_declaration(provider)


def test_public_url_declaration_fails_startup_check_without_domain() -> None:
    """当前架构给不出公网地址（素材链接是私签的）→ 声明 PUBLIC_URL 不可行。"""
    from modules.model_driver.registry import (
        ChannelDeclarationError,
        _check_channel_declaration,
    )

    provider = _Provider(
        accepted_media_formats={"image": frozenset({MediaChannel.PUBLIC_URL})},
    )
    with pytest.raises(ChannelDeclarationError, match="公网域名"):
        _check_channel_declaration(provider)


def test_base64_declaration_passes_startup_check() -> None:
    from modules.model_driver.registry import _check_channel_declaration

    provider = _Provider(
        accepted_media_formats={"image": frozenset({MediaChannel.BASE64})},
    )
    _check_channel_declaration(provider)


def test_oss_declaration_without_an_upload_target_entry_fails_startup_check() -> None:
    """声明 OSS 又有凭据端点，却签发不出上传目标 → 通道层无处可传，启动期拒。

    OSS 上传由通道层落地，驱动只负责「问供应商要一份上传目标」。少了这一半，
    声明就是空头支票 —— 与其等运行时上传失败，不如启动期就拒。
    """
    from modules.model_driver.registry import (
        ChannelDeclarationError,
        _check_channel_declaration,
    )

    provider = _Provider(
        accepted_media_formats={"image": frozenset({MediaChannel.OSS})},
        oss_upload_supported=True,
    )
    with pytest.raises(ChannelDeclarationError, match="oss_upload_target"):
        _check_channel_declaration(provider)


def test_oss_declaration_with_an_upload_target_entry_passes_startup_check() -> None:
    """声明 OSS 且能签发上传目标 → 校验通过。"""
    from modules.model_driver.registry import _check_channel_declaration

    class _OssProvider(_Provider):
        async def oss_upload_target(self, **kwargs: Any) -> Any:
            raise NotImplementedError

    provider = _OssProvider(
        accepted_media_formats={"image": frozenset({MediaChannel.OSS})},
        oss_upload_supported=True,
    )
    _check_channel_declaration(provider)


def test_driver_without_declaration_passes_startup_check() -> None:
    """没声明通道的驱动不受影响：它本来就不收参考素材。"""
    from modules.model_driver.registry import _check_channel_declaration

    _check_channel_declaration(_Provider())


def test_prod_refuses_to_start_with_invalid_channel_declaration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """生产环境：通道声明不合法 → 启动失败（不是运行时才报「图片无法访问」）。"""
    from types import SimpleNamespace

    from core.exceptions import RegistryError
    from modules.model_driver import registry

    monkeypatch.setattr("core.config.settings", SimpleNamespace(app_env="prod"))
    monkeypatch.setattr(
        registry, "_channel_errors", ["p.py: 声明了 OSS 但供应商没有上传凭据端点"]
    )

    with pytest.raises(RegistryError, match="素材通道声明不合法"):
        registry._check_channel_declarations_or_fail()


def test_dev_env_only_warns_about_invalid_channel_declaration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """开发环境：只记警告，不让整个服务起不来（第三方驱动可能还没写完）。"""
    from types import SimpleNamespace

    from modules.model_driver import registry

    monkeypatch.setattr("core.config.settings", SimpleNamespace(app_env="dev"))
    monkeypatch.setattr(
        registry, "_channel_errors", ["p.py: 声明了 OSS 但没有上传凭据端点"]
    )

    registry._check_channel_declarations_or_fail()
