"""`AssetSource` 的每个取值都必须有来处。

这类的失效非常安静：枚举里写着一个值、模型注释说它代表某种来源、`source` 字段
也在库里，**但没有任何代码会写它**。于是「网页上传」与「盘上登记」被静默合并成
同一个值，任何按来源过滤的查询都返回空，而且不报错。

真实发生过：`upload` / `generated` 从接口合并那天起就没有写入路径，而模型注释
仍写着「上传的个人素材、生成产物都落在这里」。同时那个为「上传 vs 归档」设计的
部分唯一索引 `WHERE source != 'archive'` 也跟着失效——它对所有行都不成立。

所以这里不检查「注释对不对」（那没法自动判），只检查**可执行的事实**：
每个枚举值要么被某处写入，要么在 `AWAITING_WRITE_PATH` 里写明为什么还没有。
"""

from __future__ import annotations

import ast
from pathlib import Path

from modules.asset.schema import AWAITING_WRITE_PATH, AssetSource

SRC = Path(__file__).resolve().parent.parent / "src"


def _python_files() -> list[Path]:
    return [
        path
        for path in sorted(SRC.rglob("*.py"))
        if "__pycache__" not in path.parts
    ]


def _is_source_ref(node: ast.expr | None) -> AssetSource | None:
    """认出 `AssetSource.X` 这种写法。

    `AssetSource.X` 的 AST 是 `Attribute(attr="X", value=Name("AssetSource"))`——
    只有一层 Attribute，`value` 是 Name 而不是另一层 Attribute。多判一层的话
    所有写入点都扫不到，而「扫不到」与「没写」在这个测试里长得一模一样。
    """
    if not isinstance(node, ast.Attribute):
        return None
    if not isinstance(node.value, ast.Name) or node.value.id != "AssetSource":
        return None
    try:
        # 按**名字**取（`AssetSource["ARCHIVE"]`）：StrEnum 以值为键调用时，
        # `AssetSource("ARCHIVE")` 会 ValueError（它认的是 "archive"），
        # 于是每个写入点都被判成「不认识」——扫不到与没写长得一模一样。
        return AssetSource[node.attr]
    except KeyError:
        return None


def _written_sources() -> set[AssetSource]:
    """扫出所有「把某个来源写进去」的位置。

    只认两种形态：
    - 关键字参数 `Asset(..., source=AssetSource.X)`；
    - 字典字面量 `{"source": AssetSource.X}`（导入侧就是这么写的）。

    **不认 `Asset.source == AssetSource.X`**：那是查询条件，不是写入。
    把查询也算成写入，等于让守卫自己骗自己——一个只被查、从不被写的值会显得很健康。
    """
    found: set[AssetSource] = set()
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg != "source":
                        continue
                    value = _is_source_ref(keyword.value)
                    if value is not None:
                        found.add(value)
            elif isinstance(node, ast.Dict):
                for key, value_node in zip(node.keys, node.values, strict=True):
                    if not (isinstance(key, ast.Constant) and key.value == "source"):
                        continue
                    value = _is_source_ref(value_node)
                    if value is not None:
                        found.add(value)
    return found


def test_every_source_is_either_written_or_explained() -> None:
    written = _written_sources()
    unexplained = [
        member
        for member in AssetSource
        if member not in written and member not in AWAITING_WRITE_PATH
    ]
    assert not unexplained, (
        f"这些来源没有任何写入路径，也没在 AWAITING_WRITE_PATH 里说明原因：{unexplained}。"
        "要么补上写入入口，要么登记它为什么用不上——不允许留一个永远用不到的值"
    )


def test_explained_sources_really_are_unused() -> None:
    """反过来也要成立：登记成「还没写」的值，一旦真有人写了就该从表里删掉。

    留着会让下一个人以为「这个入口还没做」，而实际上代码已经写了。
    """
    written = _written_sources()
    stale = [member for member in AWAITING_WRITE_PATH if member in written]
    assert not stale, f"这些来源已经有写入路径，应从 AWAITING_WRITE_PATH 移除：{stale}"


def test_the_scan_actually_finds_something() -> None:
    """扫描口径退化时必须红。

    这个文件里所有的 `rglob` / `ast.walk` 都是「找不到东西也返回空集」的形态，
    而空集恰好能让上面两条通过（没有未解释的、也没有过时的）。已在本仓发生过
    5 次同类事故，所以「必须真的扫到东西」本身也要断言。
    """
    written = _written_sources()
    assert written, "一个写入点都没扫到：文件定位或 AST 匹配退化了"
    assert AssetSource.ARCHIVE in written, (
        "连 `_ingest` 里的 `source=AssetSource.ARCHIVE` 都没扫到，匹配规则有问题"
    )
