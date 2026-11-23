"""本地执行器型供应商的**成片落盘位置与输出地址格式**（唯一真相）。

## 这三个字面量为什么必须放一起

同一件「成片」在两个地方各写了一遍：

| 谁 | 写的是什么 |
|---|---|
| 驱动（`bailian` / `libtv`） | 成片落在归档工作区的相对路径 + 返回给框架的「地址」 |
| 框架（`modules/task/pipeline.py`） | 靠正则认出这种地址 → 去归档工作区把字节搬进对象存储 |

字面量一旦漂移，后果很隐蔽：驱动把成片写到 `outputs/generated.mp4`，框架按另一个
路径去找 → 找不到 → **任务显示成功，但资产库里一条视频都没有**（历史上真出现过
「图片入库 6 条、视频 0 条」）。所以这里定义一次，两边都用。

## 关于 `/api/v1/templates/replacement-runs/...` 这个地址

**它现在没有路由，也不是可访问地址** —— 是驱动与框架之间的**内部协议**：

1. 驱动把成片落进归档工作区，返回这个形式的地址；
2. 框架 `persist_output` 用 `RUN_OUTPUT_URL` 认出它，从工作区搬出字节、存进
   对象存储，**换成对象 key** 落库；
3. 出参时再由 `infra/artifacts.py` 现签成能播的地址。

所以**不要**把它改成「真实路由」，也不要试图直接访问它 —— 一旦框架认不出这种
形式（第 2 步失效），落库那一步就会把它当成供应商直链存进 `source_url`，
而这个地址谁也访问不了。
"""

from __future__ import annotations

import re

__all__ = [
    "EXECUTOR_OUTPUT_RELATIVE",
    "RUN_OUTPUT_URL",
    "executor_output_url",
]

#: 成片在归档工作区里的相对路径（相对 run 目录）。驱动写在这里，框架从这里读。
EXECUTOR_OUTPUT_RELATIVE = "outputs/generated.mp4"

#: 驱动返回给框架的「本地地址」格式。`{run_id}` 是任务记录 id（32 位小写 hex）。
OUTPUT_URL_TEMPLATE = "/api/v1/templates/replacement-runs/{run_id}/output"

#: 框架识别这种地址用的正则。捕获组是 run id —— 框架据此定位归档工作区。
RUN_OUTPUT_URL = re.compile(r"/replacement-runs/([a-f0-9]{32})/output$")


def executor_output_url(run_id: str) -> str:
    """驱动侧用：把 run id 拼成框架认得的「本地地址」。"""
    return OUTPUT_URL_TEMPLATE.format(run_id=run_id)
