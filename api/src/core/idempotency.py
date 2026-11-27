"""创建类接口的请求幂等：键、指纹与重放判定。

## 它解决什么

同一个操作在网络上可能到达两次：用户双击、读超时后重试、客户端自动重放。
创建生成任务**会真的花钱**，所以「看起来一样的两次请求」不能各自落库、各自
提交供应商。

客户端为**一次提交意图**生成 `idempotency_key`：

- 同键、同指纹 → 返回原记录，不再落库/提交；
- 同键、不同指纹 → 409（这个键已经属于另一次提交，不能悄悄返回错的任务）；
- 不同键 → 两次独立提交，可以并发执行。

键由客户端生成，因为「用户真的想再生成一张相同提示词的图」和「刚才那次重试」
在参数上完全一样，服务端无法区分；只有提交方知道这是不是同一个意图。
"""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel

#: 客户端 UUID / 随机串的安全形状：只允许 URL 与新头里都安全的字符。
IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9_-]{8,64}$"
IDEMPOTENCY_KEY_MAX = 64


def request_fingerprint(payload: BaseModel) -> str:
    """请求内容的稳定指纹（键本身不参与）。

    排序键 + 紧凑分隔符：同一份 JSON 即使键顺序不同也得到同一个指纹。
    `by_alias=True` 与契约字段名一致，避免前后端对不上。
    """
    data = payload.model_dump(mode="json", by_alias=True, exclude={"idempotency_key"})
    encoded = json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def fingerprint_matches(stored: str | None, current: str) -> bool:
    """没有指纹的老行/坏行按「不匹配」处理，宁可 409 也不返回错任务。"""
    return bool(stored) and stored == current