import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ArrowRight, Play, Plus } from 'lucide-react';
import { KIND_LABEL } from '@/entities/library/model/types';
import { useLibraryPack } from '@/entities/library/api/library.queries';
import { AssetChip } from '@/entities/library/ui/AssetChip';
import { CaseCover } from '@/entities/library/ui/CaseCover';
import { PackGroupPicker } from './PackGroupPicker';
import { SlotMediaPicker } from './SlotMediaPicker';
import { useGenerateImage } from '@/entities/image/api/image.api';
import { useModels } from '@/entities/model/api/model.queries';
import { findModelSpec } from '@/entities/model/model/types';
import {
  isUserSelectable,
  paramControlsFor,
  paramDisplayText,
  resolveParamValues,
  type ParamValue,
} from '@/entities/model/lib/paramForm';
import { useCreateTask } from '@/entities/task/api/task.queries';
import {
  usePublishedSpecs,
  useSpecCompile,
  useTemplateTags,
} from '@/entities/template-spec/api/spec.queries';
import type { SpecSlotOut, TemplateSpecOut, SpecSlotIn,
} from '@/shared/api/schema';
import { AssetPicker, type PickedAsset } from '@/features/template-specs/AssetPicker';
import {
  effectiveModelIds,
  hasSingleSlotRef,
  slotTypeOf,
  referenceChipsOf,
} from '@/features/template-specs/specDraft';
import { ParamFields } from '@/entities/model/ui/ParamFields';
import { groupLabels } from '@/entities/template-spec/model/slotGroups';
import {
  Button,
  Chip,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  SearchInput,
  Segmented,
  Select,
  Skeleton,
} from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { toast } from '@/shared/lib/toast';
import { useStickyFitHeight } from '@/shared/hooks/useStickyFitHeight';
import { toSubmitPayload } from './taskPayload';
import { modelChoiceOptions, resolveModelChoice } from './modelChoice';
import { buildGenerateConfirm } from './generateConfirm';
import {
  DEFAULT_RANDOMNESS,
  RANDOMNESS_DESCRIPTION,
  RANDOMNESS_LABEL,
  displaySlotValues,
  hasRandomPrompt,
  isOptionalSlot,
  mergeSlotValues,
  resolveRandomness,
  suppressedSlotKeys,
  visibleSlots,
  type ManualSlotValues,
  type RandomnessMode,
  type SlotFillMode,
} from './randomness';
import { hasFallbackSlotRef, requiredSlotCount } from './slotIndexRefs';
import { SpecTagBar } from './SpecTagBar';
import { effectiveTemplateTagIds, templateTagLabel } from './specTagUtils';

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
   漏掉一项时页面上会直接印出英文（真机截到过）。 */
const TYPE_LABEL: Record<string, string> = KIND_LABEL;

function slotsOf(spec: TemplateSpecOut | undefined): SpecSlotOut[] {
  return spec?.latest?.slots ?? [];
}

function maxOf(slot: SpecSlotOut): number {
  return slot.maxCount ?? 1;
}

/**
 * 展示案例：作者上传/选的样例图或视频。
 *
 * 没设就明写「暂无案例」，不摆渐变块冒充缩略图 —— 那会让人以为模板带图。
 * 它是**纯展示**字段（存在草稿里、不进发布快照），所以这里只读、不可交互。
 */
/**
 * 模板卡片：案例封面 + 压在图上的名称 + 模板标签胶囊 + 一行模型。
 *
 * 名称压在封面底部而不是另起一行：卡片是「挑图」，图应该占满视觉重心，文字只做
 * 标注。版本号反而放右上角 —— 它是作者关心的东西，不该占掉选模板时的注意力。
 */
