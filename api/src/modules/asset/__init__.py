"""素材资产模块：图片 / 视频 / 音频的统一资产管理。

私人素材按归属隔离；公共目录仅包含本人主动分享的原片。
已发布封装的预览与引用走模板的包级授权，不开放全站私人原件读取。
查询按范围过滤后分页，查看与管理是两种独立能力。
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
        PermissionSpec("asset:update", "修改本人素材名称", group="素材"),
        PermissionSpec("asset:share", "分享或撤回本人原片", group="素材"),
        PermissionSpec("asset:reference:manage", "维护封装参考素材与登记文件", group="素材"),
    ),
)
