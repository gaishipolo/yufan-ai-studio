"""LLM 供应商配置的持久化守卫。

这些测试钉死「多供应商 + 模型映射」的 env 形状，以及两条安全纪律：
- 密钥明文不进任何出参（只有掩码与指纹）；
- 进程环境变量提供的值不在页面改（写了也不生效，不能骗用户）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.config import ENV_FILE
from core.exceptions import BadRequest
from modules.llm_config.migration import migrate_legacy_default, rename_provider
from modules.llm_config.store import (
    ModelMapping,
    credentials_of,
    delete_provider,
    keys_of,
    list_providers,
    normalize_name,
    read_model_chain,
    save_model_chain,
    save_provider,
)

ENV_PATH = Path(ENV_FILE)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个用例独占一份空白 .env，不碰真实配置。"""
    for key in list(__import__("os").environ):
        if key.startswith("LLM_"):
            monkeypatch.delenv(key, raising=False)
    original = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else None
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENV_PATH.write_text("# llm config test\n", encoding="utf-8")
    yield
    if original is None:
        ENV_PATH.unlink(missing_ok=True)
    else:
        ENV_PATH.write_text(original, encoding="utf-8")


def test_keys_of_按名字生成三个标准键() -> None:
    assert keys_of("minimax") == (
        "LLM_MINIMAX_BASE_URL",
        "LLM_MINIMAX_API_KEY",
        "LLM_MINIMAX_MODELS",
    )


def test_供应商名标准化为小写() -> None:
    assert normalize_name("  MiniMax  ") == "minimax"


@pytest.mark.parametrize("bad", ["", "1x", "a-b", "x" * 31])
def test_供应商名非法直接拒绝(bad: str) -> None:
    with pytest.raises(ValueError):
        normalize_name(bad)


def test_保存后能列出两供应商且模型映射正确(clean_env: None) -> None:
    save_provider(
        "minimax",
        base_url="https://api.minimaxi.com/v1",
        api_key="sk-minimax-secret",
        models=(ModelMapping("minimax/flash", "MiniMax-M3.1-Flash"),),
    )
    save_provider(
        "bailian",
        base_url="https://ws.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        api_key="sk-bailian-secret",
        models=(ModelMapping("bailian/qwen", "qwen-plus"),),
    )

    result = list_providers()
    assert [p.name for p in result.providers] == ["bailian", "minimax"]
    by_name = {p.name: p for p in result.providers}
    assert by_name["minimax"].models[0].upstream == "MiniMax-M3.1-Flash"
    assert by_name["bailian"].models[0].upstream == "qwen-plus"


def test_密钥明文不进出参只有掩码和指纹(clean_env: None) -> None:
    save_provider(
        "x",
        base_url="https://x.example/v1/",
        api_key="sk-abcdefgh1234",
        models=(),
    )
    listed = list_providers().providers[0]
    assert listed.api_key_configured is True
    assert "abcdefgh" not in listed.api_key_masked
    assert len(listed.api_key_fingerprint) == 8
    from dataclasses import asdict

    assert "sk-abcdefgh1234" not in json.dumps(asdict(listed))
    # base_url 尾部斜杠收拢
    assert listed.base_url == "https://x.example/v1"


def test_密钥留空表示不改(clean_env: None) -> None:
    save_provider("x", base_url="https://x.example/v1", api_key="sk-original", models=())
    save_provider("x", base_url="https://x2.example/v1", api_key="", models=())
    assert credentials_of("x")[1] == "sk-original"
    assert list_providers().providers[0].base_url == "https://x2.example/v1"


def test_删除供应商只删自己的键(clean_env: None) -> None:
    save_provider("a", base_url="https://a.example/v1", api_key="k-a", models=())
    save_provider("b", base_url="https://b.example/v1", api_key="k-b", models=())
    delete_provider("a")
    assert [p.name for p in list_providers().providers] == ["b"]
    assert credentials_of("b")[1] == "k-b"


def test_模型id必须带供应商前缀且不重复(clean_env: None) -> None:
    with pytest.raises(ValueError, match="x/"):
        save_provider(
            "x",
            base_url="https://x.example/v1",
            api_key="k",
            models=(ModelMapping("qwen-plus", "qwen-plus"),),
        )
    with pytest.raises(ValueError, match="重复"):
        save_provider(
            "x",
            base_url="https://x.example/v1",
            api_key="k",
            models=(
                ModelMapping("x/a", "a1"),
                ModelMapping("x/a", "a2"),
            ),
        )


