import type { Asset } from './types';

/**
 * 封面的取法。
 *
 * 后端合成的托管封面是**站内相对路径**（`/api/v1/assets/{id}/cover`），取它要带
 * 令牌，所以走 Blob；演示数据 / 供应商签名的远端封面是绝对地址，浏览器直接加载就行，
 * 再去请求一次 `/cover` 只会白拿一个 404。
 */
export interface CoverSource {
  /** 托管封面：按素材 id 取字节（请求要带 Authorization） */
  blobId?: string;
  /** 远端封面：绝对 URL，可直接交给 `<img src>` */
  directUrl?: string;
}

export function resolveCoverSource(asset: Asset): CoverSource {
  if (!asset.coverUrl) return {};
  return /^https?:\/\//i.test(asset.coverUrl)
    ? { directUrl: asset.coverUrl }
    : { blobId: asset.id };
}
