"""集群协调原语：谁有资格跑「全集群只能跑一次」的任务。

以前调度器的约束写在注释里：「本扩展点不做分布式选主，多副本要自己加锁」。
结果是 `ARCHIVE_GC_ENABLED` 只能默认关掉——不可逆删除在多实例下会各跑一份，
而代码里没有任何地方能表达「我现在是不是唯一实例」。这不是文档问题，是缺一层能力。

这里把选主做成**可插拔策略**：

- `LocalLeaderElector`：单实例部署，永远返回 True（默认，行为与以前一致）；
- `BackendLeaderElector`：用归档存储已有的 `create()` 原子条件写抢一份带 TTL 的
  租约。本地驱动是 `O_EXCL`，S3 驱动是 `If-None-Match: *`，两者都是集群级原语，
  不需要引入 Redis / 数据库 advisory lock。租约过期后别人能接管，进程崩溃不会
  把任务永久锁死。**接管本身也要原子**——见 `BackendLeaderElector` 的类注释，
  那里记着「读到过期就删掉重建」为什么会让两个实例同时选上。

剩下的边界（不打算在这层解决）：执行时间超过 TTL 的任务仍可能被执行两遍——
本策略没有租约续期，`ttl` 要按「最慢一次 handler」给足。

`LEADER_ELECTOR=backend` 不是「顺便也支持多实例」：运行工作区仍需共享盘、
配额仍需串行化、预签名仍有 TOCTOU。doctor 会把这些边界逐条报出来。
"""

from __future__ import annotations

import time
import uuid
from contextlib import suppress
from functools import lru_cache
from typing import Any, Protocol, runtime_checkable

from core.config import settings
from infra.archive import ArchiveStorage, get_archive_storage

__all__ = [
    "BackendLeaderElector",
    "LeaderElector",
    "LocalLeaderElector",
    "get_leader_elector",
    "reset_leader_elector",
]

LEASE_PREFIX = "system/leases"

# 接管标记的有效期。接管本身是毫秒级动作，这个值只决定「接管者崩在中间之后，
# 别人要等多久才能接手」。不要调小到分钟以下：集群里各实例的墙钟可能差几秒，
# 太小会让「刚写下、还没做完」的标记被误判为过期。
TAKEOVER_TICKET_TTL_SEC = 300.0



@runtime_checkable
class LeaderElector(Protocol):
    """单次执行的资格判定。"""

    # 策略名（local / backend），进 doctor 报告与日志
    name: str

    def acquire(self, name: str, *, ttl: float = 60.0) -> bool: ...
    def release(self, name: str) -> None: ...


class LocalLeaderElector:
    """单实例：所有任务都由自己跑。"""

    name = "local"

    def acquire(self, name: str, *, ttl: float = 60.0) -> bool:
        return True

    def release(self, name: str) -> None:
        return None


