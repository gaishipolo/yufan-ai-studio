"""客户端地址解析：只有直连对端是可信代理时才读 X-Forwarded-For。

这条决定限流键的取值，而限流键是「未认证请求」唯一能影响的服务端状态：
读错头就等于把限流开关交给攻击者（每请求换一个 IP 即可绕过），
完全不读头则所有请求共用一个键（一个人能拖垮全站）。

第二层容易错的是**取哪一段**：XFF 是追加语义，右侧才是我方代理写下的，
左侧由客户端自报。从右往左取第一个不可信地址才是「真实来源」（R6-01）。
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


def test_trusted_proxy_takes_the_rightmost_untrusted_hop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """对端可信时，右起第一个不可信地址才是真实客户端（追加语义）。"""
    monkeypatch.setattr(settings, "trusted_proxies", "127.0.0.1")
    assert (
        net.client_ip(_request("127.0.0.1", "198.51.100.7, 203.0.113.7"))
        == "203.0.113.7"
    )


def test_client_supplied_prefix_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """回归（R6-01）：客户端自带的伪造段不能被当成客户端地址。

    修之前取最左，攻击者每个请求换一个前缀就能让限流计数全是新键，
    于是「锁」形同虚设——这一条就是那次绕过的最小复现。
    """
    monkeypatch.setattr(settings, "trusted_proxies", "127.0.0.1")
    forged = "9.9.9.9, 198.51.100.7"  # 左边是客户端自己写的，右边是代理追加的
    assert net.client_ip(_request("127.0.0.1", forged)) == "198.51.100.7"


def test_inner_proxy_hops_are_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """多级代理：跳过所有可信跳，取第一个不在名单里的地址。"""
    monkeypatch.setattr(settings, "trusted_proxies", "127.0.0.1,10.0.0.0/8")
    chain = "198.51.100.7, 10.0.0.5, 10.0.0.6"
    assert net.client_ip(_request("127.0.0.1", chain)) == "198.51.100.7"


def test_all_hops_trusted_falls_back_to_leftmost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """整条链都在可信网段内（客户端本身是内网服务）：取最左，不返回代理自己。"""
    monkeypatch.setattr(settings, "trusted_proxies", "127.0.0.1,10.0.0.0/8")
    assert net.client_ip(_request("127.0.0.1", "10.0.0.5, 10.0.0.6")) == "10.0.0.5"


def test_garbage_hop_falls_back_to_peer(monkeypatch: pytest.MonkeyPatch) -> None:
    """链里出现非法段：退回直连对端，而不是拿一个字符串当限流键。"""
    monkeypatch.setattr(settings, "trusted_proxies", "127.0.0.1")
    assert net.client_ip(_request("127.0.0.1", "unknown")) == "127.0.0.1"


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
