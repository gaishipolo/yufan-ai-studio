import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { Wand2 } from 'lucide-react';
import {
  Button,
  Chip,
  CoverImage,
  PageHeader,
  ReferenceImages,
  Select,
  type ReferenceItem,
} from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { toast } from '@/shared/lib/toast';
import { useCreateTask, useTaskList } from '@/entities/task/api/task.queries';
import { aspectOptionsFor, durationOptionsFor, useModels } from '@/entities/model/api/model.queries';
import { useShots } from '@/entities/shot/api/shot.api';
import { LibraryFlyout } from '@/entities/library/ui/LibraryFlyout';
import { useUploadAsset } from '@/entities/library/api/library.queries';
import { useCan } from '@/shared/access/use-access';
import type { LibraryAssetDrag } from '@/shared/lib/libraryDrag';
import type { AspectRatio } from '@/entities/task/model/types';

const STYLE_PRESETS = ['电影感', '纪实手持', '广告质感', '胶片复古'];

/**
 * 工作台自己的参考图档位。
 *
 * 生图页那边「模型的 `maxReferences` 是 0 就不画上传框」，因为供应商确实不收；
 * 视频侧目前所有模型都还没声明这个字段（后端 `task/pipeline.py` 尚未把
 * `reference_asset_ids` 转成供应商要的 `reference_urls`），照搬那条规则会让
 * 整块参考图区直接消失。所以这里取「模型声明了正数才采信，否则用工作台档位」，
 * 等后端打通后再改成以模型声明为准。
 */
const DEFAULT_REFERENCE_SLOTS = 4;

