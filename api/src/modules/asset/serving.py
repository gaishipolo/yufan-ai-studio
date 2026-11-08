"""取件出参的兼容入口。

实现在 `core.serving`（2026-11-08 上移）。留这个文件是因为 `asset` 内部与
测试都按 `modules.asset.serving.serve` 引用；直接删会制造一次无收益的改名风暴。

新代码请 `from core.serving import serve`——业务模块之间不该为了一个
`RedirectResponse` 互相 import。
"""

from __future__ import annotations

from core.serving import serve

__all__ = ["serve"]
