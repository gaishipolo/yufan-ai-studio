import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useBlocker } from 'react-router-dom';
import {
  ChevronLeft,
  FolderInput,
  GripVertical,
  ImageOff,
  Layers,
  Library,
  Plus,
  RotateCcw,
  Trash2,
} from 'lucide-react';
import { useMutation } from '@tanstack/react-query';
import { useModels } from '@/entities/model/api/model.queries';
import { findModelSpec } from '@/entities/model/model/types';
import { ParamFields } from '@/entities/model/ui/ParamFields';
import {
  authorParamControls,
  resolveParamValues,
  type ParamValue,
} from '@/entities/model/lib/paramForm';
import { libraryApi } from '@/entities/library/api/library.api';
import { useLibraryDetail, useLibraryTags } from '@/entities/library/api/library.queries';
import { isPubliclyReadable, viewQuery } from '@/entities/library/model/types';
import { scopeTagOptions } from '@/entities/library/model/tagTree';
import { CaseCover } from '@/entities/library/ui/CaseCover';
import { AssetPreview } from '@/entities/library/ui/AssetPreview';
import {
  useSpecMutations,
  useSpecs,
  useTrashedSpecs,
} from '@/entities/template-spec/api/spec.queries';
import { useCaseMutations, useTemplateCases } from '@/entities/template-case/api/case.queries';
import { reuseSpecName } from '@/entities/template-case/model/caseMeta';
import type {
  SpecSlotIn,
  TemplateCaseOut,
  TemplateSpecIn,
  TemplateSpecOut,
} from '@/shared/api/schema';
import {
  Button,
  Chip,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  FormField,
  Modal,
  Notice,
  PageHeader,
  TextInput,
} from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { toast } from '@/shared/lib/toast';
import { useCan } from '@/shared/access/use-access';
import { AssetPicker, type PickKind, type PickedAsset } from '@/features/template-specs/AssetPicker';
import { AddSlotMenu } from '@/features/template-specs/AddSlotMenu';
import { CaseLibrary } from '@/features/template-specs/case-library/CaseLibrary';
import { CasePickerDialog } from '@/features/template-specs/case-library/CasePickerDialog';
import { PublishCaseDialog } from '@/features/template-specs/case-library/PublishCaseDialog';
import { DemoMenu } from '@/features/template-specs/DemoMenu';
import { ModelPicker } from '@/features/template-specs/ModelPicker';
import { useSpecLimits } from '@/entities/template-spec/api/spec.queries';
import { PromptBox } from '@/features/template-specs/PromptBox';
import { groupLabels, groupRuns } from '@/entities/template-spec/model/slotGroups';
import { SlotCard } from '@/features/template-specs/SlotCard';
import {
  SLOT_TYPE_LABELS,
  SLOT_TYPES,
  authoredFingerprint,
  blankSlot,
  blankSpec,
  dropUserSelectableParams,
  fromCaseSnapshot,
  fromSpecOut,
  joinModelSelection,
  newKey,
  nextGroupKey,
  nextGroupLabel,
  normalize,
  promptRefError,
  referenceChips,
  renumberRefs,
  rekeySlot,
  setSlotOptional,
  slotRefToken,
  slotTypeOf,
  slotsMissingTag,
  splitModelSelection,
  stripSlotRefs,
  textsToClearForRequired,
  toMixedTokens,
  type SlotType,
} from './specDraft';
import {
  anchorsToRefs,
  assignDefaultText,
  defaultTextHasRef,
  hydrateAnchors,
  parseAnchors,
  readDefaultText,
  rekeyAnchor,
  replaceAnchorWithRef,
  stripAnchors,
  syncDefaultTexts,
  unwrapAnchor,
} from './defaultText';
import {DefaultTextMenu} from './DefaultTextMenu';
import { ImageSlotPresetModal } from './ImageSlotPresetModal';
import type { CaretPoint } from './CursorMenu';
import {
  filledTextOf,
  loadPresets,
  nextPresetLabel,
  type ImageSlotPreset,
} from './imageSlotPresets';

/* 槽位类型 = 产品定的四种（图片 / 成片 / 音频 / 文本）。取值不跟着资产库走 ——
   库里多一类，槽位不该自己多出一档；展示名仍取自资产库的 kind 词表。

   **文本与音频的选项名带上「占不占编号」** —— 那是选类型时就要知道的
   差别（`{{Text N}}` 独立编号空间 / 附加输入完全不编号）。原来把它写成
   槽位下面一行常驻小字，既占版面又与下拉里的选项名对不上。 */
const TYPE_OPTIONS = SLOT_TYPES.map(type => ({
  value: type,
  label:
    type === 'text'
      ? `${SLOT_TYPE_LABELS[type]}（占 {{Text N}}）`
      : type === 'audio'
        ? `${SLOT_TYPE_LABELS[type]}（附加输入，不占编号）`
        : SLOT_TYPE_LABELS[type],
}));

/** 容器里能放什么：只有图片/视频（文本/音频不参与编号分发，也不来自包）。 */
const PACK_CHILD_OPTIONS = TYPE_OPTIONS.filter(
  item => item.value === 'image' || item.value === 'video',
);

/** 预设在菜单里的值前缀：和槽位类型值区分开，回调里一眼能认出来。 */
const PRESET_VALUE_PREFIX = 'preset:';

/**
 * 带图片插槽预设的「加槽位」选项。
 *
 * 图片这一项带二级：`默认图片`（不套预设，走的还是老路）+ 每个预设。
 * 单独留「默认图片」是为了让作者能加一个完全空白的图片槽位 ——
 * 预设只省文案，不该变成「加图片就必须套一个模板」的强制。
 */
function addSlotOptions(
  presets: ImageSlotPreset[],
  base: {value: string; label: string}[],
) {
  return base.map((item) =>
    item.value !== 'image'
      ? item
      : {
          ...item,
          children: [
            {value: 'image', label: '默认图片（不套预设）'},
            ...presets.map((preset) => ({
              value: `${PRESET_VALUE_PREFIX}${preset.id}`,
              label: preset.name.trim() || '未命名预设',
            })),
          ],
        },
  );
}

/** 顶层「加槽位」菜单：四类槽位 + 一个封装容器。 */
const ADD_SLOT_BASE = [
  ...TYPE_OPTIONS,
  {value: '__pack__', label: '封装（一次选包填多格）'},
];


const STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  published: '已发布',
  disabled: '已停用',
};

/**
 * 正在拖什么：**一个槽位**，或者**整个封装**（里面的槽位一起挪）。
 *
 * 用 key 定位比下标稳（重排过程中下标会变）。两件事要分开，因为落点规则不同：
 * 槽位能落到某一行上（插进那一行所属的组），整个封装只能落到「段与段之间」。
 */
type DragState = {kind: 'slot'; key: string} | {kind: 'group'; group: string} | null;

/** 素材选择器的目标：某个槽位，或展示案例。 */
type PickTarget = {target: 'slot'; index: number} | {target: 'demo'; kind: PickKind};

/**
 * 待确认的「设为案例」：确认之后要把这条素材落到哪儿。
 *
 * `target` 决定落点（展示案例 / 某个固定槽位），`index` 与 `label` 是槽位那边的
 * 上下文 —— 确认后要按普通挑素材的同一套逻辑把素材钉进那个槽位。
 */
type PendingCase = {
  asset: PickedAsset;
  target: 'demo' | 'slot';
  index: number;
  label: string;
};

