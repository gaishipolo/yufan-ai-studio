import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useBlocker } from 'react-router-dom';
import {
  ChevronLeft,
  FolderInput,
  ImageOff,
  Plus,
  RotateCcw,
} from 'lucide-react';
import { useMutation } from '@tanstack/react-query';
import { useModels } from '@/entities/model/api/model.queries';
import { ParamFields } from '@/entities/model/ui/ParamFields';
import {
  authorParamControls,
  resolveParamValues,
  type ParamValue,
} from '@/entities/model/lib/paramForm';
import { libraryApi } from '@/entities/library/api/library.api';
import { useLibraryDetail, useLibraryTags } from '@/entities/library/api/library.queries';
import { viewQuery } from '@/entities/library/model/types';
import { scopeTagOptions } from '@/entities/library/model/tagTree';
import { CaseCover } from '@/entities/library/ui/CaseCover';
import { AssetPreview } from '@/entities/library/ui/AssetPreview';
import {
  useSpecMutations,
  useSpecs,
  useTrashedSpecs,
} from '@/entities/template-spec/api/spec.queries';
import type { SpecSlotIn, TemplateSpecIn, TemplateSpecOut } from '@/shared/api/schema';
import {
  Button,
  Chip,
  EmptyState,
  FormField,
  Modal,
  Notice,
  PageHeader,
  TextInput,
} from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { toast } from '@/shared/lib/toast';
import { AssetPicker, type PickKind, type PickedAsset } from '@/features/template-specs/AssetPicker';
import { AddSlotMenu } from '@/features/template-specs/AddSlotMenu';
import { DemoMenu } from '@/features/template-specs/DemoMenu';
import { ModelPicker } from '@/features/template-specs/ModelPicker';
import { useSpecLimits } from '@/entities/template-spec/api/spec.queries';
import { PromptBox } from '@/features/template-specs/PromptBox';
import { SlotCard } from '@/features/template-specs/SlotCard';
import {
  SLOT_TYPE_LABELS,
  SLOT_TYPES,
  authoredFingerprint,
  blankSlot,
  blankSpec,
  dropUserSelectableParams,
  fromSpecOut,
  joinModelSelection,
  normalize,
  promptRefError,
  referenceChips,
  renumberRefs,
  rekeySlot,
  slotTypeOf,
  slotsMissingTag,
  splitModelSelection,
  stripSlotRefs,
  textsToClearForRequired,
  toMixedTokens,
  type SlotType,
} from './specDraft';

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


const STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  published: '已发布',
  disabled: '已停用',
};

/** 槽位拖拽中的下标；用 key 定位比下标稳（重排过程中下标会变）。 */
type DragState = {key: string} | null;

