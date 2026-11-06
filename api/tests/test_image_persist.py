"""生成产物落盘（运行时资产维度）+ 下载加固（R11-03）。

供应商返回的图片 URL 是**签名地址**，过期后历史页全是裂图。框架不强制落盘
（视频直链长期有效，没必要搬），只提供可选钩子 `pipeline.persist_output`。
生图这一路必须实现它——这是「运行时资产」维度对生图的验收点。

这里用 `httpx.MockTransport` 接在真实 `AsyncClient` 上：重定向、流式读取、
响应头都走 httpx 的真实行为，fake 对象很容易把「其实没校验」测成绿的。
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from modules.image.pipeline import ImageAssetSettings, ImageGenerationPipeline

_PNG = b"\x89PNG\r\n\x1a\n" + b"FAKE-PNG-BODY"
_JPEG = b"\xff\xd8\xff" + b"FAKE-JPEG-BODY"


@pytest.fixture
def fake_storage(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    stored: dict[str, Any] = {}

    class _Storage:
        async def put(self, key: str, data: bytes, content_type: str = "") -> str:
            stored.update(key=key, data=data, content_type=content_type)
            return f"/static/{key}"

    monkeypatch.setattr("modules.image.pipeline.get_storage", lambda: _Storage())
    return stored


def _patch_client(
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
    seen: list[str] | None = None,
) -> None:
    """把 pipeline 里 new 出来的 AsyncClient 换成带 MockTransport 的真实客户端。"""
    real_client = httpx.AsyncClient

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        def wrapped(request: httpx.Request) -> httpx.Response:
            if seen is not None:
                seen.append(str(request.url))
            return handler(request)

        return real_client(transport=httpx.MockTransport(wrapped), **kwargs)

    monkeypatch.setattr("modules.image.pipeline.httpx.AsyncClient", factory)


def _image_response(body: bytes = _PNG, content_type: str = "image/png") -> httpx.Response:
    return httpx.Response(200, content=body, headers={"content-type": content_type})


def _settings(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> None:
    monkeypatch.setattr(
        "modules.image.pipeline.get_image_asset_settings",
        lambda: ImageAssetSettings(**overrides),
    )


async def test_persist_downloads_and_stores(
    monkeypatch: pytest.MonkeyPatch, fake_storage: dict[str, Any]
) -> None:
    seen: list[str] = []
    _patch_client(monkeypatch, lambda _req: _image_response(), seen)

    key = await ImageGenerationPipeline().persist_output(
        "rid-1", "https://cdn.example.com/a/b.php?sig=expires"
    )

    assert key == "images/rid-1.png", (
        "落库的必须是对象 key，不是供应商签名地址，也不是本服务器拼的地址——"
        "地址一旦带上网址形态，换存储驱动时存量链接会整批失联"
    )
    assert fake_storage["key"] == "images/rid-1.png", (
        "后缀必须由 content-type 决定，不能拼 URL 路径后缀"
    )
    assert fake_storage["data"] == _PNG
    assert fake_storage["content_type"] == "image/png"
    assert len(seen) == 1


async def test_persist_keeps_own_urls_untouched(
    monkeypatch: pytest.MonkeyPatch, fake_storage: dict[str, Any]
) -> None:
    """已经是自己的地址（演示数据、重放）就不要再搬运一次。"""
    url = await ImageGenerationPipeline().persist_output("rid-2", "/static/images/rid-2.png")
    assert url == "/static/images/rid-2.png"
    assert fake_storage == {}


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/secret.png",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/internal.png",
        "http://192.168.1.1/admin.png",
        "http://[::1]/secret.png",
        "https://localhost/secret.png",
        "https://metadata.google.internal/secret.png",
        "ftp://cdn.example.com/a.png",
    ],
)
async def test_rejects_internal_and_non_public_urls(
    monkeypatch: pytest.MonkeyPatch, fake_storage: dict[str, Any], url: str
) -> None:
    """SSRF 落点：内网 / 环回 / 元数据地址与非 http(s) 协议一律不发请求。"""
    seen: list[str] = []
    _patch_client(monkeypatch, lambda _req: _image_response(), seen)
    _settings(monkeypatch)

    with pytest.raises(ValueError):
        await ImageGenerationPipeline().persist_output("rid-blocked", url)

    assert seen == [], "校验必须在发请求之前，不能先打出去再说"
    assert fake_storage == {}


async def test_rejects_redirect_to_internal_host(
    monkeypatch: pytest.MonkeyPatch, fake_storage: dict[str, Any]
) -> None:
    """初始 URL 看起来是公网域名，一次 302 就能把请求带到内网（R11-03 的实测路径）。"""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith("https://cdn.example.com/redirect"):
            return httpx.Response(
                302, headers={"location": "http://127.0.0.1/secret.png"}
            )
        return _image_response(b"INTERNAL-ONLY-SECRET")

    _patch_client(monkeypatch, handler, seen)
    _settings(monkeypatch)

    with pytest.raises(ValueError):
        await ImageGenerationPipeline().persist_output(
            "rid-redirect", "https://cdn.example.com/redirect"
        )

    assert len(seen) == 1, "重定向目标必须重新校验，不能盲目跟随"
    assert fake_storage == {}


async def test_follows_redirect_only_within_allowlist(
    monkeypatch: pytest.MonkeyPatch, fake_storage: dict[str, Any]
) -> None:
    """配了白名单时允许一跳到另一个白名单域名，每一跳都要校验。"""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "first.example.com" in str(request.url):
            return httpx.Response(302, headers={"location": "https://second.example.com/a.png"})
        return _image_response()

    _patch_client(monkeypatch, handler, seen)
    _settings(
        monkeypatch,
        allowed_hosts="first.example.com, second.example.com",
    )

    key = await ImageGenerationPipeline().persist_output(
        "rid-hop", "https://first.example.com/start"
    )

    assert key == "images/rid-hop.png"
    assert len(seen) == 2
    assert fake_storage["data"] == _PNG


async def test_allowlist_rejects_unlisted_host(
    monkeypatch: pytest.MonkeyPatch, fake_storage: dict[str, Any]
) -> None:
    seen: list[str] = []
    _patch_client(monkeypatch, lambda _req: _image_response(), seen)
    _settings(monkeypatch, allowed_hosts="cdn.example.com")

    with pytest.raises(ValueError, match="白名单"):
        await ImageGenerationPipeline().persist_output(
            "rid-host", "https://evil.example.com/a.png"
        )
    assert seen == []
    assert fake_storage == {}


@pytest.mark.parametrize(
    ("body", "content_type", "message"),
    [
        (_PNG, "text/html", "白名单"),
        (b"<html>not an image</html>", "image/png", "魔数"),
        (_JPEG, "image/webp", "魔数"),
    ],
)
async def test_rejects_content_that_is_not_an_image(
    monkeypatch: pytest.MonkeyPatch,
    fake_storage: dict[str, Any],
    body: bytes,
    content_type: str,
    message: str,
) -> None:
    _patch_client(monkeypatch, lambda _req: _image_response(body, content_type))
    _settings(monkeypatch)

    with pytest.raises(ValueError, match=message):
        await ImageGenerationPipeline().persist_output(
            "rid-bad", "https://cdn.example.com/a.png"
        )
    assert fake_storage == {}


async def test_rejects_oversize_payload_early(
    monkeypatch: pytest.MonkeyPatch, fake_storage: dict[str, Any]
) -> None:
    """流式累计字节上限：超限立即断开，不把整个响应读进内存。"""
    big = _PNG + b"x" * 4096
    _patch_client(monkeypatch, lambda _req: _image_response(big))
    _settings(monkeypatch, max_bytes=64)

    with pytest.raises(ValueError, match="MAX_BYTES"):
        await ImageGenerationPipeline().persist_output(
            "rid-big", "https://cdn.example.com/big.png"
        )
    assert fake_storage == {}


def test_cover_follows_output_when_it_is_the_same_image() -> None:
    """图片类：`cover_url` 与 `output_url` 是同一张图，封面必须跟着换。

    不换的话，前端拿 `coverUrl` 渲染的卡片会在供应商签名过期后裂图，
    而同一个对象的 `outputUrl` 明明是好的——这种「一半好一半坏」最难排查。
    """
    from generation.runner import GenerationRunner
    from infra.ai.base import GenerationProgress, GenerationState

    result = GenerationProgress(
        external_id="task-1",
        state=GenerationState.SUCCEEDED,
        progress=100,
        output_url="https://cdn.example.com/a.png?sig=1",
        cover_url="https://cdn.example.com/a.png?sig=1",
    )
    assert (
        GenerationRunner._persisted_cover(result, "/static/images/rid.png")
        == "/static/images/rid.png"
    )


def test_cover_is_left_alone_when_it_is_a_different_image() -> None:
    """视频类：封面是单独一张图，没落盘就不能指向落盘地址（那是别人的图）。"""
    from generation.runner import GenerationRunner
    from infra.ai.base import GenerationProgress, GenerationState

    result = GenerationProgress(
        external_id="task-2",
        state=GenerationState.SUCCEEDED,
        progress=100,
        output_url="https://cdn.example.com/a.mp4?sig=1",
        cover_url="https://cdn.example.com/a.jpg?sig=1",
    )
    assert (
        GenerationRunner._persisted_cover(result, "/static/videos/rid.mp4")
        == "https://cdn.example.com/a.jpg?sig=1"
    )
