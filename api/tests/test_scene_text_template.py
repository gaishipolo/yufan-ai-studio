"""文字换场景：原片是唯一媒体，用户只编辑场景段落，发布后由替换链消费。

迁移自离线打包器时代的端到端用例：入口从 `scripts/package_video_template.py`
换成 `/template-manifests`（预检 → 保存 → 发布），断言对象从 CLI 的报告换成
**投影出来的模板行**与真实的替换准备出参。

两条口径与 CLI 时期一致、不能松：

1. 提示词只能引用视频槽位（`{{ScenePrompt}}` 与 `{{Mixed 1}}` 由服务端补），
   出现 `{{Mixed 2}}` / `{{Other}}` 一律预检拒绝；
2. 场景描述是「本次唯一可变项」：不选素材包、不带散图、不带替换范围。
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from library_support import admin_headers
from tests_template_helpers import upload, video_bytes

MANIFESTS = "/api/v1/template-manifests"

DEFAULT = (
    "将原视频场景替换为明亮雅致的高档酒店会客厅：暖米色墙面、浅色石材地面、木质装饰，"
    "远处少量沙发与花艺，整体简洁、有真实空间纵深，与三位女性的礼服穿搭自然适配。"
)
FIXED = (
    "保持三位女性原有的前后站位、走动路线、人物大小及镜头运动。"
    "保留三人的面容、发型、服装及手持产品。保留原视频时长、台词和各自音色。全片无字幕。"
)


def _spec(source_id: str, prompt: str) -> dict:
    return {
        "key": f"scene-{uuid4().hex[:8]}",
        "name": "场景模板",
        "video": {"assetId": source_id},
        "packs": [],
        "functions": [
            {
                "key": "scene",
                "name": "文字换场景",
                "kind": "scene",
                "scenePromptDefault": DEFAULT,
                "prompt": prompt,
            }
        ],
    }


@pytest.mark.parametrize("extra", ["{{Mixed 2}}", "{{Other}}", "{{Mixed 0}}"])
def test_text_scene_preflight_rejects_non_video_tokens(client, extra) -> None:
    headers = admin_headers(client)
    source = upload(client, "scene.mp4", "source", video_bytes(2, audio=True), headers=headers)
    response = client.post(
        f"{MANIFESTS}/validate",
        json=_spec(source["id"], "{{ScenePrompt}}\n" + extra),
        headers=headers,
    )
    assert response.status_code == 400, response.text


def test_scene_prompt_publishes_a_template_the_replacement_chain_can_use(client) -> None:
    headers = admin_headers(client)
    source = upload(client, "scene.mp4", "source", video_bytes(2, audio=True), headers=headers)
    spec = _spec(source["id"], "{{ScenePrompt}}\n\n" + FIXED + "\n")
    saved = client.post(MANIFESTS, json=spec, headers=headers)
    assert saved.status_code == 201, saved.text
    published = client.post(f"{MANIFESTS}/{spec['key']}/publish", headers=headers)
    assert published.status_code == 200, published.text
    template_id = published.json()["data"]["templates"][0]["templateId"]

    template = client.get(
        f"/api/v1/templates/video-templates/{template_id}", headers=headers
    ).json()["data"]
    # 文字换场景不绑包、只有一段原片；模式由 rules.inputMode 表达。
    assert template["packId"] is None
    assert len(template["mediaOrder"]) == 1
    assert template["mediaOrder"][0]["type"] == "video"
    assert template["mediaOrder"][0]["assetId"] == source["id"]
    assert template["rules"]["inputMode"] == "video_scene_text"
    assert template["rules"]["scenePromptDefault"] == DEFAULT
    # 收尾换行被契约层 strip 掉（与 packager 的文件提示词同一口径）。
    assert template["prompt"] == "{{ScenePrompt}}\n\n" + FIXED

    base = {"kind": "scene", "sourceLibraryId": source["id"], "videoTemplateId": template_id}
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
