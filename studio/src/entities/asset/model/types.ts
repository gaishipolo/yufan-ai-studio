export type AssetType = 'image' | 'video' | 'audio';

/** 素材资产（后端 /assets 资源） */
export interface Asset {
  id: string;
  name: string;
  type: AssetType;
  mimeType: string;
  sizeBytes: number;
  coverUrl: string;
  /** 图片宽高 / 视频分辨率 */
  width: number;
  height: number;
  /** 被任务引用次数 */
  refCount: number;
  createdAt: string;
}

export interface AssetListParams {
  type?: AssetType | 'all';
  keyword?: string;
}

export const ASSET_TYPE_LABEL: Record<AssetType, string> = {
  image: '图片',
  video: '视频',
  audio: '音频',
};
