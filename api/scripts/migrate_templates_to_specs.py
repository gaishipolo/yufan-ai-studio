"""把旧 `video_templates`（连同它的素材包）迁成 `template_specs` **草稿**。

## 为什么需要它

P3 要删掉 `video_templates` / `template_packs*`。删之前得让运营手里那些模板有机会
以新形态继续维护 —— 否则「旧模板全没了」只能手抄一遍。

## 四条硬口径

1. **只增不删**：旧表一行不动，新表只插草稿。跑错了删掉新行即可回滚。
2. **只产草稿**（`status=draft`，不发布）：旧数据里「用哪个模型跑替换」不是模板的一列
   （是运行时按渠道定的），必须由人来确认，所以 `--model` 是必填参数、且必须声明
   `replacement=True`。
3. **素材结构归资产库**：旧素材包的成员按 `position`/`role`/`description` 原样落成一个
   `kind=pack` 的资产库封装，spec 里用**一个** `source=pack, binding=fixed` 槽位引用它。
   不把成员摊成多个 fixed 槽位 —— 那等于让模板重新拥有一份"这几张属于同一对象"的结构。
4. **幂等**：同一份旧模板重跑只会跳过（按派生出来的 spec key 判），不会长出第二份。

## 为什么在 `scripts/` 而不在模块里

它要同时写**资产库**（建封装）和**规格表**，而业务模块之间不许互相 import。
一次性运维脚本放在 `modules/` 之外，正是为此存在的（先例 `migrate_asset_roles.py`）。

## 用法

    # 先看计划（默认只读）
    ./.venv/Scripts/python.exe scripts/migrate_templates_to_specs.py --model libtv-wan3-replacement

    # 真写
    ./.venv/Scripts/python.exe scripts/migrate_templates_to_specs.py \
        --model libtv-wan3-replacement --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.context import CurrentUser
from core.template_spec import EXECUTION_PROMPT_LIMIT, MAX_SLOTS
from db.session import get_session_factory
from infra import get_storage
from modules.library.models import LibraryAsset
from modules.library.packs import PackService
from modules.library.schema import PackIn, PackItemIn
from modules.template_pack import spec_service
from modules.template_pack.models import (
    TemplatePack,
    TemplatePackReference,
    TemplateSpec,
    VideoTemplate,
)
from modules.template_pack.spec_schema import TemplateSpecIn

#: 旧模板 key 里的非法字符先换成 `-`，再拼前缀。spec key 只允许小写字母开头。
_KEY_OK = re.compile(r"[^a-z0-9_-]+")
#: `ai_<32 位 hex>_product_swap` 这种自动生成的 key：留 `product-swap` 那段才可读。
_AUTO_KEY = re.compile(r"^ai-[0-9a-f]{16,}-(.+)$")
#: 旧提示词里的写死编号。新模型禁止它（编号是派生的），迁移时逐个换回槽位引用。
_MIXED = re.compile(r"\{\{\s*Mixed\s*(\d+)\s*\}\}")
#: 迁移出来的资产库封装名后缀：重跑时靠它认出"这个封装是上次建的"。
PACK_SUFFIX = " · 迁移"


@dataclass
class PackPlan:
    name: str
    items: list[tuple[str, str, str]]  # (asset_id, role, description)
    existing_id: str = ""


@dataclass
class SpecPlan:
    template_id: str
    key: str
    name: str
    model: str
    params: dict[str, Any]
    prompt_editable: str
    slots: list[dict[str, Any]]
    pack: PackPlan | None = None
    notes: list[str] = field(default_factory=list)
    skipped: str = ""


def _spec_key(template_key: str, template_id: str) -> str:
    """派生 spec key：整串放得下就用整串，放不下就取末段 + id 短码。

    **不能粗暴截断**：`ai_<32 位 hex>_product_swap` 截到 40 位会把函数名切掉，
    同一份 manifest 的多个功能会算出同一个 key（实测撞过），迁移时第二个就被当成
    "已存在"跳过了。id 短码保证唯一，末段保留人可读的那部分。
    """
    # 下划线也归一成连字符：`ai_<hex>_product_swap` 只有末段是人可读的，
    # 不归一就 `rsplit` 切不开，整串被当成"末段"。
    full = _KEY_OK.sub("-", template_key.strip().lower()).replace("_", "-").strip("-")
    if full and len(f"mig-{full}") <= 40:
        return f"mig-{full}"
    auto = _AUTO_KEY.match(full)
    tail = (auto.group(1) if auto else full.rsplit("-", 1)[-1]) or "t"
    return f"mig-{tail}"[:32].rstrip("-") + f"-{template_id[:6]}"


def _media_images(media_order: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`media_order` 里除原片以外的项（原片是 `role=source_video` 或第一个 video）。"""
    return [item for item in media_order if str(item.get("type")) != "video"]


