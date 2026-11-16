import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ArrowRight, Play, Plus } from 'lucide-react';
import { KIND_LABEL } from '@/entities/library/model/types';
import { AssetChip } from '@/entities/library/ui/AssetChip';
import { CaseCover } from '@/entities/library/ui/CaseCover';
import { PackChip } from '@/entities/library/ui/PackChip';
import { SlotMediaPicker } from './SlotMediaPicker';
import { useGenerateImage } from '@/entities/image/api/image.api';
import { useModels } from '@/entities/model/api/model.queries';
import {
  isUserSelectable,
  paramControlsFor,
  resolveParamValues,
  type ParamValue,
} from '@/entities/model/lib/paramForm';
import { useCreateTask } from '@/entities/task/api/task.queries';
import { usePublishedSpecs, useSpecCompile } from '@/entities/template-spec/api/spec.queries';
import type { SpecSlotOut, TemplateSpecOut } from '@/shared/api/schema';
import { AssetPicker, type PickedAsset } from '@/features/template-specs/AssetPicker';
import { ParamFields } from '@/entities/model/ui/ParamFields';
import { Button, Chip, EmptyState, SearchInput, Skeleton } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { useStickyFitHeight } from '@/shared/hooks/useStickyFitHeight';
import { toSubmitPayload, type SubmitKind } from './taskPayload';

/**
 * 模板库（填表）：左边选模板，右边就地填素材 → 生成。
 *
 * 与「模板规格」的分工：那边**定表**（槽位、提示词、模型），这边**填表**（挑素材、
 * 可选地改写可编辑段），提交后走 `compile` → `POST /tasks`。所以本页不出现任何
 * 模板编辑控件 —— 要改模板回规格页；这里只有「填」和「生成」两种动作。
 *
 * 页面不做规则校验：数量上下限、素材可读性、编号展开都在服务端。
 * 这里只把「还缺哪几个槽位」算出来禁用按钮 —— 那是提交前置条件，不是业务规则。
 */

/* 类型名与卡片上的类型标签同源：都是资产库的 kind。别再写一份本地表 ——
   漏掉 `pack` 时页面上会直接印出英文 `pack`（真机截到过）。 */
const TYPE_LABEL: Record<string, string> = KIND_LABEL;

function slotsOf(spec: TemplateSpecOut | undefined): SpecSlotOut[] {
  return spec?.latest?.slots ?? [];
}

/** 封装槽位的条数由成员数决定（1/1 是占位），所以上下限一律按 1 算。 */
function minOf(slot: SpecSlotOut): number {
  return slot.type === 'pack' ? 1 : (slot.minCount ?? 1);
}

function maxOf(slot: SpecSlotOut): number {
  return slot.type === 'pack' ? 1 : (slot.maxCount ?? 1);
}

/**
 * 展示案例：作者上传/选的样例图或视频。
 *
 * 没设就明写「暂无案例」，不摆渐变块冒充缩略图 —— 那会让人以为模板带图。
 * 它是**纯展示**字段（存在草稿里、不进发布快照），所以这里只读、不可交互。
 */
/**
 * 模板卡片：案例封面 + 压在图上的名称 + 「要什么素材」的标签 + 一行模型。
 *
 * 名称压在封面底部而不是另起一行：卡片是「挑图」，图应该占满视觉重心，文字只做
 * 标注。版本号反而放右上角 —— 它是作者关心的东西，不该占掉选模板时的注意力。
 */
