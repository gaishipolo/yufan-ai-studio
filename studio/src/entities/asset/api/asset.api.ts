import { api, http } from '@/shared/api/http';
import type { AssetListOut, AssetOut } from '@/shared/api/schema';
import type { AssetListParams } from '../model/types';

const BASE = '/assets';

/**
 * 上传超时单独给：默认 20s 是给 JSON 接口定的，
 * 50MB 素材走服务端中转时这个时间连本地盘都可能不够，
 * 用默认值会得到「上传到一半被前端主动掐断」的假故障。
 */
const UPLOAD_TIMEOUT_MS = 5 * 60_000;

export const assetApi = {
  // 显式挑字段而不是直接传 params：`api.get` 的查询参数类型是 Record<string, unknown>，
  // 而 interface 没有隐式索引签名，直接传会过不了类型检查
  list: (params: AssetListParams) =>
    api.get<AssetListOut>(BASE, {
      type: params.type,
      keyword: params.keyword,
      page: params.page,
      pageSize: params.pageSize,
    }),

  get: (id: string) => api.get<AssetOut>(`${BASE}/${id}`),

  /**
   * 上传素材。
   *
   * 走的是「原始字节流」而不是 multipart：后端按 `?filename=` 取名、
   * 边收边落盘算摘要，multipart 会让它多一层解析开销和内存占用。
   */
  upload: (file: File, onProgress?: (percent: number) => void) =>
    http
      .post<AssetOut>(`${BASE}/upload`, file, {
        params: { filename: file.name },
        headers: { 'Content-Type': file.type || 'application/octet-stream' },
        timeout: UPLOAD_TIMEOUT_MS,
        onUploadProgress: (event) => {
          if (!event.total) return;
          onProgress?.(Math.min(100, Math.round((event.loaded / event.total) * 100)));
        },
      })
      .then((response) => response.data),

  remove: (id: string) => api.delete<null>(`${BASE}/${id}`),

  /**
   * 取原文件 / 缩略图的字节。
   *
   * 为什么不是直接把 `fileUrl` 塞进 `<img src>`：素材是私有内容，取文件要求
   * `asset:read`，而 `<img>` / `<video>` 发出的请求**带不上 Authorization 头**，
   * 真实鉴权下就是 401。归档素材（`ArchivedMedia`）用的也是这条路：带令牌取
   * Blob，再 `URL.createObjectURL` 交给播放器。
   *
   * 代价是整段内容先进内存（视频因此没有 Range 拖动）；素材上限 50MB、
   * 预览一次一个文件，这个取舍比「鉴权破洞或 401 破图」可控。
   */
  file: (id: string) =>
    api.get<Blob>(`${BASE}/${id}/file`, undefined, {
      responseType: 'blob',
      timeout: 120_000,
    }),

  cover: (id: string) =>
    api.get<Blob>(`${BASE}/${id}/cover`, undefined, {
      responseType: 'blob',
      timeout: 60_000,
    }),
};
