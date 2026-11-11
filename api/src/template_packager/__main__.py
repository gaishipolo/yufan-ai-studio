"""python -m template_packager manifest.json [--check-server | --apply]。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from template_packager.client import PackagingClient
from template_packager.manifest import load_manifest
from template_packager.reuse import resolve_existing_packs, server_options
from template_packager.runner import apply_manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="将视频、参考图与多项提示词封装为工作台卡片；默认仅本地预检"
    )
    parser.add_argument("manifest", type=Path, help="JSON 配置，素材路径相对于该文件")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--apply", action="store_true", help="封装模板；复用包不传参考图、不发布包，不生成视频"
    )
    modes.add_argument(
        "--check-server", action="store_true", help="只读检查后端契约、账号权限和默认包"
    )
    parser.add_argument("--api-url", default="http://127.0.0.1:8080/api/v1")
    parser.add_argument(
        "--state", type=Path, help="恢复进度文件；默认保存在 api/var/template-packager"
    )
    args = parser.parse_args(argv)
    try:
        loaded = load_manifest(args.manifest)
        if not args.apply and not args.check_server:
            report = {
                "mode": "preflight",
                "key": loaded.spec.key,
                "fingerprint": loaded.fingerprint,
                "packs": len(loaded.spec.packs),
                "functions": [f.key for f in loaded.spec.functions],
                "inputBindings": {f.key: f.bindings for f in loaded.spec.functions},
                "shared": loaded.spec.video.shared,
                "message": "本地预检通过，未联网、未上传",
            }
        else:
            with PackagingClient(args.api_url, os.environ.get("YUFAN_API_TOKEN", "")) as api:
                if args.check_server:
                    me = api.check_server(**server_options(loaded.spec))
                    existing_packs = resolve_existing_packs(loaded.spec, api, {})
                    report = {
                        "mode": "server-check",
                        "ownerId": me["id"],
                        "message": "契约与权限检查通过，未写入",
                        "defaultPacks": {
                            key: {"id": pack["id"], "name": pack["name"]}
                            for key, pack in existing_packs.items()
                        },
                    }
                else:
                    state_key = hashlib.sha256(
                        (api.base_url + ":" + loaded.spec.key).encode()
                    ).hexdigest()[:24]
                    default = (
                        Path(__file__).resolve().parents[2]
                        / "var"
                        / "template-packager"
                        / (state_key + ".json")
                    )
                    report = apply_manifest(loaded, api, (args.state or default).resolve())
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        print(f"封装失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
