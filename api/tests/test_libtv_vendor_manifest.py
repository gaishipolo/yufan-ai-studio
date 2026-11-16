"""vendor 快照必须与 `manifest.json` 的哈希逐字节一致。

桥接层（`runtime/bridge.mjs`）每次执行前做同一份校验，不一致就以 `RELEASE_INVALID`
拒绝执行 —— 症状是**所有 LibTV 替换任务在 upload 阶段失败**，报
`Vendored adapter changed: ...`，而 `git status` 干干净净，很难往换行符上想。

真发生过两次：
1. 目录从 `infra/ai/libtv_runtime/` 搬到 `modules/model_driver/libtv/runtime/`（61cdf87），
   `.gitattributes` 里的 `-text` 规则没跟着搬 → `core.autocrlf=true` 的机器把快照检出成
   CRLF → 字节哈希全变（2026-11-16 任务 40e9da57 就是这么失败的）；
2. 有人改了适配器、忘了刷新清单。

所以这里既比字节，也在「只差换行符」时把话说清楚，免得下次又排查一遍。
"""

from __future__ import annotations

import hashlib
import json

from modules.model_driver.libtv.process import VENDOR


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_vendor_files_match_manifest() -> None:
    manifest = json.loads((VENDOR / "manifest.json").read_text(encoding="utf-8"))
    files: dict[str, str] = manifest["files"]
    assert files, "manifest.json 没有 files 列表，桥接层会直接拒绝启动"

    bad: list[str] = []
    for name, expected in files.items():
        path = VENDOR / name
        assert path.is_file(), f"清单列出的文件不存在：{name}"
        raw = path.read_bytes()
        if _sha256(raw) == expected:
            continue
        hint = ""
        if _sha256(raw.replace(b"\r\n", b"\n")) == expected:
            hint = (
                "（只差换行符：vendor 被检出成 CRLF。检查 .gitattributes 里 "
                "`api/src/modules/model_driver/libtv/runtime/vendor/** -text` 这条规则"
                "——目录搬家后规则没跟就会这样，修复：改规则后删掉该目录重新 checkout）"
            )
        bad.append(f"  {name}{hint}")

    assert not bad, (
        "vendor 与 manifest.json 不一致，桥接层会以 RELEASE_INVALID 拒绝执行所有 LibTV 任务：\n"
        + "\n".join(bad)
    )
