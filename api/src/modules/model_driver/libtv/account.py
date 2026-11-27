"""LibTV 账号侧接口：积分资产、排队位次、估价、消费明细。

## 这些接口是怎么来的

2026-11-17 从 9412 那个已登录浏览器里实抓（CDP `Network.requestWillBeSent`
+ 响应体），所以字段名、入参形状都是**上游真实契约**，不是猜的：

| 目的 | 端点 | 关键出参 |
|---|---|---|
| 账号资产 | `GET api2.liblib.art/api/www/member/account` | `usablePower` / `taskCanSubmit` |
| LibTV 免费额度 | `POST .../member/libtvFreePower` | `freePower` / `expireAt` |
| 0 积分权益 | `GET .../commerce/activity/benefit` | `tags[]` / `targetName` / `endTime` |
| 进度 + 排队 | `POST api.liblib.tv/.../generation/progress` | `queueReason` / `unlimitedQueueNum` |
| 批量估价 | `POST .../generation/power/calculator/batch` | `power` / `discountRatio` |
| 消费汇总 | `POST .../power/translogs/management/summary` | `totalCount` / `totalAmount` |
| 消费明细 | `POST .../power/translogs/management` | `transAmount` / **`originalBizNo`** |

（`...` 都是 `api2.liblib.art/api/www`；排队三件套的完整字段是
`unlimitedQueueNum` / `unlimitedQueueTotal` / `unlimitedQueueLimit`。）

## 两个必须记住的事实

1. **鉴权不是 Cookie，是 `token` 头**。值就是浏览器里的 `usertoken` Cookie；
   同一个 token 对 `api.liblib.tv` 与 `api2.liblib.art` 都有效。所以本模块只从
   CDP 取一次 header，就能同时打两个域 —— 不需要把浏览器页面跑起来。
2. **`queueReason` 才是「为什么这么久还没出片」的答案**：
   - `UNLIMITED_SERIAL`：0 积分畅用队列，`unlimitedQueueNum/Total` 是**这台机器上**
     正在排这个队列的任务数，`unlimitedQueueLimit` 是名额（画布上是 10）；
   - `RATE_LIMIT`：被限流推迟，`delayInfo.remainSeconds` 还要等多久。
   以前任务中心只显示一个百分比，排队中和真在生成看起来一模一样。

## 边界

本模块**只读**：不建任务、不取消、不改账号状态。写操作仍然只在
`executor`（桥接层）里，避免出现两条能花钱的路径。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from core.exceptions import BadRequest, UpstreamError
from core.logging import get_logger
from modules.model_driver.libtv import executor
from modules.model_driver.libtv.reconcile import AutoVerdict, auto_verdict

logger = get_logger(__name__)

API_TV = "https://api.liblib.tv"
API_ART = "https://api2.liblib.art"

#: token 的缓存寿命。Cookie 会随浏览器会话轮换，5 分钟够省掉绝大多数 node 启动，
#: 又能保证换号 / 重登之后不用等太久。
_TOKEN_TTL_SEC = 300.0
#: 上游是公网接口：一次查询 12 秒还回不来，说明网络或账号那边有问题，
#: 不该拖住调用它的 HTTP 请求。
_TIMEOUT_SEC = 12.0

#: `opTypeCode=2` = 消耗（充值/赠送是别的码）；`sourceCode=30` = LibTV 生视频。
CONSUME_OP_TYPE = 2
SOURCE_LIBTV_VIDEO = 30

_tokens: dict[str, tuple[float, dict[str, str]]] = {}
#: 每个账号一把锁。快照会同时查三样东西，没锁的话冷缓存时三条路各自起一个
#: node（实测 3 次、约 600ms），而它们要的其实是同一份 header。
_token_locks: dict[str, asyncio.Lock] = {}


@dataclass(frozen=True)
class AccountAsset:
    """账号额度快照。字段名与上游一致，少的那几个是平台真正会读的。"""

    usable_power: int
    free_usable_power: int
    used_power: int
    total_power: int
    task_can_submit: int
    concurrent: int
    member_name: str
    member_level: str
    expire_date: str

    @property
    def name(self) -> str:
        return self.member_level or self.member_name


@dataclass(frozen=True)
class QueueItem:
    """一个上游任务此刻的进度与排队位次。"""

    task_id: str
    status: int
    percent: int
    queue_reason: str
    queue_position: int
    queue_total: int
    queue_limit: int
    power: int
    benefit_tag: str
    remain_seconds: int = 0
    failed_reason: str = ""
    #: 成功时的产物地址（`taskResult.videos[0].previewPath`）；其余状态为空串
    result_url: str = ""

    @property
    def queued(self) -> bool:
        return bool(self.queue_reason) and self.status == 0

    def describe(self) -> str:
        if self.queue_reason == "UNLIMITED_SERIAL":
            return (
                f"0 积分队列排队中：本机 {self.queue_total}/{self.queue_limit} "
                f"（这台排第 {self.queue_position}）· {self.percent}%"
            )
        if self.queue_reason == "RATE_LIMIT":
            return f"被上游限流推迟，约 {self.remain_seconds} 秒后重试"
        if self.status == 1:
            return f"生成中 {self.percent}%"
        if self.status == 3 and self.failed_reason:
            return f"失败：{self.failed_reason}"
        return f"状态 {self.status} · {self.percent}%"


@dataclass(frozen=True)
class ConsumptionItem:
    amount: int
    seconds: int
    source_name: str
    task_id: str
    project_name: str
    model_name: str
    summary: str


@dataclass(frozen=True)
class ConsumptionPage:
    total: int
    page: int
    page_size: int
    items: list[ConsumptionItem] = field(default_factory=list)


@dataclass(frozen=True)
class ConsumptionTotal:
    count: int
    amount: int


# --------------------------------------------------------------------------- #
# 鉴权
# --------------------------------------------------------------------------- #
def _vendor_transport() -> Path:
    return (
        Path(__file__).parent
        / "runtime/vendor/tools/js-reverse-mcp/canvas-transport.mjs"
    )


def _known_accounts() -> frozenset[str]:
    urls = set(executor.account_urls())
    credit = executor.credit_account_url()
    if credit:
        urls.add(credit)
    return frozenset(urls)


def _validated_account(account: str) -> str:
    """只接受配置里登记过的 CDP 地址。

    客户端给的 `account` 会一路传到 node 的 `loginHeaders`（它先
    `GET http://127.0.0.1:<任意端口>/json/version`）。只限制 loopback 不够：
    任何持 `task:read` 的账号都能拿它探测本机端口。账号别名不是自由输入，
    服务端只认 `.env` 里登记过的那几个。
    """
    normalized = account.strip().rstrip("/")
    if normalized and normalized not in _known_accounts():
        raise BadRequest(
            "该 LibTV 账号未登记：只接受配置里已存在的 CDP 地址"
            "（LIBTV_BROWSER_URL(S) / LIBTV_CREDITS_BROWSER_URL）"
        )
    return normalized


async def _headers(account: str) -> dict[str, str]:
    """从 CDP 那个已登录浏览器里取一次鉴权头，按账号缓存。

    走 node 而不是 Python 直连 CDP：取 Cookie 要走 WebSocket 上的
    `Storage.getCookies`，而 vendor 里已经有实现好的 `loginHeaders`
    （它会同时在两个域上找 `usertoken`），再写一份 Python 版只会多一处会漂移的口径。
    """
    account = _validated_account(account)
    now = time.monotonic()
    cached = _tokens.get(account)
    if cached and cached[0] > now:
        return cached[1]

    # 双检 + 锁：先拿到锁的那一路去起 node，其余等它写回缓存后直接命中。
    lock = _token_locks.setdefault(account, asyncio.Lock())
    async with lock:
        now = time.monotonic()
        cached = _tokens.get(account)
        if cached and cached[0] > now:
            return cached[1]
        return await _fetch_headers(account, now)


async def _fetch_headers(account: str, now: float) -> dict[str, str]:
    """真正去 CDP 取一次（只应由 `_headers` 在持有账号锁时调用）。"""
    script = (
        f"import({json.dumps(_vendor_transport().as_uri())})"
        ".then(async m => console.log(JSON.stringify("
        f"await m.loginHeaders({json.dumps(account)}))))"
    )
    try:
        process = await asyncio.create_subprocess_exec(
            executor.get_libtv_settings().node,
            "-e",
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=_TIMEOUT_SEC
        )
    except (TimeoutError, OSError) as exc:
        raise UpstreamError(f"取不到 LibTV 登录态（{account}）：{exc}") from exc
    if process.returncode != 0:
        detail = (stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise UpstreamError(
            f"取不到 LibTV 登录态（{account}）：{detail[-1] if detail else process.returncode}"
        )
    headers = json.loads(stdout.decode("utf-8"))
    # `token` 是自定义头（不是 Authorization），上游就认它；其余是防跨域与风控的固定值。
    _tokens[account] = (now + _TOKEN_TTL_SEC, headers)
    return headers


def _auth_headers(headers: dict[str, str], host: str) -> dict[str, str]:
    """按目标域换掉 Origin/Referer：跨域校验看这两个，写错就是 403。"""
    origin = "https://www.liblib.tv" if host == API_TV else "https://www.liblib.art"
    return {**headers, "Origin": origin, "Referer": f"{origin}/"}


async def _request(
    method: str,
    url: str,
    *,
    account: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    host = API_TV if url.startswith(API_TV) else API_ART
    # 忽略系统代理环境变量（如 ALL_PROXY=socks5://...）：未装 socksio 时 httpx
    # 会在建客户端时直接 ImportError，而 LibTV 上游与本机 CDP 均直连可达。
    async with httpx.AsyncClient(timeout=_TIMEOUT_SEC, trust_env=False) as client:
        response = await client.request(
            method,
            url,
            json=body,
            headers=_auth_headers(await _headers(account), host),
        )
    if response.status_code == 401 or response.status_code == 403:
        # 登录态过期：清缓存，下一次调用重新取（换号/重登后立刻恢复）。
        _tokens.pop(account, None)
        raise UpstreamError(
            f"LibTV 账号 {account} 的登录态无效或已过期，请在对应浏览器里重新登录"
        )
    if response.status_code >= 400:
        raise UpstreamError(f"LibTV 接口 HTTP {response.status_code}（{url}）")
    payload = response.json()
    if payload.get("code") not in (0, None):
        raise UpstreamError(f"LibTV 业务错误 {payload.get('code')}：{payload.get('msg') or url}")
    return payload


def _default_account(billing_mode: str = "credits") -> str:
    """默认查哪个账号：积分相关看积分账号，免费队列看第一个在线的。"""
    if billing_mode == "credits":
        return executor.credit_account_url() or executor.account_urls()[0]
    return executor.select_browser("free", has_task=False)


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #
async def asset(account: str = "") -> AccountAsset:
    """账号额度：可用积分、已用积分、还能提交几个任务。"""
    account = account or _default_account()
    payload = await _request(
        "GET", f"{API_ART}/api/www/member/account?isApp=false", account=account
    )
    data = payload.get("data") or {}
    attr = data.get("attr") or {}
    return AccountAsset(
        usable_power=int(attr.get("usablePower") or 0),
        free_usable_power=int(attr.get("freeUsablePower") or 0),
        used_power=int(attr.get("usedPower") or 0),
        total_power=int(attr.get("totalPower") or 0),
        task_can_submit=int(attr.get("taskCanSubmit") or 0),
        concurrent=int(attr.get("concurrent") or 0),
        member_name=str(data.get("name") or ""),
        member_level=str(data.get("accountLevelName") or ""),
        expire_date=str(data.get("endTime") or ""),
    )


async def free_power(account: str = "") -> dict[str, Any]:
    """LibTV 免费额度（活动送的那种），含到期时间。"""
    account = account or _default_account("free")
    payload = await _request(
        "POST", f"{API_ART}/api/www/member/libtvFreePower", account=account, body={}
    )
    return payload.get("data") or {}


async def zero_power_benefits(account: str = "") -> list[dict[str, Any]]:
    """0 积分畅用权益：哪几个模型免费、什么时候到期。

    任务的 `benefitTag`（`rateLimit_powerZero_20261111`）就来自这里；
    权益到期后同一批任务会突然开始扣积分，提前看到比账单上看到好。
    """
    account = account or _default_account()
    payload = await _request(
        "GET", f"{API_ART}/api/www/commerce/activity/benefit", account=account
    )
    items = ((payload.get("data") or {}).get("list")) or []
    return [
        {
            "modelVersionId": item.get("modelVersionId"),
            "targetName": item.get("targetName"),
            "activityName": item.get("activityName"),
            "endTime": item.get("endTime"),
            "tags": item.get("tags") or [],
        }
        for item in items
        if item.get("benefitType") == "powerZero"
    ]


def _parse_progress(payload: dict[str, Any]) -> list[QueueItem]:
    out: list[QueueItem] = []
    for item in ((payload.get("data") or {}).get("progresses")) or []:
        delay = item.get("delayInfo") or {}
        out.append(
            QueueItem(
                task_id=str(item.get("taskId") or ""),
                status=int(item.get("status") or 0),
                percent=int(item.get("progressPercent") or 0),
                queue_reason=str(item.get("queueReason") or ""),
                queue_position=int(item.get("unlimitedQueueNum") or 0),
                queue_total=int(item.get("unlimitedQueueTotal") or 0),
                queue_limit=int(item.get("unlimitedQueueLimit") or 0),
                power=int(item.get("power") or 0),
                benefit_tag=str(item.get("benefitTag") or ""),
                remain_seconds=int(delay.get("remainSeconds") or 0),
                failed_reason=str(item.get("failedReason") or ""),
                result_url=_first_video_url(item.get("taskResult")),
            )
        )
    return out


def _item_rank(item: QueueItem) -> tuple[int, int, int]:
    """区分「归属该账号的真实进度」与「非归属账号返回的 status=3 / percent=0 假失败」。"""
    non_terminal = 1 if item.status in (0, 1, 2) else 0
    has_detail = 1 if (item.queue_reason or item.failed_reason or item.percent > 0) else 0
    return (non_terminal, has_detail, item.percent)


def _is_reasonless_failure(item: QueueItem) -> bool:
    """终态却没有任何细节：非归属账号被问到时，上游就回这种「假失败」空壳。"""
    return (
        item.status not in (0, 1, 2)  # 3=失败 / 4=超时 / 其它未识别
        and not item.queue_reason
        and not item.failed_reason
        and item.percent == 0
    )


async def progress(task_ids: list[str], account: str = "") -> list[QueueItem]:
    """批量查进度：一条请求看完全部任务，顺带拿到排队位次。

    与桥接层 `poll` 的区别：那条是为了拿成片（要下载、要落库），这条只是
    **看状态**，不触发任何写操作，适合给页面轮询与轻量进度同步用。
    """
    ids = [str(item).strip() for item in task_ids if str(item).strip()]
    if not ids:
        return []
    if account:
        payload = await _request(
            "POST",
            f"{API_TV}/api/task/generation/progress",
            account=account,
            body={"taskIds": ids},
        )
        return _parse_progress(payload)

    credit = executor.credit_account_url()
    accounts = list(dict.fromkeys([*executor.account_urls(), *([credit] if credit else [])]))
    if len(accounts) <= 1:
        target = accounts[0] if accounts else _default_account("free")
        payload = await _request(
            "POST",
            f"{API_TV}/api/task/generation/progress",
            account=target,
            body={"taskIds": ids},
        )
        return _parse_progress(payload)

    # 多账号部署下（9411 免费 + 9412 积分），按非归属账号去查上游会得到
    # status=3/percent=0；并发查所有已配置账号，按 task_id 择优合并。
    results = await asyncio.gather(
        *(
            _request(
                "POST",
                f"{API_TV}/api/task/generation/progress",
                account=acc,
                body={"taskIds": ids},
            )
            for acc in accounts
        ),
        return_exceptions=True,
    )
    best: dict[str, QueueItem] = {}
    errors: list[Exception] = []
    for res in results:
        if isinstance(res, BaseException):
            if isinstance(res, Exception):
                errors.append(res)
            continue
        for item in _parse_progress(res):
            prev = best.get(item.task_id)
            if prev is None or _item_rank(item) > _item_rank(prev):
                best[item.task_id] = item
    if errors:
        # 另一个账号本次查询异常时，健康账号会对不属于它的任务回一个空壳终态
        # （status=3、percent=0，既没有 queueReason 也没有 failedReason）。这类条目
        # 不能覆盖真实归属状态，也不能当真失败：丢掉，但 task_id 必须留痕 —— 否则
        # 「归属账号真失败、上游却没给原因」会静默消失，页面一直停在生成中。
        dropped = [task_id for task_id, item in best.items() if _is_reasonless_failure(item)]
        for task_id in dropped:
            best.pop(task_id)
        logger.warning(
            "多账号进度并发查询有 %d 个账号失败，丢弃 %d 条无详情终态回执（%s），首个错误：%s",
            len(errors),
            len(dropped),
            ", ".join(dropped[:5]) or "-",
            errors[0],
        )
    if not best and errors:
        raise errors[0]
    return [best[task_id] for task_id in ids if task_id in best]


async def quote(task_ids: list[str], *, credits: bool = True, account: str = "") -> dict[str, int]:
    """按上游口径估价：`{taskId: 积分}`。

    `credits=True` 时关掉 0 积分权益标签（`disableBenefitTags`），算出来的才是
    「走积分队列要花多少」——下单前想让用户看到价钱就得这么问。
    """
    ids = [str(item).strip() for item in task_ids if str(item).strip()]
    if not ids:
        return {}
    account = account or _default_account("credits" if credits else "free")
    # 要关掉的标签必须**当场取**：活动换期后 `rateLimit_powerZero_20261111` 就不再是
    # 有效标签，写死的常量传过去等于没传 —— 报价会静默按另一条队列算，
    # 而调用方以为自己问的是「积分队列的价」。
    disable = sorted({tag for item in await zero_power_benefits(account) for tag in item["tags"]})
    body: dict[str, Any] = {
        "list": [
            {"taskId": task_id, **({"disableBenefitTags": disable} if credits and disable else {})}
            for task_id in ids
        ],
        "infiniteSwitch": 1 if credits else 0,
    }
    payload = await _request(
        "POST", f"{API_TV}/api/task/generation/power/calculator/batch", account=account, body=body
    )
    items = ((payload.get("data") or {}).get("items")) or []
    return {str(item.get("taskId")): int(item.get("power") or 0) for item in items}


async def consumption_total(account: str = "") -> ConsumptionTotal:
    """累计消耗（笔数与积分），用来对账。"""
    account = account or _default_account()
    payload = await _request(
        "POST",
        f"{API_ART}/api/www/power/translogs/management/summary",
        account=account,
        body={"opTypeCode": CONSUME_OP_TYPE},
    )
    data = payload.get("data") or {}
    return ConsumptionTotal(
        count=int(data.get("totalCount") or 0), amount=int(data.get("totalAmount") or 0)
    )


async def consumption(page: int = 1, page_size: int = 20, account: str = "") -> ConsumptionPage:
    """积分消耗明细。

    `originalBizNo` 是**上游 task_id**：想知道某次提交到底扣没扣钱、扣了多少，
    拿它去对任务号就行 —— 「提交回执未知」那种情况唯一的权威对账口径。
    """
    account = account or _default_account()
    payload = await _request(
        "POST",
        f"{API_ART}/api/www/power/translogs/management",
        account=account,
        body={"page": page, "pageSize": page_size, "opTypeCode": CONSUME_OP_TYPE},
    )
    data = payload.get("data") or {}
    items = [
        ConsumptionItem(
            amount=int(item.get("transAmount") or 0),
            seconds=int(item.get("transTime") or 0),
            source_name=str(item.get("sourceName") or ""),
            task_id=str(item.get("originalBizNo") or ""),
            project_name=str(item.get("projectName") or ""),
            model_name=str(item.get("modelName") or ""),
            summary=str(item.get("transDesc") or "")[:80],
        )
        for item in (data.get("data") or [])
    ]
    return ConsumptionPage(
        total=int(data.get("total") or 0),
        page=int(data.get("page") or page),
        page_size=int(data.get("pageSize") or page_size),
        items=items,
    )


async def _claimed_upstream_task_ids() -> set[str]:
    """查询本系统已认领的所有上游 task_id（从 `run_id:task_id` 剥出后半段）。"""
    from db.session import get_session_factory
    from generation.pipeline import list_claimed_external_ids

    async with get_session_factory()() as session:
        raw_ids = await list_claimed_external_ids(session)
    claimed: set[str] = set()
    for raw in raw_ids:
        _, sep, tail = raw.partition(":")
        # 只有 `run_id:task_id` 形状才是本系统记下的**上游**任务号。别的驱动会把
        # 裸 id（甚至有占位串）写进 external_id，它们和 LibTV 的 `originalBizNo`
        # 不是一个命名空间 —— 混进认领集合只会把真正该接回的那一单挡掉。
        if sep and tail:
            claimed.add(tail)
    return claimed


async def auto_reconcile_verdict(
    path: Path,
    created_at: datetime | None,
    account: str = "",
    *,
    claimed_task_ids: Collection[str] | None = None,
) -> AutoVerdict:
    """按消费明细与提示词快照自动判断「提交回执未知」任务的下一步。

    缺少 `prompt.txt` 或 `created_at`，或无法查询已认领任务号时一律返回 `unknown`，
    不猜、不捏造窗口起点，也不在不知情时跨单接回。
    """
    from sqlalchemy.exc import SQLAlchemyError

    prompt_file = path / "inputs/prompt.txt"
    if not prompt_file.is_file() or created_at is None:
        return AutoVerdict(action="unknown")
    if claimed_task_ids is None:
        try:
            claimed_task_ids = await _claimed_upstream_task_ids()
        except (SQLAlchemyError, OSError):
            logger.warning("查询已认领任务号失败，放弃自动对账 path=%s", path, exc_info=True)
            return AutoVerdict(action="unknown")
    # `LibTVState` 的 validator 是第一道；这里防的是绕过模型、直接传 naive 进来的调用方
    # （naive 一律按 UTC 解释，否则东八区会把窗口往前推 8 小时，扫进旧单）。
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    since = int(created_at.timestamp())
    spends: list[ConsumptionItem] = []
    for page in range(1, 4):
        data = await consumption(page, 20, account)
        spends.extend(data.items)
        # 空页或翻到提交时刻之前就够判断「有没有扣费」了，不必翻完整个账单
        if not data.items or min(item.seconds for item in data.items) < since:
            break
    return auto_verdict(
        spends,
        prompt=prompt_file.read_text(encoding="utf-8"),
        since=since,
        claimed_task_ids=claimed_task_ids,
    )


async def snapshot(account: str = "") -> dict[str, Any]:
    """一次拿全「这个账号现在什么状态」，给接口与运维命令用。

    四项并发取；任何一项失败都不该让整张快照变空 —— 额度查不到时，
    排队位次往往还是能查的，那正是排查时要看的东西。
    """
    account = account or _default_account()

    async def safe(coro, label: str, default: Any) -> Any:
        try:
            return await coro
        except UpstreamError as exc:
            logger.warning("LibTV 账号快照取 %s 失败：%s", label, exc)
            return default

    asset_info, free, benefits = await asyncio.gather(
        safe(asset(account), "额度", None),
        safe(free_power(account), "免费额度", {}),
        safe(zero_power_benefits(account), "0 积分权益", []),
    )
    return {
        "account": account,
        "asset": asset_info,
        "freePower": free.get("freePower", 0),
        "freePowerExpireAt": free.get("expireAt"),
        "zeroPowerBenefits": benefits,
        "queue": {
            "limit": 10,
            "note": "画布 0 积分队列最多同时排 10 个；位次看任务级 progress 接口",
        },
    }


def _first_video_url(raw: object) -> str:
    """`taskResult` 是个 JSON 字符串，取第一段视频的地址；形状不对就当没有。"""
    if not raw:
        return ""
    try:
        payload: object = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return ""
    if not isinstance(payload, dict):
        return ""
    videos = payload.get("videos") or []
    first = videos[0] if isinstance(videos, list) and videos else {}
    if not isinstance(first, dict):
        return ""
    return str(first.get("previewPath") or "")


