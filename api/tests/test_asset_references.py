"""v1 素材引用检查的注入口：来源齐不齐、缺来源时删不删得掉。

注入口的协议（服务层确实问、描述原样透出）由 `test_asset_api` 用替身覆盖；
真实实现的行为在 `test_asset_reference_sources.py`（封装 head）与
`test_task_library_references.py`（任务存的是 v2 资产 id，走另一套）里。
这里只留两件跟「注入口本身」有关的事：

1. 声明集合与已注册集合零缺口；
2. 来源集合未知时**必须** 503 —— 这是唯一「中性默认会造成不可逆数据丢失」的
   注入口：空映射等于「没人引用、可以删」，漏登记一个来源就是永久裂图。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from test_asset_api import png_bytes  # noqa: F401  同一套造图工具

from core.ports.assets import (
    has_asset_reference_checker,
    reference_source_count,
    reference_source_gaps,
    reset_asset_reference_checker,
)


def _upload(client: TestClient, api: str, name: str) -> dict:
    response = client.post(
        f"{api}/assets/upload",
        content=png_bytes((20, 20), "orange"),
        params={"filename": name, "category": "character"},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _trash(client: TestClient, api: str, asset: dict):
    """引用拦截发生在「移入回收站」：进了回收站才发现被引用，用户已经看不到它了。"""
    return client.put(
        f"{api}/assets/{asset['id']}/deleted",
        json={"expectedVersion": asset["stateVersion"], "deleted": True},
    )


def test_checker_is_registered_at_startup(client: TestClient) -> None:
    """声明集合与注册集合零缺口，删除才被允许。

    旧断言只数「>= 2 个来源」：第三个来源漏登记时照样全绿，而失败方向是永久丢数据。
    """
    assert has_asset_reference_checker()
    assert reference_source_count() >= 2
    assert not reference_source_gaps(), reference_source_gaps()


def test_unregistered_checker_refuses_delete(client: TestClient, api: str) -> None:
    """fail-closed：来源集合未知时删除必须 503，不能静默放行。"""
    asset = _upload(client, api, "no-checker.png")
    reset_asset_reference_checker()
    response = _trash(client, api, asset)
    assert response.status_code == 503
    assert "引用检查不可用" in response.json()["message"]
