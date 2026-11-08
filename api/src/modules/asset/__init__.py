"""素材资产模块：图片 / 视频 / 音频的统一资产管理。

私人素材按归属隔离；公共目录仅包含本人主动分享的原片。
已发布封装的预览与引用走模板的包级授权，不开放全站私人原件读取。
查询按范围过滤后分页，查看与管理是两种独立能力。
"""

from api.module import ModuleSpec
from core.permissions import PermissionSpec
from modules.asset.cli import register as register_cli
from modules.asset.health import check as health_check
from modules.asset.router import router
from modules.asset.seed import seed as seed_demo

# 必须 import：注册 @periodic("asset.gc")（副作用导入）
from modules.asset import maintenance  # noqa: E402,F401  isort: skip

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
    periodic=("asset.gc", "asset.mirror"),
    # 演示数据由模块自己种（`app seed` 遍历所有模块的 seed 钩子）
    seed=seed_demo,
    seed_order=20,
    # 运维命令（import-archive-index / migrate-blobs / prune-blobs / rebuild-mirrors）
    cli=register_cli,
    # 部署自检：归档镜像一致性、对账开关、配额水位（见 modules/asset/health.py）
    doctor=health_check,
)
