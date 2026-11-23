"""模型驱动模块自检：驱动文件是否都成功载入。

放在模块里而不是 `api/diagnostics.py`，是因为「哪些文件算驱动、校验口径是什么」
只有本模块知道；诊断层反向 import 业务模块会让删模块变成 doctor 崩溃。
"""

from __future__ import annotations

from api.module import DoctorReportLike

__all__ = ["check"]


async def check(report: DoctorReportLike) -> None:
    from modules.model_driver.registry import driver_summaries, load_errors

    infos = driver_summaries()
    errors = load_errors()
    ok = [item for item in infos if item.loaded]
    if errors:
        report.add(
            "model_driver.drivers",
            "WARN",
            f"{len(ok)} 个驱动已载入，{len(errors)} 处问题：" + "；".join(errors[:3]),
        )
    elif not ok:
        report.add("model_driver.drivers", "WARN", "drivers/ 下没有可用的模型驱动")
    else:
        report.add(
            "model_driver.drivers",
            "PASS",
            f"自动载入 {len(ok)} 个驱动：" + "、".join(item.name for item in ok),
        )
    _report_config_fingerprints(report)


def _report_config_fingerprints(report: DoctorReportLike) -> None:
    """报出各供应商**当前生效**配置的指纹。

    运维要能看出「现在生效的是哪一版配置」——改了 `.env` 之后，指纹要跟着变。
    指纹里**不含密钥明文**（敏感字段只取哈希）：它会进日志、进巡检报告。
    """
    from infra.driver_config import registered_driver_settings
    from modules.model_driver.kit.settings import config_fingerprint

    try:
        classes = registered_driver_settings()
    except Exception as exc:  # noqa: BLE001 - 巡检不该因为读配置失败而崩
        report.add("model_driver.config", "WARN", f"读不到驱动配置：{exc}")
        return
    if not classes:
        return
    parts: list[str] = []
    unreadable: list[str] = []
    for cls in classes:
        fingerprint = config_fingerprint(cls)
        if fingerprint == "invalid":
            unreadable.append(cls.__name__)
        parts.append(f"{cls.__name__}={fingerprint}")
    detail = "，".join(parts)
    if unreadable:
        detail += f"（配置读不出来：{'、'.join(unreadable)}）"
    report.add("model_driver.config", "WARN" if unreadable else "PASS", detail)
