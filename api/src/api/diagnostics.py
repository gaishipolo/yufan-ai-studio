"""部署自检：把「跑起来才发现」的隐式前提变成一条条可查的结论。

README 里那些人肉约定最容易在切 S3 时出事：

- 桶要私有 + 配 CORS —— 服务端测不了 CORS，但能明确提示；
- 发布版号的 CAS 依赖 `If-None-Match` 条件写 —— 某些 S3 兼容实现**静默忽略**
  这个头。一旦忽略，两个实例会同时写成功，而日志里一句错都没有；
- 归档索引在 DB、模板 worker 读 JSON 镜像 —— 两边不一致时模板会「看得见、
  用不了」。

`app doctor` 与 `app verify-blobs` 共用 `infra/archive/integrity.py`，避免两处
抽样逻辑漂移。所有检查都是只读的，唯一例外是条件写探针（写完立即删除）。
"""

from __future__ import annotations

import asyncio
import urllib.error
import urllib.request
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Literal

from core.config import settings
from infra.archive import get_archive_storage
from infra.archive.integrity import verify_blobs

Level = Literal["PASS", "INFO", "WARN", "FAIL"]
_CAS_PROBE = "catalog/locks/_doctor_cas.json"

__all__ = ["Check", "DoctorReport", "run_doctor"]


@dataclass(frozen=True)
class Check:
    name: str
    level: Level
    message: str


@dataclass
class DoctorReport:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, level: Level, message: str) -> None:
        self.checks.append(Check(name, level, message))

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.level == "FAIL"]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.level == "WARN"]

    def render(self) -> str:
        lines = [f"[{c.level:4}] {c.name:24} {c.message}" for c in self.checks]
        lines.append(
            f"共 {len(self.checks)} 项：失败 {len(self.failures)}、"
            f"警告 {len(self.warnings)}"
        )
        return "\n".join(lines)


def _check_ports(report: DoctorReport) -> None:
    """CLI 默认不发现模块，注入口就不会注册 —— 自检必须先加载它们。"""
    from api.discovery import discover_modules
    from app import load_extension_points
    from core.audit import has_audit_sink
    from core.ports.assets import (
        declared_reference_sources,
        reference_source_count,
        reference_source_gaps,
    )

    # CLI 不跑 lifespan，两个扩展点都要手动装配；否则会误报「审计未注册」
    load_extension_points()
    specs = discover_modules()

    # 不再「数够 2 个就 PASS」：那对第三个来源漏登记毫无察觉，而失败方向是永久丢数据。
    # 现在对账 ModuleSpec 声明集合 vs 实际注册集合，缺一即 FAIL。
    gaps = reference_source_gaps()
    declared = declared_reference_sources()
    module_names = {spec.name for spec in specs}
    undeclared = sorted(
        name
        for name, module in declared.items()
        if not module.startswith("_") and module not in module_names
    )
    if gaps:
        report.add("ports.references", "FAIL", "删除会被拒绝：" + "；".join(gaps))
    elif undeclared:
        report.add(
            "ports.references",
            "WARN",
            f"{reference_source_count()} 个来源已注册，但这些来源没有 ModuleSpec 声明："
            + "、".join(undeclared),
        )
    else:
        report.add(
            "ports.references",
            "PASS",
            f"{reference_source_count()} 个来源齐全且全部有声明（fail-closed 生效）",
        )

    if has_audit_sink():
        report.add("ports.audit", "PASS", "审计写入端已注册")
    elif settings.app_env == "prod":
        report.add("ports.audit", "FAIL", "生产环境审计写入端未注册，留痕会全部丢失")
    else:
        report.add("ports.audit", "INFO", "审计写入端未注册（非生产环境允许）")


