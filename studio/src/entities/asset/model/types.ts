import type { AssetOut, AssetSource, AssetType } from '@/shared/api/schema';

/**
 * 素材资产（后端 `/assets` 资源）。
 *
 * 直接用生成出来的 `AssetOut`，不再手写镜像类型：字段是后端契约的直出，
 * 手写一份会在后端加字段时静默落后（例如 `fileUrl` / `duration` 之前就不存在）。
 */
export type Asset = AssetOut;

/** 归档分类，与后端 `MediaCategory` 枚举一致；上传素材没有分类。 */
export type AssetCategory = NonNullable<AssetOut['category']>;

export interface AssetListParams {
  /** 类型筛选，`all` 表示不筛 */
  type?: AssetType | 'all';
  /** 文件名关键字 */
  keyword?: string;
  page?: number;
  pageSize?: number;
}

export const ASSET_TYPE_LABEL: Record<AssetType, string> = {
  image: '图片',
  video: '视频',
  audio: '音频',
};

export const ASSET_SOURCE_LABEL: Record<AssetSource, string> = {
  upload: '本地上传',
  archive: '归档导入',
  generated: '生成产物',
};

export const ASSET_CATEGORY_LABEL: Record<AssetCategory, string> = {
  source: '原片',
  character: '人物',
  product: '产品',
  scene: '场景',
  audio: '音频',
};
