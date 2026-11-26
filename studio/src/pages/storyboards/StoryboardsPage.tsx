import { CardSkeletonGrid, Chip, CoverImage, ErrorState, PageHeader } from '@/shared/ui';
import { formatDuration } from '@/shared/lib/format';
import { useStoryboards } from '@/entities/storyboard/api/storyboard.api';

/**
 * 分镜模板库。
 *
 * 这页**只有列表**：后端 `GET /api/v1/storyboards` 是这个模块唯一的接口，
 * 没有创建、没有编辑、没有删除。
 *
 * 所以以前这里挂着的两个「假交互」都摘掉了：
 * - 「新建分镜」按钮点了只弹一个 toast（`toast('新建分镜')`）—— 点了什么也没发生，
 *   却给了一个「这里能新建」的错觉；
 * - 卡片带 `cursor-pointer` 和 hover 上浮，却**没有 onClick** ——
 *   鼠标移上去像是能点，点下去毫无反应。
 *
 * 假交互比没有交互更糟：用户会以为是自己找错了地方。
 * 等后端真的有了创建接口，再把入口挂回来 —— 那时候它会是真的。
 */
export default function StoryboardsPage() {
  const { data, isLoading, isError, error, refetch } = useStoryboards();

  return (
    <>
      <PageHeader title="分镜模板库" />

      <div className="mt-4">
        {isLoading ? (
          <CardSkeletonGrid count={6} />
        ) : isError ? (
          <ErrorState error={error} title="分镜加载失败" onRetry={() => void refetch()} />
        ) : (
          <div className="grid grid-cols-[repeat(auto-fit,minmax(240px,1fr))] gap-3.5">
            {(data?.items ?? []).map((sb) => (
              <article
                key={sb.id}
                className="group overflow-hidden rounded-card border border-line-soft bg-panel transition-colors hover:border-line-strong"
              >
                <CoverImage
                  src={sb.coverUrl}
                  seed={sb.id}
                  alt={sb.name}
                  className="aspect-video w-full"
                  imgClassName="transition-transform duration-300 group-hover:scale-[1.03]"
                />
                <div className="p-2.5 pb-3">
                  <p className="text-body font-semibold text-ink">{sb.name}</p>
                  <p className="mt-0.5 text-meta text-ink-mute">{sb.description}</p>
                  <div className="mt-2 flex items-center gap-1.5">
                    <Chip className="font-mono tabular-nums">{sb.shotCount} 镜头</Chip>
                    <Chip className="font-mono tabular-nums">{formatDuration(sb.durationSec)}</Chip>
                    <Chip className="ml-auto font-mono tabular-nums">
                      {sb.aspectRatio}
                    </Chip>
                  </div>
                </div>
              </article>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
