"""隔离数据库验证通配收敛、专员角色与冲突保护；不改开发账号。"""

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.exceptions import Conflict
from db.base import Base
from modules.identity.asset_roles import apply, preview
from modules.identity.models import Role
from modules.identity.repository import RoleRepository


async def test_role_migration_preview_apply_and_guarded_rollback(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'roles.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            repo = RoleRepository(session)
            role = Role(code="editor", name="内容编辑")
            custom = Role(code="custom", name="自定义通配")
            session.add_all([role, custom])
            await session.flush()
            await repo.set_permissions(role.id, ["asset:*", "task:read"])
            await repo.set_permissions(custom.id, ["template:*"])
            await session.commit()
            report = await preview(session)
            assert "asset:*" in (await repo.permissions_for([role.id]))[role.id]
            assert report["wildcardRoles"][0]["code"] == "custom"
            await apply(session, report)
            after = (await repo.permissions_for([role.id]))[role.id]
            assert "asset:*" not in after and "asset:share" in after and "template:use" in after
            assert "asset:reference:manage" not in after
            specialist = await repo.get_by_code("asset_specialist")
            assert specialist
            assert (
                "template:pack:publish"
                in (await repo.permissions_for([specialist.id]))[specialist.id]
            )
            assert all(c["before"] == c["after"] for c in (await preview(session))["changes"])
            await repo.set_permissions(role.id, [*after, "shot:read"])
            await session.commit()
            with pytest.raises(Conflict):
                await apply(session, report, rollback=True)
            await repo.set_permissions(role.id, report["changes"][0]["after"])
            await session.commit()
            await apply(session, report, rollback=True)
            assert "asset:*" in (await repo.permissions_for([role.id]))[role.id]
            assert (await repo.permissions_for([custom.id]))[custom.id] == ["template:*"]
    finally:
        await engine.dispose()
