"""口令哈希的成本参数：只有测试环境降级，而且降级不影响「验得对不对」。

`APP_ENV=test` 时 scrypt 从 `2**14` 降到 `2**10`（150ms → 约 9ms）。全量测试里
真登录几百次，这一步原本吃掉一半以上的时间。

三条性质是降级成立的前提，任何一条破了都要让用例红：

- 只有 `test` 降级。哪天顺手把 dev/prod 也降了，那是安全问题，不是性能优化；
- 串里记的 N 与实际算的 N 一致。verify 只认串，记错就永远验不过；
- 不同强度的哈希可以互相验证。这是「不需要迁移存量口令」的依据。
"""

from __future__ import annotations

import pytest

from core import security
from core.config import reload_settings


def _recorded_n(encoded: str) -> int:
    """取哈希串里记录的 N：`scrypt$N$r$p$salt$digest`。"""
    return int(encoded.split("$")[1])


def test_test_env_uses_the_cheap_cost() -> None:
    assert security._scrypt_n() == security._TEST_SCRYPT_N
    assert _recorded_n(security.hash_password("pw")) == security._TEST_SCRYPT_N


def test_other_envs_keep_the_full_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "dev")
    reload_settings()
    assert security._scrypt_n() == security._PROD_SCRYPT_N
    assert _recorded_n(security.hash_password("pw")) == security._PROD_SCRYPT_N


def test_hash_made_with_the_cheap_cost_still_verifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """在 test 环境生成的哈希，切到满成本的环境后照样能验过。"""
    cheap = security.hash_password("pw")
    monkeypatch.setenv("APP_ENV", "dev")
    reload_settings()
    assert security.verify_password("pw", cheap) is True
    assert security.verify_password("pw-wrong", cheap) is False
