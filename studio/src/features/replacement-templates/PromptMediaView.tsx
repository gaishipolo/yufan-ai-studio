import { useState } from 'react';
import { ArchivedMedia } from '@/entities/asset/ui/ArchivedMedia';
import { ArchivedThumbnail } from '@/entities/asset/ui/ArchivedThumbnail';
import { Button } from '@/shared/ui';

export interface PromptMediaItem {
  id: string;
  token?: string;
  name: string;
  type: string;
  url?: string | null;
  previewUrl?: string | null;
  usage?: string;
}

function Thumbnail({item}: {item: PromptMediaItem}) {
  return <ArchivedThumbnail url={item.previewUrl || (item.type === 'image' ? item.url : undefined)} type={item.type} />;
}

function MediaPreview({item, onClose}: {item: PromptMediaItem | null; onClose: () => void}) {
  if (!item?.url) return null;
  return <section aria-label="媒体预览" className="space-y-2 rounded-xl border border-line bg-panel-soft p-3">
    <div className="flex items-start justify-between gap-2">
      <p className="break-all text-xs font-semibold text-ink">{item.token} · {item.name}</p>
      <Button size="sm" onClick={onClose}>收起预览</Button>
    </div>
    {item.usage && <p className="text-xs leading-5 text-ink-soft">用途：{item.usage}</p>}
    <ArchivedMedia url={item.url} type={item.type} name={item.name} downloadAllowed={false} />
  </section>;
}

export function MediaGallery({media, prompt, preview = false}: {media: PromptMediaItem[]; prompt: string; preview?: boolean}) {
  const [selected, setSelected] = useState<PromptMediaItem | null>(null);
  return <section aria-label={preview ? '模板预览输入' : '当次完整输入'} className="space-y-3">
    <p className="text-xs font-semibold text-ink">{media.filter(item => item.type === 'video').length} 个视频 · {media.filter(item => item.type === 'image').length} 张图片</p>
    <ul className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {media.map(item => <li key={item.token || item.id} className="min-w-0 space-y-1">
        <button type="button" aria-label={`查看 ${item.token || '编号未记录'}：${item.name}`} disabled={!item.url}
          onClick={() => setSelected(item)} className="w-full space-y-2 rounded-lg border border-line p-2 text-left hover:border-primary disabled:opacity-60">
          <span className="flex h-24 items-center justify-center overflow-hidden rounded bg-panel-soft [&_img]:object-contain"><Thumbnail item={item} /></span>
          <span className="block font-mono text-[11px] text-primary">{item.token || '编号未记录'} · {item.type === 'video' ? '原片' : '参考图'}</span>
          <span className="block break-all text-xs text-ink">{item.name}</span>
          <span className="block text-[11px] text-ink-mute">{!item.token ? '历史编号未记录' : prompt.includes(item.token) ? '提示词正文引用' : preview ? '将传入，正文未点名' : '已传入，正文未点名'}</span>
        </button>
        {item.usage && <details className="text-[11px] text-ink-soft"><summary className="cursor-pointer">用途说明</summary><p className="leading-5">{item.usage}</p></details>}
      </li>)}
    </ul>
    <MediaPreview item={selected} onClose={() => setSelected(null)} />
  </section>;
}

export function PromptMediaView({prompt, media}: {prompt: string; media: PromptMediaItem[]}) {
  const [selected, setSelected] = useState<PromptMediaItem | null>(null);
  return <div className="space-y-3">
    <div aria-label="可视化提示词" className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-panel-soft p-3 text-xs leading-8 text-ink-soft">
      {prompt.split(/(\{\{Mixed \d+\}\}|(?:视频|图|音频)\d+)/g).map((part, index) => {
        if (!/^(?:\{\{Mixed \d+\}\}|(?:视频|图|音频)\d+)$/.test(part)) return <span key={index}>{part}</span>;
        const item = media.find(entry => entry.token === part);
        if (!item) return <span key={index} className="rounded border border-dashed border-line px-1 text-ink-mute">{part} · 媒体未记录</span>;
        return <button key={index} type="button" aria-label={`查看 ${part}：${item.name}`} disabled={!item.url} title={item.name}
          onClick={() => setSelected(item)} className="mx-1 inline-flex max-w-full items-center gap-1.5 rounded-lg border border-primary/20 bg-primary/5 px-1.5 py-1 align-middle text-primary hover:border-primary disabled:opacity-60">
          <span className="flex h-9 w-10 shrink-0 items-center justify-center overflow-hidden rounded [&_img]:object-contain"><Thumbnail item={item} /></span>
          <span className="min-w-0 text-left leading-4"><span className="block font-mono text-[10px]">{part}</span><span className="block max-w-36 truncate text-[11px]">{item.name}</span></span>
        </button>;
      })}
    </div>
    <MediaPreview item={selected} onClose={() => setSelected(null)} />
    <details><summary className="cursor-pointer text-xs text-primary">查看提示词原文</summary><pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded border border-line p-3 text-xs leading-6 text-ink-soft">{prompt}</pre></details>
  </div>;
}
