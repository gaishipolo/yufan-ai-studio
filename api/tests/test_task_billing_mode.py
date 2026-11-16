"""LibTV 计费模式与参考素材时长门禁。

两件事，一件是**新功能**（计费模式从模型 schema 一路走到上游队列），一件是
**补一个一直只写在注释里的约束**（视频 / 音频参考素材的时长）。

## 为什么时长门禁值得单独钉住

替换链的语义是「完整待编辑原片」——用户提交一段视频，指望模型在**这段视频
的动作基础上**替换画面。传一个 3 秒片段进去，模型拿到的确实就是一个 3 秒
的参考片段，出来的片子静默地不对，而用户从头到尾没收到任何提示：任务状态
是「完成」，画面看着也像那么回事，只是内容跟他想的不一样。

百炼那条链一直有这道门禁（`drivers/bailian.py::_validated_inputs`），LibTV
这边只有一句注释。两条链的契约应当一致，所以门禁补在**受理期**（用资产库
已探测的时长，不必下载字节）与**物化期**（重新探测真实字节，兜住绕过受理期
的路径）两处。

## 为什么计费模式要按任务落库

这条值决定上游走哪条队列，而队列选择与「是否真的扣费」绑定。一条任务提交
出去之后重试时必须用当初那一条：账号权益可能变了（比如 0 积分权益到期），
重试时现读设置就会换队列，等于用不同的计费口径重下单。

所以断言落在**整条链**上：从 `paramSchema` 声明 → 受理期校验 → 落库 →
重建记录 → 归档工作区的 `run.json` → 桥接层据此选队列。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from library_support import insert_asset, png_bytes, upload_image

from core.exceptions import BadRequest
from infra.ai.base import Capability, ModelSpec

LIBTV = "libtv-wan3-replacement"


def _spec(param_schema: dict[str, Any]) -> ModelSpec:
    return ModelSpec(
        id="m",
        label="演示",
        provider="probe",
        capability=Capability.VIDEO,
        param_schema=param_schema,
    )


# ------------------------------------------------------------ 计费模式：参数层


def test_billing_mode_follows_the_model_schema() -> None:
    """声明了 `billingMode` 的模型：按 options 校验；没声明：拒绝非空值。"""
    from modules.task.params import resolve_billing_mode

    declaring = _spec(
        {"billingMode": {"type": "enum", "options": ["free", "credits"], "default": "free"}}
    )
    assert resolve_billing_mode(declaring, "credits") == "credits"
    assert resolve_billing_mode(declaring, "free") == "free"
    # 没传时取 schema 默认值（保守的那一侧：排免费队列不误扣积分）
    assert resolve_billing_mode(declaring, None) == "free"
    assert resolve_billing_mode(declaring, "  ") == "free"
    with pytest.raises(BadRequest, match="计费模式"):
        resolve_billing_mode(declaring, "premium")

    silent = _spec({})
    # 没声明的模型：不传就当免费，传了就明确拒绝
    assert resolve_billing_mode(silent, None) == "free"
    assert resolve_billing_mode(silent, "") == "free"
    with pytest.raises(BadRequest, match="没有声明"):
        resolve_billing_mode(silent, "credits")


def test_libtv_declares_billing_mode_in_its_param_schema() -> None:
    """LibTV 必须声明这个参数，否则积分加速那条路径在通用链路上不可达。"""
    from modules.model_driver.drivers.libtv import MODELS

    spec = next(item for item in MODELS if item.id == LIBTV)
    field = spec.param_schema.get("billingMode")
    assert field is not None, "LibTV 少声明了 billingMode"
    assert field.get("options") == ["free", "credits"]
    assert field.get("default") == "free"


# ------------------------------------------------------- 参考素材类型声明


def test_reference_kinds_are_declared_by_the_model() -> None:
    """参考素材能收哪几类，由模型自己声明。

    前端此前把选择器写死成「只选图片、最多 4 张」，于是视频替换模型
    （要「原片视频 + 参考图 + 音频」）在界面上根本选不到视频与音频 ——
    后端那条链明明支持，类型筛选却把用户挡在外面。
    """
    from modules.model_driver.drivers.agnes import MODELS as AGNES_MODELS
    from modules.model_driver.drivers.bailian import MODELS as BAILIAN_MODELS
    from modules.model_driver.drivers.libtv import MODELS as LIBTV_MODELS

    libtv = next(item for item in LIBTV_MODELS if item.id == LIBTV)
    assert libtv.reference_kinds == ("image", "video", "audio")
    assert libtv.max_references == 5

    bailian = next(item for item in BAILIAN_MODELS if item.replacement)
    assert bailian.reference_kinds == ("image", "video", "audio")

    # 生图模型只收图片（参考图是 base64 内联进请求体的）
    for spec in AGNES_MODELS:
        assert spec.reference_kinds == ("image",)


def test_reference_kinds_reach_the_model_catalog(client: TestClient, api: str) -> None:
    """`GET /models` 要带上这个声明，前端才有得渲染。"""
    response = client.get(f"{api}/models", params={"capability": "video"})
    assert response.status_code == 200, response.text
    items = response.json()["data"]["items"]
    libtv = next(item for item in items if item["id"] == LIBTV)
    assert sorted(libtv["referenceKinds"]) == ["audio", "image", "video"]


# ------------------------------------------------------- 计费模式：受理期与落库


def test_billing_mode_is_rejected_for_models_without_the_parameter(
    client: TestClient, api: str
) -> None:
    """未声明 `billingMode` 的模型传非空值 → 400，不是默默忽略。"""
    response = client.post(
        f"{api}/tasks",
        json={
            "prompt": "一个不需要计费模式的模型",
            "model": "seedance-2.5",
            "billingMode": "credits",
        },
    )
    assert response.status_code == 400, response.text
    assert "计费" in response.json()["message"]


def test_billing_mode_is_accepted_and_frozen_for_libtv(
    client: TestClient, api: str
) -> None:
    """声明了的模型：合法值被接受，非法值 400。"""
    ok = client.post(
        f"{api}/tasks",
        json={"prompt": "走积分加速", "model": LIBTV, "billingMode": "credits"},
    )
    assert ok.status_code == 201, ok.text

    bad = client.post(
        f"{api}/tasks",
        json={"prompt": "非法计费模式", "model": LIBTV, "billingMode": "premium"},
    )
    assert bad.status_code == 400, bad.text


def test_billing_mode_reaches_the_archive_run_json(client: TestClient, api: str) -> None:
    """计费模式要一路走到 `run.json` —— 桥接层靠它决定走哪条队列。"""
    from db.session import get_session_factory
    from modules.task.pipeline import VideoTaskStore

    response = client.post(
        f"{api}/tasks",
        json={"prompt": "积分加速的那一条", "model": LIBTV, "billingMode": "credits"},
    )
    assert response.status_code == 201, response.text
    task_id = response.json()["data"]["id"]

    async def load() -> dict[str, Any]:
        async with get_session_factory()() as session:
            record = await VideoTaskStore(session).load(task_id)
            assert record is not None
            return dict(record.params)

    params = asyncio.run(load())
    assert params["billingMode"] == "credits"

    # 落库那一列也要是对的值：重试是从这一行重建记录的
    async def read_column() -> str:
        from modules.task.models import VideoTask

        async with get_session_factory()() as session:
            row = await session.get(VideoTask, task_id)
            assert row is not None
            assert row.billing_mode == "credits"
            return row.billing_mode

    assert asyncio.run(read_column()) == "credits"


def test_missing_billing_mode_defaults_to_free(client: TestClient, api: str) -> None:
    """不传就用免费队列 —— 保守的那一侧，不会误扣积分。"""
    from db.session import get_session_factory
    from modules.task.models import VideoTask

    response = client.post(
        f"{api}/tasks", json={"prompt": "没指定计费模式", "model": LIBTV}
    )
    assert response.status_code == 201, response.text
    task_id = response.json()["data"]["id"]

    async def read_column() -> str:
        async with get_session_factory()() as session:
            row = await session.get(VideoTask, task_id)
            assert row is not None
            return row.billing_mode

    assert asyncio.run(read_column()) == "free"


# --------------------------------------------------------------- 素材时长门禁


def _insert_video_asset(
    owner_id: str, *, duration: float, name: str = "原片.mp4"
) -> str:
    """插一行带时长的视频资产（时长是受理期门禁的唯一判据）。"""
    return insert_asset(
        name=name,
        kind="video",
        mime_type="video/mp4",
        duration=duration,
        width=1080,
        height=1920,
        storage_key=f"tests/{uuid.uuid4().hex}.mp4",
        owner_id=owner_id,
        state="active",
    )


@pytest.mark.parametrize(
    ("duration", "message"),
    [
        (0.4, "时长不足"),
        (30.0, "时长超过"),
    ],
)
def test_reference_video_duration_is_rejected_at_intake(
    client: TestClient, api: str, duration: float, message: str
) -> None:
    """受理期就拒：用户不用等生成跑完才发现素材不对。"""
    from library_support import admin_headers

    headers = admin_headers(client)
    me = client.get(f"{api}/identity/me", headers=headers).json()["data"]
    asset_id = _insert_video_asset(me["id"], duration=duration)

    response = client.post(
        f"{api}/tasks",
        headers=headers,
        json={
            "prompt": "拿一段视频当原片",
            "model": LIBTV,
            "referenceLibraryIds": [asset_id],
        },
    )
    assert response.status_code == 400, response.text
    assert message in response.json()["message"]


def test_reference_video_total_duration_is_rejected(client: TestClient, api: str) -> None:
    """两段各 9 秒的单看都合法，凑起来 18 秒就超了 —— 逐段检查查不出来。"""
    from library_support import admin_headers

    headers = admin_headers(client)
    me = client.get(f"{api}/identity/me", headers=headers).json()["data"]
    first = _insert_video_asset(me["id"], duration=9.0, name="甲.mp4")
    second = _insert_video_asset(me["id"], duration=9.0, name="乙.mp4")

    response = client.post(
        f"{api}/tasks",
        headers=headers,
        json={
            "prompt": "两段加起来超了",
            "model": LIBTV,
            "referenceLibraryIds": [first, second],
        },
    )
    assert response.status_code == 400, response.text
    assert "总时长" in response.json()["message"]


def test_reference_video_within_limits_is_accepted(client: TestClient, api: str) -> None:
    """合法时长不该被误伤。"""
    from library_support import admin_headers

    headers = admin_headers(client)
    me = client.get(f"{api}/identity/me", headers=headers).json()["data"]
    asset_id = _insert_video_asset(me["id"], duration=8.0)

    response = client.post(
        f"{api}/tasks",
        headers=headers,
        json={
            "prompt": "一段八秒的原片",
            "model": LIBTV,
            "referenceLibraryIds": [asset_id],
        },
    )
    assert response.status_code == 201, response.text


def test_image_references_are_not_affected_by_the_duration_gate(
    client: TestClient, api: str
) -> None:
    """图片没有时长概念 —— 门禁只管视频与音频。"""
    asset = upload_image(client, api, {}, name="只有图片.png")
    response = client.post(
        f"{api}/tasks",
        json={
            "prompt": "图生视频",
            "model": LIBTV,
            "referenceLibraryIds": [asset["id"]],
        },
    )
    assert response.status_code == 201, response.text


# ------------------------------------------------------- 物化期：真实字节兜底


def test_materialize_rejects_an_overlong_video_reference(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """物化期重新探测真实字节：受理期没拦住的那条路在这里兜住。

    用 `imageio_ffmpeg` 现造一段 20 秒的 mp4，写进存储后物化 —— 受理期拿不到
    真实时长（历史行 / 直接调驱动），这里必须自己探测并拒绝。
    """
    import subprocess

    import imageio_ffmpeg

    from core.config import settings
    from infra import get_storage
    from infra.ai.base import GenerationRequest
    from infra.archive import reset_archive_storage
    from infra.archive.local import get_archive_settings
    from infra.archive.runs import runs_root
    from modules.model_driver.generic_inputs import materialize

    root = tmp_path / "archive"
    root.mkdir()
    monkeypatch.setattr(get_archive_settings(), "root", str(root))
    monkeypatch.setattr(settings, "archive_driver", "local")
    monkeypatch.setattr(settings, "task_runs_root", "")
    reset_archive_storage()
    try:
        source = tmp_path / "long.mp4"
        subprocess.run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(),
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=64x64:d=20",
                "-pix_fmt",
                "yuv420p",
                str(source),
            ],
            check=True,
            timeout=120,
        )
        payload = source.read_bytes()
        key = f"tests/long-{uuid.uuid4().hex}.mp4"
        asyncio.run(get_storage().put(key, payload, "video/mp4"))

        run_id = uuid.uuid4().hex
        refs = [{"id": uuid.uuid4().hex, "key": key, "kind": "video", "name": "long.mp4"}]
        request = GenerationRequest(
            capability=Capability.VIDEO,
            model=LIBTV,
            prompt="一段二十秒的原片",
            params={
                "durationSec": 5,
                "aspectRatio": "9:16",
                "referenceKeys": refs,
            },
            request_id=run_id,
        )
        with pytest.raises(BadRequest, match="时长超过"):
            asyncio.run(materialize(request))
        # 没写成半成品档案：修好素材后还能重新物化
        assert not (runs_root() / run_id / "run.json").is_file()
    finally:
        reset_archive_storage()


def test_png_bytes_helper_still_works() -> None:
    """守一下测试辅助：门禁不能把图片素材也一起拦了。"""
    assert png_bytes("red")[:4] == b"\x89PNG"
