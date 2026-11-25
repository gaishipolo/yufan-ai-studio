"""模型目录的清单与显示名口径（2026-11-25 拍板）。

- Grsai Nano 只留 4 个基础型号、GPT 只留 2 个基础型号，变体全部下线；
- 模型 id 是接口契约，改名/裁剪时**不能动**；
- 显示名统一「供应商 · 模型」，旧显示名进 aliases 让历史记录仍可解析。

这些约束都不会在运行时报错（少的模型只是下拉里看不见、旧名解析不了只是
历史任务失败），所以必须用测试钉住，不能靠人记。
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from infra.ai import get_model_router, reset_model_router
from modules.model_driver import registry
from modules.model_driver.drivers import grsai

EXPECTED_GRSAI_IDS = frozenset(
    {
        "grsai-nano-banana",
        "grsai-nano-banana-fast",
        "grsai-nano-banana-2",
        "grsai-nano-banana-pro",
        "grsai-gpt-image-2.5",
        "grsai-gpt-image-2.6",
    }
)

#: 下线型号的 id —— 它们的接口 id 也不能被复用给别的模型。
RETIRED_GRSAI_IDS = frozenset(
    {
        "grsai-nano-banana-2-cl",
        "grsai-nano-banana-2-2k-cl",
        "grsai-nano-banana-2-4k-cl",
        "grsai-nano-banana-2.1",
        "grsai-nano-banana-pro-vt",
        "grsai-nano-banana-pro-cl",
        "grsai-nano-banana-pro-vip",
        "grsai-nano-banana-pro-4k-vip",
        "grsai-gpt-image-2",
        "grsai-gpt-image-2-vip",
        "grsai-gpt-image-2.5-flare",
        "grsai-gpt-image-2.5-sunburst",
        "grsai-gpt-image-2.6-flare",
        "grsai-gpt-image-2.6-sunburst",
    }
)


def test_grsai_model_list_is_exactly_the_six_approved_models() -> None:
    ids = frozenset(m.id for m in grsai.MODELS)
    assert ids == EXPECTED_GRSAI_IDS
    assert ids.isdisjoint(RETIRED_GRSAI_IDS), "下线型号不得重新出现在目录里"


def test_grsai_labels_follow_vendor_dot_model_and_keep_old_aliases() -> None:
    for spec in grsai.MODELS:
        assert spec.label.startswith("Grsai · "), spec.id
        # 每个保留型号都必须把改名前的展示名带在 aliases 里。
        assert spec.aliases, f"{spec.id} 缺旧展示名 alias，历史任务会解析失败"
        assert all("·" not in alias for alias in spec.aliases), spec.id


def test_all_public_model_labels_follow_vendor_dot_model() -> None:
    drivers = Path(__file__).resolve().parents[1] / "src/modules/model_driver/drivers"
    checked = 0
    for path in sorted(drivers.glob("*.py")):
        if path.stem.startswith("_"):
            continue
        module = importlib.import_module(f"modules.model_driver.drivers.{path.stem}")
        for spec in getattr(module, "MODELS", ()):
            # echo 是 placeholder 示例驱动，不出现在任何模型选择器里。
            if spec.placeholder or spec.internal:
                continue
            assert " · " in spec.label, f"{spec.id} 的显示名 {spec.label!r} 不是『供应商 · 模型』"
            checked += 1
    assert checked >= 12, "没扫到足够的公开模型，守卫可能失效"


def test_legacy_grsai_labels_still_resolve() -> None:
    """改名前的展示名必须仍能解析到同一个 id（历史任务按展示名存过）。"""
    reset_model_router()
    registry.ensure_drivers_registered()
    router = get_model_router()
    for spec in grsai.MODELS:
        for old_label in spec.aliases:
            resolved = router.resolve(old_label)
            assert resolved is not None, f"旧展示名 {old_label!r} 解析不到模型"
            assert resolved.id == spec.id
    pytest.importorskip("modules.model_driver.drivers.bailian")
    assert router.resolve("百炼万相3.0") is not None
    assert router.resolve("Agnes Image 2.5 Flash") is not None
    assert router.resolve("Wan 3.0") is not None
    reset_model_router()
