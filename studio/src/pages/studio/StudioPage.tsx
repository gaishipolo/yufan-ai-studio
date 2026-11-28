import { useMemo, type ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Wand2 } from 'lucide-react';
import {
  Button,
  Chip,
  CoverImage,
  PageHeader,
  Panel,
  PanelHeader,
  ReferenceImages,
  Select,
} from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { toast } from '@/shared/lib/toast';
import { useCreateTask, useTaskList } from '@/entities/task/api/task.queries';
import { useModels } from '@/entities/model/api/model.queries';
import { useShots } from '@/entities/shot/api/shot.api';
import { LibraryFlyout } from '@/entities/library/ui/LibraryFlyout';
import { TextRefPicker } from '@/features/library/TextRefPicker';
import type { LibraryAssetKind } from '@/shared/api/schema';
import { useCan } from '@/shared/access/use-access';
import { ParamFields } from '@/entities/model/ui/ParamFields';
import { GenerationModelSwitchDialog } from '@/features/generation-form/GenerationModelSwitchDialog';
import { useGenerationForm } from '@/features/generation-form/useGenerationForm';
import { useLibtvQueue } from '@/entities/libtv/api/libtv.queries';
import {
  inFlightLibtvIds,
  liveTaskProgress,
  queueLabel,
  upstreamTaskId,
} from '@/entities/libtv/model/types';
import {
  isTaskInFlight,
  type CreateTaskPayload,
  type VideoTask,
} from '@/entities/task/model/types';

