import { useState } from 'react';
import { Wand2 } from 'lucide-react';
import { Button, CardSkeletonGrid, CoverImage, ErrorState, PageHeader, Pager, Select } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { toast } from '@/shared/lib/toast';
import { formatDateTime } from '@/shared/lib/format';
import { useGenerateImage, useImages } from '@/entities/image/api/image.api';

const SIZE_OPTIONS = ['1024×1536', '1024×1024', '1536×1024'].map((v) => ({ value: v, label: v }));
const COUNT_OPTIONS = ['1', '2', '4'].map((v) => ({ value: v, label: `${v} 张` }));

export default function ImagePage() {
  const [prompt, setPrompt] = useState('');
  const [size, setSize] = useState(SIZE_OPTIONS[0].value);
  const [count, setCount] = useState('1');
  const [page, setPage] = useState(1);

  const { data, isLoading, isFetching, isError, error, refetch } = useImages(page);
  const generate = useGenerateImage();

  const total = data?.total ?? 0;
  const pageSize = data?.pageSize ?? 24;
  // 没有数据（加载中 / 请求失败）时给 0：Pager 据此判断「还不知道总数」，不去回收页码 ——
  // 否则一次网络抖动就会把用户从第 3 页踢回第 1 页。越界回收统一在 Pager 里做。
  const totalPages = data ? Math.max(1, Math.ceil(total / pageSize)) : 0;

  const submit = () => {
    if (!prompt.trim()) {
      toast('请先填写提示词', 'error');
      return;
    }
    generate.mutate(
      { prompt: prompt.trim(), size, count: Number(count) },
      {
        onSuccess: () => {
          toast('图片生成中，稍后刷新查看', 'success');
          setPrompt('');
          // 新任务在最前面：停在旧页会看不到自己刚提交的那张
          setPage(1);
        },
        onError: (e) => toast(e.message, 'error'),
      },
    );
  };

  return (
    <>
      <PageHeader
        title="生图"
        description="文生图 / 图生图，产出可复用的角色、场景与产品图，直接作为视频生成参考。"
      />

      <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)] xl:grid-cols-[minmax(0,0.85fr)_minmax(0,1.6fr)] 2xl:grid-cols-[minmax(0,0.7fr)_minmax(0,1.9fr)]">
        <section className="self-start rounded-card border border-line-soft bg-panel p-3.5">
          <div className="mb-2.5 flex items-center gap-1.5 text-[12.5px] font-semibold text-ink">
            <span className="h-1.5 w-1.5 rounded-full bg-brand" />
            图片生成
          </div>

          <div className="mb-2.5">
            <div className="mb-1.5 text-[11.5px] text-ink-soft">提示词</div>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={4}
              placeholder="例：赛博朋克街头，霓虹灯牌，雨夜地面反光，电影级打光，超高细节"
              className="w-full resize-y rounded-lg border border-line bg-panel-soft px-2.5 py-2 text-[12.5px] leading-relaxed outline-none transition placeholder:text-[#a9b8bf] focus:border-primary-line focus:bg-white focus:shadow-ring"
            />
          </div>

          <div className="grid grid-cols-2 gap-2.5">
            <div>
              <div className="mb-1.5 text-[11.5px] text-ink-soft">尺寸</div>
              <Select block options={SIZE_OPTIONS} value={size} onChange={(e) => setSize(e.target.value)} />
            </div>
            <div>
              <div className="mb-1.5 text-[11.5px] text-ink-soft">数量</div>
              <Select block options={COUNT_OPTIONS} value={count} onChange={(e) => setCount(e.target.value)} />
            </div>
          </div>

          <Button
            variant="primary"
            size="lg"
            block
            className="mt-3"
            icon={<Wand2 size={15} />}
            loading={generate.isPending}
            onClick={submit}
          >
            开始生成
          </Button>
        </section>

        <section className="rounded-card border border-line-soft bg-panel p-3.5">
          <div className="mb-2.5 flex items-center gap-1.5 text-[12.5px] font-semibold text-ink">
            <span className="h-1.5 w-1.5 rounded-full bg-brand" />
            历史记录
            <span className="ml-auto text-[11px] font-normal text-ink-mute">
              {isError ? '共 — 张' : `共 ${data?.total ?? 0} 张`}
            </span>
          </div>

          {isLoading ? (
            <CardSkeletonGrid count={4} />
          ) : isError ? (
            <ErrorState error={error} title="生成记录加载失败" onRetry={() => void refetch()} />
          ) : (
            <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 xl:grid-cols-3 2xl:grid-cols-4">
              {(data?.items ?? []).map((job) => (
                <article
                  key={job.id}
                  className={cn(
                    'overflow-hidden rounded-card border border-line-soft bg-panel transition-all',
                    'hover:-translate-y-0.5 hover:border-line-strong hover:shadow-card',
                  )}
                >
                  <CoverImage src={job.coverUrl} seed={job.id} className="aspect-[4/5] w-full" />
                  <div className="px-2 pb-2 pt-1.5">
                    <p className="truncate text-[11.5px] text-ink">{job.name}</p>
                    <div className="mt-0.5 flex items-center justify-between text-[10.5px] text-ink-mute">
                      <span>{job.size}</span>
                      <span>{formatDateTime(job.createdAt)}</span>
                    </div>
                  </div>
                </article>
              ))}
            </div>
          )}

          {/* 后端每页 24 条、total 是全量：没有翻页入口时第 25 张起在界面上不存在 */}
          <Pager
            page={page}
            totalPages={totalPages}
            busy={isFetching}
            onChange={setPage}
            className="mt-2.5 px-0"
          />
        </section>
      </div>
    </>
  );
}
