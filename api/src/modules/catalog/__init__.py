"""模型目录模块：把「有哪些模型可用」变成接口，而不是前端常量。

价值不在这个接口本身，而在**解耦**：新增一个供应商模型时，
后端注册表多一条即可，前端选择器自动出现，不需要发版。
"""

from api.module import ModuleSpec
from core.pages import PageSpec
from core.permissions import PermissionSpec
from modules.catalog.router import router

module = ModuleSpec(
    name="catalog",
    router=router,
    tags=["模型"],
    description="模型目录：可用模型、所属厂商、参数 schema",
    permissions=(
        PermissionSpec("catalog:read", "查看模型目录", group="内容生产"),
    ),
    # 「生成工作台」这一页读的是模型目录（选模型、拉参数 schema），提交时又分别
    # 走 task / image 的接口。页面归到 catalog 是因为它**主要读**这份数据；
    # `requires` 跨模块是正常的：门控按「至少一个页面被授予」判定，不会连坐。
    pages=(
        PageSpec(
            code="page:studio",
            name="生成工作台",
            route="/studio",
            requires=(
                "catalog:read",
                "task:create",
                "image:create",
            ),
        ),
    ),
)
