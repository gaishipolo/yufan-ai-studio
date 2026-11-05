import { LayoutTemplate, Plus } from 'lucide-react';
import {
  Button,
  CardSkeletonGrid,
  Chip,
  CoverImage,
  EmptyState,
  ErrorState,
  PageHeader,
  Select,
} from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import { useTemplates } from '@/entities/template/api/template.api';

const INDUSTRY_OPTIONS = [
  { value: 'all', label: '全部行业' },
  { value: '电商', label: '电商' },
  { value: '品牌', label: '品牌' },
  { value: '内容', label: '内容' },
  { value: '本地生活', label: '本地生活' },
];

export default function TemplatesPage() {
  const { data, isLoading, isError, error, refetch } = useTemplates();

  return (
    <>
      <PageHeader
        title="模板库"
        description="沉淀高复用度的分镜结构，一键套用生成同类型短视频。"
        actions={
          <>
            <Select options={INDUSTRY_OPTIONS} defaultValue="all" />
            <Button size="sm" variant="primary" icon={<Plus size={13} />} onClick={() => toast('新建模板')}>
              新建模板
            </Button>
          </>
        }
      />

      <div className="mt-4">
        {isLoading ? (
          <CardSkeletonGrid count={6} />
        ) : isError ? (
          <ErrorState error={error} title="模板加载失败" onRetry={() => void refetch()} />
        ) : (data?.items.length ?? 0) === 0 ? (
          <EmptyState
            icon={<LayoutTemplate size={40} strokeWidth={1.4} />}
            title="还没有模板"
            description="把常用的分镜结构沉淀成模板，下次一键套用"
          />
        ) : (
          <div className="grid grid-cols-[repeat(auto-fit,minmax(240px,1fr))] gap-3.5">
            {(data?.items ?? []).map((tpl) => (
              <article
                key={tpl.id}
                className="group cursor-pointer overflow-hidden rounded-card border border-line-soft bg-panel transition-all hover:-translate-y-0.5 hover:border-line-strong hover:shadow-card"
              >
                <CoverImage
                  src={tpl.coverUrl}
                  seed={tpl.id}
                  alt={tpl.name}
                  className="aspect-video w-full"
                  imgClassName="transition-transform duration-300 group-hover:scale-[1.03]"
                />
                <div className="p-2.5 pb-3">
                  <p className="text-[13px] font-semibold text-ink">{tpl.name}</p>
                  <p className="mt-0.5 text-[11px] text-ink-mute">{tpl.description}</p>
                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    {tpl.tags.map((tag) => (
                      <Chip key={tag}>{tag}</Chip>
                    ))}
                    <Chip tone="primary" className="ml-auto">
                      {tpl.shotCount} 镜头
                    </Chip>
                    <span className="shrink-0 text-[10.5px] text-ink-mute">用过 {tpl.usageCount}</span>

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
