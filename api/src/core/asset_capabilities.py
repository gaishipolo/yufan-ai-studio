"""素材能力词表：一张素材「能被拿来做什么」。

放在 `core/` 而不是某个模块里，是因为它现在有两个互不认识的消费者：

- `modules/asset`：主体成员的逐项授权（`generate` / `download` / `derive`）；
- `modules/template`：模板槽位声明的 `required_caps`。

业务模块之间禁止横向 import（`test_modules_do_not_import_each_other`），
所以词表只要有第二个消费者就必须下移。它的原位置是
`modules/asset/subject_capabilities.py`，那里保留 re-export 不动调用方。

## 三个能力的语义边界

| 能力 | 回答的问题 | 粒度 |
| --- | --- | --- |
| `generate` | 能不能被读进模型 | **全体成员**（生成要读全部字节） |
| `download` | 能不能下载原件 | 逐成员 |
| `derive` | 能不能作为参考图进入下一次生成 | 逐成员 |

`generate` 之所以是全体成员：少一张图就读不出完整外观，成片会"看起来对但不对"，
而错误不会出现在任何日志里。
"""

from __future__ import annotations

from typing import Final

__all__ = ["CAPABILITIES", "CAPABILITY_LABELS"]

#: 主体级 / 成员级共用的能力码。顺序即展示顺序（也进提示词，别随手改）。
CAPABILITIES: Final[tuple[str, ...]] = ("generate", "download", "derive")

CAPABILITY_LABELS: Final[dict[str, str]] = {
    "generate": "可生成",
    "download": "可下载原件",
    "derive": "可衍生",
}