/** 素材选择器的目标：某个槽位，或展示案例。 */
type PickTarget = {target: 'slot'; index: number} | {target: 'demo'; kind: PickKind};

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

  /** 进入编辑：设草稿并把当前内容记成基线。 */
  const openDraft = (next: TemplateSpecIn) => {
    setDraft(next);
    setBaseline(authoredFingerprint(next));
  };

  const [picker, setPicker] = useState<PickTarget | null>(null);
  const [uploading, setUploading] = useState(false);
  /* 选了一条还不在案例池里的素材：先问一句再动它。
     「设为案例」= 把这条素材**移出**个人库（普通列表默认不再显示它），
     不是复制一份，所以要让人看见这件事再确认。 */
  const [pendingCase, setPendingCase] = useState<PickedAsset | null>(null);
  const [drag, setDrag] = useState<DragState>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const promoteCase = useMutation({
    mutationFn: (asset: PickedAsset) => libraryApi.setPurpose(asset.id, 'case'),
    onSuccess: (_row, asset) => {
      setDraft(current => (current ? {...current, demoAssetId: asset.id} : current));
      setPendingCase(null);
      toast('已移入案例池并设为展示案例', 'success');
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

  /* 当前模型里「由使用者选」的参数（计费模式）。作者不设它们 —— 写死等于
     替每个使用者决定要不要花积分。提交前会把草稿里残留的这类参数剔掉。 */
  const userSelectableKeys = useMemo(() => {
    const model = models.find(item => item.id === draft?.model);
    if (!model?.paramSchema) return [];
    return Object.entries(model.paramSchema)
      .filter(([, field]) => field?.userSelectable === true)
      .map(([key]) => key);
  }, [models, draft?.model]);

  /* 槽位能圈**任意级别**的标签（一级分组 / 二级系列 / 三级具体词）：槽位的标签
     是「圈定候选范围」，不是「打在素材上」，所以不必限叶子 —— 圈一个分组正是
     它的用途。数量按全库算，跟槽位类型无关：这里的用途是「挑范围」，不是
     「看有多少条」。 */
  const tags = useLibraryTags(viewQuery('all'));
  const tagOptions = useMemo(() => scopeTagOptions(tags.data ?? []), [tags.data]);

  const clear = () => {
    setError('');
    setHint('');
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
      // 发布闸门的前端预检：后端要求「要让用户填的图片/视频槽位必须有一级标签」，
      // 而它只会说"这些槽位要给一级标签"、不指哪里。先在本地拦一次并点名，
      // 省掉「点发布 → 400 → 回头逐个槽位找」那一轮。保存草稿不判（草稿允许半成品）。
      if (action !== 'save') {
        const missing = slotsMissingTag(payload.slots ?? []);
        if (missing.length > 0) {
          setError(
            `这些槽位还缺一级标签：${missing.join('、')}。` +
              '模板库按「标签 · 槽位名」分组让人挑素材，没有标签就只能退化成全部素材。',
          );
          return false;
        }
      }
      if (action === 'validate') {
        const result = await validate.mutateAsync(payload);
        toast(`预检通过 · 指纹 ${result.fingerprint.slice(0, 12)}`, 'success');
        return true;
      }
      const saved = await save.mutateAsync(payload);
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
  const [view, setView] = useState<'list' | 'trash'>('list');
  const [error, setError] = useState('');
  const [hint, setHint] = useState('');
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
    const rekeyed =
      patch.type !== undefined
        ? rekeySlot(renumbered.slots ?? [], index, slotTypeOf(next.type), renumbered.prompt ?? '')
        : renumbered.slots;
    setDraft({...renumbered, slots: rekeyed});
  };

  const moveSlot = (from: number, to: number) => {
    if (!draft || from === to) return;
    const next = slots.slice();
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    // 位置就是编号：拖一下，后面所有 `{{Mixed N}}` 都要跟着变。
    setDraft(renumberRefs(draft, next.map((slot, index) => ({...slot, position: index}))));
  };

  const addSlot = (type: SlotType = 'image') => {
    if (!draft) return;
    const slot = blankSlot(slots.length, type, slots);
    setDraft(renumberRefs(draft, [...slots, slot]));
    if (type === 'text') setPicker({target: 'slot', index: slots.length});
  };

  const pick = (asset: PickedAsset) => {
    if (!draft || !picker) return;
    if (picker.target === 'demo') {
      setPicker(null);
      // 已经在案例池里的直接引用；否则先确认 —— 这一步会改变素材的可见范围。
      if (asset.purpose === 'case') setDraft({...draft, demoAssetId: asset.id});
      else setPendingCase(asset);
      return;
    }
    const id = picker.index;
    const slot = slots[id];
    patchSlot(id, {
      assetId: asset.id,
      label: slot.label?.trim() ? slot.label : asset.name,
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
    setHint('');
    /* 存进去的永远是**作者写法**：手打或粘进来的 `{{slot:image_1}}`（内部 key）
       当场换成 `{{Mixed 1}}`。这样正文里只有一种写法，胶囊也能对上标签。 */
    setDraft({...draft, prompt: toMixedTokens(text, draft.slots ?? [])});
    return true;
  };

  const chips = draft ? referenceChips(draft) : [];

  /* 提示词上限：**能不能发布**的那个数（执行层，按原文字数比）。
     没取到时显示 `—`，不猜一个数字 —— 猜错了比不显示更糟。 */
  const promptLimit = limits.data?.promptExecutionLimit;
  const promptOver = promptLimit != null && (draft?.prompt ?? '').length > promptLimit;

  const moveToTrash = (item: TemplateSpecOut) => {
    if (!window.confirm(`把「${item.name}」移进回收站？可以再恢复。`)) return;
    clear();
    remove.mutateAsync(item.key).then(() => toast(`已移进回收站：${item.name}`, 'success'), fail);
  };

  /* ---------- 列表 / 回收站 ---------- */
  if (!draft) {
    const trashed = view === 'trash';
    const rows = (trashed ? trash.data?.items : list.data?.items) ?? [];
    const loading = trashed ? trash.isLoading : list.isLoading;
    return (
      <section className="flex flex-col gap-4">
        {/* 列表态是普通页头；编辑态的标题在编辑器自己的吸顶栏里（跟面包屑、
            动作按钮排在一行），所以页头由这里出，不在外层页面。 */}
        <PageHeader title="模板封装" />
        <div className="flex flex-wrap items-center justify-between gap-2">
          {/* 分段控件：外圈是一层凹槽（软底 + 内描边），选中项是"浮起来的白片"。
              以前是两颗胶囊共享一个圆角边框，选中只换字色 —— 在一排白卡上面几乎看不出
              当前在哪一档。 */}
          <div className="inline-flex items-center gap-0.5 rounded-xl bg-canvas/90 p-0.5 shadow-[inset_0_1px_2px_rgba(21,50,61,.06)] ring-1 ring-inset ring-[rgba(203,218,224,.8)]">
            {(
              [
                ['list', '模板'],
                ['trash', '回收站'],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => {
                  setView(value);
                  clear();
                }}
                className={cn(
                  'rounded-[10px] px-3 py-[5px] text-meta transition-all duration-150',
                  view === value
                    ? 'bg-panel font-medium text-ink shadow-[0_1px_3px_rgba(21,50,61,.12)]'
                    : 'text-ink-mute hover:text-ink-soft',
                )}
              >
                {label}
                {value === 'trash' && (trash.data?.total ?? 0) > 0 && (
                  <span className="ml-1 tabular-nums text-ink-mute">{trash.data?.total}</span>
                )}
              </button>
            ))}
          </div>
          {/* 列表页只有这一个动作，给它实心：整页就一个焦点，不用读字也知道从哪开始。 */}
          {!trashed && (
            <Button variant="primary" onClick={() => openDraft(blankSpec())}>
              <Plus size={14} /> 新建模板
            </Button>
          )}
        </div>

        {error && <Notice tone="error">{error}</Notice>}
        {loading ? (
          <p className="py-10 text-center text-xs text-ink-mute">正在读取…</p>
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
      </section>
    );
  }

  /* ---------- 编辑器 ---------- */
  /* 参数控件由模型声明驱动（中文标签、档位文案都在 `paramControlsFor` 里）：
     以前这里拿 `Object.entries(paramSchema)` 直接渲染，界面上印的是 `durationSec`
     这种英文 key，枚举值也是裸的 `9:16`。

     只给「作者设的」参数 —— 标了 `userSelectable` 的（计费模式）由**使用模板的人**
     在模板库选，作者写死等于替每个使用者决定要不要花积分。 */
  const paramControls = authorParamControls(models.find(item => item.id === draft.model));
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
              /* 「加槽位」也按资产库的 kind 生成：库里多一类，这里就多一个入口，
                 和槽位卡片里的「类型」下拉同源（曾经这里写死四个按钮）。 */
              <AddSlotMenu
                options={TYPE_OPTIONS}
                onAdd={type => addSlot(slotTypeOf(type))}
              />
            }
          >
            {/* 三个动作（拖动排序 / 改名称 / 切绑定）**不需要教** ——
                拖动手柄是可见的图标（`title` 里写着「顺序即编号」）、
                展示名是个长得像标题的输入框、切绑定是右侧那个写着
                「用户填 / 已固定」的分段控件。三个都自带解释，
                再摆一行教学条就是同一件事说两遍，还占一行版面。 */}
            {/* 槽位是一列清单，用细线分隔而不是一张张卡片：
                一排小方块缩到 50% 看会糊成一片，分隔线才看得出「几行、每行谁」。 */}
            <div className="divide-y divide-line-soft">
              {slots.length === 0 && (
                <p className="rounded-lg border border-dashed border-line-strong bg-panel-soft px-3 py-4 text-meta text-ink-mute">
                  没有槽位。纯提示词的模板不需要素材，从上面的按钮加一个就行。
                </p>
              )}
              {slots.map((slot, index) => (
                <SlotCard
                  key={slot.key}
                  slot={slot}
                  index={index}
                  dragging={drag?.key === slot.key}
                  onDragStart={() => setDrag({key: slot.key})}
                  onDragEnd={() => setDrag(null)}
                  onDragOver={event => drag && event.preventDefault()}
                  onDrop={() => {
                    if (!drag) return;
                    const from = slots.findIndex(item => item.key === drag.key);
                    setDrag(null);
                    if (from >= 0) moveSlot(from, index);
                  }}
                  onPatch={patch => patchSlot(index, patch)}
                  onDelete={() => {
                    const name = slot.label?.trim() || SLOT_TYPE_LABELS[slotTypeOf(slot.type)];
                    // 引用留着的下场是「点生成才报引用了不存在的槽位」，
                    // 那句话看不出是刚才删槽位删出来的，所以一并清掉并告知条数。
                    /* 顺序很重要：**先**按新结构重编号（删掉第 1 个，后面的
                       编号整体前移），**再**清掉被删槽位的引用 —— 反过来的话，
                       清理时用的还是旧编号，会漏掉几条（它们已经变号了）。 */
                    const remaining = slots.filter(item => item.key !== slot.key);
                    const renumbered = renumberRefs(draft, remaining);
                    const stripped = stripSlotRefs(renumbered.prompt ?? '', slot.key, remaining);
                    setDraft({...renumbered, prompt: stripped.text});
                    toast(
                      stripped.count > 0
                        ? `已删除槽位「${name}」，提示词里的 ${stripped.count} 处引用一并清掉`
                        : `已删除槽位「${name}」`,
                      'success',
                    );
                  }}
                  onPickAsset={() => setPicker({target: 'slot', index})}
                  tagOptions={tagOptions}
                  typeOptions={TYPE_OPTIONS}
                  slots={slots}
                  prompt={draft.prompt ?? ''}
                />
              ))}
            </div>
          </Section>
        </div>

        <div className="flex flex-col gap-4 lg:sticky lg:top-[112px]">
          {/* 提示词这一块只留**一个标题**，不套整条板块外壳。
              它是这一页真正在写的东西，和下面「展示案例」不是同级信息。
              「+ 文本素材」也去掉了：左栏「槽位」板块的加号菜单已经能加
              任意类型（含文本），同一个动作摆两个入口，用户还得判断
              哪个才是"正确"的。 */}
          <div className="rounded-2xl border border-line bg-panel p-4 shadow-[0_1px_2px_rgba(16,42,55,0.04)]">
            <p className="mb-2 text-meta font-medium text-ink">提示词</p>
            <PromptBox
              value={draft.prompt ?? ''}
              chips={chips}
              /* 两条写入路径（手打 / 按钮与 @）走**同一个**校验：
                 合法的引用与按钮插入的是同一个 token，没有理由区别对待。 */
              onChange={applyPromptText}
              onInsert={applyPromptText}
            />
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
                  不用把眼睛从卡片内容挪到标题栏去找按钮。 */}
              <DemoMenu
                disabled={busy}
                onUpload={() => fileRef.current?.click()}
                onPickImage={() => setPicker({target: 'demo', kind: 'image'})}
                onPickVideo={() => setPicker({target: 'demo', kind: 'video'})}
              />
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
        <div className="flex flex-col gap-3 p-[18px]">
          {/* 先把它要动的是哪一条摆出来：文件名一长，混在正文里就看不见了。
              平时素材列表里也是这么一条一行，形状认得出来。 */}
          <div className="flex items-center gap-2 rounded-lg border border-line-soft bg-panel-soft px-2.5 py-2">
            <FolderInput size={14} className="shrink-0 text-ink-mute" aria-hidden />
            <span className="min-w-0 flex-1 truncate text-body text-ink" title={pendingCase?.name}>
              {pendingCase?.name}
            </span>
          </div>

          {/* 后果逐条列。原来是一整段：读到第三句，第一句已经忘了。 */}
          <ul className="flex flex-col gap-1.5 text-body leading-relaxed text-ink-soft">
            <li className="flex gap-2">
              <span className="mt-[8px] size-[5px] shrink-0 rounded-full bg-ink-mute/45" />
              <span className="min-w-0">
                移入<span className="font-medium text-ink">案例池</span>
                ：模板库的所有人都能看到它
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
        <div className="flex flex-col gap-3 p-[18px]">
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
    </section>
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
    <div className="relative rounded-2xl border border-line bg-panel shadow-[0_1px_2px_rgba(16,42,55,0.04)]">
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
  busy,
  trashed,
  onEdit,
  onPublish,
  onDisable,
  onRemove,
  onRestore,
}: {
  item: TemplateSpecOut;
  busy: boolean;
  trashed: boolean;
  onEdit: () => void;
  onPublish: () => void;
  onDisable: () => void;
  onRemove: () => void;
  onRestore: () => void;
}) {
  const slotCount = item.draft?.slots?.length ?? item.latest?.slots?.length ?? 0;
  const model = item.draft?.model || item.latest?.model || '';
  /* 悬停态挂在整张卡上（不是封面那个 button）：脚上的状态与操作也算「鼠标在这张卡上」，
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
        'shadow-[0_1px_2px_rgba(16,42,55,.05)] transition-all duration-200',
        'hover:-translate-y-0.5 hover:border-line hover:shadow-card',
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
                  <span className="truncate text-ink-soft">{model}</span>
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
