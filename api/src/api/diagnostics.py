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
    from core.ports.assets import reference_source_count

    # CLI 不跑 lifespan，两个扩展点都要手动装配；否则会误报「审计未注册」
    load_extension_points()
    discover_modules()
    count = reference_source_count()
    if count == 0:
        report.add(
            "ports.references", "FAIL", "引用检查器未注册，删除素材会静默放过所有引用"
        )
    elif count < 2:
        report.add(
            "ports.references", "WARN", f"只接了 {count} 个引用来源（预期 task + template）"
        )
    else:
        report.add("ports.references", "PASS", f"{count} 个引用来源已注册")

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
            "跑 app import-archive-index 或人工核对",
        )
    else:
        report.add("archive.mirrors", "PASS", f"DB 归档行与 JSON 镜像一致（{len(archive_ids)} 条）")

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
    report.add(
        "storage.driver",
        "INFO",
        f"STORAGE_DRIVER={settings.storage_driver}（切 s3 需配齐 S3_* 四项、桶私有 + CORS）",
    )


async def run_doctor(*, sample: int = 10) -> DoctorReport:
    report = DoctorReport()
    _check_ports(report)
    await asyncio.to_thread(_check_archive_storage, report)
    await _check_database(report)
    await asyncio.to_thread(_check_workspace_and_blobs, report, sample)
    return report
