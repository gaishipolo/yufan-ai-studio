"""替换运行：准备输入、提交生成、评价、回收站。

数据从「`run.json` + 一堆旁挂 JSON」搬进 `replacement_runs` 之后，有三处判断被
重新落位，都是有意为之：

1. **owner 过滤下沉到 SQL**。旧 `list_runs()` 要把所有档案读出来再在 Python 里
   比 `owner_id`，注释里写着「留待单独一轮」——本轮就是那一轮。
2. **幂等从指纹文件变成主键**。`id` 就是前端传的 `request_id`，重复提交命中
   同一行；指纹只用来区分「同一 id 但配置变了」。
3. **最终授权校验前移到业务层**。执行器（`process.run_node`）只有文件
   系统、查不了库，现在由这里在排队前校验「原片还可用、封装还已发布」。

**状态机的语义没变**，尤其 `submission_unknown`：它表示「上一次提交可能已经
花了钱」，既不能当失败重提，也不能当成功。唯一出口是 `reconfirm()`。
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any, NamedTuple, TypeAlias

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from core import audit
from core.config import get_settings
from core.context import CurrentUser
from core.exceptions import (
    AppError,
    BadRequest,
    Conflict,
    Forbidden,
    NotFound,
    UpstreamError,
)
from core.feed import ReplacementFeedRecord
from core.library_text import (
    dumps_text_refs,
    freeze_text_refs,
    inject_masked_text_refs,
    mask_text_refs,
)
from core.logging import get_logger
from core.metrics import METRICS
from core.ports.library_lookup import (
    LibraryAssetKind,
    LibraryAssetRef,
    LibraryAssetState,
    get_library_lookup,
)
from core.serving import Served
from db.base import utcnow
from infra.archive import ArchiveStorage, get_archive_storage
from infra.media_probe import encode_text
from modules.template import video_template_service
from modules.template.asset_source import resolve_assets
from modules.template.authorization import authorization_gap
from modules.template.channels import channel_catalog, channel_label, require_channel, run_channel
from modules.template.library_media import StagedMedia, discard_staged, stage_library_refs
from modules.template.models import ReplacementReview as ReviewRow
from modules.template.models import ReplacementRun, TemplatePackVersion
from modules.template.pack_roles import PACK_ROLES, PACK_SCOPES, usage_for
from modules.template.repository import PackRepository, RunInputRow, RunRepository
from modules.template.run_clock import elapsed_seconds
from modules.template.schema import (
    PackBinding,
    PrepareReplacement,
    ReplacementCatalog,
    ReplacementInputAsset,
    ReplacementInputSummary,
    ReplacementReview,
    ReplacementReviewEvent,
    ReplacementReviewIn,
    ReplacementRunOut,
)
from modules.template.titles import task_name
from modules.template.video_template_options import (
    is_scene_text,
    scene_description,
    scope_options,
    validate_options,
)
from modules.template.video_template_schema import MAX_REFERENCE_IMAGES
from modules.template.workspace import (
    BRIDGE,
    RUNTIME,
    Compilation,
    compile_prompt,
    create_workspace,
    ensure_layout,
    run_path,
)

if TYPE_CHECKING:
    from modules.template.video_template_schema import VideoTemplateOut

logger = get_logger(__name__)

#: 归档流水线能处理的素材：本次刚从资产库落地的暂存副本。
#: 只关心「可读本地文件 + 摘要 + 宽高时长」，**不管权限语义**：
#: 谁可读在进 prepare 之前就判完了（`authorization` / `readable_ids()`），这里只搬字节。
#: 旧素材库（`StoredMedia`）已于 2026-11-13 随 `assets` 表下线，所以只剩一种。
RunMedia: TypeAlias = "StagedMedia"


class ResolvedInputs(NamedTuple):
    """`_validate_inputs()` 的结果：原片与参考图都是资产库 v2 的 id。

    为什么这里不直接给 `RunMedia`：字节要先落进工作区才有本地路径，而工作区在
    `create_workspace()` 之后才存在。校验阶段只能先交回「从哪取」
    （`LibraryAssetRef`），落盘放在 prepare 的 try 块里。
    """

    source_v2: LibraryAssetRef | None
    #: 封装发布版本自己声明的参考图。它们不是「用户临时挑的散图」，
    #: 而是 `frozen.reference_ids` 那一批，顺序即 `{{Mixed N}}` 的编号顺序。
    refs_v2: list[LibraryAssetRef]


class LibraryPackSnapshot(NamedTuple):
    """资产库 v2 封装在 prepare 那一刻展开出来的成员清单（本次任务专用）。

    **刻意不长成一个「封装版本」**：v2 封装没有不可变发布版本，成员是整体替换的
    可变列表（`PATCH /library/packs/{id}`）。如果任务直接引用「当前封装」，A 改一次
    成员，同一份历史配置就会指向另外几张图。正确做法是 prepare 时把成员 id 与用途
    冻进本次任务：**包可变、任务不可变**，与 v1 的 `TemplatePackVersion` 一个意思，
    但不新增 `packVersion` 语义、也不造一条代理版本行（那是第二事实来源）。

    字段刻意与 `TemplatePackVersion` 对齐的那几项同名（`reference_ids` / `bindings`），
    因为下游 `_usage_for_asset()` 只认「按位置对齐的 bindings」这一个形状；多造一套
    就等于让同一段提示词拼装写两遍。
    """

    pack_id: str
    name: str
    reference_ids: list[str]
    #: `[{"role": "front", "description": "..."}]`，与 `reference_ids` **按位置对齐**。
    bindings: list[dict[str, str]]
    #: v2 封装没有这两项（它们是模板封装的「附加约束 / 补充说明」）。留空字符串是为了
    #: 让 `_materialize_inputs()` 不必按快照类型分叉——它只判断「有没有内容」。
    features: str = ""
    instructions: str = ""
    wardrobe_mode: str = "preserve"
    #: v2 封装没有「运营审核过的替换范围」这一层元数据（`PACK_SCOPES` 是模板封装的
    #: 字段）。这里显式给全量，而不是让 `_check_scope()` 对 v2 分支短路：短路会让
    #: 「产品替换必须选范围」这条规则在一条路径上悄悄消失。
    supported_scopes: tuple[str, ...] = ()


#: 一次任务「参考图从哪来」的两种冻结形状。刻意用 `TypeAlias` 而不是给下游传
#: `Any`：两者共用 `reference_ids` / `bindings` / `features` / `instructions` /
#: `wardrobe_mode` / `supported_scopes` 这六个属性，少了哪一个 mypy 会当场报，
#: 而不是等到某个分支才 AttributeError。
PackSnapshot: TypeAlias = "TemplatePackVersion | LibraryPackSnapshot"

# 准备是全模块最重的 IO 端点（物化素材 + Node 编译 + 复制运行时），却只靠日志
# 记录成败；失败率高时没有任何数字能看出来。命名沿用本模块既有的 `replacement_`
# 前缀（`worker.RECOVERY_LOCK_GIVEUPS` 就是这一族），别另起 `template_`。
PREPARE_TOTAL = METRICS.counter(
    "replacement_prepare_total",
    "替换任务准备（POST /replacement-runs）的成功与失败次数",
    labels=("result",),
)
COMPILE_SECONDS = METRICS.histogram(
    "replacement_compile_seconds",
    "提示词编译（Node 子进程）耗时，含超时与失败",
    buckets=(0.2, 0.5, 1, 2, 5, 10, 20, 30),
)

# 参考图在提示词里的角色名。封装里的 `kind` 决定它替换的是什么，
# 这张表把「业务分类」翻译成「编译器认识的角色」。
_REFERENCE_ROLE = {"character": "identity", "product": "target_product", "scene": "target_scene"}

#: 暂存表里原片用的 token。固定引用的 token 是 `{{Mixed N}}`，这里必须与它们
#: 不可能重名——`stage_library_refs()` 的返回表同时装着两者，重名会互相覆盖。
SOURCE_TOKEN = "source"

# 非终态：启动恢复要扫这些行（状态机见 models.RUN_STATUSES 的注释）
UNFINISHED = ("preparing", "prepared", "queued", "waiting_free", "waiting_executor", "running")

# 评价可以反复改：只留最近 N 次结论，别把这一行写成一本日志（旧实现同样截 20 条）
REVIEW_HISTORY_LIMIT = 20


class ReplacementService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.runs = RunRepository(session)
        self.packs = PackRepository(session)
        self.storage: ArchiveStorage = get_archive_storage()

    # ------------------------------------------------------------------ #
    # 读
    # ------------------------------------------------------------------ #
    async def catalog(self, owner: str) -> ReplacementCatalog:
        from modules.template.pack_service import PackService

        runs = await self.list_runs(owner)
        packs = await PackService(self.session).available()
        return ReplacementCatalog(runs=runs, packs=packs, channels=channel_catalog())

    async def list_runs(self, owner: str) -> list[ReplacementRunOut]:
        """列表**包含**回收站里的记录：前端「回收站」页签读的就是这一份。

        `deleted_at` 由出参带出去，前端按它分页签。过滤掉的话回收站永远是空的，
        而恢复按钮也就没有入口——`list_owned(include_deleted=False)` 是任务中心的
        口径（那里删掉的记录不该再出现），不是这里的。
        """
        rows = await self.runs.list_owned(owner, include_deleted=True)
        await self._warm_template_names(rows)
        reviews = await self._review_map([row.id for row in rows])
        return [self._run_out(row, reviews.get(row.id)) for row in rows]

    async def detail(self, run_id: str, owner: str) -> ReplacementRunOut:
        row = await self.runs.get_or_404(run_id, owner=owner)
        await self._warm_template_names([row])
        return self._run_out(row, await self.runs.review(row.id))

    async def _warm_template_names(self, rows: Sequence[ReplacementRun]) -> None:
        """给「只有 `videoTemplateId`、没有名字」的老档案批量补名字。

        `_run_out` 是同步投影，不能在里面查库（要么把整条投影链路改成 async，
        要么退化成 N+1）。所以在读入口预热一次缓存，投影只读缓存。
        """
        wanted: list[str] = []
        for row in rows:
            spec = row.prompt_spec
            if not isinstance(spec, dict) or spec.get("videoTemplateName"):
                continue
            template_id = spec.get("videoTemplateId")
            if isinstance(template_id, str) and template_id:
                wanted.append(template_id)
        if wanted:
            await video_template_service.prefetch_names(self.session, wanted)

    async def feed_records(self, owner: str) -> list[ReplacementFeedRecord]:
        """任务中心只读镜像：不读提示词与完整输入摘要，只取列表要的字段。

        **不含回收站里的记录**：任务中心是「正在发生什么」，删除是用户主动
        把它从这里收走，再出现就白删了。
        """
        rows = await self.runs.list_owned(owner)
        records: list[ReplacementFeedRecord] = []
        for row in rows:
            summary = row.input_summary or {}
            assets = summary.get("assets") or []
            source_name = next(
                (item.get("name") for item in assets if item.get("role") == "source_video"),
                None,
            )
            records.append(
                ReplacementFeedRecord(
                    id=row.id,
                    kind=row.kind,
                    target=row.target,
                    status=row.status,
                    created_at=row.created_at,
                    title=_display_title(row, source_name),
                    source_name=source_name or "原片已不在",
                    progress=row.progress_percent,
                    task_id=row.task_id,
                    output_url=self._feed_output_url(row),
                    last_error=row.last_error,
                    deleted_at=row.deleted_at,
                )
            )
        return records

    def _feed_output_url(self, run: ReplacementRun) -> str | None:
        """任务中心的成片地址，判据与 `output()` 一致。

        只认 `output_key` 的话，老档案（成片还在工作区、只是没上传归档存储）
        在列表里会被标成「没有结果」，点开却能下载——同一条记录两个答案。
        这里按 `output_key` → 工作区文件的顺序判断，与下载接口同源。
        """
        if self._output_available(run):
            return f"/api/v1/templates/replacement-runs/{run.id}/output"
        return None

    # ------------------------------------------------------------------ #
    # 准备
    # ------------------------------------------------------------------ #
    async def prepare(
        self, payload: PrepareReplacement, actor: CurrentUser, *, can_read_assets: bool = True
    ) -> ReplacementRunOut:
        """`actor` 而不是 `owner: str`：文本素材的可读性要按当前主体的共享范围
        判（`{{Text N}}` 不能把别人的私有文案抄进提示词），光有一个 id 判不了。

        计数在外层：`_prepare` 内部有十来处提前 raise（权限、参数、渠道、授权闸），
        只在它自己的 `except` 处加指标会漏掉这些分支。
        """
        try:
            out = await self._prepare(payload, actor, can_read_assets=can_read_assets)
        except Exception:
            PREPARE_TOTAL.inc(result="failed")
            raise
        PREPARE_TOTAL.inc(result="ok")
        return out

    async def _prepare(
        self, payload: PrepareReplacement, actor: CurrentUser, *, can_read_assets: bool = True
    ) -> ReplacementRunOut:
        owner = actor.id
        if not can_read_assets:
            raise Forbidden("需要素材读取权限")
        require_channel(payload.channel)
        # 两套参考来源（模板封装 / 资产库封装）**互斥**：它们的 id 形状完全一样
        # （都是 32 位 hex），允许同时填就等于让「谁优先」变成隐式行为，手滑一次
        # 就换掉整条片子的参考图，而且不会报错。
        if payload.library_pack_id and any(
            (
                payload.pack_id,
                payload.pack_version,
                payload.reference_ids,
                payload.extra_pack_ids,
                payload.extra_pack_versions,
            )
        ):
            raise BadRequest(
                "资产库封装与模板素材包不能同时选择：一次任务只能有一个参考来源"
            )

        # 文本快照在受理期冻结：编译器不收花括号，所以这里只做「校验 + 冻结」，
        # 真正的注入在 `_materialize_inputs` 编译完之后（见那里的注释）。
        prompt_source = "\n".join(
            str(value or "")
            for value in (
                payload.scene_prompt,
                payload.replacement_scope,
                payload.notes,
            )
        )
        text_refs = await freeze_text_refs(
            self.session,
            actor,
            payload.text_library_ids,
            prompt=prompt_source,
        )

        # 必须带已发布版本：散图路径在 HTTP 入口一律拒绝，脚本/测试用
        # `require_pack=False` 的旧入口已随重建移除（见文档的「刻意保留的差异」）。
        # 视频模板按**原片**套用：工作台里换产品包，模板照样生效 —— 提示词不动，
        # 图片按「用途」（主图/三视图/细节）从当前素材包里取。没有模板的普通原片
        # （例如 0507 高端姐）走原来的通用提示词，行为一个字都不变。
        #
        # 模板行里存的是原片 id。旧素材库下线后原片只有一个字段（`sourceLibraryId`），
        # 不再有「两套 id 取哪个」的判断。
        video_template = await video_template_service.find_for_run(
            payload.source_library_id or "",
            payload.pack_id,
            payload.kind,
            payload.video_template_id,
            session=self.session,
        )
        text_scene = video_template is not None and is_scene_text(video_template.rules)
        # 参考来源的两条互斥取值：模板封装的冻结版本，或资产库封装展开出的快照。
        # 下游（`_materialize_inputs` / `_usage_for_asset`）只认它们共同的形状：
        # `reference_ids` + 按位置对齐的 `bindings`。
        frozen: PackSnapshot | None = None
        if text_scene:
            assert video_template is not None
            validate_options(video_template.prompt, video_template.rules)
            if payload.kind != "scene" or any(
                (
                    payload.pack_id,
                    payload.pack_version,
                    payload.reference_ids,
                    payload.extra_pack_ids,
                    payload.extra_pack_versions,
                    payload.library_pack_id,
                    payload.replacement_scope,
                    payload.notes,
                )
            ):
                raise BadRequest("文字换场景只接收原视频和场景描述，不附带素材包、散图或其他参数")
            description = scene_description(
                payload.scene_prompt
                if payload.scene_prompt is not None
                else video_template.rules.get("scenePromptDefault")
            )
            payload = payload.model_copy(
                update={
                    "target": "文字换场景",
                    "scene_prompt": description,
                    "wardrobe_mode": "preserve",
                }
            )
            # 显式标注：三个分支各给一份空值，字面量分别推断会打架（mypy `var-annotated`
            # / `no-redef`）；`set()` 还会被推成 `set[Any]`。
            roles: dict[str, str] = {}
            _pack_owners: set[str] = set()
            character_fronts: list[str] = []
            selected_packs: list[dict[str, Any]] = []
        else:
            if payload.scene_prompt is not None:
                raise BadRequest("当前功能不支持编辑场景描述，请选择文字换场景功能")
            if payload.library_pack_id:
                # B 直接选 A 在资产库封的包：展开 → 逐个成员判可读 → 用途对词表。
                # 展开结果**当场冻结**（`snapshot`），之后 A 改成员不影响这次任务。
                snapshot = await self._library_pack_inputs(
                    payload.library_pack_id, payload.kind, actor
                )
                payload = payload.model_copy(
                    update={
                        "target": snapshot.name,
                        "reference_ids": list(snapshot.reference_ids),
                        "wardrobe_mode": snapshot.wardrobe_mode,
                    }
                )
                # v2 封装没有「运营审核过的替换范围」这一层元数据（那是模板封装的
                # 字段），所以不按包限制；`replacement_scope` 仍必填，它进提示词变量。
                _check_scope(list(snapshot.supported_scopes), payload, video_template)
                roles = dict(
                    zip(
                        snapshot.reference_ids,
                        (binding["role"] for binding in snapshot.bindings),
                        strict=True,
                    )
                )
                # 资产库封装没有「归属包」这个概念：成员可能分散在多人手里，
                # 逐个成员的可读性已在 `_library_pack_inputs()` 判完，这里不按
                # 归属再判一次（会连带把「A 共享给我的封装」判死）。
                _pack_owners, character_fronts, selected_packs = set(), [], []
                frozen = snapshot
                if video_template is not None:
                    _check_video_template(
                        video_template, roles, payload.kind, character_fronts, library_pack=True
                    )
            else:
                if not payload.pack_id or not payload.pack_version:
                    raise BadRequest("必须选择专员发布的对象与版本，不能直接使用散图")
                frozen = await self.packs.get_version(payload.pack_id, payload.pack_version)
                pack = await self.packs.get_or_404(payload.pack_id)
                if frozen is None or pack.status != "published":
                    raise BadRequest("素材包未发布或已停用，请重新选择")
                if pack.kind != payload.kind:
                    raise BadRequest("素材包不存在或类型不匹配")
                payload = payload.model_copy(
                    update={
                        "target": pack.name,
                        "reference_ids": list(frozen.reference_ids),
                        "wardrobe_mode": frozen.wardrobe_mode,
                    }
                )
                _check_scope(frozen.supported_scopes, payload, video_template)
                (
                    payload,
                    roles,
                    _pack_owners,
                    character_fronts,
                    selected_packs,
                ) = await self._selected_pack_inputs(
                    payload,
                    video_template,
                    pack,
                    frozen,
                )
                if video_template is not None:
                    _check_video_template(video_template, roles, pack.kind, character_fronts)

        inputs = await self._validate_inputs(
            payload, owner, scope_required=video_template is None
        )
        from modules.template.template_references import resolve_pack_references
        from modules.template.video_template_schema import VideoTemplatePackReferenceIn

        fixed_data = video_template.rules.get("fixedReferences", []) if video_template else []
        _, fixed_refs = await resolve_pack_references(
            self.session,
            [VideoTemplatePackReferenceIn.model_validate(item) for item in fixed_data],
            actor,
        )
        # v2 固定引用与 v1 一样占参考图名额：两者最终都进 mixedList，按图数上限算，
        # 不能因为「来源不同」就多给额度。
        wanted = len(inputs.refs_v2) + len(fixed_refs)
        if wanted > MAX_REFERENCE_IMAGES:
            # 保存端按「封装时那个包」的图数校验，这里按「本次所选包」算，
            # 两者可能不同；报错必须说清怎么自助恢复，否则用户只会看到模板明明存下了却跑不了。
            raise BadRequest(
                f"当前素材包与固定引用合计最多{MAX_REFERENCE_IMAGES}张参考图片；"
                "换用图片更多的素材包会超出上限，请减少固定引用或换回图片更少的素材包"
            )
        run = await self.runs.get(payload.request_id.hex)
        fingerprint = _fingerprint(owner, payload)
        if run is not None:
            if run.fingerprint != fingerprint:
                raise Conflict("请求标识已被不同配置使用，请重新准备")
            if run.status not in ("preparing", "prepare_failed"):
                return self._run_out(run, await self.runs.review(run.id))
            run.status = "preparing"
            run.last_error = None
            run.error_code = None
        else:
            run = ReplacementRun(
                id=payload.request_id.hex,
                kind=payload.kind,
                target=payload.target,
                owner_id=owner,
                status="preparing",
                fingerprint=fingerprint,
                pack_id=payload.pack_id,
                pack_version=payload.pack_version,
                configuration=payload.model_dump(mode="json"),
                text_json=dumps_text_refs(text_refs),
            )
            await self.runs.add(run)
        # 幂等重入（`prepare_failed` 后带同一 request_id 重试）也要把快照写回：
        # 上一次可能在冻结之后、编译之前失败，不能让新一次的正文与旧行对不上。
        run.text_json = dumps_text_refs(text_refs)
        run.configuration = {**(run.configuration or {}), "fixed_references": fixed_data}
        if isinstance(frozen, LibraryPackSnapshot):
            # 资产库封装**没有**不可变发布版本，冻结只能落在本次任务这一行上：
            # 成员 id / 用途 / 顺序都在这里，之后 A 改包、删成员都不影响这次任务。
            # 不另建「封装版本表」——那是第二事实来源，且会让删除保护永远 409。
            run.configuration = {
                **(run.configuration or {}),
                "library_pack": {
                    "id": frozen.pack_id,
                    "name": frozen.name,
                    "referenceIds": list(frozen.reference_ids),
                    "bindings": [dict(item) for item in frozen.bindings],
                },
            }
        await self.session.commit()

        # 目录 + `run.json` + 索引指针一起建：`run_path()` 认指针，
        # 少一样后面每个接口（详情、执行、取件）都会 404。
        path = await asyncio.to_thread(
            create_workspace,
            run.id,
            {
                "id": run.id,
                "status": run.status,
                "kind": run.kind,
                "target": run.target,
                "owner_id": run.owner_id,
                "billing_mode": run.billing_mode,
                "submit_attempts": run.submit_attempts,
            },
        )
        staged: dict[str, StagedMedia] = {}
        # 固定引用的键就是 `{{Mixed N}}` token；包内参考图没有 token（编号由
        # `_media_in_mixed_order` 按用途排出），所以单独加前缀，免得被当成固定引用
        # 塞进 `spec["fixedReferenceTokens"]`。落库那一步（`replace_inputs`）也要
        # 用到它们，所以放在 try 外面。
        fixed_media: dict[str, RunMedia] = {}
        pack_tokens = {row.id: f"packref:{row.id}" for row in inputs.refs_v2}
        try:
            # 字节在对象存储里：先落到工作区，再走归档流水线（`storage.link` 优先
            # 硬链接，同盘零拷贝）。原片也走这里——它一样要有本地文件才能 link
            # 进 `inputs/reference-video.mp4`。
            pending: dict[str, LibraryAssetRef] = dict(fixed_refs)
            if inputs.source_v2 is not None:
                pending[SOURCE_TOKEN] = inputs.source_v2
            pending.update({pack_tokens[row.id]: row for row in inputs.refs_v2})
            staged = await stage_library_refs(path, pending)
            # 原片那个 token 只用于「本次取哪份字节」，**不能进固定引用表**：
            # 它会被写进 `spec["fixedReferenceTokens"]` 并交给编译器当 `{{Mixed N}}` 用。
            fixed_media = {
                token: item for token, item in staged.items() if token in fixed_refs
            }
            # 包内参考图换成已落地的副本，顺序与 `frozen.reference_ids` 一致：
            # 后面的用途/描述是按位置对齐 `frozen.bindings` 取的。
            refs = [staged[pack_tokens[row.id]] for row in inputs.refs_v2]
            source = staged.get(SOURCE_TOKEN)
            if source is None:  # pragma: no cover - `_validate_inputs` 已保证非空
                raise BadRequest("请选择要替换的原片")
            # 文件 IO 与 subprocess 放线程池：它们会阻塞几十毫秒到几秒，
            # 在事件循环里跑会把同进程的其他请求一起卡住。
            spec, compiled, summary = await asyncio.to_thread(
                _materialize_inputs,
                self.storage,
                path,
                payload,
                source,
                refs,
                frozen,
                video_template,
                roles,
                character_fronts,
                selected_packs,
                text_refs,
                fixed_media,
            )
        except Exception:
            logger.exception("替换输入准备失败 run_id=%s", run.id)
            run = await self.runs.get_or_404(run.id)
            run.status = "prepare_failed"
            run.error_code = "PREPARATION_FAILED"
            run.last_error = "输入准备未完成，请检查素材或编译器后用同一配置继续"
            await self.runs.save(run)
            await self.session.commit()
            raise
        finally:
            # 只删本次下载的副本：零拷贝那条路径指向内容寻址的 blob，删了等于删素材。
            await asyncio.to_thread(discard_staged, staged)

        run = await self.runs.get_or_404(run.id)
        run.status = "prepared"
        run.fingerprint = fingerprint
        run.prompt = compiled.prompt
        # 与 `prompt` 同一份字节的摘要：详情页/审计拿它复核「实际发出去的是不是这段」。
        # 只在这一处写（`prepare` 成功分支），别在提交/重试时重算——那会把「发出去的
        # 是哪段」和「现在库里是哪段」混成同一个问题。
        run.prompt_sha256 = compiled.prompt_sha256
        run.prompt_spec = spec
        # `spec["duration"]` 是 `_materialize_inputs` 刚算出来的 int（1..15），
        # 不是外部输入；这里过 `isinstance` 只为把 `dict[str, object]` 收窄成 int，
        # 不引入新的失败分支。
        raw_duration = spec.get("duration")
        run.duration = raw_duration if isinstance(raw_duration, int) else 0
        run.input_summary = summary.model_dump(mode="json")
        run.last_error = None
        run.error_code = None
        await self.runs.replace_inputs(
            run.id,
            [
                RunInputRow(
                    asset_id=item.id,
                    role="source_video" if item is source else _REFERENCE_ROLE[payload.kind],
                    position=position,
                )
                for position, item in enumerate(
                    _media_in_mixed_order(video_template, source, refs, roles, character_fronts,
                                          fixed_refs=fixed_media)
                )
            ],
        )
        await self.runs.save(run)
        await self.session.commit()
        # 投影一次：`prepare` 只归档不提交，但执行器（以及手工排查的人）读的是
        # `run.json`，它必须反映「已准备好、尚未提交」。
        from modules.template import worker

        await asyncio.to_thread(worker.sync_projection, run)
        return self._run_out(run, await self.runs.review(run.id))

    # ------------------------------------------------------------------ #
    # 执行
    # ------------------------------------------------------------------ #
    async def execute(
        self, run_id: str, owner: str, billing_mode: str | None = None
    ) -> ReplacementRunOut:
        from modules.template import worker

        current = await self.detail(run_id, owner)
        if current.deleted_at:
            raise Conflict("请先从回收站恢复任务，再继续执行")
        # 先恢复已落盘的提交回执：已有任务只续查，不再消费素材授权。
        # 数据库尚未记下 task_id 时，也不能把已经付费的调用当成新提交。
        run = await worker.recover_submission(self.session, run_id, owner)
        if not run.task_id:
            await self._assert_still_authorized(run_id, owner)
        # 先落计费模式再排队：执行器读的是行上的 billing_mode，
        # 反过来的话这一轮准备仍会按免费队列估价，加速就白点了。
        if billing_mode:
            await worker.set_billing_mode(self.session, run_id, owner, billing_mode)
        await worker.schedule(self.session, run_id, owner)
        return await self.detail(run_id, owner)

    async def reconfirm(
        self, run_id: str, owner: str, *, confirm_charged: bool
    ) -> ReplacementRunOut:
        """确认「上一次提交可能已经花了钱」后放行重提。

        没有这个出口时 `submission_unknown` 是永久死锁：排队拦、提交直接 return、
        恢复定格，三处都把路堵死。**先写审计再复位**——这一步本身就是
        「有人拍板再花一次钱」，留痕比成功更重要。
        """
        from modules.template import worker

        run, cleared = await worker.reconfirm_state(
            self.session, run_id, owner, confirm_charged=confirm_charged
        )
        await audit.record(
            "template.reconfirm",
            actor_id=owner,
            target_type="replacement_run",
            target_id=run_id,
            detail={
                "confirmedCharged": confirm_charged,
                "kind": run.kind,
                "target": run.target,
                "clearedSubmitAttempts": cleared,
            },
        )
        await self.session.commit()
        if run_channel(run.configuration) != "libtv":
            await asyncio.to_thread(worker.archive_bailian_unknown_receipt, run.id)
        await worker.schedule(self.session, run_id, owner)
        return await self.detail(run_id, owner)

    async def set_deleted(self, run_id: str, owner: str, deleted: bool) -> ReplacementRunOut:
        """进/出回收站。**幂等**：已经是目标状态时什么都不写。

        每次都刷新 `deleted_at` 看着无害，实际有两个后果：回收站按删除时间排序，
        双击一次就跳到最前；前端拿到的 `deletedAt` 每次都不一样，重发请求会被
        当成「又删了一次」。

        **撞车要重来一次**：这一列是「用户随手一点」的写，而轮询每两秒就在改同一行。
        乐观锁（`state_version`）会让输的一方抛 `StaleDataError`——把它直接抛给
        用户，等于「点删除时正在生成」就必失败一次。重读一次几乎总能成功，所以
        重试一次；仍输才报冲突，而不是 500（见 `api/errors.py` 的处理器）。
        """
        for attempt in range(2):
            try:
                return await self._set_deleted_once(run_id, owner, deleted)
            except StaleDataError:
                await self.session.rollback()
                if attempt:
                    raise Conflict("任务状态已变化，请刷新后重试") from None
        raise AssertionError("unreachable")  # pragma: no cover - 循环必然返回或抛出

    async def _set_deleted_once(
        self, run_id: str, owner: str, deleted: bool
    ) -> ReplacementRunOut:
        run = await self.runs.get_or_404(run_id, owner=owner)
        if deleted and run.deleted_at is None:
            run.deleted_at = utcnow()
        elif not deleted and run.deleted_at is not None:
            run.deleted_at = None
        else:
            return self._run_out(run, await self.runs.review(run.id))
        await self.runs.save(run)
        await self.session.commit()
        return await self.detail(run_id, owner)

    async def review(
        self, run_id: str, payload: ReplacementReviewIn, owner: str
    ) -> ReplacementRunOut:
        run = await self.runs.get_or_404(run_id, owner=owner)
        if run.status not in ("success", "failed"):
            raise Conflict("成片尚未完成，暂不能评价")
        if run.status == "success" and not self._output_available(run):
            # 成片在评价之前被清理掉：没有可比对的画面，收下的结论只是记账。
            raise Conflict("成片尚未保存，暂不能评价")
        row = await self.runs.review(run.id)
        if row is None:
            row = ReviewRow(run_id=run.id, status="pending", version=0)
        if row.version != payload.expected_version:
            raise Conflict("评价已变化，请刷新后重试")

        event = ReplacementReviewEvent(
            status=payload.status,
            version=row.version + 1,
            reason_codes=payload.reason_codes,
            comment=payload.comment,
            reviewed_by=owner,
            reviewed_at=utcnow(),
        )
        # history 只放**这一条之前**的结论：出参里 `status` 已经是当前值，
        # 再把当前塞进 history，前端时间线最后一条会重复，版本号也对不上。
        # 第一版之前是「还没有人评价」的 pending（version 0），照样记一笔——
        # 前端按 history 的长度画修订序号，缺了它序号会从 0 变 1 对不上。
        previous = {
            "status": row.status,
            "version": row.version,
            "reasonCodes": list(row.reason_codes or []),
            "comment": row.comment or "",
            "reviewedBy": row.reviewed_by,
            "reviewedAt": row.reviewed_at.isoformat() if row.reviewed_at else None,
        }
        history = [*(row.history or []), previous]
        # 评价可以反复改，不封顶会把这一行写成一本日志（旧实现同样截到 20 条）。
        row.status = payload.status
        row.version = event.version
        row.reason_codes = list(payload.reason_codes)
        row.comment = payload.comment
        row.reviewed_by = owner
        row.reviewed_at = event.reviewed_at
        row.history = history[-REVIEW_HISTORY_LIMIT:]
        await self.runs.upsert_review(row)
        await self.session.commit()
        return await self.detail(run_id, owner)

    # ------------------------------------------------------------------ #
    # 取件
    # ------------------------------------------------------------------ #
    async def input_file(self, run_id: str, asset_id: str, owner: str) -> Path:
        run = await self.runs.get_or_404(run_id, owner=owner)
        rows = await self.runs.inputs(run.id)
        match = next((row for row in rows if row.asset_id == asset_id), None)
        if match is None:
            raise NotFound("输入素材不存在")
        path = run_path(run.id) / "inputs"
        candidates = sorted(path.glob("reference-*")) + sorted(path.glob("reference-video.*"))
        # 按 position 定位：文件名是 `reference-<序号>`，序号来自 position+1。
        target = (
            next((p for p in candidates if p.name.startswith("reference-video")), None)
            if match.role == "source_video"
            else next(
                (p for p in candidates if p.name.startswith(f"reference-{match.position + 1}.")),
                None,
            )
        )
        if target is None or not target.is_file():
            raise NotFound("输入文件不存在")
        return target

    async def input_preview(self, run_id: str, asset_id: str, owner: str) -> Path:
        # input_file 先做本人任务 + 当次输入成员校验，不能凭素材 id 越权取件。
        source = await self.input_file(run_id, asset_id, owner)
        rows = await self.runs.inputs(run_id)
        row = next(item for item in rows if item.asset_id == asset_id)
        kind = "video" if row.role == "source_video" else "image"
        from modules.template.prompt_media import input_thumbnail

        return await asyncio.to_thread(input_thumbnail, source, kind)

    async def output(self, run_id: str, owner: str) -> Served:
        """成片取件：归档存储优先，工作区兜底。

        `output_key` 是「已经搬进归档存储」的凭证。没有它不代表没成片——重建前
        的老档案、以及上传失败的那些，文件仍在工作区里，本地兜底让它们照样能播，
        只是换台机器打不开。远端驱动必须走 307：几百 MB 的视频中转一次就是
        「内存 × 并发数」，而对象存储自己会处理 Range，能直接拖进度条。
        """
        run = await self.runs.get_or_404(run_id, owner=owner)
        if run.output_key:
            key = run.output_key
            url = await asyncio.to_thread(self.storage.download_url, key)
            if url is None:
                with self.storage.materialize(key) as path:
                    return Served.local(path, "video/mp4")
            if not await asyncio.to_thread(self.storage.exists, key):
                raise NotFound("成片字节不存在，请重新生成")
            return Served.remote(url, "video/mp4")
        result = run_path(run.id) / "outputs/generated.mp4"
        if not result.is_file():
            raise NotFound("原始结果尚未下载完成")
        return Served.local(result, "video/mp4")

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    async def _library_pack_inputs(
        self, pack_id: str, kind: str, actor: CurrentUser
    ) -> LibraryPackSnapshot:
        """资产库 v2 封装 → 本次任务的参考图清单（顺序即 `{{Mixed 2}}` 起的编号）。

        三道判据都不能省，缺一条就是一个「看得见却不能用」或者「能用但不该用」：

        1. **封装本身可读**：`expand_packs()` 只返回调用者现在能读的封装；拿别人的
           id 来展开得到空。与「不存在」合并成同一句话——不能告诉调用方这个封装
           确实存在（那就成了一把探测别人资产的口子）。
        2. **成员逐个可读**：封装可读 ≠ 成员可读。打包时可以把别人共享给你的一张图
           收进自己的包，之后共享被撤销，包还在、图不该再被用。`readable` 与
           `readable_ids()` 是同一条判据（含「靠封装成员身份读到」这条入口），
           这里不另写一套：重写必然漂移，而漂移的方向总是「多给权限」。
        3. **用途必须落在词表里**：v2 的 `role` 是自由字符串、历史封装可能全空，
           模板侧只认 `PACK_ROLES[kind]`。认不出就**报错并让人回封装页补**，
           绝不按 `position` 猜 `front/multiview/detail` —— `{{Mixed N}}` 与用途
           绑死，猜错的结果是「拿细节图当主图」的成片，而且不报错。
        """
        lookup = get_library_lookup()
        pack = (await lookup.get_many(self.session, [pack_id])).get(pack_id)
        members = (await lookup.expand_packs(self.session, actor, [pack_id])).get(pack_id)
        # 回收站里的封装仍然「可读」（owner 自己看得到），但不能再被拿去生成：
        # 与 v1「封装被停用」同一个口径，用户要做的动作都是「重新选一个」。
        if (
            pack is None
            or pack.kind != LibraryAssetKind.PACK
            or pack.state != LibraryAssetState.ACTIVE
            or members is None
        ):
            raise BadRequest("所选封装不存在或已停止共享，请重新选择")

        vocabulary = PACK_ROLES.get(kind) or {}
        ids: list[str] = []
        bindings: list[dict[str, str]] = []
        seen: dict[str, str] = {}
        for position, member in enumerate(members, 1):
            label = member.name or f"第{position}张图"
            if (
                member.kind != LibraryAssetKind.IMAGE
                or member.state != LibraryAssetState.ACTIVE
                or not member.readable
            ):
                raise BadRequest(
                    f"封装《{pack.name}》里的「{label}」已删除或已停止共享，"
                    "请重新选择封装或换一张图"
                )
            role = member.role.strip()
            if role not in vocabulary:
                # 空用途与「词表里没有」合并成一句话：用户要做的事一样（回封装页
                # 选一个用途），分成两句只会让人以为其中一种是数据坏了。
                raise BadRequest(
                    f"封装《{pack.name}》里的「{label}」没有声明可用途，"
                    "替换时不知道该把它当主图还是细节图；"
                    "请到资产库封装页给它选一个用途后再来使用"
                )
            if role in seen:
                # 同一用途两张图：`{{Mixed N}}` 是按用途取位的，取哪张都算猜。
                raise BadRequest(
                    f"封装《{pack.name}》里有不止一张「{vocabulary[role]}」"
                    f"（「{seen[role]}」与「{label}」），无法确定用哪一张；"
                    "请到资产库封装页改成不同用途"
                )
            seen[role] = label
            ids.append(member.id)
            bindings.append({"role": role, "description": member.description})
        if not ids:
            raise BadRequest(f"封装《{pack.name}》里没有图片，请先添加成员")
        if len(ids) > MAX_REFERENCE_IMAGES:
            # 与 v1 同一个上限：最终都进 mixedList，来源不同不该多给额度。
            raise BadRequest(
                f"封装《{pack.name}》有 {len(ids)} 张图，超过单次最多"
                f"{MAX_REFERENCE_IMAGES}张参考图片；请精简封装后再使用"
            )
        return LibraryPackSnapshot(
            pack_id=pack_id,
            name=pack.name,
            reference_ids=ids,
            bindings=bindings,
            supported_scopes=tuple(PACK_SCOPES),
        )
    async def _selected_pack_inputs(self, payload, template, primary, frozen):
        """每个已选人物包各贡献一个身份槽位；所有参考与用途以发布快照为准。"""
        from modules.template.schema import aligned_bindings
        from modules.template.video_template_bindings import character_slots

        count = character_slots(template)
        ids = [payload.pack_id, *payload.extra_pack_ids]
        if len(set(ids)) != len(ids):
            raise BadRequest("同一张人物卡不能填多个槽位，请为每位人物选不同的人")
        if payload.extra_pack_ids and (payload.kind != "character" or count <= 1):
            raise BadRequest("产品、场景和单人替换只允许选择一个素材包")
        if len(ids) != count:
            raise BadRequest(f"当前模板需要按出镜顺序选择 {count} 个人物素材包")
        if payload.extra_pack_versions and len(payload.extra_pack_versions) != len(
            payload.extra_pack_ids
        ):
            raise BadRequest("额外人物包版本必须与选择数量、顺序一致")

        chosen = [(primary, frozen)]
        versions = []
        for index, pack_id in enumerate(payload.extra_pack_ids):
            pack = await self.packs.get_or_404(pack_id)
            if (
                pack.status != "published"
                or pack.deleted_at is not None
                or pack.kind != payload.kind
            ):
                raise BadRequest("额外人物包未发布、已停用或类型不匹配")
            version = (
                payload.extra_pack_versions[index] if payload.extra_pack_versions else pack.version
            )
            snapshot = await self.packs.get_version(pack_id, version)
            if snapshot is None:
                raise BadRequest("额外人物包的所选发布版本不存在，请重新选择")
            versions.append(version)
            chosen.append((pack, snapshot))

        all_ids, fronts, metadata = [], [], []
        roles, owners = {}, set()
        for index, (pack, snapshot) in enumerate(chosen, 1):
            ref_ids = list(snapshot.reference_ids)
            bindings = aligned_bindings(payload.kind, ref_ids, snapshot.bindings)
            if count > 1:
                front = next(
                    (
                        asset_id
                        for asset_id, binding in zip(ref_ids, bindings, strict=True)
                        if binding.role == "front"
                    ),
                    None,
                )
                if front is None or front in fronts:
                    raise BadRequest(f"第 {index} 位人物缺少独立的正面身份图，请重新选择")
                fronts.append(front)
            for asset_id, binding in zip(ref_ids, bindings, strict=True):
                if asset_id in roles and roles[asset_id] != binding.role:
                    raise BadRequest("不同素材包对同一图片的用途冲突，请重新选择")
                if asset_id not in roles:
                    all_ids.append(asset_id)
                roles[asset_id] = binding.role
            owners.add(pack.owner_id)
            metadata.append(
                {
                    "slot": index,
                    "packId": pack.id,
                    "packVersion": snapshot.version,
                    "name": pack.name,
                    "referenceIds": ref_ids,
                    "bindings": [binding.model_dump() for binding in bindings],
                }
            )
        if len(all_ids) > 10:
            raise BadRequest("所选素材包合计超过10张参考图，请选择更精简的包；不会静默遗漏图片")
        return (
            payload.model_copy(
                update={
                    "reference_ids": all_ids,
                    "target": "、".join(pack.name for pack, _ in chosen),
                    "extra_pack_versions": versions,
                }
            ),
            roles,
            owners,
            fronts,
            metadata,
        )

    async def _validate_inputs(
        self,
        payload: PrepareReplacement,
        owner: str,
        *,
        scope_required: bool = True,
    ) -> ResolvedInputs:
        """校验原片与参考图。判据只有三条：可读、有效（active）、类型对。

        原片与参考图都来自资产库，不再有「两套 id 空间」的判断。
        `referenceIds` 里装的是**所选封装发布版本**的图（`prepare` 已用
        `frozen.reference_ids` 覆盖过），所以只校验它们本身。

        **不查分类**：资产库没有 `category`，角色/场景/产品由封装时的用途绑定表达；
        反推素材类型会把「别人的封装借给我用」判死。
        """
        if not payload.source_library_id:
            raise BadRequest("请选择要替换的原片")
        actor = CurrentUser(id=owner, name=owner)
        assets = await resolve_assets(
            self.session, [payload.source_library_id], actor=actor
        )
        row = assets.v2.get(payload.source_library_id)
        # 「不存在」与「不可读」合并成同一句话：后者不该告诉调用方这张片确实存在。
        if (
            row is None
            or row.state != LibraryAssetState.ACTIVE
            or payload.source_library_id not in assets.readable_v2
        ):
            raise NotFound("原片不存在或已停止共享")
        if row.kind != LibraryAssetKind.VIDEO:
            raise BadRequest("请选择已归档的原片")
        duration = (row.duration_ms or 0) / 1000
        if not 1 <= duration <= 15 or not row.has_audio:
            raise BadRequest("原片需为带音轨的完整 MP4，时长 1–15 秒；不会自动截断")

        if len(set(payload.reference_ids)) != len(payload.reference_ids):
            raise BadRequest("参考素材不能重复")
        refs_v2: list[LibraryAssetRef] = []
        if payload.reference_ids:
            ref_assets = await resolve_assets(
                self.session, payload.reference_ids, actor=actor
            )
            for asset_id in payload.reference_ids:
                ref_row = ref_assets.v2.get(asset_id)
                if ref_row is None or asset_id not in ref_assets.readable_v2:
                    raise BadRequest("参考图不存在或已删除")
                if (
                    ref_row.state != LibraryAssetState.ACTIVE
                    or ref_row.kind != LibraryAssetKind.IMAGE
                ):
                    raise BadRequest("参考图需为资产库中有效的图片")
                refs_v2.append(ref_row)
        texts = (payload.target, payload.notes, payload.replacement_scope)
        if any("{{" in text or "}}" in text for text in texts):
            raise BadRequest("请使用普通文字，不要手写素材引用标记")
        if not payload.target.strip() or (
            scope_required and payload.kind == "product" and not payload.replacement_scope.strip()
        ):
            raise BadRequest("请填写目标名称并选择产品替换范围")
        return ResolvedInputs(source_v2=row, refs_v2=refs_v2)

    def _output_available(self, run: ReplacementRun) -> bool:
        """成片还在不在：归档存储优先，工作区兜底（与 `output()` 同一套判断）。

        `output_key` 有值说明搬进归档存储了；没有则看工作区里那份——老档案、
        以及上传失败的那些，字节仍在本地。
        """
        if run.output_key:
            try:
                return bool(self.storage.exists(run.output_key))
            except (OSError, UpstreamError):
                return False
        # 归档根没挂上（`root()` 会抛 UpstreamError）、指针丢了（NotFound）、
        # 目录不可读（OSError）都按「没有成片」处理：列表里一条记录不该让整页 500。
        try:
            return (run_path(run.id) / "outputs/generated.mp4").is_file()
        except (NotFound, OSError, UpstreamError):
            return False

    async def _assert_still_authorized(self, run_id: str, owner: str) -> None:
        """提交前的最终授权：原片还可用、封装还已发布、档案还能提交。

        旧位置在 `process.run_node`（执行器的 submit 阶段），那里只有
        文件系统。细节与三处调用点见 `authorization.authorization_gap`——这里
        只负责把它变成用户看得懂的 400。
        """
        run = await self.runs.get_or_404(run_id, owner=owner)
        gap = await authorization_gap(self.session, run)
        if gap is not None:
            raise BadRequest(gap)

    async def _review_map(self, run_ids: list[str]) -> dict[str, ReviewRow]:
        """批量取评价：列表页逐条查会变成 N+1（旧实现逐条读 `review.json`）。

        返回的是**行**而不是出参：转换统一在 `_run_out` 里做一次。这里若先转成
        `schema.ReplacementReview`，`_run_out` 就得同时接受「行」和「出参」两种
        类型（详情接口走 `runs.review()` 拿的是行），类型上只能写成联合类型，
        调用方从此分不清手里是什么。
        """
        if not run_ids:
            return {}
        from sqlalchemy import select

        stmt = select(ReviewRow).where(ReviewRow.run_id.in_(run_ids))
        rows = (await self.session.scalars(stmt)).all()
        return {row.run_id: row for row in rows}

    def _run_out(self, run: ReplacementRun, review: ReviewRow | None) -> ReplacementRunOut:
        summary = (
            ReplacementInputSummary.model_validate(run.input_summary) if run.input_summary else None
        )
        prompt_mode, template_name = _prompt_origin(run.prompt_spec)
        if summary is not None:
            from modules.template.prompt_media import enrich_input_summary

            summary = enrich_input_summary(summary, run.prompt_spec)
        return ReplacementRunOut(
            id=run.id,
            channel=run_channel(run.configuration),
            channel_label=channel_label(run_channel(run.configuration)),
            kind=run.kind,  # type: ignore[arg-type]
            target=run.target,
            status=run.status,
            archive_path=_archive_path(run.id),
            created_at=run.created_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
            elapsed_sec=elapsed_seconds(run),
            prompt=run.prompt,
            prompt_sha256=run.prompt_sha256,
            text_library_ids=run.text_library_ids,
            text_bindings=run.text_bindings,
            prompt_mode=prompt_mode,
            video_template_name=template_name,
            display_title=_display_title(run, _source_name(summary) if summary else None),
            task_id=run.task_id,
            estimated_credits=run.estimated_credits,
            credits=run.credits,
            progress=run.progress_percent,
            review_status=(review.status if review else "pending"),
            review=_review_out(review) if review else None,
            input_summary=summary,
            last_error=run.last_error,
            output_url=self._feed_output_url(run),
            deleted_at=run.deleted_at,
            billing_mode="credits" if run.billing_mode == "credits" else "free",
        )


# --------------------------------------------------------------------------- #
# 纯函数
# --------------------------------------------------------------------------- #
def _fingerprint(owner: str, payload: PrepareReplacement) -> str:
    # Omitting the historical default preserves stable request IDs created before channels.
    exclude = {"channel"} if payload.channel == "libtv" else set()
    return hashlib.sha256((owner + payload.model_dump_json(exclude=exclude)).encode()).hexdigest()


def _role_labels(kind: str) -> dict[str, str]:
    """用途 key → 中文名，用来把「缺哪张图」说成人话。

    必须按素材包类型取：`front` 在产品包是「产品主图」、在人物包是「正面身份图」，
    合并所有词表会让错误提示指错东西（缺主图却说缺身份图）。
    """
    return dict(PACK_ROLES.get(kind) or {})


def _check_video_template(
    template: VideoTemplateOut,
    roles: dict[str, str],
    kind: str,
    character_fronts: list[str] | None = None,
    *,
    library_pack: bool = False,
) -> None:
    """模板按**原片**套用，图片按用途对应：换素材包时提示词与编号都不动。

    所以这里校验的是「当前素材包能不能凑出模板要的那几种用途」，而不是
    某几张固定 asset：素材包换图、升版都不再要求重新封装；凑不齐用途才拦下，
    并且要说清缺的是哪一张。

    `library_pack` 只影响**多人模板**那一句报错：v2 封装的入口一次只能选一个包、
    没有「按人物逐个选」这一步，照抄素材包那句会让用户去点一个不存在的按钮。

    也不必想着「包里多放几张正面图就能凑槽位」：封装层的用途查重不允许一个包里
    有两张「正面身份图」（见 `_library_pack_inputs`），v2 这条路的包里天生只有
    一组人物形象。退一步说，就算放得进去，封装页也没有「第 N 位人物 = 第 N 张图」
    这个声明，按成员顺序对齐就是拿位置猜身份——错配不会报错，只是把第 2 个人
    换成了第 1 个人。
    """
    validate_options(template.prompt, template.rules)
    from modules.template.video_template_bindings import (
        character_slots,
        order_reference_ids,
        reference_roles,
    )

    declared = reference_roles(template.rules, template.prompt, kind)
    slots = character_slots(template)
    if slots > 1 and (character_fronts is None or len(character_fronts) != slots):
        if library_pack:
            raise BadRequest(
                f"这个模板要 {slots} 位出镜人物，资产库封装只能整包套用、按用途取图，"
                "无法按人物分别指定；请改用模板库的人物素材包（每位人物一个），"
                "或换一个单人出镜的模板"
            )
        raise BadRequest("请为每位出镜人物分别选择人物素材包")
    if declared is not None:
        order_reference_ids(list(roles), roles, declared, kind)
    images = [item for item in template.media_order
              if item.type == "image" and item.role != "fixed"]
    provided = set(roles.values())
    needed = dict.fromkeys(
        declared if declared is not None else (item.role for item in images if item.role)
    )
    missing = [role for role in needed if role not in provided]
    if missing:
        labels = _role_labels(kind)
        names = "、".join(labels.get(role, role) for role in missing)
        raise BadRequest(
            f"当前素材包缺少「{names}」，套用不了视频模板「{template.name}」；"
            "请换一个素材包，或取消模板改用通用提示词"
        )
    # 编号上界按**当前包**的图数算（`{{Mixed 1}}` 是原片，其后是 refs）。
    # 按封装时的图片数校验会放过「换了一个图更少的包」这种越界：mixedList 只有
    # N 项，提示词却写着 {{Mixed N+1}}，执行器拿不到那一项。
    upper = len(roles) + 1
    fixed_tokens = {ref["token"] for ref in template.rules.get("fixedReferences", [])}
    bad = [
        token
        for token in re.findall(r"\{\{([^{}]+)\}\}", template.prompt)
        if token != "ReplacementScope" and f"{{{{{token}}}}}" not in fixed_tokens
        and (not re.fullmatch(r"Mixed \d+", token) or not 1 <= int(token[6:]) <= upper)
    ]
    if bad:
        raise BadRequest(
            f"视频模板提示词含越界引用：{'、'.join(bad)}"
            f"（当前素材包共 {len(roles)} 张图，最大可用 {{{{Mixed {upper}}}}}）"
        )


def _media_in_mixed_order(
    template: VideoTemplateOut | None,
    source: RunMedia,
    refs: Sequence[RunMedia],
    roles: dict[str, str] | None = None,
    character_fronts: list[str] | None = None,
    fixed_refs: Mapping[str, RunMedia] | None = None,
) -> list[RunMedia]:
    """mixedList 的顺序：普通原片沿用「参考图 + 原片」；视频模板按**用途**排。

    `{{Mixed 1}}` 永远是原片；第 2 位起按模板声明的用途（主图 / 三视图 / 细节）
    从**当前素材包**里取图。于是同一份提示词配上不同的产品包，`{{Mixed 2}}` 永远
    是那个包的正面主图、`{{Mixed 3}}` 永远是三视图，封装一次就能到处用。
    模板没声明用途、或包里还有没用上的图，按原序补在后面，一张都不少。
    """
    if template is not None and fixed_refs:
        count = int(template.rules["fixedBaseMediaCount"])
        base_template = template.model_copy(update={"media_order": template.media_order[:count]})
        ordered = _media_in_mixed_order(base_template, source, refs, roles, character_fronts)
        if len(ordered) < count:
            raise BadRequest("当前素材包缺少模板基础参考图，无法保持固定引用编号")
        extras = [fixed_refs[item["token"]] for item in template.rules["fixedReferences"]]
        fixed_ids = {item.id for item in extras}
        result = [*ordered[:count], *extras,
                  *(item for item in ordered[count:] if item.id not in fixed_ids)]
        if len({item.id for item in result}) != len(result):
            raise BadRequest("固定参考图与当前素材包图片重复，请调整引用")
        return result
    if template is None:
        return [*refs, source]
    from modules.template.video_template_bindings import (
        character_slots,
        order_reference_ids,
        place_source,
        reference_roles,
    )

    declared = reference_roles(template.rules, template.prompt, template.default_kind)
    if character_slots(template) > 1:
        if not character_fronts or len(character_fronts) != character_slots(template):
            raise BadRequest("请为每位出镜人物分别选择人物素材包")
        by_id = {ref.id: ref for ref in refs}
        if any(asset_id not in by_id for asset_id in character_fronts):
            raise BadRequest("所选人物正面图不在本次输入中")
        images = [by_id[asset_id] for asset_id in character_fronts]
        images.extend(ref for ref in refs if ref.id not in character_fronts)
        return place_source(source, images, template.rules)
    if declared is not None:
        by_id = {ref.id: ref for ref in refs}
        ordered_ids = order_reference_ids(list(by_id), roles or {}, declared, template.default_kind)
        return place_source(source, [by_id[asset_id] for asset_id in ordered_ids], template.rules)
    by_role = roles or {}
    remaining = list(refs)
    # 上面的固定引用分支已给 ordered 赋值，这里不能再带类型注解（mypy no-redef）。
    ordered = []
    for item in template.media_order:
        if item.type != "image":
            continue
        picked = None
        if item.role:
            picked = next((ref for ref in remaining if by_role.get(ref.id) == item.role), None)
        if picked is None and item.asset_id:
            # 没写用途的老模板 / 用途对不上时，退回按封装时那张图
            picked = next((ref for ref in remaining if ref.id == item.asset_id), None)
        if picked is not None:
            remaining.remove(picked)
            ordered.append(picked)
    return [source, *ordered, *remaining]


def _display_title(run: ReplacementRun, source_name: str | None) -> str:
    """统一展示名：规则在 `titles.py`，这里只负责把数据库行翻译成参数。"""
    return task_name(source_name, run.kind, run.target, run.id)


def _prompt_origin(spec: dict[str, object] | None) -> tuple[str, str | None]:
    """这条提示词是「封装时写死的」还是「按素材现编的」，以及出自哪条模板。

    **为什么要在档案里回答这个**：两种提示词长得一样，用户没法从文本判断
    自己写的那份有没有被改写。`prompt_mode=fixed` 明确告诉他「跑的就是你
    封装时那份原文」；`compiled` 则是按素材与替换范围现编的。

    `videoTemplateName` 是 prepare 时写进 spec 的；早期档案只有 id，
    这时才去模板库里反查一次（模板库是本机 JSON，查不到就只报 fixed）。

    `spec` 可能是 `None`，也可能是**字符串**（历史档案、手工导入的行直接存了
    JSON 文本）。这里只认字典，别的形状一律当「按素材现编」——列表接口不该
    因为一条老档案的字段形状把整页打成 500。
    """
    if not isinstance(spec, dict) or spec.get("promptMode") != "fixed_video_template":
        return "compiled", None
    name = spec.get("videoTemplateName")
    if isinstance(name, str) and name.strip():
        return "fixed", name.strip()
    template_id = spec.get("videoTemplateId")
    if isinstance(template_id, str) and template_id:
        return "fixed", _template_name(template_id)
    return "fixed", None


def _template_name(template_id: str) -> str | None:
    """老档案只存了模板 id：从缓存反查名字，读不到就当没有（展示层自己降级）。

    缓存由调用方（列表 / 详情入口）先 `prefetch_names()` 批量预热——`_run_out`
    是同步投影，不能在这里查库，否则要么把整条投影链路改成 async，
    要么退化成逐条查询（一个列表页 N 次）。
    """
    return video_template_service.template_name(template_id)


def _archive_path(run_id: str) -> str | None:
    """工作区路径：查得到就给，查不到也不能把整页拖下水。

    列表/详情回答的是「有哪些任务、各自什么状态」。一条历史记录的工作区被清掉
    （归档 GC、换机器、从备份只恢复了数据库）时，`run_path()` 按设计抛 404
    —— 但那一行本身还在库里，用户仍要能看到状态、看到当时提交的提示词、从回收站
    恢复它。这里把「目录没了」降级成 `None`，由前端照实标注，而不是让
    `GET /replacement-catalog` 整页 404：一条坏记录不该连带另外几十条打不开。
    """
    try:
        return str(run_path(run_id))
    except AppError:
        logger.debug("归档目录定位失败 run_id=%s", run_id, exc_info=True)
        return None


def _source_name(summary: ReplacementInputSummary) -> str | None:
    return next(
        (item.name for item in summary.assets if item.role == "source_video"),
        None,
    )


def _check_scope(
    scopes: list[str], payload: PrepareReplacement, template: VideoTemplateOut | None = None
) -> None:
    """产品替换位置必须落在素材包已审核的范围内，运营不能临时扩大。"""
    if template is not None:
        allowed = scope_options(template.rules)
        chosen = [item.strip() for item in payload.replacement_scope.split("、") if item.strip()]
        if not allowed and not chosen:
            return
        if not allowed or not chosen or any(item not in allowed for item in chosen):
            raise BadRequest("请选择当前提示词封装的替换范围：" + "、".join(allowed))
        return
    if payload.kind != "product":
        return
    allowed = [PACK_SCOPES[scope] for scope in scopes if scope in PACK_SCOPES]
    if not allowed:
        raise BadRequest("素材包未声明可替换范围，请先在封装里补齐后重新发布")
    chosen = [item.strip() for item in payload.replacement_scope.split("、") if item.strip()]
    if not chosen:
        raise BadRequest("请选择产品替换位置（素材包已审核：" + "、".join(allowed) + "）")
    beyond = [item for item in chosen if item not in allowed]
    if beyond:
        raise BadRequest("替换范围超出素材包已审核的支持范围：" + "、".join(allowed))


def _readable_source(item: RunMedia) -> Path:
    """拿到这份素材**当前**可读的本地路径。

    prepare 之前已经把它落到工作区，本身就是可读文件——不能再走一次
    `ArchiveStorage.materialize()`：那会把这个路径当成归档 key 解析到别的根目录下，
    取到的要么不存在、要么是另一个同名文件。
    """
    return item.path


def _materialize_inputs(
    storage: ArchiveStorage,
    path: Path,
    payload: PrepareReplacement,
    source: RunMedia,
    refs: Sequence[RunMedia],
    frozen,
    video_template: VideoTemplateOut | None = None,
    roles: dict[str, str] | None = None,
    character_fronts: list[str] | None = None,
    selected_packs: list[dict] | None = None,
    text_refs: Sequence[Mapping[str, Any]] = (),
    fixed_refs: Mapping[str, RunMedia] | None = None,
) -> tuple[dict[str, object], Compilation, ReplacementInputSummary]:
    """把输入落到工作区并编译提示词。**同步函数**：调用方放进线程池。

    这一段刻意不碰数据库：它慢（物化 + Node 编译）、可能抛各种 IO 异常，
    混进事务会让「一次准备」持有数据库连接好几秒。

    `video_template` 非空时走固定提示词通道：媒体顺序按模板声明的**用途**从当前
    素材包里挑图，最终文案用模板原文；`bindings` / `mixedList` 仍由通用编译器按
    同一顺序产出，执行器那套「顺序、类型、摘要」校验一个都不绕过。
    """
    ensure_layout(path)
    media = _media_in_mixed_order(video_template, source, refs, roles, character_fronts,
                                fixed_refs=fixed_refs)
    bindings: list[dict[str, str]] = []
    for index, item in enumerate(media):
        original = _readable_source(item)
        filename = (
            "reference-video.mp4"
            if item is source
            else f"reference-{index + 1}{original.suffix}"
        )
        # 不重算 sha256：素材 key 就是摘要，物化出的字节与它同源。
        # 原先一次 prepare 会把同一个 300MB 原片读三遍（校验、copy、copy 后校验）。
        storage.link(original, path / "inputs" / filename, item.sha256)
        binding = {
            "id": item.id,
            "role": "source_video" if item is source else _REFERENCE_ROLE[payload.kind],
            "type": item.type,
            "file": f"inputs/{filename}",
        }
        # 同一用途下的多张图靠 usage 区分：主图定外观、多视图只补结构、细节图只补局部。
        usage_text = _usage_for_asset(item, refs, frozen, payload.kind)
        if selected_packs and item is not source:
            details = []
            for group in selected_packs:
                if item.id not in group["referenceIds"]:
                    continue
                position = group["referenceIds"].index(item.id)
                usage_binding = group["bindings"][position]
                text = usage_for(payload.kind, usage_binding["role"])
                if usage_binding.get("description"):
                    text += "；" + usage_binding["description"]
                if character_fronts:
                    text = f"第{group['slot']}位人物（{group['name']}）：{text}"
                details.append(text)
            usage_text = "；".join(details)
        if usage_text:
            binding["usage"] = usage_text
        bindings.append(binding)

    # 执行器会给 inputs 下每个文件算一遍 sha256；同一份原片存两份等于白算。
    storage.link(
        path / "inputs/reference-video.mp4",
        path / "inputs/source-original.mp4",
        source.sha256,
    )

    # 编译器（Node）对用户输入一律拒绝 `{{` / `}}`，最终提示词里也只认
    # `{{Mixed N}}`。所以 `{{Text N}}` 先换成不含花括号的占位串，编译完再换回
    # 正文——否则一段 Markdown 文案里恰好写了 `{{...}}` 会让整次准备失败。
    variables = {
        "state_constraints": mask_text_refs(payload.notes, text_refs),
        "replacement_scope": mask_text_refs(payload.replacement_scope, text_refs),
    }
    if frozen is not None and (frozen.features or frozen.instructions):
        variables["state_constraints"] = "\n".join(
            text
            for text in (
                frozen.features,
                frozen.instructions,
                mask_text_refs(payload.notes, text_refs),
            )
            if text
        )
    if payload.kind == "character":
        variables.update(
            source_subject=(
                f"原片中按槽位对应的{len(character_fronts)}位人物"
                if character_fronts
                else "原片唯一前景主角，背景人物不替换"
            ),
            identity_description=(
                f"各位目标人物依次为{payload.target}，每组辅助图只用于对应人物。"
                if character_fronts
                else f"目标人物为{payload.target}，多张参考属于同一人。"
            ),
        )
    elif payload.kind == "product":
        variables.update(
            source_product="原片中指定展示的产品",
            target_product=payload.target,
            replacement_scope=payload.replacement_scope
            or ("按封装提示词指定的范围" if video_template is not None else ""),
        )
    else:
        variables.update(
            target_scene=payload.target,
            protected_foreground="原片人物、商品、手持物与所有交互前景对象",
        )

    duration = min(15, max(1, round(source.duration)))
    # 这里刻意从 `str` 起步：模板可以覆盖成任意画幅，不能把变量钉死成请求模型的 Literal。
    aspect_ratio = str(payload.aspect_ratio)
    if video_template is not None:
        # 封装时定下的档位优先：样片 9:16 / 5 秒，和模板里的 generation 对齐。
        generation = video_template.generation
        aspect_ratio = str(generation.get("ratio") or video_template.aspect_ratio or aspect_ratio)
        configured = generation.get("duration")
        if isinstance(configured, int):
            duration = min(15, max(2, configured))
    spec: dict[str, object] = {
        "task": payload.kind,
        "duration": duration,
        "aspect_ratio": aspect_ratio,
        "wardrobe_mode": payload.wardrobe_mode,
        "references": bindings,
        "variables": variables,
    }
    if video_template is not None and is_scene_text(video_template.rules):
        spec["inputMode"] = "video_scene_text"
        spec["fixedReferenceTokens"] = list(fixed_refs or {})
        variables["scene_prompt"] = mask_text_refs(payload.scene_prompt or "", text_refs)
    if selected_packs:
        spec["selectedPacks"] = selected_packs
    if video_template is not None:
        spec["promptMode"] = "fixed_video_template"
        spec["videoTemplateId"] = video_template.id
        # 名字一起存进档案：列表和弹窗要回答「这条提示词是谁写的」，
        # 只给 id 的话得反查模板库，而模板库是本地 JSON，换台机器就没有了。
        spec["videoTemplateName"] = video_template.name
        spec["fixedPromptSha256"] = video_template.prompt_sha256
        spec["fixed_prompt"] = video_template.prompt
    compile_started = perf_counter()
    try:
        compiled = compile_prompt(spec, node=get_settings().node_binary)
    finally:
        # finally 而不是 else：超时与失败也要能看到「编译卡了多久」，
        # 那正是需要排查的那一次。
        COMPILE_SECONDS.observe(perf_counter() - compile_started)
    if payload.channel != "libtv":
        from core.prompt_refs import translate_prompt

        spec["channel"] = payload.channel
        compiled.prompt = translate_prompt(compiled.prompt, bindings)
        compiled.compiler_version += "+bailian-references-v1"

    # 编译已结束，占位串在这里换成冻结的正文。档案 `prompt.txt` 与请求体
    # （`run.prompt` → GenerationRecord.prompt）用的是同一份字符串，不会出现
    # 「档案里还是 {{Text 1}}、实际发出去是别的文案」。
    compiled.prompt = inject_masked_text_refs(compiled.prompt, text_refs)
    # **所有后处理都在上面**，这一份字节就是发出去的那一份。落盘与摘要都从同一个
    # `bytes` 出来，顺序也刻意是「先编码、再同时写盘和算摘要」：
    # - 编码走 `encode_text`（所有文本写盘的唯一出口），它在这里拦掉 U+0000——
    #   PG 的 text 列存不下 0x00，而先写盘再落库时，盘上会留一份没人引用的字节，
    #   执行器还会在一个跟文案毫无关系的位置失败；
    # - 摘要算的是**写下去的那份字节**，不是再 `.encode()` 一次（同一份字符串两次
    #   编码虽然结果一样，但那是巧合，不是契约）。
    prompt_bytes = encode_text(compiled.prompt)
    compiled.prompt_sha256 = hashlib.sha256(prompt_bytes).hexdigest()
    (path / "inputs/prompt.txt").write_bytes(prompt_bytes)
    storage.write(path / "inputs/prompt-spec.json", spec)
    storage.write(path / "inputs/configuration.json", payload.model_dump(mode="json"))
    # 这两份是**执行器**的输入，不是给人看的：`bridge.mjs` 读 input-map 逐条上传、
    # 再从 media-inputs 取宽高时长拼请求体，最后拿 run.json 的 `file_checksums`
    # 与实算摘要比对。少一份它就在 upload 阶段直接抛错，而那时钱还没花、
    # 用户只看到「上传准备未完成」，无从判断是缺文件。
    storage.write(
        path / "inputs/input-map.json",
        [
            {**binding, "sha256": item.sha256, "name": item.name}
            for binding, item in zip(bindings, media, strict=True)
        ],
    )
    storage.write(
        path / "records/media-inputs.json",
        {
            binding["file"]: {
                "width": item.width,
                "height": item.height,
                "duration": item.duration,
            }
            for binding, item in zip(bindings, media, strict=True)
        },
    )
    storage.write(
        path / "records/prompt-compilation.json",
        compiled.model_dump(),
    )
    # 编译器快照：代码改了行为却没变时，靠它证明「当时跑的是哪一版」
    shutil.copytree(RUNTIME, path / "code/runtime", dirs_exist_ok=True)
    shutil.copyfile(BRIDGE, path / "code/runtime/bridge.mjs")

    summary = ReplacementInputSummary(
        assets=[
            ReplacementInputAsset(
                id=item.id,
                name=item.name,
                role=binding["role"],
                type=item.type,
                # 指向**归档快照**而不是素材本身：素材后来被删或换了内容，评价页
                # 仍要能打开「当初喂给模型的那张图」，否则人工验收失去依据。
                url=(
                    f"/api/v1/templates/replacement-runs/{payload.request_id.hex}/inputs/{item.id}"
                ),
            )
            for item, binding in zip(media, bindings, strict=True)
        ],
        cover_reference_id=payload.reference_ids[0] if payload.reference_ids else None,
        wardrobe_mode=payload.wardrobe_mode,
        replacement_scope=payload.replacement_scope,
        notes=payload.notes,
        scene_prompt=payload.scene_prompt,
    )
    from modules.template.prompt_media import enrich_input_summary

    summary = enrich_input_summary(summary, spec)
    (path / "00_本次调用说明.md").write_text(
        f"# {payload.target} · {payload.kind}\n\n输入已归档，尚未提交。\n"
        "inputs 保存实际文件副本；outputs 保存原始成片；records 保存状态和错误分类。\n"
        "提交前须取得估价回执（0 积分畅用队列须报 0，积分队列须报正数）；\n"
        "人工验收与生成完成分别记录。\n",
        encoding="utf-8",
    )
    return spec, compiled, summary


def _usage_for_asset(item: RunMedia, refs: Sequence[RunMedia], frozen, kind: str) -> str:
    """这张图在提示词里的说明：用途文字 + 封装里为它写的补充说明。"""
    try:
        index = refs.index(item)
    except ValueError:
        return ""
    if index >= len(frozen.bindings):
        return ""
    binding = frozen.bindings[index]
    if not isinstance(binding, dict):
        binding = PackBinding.model_validate(binding).model_dump()
    text = usage_for(kind, str(binding.get("role") or ""))
    detail = str(binding.get("description") or "").strip().rstrip("。.；;")
    if detail:
        text = f"{text}；{detail}" if text else detail
    return text


def _review_out(row: ReviewRow) -> ReplacementReview:
    return ReplacementReview(
        status=row.status,
        version=row.version,
        reason_codes=list(row.reason_codes or []),  # type: ignore[arg-type]
        comment=row.comment,
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
        history=[ReplacementReviewEvent.model_validate(item) for item in (row.history or [])],
    )
