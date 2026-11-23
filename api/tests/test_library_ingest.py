"""生成入库：落草稿箱、幂等、权限、来源校验。

五个断言方向：

1. **落点固定**：`state=draft` / `visibility=personal` / `origin=generated`，
   入参给不了这些字段；
2. **快照冻结**：`generation` 原样存进 `library_asset_generation`，详情页读回来
   不需要联表；
3. **幂等两种命中**：同一个 `sourceKey`、或同一份字节已在该 owner 的库里，
   都返回 `created=false` 且不产生第二行；
4. **权限单列**：只有 `library:generation:ingest` 能调，`library:create` 不够；
5. **拒绝只存外部链接**：没有 `storageKey` 也没有 `sourceUrl` 一律 400。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from library_support import (
    LIB_RW,
    admin_headers,
    make_user,
    png_bytes,
    put_blob,
    suffix,
    upload_image,
)

from core.exceptions import BadRequest
from modules.library.ingest import _snapshot_values


def _ingest(client: TestClient, api: str, headers: dict[str, str], body: dict):
    return client.post(f"{api}/library/assets/from-generation", json=body, headers=headers)


def _fresh_blob(name: str, color: str, size: tuple[int, int] = (32, 32)) -> str:
    """一份「还没被谁登记过」的字节。入库的新建路径只能用它测。

    `size` 可改：整组测试共用一个库，纯色 PNG 只按颜色就有 sha256 撞车风险，
    撞了会命中「同 owner 秒传」而让新建路径的用例拿到 `created=False`。
    """
    return put_blob(f"library/blobs/gen/{name}", png_bytes(color, size))


def _storage_key(client: TestClient, api: str, headers: dict[str, str], asset_id: str) -> str:
    detail = client.get(f"{api}/library/assets/{asset_id}", headers=headers).json()["data"]
    return detail["archive"]["storageKey"]


def _ingest_via_port(
    *,
    owner_id: str,
    source_key: str,
    storage_key: str = "",
    archive_key: str = "",
    source_url: str = "",
    name: str = "",
    task_id: str = "",
    generation: dict | None = None,
    tag_ids: list[str] | None = None,
) -> dict:
    """走 `library-ingest` 端口（受信任的服务端调用方那条路径）。

    HTTP 端点的 `storageKey` 现在只认「调用方自己名下的素材行」，而生成侧的
    产物在入库那一刻还不是素材行——那条路径由端口承担（`trusted=True`）。
    """
    import asyncio

    from core.ports.library_ingest import IngestRequest
    from core.ports.library_lookup import LibraryAssetKind
    from db.session import get_session_factory
    from modules.library.ingest import SqlLibraryIngest

    request = IngestRequest(
        source_module="image",
        source_task_id=task_id or source_key,
        source_key=source_key,
        kind=LibraryAssetKind.IMAGE,
        owner_id=owner_id,
        name=name,
        storage_key=storage_key,
        archive_key=archive_key,
        source_url=source_url,
        generation=generation or {},
        tag_ids=tag_ids or [],
    )

    async def run():
        async with get_session_factory()() as session:
            return await SqlLibraryIngest().ingest_generated(session, request)

    result = asyncio.run(run())
    return {"assetId": result.asset_id, "created": result.created}


def test_from_generation_lands_in_draft_with_snapshot(
    client: TestClient, api: str
) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW + ["library:generation:ingest"])
    uploaded = upload_image(client, api, owner_h, name="ref.png", color="mediumseagreen")
    key = _fresh_blob("land.png", "seagreen")

    created = _ingest_via_port(
        owner_id=owner["id"],
        source_key="image:job-42:0",
        task_id="job-42",
        storage_key=key,
        name="生成结果.png",
        generation={
            "model": "seedream",
            "model_id": "seedream-3",
            "prompt": "一只戴帽子的猫，工作室白底",
            "size": "1024x1024",
            "count": 4,
            "seed": "12345",
            "reference_library_ids": [uploaded["id"]],
            "configuration": {"steps": 28},
            "credits": 3.5,
        },
    )
    assert created["created"] is True

    detail = client.get(
        f"{api}/library/assets/{created['assetId']}", headers=owner_h
    ).json()["data"]
    assert detail["state"] == "draft"
    assert detail["visibility"] == "personal"
    assert detail["origin"] == "generated"
    assert detail["name"] == "生成结果.png"
    generation = detail["generation"]
    assert generation["prompt"] == "一只戴帽子的猫，工作室白底"
    assert generation["model"] == "seedream"
    assert generation["seed"] == "12345"
    assert generation["configuration"] == {"steps": 28}
    assert generation["sourceModule"] == "image"
    assert generation["sourceTaskId"] == "job-42"
    # v1 下线后只剩一列：`referenceLibraryIds` 就是它，不再有「另一列是不是空的」。
    assert generation["referenceLibraryIds"] == [uploaded["id"]]

    # 草稿只出现在草稿箱，不进个人库也不进选择器
    personal = client.get(
        f"{api}/library/assets", params={"library": "personal"}, headers=owner_h
    ).json()["data"]
    assert created["assetId"] not in [item["id"] for item in personal["items"]]


def test_from_generation_is_idempotent_for_same_source_key(
    client: TestClient, api: str
) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW + ["library:generation:ingest"])
    key = _fresh_blob("dup.png", "darkorange")
    fields = {
        "owner_id": owner["id"],
        "source_key": "template:run-7",
        "task_id": "run-7",
        "storage_key": key,
        "name": "模板结果.png",
    }

    first = _ingest_via_port(**fields)
    assert first["created"] is True
    second = _ingest_via_port(**fields)
    assert second["created"] is False, "命中幂等不是错误，是「已经有了」"
    assert second["assetId"] == first["assetId"]

    drafts = client.get(
        f"{api}/library/assets", params={"state": "draft"}, headers=owner_h
    ).json()["data"]
    matches = [item for item in drafts["items"] if item["id"] == first["assetId"]]
    assert len(matches) == 1, "第二次入库不能产生第二行草稿"


def test_from_generation_dedupes_identical_bytes(client: TestClient, api: str) -> None:
    """内容相同的第二次生成不新建草稿：与上传的秒传同一口径。

    `uq_library_assets_owner_sha_kind` 是数据库级唯一约束，入库不走这套判定
    就会在第二次生成同样字节时撞 500——那是最难从日志里看出原因的一种失败。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW + ["library:generation:ingest"])
    uploaded = upload_image(client, api, owner_h, name="same.png", color="rebeccapurple")
    key = _storage_key(client, api, owner_h, uploaded["id"])

    first = _ingest(
        client,
        api,
        owner_h,
        {
            "sourceModule": "task",
            "sourceTaskId": "task-1",
            "sourceKey": "task:task-1",
            "kind": "image",
            "storageKey": key,
        },
    )
    second = _ingest(
        client,
        api,
        owner_h,
        {
            "sourceModule": "task",
            "sourceTaskId": "task-2",
            "sourceKey": "task:task-2",
            "kind": "image",
            "storageKey": key,
        },
    )
    assert second.status_code == 200
    assert second.json()["data"]["assetId"] == first.json()["data"]["assetId"]


