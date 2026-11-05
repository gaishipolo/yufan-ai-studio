import type { ArchivedAsset } from '@/shared/api/schema';
export type { ArchivedAsset } from '@/shared/api/schema';
export type ArchiveCategory = ArchivedAsset['category'];
export const ARCHIVE_LABELS: Record<ArchiveCategory, string> = {
  source: '原片', character: '人物', product: '产品', scene: '场景', audio: '音频',
};
export function archiveCategory(value: string | null): ArchiveCategory {
  return value === 'character' || value === 'product' || value === 'scene' || value === 'audio' ? value : 'source';
}
/** 页面内播放走浏览器兼容的派生流；`url` 仍是原始归档文件，用于下载与生成输入。 */
export function archiveStreamUrl(url: string): string {
  return url.replace(/\/file$/, '/stream');
}
