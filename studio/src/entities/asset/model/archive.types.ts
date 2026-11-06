import type { ArchivedAsset } from '@/shared/api/schema';
export type { ArchivedAsset } from '@/shared/api/schema';
export type ArchiveCategory = ArchivedAsset['category'];
export const ARCHIVE_LABELS: Record<ArchiveCategory, string> = {
  source: '原片', character: '人物', product: '产品', scene: '场景', audio: '音频',
};
/** 分类的展示顺序。不用 `Object.keys(ARCHIVE_LABELS)`：键顺序是实现的副产品，不该决定导航顺序。 */
export const ARCHIVE_CATEGORIES: readonly ArchiveCategory[] = ['source', 'character', 'product', 'scene', 'audio'];
export function archiveCategory(value: string | null): ArchiveCategory {
  return value === 'character' || value === 'product' || value === 'scene' || value === 'audio' ? value : 'source';
}
/** 页面内播放走浏览器兼容的派生流；`url` 仍是原始归档文件，用于下载与生成输入。 */
export function archiveStreamUrl(url: string): string {
  return url.replace(/\/file$/, '/stream');
}

/** 画质版本同时更新浏览器缓存与 React Query 的 Blob 缓存。
 *  `hd-v2` 必须与后端 `infra/media_probe.py` 的 `PREVIEW_VERSION` 同名：
 *  升版时两处一起改，否则旧预览会一直命中浏览器缓存。 */
export function archivePreviewUrl(url: string): string {
  return `${url}${url.includes('?') ? '&' : '?'}v=hd-v2`;
}

/** 文件选择器的类型白名单；后端仍会独立校验，不能只靠这里拦。 */
export const ARCHIVE_ACCEPT: Record<ArchiveCategory, string> = {
  source: '.mp4',
  audio: '.mp3,.wav',
  character: '.jpg,.jpeg,.png,.webp',
  product: '.jpg,.jpeg,.png,.webp',
  scene: '.jpg,.jpeg,.png,.webp',
};
