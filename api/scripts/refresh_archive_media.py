"""刷新已登记视频的时长元数据与浏览器预览，不改动原始归档文件。

旧索引把容器时长（含 AAC 音轨尾部填充，如 15.07 秒）当成画面时长，
导致标准 15 秒原片被 1–15 秒门禁判为异常；同时 10-bit / HEVC 原片在
浏览器 <video> 里无法解码。此脚本按当前探测规则补齐两类派生数据。

    python -X utf8 scripts/refresh_archive_media.py             # 只读报告
    python -X utf8 scripts/refresh_archive_media.py --execute  # 写入 Z 盘
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.media import StoredMedia  # noqa: E402
from infra.archive.local import LocalArchiveStorage  # noqa: E402
from infra.media_probe import create_browser_preview, normalize_duration  # noqa: E402

storage = LocalArchiveStorage()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="实际写回索引并生成预览")
    args = parser.parse_args()
    durations = previews = skipped = 0
    for path in sorted((storage.root() / "catalog/assets").glob("*.json")):
        value = storage.read(path)
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
                storage.write(path, value)
        derived = storage.resolve(f"library/previews/{media.id}.mp4")
        if derived.is_file():
            skipped += 1
            continue
        original = storage.resolve(media.path)
        if not original.is_file():
            print(f"[缺失] {media.id[:8]} 原片文件不存在：{media.path}")
            continue
        if args.execute:
            if not create_browser_preview(original, derived):
                print(f"[失败] {media.id[:8]} 预览转码失败：{media.name}")
                continue
            previews += 1
            print(f"[预览] {media.id[:8]} {derived.stat().st_size // 1024} KB  {media.name}")
        else:
            previews += 1
            print(f"[预览] {media.id[:8]} 待生成  {media.name}")
    mode = "已写入" if args.execute else "只读预演（加 --execute 生效）"
    print(f"\n{mode}：时长修正 {durations} 条，预览生成 {previews} 条，已有预览 {skipped} 条")


if __name__ == "__main__":
    main()
