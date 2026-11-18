import {useMemo} from 'react';
import {useQueries} from '@tanstack/react-query';
import {libraryApi} from '@/entities/library/api/library.api';
import type {SpecSlotOut} from '@/shared/api/schema';
import {isOptionalSlot} from './randomness';

/**
 * 随机档的候选池：每个可选槽位去素材库里捞一批候选。
 *
 * ## 为什么走 `useQueries` 而不是每个槽位一个 `useLibrarySelection`
 *
 * 每个槽位一个 hook 的话，槽位数量就是 hook 数量，而 hook 数量必须固定 ——
 * 可选槽位是**随模板变**的（换模板数量就变），写成循环调用 hook 会在
 * 换模板时炸掉 React 的「hook 顺序变了」检查。`useQueries` 正好是
 * 「数量可变的一组查询」这个场景的正解。
 *
 * ## 为什么返回的引用要**稳定**
 *
 * 上层拿这个池子去「随机挑几张」。如果每次渲染都返回一个新对象，
 * 挑的结果就跟着变 —— 使用者看到界面上的素材在两次点击之间换了，
 * 像是页面在抖。所以这里按**内容签名**做 `useMemo`：只有池子里的 id
 * 真的变了，返回的引用才换。
 *
 * ## 为什么按槽位的候选集（标签）过滤
 *
 * 槽位的 `tagIds` 是作者圈定的**候选范围** —— 随机挑也必须在这个范围内挑，
 * 否则会挑进作者根本没打算用的素材（比如挑到别的产品的图）。
 *
 * ## 候选池要多深
 *
 * 每个槽位取 `RANDOM_POOL_SIZE` 张（默认 24，与素材网格一页一致）。
 * 池子太浅的话「随机」的可选范围就那么几张，几次之后用户会发现
 * 「怎么每次都差不多」—— 池子深一点，随机才真的像随机。
 */
export const RANDOM_POOL_SIZE = 24;

/**
 * 取每个可选槽位的候选素材 id 池。
 *
 * @param slots 当前模板的全部槽位（只取可选的）
 * @param enabled 只在「随机」档才发请求
 * @returns 槽位 key → 候选素材 id 列表（引用稳定）
 */
export function useRandomPools(
  slots: SpecSlotOut[],
  enabled: boolean,
): Record<string, string[]> {
  const optional = slots.filter(isOptionalSlot);
  const queries = useQueries({
    queries: optional.map(slot => ({
      queryKey: ['random-pool', slot.key, slot.type, (slot.tagIds ?? []).join(',')],
      queryFn: async () => {
        const page = await libraryApi.selection({
          kind: slot.type,
          ...(slot.tagIds?.length ? {tagIds: slot.tagIds} : {}),
          pageSize: RANDOM_POOL_SIZE,
        });
        return (page.items ?? []).map(item => item.id);
      },
      enabled,
    })),
  });

  /* 池子的**内容签名**：每个槽位挑到的 id 拼起来。
     拿它当 `useMemo` 的依赖 —— 内容不变则引用不变，上层「随机挑」的结果
     就不会在两次渲染之间跳动。 */
  const signature = optional
    .map((slot, index) => `${slot.key}=${(queries[index]?.data ?? []).join(',')}`)
    .join('&');

  return useMemo(() => {
    const pools: Record<string, string[]> = {};
    optional.forEach((slot, index) => {
      const data = queries[index]?.data;
      if (data) pools[slot.key] = data;
    });
    return pools;
    /* `optional` 每次渲染都是新数组，不能进依赖 —— 签名已经涵盖了
       「哪些槽位、每个槽位挑到哪些 id」的全部信息。 */
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);
}