def _check_archive_storage(report: DoctorReport) -> None:
    storage = get_archive_storage()
    try:
        storage.list_keys("catalog", suffix=".json")
    except Exception as exc:  # noqa: BLE001 - 自检要报告而不是崩掉
        report.add("archive.connect", "FAIL", f"归档存储不可达：{type(exc).__name__}: {exc}")
        return
    report.add(
        "archive.connect",
        "PASS",
        f"驱动 {type(storage).__name__} 可达（ARCHIVE_DRIVER={settings.archive_driver}）",
    )

    # 条件写是发布版号 CAS 的唯一支点：被静默忽略时不会有任何运行期报错
    storage.remove(_CAS_PROBE)
    try:
        first = storage.create(_CAS_PROBE, {"probe": True})
        second = storage.create(_CAS_PROBE, {"probe": True})
    except Exception as exc:  # noqa: BLE001
        report.add("archive.cas", "FAIL", f"条件写探针异常：{type(exc).__name__}: {exc}")
        return
    finally:
        # 探针清理失败不该盖掉真正的结论
        with suppress(Exception):
            storage.remove(_CAS_PROBE)
    if first and not second:
        report.add("archive.cas", "PASS", "条件写生效，多实例发布不会撞出同版号")
    else:
        report.add(
            "archive.cas",
            "FAIL",
            f"条件写失效（first={first}, second={second}）：服务端忽略了 If-None-Match，"
            "发布 CAS 会静默退化",
        )

    if settings.archive_driver == "local":
        report.add("archive.cors", "INFO", "本地驱动无需 CORS")
    else:
        url = storage.download_url(_CAS_PROBE)
        if not url:
            report.add("archive.cors", "FAIL", "远端驱动没有生成预签名地址")
        else:
            report.add(
                "archive.cors",
                "INFO",
                "远端桶需配 CORS（AllowOrigin=前端来源、AllowMethods=GET,HEAD、"
                "ExposeHeaders=Content-Length,Content-Range,ETag）；浏览器拿 307 后直连对象存储",
            )
            try:
                with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310
                    report.add("archive.presign", "PASS", f"预签名 GET 返回 {response.status}")
            except urllib.error.HTTPError as exc:
                # 探针已删除，404/NoSuchKey 说明签名被接受；403 是签名/权限真的错了
                level: Level = "FAIL" if exc.code in (401, 403) else "PASS"
                report.add(
                    "archive.presign",
                    level,
                    f"预签名返回 HTTP {exc.code}（探针已删，{exc.code} 属预期）"
                    if level == "PASS"
                    else f"预签名被拒绝：HTTP {exc.code}",
                )
            except Exception as exc:  # noqa: BLE001
                report.add(
                    "archive.presign", "WARN", f"预签名地址无法直连：{type(exc).__name__}: {exc}"
                )


