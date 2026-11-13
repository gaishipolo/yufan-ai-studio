"""会话封装：预检、HTTP 边界、丢响应恢复；不触发真实上传或视频生成。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from template_packager.client import PackagingClient
from template_packager.manifest import load_manifest
from template_packager.runner import apply_manifest


def manifest_file(tmp_path: Path) -> Path:
    (tmp_path / "clip.mp4").write_bytes(b"test-video")
    (tmp_path / "front.png").write_bytes(b"test-image")
    data = {
        "key": "demo",
        "name": "演示",
        "video": {"file": "clip.mp4"},
        "packs": [
            {
                "key": "dress",
                "name": "连衣裙",
                "kind": "product",
                "references": [{"key": "front", "file": "front.png", "role": "front"}],
                "supportedScopes": ["worn"],
            }
        ],
        "functions": [
            {
                "key": "replace",
                "name": "产品替换",
                "pack": "dress",
                "prompt": "对 {{Mixed 1}} 使用 {{Mixed 2}}。",
            },
            {
                "key": "detail",
                "name": "局部替换",
                "pack": "dress",
                "prompt": "对 {{Mixed 1}} 使用 {{Mixed 2}}，范围：{{ReplacementScope}}。",
                "scopeOptions": ["前景"],
            },
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def edit(path: Path, change) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_preflight_resolves_files_and_preserves_prompt(tmp_path):
    loaded = load_manifest(manifest_file(tmp_path))
    assert loaded.spec.video.file == tmp_path / "clip.mp4"
    assert loaded.spec.functions[0].prompt == "对 {{Mixed 1}} 使用 {{Mixed 2}}。"
    assert len(loaded.fingerprint) == 64


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["functions"][0].update(prompt="{{Mixed 1}} {{Mixed 3}}"),
        lambda d: d["functions"][0].update(scopeOptions=["前景"]),
        lambda d: d["packs"][0]["references"][0].update(role="unknown"),
        lambda d: d["functions"][1].update(key="replace"),
        lambda d: d["functions"][0].update(mediaOrder=["missing"]),
        lambda d: d["video"].update(file="missing.mp4"),
        lambda d: d["packs"][0].update(supportedScopes=[]),
        lambda d: d["functions"][0].update(typo=True),
        lambda d: (d.update(name="长" * 100), d["functions"][0].update(name="长" * 80)),
    ],
)
def test_preflight_rejects_invalid_config_before_network(tmp_path, change):
    path = manifest_file(tmp_path)
    edit(path, change)
    with pytest.raises(ValueError):
        load_manifest(path)


class Server:
    def __init__(self):
        self.writes = []
        self.templates = {}
        self.assets = {}
        self.packs = {}
        self.owner = "operator"
        self.current = True
        self.lose_response = False

    def __call__(self, request):
        path = request.url.path.removeprefix("/api/v1")
        if path == "/openapi.json":
            return httpx.Response(
                200,
                json={
                    "components": {
                        "schemas": {
                            "PrepareReplacement": {
                                "properties": {"videoTemplateId": {}} if self.current else {}
                            }
                        }
                    },
                    "paths": {
                        "/api/v1/templates/video-templates": {
                            "post": {"x-template-key-idempotency": 1} if self.current else {}
                        }
                    },
                },
            )
        if path == "/identity/me":
            return httpx.Response(
                200, json={"code": 0, "data": {"id": self.owner, "permissions": ["*"]}}
            )
        if request.method == "GET" and path == "/templates/video-templates":
            return httpx.Response(
                200, json={"code": 0, "data": {"items": list(self.templates.values())}}
            )
        if request.method == "GET" and path.startswith("/library/assets/"):
            asset = self.assets[path.split("/")[-1]]
            return httpx.Response(200, json={"code": 0, "data": asset})
        if request.method == "GET" and path == "/templates/replacement-packs":
            return httpx.Response(
                200, json={"code": 0, "data": {"items": list(self.packs.values())}}
            )
        self.writes.append(path)
        payload = (
            json.loads(request.content)
            if request.headers.get("content-type") == "application/json"
            else {}
        )
        if path in ("/library/assets/upload", "/library/source-videos"):
            import hashlib

            digest = hashlib.sha256(request.content).hexdigest()
            source = path == "/library/source-videos"
            asset = {
                "id": digest[:32],
                "name": request.url.params["filename"],
                # 视频走 `/source-videos`（origin 由服务端写死），其余按 query 的 kind 落
                "kind": "video" if source else request.url.params["kind"],
                "origin": "template_source" if source else "upload",
                "sizeBytes": len(request.content),
                "durationSec": 5.3 if source else None,
                "hasAudio": source,
                "visibility": "personal",
                "state": "active",
                "stateVersion": 0,
            }
            self.assets[asset["id"]] = asset
            result = asset
        elif path == "/library/share-requests":
            asset = self.assets[payload["assetId"]]
            # 带 `library:share:direct` 的账号提交即生效（这个假服务不模拟审批流）
            asset["visibility"] = "team"
            asset["stateVersion"] = asset.get("stateVersion", 0) + 1
            result = {"id": "req-1", "status": "approved"}
        elif path == "/templates/replacement-packs/draft":
            # 出参沿用 `ReplacementPack` 的老字段名（`referenceIds`），入参才是
            # `referenceLibraryIds`；假服务必须照抄这个不对称，否则重跑时的
            # 「远端配置是否变化」比对会拿 None 跟列表比，每次都误报。
            result = {
                **payload,
                "referenceIds": payload["referenceLibraryIds"],
                "coverReferenceId": payload["coverLibraryId"],
                "id": payload["requestId"].replace("-", ""),
                "revision": 1,
                "status": "draft",
                "version": 0,
            }
            result.pop("referenceLibraryIds", None)
            result.pop("coverLibraryId", None)
            self.packs[result["id"]] = result
        elif path.endswith("/publish"):
            result = self.packs[path.split("/")[-2]]
            result.update(status="published", version=1)
        elif path == "/templates/video-templates":
            key = payload["templateKey"]
            if key not in self.templates:
                self.templates[key] = {
                    **payload,
                    "id": f"template-{len(self.templates)}",
                    "promptSha256": "digest",
                }
            result = self.templates[key]
            if self.lose_response:
                self.lose_response = False
                raise httpx.ReadTimeout("response lost", request=request)
        else:
            raise AssertionError(f"Unexpected request: {request.method} {path}")
        return httpx.Response(200, json={"code": 0, "data": result})


def client_for(server):
    return PackagingClient("http://test/api/v1", "secret", transport=httpx.MockTransport(server))


def test_client_does_not_inherit_tls_key_logging_environment(tmp_path, monkeypatch):
    target = tmp_path / "absent-directory" / "tls-keys.log"
    monkeypatch.setenv("SSLKEYLOGFILE", str(target))
    # 不发出任何网络请求；初始化不能因调试环境变量向外写文件。
    with PackagingClient("http://127.0.0.1:8080/api/v1", "secret") as api:
        assert api.base_url.endswith("/api/v1")
    assert not target.exists()


def test_two_functions_share_source_and_pack_and_rerun_without_writes(tmp_path):
    loaded = load_manifest(manifest_file(tmp_path))
    server = Server()
    with client_for(server) as api:
        report = apply_manifest(loaded, api, tmp_path / "state.json")
        before = list(server.writes)
        again = apply_manifest(loaded, api, tmp_path / "state.json")
    assert report == again
    assert len(server.templates) == 2
    assert len(server.packs) == 1
    assert server.writes == before
    assert not any(path.endswith("/execute") for path in server.writes)
    assert not any(path.endswith("/share-requests") for path in server.writes)
    assert len({t["sourceVideoAssetId"] for t in server.templates.values()}) == 1
    assert report["functions"][1]["workbenchPath"].find("videoTemplateId=") > 0
    assert "secret" not in (tmp_path / "state.json").read_text(encoding="utf-8")


def test_lost_template_response_is_recoverable(tmp_path):
    loaded = load_manifest(manifest_file(tmp_path))
    server = Server()
    server.lose_response = True
    with client_for(server) as api:
        with pytest.raises(ValueError, match="请求未完成"):
            apply_manifest(loaded, api, tmp_path / "state.json")
        report = apply_manifest(loaded, api, tmp_path / "state.json")
    assert len(server.templates) == 2
    assert len(report["functions"]) == 2
    assert server.writes.count("/templates/replacement-packs/draft") == 1
    assert sum(path.endswith("/publish") for path in server.writes) == 1


def test_changed_content_and_different_account_cannot_reuse_state(tmp_path):
    path = manifest_file(tmp_path)
    server = Server()
    with client_for(server) as api:
        apply_manifest(load_manifest(path), api, tmp_path / "state.json")
        before = list(server.writes)
        (tmp_path / "front.png").write_bytes(b"changed image")
        with pytest.raises(ValueError, match="配置或素材已变化"):
            apply_manifest(load_manifest(path), api, tmp_path / "state.json")
        (tmp_path / "front.png").write_bytes(b"test-image")
        server.owner = "another-account"
        with pytest.raises(ValueError, match="账号或服务"):
            apply_manifest(load_manifest(path), api, tmp_path / "state.json")
    assert before == server.writes


def test_old_server_rejected_before_any_write(tmp_path):
    server = Server()
    server.current = False
    with client_for(server) as api, pytest.raises(ValueError, match="更新并重启"):
        apply_manifest(load_manifest(manifest_file(tmp_path)), api, tmp_path / "state.json")
    assert not server.writes


@pytest.mark.parametrize(
    "field, value",
    [
        ("sourceVideoAssetId", "another-video"),
        ("prompt", "changed prompt"),
    ],
)
def test_remote_template_changes_cannot_hide_behind_fingerprint(tmp_path, field, value):
    loaded = load_manifest(manifest_file(tmp_path))
    server = Server()
    with client_for(server) as api:
        apply_manifest(loaded, api, tmp_path / "state.json")
        next(iter(server.templates.values()))[field] = value
        with pytest.raises(ValueError, match="远端模板配置已变化"):
            apply_manifest(loaded, api, tmp_path / "state.json")


def test_explicit_share_and_changed_prompt_file_fingerprint(tmp_path):
    path = manifest_file(tmp_path)
    (tmp_path / "prompt.txt").write_text("{{Mixed 1}} 使用 {{Mixed 2}}", encoding="utf-8")

    def change(d):
        d["video"]["shared"] = True
        d["functions"][0].pop("prompt")
        d["functions"][0]["promptFile"] = "prompt.txt"

    edit(path, change)
    loaded = load_manifest(path)
    server = Server()
    with client_for(server) as api:
        apply_manifest(loaded, api, tmp_path / "state.json")
    assert sum(path.endswith("/share-requests") for path in server.writes) == 1
    (tmp_path / "prompt.txt").write_text("编辑 {{Mixed 1}} 使用 {{Mixed 2}}", encoding="utf-8")
    assert load_manifest(path).fingerprint != loaded.fingerprint


def test_cli_preflight_is_offline_and_missing_token_is_actionable(tmp_path, capsys, monkeypatch):
    from template_packager.__main__ import main

    path = manifest_file(tmp_path)
    monkeypatch.delenv("YUFAN_API_TOKEN", raising=False)
    assert main([str(path)]) == 0
    result = capsys.readouterr()
    assert json.loads(result.out)["mode"] == "preflight"
    assert not result.err
    assert main([str(path), "--apply"]) == 1
    assert "YUFAN_API_TOKEN" in capsys.readouterr().err
    assert not (tmp_path / "state.json").exists()


def test_real_api_packages_and_prepares_each_function(client, tmp_path):
    """真实素材探测、DB发布、模板落库和提示词编译均走项目 API；不执行生成。"""
    from uuid import uuid4

    from tests_template_helpers import login, png_bytes, video_bytes
    from video_template_support import get_template

    path = manifest_file(tmp_path)
    (tmp_path / "clip.mp4").write_bytes(video_bytes(2, audio=True))
    (tmp_path / "front.png").write_bytes(png_bytes())
    headers = login(client, "admin", "admin@12345")
    writes = []

    def forward(request):
        if request.method != "GET":
            writes.append(request.url.path)
        response = client.request(
            request.method, str(request.url), content=request.content, headers=dict(request.headers)
        )
        return httpx.Response(
            response.status_code, content=response.content, headers=dict(response.headers)
        )

    loaded = load_manifest(path)
    token = headers["Authorization"].removeprefix("Bearer ")
    with PackagingClient(
        "http://testserver/api/v1", token, transport=httpx.MockTransport(forward)
    ) as api:
        report = apply_manifest(loaded, api, tmp_path / "real-state.json")
        before = list(writes)
        assert apply_manifest(loaded, api, tmp_path / "real-state.json") == report
        assert before == writes
        # 进度文件丢失也从远端找回已发布包与模板，不产生新发布版本。
        assert apply_manifest(loaded, api, tmp_path / "recovered-state.json") == report
        assert not any(path.endswith("/publish") for path in writes[len(before) :])
    # 两个功能各封一条模板：按 report 里的 id 逐条核对，而不是数整张表
    # （测试库整场共用，别的用例也会往里写）。
    template_ids = [result["videoTemplateId"] for result in report["functions"]]
    assert len(set(template_ids)) == 2
    for template_id in template_ids:
        assert get_template(template_id).id == template_id
    for function, result in zip(loaded.spec.functions, report["functions"], strict=True):
        payload = {
            "requestId": str(uuid4()),
            "sourceLibraryId": report["sourceLibraryId"],
            "packId": result["packId"],
            "packVersion": result["packVersion"],
            "kind": "product",
            "videoTemplateId": result["videoTemplateId"],
            "replacementScope": "、".join(function.scope_options),
        }
        response = client.post("/api/v1/templates/replacement-runs", json=payload, headers=headers)
        assert response.status_code == 201, response.text
        run = response.json()["data"]
        assert run["prompt"] == function.prompt.replace("{{ReplacementScope}}", "前景")
        assert run["promptMode"] == "fixed"


def test_state_lock_prevents_competing_runs(tmp_path):
    from template_packager.runner import journal

    with (
        journal(tmp_path / "locked.json", {"owner": "one"}, "digest"),
        pytest.raises(ValueError, match="正在执行"),
        journal(tmp_path / "locked.json", {"owner": "one"}, "digest"),
    ):
        pytest.fail("should not acquire a second lock")
