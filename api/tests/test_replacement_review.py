"""人工验收：评价版本、历史、权限与输入快照。

重建前评价写在 `review/review.json`，用一把 `REVIEW_LOCK` 文件锁防并发；现在是
`replacement_reviews` 一行 + 自己的 `version` 列做乐观锁（run 的 `version` 是
封装用的，别混）。判据没变：**旧版本号必须 409**、历史要留全、验收不改生成结果。

`reviewedBy/reviewedAt` 与 `history` 的 camelCase 形状是前端契约的一部分，
所以历史里存的也是 camelCase（`reasonCodes`），不是数据库列名。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests_template_helpers import (
    API,
    add_input,
    add_review,
    add_run,
    login,
    patch_run,
    read_run,
    workspace,
)

from infra.archive.local import get_archive_settings


@pytest.fixture
def saved_run(tmp_path, monkeypatch):
    """一条「已成功」的任务：有行、有工作区、有输入快照文件与成片。"""
    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))

    async def _make(*, status: str = "success", **fields) -> tuple[str, object]:
        run = await add_run(status=status, **fields)
        # 快照里的 url 指向**归档快照**接口（prepare 就是这么写的）：
        # 素材后来被删或换内容，评价页仍要能打开当初那张图。
        await patch_run(
            run.id,
            input_summary={
                "assets": [
                    {
                        "id": "image",
                        "name": "归档参考",
                        "role": "reference",
                        "type": "image",
                        "url": f"/api/v1/templates/replacement-runs/{run.id}/inputs/image",
                    }
                ],
                "notes": "保留原动作",
                "wardrobeMode": "preserve",
                "replacementScope": "",
                "coverReferenceId": "image",
            },
        )
        path = workspace(run.id, {"id": run.id, "owner_id": run.owner_id, "status": status})
        (path / "outputs/generated.mp4").write_bytes(b"original-result")
        (path / "inputs/reference-1.png").write_bytes(b"input-snapshot")
        await add_input(run.id, "image", position=0)
        return run.id, path

    return _make


async def test_review_revisions_persist_without_changing_generation(
    client: TestClient, saved_run
) -> None:
    uid, path = await saved_run()
    before = (path / "run.json").read_bytes()
    url = f"{API}/templates/replacement-runs/{uid}"

    first = client.put(url + "/review", json={"status": "accepted", "expectedVersion": 0})
    assert first.status_code == 200, first.text
    data = first.json()["data"]
    assert data["status"] == "success" and data["reviewStatus"] == "accepted"
    assert data["review"]["version"] == 1
    assert data["review"]["reviewedBy"] == (await read_run(uid)).owner_id
    assert data["review"]["reviewedAt"].endswith("Z")

    second = client.put(
        url + "/review",
        json={
            "status": "rejected",
            "expectedVersion": 1,
            "reasonCodes": ["visual"],
            "comment": "画面异常",
        },
    )
    assert second.status_code == 200, second.text
    # 换一个请求/进程读回来：评价必须已经落库，不是进程内状态
    row = await read_run(uid)
    assert row is not None and row.status == "success"
    restored = client.get(url).json()["data"]["review"]
    assert restored["status"] == "rejected" and restored["version"] == 2
    assert [item["status"] for item in restored["history"]] == ["pending", "accepted"]
    assert restored["history"][0]["reasonCodes"] == [], "历史要用 camelCase，与出参一致"

    # 验收不是重生成：成片与输入快照一个字节都不能变
    assert (path / "run.json").read_bytes() == before
    assert (path / "outputs/generated.mp4").read_bytes() == b"original-result"
    assert (path / "inputs/reference-1.png").read_bytes() == b"input-snapshot"


async def test_stale_review_conflicts_and_keeps_latest(client: TestClient, saved_run) -> None:
    uid, _ = await saved_run()
    url = f"{API}/templates/replacement-runs/{uid}/review"
    payload = {"status": "accepted", "expectedVersion": 0}
    assert client.put(url, json=payload).status_code == 200
    assert client.put(url, json=payload).status_code == 409
    detail = client.get(url.removesuffix("/review")).json()["data"]
    assert detail["review"]["version"] == 1


async def test_review_permissions_owner_and_input_snapshot(client: TestClient, saved_run) -> None:
    uid, _ = await saved_run()
    editor = login(client, "editor", "editor@12345")
    viewer = login(client, "viewer", "viewer@12345")
    base = f"{API}/templates/replacement-runs/{uid}"
    payload = {"status": "accepted", "expectedVersion": 0}

    # viewer 没有 template:review；editor 有权限但不是这条档案的发起人
    assert client.put(base + "/review", json=payload, headers=viewer).status_code == 403
    assert client.put(base + "/review", json=payload, headers=editor).status_code == 404
    assert client.get(base + "/inputs/image", headers=editor).status_code == 404
    assert client.get(base + "/inputs/image", headers=viewer).status_code == 404

    # 发起人读得到快照字节，且摘要里给的就是这个地址
    assert client.get(base + "/inputs/image").content == b"input-snapshot"
    summary = client.get(base).json()["data"]["inputSummary"]
    assert summary["assets"][0]["name"] == "归档参考"
    assert summary["assets"][0]["url"] == f"{base}/inputs/image"
    assert summary["notes"] == "保留原动作"
    assert client.get(summary["assets"][0]["url"]).content == b"input-snapshot"


async def test_snapshot_url_survives_asset_deletion(client: TestClient, saved_run) -> None:
    """快照地址指向归档副本，不指向素材：素材删了仍要能核对当初喂了什么。

    指向素材的症状：评价页在「素材被清理之后」变成一排碎图，而那条任务的
    成片还在，验收只能靠记忆。
    """
    uid, path = await saved_run()
    summary = client.get(f"{API}/templates/replacement-runs/{uid}").json()["data"][
        "inputSummary"
    ]
    assert "/assets/" not in summary["assets"][0]["url"]
    assert "/replacement-runs/" in summary["assets"][0]["url"]
    assert (path / "inputs/reference-1.png").is_file()


@pytest.mark.parametrize("status", ["running", "queued", "prepared"])
async def test_review_requires_finished_run(client: TestClient, saved_run, status: str) -> None:
    """成片没出来之前不能验收：那时候没有可比对的画面。"""
    uid, _ = await saved_run(status=status)
    response = client.put(
        f"{API}/templates/replacement-runs/{uid}/review",
        json={"status": "accepted", "expectedVersion": 0},
    )
    assert response.status_code == 409


async def test_failed_run_can_still_be_reviewed(client: TestClient, saved_run) -> None:
    """失败的任务也要能标记「这条不能用」：否则列表里永远挂着待验收。"""
    uid, _ = await saved_run(status="failed")
    response = client.put(
        f"{API}/templates/replacement-runs/{uid}/review",
        json={
            "status": "rejected",
            "expectedVersion": 0,
            "reasonCodes": ["other"],
            "comment": "重做",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["reviewStatus"] == "rejected"


async def test_legacy_review_row_is_preserved(client: TestClient, saved_run) -> None:
    """迁移进来的老档案已经有评价：出参要照实显示，别覆盖成 pending。"""
    uid, _ = await saved_run()
    await add_review(uid, status="accepted", version=1)
    data = client.get(f"{API}/templates/replacement-runs/{uid}").json()["data"]
    assert data["reviewStatus"] == "accepted"
    assert data["review"]["version"] == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "rejected", "expectedVersion": 0},  # 不可用必须给原因
        {"status": "accepted", "expectedVersion": -1},  # 版本号不能是负数
        {"status": "rejected", "expectedVersion": 0, "reasonCodes": ["invalid"]},
        {"status": "accepted", "expectedVersion": 0, "reasonCodes": ["visual"]},  # 可用不该有原因
        {"status": "rejected", "expectedVersion": 0, "reasonCodes": ["visual", "visual"]},
    ],
)
async def test_invalid_review_payload_rejected_by_contract(
    client: TestClient, saved_run, payload: dict
) -> None:
    """这些是契约校验（422），不是业务分支：前端按字段级错误提示。"""
    uid, _ = await saved_run()
    response = client.put(f"{API}/templates/replacement-runs/{uid}/review", json=payload)
    assert response.status_code == 422, response.text
    row = await read_run(uid)
    assert row is not None


async def test_review_on_missing_input_is_404(client: TestClient, saved_run) -> None:
    """快照清单里没有这条素材：404，不猜路径。"""
    uid, path = await saved_run()
    (path / "inputs/reference-1.png").unlink()
    assert client.get(f"{API}/templates/replacement-runs/{uid}/inputs/image").status_code == 404
    assert (
        client.get(f"{API}/templates/replacement-runs/{uid}/inputs/not-in-snapshot").status_code
        == 404
    )


async def test_success_without_output_cannot_be_reviewed(client: TestClient, saved_run) -> None:
    """成片被清理掉了还收评价：结论没有画面可依，事后无法复核。"""
    uid, path = await saved_run()
    (path / "outputs/generated.mp4").unlink()
    response = client.put(
        f"{API}/templates/replacement-runs/{uid}/review",
        json={"status": "accepted", "expectedVersion": 0},
    )
    assert response.status_code == 409
    assert "成片" in response.json()["message"]


async def test_history_is_capped(client: TestClient, saved_run) -> None:
    """评价可以反复改：历史也要封顶，否则这一行会被写成一本日志。

    旧实现截到 20 条。漏了上限的症状不是功能坏，而是这条记录越用越大，
    任务详情页每次都要把整段 JSON 传下来。
    """
    from modules.template.replacement_service import REVIEW_HISTORY_LIMIT

    uid, _ = await saved_run()
    url = f"{API}/templates/replacement-runs/{uid}/review"
    total = REVIEW_HISTORY_LIMIT + 2
    for version in range(total):
        response = client.put(url, json={"status": "accepted", "expectedVersion": version})
        assert response.status_code == 200, response.text

    review = client.get(f"{API}/templates/replacement-runs/{uid}").json()["data"]["review"]
    assert review["version"] == total
    assert len(review["history"]) == REVIEW_HISTORY_LIMIT
    # 保留的是**最近**的 N 条：最后一条是「这次之前」的那次结论
    assert review["history"][-1]["version"] == total - 1
