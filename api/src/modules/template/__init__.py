"""模板库：人物 / 产品 / 场景替换（执行链）。

模块对外的全部信息都在这一个 `ModuleSpec` 里：路由、权限点、启动恢复、队列作业、
部署自检、运维命令。`api/discovery.py` 扫描到它就会自动挂载。

**2026-11-14 C2 之后本模块只剩替换执行链**：素材包与视频模板的表 / 服务 / 路由
（`/templates/replacement-packs`、`/templates/video-templates`）连同它们的权限码
与素材引用声明一起搬到了 `modules/template_pack`。执行链只经
`core.ports.template_runtime` 读封装数据，不 import 对方的任何东西。

**重建说明（2026-11-08）**：本模块被整体删掉后按 `docs/EXTENDING.md` 重新长出。
改动的是存储（JSON 目录 + 本机文件 → 六张表）与分层（模板私有的 `core/media.py`
部分、`core/pack_roles.py`、`infra/media_catalog.py` 全部收回模块内），对外契约
（路径、权限码、出入参）一比一保留。过程见 `docs/WALKTHROUGH-template-rebuild.md`。
"""

from api.module import ModuleSpec
from core.feed import register_replacement_feed
from core.permissions import PermissionSpec
from core.ports.library_references import (
    LibraryReferenceSource,
    bind_library_reference_sources,
)
from modules.template.cli import register as register_cli
from modules.template.feed_provider import read_feed
from modules.template.health import check as health_check
from modules.template.library_references import RunInputsNotBlocking
from modules.template.pipeline import KIND_REPLACEMENT
from modules.template.router import router
from modules.template.worker import resume

# 必须 import：注册队列作业（template.replacement.prepare / libtv.download /
# template.replacement.recover）。漏了它 `create_app()` 会直接报
# 「声明了但没注册」，因为 ModuleSpec.jobs 是义务而非注释。
from modules.template import worker  # noqa: E402,F401  isort: skip

register_replacement_feed(read_feed)

# 资产「彻底删除」前问「还有谁在用」。本模块确实把 v2 id 写进
# `replacement_run_inputs`，但字节在 prepare 时已物化、回放不读资产库，
# 所以这条来源如实声明、显式返回空（理由见 `library_references.py`）。
LIBRARY_REFERENCE_SOURCES = (
    LibraryReferenceSource(
        name="replacement_run_inputs.library_asset_id",
        checker=RunInputsNotBlocking(),
        location="modules/template/schema.py:PrepareReplacement.reference_library_ids",
        description="run 输入的 v2 资产；字节已物化进归档，故意不拦删除",
    ),
)
bind_library_reference_sources("template", LIBRARY_REFERENCE_SOURCES)

module = ModuleSpec(
    name="template",
    router=router,
    tags=["模板"],
    description="模板库：人物/产品/场景替换生成（素材包与视频模板在 template_pack 模块）",
    permissions=(
        PermissionSpec("template:read", "查看行业模板", group="内容生产"),
        PermissionSpec("template:use", "使用替换模板并提交零积分生成", group="内容生产"),
        PermissionSpec("template:review", "评价自己的替换成片", group="内容生产"),
        # 素材包接口已经在 modules/template_pack，但这两个码沿用重建前的命名，
        # 而 `test_permission_codes_are_namespaced_by_module` 要求码前缀等于
        # **声明它的模块名**。所以由本模块声明、对方的路由去 require（权限码是
        # 全局字符串，谁声明不影响谁能用）。改名会让已有角色静默失去权限。
        PermissionSpec("template:pack:manage", "维护本人封装草稿", group="素材"),
        PermissionSpec("template:pack:publish", "发布或停用本人封装对象", group="素材"),
    ),
    startup=resume,
    # 已有自己的恢复器（worker.resume 扫数据库）；声明负责的 kind 供架构守卫对账。
    recovery_kinds=(KIND_REPLACEMENT,),
    library_reference_sources=LIBRARY_REFERENCE_SOURCES,
    jobs=(
        "template.replacement.prepare",
        "libtv.download",
        "template.replacement.recover",
    ),
    # 部署自检：Node 版本 / 适配器快照哈希 / 远端归档是否显式配根 / 三个目录可写。
    # 只报告，不拦启动 —— 「启动即失败」是计划步 5 的事。
    doctor=health_check,
    # 运维命令：import-template-runs / template-workspaces。
    # 封装那半在 modules/template_pack（`import-template-packs`）。
    cli=register_cli,
)