export function SpecWorkspace() {
  const list = useSpecs();
  const trash = useTrashedSpecs();
  const {validate, save, publish, disable, remove, restore} = useSpecMutations();
  // 规格可以指向视频模型，也可以指向图片模型；两组的参数区都由服务端 schema 驱动。
  const videoModels = useModels('video');
  const imageModels = useModels('image');
  const models = useMemo(() => [...videoModels, ...imageModels], [videoModels, imageModels]);

  /* 上限来自后端（编辑页要显示的那个数），不在前端写死。 */
  const limits = useSpecLimits();

  /* 案例池是全员的共享内容：上传 / 把素材转成案例都要 `library:case:write`。
     没有它就不摆「设置案例」—— 后端一定 403，露出来只是让人点一次错。 */
  const canCaseWrite = useCan('library:case:write');

  /* 模板案例（模板封装这套「案例」，不是资产库的案例池）是另一套权限：
     `template_pack:case:read` 才能进案例库，`template_pack:case:write` 才能
     发布 / 编辑 / 下架 / 删除。发布案例还要 `template_pack:publish` ——
     案例快照的是模板内容，没发布权的人不该把它发到公共案例库。 */
  const canTemplateCaseRead = useCan('template_pack:case:read');
  const canTemplateCaseWrite = useCan('template_pack:case:write');
  const canPublishTemplate = useCan('template_pack:publish');
  const canPublishCase = canTemplateCaseWrite && canPublishTemplate;

  const [draft, setDraft] = useState<TemplateSpecIn | null>(null);
  /*
   * 进编辑时记一份基线，之后每次变化都跟它比。
   *
   * 比的是 `authoredFingerprint`（语义指纹）而不是 `JSON.stringify`：形状差异
   * 不是内容差异 —— `assetId: null` 与没有 `assetId`、`tagIds: []` 与没有它、
   * 缺省字段与显式默认值、键的插入顺序、`revision` 令牌，任何一条都会让
   * 「只点了几下、什么都没改」的模板反复弹「有未保存的修改」。
   *
   * 保存 / 发布成功后基线跟着换（`openDraft` 里）—— 否则刚存完还挂着
   * 「有未保存修改」，用户会以为自己白存了。
   */
  const [baseline, setBaseline] = useState('');
  /** 有未保存改动：只有这时才拦离开。 */
  const dirty = draft !== null && authoredFingerprint(draft) !== baseline;

  /**
   * 进入编辑：设草稿并把当前内容记成基线。
   *
   * **先把提交形态还原成编辑形态**：服务端存的是 `{{Mixed N}}` + `emptyText`
   * 字段（编译契约），正文里没有锚点。不还原的话，作者下次打开只看到一枚
   * 胶囊，那段兜底文案又躲回卡片里，没法在正文里二次编辑 —— 这个功能的
   * 整个意义就没了。`hydrateAnchors` 按「有兜底文案 + 有单张引用」定位，
   * 存量模板不会凭空长出高亮段。
   */
  const openDraft = (next: TemplateSpecIn) => {
    const restored: TemplateSpecIn = {
      ...next,
      prompt: hydrateAnchors(next.prompt ?? '', next.slots ?? []),
    };
    setDraft(restored);
    setBaseline(authoredFingerprint(restored));
  };

  const [picker, setPicker] = useState<PickTarget | null>(null);
  const [uploading, setUploading] = useState(false);
  /* 选了一条还不在案例池里的素材：先问一句再动它。
     「设为案例」= 把这条素材**移出**个人库（普通列表默认不再显示它），
     不是复制一份，所以要让人看见这件事再确认。

     `target` 记着确认之后要把素材落到哪儿：展示案例，还是某个固定槽位。 */
  const [pendingCase, setPendingCase] = useState<PendingCase | null>(null);
  const [drag, setDrag] = useState<DragState>(null);
  /* 拖动时的落点，存成字符串 id（`row:<key>:before` / `box:<group>` / `tail`）：
     每条 dragover 都会重算一次，用字符串才比得动 —— 对象字面量每次都是新引用，
     `setState` 会一路重渲染整列。
     **它只负责高亮**；真正插到哪儿由 drop 那一刻的鼠标位置现算，
     免得「看到的高亮位置」和「实际落点」因为一次没来得及的重渲染而对不上。 */
  const [over, setOver] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const promoteCase = useMutation({
    mutationFn: (pending: PendingCase) => libraryApi.setPurpose(pending.asset.id, 'case'),
    onSuccess: (_row, pending) => {
      if (pending.target === 'demo') {
        setDraft(current => (current ? {...current, demoAssetId: pending.asset.id} : current));
      } else {
        patchSlot(pending.index, {
          assetId: pending.asset.id,
          label: pending.label,
          binding: 'fixed',
        });
      }
      setPendingCase(null);
      toast(
        pending.target === 'demo' ? '已移入案例池并设为展示案例' : '已移入案例池，可随模板发布',
        'success',
      );
    },
    onError: (reason: unknown) => {
      setPendingCase(null);
      fail(reason);
    },
  });

  const busy =
    validate.isPending ||
    save.isPending ||
    publish.isPending ||
    disable.isPending ||
    remove.isPending ||
    restore.isPending ||
    uploading ||
    promoteCase.isPending;
  // 契约里 `slots` 是可选的（后端有默认空列表），编辑器一律按非空处理。
  const slots = draft?.slots ?? [];
  const pickerSlot = picker?.target === 'slot' ? slots[picker.index] : undefined;

  /* 容器名：声明了的用声明的，没声明的按段序回退「封装槽位1/2/…」。
     与模板库共用同一个解析（`groupLabels`），否则作者看到的框名与使用者看到
     的块名可能不是同一个 —— 这类不一致只在别人用的时候被发现。

     依赖整个 `draft`（而不是 `slots` / `draft.groups`）：`slots` 是
     `draft?.slots ?? []` 算出来的，每次渲染都是新数组，放进依赖会让这个 memo
     形同虚设（lint 也会拦）。 */
  const groupNames = useMemo(
    () => groupLabels(draft?.slots ?? [], draft?.groups),
    [draft],
  );

  /** 改容器名。没有这一条就补一条（老草稿 / 刚从 latest 打开时的常态）。 */
  const renameGroup = (group: string, label: string) => {
    if (!draft) return;
    const groups = draft.groups ?? [];
    setDraft({
      ...draft,
      groups: groups.some(item => item.key === group)
        ? groups.map(item => (item.key === group ? {...item, label} : item))
        : [...groups, {key: group, label}],
    });
  };

  /* 当前模型里「由使用者选」的参数（计费模式）。作者不设它们 —— 写死等于
     替每个使用者决定要不要花积分。提交前会把草稿里残留的这类参数剔掉。 */
  const userSelectableKeys = useMemo(() => {
    const model = findModelSpec(models, draft?.model);
    if (!model?.paramSchema) return [];
    return Object.entries(model.paramSchema)
      .filter(([, field]) => field?.userSelectable === true)
      .map(([key]) => key);
  }, [models, draft?.model]);

  /* 槽位能圈**任意级别**的标签（一级分组 / 二级系列 / 三级具体词）：槽位的标签
     是「圈定候选范围」，不是「打在素材上」，所以不必限叶子 —— 圈一个分组正是
     它的用途。数量按全库算，跟槽位类型无关：这里的用途是「挑范围」，不是
     「看有多少条」。 */
  // 圈候选集时看到的是**成员**（选择器不藏封装成员）→ 计数按成员算。
  const tags = useLibraryTags({ ...viewQuery('all'), includePackMembers: true });
  const tagOptions = useMemo(() => scopeTagOptions(tags.data ?? []), [tags.data]);

  const clear = () => {
    setError('');
    setHint('');
    setRandomHint('');
  };

  const fail = (reason: unknown) =>
    setError(reason instanceof Error ? reason.message : String(reason));

  /** 返回是否**成功**：离开保护要靠它判断「存住了没有、能不能走」。 */
  const submit = async (action: 'validate' | 'save' | 'publish'): Promise<boolean> => {
    if (!draft) return false;
    clear();
    try {
      // 剔掉「使用者可选」的参数（计费模式）：它们由用模板的人在模板库选，
      // 作者设了后端会拒。老草稿里可能残留，剔一次省得改个无关字段就被卡住。
      const payload = dropUserSelectableParams(
        normalize(draft),
        userSelectableKeys,
      );
      /*
       * **锚点段 → 槽位引用**（`⟦e:key⟧描述⟦/e⟧` → `{{Mixed N}}`）。
       *
       * 光剥标记是不够的：那段描述会原样留在正文里，填了素材也不会消失，
       * 提示词里更没有放素材的地方。锚点段其实是**插入点**：提交时让位给
       * 单张引用，描述文字退到槽位的 `emptyText`。后端按既有规则展开 ——
       * 填了 → 编号（描述消失，Q1=A），没填 → `emptyText`（描述出现）。
       *
       * 剩下的裸标记（作者自己敲的、没闭合的半截锚点）才剥掉：
       * 它们不是引用，后端会把 `⟦…⟧` 当普通字混进提示词。
       */
      const strippedPrompt = stripAnchors(
        anchorsToRefs(payload.prompt ?? '', payload.slots ?? []),
      );
      const payloadWithoutAnchors: typeof payload = {
        ...payload,
        prompt: strippedPrompt,
      };
      // 发布闸门的前端预检：后端要求「要让用户填的图片/视频槽位必须圈候选标签」，
      // 而它只会说"这些槽位要圈标签"、不指哪里。先在本地拦一次并点名，
      // 省掉「点发布 → 400 → 回头逐个槽位找」那一轮。保存草稿不判（草稿允许半成品）。
      if (action !== 'save') {
        const missing = slotsMissingTag(payload.slots ?? []);
        if (missing.length > 0) {
          setError(
            `这些槽位还没圈候选标签：${missing.join('、')}。` +
              '没圈的话列不出可用的候选：素材槽位退化成全部素材，包槽位干脆列不出包。',
          );
          return false;
        }
      }
      if (action === 'validate') {
        const result = await validate.mutateAsync(payloadWithoutAnchors);
        toast(`预检通过 · 指纹 ${result.fingerprint.slice(0, 12)}`, 'success');
        return true;
      }
      const saved = await save.mutateAsync(payloadWithoutAnchors);
      openDraft(fromSpecOut(saved));
      if (action === 'save') {
        toast(`已保存（第 ${saved.revision} 次修订）`, 'success');
        return true;
      }
      const receipt = await publish.mutateAsync(saved.key);
      openDraft(fromSpecOut(receipt.spec));
      toast(`已发布 v${receipt.version}`, 'success');
      return true;
    } catch (reason) {
      fail(reason);
      return false;
    }
  };

  /*
   * 离开保护：改过的内容只在当前这一屏，离开就没了。
   *
   * 三个出口都要挡 —— 只挡一个等于没挡（用户从另一个出口走掉，同样白改）：
   * 1. 站内切页面（`useBlocker`，项目用的是 data router，能拦）；
   * 2. 关闭 / 刷新标签页（`beforeunload`，浏览器自己的原生提示）；
   * 3. 左上角「返回模板列表」（自己实现的按钮，跟 1 走同一套确认）。
   */
  const [leaveOpen, setLeaveOpen] = useState(false);
  /** 确认后要执行的那个「离开动作」（返回列表时用；路由切换走 blocker） */
  const leaveAction = useRef<(() => void) | null>(null);

  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirty && currentLocation.pathname !== nextLocation.pathname,
  );

  /* 浏览器关标签 / 刷新：只能给原生提示（浏览器不允许自定义文案）。 */
  useEffect(() => {
    if (!dirty) return undefined;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      // 老浏览器靠 returnValue 才弹
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [dirty]);

  /* 对话框的来源有两种：路由拦截、点返回按钮。统一成一个入口。 */
  const askLeave = (proceed: () => void) => {
    if (!dirty) {
      proceed();
      return;
    }
    leaveAction.current = proceed;
    setLeaveOpen(true);
  };

  const closeLeave = () => {
    setLeaveOpen(false);
    leaveAction.current = null;
    if (blocker.state === 'blocked') blocker.reset();
  };

  /**
   * 对话框里选了「先存再走」：存完、基线刷新（dirty 变 false），
   * 再把当初被拦下的那次离开放行。
   */
  const saveThenLeave = async (mode: 'save' | 'publish') => {
    const proceed = leaveAction.current;
    setLeaveOpen(false);
    /* 用 `submit` 的返回值判断，**不能**在这之后比 `draft` 与 `baseline`：
       那两个是本次渲染的闭包里捕获的旧值，`submit` 里 setState 不会改它们，
       比出来永远是「还不一样」。 */
    const ok = await submit(mode);
    // 存失败就别走了：内容还在，错误已经贴在吸顶栏上
    if (!ok) {
      leaveAction.current = null;
      if (blocker.state === 'blocked') blocker.reset();
      return;
    }
    leaveAction.current = null;
    if (proceed) proceed();
    else if (blocker.state === 'blocked') blocker.proceed();
  };

  const leaveNow = () => {
    const proceed = leaveAction.current;
    leaveAction.current = null;
    setLeaveOpen(false);
    if (proceed) proceed();
    else if (blocker.state === 'blocked') blocker.proceed();
  };
  const [view, setView] = useState<'list' | 'cases' | 'trash'>('list');
  /** 「从案例库复用」的案例选择器。 */
  const [casePickerOpen, setCasePickerOpen] = useState(false);
  /** 「发布为案例」的弹窗。 */
  const [publishCaseOpen, setPublishCaseOpen] = useState(false);
  /* 「移进回收站」的二次确认：删除是**不可逆的方向**（虽然能恢复），
     所以要有一层页面内的确认，而不是浏览器的原生弹窗。 */
  const [trashingSpec, setTrashingSpec] = useState<TemplateSpecOut | null>(null);
  /* 案例的写操作（发布 / 编辑 / 下架 / 上架 / 删除 / 恢复）。 */
  const caseMutations = useCaseMutations();
  /* 案例库里已有的分类：发布案例时给几个现成的点选。 */
  const caseMeta = useTemplateCases({scope: 'library', pageSize: 100});
  /* 当前这份模板的发布版本：从模板列表缓存里找。找不到（刚建的）就是 0 ——
     案例上记的版本由后端定，这里只是给作者一个「我发的是哪一版」的提示。 */
  const draftVersion = useMemo(
    () => (list.data?.items ?? []).find(item => item.key === draft?.key)?.currentVersion ?? 0,
    [list.data, draft?.key],
  );
  const [error, setError] = useState('');
  const [hint, setHint] = useState('');
  /* 两个提示词框各自一条提示：共用一个的话，在随机档那段里写错引用，
     错误会显示在主提示词下面，作者会去改错的那一段。 */
  const [randomHint, setRandomHint] = useState('');
  /* 「设为槽位默认描述」菜单：框选正文后弹出来。
     **只对主提示词**：随机档那段是整段替换的（不是追加），
     那里的默认描述会跟主提示词互相矛盾 —— 两处都要一份同一个槽位的兜底，
     编译期只会用其中一处，另一处就成了没人认领的死数据。 */
  const [defaultTextRange, setDefaultTextRange] = useState<{start: number; end: number} | null>(null);
  /* 框选菜单的锚点：编辑器上报选区时一起给（视口坐标），菜单浮在框选的那段字旁。 */
  const [defaultTextPoint, setDefaultTextPoint] = useState<CaretPoint | null>(null);
  const reportTextSelection = (
    range: {start: number; end: number} | null,
    point?: CaretPoint | null,
  ) => {
    setDefaultTextRange(range);
    setDefaultTextPoint(range ? point ?? null : null);
  };
  /* 图片插槽预设：配置页改的是本地存储，不触发当前页重渲染 ——
     关掉配置弹窗时重新读一遍，二级菜单才会立刻出现刚加的预设。 */
  const [presets, setPresets] = useState<ImageSlotPreset[]>(() => loadPresets());
  const [presetConfigOpen, setPresetConfigOpen] = useState(false);
  const patchSlot = (index: number, patch: Partial<SpecSlotIn>) => {
    if (!draft) return;
    const nextSlots = slots.slice();
    const next = {...nextSlots[index], ...patch};
    // 音频/文本一律是附加输入：类型一改就同步，别让用户先撞一次 400 再回来改。
    if (next.type === 'audio' || next.type === 'text') next.refMode = 'attachment';
    if (next.type === 'image' || next.type === 'video') {
      next.refMode = 'mixed';
    }
    // 切成**必填**时清掉两段文案：必填 = 素材一定在 = 直接插编号，
    // 这两段文案对它没有意义（后端也会拦：必填槽位带文案直接 400）。
    //
    // 判断用 `next`（**已经含新 minCount**）而不是切换前的槽位：按切换前
    // 判断的话，切到必填那一刻 minCount 还是旧的（0），于是清不掉 ——
    // 后端照样 400，作者看不出原因。
    //
    // **清掉并告知**，不静默丢：作者刚写的「人物参考 {{slot:hero}}」
    // 悄悄消失，他会以为是自己记错了。切回「可选」时文案不会自动回来
    // —— 那是作者的决定，不该由系统替他猜。
    if ((next.minCount ?? 1) >= 1) {
      const cleared = textsToClearForRequired(next);
      if (cleared) {
        Object.assign(next, cleared);
        toast(
          `「${next.label?.trim() || SLOT_TYPE_LABELS[slotTypeOf(next.type)]}」改成必填了，` +
            '两段文案已清空（必填槽位一定是直接插入素材编号）',
          'success',
        );
      }
    }
    /* 切成**已固定**时也清掉两段文案 —— 与「改成必填」是同一件事的两种说法：
       素材一定在 → 直接插编号，没有「填了 / 没填」两种说法。
       固定槽位的素材在封装时就定死了，一定有素材，所以两段文案对它没有意义。

       而且固定槽位上界面**不再显示**这两个框（SlotCard 的 `!fixed && showTexts`），
       留着就成了「看不见却还在生效」：编译期固定槽位有素材 → 确实走
       filled_text（compile.py），作者会以为那句话已经没了。
    */
    if (next.binding === 'fixed' && nextSlots[index].binding !== 'fixed') {
      const cleared = textsToClearForRequired(next);
      if (cleared) {
        Object.assign(next, cleared);
        toast(
          `「${next.label?.trim() || SLOT_TYPE_LABELS[slotTypeOf(next.type)]}」改成已固定了，` +
            '两段文案已清空（固定槽位的素材是定死的，只有一种说法）',
          'success',
        );
      }
    }
    nextSlots[index] = next;
    /* **顺序不能反**：
       1. 先重编号（此时 key 还没变，`{{Mixed N}}` 能换算回它自己的 key）；
       2. 再决定改不改名 —— `rekeySlot` 靠「正文里有没有 `{{slot:key}}`」判断
          能不能改名，而重编号正好把引用变成了 key 形态，这个判断才准。
       反过来先改名，重编号时按旧 key 就查不到这个槽位，正文里会留下一条
       指向不存在槽位的引用（保存时才 400，用户看不出是刚改的类型）。

       为什么必须重编号：把图片槽位改成文本，它就从视觉编号里退出去了，
       后面的槽位整体前移 —— 不动的话 `{{Mixed 3}}` 会静默指向另一个槽位。 */
    const renumbered = renumberRefs(draft, nextSlots);
    let prompt = renumbered.prompt ?? '';
    /* 锚点跟着正文走，但它的 key 是槽位 key —— 槽位改名时必须同步改，
       否则锚点指向一个不存在的槽位，提交时被当成裸标记剥掉、描述全丢。 */
    if (patch.type !== undefined) {
      const oldKey = slots[index].key;
      const rekeyed = rekeySlot(renumbered.slots ?? [], index, slotTypeOf(next.type), prompt);
      const newKey = rekeyed[index]?.key;
      if (newKey && newKey !== oldKey) prompt = rekeyAnchor(prompt, oldKey, newKey);
      setDraft({...renumbered, prompt, slots: rekeyed});
      return;
    }
    /* 切成必填 / 固定后，后端禁止带两段文案：锚点段要变成**普通单张引用**，
       那一段从此由素材编号接管（必填一定有素材）。留成纯文字的话素材再也
       不进提示词；留成锚点则提交时没有兜底文案，发布闸会拦。 */
    if ((next.minCount ?? 1) >= 1 || next.binding === 'fixed') {
      prompt = replaceAnchorWithRef(prompt, slots[index].key, slotRefToken(nextSlots, next));
    }
    setDraft({...renumbered, prompt});
  };

  /**
   * 把槽位拖到「第 `at` 个位置之前」，并归入 `group`（空 = 顶层）。
   *
   * `at` 用**移动前**的数组坐标（`at === slots.length` = 放到最后）。
   * 跨组时会改写归属：
   * - **进容器**：`group` 换成容器的、来源换成封装包、绑定换成用户填。
   *   固定的槽位进不来（它钉着一张具体素材，改过来等于把那句话删掉而没人知道），
   *   文本/音频也进不来（不参与编号分发，包里没有它们的位置）。
   * - **出容器**：`group` 清空，**来源保留**。拖出来表示「这一格不再跟那套绑定」，
   *   不表示「不再从包里取」—— 独立槽位的来源在更多设置里可以自己改。
   */
  const moveSlotTo = (key: string, at: number, group: string) => {
    if (!draft) return;
    const from = slots.findIndex(item => item.key === key);
    if (from < 0) return;
    const moving = slots[from];
    const src = (moving.group ?? '').trim();
    const dst = group.trim();
    // 移出源之后，目标下标要往前挪一位（否则落到自己后面那一格）
    const localAt = from < at ? at - 1 : at;
    if (src === dst && localAt === from) return;

    if (dst && dst !== src) {
      if (moving.binding === 'fixed') {
        toast('固定槽位钉着一张具体素材，先改成「用户填」再拖进封装', 'error');
        return;
      }
      if (moving.type !== 'image' && moving.type !== 'video') {
        toast('封装里只能放图片或视频槽位', 'error');
        return;
      }
    }

    const rest = slots.filter((_, index) => index !== from);
    const patched =
      src === dst
        ? moving
        : dst
          ? {
              ...moving,
              group: dst,
              binding: 'user' as const,
              assetId: null,
            }
          : {...moving, group: ''};
    const next = [...rest.slice(0, localAt), patched, ...rest.slice(localAt)].map(
      (slot, index) => ({...slot, position: index}),
    );
    // 位置就是编号：拖一下，后面所有 `{{Mixed N}}` 都要跟着变。
    setDraft(renumberRefs(draft, next));
  };

  /**
   * 把**整个封装**挪到第 `at` 个位置之前。
   *
   * 成员必须相邻，所以整块搬：从数组里摘掉这一段，再整体插到目标位置。
   * `at` 落在自己这段里面（前后两端之间）= 没动 —— 把它拖到自己的成员之间
   * 没有意义，而且会让相邻性看起来被破坏。
   */
  const moveGroupTo = (group: string, at: number) => {
    if (!draft) return;
    const members = slots.filter(slot => (slot.group ?? '') === group);
    if (!members.length) return;
    const first = slots.findIndex(slot => (slot.group ?? '') === group);
    if (at > first && at <= first + members.length) return;
    const rest = slots.filter(slot => (slot.group ?? '') !== group);
    const localAt = first < at ? at - members.length : at;
    const next = [...rest.slice(0, localAt), ...members, ...rest.slice(localAt)].map(
      (slot, index) => ({...slot, position: index}),
    );
    setDraft(renumberRefs(draft, next));
  };

  const addSlot = (
    type: SlotType = 'image',
    group = '',
    preset?: ImageSlotPreset,
  ) => {
    if (!draft) return;
    /* 容器成员必须**挨着**（后端 `_validate_groups` 会拦）：所以往容器里加槽位时
       插在它最后一个成员的后面，而不是一路 append 到末尾。 */
    const at = group
      ? slots.reduce(
          (last, item, index) => ((item.group ?? '') === group ? index + 1 : last),
          0,
        )
      : slots.length;
    const slot = blankSlot(at, type, slots, group);
    if (preset) {
      /* 名称与选中时文案一次带出。文案里的 `{{slot:key}}` 换成新槽位的真实 key ——
         后端编译期再按位置展开成 `{{Mixed N}}`，比这里直接算编号更稳（拖动排序不影响）。

         **必须设成可选**：后端 `_validate_slot_texts` 对必填槽位带文案直接 422
         （必填一定有素材，两段文案没有意义）。「选中时文案」本身就暗示这是
         可选槽位；用 `setSlotOptional` 同一套规则，不在这里手抄 minCount/maxCount。 */
      Object.assign(slot, setSlotOptional(slot, true));
      slot.label = nextPresetLabel(preset.name, slots);
      slot.filledText = filledTextOf(preset, slot.key);
    }
    const next = [...slots.slice(0, at), slot, ...slots.slice(at)].map((item, index) => ({
      ...item,
      position: index,
    }));
    setDraft(renumberRefs(draft, next));
    if (type === 'text') {
      setPicker({target: 'slot', index: next.findIndex(item => item.key === slot.key)});
    }
  };

  /** 菜单值 → 加槽位。`preset:` 开头的是二级里的预设，其余是槽位类型。 */
  const addFromMenu = (value: string, group = '') => {
    if (value.startsWith(PRESET_VALUE_PREFIX)) {
      const preset = presets.find((item) => item.id === value.slice(PRESET_VALUE_PREFIX.length));
      if (preset) addSlot('image', group, preset);
      return;
    }
    addSlot(slotTypeOf(value), group);
  };

  /**
   * 加一个封装容器：默认两个图片槽位。
   *
   * 「封装也是一种槽位，里面可以放别的槽位」—— 使用者只会看到**一个**选包入口，
   * 点一次包按标签把成员分到这几个槽位。默认给两个，是因为「正面 + 三视图」
   * 这类一对是最常见的用法；只要一个也能删掉另一个。
   */
  const addPackGroup = () => {
    if (!draft) return;
    const group = nextGroupKey(slots);
    const first = blankSlot(slots.length, 'image', slots, group);
    const second = blankSlot(slots.length + 1, 'image', [...slots, first], group);
    /* 容器名**在新建这一刻就定下来**（而不是渲染时按位置算）：作者之后拖动的
       是整个容器，位置会变；名字跟着位置变等于每拖一次都改一次名。
       编号取「第一个没被占用的」，避免删过中间容器后与现存的撞名。 */
    setDraft(
      renumberRefs(
        {
          ...draft,
          groups: [
            ...(draft.groups ?? []),
            {key: group, label: nextGroupLabel(draft.groups)},
          ],
        },
        [...slots, first, second],
      ),
    );
  };

  /**
   * 删掉一组槽位（单个槽位 / 整个封装容器）。
   *
   * 顺序很重要：**先**按新结构重编号（删掉第 1 个，后面的编号整体前移），
   * **再**逐个清掉被删槽位的引用 —— 反过来的话，清理时用的还是旧编号，
   * 会漏掉几条（它们已经变号了）。
   */
  const removeSlots = (keys: string[]) => {
    if (!draft || keys.length === 0) return;
    const names = slots
      .filter(item => keys.includes(item.key))
      .map(item => item.label?.trim() || SLOT_TYPE_LABELS[slotTypeOf(item.type)]);
    const remaining = slots.filter(item => !keys.includes(item.key));
    let next = renumberRefs(draft, remaining);
    let stripped = 0;
    for (const key of keys) {
      const result = stripSlotRefs(next.prompt ?? '', key, remaining);
      // 槽位没了，正文里指向它的锚点也成了孤儿。摘掉标记、**保留那段字** ——
      // 它是作者写的提示词，不该因为删槽位就凭空消失。
      next = {...next, prompt: unwrapAnchor(result.text, key)};
      stripped += result.count;
    }
    setDraft(
      // 顺带规范化一次：容器被删光之后，`groups` 里那条名字就成了没有成员的
      // 残留 —— 它写不进发布快照，留着只会让指纹与后端对不上。
      normalize(next),
    );
    const label = keys.length > 1 ? `封装里的 ${keys.length} 个槽位` : `槽位「${names[0]}」`;
    toast(
      stripped > 0
        ? `已删除${label}，提示词里的 ${stripped} 处引用一并清掉`
        : `已删除${label}`,
      'success',
    );
  };

  /**
   * 一行槽位（含拖动落点）。
   *
   * **整行都是放置区**，落点按半区算：鼠标在行上半 → 插到它前面，下半 → 后面。
   * 高亮画在**将要插入的那个位置**（行上方 / 下方那条线），是「松手会插到这里」
   * 的预览，不是「这一行可以放」—— 后者整列都在发光，等于没信息。
   *
   * 目标行的 `group` 决定插进哪儿：容器成员的前后 = 进那个容器，独立槽位的前后
   * = 留在顶层。所以「拖进封装」和「拖出封装」是同一个动作的两侧，不用两个手势。
   */
  const slotRowOf = (slot: SpecSlotIn, index: number, inGroup: boolean) => {
    const group = (slot.group ?? '').trim();
    const edge =
      over === `row:${slot.key}:before`
        ? 'before'
        : over === `row:${slot.key}:after`
          ? 'after'
          : null;
    return (
      <div
        key={slot.key}
        data-slot-key={slot.key}
        className="relative"
        onDragOver={event => {
          if (!drag) return;
          /* 拖的是**整个封装**时，容器里的行不接落点：往外冒泡，交给外面那层
             「这一段」处理。否则把容器拖到自己某个成员行上，会被读成
             「插进这个容器」—— 而它本来就在里面，等于什么都没发生。 */
          if (drag.kind === 'group' && inGroup) return;
          /* 拖到自己身上也要 preventDefault：不然事件冒泡到容器上，
             会被当成「丢进这个封装」，整块框亮起来。松手 = 不动，只是不画线。 */
          event.preventDefault();
          event.stopPropagation();
          const same = drag.kind === 'slot' && drag.key === slot.key;
          const next = same
            ? null
            : `row:${slot.key}:${
                event.clientY - event.currentTarget.getBoundingClientRect().top <
                event.currentTarget.getBoundingClientRect().height / 2
                  ? 'before'
                  : 'after'
              }`;
          setOver(current => (current === next ? current : next));
        }}
        onDrop={event => {
          if (!drag) return;
          if (drag.kind === 'group' && inGroup) return;
          event.preventDefault();
          event.stopPropagation();
          /* 落点按**松手这一刻**的鼠标位置现算，不用 render 时的 `edge`：
             高亮最多晚一帧，插入位置不能晚一帧。 */
          const rect = event.currentTarget.getBoundingClientRect();
          const at = event.clientY - rect.top < rect.height / 2 ? index : index + 1;
          const dragged = drag;
          setOver(null);
          setDrag(null);
          if (dragged.kind === 'group') moveGroupTo(dragged.group, at);
          else moveSlotTo(dragged.key, at, group);
        }}
      >
        {edge === 'before' && <DropLine at="before" />}
        <SlotCard
          slot={slot}
          index={index}
          inGroup={inGroup}
          dragging={drag?.kind === 'slot' && drag.key === slot.key}
          onDragStart={() => setDrag({kind: 'slot', key: slot.key})}
          onDragEnd={() => {
            setDrag(null);
            setOver(null);
          }}
          onPatch={patch => patchSlot(index, patch)}
          onDelete={() => removeSlots([slot.key])}
          onPickAsset={() => setPicker({target: 'slot', index})}
          tagOptions={tagOptions}
          typeOptions={inGroup ? PACK_CHILD_OPTIONS : TYPE_OPTIONS}
          slots={slots}
          prompt={draft?.prompt ?? ''}
        />
        {edge === 'after' && <DropLine at="after" />}
      </div>
    );
  };

  /** 拖动源所在的容器（空 = 顶层）。只有它非空时才需要「移出封装」那个落点。 */
  const draggingGroup =
    drag?.kind === 'slot'
      ? (slots.find(item => item.key === drag.key)?.group ?? '').trim()
      : '';

  const pick = (asset: PickedAsset) => {
    if (!draft || !picker) return;
    if (picker.target === 'demo') {
      setPicker(null);
      // 已经在案例池里的直接引用；否则先确认 —— 这一步会改变素材的可见范围。
      if (asset.purpose === 'case') setDraft({...draft, demoAssetId: asset.id});
      else setPendingCase({asset, target: 'demo', index: 0, label: ''});
      return;
    }
    const id = picker.index;
    const slot = slots[id];
    const label = slot.label?.trim() ? slot.label : asset.name;
    /* 挑了素材 = 钉住了：这个槽位从此是「已固定」。
       别的槽位本来就是这样 —— 素材只能钉在固定槽位上（SlotCard 的「选择素材」
       按钮也只在固定槽位上出现，切回「用户填」会把素材清掉）；文本槽位新增后
       直接弹选择器，原来落在「用户填」上，于是素材钉在一个「用户填」的槽位上，
       后端规则「由用户选择时不能预置 assetId」会让整份草稿存不下去。 */
    /* 固定槽位钉的那条素材会**随模板发布给所有人**——但**文本例外**：
       发布时正文直接抄进提示词（后端 `_inline_fixed_text`），模板里不再有
       这条素材的引用，复用者根本不会去读它，所以不要求它公开可读。
       图 / 视频没法内联，仍然必须先移进案例池或改成团队可见。 */
    if (slotTypeOf(slot.type) !== 'text' && !isPubliclyReadable(asset)) {
      setPicker(null);
      setPendingCase({asset, target: 'slot', index: id, label});
      return;
    }
    patchSlot(id, {
      assetId: asset.id,
      label,
      binding: 'fixed',
    });
    setPicker(null);
  };

  const uploadDemo = async (file: File) => {
    if (!draft) return;
    clear();
    setUploading(true);
    try {
      // 案例与普通素材分开落库：`purpose=case` 让它进案例池（对所有人可读、
      // 不进个人库列表），模板只引用一个 id，不复制字节。
      const asset = file.type.startsWith('video/')
        ? await libraryApi.uploadSourceVideo(file, {purpose: 'case'})
        : await libraryApi.upload(file, 'image', {purpose: 'case'});
      setDraft(current => (current ? {...current, demoAssetId: asset.id} : current));
    } catch (reason) {
      fail(reason);
    } finally {
      setUploading(false);
    }
  };

  /**
   * 提示词里的引用只认**对的东西**，不按「有没有花括号」一刀切。
   *
   * 原来的判据是「花括号变多就拒」：它拦不住真正的问题（拼错的 key 同样只是
   * 一对花括号），却把正确的写法一起拦了 —— 从别处粘一段 `{{slot:image_1}}`
   * 进来，内容完全正确，界面却报「不要手写大括号」，而且写入被拒、`value` 没变，
   * 连重渲染都不触发，粘进去的字以「半截文本」的样子留在框里（看见的和会存的
   * 不是一回事）。
   *
   * 现在按 `promptRefError` 判：key 存在与否、编号有没有手写、花括号闭没闭合。
   * 合法的引用一律收 —— 它与按钮插入的是同一个 token。
   *
   * 返回 `false` 表示**没写进去**，编辑器据此把 DOM 对齐回 `value`。
   */
  const applyPromptText = (text: string): boolean => {
    if (!draft) return false;
    const error = promptRefError(text, draft.slots ?? []);
    if (error) {
      setHint(error);
      return false;
    }
    /* 后端规则：empty_text 不许含槽位引用。正文里已框选的默认描述段是
       emptyText 的真相来源，所以段内同样不许插入引用 —— 否则指派时能拦、
       在段内打 @ 却溜过去，到发布才吃 400（两处规则不一致，用户很困惑）。 */
    if (parseAnchors(text).some((span) => defaultTextHasRef(text.slice(span.start, span.end)))) {
      setHint('默认描述里不能带素材引用（没填素材时没有编号可指）；请在框选段之外引用');
      return false;
    }
    setHint('');
    /* 存进去的永远是**作者写法**：手打或粘进来的 `{{slot:image_1}}`（内部 key）
       当场换成 `{{Mixed 1}}`。这样正文里只有一种写法，胶囊也能对上标签。 */
    const authored = toMixedTokens(text, draft.slots ?? []);
    /* 正文里的「默认描述段」是**唯一真相来源**：
       作者在正文里改了某段文字，那段就是对应槽位的「未选时文案」——
       同步过去，别让槽位卡片里的那份变成过期副本。
       （`syncDefaultTexts` 顺带处理「作者把整段删了」：该槽位的兜底文案清空，
       不留一段没人认领的死数据。） */
    const slots = syncDefaultTexts(authored, draft.slots ?? [], draft.prompt ?? '');
    setDraft({...draft, prompt: authored, slots});
    return true;
  };

  const chips = draft ? referenceChips(draft) : [];
  /* 正文里框选出的默认描述段悬停时，气泡要说出它属于哪个槽位。
     只取展示名，不重复那段描述文本（唯一真相仍在正文）。 */
  const slotNames = useMemo(
    () =>
      Object.fromEntries(
        (draft?.slots ?? []).map((slot) => [
          slot.key,
          slot.label?.trim() || SLOT_TYPE_LABELS[slotTypeOf(slot.type)],
        ]),
      ),
    [draft?.slots],
  );

  /* 随机档提示词：与主提示词**同一套**引用规则（都引用同一批槽位），
     只是写进另一个字段。校验也要各管各的提示，见 `randomHint` 的注释。 */
  const applyRandomPromptText = (text: string): boolean => {
    if (!draft) return false;
    const error = promptRefError(text, draft.slots ?? []);
    if (error) {
      setRandomHint(error);
      return false;
    }
    setRandomHint('');
    setDraft({...draft, randomPrompt: toMixedTokens(text, draft.slots ?? [])});
    return true;
  };

  /**
   * 把正文里选中的那段设为某槽位的「默认描述」。
   *
   * **正文是唯一真相来源**：那段文字**原地留在正文里**（加锚点标记，
   * 渲染成高亮），同时成为该槽位的 `emptyText`（= 编译期的未选时文案）。
   * 作者在正文里二次编辑它，`applyPromptText` 会把改动同步回槽位。
   *
   * **旧的那段先摘掉**：同一个槽位只能有一段默认描述 ——
   * 否则正文里留下两段都声称是这个槽位的兜底，编译期用哪段就成了
   * 「看谁先被匹配上」的事。
   */
  const handleAssignDefaultText = (slotKey: string, range: {start: number; end: number}) => {
    if (!draft) return;
    const authored = draft.prompt ?? '';
    /* 偏移换算放在纯函数里（含「重新框选同一段」的情况）：
       组件里按整段长度平移曾把正文切碎，见 defaultText.assignDefaultText。 */
    const next = assignDefaultText(authored, slotKey, range);
    /* 后端规则：`emptyText` 不许含任何槽位引用（没素材就没有编号可指）。
       作者框到一段带胶囊的文字时，在这里就拦住并说清为什么 —— 否则要等到
       发布才吃一个 400，而那时界面早就看不出是哪一段惹的祸。 */
    const picked = readDefaultText(next, slotKey);
    if (defaultTextHasRef(picked)) {
      setHint('默认描述里不能带素材引用（没填素材时没有编号可指）；请重新框一段纯文字');
      return;
    }
    const slots = syncDefaultTexts(next, draft.slots ?? [], authored);
    setDraft({...draft, prompt: next, slots});
    setDefaultTextRange(null);
    const slot = slots.find(item => item.key === slotKey);
    const name = slot?.label?.trim() || '该槽位';
    toast(`「${name}」的默认描述已设为「${slot?.emptyText?.trim() ?? ''}」`, 'success');
  };

  /**
   * 取消一段默认描述：摘掉锚点标记、文字留在正文（作者写的字不该凭空消失），
   * 同步清掉槽位的 emptyText。这样指派就可逆了。
   */
  const handleCancelDefaultText = (slotKey: string) => {
    if (!draft) return;
    const authored = draft.prompt ?? '';
    const next = unwrapAnchor(authored, slotKey);
    if (next === authored) return;
    const slots = syncDefaultTexts(next, draft.slots ?? [], authored);
    setDraft({...draft, prompt: next, slots});
    setDefaultTextRange(null);
    toast('已取消默认描述，这段文字仍是普通正文', 'success');
  };

  /* 提示词上限：**能不能发布**的那个数（执行层，按原文字数比）。
     没取到时显示 `—`，不猜一个数字 —— 猜错了比不显示更糟。
     两段各自比：随机档那段是整段替换，不是追加，所以不合并算。 */
  const promptLimit = limits.data?.promptExecutionLimit;
  const promptOver = promptLimit != null && (draft?.prompt ?? '').length > promptLimit;
  const randomPromptOver =
    promptLimit != null && (draft?.randomPrompt ?? '').length > promptLimit;

  /* 移进回收站：先问一句，问的是「这次会发生什么」。
     确认框是页面内的 `ConfirmDialog`（能摊开事实清单），不是浏览器的
     `window.confirm` —— 原生那个样式不受控，还会把整个页面卡住。 */
  const moveToTrash = (item: TemplateSpecOut) => {
    setTrashingSpec(item);
  };

  const confirmTrash = () => {
    const item = trashingSpec;
    if (!item) return;
    setTrashingSpec(null);
    clear();
    remove.mutateAsync(item.key).then(() => toast(`已移进回收站：${item.name}`, 'success'), fail);
  };

  /**
   * 从案例复用：把案例的配置快照打开成一份**新草稿**。
   *
   * 案例与模板是「快照」关系，不是引用关系 —— 复用出来的模板与案例各自独立，
   * 之后改案例不会影响它，反之亦然。所以 key 与名字都要换成新的：沿用源模板的
   * key 会把别人的模板覆盖掉。
   */
  const reuseCase = (item: TemplateCaseOut) => {
    const base = fromCaseSnapshot(item.config, {
      key: newKey('spec'),
      name: reuseSpecName(item),
    });
    /* 作者私有的固定素材，后端在快照里已经按可见性裁掉了（复用者读不到）。
       这种槽位到这里没有素材，要复用者自己挑一张 —— 提前说一句，
       免得打开一看是空的，以为模板坏了。 */
    const blanks = (base.slots ?? []).filter(
      slot => slot.binding === 'fixed' && !slot.assetId,
    ).length;
    clear();
    setCasePickerOpen(false);
    openDraft({...base, description: item.description || base.description});
    toast(
      blanks > 0
        ? `已复用「${item.name}」，有 ${blanks} 个固定槽位需要自己选素材`
        : `已复用「${item.name}」，可以接着改`,
      'success',
    );
  };

  /**
   * 发布为案例：案例快照的是**服务端已保存的草稿**。
   *
   * 所以先存一遍当前内容 —— 免得发出去的案例里少掉用户还没保存的改动
   * （用户以为发出去的是屏幕上的这一版）。
   */
  const publishCase = async (payload: {name: string; description: string; category: string}) => {
    if (!draft) throw new Error('没有正在编辑的模板');
    const saved = await submit('save');
    if (!saved) throw new Error('模板还没存住（见上方提示），先解决保存的问题');
    await caseMutations.create.mutateAsync({specKey: draft.key, ...payload});
    setPublishCaseOpen(false);
    toast(`已发布为案例「${payload.name}」`, 'success');
  };

  /* ---------- 列表 / 回收站 ---------- */
  if (!draft) {
    const trashed = view === 'trash';
    const rows = (trashed ? trash.data?.items : list.data?.items) ?? [];
    const loading = trashed ? trash.isLoading : list.isLoading;
    const listQueryError = trashed ? (trash.isError ? trash.error : null) : (list.isError ? list.error : null);
    /* 三档：模板 / 案例库 / 回收站。
       案例库没有读权限时不摆这一档 —— 后端一定 403，摆出来点了只看到一个空页。 */
    const viewTabs: {value: 'list' | 'cases' | 'trash'; label: string}[] = [
      {value: 'list', label: '模板'},
      ...(canTemplateCaseRead ? [{value: 'cases' as const, label: '案例库'}] : []),
      {value: 'trash', label: '回收站'},
    ];
    return (
      <section className="flex flex-col gap-4">
        {/* 列表态是普通页头；编辑态的标题在编辑器自己的吸顶栏里（跟面包屑、
            动作按钮排在一行），所以页头由这里出，不在外层页面。 */}
        <PageHeader title="模板封装" />
        <div className="flex flex-wrap items-center justify-between gap-2">
          {/* 分段控件：外圈是一层凹槽（软底 + 内描边），选中项是"浮起来的白片"。
              以前是两颗胶囊共享一个圆角边框，选中只换字色 —— 在一排白卡上面几乎看不出
              当前在哪一档。 */}
          <div className="inline-flex items-center gap-0.5 rounded-xl bg-canvas/90 p-0.5 shadow-[inset_0_1px_2px_rgba(21,50,61,.06)] ring-1 ring-inset ring-line-strong">
            {viewTabs.map(tab => (
              <button
                key={tab.value}
                type="button"
                onClick={() => {
                  setView(tab.value);
                  clear();
                }}
                className={cn(
                  'rounded-pill px-3 py-[5px] text-meta transition-all duration-150',
                  view === tab.value
                    ? 'bg-panel font-medium text-ink shadow-[0_1px_3px_rgba(21,50,61,.12)]'
                    : 'text-ink-mute hover:text-ink-soft',
                )}
              >
                {tab.label}
                {tab.value === 'trash' && (trash.data?.total ?? 0) > 0 && (
                  <span className="ml-1 tabular-nums text-ink-mute">{trash.data?.total}</span>
                )}
              </button>
            ))}
          </div>
          {/* 列表页只有这一个动作，给它实心：整页就一个焦点，不用读字也知道从哪开始。
              「从案例复用」是第二个入口，描边即可 —— 它是**另一种**开始方式，
              不该跟「新建模板」抢同一个权重。 */}
          {!trashed && (
            <div className="flex items-center gap-2">
              <Button variant="primary" onClick={() => openDraft(blankSpec())}>
                <Plus size={14} /> 新建模板
              </Button>
              {canTemplateCaseRead && (
                <Button variant="secondary" onClick={() => setCasePickerOpen(true)}>
                  <FolderInput size={14} /> 从案例复用
                </Button>
              )}
            </div>
          )}
        </div>

        {view === 'cases' ? (
          /* 案例库：另一档内容，口径（公共 / 我的 / 已删除）在面板内部再切一层。 */
          <CaseLibrary onReuse={reuseCase} />
        ) : (
          <>
            {error && <Notice tone="error">{error}</Notice>}
            {loading ? (
              <p className="py-10 text-center text-xs text-ink-mute">正在读取…</p>
            ) : listQueryError ? (
              <ErrorState
                title={trashed ? '回收站加载失败' : '模板列表加载失败'}
                error={listQueryError}
                onRetry={() => void (trashed ? trash.refetch() : list.refetch())}
                retrying={trashed ? trash.isFetching : list.isFetching}
              />
            ) : rows.length === 0 ? (
              /* 「新建模板」按钮就在上方，不用来一段话再说一遍怎么新建 */
              <EmptyState title={trashed ? '回收站是空的' : '还没有模板'} />
            ) : (
          /* 卡片而不是表格行：一张模板的"长相"是它的案例封面，列表里也该看得见。
             操作放在卡片脚上，扫一眼就知道哪张能发布、哪张已停用。
             一行 5 个（`lg` 档 3 列过渡）：封面是 9:16，列数再少每张卡就太高。 */
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
            {rows.map(item => (
              <SpecCard
                key={item.id}
                item={item}
                modelLabel={
                  findModelSpec(models, item.draft?.model || item.latest?.model)?.label
                }
                busy={busy}
                trashed={trashed}
                onEdit={() => {
                  openDraft(fromSpecOut(item));
                  clear();
                }}
                onPublish={() =>
                  publish
                    .mutateAsync(item.key)
                    .then(result =>
                      toast(
                        /* 停用态走的是同一个接口，但版本号不变（幂等分支）——
                           这时候说「已发布 v1」会让人以为又发了一版。 */
                        item.status === 'disabled'
                          ? `已启用 ${item.name}`
                          : `已发布 ${item.name} v${result.version}`,
                        'success',
                      ),
                    )
                    .catch(fail)
                }
                onDisable={() =>
                  disable
                    .mutateAsync(item.key)
                    .then(() => toast(`已停用 ${item.name}`, 'success'))
                    .catch(fail)
                }
                onRemove={() => moveToTrash(item)}
                onRestore={() =>
                  restore
                    .mutateAsync(item.key)
                    .then(() => toast(`已恢复 ${item.name}`, 'success'))
                    .catch(fail)
                }
              />
            ))}
          </div>
            )}
          </>
        )}

        {/* 图片插槽预设配置：关掉时重新读本地存储，二级菜单立刻出现刚加的预设。 */}
        <ImageSlotPresetModal
          open={presetConfigOpen}
          onClose={() => {
            setPresets(loadPresets());
            setPresetConfigOpen(false);
          }}
        />

        {/* 案例选择器：新建模板流程里的「从案例库复用」入口。 */}
        <CasePickerDialog
          open={casePickerOpen}
          onClose={() => setCasePickerOpen(false)}
          onPick={reuseCase}
        />

        {/* 移进回收站的二次确认：删除这种**方向不可逆**的动作要让用户
            明确点一次，按钮上写着会发生什么（可恢复），不用去猜。 */}
        <ConfirmDialog
          open={trashingSpec !== null}
          title="移进回收站？"
          description={`「${trashingSpec?.name ?? ''}」会移进回收站，随时可以恢复。`}
          details={[
            {label: '模板', value: trashingSpec?.name ?? '—'},
            {label: '影响', value: '不再出现在模板列表里'},
          ]}
          notice="回收站里的模板仍然保留全部配置，恢复后与原来一致。"
          noticeTone="info"
          confirmTone="danger"
          confirmLabel="移进回收站"
          onCancel={() => setTrashingSpec(null)}
          onConfirm={confirmTrash}
        />
      </section>
    );
  }

  /* ---------- 编辑器 ---------- */
  /* 参数控件由模型声明驱动（中文标签、档位文案都在 `paramControlsFor` 里）：
     以前这里拿 `Object.entries(paramSchema)` 直接渲染，界面上印的是 `durationSec`
     这种英文 key，枚举值也是裸的 `9:16`。

     只给「作者设的」参数 —— 标了 `userSelectable` 的（计费模式）由**使用模板的人**
     在模板库选，作者写死等于替每个使用者决定要不要花积分。 */
  const paramControls = authorParamControls(findModelSpec(models, draft.model));
  const paramOverrides: Record<string, ParamValue> = {};
  for (const [key, value] of Object.entries(draft.params ?? {})) {
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
      paramOverrides[key] = value;
    }
  }
  const paramValues = resolveParamValues(paramControls, paramOverrides);
  return (
    <section className="flex flex-col gap-4">
      {/* 操作栏吸顶：槽位一多就要滚很久，保存/发布不该被滚出屏幕。
          `top-[59px]` 让开顶栏那条同为 sticky 的 header，否则会被它盖住。 */}
      <div className="sticky top-[59px] z-20 -mx-1 flex flex-col gap-1.5 border-b border-line-soft bg-panel px-1 py-2">
        <div className="flex flex-wrap items-center justify-between gap-3">
          {/* 标题、面包屑、动作按钮排在一行：眼睛扫过一行就知道
              「我在哪、我编辑的是哪个、下一步按哪个」。 */}
          <div className="flex min-w-0 items-center gap-2">
            <button
              type="button"
              aria-label="返回模板列表"
              className="flex shrink-0 items-center gap-1 text-lead font-semibold tracking-[.2px] text-ink transition-colors hover:text-primary"
              onClick={() =>
                askLeave(() => {
                  setDraft(null);
                  clear();
                })
              }
            >
              <ChevronLeft size={16} />
              模板封装
            </button>
            <span className="shrink-0 text-body text-ink-mute">/</span>
            <span className="min-w-0 truncate text-body text-ink-soft">
              {draft.name || '未命名模板'}
            </span>
          </div>
          <div className="flex items-center gap-2">
            {/* 「发布为案例」排在最左边：它是**另一种对外动作**（发到公共案例库），
                跟右边三个「改这份模板」的动作不是一回事，挨太近容易被误按。
                没权限就不摆 —— 后端一定 403。 */}
            {canPublishCase && (
              <Button
                variant="secondary"
                disabled={busy}
                onClick={() => setPublishCaseOpen(true)}
              >
                <Library size={14} /> 发布为案例
              </Button>
            )}
            {/* 三个动作的权重必须分开：一页只留一个实心按钮（保存并发布），
                否则三颗长得一样的白按钮并排，用户每次都要读字才知道该点哪个。

                三档靠**有没有填充**分，不靠「有没有边框」——
                以前「预检」是无边框的 ghost，夹在两个有边框的按钮中间，
                看着像贴着的一段说明文字。三颗现在都是按钮，只有
                「保存并发布」是实心的。

                别把它做成「淡灰色的描边按钮」：那读起来像**禁用**。
                预检只是不改数据，不是不可点。 */}
            <Button variant="secondary" disabled={busy} onClick={() => void submit('validate')}>
              预检
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => void submit('save')}>
              保存草稿
            </Button>
            <Button variant="primary" disabled={busy} onClick={() => void submit('publish')}>
              保存并发布
            </Button>
          </div>
        </div>
        {/* 错误留在吸顶区：槽位多的时候用户是在页面底部点「保存并发布」的，
            错误留在页首等于没提示（预检失败/发布被拦正是最需要看见的时候）。
            成功提示走浮层 toast（不占文档流）—— 内联横幅出现/消失会顶动下面的表单。 */}
        {error && <Notice tone="error">{error}</Notice>}
      </div>

      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
        <div className="flex flex-col gap-4">
          <Section title="基本信息">
            <div className="grid gap-3 sm:grid-cols-[1.4fr_1fr]">
              <FormField label="名称" required>
                <TextInput
                  value={draft.name}
                  placeholder="例如：米兰之夜 · 产品替换"
                  onChange={event => setDraft({...draft, name: event.target.value})}
                />
              </FormField>
            </div>
            {/* 模型：**一个**多选控件，不是「单选 + 多选」两个。
                这份模板能用哪几款模型、以及先拿哪款，本来就是一件事；拆成两个控件
                会让用户在同一块信息里找两遍，还得出「模型和允许选用的模型不一样」
                这种没法自洽的心智模型。

                **勾选顺序第一个是默认模型**（契约里的 `model`），其余是
                「使用者也可以挑的」那几款（`allowedModels`）。契约里 `model` 必填，
                所以提交时拆成两半 —— 见 `splitModelSelection`。

                顺序即默认序这件事要让人看见：展开列表里已勾的排在最前，
                第一个标「默认」。用户改顺序就改默认，不另设一个下拉。 */}
            <FormField className="mt-3" label="模型" required>
              <ModelPicker
                models={models}
                value={joinModelSelection(draft.model, draft.allowedModels ?? [])}
                onChange={selection => {
                  const {model, allowedModels} = splitModelSelection(selection);
                  setDraft({...draft, model, allowedModels, params: {}});
                }}
              />
            </FormField>
            {paramControls.length > 0 && (
              <ParamFields
                className="mt-3"
                idPrefix="spec-param"
                controls={paramControls}
                values={paramValues}
                onChange={(key, value) =>
                  setDraft({...draft, params: {...draft.params, [key]: value}})
                }
              />
            )}
          </Section>

          <Section
            title="槽位"
            extra={
              <div className="flex items-center gap-1.5">
                {/* 配置入口紧挨着「加槽位」：预设就是在那儿用的，分两处找会漏。 */}
                <button
                  type="button"
                  onClick={() => setPresetConfigOpen(true)}
                  title="配置「加槽位 → 图片」下的预设"
                  className="inline-flex h-7 items-center gap-1 rounded-pill border border-line bg-panel px-2.5 text-xs font-medium text-ink-soft transition-colors hover:border-line-strong hover:text-ink"
                >
                  <Layers size={12} />
                  图片插槽配置
                </button>
                <AddSlotMenu
                  options={addSlotOptions(presets, ADD_SLOT_BASE)}
                  onAdd={(value) =>
                    value === '__pack__' ? addPackGroup() : addFromMenu(value)
                  }
                />
              </div>
            }
          >
            {/* 三个动作（拖动排序 / 改名称 / 切绑定）**不需要教** ——
                拖动手柄是可见的图标（`title` 里写着「顺序即编号」）、
                展示名是个长得像标题的输入框、切绑定是右侧那个写着
                「用户填 / 已固定」的分段控件。三个都自带解释，
                再摆一行教学条就是同一件事说两遍，还占一行版面。 */}
            {/* 槽位是一列清单，用细线分隔而不是一张张卡片：
                一排小方块缩到 50% 看会糊成一片，分隔线才看得出「几行、每行谁」。 */}
            <div className="flex flex-col gap-2">
              {slots.length === 0 && (
                <p className="rounded-lg border border-dashed border-line-strong bg-panel-soft px-3 py-4 text-meta text-ink-mute">
                  没有槽位。纯提示词的模板不需要素材，从上面的按钮加一个就行。
                </p>
              )}
              {/* 按「独立槽位 / 封装容器」切分渲染：容器里的几格画在一个框里，
                  因为它们在模板库那边只有**一个**选包入口。 */}
              {groupRuns(slots).map(run =>
                run.group === '' ? (
                  slotRowOf(run.members[0].slot, run.members[0].index, false)
                ) : (
                  /* 外面再套一层「这一段」：拖**整个封装**时落点是段与段之间，
                     而框里面全是行，行本身不接这个落点（见 slotRowOf 的 kind 判断）。 */
                  <div
                    key={run.group}
                    data-slot-run={run.group}
                    className="relative"
                    onDragOver={event => {
                      if (drag?.kind !== 'group') return;
                      event.preventDefault();
                      event.stopPropagation();
                      const rect = event.currentTarget.getBoundingClientRect();
                      const next = `run:${run.group}:${
                        event.clientY - rect.top < rect.height / 2 ? 'before' : 'after'
                      }`;
                      setOver(current => (current === next ? current : next));
                    }}
                    onDrop={event => {
                      if (drag?.kind !== 'group') return;
                      event.preventDefault();
                      event.stopPropagation();
                      const rect = event.currentTarget.getBoundingClientRect();
                      const at =
                        event.clientY - rect.top < rect.height / 2
                          ? run.members[0].index
                          : run.members[run.members.length - 1].index + 1;
                      const group = drag.group;
                      setOver(null);
                      setDrag(null);
                      moveGroupTo(group, at);
                    }}
                  >
                    {(over === `run:${run.group}:before` ||
                      over === `before-group:${run.group}`) && (
                      <DropLine at="before" scope="group" />
                    )}
                    <div
                      data-slot-group={run.group}
                      className={cn(
                        'rounded-xl border border-primary-line/50 bg-primary-soft/20 px-2 pb-2 pt-1 transition-shadow',
                        // 拖到框的**下半**（底部按钮那一圈）= 追加到这个容器的末尾
                        over === `box:${run.group}` && 'ring-2 ring-primary/35',
                        // 拖的就是这个封装：淡一档，表示「它正在被搬走」
                        drag?.kind === 'group' && drag.group === run.group && 'opacity-50',
                      )}
                      onDragOver={event => {
                        if (drag?.kind !== 'slot') return;
                        /* 行自己会 `stopPropagation`，所以能走到这里就说明指针在框的
                           **空白处** —— 只有两头：标题栏和底部按钮那一圈。
                           上半是标题栏：那是「插到这个封装**前面**」（另起一格，顶层）；
                           下半是底部：那是「丢进这个封装」（追加到末尾）。

                           以前整块框都算「追加到末尾」，于是「把外面的槽位插到封装
                           前面」在容器排第一时**完全没有落点** —— 它上面没有行可以让
                           你拖到「某一行的下半区」。 */
                        event.preventDefault();
                        const rect = event.currentTarget.getBoundingClientRect();
                        const next =
                          event.clientY - rect.top < rect.height / 2
                            ? `before-group:${run.group}`
                            : `box:${run.group}`;
                        setOver(current => (current === next ? current : next));
                      }}
                      onDrop={event => {
                        if (drag?.kind !== 'slot') return;
                        event.preventDefault();
                        const rect = event.currentTarget.getBoundingClientRect();
                        const before = event.clientY - rect.top < rect.height / 2;
                        const key = drag.key;
                        setOver(null);
                        setDrag(null);
                        if (before) {
                          // 插到容器的第一个成员**之前**，但归**顶层**（group 空）
                          moveSlotTo(key, run.members[0].index, '');
                          return;
                        }
                        const last = run.members[run.members.length - 1];
                        moveSlotTo(key, last.index + 1, run.group);
                      }}
                    >
                    <div className="flex items-center gap-1.5 px-1 py-1">
                      <span
                        draggable
                        data-group-handle={run.group}
                        onDragStart={event => {
                          event.stopPropagation();
                          setDrag({kind: 'group', group: run.group});
                        }}
                        onDragEnd={() => {
                          setDrag(null);
                          setOver(null);
                        }}
                        title="拖动整个封装（里面的槽位一起移动）"
                        className="flex shrink-0 cursor-grab items-center text-ink-mute transition-colors hover:text-ink active:cursor-grabbing"
                      >
                        <GripVertical size={12} />
                      </span>
                      <Layers size={13} className="text-primary" aria-hidden />
                      <input
                        /* 容器名：和槽位的展示名同一套交互 —— 平时像一行标题，
                           hover / 聚焦才显出边框（「看着像文本、点进去能改」）。
                           **边框隐形**是必须的：这里一显眼，整块就看不出
                           「这是个可以改名的标题」，会以为是个输入框表单。 */
                        value={
                          draft?.groups?.find(item => item.key === run.group)?.label ?? ''
                        }
                        placeholder={groupNames.get(run.group) ?? ''}
                        aria-label="封装名称"
                        onChange={event => renameGroup(run.group, event.target.value)}
                        className={cn(
                          'min-w-0 max-w-[11rem] rounded-md border border-transparent bg-transparent px-1.5 py-1 text-meta font-medium text-ink outline-none transition-colors',
                          'hover:border-line-soft focus:border-primary-line focus:bg-panel focus:ring-2 focus:ring-primary/10',
                          'placeholder:text-ink-mute',
                        )}
                      />
                      <span className="text-micro text-ink-mute">
                        {run.members.length} 个槽位 · 使用者选一次包，一起填好
                      </span>
                      <button
                        type="button"
                        title="删除整个封装槽位（连同里面的槽位）"
                        onClick={() => removeSlots(run.members.map(item => item.slot.key))}
                        className="ml-auto rounded-md p-1 text-ink-mute transition-colors hover:bg-danger-soft hover:text-danger"
                      >
                        <Trash2 size={12} />
                      </button>
                    </div>
                    <div className="divide-y divide-line-soft border-t border-primary-line/30">
                      {run.members.map(({slot, index}) => slotRowOf(slot, index, true))}
                    </div>
                    <div className="mt-1 flex justify-end">
                      <AddSlotMenu
                        options={addSlotOptions(presets, PACK_CHILD_OPTIONS)}
                        onAdd={value => addFromMenu(value, run.group)}
                      />
                    </div>
                    </div>
                    {over === `run:${run.group}:after` && <DropLine at="after" scope="group" />}
                  </div>
                ),
              )}
              {/* 拖的是容器里的槽位时，末尾单给一个「放到封装外面」的位置。
                  没有它，「容器是最后一段」就拖不出去 —— 后面没有行可以当落点，
                  而这一格在视觉上又确实在框里。 */}
              {draggingGroup !== '' && (
                <div
                  data-drop-tail="1"
                  onDragOver={event => {
                    event.preventDefault();
                    setOver(current => (current === 'tail' ? current : 'tail'));
                  }}
                  onDrop={event => {
                    if (drag?.kind !== 'slot') return;
                    event.preventDefault();
                    const key = drag.key;
                    setOver(null);
                    setDrag(null);
                    moveSlotTo(key, slots.length, '');
                  }}
                  className={cn(
                    'rounded-lg border border-dashed px-3 py-2 text-center text-micro transition-colors',
                    over === 'tail'
                      ? 'border-primary bg-primary-soft text-primary-deep'
                      : 'border-line-strong text-ink-mute',
                  )}
                >
                  拖到这里移出封装（放在最后）
                </div>
              )}
            </div>
          </Section>
        </div>

        <div className="flex flex-col gap-4 lg:sticky lg:top-[112px]">
          {/* 提示词这一块只留**一个标题**，不套整条板块外壳。
              它是这一页真正在写的东西，和下面「展示案例」不是同级信息。
              「+ 文本素材」也去掉了：左栏「槽位」板块的加号菜单已经能加
              任意类型（含文本），同一个动作摆两个入口，用户还得判断
              哪个才是"正确"的。 */}
          <div className="rounded-2xl border border-line bg-panel p-4">
            {/* 这一页真正在写的东西，字阶与「展示案例」的分区标题同级（lead）——
                以前用 meta，比次级分区还小，扫一眼先看到「展示案例」，层次反了。
                仍只是一行字，不套整条标题带。 */}
            <p className="mb-2 flex items-center gap-2 text-lead font-semibold tracking-[.2px] text-ink">
              <span className="h-3.5 w-[3px] rounded-full bg-primary" />
              提示词
            </p>
            <div className="relative">
              <PromptBox
                value={draft.prompt ?? ''}
                chips={chips}
                slotNames={slotNames}
                /* 两条写入路径（手打 / 按钮与 @）走**同一个**校验：
                   合法的引用与按钮插入的是同一个 token，没有理由区别对待。 */
                onChange={applyPromptText}
                onInsert={applyPromptText}
                onSelectionChange={reportTextSelection}
              />
              {/* 「设为槽位默认描述」菜单：框选正文里的一段就弹出来。
                  菜单贴着提示词框 —— 作者的注意力在那段文字上，
                  菜单飘到别处就得再找一次「刚才框的是哪段」。 */}
              <DefaultTextMenu
                slots={draft.slots ?? []}
                range={defaultTextRange}
                point={defaultTextPoint}
                prompt={draft.prompt ?? ''}
                onPick={handleAssignDefaultText}
                onCancel={handleCancelDefaultText}
                onDismiss={() => setDefaultTextRange(null)}
              />
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {hint && <p className="text-meta text-warn-deep">{hint}</p>}
              {/* 字数是一门**约束**（超了发不出去），不是解释，必须留着。
                  显示的是**能不能发布**的上限（执行层，按原文字数比），
                  不是存草稿的上限 —— 后者是 20000，照着它写等于白写。
                  数字从后端取（`/template-specs/limits`），前端不再抄一份。 */}
              <span
                className={cn(
                  'ml-auto font-mono text-micro tabular-nums',
                  promptOver ? 'text-danger-deep' : 'text-ink-mute',
                )}
              >
                {(draft.prompt ?? '').length} / {promptLimit ?? '—'}
              </span>
            </div>

            {/* 随机档提示词：和上面那框长得一样（同样能 @ 槽位），因为它俩是
                **同一件事的两份**——随机档时整段替换，不是追加。标题必须留：
                两个一样的框并排放，没有标题就分不清哪个是默认。 */}
            <div className="mt-4 border-t border-line-soft pt-3">
              <p className="mb-2 text-meta font-medium text-ink">随机档提示词</p>
              <PromptBox
                value={draft.randomPrompt ?? ''}
                chips={chips}
                slotNames={slotNames}
                onChange={applyRandomPromptText}
                onInsert={applyRandomPromptText}
                placeholder="模板库选「随机」时整段替换上面那段；留空则这一档不可用"
                ariaLabel="随机档提示词"
              />
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {randomHint && <p className="text-meta text-warn-deep">{randomHint}</p>}
                <span
                  className={cn(
                    'ml-auto font-mono text-micro tabular-nums',
                    randomPromptOver ? 'text-danger-deep' : 'text-ink-mute',
                  )}
                >
                  {(draft.randomPrompt ?? '').length} / {promptLimit ?? '—'}
                </span>
              </div>
            </div>
          </div>

          <Section
            title="展示案例"
          >
            <div className="flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <DemoCase
                  assetId={draft.demoAssetId ?? null}
                  onClear={() => setDraft({...draft, demoAssetId: null})}
                />
              </div>
              {/* 动作放在它作用的东西旁边：案例在左边，「设置案例」在右边，
                  不用把眼睛从卡片内容挪到标题栏去找按钮。

                  没有 `library:case:write` 就不出现：三个入口（上传 / 选图 / 选视频）
                  都会改案例池，后端一律 403 —— 露出来只是让人点一次错。
                  已有案例照样能看（卡片在左边），只是不能换。 */}
              {canCaseWrite && (
                <DemoMenu
                  disabled={busy}
                  onUpload={() => fileRef.current?.click()}
                  onPickImage={() => setPicker({target: 'demo', kind: 'image'})}
                  onPickVideo={() => setPicker({target: 'demo', kind: 'video'})}
                />
              )}
            </div>
            <input
              ref={fileRef}
              type="file"
              accept="image/*,video/*"
              className="hidden"
              onChange={event => {
                const file = event.target.files?.[0];
                event.target.value = '';
                if (file) void uploadDemo(file);
              }}
            />
          </Section>
        </div>
      </div>

      <AssetPicker
        open={picker !== null}
        kind={
          picker?.target === 'demo'
            ? picker.kind
            : slotTypeOf(pickerSlot?.type ?? 'image')
        }
        /* 出「我的素材 / 案例池」两档的场合：
           - 选展示案例：案例不进普通列表，没有这一档作者传过的案例第二次就挑不到；
           - 挑图 / 视频固定素材：挑了就是钉住，钉住的那条要**随模板公开**，
             公开的素材就在案例池里。
           文本槽位不给这一档：正文发布时会抄进提示词，不存在可见性问题。 */
        casePool={
          picker !== null &&
          (picker.target === 'demo' || slotTypeOf(pickerSlot?.type ?? 'image') !== 'text')
        }
        title={
          picker?.target === 'demo'
            ? '选择展示案例'
            : slotTypeOf(pickerSlot?.type ?? 'image') === 'text'
              ? undefined
              : '选择固定素材'
        }
        /* 槽位的标签就是候选集范围：封装页挑固定素材时也要收窄，
           否则作者能选到模板使用者那边根本看不到的东西。 */
        tagIds={picker?.target === 'slot' ? (pickerSlot?.tagIds ?? []) : []}
        selected={
          picker === null
            ? []
            : picker.target === 'demo'
              ? [draft.demoAssetId ?? ''].filter(Boolean)
              : [slots[picker.index].assetId ?? ''].filter(Boolean)
        }
        onClose={() => setPicker(null)}
        onToggle={pick}
      />

      {/* 把一条普通素材变成案例：要说清「不是复制一份」，否则作者会以为素材
          还在自己库里的老位置，回头找不到就当成丢了。 */}
      <Modal
        open={pendingCase !== null}
        onClose={() => setPendingCase(null)}
        title="把这条素材设为案例"
        width={460}
      >
        <div className="flex flex-col gap-3 px-[18px] py-4">
          {/* 先把它要动的是哪一条摆出来：文件名一长，混在正文里就看不见了。
              平时素材列表里也是这么一条一行，形状认得出来。 */}
          <div className="flex items-center gap-2 rounded-lg border border-line-soft bg-panel-soft px-2.5 py-2">
            <FolderInput size={14} className="shrink-0 text-ink-mute" aria-hidden />
            <span className="min-w-0 flex-1 truncate text-body text-ink" title={pendingCase?.asset.name}>
              {pendingCase?.asset.name}
            </span>
          </div>

          {/* 后果逐条列。原来是一整段：读到第三句，第一句已经忘了。 */}
          <ul className="flex flex-col gap-1.5 text-body leading-relaxed text-ink-soft">
            <li className="flex gap-2">
              <span className="mt-[8px] size-[5px] shrink-0 rounded-full bg-ink-mute/45" />
              <span className="min-w-0">
                移入<span className="font-medium text-ink">案例池</span>
                ：{pendingCase?.target === 'slot' ? '所有复用这个模板的人' : '模板库的所有人'}
                都能看到它
              </span>
            </li>
            <li className="flex gap-2">
              <span className="mt-[8px] size-[5px] shrink-0 rounded-full bg-ink-mute/45" />
              <span className="min-w-0">不再出现在你的个人素材列表里</span>
            </li>
            <li className="flex gap-2">
              <span className="mt-[8px] size-[5px] shrink-0 rounded-full bg-ink-mute/45" />
              <span className="min-w-0">素材只有一份，不是复制一份</span>
            </li>
          </ul>

          {/* 这是**读者猜不到**的两件事（既有模板受不受影响、能不能反悔），
              不是复述上面的话 —— 收成一条注脚。
              用「分隔线 + 弱化文字」而不是 `Notice`：Notice 的底色和描边跟输入框
              是同一套语言，摆在弹窗里会被读成「这里能填」。 */}
          <p className="border-t border-line-soft pt-3 text-meta leading-relaxed text-ink-mute">
            已经在别处用到的模板不受影响；之后也可以改回普通素材。
          </p>

          <div className="flex justify-end gap-2 pt-1">
            <Button onClick={() => setPendingCase(null)}>取消</Button>
            <Button
              variant="primary"
              loading={promoteCase.isPending}
              onClick={() => pendingCase && promoteCase.mutate(pendingCase)}
            >
              确认设为案例
            </Button>
          </div>
        </div>
      </Modal>

      {/* 离开前的三选一。**一个出口一套**，别只挡返回按钮 ——
          从浏览器后退、点侧边栏切页面走的是另一条路，那条不挡等于白做。 */}
      <Modal
        open={leaveOpen || blocker.state === 'blocked'}
        onClose={closeLeave}
        title="还有未保存的修改"
        width={440}
      >
        <div className="flex flex-col gap-3 px-[18px] py-4">
          <p className="text-body leading-relaxed text-ink-soft">
            这些修改只存在当前页面，离开就没了。
          </p>

          {/* 两条「先存再走」是推荐路径：并排摆在一起、占满整行 ——
              它们回答的是同一个问题（存下来），只是存成哪一种。 */}
          <div className="flex flex-col gap-2">
            <Button
              variant="primary"
              block
              disabled={busy}
              onClick={() => void saveThenLeave('publish')}
            >
              保存并发布
            </Button>
            <Button block disabled={busy} onClick={() => void saveThenLeave('save')}>
              保存草稿后离开
            </Button>
          </div>

          {/* 「不保存」那一侧的决定收到底部一行：左边是留下（退路），
              右边是放弃（危险）。四个按钮平铺成一个菜单时，用户得
              从头读到尾才知道哪个是退路。 */}
          <div className="mt-1 flex items-center justify-between border-t border-line-soft pt-3">
            {/* 两个都用**带描边**的样子：它们各自是一个动作，不是一段文字。
                用 ghost（`border-transparent`）会被读成可点的文字链接，
                危险的那个还会因为只剩红色而显得比「留在这一页」更像主按钮。 */}
            <Button variant="secondary" disabled={busy} onClick={closeLeave}>
              留在这一页
            </Button>
            <Button
              variant="secondary"
              className="border-danger-line text-danger hover:border-danger hover:bg-danger-soft hover:text-danger"
              disabled={busy}
              onClick={leaveNow}
            >
              不保存，直接离开
            </Button>
          </div>
        </div>
      </Modal>

      {/* 「发布为案例」弹窗：发布的是**这份模板当前内容的一份快照**，
          提交前会先把模板存一遍（见 `publishCase`）。 */}
      <PublishCaseDialog
        open={publishCaseOpen}
        spec={{
          key: draft.key,
          name: draft.name,
          version: draftVersion,
          slotCount: slots.length,
          model: draft.model,
        }}
        categories={caseMeta.data?.categories ?? []}
        onClose={() => setPublishCaseOpen(false)}
        onSubmit={publishCase}
      />

      {/* 图片插槽预设配置。编辑器与列表是两个独立的 render 分支，
          按钮只在编辑器里，所以这里也要挂一份（关掉时刷新二级选项）。 */}
      <ImageSlotPresetModal
        open={presetConfigOpen}
        onClose={() => {
          setPresets(loadPresets());
          setPresetConfigOpen(false);
        }}
      />
    </section>
  );
}

