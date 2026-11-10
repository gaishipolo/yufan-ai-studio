"""生图参数透传：模型声明的参数要能提交、落库、重试时还原。

背景：生图入参原先只有 `size` / `count` 两个硬编码字段，模型在 `param_schema`
里多声明一个 `quality` 也没有任何路径送达供应商；前端同理只渲染那两个控件。
现在参数面完全由 schema 决定，这条链路必须整根通：受理校验 → 落库 → 重建
`GenerationRecord`（重试走这条）→ 送达 `provider.submit`。
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from modules.image.models import ImageJob, dumps_params, parse_params_json
from modules.image.pipeline import ImageJobStore
from modules.image.schema import ImageJobOut

API = "/api/v1"


def _login(
    client: TestClient, username: str = "admin", password: str = "admin@12345"
) -> dict[str, str]:
    resp = client.post(f"{API}/identity/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['accessToken']}"}


def _job(**overrides: object) -> ImageJob:
    """未 flush 的 ORM 实例：列默认值不会自动填，这里显式给全，才能直接过出参校验。"""
    fields: dict[str, object] = {
        "id": "img-params-1",
        "name": "参数透传",
        "prompt": "a white cat",
        "size": "1024x1024",
        "count": 1,
        "owner_id": "u_001",
        "status": "queued",
        "progress": 0,
        "model": "Seedream 3",
        "cover_url": "",
        "created_at": datetime(2026, 11, 10, tzinfo=UTC),
    }
    fields.update(overrides)
    return ImageJob(**fields)  # type: ignore[arg-type]


def test_extra_params_survive_the_record_mapping() -> None:
    """重试是从 `_to_record` 重建请求的：这里丢掉就等于重试时静默换参数。"""
    job = _job(params_json='{"quality": "high", "steps": 20}')
    record = ImageJobStore._to_record(job)

    assert record.params == {"quality": "high", "steps": 20, "size": "1024x1024", "count": 1}


def test_columns_win_over_stale_json_keys() -> None:
    """历史脏数据里若混进 size / count，以独立列为准（列表和角标读的是列）。"""
    job = _job(size="768x1024", count=2, params_json='{"size": "1x1", "count": 9}')
    record = ImageJobStore._to_record(job)

    assert record.params["size"] == "768x1024"
    assert record.params["count"] == 2


def test_out_schema_exposes_params() -> None:
    """前端换模型回显 / 历史详情要能看到当时用的参数。"""
    job = _job(params_json='{"quality": "low"}')
    assert ImageJobOut.model_validate(job).params == {"quality": "low"}


def test_blank_or_broken_json_is_treated_as_no_params() -> None:
    """老记录是空串、手改坏的 JSON 也不能让列表接口 500。"""
    assert parse_params_json("") == {}
    assert parse_params_json(None) == {}
    assert parse_params_json("{not json") == {}
    assert parse_params_json("[1, 2]") == {}
    assert dumps_params({}) == ""


def test_unknown_param_is_rejected_at_submit(client: TestClient) -> None:
    """打错字的键要当场 400：被静默忽略的话，用户以为改的值生效了。"""
    headers = _login(client)
    resp = client.post(
        f"{API}/generations/image",
        headers=headers,
        json={"prompt": "参数打错字", "params": {"szie": "512x512"}},
    )

    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert "szie" in str(body)
    assert body.get("details", {}).get("availableParams")


def test_declared_param_round_trips_through_the_api(client: TestClient) -> None:
    """模型没声明额外字段时 `params` 也能安全传空对象，接口不被卡住。"""
    headers = _login(client)
    created = client.post(
        f"{API}/generations/image",
        headers=headers,
        json={"prompt": "参数透传冒烟", "size": "1024x1024", "count": 1, "params": {}},
    )
    assert created.status_code == 201, created.text
    data = created.json()["data"]
    assert data["params"] == {}
    # 顶层 size / count 仍然按列回读，不因为引入 params 而变形
    assert data["size"] == "1024x1024"
    assert data["count"] == 1
