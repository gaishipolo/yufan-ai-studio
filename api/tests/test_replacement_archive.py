"""准备阶段：输入快照自洽、发布版本冻结、非法原片不静默截断。

这一组用例是**重建后唯一能证明整条链路走得通的东西**——被移走的旧用例原本覆盖
它们，而删除之后 `prepare` 的三个致命缺陷全靠肉眼看代码才发现：

1. 新建 run 从不登记索引指针，`_run_out()` 拼 `archivePath` 时自己 404；
2. 不写 `inputs/input-map.json`，执行器在 upload 阶段读文件直接 ENOENT；
3. `run.json` 没有 `file_checksums`，执行器拿 `undefined` 跟实算摘要比。

三条的共同点：**每一步单独看都"合理"，串起来第一次点生成就炸**，而全量测试全绿。
所以这里的断言刻意贴到文件级（清单、摘要、投影），而不是只看接口返回 200。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests_template_helpers import (
    ADMIN,
    API,
    add_pack,
    drop_pack,
    drop_run,
    login,
    patch_run,
    png_bytes,
    read_run,
    upload,
    video_bytes,
    workspace,
)
from video_template_support import aput

from core.config import settings
from infra.archive import checksum, get_archive_storage
from infra.archive.local import get_archive_settings
from infra.archive.runs import read_pointer, runs_root
from modules.template import worker
from modules.template.workspace import BRIDGE, run_path


@pytest.fixture
def archive_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))
    return tmp_path


@pytest.fixture
def materials(client: TestClient, archive_root: Path):
    """一份原片 + 一张参考图 + 一个已发布封装（都归演示超管）。

    走真实上传：`prepare` 会读素材的时长、音轨与 `sha256`，手写记录测不出这些。
    """

    def _make(kind: str = "character", *, duration: float = 2, audio: bool = True):
        video = upload(client, "source.mp4", "source", video_bytes(duration, audio=audio))
        image = upload(client, f"{kind}.png", kind, png_bytes())
        return video, image

    return _make


async def _pack(kind: str = "character", *, image_ids: list[str], **extra):
    return await add_pack(
        owner=ADMIN,
        kind=kind,
        name="已发布人物" if kind == "character" else "已发布产品",
        reference_ids=image_ids,
        features="保持身份",
        instructions="只替换指定人物",
        wardrobe_mode="reference" if kind == "character" else "preserve",
        **extra,
    )


def _payload(kind: str, source_id: str, pack, **extra) -> dict:
    values = {
        "requestId": str(uuid.uuid4()),
        "kind": kind,
        "sourceLibraryId": source_id,
        "packId": pack.id,
        "packVersion": pack.version,
        "replacementScope": "人物上身穿着" if kind == "product" else "",
    }
    values.update(extra)
    return values


async def test_prepare_creates_a_reachable_run(client: TestClient, materials) -> None:
    """新建 run 必须同时有行、目录、`run.json`、指针——少一个后面全 404。

    这条专门回归「只建目录不登记指针」：接口自己会 404，错误信息还是
    「调用档案不存在」，看起来像前端传错了 id。
    """
    video, image = materials()
    pack = await _pack(image_ids=[image["id"]])
    try:
        payload = _payload("character", video["id"], pack)
        created = client.post(f"{API}/templates/replacement-runs", json=payload)
        assert created.status_code == 201, created.text
        run = created.json()["data"]
        try:
            assert run["status"] == "prepared"
            assert run["archivePath"], "出参里没有工作区路径：执行器和详情页都定位不到档案"

            path = Path(run["archivePath"])
            root = str(await asyncio.to_thread(runs_root))
            assert str(path).startswith(root)
            assert (path / "run.json").is_file()
            assert read_pointer(get_archive_storage(), run["id"])
            assert client.get(f"{API}/templates/replacement-runs/{run['id']}").status_code == 200
        finally:
            await drop_run(run["id"])
    finally:
        await drop_pack(pack.id)


async def test_input_thumbnail_uses_snapshot_and_requires_run_owner(
    client: TestClient, materials
) -> None:
    video, image = materials()
    pack = await _pack(image_ids=[image["id"]])
    run_id = None
    try:
        created = client.post(
            f"{API}/templates/replacement-runs", json=_payload("character", video["id"], pack)
        )
        assert created.status_code == 201, created.text
        run = created.json()["data"]
        run_id = run["id"]
        items = run["inputSummary"]["assets"]
        assert [item["token"] for item in items] == ["{{Mixed 1}}", "{{Mixed 2}}"]
        other = login(client, "editor", "editor@12345")
        for item in items:
            url = item["previewUrl"]
            assert url == item["url"] + "/thumbnail"
            response = client.get(url)
            assert response.status_code == 200, response.text
            assert response.headers["content-type"].startswith("image/jpeg")
            assert response.content[:2] == b"\xff\xd8"
            assert client.get(url).content == response.content
            assert client.get(url, headers=other).status_code == 404
        assert client.get(
            f"{API}/templates/replacement-runs/{run_id}/inputs/not-an-input/thumbnail"
        ).status_code == 404
    finally:
        if run_id:
            await drop_run(run_id)
        await drop_pack(pack.id)


async def test_prepare_is_self_contained_and_idempotent(client: TestClient, materials) -> None:
    video, image = materials()
    pack = await _pack(image_ids=[image["id"]])
    payload = _payload("character", video["id"], pack)
    try:
        first = client.post(f"{API}/templates/replacement-runs", json=payload)
        assert first.status_code == 201, first.text
        run = first.json()["data"]
        try:
            again = client.post(f"{API}/templates/replacement-runs", json=payload)
            assert again.json()["data"]["id"] == run["id"], "同一个 requestId 只能有一条记录"

            path = Path(run["archivePath"])
            # 原片与参考图都落成真实文件（执行器要真实路径）
            assert (path / "inputs/reference-video.mp4").is_file()
            assert (path / "inputs/reference-1.png").is_file()
            assert "{{Mixed 1}}" in run["prompt"] and "{{Mixed 2}}" in run["prompt"]
            assert "{{source_video}}" not in run["prompt"]
            # 未冒充「已经发出请求」：这份文件只有走到 upload 阶段才该出现
            assert not (path / "inputs/request.json").exists()
            assert run["status"] == "prepared" and run["taskId"] is None

            projection = json.loads((path / "run.json").read_text(encoding="utf-8"))
            assert projection["status"] == "prepared"
            assert projection["billing_mode"] == "free"

            # 换了配置就不能复用同一个 requestId
            other = client.post(
                f"{API}/templates/replacement-runs",
                json={**payload, "notes": "换一份配置"},
            )
            assert other.status_code == 409
        finally:
            await drop_run(run["id"])
    finally:
        await drop_pack(pack.id)


async def test_prepare_writes_what_the_executor_reads(client: TestClient, materials) -> None:
    """清单、媒体元信息、摘要三者必须都在：执行器缺一样就在 upload 阶段报错。

    - `inputs/input-map.json`：逐条上传清单（含用途文字）
    - `records/media-inputs.json`：宽高时长，拼请求体用
    - `run.json.file_checksums`：与实算摘要比对，防快照被换过
    """
    video, image = materials()
    pack = await _pack(image_ids=[image["id"]])
    try:
        created = client.post(
            f"{API}/templates/replacement-runs", json=_payload("character", video["id"], pack)
        )
        run = created.json()["data"]
        try:
            path = Path(run["archivePath"])
            mapping = json.loads((path / "inputs/input-map.json").read_text(encoding="utf-8"))
            # 人物包的第一张参考图角色是 `identity`（真人是身份图，不是普通参考）
            assert [entry["role"] for entry in mapping] == ["identity", "source_video"]
            for entry in mapping:
                assert checksum(path / entry["file"]) == entry["sha256"], (
                    "清单里的摘要跟物化出来的字节不一致，执行器会拒绝提交"
                )
                assert entry["name"]

            media = json.loads((path / "records/media-inputs.json").read_text(encoding="utf-8"))
            assert set(media) == {entry["file"] for entry in mapping}
            assert media["inputs/reference-video.mp4"]["duration"] > 0

            projection = json.loads((path / "run.json").read_text(encoding="utf-8"))
            assert projection["file_checksums"] == {
                entry["file"]: entry["sha256"] for entry in mapping
            }
        finally:
            await drop_run(run["id"])
    finally:
        await drop_pack(pack.id)


async def test_source_is_snapshotted_once(client: TestClient, materials) -> None:
    """原片在 run 目录里只占一份字节：两个文件名是同一份（硬链接）。

    以前这里是两份 300MB 的副本，共享盘从 200MB 涨到 500MB 就是这么来的。
    """
    video, image = materials()
    pack = await _pack(image_ids=[image["id"]])
    try:
        run = client.post(
            f"{API}/templates/replacement-runs", json=_payload("character", video["id"], pack)
        ).json()["data"]
        try:
            path = Path(run["archivePath"])
            reference = path / "inputs/reference-video.mp4"
            original = path / "inputs/source-original.mp4"
            assert reference.stat().st_ino == original.stat().st_ino
        finally:
            await drop_run(run["id"])
    finally:
        await drop_pack(pack.id)


async def test_published_configuration_wins_over_request(client: TestClient, materials) -> None:
    """目标名、服装模式、补充说明都来自**发布版本**，不接受调用方覆盖。

    否则「公共原片 + 自己的封装」这条流程里，调用方能悄悄改掉别人审核过的约束。
    """
    video, image = materials()
    pack = await _pack(image_ids=[image["id"]])
    try:
        run = client.post(
            f"{API}/templates/replacement-runs",
            json=_payload("character", video["id"], pack, target="试图覆盖名称"),
        ).json()["data"]
        try:
            assert run["target"] == "已发布人物"
            assert "人物参考的服装" in run["prompt"]
            assert "声音：原片内嵌音轨" in run["prompt"]
            assert "已发布人物" in run["prompt"]
            path = Path(run["archivePath"])
            assert (path / "code/runtime/bridge.mjs").is_file(), "编译器快照缺失后无法复现提示词"
        finally:
            await drop_run(run["id"])
    finally:
        await drop_pack(pack.id)


async def test_prompt_sha256_covers_the_bytes_that_are_actually_sent(
    client: TestClient, materials
) -> None:
    """`prompt_sha256` 必须与 `prompt.txt`、档案 JSON 里那一份同源同值。

    编译器交的 `template_sha256` / `spec_sha256` 哈希的是模板和输入，而之后 Python
    还会注入文本正文、改写引用写法——它们对不上「实际发出去的那段字节」。这条用例
    盯的就是那个缺口：三处（接口出参、`inputs/prompt.txt`、`records/prompt-compilation.json`）
    必须是同一份字符串的摘要，少一处就没法复核。
    """
    video, image = materials()
    pack = await _pack(image_ids=[image["id"]])
    try:
        run = client.post(
            f"{API}/templates/replacement-runs", json=_payload("character", video["id"], pack)
        ).json()["data"]
        try:
            path = Path(run["archivePath"])
            prompt_txt = (path / "inputs/prompt.txt").read_text(encoding="utf-8")
            digest = hashlib.sha256(prompt_txt.encode("utf-8")).hexdigest()

            assert run["prompt"] == prompt_txt
            assert run["promptSha256"] == digest
            record = json.loads(
                (path / "records/prompt-compilation.json").read_text(encoding="utf-8")
            )
            assert record["prompt_sha256"] == digest
        finally:
            await drop_run(run["id"])
    finally:
        await drop_pack(pack.id)


@pytest.mark.parametrize(("duration", "audio"), [(16, True), (2, False)])
async def test_invalid_source_is_never_silently_trimmed(
    client: TestClient, materials, duration: float, audio: bool
) -> None:
    """超长 / 没音轨必须在准备阶段拒掉：悄悄截断等于改了用户的原片。"""
    video, image = materials(duration=duration, audio=audio)
    pack = await _pack(image_ids=[image["id"]])
    try:
        response = client.post(
            f"{API}/templates/replacement-runs", json=_payload("character", video["id"], pack)
        )
        assert response.status_code == 400, response.text
        assert "1–15" in response.json()["message"] or "音轨" in response.json()["message"]
    finally:
        await drop_pack(pack.id)


async def test_scattered_references_are_rejected(client: TestClient, materials) -> None:
    """散图入口已随重建移除：必须选已发布对象与版本。"""
    video, image = materials()
    try:
        response = client.post(
            f"{API}/templates/replacement-runs",
            json={
                "requestId": str(uuid.uuid4()),
                "kind": "character",
                "sourceLibraryId": video["id"],
                "referenceIds": [image["id"]],
                "target": "随便",
            },
        )
        assert response.status_code == 400
        assert "必须选择专员发布的对象" in response.json()["message"]
    finally:
        pass


async def test_product_scope_must_come_from_the_pack(client: TestClient, materials) -> None:
    """产品替换位置只能落在封装审核过的范围里。"""
    video, image = materials(kind="product")
    pack = await _pack("product", image_ids=[image["id"]], scopes=["worn"])
    try:
        ok = client.post(
            f"{API}/templates/replacement-runs", json=_payload("product", video["id"], pack)
        )
        assert ok.status_code == 201, ok.text
        await drop_run(ok.json()["data"]["id"])

        beyond = client.post(
            f"{API}/templates/replacement-runs",
            json=_payload("product", video["id"], pack, replacementScope="屏幕商品"),
        )
        assert beyond.status_code == 400
        assert "替换范围超出" in beyond.json()["message"]
    finally:
        await drop_pack(pack.id)


@pytest.mark.parametrize("selectable_scope", [False, True])
async def test_selected_video_function_controls_prompt_and_scope(
    client: TestClient, materials, tmp_path: Path, monkeypatch, selectable_scope: bool
) -> None:
    video, image = materials(kind="product")
    pack = await _pack("product", image_ids=[image["id"]], scopes=["worn"])
    prompt = "编辑 {{Mixed 1}}，使用 {{Mixed 2}}。"
    if selectable_scope:
        prompt += "范围：{{ReplacementScope}}。"
    base = {
        "templateKey": "function", "description": "", "promptSha256": "0" * 64,
        "sourceVideoName": "source.mp4", "sourceVideoAssetId": video["id"],
        "sourceVideoPath": "", "sourceDurationSec": 2, "aspectRatio": "9:16",
        "packId": pack.id, "defaultKind": "product", "mediaOrder": [],
        "createdAt": "2026-11-09T00:00:00Z", "updatedAt": "2026-11-09T00:00:00Z",
    }
    # 两条模板必须各有 `templateKey`：它是「重试稳定标识」，表上唯一。
    # 旧用例里两条共用一个 key 之所以能过，是因为造数直接写 catalog.json，
    # 绕过了 `_append_record` 的同键去重（同键不同配置在真实路径上是 409）。
    records = [
        {**base, "id": "pinned", "templateKey": "function-a", "name": "另一功能",
         "pinned": True, "prompt": "另一功能 {{Mixed 1}} {{Mixed 2}}"},
        {**base, "id": "selected", "templateKey": "function-b", "name": "所选功能",
         "prompt": prompt,
         "rules": {"scopeOptions": ["只换前景"] if selectable_scope else []}},
    ]
    for item in records:
        await aput(**item)
    try:
        response = client.post(f"{API}/templates/replacement-runs", json=_payload(
            "product", video["id"], pack, videoTemplateId="selected",
            replacementScope="只换前景" if selectable_scope else "",
        ))
        assert response.status_code == 201, response.text
        result = response.json()["data"]
        try:
            assert result["prompt"] == prompt.replace("{{ReplacementScope}}", "只换前景")
            assert result["videoTemplateName"] == "所选功能"
        finally:
            await drop_run(result["id"])
    finally:
        await drop_pack(pack.id)


async def test_function_editor_persists_options_and_rejects_pack_type_mismatch(
    client: TestClient, materials, tmp_path: Path, monkeypatch
) -> None:
    video, image = materials(kind="product")
    pack = await _pack("product", image_ids=[image["id"]], scopes=["worn"])
    draft = {
        "name": "新功能", "sourceVideoAssetId": video["id"], "packId": pack.id,
        "prompt": "{{Mixed 1}} {{Mixed 2}} 范围：{{ReplacementScope}}",
        "defaultKind": "character", "rules": {
            "workbenchLabel": "局部替换", "scopeOptions": ["前景", "背景"],
        },
    }
    try:
        rejected = client.post(f"{API}/templates/video-templates", json=draft)
        assert rejected.status_code == 400, rejected.text
        draft["defaultKind"] = "product"
        saved = client.post(f"{API}/templates/video-templates", json=draft)
        assert saved.status_code == 200, saved.text
        assert saved.json()["data"]["rules"]["scopeOptions"] == ["前景", "背景"]
    finally:
        await drop_pack(pack.id)


async def test_unknown_submission_cannot_be_executed(client: TestClient, materials) -> None:
    """`submission_unknown` 是死锁：既不能当失败重提，也不能当成功继续。"""
    video, image = materials()
    pack = await _pack(image_ids=[image["id"]])
    try:
        run = client.post(
            f"{API}/templates/replacement-runs", json=_payload("character", video["id"], pack)
        ).json()["data"]
        try:
            from tests_template_helpers import patch_run

            await patch_run(run["id"], status="submission_unknown", submit_attempts=1)
            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(settings, "worker_enabled", True)
                response = client.post(f"{API}/templates/replacement-runs/{run['id']}/execute")
            assert response.status_code == 409
        finally:
            await drop_run(run["id"])
    finally:
        await drop_pack(pack.id)


def test_write_permission_is_required(client: TestClient, archive_root: Path) -> None:
    """只读账号不能提交：`prepare` 会占队列，也会在未来某次点执行时真花钱。"""
    from api.deps import get_actor
    from core.context import CurrentUser

    client.app.dependency_overrides[get_actor] = lambda: CurrentUser(
        id="limited", name="只读", permissions=frozenset({"template:read", "library:read"})
    )
    try:
        assert client.post(f"{API}/templates/replacement-runs", json={}).status_code == 403
        # 只有 `library:read` 传不了图：上传要 `library:create`
        assert (
            client.post(
                f"{API}/library/assets/upload",
                params={"filename": "test.png", "kind": "image"},
                content=b"test",
            ).status_code
            == 403
        )
    finally:
        client.app.dependency_overrides.pop(get_actor, None)


def test_editor_cannot_prepare_for_someone_else(client: TestClient, archive_root: Path) -> None:
    """权限组合：`template:read` 不够（403），有 `template:use` 但要别人原片也拿不到。"""
    headers = login(client, "viewer", "viewer@12345")
    blocked = client.post(f"{API}/templates/replacement-runs", json={}, headers=headers)
    assert blocked.status_code == 403


def test_workspace_helper_matches_production_layout() -> None:
    """测试夹具造的工作区必须与真实布局同形，否则断言的其实是夹具自己的洞。"""
    run_id = uuid.uuid4().hex
    path = workspace(run_id, {"id": run_id, "status": "prepared"})
    try:
        assert path == run_path(run_id)
        for folder in ("inputs", "outputs", "records", "review", "code"):
            assert (path / folder).is_dir()
    finally:
        import shutil

        shutil.rmtree(path, ignore_errors=True)


# --------------------------------------------------------------------------- #
# 执行器契约：run.json 的字段名与「零报价」闸门
# --------------------------------------------------------------------------- #
def _write_fake_vendor(root: Path, *, submit_body: str) -> Path:
    """搭一个「假 vendor」目录：桥接层只认文件，不认网络。

    这些替身与本仓库 vendor 里固定版适配器的接口逐字对齐
    （`upload/update/withRunLock` 的签名、`submit(api, run, id, verify)` 的四个参数），
    所以桥接层的真实代码会被执行到，而网络请求全部落在替身上。manifest.json
    按真格式生成，让 `verifyVendor()` 的哈希校验也真实跑一遍。
    """
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
        "libtv/workbench/submit.mjs": submit_body,
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
        dest = root / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    from hashlib import sha256

    manifest = {
        "schema": 1,
        "status": "test-fixture",
        "files": {name: sha256((root / name).read_bytes()).hexdigest() for name in files},
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


SUBMIT_REACHED = """
  import {writeFile} from 'node:fs/promises';
  import path from 'node:path';
  import {update} from '../../tools/js-reverse-mcp/canvas-run.mjs';
  export async function submit(_api,run) {
    await writeFile(path.join(run,'records/CREATE_WAS_REACHED'),'yes');
    await update(run,{status:'submitted',task_id:'fake-task',estimated_credits:0});
    return {state:'submitted'};
  }
