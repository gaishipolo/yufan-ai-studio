"""客户端地址解析。

**只有直连对端是可信代理时才读 `X-Forwarded-For`**：
这个头由客户端自己就能伪造，无条件信任等于把限流键换成攻击者可控的随机值
（每次换一个 IP，限流就失效）；而完全不读它，部署在 nginx / ingress 后面时
所有请求的 `client.host` 都是代理地址，键退化成「只按用户名」。

所以默认**不信任任何代理**，部署时把反代的地址（或网段）配进 `TRUSTED_PROXIES`：

    TRUSTED_PROXIES=127.0.0.1,10.0.0.0/8,::1

等价替代方案是用 uvicorn 的 `--proxy-headers --forwarded-allow-ips=<代理IP>`，
两者选一个即可；这里自带一份是为了让「谁可信」成为应用配置的一部分，
不依赖启动命令写没写对。
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache

from fastapi import Request

from core.config import settings

_Networks = tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]


@lru_cache(maxsize=8)
def _parse(raw: str) -> _Networks:
    nets: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for item in raw.replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        # strict=False：允许写 `10.0.0.1/8` 这种「主机位非零」的网段写法
        nets.append(ipaddress.ip_network(item, strict=False))
    return tuple(nets)


def trusted_networks() -> _Networks:
    return _parse(settings.trusted_proxies)


def client_ip(request: Request) -> str:
    """取请求的真实来源地址（限流键用）。"""
    peer = request.client.host if request.client else ""
    nets = trusted_networks()
    if not peer or not nets:
        return peer

    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:  # pragma: no cover - client.host 一定是合法地址
        return peer
    if not any(addr in net for net in nets):
        return peer

    forwarded = request.headers.get("x-forwarded-for", "")
    if not forwarded:
        return peer
    # 取最左边的第一个地址：那是「最初的客户端」。
    # 右侧的每一跳都可以由我们的代理追加/覆盖，左侧只有最外层能决定，
    # 所以前提始终是「直连对端可信」——它才可能帮我们挡住伪造的左侧值。
    first = forwarded.split(",", 1)[0].strip()
    return first or peer


__all__ = ["client_ip", "trusted_networks"]