def test_旧llm模型id不再兼容(clean_env: None) -> None:
    with pytest.raises(ValueError, match="已停用"):
        save_provider(
            "minimax",
            base_url="https://api.minimaxi.com/v1",
            api_key="k",
            models=(ModelMapping("llm/flash", "MiniMax-M3.1-Flash"),),
        )
    with pytest.raises(ValueError, match="minimax/"):
        save_provider(
            "minimax",
            base_url="https://api.minimaxi.com/v1",
            api_key="",
            models=(ModelMapping("other/flash", "x"),),
        )


def test_供应商改名会迁移env键模型前缀和链(clean_env: None) -> None:
    save_provider(
        "minimax",
        base_url="https://api.minimaxi.com/v1",
        api_key="k-minimax",
        models=(ModelMapping("minimax/flash", "F"), ModelMapping("minimax/pro", "P")),
    )
    save_provider(
        "bailian",
        base_url="https://bailian.example/v1",
        api_key="k-bailian",
        models=(ModelMapping("bailian/qwen", "Q"),),
    )
    save_model_chain(["minimax/pro", "bailian/qwen"])

    result = rename_provider("minimax", "minimax2")

    assert result.provider.name == "minimax2"
    assert result.model_ids["minimax/flash"] == "minimax2/flash"
    assert read_model_chain() == ("minimax2/pro", "bailian/qwen")
    text = ENV_PATH.read_text(encoding="utf-8")
    assert "LLM_MINIMAX_" not in text
    assert "LLM_MINIMAX2_BASE_URL=https://api.minimaxi.com/v1" in text
    assert "minimax2/flash" in text
    by_name = {provider.name: provider for provider in list_providers().providers}
    assert "minimax" not in by_name
    assert by_name["minimax2"].models[0].id == "minimax2/flash"
    assert credentials_of("minimax2")[1] == "k-minimax"


def test_网关地址拒绝内网与参数(clean_env: None) -> None:
    for bad in ("notaurl", "http://127.0.0.1:8080", "https://x.example/v1?key=1"):
        with pytest.raises(ValueError):
            save_provider("x", base_url=bad, api_key="k", models=())


def test_迁移旧default到具名供应商(clean_env: None) -> None:
    ENV_PATH.write_text(
        "LLM_BASE_URL=https://old.example/v1\n"
        "LLM_API_KEY=sk-old-key\n"
        'LLM_MODELS=[{"id":"llm/flash","upstream":"F"},{"id":"llm/pro","upstream":"P"}]\n'
        'LLM_MODEL_CHAIN=["llm/pro"]\n',
        encoding="utf-8",
    )
    assert list_providers().providers == ()

    result = migrate_legacy_default("primary")

    assert result.provider.name == "primary"
    assert result.model_ids["llm/flash"] == "primary/flash"
    assert read_model_chain() == ("primary/pro",)
    text = ENV_PATH.read_text(encoding="utf-8")
    assert "LLM_BASE_URL=" not in text
    assert "LLM_MODELS=" not in text
    assert "LLM_PRIMARY_BASE_URL=https://old.example/v1" in text
    assert "primary/flash" in text
    assert credentials_of("primary")[1] == "sk-old-key"
    assert [p.name for p in list_providers().providers] == ["primary"]


def test_迁移早期LLM_DEFAULT形状(clean_env: None) -> None:
    ENV_PATH.write_text(
        "LLM_DEFAULT_BASE_URL=https://old.example/v1\n"
        "LLM_DEFAULT_API_KEY=sk-old\n"
        'LLM_DEFAULT_MODELS=[{"id":"llm/x","upstream":"X"}]\n',
        encoding="utf-8",
    )
    result = migrate_legacy_default("primary")

    assert result.provider.models[0].id == "primary/x"
    assert read_model_chain() == ("primary/x",)
    text = ENV_PATH.read_text(encoding="utf-8")
    assert "LLM_DEFAULT_" not in text
    assert "LLM_PRIMARY_BASE_URL=https://old.example/v1" in text
    assert credentials_of("primary")[1] == "sk-old"


def test_迁移只配LLM_MODEL的旧V1配置(clean_env: None) -> None:
    ENV_PATH.write_text(
        "LLM_BASE_URL=https://old.example/v1\nLLM_API_KEY=sk-old\nLLM_MODEL=qwen-plus\n",
        encoding="utf-8",
    )
    result = migrate_legacy_default("primary")

    assert result.provider.models[0].id == "primary/qwen-plus"
    assert result.provider.models[0].upstream == "qwen-plus"
    assert read_model_chain() == ("primary/qwen-plus",)


