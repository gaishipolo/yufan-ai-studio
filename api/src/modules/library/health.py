"""资产库 v2 的部署自检：`ModuleSpec.doctor` 指向这里。

M0 只有一项：三个注入口有没有接上实现。它现在是 INFO 而不是 WARN，因为
M0 的定位就是「骨架先上线、数据源后接」——M2 交付时这里会自动变成 PASS，
不需要改代码。真正的存储/回收站/共享队列检查在 M1/M2 加。

纪律与其它模块自检一致：只往报告里加自己那条，不改别人的结论。
"""

from __future__ import annotations

from api.module import DoctorReportLike
from core.ports.library_ingest import has_library_ingest
from core.ports.library_lookup import has_library_lookup
from core.ports.library_references import has_library_references

__all__ = ["check"]


async def check(report: DoctorReportLike) -> None:
    missing = [
        name
        for name, connected in (
            ("library-lookup", has_library_lookup()),
            ("library-ingest", has_library_ingest()),
            ("library-references", has_library_references()),
        )
        if not connected
    ]
    if missing:
        report.add(
            "library.ports",
            "INFO",
            f"注入口未接上：{', '.join(missing)}（M2 交付前正常，生成入库与彻底删除暂不可用）",
        )
        return
    report.add("library.ports", "PASS", "三个注入口均已接上")
