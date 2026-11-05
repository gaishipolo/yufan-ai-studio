import { Plus } from 'lucide-react';
import { Button, CardSkeletonGrid, Chip, CoverImage, PageHeader } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import { formatDuration } from '@/shared/lib/format';
import { useStoryboards } from '@/entities/storyboard/api/storyboard.api';

export default function StoryboardsPage() {
  const { data, isLoading } = useStoryboards();

  return (
    <>
      <PageHeader
        title="分镜模板库"
        description="已编排好的镜头序列，可直接生成视频任务或拆分到工作台二次编辑。"
        actions={
          <Button size="sm" variant="primary" icon={<Plus size={13} />} onClick={() => toast('新建分镜')}>
            新建分镜
          </Button>
        }
      />

      <div className="mt-4">
        {isLoading ? (
          <CardSkeletonGrid count={6} />
        ) : (
          <div className="grid grid-cols-[repeat(auto-fit,minmax(240px,1fr))] gap-3.5">
            {(data?.items ?? []).map((sb) => (
              <article
                key={sb.id}
                className="group cursor-pointer overflow-hidden rounded-card border border-line-soft bg-panel transition-all hover:-translate-y-0.5 hover:border-line-strong hover:shadow-card"
              >
                <CoverImage
                  src={sb.coverUrl}
                  seed={sb.id}
                  alt={sb.name}
                  className="aspect-video w-full"
                  imgClassName="transition-transform duration-300 group-hover:scale-[1.03]"
                />
                <div className="p-2.5 pb-3">
                  <p className="text-[13px] font-semibold text-ink">{sb.name}</p>
                  <p className="mt-0.5 text-[11px] text-ink-mute">{sb.description}</p>
                  <div className="mt-2 flex items-center gap-1.5">
                    <Chip>{sb.shotCount} 镜头</Chip>
                    <Chip>{formatDuration(sb.durationSec)}</Chip>
                    <Chip tone="primary" className="ml-auto">
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
