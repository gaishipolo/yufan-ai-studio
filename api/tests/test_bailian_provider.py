import hashlib
import json

import httpx
import pytest

from core.exceptions import BadRequest, UpstreamError
from infra.ai import bailian
from infra.ai.base import Capability, GenerationRequest, GenerationState


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    (tmp_path / "inputs").mkdir()
    (tmp_path / "records").mkdir()
    (tmp_path / "inputs/prompt.txt").write_text(
        "将{{Mixed 1}}的人替换为Mixed 2，参考Mixed 3", encoding="utf-8"
    )
    (tmp_path / "inputs/prompt-spec.json").write_text(
        json.dumps({"duration": 5, "aspect_ratio": "9:16"}), encoding="utf-8"
    )
    entries = []
    for i, kind in enumerate(("video", "image", "image"), 1):
        file = f"inputs/{i}.mp4" if kind == "video" else f"inputs/{i}.png"
        content = f"media-{i}".encode()
        (tmp_path / file).write_bytes(content)
        entries.append({"file": file, "type": kind, "sha256": hashlib.sha256(content).hexdigest()})
    (tmp_path / "inputs/input-map.json").write_text(json.dumps(entries), encoding="utf-8")
    (tmp_path / "records/media-inputs.json").write_text(
        json.dumps({"inputs/1.mp4": {"duration": 8, "width": 720, "height": 1280,
                                      "fps": 30},
                    "inputs/2.png": {"width": 720, "height": 1280},
                    "inputs/3.png": {"width": 720, "height": 1280}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(bailian, "run_path", lambda _: tmp_path)
    return tmp_path


def provider(handler):
    return bailian.BailianProvider(
        settings=bailian.BailianSettings(api_key="test-secret", _env_file=None),
        transport=httpx.MockTransport(handler),
    )


def upload_handler(request):
    if request.url.path.endswith("/uploads"):
        return httpx.Response(
            200,
            json={
                "data": {
                    "upload_host": "https://upload.example.com",
                    "upload_dir": "dashscope-instant/test",
                    "oss_access_key_id": "signed-key",
                    "signature": "signed-secret",
                    "policy": "policy",
                    "x_oss_object_acl": "private",
                    "x_oss_forbid_overwrite": "true",
                }
            },
        )
    if request.url.host == "upload.example.com":
        assert "Authorization" not in request.headers
        return httpx.Response(200)
    raise AssertionError(str(request.url))


async def test_prepares_model_bound_media_and_converts_mixed_references(workspace):
    seen = []

    def handle(request):
        seen.append(request)
        return upload_handler(request)

    payload = await provider(handle).prepare_inputs("run", model=bailian.PRIME_MODEL_ID)
    assert payload["parameters"]["resolution"] == "720P"
    assert payload["model"] == "wan3.0-video-prime"
    assert payload["input"]["prompt"] == "将视频1的人替换为图1，参考图2"
    assert [m["type"] for m in payload["input"]["media"]] == [
        "reference_video",
        "reference_image",
        "reference_image",
    ]
    assert all(
        r.url.params["model"] == "wan3.0-video-prime"
        for r in seen
        if r.url.path.endswith("/uploads")
    )
    saved = (workspace / "records/bailian-request.json").read_text(encoding="utf-8")
    assert "test-secret" not in saved and "signed-secret" not in saved


async def test_text_only_request_omits_media_field(workspace):
    (workspace / "inputs/prompt.txt").write_text("一只猫在窗台打哈欠", encoding="utf-8")
    (workspace / "inputs/input-map.json").write_text("[]", encoding="utf-8")
    (workspace / "records/media-inputs.json").write_text("{}", encoding="utf-8")

    payload = await provider(upload_handler).prepare_inputs("run")

    assert payload["input"]["prompt"] == "一只猫在窗台打哈欠"
    assert "media" not in payload["input"]


@pytest.mark.parametrize(
    "change, message",
    [
        ("prompt", "20000"),
        ("duration", "30"),
        ("fps", "16"),
        ("checksum", "校验"),
        ("path", "路径"),
        ("video_duration", "15"),
    ],
)
async def test_rejects_invalid_input_before_upload(workspace, change, message):
    if change == "prompt":
        (workspace / "inputs/prompt.txt").write_text("字" * 20001, encoding="utf-8")
    elif change == "duration":
        (workspace / "inputs/prompt-spec.json").write_text('{"duration": 25}')
    elif change in ("fps", "video_duration"):
        (workspace / "records/media-inputs.json").write_text(
            json.dumps(
                {"inputs/1.mp4": {"duration": 16 if change == "video_duration" else 8,
                                   "fps": 12, "width": 720, "height": 1280}}
            )
        )
    elif change == "checksum":
        (workspace / "inputs/1.mp4").write_bytes(b"changed")
    else:
        entries = json.loads((workspace / "inputs/input-map.json").read_text())
        entries[0]["file"] = "../outside.mp4"
        (workspace / "inputs/input-map.json").write_text(json.dumps(entries))
    with pytest.raises(BadRequest, match=message):
        await provider(lambda _: pytest.fail("must validate before upload")).prepare_inputs("run")


async def test_submit_persists_receipt_and_retry_reuses_task(workspace):
    calls = []

    def handle(request):
        if "video-synthesis" in request.url.path:
            calls.append(request)
            assert request.headers["X-DashScope-Async"] == "enable"
            assert request.headers["X-DashScope-OssResourceResolve"] == "enable"
            return httpx.Response(200, json={"output": {"task_id": "task-123"}})
        return upload_handler(request)

    p = provider(handle)
    req = GenerationRequest(
        Capability.VIDEO, bailian.MODEL_ID, "", params={"archiveRunId": "run"}, request_id="run"
    )
    assert await p.submit(req) == "run:task-123"
    assert await p.submit(req) == "run:task-123"
    assert len(calls) == 1
    receipt = json.loads((workspace / "records/bailian-submission.json").read_text())
    assert receipt["task_id"] == "task-123"


async def test_ambiguous_submission_never_automatically_resubmits(workspace):
    calls = []

    def handle(request):
        if "video-synthesis" in request.url.path:
            calls.append(request)
            raise httpx.ReadTimeout("secret signed url", request=request)
        return upload_handler(request)

    p = provider(handle)
    req = GenerationRequest(
        Capability.VIDEO, bailian.MODEL_ID, "", params={"archiveRunId": "run"}, request_id="run"
    )
    for _ in range(2):
        with pytest.raises(UpstreamError, match="禁止自动重提") as error:
            await p.submit(req)
        assert "secret" not in str(error.value)
    assert len(calls) == 1


async def test_poll_downloads_before_reporting_success(workspace):
    def handle(request):
        if request.url.path.endswith("/tasks/task-123"):
            return httpx.Response(
                200,
                json={
                    "output": {
                        "task_status": "SUCCEEDED",
                        "video_url": "https://result.example/video.mp4?secret=x",
                    }
                },
            )
        assert "Authorization" not in request.headers
        return httpx.Response(200, content=b"video-result")

    progress = await provider(handle).poll("run:task-123")
    assert progress.state == GenerationState.SUCCEEDED
    assert progress.output_url == "/api/v1/templates/replacement-runs/run/output"
    assert (workspace / "outputs/generated.mp4").read_bytes() == b"video-result"


@pytest.mark.parametrize(
    "status, state",
    [("PENDING", "queued"), ("RUNNING", "running"), ("FAILED", "failed"), ("CANCELED", "failed")],
)
async def test_poll_normalizes_statuses(workspace, status, state):
    p = provider(
        lambda _: httpx.Response(
            200,
            json={
                "output": {"task_status": status, "message": "test-secret https://signed.example"}
            },
        )
    )
    progress = await p.poll("run:task")
    assert progress.state == state
    assert "test-secret" not in (progress.error or "")


async def test_submit_revalidates_prepared_payload_limits_before_paid_request(workspace):
    (workspace / "records/bailian-request.json").write_text(
        json.dumps(
            {
                "model": "wan3.0-video",
                "input": {"prompt": "x" * 20001, "media": []},
                "parameters": {"duration": 5},
            }
        )
    )
    req = GenerationRequest(
        Capability.VIDEO, bailian.MODEL_ID, "", params={"archiveRunId": "run"}, request_id="run"
    )
    with pytest.raises(BadRequest, match="20000"):
        await provider(lambda _: pytest.fail("must validate before paid POST")).submit(req)
    assert not (workspace / "records/bailian-submission.json").exists()


async def test_poll_bounded_download_removes_partial_file_and_keeps_task(workspace, monkeypatch):
    monkeypatch.setattr(bailian, "_MAX_DOWNLOAD", 3)

    def handle(request):
        if "/tasks/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "output": {
                        "task_status": "SUCCEEDED",
                        "video_url": "https://result.example/file?secret=x",
                    }
                },
            )
        return httpx.Response(200, content=b"too-large")

    with pytest.raises(UpstreamError, match="继续查询") as error:
        await provider(handle).poll("run:task")
    assert "secret" not in str(error.value)
    assert not list((workspace / "outputs").iterdir())


