"""从授权公共目录选择兼容的默认包；不读取别人的私人素材或改动发布包。"""

from __future__ import annotations

from core.exceptions import BadRequest
from modules.template.video_template_bindings import (
    allows_partial_slots,
    ensure_distinct_roles,
    order_reference_ids,
    reference_roles,
)
from template_packager.client import PackagingClient
from template_packager.manifest import Manifest


def server_options(spec: Manifest) -> dict:
    creating = any(pack.references for pack in spec.packs)
    return {
        "sharing": spec.video.shared,
        "creating_packs": creating,
        "uploading": creating or spec.video.file is not None,
        "role_bindings": 2 if any(f.bindings is not None for f in spec.functions) else False,
        "scene_text": any(f.scene_prompt_default is not None for f in spec.functions),
    }


def resolve_existing_packs(spec: Manifest, api: PackagingClient, state: dict) -> dict:
    requested = [pack for pack in spec.packs if not pack.references]
    if not requested:
        return {}
    available = []
    for page in range(1, 1001):
        data = api.request(
            "GET",
            "/templates/replacement-packs/available",
            params={"kind": "all", "page": page, "pageSize": 200},
        )
        available.extend(data["items"])
        if page * 200 >= data["total"]:
            break
        if not data["items"]:
            raise ValueError("素材包目录分页不完整，请刷新后重试")
    else:
        raise ValueError("素材包目录过大，请显式缩小查询范围")
    chosen = {}
    selections = state.setdefault("reusedPacks", {})
    for requested_pack in requested:
        wanted = requested_pack.pack_id or selections.get(requested_pack.key)
        candidates = [
            pack
            for pack in available
            if pack["kind"] == requested_pack.kind
            and (not wanted or pack["id"] == wanted)
            and pack.get("available") is not False
            and pack.get("version", 0) > 0
        ]
        for candidate in sorted(candidates, key=lambda p: (p["name"], p["id"])):
            ids = candidate["referenceIds"]
            bindings = candidate.get("bindings", [])
            if not ids or len(bindings) != len(ids):
                continue
            roles = {
                asset_id: binding["role"] for asset_id, binding in zip(ids, bindings, strict=True)
            }
            try:
                for function in spec.functions:
                    if function.pack != requested_pack.key:
                        continue
                    required = reference_roles(
                        {"referenceBindings": function.bindings},
                        function.prompt or "",
                        candidate["kind"],
                    )
                    try:
                        order_reference_ids(ids, roles, required or [], candidate["kind"])
                    except BadRequest:
                        # 多槽位模板（同一用途声明多次）允许默认包只填一部分：
                        # 剩下的槽位运行时由用户逐个选卡补上。
                        if not allows_partial_slots(required or [], candidate["kind"]):
                            raise
                        ensure_distinct_roles(roles, required or [], candidate["kind"])
            except BadRequest:
                continue
            chosen[requested_pack.key] = candidate
            selections[requested_pack.key] = candidate["id"]
            break
        else:
            raise ValueError(
                f"没有可用且满足声明用途的已发布素材包：{requested_pack.key}；"
                "请核对 packId、类型与图片用途"
            )
    return chosen
