"""刷新已登记视频的时长元数据与浏览器预览，不改动原始归档文件。

旧索引把容器时长（含 AAC 音轨尾部填充，如 15.07 秒）当成画面时长，
导致标准 15 秒原片被 1–15 秒门禁判为异常；同时 10-bit / HEVC 原片在
浏览器 <video> 里无法解码。此脚本按当前探测规则补齐两类派生数据。

    python -X utf8 scripts/refresh_archive_media.py             # 只读报告
    python -X utf8 scripts/refresh_archive_media.py --execute  # 写入当前归档驱动
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.media import StoredMedia  # noqa: E402
from infra.archive import BlobStore, get_archive_storage  # noqa: E402
from infra.media_probe import normalize_duration  # noqa: E402

# 走驱动工厂而不是 `LocalArchiveStorage()`：归档切到对象存储之后，
# 直接实例化本地驱动会「读本地旧盘、写本地旧盘」，跑完看起来成功、线上什么也没变。
storage = get_archive_storage()
blobs = BlobStore(storage)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="实际写回索引并生成预览")
    args = parser.parse_args()
    durations = previews = skipped = 0
    # 枚举走 `list_keys`，不在 `root()` 上 glob：远端驱动没有本地根目录
    for key in storage.list_keys("catalog/assets", suffix=".json"):
        value = storage.read(key)
        if not isinstance(value, dict):
            continue
        media = StoredMedia.model_validate(value)
        if media.type != "video":
            continue
        normalized = normalize_duration(media.duration)
        if normalized != media.duration:
            print(f"[时长] {media.id[:8]} {media.duration} -> {normalized}  {media.name}")
            durations += 1
            if args.execute:
                value["duration"] = normalized
                storage.write(key, value)
        derived = f"library/previews/{media.id}.mp4"
        if storage.exists(derived):
            skipped += 1
            continue
        if not storage.exists(media.path):
            print(f"[缺失] {media.id[:8]} 原片文件不存在：{media.path}")
            continue
        if args.execute:
            # 物化 + 临时目录 + 回传由 BlobStore 统一处理，本地/远端驱动同一条路
            if not blobs.derive(media.path, derived, kind="video", clip=True):
                print(f"[失败] {media.id[:8]} 预览转码失败：{media.name}")
                continue
            previews += 1
            print(f"[预览] {media.id[:8]} 已生成  {media.name}")
        else:
            previews += 1
            print(f"[预览] {media.id[:8]} 待生成  {media.name}")
    mode = "已写入" if args.execute else "只读预演（加 --execute 生效）"
    print(f"\n{mode}：时长修正 {durations} 条，预览生成 {previews} 条，已有预览 {skipped} 条")


if __name__ == "__main__":
    main()
