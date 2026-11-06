import { useSearchParams } from 'react-router-dom';
import { useCan } from '@/shared/access/use-access';
import { Button } from '@/shared/ui';
import { SourceLibrary } from './SourceLibrary';
import { PublicPacks } from './PublicPacks';
import { PackManager } from './PackManager';
import { ArchiveLibrary } from './ArchiveLibrary';
import { ArchivedResults } from './ArchivedResults';

export function AssetWorkspace() {
  const [params, setParams] = useSearchParams();
  const manager = useCan('template:pack:manage');
  const referenceManager = useCan('asset:reference:manage');
  const templateRead = useCan('template:read');
  const tab = params.get('scope') ?? 'public';
  const type = params.get('type') ?? 'source';
  const change = (key: string, value: string) => setParams(previous => {const next = new URLSearchParams(previous); next.set(key, value); next.delete('page'); next.delete('q'); next.delete('state'); return next;});
  return <div className="space-y-5">
    <div className="flex flex-wrap gap-2">
      <Button variant={tab === 'public' ? 'primary' : 'secondary'} aria-pressed={tab === 'public'} onClick={() => change('scope', 'public')}>公共素材</Button>
      <Button variant={tab === 'mine' ? 'primary' : 'secondary'} aria-pressed={tab === 'mine'} onClick={() => change('scope', 'mine')}>我的素材</Button>
      {referenceManager && <Button variant={tab === 'references' ? 'primary' : 'secondary'} aria-pressed={tab === 'references'} onClick={() => change('scope', 'references')}>参考素材维护</Button>}
      {manager && <Button variant={tab === 'packs' ? 'primary' : 'secondary'} aria-pressed={tab === 'packs'} onClick={() => change('scope', 'packs')}>封装与发布</Button>}
    </div>
    {tab === 'public' ? <>
      <p className="text-xs text-ink-soft">专员发布的替换对象与用户主动分享的原片，全工作台授权账号可调用。</p>
      <div className="flex gap-2">{[['source', '原片'], ['product', '产品'], ['character', '人物'], ['scene', '场景']].map(([key, label]) => <Button key={key} variant={type === key ? 'primary' : 'secondary'} aria-pressed={type === key} onClick={() => change('type', key)}>{label}</Button>)}</div>
      {type === 'source' ? <SourceLibrary scope="public" /> : templateRead ? <PublicPacks key={type} kind={type === 'product' ? 'product' : type === 'scene' ? 'scene' : 'character'} /> : <p>当前账号无模板读取权限。</p>}
    </> : tab === 'references' && referenceManager ? <ArchiveLibrary /> : tab === 'packs' && manager ? <PackManager /> : <>
      <div className="flex gap-2"><Button onClick={() => change('view', 'active')}>个人原片</Button><Button onClick={() => change('view', 'trash')}>回收站</Button><Button onClick={() => change('view', 'history')}>历史素材</Button></div>
      <SourceLibrary key={params.get('view')} scope={params.get('view') === 'trash' ? 'trash' : params.get('view') === 'history' ? 'history' : 'mine'} />
    </>}
    {tab !== 'references' && <ArchivedResults />}
  </div>;
}
