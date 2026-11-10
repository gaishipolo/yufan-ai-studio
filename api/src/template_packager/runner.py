"""可恢复封装流程；只发布素材和模板，从不创建或执行生成任务。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlencode
from uuid import NAMESPACE_URL, uuid5

from modules.template.pack_roles import BASE_ROLE
from template_packager.client import PackagingClient
from template_packager.manifest import LoadedManifest, digest_file


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
    actor = api.check_server(sharing=spec.video.shared)
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

        def upload(file: Path, category: str) -> dict:
            digest = loaded.digests[file]
            key = f"{category}:{digest}"
            previous = state["assets"].get(key)
            if previous:
                asset = api.request("GET", f"/assets/{previous['id']}")
            else:
                # 防止预检后、执行前文件被改，摘要不能与实际上传字节脱节。
                if digest_file(file) != digest:
                    raise ValueError("配置或素材已变化，请重新预检")
                asset = api.upload(file, category)
            if (
                asset.get("deletedAt")
                or asset.get("sha256") != digest
                or asset.get("category") != category
            ):
                raise ValueError("已登记素材状态或摘要与配置不一致，请核对素材库")
            state["assets"][key] = {"id": asset["id"]}
            checkpoint()
            return asset

        source = upload(spec.video.file, "source")
        if not source.get("hasAudio") or not 1 <= source.get("duration", 0) <= 15:
            raise ValueError("原片需为1–15秒且含音轨；已上传素材保留供核对")
        if spec.video.shared and not source.get("shared"):
            source = api.request(
                "PUT",
                f"/assets/{source['id']}/sharing",
                json={"expectedVersion": source["stateVersion"], "shared": True},
            )

        managed = {
            item["id"]: item for item in api.request("GET", "/templates/replacement-packs")["items"]
        }
        packs = {}
        assets = {}
        for pack in spec.packs:
            refs = [upload(ref.file, pack.kind) for ref in pack.references]
            assets[pack.key] = {
                ref.key: asset for ref, asset in zip(pack.references, refs, strict=True)
            }
            request_id = uuid5(namespace, "pack:" + pack.key)
            payload = {
                "requestId": str(request_id),
                "expectedRevision": 0,
                "name": pack.name,
                "kind": pack.kind,
                "referenceIds": [ref["id"] for ref in refs],
                "coverReferenceId": refs[0]["id"],
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
                comparable = [
                    "name",
                    "kind",
                    "referenceIds",
                    "coverReferenceId",
                    "wardrobeMode",
                    "bindings",
                    "supportedScopes",
                ]
                if any(current.get(key) != payload[key] for key in comparable):
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
            pack = packs[function.pack]
            function_refs = assets[function.pack]
            prompt = function.prompt or ""
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
                "sourceDurationSec": source["duration"],
                "defaultKind": pack["kind"],
                "packId": pack["id"],
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
                    "kind": pack["kind"],
                    "packId": pack["id"],
                    "packVersion": pack["version"],
                    "sourceScope": "public" if source.get("shared") else "mine",
                }
            )
            functions.append(
                {
                    "key": function.key,
                    "name": function.name,
                    "videoTemplateId": template["id"],
                    "packId": pack["id"],
                    "packVersion": pack["version"],
                    "promptSha256": hashlib.sha256(prompt.encode()).hexdigest(),
                    "mediaOrder": template["mediaOrder"],
                    "workbenchPath": "/templates?" + query,
                }
            )
            state["functions"] = functions
            checkpoint()
        report = {
            "key": spec.key,
            "fingerprint": loaded.fingerprint,
            "sourceId": source["id"],
            "sourceName": source["name"],
            "shared": source.get("shared", False),
            "functions": functions,
        }
        state["report"] = report
        checkpoint()
        return report
