import { useState } from 'react';
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
import { useImageModels } from '@/entities/model/api/model.queries';
import {
  isEmptyParamValue,
  paramControlsFor,
  resolveParamValues,
  type ParamValue,
} from '@/entities/model/lib/paramForm';
import { ParamFields } from '@/entities/model/ui/ParamFields';
import { useGenerateImage, useImages } from '@/entities/image/api/image.api';
import type { GenerateImagePayload } from '@/entities/image/model/types';
import { imageSrcOf, imageStatusChipOf } from '@/entities/image/model/status';

export default function ImagePage() {
  const [prompt, setPrompt] = useState('');
  const [modelId, setModelId] = useState('');
  // 只存「用户改过的那几个」，没改的交给 schema 默认值。
  // 存最终值的话，换模型后旧值会留在 state 里，而新模型未必认这些值。
  const [overrides, setOverrides] = useState<Record<string, ParamValue>>({});
  const [page, setPage] = useState(1);

  const { models, isLoading: modelsLoading, isError: modelsError } = useImageModels();
  const { data, isLoading, isFetching, isError, error, refetch } = useImages(page);
  const generate = useGenerateImage();

  // 用户没选过时用 isDefault 模型 —— 后端「model 留空」取的也是它，前端不编默认值
  const model =
    models.find((m) => m.id === modelId) ?? models.find((m) => m.isDefault) ?? models[0];
  // 控件完全由 paramSchema 决定：后端声明什么，这里就渲染什么
  const controls = paramControlsFor(model);
  const values = resolveParamValues(controls, overrides);
  const modelOptions = models.map((m) => ({ value: m.id, label: m.label }));
  const canSubmit = Boolean(model);

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
    if (!model) {
      toast('暂无可用生图模型，请稍后重试', 'error');
      return;
    }

    // `size` / `count` 是接口上的顶层字段（历史列表、统计都按列读），
    // 其余参数走 `params`。键名是契约的一部分，不能随 schema 改名。
    const payload: GenerateImagePayload = { prompt: prompt.trim() };
    const extra: Record<string, ParamValue> = {};
    for (const control of controls) {
      const value = values[control.key];
      if (isEmptyParamValue(value)) continue;
      if (control.key === 'size') payload.size = String(value);
      else if (control.key === 'count') payload.count = Number(value);
      else extra[control.key] = value;
    }
    // 空对象不发：接口里 `params` 是可选字段，发 `{}` 只是噪音
    if (Object.keys(extra).length) payload.params = extra;
    // 默认模型留空，交给后端取默认；只有用户显式改选才传 model
    if (!model.isDefault) payload.model = model.id;

    generate.mutate(payload, {
      onSuccess: () => {
        toast('已提交，正在排队生成', 'success');
        setPrompt('');
        // 新任务在最前面：停在旧页会看不到自己刚提交的那张。
        // 回到第 1 页后 useImages(1) 会因为 queued 自动开始轮询
        setPage(1);
      },
      onError: (e) => toast(e.message, 'error'),
    });
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
                // 换模型的参数面可能完全不同（张数上限、尺寸白名单、甚至多出
                // 一组新控件），旧改动一律清掉，避免带着上一个模型的值提交
                setOverrides({});
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

          <ParamFields
            controls={controls}
            values={values}
            onChange={(key, value) => setOverrides((prev) => ({ ...prev, [key]: value }))}
          />

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
