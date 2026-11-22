import { useMemo, useState, type ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Wand2 } from 'lucide-react';
import {
  Button,
  Chip,
  ConfirmDialog,
  CoverImage,
  PageHeader,
  Panel,
  PanelHeader,
  ReferenceImages,
  Select,
  type ReferenceItem,
} from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { toast } from '@/shared/lib/toast';
import { useCreateTask, useTaskList } from '@/entities/task/api/task.queries';
import { useModels } from '@/entities/model/api/model.queries';
import { useShots } from '@/entities/shot/api/shot.api';
import { LibraryFlyout } from '@/entities/library/ui/LibraryFlyout';
import { useUploadAsset } from '@/entities/library/api/library.queries';
import { TextRefPicker } from '@/features/library/TextRefPicker';
import type { LibraryAssetKind, LibraryAssetOut } from '@/shared/api/schema';
import { useCan } from '@/shared/access/use-access';
import type { LibraryAssetDrag } from '@/shared/lib/libraryDrag';
import {
  paramControlsFor,
  resolveParamValues,
  type ParamValue,
} from '@/entities/model/lib/paramForm';
import { ParamFields } from '@/entities/model/ui/ParamFields';
import {
  toTaskPayload,
  type TaskPayloadResult,
} from '@/features/template-library/taskPayload';
import { useLibtvQueue } from '@/entities/libtv/api/libtv.queries';
import {
  inFlightLibtvIds,
  liveTaskProgress,
  queueLabel,
  upstreamTaskId,
} from '@/entities/libtv/model/types';
import { isTaskInFlight, type VideoTask } from '@/entities/task/model/types';

