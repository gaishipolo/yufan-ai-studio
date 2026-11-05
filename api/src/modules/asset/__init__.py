"""素材资产模块：图片 / 视频 / 音频的统一资产管理。"""

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
