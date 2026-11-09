import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Wand2 } from 'lucide-react';
import {
  Button,
  CardSkeletonGrid,
  Chip,
  CoverImage,
  ErrorState,
  PageHeader,
  Pager,
  Select,
} from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { toast } from '@/shared/lib/toast';
import { formatDateTime } from '@/shared/lib/format';
import { imageFormSchemaFor, useImageModels } from '@/entities/model/api/model.queries';
import { useGenerateImage, useImages } from '@/entities/image/api/image.api';
import { imageSrcOf, imageStatusChipOf } from '@/entities/image/model/status';

export default function ImagePage() {
  const [params] = useSearchParams();
  /**
   * `?prompt=` 是素材详情「提取提示词微调」的落点：它把配方里的提示词带过来，
   * **只填不提交**——提交会扣费，而用户点的是"拿来看看"。
   *
   * 用 effect 同步而不是只靠 `useState` 初值：页面已经挂在 `/image` 上的时候
   * 再点一次"提取提示词"，初值不会重跑，输入框里还是上一段文字。
   */
  const preset = params.get('prompt') ?? '';
  const [prompt, setPrompt] = useState(preset);
  useEffect(() => {
    if (preset) setPrompt(preset);
  }, [preset]);
  const [modelId, setModelId] = useState('');
  const [sizeOverride, setSizeOverride] = useState('');
  const [countOverride, setCountOverride] = useState('');
  const [page, setPage] = useState(1);

  const { models, isLoading: modelsLoading, isError: modelsError } = useImageModels();
  const { data, isLoading, isFetching, isError, error, refetch } = useImages(page);
  const generate = useGenerateImage();

  // 用户没选过时用 isDefault 模型 —— 后端「model 留空」取的也是它，前端不编默认值
  const model =
    models.find((m) => m.id === modelId) ?? models.find((m) => m.isDefault) ?? models[0];
  const form = imageFormSchemaFor(model);
  const modelOptions = models.map((m) => ({ value: m.id, label: m.label }));
  const sizeOptions = form.sizeOptions.map((v) => ({ value: v, label: v }));
  const countOptions = form.countOptions.map((v) => ({ value: String(v), label: `${v} 张` }));

  // 存的是「用户覆盖值」而不是最终值：换模型或后端收紧档位后，
  // 旧值不在新 options 里会自动回落到该模型的 default，不会出现选了却没生效的假象
  const size = sizeOptions.some((o) => o.value === sizeOverride)
    ? sizeOverride
    : form.sizeDefault;
  const countFallback =
    countOptions.find((o) => o.value === String(form.countDefault))?.value ??
    countOptions[0]?.value ??
    '';
  const count = countOptions.some((o) => o.value === countOverride) ? countOverride : countFallback;
  const canSubmit = Boolean(model && size && count);

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
    if (!model || !size || !count) {
      toast('暂无可用生图模型，请稍后重试', 'error');
      return;
    }
    generate.mutate(
      {
        prompt: prompt.trim(),
        size,
        count: Number(count),
        // 默认模型留空，交给后端取默认；只有用户显式改选才传 model
        ...(model.isDefault ? {} : { model: model.id }),
      },
      {
        onSuccess: () => {
          toast('已提交，正在排队生成', 'success');
          setPrompt('');
          // 新任务在最前面：停在旧页会看不到自己刚提交的那张。
          // 回到第 1 页后 useImages(1) 会因为 queued 自动开始轮询
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

          <div className="mb-2.5">
            <div className="mb-1.5 text-[11.5px] text-ink-soft">模型</div>
            <Select
              block
              aria-label="模型"
              options={modelOptions}
              value={model?.id ?? ''}
              disabled={!modelOptions.length}
              onChange={(e) => {
                setModelId(e.target.value);
                setSizeOverride('');
                setCountOverride('');
              }}
            />
            {!model && (
              <p className="mt-1 text-[11px] text-ink-mute">
                {modelsLoading
                  ? '模型目录加载中…'
                  : modelsError
                    ? '模型目录加载失败，请稍后重试'
                    : '暂无可用的生图模型'}
              </p>
            )}
          </div>

          <div className="grid grid-cols-2 gap-2.5">
            <div>
              <div className="mb-1.5 text-[11.5px] text-ink-soft">尺寸</div>
              <Select
                block
                aria-label="尺寸"
                options={sizeOptions}
                value={size}
                disabled={!sizeOptions.length}
                onChange={(e) => setSizeOverride(e.target.value)}
              />
            </div>
            <div>
              <div className="mb-1.5 text-[11.5px] text-ink-soft">数量</div>
              <Select
                block
                aria-label="数量"
                options={countOptions}
                value={count}
                disabled={!countOptions.length}
                onChange={(e) => setCountOverride(e.target.value)}
              />
            </div>
          </div>

          <Button
            variant="primary"
            size="lg"
            block
            className="mt-3"
            icon={<Wand2 size={15} />}
            loading={generate.isPending}
            disabled={!canSubmit}
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
              {(data?.items ?? []).map((job) => {
                const chip = imageStatusChipOf(job);
                return (
                  <article
                    key={job.id}
                    title={job.errorMessage ?? undefined}
                    className={cn(
                      'overflow-hidden rounded-card border border-line-soft bg-panel transition-all',
                      'hover:-translate-y-0.5 hover:border-line-strong hover:shadow-card',
                    )}
                  >
                    <div className="relative">
                      <CoverImage
                        src={imageSrcOf(job)}
                        seed={job.id}
                        className="aspect-[4/5] w-full"
                      />
                      {chip && (
                        <div className="absolute left-1.5 top-1.5 z-10">
                          <Chip tone={chip.tone}>{chip.text}</Chip>
                        </div>
                      )}
                      {chip?.progress != null && (
                        <div className="absolute bottom-0 left-0 right-0 h-1 bg-black/10">
                          <div
                            className="h-full bg-primary transition-all"
                            style={{ width: `${chip.progress}%` }}
                          />
                        </div>
                      )}
                    </div>
                    <div className="px-2 pb-2 pt-1.5">
                      <p className="truncate text-[11.5px] text-ink">{job.name}</p>
                      <div className="mt-0.5 flex items-center justify-between text-[10.5px] text-ink-mute">
                        <span>{job.size}</span>
                        <span>{formatDateTime(job.createdAt)}</span>
                      </div>
                      {job.errorMessage && (
                        <p
                          className="mt-1 truncate text-[10.5px] text-[#d94036]"
                          title={job.errorMessage}
                        >
                          {job.errorMessage}
                        </p>
                      )}
                    </div>
                  </article>
                );
              })}
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