export default function StudioPage() {
  const navigate = useNavigate();
  const create = useCreateTask();
  const { data: shots } = useShots();
  const { data: recent } = useTaskList({ page: 1, pageSize: 3, scope: 'mine' });

  // 模型与它声明的全部参数都来自后端 /models：新增模型不需要改这里
  const models = useModels('video');
  const [prompt, setPrompt] = useState('');
  const [modelId, setModelId] = useState('');
  const [error, setError] = useState('');
  /* 参数只存「用户改过的那几个」，没改的交给 schema 默认值。
     存最终值的话，换模型后旧值会留在 state 里，而新模型未必认这些值。 */
  const [overrides, setOverrides] = useState<Record<string, ParamValue>>({});
  /* 参考图：先落素材库拿 id，再随请求下发 id —— 与生图页同一条通道，
     提交的是 id 而不是图片字节，同一张素材可以被多个任务反复引用 */
  const [references, setReferences] = useState<ReferenceItem[]>([]);
  // 文本引用：独立通道，提交时只发 textLibraryIds，不进 referenceLibraryIds
  const [textRefs, setTextRefs] = useState<LibraryAssetOut[]>([]);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const canReadLibrary = useCan('library:read');
  const canReadQueue = useCan('task:read');
  const upload = useUploadAsset();

  /* 换模型会作废已选参考图（参考图上限与支持类型都由模型声明）。
     以前是**静默清空**：用户挑了半天图，换个模型，图无声无息没了 ——
     下拉框换了值，图没了，界面不说发生了什么。丢东西该问一声。
     参考图为空时不问，直接换。 */
  const [pendingModelId, setPendingModelId] = useState<string | null>(null);

  const recentLibtvIds = useMemo(
    () => inFlightLibtvIds(recent?.items ?? []),
    [recent?.items],
  );
  const { data: queueData } = useLibtvQueue(recentLibtvIds, canReadQueue);
  const queueByTask = useMemo(
    () => new Map((queueData?.items ?? []).map((item) => [item.taskId, item])),
    [queueData?.items],
  );

  const currentModel = useMemo(
    () => models.find((item) => item.id === modelId) ?? models.find((m) => m.isDefault) ?? models[0],
    [models, modelId],
  );
  const modelOptions = useMemo(
    () => models.map((item) => ({ value: item.id, label: item.label })),
    [models],
  );

  /* 参数控件完全由模型声明驱动：后端声明什么，这里就渲染什么。
     这一条是硬纪律 —— 之前工作台写死「时长/画幅/风格预设」三件套，
     于是：
     - 风格预设无条件提交，而模型没声明 `stylePreset` 时后端直接 400
       （实测：选 LibTV 提交必失败）；
     - 模型声明了新参数（计费模式等）界面里根本没有入口。
     现在按 schema 渲染，声明了就有，不声明就不出现。 */
  const paramControls = paramControlsFor(currentModel);
  const paramValues = resolveParamValues(paramControls, overrides);

  /* 参考素材的数量上限与类型都由模型声明（`maxReferences` / `referenceKinds`），
     不在界面里写死「只选图片、最多 4 张」——那会把视频替换模型的用户挡在门外。

     `maxReferences = 0` 就**整块不画**：模型没声明收参考素材，后端受理期会
     直接拒（`模型 X 不支持参考素材，请换用支持参考素材的模型`）。
     画一个上传框让用户填完再吃一个 400，比不画更糟。 */
  const referenceKinds = useMemo(
    () => currentModel?.referenceKinds?.length ? currentModel.referenceKinds : ['image'],
    [currentModel],
  );
  const maxReferences = currentModel?.maxReferences ?? 0;

  /* 上传的素材类型跟着模型声明走：模型收音频时，音轨要按 `audio` 入库 ——
     按 `image` 入库会被后端按图片校验拒掉（探测出来的宽高也对不上）。 */
  const uploadKindFor = (file: File): 'image' | 'audio' | null => {
    if (file.type.startsWith('audio/')) {
      return referenceKinds.includes('audio') ? 'audio' : null;
    }
    return 'image';
  };

  const addReferences = async (files: FileList | null) => {
    const picked = Array.from(files ?? []);
    if (!picked.length) return;
    const room = maxReferences - references.length;
    if (room <= 0) {
      toast(`这个模型最多接受 ${maxReferences} 张参考图`, 'error');
      return;
    }
    // 多选超量时只取前面的：先传多余的那几张再丢掉，等于白花一次上传
    for (const file of picked.slice(0, room)) {
      const kind = uploadKindFor(file);
      if (!kind) {
        toast('这个模型不收音频参考素材', 'error');
        continue;
      }
      try {
        const asset = await upload.mutateAsync({ file, kind });
        setReferences((prev) => [
          ...prev,
          { id: asset.id, name: asset.name, url: asset.previewUrl ?? asset.url ?? '' },
        ]);
      } catch (e) {
        toast(e instanceof Error ? e.message : '参考图上传失败', 'error');
      }
    }
  };

  /** 从素材库加入：不重复上传，只把 id / 名称 / 缩略图接进列表 */
  const addLibraryAsset = (asset: LibraryAssetDrag) => {
    // 同一张重复加入没有意义：多图合成里同一张出现两次只会白占一个名额
    if (references.some((item) => item.id === asset.id)) return;
    if (references.length >= maxReferences) {
      toast(`最多 ${maxReferences} 张参考图，先移除一张再添加`, 'error');
      return;
    }
    setReferences((prev) => [...prev, { id: asset.id, name: asset.name, url: asset.url }]);
  };

  /**
   * 真正切换模型：清掉与旧模型绑定的状态。
   *
   * 参考图上限与支持类型都由模型声明，所以旧图在新模型下不一定还能用；
   * 参数同理（新模型未必认这些键，留在 state 里会被后端拒）。
   */
  const applyModel = (nextId: string) => {
    setModelId(nextId);
    setReferences([]);
    setLibraryOpen(false);
    setOverrides({});
  };

  /**
   * 提交：按 `/tasks` 的契约摊平参数。
   *
   * 摊平规则**只有一份**：`toTaskPayload`（模板库那条链也在用）。
   * 它对「`/tasks` 表达不了的键」是**报错而不是丢掉** —— 丢掉的话用户会拿到
   * 一个「参数其实没用上」的任务，界面上还显示成功。
   */
  const submit = () => {
    if (!prompt.trim()) {
      setError('请先填写画面描述');
      return;
    }
    if (!currentModel) {
      setError('暂无可用模型，请稍后重试');
      return;
    }
    setError('');

    /* 编译结果形状与 `compile` 一致：这里没有槽位与模板，直接把参数与素材摊平。
       顶层字段由模型 schema 决定（时长/画幅…），其余声明的参数进 `params`。 */
    const compiled = {
      version: 0,
      model: currentModel.id,
      prompt: prompt.trim(),
      params: Object.fromEntries(
        Object.entries(paramValues).filter(([, value]) => value !== '' && value !== undefined),
      ),
      referenceLibraryIds: references.map((item) => item.id),
      textLibraryIds: textRefs.map((item) => item.id),
      slots: [],
    };

    const plan: TaskPayloadResult = toTaskPayload(
      compiled,
      prompt.trim().slice(0, 40),
      {
        durationSec: Number(currentModel.paramSchema?.durationSec?.default ?? 0) || undefined,
        aspectRatio: (currentModel.paramSchema?.aspectRatio?.default as string | undefined) ?? undefined,
      },
      // 带上模型的参数声明：提交前就能判定「这个键模型没声明」，早一步报出来
      currentModel,
    );
    if (!plan.ok) {
      setError(plan.message);
      return;
    }

    create.mutate(
      {
        ...plan.payload,
        prompt: plan.payload.prompt,
      },
      {
        onSuccess: () => {
          // 参考图跟着这次请求走了，留着会让下一个任务莫名其妙沿用旧图
          setReferences([]);
          setTextRefs([]);
          setLibraryOpen(false);
          toast('已提交生成，正在跳转任务中心', 'success');
          navigate('/tasks');
        },
        onError: (e) => toast(e.message, 'error'),
      },
    );
  };

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
              busy={upload.isPending}
              onPick={(files) => void addReferences(files)}
              onRemove={(id) => setReferences((prev) => prev.filter((item) => item.id !== id))}
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
                'w-full resize-y rounded-lg border bg-panel-soft px-2.5 py-2 text-body leading-relaxed outline-none transition placeholder:text-[#a9b8bf] focus:border-primary-line focus:bg-white focus:shadow-ring',
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
                onChange={(e) => {
                  const nextId = e.target.value;
                  if (nextId === currentModel?.id) return;
                  /* 有参考图就问一声再丢：静默清空等于替用户做决定。
                     参数（overrides）也一起重置 —— 新模型未必认这些键，
                     留在 state 里会被后端拒。那个不需要问：参数本来就是
                     「按模型 schema 走」，换模型重置是预期行为，不是丢失。 */
                  if (references.length > 0) {
                    setPendingModelId(nextId);
                    return;
                  }
                  applyModel(nextId);
                }}
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
              onChange={(key, value) => setOverrides((prev) => ({ ...prev, [key]: value }))}
            />
          )}

          <Button
            variant="primary"
            size="lg"
            block
            className="mt-1"
            icon={<Wand2 size={15} />}
            loading={create.isPending}
            onClick={submit}
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
                className="flex items-center gap-2.5 rounded-[9px] border border-line-soft bg-[#fcfefe] p-[7px]"
              >
                <span className="grid h-5 w-5 shrink-0 place-items-center rounded-md bg-primary-soft font-mono text-micro font-bold tabular-nums text-[#2b6bd4]">
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
                  className="flex items-center gap-2.5 rounded-[9px] border border-line-soft bg-[#fcfefe] p-[7px] text-left transition-colors hover:border-line-strong"
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

      {/* 换模型要丢参考图，先问一声。
          静默清空是替用户做决定 —— 挑了半天图，换个模型，图没了，
          而界面只换了下拉框的值，没说发生了什么。 */}
      <ConfirmDialog
        open={pendingModelId !== null}
        title="更换模型"
        description="参考图与已调参数属于当前模型，换模型后会一并清空。"
        details={[
          { label: '参考图', value: `${references.length} 张` },
          {
            label: '新模型',
            value: models.find((item) => item.id === pendingModelId)?.label ?? pendingModelId ?? '',
          },
        ]}
        notice="素材本身还在素材库里，重新添加即可。"
        confirmLabel="清空并更换"
        onCancel={() => setPendingModelId(null)}
        onConfirm={() => {
          if (pendingModelId) applyModel(pendingModelId);
          setPendingModelId(null);
        }}
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
