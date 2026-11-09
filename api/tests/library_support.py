"""资产库 M2 用例的共享夹具：造角色、用户、小组、资产与草稿。

不放进 `conftest.py`：只有 v2 的用例需要这些（建角色、建组、直接插草稿行），
放进全局夹具会让别的模块的用例也依赖它们，而 `conftest` 里每多一样东西，
「这个用例为什么需要它」就更难回答一次。

命名说明：本文件不以 `test_` 开头，pytest 不会把它当用例文件收集。
"""

from __future__ import annotations

import asyncio
import io
import uuid
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image

IDENTITY = "/api/v1/identity"
ADMIN = ("admin", "admin@12345")


def suffix() -> str:
    """给角色 / 用户名 / 组名加一段唯一后缀：测试库是 session 级共享的。"""
    return uuid.uuid4().hex[:8]


def login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post(
        f"{IDENTITY}/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['data']['accessToken']}"}


def admin_headers(client: TestClient) -> dict[str, str]:
    return login(client, *ADMIN)


def make_role(
    client: TestClient, admin: dict[str, str], permissions: list[str]
) -> dict[str, Any]:
    code = f"lib_{suffix()}"
    response = client.post(
        f"{IDENTITY}/roles",
        json={"code": code, "name": code, "permissions": permissions},
        headers=admin,
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def make_user(
    client: TestClient,
    admin: dict[str, str],
    permissions: list[str],
    *,
    group_ids: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """建「专属角色 + 用户」并登录，返回 (用户, 请求头)。"""
    role = make_role(client, admin, permissions)
    username = f"lib_{suffix()}"
    payload: dict[str, Any] = {
        "username": username,
        "password": "lib-pass-12345",
        "displayName": username,
        "roleIds": [role["id"]],
    }
    if group_ids:
        payload["groupIds"] = group_ids
    response = client.post(f"{IDENTITY}/users", json=payload, headers=admin)
    assert response.status_code == 201, response.text
    return response.json()["data"], login(client, username, "lib-pass-12345")


def make_group(client: TestClient, admin: dict[str, str], members: list[str]) -> dict[str, Any]:
    name = f"lib_group_{suffix()}"
    response = client.post(
        f"{IDENTITY}/groups", json={"name": name, "description": "资产库用例"}, headers=admin
    )
    assert response.status_code == 201, response.text
    group = response.json()["data"]
    if members:
        set_members(client, admin, group["id"], members)
    return group


def set_members(
    client: TestClient, admin: dict[str, str], group_id: str, user_ids: list[str]
) -> None:
    response = client.put(
        f"{IDENTITY}/groups/{group_id}/members",
        json={"userIds": user_ids},
        headers=admin,
    )
    assert response.status_code == 200, response.text


def png_bytes(color: str = "purple", size: tuple[int, int] = (32, 32)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def upload_image(
    client: TestClient,
    api: str,
    headers: dict[str, str],
    *,
    name: str = "cover.png",
    color: str = "purple",
) -> dict[str, Any]:
    response = client.post(
        f"{api}/library/assets/upload",
        content=png_bytes(color),
        params={"filename": name, "kind": "image"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def insert_asset(**fields: Any) -> str:
    """直接插一行资产（造草稿用）。

    M2 还没有「生成入库」接口（那是 M3 的 `POST /assets/from-generation`），
    但草稿的采纳 / 驳回 / 恢复回草稿必须现在就测。等接口上线后再改成走接口，
    这个函数就是那一步要删掉的东西。
    """
    from modules.library.models import LibraryAsset

    async def run() -> str:
        from db.session import get_session_factory

        async with get_session_factory()() as session:
            row = LibraryAsset(
                name=fields.get("name", "draft.png"),
                kind=fields.get("kind", "image"),
                origin=fields.get("origin", "generated"),
                mime_type=fields.get("mime_type", "image/png"),
                size_bytes=fields.get("size_bytes", 1024),
                storage_key=fields.get("storage_key", ""),
                cover_key=fields.get("cover_key", ""),
                owner_id=fields["owner_id"],
                visibility=fields.get("visibility", "personal"),
                state=fields.get("state", "draft"),
                state_version=fields.get("state_version", 1),
                source_key=fields.get("source_key"),
            )
            session.add(row)
            await session.commit()
            return row.id

    return asyncio.run(run())


def fetch_asset(asset_id: str) -> dict[str, Any] | None:
    """读回一行资产（断言不变式用：字段是否真的落了盘）。"""
    from sqlalchemy import select

    from modules.library.models import LibraryAsset

    async def run() -> dict[str, Any] | None:
        from db.session import get_session_factory

        async with get_session_factory()() as session:
            row = (
                await session.scalars(
                    select(LibraryAsset).where(LibraryAsset.id == asset_id)
                )
            ).first()
            if row is None:
                return None
            return {
                "state": row.state,
                "visibility": row.visibility,
                "trashed_from_state": row.trashed_from_state,
                "trash_reason": row.trash_reason,
                "deleted_at": row.deleted_at,
                "owner_id": row.owner_id,
                "state_version": row.state_version,
            }

    return asyncio.run(run())


def fetch_group_rows(asset_id: str) -> list[str]:
    from sqlalchemy import select

    from modules.library.models import LibraryAssetGroup

    async def run() -> list[str]:
        from db.session import get_session_factory

        async with get_session_factory()() as session:
            return list(
                await session.scalars(
                    select(LibraryAssetGroup.group_id).where(
                        LibraryAssetGroup.asset_id == asset_id
                    )
                )
            )

    return asyncio.run(run())


def fetch_share_requests(asset_id: str) -> list[dict[str, Any]]:
    from sqlalchemy import select

    from modules.library.models import LibraryShareRequest

    async def run() -> list[dict[str, Any]]:
        from db.session import get_session_factory

        async with get_session_factory()() as session:
            rows = (
                await session.scalars(
                    select(LibraryShareRequest)
                    .where(LibraryShareRequest.asset_id == asset_id)
                    .order_by(LibraryShareRequest.created_at)
                )
            ).all()
            return [
                {
                    "id": row.id,
                    "action": row.action,
                    "target": row.target,
                    "group_id": row.group_id,
                    "status": row.status,
                    "reviewer_id": row.reviewer_id,
                    "note": row.note,
                }
                for row in rows
            ]

    return asyncio.run(run())


def make_session_asset(*, state: str = "trash", **fields: Any) -> str:
    """造一条任意状态的资产行（GC 用例用：它们不经过 HTTP，不需要登录）。"""
    from core.config import settings

    owner_id = fields.pop("owner_id", settings.demo_user_id)
    return insert_asset(owner_id=owner_id, state=state, **fields)


def expire_trash(asset_id: str) -> None:
    """把回收站条目的过期时间调到过去，用来测 GC（不想等 30 天）。"""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from modules.library.models import LibraryAsset

    async def run() -> None:
        from db.session import get_session_factory

        async with get_session_factory()() as session:
            await session.execute(
                update(LibraryAsset)
                .where(LibraryAsset.id == asset_id)
                .values(trash_expires_at=datetime.now(UTC) - timedelta(days=1))
            )
            await session.commit()

    asyncio.run(run())


def insert_pack_item(asset_id: str) -> str:
    """造一条封装成员行：用来验证「被封装引用 → 彻底删除被拒」。

    封装接口本身是 M3 的，但引用检查现在就必须可测——它挡的是不可逆删除。
    """
    from modules.library.models import LibraryPackItem

    async def run() -> str:
        from db.session import get_session_factory

        pack_id = f"pack_{suffix()}"
        async with get_session_factory()() as session:
            session.add(
                LibraryPackItem(pack_id=pack_id, asset_id=asset_id, position=1, role="主角")
            )
            await session.commit()
        return pack_id

    return asyncio.run(run())


#: 资产库写操作的基础权限集合。用例按需增删，别在用例里手抄这一串。
LIB_RW = [
    "library:read",
    "library:create",
    "library:update",
    "library:delete",
    "library:share",
]


def put_blob(key: str, data: bytes, mime: str = "image/png") -> str:
    """直接往对象存储写一份字节，模拟「生成结果已经落在存储里」。

    生成入库的用例不能拿「刚上传的那张图」当来源：同一份字节已经登记在这个
    owner 的库里了，入库会走内容去重（`created=false`），测不到新建路径。
    """
    from infra import get_storage

    async def run() -> None:
        await get_storage().put(key, data, mime)

    asyncio.run(run())
    return key


def fetch_pack_items(pack_id: str) -> list[dict[str, Any]]:
    """读回封装成员行（断言「彻底删除封装不留孤儿行」用）。"""
    from sqlalchemy import select

    from modules.library.models import LibraryPackItem

    async def run() -> list[dict[str, Any]]:
        from db.session import get_session_factory

        async with get_session_factory()() as session:
            rows = (
                await session.scalars(
                    select(LibraryPackItem)
                    .where(LibraryPackItem.pack_id == pack_id)
                    .order_by(LibraryPackItem.position)
                )
            ).all()
            return [
                {
                    "asset_id": row.asset_id,
                    "position": row.position,
                    "role": row.role,
                }
                for row in rows
            ]

    return asyncio.run(run())


def build_download_job(job_id: str) -> None:
    """同步执行一次打包（测试里没有常驻 worker）。

    `build_download` 只处理 `queued`，所以重复调用是安全的；用例里调两次不会把
    同一个包压两遍，也不会把状态从 succeeded 改回去。
    """
    from modules.library.downloads import build_download

    asyncio.run(build_download(job_id))


def backdate_download(
    job_id: str, *, expires_days: float = 1, updated_days: float = 0
) -> None:
    """把打包任务的过期时间 / 更新时间调到过去，用来测 GC（不想等 24 小时）。"""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from modules.library.models import LibraryDownloadJob

    async def run() -> None:
        from db.session import get_session_factory

        now = datetime.now(UTC)
        values: dict[str, Any] = {
            "expires_at": now - timedelta(days=expires_days),
        }
        if updated_days:
            values["updated_at"] = now - timedelta(days=updated_days)
        async with get_session_factory()() as session:
            await session.execute(
                update(LibraryDownloadJob)
                .where(LibraryDownloadJob.id == job_id)
                .values(**values)
            )
            await session.commit()

    asyncio.run(run())


def fetch_download_job(job_id: str) -> dict[str, Any] | None:
    from modules.library.models import LibraryDownloadJob

    async def run() -> dict[str, Any] | None:
        from db.session import get_session_factory

        async with get_session_factory()() as session:
            row = await session.get(LibraryDownloadJob, job_id)
            if row is None:
                return None
            return {
                "status": row.status,
                "storage_key": row.storage_key,
                "size_bytes": row.size_bytes,
                "asset_ids": list(row.asset_ids or []),
                "error": row.error,
            }

    return asyncio.run(run())