function TemplateCard({
  item,
  selected,
  onSelect,
  modelLabel,
  tagLabels,
}: {
  item: TemplateSpecOut;
  selected: boolean;
  onSelect: () => void;
  /** 模型的**友好名**（目录里的中文名）。拿不到就退回 id。 */
  modelLabel?: string;
  /** 由 `item.tagIds` 经词表映射出的**全路径**标签（父 / 名）。 */
  tagLabels: string[];
}) {
  /* 悬停态挂在**整张卡**上：封面只占卡片上半截，鼠标落在名称/标签那一带时
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
        'group flex flex-col items-stretch rounded-2xl border bg-panel p-1.5 text-left transition-all duration-200',
        selected
          ? 'border-primary-line shadow-[0_10px_24px_-12px_rgba(43,124,255,.55)] ring-1 ring-primary/20'
          : 'border-line-soft hover:border-line-strong',
      )}
    >
      {/* 显式 w-full + shrink-0：这层是卡片里第一个 flex item，某些 Edge 内核
          不会把 flex column 的 align-items:stretch 应用到含 aspect-ratio 的子树，
          于是它宽度算成 0，下面 absolute 的封面跟着全塌（图片其实早已解码成功）。
          钉死宽度并禁止压缩，任何内核都按卡片内容宽度渲染。 */}
      <div className="relative w-full shrink-0 overflow-hidden rounded-xl">
        <CaseCover
          assetId={item.demoAssetId}
          seed={item.key}
          className="aspect-[4/5] w-full shrink-0"
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
        {/* 卡片下方只摆模板标签，不再摆插槽状态（待填 N / 纯提示词 / 图片·视频）：
            那些说的是「这张表要填什么」，只有点进去才用得上；标签说的是
            「这张模板归哪几档」，才是挑模板时该看的。样式与素材库卡片一致，
            最多 4 枚，超出用 +N —— 再多会把卡片撑得参差。 */}
        <div className="flex min-h-[19px] flex-wrap items-center gap-1">
          {tagLabels.length > 0 ? (
            <>
              {tagLabels.slice(0, 4).map((label, index) => (
                <span
                  key={`${label}-${index}`}
                  title={label}
                  className="inline-flex max-w-full items-center rounded-pill border border-line-soft bg-panel-soft px-1.5 py-px text-xs leading-[15px] text-ink-soft"
                >
                  <span className="max-w-[110px] truncate">{label}</span>
                </span>
              ))}
              {tagLabels.length > 4 && (
                <span className="shrink-0 text-micro text-ink-mute">
                  +{tagLabels.length - 4}
                </span>
              )}
            </>
          ) : (
            <span className="text-micro text-ink-mute/70">未打标签</span>
          )}
        </div>
        {/* `mt-auto`：一行里卡片的 chip 数量不同时会换行成一两行，不推到底的话
            底栏会各自停在不同高度，一排看过去参差。 */}
        <div className="mt-auto flex items-center justify-between gap-2 border-t border-line-soft pt-1.5">
          <span className="min-w-0 truncate text-micro text-ink-mute">
            {/* 模型显示**友好名**而不是裸 id（`libtv-wan3-replacement` 这种
                id 是给编译器看的，使用者看不懂）。拿不到目录条目时退回 id。 */}
            {modelLabel ?? item.latest?.model ?? '—'}
          </span>
          <span
            className={cn(
              'flex shrink-0 items-center gap-0.5 rounded-xs px-1.5 py-[1px] text-micro transition-colors',
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
  /* 标签筛选留在组件 state（不进 URL）：模板库是填表入口，筛选只是这次浏览的
     视角，不该变成可以分享/回退的页面状态。 */
  const [selectedTagIds, setSelectedTagIds] = useState<string[]>([]);
  const tagsQuery = useTemplateTags();
  const tags = useMemo(() => tagsQuery.data ?? [], [tagsQuery.data]);
  /* 发请求前收敛：同维度里选了维度又选了它的取值时只留取值。
     界面上两枚胶囊都高亮，真正参与筛选的是这一份。 */
  const effectiveTagIds = useMemo(
    () => effectiveTemplateTagIds(tags, selectedTagIds),
    [tags, selectedTagIds],
  );
  const published = usePublishedSpecs(effectiveTagIds);
  const compile = useSpecCompile();
  const createTask = useCreateTask();
  const navigate = useNavigate();
  const generateImage = useGenerateImage();
  const videoModels = useModels('video');
  const imageModels = useModels('image');

  const [keyword, setKeyword] = useState('');
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  /* 使用者手动挑的素材。**值的 `undefined` 有含义**：「没动过这个槽位」。
     随机档下系统会替使用者填可选槽位，如果用 `[]` 当「没动过」，
     那「我不要系统挑的这张」就永远删不掉 —— 手动挑过的（哪怕挑空）
     必须能盖掉系统填的。 */
  const [values, setValues] = useState<ManualSlotValues>({});
  /* 使用者对「运行时参数」的选择（计费模式等）。跟素材选择分开存：
     素材是这次填的，计费模式是这次选的 —— 换模板时两者都要重来。 */
  const [userChoices, setUserChoices] = useState<Record<string, ParamValue>>({});
  /* 可选槽位的「填法」：挑素材，还是直接改写提示词里那一格。
     两张表分开存：切回「选素材」时原来挑的素材要还在（只是不提交），
     改过的文案也不清空 —— 想退回作者原话只能靠自己改回去，
     这里偷偷清掉等于让「我刚才写的话」凭空消失。 */
  const [slotModes, setSlotModes] = useState<Record<string, SlotFillMode>>({});
  const [slotTexts, setSlotTexts] = useState<Record<string, string>>({});
  const [picker, setPicker] = useState<string | null>(null);
  /* 使用者挑的模型。作者声明了「允许选用的模型」时才有得挑；清单为空 = 只有
     固定模型（老模板），页面上连下拉都不出现。空串 = 还没挑（用固定模型）。 */
  const [chosenModel, setChosenModel] = useState('');
  const [note, setNote] = useState('');
  const [error, setError] = useState('');
  /* 随机性三档：随机（整段换成作者的随机档提示词）/ 均衡（可选槽位用未选时文案）/
     自定义（使用者全权挑）。默认均衡 —— 这是「我只给关键素材」的常见用法，
     而且均衡档下右栏最干净（不摆一堆填不了的槽位）。 */
  const [randomness, setRandomness] = useState<RandomnessMode>(DEFAULT_RANDOMNESS);
  /* 二次确认：生成是要花钱的动作（积分加速更甚），按下之前先把这次会发生
     什么摊开写清楚 —— 「确定吗？」等于让人盲按。 */
  const [confirming, setConfirming] = useState(false);

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
  /* 词表 id → 全路径显示名。卡片只拿映射结果，不各自查词表。 */
  const tagLabelById = useMemo(
    () => new Map(tags.map(tag => [tag.id, templateTagLabel(tag)])),
    [tags],
  );
  const hasActiveFilter = keyword.trim().length > 0 || selectedTagIds.length > 0;

  const spec = items.find(item => item.key === selectedKey) ?? items[0];
  const slots = slotsOf(spec);
  const version = spec?.latest;
  const focused = spec?.key ?? null;
  /* 这个模板有没有配随机档提示词：只看**已发布版本**。草稿里写了但没发版，
     使用者拿到的仍是旧版本，选随机档会被后端 400。 */
  const randomAvailable = hasRandomPrompt(version);
  /* 档位用**派生值**而不是在 effect 里改 state：换模板后 state 可能还停在
     `random`，而新模板没配那一段。派生让「选中的档」与「实际生效的档」永远
     一致；用 effect 同步会有一个渲染窗口，那期间提交会带上错的标记。 */
  /* 自定义档的意义是「每张素材都由使用者亲自挑」—— 没有可选槽位时它和均衡档
     等价（可选的那张本来就不出现），这一档就该像随机档那样**置灰**：
     显示但点不动 + 说清原因，别让用户以为是自己挑错了。 */
  const customAvailable = slots.some(slot => (slot.minCount ?? 1) === 0);
  const resolvedRandomness = resolveRandomness(randomness, version);
  const effectiveRandomness: RandomnessMode =
    resolvedRandomness === 'custom' && !customAvailable ? 'balanced' : resolvedRandomness;
  /* 这一档真正发出去的那段提示词：均衡/自定义用主的，随机用随机档那段。
     下面「哪些槽位必须显示」的判据（提示词里 `{{slot:x.N}}` 把可选槽位钉住）
     要按**实际用的那段**算 —— 拿主提示词去判随机档，会把随机档钉住的槽位
     误当成可以省掉。 */
  const activePrompt =
    effectiveRandomness === 'random'
      ? (version?.randomPrompt ?? '')
      : (version?.prompt ?? '');
  /* 这一档下右栏显示哪些槽位：**必填的三档都显示**，可选的按档位处理
     （均衡 = 整个让给模型，不出现）。固定槽位在三种档位下都不出现 ——
     那是作者在封装页选死的，填表的人既不能换也用不着知道它长什么样
     （一排只读的 chip 只会让人以为这里还能点）。

     **提示词要一起传进去**：均衡档下有些可选槽位不能被让给模型
     （提示词里按「第 N 张」引用了它，省掉就会 400）。见 `slotIndexRefs`。 */
  const fillSlots = visibleSlots(slots, effectiveRandomness, activePrompt);
  /* 胶囊上显示的槽位名：用**全部**槽位算 —— 均衡档会隐掉可选槽位，
     但作者写的那段话里可能正引用着它。 */
  const refChips = useMemo(
    () => referenceChipsOf(slots as unknown as SpecSlotIn[]),
    [slots],
  );

  /* 容器名：**按完整槽位算**，不按 `fillSlots`。
     随机 / 均衡档会隐掉一部分可选槽位，若按 `fillSlots` 的段序编号，
     同一个容器在编辑页叫「封装槽位2」、在模板库叫「封装槽位1」。
     名字是作者起的（有声明就是固定的），回退名也应当稳定。 */
  const groupNames = useMemo(
    () => groupLabels(slots, version?.groups),
    [slots, version?.groups],
  );
  /* 这一档下**不该传素材**的槽位：随机/均衡档把可选槽位让给文案，
     所以它们既不出现在右栏，也不进 `slotValues`。 */
  const suppressed = useMemo(
    () => suppressedSlotKeys(slots, effectiveRandomness, activePrompt),
    [slots, effectiveRandomness, activePrompt],
  );

  /* 这份模板能跑的全部模型：固定模型 + 作者声明的允许清单。
     固定模型永远在里头 —— 它是模板自己的模型。 */
  const allowedModels = useMemo(
    () => effectiveModelIds(version?.model ?? '', version?.allowedModels ?? []),
    [version?.model, version?.allowedModels],
  );

  /* 换模板就清空上一次的填写：不做"记忆上次填的素材"，
     那会让用户以为新模板也用了同一张图。计费模式这类选择同理 —— 上一张
     模板的计费选择不该跟着带过来（那是上一笔生意的决定）。

     填法与改写的文案一起清：槽位 key 是自动生成的（`image_1`…），
     两个模板重名是常态 —— 留着的话，上一个模板里改成文案的格子会让
     新模板的同一格一打开就是输入框。 */
  useEffect(() => {
    setValues({});
    setUserChoices({});
    setChosenModel('');
    setNote('');
    setError('');
    setSlotModes({});
    setSlotTexts({});
  }, [focused, version?.version]);

  /** 换模型要清掉上一款的用户选择：两款模型的 `userSelectable` 参数未必相同，
     残留的 `paramOverrides` 会被后端以「模型 X 没有这些参数」拒掉，而界面上那个控件
     已经消失了。 */
  useEffect(() => {
    setUserChoices({});
  }, [chosenModel]);

  /* 模板只声明模型，「提交到哪个接口」由模型能力决定：视频进 /tasks、图片进
     /generations/image。判不出来（模型清单里没有）就按视频处理 —— 那是这条链路
     原本唯一支持的方向，报错信息也比「静默当图片」清楚。

     这三样（接口、参数默认值、参数控件）全都跟着**选中的那款模型**走：
     选了另一款模型却拿固定模型的默认值提交，等于用 A 的参数去跑 B。 */
  const choice = resolveModelChoice(
    version?.model ?? '',
    version?.allowedModels ?? [],
    chosenModel,
    imageModels,
    videoModels,
  );
  const modelId = choice.id;
  const model = choice.model;
  const kind = choice.kind;
  const defaults = choice.defaults;

  /** 模型 id → 目录里的友好名。卡片上显示的是这个，不是裸 id。 */
  const modelLabelOf = useCallback(
    (id: string): string | undefined =>
      findModelSpec([...videoModels, ...imageModels], id)?.label,
    [videoModels, imageModels],
  );

  /* 每个槽位**实际**最少要几张。
     提示词里写了「第 2 张」的槽位实际至少要 2 张（后端取 `owned[index - 1]`，
     越界就 400）。只看 `minCount` 的话，界面显示「不缺料」，用户一点生成
     就收到一句「第 2 张不存在」—— 那不像界面问题，像模板坏了。 */
  const requiredCounts = useMemo(() => {
    const map: Record<string, number> = {};
    for (const slot of slots) {
      // 按**实际生效的那段**提示词算：随机档用 randomPrompt，
      // 它的「第 N 张」与主提示词未必一致。
      map[slot.key] = requiredSlotCount(slot, activePrompt, slots);
    }
    return map;
  }, [slots, activePrompt]);

  /* 这一格能不能「改提示词」。三条缺一不可：
     - 作者标了可选（必填没有「没选」这个状态）；
     - 提示词里真有插入点（单张 / 整组引用，见 `hasFallbackSlotRef`）——
       没有插入点，敲的字一个也进不了提示词；
     - 提示词没按「第 N 张」把它钉住（钉住了就必须交素材，`requiredCount > 0`）。 */
  const canEditText = (slot: SpecSlotOut): boolean =>
    isOptionalSlot(slot) &&
    (requiredCounts[slot.key] ?? 0) === 0 &&
    hasFallbackSlotRef(activePrompt, slot.key, slots);

  /** 这一档下**真的在改文案**的格子：状态里是 text，且这一格现在确实能改。 */
  const textModeKeys = fillSlots
    .filter(slot => slotModes[slot.key] === 'text' && canEditText(slot))
    .map(slot => slot.key);

  /* 作者的「未选时文案」，按 key 索引：改写框的初值就是它，
     所以「自动取封装时写的那段」不需要 effect，也不会有先空白再填上的闪动。 */
  const emptyTextOf = useMemo(() => {
    const map: Record<string, string> = {};
    for (const slot of slots) map[slot.key] = slot.emptyText ?? '';
    return map;
  }, [slots]);
  const textOfKey = (key: string): string => slotTexts[key] ?? emptyTextOf[key] ?? '';

  /* **真正提交给后端的素材**：使用者手动挑的，剔除这一档不该传的槽位。
     随机档的可选槽位不传素材（提示词用作者的随机档那一段）。

     改成文案的格子同样不传素材：这一格说的是文字，存的旧素材只是
     「切回来时还在」的那一份。 */
  const submitValues = mergeSlotValues(values, [...suppressed, ...textModeKeys]);

  /* 缺料判定看的是**真正会提交的**那份：均衡档下可选槽位已经被剔除了，
     不该再拿它们去算「还缺几项」。

     **下限不是 `minCount` 而是 `requiredCounts`**：见上。

     改成文案的格子按**文案**判：这一格没素材是正常的，空的是那句话。 */
  const missing = slots.filter(slot => {
    if (slot.binding !== 'user') return false;
    if (suppressed.includes(slot.key)) return false;
    if (textModeKeys.includes(slot.key)) return textOfKey(slot.key).trim().length === 0;
    return (submitValues[slot.key] ?? []).length < (requiredCounts[slot.key] ?? 0);
  });

  /* 切档位：随机档不传素材、整段换提示词，所以不必「接管/交还」素材 ——
     使用者在自定义档挑的素材留在 `values` 里，切到随机档时被 suppressed
     不提交，切回自定义档又原样回来。 */
  const changeRandomness = (next: RandomnessMode) => {
    if (next === randomness) return;
    setRandomness(next);
  };

  /* ------------------------------------------------------------------
     封装包：在网格里点一个包 = 一次填好几格
     ------------------------------------------------------------------ */

  /** 待拉详情的包 + 它属于哪个容器。点了包就设它，详情回来后分发并清空。 */
  const [pendingPack, setPendingPack] = useState<{packId: string; group: string} | null>(
    null,
  );
  /** 记住这一批格子是哪个包填的：包没了要能精确清回它们，别连手挑的一起清。 */
  const [packFill, setPackFill] = useState<{packId: string; keys: string[]} | null>(null);

  /** 使用者动了某个槽位：写入手动值（随机档的可选槽位不显示，碰不到这里）。 */
  const setSlotValues = (key: string, ids: string[]) => {
    setValues(current => ({...current, [key]: ids}));
    /* 手动改过的格子就不再是「这个包填的」：留着这条记账，包被删时会把
       用户刚手挑的那张一起清掉。其余格子照旧订阅。 */
    setPackFill(current => {
      if (!current || !current.keys.includes(key)) return current;
      const keys = current.keys.filter(item => item !== key);
      return keys.length ? {...current, keys} : null;
    });
  };

  /* 换模板要清掉「这几格是这个包填的」这条记账：槽位 key 是自动生成的
     （`image_1`、`image_2`…），两个模板**重名是常态** —— 留着的话，上一个
     模板用的包在别处被删，会把这个模板里手挑的素材一起清掉。

     `pendingPack` 同样要清：点了包、详情还在路上时换了模板，回来那一笔
     会把上一个模板的包分发进新模板的槽位里。 */
  useEffect(() => {
    setPackFill(null);
    setPendingPack(null);
  }, [focused, version?.version]);

  /* 分发之后仍然订阅这个包的详情：`packFill` 在，就说明「这几格是它填的」，
     包被别处删掉时要能被发现（否则用户看到的是一份再也取不到图的配置）。
     分发那一刻 `pendingPack` 有值、之后 `packFill.packId` 接手，同一个 query 复用。 */
  const activePackId = pendingPack?.packId ?? packFill?.packId ?? null;
  const packQuery = useLibraryPack(activePackId);

  /**
   * 把一个包分发到各槽位。
   *
   * 两道判据：
   * 1. **这一格在同一个容器里**（`group` 相同）。独立槽位不参与 —— 它只从候选
   *    素材里挑，点包不该动它；
   * 2. **成员的标签 ∩ 这个槽位的候选标签**。它同时也是「这个包能不能填这一格」，
   *    列表那边用的是同一个判据（后端按 `tagIds` 过滤），所以这里匹配不到
   *    只可能是成员后来被删了。
   *
   * 容器里的几格由**同一次点包**一起填，这就是「几个插槽的素材来自同一个包」。
   *
   * 三种情况分开处理，**不静默取第一张**：
   * - 恰好 1 张 → 填；
   * - 0 张 → 不动（必填槽位会自己亮起缺料星号）；
   * - 多张 → 也不动，并说出来是哪几格 —— 猜一张进去，生成的图错了也没人知道。
   */
  const distributePack = (
    packId: string,
    group: string,
    members: {id: string; tagIds?: string[]; deleted?: boolean}[],
    packName: string,
  ) => {
    const patch: Record<string, string[]> = {};
    const ambiguous: string[] = [];

    for (const slot of fillSlots) {
      if (slot.type !== 'image' && slot.type !== 'video') continue;
      /* 改成文案的格子不再由包填：它这一格说的是文字，包往里塞一张图
         等于把用户刚写的话覆盖掉（而输入框还留着，看起来两个说法并存）。 */
      if (textModeKeys.includes(slot.key)) continue;
      /* 容器只填**自己**那几格：同一个包在两个容器里各出现一次，点哪个就填哪个，
         否则「点一次」会把手伸到另一个容器里去。独立槽位（`group` 为空）
         自然被排除 —— 它只从候选素材里挑。 */
      if ((slot.group ?? '') !== group) continue;
      const wanted = new Set(slot.tagIds ?? []);
      if (!wanted.size) continue; // 没候选标签就不知道该取哪张
      const matched = members.filter(
        member => !member.deleted && (member.tagIds ?? []).some(tagId => wanted.has(tagId)),
      );
      if (matched.length === 1) patch[slot.key] = [matched[0].id];
      else if (matched.length > 1) {
        ambiguous.push(`${slot.label || slot.key}（${matched.length} 张）`);
      }
    }

    const keys = Object.keys(patch);
    if (keys.length) {
      setValues(current => ({...current, ...patch}));
      setPackFill({packId, keys});
    }
    if (ambiguous.length) {
      toast(`「${packName}」里这几格有多张可选，请自己挑：${ambiguous.join('、')}`, 'error');
    } else if (keys.length) {
      toast(`已从「${packName}」填入 ${keys.length} 格`);
    } else {
      toast(`「${packName}」里没有能对上这些槽位的素材`, 'error');
    }
  };

  /* 详情回来就分发，并把待办清掉。 */
  useEffect(() => {
    if (!pendingPack || !packQuery.data) return;
    distributePack(
      pendingPack.packId,
      pendingPack.group,
      packQuery.data.members ?? [],
      packQuery.data.name,
    );
    setPendingPack(null);
    // distributePack 依赖 fillSlots，但它只在这次回调里用一次，不必进依赖
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingPack, packQuery.data]);

  /* 包被删掉（明确的 404）时，把由它填的那几格清回去。
     **只认 404**：网络抖动或服务重启也会让请求失败，那种时候清空等于
     在用户没操作的情况下丢掉已配好的素材。 */
  useEffect(() => {
    if (!packFill || !packQuery.isError) return;
    const status = (packQuery.error as {status?: number} | null)?.status;
    if (status !== 404) return;
    const keys = packFill.keys;
    setValues(current => {
      const next = {...current};
      for (const key of keys) delete next[key];
      return next;
    });
    setPackFill(null);
    toast('这个封装包已经不在了，相关素材已清空，请重新选', 'error');
  }, [packFill, packQuery.isError, packQuery.error]);

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
    return {key: control.key, label: control.label, text: paramDisplayText(control, raw)};
  });

  /* 填了几项：只看要用户填的那几个槽位。进度条用它，省得只在按钮上写一句
     「还缺 2 项」——那句话得滚到底才看得见。 */
  const filledCount = fillSlots.length - missing.length;
  const progress = fillSlots.length > 0 ? Math.round((filledCount / fillSlots.length) * 100) : 0;

  const busy = compile.isPending || createTask.isPending || generateImage.isPending;

  /* 二次确认框的内容。**在按下之前**把这次提交会带出去的东西摊开：
     模板、模型、素材、生效参数、计费方式。

     素材那行算的是**真正会进这次请求的**数量 —— 使用者填的槽位，加上作者
     已经固定好的那些（固定槽位的素材也会被编译进提示词，漏算就成了「界面
     说 2 项，实际发出去 4 张」）。

     计费方式取**当前生效的值**（使用者选了就用选的，没选就用模型声明的
     默认），而不是「有没有这个参数」—— 两者不是一回事。 */
  const fixedSlots = slots.filter(slot => slot.binding === 'fixed' && slot.assetId);
  const confirmSlots = [
    ...fillSlots.map(slot => {
      const count = (submitValues[slot.key] ?? []).length;
      /* 使用者自己改的那段话：这一格没素材，但它不是「没填」——
         摆成「（未填）」会让人以为提示词里用的是作者的兜底文案。 */
      if (textModeKeys.includes(slot.key)) {
        const text = textOfKey(slot.key).trim();
        return {
          label: slot.label || slot.key,
          count,
          ...(text ? {override: text} : {}),
        };
      }
      /* 可选槽位**没填** → 提示词里会用「未选时文案」。摆出来，使用者
         才知道这次生成会用什么内容。

         均衡档下可选槽位被整个剔除了，不进 `fillSlots` —— 它不传素材，
         也就没有「未填则用」这回事（提示词里那段根本没被引用）。

         **还要它真的被引用**：后端只在**单张引用**时用「未选时文案」
         （`compile.py::_fallback_or_raise`）。没被引用的槽位，它的兜底文案
         是死数据 —— 摆出来会让使用者以为这段话会进提示词。 */
      const fallback =
        isOptionalSlot(slot) &&
        count === 0 &&
        hasSingleSlotRef(version?.prompt ?? '', slot.key, slots)
          ? slot.emptyText?.trim() ?? ''
          : '';
      return {
        label: slot.label || slot.key,
        count,
        ...(fallback ? {fallback} : {}),
      };
    }),
    ...fixedSlots.map(slot => ({label: slot.label || slot.key, count: 1})),
  ];
  const confirmParams = [
    ...paramChips,
    /* 使用者改的运行时参数（计费模式除外，它单独一行）。 */
    ...userControls
      .filter(control => control.key !== 'billingMode')
      .map(control => ({
        key: control.key,
        label: control.label,
        text: paramDisplayText(control, userValues[control.key]),
      })),
  ];
  const billingMode =
    typeof userValues.billingMode === 'string' ? userValues.billingMode : undefined;
  const confirm = buildGenerateConfirm({
    templateName: spec?.name ?? '',
    version: version?.version ?? 0,
    modelLabel: model?.label ?? modelId,
    slots: confirmSlots,
    params: confirmParams,
    ...(billingMode ? {billingMode} : {}),
  });

  const pickerSlot = slots.find(slot => slot.key === picker);

  /* 选择器的语义是 `onToggle`（点已选的等于取消），多选槽位不关弹层：
     一次挑三张比反复开关三次省事；单选槽位选一张就替换掉原来的。
     「已选」的基准就是**使用者亲手挑的那份** —— 随机档不填素材，
     界面上显示的也只有手动值，不会再有「系统挑的」混在里面。 */
  const togglePick = (asset: PickedAsset) => {
    if (!pickerSlot) return;
    const key = pickerSlot.key;
    const limit = maxOf(pickerSlot);
    const base = displaySlotValues(key, values);
    const next = base.includes(asset.id)
      ? base.filter(id => id !== asset.id)
      : (limit > 1 ? [...base, asset.id] : [asset.id]).slice(0, limit);
    setSlotValues(key, next);
    /* 单选（含封装）选完就关：留着一个已经选好的弹层，用户只能再去点右上角，
       而这时提示词里的缩略图早就该出来了。多选槽位仍然不关，一次挑三张。 */
    if (limit <= 1) setPicker(null);
  };

  const submit = async () => {
    if (!spec) return;
    setError('');
    setNote('');
    try {
      /* 改成文案的格子：把那段话交给后端去替换「未选时文案」。
         只送非空的 —— 空的那格已经算进「还缺」里，按钮点不到这儿；
         真送上去后端也会 400 说「不能为空」。 */
      const slotTextOverrides: Record<string, string> = {};
      for (const key of textModeKeys) {
        const text = textOfKey(key).trim();
        if (text) slotTextOverrides[key] = text;
      }
      const compiled = await compile.mutateAsync({
        key: spec.key,
        payload: {
          /* 真正会提交的那份：随机/均衡档的可选槽位已剔除，不传素材。 */
          slotValues: submitValues,
          /* 随机档：让后端把提示词**整段**换成作者的随机档那一段。
             其它档不传（走主提示词）。用派生档位 —— state 里残留的 `random`
             在没配的模板上不算数。 */
          useRandomPrompt: effectiveRandomness === 'random',
          // 选中的模型：清单里挑的那款。不传则用模板的固定模型。
          model: modelId,
          ...(Object.keys(slotTextOverrides).length ? {slotTextOverrides} : {}),
          // 使用者选的运行时参数（计费模式等）：随这次编译走，不落进模板版本。
          ...(Object.keys(userChoices).length ? {paramOverrides: userChoices} : {}),
        },
      });
      const plan = toSubmitPayload(compiled, spec.name, kind, defaults, model);
      if (!plan.ok) {
        setError(plan.message);
        setConfirming(false);
        return;
      }
      if (plan.kind === 'image') {
        const job = await generateImage.mutateAsync(plan.payload);
        /* 任务名已在 `toImagePayload` 里随 payload 传模板名（与视频那条对齐）。
           不读 `job.name` 是因为这里只是「这一刻提交成功了吗」的提示，
           用本地的 spec.name 即可，不必等响应字段。 */
        setNote(`已提交生图：${spec.name}（${job.id}）`);
        // 右栏那条提示是「这一刻的状态」，滚下去就看不见了；提交这种一次性动作
        // 还要一条会自己消失的反馈，用户才知道「点到了、东西进去了」。
        toast('已提交生图，稍后到资产库查看', 'success');
      } else {
        const task = await createTask.mutateAsync(plan.payload);
        setNote(`已提交：${task.title || task.id}`);
        toast('任务已提交，可在任务中心查看进度', 'success');
      }
      setConfirming(false);
    } catch (reason) {
      /* 失败时也关掉确认框：错误显示在右栏底部，弹窗盖着就看不见 ——
         留着框等于把错误藏起来。 */
      const message = reason instanceof Error ? reason.message : String(reason);
      setError(message);
      setConfirming(false);
      // 失败同样要弹：错误条在右栏底部，正填着表的人未必看得到。
      toast(message, 'error');
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

  if (published.isError) {
    return (
      <ErrorState
        title="模板加载失败"
        error={published.error}
        onRetry={() => void published.refetch()}
        retrying={published.isFetching}
      />
    );
  }

  if (items.length === 0) {
    /* 筛空了 ≠ 没有模板：这时说「还没有已发布的模板」是在骗人，
       得给出一键回到全量的出口（两行筛选条在这个分支里也随之消失）。 */
    if (hasActiveFilter) {
      return (
        <EmptyState
          title="没有符合筛选条件的模板"
          action={
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                setKeyword('');
                setSelectedTagIds([]);
              }}
            >
              清除筛选
            </Button>
          }
        />
      );
    }
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
          <span className="rounded-xs bg-panel-soft px-2 py-[2px] text-micro text-ink-soft ring-1 ring-inset ring-line-soft">
            {items.length} 个模板
          </span>
        </div>
        {/* 两行标签筛选（维度 / 取值）放在工具栏下方，跟搜索框同属「挑模板」的
            前置动作；选中项发后端筛选，关键词仍在本地滤。 */}
        <SpecTagBar
          tags={tags}
          selected={selectedTagIds}
          busy={tagsQuery.isPending}
          onChange={setSelectedTagIds}
        />
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
                modelLabel={modelLabelOf(item.latest?.model ?? '')}
                tagLabels={(item.tagIds ?? [])
                  .map(id => tagLabelById.get(id))
                  .filter((label): label is string => !!label)}
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
              {/* 这里**不放「改模板」入口**（2026-11-22 用户要求去掉）：这一栏是
                  「填」的地方，跳到规格页是另一件事；改模板走左侧导航。
                  别因为「顺手」再加回来。 */}
              <div className="min-w-0">
                <div className="truncate text-lead font-semibold leading-tight tracking-[.1px] text-ink">
                  {spec.name}
                </div>
                <div className="mt-1.5 flex min-w-0 items-center gap-1.5 text-meta text-ink-mute">
                  <span className="shrink-0 rounded-xs bg-primary-soft px-1.5 py-[1px] text-micro font-medium text-primary-deep ring-1 ring-inset ring-primary-line/60">
                    v{version.version}
                  </span>
                  <span className="truncate text-ink-soft">{model?.label ?? modelId}</span>
                </div>
              </div>
            </header>

            {/* 滚动条用细的（`scroll-thin`，见 index.css）：这栏只有 400px 宽，
                8px 的全局滚动条会把槽位块挤窄。"stable" 让有没有滚动条两种情况等宽，
                切模板时内容不会左右跳。 */}
            <div className="scroll-thin flex min-h-0 flex-1 flex-col gap-3.5 overflow-y-auto overscroll-contain px-4 py-3.5 [scrollbar-gutter:stable]">
              {/* 随机性三档：**决定可选槽位的素材由谁给**。
                  放在素材区之前，因为它是这一栏的**前提** —— 先定「谁说了算」，
                  再看下面要填哪几格。
                  **无条件出现**（2026-11-18 用户拍板：「全是必填也显示」）——
                  三档是这一栏的前提，全是必填的模板也该让人看见这个选择。 */}
              {/* 控件与下面那行说明是**一个整体**（外面这层 div 就是为它包的）：
                  外层容器的 `gap-3.5` 是给「区块之间」用的，说明跟控件之间只该
                  差 6px，否则那行字看起来像另一个区块的标题。 */}
              <div>
                <div className="flex items-center gap-2">
                  {/* 没配随机档提示词的模板：这一档**显示但点不动** —— 直接不渲染
                      等于这个能力不存在，用户不会想到去问；显示了才需要对一句原因。 */}
                  <Segmented
                    aria-label="随机性"
                    value={effectiveRandomness}
                    onChange={changeRandomness}
                    options={[
                      {value: 'random', label: RANDOMNESS_LABEL.random},
                      {value: 'balanced', label: RANDOMNESS_LABEL.balanced},
                      {value: 'custom', label: RANDOMNESS_LABEL.custom},
                    ]}
                    hints={{
                      random: randomAvailable
                        ? '整段换成作者写的随机档提示词，场景描述不固定'
                        : '该模板未配置随机档提示词',
                      balanced: '可选的那张不传素材，用固定文案',
                      custom: customAvailable
                        ? '每张素材都由你亲自挑'
                        : '该模板没有可选槽位',
                    }}
                    disabledValues={[
                      ...(randomAvailable ? [] : ['random']),
                      ...(customAvailable ? [] : ['custom']),
                    ] as RandomnessMode[]}
                    className="flex-1"
                  />
                </div>
                {/* 这一行是**唯一**允许挂在控件下面的说明，两件事值得写出来：
                    ① 三档的差别在**生成结果**上（人物变不变、台词变不变），
                       哪几格能填是布局在说，「结果长什么样」布局说不出来；
                    ② 它随档位切换而变，读的人不用逐个试。
                    `aria-live`：切档时读屏要念出新的一句话，否则只有视觉有效。 */}
                <p className="mt-1.5 text-meta text-ink-soft" aria-live="polite">
                  {RANDOMNESS_DESCRIPTION[effectiveRandomness]}
                </p>
              </div>
              {!randomAvailable && (
                <p className="mt-1 text-micro text-ink-mute">
                  该模板未配置随机档提示词，随机档不可用
                </p>
              )}
              {!customAvailable && (
                <p className="mt-1 text-micro text-ink-mute">
                  该模板没有可选槽位，自定义档不可用
                </p>
              )}
              {/* 作者声明了「允许选用的模型」才出现这个下拉：使用模板的人从
                  清单里挑一款，而不是只能用一个写死的模型。只有一款时不出
                  现（老模板的样子）。 */}
              {allowedModels.length > 1 && (
                <PanelSection title="模型">
                  <Select
                    block
                    value={modelId}
                    options={modelChoiceOptions(allowedModels, imageModels, videoModels)}
                    onChange={event => setChosenModel(event.target.value)}
                  />
                </PanelSection>
              )}
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
                      /* 界面上显示的素材：只有使用者亲手挑的（随机/均衡档的
                         可选槽位不出现在这里，由文案接管）。 */
                      const filled = displaySlotValues(slot.key, values);

                      /* 封装容器整段由**第一个成员**画出来（一个选包入口），
                         其余成员跳过 —— 各画一次就回到「点了只一次、显示还是两个」。 */
                      const group = (slot.group ?? '').trim();
                      if (group) {
                        const members = fillSlots.filter(
                          item => (item.group ?? '').trim() === group,
                        );
                        if (members[0].key !== slot.key) return null;
                        return (
                          <PackGroupPicker
                            key={group}
                            label={groupNames.get(group) ?? ''}
                            members={members}
                            values={values}
                            canEditText={members
                              .filter(canEditText)
                              .map(member => member.key)}
                            slotModes={slotModes}
                            textOf={textOfKey}
                            onMode={(key, mode) =>
                              setSlotModes(current => ({...current, [key]: mode}))
                            }
                            onText={(key, value) =>
                              setSlotTexts(current => ({...current, [key]: value}))
                            }
                            chips={refChips}
                            onPickPack={packId => setPendingPack({packId, group})}
                            onClear={key => setSlotValues(key, [])}
                          />
                        );
                      }
                      /* 图片/视频：一格一个选择器，里面是「素材 + 封装包」同一个网格，
                         上面一行标签用来在候选范围里再筛。其余（文本/音频）保留行式写法。 */
                      if (slot.type === 'image' || slot.type === 'video') {
                        const editable = canEditText(slot);
                        return (
                          <SlotMediaPicker
                            key={slot.key}
                            slot={slot}
                            selected={filled}
                            onChange={ids => setSlotValues(slot.key, ids)}
                            requiredCount={requiredCounts[slot.key]}
                            mode={slotModes[slot.key] === 'text' ? 'text' : 'pick'}
                            canEditText={editable}
                            text={textOfKey(slot.key)}
                            onModeChange={mode =>
                              setSlotModes(current => ({...current, [slot.key]: mode}))
                            }
                            chips={refChips}
                            onTextChange={value =>
                              setSlotTexts(current => ({...current, [slot.key]: value}))
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
                              {/* 星号标的是「**还没填够**」，不是「有下限」。
                                  之前标的是「有下限」—— 结果一个已经填好的必填槽位
                                  和一个空着的必填槽位长得一模一样，用户只能自己
                                  逐格核对，或点了提交再吃一个错误。 */}
                              {(requiredCounts[slot.key] ?? 0) > filled.length && (
                                <span className="text-warn-deep">*</span>
                              )}
                            </span>
                            <span className="text-micro text-ink-mute">
                              {TYPE_LABEL[slot.type] ?? slot.type}
                              {maxOf(slot) > 1 ? ` · 最多 ${maxOf(slot)}` : ''}
                            </span>
                          </div>

                          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
                            {filled.map(assetId => (
                              <AssetChip
                                key={assetId}
                                assetId={assetId}
                                onRemove={() =>
                                  setSlotValues(
                                    slot.key,
                                    filled.filter(id => id !== assetId),
                                  )
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
                                : `选择${TYPE_LABEL[slot.type] ?? '素材'}`}
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </PanelSection>
              ) : (
                /* 没有要填的槽位 → **什么都不说**。

                   原来这里有三句散文（纯提示词 / 均衡模式 / 素材已固定，
                   每句后面都跟着「直接生成即可」）。三处都在替**已经看得见
                   的东西**说话：

                   - 「直接生成即可」= 下面那个可点的主按钮；
                   - 「纯提示词」/「素材已固定」这类插槽状态已从卡片上移除
                     （卡片下方只摆模板标签），再讲一遍等于复活旧信息；
                   - 「均衡模式：可选素材交给模型」= 上方那个分段控件
                     （均衡档时可选槽位整个不出现，这本身就是解释）。

                   曾经担心的是「说错会让使用者以为某张图参与了生成」——
                   但那种误读的前提是**右栏没给出正确信号**。现在信号在控件上。
                   留一段散文反而是在替控件说话（且说的是旧词「均衡模式」）。 */
                null
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
                onClick={() => setConfirming(true)}
              >
                <Play size={13} />
                {busy
                  ? '提交中…'
                  : missing.length > 0
                    ? /* 缺的是素材就说素材；有格子是「改成文案但还空着」时
                         去掉这个后缀 —— 写「还缺 1 项素材」会让人去素材里找，
                         而那里什么都没有（空的是输入框）。 */
                      `还缺 ${missing.length} 项${
                        missing.some(slot => textModeKeys.includes(slot.key)) ? '' : '素材'
                      }`
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
        kind={slotTypeOf(pickerSlot?.type ?? 'image')}
        /* 槽位的标签决定候选集：这里与封装页的固定选择器看的是同一批素材。 */
        tagIds={pickerSlot?.tagIds ?? []}
        selected={pickerSlot ? displaySlotValues(pickerSlot.key, values) : []}
        onClose={() => setPicker(null)}
        onToggle={togglePick}
      />

      {/* 二次确认：生成会提交一条真实的任务（可能扣积分），按下之前把这次
          会发生什么摊开。素材还缺时按钮本来就是灰的，所以这个框只在
          「确实要提交」的时候出现。 */}
      <ConfirmDialog
        open={confirming}
        title={kind === 'image' ? '确认生成图片？' : '确认生成视频？'}
        description="提交后会排队生成，可在任务中心查看进度。"
        details={confirm.rows.map(row => ({label: row.label, value: row.value}))}
        {...(confirm.notice ? {notice: confirm.notice} : {})}
        noticeTone={confirm.noticeTone}
        confirmLabel={kind === 'image' ? '生成图片' : '生成视频'}
        busy={busy}
        onCancel={() => setConfirming(false)}
        onConfirm={() => void submit()}
      />
    </div>
  );
}
