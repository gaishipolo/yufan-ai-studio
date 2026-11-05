"""客户端地址解析。

**只有直连对端是可信代理时才读 `X-Forwarded-For`**：
这个头由客户端自己就能伪造，无条件信任等于把限流键换成攻击者可控的随机值
（每次换一个 IP，限流就失效）；而完全不读它，部署在 nginx / ingress 后面时
所有请求的 `client.host` 都是代理地址，键退化成「只按用户名」。

所以默认**不信任任何代理**，部署时把反代的地址（或网段）配进 `TRUSTED_PROXIES`：

    TRUSTED_PROXIES=127.0.0.1,10.0.0.0/8,::1

读取方向是**从右往左**（R6-01）：`X-Forwarded-For` 是**追加**语义，每一跳在
已有值后面追加自己看到的对端，所以最右边那一段才是我们的代理亲眼看到的地址，
而左边的任意一段都可能是客户端自己写进来的：

    客户端自报:    X-Forwarded-For: 9.9.9.9          ← 攻击者随手写的
    nginx 追加后:  X-Forwarded-For: 9.9.9.9, 203.0.113.7
    真实来源 = 右起第一个「不可信」地址 = 203.0.113.7

取最左 = 取攻击者可控值，限流键就永远是新值，锁等于不存在。uvicorn 的
`--proxy-headers` 也是同一套逻辑（源码注释：*each proxy appends to the header
list so check it in reverse order*）。

两条部署纪律（写在这里，因为出错时现象都是「限流看起来生效了」）：

1. **不要把 `TRUSTED_PROXIES` 配成 `0.0.0.0/0`**。那样「跳过可信」等于
   「永远取最左」，又回到可伪造。
2. **不要同时开 uvicorn `--proxy-headers`**。它会先把 `request.client` 改写成
   真实客户端，本函数随后看到「对端不可信」而直接用 `peer`——结果碰巧是对的，
   但「到底谁在解析这个头」变得不可推理。两条路径只保留一条：要么用启动参数，
   要么用这份配置。
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


def _as_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """解析成地址对象；不是合法 IP 就返回 None（IPv6 带方括号也认）。"""
    try:
        return ipaddress.ip_address(host.strip().strip("[]"))
    except ValueError:
        return None


def _is_trusted(host: str, nets: _Networks) -> bool:
    """`host` 是否落在可信网段内。

    不合法地址一律**不可信**：把 `unknown`、`_hidden` 这类 nginx 占位符当成
    「可信代理」会让整条链的判定歪掉，而它们在数学上也不该是任何网段。
    """
    addr = _as_ip(host)
    return addr is not None and any(addr in net for net in nets)


def client_ip(request: Request) -> str:
    """取请求的真实来源地址（限流键用）。"""
    peer = request.client.host if request.client else ""
    nets = trusted_networks()
    if not peer or not nets or not _is_trusted(peer, nets):
        # 直连对端不可信（或没配代理）：头里的值一个字都不信
        return peer

    forwarded = request.headers.get("x-forwarded-for", "")
    if not forwarded:
        return peer

    hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
    # 从右往左：跳过可信代理，第一个不可信的地址就是「离我们最近的真实客户端」
    for hop in reversed(hops):
        if _is_trusted(hop, nets):
            continue
        # 取到不可信但格式非法的段：说明链被污染，退回直连对端，
        # 不让一个非法字符串成为限流键（那等于每请求换一个键）
        return hop if _as_ip(hop) is not None else peer
    # 整条链都是可信代理：说明客户端本身在内网，最左一段就是它
    return hops[0] if hops else peer


__all__ = ["client_ip", "trusted_networks"]
