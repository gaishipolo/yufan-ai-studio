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
  Panel,
  PanelHeader,
  Select,
  ReferenceImages,
} from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { FORM_LIMITS } from '@/shared/lib/formLimits';
import { toast } from '@/shared/lib/toast';
import { formatDateTime } from '@/shared/lib/format';
import { useImageModels } from '@/entities/model/api/model.queries';
import { useCan } from '@/shared/access/use-access';
import { ParamFields } from '@/entities/model/ui/ParamFields';
import { LibraryFlyout } from '@/entities/library/ui/LibraryFlyout';
import { TextRefPicker } from '@/features/library/TextRefPicker';
import { useGenerateImage, useImages } from '@/entities/image/api/image.api';
import type { GenerateImagePayload, ImageJob } from '@/entities/image/model/types';
import { imageSrcOf, imageStatusChipOf } from '@/entities/image/model/status';
import { GenerationModelSwitchDialog } from '@/features/generation-form/GenerationModelSwitchDialog';
import { useGenerationForm } from '@/features/generation-form/useGenerationForm';

export default function ImagePage() {
  const [page, setPage] = useState(1);
  const { models, isLoading: modelsLoading, isError: modelsError } = useImageModels();
  const { data, isLoading, isFetching, isError, error, refetch } = useImages(page);
  const generate = useGenerateImage();
  const canGenerate = useCan('image:create');

  const {
    prompt,
    setPrompt,
    promptLimit,
    currentModel: model,
    modelOptions,
    changeModel,
    pendingModelId,
    pendingModel,
    confirmModelChange,
    cancelModelChange,
    paramControls: controls,
    paramValues: values,
    setParam,
    references,
    referenceKinds,
    referenceLimits,
    maxReferences,
    addReferences,
    addLibraryAsset,
    removeReference,
    textRefs,
    setTextRefs,
    libraryOpen,
    setLibraryOpen,
    uploading,
    error: formError,
    submit,
    pending,
    canSubmit,
  } = useGenerationForm<ImageJob>({
    kind: 'image',
    models,
    promptLimit: FORM_LIMITS.imagePrompt,
    canSubmit: canGenerate,
    omitDefaultModel: true,
    clearPromptOnSuccess: true,
    submit: payload =>
      new Promise<ImageJob>((resolve, reject) => {
        generate.mutate(payload as GenerateImagePayload, {
          onSuccess: resolve,
          onError: reject,
        });
      }),
    onSuccess: () => {
      toast('已提交，正在排队生成', 'success');
      // 新任务在最前面：停在旧页会看不到自己刚提交的那张。
      // 回到第 1 页后 useImages(1) 会因为 queued 自动开始轮询。
      setPage(1);
    },
    onError: message => toast(message, 'error'),
    onValidationError: message => toast(message, 'error'),
  });

  const total = data?.total ?? 0;
  const pageSize = data?.pageSize ?? 24;
  // 没有数据（加载中 / 请求失败）时给 0：Pager 据此判断「还不知道总数」，不去回收页码 ——
  // 否则一次网络抖动就会把用户从第 3 页踢回第 1 页。越界回收统一在 Pager 里做。
  const totalPages = data ? Math.max(1, Math.ceil(total / pageSize)) : 0;


  return (
    <>
      <PageHeader title="生图" />

      <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)] xl:grid-cols-[minmax(0,0.85fr)_minmax(0,1.6fr)] 2xl:grid-cols-[minmax(0,0.7fr)_minmax(0,1.9fr)]">
        <Panel className="self-start">
          <PanelHeader title="图片生成" />

          <div className="mb-2.5">
            <div className="mb-1.5 text-meta text-ink-soft">提示词</div>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={4}
              maxLength={promptLimit}
              placeholder="例：赛博朋克街头，霓虹灯牌，雨夜地面反光，电影级打光，超高细节"
              className={cn(
                'w-full resize-y rounded-lg border bg-panel-soft px-2.5 py-2 text-body leading-relaxed outline-none transition placeholder:text-ink-mute focus:border-primary-line focus:bg-panel focus:shadow-ring',
                formError ? 'border-danger' : 'border-line',
              )}
            />
            {formError && <p className="mt-1 text-meta text-danger">{formError}</p>}
            <div className="mt-1 text-right font-mono text-micro tabular-nums text-ink-mute">
              {prompt.length} / {promptLimit}
            </div>
          </div>

          <div className="mb-2.5">
            <TextRefPicker
              value={textRefs}
              onChange={setTextRefs}
              prompt={prompt}
              onPromptChange={setPrompt}
            />
          </div>

          <div className="mb-2.5">
            <div className="mb-1.5 text-meta text-ink-soft">模型</div>
            <Select
              block
              aria-label="模型"
              options={modelOptions}
              value={model?.id ?? ''}
              disabled={!modelOptions.length}
              onChange={(e) => changeModel(e.target.value)}
            />
            {!model && (
              <p className="mt-1 text-meta text-ink-mute">
                {modelsLoading
                  ? '模型目录加载中…'
                  : modelsError
                    ? '模型目录加载失败，请稍后重试'
                    : '暂无可用的生图模型'}
              </p>
            )}
          </div>

          {maxReferences > 0 && (
            <ReferenceImages
              items={references}
              max={maxReferences}
              kinds={referenceKinds}
              limits={referenceLimits}
              busy={uploading}
              onPick={addReferences}
              onRemove={removeReference}
              onAddAsset={addLibraryAsset}
              onOpenLibrary={() => setLibraryOpen((open) => !open)}
              libraryOpen={libraryOpen}
            />
          )}

          <ParamFields
            controls={controls}
            values={values}
            onChange={setParam}
          />

          <Button
            variant="primary"
            size="lg"
            block
            className="mt-3"
            icon={<Wand2 size={15} />}
            loading={pending}
            disabled={!canSubmit}
            title={canGenerate ? undefined : '当前账号没有生图权限（image:create）'}
            onClick={() => void submit()}
          >
            开始生成
          </Button>
        </Panel>

        <Panel>
          {/* 张数是数得出来的，写在标题右边；不另起一行解释「这些图是从哪来的」 */}
          <PanelHeader
            title="历史记录"
            meta={isError ? '—' : `${data?.total ?? 0} 张`}
          />

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
                      'hover:border-line-strong',
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
                      {/* 用了参考图要说出来：否则用户对比两张图发现「不像」，也看不出
                          差别在哪——参考图只在提交时存在，成图里是看不出来的 */}
                      {job.referenceLibraryIds?.length ? (
                        <div className="absolute right-1.5 top-1.5 z-10">
                          <Chip>参考 {job.referenceLibraryIds.length}</Chip>
                        </div>
                      ) : null}
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
                      <p className="truncate text-meta text-ink">{job.name}</p>
                      <div className="mt-0.5 flex items-center justify-between text-micro text-ink-mute">
                        {/* 尺寸是规格：等宽排，一列竖着扫就对得上 */}
                        <span className="font-mono tabular-nums">{job.size}</span>
                        <span>{formatDateTime(job.createdAt)}</span>
                      </div>
                      {job.errorMessage && (
                        <p
                          className="mt-1 truncate text-micro text-danger-deep"
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
        </Panel>
      </div>

      {/* 悬浮在页面之上、不加遮罩：遮罩会挡住左侧参考图区，拖拽就落不下去了 */}
      {libraryOpen && maxReferences > 0 && (
        <LibraryFlyout
          onClose={() => setLibraryOpen(false)}
          max={maxReferences}
          pickedIds={references.map((item) => item.id)}
          onPick={addLibraryAsset}
        />
      )}

      <GenerationModelSwitchDialog
        open={pendingModelId !== null}
        referenceCount={references.length}
        nextModelLabel={pendingModel?.label}
        onCancel={cancelModelChange}
        onConfirm={confirmModelChange}
      />
    </>
  );
}
