"""变异扫描：逐个破坏框架关键不变量，看现有测试能否发现。

判据：
- 变红（测试失败）= 有守卫，这条不变量是被测试保护着的
- 全绿（测试通过）= 缺口，破坏了也没人知道

每个变异都保证 finally 还原，结束时按 sha256 校验全部文件已复原。
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTEST = ROOT / ".venv" / "Scripts" / "python.exe"


@dataclass(frozen=True)
class Mutant:
    ident: str
    target: str
    old: str
    new: str
    tests: tuple[str, ...]
    why: str


MUTANTS: tuple[Mutant, ...] = (
    Mutant(
        "perm-super",
        "src/core/context.py",
        '        if SUPER_PERMISSION in self.permissions:\n            return True\n',
        "",
        ("tests/test_route_policy.py", "tests/test_identity_api.py"),
        "超管 `*` 不再豁免：超管调任何接口都该 403",
    ),
    Mutant(
        "perm-wildcard",
        "src/core/context.py",
        '        return f"{resource}:*" in self.permissions\n',
        "        return False\n",
        ("tests/test_route_policy.py", "tests/test_catalog.py", "tests/test_permissions.py"),
        "`resource:*` 通配不再生效：有 task:* 却过不了 task:read",
    ),
    Mutant(
        "perm-all-any",
        "src/core/context.py",
        "        return all(self.has(code) for code in codes)\n",
        "        return any(self.has(code) for code in codes)\n",
        ("tests/test_route_policy.py", "tests/test_permissions.py"),
        "has_all 退化成 has_any：只满足其中一个权限码就放行",
    ),
    Mutant(
        "require-open",
        "src/api/deps.py",
        "        if not granted:\n",
        "        if False:\n",
        ("tests/test_route_policy.py", "tests/test_identity_api.py"),
        "权限不足不再拒绝：require() 恒放行",
    ),
    Mutant(
        "err-code-scale",
        "src/api/errors.py",
        "        code = exc.status_code * 100\n",
        "        code = exc.status_code * 10\n",
        ("tests/test_module_errors.py", "tests/test_error_envelope.py"),
        "错误码少乘 10：前端按 code//100 推 HTTP 状态会全部错位",
    ),
    Mutant(
        "port-silent-override",
        "src/core/ports/host.py",
        "        if existing is not None and existing is not impl and not replace:\n"
        "            raise RegistryError(\n"
        '                f"注入口 {self.name} 只允许一个实现："\n',
        "        if False:\n            raise RegistryError(\n"
        '                f"注入口 {self.name} 只允许一个实现："\n',
        ("tests/test_architecture.py", "tests/test_ports.py"),
        "单例注入口重复注册不再报错：两个实现静默互相覆盖",
    ),
    Mutant(
        "assembly-no-fatal",
        "src/api/assembly.py",
        "    if not fatal:\n        return\n    raise RegistryError(\n",
        "    if fatal:\n        return\n    return\n    raise RegistryError(\n",
        ("tests/test_assembly.py",),
        "声明了没注册的装配物不再阻止启动",
    ),
    Mutant(
        "outbox-lenient-publish",
        "src/db/outbox.py",
        "            await bus.publish_strict(name, **_decode(payload))\n",
        "            await bus.publish(name, **_decode(payload))\n",
        ("tests/test_outbox.py",),
        "outbox 改用永不抛的 publish：订阅者失败被记成投递成功",
    ),
    Mutant(
        "serving-302",
        "src/core/serving.py",
        "        return RedirectResponse("
        "self.url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)\n",
        "        return RedirectResponse(self.url, status_code=302)\n",
        ("tests/test_archive_serving.py",),
        "远端取件从 307 变 302：预签名 URL 的 Range 与大文件下载失效",
    ),
    Mutant(
        "password-accept-all",
        "src/core/security.py",
        "        return hmac.compare_digest(digest.hex(), digest_hex)\n",
        "        return True\n",
        ("tests/test_identity_api.py", "tests/test_password_offload.py"),
        "口令校验恒真：任意密码都能登录",
    ),
    Mutant(
        "tx-no-rollback",
        "src/db/session.py",
        "        except Exception:\n            await session.rollback()\n            raise\n",
        "        except Exception:\n            raise\n",
        ("tests/test_fault_tolerance.py", "tests/test_task_api.py", "tests/test_session_scope.py"),
        "请求事务异常后不回滚：脏数据随下一个提交一起落库",
    ),
    Mutant(
        "trace-not-injected",
        "src/api/middleware.py",
        "        set_request_id(rid)\n",
        "        pass\n",
        ("tests/test_observability.py", "tests/test_trace.py"),
        "请求 id 不再写入上下文：日志全成 `-`，无法按请求串联",
    ),
    Mutant(
        "prune-deletes-pending",
        "src/db/outbox.py",
        '                OutboxEvent.status.in_((OUTBOX_DELIVERED, OUTBOX_FAILED)),\n',
        "",
        ("tests/test_outbox.py",),
        "清理不再区分终态：还没投递的事件被一起删掉（等于丢事件）",
    ),
    Mutant(
        "serving-no-exists-check",
        "src/core/serving.py",
        "            if not self.path.is_file():\n",
        "            if False:\n",
        ("tests/test_serving.py", "tests/test_archive_serving.py"),
        "本地产物不检查存在性：文件被清掉时用户看到 500 而不是 404",
    ),
    Mutant(
        "loader-loose-name-check",
        "src/infra/loader.py",
        "    if not (name.isidentifier() and name.isascii()):\n",
        '    if not name.replace("_", "").isalnum():\n',
        ("tests/test_driver_loader.py",),
        "驱动名校验退回 isalnum：中文名放行后给出不可能的建文件提示",
    ),
)


def _read(path: Path) -> tuple[str, str]:
    text = path.read_bytes().decode("utf-8")
    return text, ("\r\n" if "\r\n" in text else "\n")


VERDICT_FAIL = "fail"        # 测试变红：不变量被守住了
VERDICT_PASS = "pass"        # 全绿：缺口
VERDICT_INVALID = "invalid"  # 收集/夹具报错：这一轮不算数


def _classify(stdout: str) -> tuple[str, str]:
    """从 pytest 输出判定这一轮有没有真的跑起来。

    陷阱：`--basetemp` 给两层目录会让 `tmp_path` 夹具直接 FileNotFoundError，
    表现为「12 passed, 2 errors」——如果只看退出码，这个会被当成「守卫有效」，
    于是扫描器在环境坏掉之后反而报告一切正常。
    """
    lines = [ln.strip() for ln in stdout.strip().splitlines() if ln.strip()]
    summary = lines[-1] if lines else ""
    if " failed" in summary or "failed," in summary:
        return VERDICT_FAIL, summary
    if " error" in summary or "errors" in summary:
        return VERDICT_INVALID, summary
    if "passed" in summary:
        return VERDICT_PASS, summary
    return VERDICT_INVALID, summary or "无输出"


def _run(tests: tuple[str, ...], index: int) -> tuple[str, str]:
    """跑测试，返回 (判定, 摘要行)。"""
    # 必须单层：`var/mut-tmp/<n>` 两层时 tmp_path 夹具会 FileNotFoundError
    basetemp = f"var/mtp{index}"
    cmd = [
        str(PYTEST),
        "-m",
        "pytest",
        "-q",
        "--no-header",
        "-p",
        "no:cacheprovider",
        f"--basetemp={basetemp}",
        *tests,
    ]
    proc = subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=900,
    )
    if not (proc.stdout or "").strip():
        return VERDICT_INVALID, (proc.stderr or "").strip()[-200:] or "无输出"
    return _classify(proc.stdout or "")


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    originals: dict[Path, bytes] = {}
    results: list[tuple[str, str, str, str]] = []

    try:
        for index, mutant in enumerate(MUTANTS, start=1):
            if only and only not in mutant.ident:
                continue
            path = ROOT / mutant.target
            raw = path.read_bytes()
            originals.setdefault(path, raw)
            text, nl = _read(path)
            old = mutant.old.replace("\n", nl)
            new = mutant.new.replace("\n", nl)
            count = text.count(old)
            if count != 1:
                results.append(
                    (mutant.ident, VERDICT_INVALID, f"锚点命中 {count} 次，跳过", mutant.why)
                )
                continue
            path.write_bytes(text.replace(old, new, 1).encode("utf-8"))
            try:
                verdict, summary = _run(mutant.tests, index)
            finally:
                path.write_bytes(raw)
            results.append((mutant.ident, verdict, summary, mutant.why))
            print(f"[{index:02d}] {mutant.ident:24s} {verdict:8s} {summary}", flush=True)
    finally:
        for path, raw in originals.items():
            if hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(raw).digest():
                path.write_bytes(raw)
                print(f"已复原 {path.relative_to(ROOT)}", flush=True)

    print("\n=== 汇总 ===")
    for ident, verdict, _summary, why in results:
        mark = {"fail": "OK  ", "pass": "GAP ", "invalid": "??  "}[verdict]
        print(f"{mark} {ident:24s} {why}")
    gaps = [r for r in results if r[1] == VERDICT_PASS]
    invalids = [r for r in results if r[1] == VERDICT_INVALID]
    print(
        f"\n共 {len(results)} 个变异 | 守住 {len(results) - len(gaps) - len(invalids)}"
        f" | 缺口 {len(gaps)} | 无效 {len(invalids)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