/**
 * 插入位置的提示线：拖动时画在「松手会插到这里」的那条缝上。
 *
 * 用绝对定位跨在行的上/下边缘，不占版面 —— 占位的话整列会在拖动时抖一下，
 * 而抖动的代价是用户以为拖错了。`pointer-events-none` 是必须的：这条线正好
 * 压在行的命中区边缘，能收事件的话会让另一半区的判定闪断。
 */
function DropLine({at, scope = 'row'}: {at: 'before' | 'after'; scope?: 'row' | 'group'}) {
  return (
    <span
      aria-hidden
      data-drop-line={at}
      data-drop-scope={scope}
      className={cn(
        'pointer-events-none absolute inset-x-0 z-10 h-[3px] rounded-full bg-primary',
        'shadow-[0_0_0_3px_rgba(43,124,255,.16)]',
        at === 'before' ? '-top-[5px]' : '-bottom-[5px]',
      )}
    />
  );
}

/** 展示案例：没设就明说没有，不要拿渐变块冒充缩略图。 */
function DemoCase({assetId, onClear}: {assetId: string | null; onClear: () => void}) {
  const detail = useLibraryDetail(assetId);
  if (!assetId) {
    return (
      <div className="flex h-40 flex-col items-center justify-center gap-2 rounded-xl border border-[rgba(211,224,229,.7)] bg-canvas/40 shadow-[inset_0_1px_0_rgba(255,255,255,.9)]">
        <span className="grid size-9 place-items-center rounded-full bg-panel text-ink-mute ring-1 ring-inset ring-line-soft">
          <ImageOff size={15} />
        </span>
        <span className="text-meta text-ink-mute">暂无案例</span>
      </div>
    );
  }
  return (
    <div className="rounded-xl border border-line-soft bg-panel-soft p-2">
      {/* 视频只给封面 + 播放按钮，点开才取流；封面一直有，不会出现黑块。 */}
      <AssetPreview assetId={assetId} className="h-44 w-full rounded-lg" />
      <div className="mt-2 flex items-center justify-between gap-2 px-1">
        <p className="truncate text-meta text-ink-soft">
          {detail.data?.name ?? assetId.slice(0, 8)}
        </p>
        <button
          type="button"
          onClick={onClear}
          className="shrink-0 text-meta text-ink-mute transition-colors hover:text-danger"
        >
          移除案例
        </button>
      </div>
    </div>
  );
}

