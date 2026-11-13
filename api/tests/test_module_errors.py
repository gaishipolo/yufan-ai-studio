"""`ModuleErrors` 的号段与码值约束。

两种失败必须分开：
- 同一个模块内 `code(409, 1)` 写两次 —— 手抖，`_used` 当场拦下；
- 两个模块各写一次 `code(409, 1)` —— 号段可以共用，**码值不能**，
  否则前端拿到 40991 不知道是谁忙。

后者曾经完全没有检测：`--slot` 的注释宣称「参与号段冲突检测」，
但实现里只查了模块自己。导出模块落地时正好用了文档里标为
「示例预留」的 9 号，才把这条缺口暴露出来。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from core.exceptions import ModuleErrors
from core.registry import RegistryError


def test_slot_bounds() -> None:
    for bad in (0, 10, -1):
        with pytest.raises(ValueError):
            ModuleErrors("bounds", slot=bad)


def test_seq_bounds() -> None:
    errors = ModuleErrors("seq_bounds", slot=1)
    for bad in (0, 10):
        with pytest.raises(ValueError):
            errors.code(409, bad)


def test_http_status_bounds() -> None:
    errors = ModuleErrors("status_bounds", slot=1)
    for bad in (200, 399, 600):
        with pytest.raises(ValueError):
            errors.code(bad, 1)


def test_code_keeps_http_status_prefix() -> None:
    errors = ModuleErrors("prefix", slot=7)
    assert errors.code(404, 3) // 100 == 404
    assert errors.code(410, 1) // 100 == 410


def test_same_code_twice_in_one_module_is_rejected() -> None:
    errors = ModuleErrors("dup_self", slot=3)
    errors.code(409, 5)

    with pytest.raises(RegistryError, match="错误码重复"):
        errors.code(409, 5)


def test_two_modules_may_share_a_slot_with_distinct_seq() -> None:
    """号段可共用：7 号就是 template / storyboard / shot 三家一起用的先例。"""
    left = ModuleErrors("share_left", slot=7)
    right = ModuleErrors("share_right", slot=7)

    assert left.code(409, 6) != right.code(409, 4)


def test_two_modules_may_not_share_a_code() -> None:
    # 用 499 而不是 409：409 号段已经被真实模块占掉大半，写死 (409, 7) 这种
    # 「碰巧还空着」的组合，会在下一个模块新增错误码时莫名其妙变红——那时失败
    # 信息指向的是「号段被占用」，而用例的本意是「两个模块不能抢同一个码」。
    first = ModuleErrors("clash_first", slot=8)
    second = ModuleErrors("clash_second", slot=8)
    first.code(499, 7)

    with pytest.raises(RegistryError, match="已被模块 clash_first 占用"):
        second.code(499, 7)


def test_recreating_same_module_name_is_allowed() -> None:
    """同一模块被重复构造（reload / 测试重跑）不该被自己挡住。"""
    first = ModuleErrors("reload_me", slot=6)
    first.code(409, 6)

    again = ModuleErrors("reload_me", slot=6)
    assert again.code(409, 6) == 40966


def test_real_modules_land_in_their_own_segment() -> None:
    """导入即注册：真实模块各自的码值落在自己号段，同状态码下互不相同。

    锁定的是编号规则本身（`状态码 × 100 + slot × 10 + seq`）：
    三位前缀是 HTTP 状态码，十位是号段。改了任何一个，这条会红。
    """
    from modules.export.errors import ExportBusy
    from modules.identity.errors import CredentialInvalid
    from modules.library.errors import AssetNotEditable, SharedAssetNotDeletable

    assert AssetNotEditable.code == 40088  # library, slot 8
    assert SharedAssetNotDeletable.code == 40981
    assert ExportBusy.code == 40991  # export, slot 9
    assert CredentialInvalid.code == 40121  # identity, slot 2
    assert SharedAssetNotDeletable.code != ExportBusy.code


def test_discovery_loads_errors_without_business_import() -> None:
    """`errors.py` 必须由 discovery 主动加载，而不是「碰巧被谁 import 过」。

    导出模块的路由还是 501 占位，没有任何业务代码 import 它的 `errors.py`。
    修复前 `docs/API.md` 的错误码表里稳定少 40991 这一行；而只要跑过任何
    直接 import 该文件的测试，生成结果就多一行——**生成物不可复现**。
    用子进程测，避免 pytest 进程里别的测试已经把它 import 过而恒真。
    """
    src = (Path(__file__).resolve().parents[1] / "src").as_posix()
    program = (
        "import sys\n"
        f"sys.path.insert(0, {src!r})\n"
        "from api.discovery import discover_modules\n"
        "discover_modules()\n"
        "assert 'modules.export.errors' in sys.modules, 'discovery 没有导入 errors.py'\n"
        "from modules.export.errors import ExportBusy\n"
        "assert ExportBusy.code == 40991\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True
    )
    assert completed.returncode == 0, completed.stderr
