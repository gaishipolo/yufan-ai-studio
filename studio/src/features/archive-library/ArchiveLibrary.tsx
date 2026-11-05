import { useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Film, Image, Music, UploadCloud } from 'lucide-react';
import { useArchiveAssets, useArchiveImport, useArchiveUpload } from '@/entities/asset/api/archive.queries';
import { ARCHIVE_LABELS, archiveCategory, type ArchiveCategory } from '@/entities/asset/model/archive.types';
import { ArchivedMedia, ArchivedThumbnail } from '@/entities/asset/ui/ArchivedMedia';
import { Button, EmptyState, Modal } from '@/shared/ui';
import { Can } from '@/shared/access/Can';
import { formatBytes } from '@/shared/lib/format';
import { toast } from '@/shared/lib/toast';
import { ArchivedResults } from './ArchivedResults';

export function ArchiveLibrary() {
  const [params, setParams] = useSearchParams();
  const category = archiveCategory(params.get('category'));
  const setCategory = (value: ArchiveCategory) => setParams(previous => {previous.set('category', value); return previous;}, {replace: true});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [relativePath, setRelativePath] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const {data, isLoading, error} = useArchiveAssets(category);
  const selected = data?.items.find(item => item.id === selectedId);
  const upload = useArchiveUpload();
  const importing = useArchiveImport();
  async function uploadFiles(files: FileList | null) {
    if (!files) return;
    try {
      for (const file of Array.from(files)) await upload.mutateAsync({file, category});
      toast('素材已存入 Z 盘归档', 'success');
    } catch (reason) {toast(reason instanceof Error ? reason.message : '上传失败', 'error');}
    finally {if (input.current) input.current.value = '';}
  }
  return <section className="space-y-5">
    <div className="rounded-xl border border-primary/20 bg-primary/5 p-4">
      <p className="text-sm font-semibold text-ink">统一素材归档</p>
      <p className="mt-1 break-all text-xs text-ink-soft">{data?.root ?? 'Z:\\yufan_ai_studio'} · 输入与生成结果统一存储于归档盘</p>
      <p className="mt-1 text-xs text-ink-mute">按原片、人物、产品、场景和音频分类；每次调用保存独立输入快照。原片要求带音轨的 1–15 秒 MP4。</p>
    </div>
    <div className="flex flex-wrap items-center gap-2">
      {Object.entries(ARCHIVE_LABELS).map(([key, label]) => <Button key={key}
        variant={category === key ? 'primary' : 'secondary'}
        onClick={() => setCategory(archiveCategory(key))}>{label}</Button>)}
      <span className="ml-auto text-xs text-ink-mute">{data?.total ?? 0} 个文件 · {formatBytes(data?.usedBytes ?? 0)}</span>
      <Can permission="asset:create"><Button icon={<UploadCloud size={14} />} loading={upload.isPending}
        onClick={() => input.current?.click()}>上传{ARCHIVE_LABELS[category]}</Button></Can>
      <input ref={input} type="file" multiple hidden aria-label="上传归档素材"
        accept={category === 'source' ? '.mp4' : category === 'audio' ? '.mp3,.wav' : '.jpg,.jpeg,.png,.webp'}
        onChange={event => {void uploadFiles(event.target.files);}} />
    </div>
    <Can permission="asset:create"><form className="flex flex-wrap gap-2 rounded-xl border border-line p-3"
      onSubmit={event => {event.preventDefault(); importing.mutate({relativePath, category}, {
        onSuccess: () => {setRelativePath(''); toast('已登记并复制到分类素材库', 'success');},
        onError: reason => toast(reason.message, 'error'),
      });}}>
      <label className="flex min-w-64 flex-1 items-center gap-2 text-xs text-ink-soft">从 inbox 登记
        <input value={relativePath} onChange={event => setRelativePath(event.target.value)} required
          placeholder="相对于 inbox 的路径，例如 人物/参考图.png" className="min-w-0 flex-1 rounded-lg border border-line bg-panel p-2" /></label>
      <Button type="submit" loading={importing.isPending}>登记到{ARCHIVE_LABELS[category]}</Button>
      <p className="w-full text-xs text-ink-mute">也可先将文件放入 Z:\yufan_ai_studio\inbox，再填写相对路径；原文件保留。</p>
    </form></Can>
    {error ? <p role="alert" className="text-sm text-danger">{error.message}</p> : isLoading ? <p className="text-sm text-ink-mute">正在读取归档盘…</p> :
      !data?.items.length ? <EmptyState title="暂无归档素材" description="上传文件，或从 inbox 登记已有素材。" /> :
      <div className="grid grid-cols-[repeat(auto-fill,minmax(190px,1fr))] gap-3">
        {data.items.map(asset => <button key={asset.id} onClick={() => setSelectedId(asset.id)}
          className="overflow-hidden rounded-xl border border-line bg-panel p-3 text-left transition hover:border-primary">
          <div className="mb-3 grid h-24 place-items-center rounded-lg bg-panel-soft text-primary">
            {asset.previewUrl ? <ArchivedThumbnail url={asset.previewUrl} name={asset.name} /> : asset.type === 'video' ? <Film size={30} /> : asset.type === 'audio' ? <Music size={30} /> : <Image size={30} />}
          </div>
          <p className="truncate text-sm font-semibold text-ink">{asset.name}</p>
          <p className="mt-1 text-xs text-ink-mute">{formatBytes(asset.sizeBytes)}{(asset.duration ?? 0) > 0 ? ` · ${(asset.duration ?? 0).toFixed(1)}秒` : ` · ${asset.width}×${asset.height}`}</p>
        </button>)}
      </div>}
    <Modal open={!!selected} onClose={() => setSelectedId(null)} title={selected?.name}>
      {selected && <div className="space-y-3 p-1"><ArchivedMedia url={selected.url} type={selected.type} name={selected.name} />
        <p className="break-all text-xs text-ink-mute">归档位置：{selected.path}</p>
        <p className="break-all text-xs text-ink-mute">SHA-256：{selected.sha256}</p></div>}
    </Modal>
    <ArchivedResults />
  </section>;
}