# --------------------------------------------------------------------------- #
# 存储键的归属（B1）
#
# storageKey / archiveKey 是**调用方给的对象存储地址**。不校验归属，这个
# 端点就是一个「任意对象读取」原语：主存储里装的是所有人的私有原片，知道 key
# 就能把别人的字节读成自己名下的素材，随后 GET /assets/{id}/file 就把它拿走。
# --------------------------------------------------------------------------- #


def test_http_ingest_refuses_a_storage_key_belonging_to_somebody_else(
    client: TestClient, api: str
) -> None:
    """别人的素材键不能拿来做自己的入库。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW + ["library:generation:ingest"])
    intruder, intruder_h = make_user(client, admin, LIB_RW + ["library:generation:ingest"])

    uploaded = upload_image(client, api, owner_h, name="private.png", color="maroon")
    key = _storage_key(client, api, owner_h, uploaded["id"])

    response = _ingest(
        client,
        api,
        intruder_h,
        {
            "sourceModule": "image",
            "sourceTaskId": "steal",
            "sourceKey": "image:steal",
            "kind": "image",
            "storageKey": key,
        },
    )
    assert response.status_code == 403, response.text
    assert response.json()["code"] == 40383


def test_http_ingest_refuses_a_key_that_is_not_registered_to_any_asset(
    client: TestClient, api: str
) -> None:
    """库里没有对应行的键同样不能读——那正是「猜到 key 就能拿走」的另一半。

    生成侧的中间产物、别的模块的对象都不在资产表里；它们在主存储里照样是
    有效地址。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW + ["library:generation:ingest"])
    key = _fresh_blob("loose.png", "teal")

    response = _ingest(
        client,
        api,
        owner_h,
        {
            "sourceModule": "image",
            "sourceTaskId": "loose",
            "sourceKey": "image:loose",
            "kind": "image",
            "storageKey": key,
        },
    )
    assert response.status_code == 403, response.text