def test_迁移把旧default模型id按上游名派生(clean_env: None) -> None:
    """真实生产形状：旧 LLM_MODELS 把 llm/default 当普通模型写进去。

    迁移不能拒绝，也不能留一个叫 primary/default 的模型；按上游名派生，
    并且不要把 llm/default 收窄成具体模型（它表示「走整条链」）。
    """
    ENV_PATH.write_text(
        "LLM_BASE_URL=https://old.example/v1\n"
        "LLM_API_KEY=sk-old\n"
        'LLM_MODELS=[{"id":"llm/default","upstream":"MiniMax-M3.1-Flash"},'
        '{"id":"llm/minimax-m3","upstream":"MiniMax-M3"}]\n'
        "LLM_MODEL_CHAIN=[]\n",
        encoding="utf-8",
    )

    result = migrate_legacy_default("primary")

    assert [m.id for m in result.provider.models] == [
        "primary/minimax-m3.1-flash",
        "primary/minimax-m3",
    ]
    assert "llm/default" not in result.model_ids
    assert result.model_ids["llm/minimax-m3"] == "primary/minimax-m3"
    # 旧的空链 = 没设过默认档：取第一项，不留下空链。
    assert read_model_chain() == ("primary/minimax-m3.1-flash",)


def test_default名称被保留(clean_env: None) -> None:
    with pytest.raises(ValueError, match="保留名"):
        normalize_name("default")
    with pytest.raises(ValueError, match="保留名"):
        save_provider("default", base_url="https://x.example/v1", api_key="k", models=())


