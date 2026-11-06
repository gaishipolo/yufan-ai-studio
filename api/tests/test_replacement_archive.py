"""不访问 LibTV、不碰 Z 盘：验证归档字节、权限、幂等和未知提交门禁。"""

from __future__ import annotations

import io
import json
import os
import subprocess
import uuid
from pathlib import Path

import imageio_ffmpeg
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from tests_archive_helpers import archive

from core.config import settings
from core.context import CurrentUser
from core.exceptions import BadRequest, Conflict, UpstreamError
from infra.ai.libtv import get_libtv_settings
from infra.archive.local import get_archive_settings
from modules.template.replacement_schema import PrepareReplacement
from modules.template.replacement_service import BRIDGE, prepare
from modules.template.replacement_worker import schedule


@pytest.fixture
def archive_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))
    return tmp_path


def make_image(path: Path) -> Path:
    # 每个测试用不同像素：摘要相同会被去重成上一条记录，而那条记录的文件在
    # 上一个测试的临时目录里（会话级数据库跨测试共享）
    Image.new("RGB", (32, 48), tuple(os.urandom(3))).save(path, format="PNG")
    return path


@pytest.fixture
def materials(client: TestClient, archive_root: Path) -> tuple[dict, dict]:
    """经真实上传接口登记原片与人物参考图。

    以前这里直接调 `ArchiveAssetRepository().register()`：那是「JSON 即索引」
    时代的同步写法，索引搬进 `assets` 表后它已经没有对应的真实路径了。
    """
    video = archive_root / "fixture.mp4"
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x{os.urandom(3).hex()}:s=64x64:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(video),
        ],
        check=True,
        capture_output=True,
    )
    image = make_image(archive_root / "fixture.png")
    return (
        archive.upload(client, "source.mp4", "source", video.read_bytes()),
        archive.upload(client, "identity.png", "character", image.read_bytes()),
    )


def test_upload_and_owner_scoping(client: TestClient, archive_root: Path) -> None:
    from api.deps import get_actor

    image = io.BytesIO()
    Image.new("RGB", (16, 16), tuple(os.urandom(3))).save(image, format="PNG")
    item = archive.upload(client, "参考.png", "character", image.getvalue())
    assert (archive_root / item["path"]).read_bytes() == image.getvalue()
    assert client.get(item["url"]).content == image.getvalue()

    # 换一个没有这条素材的操作者：列表为空、取文件 404（索引在 DB，scoping 由查询保证）
    client.app.dependency_overrides[get_actor] = lambda: CurrentUser(
        id="another-user", name="他人", permissions=frozenset({"asset:read"})
    )
    try:
        listed = client.get("/api/v1/assets/archive").json()["data"]
        assert listed["items"] == [] and listed["total"] == 0
        assert client.get(f"/api/v1/assets/archive/{item['id']}/file").status_code == 404
    finally:
        client.app.dependency_overrides.clear()


