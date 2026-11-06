import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { ArchivedAsset } from '@/shared/api/schema';
import { useArchiveChange } from '@/entities/asset/api/archive.queries';
import { replacementFitness } from '@/entities/asset/model/archive-fit';
import { Can } from '@/shared/access/Can';
import { Button, Modal } from '@/shared/ui';

export function AssetActions({asset}: {asset: ArchivedAsset}) {
  const change = useArchiveChange();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(asset.name);
  const fitness = replacementFitness(asset);
  const base = {id: asset.id, expectedVersion: asset.stateVersion ?? 0};
  return <div className="space-y-2 p-2 text-xs">
    <p className="text-ink-mute">{asset.deletedAt ? '回收站' : asset.shared ? '已分享' : asset.editable ? '仅自己可见' : '公共原片'}{asset.editable ? ' · 本人上传' : ' · 用户分享'}</p>
    {!asset.deletedAt && fitness.ok && <Link className="text-primary underline" to={`/templates?sourceId=${encodeURIComponent(asset.id)}`}>用于替换</Link>}
    {asset.editable && <div className="flex flex-wrap gap-2">
      {!asset.deletedAt && <>
        <Can permission="asset:update"><Button size="sm" disabled={change.isPending} onClick={() => setEditing(true)}>重命名</Button></Can>
        {asset.category === 'source' && <Can permission="asset:share"><Button size="sm" disabled={change.isPending}
          onClick={() => change.mutate({...base, action: 'sharing', shared: !asset.shared})}>{asset.shared ? '取消分享' : '分享至公共素材'}</Button></Can>}
      </>}
      <Can permission="asset:delete"><Button size="sm" disabled={change.isPending}
        onClick={() => change.mutate({...base, action: 'deleted', deleted: !asset.deletedAt})}>{asset.deletedAt ? '恢复为私有' : '移入回收站'}</Button></Can>
    </div>}
    {change.isPending && <p role="status">正在保存，请勿重复操作…</p>}
    {change.error && !editing && <p role="alert" className="text-danger">状态未确认：{change.error.message}。请刷新核对后继续。</p>}
    {change.isSuccess && !editing && <p role="status">已保存</p>}
    {asset.editable && !asset.deletedAt && <p className="text-ink-mute">分享允许他人用于任务；撤回不删除已有任务的输入副本和结果。</p>}
    <Modal open={editing} title="修改素材名称" onClose={() => setEditing(false)}>
      <form className="space-y-3 p-4" onSubmit={e => {e.preventDefault(); change.mutate({...base, action: 'name', name}, {onSuccess: () => setEditing(false)});}}>
        <label className="block">名称<input className="mt-2 w-full rounded border border-line bg-panel p-2" value={name} maxLength={160} required onChange={e => setName(e.target.value)} /></label>
        {change.error && <p role="alert" className="text-danger">{change.error.message}</p>}
        <Button type="submit" loading={change.isPending}>保存名称</Button>
      </form>
    </Modal>
  </div>;
}
