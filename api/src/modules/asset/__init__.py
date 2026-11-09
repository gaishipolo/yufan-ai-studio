"""素材资产模块：图片 / 视频 / 音频的统一资产管理，入口只有 `/assets`。

私人素材按归属隔离；公共目录仅包含本人主动分享的原片。
已发布封装的预览与引用走模板的包级授权，不开放全站私人原件读取。
查询按范围过滤后分页，查看与管理是两种独立能力。
"""

from api.module import ModuleSpec
from core.permissions import PermissionSpec
from core.ports.assets import AssetReferenceSource, bind_asset_reference_sources
from modules.asset.asset_references import RecipeAssetReferences
from modules.asset.cli import register as register_cli
from modules.asset.health import check as health_check
from modules.asset.library_service import bootstrap_library
from modules.asset.lookup import install as install_lookup
from modules.asset.router import router
from modules.asset.seed import seed as seed_demo
from modules.asset.subject_lookup import install as install_subject_lookup

# 必须 import：注册 @periodic("asset.gc")（副作用导入）
from modules.asset import maintenance  # noqa: E402,F401  isort: skip

#: 素材被谁引用——生成配方这一条由本模块自己声明。
#:
#: 删除保护原本只聚合**别的模块**手里的引用（模板封装、任务参考图）。M3 引入
#: `asset_refs` 之后，"用它生成过一张成品"也成了一种使用，而且它落在本模块里
#: ——正因为落在自己家，最容易被忘掉。`tests/test_architecture.py` 的 AST 守卫
#: 会扫到 `reference_asset_ids` 这个字段名，没声明就红。
ASSET_REFERENCE_SOURCES = (
    AssetReferenceSource(
        name="asset.recipes",
        checker=RecipeAssetReferences(),
        location="modules/asset/asset_references.py",
        description="生成配方引用（产出仍存活的那些）",
    ),
)
bind_asset_reference_sources("asset", ASSET_REFERENCE_SOURCES)

# 把「素材能查」挂到注入口：模板库 prepare 时要读素材归属与分类，
# 但它不能 import 本模块（业务模块之间不许互相 import，见 EXTENDING §5.1）。
install_lookup()
# 主体的冻结视图也挂在注入口：模板 prepare 要按「旧包 → 主体版本」取成员，
# 同样不能 import 本模块。没挂上时模板回落旧路径（不是空图）。
install_subject_lookup()

module = ModuleSpec(
    name="asset",
    router=router,
    tags=["素材"],
    permissions=(
        PermissionSpec("asset:read", "查看素材", group="素材"),
        PermissionSpec("asset:create", "上传素材", group="素材"),
        PermissionSpec("asset:delete", "删除素材", group="素材"),
        PermissionSpec("asset:update", "修改本人素材（名称、标签）", group="素材"),
        PermissionSpec("asset:share", "分享或撤回本人原片", group="素材"),
        PermissionSpec("asset:reference:manage", "维护封装参考素材与登记文件", group="素材"),
        # 团队是**库的边界**，它属于素材库这个产品，不是一个独立的组织系统：
        # 三个团队的存在意义就是「这个库归谁」，M5 的复制进团队 / 待审区 /
        # 提升到公共库全都长在素材库上。所以权限码挂在 `asset` 名下，
        # 用 `资源:子资源:动作` 的既有语法（同 `asset:reference:manage`）。
        #
        # 两个码分开的理由：能看团队（导航要用）不等于能建团队、任命管理员——
        # 合成一个码会让「只想让顶栏显示团队」也必须给管理权。
        PermissionSpec("asset:team:read", "查看团队（库切换器）", group="素材"),
        PermissionSpec(
            "asset:team:manage",
            "管理团队（建团队、任命管理员）",
            group="素材",
            description="任命其他团队管理员必须由平台管理员执行，团队管理员不能自行任命同级",
        ),
        # 词表是全局配置：一个词的错误（"高级感"）会影响所有人的筛选与检索。
        # 所以新增词不跟 `asset:create` 共用一个码——那个码代表"能上传素材"。
        PermissionSpec("asset:tag:manage", "管理标签词表（新增标签）", group="素材"),
        # 「管理团队成员」(`asset:team:member:manage`) 待 M5 的成员管理接口一起上线：
        # 现在没有任何判定点，声明了只会让角色配置界面多一个勾不出效果的选项。
        # `test_permission_coverage.py` 的规则是「用上了再声明」，不是先占位。
    ),
    # 装配：团队与它们的分类树对齐。放 `bootstrap` 而不是 `startup`——
    # 只读 API 实例不执行 startup，于是「团队没有分类树」会长期存在。
    bootstrap=bootstrap_library,
    periodic=("asset.gc", "asset.mirror", "asset.candidate.gc"),
    # 引用来源：删素材前要问「还有谁在用它」，其中一条是"被生成配方引用过"
    asset_reference_sources=ASSET_REFERENCE_SOURCES,
    # 演示数据由模块自己种（`app seed` 遍历所有模块的 seed 钩子）
    seed=seed_demo,
    seed_order=20,
    # 运维命令（import-archive-index / migrate-blobs / prune-blobs / rebuild-mirrors）
    cli=register_cli,
    # 部署自检：归档镜像一致性、对账开关、配额水位（见 modules/asset/health.py）
    doctor=health_check,
)