def test_进程环境变量提供的项不能在页面改(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_X_BASE_URL", "https://from-env.example/v1")
    ENV_PATH.write_text("LLM_X_API_KEY=k\n", encoding="utf-8")
    with pytest.raises(ValueError, match="环境变量"):
        save_provider("x", base_url="https://from-page.example/v1", api_key="", models=())


def test_环境变量提供的供应商也能列出(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """容器部署常把 LLM_* 注入环境；页面不能只扫 .env（2026-11-27 审查）。"""
    monkeypatch.setenv("LLM_ENVONLY_BASE_URL", "https://env-only.example/v1")
    monkeypatch.setenv("LLM_ENVONLY_API_KEY", "sk-env-only")
    monkeypatch.setenv(
        "LLM_ENVONLY_MODELS", '[{"id":"envonly/flash","upstream":"EnvOnly"}]'
    )
    by_name = {provider.name: provider for provider in list_providers().providers}
    assert set(by_name) == {"envonly"}
    assert by_name["envonly"].source == "environment"
    assert by_name["envonly"].models == (ModelMapping("envonly/flash", "EnvOnly"),)


def test_默认模型完全由模型链决定(clean_env: None) -> None:
    from infra.llm.providers.openai_compatible import get_llm_settings

    save_provider(
        "primary",
        base_url="https://api.example/v1",
        api_key="sk-real",
        models=(ModelMapping("primary/flash", "F"), ModelMapping("primary/pro", "P")),
    )
    save_model_chain(["primary/pro", "primary/flash"])

    assert get_llm_settings().model_chain_ids() == ("primary/pro", "primary/flash")
    assert read_model_chain() == ("primary/pro", "primary/flash")


def test_保存或删除供应商会剪掉降级链里的失效模型(clean_env: None) -> None:
    save_provider(
        "minimax",
        base_url="https://api.minimaxi.com/v1",
        api_key="k",
        models=(ModelMapping("minimax/flash", "F"), ModelMapping("minimax/pro", "P")),
    )
    save_provider(
        "bailian",
        base_url="https://bailian.example/v1",
        api_key="k",
        models=(ModelMapping("bailian/qwen", "Q"),),
    )
    save_model_chain(["minimax/pro", "minimax/flash", "bailian/qwen"])

    # 从供应商里移除 minimax/pro → 链里对应项必须一起消失
    save_provider(
        "minimax",
        base_url="https://api.minimaxi.com/v1",
        api_key="",
        models=(ModelMapping("minimax/flash", "F"),),
    )
    assert read_model_chain() == ("minimax/flash", "bailian/qwen")

    delete_provider("minimax")
    assert read_model_chain() == ("bailian/qwen",)


def test_模型优先链的校验(clean_env: None) -> None:
    save_provider(
        "primary",
        base_url="https://api.example/v1",
        api_key="k",
        models=(ModelMapping("primary/a", "A"),),
    )
    with pytest.raises(ValueError, match="llm/default"):
        save_model_chain(["llm/default"])
    with pytest.raises(ValueError, match="旧 llm/*"):
        save_model_chain(["llm/a"])
    with pytest.raises(ValueError, match="不在任何供应商"):
        save_model_chain(["ghost/x"])
    with pytest.raises(ValueError, match="重复"):
        save_model_chain(["primary/a", "primary/a"])
    with pytest.raises(ValueError, match="不能为空"):
        save_model_chain([""])


def test_模型优先链被环境变量覆盖时拒绝页面保存(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_MODEL_CHAIN", '["llm/a"]')
    with pytest.raises(ValueError, match="环境变量"):
        save_model_chain([])


def test_坏模型列表只影响该供应商并返回configError(clean_env: None) -> None:
    """一个供应商的坏键不能把整个配置页变成 500。"""
    ENV_PATH.write_text(
        "LLM_BAD_BASE_URL=https://bad.example/v1\n"
        "LLM_BAD_MODELS=not-json\n"
        "LLM_GOOD_BASE_URL=https://good.example/v1\n"
        'LLM_GOOD_MODELS=[{"id":"good/flash","upstream":"Flash"}]\n',
        encoding="utf-8",
    )
    by_name = {provider.name: provider for provider in list_providers().providers}
    assert by_name["bad"].config_error.startswith("LLM_BAD_MODELS")
    assert by_name["bad"].models == ()
    assert by_name["good"].config_error == ""
    assert by_name["good"].models == (ModelMapping("good/flash", "Flash"),)


def test_坏模型列表运行时明确400(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from infra.llm.providers.openai_compatible import load_provider

    monkeypatch.setenv("LLM_BAD_BASE_URL", "https://bad.example/v1")
    monkeypatch.setenv("LLM_BAD_MODELS", "not-json")
    with pytest.raises(BadRequest, match="LLM_BAD_MODELS"):
        load_provider("bad")


def test_一百个模型可保存且不被2048长度卡住(clean_env: None) -> None:
    """上限是「100 个模型」，不能在实际只有二十几个时被行长先拦住。"""
    models = tuple(ModelMapping(f"x/m{i}", "u" * 80) for i in range(100))
    save_provider(
        "x",
        base_url="https://x.example/v1",
        api_key="k",
        models=models,
    )
    assert len(list_providers().providers[0].models) == 100

    with pytest.raises(ValueError, match="最多"):
        save_provider(
            "x",
            base_url="https://x.example/v1",
            api_key="",
            models=models + (ModelMapping("x/extra", "u"),),
        )


def test_坏降级链不阻断供应商列表(clean_env: None) -> None:
    from modules.llm_config.router import _providers_out
    from modules.llm_config.store import ProviderListOut

    ENV_PATH.write_text("LLM_MODEL_CHAIN=not-json\n", encoding="utf-8")
    out = _providers_out(ProviderListOut(()))
    assert out.model_chain == []
    assert "不是合法 JSON" in out.model_chain_error


def test_供应商超时来自环境变量(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """连通测试与正式调用共用 LLM_<NAME>_TIMEOUT_SEC，不再写死 30s。"""
    from infra.llm.providers.source import provider_config

    monkeypatch.setenv("LLM_X_BASE_URL", "https://x.example/v1")
    monkeypatch.setenv("LLM_X_TIMEOUT_SEC", "7.5")
    assert provider_config("x").timeout_sec == 7.5

    monkeypatch.setenv("LLM_X_TIMEOUT_SEC", "not-a-number")
    with pytest.raises(ValueError, match="不是数字"):
        provider_config("x")


def test_私网网关需要LLM_ALLOWED_HOSTS(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from infra.env_settings import clear_cache

    clear_cache()
    with pytest.raises(ValueError, match="LLM_ALLOWED_HOSTS"):
        save_provider(
            "local",
            base_url="http://127.0.0.1:11434/v1",
            api_key="k",
            models=(),
        )

    monkeypatch.setenv("LLM_ALLOWED_HOSTS", "127.0.0.1")
    save_provider(
        "local",
        base_url="http://127.0.0.1:11434/v1",
        api_key="k",
        models=(),
    )
    assert list_providers().providers[0].base_url == "http://127.0.0.1:11434/v1"

    # 白名单是额外放行，不是替换公网校验：未登记的其他私网仍拒
    with pytest.raises(ValueError, match="LLM_ALLOWED_HOSTS"):
        save_provider(
            "other",
            base_url="http://10.0.0.5:8000/v1",
            api_key="k",
            models=(),
        )

    # 公网主机不受白名单影响
    save_provider(
        "public",
        base_url="https://api.example.com/v1",
        api_key="k",
        models=(),
    )


def test_保存删除与模型链都持有env写锁(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextlib import contextmanager

    import modules.llm_config.store as store_module

    calls: list[int] = []

    @contextmanager
    def spy():
        calls.append(1)
        yield

    monkeypatch.setattr(store_module, "env_write_lock", spy)
    store_module.save_provider(
        "x", base_url="https://x.example/v1", api_key="k", models=()
    )
    store_module.save_model_chain([])
    store_module.delete_provider("x")
    assert len(calls) == 3