def test_http_ingest_refuses_an_archive_key(client: TestClient, api: str) -> None:
    """归档键只对内部调用方开放：归档盘是另一个驱动，没有资产行可反查归属。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW + ["library:generation:ingest"])

    response = _ingest(
        client,
        api,
        owner_h,
        {
            "sourceModule": "template",
            "sourceTaskId": "run-archive",
            "sourceKey": "template:run-archive",
            "kind": "image",
            "archiveKey": "runs/whatever/outputs/generated.mp4",
        },
    )
    assert response.status_code == 403, response.text


def test_http_ingest_accepts_the_callers_own_storage_key(
    client: TestClient, api: str
) -> None:
    """自己名下的键照常能用（走内容去重，命中已有那条）。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW + ["library:generation:ingest"])
    uploaded = upload_image(client, api, owner_h, name="mine.png", color="orange")
    key = _storage_key(client, api, owner_h, uploaded["id"])

    response = _ingest(
        client,
        api,
        owner_h,
        {
            "sourceModule": "image",
            "sourceTaskId": "mine-1",
            "sourceKey": "image:mine-1",
            "kind": "image",
            "storageKey": key,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["assetId"] == uploaded["id"]
    assert response.json()["data"]["created"] is False


def test_port_ingest_accepts_a_fresh_storage_key(client: TestClient) -> None:
    """端口是受信任的服务端调用方：键是它自己刚写进去的，归属由它负责。"""
    admin = admin_headers(client)
    owner, _ = make_user(client, admin, LIB_RW + ["library:generation:ingest"])
    key = _fresh_blob("port.png", "olive")

    created = _ingest_via_port(
        owner_id=owner["id"],
        source_key="image:port-1",
        storage_key=key,
        name="端口入库.png",
    )
    assert created["created"] is True


# --------------------------------------------------------------------------- #
# 幂等键按人隔离（B5）
# --------------------------------------------------------------------------- #


def test_ingest_source_key_is_owner_scoped(client: TestClient) -> None:
    """同一个 sourceKey 被两个用户使用时，后一个不能拿到前一个的资产 id。

    uq_library_assets_source_key 是**全局**唯一索引：只按 key 查的话，B 的
    入库会命中 A 的行——B 的字节从未入库，返回的却是 A 的资产 id（还是
    200 + created=false，调用方会以为已经存好了）。命中但 owner 不一致时必须
    409。
    """
    import pytest

    from core.exceptions import Conflict

    admin = admin_headers(client)
    first, _ = make_user(client, admin, LIB_RW + ["library:generation:ingest"])
    second, _ = make_user(client, admin, LIB_RW + ["library:generation:ingest"])

    key = _fresh_blob("scoped.png", "navy")
    created = _ingest_via_port(
        owner_id=first["id"], source_key="image:shared-key", storage_key=key
    )
    assert created["created"] is True

    other_key = _fresh_blob("scoped-2.png", "olive")
    with pytest.raises(Conflict) as excinfo:
        _ingest_via_port(
            owner_id=second["id"],
            source_key="image:shared-key",
            storage_key=other_key,
        )
    assert excinfo.value.http_status == 409
    assert excinfo.value.details == {"sourceKey": "image:shared-key"}


# --------------------------------------------------------------------------- #
# storageKey 分支必须流式落盘（B11）
# --------------------------------------------------------------------------- #


def test_storage_key_branch_streams_the_object_instead_of_loading_it(
    client: TestClient,
) -> None:
    """远端驱动下不能把整份对象读进内存：峰值是「并发数 × 文件大小」。

    成片允许 300MB，而体积上限的判断发生在整份读进内存**之后**，拦不住峰值。
    """
    import asyncio

    from core.ports.library_ingest import IngestRequest
    from core.ports.library_lookup import LibraryAssetKind
    from db.session import get_session_factory
    from modules.library.ingest import GenerationIngest

    admin = admin_headers(client)
    owner, _ = make_user(client, admin, LIB_RW + ["library:generation:ingest"])
    payload = png_bytes("teal")

    class _RemoteStorage:
        """远端驱动的关键特征：没有本地路径，字节只能分块拿。"""

        def local_path(self, key: str) -> None:
            return None

        async def get(self, key: str) -> bytes:
            raise AssertionError("入库不该把整份对象读进内存（要用 iter_chunks）")

        async def iter_chunks(self, key: str, *, chunk_size: int = 1024 * 1024):  # noqa: ANN202
            yield payload

        async def put_file(self, key: str, path, content_type: str = "") -> str:  # noqa: ANN001
            return ""

    async def run() -> str:
        async with get_session_factory()() as session:
            service = GenerationIngest(session, _RemoteStorage())
            result = await service.run(
                IngestRequest(
                    source_module="image",
                    source_task_id="stream-1",
                    source_key="image:stream-1",
                    kind=LibraryAssetKind.IMAGE,
                    owner_id=owner["id"],
                    name="流式入库.png",
                    storage_key="images/stream-1.png",
                ),
                trusted=True,
            )
            return result.asset_id

    asset_id = asyncio.run(run())
    assert asset_id

    from library_support import fetch_asset

    row = fetch_asset(asset_id)
    assert row is not None
    assert (row["storage_key"] or "").startswith("library/blobs/")


def test_from_generation_requires_its_own_permission(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    _, creator_h = make_user(client, admin, ["library:read", "library:create"])
    denied = _ingest(
        client,
        api,
        creator_h,
        {
            "sourceModule": "image",
            "sourceTaskId": "x",
            "sourceKey": "image:x",
            "kind": "image",
        },
    )
    assert denied.status_code == 403, "能上传不等于能伪造「这是某次生成的结果」"


def test_from_generation_rejects_bad_kind_and_missing_source(
    client: TestClient, api: str
) -> None:
    admin = admin_headers(client)
    _, owner_h = make_user(client, admin, LIB_RW + ["library:generation:ingest"])

    audio = _ingest(
        client,
        api,
        owner_h,
        {
            "sourceModule": "image",
            "sourceTaskId": "a",
            "sourceKey": "image:a",
            "kind": "audio",
        },
    )
    assert audio.status_code == 422, "只有图片与视频能进草稿箱"

    missing = _ingest(
        client,
        api,
        owner_h,
        {
            "sourceModule": "image",
            "sourceTaskId": "b",
            "sourceKey": "image:b",
            "kind": "image",
        },
    )
    assert missing.status_code == 400
    assert missing.json()["code"] == 40085


def test_snapshot_rejects_two_spellings_of_the_same_reference_column() -> None:
    """同一列的两种写法给了不同值 → 400，而不是「最后一个说了算」。

    `reference_library_ids`（列名）与 `referenceLibraryIds`（契约名）都映射到
    同一列：内部调用方（流水线）混着用是可能的，而静默取最后一个会让「档案里
    记的是哪几张图」取决于键在字典里的顺序——这种错平时看不见。
    """
    with pytest.raises(BadRequest, match="两个不同的取值"):
        _snapshot_values(
            {
                "reference_library_ids": ["a1"],
                "referenceLibraryIds": ["b2"],
            }
        )


def test_snapshot_accepts_either_spelling_of_the_reference_column() -> None:
    """同一列的两个键名都能用，且落到同一个列名上。

    HTTP 进来的 `GenerationSnapshotIn` 是模型对象，读的是属性名（snake）；
    流水线（生图 / 任务）传的是原始字典，生图用契约名 `referenceLibraryIds`。
    v1 那条独立的 `reference_asset_ids` 列已随 `assets` 表删除，所以这里只验
    「两种写法归一」，不再验「互不串列」。
    """
    assert _snapshot_values({"referenceLibraryIds": ["new"]})["reference_library_ids"] == ["new"]
    assert (
        _snapshot_values({"reference_library_ids": ["new"]})["reference_library_ids"] == ["new"]
    )


# --------------------------------------------------------------------------- #
# `sourceUrl` 的出站护栏：白名单 + 流式落盘
#
# 旧实现把地址直接交给 `fetch_bytes`（不带白名单），而那个兜底**不解析 DNS**：
# 域名指向内网时拦不住——持 `library:generation:ingest` 的账号就能让服务端
# 替他访问内网。成片还允许 300MB，整份读进内存的峰值是「并发数 × 文件大小」。
# --------------------------------------------------------------------------- #
def test_source_url_outside_the_allowlist_is_refused(
    client: TestClient, api: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from modules.library.ingest import get_ingest_download_settings

    monkeypatch.setattr(
        get_ingest_download_settings(), "allowed_hosts", "cdn.allowed.example"
    )
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW + ["library:generation:ingest"])

    response = _ingest(
        client,
        api,
        owner_h,
        {
            "sourceModule": "image",
            "sourceTaskId": "job-evil",
            "sourceKey": "image:job-evil:0",
            "kind": "image",
            "name": "偷跑.png",
            "sourceUrl": "https://intranet.example/x.png",
        },
    )

    assert response.status_code == 400, response.text
    assert "白名单" in response.text


def test_source_url_inside_the_allowlist_is_downloaded(
    client: TestClient, api: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    from infra import http as http_module
    from modules.library.ingest import get_ingest_download_settings

    payload = png_bytes("steelblue")
    monkeypatch.setattr(get_ingest_download_settings(), "allowed_hosts", "cdn.allowed.example")
    monkeypatch.setattr(
        http_module,
        "_HTTP",
        httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, content=payload, headers={"content-type": "image/png"}
                )
            ),
            follow_redirects=False,
        ),
    )
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW + ["library:generation:ingest"])

    response = _ingest(
        client,
        api,
        owner_h,
        {
            "sourceModule": "image",
            "sourceTaskId": "job-ok",
            "sourceKey": "image:job-ok:0",
            "kind": "image",
            "name": "下载入库.png",
            "sourceUrl": "https://cdn.allowed.example/x.png",
        },
    )

    assert response.status_code == 201, response.text
    created = response.json()["data"]
    detail = client.get(
        f"{api}/library/assets/{created['assetId']}", headers=owner_h
    ).json()["data"]
    assert detail["state"] == "draft"


