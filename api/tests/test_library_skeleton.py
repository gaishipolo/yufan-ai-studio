"""资产库骨架：契约与 fail-closed。

这份文件守的是**跨批次不变的那几件事**，与具体业务行为无关：

1. **契约**：权限点集合、错误码编码、空页字段齐全；
2. **注入口**：三个 v2 注入口未注册时 fail-closed（返回中性值等于静默放行）；
3. **装配**：模块被自动发现，路由挂在 `/api/v1/library` 下，且 v1 的 `/assets`
   一个都没回来（`test_v1_asset_library_is_gone` 另有源码级守卫）。

业务行为（上传、共享、删除矩阵、状态机）在 M1–M3 各自的测试文件里，不放这里。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from api.discovery import active_modules
from core.exceptions import AppError, Unavailable
from core.permissions import catalog
from core.ports.library_ingest import get_library_ingest, reset_library_ingest
from core.ports.library_lookup import get_library_lookup, reset_library_lookup
from core.ports.library_references import (
    require_library_references,
    reset_library_references,
)
from modules.library import errors as library_errors

#: 已接入 `ModuleSpec` 的权限点（M3 完成态）。清单必须与真实判定点同步：
#: `test_permission_coverage` 不允许「声明了但没人用」的僵尸权限点，
#: 也不允许「服务层判了但没声明」（那样普通用户永远 403）。
LIBRARY_PERMISSIONS = {
    "library:read",
    "library:create",
    "library:update",
    "library:delete",
    "library:delete:any",
    "library:draft:review",
    "library:share",
    "library:share:direct",
    "library:share:approve",
    "library:share:approve:team",
    "library:share:approve:group",
    "library:tag:manage",
    "library:generation:ingest",
}


def test_library_module_is_discovered_with_its_permissions() -> None:
    spec = next((item for item in active_modules() if item.name == "library"), None)
    assert spec is not None, "modules/library 没有导出 module: ModuleSpec"
    assert spec.permission_codes() == frozenset(LIBRARY_PERMISSIONS)
    assert spec.router.routes, "模块必须至少有一条路由（架构守卫会拦空模块）"


def test_library_permissions_are_in_the_catalog() -> None:
    declared = set(catalog.codes())
    missing = sorted(LIBRARY_PERMISSIONS - declared)
    assert not missing, f"权限点没有进全局目录：{missing}"


@pytest.fixture(autouse=True)
def _restore_ports() -> Iterator[None]:
    """用例里 reset 过的注入口，跑完要按真实实现装回去。

    引用检查是 fail-closed 的：它一旦空着，后续所有「彻底删除」用例都会 503，
    而失败信息读起来像「purge 坏了」。恢复动作放在这里，而不是让每个用例
    自己 finally——漏写一次会污染整场。
    """
    yield
    from core.ports.library_ingest import (
        has_library_ingest,
        register_library_ingest,
    )
    from core.ports.library_lookup import has_library_lookup
    from core.ports.library_references import bind_library_reference_sources
    from modules.library.ingest import SqlLibraryIngest
    from modules.library.lookup import install as install_lookup

    # 只在「被本用例清空过」时补装：注入口不允许重复注册（那是配置错误），
    # 无条件补装会把每个用例的 teardown 都变成 RegistryError。
    if not has_library_lookup():
        install_lookup()
    if not has_library_ingest():
        register_library_ingest(SqlLibraryIngest())

    # 引用来源是「多模块声明」的，必须按**全部**模块的声明补装，不能只补 library：
    # `reset_library_references()` 会把 image 等模块在 import 期登记的来源一起清掉，
    # 只补自己那份会让后面所有删除用例 503（表现为「purge 坏了」，极难定位）。
    for spec in active_modules():
        if spec.library_reference_sources:
            bind_library_reference_sources(spec.name, spec.library_reference_sources)


def test_library_list_returns_the_contract_shape(client: TestClient, api: str) -> None:
    """M1 起列表可能非空，所以用「必然匹配不到」的关键词保证空页。

    这里守的是**形状**：分面字段一个不能少（前端侧栏角标直接读它），
    而不是「库一定是空的」。
    """
    response = client.get(f"{api}/library/assets", params={"keyword": "__no_such_asset__"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["items"] == []
    assert data["total"] == 0
    assert set(data["facets"]) == {"all", "personal", "group", "team", "drafts", "trash"}


def test_library_list_accepts_the_contract_query_params(client: TestClient, api: str) -> None:
    """M0 空实现也必须收下正式参数，否则 M1 接表时契约会再变一次。"""
    response = client.get(
        f"{api}/library/assets",
        params={
            "library": "group",
            "state": "draft",
            "kind": "image",
            "tagIds": ["t1", "t2"],
            "keyword": "圣诞",
            "sort": "-createdAt",
            "page": 2,
            "pageSize": 12,
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["page"] == 2
    assert data["pageSize"] == 12


def test_library_facets_endpoint(client: TestClient, api: str) -> None:
    """只断言形状与类型：测试库是 session 级共享的，前面用例会留下草稿与回收站条目。

    管理员（`*`）的角标是全量的，所以更不能断言「一定是 0」。
    """
    response = client.get(f"{api}/library/facets")
    assert response.status_code == 200
    data = response.json()["data"]
    assert set(data) == {"all", "personal", "group", "team", "drafts", "trash"}
    assert all(isinstance(value, int) and value >= 0 for value in data.values())


def test_library_paths_are_registered_in_openapi(client: TestClient, api: str) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/library/assets" in paths
    assert "/api/v1/library/facets" in paths
    assert "/api/v1/assets" not in paths, "v1 素材接口已下线"


@pytest.mark.parametrize(
    ("reset", "getter"),
    [
        (reset_library_lookup, get_library_lookup),
        (reset_library_ingest, get_library_ingest),
        # 引用检查没有单例 getter 了（来源是「声明 + 实现」两表对账），
        # 用同步前置断言 `require_library_references()` 当探针，语义一模一样。
        (reset_library_references, require_library_references),
    ],
    ids=["lookup", "ingest", "references"],
)
def test_v2_ports_are_fail_closed(
    reset: Callable[[], None], getter: Callable[[], object]
) -> None:
    """未注册时必须抛 503，不能返回空值。

    三个都在 M1/M2 接上了实现，但「实现被漏装配」仍然会发生（模块没被发现、
    import 失败）。这里临时清空注册表，模拟的正是那种状态。
    """
    reset()
    with pytest.raises(Unavailable):
        getter()


def test_error_codes_encode_their_http_status() -> None:
    classes = [
        library_errors.SharedAssetNotDeletable,
        library_errors.TeamDeleteForbidden,
        library_errors.ShareRevokePending,
        library_errors.DraftNotReviewable,
        library_errors.AssetNotInTrash,
        library_errors.TrashEntryReferenced,
        library_errors.RevokeReasonRequired,
        library_errors.DraftReviewForbidden,
    ]
    for cls in classes:
        assert issubclass(cls, AppError)
        assert cls.code // 100 == cls.http_status, f"{cls.__name__} 的编码与状态码不符"
        assert cls.code % 100 // 10 == 8, f"{cls.__name__} 不在 slot 8"
