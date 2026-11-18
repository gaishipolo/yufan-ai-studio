"""LibTV 双账号：按计费模式选账号、账号绑定、每账号一把串行锁。

背景：9411 那个账号的积分快用完了，但它的 0 积分畅用（免费）队列照常能用；
积分加速必须落到 9412 那个还有额度的账号上。所以「哪个账号」不是随机或
轮询出来的，而是**由计费模式决定**的：

- `billingMode=credits` → `LIBTV_CREDITS_BROWSER_URL`（9412），**不降级**；
- `billingMode=free`    → 账号池里第一个在线的（9411 优先，掉线才用 9412）。

另一条不能破的约束是**账号绑定**：一次任务从上传到下载必须回同一个账号，
上游 `task_id` 和上传素材都属于那个账号。绑定记在 `run.json::browser_url`。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from modules.model_driver.libtv import executor as libtv
from modules.model_driver.libtv import process as libtv_process
from modules.model_driver.libtv.state import LibTVState

FREE = "http://127.0.0.1:9411"
CREDITS = "http://127.0.0.1:9412"


def _settings(
    *, browser_url: str = FREE, browser_urls: str = "", credits_browser_url: str = ""
) -> SimpleNamespace:
    return SimpleNamespace(
        browser_url=browser_url,
        browser_urls=browser_urls,
        credits_browser_url=credits_browser_url,
    )


@pytest.fixture
def two_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    """9411 免费、9412 积分。"""
    monkeypatch.setattr(
        libtv,
        "get_libtv_settings",
        lambda: _settings(browser_urls=f"{FREE},{CREDITS}", credits_browser_url=CREDITS),
    )


# ---------- 账号池解析 ----------


def test_single_account_configuration_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """没配账号池时退回单账号行为：老部署一行都不用改。"""
    monkeypatch.setattr(libtv, "get_libtv_settings", lambda: _settings())
    assert libtv.account_urls() == (FREE,)
    assert libtv.credit_account_url() == ""


def test_account_pool_is_ordered_and_deduplicated(two_accounts: None) -> None:
    assert libtv.account_urls() == (FREE, CREDITS)


def test_account_urls_tolerate_trailing_slash_and_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`...:9411/` 与 `...:9411 ` 是同一个账号，不该被当成两个。"""
    monkeypatch.setattr(
        libtv,
        "get_libtv_settings",
        lambda: _settings(browser_urls=f" {FREE}/ , {CREDITS} "),
    )
    assert libtv.account_urls() == (FREE, CREDITS)


# ---------- 选号规则 ----------


def test_credit_queue_uses_its_own_account(two_accounts: None) -> None:
    assert libtv.select_browser("credits", has_task=False) == CREDITS


def test_credit_queue_does_not_fall_back_to_the_free_only_account(
    monkeypatch: pytest.MonkeyPatch, two_accounts: None
) -> None:
    """9412 掉线时仍然返回 9412。

    回落到 9411 只会换来一次被上游拒绝的提交（那个账号没有积分），
    而且错误发生在「已经传完素材」之后。宁可报「浏览器未连接」。
    """
    monkeypatch.setattr(libtv, "browser_online", lambda url: url == FREE)
    assert libtv.select_browser("credits", has_task=False) == CREDITS


def test_free_queue_takes_the_first_online_account(
    monkeypatch: pytest.MonkeyPatch, two_accounts: None
) -> None:
    monkeypatch.setattr(libtv, "browser_online", lambda url: url == CREDITS)
    assert libtv.select_browser("free", has_task=False) == CREDITS


def test_free_queue_prefers_the_primary_account(
    monkeypatch: pytest.MonkeyPatch, two_accounts: None
) -> None:
    """两个都在线时用 9411：免费队列不花积分，额度快用完的账号正好拿来跑免费。"""
    monkeypatch.setattr(libtv, "browser_online", lambda url: True)
    assert libtv.select_browser("free", has_task=False) == FREE


def test_free_queue_falls_back_to_the_primary_when_all_are_offline(
    monkeypatch: pytest.MonkeyPatch, two_accounts: None
) -> None:
    """全都没起来也别返回空串：报错消息要带着一个具体端口，人才知道去看哪个。"""
    monkeypatch.setattr(libtv, "browser_online", lambda url: False)
    assert libtv.select_browser("free", has_task=False) == FREE


def test_existing_upstream_task_stays_on_the_submitting_account(
    two_accounts: None,
) -> None:
    """已经有 task_id 的历史任务：换号查不到任务，所以不按计费模式重挑。"""
    assert libtv.select_browser("credits", has_task=True) == FREE


# ---------- 账号绑定 ----------


def _state(monkeypatch: pytest.MonkeyPatch, **fields: Any) -> None:
    monkeypatch.setattr(libtv, "state", lambda path: LibTVState(id="a" * 32, **fields))


def test_pinned_account_wins_over_configuration(
    monkeypatch: pytest.MonkeyPatch, two_accounts: None
) -> None:
    """档案里绑的是 9411，就继续用 9411 —— 即使这一单是积分模式。

    这正是「改配置后历史任务仍然能查」的那条路径。
    """
    _state(monkeypatch, billing_mode="credits", browser_url=FREE, task_id="upstream")
    assert libtv.browser_for(Path(".")) == FREE


def test_unpinned_new_run_follows_the_billing_mode(
    monkeypatch: pytest.MonkeyPatch, two_accounts: None
) -> None:
    _state(monkeypatch, billing_mode="credits")
    assert libtv.browser_for(Path(".")) == CREDITS


