"""跨模块共享的枚举词汇。

当某个枚举被两个以上模块使用时，上提到这里，避免模块间互相 import。
"""

from __future__ import annotations

from enum import StrEnum


class AspectRatio(StrEnum):
    """画面比例：竖屏 / 横屏 / 方形。"""

    PORTRAIT = "9:16"
    LANDSCAPE = "16:9"
    SQUARE = "1:1"

    @property
    def size(self) -> tuple[int, int]:
        return {
            AspectRatio.PORTRAIT: (720, 1280),
            AspectRatio.LANDSCAPE: (1280, 720),
            AspectRatio.SQUARE: (1024, 1024),
        }[self]
