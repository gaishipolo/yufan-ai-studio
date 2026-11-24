import hashlib
import json

import httpx
import pytest
from storage_stub import DirStorage

from core.exceptions import BadRequest, NotFound, UpstreamError
from db.session import get_session_factory
from infra.ai.base import Capability, GenerationRequest, GenerationState
from modules.model_driver.drivers import bailian
from modules.model_driver.models import SubmissionReceipt


@pytest.fixture
async def workspace(tmp_path, monkeypatch):
    (tmp_path / "inputs").mkdir()
    (tmp_path / "records").mkdir()
    (tmp_path / "inputs/prompt.txt").write_text(
        "将{{Mixed 1}}的人替换为{{Mixed 2}}，参考{{Mixed 3}}", encoding="utf-8"
    )
    (tmp_path / "inputs/prompt-spec.json").write_text(
        json.dumps({"duration": 5, "aspect_ratio": "9:16"}), encoding="utf-8"
    )
    entries = []
    blobs = tmp_path / "blobs"
    for i, kind in enumerate(("video", "image", "image"), 1):
        file = f"inputs/{i}.mp4" if kind == "video" else f"inputs/{i}.png"
        content = f"media-{i}".encode()
        (tmp_path / file).write_bytes(content)
        key = f"tests/bailian/media-{i}"
        (blobs / key).parent.mkdir(parents=True, exist_ok=True)
        (blobs / key).write_bytes(content)
        entries.append(
            {
                "file": file,
                "type": kind,
                # 归档条目要写明素材在**我们存储**里的对象键：OSS 通道据此从存储
                # 取字节上传，不靠「文件名对得上」去猜。
                "storage_key": key,
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    (tmp_path / "inputs/input-map.json").write_text(json.dumps(entries), encoding="utf-8")
    (tmp_path / "records/media-inputs.json").write_text(
        json.dumps({"inputs/1.mp4": {"duration": 8, "width": 720, "height": 1280,
                                      "fps": 30},
                    "inputs/2.png": {"width": 720, "height": 1280},
                    "inputs/3.png": {"width": 720, "height": 1280}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(bailian, "run_path", lambda _: tmp_path)
    # 素材字节从**我们的存储**读（OSS 通道的要求）→ 给测试一份存储。
    monkeypatch.setattr("infra.get_storage", lambda: DirStorage(blobs))

    # 回执在 DB（全局测试库），不像文件那样随 tmp_path 隔离 —— 每条测试
    # 开跑前清表，否则上一条的回执会污染这一条。
    from sqlalchemy import delete

    async with get_session_factory()() as session:
        await session.execute(delete(SubmissionReceipt))
        await session.commit()
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
    # 回执在 DB：工作区删掉也能恢复（2026-11-23 落库）
    from sqlalchemy import select

    async with get_session_factory()() as session:
        result = await session.execute(
            select(SubmissionReceipt).where(
                SubmissionReceipt.provider == "bailian",
                SubmissionReceipt.run_id == "run",
            )
        )
        receipt = result.scalar_one()
    assert receipt.task_id == "task-123"
    assert receipt.status == "submitted"


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
    # 校验失败不能抢占提交权 —— 否则后续合法重试会被 submitting 回执锁死
    from sqlalchemy import func, select

    async with get_session_factory()() as session:
        count = await session.scalar(
            select(func.count()).select_from(SubmissionReceipt).where(
                SubmissionReceipt.provider == "bailian",
                SubmissionReceipt.run_id == "run",
            )
        )
    assert count == 0


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
    actual = bailian.translate_prompt(
        "{{Mixed 2}}参照{{Mixed 1}}和{{Mixed 4}}，声音{{Mixed 3}}", entries
    )
    assert actual == "视频1参照图1和图2，声音音频1"
    assert bailian.translate_prompt(actual, entries) == actual
    with pytest.raises(BadRequest, match="不存在"):
        bailian.translate_prompt("{{Mixed 5}}", entries)


def test_bare_mixed_is_plain_text_not_a_reference():
    """裸 `Mixed N` 是普通文本：不翻译、越界也不报错。

    同一个裸写提示词，以前"恰好有编号时被改写、越界时突然 400"两种坏结局，
    都跟用户输入无关。现在一律原样透传。
    """
    entries = [{"type": "image"}, {"type": "video"}]
    assert bailian.translate_prompt("这个是 Mixed 1 型号，参照 Mixed 9", entries) == (
        "这个是 Mixed 1 型号，参照 Mixed 9"
    )
    # 正式写法在同一段里照常翻译，两者互不影响。
    assert bailian.translate_prompt("参照 {{Mixed 2}} 与 Mixed 9", entries) == (
        "参照 视频1 与 Mixed 9"
    )


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


# -------------------------------------------------------------- 标准符合性
def test_driver_passes_contract_validation() -> None:
    from modules.model_driver.spec import validate_driver

    assert validate_driver(bailian.BailianProvider()) == []


def test_declared_models_belong_to_this_provider() -> None:
    ids = [model.id for model in bailian.BailianProvider.models]
    assert ids == [bailian.MODEL_ID, bailian.PRIME_MODEL_ID]
    for model in bailian.BailianProvider.models:
        assert model.provider == "bailian"
        assert model.capability is Capability.VIDEO


def test_driver_is_auto_loaded_without_env_listing(client) -> None:  # noqa: ANN001
    """核心诉求：不进 `AI_PROVIDERS` 也要被发现、校验、接进路由。

    替换链是按 `bailian-wan3-*` 这个模型 id 找供应商的；驱动没被自动载入时，
    症状不是启动失败，而是渠道在选择列表里静默消失。
    """
    from core.config import settings
    from infra import get_model_router
    from modules.model_driver.registry import driver_summaries, load_errors

    assert not hasattr(settings, "ai_provider_list"), "白名单配置已整体下线"
    assert not load_errors(), load_errors()

    infos = {item.name: item for item in driver_summaries()}
    assert infos["bailian"].loaded is True
    assert infos["bailian"].model_count == 2

    assert get_model_router().provider_for(bailian.MODEL_ID).name == "bailian"


def test_channel_reports_missing_key_before_any_upload() -> None:
    """没配密钥要在**选渠道时**就说清楚，不能等用户点了「开始」才在上传阶段炸。"""
    cold = bailian.BailianProvider(
        settings=bailian.BailianSettings(api_key="", _env_file=None)
    )
    assert "密钥" in cold.unavailable_reason()

    ready = bailian.BailianProvider(
        settings=bailian.BailianSettings(api_key="sk-x", _env_file=None)
    )
    assert ready.unavailable_reason() == ""


def test_prompt_translation_is_shared_with_the_replacement_chain() -> None:
    """档案（`inputs/prompt.txt`）与请求体必须用同一套重编号规则。

    两份实现迟早编出「档案里写视频1、实际发出去是视频2」——出图正常、只是
    内容不对，线上极难发现。所以这条钉的是「同一个函数对象」。
    """
    from core import prompt_refs

    assert bailian.translate_prompt is prompt_refs.translate_prompt


def test_submit_budget_covers_the_upload_fallback() -> None:
    """提交里可能兜底跑一次素材上传（单文件上限 100MB）。

    预算停在框架默认的 120 秒时，上传没完就被取消 → 任务变成「有提交意图、
    没有任务号」= 结果未知、禁止重提，而上传本身并不产生费用。
    """
    assert bailian.BailianProvider.submit_timeout_sec >= 300


async def test_generic_entry_materializes_workspace_before_locating_it(monkeypatch):
    """通用入口（params 里没有 `archiveRunId`）必须**先建档案、再取目录**。

    真实故障（2026-11-16，任务 0454d72e）：`submit` 先 `run_path` 后 `materialize`，
    而 `run_path` 读的指针与 `run.json` 恰恰由 `materialize` 创建 —— 于是任务受理
    0.1 秒后以 `NotFound: 调用档案不存在` 失败，一次上游请求都没发。
    这里用调用顺序钉住它，防止再被调回去。
    """
    calls: list[str] = []

    async def fake_materialize(request):
        calls.append("materialize")

    def fake_run_path(run_id: str):
        calls.append("run_path")
        # 真实现：指针/run.json 还没建时就是这样抛的
        raise NotFound("调用档案不存在")

    monkeypatch.setattr(bailian, "materialize", fake_materialize)
    monkeypatch.setattr(bailian, "run_path", fake_run_path)

    req = GenerationRequest(
        Capability.VIDEO, bailian.MODEL_ID, "", params={}, request_id="a" * 32
    )
    with pytest.raises(NotFound):
        await provider(lambda _: pytest.fail("不该打到上游")).submit(req)
    assert calls[:2] == ["materialize", "run_path"]


async def test_archive_run_id_skips_materialize(workspace, monkeypatch):
    """替换链已经把档案建好（带 `archiveRunId`）：不许再物化一次。

    `materialize` 虽然幂等，但真跑起来会重新下载参考素材；替换链每条任务都
    多下一遍几百 MB 的片子，是白花的带宽与延迟。
    """
    materialized: list[object] = []

    async def fake_materialize(request):
        materialized.append(request)

    monkeypatch.setattr(bailian, "materialize", fake_materialize)

    def handle(request):
        if "video-synthesis" in request.url.path:
            return httpx.Response(200, json={"output": {"task_id": "task-9"}})
        return upload_handler(request)

    req = GenerationRequest(
        Capability.VIDEO,
        bailian.MODEL_ID,
        "",
        params={"archiveRunId": "run"},
        request_id="run",
    )
    assert await provider(handle).submit(req) == "run:task-9"
    assert materialized == []


async def test_rejects_media_without_a_storage_key(workspace):
    """条目没写存储键 → 明确报错，不去猜哪个对象是自己的素材。"""
    entries = json.loads((workspace / "inputs/input-map.json").read_text())
    entries[0].pop("storage_key")
    (workspace / "inputs/input-map.json").write_text(json.dumps(entries), encoding="utf-8")

    with pytest.raises(BadRequest, match="存储键"):
        await provider(lambda _: pytest.fail("must fail before upload")).prepare_inputs("run")


async def test_rejects_bytes_that_differ_from_the_archive_digest(workspace):
    """存储里的字节与归档记的 sha256 不符 → 拒绝上传。

    这条是「字节从存储读」带来的新防线：物化之后对象被覆盖过，上游拿到的
    就会是一份和归档对不上的素材。
    """
    key = "tests/bailian/media-1"
    (workspace / "blobs" / key).write_bytes(b"replaced-after-archive")

    with pytest.raises(BadRequest, match="校验失败"):
        await provider(lambda _: pytest.fail("must fail before upload")).prepare_inputs("run")


async def test_reports_a_missing_object_in_our_storage(workspace):
    """存储里的对象没了 → 上游错误（不是我们代码写错了）。"""
    (workspace / "blobs/tests/bailian/media-1").unlink()

    with pytest.raises(UpstreamError, match="上传失败"):
        await provider(lambda _: pytest.fail("must fail before upload")).prepare_inputs("run")


async def test_uploads_the_bytes_from_our_storage(workspace):
    """真正传上去的字节来自存储，不是工作区那份快照。"""
    sent: list[bytes] = []

    def handle(request):
        if request.url.host == "upload.example.com":
            sent.append(request.content)
        return upload_handler(request)

    await provider(handle).prepare_inputs("run")

    assert sent, "素材必须真的上传了"
    assert all(b"media-" in body for body in sent)
