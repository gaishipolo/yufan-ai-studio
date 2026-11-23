"""「缺密钥」判定的唯一口径（`kit/credentials.py`）+ 防漂移守卫。

## 这组测试在防什么

2026-11-23 事故（设计文档 §2.4）：`GRSAI_API_KEY` 为空 → 请求根本没发出，
但驱动抛了 `UpstreamError`(502) → 框架 `_possibly_delivered` 按「可能已受理」
处理 → 用户看到「提交结果未知，请到上游核对一个不存在的任务」。

根因不是有人写错，而是**「缺 key 抛哪种异常」没有唯一真相**：grsai / bailian
抛 4xx，agnes / agnes_video 抛 5xx。本组测试把口径钉死，并加一条**架构守卫**，
让「某个驱动又自己写了一套缺 key 判定」这件事在 CI 里就暴露。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.exceptions import AppError, BadRequest, UpstreamError
from modules.model_driver.kit.credentials import require_api_key

_DRIVERS_DIR = (
    Path(__file__).resolve().parent.parent / "src" / "modules" / "model_driver" / "drivers"
)


# --------------------------------------------------------------------------- kit 单测


def test_empty_key_raises_bad_request() -> None:
    """空串 → 4xx。5xx 会落进「可能已受理」那一侧，把本地配置问题说成上游故障。"""
    with pytest.raises(BadRequest) as exc:
        require_api_key("", vendor="Agnes", env_name="AGNES_API_KEY")

    assert exc.value.http_status == 400
    assert "AGNES_API_KEY" in str(exc.value)
    assert "Agnes" in str(exc.value)


def test_blank_key_raises_bad_request() -> None:
    """纯空格等同于没配（`.env` 里手抖多打空格是常见形态）。"""
    with pytest.raises(BadRequest):
        require_api_key("   ", vendor="Agnes", env_name="AGNES_API_KEY")


def test_none_key_raises_bad_request() -> None:
    with pytest.raises(BadRequest):
        require_api_key(None, vendor="Agnes", env_name="AGNES_API_KEY")


def test_valid_key_is_returned_stripped() -> None:
    """有值就返回去掉空白的 key，调用方直接放进 Authorization 头。"""
    assert require_api_key("  sk-abc  ", vendor="Grsai", env_name="GRSAI_API_KEY") == "sk-abc"


def test_note_is_appended_for_context() -> None:
    """补充说明由驱动给（保留各驱动的上下文，不强行统一文案）。"""
    with pytest.raises(BadRequest) as exc:
        require_api_key("", vendor="百炼", env_name="BAILIAN_API_KEY", note="北京地域")

    assert "北京地域" in str(exc.value)


def test_missing_key_is_never_upstream_error() -> None:
    """反向断言：缺 key **永远**不是 5xx。

    这条是整个 kit 件存在的理由 —— 只要有驱动漏进来，它就会在 CI 里报。
    """
    with pytest.raises(AppError) as exc:
        require_api_key("", vendor="Agnes", env_name="AGNES_API_KEY")

    assert not isinstance(exc.value, UpstreamError)
    assert exc.value.http_status < 500


# --------------------------------------------------------------------------- 架构守卫


def test_drivers_do_not_roll_their_own_missing_key_check() -> None:
    """驱动里不允许再出现「自己判空 + 自己抛异常」的缺 key 写法。

    匹配 `if not ...api_key...` 之后 6 行内出现 `raise` 的形态；
    命中就说明该驱动绕过了 `kit.credentials.require_api_key`。
    """
    pattern = re.compile(
        r"if\s+not\s+[^\n]*api_key[^\n]*:\s*\n(?:\s+[^\n]*\n){0,6}?\s+raise\s+\w+",
        re.IGNORECASE,
    )
    offenders: list[str] = []
    for path in sorted(_DRIVERS_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for match in pattern.finditer(source):
            line_no = source[: match.start()].count("\n") + 1
            offenders.append(f"{path.name}:{line_no}")

    assert not offenders, (
        "这些驱动自己判空了 api_key —— 必须改用 "
        "modules.model_driver.kit.credentials.require_api_key（"
        + ", ".join(offenders)
        + "）"
    )


def test_all_drivers_import_the_shared_credential_check() -> None:
    """每个真正要发请求的驱动都走 kit 的凭据判定。

    `echo` 是零 HTTP 的假驱动，不参与。
    """
    expected = {
        "agnes.py",
        "agnes_video.py",
        "bailian.py",
        "grsai.py",
    }
    missing = [
        path.name
        for path in sorted(_DRIVERS_DIR.glob("*.py"))
        if path.name in expected
        and "require_api_key" not in path.read_text(encoding="utf-8")
    ]

    assert not missing, f"这些驱动没用 kit 的凭据判定：{missing}"
