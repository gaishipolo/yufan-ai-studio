/**
 * 归档媒体的 URL 约定。
 *
 * 只在工作台与模板页用：这些地址指向**受保护**的取文件接口，不是对象存储的
 * 公开地址，所以页面必须带上画质版本参数（浏览器缓存与 React Query 的 Blob
 * 缓存都按 URL 命中）。
 *
 * v1 素材库的词汇（`ArchivedAsset` / `ARCHIVE_LABELS` / 分类白名单）随
 * `assets` 表于 2026-11-13 下线，这里只剩两条 URL 变换。
 */

/** 页面内播放走浏览器兼容的派生流；`url` 仍是原始文件，用于下载与生成输入。 */
export function archiveStreamUrl(url: string): string {
  return url.replace(/\/file$/, '/stream');
}

/**
 * 画质版本同时更新浏览器缓存与 React Query 的 Blob 缓存。
 *
 * `hd-v2` 必须与后端 `infra/media_probe.py` 的 `PREVIEW_VERSION` 同名：
 * 升版时两处一起改，否则旧预览会一直命中浏览器缓存。
 */
export function archivePreviewUrl(url: string): string {
  return `${url}${url.includes('?') ? '&' : '?'}v=hd-v2`;
}
