"""文字换场景：原片为唯一媒体，用户仅编辑场景段落，不覆盖固定约束。"""

from __future__ import annotations

import json
from uuid import uuid4

import httpx
import pytest
from tests_template_helpers import login, upload, video_bytes
from video_template_support import get_template

from template_packager.client import PackagingClient
from template_packager.manifest import load_manifest
from template_packager.runner import apply_manifest

DEFAULT = (
    "将原视频场景替换为明亮雅致的高档酒店会客厅：暖米色墙面、浅色石材地面、木质装饰，"
    "远处少量沙发与花艺，整体简洁、有真实空间纵深，与三位女性的礼服穿搭自然适配。"
)
FIXED = (
    "保持三位女性原有的前后站位、走动路线、人物大小及镜头运动。"
    "保留三人的面容、发型、服装及手持产品。保留原视频时长、台词和各自音色。全片无字幕。"
)


@pytest.mark.parametrize("extra", ["{{Mixed 2}}", "{{Other}}", "{{Mixed 0}}"])
def test_text_scene_preflight_rejects_non_video_tokens(tmp_path, extra):
    config = {
        "key": "invalid-text-scene",
        "name": "场景模板",
        "video": {"assetId": "source"},
        "functions": [
            {
                "key": "scene",
                "name": "文字换场景",
                "kind": "scene",
                "scenePromptDefault": DEFAULT,
                "prompt": "{{ScenePrompt}}\n" + extra,
            }
        ],
    }
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        load_manifest(path)


def test_scene_prompt_only_flow_from_packaging_to_snapshot(client, tmp_path):
    source = upload(client, "scene-text.mp4", "source", video_bytes(2, audio=True))
    (tmp_path / "prompt.txt").write_text("{{ScenePrompt}}\n\n" + FIXED + "\n", encoding="utf-8")
    config = {
        "key": "hotel-scene-v1",
        "name": "现有视频",
        "video": {"assetId": source["id"]},
        "functions": [
            {
                "key": "scene-text",
                "name": "文字换场景",
                "kind": "scene",
                "scenePromptDefault": DEFAULT,
                "promptFile": "prompt.txt",
            }
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    loaded = load_manifest(path)
    headers = login(client, "admin", "admin@12345")
    writes = []

    def forward(request):
        if request.method != "GET":
            writes.append(request.url.path)
        response = client.request(
            request.method, str(request.url), content=request.content, headers=dict(request.headers)
        )
        return httpx.Response(response.status_code, content=response.content)

    with PackagingClient(
        "http://testserver/api/v1",
        headers["Authorization"][7:],
        transport=httpx.MockTransport(forward),
    ) as api:
        report = apply_manifest(loaded, api, tmp_path / "state.json")
        before = list(writes)
        assert apply_manifest(loaded, api, tmp_path / "state.json") == report
        assert writes == before
    assert writes == ["/api/v1/templates/video-templates"]
    item = report["functions"][0]
    assert item["requiredPackCount"] == 0
    template = get_template(item["videoTemplateId"])
    assert template.pack_id is None
    assert len(template.media_order) == 1 and template.media_order[0].type == "video"
    base = {"kind": "scene", "sourceId": source["id"], "videoTemplateId": template.id}
    changed = "将背景换成海边落地窗会客厅，保持真实透视与原有人物。"
    prepared = []
    for text in (None, changed):
        payload = {**base, "requestId": str(uuid4())}
        if text is not None:
            payload["scenePrompt"] = text
        response = client.post("/api/v1/templates/replacement-runs", json=payload, headers=headers)
        assert response.status_code == 201, response.text
        run = response.json()["data"]
        prepared.append(run["id"])
        expected = DEFAULT if text is None else text
        assert run["prompt"] == expected + "\n\n" + FIXED
        assert run["inputSummary"]["scenePrompt"] == expected
        assert [asset["id"] for asset in run["inputSummary"]["assets"]] == [source["id"]]
    assert prepared[0] != prepared[1]
    for invalid in ("", " ", "{{Mixed 2}} 改写", "{{ScenePrompt}}"):
        response = client.post(
            "/api/v1/templates/replacement-runs",
            headers=headers,
            json={**base, "requestId": str(uuid4()), "scenePrompt": invalid},
        )
        assert response.status_code == 400, response.text
    # U+0000 是合法 UTF-8，sqlite 也存得下，所以它必须由落盘那一关拦——
    # 提示词走 `encode_text`（所有文本写盘的唯一出口），在写任何字节之前抛 400。
    # 少了那一关，这里会 201 并留下一条 PG 上必炸（`value too long`/NUL）的档案。
    response = client.post(
        "/api/v1/templates/replacement-runs",
        headers=headers,
        json={**base, "requestId": str(uuid4()), "scenePrompt": "海边落地窗\x00会客厅"},
    )
    assert response.status_code == 400, response.text
    assert "U+0000" in response.json()["message"]
    # 不能在该模式夹带散图、包或额外人物。
    response = client.post(
        "/api/v1/templates/replacement-runs",
        headers=headers,
        json={**base, "requestId": str(uuid4()), "referenceIds": ["foreign"]},
    )
    assert response.status_code == 400
