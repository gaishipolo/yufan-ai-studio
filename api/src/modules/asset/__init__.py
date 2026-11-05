"""素材资产模块：图片 / 视频 / 音频的统一资产管理。

**这里没有 `asset:read:all`，是决定而不是遗漏。**
`task` 与 `image` 都有「看全站」的开关，素材始终只有 `owner_id == actor.id`：
素材是个人上传的原始媒体，没有「查看他人上传」的业务场景，多一个 `read:all`
就多一条把内部素材外泄的路径。

要打开它，先回答「谁需要看别人的原始上传」，再加权限点并把列表补上分页——
照 `modules/image` 的样子做，别只加权限点（范围一开，全量返回就是事故）。
"""

from api.module import ModuleSpec
from core.permissions import PermissionSpec
from modules.asset.router import router

module = ModuleSpec(
    name="asset",
    router=router,
    tags=["素材"],
    permissions=(
        PermissionSpec("asset:read", "查看素材", group="素材"),
        PermissionSpec("asset:create", "上传素材", group="素材"),
        PermissionSpec("asset:delete", "删除素材", group="素材"),
    ),
)
