"""LibTV 执行器监督层：提交 / 轮询 / 下载都交给独立子进程与 Node 桥接层。

本模块由 `drivers/libtv.py` 的 Provider 调用，也通过 `core/ports/model_generation`
暴露给模板库（业务模块不许直接 import `modules/*`）。归档工作区的目录约定
由 `infra.archive` 与 `generic_inputs` 负责，这里只认 `run.json` 与执行回执。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import uuid
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

from pydantic import BaseModel

from core.config import get_settings
from core.exceptions import BadRequest
from core.logging import get_logger
from infra.archive import get_archive_storage, run_dir
from infra.driver_config import DriverSettings, driver_config
from modules.model_driver.libtv.state import (
    LIBTV_PROCESS_PROTOCOL,
    ExecutorResult,
    LibTVState,
)

logger = get_logger(__name__)
MODEL_ID = "libtv-wan3-replacement"
# 这些故障需要修复执行环境，再由「继续」恢复已有 task_id 的查询。
EXECUTOR_BLOCKED = frozenset(
    {
        "STALE_LOCK",
        "LOCK_CLEANUP_FAILED",
        "EXECUTOR_IO_FAILED",
        "EXECUTOR_CRASHED",
        "EXECUTOR_ENVIRONMENT_BLOCKED",
    }
)


class LibTvSettings(DriverSettings):
    """LibTV 驱动的参数。环境变量前缀 `LIBTV_`（键名与迁移前一致）。"""

    model_config = driver_config("LIBTV")

    # node 可执行文件**不在这里**：它是运行期依赖，归 `core.config.node_binary`
    # （`LIBTV_NODE` 已注册为该字段的别名，旧部署照旧生效）。
    # 适配器从已登录的本地浏览器读 Cookie；显式下发，避免依赖 API 进程继承的环境变量。
    #
    # 单账号时代的键，**仍然有效**：既是「免费队列账号池」的默认值，也是所有
    # 没有绑定过账号的历史任务的回退账号。双账号部署请用下面两个键。
    browser_url: str = "http://127.0.0.1:9226"
    # 免费队列账号池，逗号分隔、按优先级排列。第一个是默认账号。
    # 免费队列不花积分，所以这里可以放额度快用完的账号；它掉线时才会往后取。
    browser_urls: str = ""
    # 积分队列专用账号。9411 的积分快用完了，积分加速（`billingMode=credits`）
    # 必须落到还有额度的那个账号上，所以**单独一个键**，不从账号池里挑：
    # 从池子里挑意味着「谁在线谁接单」，而积分只在这个账号上有。
    credits_browser_url: str = ""


@lru_cache
def get_libtv_settings() -> LibTvSettings:
    return LibTvSettings()


def _normalize_url(value: str) -> str:
    """去掉空白与结尾斜杠：`http://127.0.0.1:9411/` 与 `...:9411` 是同一个账号。"""
    return value.strip().rstrip("/")


def account_urls() -> tuple[str, ...]:
    """免费队列可用的账号，按配置顺序去重。

    没配 `LIBTV_BROWSER_URLS` 时退回单账号时代的行为（只有 `LIBTV_BROWSER_URL`），
    老部署一行都不用改。
    """
    settings = get_libtv_settings()
    raw = settings.browser_urls.replace(";", ",").replace("\n", ",")
    urls = [_normalize_url(item) for item in raw.split(",") if item.strip()]
    if not urls:
        urls = [_normalize_url(settings.browser_url)]
    return tuple(dict.fromkeys(urls))


def credit_account_url() -> str:
    """积分队列账号；没配就是空串，由调用方回退到默认账号。"""
    return _normalize_url(get_libtv_settings().credits_browser_url)


# CDP 探测的超时。这个值不能大：它挡在每一次上传/提交前面，
# 而浏览器就是在 127.0.0.1，半秒都连不上的端口就是没起来。
_PROBE_TIMEOUT_SEC = 2.0


def browser_online(url: str) -> bool:
    """这个 CDP 端口现在有没有浏览器在监听。

    只探测 loopback：这两个账号都是本机 Chrome，允许远端地址等于把
    「浏览器里已登录的 Cookie」交给任意主机，和适配器里
    `Only a loopback CDP endpoint is allowed` 是同一道门。
    """
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        return False
    try:
        with urlopen(f"{url}/json/version", timeout=_PROBE_TIMEOUT_SEC) as response:
            return response.status == 200
    except (OSError, ValueError):
        # 连不上 / 超时 / 端口上不是 CDP，全部当作「这个账号现在用不了」。
        return False


def select_browser(billing_mode: str, *, has_task: bool) -> str:
    """按计费模式挑一个浏览器账号。

    三条规则，顺序即优先级：

    1. **已经有上游 `task_id` 的任务不换号**。这时返回的是账号池第一位，
       也就是单账号时代唯一那个账号 —— 历史任务都是它提交的，换号后
       查询会得到「任务不存在」，而且看起来像上游把任务弄丢了。
    2. **积分队列只认 `LIBTV_CREDITS_BROWSER_URL`**，不做「它掉线就用别的
       账号」的降级：另一个账号上没有积分，降级过去只会换来一次被拒绝的提交。
       没配这个键时才回退到默认账号（单账号部署）。
    3. **免费队列按顺序取第一个在线的**：免费不花积分，优先用额度快用完的
       那个；它没起来才往后取。
    """
    pool = account_urls()
    primary = pool[0]
    if has_task:
        return primary
    if billing_mode == "credits":
        return credit_account_url() or primary
    for url in pool:
        if browser_online(url):
            return url
    # 全都没起来时仍然返回默认账号：真正的错误由适配器报
    # （`EXECUTOR_BROWSER_UNAVAILABLE`，消息里带着这个地址和启动方式），
    # 这里返回空串只会让那句提示丢掉它最需要的信息。
    return primary


def browser_for(path: Path) -> str:
    """这个任务该用哪个浏览器账号。

    `run.json` 里的 `browser_url` 是**账号绑定**：桥接层在第一个阶段就把当时
    用的账号写进去，后续的上传/提交/查询/下载只读它。上游的 `task_id` 与上传
    素材都属于那个账号，中途换号 = 查到别人的任务或查不到任务。
    """
    try:
        meta = state(path)
    except Exception:  # noqa: BLE001 - 读不到档案时退回默认账号，别把阶段卡死在这里
        # run.json 缺失或不合法时，后面的桥接层会给出一条准确的错；
        # 这里如果直接抛，用户看到的是 `EXECUTOR_IO_FAILED`，
        # 和「档案没准备好」这件事对不上，排查方向会跑偏。
        logger.warning("LibTV 档案不可读，回退默认账号 path=%s", path)
        return account_urls()[0]
    pinned = _normalize_url(meta.browser_url or "")
    if pinned:
        return pinned
    return select_browser(meta.billing_mode or "free", has_task=bool(meta.task_id))


# 不能传给执行器的环境变量。原因很具体：宿主若从带 WorkBuddy 垫片的终端启动，
# `PYTHONPATH` 指向垫片目录，子进程会加载那里的 `sitecustomize`，把 `unlink` 换成
# 「进回收站 + 批量删除守卫」。执行器清理 `records/worker.lock` 时撞上守卫就退出，
# 任务被写成「LibTV 查询中断：safe-delete 安全守卫……」，而上游其实还在正常生成。
# 执行器不需要这些变量：导入路径由 venv 的 .pth 提供，node 也不需要额外的 --require。
_SHIM_ENV_KEYS = frozenset({"PYTHONPATH", "NODE_OPTIONS", "BASH_ENV"})
_SHIM_ENV_PREFIXES = ("CODEBUDDY_SAFE_DELETE",)


def supervisor_env() -> dict[str, str]:
    """执行器子进程的环境：继承宿主，但剥掉会改变删除语义的垫片变量。"""
    return {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in _SHIM_ENV_KEYS and not key.upper().startswith(_SHIM_ENV_PREFIXES)
    }


class Pointer(BaseModel):
    path: str


class AdapterError(BaseModel):
    code: str
    stage: str
    # 适配器给的原始说明。**必须留着**：`LOGIN_REQUIRED` 既可能是「浏览器根本没连上」，
    # 也可能是「浏览器连着但没登录」，两者的下一动作完全不同，只有 detail 能分开。
    detail: str = ""


# CDP 连接层的失败特征（见 vendor 的 canvas-transport：先 GET /json/version，再连 ws）。
_BROWSER_DOWN_HINTS = (
    "fetch failed",
    "CDP discovery",
    "CDP connection",
    "Only a loopback",
    "Unexpected CDP",
)


def explain_login_failure(
    result: ExecutorResult, error: AdapterError | None, browser: str = ""
) -> ExecutorResult:
    """把登录阶段的粗码分成「浏览器没连上」与「浏览器里没登录」。

    适配器在两种情况下都写 `LOGIN_REQUIRED`（stage=login），但用户要做的事不同：
    前者去启动/重启执行器浏览器（`start-services.ps1 -WithBrowser`），后者才是登录。
    混成一个码的后果已经在生产上出现过：浏览器进程没起来，页面却提示「登录」，
    人去重登一个仍然有效的会话，真正停掉的浏览器一直没人管。

    `browser` 是**这个任务实际用的账号**（多账号部署下有 9411/9412 两个）。
    不传时退回默认账号：那句提示里的端口是「去哪儿排查」的唯一线索，
    报错的人必须看到出问题的是哪一个账号，而不是默认那一个。
    """
    if error is None or error.code != "LOGIN_REQUIRED":
        return result
    account = browser or account_urls()[0]
    detail = error.detail or error.code
    if any(hint in detail for hint in _BROWSER_DOWN_HINTS):
        # 文案跟着阶段走：upload 阶段还没花钱，说「不会重复计费」；查询/下载阶段
        # 任务已经在上游跑着，得说「恢复查询」而不是假装还没提交。
        if result.stage in ("poll", "download"):
            tail = "启动后会自动恢复查询，上游任务未取消"
        else:
            tail = (
                "在你自己终端运行 start-services.ps1 -WithBrowser 启动后点继续；"
                "尚未提交，不会重复计费"
            )
        return result.model_copy(
            update={
                "code": "EXECUTOR_BROWSER_UNAVAILABLE",
                "message": f"执行器浏览器未连接（CDP {account}，{detail}）。{tail}",
            }
        )
    return result.model_copy(
        update={
            "code": "EXECUTOR_LOGIN_REQUIRED",
            "message": f"账号 {account} 的浏览器里没有有效的 LibTV 登录，请在该浏览器登录后点继续",
        }
    )


def run_path(run_id: str) -> Path:
    """索引指针 → 运行工作区目录。

    档案在本地工作区、指针在归档存储，两边分开（见 `infra/archive/runs.py`）：
    执行器要真实目录，而列表/恢复只依赖指针。
    """
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise BadRequest("调用标识无效")
    return run_dir(get_archive_storage(), run_id)


def state(path: Path) -> LibTVState:
    return LibTVState.model_validate(get_archive_storage().read(path / "run.json"))


def save_state(path: Path, meta: LibTVState) -> None:
    """把状态写回 `run.json`（人工解封 / 自动对账后继续提交都用它）。

    `mode="json"` 是必须的：`created_at` 是 datetime，直接写会变成 Python repr，
    桥接层 `load()` 读到就炸。
    """
    get_archive_storage().write(path / "run.json", meta.model_dump(mode="json"))


def read_result(path: Path) -> tuple[ExecutorResult, AdapterError | None]:
    storage = get_archive_storage()
    result = ExecutorResult.model_validate(storage.read(path))
    errorfile = path.parent / "executor-error.json"
    error = (
        AdapterError.model_validate(storage.read(errorfile)) if errorfile.exists() else None
    )
    return result, error


def environment_blocked(logfile: Path) -> bool:
    """只认本次监督日志的明确守卫标记，不向用户透出原始日志或推测普通崩溃原因。"""
    try:
        with logfile.open("rb") as stream:
            # 读取有界尾部，避免把大日志全量载入内存。
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 65536))
            return b"[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED]" in stream.read(65536)
    except OSError:
        # 辅助诊断失败不能掩盖原有执行器故障。
        logger.warning("LibTV 监督日志不可读 log=%s", logfile)
        return False


async def execute_stage(run_id: str, stage: str) -> ExecutorResult:
    try:
        return await _execute_stage(run_id, stage)
    except OSError:
        logger.exception("LibTV 阶段文件访问或进程启动失败 run_id=%s stage=%s", run_id, stage)
        return ExecutorResult(
            code="EXECUTOR_IO_FAILED",
            stage=stage,
            message="执行器文件或进程不可用；请检查共享盘、文件权限和运行环境",
        )


async def _execute_stage(run_id: str, stage: str) -> ExecutorResult:
    path = await asyncio.to_thread(run_path, run_id)
    # 账号在**这一层**定：任务有可能被绑定到 9411/9412 中的某一个，
    # 而绑定关系存在档案里（`run.json::browser_url`），只有这里同时看得到档案与配置。
    browser = await asyncio.to_thread(browser_for, path)
    # 适配器目录由监督进程按仓库内位置计算，这里不再接收任何外部路径配置。
    resultfile = path / "records" / f"operation-{uuid.uuid4().hex}.json"
    # 参数走 stdin 的 JSON（带版本号），不再靠位置对齐：新增参数只要给默认值，
    # 启动方不必同步改一遍，也不会出现「读串位」这种错位到别的字段的故障。
    payload = json.dumps(
        {
            "version": LIBTV_PROCESS_PROTOCOL,
            "stage": stage,
            "run": str(path),
            "node": get_settings().node_binary,
            "result": str(resultfile),
            # 账号也走参数包：监督进程要用它挑账号级串行锁，桥接层要用它连 CDP。
            # 只给环境变量不够——锁名与「连哪个浏览器」必须是同一个来源。
            "browser": browser,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    logdir = path / "logs"
    await asyncio.to_thread(logdir.mkdir, parents=True, exist_ok=True)
    logfile = logdir / f"supervisor-{resultfile.stem}.log"
    with logfile.open("wb") as errors:
        process = await asyncio.to_thread(
            subprocess.Popen,
            [sys.executable, "-m", "modules.model_driver.libtv.process"],
            # A console Ctrl+C during API restart must not interrupt the durable
            # supervisor before it can write its receipt.
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=errors,
            env={**supervisor_env(), "JS_REVERSE_BROWSER_URL": browser},
        )
        # 取消等待不杀监督进程；子进程继承日志句柄，可继续保存回执。
        # Windows 的 uvicorn reload 使用 Selector loop，不支持 asyncio 子进程 API。
        await asyncio.to_thread(process.communicate, payload)
    if process.returncode and await asyncio.to_thread(environment_blocked, logfile):
        logger.error(
            "LibTV 监督进程被安全守卫中断 run_id=%s stage=%s exit=%s log=%s",
            run_id,
            stage,
            process.returncode,
            logfile,
        )
        return ExecutorResult(
            code="EXECUTOR_ENVIRONMENT_BLOCKED",
            stage=stage,
            message=(
                "运行环境的 safe-delete 安全守卫阻止了执行器清理；"
                "请检查 API 服务运行环境及删除操作授权，处理后再继续；"
                f"诊断日志：logs/{logfile.name}"
            ),
        )
    if not resultfile.exists():
        logger.error(
            "LibTV 监督进程未返回回执 run_id=%s stage=%s exit=%s log=%s",
            run_id,
            stage,
            process.returncode,
            logfile,
        )
        return ExecutorResult(
            code="EXECUTOR_CRASHED",
            stage=stage,
            message=(
                f"监督进程未返回回执（退出码 {process.returncode}）；请检查 logs/{logfile.name}"
            ),
        )
    result, error = await asyncio.to_thread(read_result, resultfile)
    if result.code != "OK":
        logger.warning(
            "LibTV 阶段未完成 run_id=%s stage=%s code=%s detail=%s",
            run_id,
            stage,
            result.code,
            error.code if error is not None else None,
        )
        if error is not None and not result.message:
            # 只回一个码等于没说：`RELEASE_INVALID` 可能是清单不一致、桥接层参数
            # 不合法，也可能是某个 vendor 模块 import 失败，三者的下一动作完全不同。
            # 桥接层已经把这些写进 `records/executor-error.json` 的 detail（长凭证
            # 由它自己脱敏），这里必须原样带给用户，否则任务详情页只剩一个码。
            result.message = f"{error.code}：{error.detail}" if error.detail else error.code
        # 登录阶段的粗码在这里落脚：浏览器没连上 / 没登录，两种下一动作。
        result = explain_login_failure(result, error, browser)
    return result


