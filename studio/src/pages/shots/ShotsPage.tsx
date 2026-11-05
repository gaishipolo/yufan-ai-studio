import { useMemo, useState } from 'react';
import { CardSkeletonGrid, CoverImage, PageHeader, SearchInput } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import { formatDuration } from '@/shared/lib/format';
import { useShots } from '@/entities/shot/api/shot.api';

export default function ShotsPage() {
  const { data, isLoading } = useShots();
  const [keyword, setKeyword] = useState('');

  const items = useMemo(() => {
    const list = data?.items ?? [];
    const kw = keyword.trim().toLowerCase();
    if (!kw) return list;
    return list.filter(
      (s) =>
        s.name.toLowerCase().includes(kw) || s.promptFragment.toLowerCase().includes(kw),
    );
  }, [data, keyword]);

  return (
    <>
      <PageHeader
        title="镜头库"
        description="标准化运镜词条，插入提示词即可复用相同镜头语言。"
        actions={
          <SearchInput
            placeholder="搜索运镜 / 英文指令"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            wrapperClassName="w-full sm:w-[240px]"
          />
        }
      />

      <div className="mt-4">
        {isLoading ? (
          <CardSkeletonGrid count={6} />
        ) : (
          <div className="grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-2.5 md:grid-cols-[repeat(auto-fit,minmax(200px,1fr))] md:gap-3.5">
            {items.map((shot) => (
              <button
                key={shot.id}
                type="button"
                onClick={() => {
                  void navigator.clipboard?.writeText(shot.promptFragment).catch(() => undefined);
                  toast(`已复制运镜指令：${shot.promptFragment.slice(0, 24)}…`, 'success');
                }}
                aria-label={`复制运镜指令：${shot.name}`}
                className="group w-full cursor-pointer overflow-hidden rounded-card border border-line-soft bg-panel text-left transition-all hover:-translate-y-0.5 hover:border-line-strong hover:shadow-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
              >
                <div className="relative">
                  <CoverImage
                    src={shot.coverUrl}
                    seed={shot.id}
                    className="aspect-video w-full"
                    imgClassName="transition-transform duration-300 group-hover:scale-[1.03]"
                  />
                  <span className="absolute right-[7px] top-[7px] rounded-[5px] bg-[rgba(10,22,28,.55)] px-1.5 py-px text-[10px] text-white backdrop-blur-[4px]">
                    {formatDuration(shot.durationSec)}
                  </span>
                </div>
                <div className="px-2.5 pb-2.5 pt-2">
                  <p className="truncate text-[11.5px] text-ink">{shot.name}</p>
                  <div className="mt-1 flex items-center justify-between gap-2 text-[10.5px] text-ink-mute">
                    <span className="truncate font-mono">{shot.promptFragment}</span>
                    <span className="shrink-0">热度 {shot.popularity}</span>
                  </div>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