export default function StudioPage() {
  const navigate = useNavigate();
  const create = useCreateTask();
  const { data: shots } = useShots();
  const { data: recent } = useTaskList({ page: 1, pageSize: 3, scope: 'mine' });
  const models = useModels('video');
  const canReadLibrary = useCan('library:read');
  const canReadQueue = useCan('task:read');

  const {
    prompt,
    setPrompt,
    currentModel,
    modelOptions,
    changeModel,
    pendingModelId,
    pendingModel,
    confirmModelChange,
    cancelModelChange,
    paramControls,
    paramValues,
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
    error,
    submit,
    pending,
  } = useGenerationForm<VideoTask>({
    kind: 'video',
    models,
    canReadLibrary,
    titleFromPrompt: text => text.slice(0, 40),
    submit: payload =>
      new Promise<VideoTask>((resolve, reject) => {
        create.mutate(payload as CreateTaskPayload, {
          onSuccess: resolve,
          onError: reject,
        });
      }),
    onSuccess: () => {
      toast('已提交生成，正在跳转任务中心', 'success');
      navigate('/tasks');
    },
    onError: message => toast(message, 'error'),
  });

  const recentLibtvIds = useMemo(
    () => inFlightLibtvIds(recent?.items ?? []),
    [recent?.items],
  );
  const { data: queueData } = useLibtvQueue(recentLibtvIds, canReadQueue);
  const queueByTask = useMemo(
    () => new Map((queueData?.items ?? []).map((item) => [item.taskId, item])),
    [queueData?.items],
  );

  const storyboard = useMemo(() => (shots?.items ?? []).slice(0, 4), [shots]);

  return (
    <>
      {/* 标题只说事：这一页叫什么。做什么、怎么用，页面里已经摆着输入框、
          参考图区和参数控件了 —— 再写一段话介绍一遍，是把看得见的东西
          重新念一遍。流程说明在顶部模块宫格里写着「描述→脚本→分镜→成片」，
          不用在这儿再抄一遍。 */}
      <PageHeader title="生成工作台" />

      <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)] 2xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1fr)]">
        {/* 左：创作输入 */}
        <Panel className="self-start lg:row-span-2 2xl:row-span-1">
          <PanelHeader title="创作输入" />

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
              // 没有素材库查看权限时不画那个按钮：画出来点了只会弹一句「没有权限」
              onOpenLibrary={canReadLibrary ? () => setLibraryOpen((open) => !open) : undefined}
              libraryOpen={libraryOpen && canReadLibrary}
            />
          )}

          <Field label="画面描述" required>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={3}
              placeholder="例：超市抢购场景，手持镜头跟拍，人群涌动，暖色顶光，电影感浅景深，15 秒"
              className={cn(
                'w-full resize-y rounded-lg border bg-panel-soft px-2.5 py-2 text-body leading-relaxed outline-none transition placeholder:text-ink-mute focus:border-primary-line focus:bg-panel focus:shadow-ring',
                error ? 'border-danger' : 'border-line',
              )}
            />
            {error && <p className="mt-1 text-meta text-danger">{error}</p>}
          </Field>

          <div className="mb-2.5">
            <TextRefPicker
              value={textRefs}
              onChange={setTextRefs}
              prompt={prompt}
              onPromptChange={setPrompt}
            />
          </div>

          <div className="grid grid-cols-2 gap-2.5">
            {/* 模型是什么，写在它自己标签旁边：这一行只交代身份，
                至于它支持哪些时长画幅，下面那些控件自己会说。 */}
            <Field label="生成模型" hint={currentModel?.description}>
              <Select
                block
                options={modelOptions}
                value={currentModel?.id ?? ''}
                onChange={(e) => changeModel(e.target.value)}
              />
            </Field>
          </div>

          {/* 参数区完全由模型声明驱动：时长、画幅、计费模式……后端声明什么就渲染什么，
              没声明的不出现（省得提交时被 400）。 */}
          {paramControls.length > 0 && (
            <ParamFields
              className="mb-2.5"
              idPrefix="studio-param"
              controls={paramControls}
              values={paramValues}
              onChange={setParam}
            />
          )}

          <Button
            variant="primary"
            size="lg"
            block
            className="mt-1"
            icon={<Wand2 size={15} />}
            loading={pending}
            onClick={() => void submit()}
          >
            生成视频任务
          </Button>
        </Panel>

        {/* 右：常用镜头 + 最近生成（窄屏叠成一列，2xl 起并排铺满） */}
        <Panel className="self-start">
          {/* 这里列的是镜头库里的运镜词条，不是「系统把你这段话拆成了 4 个镜头」——
              以前标题写着「自动拆解」，是在替一个并不存在的机制吹牛。
              标题改成它真正是的东西，右边给一条去镜头库的路。 */}
          <PanelHeader
            title="常用镜头"
            meta={storyboard.length || undefined}
            actions={
              <Link
                to="/shots"
                className="text-meta text-ink-mute transition-colors hover:text-primary"
              >
                全部 →
              </Link>
            }
          />
          <div className="flex flex-col gap-2">
            {storyboard.map((shot, i) => (
              <div
                key={shot.id}
                className="flex items-center gap-2.5 rounded-lg border border-line-soft bg-panel p-[7px]"
              >
                <span className="grid h-5 w-5 shrink-0 place-items-center rounded-md bg-primary-soft font-mono text-micro font-bold tabular-nums text-primary-deep">
                  {i + 1}
                </span>
                <CoverImage src={shot.coverUrl} seed={shot.id} className="h-[34px] w-11 shrink-0 rounded-md" />
                <div className="min-w-0 flex-1">
                  <b className="block truncate text-xs font-semibold text-ink">{shot.name}</b>
                  {/* 运镜词条本身才是有用的内容 —— 以前这里写「运镜已编排」，
                      说了等于没说，用户看不到能插进提示词的那句话。 */}
                  <span className="block truncate text-micro text-ink-mute">{shot.promptFragment}</span>
                </div>
                <span className="shrink-0 font-mono text-micro tabular-nums text-ink-mute">
                  {shot.durationSec}s
                </span>
              </div>
            ))}
          </div>
        </Panel>

        <Panel className="self-start">
          <PanelHeader title="最近生成" />
          <div className="flex flex-col gap-2">
            {(recent?.items ?? []).map((task: VideoTask) => {
              const queueItem = queueByTask.get(upstreamTaskId(task.externalId) ?? '') ?? null;
              const hint = queueLabel(queueItem);
              const shownProgress = liveTaskProgress(task.progress, queueItem);
              const inFlight = isTaskInFlight(task);
              return (
                <button
                  key={task.id}
                  type="button"
                  onClick={() => navigate('/tasks')}
                  className="flex items-center gap-2.5 rounded-lg border border-line-soft bg-panel p-[7px] text-left transition-colors hover:border-line-strong"
                >
                  <CoverImage src={task.coverUrl} seed={task.id} className="h-[34px] w-11 shrink-0 rounded-md" />
                  <div className="min-w-0 flex-1">
                    <b className="block truncate text-xs font-semibold text-ink">{task.title}</b>
                    <span className="block truncate text-micro text-ink-mute">{task.model}</span>
                    {inFlight && hint ? (
                      <span className="block truncate text-micro text-warn-deep" title={hint}>
                        {hint}
                      </span>
                    ) : task.status === 'failed' && task.errorMessage ? (
                      <span
                        className="block truncate text-micro text-danger"
                        title={task.errorMessage}
                      >
                        {task.errorMessage}
                      </span>
                    ) : null}
                  </div>
                  {/* 画幅是规格不是文字：等宽排，一列竖着扫就对得上 */}
                  <span className="shrink-0 font-mono text-micro tabular-nums text-ink-mute">
                    {task.aspectRatio}
                  </span>
                  <Chip tone={task.status === 'completed' ? 'ok' : task.status === 'failed' ? 'danger' : 'brand'}>
                    {task.status === 'completed'
                      ? '已完成'
                      : task.status === 'failed'
                        ? '失败'
                        : `生成中 · ${shownProgress}%`}
                  </Chip>
                </button>
              );
            })}
          </div>
        </Panel>
      </div>

      {/* 悬浮在页面之上、不加遮罩：遮罩会挡住左侧参考图区，拖拽就落不下去了 */}
      {libraryOpen && canReadLibrary && maxReferences > 0 && (
        <LibraryFlyout
          onClose={() => setLibraryOpen(false)}
          max={maxReferences}
          pickedIds={references.map((item) => item.id)}
          onPick={addLibraryAsset}
          /* 素材库悬浮栏的候选类型也跟模型走：视频替换模型要「原片视频 +
             参考图 + 音频」，只给图片的话用户根本选不到原片。 */
          kinds={referenceKinds as LibraryAssetKind[]}
        />
      )}

      {/* 换模型要丢参考素材，先问一声；文案与事实清单由共享弹窗统一。 */}
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

function Field({
  label,
  hint,
  required,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="mb-2.5">
      {/* 标签在左，注解推到右端 —— 一眼看得出哪段字是字段名、哪段是补充说明。
          挤在标签后面会被读成同一个词组（"生成模型 通用短视频生成…"）。 */}
      <div className="mb-1.5 flex items-baseline gap-x-2 text-meta text-ink-soft">
        <span className="shrink-0 font-medium">
          {label}
          {required && <span className="ml-0.5 text-danger">*</span>}
        </span>
        {hint && (
          <span className="ml-auto min-w-0 max-w-[72%] text-right text-micro text-ink-mute">{hint}</span>
        )}
      </div>
      {children}
    </div>
  );
}