async def _check_database(report: DoctorReport) -> None:
    from sqlalchemy import func, or_, select

    from db.session import get_session_factory
    from modules.asset.models import Asset
    from modules.asset.schema import AssetSource
    from modules.image.models import ImageJob

    async with get_session_factory()() as session:
        archive_ids = set(
            (
                await session.execute(
                    select(Asset.id).where(Asset.source == AssetSource.ARCHIVE)
                )
            ).scalars().all()
        )
        stale = (
            await session.execute(
                select(func.count())
                .select_from(ImageJob)
                .where(
                    or_(
                        ImageJob.output_url.like("/static/%"),
                        ImageJob.cover_url.like("/static/%"),
                    )
                )
            )
        ).scalar_one()
        quota = max(0, settings.archive_quota_bytes_per_user)
        usage: list[tuple[str, int]] = []
        if quota:
            usage = list(
                (
                    await session.execute(
                        select(Asset.owner_id, func.sum(Asset.size_bytes))
                        .where(
                            Asset.source == AssetSource.ARCHIVE,
                            Asset.deleted_at.is_(None),
                        )
                        .group_by(Asset.owner_id)
                    )
                ).all()
            )

    storage = get_archive_storage()
    mirror_ids = {
        key.rsplit("/", 1)[-1].removesuffix(".json")
        for key in storage.list_keys("catalog/assets", suffix=".json")
    }
    missing = sorted(archive_ids - mirror_ids)
    orphan = sorted(mirror_ids - archive_ids)
    if missing or orphan:
        report.add(
            "archive.mirrors",
            "WARN",
            f"缺镜像 {len(missing)} 条（模板 worker 读不到），孤儿镜像 {len(orphan)} 份；"
            "跑 app rebuild-mirrors 按 DB 幂等重建。"
            "注意 app import-archive-index 是 JSON→DB 的单向导入，方向相反，修不了缺镜像",
        )
    else:
        report.add("archive.mirrors", "PASS", f"DB 归档行与 JSON 镜像一致（{len(archive_ids)} 条）")

    if settings.archive_mirror_enabled:
        report.add(
            "archive.mirror.reconcile",
            "PASS",
            f"周期对账已启用（每 {settings.archive_mirror_interval_sec:g}s，幂等，无需选主）",
        )
    else:
        report.add(
            "archive.mirror.reconcile",
            "WARN",
            "周期对账已关闭：投影失败只能靠人工跑 app rebuild-mirrors 收敛",
        )

    if stale:
        report.add(
            "image.urls",
            "WARN",
            f"{stale} 条生成结果仍是旧 /static 地址；跑 app migrate-storage 剥成 key",
        )
    else:
        report.add("image.urls", "PASS", "生成结果没有落库的旧地址")

    if not quota:
        report.add("archive.quota", "INFO", "未设置归档容量上限（ARCHIVE_QUOTA_BYTES_PER_USER=0）")
    else:
        near = [(owner, int(used or 0)) for owner, used in usage if (used or 0) >= quota * 0.8]
        if near:
            report.add(
                "archive.quota",
                "WARN",
                f"{len(near)} 个用户已用超过配额 80%："
                + "、".join(f"{owner}={used // 1024 // 1024}MB" for owner, used in near[:5]),
            )
        else:
            report.add(
                "archive.quota",
                "PASS",
                f"全部用户归档用量低于配额 80%（上限 {quota} 字节）",
            )


def _check_workspace_and_blobs(report: DoctorReport, sample: int) -> None:
    from infra.archive.runs import orphan_pointers, pointer_ids

    storage = get_archive_storage()
    total = len(pointer_ids())
    missing = orphan_pointers()
    if missing:
        report.add(
            "archive.runs",
            "WARN",
            f"{len(missing)}/{total} 个档案只有指针、没有本机工作区；"
            "多实例部署要把 REPLACEMENT_RUNS_ROOT 指到共享盘",
        )
    else:
        report.add("archive.runs", "PASS", f"运行工作区可见 {total}/{total} 个档案")

    try:
        result = verify_blobs(storage, sample=sample)
    except Exception as exc:  # noqa: BLE001 - 自检要报告而不是崩掉
        report.add("archive.integrity", "FAIL", f"完整性巡检无法完成：{type(exc).__name__}: {exc}")
    else:
        report.add(
            "archive.integrity",
            "FAIL" if not result.healthy else "PASS",
            result.summary() + ("" if result.healthy else f"；损坏对象：{result.bad[:5]}"),
        )
    from core.coordination import get_leader_elector
    from infra.blob import client_cache_info

    elector = get_leader_elector()
    mode = elector.name
    if settings.archive_gc_enabled and mode == "local":
        report.add(
            "runtime.leader",
            "FAIL",
            "ARCHIVE_GC_ENABLED=true 但 LEADER_ELECTOR=local：多实例会各删一份。"
            "改成 backend（归档存储条件写租约），或确认全集群只有一个进程",
        )
    else:
        report.add(
            "runtime.leader",
            "PASS" if mode == "backend" else "INFO",
            f"周期任务选主={mode}；GC={'开启' if settings.archive_gc_enabled else '关闭'}",
        )
    cache = client_cache_info()
    report.add(
        "storage.topology",
        "INFO",
        f"STORAGE_DRIVER={settings.storage_driver} / ARCHIVE_DRIVER={settings.archive_driver}"
        f" / LEADER_ELECTOR={mode}；共享 S3 client 缓存 {cache['currsize']} 个"
        f"（两条通路连接参数相同应为 1）",
    )


