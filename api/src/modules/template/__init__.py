"""模板库：封装（参考素材包）与人物/产品/场景替换。

模块对外的全部信息都在这一个 `ModuleSpec` 里：路由、权限点、启动恢复、素材
引用来源、队列作业、种子数据。`api/discovery.py` 扫描到它就会自动挂载。

**重建说明（2026-11-08）**：本模块被整体删掉后按 `docs/EXTENDING.md` 重新长出。
改动的是存储（JSON 目录 + 本机文件 → 六张表）与分层（模板私有的 `core/media.py`
部分、`core/pack_roles.py`、`infra/media_catalog.py` 全部收回模块内），对外契约
（路径、权限码、出入参）一比一保留。过程见 `docs/WALKTHROUGH-template-rebuild.md`。
"""

from api.module import ModuleSpec
from core.feed import register_replacement_feed
from core.permissions import PermissionSpec
from core.ports.assets import AssetReferenceSource, bind_asset_reference_sources
from core.ports.library_references import (
    LibraryReferenceSource,
    bind_library_reference_sources,
)
from modules.template.asset_references import PackAssetReferences
from modules.template.cli import register as register_cli
from modules.template.feed_provider import read_feed
from modules.template.health import check as health_check
from modules.template.library_references import PackLibraryReferences
from modules.template.pipeline import KIND_REPLACEMENT
from modules.template.router import router
from modules.template.seed import seed as seed_demo
from modules.template.worker import resume

# 必须 import：注册队列作业（template.replacement.prepare / libtv.download /
# template.replacement.recover）。漏了它 `create_app()` 会直接报
# 「声明了但没注册」，因为 ModuleSpec.jobs 是义务而非注释。
from modules.template import worker  # noqa: E402,F401  isort: skip

register_replacement_feed(read_feed)

# 素材删除前问「还有谁在用」：封装 head 把 assets.id 写进
# template_pack_references。声明与运行时注册是同一份常量。
ASSET_REFERENCE_SOURCES = (
    AssetReferenceSource(
        name="template_pack_references.asset_id",
        checker=PackAssetReferences(),
        location="modules/template/models.py:TemplatePackReference.asset_id",
        description="封装 head 把 assets.id 写进 template_pack_references；"
        "已发布的历史版本（template_pack_versions）不算引用，否则老素材永远删不掉",
    ),
)
bind_asset_reference_sources("template", ASSET_REFERENCE_SOURCES)

# v2 资产的「彻底删除」问的是另一套 id、另一个注入口。名字必须与上面那条区分开
# （`name` 全局唯一，写进日志与 `app doctor` 报告），否则 `bind_*` 会按名字互相覆盖。
# 不拦的四处（冻结版本 / 运行输入 / 视频模板清单 / 封面）在 `library_references.py`
# 的表格里逐条写了理由。
LIBRARY_REFERENCE_SOURCES = (
    LibraryReferenceSource(
        name="template_pack_references.library_asset_id",
        checker=PackLibraryReferences(),
        location="modules/template/models.py:TemplatePackReference.asset_id",
        description="封装 head 里引用的 v2 资产；已删除的封装与冻结的历史版本不算",
    ),
)
bind_library_reference_sources("template", LIBRARY_REFERENCE_SOURCES)

module = ModuleSpec(
    name="template",
    router=router,
    tags=["模板"],
    description="模板库：参考素材封装与人物/产品/场景替换生成",
    permissions=(
        PermissionSpec("template:read", "查看行业模板", group="内容生产"),
        PermissionSpec("template:use", "使用替换模板并提交零积分生成", group="内容生产"),
        PermissionSpec("template:review", "评价自己的替换成片", group="内容生产"),
        PermissionSpec("template:pack:manage", "维护本人封装草稿", group="素材"),
        PermissionSpec("template:pack:publish", "发布或停用本人封装对象", group="素材"),
    ),
    startup=resume,
    # 已有自己的恢复器（worker.resume 扫数据库）；声明负责的 kind 供架构守卫对账。
    recovery_kinds=(KIND_REPLACEMENT,),
    asset_reference_sources=ASSET_REFERENCE_SOURCES,
    library_reference_sources=LIBRARY_REFERENCE_SOURCES,
    jobs=(
        "template.replacement.prepare",
        "libtv.download",
        "template.replacement.recover",
    ),
    # 部署自检：Node 版本 / 适配器快照哈希 / 远端归档是否显式配根 / 三个目录可写。
    # 只报告，不拦启动 —— 「启动即失败」是计划步 5 的事。
    doctor=health_check,
    seed=seed_demo,
    seed_order=30,
    # 运维命令：import-template-legacy —— 把 catalog/*.json 与 runs/<id>/run.json
    # 迁进六张表。升级必须跑一次，否则「重建」等于「模板库清空」。
    cli=register_cli,
)