def test_ingest_download_requires_an_allowlist_in_prod() -> None:
    """生产必须显式配 `LIBRARY_INGEST_ALLOWED_HOSTS`，不许靠黑名单兜底。

    兜底不解析 DNS：域名指向内网时拦不住，而且日志里一句错都没有。
    与成图落盘（`IMAGE_ASSET_ALLOWED_HOSTS`）同一条口径。
    """
    from modules.library.ingest import IngestDownloadSettings

    assert IngestDownloadSettings.prod_required_fields == ("allowed_hosts",)


def test_blob_key_normalizes_the_extension() -> None:
    """key 里的扩展名必须消毒 —— 与归档层同一个 `normalize_suffix`。

    上游现在用白名单挡住了（`upload` 先 `Path(filename).suffix` 再查
    `EXTENSIONS`），但「函数本身挡住」和「调用方恰好挡住」是两件事：后者换个
    调用点就失效，而失效的样子是路径穿越，或者同一份内容因为 `.PNG` / `.png`
    落到两个 key 上（去重按 sha256 合并了行，字节却存了两份）。
    """
    from modules.library.service import blob_key

    digest = "a" * 64
    assert blob_key(digest, ".png").endswith(f"{digest}.png")
    # 大小写归一
    assert blob_key(digest, ".PNG").endswith(f"{digest}.png")
    # 非法后缀被消成空
    assert blob_key(digest, "../../../etc/passwd").endswith(digest)
    assert blob_key(digest, "").endswith(digest)