def _numbering(slots: list[dict[str, Any]], pack: PackPlan | None) -> dict[int, str]:
    """`{{Mixed N}}` → 槽位引用。**每个编号都要有落点**，否则迁移会把语义改掉。

    旧模型里编号就是 `media_order` 的下标：1 是原片，2 起是参考图。新模型里
    - 单素材槽位（fixed）占 1 个号 → `{{slot:key}}`；
    - 封装槽位占 N 个号（N = 成员数）→ 第 j 个是 `{{slot:key.j}}`。
    用 `.j` 而不是整组 `{{slot:key*}}`：旧提示词常常只引用其中一张
    （"以 {{Mixed 3}} 三视图为准"），整组引用会把话变意思。
    """
    mapping: dict[int, str] = {}
    number = 0
    for slot in slots:
        if slot["source"] == "pack":
            count = len(pack.items) if pack else 0
            for index in range(1, count + 1):
                number += 1
                mapping[number] = f"{{{{slot:{slot['key']}.{index}}}}}"
        else:
            number += 1
            mapping[number] = f"{{{{slot:{slot['key']}}}}}"
    return mapping


def _translate(prompt: str, mapping: dict[int, str]) -> tuple[str, list[int]]:
    """把写死的 `{{Mixed N}}` 换成槽位引用，返回新文本与找不到落点的编号。"""
    missing: list[int] = []

    def repl(match: re.Match[str]) -> str:
        number = int(match.group(1))
        target = mapping.get(number)
        if target is None:
            missing.append(number)
            return match.group(0)
        return target

    return _MIXED.sub(repl, prompt), missing


async def _pack_plan(
    session: AsyncSession, template: VideoTemplate, *, owner_id: str
) -> PackPlan | None:
    if not template.pack_id:
        return None
    pack = await session.get(TemplatePack, template.pack_id)
    if pack is None:
        return None
    refs = list(
        await session.scalars(
            select(TemplatePackReference)
            .where(TemplatePackReference.pack_id == pack.id)
            .order_by(TemplatePackReference.position)
        )
    )
    if not refs:
        return None
    name = f"{pack.name}{PACK_SUFFIX}"
    # 复用上一次跑建出来的封装：名字 + 归属 + kind 三项都吻合才算，避免把别人的
    # 同名封装误认成自己建的。
    existing = await session.scalar(
        select(LibraryAsset).where(
            LibraryAsset.kind == "pack",
            LibraryAsset.name == name,
            LibraryAsset.owner_id == owner_id,
            LibraryAsset.state == "active",
        )
    )
    return PackPlan(
        name=name,
        items=[(ref.asset_id, ref.role or "", ref.description or "") for ref in refs],
        existing_id="" if existing is None else existing.id,
    )


async def plan_all(
    session: AsyncSession, *, model: str, owner_id: str
) -> list[SpecPlan]:
    templates = list(
        await session.scalars(select(VideoTemplate).order_by(VideoTemplate.created_at))
    )
    plans: list[SpecPlan] = []
    for template in templates:
        plan = SpecPlan(
            template_id=template.id,
            key=_spec_key(template.template_key, template.id),
            name=template.name,
            model=model,
            params={},
            prompt_editable=template.prompt,
            slots=[],
        )
        # 幂等只按派生 key 判：同一个旧模板重跑生成的 key 恒定，撞上就跳过。
        # 不比对 attributes 里的来源标记 —— key 本身就是从它派生出来的。
        taken = await session.scalar(
            select(TemplateSpec.id).where(TemplateSpec.spec_key == plan.key)
        )
        if taken:
            plan.skipped = "同名规格已存在（重跑跳过）"

        generation = template.generation if isinstance(template.generation, dict) else {}
        if template.aspect_ratio:
            plan.params["aspectRatio"] = template.aspect_ratio
        duration = generation.get("duration")
        if isinstance(duration, int) and duration > 0:
            plan.params["durationSec"] = duration

        position = 0
        if template.source_video_asset_id:
            plan.slots.append(
                {
                    "key": "source",
                    "type": "video",
                    "source": "asset",
                    "binding": "fixed",
                    "assetId": template.source_video_asset_id,
                    "position": position,
                    "label": "原片",
                }
            )
            position += 1

        pack = await _pack_plan(session, template, owner_id=owner_id)
        if pack is not None:
            plan.pack = pack
            plan.slots.append(
                {
                    "key": "views",
                    "type": "image",
                    "source": "pack",
                    "binding": "fixed",
                    "packId": pack.existing_id or "",  # apply 时回填
                    "position": position,
                    "label": "素材包",
                }
            )
            position += 1
            plan.notes.append(f"素材包 → 资产库封装《{pack.name}》（{len(pack.items)} 张）")
        else:
            # 没有素材包：旧模板里的图各占一个固定槽位。这是**降级**路径（模板重新
            # 拥有一份结构），只在旧模板本身没有"一组同对象的图"时才走到。
            images = _media_images(list(template.media_order or []))
            for index, item in enumerate(images, 1):
                if position >= MAX_SLOTS:
                    plan.notes.append("槽位超上限，剩下的图没迁")
                    break
                plan.slots.append(
                    {
                        "key": f"ref{index}",
                        "type": "image",
                        "source": "asset",
                        "binding": "fixed",
                        "assetId": item.get("assetId") or "",
                        "position": position,
                        "label": item.get("role") or f"参考图 {index}",
                    }
                )
                position += 1
            if images:
                plan.notes.append("没有素材包：参考图按降级路径拆成独立槽位")

        translated, missing = _translate(plan.prompt_editable, _numbering(plan.slots, pack))
        plan.prompt_editable = translated
        if missing:
            plan.skipped = (
                "提示词引用了旧模板里不存在的编号 "
                + "、".join(str(item) for item in sorted(set(missing)))
            )
            plan.notes.append("整条跳过：免得写出一份引用不到的草稿")
        elif translated != template.prompt:
            plan.notes.append("提示词里的 {{Mixed N}} 已换成槽位引用")

        if len(plan.prompt_editable) > EXECUTION_PROMPT_LIMIT:
            plan.notes.append(
                f"提示词 {len(plan.prompt_editable)} 字，超过执行上限 "
                f"{EXECUTION_PROMPT_LIMIT}，发布时会被拒（草稿可以存）"
            )
        plans.append(plan)
    return plans


