"""模板「允许选用的模型清单」：作者声明 → 发布快照 → 使用者挑选。

三条必须钉死：

1. **清单里的模型必须在目录里** —— 与「发布时该模型必须在目录里存在」同一口径。
   清单是给使用者看的选项，里面出现一个不存在的模型，使用者挑到它才 400，
   那时素材都填完了。
2. **清单随发布快照走** —— 使用者读的是版本，不是草稿。草稿改了清单没发版，
   使用者看到的仍是旧清单。
3. **清单为空 = 只有固定模型** —— 老模板继续能用（兼容性的底线）。

另外两条边界：清单里的每一款模型都要能承接作者写的参数与参考素材
（挑到哪一款都不该被拒），以及使用者挑了一个不在清单里的模型时**当场 400**
（静默换成固定模型 = 用户选的东西没生效，而页面看起来一切正常）。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from library_support import LIB_RW, admin_headers, make_user, suffix

API = "/api/v1/template-specs"
PERMS = [
    "template_pack:read",
    "template_pack:create",
    "template_pack:publish",
    "catalog:read",
    *LIB_RW,
]
#: 固定模型与清单里的另一款：两者都声明 durationSec / aspectRatio，参数互不打架。
MODEL = "libtv-wan3-replacement"
OTHER_MODEL = "bailian-wan3-replacement"
#: 生图模型：只声明 size / count，作者写的 durationSec 它不认。
IMAGE_MODEL = "agnes-image-2.5-flash"
#: 纯文生视频模型：`max_references = 0`，接不了需要编号的槽位。
TEXT_ONLY_MODEL = "agnes-video-2.5-flash"

#: 用户填的图片槽位必须带一级标签（发布前校验）。
_TAG = "tag-allowed-models"


def _headers(client: TestClient) -> dict[str, str]:
    admin = admin_headers(client)
    _, headers = make_user(client, admin, PERMS)
    return headers


def _draft(**over: Any) -> dict[str, Any]:
    """一份纯提示词模板：没有槽位，所以这些用例不必上传素材（快，也少依赖）。"""
    draft: dict[str, Any] = {
        "key": f"models-{suffix()}",
        "name": "模型清单演示",
        "model": MODEL,
        "params": {"durationSec": 6, "aspectRatio": "9:16"},
        "prompt": "一只猫在窗台上打哈欠",
        "slots": [],
    }
    draft.update(over)
    return draft


def _draft_with_slot(**over: Any) -> dict[str, Any]:
    """带一个「用户填的图片」槽位的模板（用户槽位不需要素材，但要有标签）。"""
    draft = _draft(**over)
    draft["slots"] = [
        {
            "key": "hero",
            "type": "image",
            "binding": "user",
            "tagId": _TAG,
            "minCount": 1,
            "maxCount": 1,
            "position": 0,
            "label": "主图",
        }
    ]
    return draft


def _publish(client: TestClient, headers: dict[str, str], draft: dict[str, Any]):
    saved = client.post(API, json=draft, headers=headers)
    assert saved.status_code == 200, saved.text
    published = client.post(f"{API}/{draft['key']}/publish", headers=headers)
    assert published.status_code == 200, published.text
    return published


def test_allowed_model_must_exist_in_the_catalog(client: TestClient) -> None:
    """清单里的模型必须真的在目录里 —— 与固定模型同一套口径。

    不查目录的后果：使用者从清单里挑了第三款，点生成才 400，
    而那时素材都填完了、错因还跟作者八竿子打不着。
    """
    headers = _headers(client)
    draft = _draft(allowedModels=["not-a-real-model"])
    response = client.post(f"{API}/validate", json=draft, headers=headers)
    assert response.status_code == 400, response.text
    assert "不存在或未启用" in response.json()["message"]
    assert "not-a-real-model" in response.json()["message"]


def test_published_version_carries_the_allowed_models(client: TestClient) -> None:
    """清单进**发布快照**：使用者读的是版本，不是草稿。"""
    headers = _headers(client)
    draft = _draft(allowedModels=[OTHER_MODEL])
    _publish(client, headers, draft)

    detail = client.get(f"{API}/{draft['key']}", headers=headers).json()["data"]
    assert detail["latest"]["allowedModels"] == [OTHER_MODEL]
    # 草稿里也在（作者看得见自己声明了什么）
    assert detail["draft"]["allowedModels"] == [OTHER_MODEL]


def test_compile_uses_the_model_the_user_picked(client: TestClient) -> None:
    """使用者在清单里挑了哪一款，编译出来的请求就用哪一款。"""
    headers = _headers(client)
    draft = _draft(allowedModels=[OTHER_MODEL])
    _publish(client, headers, draft)

    body = client.post(
        f"{API}/{draft['key']}/compile",
        json={"slotValues": {}, "model": OTHER_MODEL},
        headers=headers,
    )
    assert body.status_code == 200, body.text
    assert body.json()["data"]["model"] == OTHER_MODEL


def test_compile_rejects_a_model_outside_the_allowed_list(client: TestClient) -> None:
    """挑了不在清单里的模型 → 当场 400。

    静默换成固定模型更糟：用户选的东西没生效，而页面看起来一切正常
    （模型名还显示着他选的那款）。
    """
    headers = _headers(client)
    draft = _draft(allowedModels=[OTHER_MODEL])
    _publish(client, headers, draft)

    body = client.post(
        f"{API}/{draft['key']}/compile",
        json={"slotValues": {}, "model": TEXT_ONLY_MODEL},
        headers=headers,
    )
    assert body.status_code == 400, body.text
    assert "不在该模板允许的清单里" in body.json()["message"]


def test_spec_without_allowed_models_behaves_like_before(client: TestClient) -> None:
    """清单为空 = 只有固定模型（老模板的行为，一条都不变）。"""
    headers = _headers(client)
    draft = _draft()
    _publish(client, headers, draft)

    detail = client.get(f"{API}/{draft['key']}", headers=headers).json()["data"]
    assert detail["latest"]["allowedModels"] == []

    body = client.post(
        f"{API}/{draft['key']}/compile", json={"slotValues": {}}, headers=headers
    )
    assert body.status_code == 200, body.text
    assert body.json()["data"]["model"] == MODEL


def test_allowed_model_must_support_the_authored_params(client: TestClient) -> None:
    """清单里的每一款都要能承接作者写的参数 —— 挑到哪一款都不该被拒。"""
    headers = _headers(client)
    draft = _draft(allowedModels=[IMAGE_MODEL])
    response = client.post(f"{API}/validate", json=draft, headers=headers)
    assert response.status_code == 400, response.text
    assert "不支持参数" in response.json()["message"]
    assert IMAGE_MODEL in response.json()["message"]


def test_allowed_model_must_accept_reference_slots(client: TestClient) -> None:
    """清单里不能放接不了参考素材的模型：有编号槽位时它跑不起来。"""
    headers = _headers(client)
    draft = _draft_with_slot(allowedModels=[TEXT_ONLY_MODEL])
    response = client.post(f"{API}/validate", json=draft, headers=headers)
    assert response.status_code == 400, response.text
    assert "不接受参考素材" in response.json()["message"]


def test_model_with_too_few_reference_slots_is_rejected(client: TestClient) -> None:
    """「最多接 4 张」的模型配 5 个槽位：发布时就要拦下来。

    以前只判 `max_references == 0`，这类「接得下、但接不全」的组合照发不误 ——
    作者以为发出去了，使用者在模板库填满 5 张才被 400 打回，那是两头都解释不通
    的时刻。
    """
    headers = _headers(client)
    draft = _draft(model=IMAGE_MODEL, params={})
    draft["slots"] = [
        {
            "key": f"image_{index}",
            "type": "image",
            "binding": "user",
            "tagId": _TAG,
            "minCount": 1,
            "maxCount": 1,
            "position": index,
            "label": f"图 {index}",
        }
        for index in range(5)
    ]
    response = client.post(f"{API}/validate", json=draft, headers=headers)
    assert response.status_code == 400, response.text
    assert "最多接 4 张" in response.json()["message"]


def test_allowed_list_is_part_of_the_version_fingerprint(client: TestClient) -> None:
    """改了清单 = 改了模板 = 新版本（旧版本保持发布那一刻的样子）。"""
    headers = _headers(client)
    draft = _draft(allowedModels=[OTHER_MODEL])
    _publish(client, headers, draft)

    changed = dict(draft, allowedModels=[OTHER_MODEL, MODEL])
    saved = client.post(API, json=changed, headers=headers)
    assert saved.status_code == 200, saved.text
    published = client.post(f"{API}/{draft['key']}/publish", headers=headers)
    assert published.status_code == 200, published.text
    assert published.json()["data"]["version"] == 2

    detail = client.get(f"{API}/{draft['key']}", headers=headers).json()["data"]
    assert detail["latest"]["allowedModels"] == [OTHER_MODEL, MODEL]
