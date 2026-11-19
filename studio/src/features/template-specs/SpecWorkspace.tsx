import { useMemo, useRef, useState, type ReactNode } from 'react';
import {
  ChevronLeft,
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
import { AssetPicker, type PickKind, type PickedAsset } from '@/features/template-specs/AssetPicker';
import { AddSlotMenu } from '@/features/template-specs/AddSlotMenu';
import { DemoMenu } from '@/features/template-specs/DemoMenu';
import { ModelPicker } from '@/features/template-specs/ModelPicker';
import { PromptBox } from '@/features/template-specs/PromptBox';
import { SlotCard } from '@/features/template-specs/SlotCard';
import {
  MAX_PROMPT,
  SLOT_TYPE_LABELS,
  SLOT_TYPES,
  blankSlot,
  blankSpec,
  dropUserSelectableParams,
  fromSpecOut,
  joinModelSelection,
  normalize,
  referenceChips,
  rekeySlot,
  slotTypeOf,
  slotsMissingTag,
  splitModelSelection,
  stripSlotRefs,
  textsToClearForRequired,
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

  const [draft, setDraft] = useState<TemplateSpecIn | null>(null);
  const [view, setView] = useState<'list' | 'trash'>('list');
  const [note, setNote] = useState('');
  const [error, setError] = useState('');
  const [hint, setHint] = useState('');
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
      setNote('已移入案例池并设为展示案例');
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
    setNote('');
    setHint('');
  };

  const fail = (reason: unknown) =>
    setError(reason instanceof Error ? reason.message : String(reason));

  const submit = async (action: 'validate' | 'save' | 'publish') => {
    if (!draft) return;
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
          return;
        }
      }
      if (action === 'validate') {
        const result = await validate.mutateAsync(payload);
        setNote(`预检通过 · 指纹 ${result.fingerprint.slice(0, 12)}`);
        return;
      }
      const saved = await save.mutateAsync(payload);
      setDraft(fromSpecOut(saved));
      if (action === 'save') {
        setNote(`已保存（第 ${saved.revision} 次修订）`);
        return;
      }
      const receipt = await publish.mutateAsync(saved.key);
      setDraft(fromSpecOut(receipt.spec));
      setNote(`已发布 v${receipt.version}`);
    } catch (reason) {
      fail(reason);
    }
  };

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
        setNote(
          `「${next.label?.trim() || SLOT_TYPE_LABELS[slotTypeOf(next.type)]}」改成必填了，` +
            '两段文案已清空（必填槽位一定是直接插入素材编号）',
        );
      }
    }
    nextSlots[index] = next;
    // 改类型时跟着改名（只在提示词没引用过时；引用过就留着，别让引用静默失效）。
    const rekeyed =
      patch.type !== undefined
        ? rekeySlot(nextSlots, index, slotTypeOf(next.type), draft.prompt ?? '')
        : nextSlots;
    setDraft({...draft, slots: rekeyed});
  };

  const moveSlot = (from: number, to: number) => {
    if (!draft || from === to) return;
    const next = slots.slice();
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    setDraft({...draft, slots: next.map((slot, index) => ({...slot, position: index}))});
  };

  const addSlot = (type: SlotType = 'image') => {
    if (!draft) return;
    const slot = blankSlot(slots.length, type, slots);
    setDraft({...draft, slots: [...slots, slot]});
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
   * 提示词只让改**描述文字**，引用一律从按钮插入或打 `@` 选。
   *
   * 手写 `{{slot:x}}` 会在两处出错且都不报错：拼错 key → 编译期才 400；手写
   * `{{Mixed N}}` → 数字与本次填充量对不上，少填一张就整体错位。所以这里直接
   * 拒绝新增花括号：要么删字，要么用按钮或 `@`。
   *
   * `inserted` 例外：按钮插入与 `@` 提及都是程序自己写进去的 token，本来就带
   * 花括号 —— 那道防线是拦「手打」的，不是拦「程序插」的。
   */
  const applyPromptText = (text: string, options?: {inserted?: boolean}) => {
    if (!draft) return;
    const before = draft.prompt ?? '';
    const braces = (value: string) => (value.match(/[{}]/g) ?? []).length;
    if (!options?.inserted && braces(text) > braces(before)) {
      setHint('引用请用 @ 或下方按钮插入，不要手写大括号');
      return;
    }
    setHint('');
    setDraft({...draft, prompt: text});
  };

  /** 程序写入的引用（按钮 / `@` 提及）：允许带花括号。 */
  const insertPromptText = (text: string) => {
    applyPromptText(text, {inserted: true});
  };

  const setPromptText = (text: string) => {
    applyPromptText(text);
  };

  const chips = draft ? referenceChips(draft) : [];

  const moveToTrash = (item: TemplateSpecOut) => {
    if (!window.confirm(`把「${item.name}」移进回收站？可以再恢复。`)) return;
    clear();
    remove.mutateAsync(item.key).then(() => setNote(`已移进回收站：${item.name}`), fail);
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
            <Button variant="primary" onClick={() => setDraft(blankSpec())}>
              <Plus size={14} /> 新建模板
            </Button>
          )}
        </div>

        {error ? (
          <Notice tone="error">{error}</Notice>
        ) : note ? (
          <Notice tone="success">{note}</Notice>
        ) : null}
        {loading ? (
          <p className="py-10 text-center text-xs text-ink-mute">正在读取…</p>
        ) : rows.length === 0 ? (
          /* 「新建模板」按钮就在上方，不用来一段话再说一遍怎么新建 */
          <EmptyState title={trashed ? '回收站是空的' : '还没有模板'} />
        ) : (
          /* 卡片而不是表格行：一张模板的"长相"是它的案例封面，列表里也该看得见。
             操作放在卡片脚上，扫一眼就知道哪张能发布、哪张已停用。 */
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {rows.map(item => (
              <SpecCard
                key={item.id}
                item={item}
                busy={busy}
                trashed={trashed}
                onEdit={() => {
                  setDraft(fromSpecOut(item));
                  clear();
                }}
                onPublish={() =>
                  publish
                    .mutateAsync(item.key)
                    .then(result =>
                      setNote(
                        /* 停用态走的是同一个接口，但版本号不变（幂等分支）——
                           这时候说「已发布 v1」会让人以为又发了一版。 */
                        item.status === 'disabled'
                          ? `已启用 ${item.name}`
                          : `已发布 ${item.name} v${result.version}`,
                      ),
                    )
                    .catch(fail)
                }
                onDisable={() =>
                  disable
                    .mutateAsync(item.key)
                    .then(() => setNote(`已停用 ${item.name}`))
                    .catch(fail)
                }
                onRemove={() => moveToTrash(item)}
                onRestore={() =>
                  restore
                    .mutateAsync(item.key)
                    .then(() => setNote(`已恢复 ${item.name}`))
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
              onClick={() => {
                setDraft(null);
                clear();
              }}
            >
              <ChevronLeft size={16} />
              模板封装
            </button>
            <span className="shrink-0 text-body text-ink-mute">/</span>
            <span className="min-w-0 truncate text-body text-ink-soft">
              {draft.name || '未命名模板'}
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            {/* 三个动作的权重必须分开：一页只留一个实心按钮（保存并发布），
                否则三颗长得一样的白按钮并排，用户每次都要读字才知道该点哪个。 */}
            <Button variant="ghost" disabled={busy} onClick={() => void submit('validate')}>
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
        {/* 提示放进吸顶区：槽位多的时候用户是在页面底部点「保存并发布」的，
            提示留在页首等于没提示（预检失败/发布被拦正是最需要看见的时候）。 */}
        {error ? (
          <Notice tone="error">{error}</Notice>
        ) : note ? (
          <Notice tone="success">{note}</Notice>
        ) : null}
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
                    const stripped = stripSlotRefs(draft.prompt ?? '', slot.key);
                    setDraft({
                      ...draft,
                      slots: slots.filter(item => item.key !== slot.key),
                      prompt: stripped.text,
                    });
                    setNote(
                      stripped.count > 0
                        ? `已删除槽位「${name}」，提示词里的 ${stripped.count} 处引用一并清掉`
                        : `已删除槽位「${name}」`,
                    );
                  }}
                  onPickAsset={() => setPicker({target: 'slot', index})}
                  tagOptions={tagOptions}
                  typeOptions={TYPE_OPTIONS}
                  prompt={draft.prompt ?? ''}
                />
              ))}
            </div>
          </Section>
        </div>

        <div className="flex flex-col gap-4 lg:sticky lg:top-[112px]">
          {/* 提示词放到最上面：它是作者这一页真正在写的东西。
              槽位速览是它的索引，展示案例是它的产出预览 —— 顺序按「写 → 查 → 验」。 */}
          <Section
            title="提示词"
            extra={
              <Button variant="ghost" size="sm" onClick={() => addSlot('text')}>
                <Plus size={12} /> 文本素材
              </Button>
            }
            meta={`字数 ${(draft.prompt ?? '').length} / ${MAX_PROMPT}`}
          >
            <PromptBox
              value={draft.prompt ?? ''}
              chips={chips}
              onChange={setPromptText}
              onInsert={insertPromptText}
            />
            {hint && <p className="mt-2 text-meta text-warn-deep">{hint}</p>}
          </Section>

          {/* 槽位速览：一眼看清「有几个槽位、分别是谁定的」。
              点名不如看颜色快 —— 绿点=作者定死，蓝点=使用者挑。 */}
          <Section title="槽位速览">
            <div className="flex flex-wrap gap-1.5">
              {slots.length === 0 && (
                <span className="text-meta text-ink-mute">还没有槽位</span>
              )}
              {slots.map((slot, index) => {
                const fixed = (slot.binding ?? 'user') === 'fixed';
                return (
                  <span
                    key={slot.key}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-line-soft bg-panel-soft px-2 py-1 text-meta text-ink-soft"
                  >
                    <span
                      className={cn('size-1.5 shrink-0 rounded-full', fixed ? 'bg-ok' : 'bg-primary')}
                    />
                    {slot.label?.trim() || `${SLOT_TYPE_LABELS[slotTypeOf(slot.type)]} ${index + 1}`}
                    <span className="text-micro text-ink-mute">
                      {fixed ? '已固定' : '用户填写'}
                    </span>
                  </span>
                );
              })}
            </div>
          </Section>

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
        <div className="flex flex-col gap-3">
          <p className="text-body leading-relaxed text-ink-soft">
            「{pendingCase?.name}」会被移入
            <span className="font-medium text-ink"> 案例池 </span>
            ：模板库的所有人都能看到它，它也不再出现在你的个人素材列表里。
            素材只有一份，不是复制。
          </p>
          <p className="text-meta text-ink-mute">
            已经在别处用到的模板不受影响；这一步可以撤销（设为案例后可再改回普通素材）。
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
        <CaseCover
          assetId={item.demoAssetId}
          seed={item.key}
          className="aspect-[16/9] w-full"
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

      <div className="mt-2 flex items-center gap-1 border-t border-line-soft px-2 py-1.5 text-meta">
        {trashed ? (
          <button
            type="button"
            disabled={busy}
            onClick={onRestore}
            className="flex items-center gap-1 rounded-md px-1.5 py-1 text-ink-soft transition-colors hover:bg-primary-soft hover:text-primary disabled:opacity-50"
          >
            <RotateCcw size={11} /> 恢复
          </button>
        ) : (
          <>
            <button
              type="button"
              onClick={onEdit}
              className="rounded-md px-1.5 py-1 font-medium text-primary transition-colors hover:bg-primary-soft hover:text-primary-deep"
            >
              编辑
            </button>
            {/* 「发布」与「启用」是同一个动作（`POST /publish`），但说的不是一回事：
                发布 = 把当前草稿冻结成新版本；启用 = 把下架的模板放回去。
                停用后再点「发布」会让人以为又要发一版。

                以前停用态**整个按钮都不画**（`status !== 'disabled'` 才显示），
                于是下架的模板只剩「编辑」和「删除」—— 想放回去没有任何入口，
                是条死胡同。 */}
            <button
              type="button"
              disabled={busy}
              onClick={onPublish}
              className="rounded-md px-1.5 py-1 text-ink-soft transition-colors hover:bg-panel-soft hover:text-primary disabled:opacity-50"
            >
              {item.status === 'disabled' ? '启用' : '发布'}
            </button>
            {item.status === 'published' && (
              <button
                type="button"
                disabled={busy}
                onClick={onDisable}
                className="rounded-md px-1.5 py-1 text-ink-soft transition-colors hover:bg-warn-soft hover:text-warn-deep disabled:opacity-50"
              >
                停用
              </button>
            )}
            <button
              type="button"
              disabled={busy}
              onClick={onRemove}
              className="ml-auto rounded-md px-1.5 py-1 text-ink-mute transition-colors hover:bg-danger-soft hover:text-danger disabled:opacity-50"
            >
              删除
            </button>
          </>
        )}
      </div>
    </div>
  );
}
