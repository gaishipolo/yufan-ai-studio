import { useAssetCategories } from '@/entities/asset/api/category.api';
import { Tabs } from '@/shared/ui';
import { MEDIA_TYPES } from './model/library';
import { cn } from '@/shared/lib/cn';

/**
 * 第②段：分类条。
 *
 * 两级，但**只有一级是真节点**：
 * - 一级是分类树（收件箱 + 五个初始用途 + 用户自建），对应 `category_id`，
 *   每个库一棵、互不共享；
 * - 二级是媒体类型分面（全部 / 图片 / 视频 / 音频）。它不是分类树的节点（C1），
 *   只是同一个分类下的一个筛选维度——所以它折叠在下方，且**永远不参与层级**。
 *
 * 为什么不做成两个真实层级：建了 `parent_id` 就一定有人往里插第三层，然后
 * 面包屑、计数、拖拽落点全部要重做，而"媒体类型"作为节点的唯一好处是
 * "能移动它"，那件事本来就没有意义。
 *
 * **树从接口取，不从列表响应里挤**：切库的瞬间列表还是上一个库的数据，
 * 从它推出来的分类条会先闪一下旧树再跳成新树，那个闪烁会被读成"点错了"。
 *
 * 计数缺失时不显示数字：`count` 为 0 与"拿不到"是两件事，把后者渲染成 0
 * 会让人以为这个分类空了。
 */
export function CategoryBar({
  library,
  categoryId,
  mediaType,
  onCategory,
  onMediaType,
}: {
  library: string;
  /** 空串 = 全部素材（跨分类聚合） */
  categoryId: string;
  /** 空串 = 不限媒体类型 */
  mediaType: string;
  onCategory: (next: string) => void;
  onMediaType: (next: string) => void;
}) {
  const {data, error} = useAssetCategories(library);
  const items = data?.items ?? [];
  const total = items.reduce((sum, item) => sum + (item.count ?? 0), 0);

  // 拿不到树时**仍然渲染「全部」与媒体类型**：分类条整块消失会让页面结构
  // 跳一下，而用户会以为是页面坏了。分类读失败是局部问题，不该连累筛选。
  return (
    <div className="space-y-2">
      <Tabs
        aria-label="素材分类"
        items={[
          // 「全部素材」放在最前：它是跨分类聚合，也是绝大多数时候的落点
          {value: '', label: '全部素材', count: data ? total : undefined},
          ...items.map(item => ({
            value: item.id,
            label: item.name,
            count: item.count,
          })),
        ]}
        value={categoryId}
        onChange={onCategory}
      />
      {error && (
        <p className="text-[11px] text-ink-mute">
          分类读不出来（{error.message}），只能用媒体类型筛选。
        </p>
      )}
      <div className="flex flex-wrap items-center gap-1.5 text-xs text-ink-mute">
        <span className="shrink-0">媒体类型</span>
        {MEDIA_TYPES.map(item => (
          <button
            key={item.key || 'all'}
            type="button"
            aria-pressed={mediaType === item.key}
            onClick={() => onMediaType(item.key)}
            className={cn(
              'inline-flex h-6 items-center rounded-pill border px-2 text-[11px]',
              mediaType === item.key
                ? 'border-primary-line bg-primary-soft font-semibold text-primary-deep'
                : 'border-line bg-panel text-ink-soft hover:border-line-strong',
            )}
          >
            {item.label}
          </button>
        ))}
      </div>
    </div>
  );
}