function TemplateCard({
  item,
  selected,
  onSelect,
}: {
  item: TemplateSpecOut;
  selected: boolean;
  onSelect: () => void;
}) {
  const itemSlots = slotsOf(item);
  const fixed = itemSlots.filter(slot => slot.binding === 'fixed').length;
  const pending = itemSlots.length - fixed;
  // 要什么类型的素材：卡片上一眼能看出来，省得点进去才发现是视频模板。
  const kinds = [...new Set(itemSlots.map(slot => slot.type))];
  /* 悬停态挂在**整张卡**上：封面只占卡片上半截，鼠标落在名称/chips 那一带时
     视频区收不到 mouseenter，看起来就是「这张卡不会动」。 */
  const [hovering, setHovering] = useState(false);
  return (
    <button
      type="button"
      onClick={onSelect}
      onMouseEnter={() => setHovering(true)}
      onMouseLeave={() => setHovering(false)}
      className={cn(
        // 三层深度的中间那层：卡片静止时也有一线投影（告诉眼睛"这是浮在页面上的"），
        // 悬停抬起半像素并加深，选中的用蓝色投影而不是只描一圈边 —— 只描边在
        // 一排白卡里几乎看不出来（尤其是缩略图把边压满的时候）。
        'group flex flex-col rounded-2xl border bg-panel p-1.5 text-left transition-all duration-200',
        selected
          ? 'border-primary-line shadow-[0_10px_24px_-12px_rgba(43,124,255,.55)] ring-1 ring-primary/20'
          : 'border-line-soft shadow-[0_1px_2px_rgba(16,42,55,.05)] hover:-translate-y-0.5 hover:border-line hover:shadow-card',
      )}
    >
      <div className="relative overflow-hidden rounded-xl">
        <CaseCover
          assetId={item.demoAssetId}
          seed={item.key}
          className="aspect-[4/5] w-full"
          hoverPlay
          active={hovering}
        />
        <span className="pointer-events-none absolute right-1.5 top-1.5 rounded-full bg-black/40 px-1.5 py-0.5 text-micro font-medium leading-[14px] text-white/95 backdrop-blur-sm">
          v{item.currentVersion}
        </span>
        <div className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/70 via-black/25 to-transparent px-2.5 pb-1.5 pt-9">
          <div className="truncate text-xs font-medium text-white">{item.name}</div>
        </div>
      </div>

      <div className="flex flex-col gap-1.5 px-1 pb-0.5 pt-2">
        <div className="flex flex-wrap gap-1">
          {itemSlots.length === 0 ? (
            <Chip tone="ok">纯提示词</Chip>
          ) : pending > 0 ? (
            <Chip tone="warn">待填 {pending}</Chip>
          ) : (
            <Chip tone="ok">素材已固定</Chip>
          )}
          {kinds.map(kind => (
            <Chip key={kind} tone="primary">
              {TYPE_LABEL[kind] ?? kind}
            </Chip>
          ))}
        </div>
        {/* `mt-auto`：一行里卡片的 chip 数量不同时会换行成一两行，不推到底的话
            底栏会各自停在不同高度，一排看过去参差。 */}
        <div className="mt-auto flex items-center justify-between gap-2 border-t border-line-soft pt-1.5">
          <span className="min-w-0 truncate text-micro text-ink-mute">
            {item.latest?.model ?? '—'}
          </span>
          <span
            className={cn(
              'flex shrink-0 items-center gap-0.5 rounded-full px-1.5 py-[1px] text-micro transition-colors',
              selected
                ? 'bg-primary text-white'
                : 'bg-panel-soft text-ink-soft ring-1 ring-inset ring-line-soft group-hover:bg-primary-soft group-hover:text-primary group-hover:ring-primary-line',
            )}
          >
            填写 <ArrowRight size={10} />
          </span>
        </div>
      </div>
    </button>
  );
}

/** 右栏分区：标题一行 + 内容；分区之间用细线，不再套一层边框盒。 */
function PanelSection({
  title,
  extra,
  children,
}: {
  title: string;
  extra?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-2.5 border-t border-line-soft pt-3.5 first:border-t-0 first:pt-0">
      <div className="flex items-center justify-between gap-2">
        {/* 3px 色标与封装页的分区标题同源：整页只有「标题栏 / 分区」这两级用彩色，
            其余一律灰阶 —— 层次靠这个对比撑起来，不靠更多颜色。 */}
        <h3 className="flex items-center gap-1.5 text-lead font-semibold tracking-[.2px] text-ink">
          <span className="h-2.5 w-[3px] rounded-full bg-primary" />
          {title}
        </h3>
        {extra}
      </div>
      {children}
    </section>
  );
}