def test_unpinned_free_run_uses_the_account_pool(
    monkeypatch: pytest.MonkeyPatch, two_accounts: None
) -> None:
    _state(monkeypatch, billing_mode="free")
    monkeypatch.setattr(libtv, "browser_online", lambda url: url == CREDITS)
    assert libtv.browser_for(Path(".")) == CREDITS


def test_missing_archive_falls_back_to_the_primary_account(
    monkeypatch: pytest.MonkeyPatch, two_accounts: None
) -> None:
    """档案读不到时不能把整个阶段卡在选号上：退回默认账号，让桥接层报真正的错。"""

    def broken(path: Any) -> LibTVState:
        raise FileNotFoundError("run.json")

    monkeypatch.setattr(libtv, "state", broken)
    assert libtv.browser_for(Path(".")) == FREE


# ---------- 账号级串行锁 ----------


def test_each_account_gets_its_own_lock() -> None:
    """两个账号各一把锁：9411 上传时 9412 的积分任务不该排队。"""
    assert libtv_process.account_lock_name(FREE) != libtv_process.account_lock_name(CREDITS)
    assert libtv_process.account_lock_name(FREE).startswith(libtv_process.ACCOUNT_LOCK_NAME)
    assert "9412" in libtv_process.account_lock_name(CREDITS)


def test_same_account_shares_one_lock() -> None:
    assert libtv_process.account_lock_name(FREE) == libtv_process.account_lock_name(f"{FREE}/")


def test_missing_account_keeps_the_legacy_lock_name() -> None:
    assert libtv_process.account_lock_name("") == libtv_process.ACCOUNT_LOCK_NAME


# ---------- 账号一路传到子进程 ----------


async def test_stage_passes_the_account_to_supervisor_and_bridge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, two_accounts: None
) -> None:
    """选中的账号必须同时进参数包与子进程环境。

    只给环境变量不够：监督进程要用参数包里的账号挑锁，两者取不同的来源就会
    「锁在 9411 上、浏览器连的是 9412」。
    """
    monkeypatch.setattr(libtv, "run_path", lambda _: tmp_path)
    _state(monkeypatch, billing_mode="credits")
    captured: dict[str, Any] = {}
    payloads: list[dict[str, Any]] = []

    def start(*args: Any, **kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)

        def communicate(payload: bytes) -> None:
            payloads.append(json.loads(payload))

        return SimpleNamespace(communicate=communicate, returncode=1)

    monkeypatch.setattr(libtv.subprocess, "Popen", start)
    await libtv.execute_stage("a" * 32, "submit")

    assert captured["env"]["JS_REVERSE_BROWSER_URL"] == CREDITS
    assert payloads[0]["browser"] == CREDITS


def test_browser_outage_names_the_offending_account() -> None:
    """报错里的端口是「去看哪个浏览器」的唯一线索，必须是真正出问题的那个。"""
    explained = libtv.explain_login_failure(
        libtv.ExecutorResult(code="ADAPTER_FAILED", stage="submit"),
        libtv.AdapterError(code="LOGIN_REQUIRED", stage="login", detail="fetch failed"),
        CREDITS,
    )
    assert "9412" in explained.message
    assert "9411" not in explained.message


async def test_account_request_ignores_socks_proxy_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """宿主环境带 ALL_PROXY=socks5:// 时，未装 socksio 也不该在建 client 时直接炸掉。"""
    import httpx

    from modules.model_driver.libtv import account

    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:7897")

    async def fake_headers(_account: str) -> dict[str, str]:
        return {"token": "tok"}

    async def fake_request(
        self: httpx.AsyncClient, method: str, url: str, **kwargs: Any
    ) -> httpx.Response:
        return httpx.Response(200, json={"code": 0, "data": {}})

    monkeypatch.setattr(account, "_headers", fake_headers)
    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    payload = await account._request(
        "GET", f"{account.API_ART}/api/www/member/account", account=CREDITS
    )
    assert payload == {"code": 0, "data": {}}


async def test_progress_merges_multi_account_responses(
    monkeypatch: pytest.MonkeyPatch, two_accounts: None
) -> None:
    """9411 查 9412 的任务会回 status=3/percent=0；多账号下要择优取归属账号的真实进度。"""
    from modules.model_driver.libtv import account

    async def fake_request(
        method: str, url: str, *, account: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if account == FREE:
            return {
                "code": 0,
                "data": {
                    "progresses": [
                        {
                            "taskId": "t-free",
                            "status": 0,
                            "progressPercent": 18,
                            "queueReason": "UNLIMITED_SERIAL",
                            "unlimitedQueueNum": 2,
                            "unlimitedQueueTotal": 4,
                            "unlimitedQueueLimit": 10,
                        },
                        {"taskId": "t-credits", "status": 3, "progressPercent": 0},
                    ]
                },
            }
        return {
            "code": 0,
            "data": {
                "progresses": [
                    {"taskId": "t-free", "status": 3, "progressPercent": 0},
                    {"taskId": "t-credits", "status": 1, "progressPercent": 66},
                ]
            },
        }

    monkeypatch.setattr(account, "_request", fake_request)
    items = await account.progress(["t-free", "t-credits"])

    by_id = {item.task_id: item for item in items}
    assert by_id["t-free"].status == 0 and by_id["t-free"].percent == 18
    assert by_id["t-credits"].status == 1 and by_id["t-credits"].percent == 66
