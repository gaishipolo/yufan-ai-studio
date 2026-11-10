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