/** 分区卡片：左侧色条 + 标题。色条统一用主色 —— 颜色留给**状态**用
 *  （固定的槽位是绿的、用户填的是蓝的），不再拿颜色给区块编号，
 *  否则一屏里五根不同颜色的条，抢的是「状态」的注意力。 */
function Section({
  title,
  hint,
  extra,
  meta,
  children,
}: {
  title: string;
  hint?: string;
  extra?: ReactNode;
  /** 标题右边的状态位（字数、计数）：右对齐，排在操作下面 */
  meta?: ReactNode;
  children: ReactNode;
}) {
  return (
    // 分区卡片：标题单独做成一条"带子"（浅底 + 下边线），内容区是白的。
    // 以前标题和内容同一片白，四块卡片只剩四根 1px 边框在区分——缩到 50% 看，
    // 整页就是四块一样的方块。有带子之后，眼睛能先认出「这是哪一块」，再读内容。
    <div className="relative rounded-2xl border border-line bg-panel">
      {/* 标题带子的圆角靠它自己那层裁（`rounded-t-2xl`），整张卡片不用
          `overflow-hidden` —— 提示词里的 @ 候选面板要从卡片里浮出去，
          被裁掉的话用户只看到输入框闪一下、什么也没出现。 */}
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-t-2xl border-b border-line-soft bg-gradient-to-r from-panel-soft to-panel px-4 py-2.5">
        <h3 className="flex items-center gap-2 text-lead font-semibold tracking-[.2px] text-ink">
          <span className="h-3.5 w-[3px] rounded-full bg-primary" />
          {title}
          {hint && <span className="font-normal text-ink-mute">· {hint}</span>}
        </h3>
        {(extra || meta) && (
          <div className="flex flex-col items-end gap-1">
            {extra && <div className="flex flex-wrap items-center gap-1">{extra}</div>}
            {meta && (
              <div className="font-mono text-micro tabular-nums text-ink-mute">{meta}</div>
            )}
          </div>
        )}
      </div>
      <div className="p-4">{children}</div>
    </div>
  );
}

