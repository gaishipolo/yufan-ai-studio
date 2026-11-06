"""前端用途词表必须与后端逐字一致。

`studio/src/entities/template/model/packRoles.ts` 是 `core/pack_roles.py` 的副本，
编辑器在目录接口返回前就要渲染选项。两边漂移时前端会显示旧标签、提交旧 key，
所以在这里直接读 TS 源文件比对，而不是靠评审时肉眼核对。
"""

from __future__ import annotations

import re
from pathlib import Path

from core.pack_roles import BASE_ROLE, PACK_ROLES, PACK_SCOPES

STUDIO_TS = (
    Path(__file__).resolve().parents[2]
    / "studio"
    / "src"
    / "entities"
    / "template"
    / "model"
    / "packRoles.ts"
)
PAIR = re.compile(r"(\w+)\s*:\s*'([^']*)'")


def _source() -> str:
    assert STUDIO_TS.is_file(), f"找不到前端词表：{STUDIO_TS}"
    return STUDIO_TS.read_text(encoding="utf-8")


def _kind_block(source: str, kind: str) -> str:
    matched = re.search(rf"{kind}:\s*\{{([^}}]*)\}}", source)
    assert matched is not None, f"前端词表缺少分类 {kind}"
    return matched.group(1)


def _flat_block(source: str, name: str) -> str:
    start = source.index(name)
    return source[start : source.index("};", start)]


def test_role_labels_match_frontend() -> None:
    source = _source()
    for kind, roles in PACK_ROLES.items():
        assert dict(PAIR.findall(_kind_block(source, kind))) == roles, f"{kind} 用途表不一致"


def test_base_roles_match_frontend() -> None:
    assert dict(PAIR.findall(_flat_block(_source(), "BASE_ROLES"))) == BASE_ROLE


def test_scopes_match_frontend() -> None:
    assert dict(PAIR.findall(_flat_block(_source(), "PACK_SCOPES"))) == PACK_SCOPES
