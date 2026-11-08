"""生图模块的部署自检：`ModuleSpec.doctor` 指向这里。

**为什么这条检查在模块里而不是 `api/diagnostics.py`**：它问的是
「`image_jobs` 里还有没有落库的旧 `/static/...` 地址」，只有生图模块知道
这两列曾经存过什么。写在诊断层里，诊断层就得 `from modules.image.models import`
——删掉生图模块，`app doctor` 会在第五项上 `ModuleNotFoundError` 崩掉，
后面十几项一条都看不到（2026-11-08 实测）。

自检纪律：只往报告里加自己的条目、只读不写。
"""

from __future__ import annotations

from api.module import DoctorReportLike

__all__ = ["check"]


async def check(report: DoctorReportLike) -> None:
    """历史行里是否还留着旧 `/static/` 绝对地址。

    这些是 2026-11 之前 `url_for()` 的产物。现在库里只存对象 key、地址在出参时
    按当前驱动现签，所以老值留着等于把「生成那一刻的驱动」固化进数据：
    换一次驱动，历史页整批裂图，而接口本身没有任何报错。
    """
    from sqlalchemy import func, or_, select

    from db.session import get_session_factory
    from modules.image.models import ImageJob

    async with get_session_factory()() as session:
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

    if stale:
        report.add(
            "image.urls",
            "WARN",
            f"{stale} 条生成结果仍是旧 /static 地址；跑 app migrate-storage 剥成 key",
        )
    else:
        report.add("image.urls", "PASS", "生成结果没有落库的旧地址")
