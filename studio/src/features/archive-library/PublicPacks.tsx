import { useDebouncedValue } from '@/shared/hooks/useDebouncedValue';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useAvailablePacks } from '@/entities/template/api/pack.queries';
import { ArchivedThumbnail } from '@/entities/asset/ui/ArchivedThumbnail';
import { Button, EmptyState } from '@/shared/ui';
import { ErrorState } from '@/shared/ui/ErrorState';

export function PublicPacks({kind}: {kind: 'character' | 'product' | 'scene'}) {
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const debouncedSearch = useDebouncedValue(search);
  const query = useAvailablePacks(kind, debouncedSearch, page);
  const items = query.data?.items;
  return <section className="space-y-4">
    <label className="text-xs">搜索已发布对象<input className="ml-2 rounded border border-line bg-panel p-2" type="search" value={search} onChange={e => {setSearch(e.target.value); setPage(1);}} /></label>
    {query.error && <ErrorState error={query.error} title="公共对象读取失败" onRetry={() => {void query.refetch();}} />}
    {query.isPending && <p role="status">正在读取已发布对象…</p>}
    {!query.error && items?.length === 0 && <EmptyState title={search ? '没有匹配的对象' : '暂无已发布对象'} description="请联系素材专员封装发布。" />}
    <div className="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-3">{items?.map(pack => <article key={pack.id} className="space-y-2 rounded-xl border border-line bg-panel p-3">
      <div className="h-28"><ArchivedThumbnail url={pack.coverUrl ?? null} type="image" /></div><p>{pack.name}</p><p className="text-xs text-ink-mute">版本 {pack.version} · {pack.referenceIds.length} 张图</p><p className="text-xs">{pack.features}</p>
      {pack.available ? <Link className="text-xs text-primary underline" to={`/templates?kind=${kind}&packId=${encodeURIComponent(pack.id)}&packVersion=${pack.version}`}>用于替换</Link> : <p className="text-xs text-danger">素材待维护，暂不可用</p>}
    </article>)}</div>
    <div className="flex gap-2"><Button disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</Button><span className="p-2 text-xs">第 {page} 页 · {query.data?.total ?? '—'} 项</span><Button disabled={!query.data || page * 24 >= query.data.total} onClick={() => setPage(page + 1)}>下一页</Button></div>
    <Button onClick={() => {void query.refetch();}} loading={query.isFetching}>刷新已发布对象</Button>
  </section>;
}
