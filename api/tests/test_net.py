"""客户端地址解析：只有直连对端是可信代理时才读 X-Forwarded-For。

这条决定限流键的取值，而限流键是「未认证请求」唯一能影响的服务端状态：
读错头就等于把限流开关交给攻击者（每请求换一个 IP 即可绕过），
完全不读头则所有请求共用一个键（一个人能拖垮全站）。
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from core import net
from core.config import Settings, settings


def _request(peer: str, forwarded: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request({"type": "http", "client": (peer, 12345), "headers": headers})


@pytest.fixture(autouse=True)
def _clear_parse_cache() -> None:
    net._parse.cache_clear()
    yield
    net._parse.cache_clear()


def test_forwarded_header_is_ignored_when_no_proxy_trusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """默认不信任任何代理：伪造的 `X-Forwarded-For` 不改变限流键。"""
    monkeypatch.setattr(settings, "trusted_proxies", "")
    assert net.client_ip(_request("203.0.113.9", "1.2.3.4")) == "203.0.113.9"


def test_trusted_proxy_yields_the_original_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """对端在名单里：取最左边的第一跳，而不是代理链里的任意一跳。"""
    monkeypatch.setattr(settings, "trusted_proxies", "127.0.0.1")
    assert net.client_ip(_request("127.0.0.1", "198.51.100.7, 10.0.0.1")) == "198.51.100.7"


def test_untrusted_peer_still_ignored_when_proxy_list_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """名单只对名单内的地址生效——外网直连伪造头依然无效。"""
    monkeypatch.setattr(settings, "trusted_proxies", "10.0.0.0/8")
    assert net.client_ip(_request("203.0.113.9", "1.2.3.4")) == "203.0.113.9"


def test_malformed_trusted_proxies_fails_fast() -> None:
    """写错一个网段就启动失败，而不是静默退回「不信任」（那种错上线后查不出来）。"""
    with pytest.raises(ValueError, match="TRUSTED_PROXIES"):
        Settings(trusted_proxies="not-an-ip")