"""

SUBMIT_UNUSED = "export async function submit(){throw Error('not used');}"


def _run_bridge(path: Path, vendor: Path, commands: list[str], *, quote: int) -> None:
    """真跑桥接层：`commands` 按顺序执行，`TEST_QUOTE` 控制估价。

    argv 第 5 项是适配器目录（生产由监督进程算出仓库内 vendor），测试换成替身目录。
    """
    script = (
        f"import {{main}} from {json.dumps(BRIDGE.as_uri())};"
        f"process.argv={json.dumps(['node', 'test', 'upload', str(path), str(vendor)])};"
        "await main();"
        + "".join(f"process.argv[2]={json.dumps(command)!s};await main();" for command in commands)
    )
    response = subprocess.run(
        [settings.node_binary, "--input-type=module", "-e", script],
        env={**os.environ, "TEST_QUOTE": str(quote)},
        capture_output=True,
    )
    assert response.returncode == 0, response.stderr.decode(errors="replace")


async def _prepared(client: TestClient, materials, kind: str = "character"):
    video, image = materials(kind=kind)
    pack = await _pack(kind, image_ids=[image["id"]], scopes=["worn"] if kind == "product" else [])
    created = client.post(
        f"{API}/templates/replacement-runs", json=_payload(kind, video["id"], pack)
    )
    assert created.status_code == 201, created.text
    return created.json()["data"], pack


def test_free_quote_gate_and_uncertain_states() -> None:
    """`freeQuote` 只认「真的是 0」；`canSubmit` 只要拿到过任务号就不再放行。

    第二句是「重复计费」的最后一道闸：字段名写成 `taskId` 时 `meta.task_id` 恒为
    undefined，`canSubmit` 会一直放行。
    """
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
        [settings.node_binary, "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
    )


async def test_projection_uses_the_field_names_the_executor_reads(
    client: TestClient, materials
) -> None:
    """`run.json` 是喂给执行器的，字段名必须对齐它：`task_id`、`billing_mode`。

    写成 `taskId` 的后果比「读不到」严重：`canSubmit()` 认为这条从没提交过，
    于是重提交、重复计费，而数据库里那条记录明明已经有任务号。
    """
    run, pack = await _prepared(client, materials)
    try:
        await patch_run(run["id"], status="uploaded", task_id="upstream-1", billing_mode="credits")
        row = await read_run(run["id"])

        await asyncio.to_thread(worker.sync_projection, row)

        state = json.loads((run_path(run["id"]) / "run.json").read_text(encoding="utf-8"))
        assert state["task_id"] == "upstream-1"
        assert "taskId" not in state
        assert state["billing_mode"] == "credits"
        assert state["status"] == "uploaded"
        assert state["file_checksums"], "缺摘要表执行器在 upload 阶段直接抛错"
    finally:
        await drop_run(run["id"])
        await drop_pack(pack.id)


@pytest.mark.parametrize("quote", [0, 5])
async def test_bridge_only_reaches_submission_after_zero_quote(
    client: TestClient, materials, tmp_path, quote: int
) -> None:
    """真实运行桥接层：报价非 0 时它停在 `waiting_free`，绝不提交。"""
    run, pack = await _prepared(client, materials)
    try:
        await patch_run(run["id"], status="queued")
        await asyncio.to_thread(worker.sync_projection, await read_run(run["id"]))
        path = run_path(run["id"])
        vendor = _write_fake_vendor(tmp_path / "fake-vendor", submit_body=SUBMIT_REACHED)

        _run_bridge(path, vendor, ["submit"] if quote == 0 else [], quote=quote)

        updated = json.loads((path / "run.json").read_text(encoding="utf-8"))
        assert (path / "records/CREATE_WAS_REACHED").exists() is (quote == 0)
        assert updated["status"] == ("submitted" if quote == 0 else "waiting_free")
        assert updated.get("task_id") == ("fake-task" if quote == 0 else None)
    finally:
        await drop_run(run["id"])
        await drop_pack(pack.id)


@pytest.mark.parametrize(("quote", "expected"), [(40, "uploaded"), (0, "service_error")])
async def test_bridge_routes_credits_mode_through_paid_queue(
    client: TestClient, materials, tmp_path, quote: int, expected: str
) -> None:
    """加速 = 只给这一条请求关掉 0 积分标签，不碰账号级开关。

    估价必须是正数：报 0 说明标签没关掉，这时候放行等于「以为在花钱插队，
    实际又排回免费队列」，所以要停下等人核对。
    """
    run, pack = await _prepared(client, materials)
    try:
        await patch_run(run["id"], status="queued", billing_mode="credits")
        await asyncio.to_thread(worker.sync_projection, await read_run(run["id"]))
        path = run_path(run["id"])
        vendor = _write_fake_vendor(tmp_path / "fake-vendor", submit_body=SUBMIT_UNUSED)

        _run_bridge(path, vendor, [], quote=quote)

        updated = json.loads((path / "run.json").read_text(encoding="utf-8"))
        assert updated["status"] == expected
        request = json.loads((path / "inputs/request.json").read_text(encoding="utf-8"))
        assert request["params"]["infiniteSwitch"] == 1
        assert request["disableBenefitTags"] == ["rateLimit_powerZero_test"]
        if quote:
            assert updated["estimated_credits"] == quote
        else:
            assert "积分队列" in updated["last_error"]
            assert updated["estimated_credits"] is None
    finally:
        await drop_run(run["id"])
        await drop_pack(pack.id)


async def test_multi_character_slots_are_filled_by_extra_packs(
    client: TestClient, materials, tmp_path: Path, monkeypatch
) -> None:
    """三个「正面身份图」槽位 = 视频里三位人物，运行时各选一张人物卡。

    **为什么封装不能按「一个包填满所有槽位」校验**：人物包是一人一包、各一张
    正面图，3 个槽位天然填不满。所以多槽位模板在封装时把填不满的槽位留空，
    写成「运行时选正面身份图」的声明；等 prepare 带上 `extraPackIds`，再按
    **槽位顺序**把第 1/2/3 个人的正面图填进去。每种用途只声明一个槽位的模板
    仍按老规矩报缺项，不能被这条放宽带沟里。
    """
    video, face1 = materials("character")
    face2 = upload(client, "face2.png", "character", png_bytes())
    face3 = upload(client, "face3.png", "character", png_bytes())
    packs = [
        await add_pack(
            owner=ADMIN, kind="character", name=f"人物{index}",
            reference_ids=[face["id"]], features="保持身份",
            instructions="只替换指定人物", wardrobe_mode="reference",
        )
        for index, face in enumerate([face1, face2, face3], 1)
    ]
    draft = {
        "name": "三人换脸",
        "sourceVideoAssetId": video["id"],
        "packId": packs[0].id,
        "defaultKind": "character",
        "prompt": (
            "编辑 {{Mixed 1}}：{{Mixed 2}} 是第 1 位，{{Mixed 3}} 是第 2 位，"
            "{{Mixed 4}} 是第 3 位。"
        ),
        "rules": {
            "referenceBindings": {
                "{{Mixed 1}}": "source_video",
                "{{Mixed 2}}": "front",
                "{{Mixed 3}}": "front",
                "{{Mixed 4}}": "front",
            }
        },
    }
    runs: list[str] = []
    try:
        created = client.post(f"{API}/templates/video-templates", json=draft)
        assert created.status_code == 200, created.text
        media = created.json()["data"]["mediaOrder"]
        assert [item["role"] for item in media] == ["source_video", "front", "front", "front"]
        assert media[1]["assetId"] == face1["id"]
        # 默认包只有一张正面图：第 2、3 槽留空，等运行时选人
        assert media[2]["assetId"] is None and media[3]["assetId"] is None
        assert "运行时选" in media[2]["name"]

        response = client.post(
            f"{API}/templates/replacement-runs",
            json=_payload(
                "character", video["id"], packs[0],
                extraPackIds=[packs[1].id, packs[2].id],
            ),
        )
        assert response.status_code == 201, response.text
        result = response.json()["data"]
        runs.append(result["id"])
        assert result["target"] == "人物1、人物2、人物3"
        assert result["promptMode"] == "fixed"
        # 槽位顺序 = 选人顺序，不能因为包在库里的先后而串位
        refs = [
            asset["id"] for asset in result["inputSummary"]["assets"]
            if asset["role"] != "source_video"
        ]
        assert refs == [face1["id"], face2["id"], face3["id"]]

        # 同一张人物卡填两个槽位等于把第 1 个人当成第 2 个人，必须在准备前拦下
        duplicated = client.post(
            f"{API}/templates/replacement-runs",
            json=_payload(
                "character", video["id"], packs[0], extraPackIds=[packs[0].id],
            ),
        )
        assert duplicated.status_code == 400, duplicated.text
        assert "同一张人物卡" in duplicated.text
    finally:
        for run_id in runs:
            await drop_run(run_id)
        for pack in packs:
            await drop_pack(pack.id)
