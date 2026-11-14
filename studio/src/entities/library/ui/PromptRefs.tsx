import { useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useReadText } from '@/entities/library/api/library.queries';
import { libraryApi } from '@/entities/library/api/library.api';
import { useLibraryDetail, useLibraryMedia } from '@/entities/library/api/library.queries';
import { AssetPreview } from '@/entities/library/ui/AssetPreview';
import { Modal } from '@/shared/ui/Modal';
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
 *
 * **缩略块可点**：这是提示词里唯一的「看一眼原图」的入口，点开是弹层大图 /
 * 可播放视频。封装（一组图）在行内给前两张 + 「N 张」，点开看全部。
 */

export type SlotMode = {kind: 'one'} | {kind: 'all'} | {kind: 'index'; at: number};

export type SlotResolution =
  | {kind: 'asset'; ids: string[]}
  | {kind: 'text'; ids: string[]}
  /** 资产库封装：行内给成员缩略图，点开看整组 */
  | {kind: 'pack'; packId: string}
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

type ZoomTarget = {kind: 'asset'; assetId: string} | {kind: 'pack'; packId: string};

const TOKEN = /\{\{\s*([^{}]+?)\s*\}\}/g;
const SLOT = /^slot:([a-z][a-z0-9_]{0,39})(\*|\.(\d+))?$/;
const TEXT = /^Text\s+(\d+)$/;
const MIXED = /^Mixed\s+(\d+)$/;

/** 正文太长就别整段塞进预览：这里只是「这段引用是什么」，不是阅读器。 */
const MAX_INLINE_CHARS = 300;
/** 封装在行内最多摊两张，再多就只是噪音。 */
const MAX_INLINE_PACK = 2;

export function PromptRefs({
  value,
  textIds = [],
  referenceIds = [],
  slot,
  className,
}: PromptRefsProps) {
  const [zoom, setZoom] = useState<ZoomTarget | null>(null);

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
        onZoom={setZoom}
      />,
    );
    cursor = at + match[0].length;
  }
  if (cursor < value.length) parts.push(value.slice(cursor));

  return (
    <>
      <span className={cn('whitespace-pre-wrap break-words', className)}>{parts}</span>
      <AssetLightbox target={zoom} onClose={() => setZoom(null)} />
    </>
  );
}