export function SpecLibrary() {
  const published = usePublishedSpecs();
  const compile = useSpecCompile();
  const createTask = useCreateTask();
  const navigate = useNavigate();
  const generateImage = useGenerateImage();
  const videoModels = useModels('video');
  const imageModels = useModels('image');

  const [keyword, setKeyword] = useState('');
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, string[]>>({});
  /* 使用者对「运行时参数」的选择（计费模式等）。跟素材选择分开存：
     素材是这次填的，计费模式是这次选的 —— 换模板时两者都要重来。 */
  const [userChoices, setUserChoices] = useState<Record<string, ParamValue>>({});
  const [picker, setPicker] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const [error, setError] = useState('');

  /* 右栏的高度上限：不写死「视口减几」，量它自己站在哪儿。
     页面刚打开时它还没吸住（上面顶着「模板库」标题），自然位置比吸顶位置低，
     只按吸顶位置算上限就会把底边顶出视口 —— 用户看到的正是「滚动到顶部右栏被遮一点」。
     右栏是数据回来才挂的，所以上限交给回调 ref：面板一挂上就量一次。 */
  const {ref: asideRef, maxHeight: asideMaxHeight} = useStickyFitHeight();

  const items = useMemo(() => {
    const all = published.data?.items ?? [];
    const word = keyword.trim().toLowerCase();
    return word ? all.filter(item => item.name.toLowerCase().includes(word)) : all;
  }, [published.data, keyword]);

  const spec = items.find(item => item.key === selectedKey) ?? items[0];
  const slots = slotsOf(spec);
  /* 只列「使用时填」的槽位：固定槽位是作者在封装页选死的，填表的人既不能换也用不着
     知道它长什么样 —— 一排只读的 chip 只会让人以为这里还能点。 */
  const fillSlots = slots.filter(slot => slot.binding === 'user');
  const version = spec?.latest;
  const focused = spec?.key ?? null;

  /* 换模板就清空上一次的填写：不做"记忆上次填的素材"，
     那会让用户以为新模板也用了同一张图。计费模式这类选择同理 —— 上一张
     模板的计费选择不该跟着带过来（那是上一笔生意的决定）。 */
  useEffect(() => {
    setValues({});
    setUserChoices({});
    setNote('');
    setError('');
  }, [focused, version?.version]);

  /* 模板只声明模型，「提交到哪个接口」由模型能力决定：视频进 /tasks、图片进
     /generations/image。判不出来（模型清单里没有）就按视频处理 —— 那是这条链路
     原本唯一支持的方向，报错信息也比「静默当图片」清楚。 */
  const imageModel = imageModels.find(item => item.id === version?.model);
  const videoModel = videoModels.find(item => item.id === version?.model);
  const model = imageModel ?? videoModel;
  const kind: SubmitKind = imageModel ? 'image' : 'video';
  const defaults =
    kind === 'image'
      ? {
          size: String(imageModel?.paramSchema?.size?.default ?? '') || undefined,
          count: Number(imageModel?.paramSchema?.count?.default ?? 0) || undefined,
        }
      : {
          durationSec: Number(videoModel?.paramSchema?.durationSec?.default ?? 0) || undefined,
          aspectRatio: (videoModel?.paramSchema?.aspectRatio?.default as string | undefined) ?? undefined,
        };

  const missing = slots.filter(slot => {
    if (slot.binding !== 'user') return false;
    return (values[slot.key] ?? []).length < minOf(slot);
  });

  /* 参数分两类：
     - 作者设的（时长、画幅…）：只读展示，标签与档位文案全部来自模型的
       `paramSchema`（`paramControlsFor`），前端不写死任何一种、也不印英文 key；
     - 使用者选的（`userSelectable`，如计费模式）：**这里能改** —— 写死等于
       模板作者替每个使用者决定了要不要花积分，而积分是用的人的钱。
       改完随 `paramOverrides` 传给 compile，不落进模板版本。 */
  const paramControls = paramControlsFor(model);
  const authorControls = paramControls.filter(
    control => !isUserSelectable(model?.paramSchema?.[control.key]),
  );
  const userControls = paramControls.filter(control =>
    isUserSelectable(model?.paramSchema?.[control.key]),
  );
  const overrides: Record<string, ParamValue> = {};
  for (const [key, value] of Object.entries(version?.params ?? {})) {
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
      overrides[key] = value;
    }
  }
  const paramValues = resolveParamValues(paramControls, overrides);
  /* 使用者选的值：以自己选过的为准，没选过的取 schema 默认值。 */
  const userValues = resolveParamValues(userControls, {
    ...Object.fromEntries(
      Object.entries(paramValues).filter(([key]) =>
        userControls.some(control => control.key === key),
      ),
    ),
    ...userChoices,
  });
  const paramChips = authorControls.map(control => {
    const raw = paramValues[control.key];
    const option = control.options.find(item => item.value === String(raw));
    const text =
      option?.label ??
      (raw === '' || raw === undefined ? '默认' : String(raw).replace(/(\d)x(\d)/, '$1×$2'));
    return {key: control.key, label: control.label, text};
  });

  /* 填了几项：只看要用户填的那几个槽位。进度条用它，省得只在按钮上写一句
     「还缺 2 项」——那句话得滚到底才看得见。 */
  const filledCount = fillSlots.length - missing.length;
  const progress = fillSlots.length > 0 ? Math.round((filledCount / fillSlots.length) * 100) : 0;

  const busy = compile.isPending || createTask.isPending || generateImage.isPending;

  const pickerSlot = slots.find(slot => slot.key === picker);

  /* 选择器的语义是 `onToggle`（点已选的等于取消），多选槽位不关弹层：
     一次挑三张比反复开关三次省事；单选槽位选一张就替换掉原来的。 */
  const togglePick = (asset: PickedAsset) => {
    if (!pickerSlot) return;
    const key = pickerSlot.key;
    const limit = maxOf(pickerSlot);
    setValues(current => {
      const existing = current[key] ?? [];
      if (existing.includes(asset.id)) {
        return {...current, [key]: existing.filter(id => id !== asset.id)};
      }
      const next = limit > 1 ? [...existing, asset.id] : [asset.id];
      return {...current, [key]: next.slice(0, limit)};
    });
    /* 单选（含封装）选完就关：留着一个已经选好的弹层，用户只能再去点右上角，
       而这时提示词里的缩略图早就该出来了。多选槽位仍然不关，一次挑三张。 */
    if (limit <= 1) setPicker(null);
  };

  const submit = async () => {
    if (!spec) return;
    setError('');
    setNote('');
    try {
      const compiled = await compile.mutateAsync({
        key: spec.key,
        payload: {
          slotValues: values,
          // 使用者选的运行时参数（计费模式等）：随这次编译走，不落进模板版本。
          ...(Object.keys(userChoices).length ? {paramOverrides: userChoices} : {}),
        },
      });
      const plan = toSubmitPayload(compiled, spec.name, kind, defaults, model);
      if (!plan.ok) {
        setError(plan.message);
        return;
      }
      if (plan.kind === 'image') {
        const job = await generateImage.mutateAsync(plan.payload);
        // 生图的 `name` 是从提示词派生的，会显示成 `{{Text 1}}`；用模板名更好认
        setNote(`已提交生图：${spec.name}（${job.id}）`);
      } else {
        const task = await createTask.mutateAsync(plan.payload);
        setNote(`已提交：${task.title || task.id}`);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  if (published.isLoading) {
    return (
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {Array.from({length: 6}).map((_, index) => (
          <Skeleton key={index} className="h-[360px] rounded-2xl" />
        ))}
      </div>
    );
  }

  if (items.length === 0) {
    return (
      /* 空状态是一次邀请，不是段说明：按钮里直接写「去哪儿做」，
         就不用在下面再补一句「先在模板封装里定一张表并发布」。 */
      <EmptyState
        title="还没有已发布的模板"
        action={
          <Button size="sm" variant="primary" onClick={() => navigate('/templates/specs')}>
            去模板封装新建
          </Button>
        }
      />
    );
  }

  return (
    <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_400px]">
      <div className="flex flex-col gap-3">
        {/* 工具栏吸顶：模板多到要滚的时候，筛选框不该跟着滚走。
            偏移 54px 是顶栏高度（那条 `sticky top-0` 的 header 在最上层，不躲开会被盖住）；
            底部用渐变淡出而不是一条硬线 —— 内容从工具栏底下"化"进去，
            看起来是浮在上层，而不是被一刀切开。 */}
        <div className="sticky top-[59px] z-10 -mx-1 flex items-center gap-2.5 bg-[linear-gradient(180deg,#fff_0%,#fff_72%,rgba(255,255,255,0))] px-1 pb-3 pt-2">
          <SearchInput
            value={keyword}
            onChange={event => setKeyword(event.target.value)}
            placeholder="按名称筛选"
            wrapperClassName="w-56"
          />
          <span className="rounded-full bg-panel-soft px-2 py-[2px] text-micro text-ink-soft ring-1 ring-inset ring-line-soft">
            {items.length} 个模板
          </span>
        </div>
        {/* 网格放在一层很淡的"托盘"上：卡片本身是白的，如果页面也是白的，
            就只能靠 1px 描边区分，看一眼全是线。给托盘一点底色，卡片立刻浮起来
            —— 比给每张卡加更重的阴影更省、也更不会脏。 */}
        <div className="rounded-2xl bg-canvas/40 p-2.5 shadow-[inset_0_1px_0_rgba(255,255,255,.9)] ring-1 ring-inset ring-[rgba(211,224,229,.45)]">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {items.map(item => (
              <TemplateCard
              key={item.key}
              item={item}
                selected={item.key === spec?.key}
                onSelect={() => setSelectedKey(item.key)}
              />
            ))}
          </div>
        </div>
      </div>

      {/* 右栏固定（`xl` 以上吸顶），中间那一段自己滚：槽位多的时候整块会比视口还高，
          不自己滚的话「生成」按钮就永久停在屏幕下面，填到最后还得滚页面去找它。
          头（模板名/版本）和脚（提交按钮）留在滚动区外，滚的只有中间的素材区。

          高度上限交给 `useStickyFitHeight` 量：以前写死 `100vh - 91px`，
          那个数只对「吸住之后」成立 —— 页面刚打开时它还顶着「模板库」标题，
          自然位置比吸顶位置低 39px，底边就被视口切掉 11px。 */}
      <aside
        ref={asideRef}
        style={{maxHeight: asideMaxHeight}}
        className="relative flex flex-col overflow-hidden rounded-2xl border border-line bg-panel shadow-float xl:sticky xl:top-[71px]"
      >
        {/* 一条从顶到底的渐变细线：右栏是「干活的地方」，左侧网格是「挑东西的地方」，
            给干活的那一侧一条自己的脊线，视线落在整页右侧时不会跟卡片混在一起。 */}
        <span
          aria-hidden
          className="pointer-events-none absolute inset-y-5 left-[1px] w-[2px] rounded-full bg-gradient-to-b from-primary/45 via-brand/25 to-transparent"
        />
        {spec && version ? (
          <>
            <header className="shrink-0 border-b border-line-soft bg-gradient-to-b from-panel-soft via-panel-soft/60 to-panel px-4 pb-3.5 pt-4">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate text-lead font-semibold leading-tight tracking-[.1px] text-ink">
                    {spec.name}
                  </div>
                  <div className="mt-1.5 flex min-w-0 items-center gap-1.5 text-meta text-ink-mute">
                    <span className="shrink-0 rounded-full bg-primary-soft px-1.5 py-[1px] text-micro font-medium text-primary-deep ring-1 ring-inset ring-primary-line/60">
                      v{version.version}
                    </span>
                    <span className="truncate text-ink-soft">{version.model}</span>
                  </div>
                </div>
                <Link
                  to="/templates/specs"
                  className="shrink-0 rounded-md border border-line px-1.5 py-0.5 text-meta text-ink-soft transition-colors hover:border-primary-line hover:text-primary"
                >
                  改模板
                </Link>
              </div>
            </header>

            {/* 滚动条用细的（`scroll-thin`，见 index.css）：这栏只有 400px 宽，
                8px 的全局滚动条会把槽位块挤窄。"stable" 让有没有滚动条两种情况等宽，
                切模板时内容不会左右跳。 */}
            <div className="scroll-thin flex min-h-0 flex-1 flex-col gap-3.5 overflow-y-auto overscroll-contain px-4 py-3.5 [scrollbar-gutter:stable]">
              {fillSlots.length > 0 ? (
                <PanelSection
                  title="填入素材"
                  extra={
                    /* 进度放在它衡量的东西旁边：放在标题栏里会变成一条压在名字下的
                       粗线，看着像分隔符，而不是"填了几项"。 */
                    <span className="flex items-center gap-1.5">
                      <span className="h-1 w-16 overflow-hidden rounded-full bg-line-soft">
                        <span
                          className={cn(
                            'block h-full rounded-full transition-all duration-300 ease-smooth',
                            filledCount === fillSlots.length ? 'bg-ok' : 'bg-primary',
                          )}
                          style={{width: `${progress}%`}}
                        />
                      </span>
                      <span className="text-micro tabular-nums text-ink-mute">
                        {filledCount}/{fillSlots.length}
                      </span>
                    </span>
                  }
                >
                  <div className="flex flex-col gap-2">
                    {fillSlots.map((slot, index) => {
                      const filled = values[slot.key] ?? [];
                      /* 图片/视频：按「一级标签 · 槽位名」分组、点二级标签看素材。
                         其余（文本/音频/封装）保留行式写法。 */
                      if (slot.type === 'image' || slot.type === 'video') {
                        return (
                          <SlotMediaPicker
                            key={slot.key}
                            slot={slot}
                            selected={filled}
                            onChange={ids =>
                              setValues(current => ({...current, [slot.key]: ids}))
                            }
                          />
                        );
                      }
                      return (
                        <div key={slot.key} className="flex gap-2">
                          <div className="flex w-24 shrink-0 flex-col gap-0.5">
                            <span className="flex items-baseline gap-1 text-xs leading-tight text-ink">
                              <span className="shrink-0 text-ink-mute">{index + 1}.</span>
                              <span className="line-clamp-2">{slot.label || slot.key}</span>
                              {minOf(slot) > 0 && <span className="text-warn-deep">*</span>}
                            </span>
                            <span className="text-micro text-ink-mute">
                              {TYPE_LABEL[slot.type] ?? slot.type}
                              {maxOf(slot) > 1 ? ` · 最多 ${maxOf(slot)}` : ''}
                            </span>
                          </div>

                          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
                            {slot.type === 'pack' && filled[0] && (
                              <PackChip
                                packId={filled[0]}
                                onRemove={() => setValues(current => ({...current, [slot.key]: []}))}
                              />
                            )}
                            {slot.type !== 'pack' &&
                              filled.map(assetId => (
                                <AssetChip
                                  key={assetId}
                                  assetId={assetId}
                                  onRemove={() =>
                                    setValues(current => ({
                                      ...current,
                                      [slot.key]: (current[slot.key] ?? []).filter(
                                        id => id !== assetId,
                                      ),
                                    }))
                                  }
                                />
                              ))}
                            <button
                              type="button"
                              onClick={() => setPicker(slot.key)}
                              className="flex items-center gap-1 rounded-lg border border-line-soft bg-panel-soft px-2 py-1 text-meta text-ink-soft transition-colors hover:border-primary-line hover:bg-primary-soft hover:text-primary"
                            >
                              <Plus size={11} />
                              {filled.length > 0
                                ? '再选'
                                : `选择${slot.type === 'pack' ? '封装' : (TYPE_LABEL[slot.type] ?? '素材')}`}
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                  {/* 用途说明只在该槽位还没填时出现：填完它就是噪音。 */}
                  {fillSlots.some(slot => slot.usage && (values[slot.key] ?? []).length === 0) && (
                    <ul className="mt-1 flex flex-col gap-0.5 border-t border-line-soft pt-2 text-micro leading-relaxed text-ink-mute">
                      {fillSlots
                        .filter(slot => slot.usage && (values[slot.key] ?? []).length === 0)
                        .map(slot => (
                          <li key={slot.key} className="truncate">
                            {slot.label || slot.key}：{slot.usage}
                          </li>
                        ))}
                    </ul>
                  )}
                </PanelSection>
              ) : (
                /* 没有要填的槽位（纯提示词，或者素材全被作者固定）：这一栏不必空着占位，
                    说一句能直接生成就够。两种情况的说法分开，别让人以为漏了槽位。 */
                <p className="text-meta text-ink-mute">
                  {slots.length === 0
                    ? '这个模板是纯提示词，直接生成即可。'
                    : '素材都由作者固定了，直接生成即可。'}
                </p>
              )}

              {paramChips.length > 0 && (
                <PanelSection title="参数">
                  <div className="flex flex-wrap gap-1.5">
                    {paramChips.map(item => (
                      <Chip key={item.key}>
                        <span className="text-ink-mute">{item.label}</span>
                        <span className="ml-1 text-ink-soft">{item.text}</span>
                      </Chip>
                    ))}
                  </div>
                </PanelSection>
              )}

              {/* 使用者能选的参数（计费模式）。**不写死在模板里** ——
                  积分是用的人的钱，作者替别人决定要不要花积分不对。 */}
              {userControls.length > 0 && (
                <PanelSection title="生成选项">
                  <div className="flex flex-col gap-2.5">
                    {userControls.map(control => (
                      <ParamFields
                        key={control.key}
                        className="grid-cols-1"
                        idPrefix={`user-param-${control.key}`}
                        controls={[control]}
                        values={userValues}
                        onChange={(key, value) =>
                          setUserChoices(current => ({...current, [key]: value}))
                        }
                      />
                    ))}
                  </div>
                </PanelSection>
              )}
            </div>

            <div className="flex shrink-0 flex-col gap-2 border-t border-line-soft bg-gradient-to-b from-panel to-panel-soft px-4 py-3">
              {error && <p className="text-meta text-danger">{error}</p>}
              {note && (
                <p className="flex items-center gap-2 text-meta text-ink-soft">
                  <span className="truncate">{note}</span>
                  <Link to="/tasks" className="shrink-0 text-primary hover:underline">
                    去任务中心
                  </Link>
                </p>
              )}
              {/* 只有"真的能点"的时候才是实心：缺素材时它退成灰按钮 —— 半透明的
                  蓝底白字既不像能点、也读不清，是最差的一种禁用态。 */}
              <Button
                block
                size="lg"
                variant={missing.length > 0 ? 'secondary' : 'primary'}
                disabled={busy || missing.length > 0}
                onClick={() => void submit()}
              >
                <Play size={13} />
                {busy
                  ? '提交中…'
                  : missing.length > 0
                    ? `还缺 ${missing.length} 项素材`
                    : kind === 'image'
                      ? '生成图片'
                      : '生成视频'}
              </Button>
            </div>
          </>
        ) : (
          <p className="px-4 py-8 text-center text-xs text-ink-mute">左侧选一个模板。</p>
        )}
      </aside>

      <AssetPicker
        open={picker !== null}
        kind={pickerSlot?.type ?? 'image'}
        /* 槽位的一级标签决定候选集：这里与封装页的固定选择器看的是同一批素材。 */
        tagIds={pickerSlot?.tagId ? [pickerSlot.tagId] : []}
        selected={pickerSlot ? (values[pickerSlot.key] ?? []) : []}
        onClose={() => setPicker(null)}
        onToggle={togglePick}
      />
    </div>
  );
}
