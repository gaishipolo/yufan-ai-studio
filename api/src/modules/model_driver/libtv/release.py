"""vendor 快照与 `manifest.json` 的发布一致性校验。

三处需要同一套判断，就在这里放一份实现：

- **桥接层**（`runtime/bridge.mjs::verifyVendor`）在 Node 里做同一件事，不一致就以
  `RELEASE_INVALID` 拒绝执行 —— 它拿不到本模块，逻辑只能各写一遍；
- **监督进程**（`process.py`）在启动 Node 之前先查一遍：Node 那边的报错只剩一个
  码，落到用户眼前是「提交生成失败：素材上传准备未完成：RELEASE_INVALID」，
  既没有文件名也没有「该动哪里」，而这个仓库已经被它坑过两次；
- **pytest**（`tests/test_libtv_vendor_manifest.py`）在 CI 里拦回归。

## 为什么值得单独拦一道

2026-11-17 的真实故障：同一套 Postgres 队列与运行工作区被**两个 checkout** 共用
（主仓库 + 一个 11 月 10 日的老工作树，结构还是 `infra/ai/libtv_runtime/`），
任务被哪边接走就看谁先抢到 job。老那边 vendor 与清单对不上，于是同一个提示词
时而成功、时而 `RELEASE_INVALID`。桥接层当时只说了一个码，排查成本极高。

所以这里的失败出口要带三样东西：**哪个文件**、**期望/实际哈希**、
**下一步该做什么**（刷新清单 / 确认是不是有第二个 checkout 在跑）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

__all__ = ["VendorMismatch", "describe_mismatches", "verify_vendor"]

#: 清单只允许指向 vendor 目录**里面**的文件。写成 `../` 或绝对路径一律拒绝：
#: 清单是仓库内的常量，能指到目录外就说明它已经不该被信任。
_MAX_DETAIL = 400


@dataclass(frozen=True)
class VendorMismatch:
    """一个文件与清单不一致。`line_ending_only` 用来把「换行符被检出成 CRLF」分开。"""

    name: str
    expected: str
    actual: str
    line_ending_only: bool = False

    @property
    def hint(self) -> str:
        if self.line_ending_only:
            return (
                "（只差换行符：vendor 被检出成 CRLF。检查 .gitattributes 里 "
                "`api/src/modules/model_driver/libtv/runtime/vendor/** -text` 这条规则，"
                "改完删掉该目录重新 checkout）"
            )
        return "（内容被改过而清单没刷新，或这两个文件不属于同一版本）"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_vendor(vendor: Path) -> list[VendorMismatch]:
    """返回全部不一致项；空列表 = 可以执行。

    清单本身读不出来/没有 `files` 时抛 `OSError` / `ValueError`：那是「发布物缺失」，
    与「某个文件被改过」不是同一类问题，调用方要分开说。
    """
    manifest = json.loads((vendor / "manifest.json").read_text(encoding="utf-8"))
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("manifest.json 没有 files 列表，桥接层会直接拒绝启动")

    base = vendor.resolve()
    mismatches: list[VendorMismatch] = []
    for name, expected in files.items():
        path = (vendor / name).resolve()
        if base not in path.parents:
            raise ValueError(f"清单路径越界：{name}")
        if not path.is_file():
            mismatches.append(VendorMismatch(name=name, expected=str(expected), actual="缺失"))
            continue
        raw = path.read_bytes()
        actual = _sha256(raw)
        if actual == str(expected):
            continue
        mismatches.append(
            VendorMismatch(
                name=name,
                expected=str(expected),
                actual=actual,
                # 只差换行符时给出对症的修法，别让人又去怀疑内容被人改过。
                line_ending_only=_sha256(raw.replace(b"\r\n", b"\n")) == str(expected),
            )
        )
    return mismatches


def describe_mismatches(mismatches: list[VendorMismatch], vendor: Path) -> str:
    """一行能贴进任务详情的说明：文件、期望/实际、下一动作，都给全。"""
    if not mismatches:
        return ""
    head = f"执行器发布物与清单不一致（{vendor}）："
    items = [
        f"{m.name} 期望 {m.expected[:12]} 实际 {str(m.actual)[:12]}{m.hint}"
        for m in mismatches[:3]
    ]
    more = f"；另有 {len(mismatches) - 3} 个文件同样不一致" if len(mismatches) > 3 else ""
    tail = "。刷新 vendor/manifest.json，或确认是不是有第二个 checkout 接了同一个队列"
    return (head + "；".join(items) + more + tail)[:_MAX_DETAIL]
