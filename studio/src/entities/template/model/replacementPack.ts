import type { ReplacementPack } from '@/shared/api/schema';

/**
 * 这个包这一次能不能选。
 *
 * **以服务端 `available` 为准**：发布时已经校验过字节、用途与缺口，缺一项就标
 * `False`（仍出现在列表里但不可选，见 `pack_service._public`）。
 *
 * 曾经有一层「拿工作台的原片列表逐张比对包内图片」的前端自检，看起来更严格，
 * 实际上从来没成立过：那张列表是 `category=source` 的视频，包内成员是图片，
 * `asset.category === pack.kind && asset.type === 'image'` 对视频永远为假。
 * 原片列表切到资产库 v2（只有 `kind=video` + `origin`）之后更没有图片可比，
 * 留着这段等于「服务端漏标就让所有包都不可选」，所以只保留结构自检。
 */
export function isReplacementPackAvailable(pack: ReplacementPack | undefined): boolean {
  if (pack?.available != null) return pack.available && (pack.version ?? 0) > 0;
  return !!pack && pack.referenceIds.length > 0 && pack.referenceIds.length <= 10 &&
    new Set(pack.referenceIds).size === pack.referenceIds.length;
}

export function replacementPackCoverId(pack: ReplacementPack): string | undefined {
  return pack.coverReferenceId && pack.referenceIds.includes(pack.coverReferenceId) ? pack.coverReferenceId : pack.referenceIds[0];
}