export default function StudioPage() {
  const navigate = useNavigate();
  const create = useCreateTask();
  const { data: shots } = useShots();
  const { data: recent } = useTaskList({ page: 1, pageSize: 3, scope: 'mine' });

  // 模型、时长、比例全部来自后端 /models：新增模型不需要改这里
  const models = useModels('video');
  const [prompt, setPrompt] = useState('');
  const [modelId, setModelId] = useState('');
  const [duration, setDuration] = useState('');
  const [ratio, setRatio] = useState<AspectRatio>('9:16');
  const [style, setStyle] = useState(STYLE_PRESETS[0]);
  const [error, setError] = useState('');
  /* 参考图：先落素材库拿 id，再随请求下发 id —— 与生图页同一条通道，
     提交的是 id 而不是图片字节，同一张素材可以被多个任务反复引用 */
  const [references, setReferences] = useState<ReferenceItem[]>([]);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const canReadLibrary = useCan('library:read');
  const upload = useUploadAsset();

  const currentModel = useMemo(
    () => models.find((item) => item.id === modelId) ?? models.find((m) => m.isDefault) ?? models[0],
    [models, modelId],
  );
  const modelOptions = useMemo(
    () => models.map((item) => ({ value: item.id, label: item.label })),
    [models],
  );
  const durationValues = useMemo(() => durationOptionsFor(currentModel), [currentModel]);
  const durationOptions = useMemo(
    () => durationValues.map((value) => ({ value: String(value), label: `${value} 秒` })),
    [durationValues],
  );
  const ratioOptions = useMemo(() => aspectOptionsFor(currentModel), [currentModel]);

  /**
   * 缺省值取模型自己声明的 `default`，不取「最长的那个档位」。
   *
   * 以前时长默认落在最后一档（12 秒），而模型声明的是 5 秒：视频按秒计费，
   * 这等于默认给用户下了一个 2.4 倍时长的单，还看不出是自己选的。
   * 声明的 default 不在档位里（供应商配置写歪了）才退回到最后一档。
   */
  const durationFallback = useMemo(() => {
    const declared = Number(currentModel?.paramSchema?.durationSec?.default);
    if (Number.isFinite(declared) && durationValues.includes(declared)) return String(declared);
    return String(durationValues[durationValues.length - 1] ?? '');
  }, [currentModel, durationValues]);
  const ratioFallback = useMemo(() => {
    const declared = currentModel?.paramSchema?.aspectRatio?.default;
    if (declared && ratioOptions.some((one) => one.value === declared)) {
      return declared as AspectRatio;
    }
    return ratioOptions[0]?.value;
  }, [currentModel, ratioOptions]);
  const maxReferences =
    currentModel?.maxReferences && currentModel.maxReferences > 0
      ? currentModel.maxReferences
      : DEFAULT_REFERENCE_SLOTS;

  // 换模型时把时长/比例收敛到该模型支持的范围内
  useEffect(() => {
    if (!durationValues.length) return;
    setDuration((prev) =>
      prev && durationValues.includes(Number(prev)) ? prev : durationFallback,
    );
  }, [durationValues, durationFallback]);

  useEffect(() => {
    if (!ratioOptions.length || !ratioFallback) return;
    setRatio((prev) =>
      ratioOptions.some((item) => item.value === prev) ? prev : ratioFallback,
    );
  }, [ratioOptions, ratioFallback]);

  const storyboard = useMemo(() => (shots?.items ?? []).slice(0, 4), [shots]);

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
      try {
        const asset = await upload.mutateAsync({ file, kind: 'image' });
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

  const submit = () => {
    if (!prompt.trim()) {
      setError('请先填写画面描述');
      return;
    }
    setError('');
    create.mutate(
      {
        prompt: `${prompt.trim()}，${style}风格`,
        // 提交目录 id：后端会归一成「id + 可读名」，避免同一个字段混装两种形态
        model: currentModel?.id ?? '',
        durationSec: Number(duration),
        aspectRatio: ratio,
        stylePreset: style,
        ...(references.length ? { referenceAssetIds: references.map((item) => item.id) } : {}),
      },
      {
        onSuccess: () => {
          // 参考图跟着这次请求走了，留着会让下一个任务莫名其妙沿用旧图
          setReferences([]);
          setLibraryOpen(false);
          toast('已提交生成，正在跳转任务中心', 'success');
          navigate('/tasks');
        },
        onError: (e) => toast(e.message, 'error'),
      },
    );
  };

  return (
    <>
      <PageHeader
        title="生成工作台"
        description="一句话 → 脚本 → 分镜 → 成片。上传参考图并描述画面，系统自动拆解镜头序列。"
        actions={
          <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-ink-mute">
            {['描述', '脚本', '分镜', '成片'].map((step, i) => (
              <span key={step} className="flex items-center gap-1.5">
                <b className="font-semibold text-ink-soft">{step}</b>
                {i < 3 && <span className="text-[#c8d5db]">→</span>}
              </span>
            ))}
          </div>
        }
      />

      <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)] 2xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1fr)]">
        {/* 左：创作输入 */}
        <section className="self-start rounded-card border border-line-soft bg-panel p-3.5 lg:row-span-2 2xl:row-span-1">
          <SectionTitle>创作输入</SectionTitle>

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
            emptyHint="也可以点右上角「素材库」把图拖进来"
          />

          <Field label="画面描述" required>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={3}
              placeholder="例：超市抢购场景，手持镜头跟拍，人群涌动，暖色顶光，电影感浅景深，15 秒"
              className={cn(
                'w-full resize-y rounded-lg border bg-panel-soft px-2.5 py-2 text-[12.5px] leading-relaxed outline-none transition placeholder:text-[#a9b8bf] focus:border-primary-line focus:bg-white focus:shadow-ring',
                error ? 'border-danger' : 'border-line',
              )}
            />
            {error && <p className="mt-1 text-[11px] text-danger">{error}</p>}
          </Field>

          <div className="grid grid-cols-2 gap-2.5">
            <Field label="生成模型">
              <Select
                block
                options={modelOptions}
                value={currentModel?.id ?? ''}
                onChange={(e) => {
                  setModelId(e.target.value);
                  // 换模型的参考图上限/支持情况可能完全不同，旧图一律清掉
                  setReferences([]);
                  setLibraryOpen(false);
                }}
              />
            </Field>
            <Field label="时长">
              <Select block options={durationOptions} value={duration} onChange={(e) => setDuration(e.target.value)} />
            </Field>
          </div>

          {/* 模型能做什么写在这里：同一次提交会带上时长上限与画幅白名单，用户不用去试错 */}
          {currentModel?.description && (
            <p className="-mt-1 mb-2.5 text-[10.5px] leading-snug text-ink-mute">
              {currentModel.description}
            </p>
          )}

          <Field label="画面比例">
            <div className="flex flex-wrap gap-1.5">
              {ratioOptions.map((r) => (
                <ChipButton key={r.value} active={ratio === r.value} onClick={() => setRatio(r.value)}>
                  {r.label}
                </ChipButton>
              ))}
            </div>
          </Field>

          <Field label="风格预设">
            <div className="flex flex-wrap gap-1.5">
              {STYLE_PRESETS.map((s) => (
                <ChipButton key={s} active={style === s} onClick={() => setStyle(s)}>
                  {s}
                </ChipButton>
              ))}
            </div>
          </Field>

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
        </section>

        {/* 右：分镜 + 最近生成（窄屏叠成一列，2xl 起并排铺满） */}
        <section className="self-start rounded-card border border-line-soft bg-panel p-3.5">
          <SectionTitle extra={`自动拆解 · ${storyboard.length} 个镜头`}>分镜序列</SectionTitle>
          <div className="flex flex-col gap-2">
            {storyboard.map((shot, i) => (
              <div
                key={shot.id}
                className="flex items-center gap-2.5 rounded-[9px] border border-line-soft bg-[#fcfefe] p-[7px]"
              >
                <span className="grid h-5 w-5 shrink-0 place-items-center rounded-md bg-primary-soft text-[10.5px] font-bold text-[#2b6bd4]">
                  {i + 1}
                </span>
                <CoverImage src={shot.coverUrl} seed={shot.id} className="h-[34px] w-11 shrink-0 rounded-md" />
                <div className="min-w-0 flex-1">
                  <b className="block truncate text-[11.5px] font-semibold text-ink">{shot.name}</b>
                  <span className="text-[10.5px] text-ink-mute">{shot.durationSec}s · 运镜已编排</span>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="self-start rounded-card border border-line-soft bg-panel p-3.5">
          <SectionTitle>最近生成</SectionTitle>
          <div className="flex flex-col gap-2">
            {(recent?.items ?? []).map((task) => (
              <button
                key={task.id}
                type="button"
                onClick={() => navigate('/tasks')}
                className="flex items-center gap-2.5 rounded-[9px] border border-line-soft bg-[#fcfefe] p-[7px] text-left transition-colors hover:border-line-strong"
              >
                <CoverImage src={task.coverUrl} seed={task.id} className="h-[34px] w-11 shrink-0 rounded-md" />
                <div className="min-w-0 flex-1">
                  <b className="block truncate text-[11.5px] font-semibold text-ink">{task.title}</b>
                  <span className="text-[10.5px] text-ink-mute">
                    {task.model} · {task.aspectRatio}
                  </span>
                </div>
                <Chip tone={task.status === 'completed' ? 'ok' : task.status === 'failed' ? 'danger' : 'primary'}>
                  {task.status === 'completed' ? '已完成' : task.status === 'failed' ? '失败' : '生成中'}
                </Chip>
              </button>
            ))}
          </div>
        </section>
      </div>

      {/* 悬浮在页面之上、不加遮罩：遮罩会挡住左侧参考图区，拖拽就落不下去了 */}
      {libraryOpen && canReadLibrary && (
        <LibraryFlyout
          onClose={() => setLibraryOpen(false)}
          max={maxReferences}
          pickedIds={references.map((item) => item.id)}
          onPick={addLibraryAsset}
        />
      )}
    </>
  );
}

function SectionTitle({ children, extra }: { children: ReactNode; extra?: string }) {
  return (
    <div className="mb-2.5 flex items-center gap-1.5 text-[12.5px] font-semibold text-ink">
      <span className="h-1.5 w-1.5 rounded-full bg-brand" />
      {children}
      {extra && <span className="ml-auto text-[11px] font-normal text-ink-mute">{extra}</span>}
    </div>
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
      <div className="mb-1.5 flex items-center gap-1 text-[11.5px] text-ink-soft">
        {label}
        {required && <span className="text-danger">*</span>}
        {hint && <span className="ml-auto text-[11px] text-ink-mute">{hint}</span>}
      </div>
      {children}
    </div>
  );
}

function ChipButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'rounded-pill border px-2.5 py-1 text-[11.5px] transition-colors',
        active
          ? 'border-[#bcd6ff] bg-primary-soft font-semibold text-[#2b6bd4]'
          : 'border-line bg-panel text-ink-soft hover:border-[#b9d3ff] hover:text-primary',
      )}
    >
      {children}
    </button>
  );
}
