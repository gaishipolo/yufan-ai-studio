"""素材模块里唯一还活着的枚举：记录是怎么进库的。

**为什么只剩这一个**：路由与出参都归 `archive_schema.py` 了。上一版这里还有
`AssetOut` / `AssetCounts` / `AssetListOut`，那是 `/assets` 旧一套的出参；两套接口
合并成一套之后，没有人再产出它们，留着只会让人以为还有第二条通路。

`AssetSource` 留着是因为它描述的是**数据来源**（上传 / 归档导入 / 生成），
归档侧的仓储、导入、清理都按它分支。它不参与鉴权，只用于筛选与运营统计。
"""

from __future__ import annotations

from enum import StrEnum


class AssetSource(StrEnum):
    UPLOAD = "upload"
    ARCHIVE = "archive"
    GENERATED = "generated"
