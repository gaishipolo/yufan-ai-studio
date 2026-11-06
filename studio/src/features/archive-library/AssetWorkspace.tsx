import { useSearchParams } from 'react-router-dom';
import { useCan } from '@/shared/access/use-access';
import { Button } from '@/shared/ui';
import { AssetLibrary } from '@/features/asset-library/AssetLibrary';
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
  /**
   * 「我的素材」有两条存储通路，不能混成一个列表：
   * - `upload` → `AssetLibrary`，走 `/assets`，新文件进对象存储（当前 S3 桶）；
   * - `active`/`trash`/`history` → `SourceLibrary`，走 `/assets/archive`，文件在归档盘。
   * 默认给上传，因为它是这个工作台唯一能主动写入的入口。
   */
  const view = params.get('view') ?? 'upload';
  const change = (key: string, value: string) => setParams(previous => {const next = new URLSearchParams(previous); next.set(key, value); next.delete('page'); next.delete('q'); next.delete('state'); return next;});
  const setView = (value: string) => setParams(previous => {const next = new URLSearchParams(previous); next.set('view', value); next.delete('page'); next.delete('q'); next.delete('state'); return next;});
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
      <div className="flex flex-wrap gap-2">
        <Button variant={view === 'upload' ? 'primary' : 'secondary'} aria-pressed={view === 'upload'} onClick={() => setView('upload')}>上传素材</Button>
        <Button variant={view === 'active' ? 'primary' : 'secondary'} aria-pressed={view === 'active'} onClick={() => setView('active')}>个人原片</Button>
        <Button variant={view === 'trash' ? 'primary' : 'secondary'} aria-pressed={view === 'trash'} onClick={() => setView('trash')}>回收站</Button>
        <Button variant={view === 'history' ? 'primary' : 'secondary'} aria-pressed={view === 'history'} onClick={() => setView('history')}>历史素材</Button>
      </div>
      {view === 'upload'
        ? <AssetLibrary />
        : <SourceLibrary key={view} scope={view === 'trash' ? 'trash' : view === 'history' ? 'history' : 'mine'} />}
    </>}
    {tab !== 'references' && <ArchivedResults />}
  </div>;
}
