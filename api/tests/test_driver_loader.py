"""驱动解析：`infra.<kind>.<name>` 的动态加载与它的两种「拒绝」。

约定是「新增一个驱动 = 新增一个文件 + 改 `.env` 里的名字」，所以 `.env` 里那个
名字是**唯一的外部输入**，它直接决定 import 什么模块。两条拒绝必须分清：

- 名字不合法 → `RegistryError`（配置写错了，改配置）
- 名字合法但文件不存在 → `NotImplementedError`（功能没做，去写文件）

混成一种是运维事故：生产把 `STORAGE_DRIVER` 写成 `s3` 而代码没实现，如果这里
**降级回本地磁盘**，服务照常启动、图片照常能传，只是全写到一台机器的盘上——
换实例就 404，而日志里一个字都没有。所以「未实现必须启动失败」。

**这个模块此前零测试**，变异扫描把名字校验改成 `if False:` 后 46 条测试全绿。
"""

from __future__ import annotations

import pytest

from core.registry import RegistryError
from infra.loader import load_driver


def test_loads_a_real_driver_and_instantiates_it() -> None:
    from infra.storage.local import LocalStorage

    assert isinstance(load_driver("storage", "local"), LocalStorage)


def test_rejects_names_that_are_not_identifiers() -> None:
    """路径穿越、点号、斜杠、空格都过不去——这个名字会直接拼进 import 路径。"""
    for name in ("../../etc/passwd", "local.py", "a/b", "a b", "", "-x"):
        with pytest.raises(RegistryError):
            load_driver("storage", name)


def test_rejects_non_ascii_names() -> None:
    """`isalnum()` 对中文返回 True，所以旧校验会放行 `中文`。

    放行的后果不是安全问题，是**误导性提示**：错误消息会说「请创建
    src/infra/storage/中文.py」，而项目约定模块名只能是 ASCII——照着做只会
    在别处（迁移脚本、日志、`.env` 编码）继续踩。
    """
    with pytest.raises(RegistryError):
        load_driver("storage", "中文驱动")


def test_rejects_names_starting_with_a_digit() -> None:
    """`3d` 是 alnum 但不是合法模块名，import 必然失败，同样该在入口拦下。"""
    with pytest.raises(RegistryError):
        load_driver("storage", "3d")


def test_missing_driver_raises_instead_of_falling_back() -> None:
    """未实现 → 明确报错，**绝不**落回某个默认实现。

    降级回本地磁盘是最坏的失败方式：启动成功、功能看似正常、数据全写在本机，
    直到换实例访问历史记录才发现。所以这里断言的是「抛错」而不是「能用」。
    """
    with pytest.raises(NotImplementedError) as exc:
        load_driver("storage", "no_such_driver")

    message = str(exc.value)
    assert "no_such_driver" in message
    # 消息要直接给出下一步动作，而不是只报「找不到」
    assert "src/infra/storage/no_such_driver.py" in message


def test_module_without_driver_symbol_is_rejected(tmp_path, monkeypatch) -> None:
    """模块存在但没定义 `Driver`：不能返回 None 让调用方在别处炸。"""
    module = type("_FakeModule", (), {"__name__": "infra.storage.emptydriver"})()
    monkeypatch.setattr(
        "infra.loader._import_driver_module", lambda kind, name: module
    )

    with pytest.raises(TypeError) as exc:
        load_driver("storage", "emptydriver")

    assert "Driver" in str(exc.value)
