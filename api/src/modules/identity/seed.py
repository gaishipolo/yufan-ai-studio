"""身份模块的演示数据：三个账号，密码固定（仅演示库）。

## 为什么账号必须由这个模块种

其它模块的演示行都带 `owner_id` / `author_id`。以前整个 `src/seed.py` 一起种，
「谁先谁后」根本不成问题；拆到各模块之后，顺序就必须显式声明：
`ModuleSpec(seed_order=10)` 让它排在所有业务模块前面。

## 口令为什么走 `IdentityService.set_password`

那是唯一的写入点（同时写 `password_changed_at`）。seed 自己调 `hash_password`
会让「改口令必须写 password_changed_at」多出第四个例外，下一个加路径的人就会
照着错的那份抄（R6-08）。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from api.module import SeedContext
from modules.identity.models import User
from modules.identity.service import IdentityService

# (id, 用户名, 昵称, 角色标识, 密码) —— 演示库专用口令，生产环境请勿使用
_USERS: list[tuple[str, str, str, str, str]] = [
    # 演示口令也按正式策略（>=10 位、非纯数字）设置：若演示账号自己都不满足策略，
    # 「改密码」这一步就会当场 422，第一印象很糟（R4-14/R4-16）
    ("u_001", "admin", "老大", "admin", "admin@12345"),
    ("u_002", "editor", "小林", "editor", "editor@12345"),
    ("u_003", "viewer", "阿 May", "viewer", "viewer@12345"),
]


async def seed(session: AsyncSession, context: SeedContext) -> int:
    # 内置角色（admin/editor/viewer）由模块自己保证存在，seed 不重复定义
    identity = IdentityService(session)
    await identity.ensure_builtin_roles()
    roles = {role.code: role for role in await identity.roles.list_all()}

    users: list[User] = []
    for index, (user_id, username, display_name, _, password) in enumerate(_USERS):
        # 第一个演示账号的 id 必须与 `SeedContext.demo_user_id` 一致：其它模块
        # 就是按那个 id 种 `owner_id` 的。不一致的表现不是报错，而是列表页
        # 显示一个「不存在的人」——所以这里对齐一次，改配置也不会错位。
        if index == 0:
            user_id = context.demo_user_id
            display_name = context.demo_user_name
        user = User(
            id=user_id,
            username=username,
            display_name=display_name,
            email=f"{username}@demo.local",
            is_active=True,
        )
        await identity.set_password(user, password)
        users.append(user)

    session.add_all(users)
    await session.flush()
    for (_, _, _, role_code, _), user in zip(_USERS, users, strict=True):
        await identity.users.set_roles(user.id, [roles[role_code].id])
    await session.commit()
    return len(users)
