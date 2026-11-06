import { useDebouncedValue } from '@/shared/hooks/useDebouncedValue';
import { useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useArchiveAssets } from '@/entities/asset/api/archive.queries';
import type { ArchivedAsset } from '@/shared/api/schema';
import { Can } from '@/shared/access/Can';
import { Button, EmptyState } from '@/shared/ui';
import { ErrorState } from '@/shared/ui/ErrorState';
import { ArchiveCard } from './ArchiveCard';
import { ArchiveDetail } from './ArchiveDetail';
import { AssetActions } from './AssetActions';
import { ArchiveUploadPanel } from './ArchiveUploadPanel';
import { useUploadBatchStore } from './model/use-upload-batch';
import { isBatchLocked } from './model/upload-batch';

export function SourceLibrary({scope}: {scope: 'mine' | 'public' | 'trash' | 'history'}) {
  const [params, setParams] = useSearchParams();
  const keyword = params.get('q') ?? '';
  const debouncedKeyword = useDebouncedValue(keyword);
  const state = params.get('state') === 'shared' ? 'shared' : params.get('state') === 'private' ? 'private' : 'all';
  const page = Math.max(1, Number(params.get('page')) || 1);
  const query = useArchiveAssets(scope === 'history' ? 'all' : 'source', {scope: scope === 'history' ? 'mine' : scope, keyword: debouncedKeyword, state, page, pageSize: 24});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = query.data?.items.find(item => item.id === selectedId);
  const input = useRef<HTMLInputElement>(null);
  const start = useUploadBatchStore(s => s.start);
  const batch = useUploadBatchStore(s => s.batch);
  const locked = isBatchLocked(batch);
  const update = (key: string, value: string) => setParams(previous => {
    const next = new URLSearchParams(previous); next.set(key, value); if (key !== 'page') next.set('page', '1'); return next;
  });
  const open = (asset: ArchivedAsset) => setSelectedId(asset.id);
  return <section className="space-y-4">
    <div className="flex flex-wrap gap-2">
      <label className="text-xs">名称搜索<input type="search" className="ml-2 rounded border border-line bg-panel p-2" value={keyword} onChange={e => update('q', e.target.value)} /></label>
      {scope === 'mine' && <label className="text-xs">可见性<select value={state} onChange={e => update('state', e.target.value)} className="ml-2 rounded border border-line bg-panel p-2"><option value="all">全部</option><option value="private">私有</option><option value="shared">已分享</option></select></label>}
      {scope === 'mine' && <Can permission="asset:create"><Button disabled={locked} onClick={() => input.current?.click()}>上传个人原片</Button></Can>}
      <Button loading={query.isFetching} onClick={() => {void query.refetch();}}>刷新</Button>
      <span className="p-2 text-xs text-ink-mute">{query.data ? `匹配 ${query.data.total} 项` : '匹配 — 项'}</span>
    </div>
    <input ref={input} type="file" multiple accept=".mp4" hidden aria-label="上传个人原片" disabled={locked} onChange={e => {
      const files = Array.from(e.target.files ?? []); e.target.value = ''; if (files.length) start(files, 'source');
    }} />
    {scope === 'mine' && <><p className="text-xs text-ink-mute">上传默认仅自己可见；分享后其他用户才可在公共原片中调用。</p><ArchiveUploadPanel /></>}
    {query.error && <ErrorState error={query.error} title={query.data ? '更新失败，保留上次内容' : '素材读取失败'} onRetry={() => {void query.refetch();}} />}
    {query.isPending && !query.data && <p role="status">正在读取素材…</p>}
    {query.data && !query.data.items.length && !query.error && <EmptyState title={keyword ? '没有找到匹配的原片' : scope === 'public' ? '暂无公共原片' : scope === 'trash' ? '回收站为空' : '暂无素材'} description={scope === 'public' ? '可切换到我的素材上传原片。' : '上传原片后可在这里管理。'} />}
    <div className="grid grid-cols-[repeat(auto-fill,minmax(210px,1fr))] gap-3">
      {query.data?.items.map(asset => <article key={asset.id} className="rounded-xl border border-line bg-panel p-2">
        {asset.deletedAt ? <p className="p-3 text-sm">{asset.name}</p> : <ArchiveCard asset={asset} onOpen={open} />}
        <AssetActions asset={asset} />
      </article>)}
    </div>
    <div className="flex items-center gap-3"><Button disabled={page <= 1 || query.isFetching} onClick={() => update('page', String(page - 1))}>上一页</Button><span className="text-xs">第 {page} 页</span><Button disabled={!query.data || page * 24 >= query.data.total || query.isFetching} onClick={() => update('page', String(page + 1))}>下一页</Button></div>
    <ArchiveDetail asset={selected ?? null} onClose={() => setSelectedId(null)} />
  </section>;
}
