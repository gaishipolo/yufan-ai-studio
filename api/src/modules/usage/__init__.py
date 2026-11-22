"""用量看板模块。

回答一个问题：**这段时间里生成了多少东西**。

数据来自各生成能力自己注册的读取器（`core.ports.usage_metrics`），看板只负责
合并 —— 视频、生图、以及将来接上的音频 / 数字人，都不用改看板。
"""

from api.module import ModuleSpec
from core.pages import PageSpec
from modules.usage.permissions import PAGE, SPECS
from modules.usage.router import router

module = ModuleSpec(
    name="usage",
    router=router,
    tags=["用量看板"],
    description="生成量统计：区间汇总、趋势、构成与明细",
    permissions=SPECS,
    pages=(
        PageSpec(
            code=PAGE,
            name="用量看板",
            route="/usage",
            description="按区间查看生成次数、已产出时长 / 张数、趋势与构成",
        ),
    ),
)

__all__ = ["module"]
