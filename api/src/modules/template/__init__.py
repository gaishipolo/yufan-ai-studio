"""模板模块：行业模板与分镜结构复用。"""

from api.module import ModuleSpec
from core.feed import register_replacement_feed
from core.permissions import PermissionSpec
from core.ports.assets import AssetReferenceSource, bind_asset_reference_sources
from modules.template.asset_references import PackAssetReferences
from modules.template.feed_provider import read_feed
from modules.template.replacement_pipeline import KIND_REPLACEMENT
from modules.template.replacement_worker import resume
from modules.template.router import router

register_replacement_feed(read_feed)
# 删除归档素材前问「还有谁在用」：封装 head / 草稿把 `assets.id` 写进 referenceIds。
# 历史发布版本刻意不算引用，否则老素材永远删不掉。
ASSET_REFERENCE_SOURCES = (
    AssetReferenceSource(
        name="media_packs.reference_ids",
        checker=PackAssetReferences(),
        location="core/media.py:MediaPack.reference_ids",
        description="封装 head / 草稿把 assets.id 写进 catalog/packs 与 "
        "catalog/pack_drafts 的 referenceIds；历史发布版本不算引用",
    ),
)
bind_asset_reference_sources("template", ASSET_REFERENCE_SOURCES)

module = ModuleSpec(
    name="template",
    router=router,
    tags=["模板"],
    permissions=(
        PermissionSpec("template:read", "查看行业模板", group="内容生产"),
        PermissionSpec("template:use", "使用替换模板并提交零积分生成", group="内容生产"),
        PermissionSpec("template:review", "评价自己的替换成片", group="内容生产"),
        PermissionSpec("template:pack:manage", "维护本人封装草稿", group="素材"),
        PermissionSpec("template:pack:publish", "发布或停用本人封装对象", group="素材"),
    ),
    startup=resume,
    # 已有自己的恢复器；声明负责的 kind 供架构守卫对账。
    recovery_kinds=(KIND_REPLACEMENT,),
    asset_reference_sources=ASSET_REFERENCE_SOURCES,
    jobs=(
        "template.replacement.prepare",
        "libtv.download",
        "template.replacement.recover",
    ),
)