async def _check_cache_channel(report: DoctorReport) -> None:
    """缓存驱动真做一次读写往返。

    只报 `CACHE_DRIVER=memory` 没有意义：换成 Redis 后连不上，启动、健康检查、
    业务只读接口全都正常，直到限流开始每请求 500 才发现。这里用「写 → 读 → 删」
    证明它真的可用，而不是「配置看起来对」。
    """
    from infra import get_cache
    from infra.cache.base import cache_key

    probe = cache_key("doctor", "probe")
    try:
        cache = get_cache()
        await cache.set(probe, b"1", ttl=30)
        value = await cache.get(probe)
        await cache.delete(probe)
    except Exception as exc:  # 缓存驱动是可选依赖，故障不该让 doctor 自身崩溃
        report.add(
            "runtime.cache",
            "FAIL",
            f"CACHE_DRIVER={settings.cache_driver} 不可用：{exc}",
        )
        return
    if value != b"1":
        report.add(
            "runtime.cache",
            "FAIL",
            f"CACHE_DRIVER={settings.cache_driver} 读写不一致（写 1 读回 {value!r}）",
        )
        return
    report.add(
        "runtime.cache",
        "PASS",
        f"CACHE_DRIVER={settings.cache_driver}，读写往返正常；前缀 {settings.cache_key_prefix!r}",
    )


async def _check_queue_channel(report: DoctorReport) -> None:
    """队列：db 驱动要证明表真的可读写；memory 驱动在生产是「重启就丢任务」。

    只报驱动名没有意义。`QUEUE_DRIVER=db` 却没跑迁移时，启动、健康检查、接口
    全都正常，第一条任务入队才 500。这里真查一次在途数量，把这类部署事故拦在自检里。
    """
    from infra import get_queue

    try:
        queue = get_queue()
        depth = await queue.size()
    except Exception as exc:  # 队列是可选依赖，故障不该让 doctor 自身崩溃
        report.add(
            "runtime.queue",
            "FAIL",
            f"QUEUE_DRIVER={settings.queue_driver} 不可用：{exc}"
            "（db 驱动要先跑 alembic upgrade head 建表）",
        )
        return

    # 用鸭子类型而不是 isinstance：不想为了一个检查把 db 驱动 import 进来
    stats_fn = getattr(queue, "stats", None)
    if stats_fn is not None:
        stats = await stats_fn()
        detail = (
            f"QUEUE_DRIVER={settings.queue_driver}，在途 {depth} 条"
            f"（pending {stats.get('pending', 0)} / running {stats.get('running', 0)}），"
            f"死信 {stats.get('dead', 0)} 条；租约 {settings.queue_lease_sec:g}s"
        )
        if stats.get("dead"):
            report.add("runtime.queue", "WARN", detail + "。死信需人工确认后清理")
        else:
            report.add("runtime.queue", "PASS", detail)
        return

    detail = (
        f"QUEUE_DRIVER={settings.queue_driver}，在途 {depth} 条；"
        "重启会丢在途任务，跨实例不可见"
    )
    if settings.worker_enabled and settings.app_env == "prod":
        report.add("runtime.queue", "WARN", detail + "。生产建议 QUEUE_DRIVER=db")
    else:
        report.add("runtime.queue", "INFO", detail)


def _check_http_channel(report: DoctorReport) -> None:
    """出站 HTTP 只报配置，不发真实请求。

    doctor 会在没有外网的机器上跑，探测供应商连通性属于部署验收，不是自检。
    """
    from infra.http import get_http_settings

    config = get_http_settings()
    report.add(
        "runtime.http",
        "INFO",
        f"出站超时 {config.timeout_sec}s / 连接池 {config.max_connections}"
        f"；Worker 并发 {settings.worker_concurrency}"
        f"（连接池应不小于并发的 2 倍）",
    )


async def run_doctor(*, sample: int = 10) -> DoctorReport:
    report = DoctorReport()
    _check_ports(report)
    await asyncio.to_thread(_check_archive_storage, report)
    await _check_database(report)
    await _check_cache_channel(report)
    await _check_queue_channel(report)
    _check_http_channel(report)
    await asyncio.to_thread(_check_workspace_and_blobs, report, sample)
    return report
