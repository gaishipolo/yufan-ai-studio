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
from core.exceptions import BadRequest, Conflict, NotFound, UpstreamError
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
    Image.new("RGB", (32, 48), "purple").save(path, format="PNG")
    return path


@pytest.fixture
def materials(archive_root: Path) -> tuple[dict, dict]:
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
            "color=c=blue:s=64x64:d=2",
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
        archive.register(video, "source.mp4", "source", "u_001"),
        archive.register(image, "identity.png", "character", "u_001"),
    )


def test_upload_and_owner_scoping(client: TestClient, archive_root: Path) -> None:
    image = io.BytesIO()
    Image.new("RGB", (16, 16), "blue").save(image, format="PNG")
    response = client.post(
        "/api/v1/assets/archive/upload",
        params={"filename": "参考.png", "category": "character"},
        content=image.getvalue(),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 201, response.text
    item = response.json()["data"]
    assert (archive_root / item["path"]).read_bytes() == image.getvalue()
    assert client.get(item["url"]).content == image.getvalue()
    assert archive.assets("another-user") == []
    with pytest.raises(NotFound):
        archive.asset(item["id"], "another-user")


def test_archive_rejects_traversal_and_wrong_bytes(archive_root: Path) -> None:
    with pytest.raises(BadRequest):
        archive.within(archive_root, "../outside")
    invalid = archive_root / "not-an-image.png"
    invalid.write_bytes(b"not an image")
    with pytest.raises(BadRequest):
        archive.register(invalid, "bad.png", "character", "owner")
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
    image["category"] = kind
    archive.save(archive_root / "catalog/assets" / (image["id"] + ".json"), image)
    payload = PrepareReplacement.model_validate(
        {
            "requestId": str(uuid.uuid4()),
            "kind": kind,
            "target": "目标",
            "sourceId": source["id"],
            "referenceIds": [image["id"]],
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
    source.update(changes)
    archive.save(archive_root / "catalog/assets" / (source["id"] + ".json"), source)
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