def test_archive_rejects_traversal_and_wrong_bytes(
    client: TestClient, archive_root: Path
) -> None:
    with pytest.raises(BadRequest):
        archive.within(archive_root, "../outside")
    response = client.post(
        "/api/v1/assets/archive/upload",
        params={"filename": "bad.png", "category": "character"},
        content=b"not an image",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 400
    assert not list((archive_root / "catalog/assets").glob("*.json"))


def test_import_does_not_escape_inbox(client: TestClient, archive_root: Path) -> None:
    make_image(archive_root / "secret.png")
    response = client.post(
        "/api/v1/assets/archive/import",
        json={"relativePath": "../secret.png", "category": "character"},
    )
    assert response.status_code == 400


@pytest.mark.parametrize("kind", ["character", "product", "scene"])
def test_prepare_is_self_contained_and_idempotent(
    materials: tuple[dict, dict],
    archive_root: Path,
    kind: str,
) -> None:
    source, image = materials
    archive.patch(archive_root / "catalog/assets" / (image["id"] + ".json"), category=kind)
    payload = PrepareReplacement.model_validate(
        {
            "requestId": str(uuid.uuid4()),
            "kind": kind,
            "target": "目标",
            "sourceId": source["id"],
            "referenceIds": [image["id"]],
            # 产品必须显式给出替换范围；服务端不再用默认值替调用方承诺位置。
            "replacementScope": "人物上身穿着" if kind == "product" else "",
        }
    )
    run = prepare(payload, "u_001")
    assert prepare(payload, "u_001").id == run.id
    path = Path(run.archive_path)
    assert path.is_relative_to(archive_root)
    assert (path / "inputs/reference-video.mp4").read_bytes() == (
        archive_root / source["path"]
    ).read_bytes()
    assert "{{Mixed 1}}" in run.prompt and "{{Mixed 2}}" in run.prompt
    assert "{{source_video}}" not in run.prompt
    assert not (path / "inputs/request.json").exists()  # 未冒充已经实际发送请求
    assert run.status == "prepared" and run.task_id is None
    meta = archive.read(path / "run.json")
    assert meta["max_credits"] == 0 and meta["review_status"] == "pending"
    mapping = json.loads((path / "inputs/input-map.json").read_text(encoding="utf-8"))
    for entry in mapping:
        assert archive.checksum(path / entry["file"]) == entry["sha256"]
    with pytest.raises(Conflict):
        prepare(payload.model_copy(update={"target": "不同目标"}), "u_001")


def test_prepare_detects_input_changes(materials: tuple[dict, dict], archive_root: Path) -> None:
    source, image = materials
    (archive_root / image["path"]).write_bytes(b"changed")
    payload = PrepareReplacement(
        request_id=uuid.uuid4(),
        kind="character",
        target="目标",
        source_id=source["id"],
        reference_ids=[image["id"]],
    )
    with pytest.raises(BadRequest):
        prepare(payload, "u_001")


def test_pack_selection_uses_published_configuration(
    materials: tuple[dict, dict],
    archive_root: Path,
) -> None:
    source, image = materials
    archive.save(
        archive_root / "catalog/packs/published.json",
        {
            "id": "published",
            "ownerId": "u_001",
            "kind": "character",
            "name": "已发布人物",
            "referenceIds": [image["id"]],
            "wardrobeMode": "reference",
            "features": "保持身份",
            "instructions": "只替换指定人物",
        },
    )
    run = prepare(
        PrepareReplacement(
            request_id=uuid.uuid4(),
            kind="character",
            target="试图覆盖名称",
            source_id=source["id"],
            reference_ids=["untrusted-id"],
            pack_id="published",
        ),
        "u_001",
    )
    path = Path(run.archive_path)
    assert run.target == "已发布人物"
    assert "人物参考的服装" in run.prompt
    assert "声音：原片内嵌音轨" in run.prompt
    assert "已发布人物" in run.prompt
    assert "{{Mixed 1}}" in run.prompt
    assert (path / "inputs/reference-pack.json").is_file()


@pytest.mark.parametrize("changes", [{"duration": 15.01}, {"hasAudio": False}])
def test_invalid_source_is_never_silently_trimmed(
    materials: tuple[dict, dict],
    archive_root: Path,
    changes: dict,
) -> None:
    source, image = materials
    archive.patch(archive_root / "catalog/assets" / (source["id"] + ".json"), **changes)
    with pytest.raises(BadRequest):
        prepare(
            PrepareReplacement(
                request_id=uuid.uuid4(),
                kind="character",
                target="人物",
                source_id=source["id"],
                reference_ids=[image["id"]],
            ),
            "u_001",
        )


async def test_unknown_submission_cannot_be_repeated(
    materials: tuple[dict, dict],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, image = materials
    run = prepare(
        PrepareReplacement(
            request_id=uuid.uuid4(),
            kind="character",
            target="目标",
            source_id=source["id"],
            reference_ids=[image["id"]],
        ),
        "u_001",
    )
    path = Path(run.archive_path)
    meta = archive.read(path / "run.json")
    meta["status"] = "submission_unknown"
    archive.save(path / "run.json", meta)
    monkeypatch.setattr(settings, "worker_enabled", True)
    with pytest.raises(Conflict):
        await schedule(run.id, "u_001")


def test_missing_archive_never_falls_back(
    archive_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = archive_root / "offline-share"
    monkeypatch.setattr(get_archive_settings(), "root", str(missing))
    with pytest.raises(UpstreamError):
        archive.root()
    assert not missing.exists()


def test_write_permission_is_required(client: TestClient, archive_root: Path) -> None:
    from api.deps import get_actor

    client.app.dependency_overrides[get_actor] = lambda: CurrentUser(
        id="limited", name="只读", permissions=frozenset({"template:read", "asset:read"})
    )
    try:
        assert client.post("/api/v1/templates/replacement-runs", json={}).status_code == 403
        assert (
            client.post(
                "/api/v1/assets/archive/upload",
                params={"filename": "test.png", "category": "character"},
                content=b"test",
            ).status_code
            == 403
        )
    finally:
        client.app.dependency_overrides.clear()


def test_free_quote_gate_and_uncertain_states() -> None:
    script = f"""
      import {{freeQuote, canSubmit}} from {json.dumps(BRIDGE.as_uri())};
      import assert from 'node:assert/strict';
      for (const cost of [1,-1,null,'0',undefined,NaN,Infinity])
        assert.equal(freeQuote(cost),false);
      assert.equal(freeQuote(0),true);
      for (const status of ['submitting','submission_unknown','success','failed'])
        assert.equal(canSubmit({{status,task_id:null}}),false);
      assert.equal(canSubmit({{status:'uploaded',task_id:'existing'}}),false);
      assert.equal(canSubmit({{status:'uploaded',task_id:null}}),true);
    """
    subprocess.run(
        [get_libtv_settings().node, "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
    )


@pytest.mark.parametrize("quote", [0, 5])
def test_bridge_only_reaches_submission_after_zero_quote(
    materials: tuple[dict, dict],
    archive_root: Path,
    quote: int,
) -> None:
    """真实运行新桥接器，替身只模拟旧适配器与上游；绝不访问网络。"""
    source, image = materials
    run = prepare(
        PrepareReplacement(
            request_id=uuid.uuid4(),
            kind="character",
            target="测试",
            source_id=source["id"],
            reference_ids=[image["id"]],
        ),
        "u_001",
    )
    path = Path(run.archive_path)
    meta = archive.read(path / "run.json")
    meta["status"] = "queued"
    archive.save(path / "run.json", meta)
    legacy = archive_root / "fake-legacy"
    files = {
        "scripts/libtv_video.mjs": "console.log('fake release verified');",
        "tools/js-reverse-mcp/release.json": "{}",
        "tools/js-reverse-mcp/canvas-transport.mjs": """
          export class CanvasApi {
            static async fromBrowser() { return new CanvasApi(); }
            async request(route) {
              if (route !== '/api/task/generation/power/calculator')
                throw Error('Unexpected network endpoint');
              return {data:{power:Number(process.env.TEST_QUOTE)}};
            }
          }
        """,
        "tools/js-reverse-mcp/canvas-run.mjs": """
          import {readFile,writeFile} from 'node:fs/promises';
          import path from 'node:path';
          export const load = async run => JSON.parse(await readFile(path.join(run,'run.json')));
          export const save = (filename,value) => writeFile(filename,JSON.stringify(value));
          export const update = async (run,value) => save(path.join(run,'run.json'),
            {...await load(run),...value});
          export const withRunLock = async (_run,action) => action();
          export const poll = async () => {throw Error('No polling expected');};
        """,
        "libtv/adapters/canvas-media.mjs": """
          import {readFile} from 'node:fs/promises';
          import {createHash} from 'node:crypto';
          export const accountUploadUuid = async () => 'fake-account';
          export class MediaUploader {
            async upload(filename) {return {url:'https://example.invalid/media',
              sha256:createHash('sha256').update(await readFile(filename)).digest('hex'),
              audit:{passed:true}};}
          }
        """,
        "libtv/workbench/submit.mjs": """
          import {writeFile} from 'node:fs/promises';
          import path from 'node:path';
          import {update} from '../../tools/js-reverse-mcp/canvas-run.mjs';
          export async function submit(_api,run) {
            await writeFile(path.join(run,'records/CREATE_WAS_REACHED'),'yes');
            await update(run,{status:'submitted',task_id:'fake-task',estimated_credits:0});
            return {state:'submitted'};
          }
        """,
    }
    for filename, content in files.items():
        dest = legacy / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    script = (
        f"import {{main}} from {json.dumps(BRIDGE.as_uri())};"
        f"process.argv={json.dumps(['node', 'test', 'upload', str(path), str(legacy)])};"
        "await main();"
        + ("process.argv[2]='submit';await main();" if quote == 0 else "")
    )
    response = subprocess.run(
        [get_libtv_settings().node, "--input-type=module", "-e", script],
        env={**os.environ, "TEST_QUOTE": str(quote)},
        capture_output=True,
    )
    assert response.returncode == 0, response.stderr.decode(errors="replace")
    updated = archive.read(path / "run.json")
    assert (path / "records/CREATE_WAS_REACHED").exists() is (quote == 0)
    assert updated["status"] == ("submitted" if quote == 0 else "waiting_free")
    assert updated.get("task_id") == ("fake-task" if quote == 0 else None)


@pytest.mark.parametrize(("quote", "expected"), [(40, "uploaded"), (0, "service_error")])
def test_bridge_routes_credits_mode_through_paid_queue(
    materials: tuple[dict, dict], archive_root: Path, quote: int, expected: str
) -> None:
    """加速 = 只给这一条请求关掉 0 积分标签，不碰账号级开关。

    估价必须给正数才算切换成功：报 0 说明标签没关掉，这时候放行等于
    「以为在花钱插队，其实又排回免费队列」，所以要停下等人核对。
    """
    source, image = materials
    run = prepare(
        PrepareReplacement(
            request_id=uuid.uuid4(),
            kind="character",
            target="加速目标",
            source_id=source["id"],
            reference_ids=[image["id"]],
        ),
        "u_001",
    )
    path = Path(run.archive_path)
    meta = archive.read(path / "run.json")
    meta["status"] = "queued"
    meta["billing_mode"] = "credits"
    archive.save(path / "run.json", meta)
    legacy = archive_root / "fake-legacy"
    files = {
        "scripts/libtv_video.mjs": "console.log('fake release verified');",
        "tools/js-reverse-mcp/release.json": "{}",
        "tools/js-reverse-mcp/canvas-transport.mjs": """
          export class CanvasApi {
            static async fromBrowser() { return new CanvasApi(); }
            async request(route) {
              if (route !== '/api/task/generation/power/calculator')
                throw Error('Unexpected network endpoint');
              return {data:{power:Number(process.env.TEST_QUOTE)}};
            }
          }
        """,
        "tools/js-reverse-mcp/canvas-run.mjs": """
          import {readFile,writeFile} from 'node:fs/promises';
          import path from 'node:path';
          export const load = async run => JSON.parse(await readFile(path.join(run,'run.json')));
          export const save = (filename,value) => writeFile(filename,JSON.stringify(value));
          export const update = async (run,value) => save(path.join(run,'run.json'),
            {...await load(run),...value});
          export const withRunLock = async (_run,action) => action();
          export const poll = async () => {throw Error('No polling expected');};
        """,
        "libtv/adapters/canvas-media.mjs": """
          import {readFile} from 'node:fs/promises';
          import {createHash} from 'node:crypto';
          export const accountUploadUuid = async () => 'fake-account';
          export class MediaUploader {
            async upload(filename) {return {url:'https://example.invalid/media',
              sha256:createHash('sha256').update(await readFile(filename)).digest('hex'),
              audit:{passed:true}};}
          }
        """,
        "libtv/workbench/submit.mjs": "export async function submit(){throw Error('not used');}",
        "libtv/adapters/billing.mjs": """
          export async function accountCreditTags() {return ['rateLimit_powerZero_test'];}
          export function applyBilling(request, mode, tags) {
            const out = structuredClone(request);
            out.params.infiniteSwitch = mode === 'credits' ? 1 : 0;
            if (mode === 'credits') {
              out.disableBenefitTags = tags;
              out.params.disableBenefitTags = tags;
            }
            return out;
          }
        """,
    }
    for filename, content in files.items():
        dest = legacy / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    script = (
        f"import {{main}} from {json.dumps(BRIDGE.as_uri())};"
        f"process.argv={json.dumps(['node', 'test', 'upload', str(path), str(legacy)])};"
        "await main();"
    )
    response = subprocess.run(
        [get_libtv_settings().node, "--input-type=module", "-e", script],
        env={**os.environ, "TEST_QUOTE": str(quote)},
        capture_output=True,
    )
    assert response.returncode == 0, response.stderr.decode(errors="replace")
    updated = archive.read(path / "run.json")
    assert updated["status"] == expected
    request = json.loads((path / "inputs/request.json").read_text(encoding="utf-8"))
    assert request["params"]["infiniteSwitch"] == 1
    assert request["disableBenefitTags"] == ["rateLimit_powerZero_test"]
    if quote:
        assert updated["estimated_credits"] == quote
    else:
        assert "积分队列" in updated["last_error"]
        assert updated["estimated_credits"] is None


def test_poll_never_overwrites_the_charged_credits_with_zero(
    materials: tuple[dict, dict], archive_root: Path
) -> None:
    """轮询不能把真实扣费改写成 0。

    上游 progress.power 在排队/生成阶段恒为 0，只有提交回执 data.power 是真正
    结算的那笔。早期实现无条件用 progress.power 覆盖 credits，档案于是显示
    「0 积分」，等于把扣费记录抹掉——用户根本无法核对加速到底花了多少。
    """
    source, image = materials
    run = prepare(
        PrepareReplacement(
            request_id=uuid.uuid4(),
            kind="character",
            target="积分核对",
            source_id=source["id"],
            reference_ids=[image["id"]],
        ),
        "u_001",
    )
    path = Path(run.archive_path)
    meta = archive.read(path / "run.json")
    meta.update({
        "status": "running",
        "billing_mode": "credits",
        "task_id": "20600000000000000001",
        # 已被旧版轮询抹成 0，必须能从提交回执里自愈。
        "credits": 0,
        "estimated_credits": 240,
    })
    archive.save(path / "run.json", meta)
    archive.save(
        path / "records/submission.json",
        {"code": 0, "data": {"power": 240, "taskId": "20600000000000000001"}},
    )
    legacy = archive_root / "fake-legacy"
    files = {
        "scripts/libtv_video.mjs": "console.log('fake release verified');",
        "tools/js-reverse-mcp/release.json": "{}",
        "tools/js-reverse-mcp/canvas-transport.mjs": """
          export class CanvasApi {
            static async fromBrowser() { return new CanvasApi(); }
            async request(route) {
              if (route !== '/api/task/generation/progress')
                throw Error('Unexpected network endpoint');
              return {code:0,data:{progresses:[{taskId:'20600000000000000001',
                status:1,progressPercent:7,power:0}]}};
            }
          }
        """,
        "tools/js-reverse-mcp/canvas-run.mjs": """
          import {readFile,writeFile} from 'node:fs/promises';
          import path from 'node:path';
          export const load = async run => JSON.parse(await readFile(path.join(run,'run.json')));
          export const save = (filename,value) => writeFile(filename,JSON.stringify(value));
          export const update = async (run,value) => save(path.join(run,'run.json'),
            {...await load(run),...value});
          export const withRunLock = async (_run,action) => action();
          export const poll = async () => {throw Error('No polling expected');};
        """,
        "libtv/adapters/canvas-media.mjs": """
          export const accountUploadUuid = async () => 'fake-account';
          export class MediaUploader { async upload() {throw Error('not used');} }
        """,
        "libtv/workbench/submit.mjs": "export async function submit(){throw Error('not used');}",
    }
    for filename, content in files.items():
        dest = legacy / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    script = (
        f"import {{main}} from {json.dumps(BRIDGE.as_uri())};"
        f"process.argv={json.dumps(['node', 'test', 'poll', str(path), str(legacy)])};"
        "await main();"
    )
    response = subprocess.run(
        [get_libtv_settings().node, "--input-type=module", "-e", script],
        capture_output=True,
    )
    assert response.returncode == 0, response.stderr.decode(errors="replace")
    updated = archive.read(path / "run.json")
    assert updated["status"] == "running"
    assert updated["credits"] == 240
    assert updated["estimated_credits"] == 240