# --------------------------------------------------------------------------- #
# 生成产物继承参考素材的标签
# --------------------------------------------------------------------------- #
def _tag(
    client: TestClient, api: str, headers: dict[str, str], name: str, parent: str | None = None
) -> dict:
    body = {"name": name}
    if parent:
        body["parentId"] = parent
    resp = client.post(f"{api}/library/tags", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def _tag_asset_direct(asset_id: str, tag_ids: list[str]) -> None:
    """绕过界面守卫打标：要造「参考素材身上有维度标签」这个存量状态。"""
    import asyncio

    from db.session import get_session_factory
    from modules.library.tags import set_asset_tags

    async def run() -> None:
        async with get_session_factory()() as session:
            await set_asset_tags(session, asset_id, tag_ids, reject_groups=False)
            await session.commit()

    asyncio.run(run())


def _detail_tags(client: TestClient, api: str, headers: dict[str, str], asset_id: str) -> set[str]:
    resp = client.get(f"{api}/library/assets/{asset_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return {item["id"] for item in resp.json()["data"]["tags"]}


def test_generated_asset_inherits_reference_tags_union(client: TestClient, api: str) -> None:
    """多张参考图取并集；维度标签不继承，孤立一级照常继承。"""
    admin = admin_headers(client)
    owner, h = make_user(
        client, admin, LIB_RW + ["library:tag:manage", "library:generation:ingest"]
    )
    mark = suffix()
    dim = _tag(client, api, h, f"产品-{mark}")
    a6036 = _tag(client, api, h, f"6036-{mark}", dim["id"])
    t35 = _tag(client, api, h, f"T35-{mark}", dim["id"])
    standalone = _tag(client, api, h, f"孤立词-{mark}")

    ref1 = upload_image(client, api, h, name=f"r1-{mark}.png", color="red")
    ref2 = upload_image(client, api, h, name=f"r2-{mark}.png", color="blue")
    _tag_asset_direct(ref1["id"], [dim["id"], a6036["id"]])
    _tag_asset_direct(ref2["id"], [t35["id"], standalone["id"]])

    created = _ingest_via_port(
        owner_id=owner["id"],
        source_key=f"task:tag-union-{mark}",
        storage_key=_fresh_blob(f"union-{mark}.png", "green", (41, 41)),
        # 任务侧用的是列名快照键
        generation={"reference_library_ids": [ref1["id"], ref2["id"]]},
    )
    assert created["created"] is True
    ids = _detail_tags(client, api, h, created["assetId"])
    assert {a6036["id"], t35["id"], standalone["id"]} <= ids
    assert dim["id"] not in ids, "维度只用于分组，继承它等于给一个筛不出来的假标签"


def test_generated_image_keeps_configured_tags_and_inherits_reference(
    client: TestClient, api: str
) -> None:
    """生图侧用契约名快照键；显式配置的标签与继承的标签并存。"""
    admin = admin_headers(client)
    owner, h = make_user(
        client, admin, LIB_RW + ["library:tag:manage", "library:generation:ingest"]
    )
    mark = suffix()
    configured = _tag(client, api, h, f"显式-{mark}")
    from_ref = _tag(client, api, h, f"继承-{mark}")
    ref = upload_image(client, api, h, name=f"ref-{mark}.png", color="orange")
    _tag_asset_direct(ref["id"], [from_ref["id"]])

    created = _ingest_via_port(
        owner_id=owner["id"],
        source_key=f"image:tag-inherit-{mark}",
        storage_key=_fresh_blob(f"inherit-{mark}.png", "yellow", (42, 42)),
        generation={"referenceLibraryIds": [ref["id"]]},
        tag_ids=[configured["id"]],
    )
    assert created["created"] is True
    ids = _detail_tags(client, api, h, created["assetId"])
    assert {configured["id"], from_ref["id"]} <= ids


def test_generated_asset_without_reference_has_no_inherited_tags(
    client: TestClient, api: str
) -> None:
    """没有参考素材就没有继承来源 —— 不能凭空给产物打标。"""
    admin = admin_headers(client)
    owner, h = make_user(client, admin, LIB_RW + ["library:generation:ingest"])
    mark = suffix()
    created = _ingest_via_port(
        owner_id=owner["id"],
        source_key=f"image:no-ref-{mark}",
        storage_key=_fresh_blob(f"noref-{mark}.png", "purple", (43, 43)),
        generation={"prompt": "没有参考图"},
    )
    assert created["created"] is True
    assert _detail_tags(client, api, h, created["assetId"]) == set()


def test_from_generation_rejects_foreign_reference(client: TestClient, api: str) -> None:
    """HTTP 入口的 generation 由调用方自填：猜别人的素材 id 不能借到它的标签。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW + ["library:generation:ingest"])
    other, other_h = make_user(client, admin, LIB_RW)
    assert other["id"] != owner["id"]
    mark = suffix()
    foreign = upload_image(client, api, other_h, name=f"foreign-{mark}.png", color="red")
    source = upload_image(client, api, owner_h, name=f"source-{mark}.png", color="green")

    resp = _ingest(
        client,
        api,
        owner_h,
        {
            "sourceModule": "image",
            "sourceTaskId": f"job-{mark}",
            "sourceKey": f"image:foreign-ref-{mark}",
            "kind": "image",
            # HTTP 入口的 storageKey 必须指向调用方自己的素材行
            "storageKey": _storage_key(client, api, owner_h, source["id"]),
            "generation": {"referenceLibraryIds": [foreign["id"]]},
        },
    )
    assert resp.status_code == 403, resp.text


def test_from_generation_does_not_reject_own_reference(
    client: TestClient, api: str
) -> None:
    """可读校验不能误拒自己的素材。

    HTTP 入口的 storageKey 必须指向调用方已有的素材行，所以必然命中字节去重
    （`created=false` 是既有语义）；继承本身由端口用例覆盖，这条只证明没有被
    「猜 id 借标签」的校验连带挡住合法使用。
    """
    admin = admin_headers(client)
    owner, h = make_user(client, admin, LIB_RW + ["library:generation:ingest"])
    mark = suffix()
    ref = upload_image(client, api, h, name=f"own-ref-{mark}.png", color="orange")
    source = upload_image(client, api, h, name=f"own-source-{mark}.png", color="blue")

    resp = _ingest(
        client,
        api,
        h,
        {
            "sourceModule": "image",
            "sourceTaskId": f"job-own-{mark}",
            "sourceKey": f"image:own-ref-{mark}",
            "kind": "image",
            "storageKey": _storage_key(client, api, h, source["id"]),
            "generation": {"referenceLibraryIds": [ref["id"]]},
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["created"] is False
