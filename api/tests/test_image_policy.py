"""R12-03 / R12-04：驱动配置「自描述」的机械守卫。

上两轮的教训是：加一道守卫会改变写入顺序（refresh），从配置推导会引入
聚合口径（全局注册表按字段名猜语义）。这里把「谁贡献预算」「谁在生产必填」
变成驱动自己声明的字段，并钉住声明缺失时的行为。
"""

from __future__ import annotations

import pytest

import infra.driver_config as dc
import modules.image.maintenance as maintenance
from modules.image.maintenance import _stale_after_sec
from modules.image.pipeline import ImageAssetSettings, _validate_asset_url


def test_stale_threshold_ignores_unrelated_drivers() -> None:
    """与生图无关的驱动 timeout=3600 不许把阈值拖到 90 分钟（R12-03）。"""
    baseline = _stale_after_sec()
    assert baseline is not None

    class UnrelatedSettings(dc.DriverSettings):
        model_config = dc.driver_config("R12UNRELATED")
        timeout_sec: float = 3600.0

    assert _stale_after_sec() == baseline
    assert baseline < 600


def test_declared_budget_without_timeout_is_loud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置坏了必须响亮地失败，不能 `except: continue` 悄悄退回 90 秒。

    用假类而不是真的 DriverSettings 子类：后者会在类定义时登记进全局
    `_REGISTRY`，把这条用例变成污染后面所有测试的定时炸弹。
    """
    class BadSettings:
        submit_budget_for = frozenset({"image"})

        def __init__(self) -> None:
            pass

    monkeypatch.setattr(dc, "registered_driver_settings", lambda: (BadSettings,))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="timeout_sec"):
        dc.registered_submit_budgets("image")


def test_no_budget_returns_none_instead_of_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一条声明都没有时巡检跳过，而不是用猜出来的阈值判死在途任务。"""
    monkeypatch.setattr(maintenance, "registered_submit_budgets", lambda cap: [])
    assert _stale_after_sec() is None


def test_prod_requires_asset_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """生产环境不许依赖黑名单兜底（R12-04）。"""
    assert ImageAssetSettings.prod_required_fields == ("allowed_hosts",)
    monkeypatch.delenv("IMAGE_ASSET_ALLOWED_HOSTS", raising=False)

    with pytest.raises(RuntimeError, match="驱动配置缺失"):
        dc.ensure_prod_driver_settings("prod")
    assert dc.ensure_prod_driver_settings("test") is None, "非 prod 允许走兜底"

    monkeypatch.setenv("IMAGE_ASSET_ALLOWED_HOSTS", "cdn.example.com")
    assert dc.ensure_prod_driver_settings("prod") is None


def test_default_fallback_boundary_is_explicit() -> None:
    """把兜底的真实边界钉住：只挡非公网 IP 字面量，不解析 DNS。

    这不是「修好了 SSRF」的证明，恰恰相反——它提醒后来人生产必须配白名单，
    别把文件头注释当成实现。
    """
    for host in ("8.8.8.8", "attacker.example.com", "169-254-169-254.nip.io"):
        _validate_asset_url(f"http://{host}/x.png", frozenset())  # 放行
    with pytest.raises(ValueError, match="非公网 IP"):
        _validate_asset_url("http://169.254.169.254/latest/", frozenset())


def test_discovery_makes_startup_checks_see_every_driver() -> None:
    """注册表只记「被 import 过」的子类，启动自检不能靠谁碰巧 import 了驱动（R13-05）。

    只加载 echo 的部署里 `AgnesSettings` 原本不在册，`ensure_prod_driver_settings()`
    扫不到它；`maintenance._stale_after_sec()` 靠函数内 import 兜住纯属巧合。
    """
    dc.discover_driver_settings()
    registered = {cls.__name__ for cls in dc.registered_driver_settings()}
    assert "AgnesSettings" in registered
    assert "ImageAssetSettings" in registered


def test_precheck_computes_every_declared_budget() -> None:
    """启动期预检必须能算出 IMAGE 预算，否则巡检阈值无解（R13-06）。"""
    dc.discover_driver_settings()
    assert dc.precheck_submit_budgets().get("image"), (
        "没有驱动声明 IMAGE 提交预算：巡检会直接跳过，且没人会发现"
    )


def test_precheck_is_loud_on_broken_declaration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """坏配置在启动期就炸，而不是每 300 秒被调度器吞掉一条日志（R13-06）。"""

    class BadSettings:
        submit_budget_for = frozenset({"image"})

        def __init__(self) -> None:
            pass

    monkeypatch.setattr(dc, "registered_driver_settings", lambda: (BadSettings,))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="timeout_sec"):
        dc.precheck_submit_budgets()
