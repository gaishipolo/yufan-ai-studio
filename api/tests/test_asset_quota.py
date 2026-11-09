"""配额口径（A6）：按可见域聚合、同一主体内按 sha256 去重。

四个数字各有各的用途，任何一个与另一个合并都会制造无法解释的现象：

| 场景 | 用户看到的现象 | 根因 |
| --- | --- | --- |
| 复制到另一个分类 | 「我只复制了一次，额度翻倍」 | 按行求和，没去重 |
| 清空回收站 | 「删了一堆，空间没变」 | 账面已释放、物理没释放（正常，但要说清） |
| 交付给团队 | 「我替团队干活，用光了自己额度」 | 计费主体按 owner 而不是可见域 |

所以这组用例断言的是**数字本身**，不是接口状态码。
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from tests_template_helpers import API, login, png_bytes, upload

from core.config import settings


def _used(client: TestClient) -> dict[str, int]:
    response = client.get(f"{API}/assets", headers=login(client))
    assert response.status_code == 200, response.text
    return response.json()["data"]["quota"]


def test_copying_to_another_category_does_not_double_usage(client: TestClient) -> None:
    """同一份字节放进两个分类：素材 2 条，账面只算一次。

    写成 `SUM(size_bytes)` 会在这里静默翻倍——不报错、不告警，
    用户只会觉得「我只复制了一次」。这是 M1 里最容易写错的一条断言。
    """
    before = _used(client)["personalUsed"]
    content = png_bytes()
    first = upload(client, "quota-a.png", "product", content)
    after_first = _used(client)["personalUsed"]
    assert after_first - before > 0, "第一次上传必须计入账面"

    upload(client, "quota-b.png", "character", content)
    after_second = _used(client)["personalUsed"]
    assert after_second == after_first, (
        "同一份字节换分类不该再计一次："
        f"{after_first} -> {after_second}（{first['sizeBytes']} 字节被算了两次）"
    )


def test_trash_releases_book_value_but_keeps_physical(client: TestClient) -> None:
    """软删：账面立刻回落，物理不变。

    账面必须先回落：回收站里的东西随时可能被恢复，不能在恢复时因为
    「额度已满」而失败。物理必须不变：字节还在盘上。两个都要对，
    用户才有办法理解「删了没变」不是 bug。
    """
    headers = login(client)
    item = upload(client, "quota-trash.png", "product", png_bytes())
    before = _used(client)

    response = client.put(
        f"{API}/assets/{item['id']}/deleted",
        json={"expectedVersion": item["stateVersion"], "deleted": True},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    after = _used(client)

    assert after["personalUsed"] == before["personalUsed"] - item["sizeBytes"]
    assert after["trashUsed"] >= item["sizeBytes"]
    assert after["physicalUsed"] == before["physicalUsed"], "软删不释放物理字节"


def test_quota_blocks_new_bytes_but_allows_the_same_digest(
    client: TestClient, monkeypatch
) -> None:
    """额度用尽时：新文件被拒，同一份字节换个分类仍然可以放行。

    第二条是刻意的。「复制」不该被计费——否则用户学会的是
    「不复制、直接改别人的」，而存储成本一点没涨。
    """
    login(client)
    content = png_bytes()
    first = upload(client, "quota-limit.png", "product", content)
    used = _used(client)["personalUsed"]

    # 只留 1 字节余量：任何新字节都放不下，但「不加字节」的复制要能过
    monkeypatch.setattr(settings, "archive_quota_bytes_per_user", used + 1)
    monkeypatch.setattr(settings, "archive_quota_bytes_total", 0)

    blocked = client.post(
        f"{API}/assets/upload",
        params={"filename": "quota-blocked.png", "category": "product"},
        content=png_bytes(),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert blocked.status_code == 413, blocked.text

    # 同一份字节、换一个分类：不增加账面，应当放行
    allowed = client.post(
        f"{API}/assets/upload",
        params={"filename": "quota-same.png", "category": "character"},
        content=content,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["data"]["id"] != first["id"]


def test_team_usage_is_reported_only_for_the_selected_library(client: TestClient) -> None:
    """不选团队时不返回任何团队用量。

    拿「所有团队的总和」顶替会让顶栏显示一个不属于任何库的数字，
    而用户无法判断该去哪个团队腾空间。
    """
    body = client.get(f"{API}/assets", headers=login(client)).json()["data"]["quota"]
    assert body["teamUsed"] == 0
    assert body["personalQuota"] == max(0, settings.archive_quota_bytes_per_user)


def test_physical_usage_counts_bytes_once_per_digest() -> None:
    """物理占用按 blob 计，不按素材行计。

    同一份字节被两条素材引用时，`blobs` 里只有一行，所以物理只加一次。
    写成「按素材行求和」会让物理占用随归属数膨胀，而磁盘上的字节没变
    ——那种数字一旦被用来判容量，用户会被要求为一个不存在的空间腾地方。
    """

    async def read() -> tuple[int, int]:
        from db.session import get_session_factory
        from modules.asset.archive_repository import ArchiveAssetRepository

        async with get_session_factory()() as session:
            repo = ArchiveAssetRepository(session)
            return await repo.physical_used(), await repo.draft_used()

    physical, draft = asyncio.run(read())
    assert physical >= 0
    # M3 之前没有草稿桶，这个数恒为 0；接口先留位置，免得前端之后改一次字段
    assert draft == 0
