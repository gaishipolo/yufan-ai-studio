"""「这次提交有没有可能已被上游受理」的唯一判定。

## 为什么必须有这一份，而不是两处各写一遍

两个地方要用**同一个**结论，否则两边会打架：

- `generation/runner.py`：决定「能不能安全重提」—— 判成「可能已受理」就保留
  `submit_attempts`、把任务置失败等人核对；判成「确定没送达」才允许自动重试。
- `modules/model_driver/kit/submission_guard.py`：决定提交台账把这一轮记成
  `rejected`（明确拒绝 → 可安全重试）还是 `submission_unknown`（可能已受理 →
  禁止自动重提）。

分歧的后果很具体：框架判「可安全重试」而台账判「可能已受理」时，人会同时看到
「任务失败」与「可能已计费」两句矛盾的话；反过来则让人以为重提是安全的，而它
其实可能产生第二份账单。判定只有一份实现，双方都从这里取。

## 判定规则（按 **HTTP 语义**分类，不按异常类）

- 连接都没建立（DNS 解析 / 握手 / 被拒绝）：请求必然没送达 → False。
- `AppError` 4xx（含 429）：供应商明确拒绝，没受理 → False。
- `AppError` 5xx / 408：**可能已受理**。网关 502 的典型成因就是
  「上游已经受理、回程失败」，把它当「确定没送达」等于用一次自动重提去赌
  对方没收到，赌错就是第二份账单。
- `AppError` 501：功能没实现，请求根本没发出去 → False（否则脚手架生成的
  第一条任务会顶着「可能已计费」的文案失败）。
- 读/写超时、连接中途被重置、其它未知异常：按「可能已送达」处理。
  宁可让一条任务失败等人核对，也不能赌它没送达。

注：这里只识别 httpx 的异常类别；用别的 HTTP 客户端的供应商抛出的超时不会被
精确归类，会落到「可能已送达」这个安全默认值。
"""

from __future__ import annotations

import httpx

from core.exceptions import AppError

__all__ = ["possibly_delivered"]


def possibly_delivered(exc: BaseException) -> bool:
    """这次提交失败有没有可能「供应商已经受理」。"""
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return False
    if isinstance(exc, AppError):
        if exc.http_status == 501:
            return False
        return exc.http_status == 408 or exc.http_status >= 500
    return True
