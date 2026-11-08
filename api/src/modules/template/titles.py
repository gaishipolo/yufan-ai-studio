"""统一的展示名：列表、核对弹窗、档案详情、任务中心共用一套规则。

**为什么单独一个文件**：这四处在重建前后各拼过一遍，结果同一个任务在列表里叫
「张三 · 人物替换」，在任务中心叫「原片已不在 · 人物替换」——同一条记录两个名字，
用户以为自己点错了。规则放一处、谁要谁调，是唯一能保证一致的做法。

三条约定（都有对应测试）：

1. **超长截断不换行**：原片名可能很长，列表是单行布局，折行会把行高撑乱。
   截到 18 个字加省略号，够认出是哪条片子。
2. **后缀一定去掉**：`.mp4` / `.MP4` / `.mov` 都去掉。留着后缀在一列名字里
   只是噪音，用户分不出「同一个片子的两个版本」。
3. **缺失要说清是哪种缺失**：从没记录过（老档案）与记录了但是空的，是两件事。
   前者说明「当时没存」，后者说明「存了个空名字」，运维排查时区别很大。
"""

from __future__ import annotations

KIND_LABELS = {"character": "人物", "product": "产品", "scene": "场景"}

# 展示名的原片名部分最多这么长（含省略号前）。18 个字在窄侧栏里仍能一行放下。
NAME_LIMIT = 18

MISSING_NAME = "历史档案未记录原片"
BLANK_NAME = "未命名原片"


def _trim(name: str) -> str:
    stem = name.rsplit(".", 1)[0] if "." in name else name
    stem = stem.strip() or BLANK_NAME
    if len(stem) <= NAME_LIMIT:
        return stem
    return f"{stem[:NAME_LIMIT]}…"


def task_name(
    source_name: str | None, kind: str, target: str, run_id: str
) -> str:
    """`<原片> · <人物|产品|场景>替换 · <对象> · #<前8位>`。空的部分整段省略。"""
    head = MISSING_NAME if source_name is None else _trim(source_name)
    parts = [head, f"{KIND_LABELS.get(kind, kind)}替换"]
    if target.strip():
        parts.append(target.strip())
    if run_id:
        parts.append(f"#{run_id[:8].upper()}")
    return " · ".join(parts)