async def apply_plans(session: AsyncSession, plans: list[SpecPlan], *, owner_id: str) -> None:
    actor = CurrentUser(
        id=owner_id,
        name=owner_id,
        roles=("admin",),
        permissions=frozenset({"*"}),
    )
    storage = get_storage()
    service = PackService(session, storage)
    # 同一次跑里多个模板可能指向同一个旧素材包（同一份封装被两条功能复用）。
    # plan 阶段查库时它们都还没创建，所以这里按名字记一份本轮的账，避免建两遍。
    created_by_name: dict[str, str] = {}
    for plan in plans:
        if plan.skipped:
            print(f"跳过 {plan.key}：{plan.skipped}")
            continue
        if plan.pack is not None:
            # 本轮已经为同名旧包建过封装：直接复用，别再建第二份。
            if not plan.pack.existing_id:
                plan.pack.existing_id = created_by_name.get(plan.pack.name, "")
            if not plan.pack.existing_id:
                created = await service.create(
                    PackIn(
                        name=plan.pack.name,
                        items=[
                            PackItemIn(asset_id=asset_id, role=role, description=description)
                            for asset_id, role, description in plan.pack.items
                        ],
                    ),
                    actor,
                )
                plan.pack.existing_id = created.id
                created_by_name[plan.pack.name] = created.id
                print(f"  建封装 {plan.pack.name} → {created.id}")

        for slot in plan.slots:
            if slot["source"] == "pack":
                slot["packId"] = plan.pack.existing_id if plan.pack else ""
        payload = TemplateSpecIn(
            key=plan.key,
            name=plan.name[:160],
            description=f"由旧视频模板迁移（{plan.template_id}）",
            model=plan.model,
            params=plan.params,
            prompt={"locked": "", "editable": plan.prompt_editable},
            slots=plan.slots,
        )
        saved = await spec_service.save(session, actor, payload)
        print(f"  建草稿 {saved.key}（{len(plan.slots)} 槽位）")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="旧 video_templates → template_specs 草稿"
    )
    parser.add_argument(
        "--model",
        required=True,
        help="旧模板没记模型，必须指定，且该模型须声明 replacement",
    )
    parser.add_argument("--owner", default=settings.demo_user_id, help="新规格与封装的归属账号")
    parser.add_argument("--apply", action="store_true", help="真写；不带则只打印计划")
    parser.add_argument("--json", action="store_true", help="以 JSON 打印计划")
    args = parser.parse_args()

    # 脚本不在 CLI 框架里跑，没人替我们触发模块发现；端口没装配时
    # `get_model_generation()` 会直接抛 Unavailable。CLI 那条路是
    # `cli._ensure_modules()` 保证的，这里显式来一次。
    from api.discovery import active_modules

    active_modules()

    async with get_session_factory()() as session:
        # 先确认这个模型确实能跑替换：否则迁出来的草稿发布也跑不了。
        from core.ports.model_generation import get_model_generation

        catalog = {item.id: item for item in get_model_generation().models()}
        model = catalog.get(args.model)
        if model is None:
            raise SystemExit(f"模型不存在：{args.model}")
        if not model.replacement:
            raise SystemExit(f"{args.model} 没有声明 replacement，不能作为替换模板的模型")

        plans = await plan_all(session, model=args.model, owner_id=args.owner)
        if args.json:
            print(
                json.dumps(
                    [
                        {
                            "key": item.key,
                            "from": item.template_id,
                            "slots": len(item.slots),
                            "pack": item.pack.name if item.pack else "",
                            "skipped": item.skipped,
                            "notes": item.notes,
                        }
                        for item in plans
                    ],
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            for item in plans:
                flag = f"跳过：{item.skipped}" if item.skipped else "待迁移"
                print(f"[{flag}] {item.key} ← {item.name}（{len(item.slots)} 槽位）")
                for note in item.notes:
                    print(f"    · {note}")
        if not args.apply:
            print("\n只读预览。确认无误后加 --apply。")
            return
        await apply_plans(session, plans, owner_id=args.owner)
        await session.commit()
        print("\n完成：以上为新建的草稿（未发布）。")


if __name__ == "__main__":
    asyncio.run(main())
