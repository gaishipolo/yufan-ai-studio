"""按用途封装：复用已发布包，引用槽位固定，辅助图片全量附带。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_video_template_binding import template

from core.exceptions import BadRequest
from modules.template.replacement_service import _check_video_template, _media_in_mixed_order
from template_packager.manifest import load_manifest


def reuse_manifest(tmp_path: Path, *, kind="product", role="front") -> Path:
    path = tmp_path / "reuse.json"
    path.write_text(
        json.dumps(
            {
                "key": "reuse-v1",
                "name": "复用包的模板",
                "video": {"assetId": "existing-video"},
                "packs": [{"key": "target", "kind": kind, "packId": "published-pack"}],
                "functions": [
                    {
                        "key": "replace",
                        "name": "替换",
                        "pack": "target",
                        "prompt": "对 {{Mixed 1}} 使用 {{Mixed 2}}。",
                        "bindings": {"{{Mixed 1}}": "source_video", "{{Mixed 2}}": role},
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    "kind,role", [("product", "front"), ("character", "front"), ("scene", "main")]
)
def test_manifest_can_reference_existing_video_and_pack_without_local_images(tmp_path, kind, role):
    loaded = load_manifest(reuse_manifest(tmp_path, kind=kind, role=role))
    assert not loaded.digests
    assert loaded.spec.video.asset_id == "existing-video"
    assert loaded.spec.functions[0].bindings["{{Mixed 2}}"] == role


@pytest.mark.parametrize(
    "bindings",
    [
        {"{{Mixed 1}}": "source_video", "{{Mixed 2}}": "unknown"},
        {"{{Mixed 1}}": "source_video", "{{Mixed 3}}": "front"},
        {"{{Mixed 1}}": "front", "{{Mixed 2}}": "front"},
    ],
)
def test_invalid_slot_declarations_fail_offline(tmp_path, bindings):
    path = reuse_manifest(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["functions"][0]["bindings"] = bindings
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        load_manifest(path)


def test_only_declared_roles_are_required_and_all_current_images_are_attached():
    configured = template(
        rules={"referenceBindings": {"{{Mixed 1}}": "source_video", "{{Mixed 2}}": "front"}}
    )
    configured.prompt = "使用 {{Mixed 1}} 和 {{Mixed 2}}。"
    # 样例包有三视图、细节图；当前包只有正面、侧面，也应该可以调用。
    current_roles = {"side": "side", "front": "front"}
    _check_video_template(configured, current_roles, "product")
    source = SimpleNamespace(id="video")
    images = [SimpleNamespace(id="side"), SimpleNamespace(id="front")]
    ordered = _media_in_mixed_order(configured, source, images, current_roles)
    assert [item.id for item in ordered] == ["video", "front", "side"]
    # 换到图片更多的包，未引用图依然完整附带。
    images += [SimpleNamespace(id="detail")]
    ordered = _media_in_mixed_order(
        configured, source, images, {**current_roles, "detail": "detail"}
    )
    assert [item.id for item in ordered] == ["video", "front", "side", "detail"]


def test_missing_declared_role_and_repeated_role_shortage_are_rejected():
    configured = template(
        rules={
            "referenceBindings": {
                "{{Mixed 1}}": "source_video",
                "{{Mixed 2}}": "front",
                "{{Mixed 3}}": "front",
            }
        }
    )
    configured.prompt = "{{Mixed 1}} {{Mixed 2}} {{Mixed 3}}"
    with pytest.raises(BadRequest):
        _check_video_template(configured, {"one": "front"}, "product")


def test_auto_default_is_compatible_and_stable_across_retries(tmp_path):
    from template_packager.reuse import resolve_existing_packs

    path = reuse_manifest(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["packs"][0].pop("packId")
    path.write_text(json.dumps(data), encoding="utf-8")
    spec = load_manifest(path).spec
    items = [
        {
            "id": "missing-front",
            "name": "A",
            "kind": "product",
            "version": 1,
            "referenceIds": ["side"],
            "bindings": [{"role": "side"}],
        },
        {
            "id": "compatible",
            "name": "B",
            "kind": "product",
            "version": 1,
            "referenceIds": ["front"],
            "bindings": [{"role": "front"}],
        },
    ]

    class Catalog:
        def request(self, method, path, **kwargs):
            assert method == "GET" and path.endswith("/available")
            return {"items": items, "total": len(items)}

    state = {}
    assert resolve_existing_packs(spec, Catalog(), state)["target"]["id"] == "compatible"
    items.append({**items[1], "id": "new-earlier", "name": "A"})
    assert resolve_existing_packs(spec, Catalog(), state)["target"]["id"] == "compatible"
    items[:] = [item for item in items if item["id"] != "compatible"]
    with pytest.raises(ValueError, match="已发布素材包"):
        resolve_existing_packs(spec, Catalog(), state)


def test_local_pack_with_bindings_checks_declared_uses_before_upload(tmp_path):
    from test_template_packager import manifest_file

    path = manifest_file(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["functions"][0]["bindings"] = {"{{Mixed 1}}": "source_video", "{{Mixed 2}}": "detail"}
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="足够"):
        load_manifest(path)


def test_real_api_reuses_all_three_pack_types_and_switches_to_smaller_pack(
    client, tmp_path, monkeypatch
):
    import asyncio
    from uuid import uuid4

    import httpx
    from tests_template_helpers import add_pack, login, png_bytes, upload, video_bytes

    from modules.template import video_template_store
    from template_packager.client import PackagingClient
    from template_packager.runner import apply_manifest

    monkeypatch.setattr(video_template_store, "CATALOG_PATH", tmp_path / "catalog.json")
    source = upload(client, "reuse-video.mp4", "source", video_bytes(2, audio=True))
    data = {
        "key": "reuse-all",
        "name": "三类替换",
        "video": {"assetId": source["id"]},
        "packs": [],
        "functions": [],
    }
    alternate = {}
    expected_images = {}
    for kind, base, extra in [
        ("product", "front", "detail"),
        ("character", "front", "fullbody"),
        ("scene", "main", "detail"),
    ]:
        side = "angle" if kind == "scene" else "side"
        ids = [
            upload(client, f"{role}.png", kind, png_bytes())["id"] for role in [side, base, extra]
        ]
        original = asyncio.run(
            add_pack(
                kind=kind,
                reference_ids=ids,
                roles=[side, base, extra],
                required_roles=[base],
                scopes=["held"] if kind == "product" else [],
            )
        )
        other = asyncio.run(
            add_pack(
                kind=kind,
                reference_ids=ids[:2],
                roles=[side, base],
                required_roles=[base],
                scopes=["held"] if kind == "product" else [],
            )
        )
        alternate[kind] = other
        expected_images[kind] = [ids[1], ids[0]]
        data["packs"].append({"key": kind, "kind": kind, "packId": original.id})
        data["functions"].append(
            {
                "key": kind,
                "name": kind,
                "pack": kind,
                "prompt": "对 {{Mixed 1}} 使用 {{Mixed 2}}。其余图片仅补充参考。",
                "bindings": {"{{Mixed 1}}": "source_video", "{{Mixed 2}}": base},
            }
        )
    path = tmp_path / "all.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    headers = login(client, "admin", "admin@12345")
    writes = []

    def forward(request):
        if request.method != "GET":
            writes.append(request.url.path)
        response = client.request(
            request.method, str(request.url), content=request.content, headers=dict(request.headers)
        )
        if request.url.path.endswith("/identity/me"):
            body = response.json()
            body["data"]["permissions"] = ["asset:read", "template:read", "template:pack:manage"]
            return httpx.Response(200, json=body)
        return httpx.Response(response.status_code, content=response.content)

    with PackagingClient(
        "http://testserver/api/v1",
        headers["Authorization"][7:],
        transport=httpx.MockTransport(forward),
    ) as api:
        loaded = load_manifest(path)
        report = apply_manifest(loaded, api, tmp_path / "state.json")
        before = list(writes)
        assert apply_manifest(loaded, api, tmp_path / "state.json") == report
        assert writes == before
    assert len(writes) == 3
    assert all(path == "/api/v1/templates/video-templates" for path in writes)
    for result in report["functions"]:
        assert len(result["mediaOrder"]) == 4  # 原片+样例包全部三张图，只有前两项被点名。
        assert [item["inPrompt"] for item in result["mediaOrder"]] == [True, True, False, False]
        other = alternate[result["key"]]
        response = client.post(
            "/api/v1/templates/replacement-runs",
            headers=headers,
            json={
                "requestId": str(uuid4()),
                "sourceId": source["id"],
                "kind": result["key"],
                "videoTemplateId": result["videoTemplateId"],
                "packId": other.id,
                "packVersion": 1,
            },
        )
        assert response.status_code == 201, response.text
        run = response.json()["data"]
        assert run["prompt"] == data["functions"][0]["prompt"]
        actual = [item for item in run["inputSummary"]["assets"] if item["type"] == "image"]
        assert [item["id"] for item in actual] == expected_images[result["key"]]


def test_old_role_contract_is_rejected_before_any_write(tmp_path):
    from test_template_packager import Server, client_for

    server = Server()  # 有旧的防重契约，还没有用途绑定契约。
    with client_for(server) as api, pytest.raises(ValueError, match="用途绑定契约"):
        api.check_server(creating_packs=False, uploading=False, role_bindings=True)
    assert not server.writes
