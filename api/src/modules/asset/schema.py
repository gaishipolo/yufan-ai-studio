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
    """素材记录的来源。

    **只有 `ARCHIVE` 有写入路径**：网页上传与「从盘上登记」都走同一个
    `ArchiveService._ingest`，字节一律进归档存储（S3 或本地 `blobs/`），
    所以「归档」已经是素材的存在形式，而不是它的一种来源。

    另外两个值是历史遗留与预留，**没有写入路径**：

    - `UPLOAD`：`_ingest` 统一写 `archive` 之前留下的行（线上可能还有几条）。
      `ArchiveRepository` 的每个查询都带 `source == ARCHIVE`，所以这些行在
      列表里本来就已经看不见——不要再指望靠它区分入口；
    - `GENERATED`：生成产物目前落在运行工作区，还没有入库这步。

    要新增写入路径，必须同时放宽 `ArchiveRepository` 里所有 `source == ARCHIVE`
    过滤，否则素材能写进去、却在「我的素材」里查不到。这条由
    `tests/test_asset_sources.py` 的写入路径守卫盯着：新增枚举值而不给它写入路径
    （或不在 `AWAITING_WRITE_PATH` 里说明原因）会直接红。
    """

    ARCHIVE = "archive"
    UPLOAD = "upload"
    GENERATED = "generated"


# 还没有写入路径的来源，以及为什么。加新值时必须先想清楚：是补写入路径，
# 还是登记到这里——**不允许**默默加一个永远用不上的值。
AWAITING_WRITE_PATH: dict[AssetSource, str] = {
    AssetSource.UPLOAD: "早期写入路径的遗留数据；入口统一后不再产生新行",
    AssetSource.GENERATED: "生成产物落在运行工作区，尚未实现入库",
}
