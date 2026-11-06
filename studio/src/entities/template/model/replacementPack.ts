import type { ArchivedAsset, ReplacementPack } from '@/shared/api/schema';

/** 隐藏多视图时仍校验完整素材包，避免只提交封面。 */
export function isReplacementPackAvailable(pack: ReplacementPack | undefined, assets: ArchivedAsset[]): boolean {
  if (pack?.available != null) return pack.available && (pack.version ?? 0) > 0;
  return !!pack && pack.referenceIds.length > 0 && pack.referenceIds.length <= 10 &&
    new Set(pack.referenceIds).size === pack.referenceIds.length &&
    pack.referenceIds.every(id => assets.some(asset => asset.id === id && asset.category === pack.kind && asset.type === 'image'));
}

export function replacementPackCoverId(pack: ReplacementPack): string | undefined {
  return pack.coverReferenceId && pack.referenceIds.includes(pack.coverReferenceId) ? pack.coverReferenceId : pack.referenceIds[0];
}
