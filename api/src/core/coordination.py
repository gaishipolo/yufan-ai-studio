"""集群协调原语：谁有资格跑「全集群只能跑一次」的任务。

以前调度器的约束写在注释里：「本扩展点不做分布式选主，多副本要自己加锁」。
结果是 `ARCHIVE_GC_ENABLED` 只能默认关掉——不可逆删除在多实例下会各跑一份，
而代码里没有任何地方能表达「我现在是不是唯一实例」。这不是文档问题，是缺一层能力。

这里把选主做成**可插拔策略**：

- `LocalLeaderElector`：单实例部署，永远返回 True（默认，行为与以前一致）；
- `BackendLeaderElector`：用归档存储已有的 `create()` 原子条件写抢一份带 TTL 的
  租约。本地驱动是 `O_EXCL`，S3 驱动是 `If-None-Match: *`，两者都是集群级原语，
  不需要引入 Redis / 数据库 advisory lock。租约过期后别人能接管，进程崩溃不会
  把任务永久锁死。

`LEADER_ELECTOR=backend` 不是「顺便也支持多实例」：运行工作区仍需共享盘、
配额仍需串行化、预签名仍有 TOCTOU。doctor 会把这些边界逐条报出来。
"""

from __future__ import annotations

import time
import uuid
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
    续租不是必需的——周期任务的执行时间远小于 TTL，`acquire` 成功即执行一轮。
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

    def _read(self, key: str) -> dict[str, Any] | None:
        try:
            if not self.storage.exists(key):
                return None
            value = self.storage.read(key)
        except (FileNotFoundError, OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def acquire(self, name: str, *, ttl: float = 60.0) -> bool:
        key = self._key(name)
        now = time.time()
        current = self._read(key)
        if current is not None:
            holder = str(current.get("holder", ""))
            try:
                expires_at = float(current.get("expiresAt", 0))
            except (TypeError, ValueError):
                expires_at = 0.0
            if holder != self.instance_id and expires_at > now:
                return False
            # 过期租约（或自己上一轮的残留）先清掉，再用条件写抢。
            # 两个实例同时清、同时 create，只有一个 create 会成功。
            try:
                self.storage.remove(key)
            except Exception:  # noqa: BLE001 - 删不掉就抢不到，失败方向是安全
                return False
        try:
            return self.storage.create(
                key, {"holder": self.instance_id, "expiresAt": now + ttl}
            )
        except Exception:  # noqa: BLE001 - 存储不可达时不该让调度器崩掉
            return False

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
