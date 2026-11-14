import type { ReactNode } from 'react';
import { useReadText } from '@/entities/library/api/library.queries';
import { AssetPreview } from '@/entities/library/ui/AssetPreview';
import { cn } from '@/shared/lib/cn';

/**
 * 把提示词里的占位符渲染成**真的东西**。
 *
 * 三套占位符各有来源，不能混：
 *
 * | 写法 | 谁写的 | 换成什么 |
 * |---|---|---|
 * | `{{slot:key}}` | 模板（填表前） | 用户选的那张图 / 那份文案；没选就是个「选择」按钮 |
 * | `{{Mixed N}}` | `compile` 派生的视觉编号 | 第 N 张参考素材的缩略图 |
 * | `{{Text N}}` | `compile` 派生的文本编号 | 第 N 份文案的正文 |
 *
 * **封装页不用它**：那边要看见 `{{slot:key}}` 本身，否则作者不知道该往提示词里
 * 插哪个引用。
 *
 * 解析不到就原样显示占位符（灰底小字），不猜、不吞：历史任务里的 `{{Text 1}}`
 * 没有绑文案时，看到占位符比看到一段空白更有用。
 */

export type SlotMode = {kind: 'one'} | {kind: 'all'} | {kind: 'index'; at: number};

export type SlotResolution =
  | {kind: 'asset'; ids: string[]}
  | {kind: 'text'; ids: string[]}
  | {kind: 'empty'; label: string; onPick?: () => void};

export interface PromptRefsProps {
  value: string;
  /** 第 N 份文案的资产 id（与 `compile` 的 `textLibraryIds` 同序） */
  textIds?: string[];
  /** 第 N 位视觉参考的资产 id（与 `referenceLibraryIds` 同序） */
  referenceIds?: string[];
  /** 模板库专用：`{{slot:key*}}` / `{{slot:key.N}}` 的解析 */
  slot?: (key: string, mode: SlotMode) => SlotResolution | null;
  className?: string;
}

const TOKEN = /\{\{\s*([^{}]+?)\s*\}\}/g;
const SLOT = /^slot:([a-z][a-z0-9_]{0,39})(\*|\.(\d+))?$/;
const TEXT = /^Text\s+(\d+)$/;
const MIXED = /^Mixed\s+(\d+)$/;

/** 正文太长就别整段塞进预览：这里只是「这段引用是什么」，不是阅读器。 */
const MAX_INLINE_CHARS = 300;

export function PromptRefs({
  value,
  textIds = [],
  referenceIds = [],
  slot,
  className,
}: PromptRefsProps) {
  const parts: ReactNode[] = [];
  let cursor = 0;
  for (const match of value.matchAll(TOKEN)) {
    const at = match.index ?? 0;
    if (at > cursor) parts.push(value.slice(cursor, at));
    parts.push(
      <Token
        key={`${at}-${match[1]}`}
        expression={match[1]}
        textIds={textIds}
        referenceIds={referenceIds}
        slot={slot}
      />,
    );
    cursor = at + match[0].length;
  }
  if (cursor < value.length) parts.push(value.slice(cursor));

  return <span className={cn('whitespace-pre-wrap break-words', className)}>{parts}</span>;
}

function Token({
  expression,
  textIds,
  referenceIds,
  slot,
}: {
  expression: string;
  textIds: string[];
  referenceIds: string[];
  slot: PromptRefsProps['slot'];
}) {
  const text = TEXT.exec(expression);
  if (text) {
    const assetId = textIds[Number(text[1]) - 1];
    return assetId ? <InlineText assetId={assetId} /> : <Raw expression={expression} />;
  }

  const mixed = MIXED.exec(expression);
  if (mixed) {
    const assetId = referenceIds[Number(mixed[1]) - 1];
    return assetId ? <InlineAsset assetId={assetId} /> : <Raw expression={expression} />;
  }

  const slotMatch = SLOT.exec(expression);
  if (slotMatch && slot) {
    const [, key, modifier, index] = slotMatch;
    const mode: SlotMode = modifier === '*'
      ? {kind: 'all'}
      : index
        ? {kind: 'index', at: Number(index)}
        : {kind: 'one'};
    const resolved = slot(key, mode);
    if (!resolved) return <Raw expression={expression} />;
    if (resolved.kind === 'empty') return <PickInline label={resolved.label} onPick={resolved.onPick} />;
    if (resolved.kind === 'text') {
      return (
        <>
          {resolved.ids.map(id => (
            <InlineText key={id} assetId={id} />
          ))}
        </>
      );
    }
    return (
      <>
        {resolved.ids.map(id => (
          <InlineAsset key={id} assetId={id} />
        ))}
      </>
    );
  }

  return <Raw expression={expression} />;
}

/** 没解析出来的占位符：原样显示，但视觉上明显是「未替换」。 */
function Raw({ expression }: { expression: string }) {
  return (
    <code className="rounded bg-panel-soft px-1 text-[0.92em] text-ink-mute">{`{{${expression}}}`}</code>
  );
}

/** 模板库里「还没选」的位置：就地给一个按钮，点了直接开选择器。 */
function PickInline({ label, onPick }: { label: string; onPick?: () => void }) {
  if (!onPick) {
    return (
      <span className="mx-0.5 inline-flex items-center rounded border border-dashed border-line-strong px-1.5 text-[0.92em] text-ink-mute">
        {label}
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={onPick}
      className="mx-0.5 inline-flex items-center rounded border border-dashed border-primary-line bg-primary-soft px-1.5 text-[0.92em] text-primary transition-colors hover:border-primary hover:bg-primary/10"
    >
      {label}
    </button>
  );
}

/** 文本引用：显示正文本身，太长才截断。 */
function InlineText({ assetId }: { assetId: string }) {
  const text = useReadText(assetId);
  const content = text.data?.content?.trim();
  if (!content) {
    return (
      <span className="mx-0.5 inline-flex items-center rounded bg-panel-soft px-1 text-[0.92em] text-ink-mute">
        {text.isPending ? '读取文案…' : '文案不可用'}
      </span>
    );
  }
  const shown = content.length > MAX_INLINE_CHARS ? `${content.slice(0, MAX_INLINE_CHARS)}…` : content;
  return <span className="rounded bg-panel-soft px-1 text-ink">{shown}</span>;
}

/** 图 / 视频引用：一个跟文字差不多高的缩略块，视频悬停可播。 */
function InlineAsset({ assetId }: { assetId: string }) {
  return (
    <span
      className="mx-0.5 inline-flex h-[1.45em] w-[1.45em] translate-y-[0.28em] overflow-hidden rounded-[3px] ring-1 ring-line-strong"
      title="引用的素材"
    >
      <AssetPreview assetId={assetId} className="h-full w-full" hoverPlay />
    </span>
  );
}
