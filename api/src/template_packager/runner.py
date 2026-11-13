"""可恢复封装流程；只发布素材和模板，从不创建或执行生成任务。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import NAMESPACE_URL, uuid5

from modules.library.schema import SOURCE_VIDEO_ORIGINS
from modules.template.pack_roles import BASE_ROLE
from template_packager.client import PackagingClient
from template_packager.manifest import LoadedManifest, digest_file
from template_packager.reuse import resolve_existing_packs, server_options


def atomic_json(path: Path, data: dict) -> None:
    descriptor, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def journal(path: Path, identity: dict, fingerprint: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(path.suffix + ".lock")
    try:
        handle = lock.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise ValueError(f"该封装正在执行；若上次进程已退出，请移除锁文件：{lock}") from exc
    try:
        with handle:
            handle.write(str(os.getpid()))
        state = (
            json.loads(path.read_text(encoding="utf-8"))
            if path.exists()
            else {
                "version": 1,
                "identity": identity,
                "fingerprint": fingerprint,
                "assets": {},
                "packs": {},
            }
        )
        if state.get("version") != 1:
            raise ValueError("进度文件版本不支持")
        if state.get("identity") != identity:
            raise ValueError("进度文件属于不同账号或服务，请核对环境与进度文件")
        if state.get("fingerprint") != fingerprint:
            raise ValueError("配置或素材已变化；请使用新的封装 key 和进度文件创建新版本")
        atomic_json(path, state)
        yield state
    finally:
        lock.unlink(missing_ok=True)


def apply_manifest(loaded: LoadedManifest, api: PackagingClient, state_path: Path) -> dict:
    spec = loaded.spec
    actor = api.check_server(**server_options(spec))
    owner = actor["id"]
    identity = {"api": api.base_url, "owner": owner, "key": spec.key}
    namespace = uuid5(NAMESPACE_URL, f"yufan-packager:{owner}:{spec.key}")
    prefix = f"ai_{namespace.hex}_"

    with journal(state_path, identity, loaded.fingerprint) as state:
        existing_templates = api.request("GET", "/templates/video-templates")["items"]
        ours = [item for item in existing_templates if item["templateKey"].startswith(prefix)]
        if any(
            item.get("rules", {}).get("packagingFingerprint") != loaded.fingerprint for item in ours
        ):
            raise ValueError("配置或素材已变化；远端已有相同封装 key，请使用新的 key")

        def checkpoint():
            atomic_json(state_path, state)

        reused = resolve_existing_packs(spec, api, state)
        checkpoint()  # 自动选择结果持久化；后续重试不悄悄换默认包。

        def upload(file: Path, kind: str) -> dict:
            digest = loaded.digests[file]
            key = f"{kind}:{digest}"
            previous = state["assets"].get(key)
            if previous:
                asset = api.request("GET", f"/library/assets/{previous['id']}")
            else:
                # 防止预检后、执行前文件被改，摘要不能与实际上传字节脱节。
                if digest_file(file) != digest:
                    raise ValueError("配置或素材已变化，请重新预检")
                asset = api.upload(file, kind)
            # v2 出参不再暴露 `sha256`（字节寻址是服务端内部细节），所以落地校验
            # 只能比对「类型 + 字节数 + 在库状态」：这三样一起错的可能性远低于
            # 「传错文件」，而真正防篡改的那一关在上面的 `digest_file`。
            if (
                asset.get("state") != "active"
                or asset.get("kind") != kind
                or asset.get("sizeBytes") != file.stat().st_size
            ):
                raise ValueError("已登记素材状态或大小与配置不一致，请核对素材库")
            state["assets"][key] = {"id": asset["id"]}
            checkpoint()
            return asset

        if spec.video.file is not None:
            source = upload(spec.video.file, "video")
        else:
            source = api.request("GET", f"/library/assets/{spec.video.asset_id}")
            if (
                source.get("state") != "active"
                or source.get("kind") != "video"
                or source.get("origin") not in SOURCE_VIDEO_ORIGINS
            ):
                raise ValueError("assetId 必须是当前账号可读取的有效视频原片")
        if not source.get("hasAudio") or not 1 <= (source.get("durationSec") or 0) <= 15:
            raise ValueError("原片需为1–15秒且含音轨；已上传素材保留供核对")
        if spec.video.shared and source.get("visibility") != "team":
            api.share_to_team(source)
            # 共享申请在没带 `library:share:direct` 的账号上只会留下一条待审单，
            # 回读一次才知道到底生效没有——凭 200 当成功会让后面所有函数都用
            # 一个别人看不见的原片建模板。
            source = api.request("GET", f"/library/assets/{source['id']}")
            if source.get("visibility") != "team":
                raise ValueError("原片共享未生效：账号缺少 library:share:direct 或申请仍在审批中")

        managed = (
            {
                item["id"]: item
                for item in api.request("GET", "/templates/replacement-packs")["items"]
            }
            if any(pack.references for pack in spec.packs)
            else {}
        )
        packs = {}
        assets: dict[str, dict[str, Any]] = {}
        for pack in spec.packs:
            if not pack.references:
                packs[pack.key] = reused[pack.key]
                assets[pack.key] = {}
                continue
            refs = [upload(ref.file, "image") for ref in pack.references]
            assets[pack.key] = {
                ref.key: asset for ref, asset in zip(pack.references, refs, strict=True)
            }
            request_id = uuid5(namespace, "pack:" + pack.key)
            payload = {
                "requestId": str(request_id),
                "expectedRevision": 0,
                "name": pack.name,
                "kind": pack.kind,
                "referenceLibraryIds": [ref["id"] for ref in refs],
                "coverLibraryId": refs[0]["id"],
                "wardrobeMode": pack.wardrobe_mode,
                "bindings": [
                    {"role": ref.role, "description": ref.description} for ref in pack.references
                ],
                "requiredRoles": list(dict.fromkeys([BASE_ROLE[pack.kind], *pack.required_roles])),
                "supportedScopes": pack.supported_scopes,
            }
            # requestId 是包主键；丢失本地进度仍能在服务端找到同一草稿/发布版本。
            current = managed.get(request_id.hex)
            if current:
                # 入参与出参的字段名不同：请求叫 `referenceLibraryIds`，响应仍叫
                # `referenceIds`（那是 ReplacementPack 的既有契约）。逐项比对要先
                # 映射一遍，否则每次重跑都会误判成「远端配置已变化」。
                comparable = {
                    "name": "name",
                    "kind": "kind",
                    "referenceIds": "referenceLibraryIds",
                    "coverReferenceId": "coverLibraryId",
                    "wardrobeMode": "wardrobeMode",
                    "bindings": "bindings",
                    "supportedScopes": "supportedScopes",
                }
                if any(
                    current.get(remote) != payload[local]
                    for remote, local in comparable.items()
                ):
                    raise ValueError("远端素材包配置已变化，请使用新的封装 key")
                if not set(payload["requiredRoles"]) <= set(current.get("requiredRoles", [])):
                    raise ValueError("远端素材包必备用途已变化")
                if current["status"] == "disabled":
                    raise ValueError("素材包已停用，请核对后使用新的封装 key")
            else:
                current = api.request("PUT", "/templates/replacement-packs/draft", json=payload)
            if current["status"] != "published":
                current = api.request(
                    "POST",
                    f"/templates/replacement-packs/{current['id']}/publish",
                    json={"expectedRevision": current["revision"]},
                )
            packs[pack.key] = current
            state["packs"][pack.key] = {"id": current["id"], "version": current["version"]}
            checkpoint()

        functions = []
        for function in spec.functions:
            scene_text = function.scene_prompt_default is not None
            function_pack: dict[str, Any] = (
                {"kind": "scene", "id": None, "name": "文字描述", "version": 0}
                if scene_text
                else packs[function.pack or ""]
            )
            function_refs = {} if scene_text else assets[function.pack or ""]
            # HTTP CamelModel 会清理首尾空白；文件提示词与该边界保持一致，
            # 避免文件末尾换行导致重试误判远端配置变化。内部段落保持原文。
            prompt = (function.prompt or "").strip()
            media = [
                {
                    "token": "{{Mixed 1}}",
                    "role": "source_video",
                    "type": "video",
                    "assetId": source["id"],
                    "name": source["name"],
                    "inPrompt": True,
                }
            ]
            for index, key in enumerate(function.media_order, 2):
                ref = function_refs[key]
                token = f"{{{{Mixed {index}}}}}"
                role = next(p for p in spec.packs if p.key == function.pack)
                usage = next(r.role for r in role.references if r.key == key)
                media.append(
                    {
                        "token": token,
                        "role": usage,
                        "type": "image",
                        "assetId": ref["id"],
                        "name": ref["name"],
                        "inPrompt": token in prompt,
                    }
                )
            payload = {
                "templateKey": prefix + function.key,
                "name": f"{spec.name} · {function.name}",
                "prompt": prompt,
                "sourceVideoAssetId": source["id"],
                "sourceVideoName": source["name"],
                "sourceDurationSec": source["durationSec"],
                "defaultKind": function_pack["kind"],
                "packId": function_pack["id"],
                "mediaOrder": media,
                "aspectRatio": function.aspect_ratio,
                "rules": {
                    "workbenchLabel": function.name,
                    "scopeOptions": function.scope_options,
                    "packagingFingerprint": loaded.fingerprint,
                },
                "generation": {"duration": function.duration}
                if function.duration is not None
                else {},
            }
            if function.bindings is not None:
                payload.pop("mediaOrder")
                payload["rules"]["referenceBindings"] = function.bindings
            if scene_text:
                payload["rules"].update(
                    inputMode="video_scene_text", scenePromptDefault=function.scene_prompt_default
                )
            # 服务端在临界区按 templateKey 防重；响应丢失也不会多出第二条。
            existing = next(
                (item for item in ours if item["templateKey"] == payload["templateKey"]), None
            )
            if existing:
                # fingerprint 是来源标签，不代表远端仍保持原配置（如 attach-source）。
                different = any(
                    existing.get(key) != value
                    for key, value in payload.items()
                    if key not in {"rules", "generation", "mediaOrder"}
                )
                for field in ("rules", "generation"):
                    different |= any(
                        existing.get(field, {}).get(key) != value
                        for key, value in payload[field].items()
                    )
                if function.bindings is None:
                    actual_media = existing.get("mediaOrder", [])
                    different |= len(actual_media) != len(media)
                    different |= any(
                        any(actual.get(key) != value for key, value in expected.items())
                        for expected, actual in zip(media, actual_media, strict=False)
                    )
                if different:
                    raise ValueError("远端模板配置已变化，请核对原片、提示词与媒体顺序")
            template = existing or api.request("POST", "/templates/video-templates", json=payload)
            query = urlencode(
                {
                    "sourceId": source["id"],
                    "videoTemplateId": template["id"],
                    "kind": function_pack["kind"],
                    **(
                        {"packId": function_pack["id"], "packVersion": function_pack["version"]}
                        if not scene_text
                        else {}
                    ),
                    "sourceScope": "public" if source.get("visibility") == "team" else "mine",
                }
            )
            functions.append(
                {
                    "key": function.key,
                    "name": function.name,
                    "videoTemplateId": template["id"],
                    "packId": function_pack["id"],
                    "packName": function_pack["name"],
                    "packVersion": function_pack["version"],
                    "promptSha256": hashlib.sha256(prompt.encode()).hexdigest(),
                    "mediaOrder": template["mediaOrder"],
                    **(
                        {
                            "inputMode": "video_scene_text",
                            "scenePromptDefault": function.scene_prompt_default,
                        }
                        if scene_text
                        else {}
                    ),
                    **(
                        {
                            "bindings": function.bindings,
                            "includeAllPackImages": not scene_text,
                            "requiredPackCount": 0
                            if scene_text
                            else max(1, list(function.bindings.values()).count("front"))
                            if function_pack["kind"] == "character"
                            else 1,
                        }
                        if function.bindings is not None
                        else {}
                    ),
                    "workbenchPath": "/templates?" + query,
                }
            )
            state["functions"] = functions
            checkpoint()
        report = {
            "key": spec.key,
            "fingerprint": loaded.fingerprint,
            # 键名跟着契约走：v1 下线后「原片」只有 `sourceLibraryId` 一种叫法。
            # （`workbenchPath` 里的 `sourceId` 是前端路由参数，与这里不是一回事。）
            "sourceLibraryId": source["id"],
            "sourceName": source["name"],
            "shared": source.get("visibility") == "team",
            "functions": functions,
        }
        state["report"] = report
        checkpoint()
        return report
