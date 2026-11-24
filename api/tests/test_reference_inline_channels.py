"""按声明准备参考素材：通道分流、零读字节、同类型通道必须一致。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest

from core.exceptions import BadRequest
from infra.media_channel import ChannelPlan, MediaChannel
from infra.reference_inline import IMAGE_MIME_BY_SUFFIX, prepare_inline_payloads

SUPPORTED = frozenset({MediaChannel.BASE64, MediaChannel.OSS, MediaChannel.WORKSPACE})


class _FakeStorage:
    """记录被读过的 key —— 「不该读的字节」得能断言。"""

    def __init__(self, blobs: Mapping[str, bytes]) -> None:
        self._blobs = dict(blobs)
        self.reads: list[str] = []

    async def get(self, key: str) -> bytes | None:
        self.reads.append(key)
        return self._blobs.get(key)


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> _FakeStorage:
    fake = _FakeStorage({"refs/small.png": b"tiny", "refs/big.png": b"x" * 100})
    monkeypatch.setattr("infra.reference_inline.get_storage", lambda: fake)
    return fake


async def _prepare(
    keys: Sequence[str],
    *,
    accepted: Mapping[str, frozenset[MediaChannel]],
    supported: frozenset[MediaChannel] = SUPPORTED,
    base64_max_bytes: int = 12 * 1024 * 1024,
) -> tuple[tuple[str, ...], dict[str, ChannelPlan]]:
    return await prepare_inline_payloads(
        keys,
        accepted=accepted,
        supported=supported,
        mime_by_suffix=IMAGE_MIME_BY_SUFFIX,
        max_bytes=10 * 1024 * 1024,
        base64_max_bytes=base64_max_bytes,
    )


async def test_base64_only_inlines_and_reads_bytes_once(storage: _FakeStorage) -> None:
    uris, plans = await _prepare(
        ["refs/small.png"], accepted={"image": frozenset({MediaChannel.BASE64})}
    )

    assert len(uris) == 1
    assert uris[0].startswith("data:image/png;base64,")
    assert storage.reads == ["refs/small.png"]
    assert plans["refs/small.png"].channel is MediaChannel.BASE64
    assert not plans["refs/small.png"].fell_back


async def test_oss_only_never_reads_bytes(storage: _FakeStorage) -> None:
    """走 OSS / 工作区的素材：框架一个字节都不碰（物化器自己去取）。"""
    uris, plans = await _prepare(
        ["refs/small.png"], accepted={"image": frozenset({MediaChannel.OSS})}
    )

    assert uris == ()
    assert storage.reads == []
    assert plans["refs/small.png"].channel is MediaChannel.OSS


async def test_workspace_channel_keeps_material_for_the_materializer(
    storage: _FakeStorage,
) -> None:
    """工作区通道（LibTV 那种）：素材留给物化器落工作区。"""
    uris, plans = await _prepare(
        ["refs/small.png"], accepted={"image": frozenset({MediaChannel.WORKSPACE})}
    )

    assert uris == ()
    assert storage.reads == []
    assert plans["refs/small.png"].channel is MediaChannel.WORKSPACE


async def test_driver_without_channel_declaration_is_rejected(
    storage: _FakeStorage,
) -> None:
    """没声明通道的驱动带参考素材 → 明确报错，不静默丢素材。"""
    with pytest.raises(BadRequest, match="没有为 image 声明任何素材通道"):
        await _prepare(["refs/small.png"], accepted={})


async def test_channel_without_implementation_on_this_chain_is_rejected(
    storage: _FakeStorage,
) -> None:
    """声明了这条链没实现的通道 → 报错（不能悄悄走别的通道）。"""
    with pytest.raises(BadRequest, match="没有落地实现"):
        await _prepare(
            ["refs/small.png"],
            accepted={"image": frozenset({MediaChannel.WORKSPACE})},
            supported=frozenset({MediaChannel.BASE64}),
        )


async def test_same_kind_mixed_channels_unify_to_the_conservative_one(
    storage: _FakeStorage,
) -> None:
    """同一类型里一半内联一半不内联会让素材顺序错位 → 统一走保守那档。"""
    uris, plans = await _prepare(
        ["refs/small.png", "refs/big.png"],
        accepted={"image": frozenset({MediaChannel.BASE64, MediaChannel.OSS})},
        base64_max_bytes=10,
    )

    assert uris == ()
    assert plans["refs/small.png"].channel is MediaChannel.OSS
    assert plans["refs/big.png"].channel is MediaChannel.OSS
    assert plans["refs/small.png"].fell_back


async def test_unknown_suffix_is_rejected_for_inline_drivers(
    storage: _FakeStorage,
) -> None:
    with pytest.raises(BadRequest, match="参考素材格式不支持"):
        await _prepare(
            ["refs/notes.txt"], accepted={"image": frozenset({MediaChannel.BASE64})}
        )