function Token({
  expression,
  textIds,
  referenceIds,
  slot,
  onZoom,
}: {
  expression: string;
  textIds: string[];
  referenceIds: string[];
  slot: PromptRefsProps['slot'];
  onZoom: (target: ZoomTarget) => void;
}) {
  const text = TEXT.exec(expression);
  if (text) {
    const assetId = textIds[Number(text[1]) - 1];
    return assetId ? <InlineText assetId={assetId} /> : <Raw expression={expression} />;
  }

  const mixed = MIXED.exec(expression);
  if (mixed) {
    const assetId = referenceIds[Number(mixed[1]) - 1];
    return assetId ? (
      <InlineThumb assetId={assetId} onZoom={() => onZoom({kind: 'asset', assetId})} />
    ) : (
      <Raw expression={expression} />
    );
  }

  const slotMatch = SLOT.exec(expression);
  if (slotMatch && slot) {
    const [, key, modifier, index] = slotMatch;
    const mode: SlotMode =
      modifier === '*'
        ? {kind: 'all'}
        : index
          ? {kind: 'index', at: Number(index)}
          : {kind: 'one'};
    const resolved = slot(key, mode);
    if (!resolved) return <Raw expression={expression} />;
    if (resolved.kind === 'empty') {
      return <PickInline label={resolved.label} onPick={resolved.onPick} />;
    }
    if (resolved.kind === 'pack') {
      return (
        <InlinePack
          packId={resolved.packId}
          onZoomPack={() => onZoom({kind: 'pack', packId: resolved.packId})}
          onZoomAsset={assetId => onZoom({kind: 'asset', assetId})}
        />
      );
    }
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
          <InlineThumb
            key={id}
            assetId={id}
            onZoom={() => onZoom({kind: 'asset', assetId: id})}
          />
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

/** 图 / 视频引用：跟文字差不多高的缩略块，点一下看大图（视频可直接播）。 */
function InlineThumb({
  assetId,
  onZoom,
  size = 'md',
}: {
  assetId: string;
  onZoom(): void;
  size?: 'md' | 'sm';
}) {
  return (
    <button
      type="button"
      onClick={onZoom}
      title="点击查看大图"
      className={cn(
        'mx-0.5 inline-flex shrink-0 cursor-zoom-in overflow-hidden rounded-[3px] align-[-0.28em] ring-1 ring-line-strong transition-shadow hover:ring-primary',
        size === 'md' ? 'h-[1.45em] w-[1.45em]' : 'h-[1.2em] w-[1.2em]',
      )}
    >
      <AssetPreview assetId={assetId} className="pointer-events-none h-full w-full" hoverPlay />
    </button>
  );
}

/**
 * 封装引用：行内摊开前两张成员 + 「N 张」。
 *
 * 封装本身没有「一张图」可以代表它（成员可能是一套产品图），所以不拿首图冒充整组，
 * 也不在提示词里放一个大数字 —— 两张缩略图 + 计数既说明规模，又留了点击看全的入口。
 */
function InlinePack({
  packId,
  onZoomPack,
  onZoomAsset,
}: {
  packId: string;
  /** 点「N 张」：看整组成员 */
  onZoomPack(): void;
  /** 点某一张：看那一张的大图（不是整组） */
  onZoomAsset(assetId: string): void;
}) {
  const pack = useQuery({
    queryKey: ['library', 'pack', packId],
    queryFn: () => libraryApi.packDetail(packId),
    enabled: !!packId,
    staleTime: 30_000,
  });
  const items = pack.data?.packItems ?? [];
  if (pack.isPending) {
    return (
      <span className="mx-0.5 inline-flex items-center rounded bg-panel-soft px-1 text-[0.92em] text-ink-mute">
        读取封装…
      </span>
    );
  }
  if (items.length === 0) return <Raw expression={`slot:${packId}`} />;
  return (
    <span className="mx-0.5 inline-flex items-center align-middle">
      {items.slice(0, MAX_INLINE_PACK).map(item => (
        <InlineThumb
          key={item.assetId}
          assetId={item.assetId}
          onZoom={() => onZoomAsset(item.assetId)}
          size="sm"
        />
      ))}
      <button
        type="button"
        onClick={onZoomPack}
        className="inline-flex items-center rounded border border-line bg-panel px-1 text-[0.85em] text-ink-mute transition-colors hover:border-primary-line hover:text-primary"
        title="查看全部成员"
      >
        {items.length} 张
      </button>
    </span>
  );
}

/**
 * 大图弹层。图看原图，视频直接能播 —— 视频要经 `useLibraryMedia` 取 blob，
 * `/stream` 要 Bearer 头，`<video src>` 直连会 401（同 `AssetPreview` 的理由）。
 */
function AssetLightbox({
  target,
  onClose,
}: {
  target: ZoomTarget | null;
  onClose: () => void;
}) {
  if (!target) return null;
  return (
    <Modal open onClose={onClose} title={target.kind === 'pack' ? '封装成员' : '素材预览'} width={760}>
      <div className="p-[18px]">
        {target.kind === 'asset' ? (
          <AssetStage assetId={target.assetId} big />
        ) : (
          <PackStage packId={target.packId} />
        )}
      </div>
    </Modal>
  );
}

function AssetStage({ assetId, big = false }: { assetId: string; big?: boolean }) {
  const detail = useLibraryDetail(assetId);
  const isVideo = detail.data?.kind === 'video';
  const media = useLibraryMedia(assetId, isVideo);
  if (isVideo) {
    if (!media.url) {
      return (
        <div className="flex h-40 items-center justify-center rounded-lg bg-panel-soft text-[12px] text-ink-mute">
          {media.isPending ? '正在读取视频…' : '视频暂时读不出来'}
        </div>
      );
    }
    return (
      <video
        src={media.url}
        poster={detail.data?.coverUrl ?? undefined}
        controls
        autoPlay
        playsInline
        className="max-h-[70vh] w-full rounded-lg bg-black object-contain"
      >
        <track kind="captions" />
      </video>
    );
  }
  const src = detail.data?.coverUrl ?? detail.data?.url;
  return (
    <img
      src={src}
      alt={detail.data?.name ?? '素材'}
      className={cn('mx-auto rounded-lg object-contain', big ? 'max-h-[70vh]' : 'max-h-40')}
    />
  );
}

function PackStage({ packId }: { packId: string }) {
  const pack = useQuery({
    queryKey: ['library', 'pack', packId],
    queryFn: () => libraryApi.packDetail(packId),
    enabled: !!packId,
    staleTime: 30_000,
  });
  const items = pack.data?.packItems ?? [];
  if (pack.isPending) {
    return <p className="py-10 text-center text-[12px] text-ink-mute">正在读取封装…</p>;
  }
  if (items.length === 0) {
    return <p className="py-10 text-center text-[12px] text-ink-mute">这个封装没有成员。</p>;
  }
  return (
    <div className="max-h-[70vh] overflow-y-auto">
      <p className="mb-2 text-[12px] text-ink-soft">{pack.data?.name}</p>
      <div className="grid grid-cols-3 gap-2">
        {items.map(item => (
          <figure key={item.assetId} className="flex flex-col gap-1">
            <div className="h-32 overflow-hidden rounded-lg">
              <AssetPreview assetId={item.assetId} className="h-full w-full" hoverPlay />
            </div>
            <figcaption className="truncate text-[10px] text-ink-mute">
              {item.role || item.name || item.assetId.slice(0, 8)}
            </figcaption>
          </figure>
        ))}
      </div>
    </div>
  );
}
