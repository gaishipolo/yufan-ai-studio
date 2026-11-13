"""通用生成入口 → 替换链归档工作区：物化器的正向与退化守卫。

三个替换模型已经进了 `GET /models`，用户在生成工作台选中它们时不会再有
`archiveRunId`。两个驱动都只认模板库那套归档档案，所以「通用请求 → 档案」
这一步一旦写歪，症状不是 400，而是：

- 百炼在异步提交里报「素材路径缺失」，用户只看到任务失败；
- LibTV 的 `upload` 阶段读不到 `prompt-spec.references` / `prompt-compilation.json`，
  或者 `canvas-run.mjs::update()` 因为少一个目录直接 ENOENT。

这里钉住档案形状、幂等、受理期之外的字节丢失，以及 `/tasks` 到
`referenceKeys` 的冻结链。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from library_support import png_bytes, upload_image

from core.config import settings
from core.exceptions import BadRequest
from infra import get_storage
from infra.ai.base import Capability, GenerationRequest
from infra.archive import get_archive_storage, reset_archive_storage, run_dir
from infra.archive.local import get_archive_settings
from infra.archive.runs import runs_root
from modules.model_driver.generic_inputs import materialize

PROMPT = "把第一张图放进第二张图的场景"


@pytest.fixture
def archive_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "archive"
    root.mkdir()
    monkeypatch.setattr(get_archive_settings(), "root", str(root))
    monkeypatch.setattr(settings, "archive_driver", "local")
    monkeypatch.setattr(settings, "replacement_runs_root", "")
    reset_archive_storage()
    yield root
    reset_archive_storage()


def _request(run_id: str, refs: list[dict[str, str]], **params: Any) -> GenerationRequest:
    return GenerationRequest(
        capability=Capability.VIDEO,
        model="bailian-wan3-replacement",
        prompt=PROMPT,
        params={"durationSec": 5, "aspectRatio": "9:16", "referenceKeys": refs, **params},
        request_id=run_id,
    )


def _read(folder: Path, name: str) -> Any:
    return json.loads((folder / name).read_text(encoding="utf-8"))


def _put_png() -> tuple[str, bytes]:
    key = f"tests/generic-{uuid.uuid4().hex}.png"
    payload = png_bytes("orange")
    asyncio.run(get_storage().put(key, payload, "image/png"))
    return key, payload


def test_materialize_writes_the_shape_both_drivers_read(archive_root: Path) -> None:
    from modules.model_driver.libtv.state import LibTVState

    key, payload = _put_png()
    asset_id = uuid.uuid4().hex
    run_id = uuid.uuid4().hex
    refs = [{"id": asset_id, "key": key, "kind": "image", "name": "ref.png"}]

    asyncio.run(materialize(_request(run_id, refs)))

    folder = run_dir(get_archive_storage(), run_id)
    assert (folder / "inputs/prompt.txt").read_text(encoding="utf-8") == PROMPT
    # 五个目录一个都不能少：canvas-run.update() 会逐个 readdir 算校验和。
    for name in ("inputs", "outputs", "records", "logs", "review", "code"):
        assert (folder / name).is_dir(), name

    spec = _read(folder, "inputs/prompt-spec.json")
    expected_ref = {"id": asset_id, "type": "image", "file": "inputs/0.png"}
    assert spec["duration"] == 5
    assert spec["aspect_ratio"] == "9:16"
    assert spec["references"] == [expected_ref]

    entries = _read(folder, "inputs/input-map.json")
    assert entries[0]["type"] == "image"
    assert entries[0]["sha256"] == hashlib.sha256(payload).hexdigest()
    assert entries[0]["metadata"]["width"] > 0
    assert entries[0]["fps"] == 0, "帧率不能写死，交给驱动现探"

    compilation = _read(folder, "records/prompt-compilation.json")
    assert compilation["prompt"] == PROMPT
    assert compilation["bindings"] == [expected_ref]
    assert "inputs/0.png" in _read(folder, "records/media-inputs.json")

    run = _read(folder, "run.json")
    assert run["status"] == "uploaded"
    assert run["file_checksums"]["inputs/0.png"] == entries[0]["sha256"]
    state = LibTVState.model_validate(run)
    assert state.preparation_ready is True
    assert state.framework_status == "queued"


def test_materialize_is_idempotent_and_keeps_upstream_receipt(archive_root: Path) -> None:
    """重试会再进一次 submit；重写 run.json 会把上游 task_id 抹掉 → 二次提交。"""
    key, _ = _put_png()
    run_id = uuid.uuid4().hex
    refs = [{"id": uuid.uuid4().hex, "key": key, "kind": "image", "name": "ref.png"}]

    asyncio.run(materialize(_request(run_id, refs)))
    folder = runs_root() / run_id
    run_file = folder / "run.json"
    run = _read(folder, "run.json")
    run["task_id"] = "upstream-task-42"
    run_file.write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")

    asyncio.run(
        materialize(
            GenerationRequest(
                capability=Capability.VIDEO,
                model="bailian-wan3-replacement",
                prompt="换一段提示词也不该重写档案",
                params={"durationSec": 5, "aspectRatio": "9:16", "referenceKeys": []},
                request_id=run_id,
            )
        )
    )

    assert _read(folder, "run.json")["task_id"] == "upstream-task-42"
    assert (folder / "inputs/prompt.txt").read_text(encoding="utf-8") == PROMPT


def test_missing_blob_is_a_bad_request_not_a_server_error(archive_root: Path) -> None:
    run_id = uuid.uuid4().hex
    refs = [
        {
            "id": uuid.uuid4().hex,
            "key": f"tests/gone-{uuid.uuid4().hex}.png",
            "kind": "image",
            "name": "gone.png",
        }
    ]
    with pytest.raises(BadRequest, match="字节已丢失"):
        asyncio.run(materialize(_request(run_id, refs)))
    # 没写 run.json：修好素材后重试还能重新物化，不会撞上「半成品档案」。
    assert not (runs_root() / run_id / "run.json").is_file()


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"durationSec": 99}, "输出时长"),
        ({"aspectRatio": "3:2"}, "不支持的画幅"),
    ],
)
def test_bad_params_are_rejected_before_any_file_is_written(
    archive_root: Path, params: dict[str, Any], message: str
) -> None:
    run_id = uuid.uuid4().hex
    with pytest.raises(BadRequest, match=message):
        asyncio.run(materialize(_request(run_id, [], **params)))
    assert not (runs_root() / run_id).exists()


def test_unsupported_reference_kind_is_rejected(archive_root: Path) -> None:
    run_id = uuid.uuid4().hex
    refs = [{"id": "a", "key": "k", "kind": "pack", "name": "bundle"}]
    with pytest.raises(BadRequest, match="不支持的参考素材类型"):
        asyncio.run(materialize(_request(run_id, refs)))


# ---------------------------------------------------------------- /tasks 冻结链


def test_task_pipeline_freezes_reference_keys(client: TestClient, api: str) -> None:
    """`POST /tasks` 的资产库 id 要在重建记录时变成驱动认得的存储键。"""
    asset = upload_image(client, api, {}, name="generic-ref.png")
    response = client.post(
        f"{api}/tasks",
        json={
            "prompt": "带参考素材的通用任务",
            "model": "libtv-wan3-replacement",
            "referenceLibraryIds": [asset["id"]],
        },
    )
    assert response.status_code == 201, response.text
    task_id = response.json()["data"]["id"]

    async def load() -> dict[str, Any]:
        from db.session import get_session_factory
        from modules.task.pipeline import VideoTaskStore

        async with get_session_factory()() as session:
            record = await VideoTaskStore(session).load(task_id)
            assert record is not None
            return dict(record.params)

    params = asyncio.run(load())
    keys = params["referenceKeys"]
    assert len(keys) == 1
    assert keys[0]["id"] == asset["id"]
    assert keys[0]["kind"] == "image"
    assert keys[0]["key"].endswith(".png")


def test_task_pipeline_refuses_a_lost_reference() -> None:
    """历史脏行丢了资产时整条拒绝，不能悄悄少一张图去生成。"""

    async def run() -> None:
        from db.session import get_session_factory
        from modules.task.models import VideoTask
        from modules.task.pipeline import VideoTaskStore

        row_id = uuid.uuid4().hex
        lost_id = uuid.uuid4().hex
        async with get_session_factory()() as session:
            session.add(
                VideoTask(
                    id=row_id,
                    title="丢过参考素材的历史任务",
                    prompt="第二张图里的角色",
                    model="libtv-wan3-replacement",
                    author_id="u_admin",
                    author_name="admin",
                    reference_library_ids=[lost_id],
                )
            )
            await session.flush()
            with pytest.raises(BadRequest, match="参考素材已不可用"):
                await VideoTaskStore(session).load(row_id)
            await session.rollback()

    asyncio.run(run())