def test_translate_prompt_is_idempotent_and_rejects_unknown_references():
    entries = [{"type": "image"}, {"type": "video"}, {"type": "audio"}, {"type": "image"}]
    actual = bailian.translate_prompt("{{Mixed 2}}参照Mixed 1和Mixed 4，声音Mixed 3", entries)
    assert actual == "视频1参照图1和图2，声音音频1"
    assert bailian.translate_prompt(actual, entries) == actual
    with pytest.raises(BadRequest, match="不存在"):
        bailian.translate_prompt("Mixed 5", entries)


@pytest.mark.parametrize("kind,meta,message", [
    ("image", {"width": 100, "height": 720}, "尺寸"),
    ("image", {"width": 240, "height": 8000}, "8:1"),
    ("video", {"width": 0, "height": 0, "duration": 8, "fps": 30}, "尺寸"),
    ("video", {"width": 720, "height": 1280, "duration": 8}, "帧率"),
])
async def test_rejects_unverified_media_constraints(workspace, monkeypatch, kind, meta, message):
    monkeypatch.setattr(bailian, "_fps", lambda _: None)
    mapping = json.loads((workspace / "records/media-inputs.json").read_text())
    mapping["inputs/2.png" if kind == "image" else "inputs/1.mp4"] = meta
    (workspace / "records/media-inputs.json").write_text(json.dumps(mapping))
    with pytest.raises(BadRequest, match=message):
        await provider(lambda _: pytest.fail("must reject before uploading")).prepare_inputs("run")


async def test_definite_rejection_can_retry_without_unknown_billing(workspace):
    calls = []

    def handle(request):
        if "video-synthesis" in request.url.path:
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(400, json={"code": "InvalidParameter"})
            return httpx.Response(200, json={"output": {"task_id": "task-ok"}})
        return upload_handler(request)

    p = provider(handle)
    req = GenerationRequest(Capability.VIDEO, bailian.MODEL_ID, "",
                            params={"archiveRunId": "run"}, request_id="run")
    with pytest.raises(BadRequest, match="拒绝"):
        await p.submit(req)
    assert await p.submit(req) == "run:task-ok"
    assert len(calls) == 2
