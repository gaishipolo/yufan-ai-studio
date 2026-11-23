"""`/tasks` 的通用参数通道，与「计费模式由使用者选」。

## 为什么要有这条通道

以前 `/tasks` 的参数只有四个写死的顶层键（时长 / 画幅 / 风格 / 计费），于是
前端只有两个选择：写死一套表单（模型一改就 400），或者把声明的参数全塞进
提示词（供应商收到的是一段文字，不是参数）。

工作台写死表单的直接后果是**提交必定 400**：它无条件带上 `stylePreset`，
而所有模型都没声明这个参数 —— 实测
`模型 Agnes Video 2.5 Flash 没有声明风格预设（stylePreset），不能指定风格`。

现在键一律来自模型 `paramSchema`，未声明的键当场 400。

## 为什么计费模式不由模板作者写死

写死 = 作者替每个使用者决定了要不要花积分，而积分是**用的人**的钱。
所以标了 `userSelectable` 的参数：封装时设了就报错，使用时由使用者选。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from core.exceptions import BadRequest
from infra.ai.base import Capability, ModelSpec

LIBTV = "libtv-wan3.0"

#: 一个**声明了额外参数**的测试模型。真实驱动目前都只声明了时长与画幅，
#: 而这条通道的价值就在于「模型多声明一个参数，前端不用改」。
EXTRA_PARAM_MODEL = "probe-extra-param"


def _spec(param_schema: dict[str, Any]) -> ModelSpec:
    return ModelSpec(
        id="m",
        label="演示",
        provider="probe",
        capability=Capability.VIDEO,
        param_schema=param_schema,
    )


@pytest.fixture()
def extra_param_model() -> Iterator[str]:
    """临时注册一个声明了额外参数的模型，测完摘掉。

    路由是进程单例：不摘的话会漏给后面的用例，「公开目录里只有哪些模型」
    那类断言会被自己人破坏。

    **单独一个供应商名**：摘的时候按供应商名整个摘，挂在 libtv 上会把
    LibTV 自己的模型一起带走（后面那条用例就找不到模型了）。
    """
    from infra.ai import get_model_router

    router = get_model_router()
    spec = ModelSpec(
        id=EXTRA_PARAM_MODEL,
        label="探针·额外参数",
        provider="probe_extra",
        capability=Capability.VIDEO,
        param_schema={
            "durationSec": {"type": "int", "min": 2, "max": 30, "step": 1, "default": 5},
            "aspectRatio": {"type": "enum", "options": ["9:16"], "default": "9:16"},
            "quality": {"type": "enum", "options": ["hd", "sd"], "default": "hd"},
            "seed": {"type": "int", "min": 0, "max": 100, "default": 7},
        },
    )

    class _Provider:
        name = "probe_extra"
        capabilities = frozenset({Capability.VIDEO})
        supports_poll = False
        models = (spec,)

    router.register(_Provider())
    try:
        yield EXTRA_PARAM_MODEL
    finally:
        router.unregister("probe_extra")


# ------------------------------------------------------- 参数层：键与值的口径


def test_extra_params_follow_the_model_schema() -> None:
    """声明了的键按 schema 收敛；没声明的键当场拒。"""
    from modules.task.params import resolve_extra_params

    declaring = _spec(
        {
            "durationSec": {"type": "int", "min": 2, "max": 30, "default": 5},
            "quality": {"type": "enum", "options": ["hd", "sd"], "default": "hd"},
            "seed": {"type": "int", "min": 0, "max": 100, "default": 7},
        }
    )
    # 枚举值收敛成字符串，数字按声明的类型走
    assert resolve_extra_params(declaring, {"quality": "sd"}) == {"quality": "sd"}
    assert resolve_extra_params(declaring, {"seed": 42}) == {"seed": 42}

    # 没声明的键 → 明确报错，不是静默忽略
    with pytest.raises(BadRequest, match="szie"):
        resolve_extra_params(declaring, {"szie": "1024x1536"})

    # 枚举值不在 options 内 → 报错
    with pytest.raises(BadRequest, match="quality"):
        resolve_extra_params(declaring, {"quality": "ultra"})

    # 超出范围 → 报错
    with pytest.raises(BadRequest, match="seed"):
        resolve_extra_params(declaring, {"seed": 999})


def test_extra_params_reject_the_top_level_keys() -> None:
    """顶层那几个键有专门的字段，不许再塞进 `params`。

    两份真相的后果：首提读顶层、重试读 params —— 同一条任务两次用的时长
    可能不一样，而用户看不出来。
    """
    from modules.task.params import TOP_LEVEL_KEYS, resolve_extra_params

    spec = _spec(
        {
            "durationSec": {"type": "int", "min": 2, "max": 30, "default": 5},
            "aspectRatio": {"type": "enum", "options": ["9:16"], "default": "9:16"},
            "stylePreset": {"type": "enum", "options": ["film"], "default": "film"},
            "billingMode": {"type": "enum", "options": ["free"], "default": "free"},
        }
    )
    for key in sorted(TOP_LEVEL_KEYS):
        with pytest.raises(BadRequest, match=key):
            resolve_extra_params(spec, {key: "x"})


def test_extra_params_keep_only_the_keys_the_user_gave() -> None:
    """没给值的键不进结果 —— 那些值该由模型默认值决定。

    落库就意味着「用户提交过」：重试时它是个显式选择，而用户当时并没有选。
    """
    from modules.task.params import resolve_extra_params

    spec = _spec(
        {
            "durationSec": {"type": "int", "min": 2, "max": 30, "default": 5},
            "quality": {"type": "enum", "options": ["hd", "sd"], "default": "hd"},
        }
    )
    assert resolve_extra_params(spec, {}) == {}
    assert resolve_extra_params(spec, {"quality": "sd"}) == {"quality": "sd"}


def test_extra_params_reject_null() -> None:
    """`null` 不是「没传」：传了 null 却被静默丢掉的话，用户以为改了那个值。"""
    from modules.task.params import resolve_extra_params

    spec = _spec({"quality": {"type": "enum", "options": ["hd"], "default": "hd"}})
    with pytest.raises(BadRequest, match="null"):
        resolve_extra_params(spec, {"quality": None})  # type: ignore[dict-item]


def test_extra_params_pass_through_when_the_model_declares_nothing() -> None:
    """模型一个参数都没声明 = 没有契约可依，原样透传给供应商判断。"""
    from modules.task.params import resolve_extra_params

    spec = _spec({})
    assert resolve_extra_params(spec, {"whatever": 1}) == {"whatever": 1}


# --------------------------------------------------------------- 受理期与落库


def test_unknown_params_are_rejected_at_intake(client: TestClient, api: str) -> None:
    """未声明的键在受理期就 400 —— 用户不用等生成跑完才发现参数没用上。"""
    response = client.post(
        f"{api}/tasks",
        json={
            "prompt": "带一个没声明的参数",
            "model": LIBTV,
            "params": {"szie": "1024x1536"},
        },
    )
    assert response.status_code == 400, response.text
    assert "szie" in response.json()["message"]


def test_top_level_keys_are_rejected_inside_params(client: TestClient, api: str) -> None:
    """顶层键塞进 `params` → 400。"""
    response = client.post(
        f"{api}/tasks",
        json={
            "prompt": "顶层键重复了",
            "model": LIBTV,
            "params": {"durationSec": 8},
        },
    )
    assert response.status_code == 400, response.text
    assert "durationSec" in response.json()["message"]


def test_declared_params_are_accepted_and_frozen(
    client: TestClient, api: str, extra_param_model: str
) -> None:
    """模型声明了的参数被接受，并落进 `params_json`。

    落库是必须的：重试是从这一行重建生成请求的，不落库就会用默认参数重新
    提交 —— 用户选的参数无声丢失，而页面上什么问题都看不出来。
    """
    from db.session import get_session_factory
    from modules.task.models import VideoTask, parse_params_json
    from modules.task.pipeline import VideoTaskStore

    response = client.post(
        f"{api}/tasks",
        json={
            "prompt": "带一个模型声明过的参数",
            "model": extra_param_model,
            "params": {"quality": "sd", "seed": 42},
        },
    )
    assert response.status_code == 201, response.text
    task_id = response.json()["data"]["id"]

    async def read() -> tuple[str, dict[str, Any]]:
        async with get_session_factory()() as session:
            row = await session.get(VideoTask, task_id)
            assert row is not None
            record = await VideoTaskStore(session).load(task_id)
            assert record is not None
            return row.params_json, dict(record.params)

    params_json, record_params = asyncio.run(read())
    assert parse_params_json(params_json) == {"quality": "sd", "seed": 42}
    # 重建记录时也要带上：重试用的是这份
    assert record_params["quality"] == "sd"
    assert record_params["seed"] == 42


def test_params_are_rejected_when_the_model_declares_none_of_them(
    client: TestClient, api: str
) -> None:
    """模型没声明的参数 → 400（与生图那条链同一口径）。"""
    response = client.post(
        f"{api}/tasks",
        json={
            "prompt": "模型不认的参数",
            "model": LIBTV,
            "params": {"quality": "hd"},
        },
    )
    assert response.status_code == 400, response.text
    assert "quality" in response.json()["message"]


# ------------------------------------------------- 计费模式：由使用者选而不是作者


def test_libtv_marks_billing_mode_as_user_selectable() -> None:
    """LibTV 必须标 `userSelectable` —— 积分是用的人的钱，作者不该替别人定。"""
    from modules.model_driver.drivers.libtv import MODELS

    spec = next(item for item in MODELS if item.id == LIBTV)
    field = spec.param_schema.get("billingMode")
    assert field is not None
    assert field.get("userSelectable") is True


def _create_spec(client: TestClient, api: str, body: dict[str, Any]) -> dict[str, str]:
    """建一份模板草稿，返回带鉴权头的 dict。

    保存草稿返回 **200**（不是 201）：这是「同内容幂等」的保存接口，
    重复保存不新建版本。
    """
    from library_support import admin_headers

    headers = admin_headers(client)
    response = client.post(f"{api}/template-specs", headers=headers, json=body)
    assert response.status_code == 200, response.text
    return headers


def test_author_may_not_freeze_a_user_selectable_param(client: TestClient, api: str) -> None:
    """作者在封装页写死计费模式 → 报错，并说清该由谁来选。"""
    from library_support import admin_headers

    headers = admin_headers(client)
    response = client.post(
        f"{api}/template-specs",
        headers=headers,
        json={
            "key": "billing-locked",
            "name": "写死计费模式的模板",
            "model": LIBTV,
            "prompt": "固定模板",
            "params": {"billingMode": "credits"},
            "slots": [],
        },
    )
    assert response.status_code == 400, response.text
    assert "使用模板的人" in response.json()["message"]


def test_user_choice_is_merged_into_the_compiled_result(
    client: TestClient, api: str
) -> None:
    """使用者在模板库选的值随 compile 走，但不落进模板版本。"""
    headers = _create_spec(
        client,
        api,
        {
            "key": "billing-choosable",
            "name": "计费模式可选的模板",
            "model": LIBTV,
            "prompt": "固定模板",
            "params": {},
            "slots": [],
        },
    )
    published = client.post(
        f"{api}/template-specs/billing-choosable/publish", headers=headers, json={}
    )
    assert published.status_code == 200, published.text

    compiled = client.post(
        f"{api}/template-specs/billing-choosable/compile",
        headers=headers,
        json={"paramOverrides": {"billingMode": "credits"}},
    )
    assert compiled.status_code == 200, compiled.text
    assert compiled.json()["data"]["params"]["billingMode"] == "credits"

    # 模板版本本身没被改：使用者的选择不落进 spec.params
    detail = client.get(f"{api}/template-specs/billing-choosable", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["latest"]["params"].get("billingMode") is None


def test_user_may_not_override_an_author_owned_param(
    client: TestClient, api: str
) -> None:
    """使用者只能改标了 `userSelectable` 的键；作者设的改不了。"""
    headers = _create_spec(
        client,
        api,
        {
            "key": "override-guard",
            "name": "作者设了时长的模板",
            "model": LIBTV,
            "prompt": "固定模板",
            "params": {"durationSec": 6},
            "slots": [],
        },
    )
    client.post(f"{api}/template-specs/override-guard/publish", headers=headers, json={})

    bad = client.post(
        f"{api}/template-specs/override-guard/compile",
        headers=headers,
        json={"paramOverrides": {"durationSec": 12}},
    )
    assert bad.status_code == 400, bad.text
    assert "durationSec" in bad.json()["message"]


def test_user_override_with_an_illegal_value_is_rejected(
    client: TestClient, api: str
) -> None:
    """枚举值必须落在 options 内 —— 非法值当场 400。"""
    headers = _create_spec(
        client,
        api,
        {
            "key": "override-value",
            "name": "计费值校验",
            "model": LIBTV,
            "prompt": "固定模板",
            "params": {},
            "slots": [],
        },
    )
    client.post(f"{api}/template-specs/override-value/publish", headers=headers, json={})

    bad = client.post(
        f"{api}/template-specs/override-value/compile",
        headers=headers,
        json={"paramOverrides": {"billingMode": "premium"}},
    )
    assert bad.status_code == 400, bad.text
    assert "billingMode" in bad.json()["message"]


def test_user_may_not_pass_a_param_the_model_does_not_know(
    client: TestClient, api: str
) -> None:
    """模型压根没这个参数 → 400，不是「作者改不了」那种理由。"""
    headers = _create_spec(
        client,
        api,
        {
            "key": "override-unknown",
            "name": "未知参数",
            "model": LIBTV,
            "prompt": "固定模板",
            "params": {},
            "slots": [],
        },
    )
    client.post(f"{api}/template-specs/override-unknown/publish", headers=headers, json={})

    bad = client.post(
        f"{api}/template-specs/override-unknown/compile",
        headers=headers,
        json={"paramOverrides": {"nope": 1}},
    )
    assert bad.status_code == 400, bad.text
    assert "nope" in bad.json()["message"]
