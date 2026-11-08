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
    Mutant(
        "perm-include-blind",
        "src/api/assembly.py",
        "    for route, full_path in iter_api_routes(routes):\n",
        '    for route, full_path in ((r, str(getattr(r, "path", "?"))) for r in routes):\n',
        ("tests/test_assembly.py",),
        "启动期权限核对退回裸迭代：include 聚合的子路由一条都扫不到，检查永远通过",
    ),
    Mutant(
        "perm-zombie",
        "src/modules/image/service.py",
        "        if not actor.has(PERM_READ_ALL):\n",
        '        if not actor.has("image:read"):\n',
        ("tests/test_permission_coverage.py",),
        "某个已声明的权限点失去唯一判定点：声明的开关什么也不控制",
    ),
    Mutant(
        "perm-undeclared-service",
        "src/modules/task/service.py",
        "        return task.author_id == actor.id or actor.has(PERM_READ_ALL)\n",
        '        return task.author_id == actor.id or actor.has("task:raed:all")\n',
        ("tests/test_permission_coverage.py",),
        "服务层权限码拼错：路由层检查看不见，普通用户会莫名 403",
    ),
    Mutant(
        "role-builtin-typo",
        "src/modules/identity/permissions.py",
        '            "image:read",\n',
        '            "image:raed",\n',
        ("tests/test_permission_coverage.py",),
        "内置角色模板里的权限码写错：角色建出来就少一个能力，超管自测发现不了",
    ),
    Mutant(
        "token-typ-blind",
        "src/core/security.py",
        '    if claims.get("typ") != expected_typ:\n'
        '        raise Unauthorized("登录凭证无效")\n',
        "    if False:\n" '        raise Unauthorized("登录凭证无效")\n',
        ("tests/test_sessions.py", "tests/test_identity_api.py"),
        "不校验令牌用途：refresh 票能当 access 用，一张票可以无限续期",
    ),
    Mutant(
        "token-sub-blind",
        "src/core/security.py",
        '    if not str(claims.get("sub") or "").strip():\n'
        '        raise Unauthorized("登录凭证无效")\n',
        "    if False:\n" '        raise Unauthorized("登录凭证无效")\n',
        ("tests/test_token_claims.py", "tests/test_sessions.py", "tests/test_identity_api.py"),
        "不校验令牌主体：无 sub 的票在按 id 查库时变成「查不到用户」",
    ),
    Mutant(
        "pwd-change-blind",
        "src/modules/identity/principal.py",
        "            if principal.issued_at < int(changed_at.timestamp()):\n"
        "                return None\n",
        "            if False:\n                return None\n",
        ("tests/test_sessions.py", "tests/test_identity_api.py"),
        "改密后旧令牌继续可用：泄露的票在改密之后依然能进系统",
    ),
    Mutant(
        "session-revoke-blind",
        "src/modules/identity/principal.py",
        "            if active is None:\n                return None\n",
        "            if False:\n                return None\n",
        ("tests/test_sessions.py",),
        "登出/踢下线不生效：无状态令牌只靠 TTL，复制走的那张还能用满有效期",
    ),
    Mutant(
        "audit-failure-breaks-request",
        "src/core/audit.py",
        '    except Exception:  # noqa: BLE001 - 留痕失败不能影响业务结果\n'
        '        logger.exception("审计写入失败 action=%s target=%s", action, target_id)\n',
        "    except Exception:  # noqa: BLE001\n        raise\n",
        ("tests/test_audit.py",),
        "审计写失败把业务请求一起炸掉：留痕成了可用性的单点",
    ),
    Mutant(
        "outbox-attempts-frozen",
        "src/db/outbox.py",
        "        attempts = row.attempts + 1\n",
        "        attempts = row.attempts\n",
        ("tests/test_outbox.py",),
        "投递失败不再累加 attempts：永远到不了上限，失败事件无限重试",
    ),
    Mutant(
        "scheduler-last-not-set",
        "src/core/scheduler.py",
        "            # 也不该把这个任务饿死到下次启动\n"
        "            self._last[job.name] = now\n",
        "            # 也不该把这个任务饿死到下次启动\n            pass\n",
        ("tests/test_scheduler.py",),
        "跑完不记时间：handler 抛异常后同一轮立刻重试，变成热循环",
    ),
    Mutant(
        "token-claims-not-required",
        "src/core/security.py",
        '            options={"require": ["exp", "iat"]},\n',
        "",
        ("tests/test_token_claims.py",),
        "不再要求 exp/iat 存在：无过期的票永久有效，无 iat 的票静默绕过改密失效",
    ),
    Mutant(
        "refresh-sid-blind",
        "src/core/security.py",
        '    if not str(claims.get("sid") or "").strip():\n'
        '        raise Unauthorized("登录凭证无效")\n',
        "    if False:\n" '        raise Unauthorized("登录凭证无效")\n',
        ("tests/test_token_claims.py",),
        "刷新票不要求 sid：会话轮换找不到要吊销的那一条",
    ),
    Mutant(
        "refresh-jti-blind",
        "src/core/security.py",
        '    if not str(claims.get("jti") or "").strip():\n'
        '        raise Unauthorized("登录凭证无效")\n',
        "    if False:\n" '        raise Unauthorized("登录凭证无效")\n',
        ("tests/test_token_claims.py",),
        "刷新票不要求 jti：重放检测失去依据",
    ),
    Mutant(
        "scaffold-perm-unused",
        "src/scaffold/templates.py",
        '${Name}UpdateDep = Annotated[CurrentUser, Depends(require("${perm}:update"))]\n',
        "",
        ("tests/test_scaffold.py",),
        "脚手架模板声明了 :update 却没有 require：新模块一落地就撞上僵尸权限守卫",
    ),
    Mutant(
        "backup-leaves-handle",
        "src/db/backup.py",
        "    # 否则「恢复成功」之后紧接着的任何 rename / 二次备份都可能撞上占用。\n"
        "    with closing(sqlite3.connect(source)) as src, "
        "closing(sqlite3.connect(target)) as dst:\n",
        "    # 否则「恢复成功」之后紧接着的任何 rename / 二次备份都可能撞上占用。\n"
        "    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:\n",
        ("tests/test_backup.py",),
        "恢复不再关闭 sqlite 连接：文件句柄留到 GC，Windows 上挡住恢复时替换文件",
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


def _run(tests: tuple[str, ...], index: int, suffix: str = "") -> tuple[str, str]:
    """跑测试，返回 (判定, 摘要行)。"""
    # 必须单层：`var/mut-tmp/<n>` 两层时 tmp_path 夹具会 FileNotFoundError
    basetemp = f"var/mtp{index}{suffix}"
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


def _run_with_retry(tests: tuple[str, ...], index: int) -> tuple[str, str]:
    """判定为 `invalid` 时重跑一次，用另一个 basetemp。

    为什么需要：`invalid` 有两种来源——真的环境坏了，或者这条用例本来就不稳。
    两者混在一起时，扫描器会把「偶发」报成「没测」，而它的全部价值就在判定可信。
    重跑只发生在 invalid 上（`fail` / `pass` 不重跑），代价是极少数情况多几秒。
    """
    verdict, summary = _run(tests, index)
    if verdict != VERDICT_INVALID:
        return verdict, summary
    retry_verdict, retry_summary = _run(tests, index, suffix="b")
    return retry_verdict, f"重跑一次：{retry_summary}"


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
                # 静默跳过是扫描器自己的盲区：锚点因为缩进/换行变化不再唯一时，
                # 这条不变量就从「守住了」变成「没测」，而汇总里只显示一个 `??`。
                results.append(
                    (mutant.ident, VERDICT_INVALID, f"锚点命中 {count} 次，跳过", mutant.why)
                )
                print(
                    f"[{index:02d}] {mutant.ident:24s} {'skip':8s} 锚点命中 {count} 次",
                    flush=True,
                )
                continue
            path.write_bytes(text.replace(old, new, 1).encode("utf-8"))
            try:
                verdict, summary = _run_with_retry(mutant.tests, index)
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
