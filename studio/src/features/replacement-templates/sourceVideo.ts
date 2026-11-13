import type { ArchivedAsset, LibraryAssetOut } from '@/shared/api/schema';
import type { ReplacementFitness } from '@/entities/asset/model/archive-fit';

/**
 * 工作台里的「原片视图」：只留卡片、适配判定与提示词预览真正读的几项。
 *
 * **为什么不直接复用 `ArchivedAsset`**：v2 的 `library_assets` 不对外暴露
 * `path` / `sha256`，投影过去只能编两个空串——「看起来有、其实是空的」字段
 * 迟早被别处当真用（下载、去重、缓存键都读过它们）。这里两个来源各自显式
 * 映射，页面只认这一份形状，不再感知 id 空间。
 */
export interface SourceVideo {
  id: string;
  name: string;
  /** 一律 `video`：候选列表本来就是按 `kind=video` / `category=source` 取的 */
  type: 'video';
  /** 原件地址（生成输入与下载都读它） */
  url: string;
  previewUrl: string | null;
  /** 单位秒；`null` 表示探测失败，与「确实很短」是两件事 */
  durationSec: number | null;
  width: number | null;
  height: number | null;
  hasAudio: boolean | null;
}

export function sourceVideoOfArchive(asset: ArchivedAsset): SourceVideo {
  return {
    id: asset.id,
    name: asset.name,
    type: 'video',
    url: asset.url,
    previewUrl: asset.previewUrl ?? null,
    durationSec: asset.duration ?? null,
    width: asset.width ?? null,
    height: asset.height ?? null,
    hasAudio: asset.hasAudio ?? null,
  };
}

/** 列表项与详情项共同的字段；详情比列表多几个，投影不关心。 */
type LibraryVideo = Pick<
  LibraryAssetOut,
  'id' | 'name' | 'url' | 'previewUrl' | 'durationSec' | 'width' | 'height' | 'hasAudio'
>;

export function sourceVideoOfLibrary(asset: LibraryVideo): SourceVideo {
  return {
    id: asset.id,
    name: asset.name,
    type: 'video',
    url: asset.url ?? '',
    previewUrl: asset.previewUrl ?? null,
    durationSec: asset.durationSec ?? null,
    width: asset.width ?? null,
    height: asset.height ?? null,
    hasAudio: asset.hasAudio ?? null,
  };
}

/**
 * 能不能拿这条原片做替换。
 *
 * 与后端 prepare 的门禁同一口径（1–15 秒 + 必须有音轨），提前到页面上只是让
 * 「注定失败的准备」不用真的发出去。**不再判分类**：v2 的资产没有 `category`，
 * 「是不是原片」由列表的 `origin` 决定，在这里再判一次只能靠猜。
 */
export function sourceVideoFitness(video: SourceVideo): ReplacementFitness {
  if (video.hasAudio !== true) {
    return {ok: false, reason: video.hasAudio === false ? '缺少音轨' : '音轨信息缺失，需重新探测'};
  }
  if (video.durationSec == null) return {ok: false, reason: '时长未知，需重新探测'};
  if (video.durationSec < 1 || video.durationSec > 15) return {ok: false, reason: '时长不在 1–15 秒'};
  return {ok: true};
}
