import { useState } from 'react';
import { ChevronDown, GripVertical, Trash2 } from 'lucide-react';
import { AssetChip } from '@/entities/library/ui/AssetChip';
import type { SpecSlotIn } from '@/shared/api/schema';
import { Button, FormField, MultiSelect, Select } from '@/shared/ui';
import { MentionTextarea } from './MentionTextarea';
import { cn } from '@/shared/lib/cn';
import {
  SLOT_TYPE_LABELS,
  hasSingleSlotRef,
  isOptionalSlot,
  mixedIndexOf,
  selfRefChips,
  setSlotOptional,
  slotRefToken,
  slotTypeOf,
} from './specDraft';
import { SLOT_TYPE_ICONS } from './slotIcons';
import { parseAnchors } from './defaultText';

/**
 * 一个槽位卡片。
 *
 * **默认只露出高频项**（展示名、谁定、标签），低频项（类型、数量范围、用途说明）
 * 收进「更多设置」。以前这些平铺在一张卡上：7 个控件、高约 160px，三个槽位就
 * 1415px，滑到第三个槽位时第一个早滚出屏幕 —— 但「类型」和「最少/最多」十次里
 * 改不到一次。
 *
 * 折叠不是隐藏功能：所有字段都还在，只是收起来了。
 */
export function SlotCard({
  slot,
  index,
  dragging,
  onDragStart,
  onDragEnd,
  onPatch,
  onDelete,
  onPickAsset,
  tagOptions,
  typeOptions,
  slots,
  prompt,
  inGroup = false,
}: {
  slot: SpecSlotIn;
  index: number;
  dragging: boolean;
  onDragStart: () => void;
  onDragEnd: () => void;
  onPatch: (patch: Partial<SpecSlotIn>) => void;
  onDelete: () => void;
  onPickAsset: () => void;
  tagOptions: {value: string; label: string}[];
  typeOptions: {value: string; label: string}[];
  /** 全部槽位：算「自己是第几个图片/成片槽位」（引用写法 `{{Mixed N}}` 要用）。 */
  slots: SpecSlotIn[];
  /** 整段提示词：用来判断这个槽位有没有**插入点**（见下面 `hasInsertPoint`）。 */
  prompt: string;
  /** 在封装容器里：只能由使用者填（包由使用者选），所以不给「用户填 / 已固定」。 */
  inGroup?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const fixed = (slot.binding ?? 'user') === 'fixed';
  const type = slotTypeOf(slot.type);
  // 候选标签只对「让用户挑的图片/视频」有意义：模板库那一格靠它圈定候选范围
  // （和封装包的成员标签取交集），文本/音频走各自的选择器，固定槽位作者已经选死。
  const needsTag = type === 'image' || type === 'video';
  const name = slot.label?.trim() || `${SLOT_TYPE_LABELS[type]} ${index + 1}`;
  const TypeIcon = SLOT_TYPE_ICONS[type];
  /* 可选 = `minCount === 0`。**不加独立的 `required` 字段**：两个事实源
     不同步时，界面显示「必填」而编译器放行（或反过来），用户看到的与
     实际生效的不一致 —— 这类错只在生成结果里显形。 */
  const optional = isOptionalSlot(slot);

  /* 插入点有两种写法，缺一不可识别：
     1. 正文里的 `{{Mixed N}}` / `{{slot:key}}` 单张引用；
     2. **框选设出来的默认描述段**（`⟦e:key⟧…⟦/e⟧`）—— 它在提交时会被
        `anchorsToRefs` 换成单张引用，是同一个插入点的另一种编辑态写法。
     只认第 1 种的话，作者刚设完默认描述，这里的占位符却还说
     「要在正文写 {{Mixed N}} 引用才生效」—— 明明已经生效了。 */
  const hasInsertPoint =
    hasSingleSlotRef(prompt, slot.key, slots) ||
    parseAnchors(prompt).some(item => item.slotKey === slot.key);
  const showTexts = optional;
  /* 占位符里的 token：**当前槽位自己的**引用。
     写死 `{{slot:image_1}}` 的话，第二个槽位显示的就是第一个槽位的 token ——
     作者照着抄进提示词，引用到的还是那个槽位自己（编号会错位）。 */
  const token = slotRefToken(slots, slot);

  /* 「选中时文案」的占位提示（不是常驻小字）。
     说明放在框内占位符里：提示跟着框走、不占额外版面、填了内容就自动消失。

     没有插入点时必须说清「填了用不上」—— 提示词里没有插这个槽位的地方，
     编译时没有位置放这两段话，不说的话就是让人白填一场（还不自知）。 */
  const filledPlaceholder = hasInsertPoint
    ? '使用者填了素材时，提示词里这一段怎么说（打 @ 插入编号）'
    : `使用者填了素材时用这段；提示词里写下 ${token} 引用这个槽位，这段话才会被用上`;

  /* 「未选时文案」不在这里显示（2026-11-25 用户定）：
     它的唯一编辑与查看处就是提示词正文里那一段高亮文字。
     卡片里再摆一遍正文的快照 —— 哪怕只读 —— 也是同一句话两处维护，
     且会让作者以为「这里才是字段、正文只是显示」，改错地方。
     正文里还没有时，选区菜单本身会告诉他可以框选。 */

  return (
    <div
      className={cn(
        // 槽位是一行，不是一张卡：一排槽位用细线分隔就够了，
        // 每行都画一圈边框会让这一栏变成「一堆小方块」——缩到 50% 看，
        // 四块一样的方块根本分不出谁是谁。
        // 固定的槽位只把左边描一道绿边（而不是整行铺绿底）：绿底会让
        // 「少数固定槽位」比「多数用户填槽位」更抢眼，扫一眼反而分不清主次。
        'relative flex flex-col px-1 py-2 transition-colors',
        dragging && 'rounded-lg bg-primary-soft/70 ring-1 ring-primary/20',
      )}
    >
      <div className="flex items-center gap-2">
        <span
          draggable
          onDragStart={onDragStart}
          onDragEnd={onDragEnd}
          className="flex shrink-0 cursor-grab items-center gap-1.5 text-meta text-ink-mute active:cursor-grabbing"
          title="拖动调整顺序（顺序即编号）"
        >
          <GripVertical size={13} />
          <span className="flex h-[18px] w-[18px] items-center justify-center rounded-full bg-primary text-micro font-semibold text-white shadow-[0_1px_3px_rgba(23,21,15,.30)]">
            {index + 1}
          </span>
        </span>

        {/* 类型标记：以前类型藏在下拉框里，要点开才知道这个槽位要图片还是成片。
            现在一眼可见，也提示这个槽位的类型可以在下方那行改。
            改成图标是因为文字chip（"成片"/"图片"）在 14px 的行里跟展示名挤成一团，
            图标 + 可读名称（给读屏）最省地方。 */}
        <span
          role="img"
          aria-label={SLOT_TYPE_LABELS[type]}
          title={`类型：${SLOT_TYPE_LABELS[type]}`}
          className="grid size-7 shrink-0 place-items-center rounded-lg bg-panel-soft text-ink-soft ring-1 ring-inset ring-line-soft"
        >
          <TypeIcon size={14} />
        </span>

        {/* 展示名直接就是标题：以前它躺在一堆下拉框中间，要先找才知道这个槽位叫什么。
            空着时显示占位提示，一眼知道还没起名。
            `min-w-0` 是关键：没有它，这个 input 会按内容宽度撑开父级
            （素材名当展示名时尤其明显），把后面的按钮全挤出可视区。
            边框平时是隐形的：它长得像一行标题而不是一个输入框，
            鼠标移上去或点进去才显出边框 —— 「看着像文本、点进去能改」。 */}
        <input
          value={slot.label ?? ''}
          placeholder={name}
          aria-label="展示名"
          onChange={event => onPatch({label: event.target.value})}
          className={cn(
            'min-w-0 flex-1 rounded-md border border-transparent bg-transparent px-1.5 py-1 text-body outline-none transition-colors',
            'hover:border-line-soft focus:border-primary-line focus:bg-panel focus:ring-2 focus:ring-primary/10',
            slot.label?.trim() ? 'text-ink' : 'placeholder:text-ink-mute',
          )}
        />

        {/* 谁定：两个选项的东西，没有下拉值得占一格 —— 直接一个分段控件。
            选中项是**实心**的：这是这一行里唯一需要被一眼看出来的状态，
            「这一格在说什么」比「这一格是什么颜色」更重要。
            「用户填」是绝大多数槽位的默认档，所以它默认就是实心的。

            **容器里的槽位不给这个开关**：它只能是「用户填」（包由使用者选），
            切成固定会被后端拒（固定槽位钉的是一张具体素材）。 */}
        {!inGroup && (
        <div className="flex shrink-0 items-center gap-0.5 rounded-lg border border-line-soft bg-panel-soft p-0.5">
          {BINDING_TOGGLES.map(item => {
            const active = (slot.binding ?? 'user') === item.value;
            return (
              <button
                key={item.value}
                type="button"
                aria-pressed={active}
                title={item.hint}
                onClick={() =>
                  onPatch({
                    binding: item.value,
                    // 固定要素材、用户填要清掉素材，两边互斥。
                    ...(item.value === 'fixed' ? {} : {assetId: null}),
                  })
                }
                className={cn(
                  'rounded-lg px-2 py-[3px] text-micro transition-all',
                  active
                    ? 'bg-ink font-medium text-white shadow-[0_1px_2px_rgba(21,50,61,.28)]'
                    : 'text-ink-mute hover:text-ink-soft',
                )}
              >
                {item.label}
              </button>
            );
          })}
        </div>
        )}

        {/* 必填 / 可选：只在「用户填」的槽位上出现（固定槽位的素材早就定死了，
            不存在「选不选」）。

            这是**作者在封装时的一次性决定**：使用者要不要挑这张图。挑了 →
            用挑的那张；没挑 → 用「未选时文案」兜底。做成两个小字而不是下拉，
            因为它只有两档，且是高频决定。 */}
        {!fixed && (
          <div className="flex shrink-0 items-center gap-0.5 rounded-lg border border-line-soft bg-panel-soft p-0.5">
            {REQUIRED_TOGGLES.map(item => {
              const active = optional === item.optional;
              return (
                <button
                  key={item.label}
                  type="button"
                  aria-pressed={active}
                  title={item.hint}
                  onClick={() => onPatch(setSlotOptional(slot, item.optional))}
                  /* 这一组比「谁定」轻一级：一行里两个都是实心黑会互相抢焦点，
                     扫一眼分不出哪个才是这一行的主状态。用浅蓝底 + 描边表达
                     「已选」，与「用户填」的实心黑形成主次。 */
                  className={cn(
                    'rounded-lg px-2 py-[3px] text-micro transition-all',
                    active
                      ? 'bg-primary-soft font-medium text-primary-deep ring-1 ring-inset ring-primary-line'
                      : 'text-ink-mute hover:text-ink-soft',
                  )}
                >
                  {item.label}
                </button>
              );
            })}
          </div>
        )}

        <button
          type="button"
          onClick={() => setOpen(value => !value)}
          aria-expanded={open}
          aria-label={open ? '收起更多设置' : '展开更多设置'}
          title="更多设置"
          className={cn(
            'grid size-7 shrink-0 place-items-center rounded-md text-ink-mute transition-colors hover:bg-panel-soft hover:text-ink-soft',
            open && 'bg-panel-soft text-ink-soft',
          )}
        >
          <ChevronDown size={14} className={cn('transition-transform', open && 'rotate-180')} />
        </button>

        <button
          type="button"
          className="shrink-0 rounded-md p-1 text-ink-mute transition-colors hover:bg-danger-soft hover:text-danger"
          title="删除槽位"
          onClick={onDelete}
        >
          <Trash2 size={13} />
        </button>
      </div>

      {/* 类型 + 候选标签**常驻主行下方**，默认可见，不用点「更多设置」。
          两者都决定这个槽位「能不能配出来」：类型错了拿错素材，标签没圈
          发布会被 400 打回 —— 都是发布前就要看到的东西，不是低频设置。

          展开区那份同步删掉，不两处重复（同一件事两处维护必然漂移）。
          类型对所有槽位都显示；候选标签只对「用户填的图片/视频」有意义。 */}
      <div className="mt-2 flex items-center gap-2">
        <div className="w-[150px] shrink-0">
          <Select
            aria-label="类型"
            block
            value={slot.type}
            options={typeOptions}
            onChange={event =>
              onPatch({type: event.target.value as SpecSlotIn['type']})
            }
          />
        </div>
        {needsTag && !fixed && (
          <>
            <div className="min-w-0 flex-1">
              {/* 槽位的标签是「圈定候选范围」，不是「打在素材上」：
                  所以任意级别都能圈（分组也行），多个标签之间是并集。 */}
              <MultiSelect
                ariaLabel="候选标签"
                options={tagOptions}
                value={slot.tagIds ?? []}
                onChange={values => onPatch({tagIds: values})}
                placeholder="候选标签：不限（全部素材）"
                emptyText="还没有标签"
              />
            </div>
            {!slot.tagIds?.length && (
              <span className="shrink-0 whitespace-nowrap rounded-md bg-warn-soft px-1.5 py-0.5 text-micro text-warn-deep">
                缺标签 · 发布前必须选
              </span>
            )}
          </>
        )}
      </div>

      {/* 固定槽位选好了东西：显示它是什么，而不是一串 id。
          单独一行、缩进对齐到槽位名，左边一道绿 —— 扫一眼就知道
          「这几个是作者定死的，那几个留给使用者挑」。 */}
      {fixed && (
        <div className="mt-2 ml-9 flex items-center gap-2 rounded-lg border border-line-soft border-l-[3px] border-l-ok bg-panel-soft py-1.5 pl-2.5 pr-2">
          {slot.assetId ? (
            <AssetChip assetId={slot.assetId} className="border-transparent bg-panel" />
          ) : (
            <span className="text-meta text-ink-mute">还没选素材</span>
          )}
          <Button
            onClick={onPickAsset}
            variant="secondary"
            size="sm"
            className="ml-auto shrink-0"
          >
            {slot.assetId ? '更换' : '选择素材'}
          </Button>
        </div>
      )}

      {/* 更多设置：低频项收在这里。默认收起，主行只剩真正天天改的东西。 */}
      {open && (
        <div className="mt-3 grid gap-2 border-t border-line-soft pt-3 sm:grid-cols-2">
          {/* 文本槽位「占 {{Text N}}」、音频槽位「不占编号」这类**类型属性**
              已经写进类型下拉的选项名里（见 SpecWorkspace 的 TYPE_OPTIONS），
              不在这里再摆一行常驻小字 —— 同一件事说两遍，且那行还占版面。 */}


          {/* 「选中时文案」：**这个槽位在提示词里怎么说**。

              为什么放在槽位里而不是提示词正文：正文里 `{{slot:key}}` 是
              **唯一插入点**，而「有素材时这么说 / 没素材时那么说」是同一个
              槽位的两种说法。

              只对**「用户填 + 可选」**的槽位有意义：
              - 固定槽位的素材早就定死了，不存在「没素材」这一说；
              - 必填槽位一定有素材（**直接插编号**），也不存在「没素材」。

              「选中时文案」里要引用编号时**打 `@` 选**（候选只有这个槽位
              自己的几种写法），不要手敲 `{{slot:key}}` —— 手敲拼错了要到
              编译期才报错，而槽位改名后手敲的引用会静默失效。 */}
          {!fixed && showTexts && (
            <FormField
              label="选中时文案"
              htmlFor="slot-filled-text"
              className="sm:col-span-2"
            >
              <MentionTextarea
                id="slot-filled-text"
                aria-label="选中时文案"
                value={slot.filledText ?? ''}
                candidates={selfRefChips(slot, mixedIndexOf(slots, slot.key))}
                rows={2}
                placeholder={filledPlaceholder}
                panelHint="选择要插入的编号"
                onChange={text => onPatch({filledText: text})}
                onInsert={text => onPatch({filledText: text})}
              />
            </FormField>
          )}

        </div>
      )}
    </div>
  );
}

const BINDING_TOGGLES: {value: SpecSlotIn['binding']; label: string; hint: string}[] = [
  {value: 'user', label: '用户填', hint: '使用者填表时挑素材'},
  {value: 'fixed', label: '已固定', hint: '封装时就定好素材'},
];

/**
 * 必填 / 可选。
 *
 * `optional` 是「这一档代表可选」，判据是 `minCount === 0`（见
 * `specDraft.isOptionalSlot`）。这里**不另存一个 `required` 字段**。
 */
const REQUIRED_TOGGLES: {optional: boolean; label: string; hint: string}[] = [
  {optional: false, label: '必填', hint: '使用者必须挑素材，不挑不能生成'},
  {optional: true, label: '可选', hint: '不挑也能生成，用「未选时文案」兜底'},
];

