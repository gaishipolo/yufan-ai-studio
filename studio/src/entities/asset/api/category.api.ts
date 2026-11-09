import { useQuery } from '@tanstack/react-query';
import { api } from '@/shared/api/http';
import type { AssetCategoryList } from '@/shared/api/schema';

/**
 * 分类树（第②段分类条的数据源）。
 *
 * **和素材列表分开取**：分类条要在没有列表请求时也能渲染（首屏、刚切库的瞬间）。
 * 从列表响应里挤出来的话，切库时会先闪一下上一棵树再跳成新树，那个闪烁会被
 * 用户读成「点错了」。
 *
 * `library` 进 queryKey：不带的后果是切库后拿到的还是上一棵缓存树，而分类条
 * 看起来"正常显示"，只是内容属于另一个库——这种错误比空列表更难发现。
 */
export function useAssetCategories(library: string) {
  return useQuery({
    queryKey: ['asset-categories', library],
    queryFn: () => api.get<AssetCategoryList>('/asset-categories', {library}),
    // 分类树变得很慢：只有用户自己加分类才变，而那个动作会主动 invalidate。
    // 30 秒的窗口足够覆盖「切库来回点」的抖动。
    staleTime: 30_000,
  });
}