class BackendLeaderElector:
    """用归档存储的条件写抢租约；同一时刻全集群只有一个实例成功。

    租约值里带 `holder` 与过期时间：进程挂掉不会释放文件，靠 TTL 让别的实例接管。
    续租不是必需的——周期任务的执行时间远小于 TTL，`acquire` 成功即执行一轮；
    超过 TTL 的长任务仍可能被执行两遍，这是本策略的已知边界，doctor 会报出来。

    ## 接管为什么需要一个额外的键（这一条不是啰嗦，是事故）

    `create()` 是这里唯一的原子原语（本地 `O_EXCL` / S3 `If-None-Match: *`），
    而**删除不是**。「读到过期 → 删掉 → 重建」因此不是原子的：

        A 读到过期租约 ┐
        B 读到过期租约 ┘（同一份）
        B 删除 → B 重建成功（B 认为自己是 leader，开始跑 GC）
        A 删除 ← 把 B 刚写的那份删了！
        A 重建成功（A 也认为自己是 leader）

    两个实例同时跑同一个「不可逆删除」任务。注释里写「只有一个 create 会成功」
    是错的：抢占发生在 create 之前，那次 create 早已成功。

    补法是给「接管」这个动作本身也找一个原子性：接管权写在一个**只由被接管的
    那份租约决定**的键上（`leases/<name>.takeover-<旧到期毫秒>.json`）。所有
    竞争者算出的键名完全相同，于是 `create` 的原子性直接给出唯一赢家；
    输家不删不建，直接返回 False。

    这块接管标记只挡「握着同一份过期租约的其他人」，所以按
    `takeover-<旧到期>` 命名天然按代际隔离，不会挡住下一代租约的接管。
    它带 `TAKEOVER_TICKET_TTL_SEC` 的有效期：接管者如果崩在「删旧租约」和
    「写新租约」之间，这块标记会过期，后人可以接手，不至于把任务永久锁死。
    """

    name = "backend"

    def __init__(
        self,
        storage: ArchiveStorage | None = None,
        *,
        instance_id: str = "",
    ) -> None:
        self.storage = storage if storage is not None else get_archive_storage()
        self.instance_id = instance_id or uuid.uuid4().hex

    @staticmethod
    def _key(name: str) -> str:
        return f"{LEASE_PREFIX}/{name}.json"

    @staticmethod
    def _ticket_key(name: str, expires_at: float) -> str:
        """接管权键名：只由「被接管的那份租约」决定，与谁在抢无关。"""
        return f"{LEASE_PREFIX}/{name}.takeover-{int(expires_at * 1000)}.json"

    def _read(self, key: str) -> dict[str, Any] | None:
        try:
            if not self.storage.exists(key):
                return None
            value = self.storage.read(key)
        except (FileNotFoundError, OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def _claim(self, key: str, now: float, ttl: float) -> bool:
        """原子创建租约；输家返回 False。"""
        try:
            return self.storage.create(
                key, {"holder": self.instance_id, "expiresAt": now + ttl}
            )
        except Exception:  # noqa: BLE001 - 存储不可达时不该让调度器崩掉
            return False

    def _claim_ticket(self, ticket: str, now: float) -> bool:
        """抢「接管权」；返回是否归我。"""
        payload = {"holder": self.instance_id, "takenAt": now}
        try:
            if self.storage.create(ticket, payload):
                return True
            existing = self._read(ticket)
        except Exception:  # noqa: BLE001 - 抢不到就是没抢到，失败方向是安全
            return False
        if existing is None:
            # 刚好被清掉（上一次接管崩在中间）：再抢一次
            try:
                return self.storage.create(ticket, payload)
            except Exception:  # noqa: BLE001
                return False
        try:
            taken_at = float(existing.get("takenAt", 0))
        except (TypeError, ValueError):
            taken_at = 0.0
        if now - taken_at < TAKEOVER_TICKET_TTL_SEC:
            return False
        # 标记已过期：接管者要么早就做完，要么崩在中间。此刻不可能还有人握着
        # 一份这么旧的读结果，所以删掉重抢是安全的。
        try:
            self.storage.remove(ticket)
            return bool(self.storage.create(ticket, payload))
        except Exception:  # noqa: BLE001
            return False

    def acquire(self, name: str, *, ttl: float = 60.0) -> bool:
        key = self._key(name)
        now = time.time()
        current = self._read(key)
        if current is None:
            return self._claim(key, now, ttl)
        holder = str(current.get("holder", ""))
        try:
            expires_at = float(current.get("expiresAt", 0))
        except (TypeError, ValueError):
            expires_at = 0.0
        if expires_at > now:
            # 未过期：只有我自己能「续租」。**不要在这里删别人的租约**——
            # 那一句正是上面那段竞态的入口。
            return holder == self.instance_id
        return self._take_over(name, key, expires_at, now, ttl)

    def _take_over(
        self, name: str, key: str, expires_at: float, now: float, ttl: float
    ) -> bool:
        ticket = self._ticket_key(name, expires_at)
        if not self._claim_ticket(ticket, now):
            return False
        # 到此为止我是唯一有权清理这份过期租约的实例
        try:
            self.storage.remove(key)
        except Exception:  # noqa: BLE001 - 删不掉就抢不到，失败方向是安全
            # 旧租约还在，说明什么都没发生：把标记撤掉，让后来的人还能正常接管，
            # 否则这个任务会被一块永远不会过期的标记锁死（要等 TTL + 重抢）。
            with suppress(Exception):
                self.storage.remove(ticket)
            return False
        # 清掉之后、重建之前有第三个实例用普通 create 抢到了（那是一次合法的原子
        # 领取）。标记留着：否则晚到的读者会把这份新租约再删一次。
        return self._claim(key, now, ttl)

    def release(self, name: str) -> None:
        key = self._key(name)
        current = self._read(key)
        if current is None or str(current.get("holder", "")) != self.instance_id:
            return
        try:
            self.storage.remove(key)
        except Exception:  # noqa: BLE001 - TTL 兜底，不释放也只是多等一轮
            return


@lru_cache(maxsize=1)
def get_leader_elector() -> LeaderElector:
    mode = settings.leader_elector.strip().lower()
    if mode == LocalLeaderElector.name:
        return LocalLeaderElector()
    if mode == BackendLeaderElector.name:
        return BackendLeaderElector()
    raise RuntimeError(
        f"LEADER_ELECTOR 只能是 local 或 backend，收到 {mode!r}；"
        "多实例部署必须用 backend，否则 GC 会各跑一份"
    )


def reset_leader_elector() -> None:
    """仅测试用：清空按配置缓存的选主器。"""
    get_leader_elector.cache_clear()
