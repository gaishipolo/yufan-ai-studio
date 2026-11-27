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
from modules.llm_config.store import (
    ModelMapping,
    credentials_of,
    delete_provider,
    keys_of,
    list_providers,
    normalize_name,
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
        models=(ModelMapping("llm/flash", "MiniMax-M3.1-Flash"),),
    )
    save_provider(
        "bailian",
        base_url="https://ws.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        api_key="sk-bailian-secret",
        models=(ModelMapping("llm/qwen", "qwen-plus"),),
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


def test_模型id必须带llm前缀且不重复(clean_env: None) -> None:
    with pytest.raises(ValueError, match="llm/"):
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
                ModelMapping("llm/a", "a1"),
                ModelMapping("llm/a", "a2"),
            ),
        )


def test_网关地址拒绝内网与参数(clean_env: None) -> None:
    for bad in ("notaurl", "http://127.0.0.1:8080", "https://x.example/v1?key=1"):
        with pytest.raises(ValueError):
            save_provider("x", base_url=bad, api_key="k", models=())


def test_旧形状LLM_BASE_URL显示为default且保存后统一到LLM下标(clean_env: None) -> None:
    """上一轮 V1 写的 LLM_BASE_URL/API_KEY/MODEL 不能在升级后凭空消失。

    default 保存后仍是 LLM_*（不是 LLM_DEFAULT_*）—— 必须与运行时
    LLMSettings 前缀一致，否则页面配好了 complete() 读空。
    """
    ENV_PATH.write_text(
        "LLM_BASE_URL=https://old.example/v1\n"
        "LLM_API_KEY=sk-old-key\n"
        "LLM_MODEL=MiniMax-M3.1-Flash\n",
        encoding="utf-8",
    )
    listed = list_providers().providers
    assert len(listed) == 1
    assert listed[0].name == "default"
    assert listed[0].models == (ModelMapping("llm/default", "MiniMax-M3.1-Flash"),)
    assert listed[0].api_key_configured is True

    # 保存统一到 LLM_* 下标：模型单值 LLM_MODEL → 数组 LLM_MODELS，密钥不丢
    save_provider(
        "default",
        base_url="https://new.example/v1",
        api_key="",
        models=(ModelMapping("llm/default", "qwen-plus"),),
    )
    text = ENV_PATH.read_text(encoding="utf-8")
    assert "LLM_MODEL=" not in text
    assert "LLM_BASE_URL=https://new.example/v1" in text
    assert "LLM_MODELS=" in text
    assert credentials_of("default")[1] == "sk-old-key"
    assert list_providers().providers[0].models[0].upstream == "qwen-plus"


def test_页面保存的default运行时complete读得到(clean_env: None) -> None:
    """跨层守卫：页面写 default 供应商的键，必须与 infra.llm 读的键是同一组。

    2026-11-27 审查抓到的断链：页面保存写 LLM_DEFAULT_*，运行时 LLMSettings
    前缀是 LLM_ 读 LLM_*，结果页面配好了调用却读空。两层各自测试都绿，
    只有把「保存 → 运行时读取」放进同一个用例才抓得到。
    """
    from infra.llm.providers.openai_compatible import get_llm_settings, resolve_model

    save_provider(
        "default",
        base_url="https://api.minimaxi.com/v1",
        api_key="sk-real-key",
        models=(ModelMapping("llm/flash", "MiniMax-M3.1-Flash"),),
    )
    settings = get_llm_settings()
    assert settings.base_url == "https://api.minimaxi.com/v1"
    assert settings.bearer_token() == "sk-real-key"
    assert resolve_model("llm/flash", settings) == "MiniMax-M3.1-Flash"


def test_早期LLM_DEFAULT形状可读且保存后清理(clean_env: None) -> None:
    ENV_PATH.write_text(
        "LLM_DEFAULT_BASE_URL=https://old.example/v1\n"
        "LLM_DEFAULT_API_KEY=sk-old\n"
        'LLM_DEFAULT_MODELS=[{"id":"llm/x","upstream":"X"}]\n',
        encoding="utf-8",
    )
    listed = list_providers().providers
    assert len(listed) == 1
    assert listed[0].base_url == "https://old.example/v1"
    assert listed[0].models[0].upstream == "X"

    save_provider("default", base_url="https://new.example/v1", api_key="", models=())
    text = ENV_PATH.read_text(encoding="utf-8")
    assert "LLM_DEFAULT_BASE_URL=" not in text
    assert "LLM_BASE_URL=https://new.example/v1" in text
    assert credentials_of("default")[1] == "sk-old"


def test_未迁移的default也能删掉(clean_env: None) -> None:
    """审查 P1：旧形状 default 点删除不能报「供应商不存在」。"""
    ENV_PATH.write_text(
        "LLM_BASE_URL=https://old.example/v1\nLLM_API_KEY=sk-old\nLLM_MODEL=M\n",
        encoding="utf-8",
    )
    delete_provider("default")
    remaining = [
        line for line in ENV_PATH.read_text(encoding="utf-8").splitlines() if line.startswith("LLM")
    ]
    assert remaining == []
    assert list_providers().providers == ()


def test_进程环境变量提供的项不能在页面改(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_X_BASE_URL", "https://from-env.example/v1")
    ENV_PATH.write_text("LLM_X_API_KEY=k\n", encoding="utf-8")
    with pytest.raises(ValueError, match="环境变量"):
        save_provider("x", base_url="https://from-page.example/v1", api_key="", models=())
