"""生图任务名：模板库传模板名时用模板名，否则仍从提示词派生。

视频任务走 `POST /tasks`，有 `title` 字段，模板库传的是模板名；
生图以前没有名字字段，后端直接拿提示词前 120 字当名字，卡片被一长段话挤满，
同一个模板出的多条任务也看不出是同一个模板。现在有了可选 `name`。

这里读**数据库里的 ImageJob.name**，不是响应里的映射 —— 响应可能另有加工，
钉住真值才能确定「任务中心那张卡片显示什么」。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi.testclient import TestClient

from db.session import get_session_factory
from modules.image.models import ImageJob

API = "/api/v1"


def _login(client: TestClient) -> dict[str, str]:
    resp = client.post(
        f"{API}/identity/login", json={"username": "admin", "password": "admin@12345"}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['accessToken']}"}


def _stored_name(job_id: str) -> str:
    async def run() -> str:
        async with get_session_factory()() as session:
            row = await session.get(ImageJob, job_id)
            assert row is not None, f"生图记录不存在：{job_id}"
            return row.name

    return asyncio.run(run())


def _cleanup(job_id: str) -> None:
    async def run() -> None:
        from sqlalchemy import delete

        async with get_session_factory()() as session:
            await session.execute(delete(ImageJob).where(ImageJob.id == job_id))
            await session.commit()

    asyncio.run(run())


def test_same_idempotency_key_replays_the_same_image_job(
    client: TestClient,
) -> None:
    """双击/超时重试只创建一条生图；同键换参数必须 409。"""
    from uuid import uuid4

    headers = _login(client)
    key = f"image-{uuid4().hex}"
    body = {"prompt": "幂等测试：一只白猫", "idempotencyKey": key}
    first = _submit(client, headers, body)
    second = _submit(client, headers, body)
    assert first == second

    changed = client.post(
        f"{API}/generations/image",
        json={**body, "prompt": "同键但换了提示词"},
        headers=headers,
    )
    assert changed.status_code == 409, changed.text
    _cleanup(first)


def _submit(client: TestClient, headers: dict[str, str], body: dict[str, Any]) -> str:
    resp = client.post(f"{API}/generations/image", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["data"]["id"])


def test_传了name就用它当任务名(client: TestClient) -> None:
    """模板库那条路：传模板名，卡片上显示的应该是模板名而不是整段提示词。"""
    headers = _login(client)
    job_id = _submit(
        client,
        headers,
        {"prompt": "一段很长的提示词，本不该出现在任务名里", "name": "都在模仿我的版型"},
    )
    try:
        assert _stored_name(job_id) == "都在模仿我的版型"
    finally:
        _cleanup(job_id)


def test_不传name仍从提示词派生(client: TestClient) -> None:
    """直接生图（不经过模板库）没有模板名，保持老行为，别把任务名弄成空。"""
    headers = _login(client)
    job_id = _submit(client, headers, {"prompt": "赛博朋克少女半身像"})
    try:
        assert _stored_name(job_id) == "赛博朋克少女半身像"
    finally:
        _cleanup(job_id)


def test_name超长被拒(client: TestClient) -> None:
    """和库列 200 字对齐：超长在入参就拦，别等写库被截。"""
    headers = _login(client)
    resp = client.post(
        f"{API}/generations/image",
        json={"prompt": "正常提示词", "name": "长" * 201},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text


def test_模板名的长度能原样存下(client: TestClient) -> None:
    """模板名允许 160 字，模板库把它原样当任务名传 —— 卡片上要看到完整的名字。"""
    headers = _login(client)
    title = "长模板名" * 40  # 160 字，正好是模板名的上限
    job_id = _submit(client, headers, {"prompt": "正常提示词", "name": title})
    try:
        assert _stored_name(job_id) == title
    finally:
        _cleanup(job_id)


def test_任务名上限与模板名和视频标题对齐() -> None:
    """三个上限必须是同一条线：模板名（160）≤ 生图任务名 ≤ 库列宽 = 视频标题。

    漂移的表现是「模板能发布、点生成才 422」：模板名上限 160，模板库把它原样
    当任务名传，生图接口的上限比它小就把提交挡掉了。列宽又比接口小，则写库时
    截断，同一模板的生图与视频卡片会显示成两个名字。
    """
    from annotated_types import MaxLen

    from modules.image.schema import GenerateImageIn
    from modules.task.schema import CreateTaskIn
    from modules.template_pack.spec_schema import TemplateSpecIn

    def _limit(model: Any, field: str) -> int:
        return [
            item.max_length
            for item in model.model_fields[field].metadata
            if isinstance(item, MaxLen)
        ][0]

    image_limit = _limit(GenerateImageIn, "name")
    assert image_limit >= _limit(TemplateSpecIn, "name")
    assert image_limit == _limit(CreateTaskIn, "title")
    assert image_limit == ImageJob.__table__.c.name.type.length