/**
 * 模板卡片（列表页）。
 *
 * 用卡片而不是表格行：一张模板的"长相"是它的案例封面，表格里这条信息全丢了，
 * 一排十一行看起来像数据库导出。操作放在卡片脚上 —— 扫一眼就知道哪张能发布。
 */
function SpecCard({
  item,
  modelLabel,
  busy,
  trashed,
  onEdit,
  onPublish,
  onDisable,
  onRemove,
  onRestore,
}: {
  item: TemplateSpecOut;
  /** 模型的友好名（目录里的 label）。**卡片上不印 id** —— 模板库那边显示的是
      这个名字，同一个模型在两个页面叫法不同只会让人以为不是同一个。 */
  modelLabel?: string;
  busy: boolean;
  trashed: boolean;
  onEdit: () => void;
  onPublish: () => void;
  onDisable: () => void;
  onRemove: () => void;
  onRestore: () => void;
}) {
  const slotCount = item.draft?.slots?.length ?? item.latest?.slots?.length ?? 0;
  const model = item.draft?.model || item.latest?.model || '';  /* 悬停态挂在整张卡上（不是封面那个 button）：脚上的状态与操作也算「鼠标在这张卡上」，
     视频不该因为鼠标压到下面几行就断掉。 */
  const [hovering, setHovering] = useState(false);
  return (
    <div
      onMouseEnter={() => setHovering(true)}
      onMouseLeave={() => setHovering(false)}
      className={cn(
        // 卡片是「浮起来的一层」：静止就有一线投影，悬停再抬半像素。
        // 只描边的话，浅色封面与白底会连成一片（案例图经常是浅色），
        // 卡片边界得靠投影与封面内描边一起撑住。
        'group flex flex-col overflow-hidden rounded-2xl border border-line-soft bg-panel',
        'transition-all duration-200',
        'hover:border-line-strong',
      )}
    >
      <button
        type="button"
        onClick={onEdit}
        disabled={trashed}
        className="relative block w-full disabled:cursor-default"
      >
        {/* 9:16：库里案例视频全是竖版（720×1280 / 1080×1920），16:9 的
            封面上下一裁就只剩中间一条。 */}
        <CaseCover
          assetId={item.demoAssetId}
          seed={item.key}
          className="aspect-[9/16] w-full"
          hoverPlay
          active={hovering}
        />
        <span
          aria-hidden
          className="pointer-events-none absolute inset-0 rounded-t-2xl ring-1 ring-inset ring-black/[.04]"
        />
        <span className="absolute left-2 top-2">
          {trashed ? (
            <Chip tone="danger">回收站</Chip>
          ) : (
            <Chip
              tone={
                item.status === 'published' ? 'ok' : item.status === 'disabled' ? 'warn' : 'default'
              }
              className="backdrop-blur-sm"
            >
              {STATUS_LABELS[item.status] ?? item.status}
            </Chip>
          )}
        </span>
        {!trashed && item.currentVersion > 0 && (
          <span className="absolute right-2 top-2 rounded-full bg-black/40 px-1.5 py-0.5 text-micro font-medium leading-[14px] text-white/95 backdrop-blur-sm">
            v{item.currentVersion}
          </span>
        )}
      </button>

      <div className="flex min-w-0 flex-1 flex-col gap-0.5 px-3 pt-2.5">
        <button
          type="button"
          onClick={onEdit}
          disabled={trashed}
          className="truncate text-left text-body font-medium text-ink transition-colors hover:text-primary disabled:cursor-default disabled:hover:text-ink"
        >
          {item.name}
        </button>
        <p className="flex items-center gap-1.5 truncate text-meta text-ink-mute">
          {trashed ? (
            `删除于 ${item.deletedAt ? new Date(item.deletedAt).toLocaleString() : '—'}`
          ) : (
            <>
              <span className="shrink-0 tabular-nums">{slotCount} 个槽位</span>
              {model && (
                <>
                  <span className="shrink-0 text-line-strong">·</span>
                  {/* 目录里还认得出这个模型时显示友好名，认不出（已下线 / 目录没拉到）
                      就照实显示 id —— 那时 id 是唯一能定位问题的线索。 */}
                  <span className="truncate text-ink-soft" title={model}>
                    {modelLabel || model}
                  </span>
                </>
              )}
            </>
          )}
        </p>
      </div>

      {/* 脚上四个动作：都是**按钮**，不是一行文字链接。
          以前是 11px 无边框的裸文字（`px-1.5 py-1`），既没有可点面积的暗示，
          四个又同样弱 ——「编辑」是蓝字、「删除」是灰字，剩下的分不出轻重。
          现在统一成 `size="sm"` 的小按钮（与全局 Button 同一种形状），
          差别只靠字色与 hover 的底色表达。 */}
      <div className="mt-2 flex items-center gap-1.5 border-t border-line-soft px-2 py-2">
        {trashed ? (
          <Button
            size="sm"
            icon={<RotateCcw size={11} />}
            disabled={busy}
            onClick={onRestore}
          >
            恢复
          </Button>
        ) : (
          <>
            <Button
              size="sm"
              className="text-primary hover:border-primary-line hover:bg-primary-soft hover:text-primary-deep"
              onClick={onEdit}
            >
              编辑
            </Button>
            {/* 「发布」与「启用」是同一个动作（`POST /publish`），但说的不是一回事：
                发布 = 把当前草稿冻结成新版本；启用 = 把下架的模板放回去。
                停用后再点「发布」会让人以为又要发一版。

                以前停用态**整个按钮都不画**（`status !== 'disabled'` 才显示），
                于是下架的模板只剩「编辑」和「删除」—— 想放回去没有任何入口，
                是条死胡同。 */}
            <Button size="sm" disabled={busy} onClick={onPublish}>
              {item.status === 'disabled' ? '启用' : '发布'}
            </Button>
            {item.status === 'published' && (
              <Button
                size="sm"
                className="hover:border-warn-line hover:bg-warn-soft hover:text-warn-deep"
                disabled={busy}
                onClick={onDisable}
              >
                停用
              </Button>
            )}
            <Button
              size="sm"
              className="ml-auto hover:border-danger-line hover:bg-danger-soft hover:text-danger"
              disabled={busy}
              onClick={onRemove}
            >
              删除
            </Button>
          </>
        )}
      </div>
    </div>
  );
}
