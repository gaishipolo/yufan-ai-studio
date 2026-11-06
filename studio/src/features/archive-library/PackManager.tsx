import { useState } from 'react';
import { useManagedPacks, usePackMutations } from '@/entities/template/api/pack.queries';
import type { PackManaged } from '@/shared/api/schema';
import { Can } from '@/shared/access/Can';
import { Button, EmptyState } from '@/shared/ui';
import { ErrorState } from '@/shared/ui/ErrorState';
import { ARCHIVE_LABELS } from '@/entities/asset/model/archive.types';
import { roleLabel } from '@/entities/template/model/packRoles';
import { PackEditor } from './PackEditor';

export function PackManager() {
  const query = useManagedPacks();
  const {action} = usePackMutations();
  const [editor, setEditor] = useState<{pack: PackManaged | null} | null>(null);
  return <section className="space-y-4">
    <div className="flex gap-2"><Button onClick={() => setEditor({pack: null})}>创建封装草稿</Button><Button loading={query.isFetching} onClick={() => {void query.refetch();}}>刷新</Button></div>
    {query.error && <ErrorState error={query.error} title="封装管理读取失败" onRetry={() => {void query.refetch();}} />}
    {query.isPending && <p role="status">正在读取草稿和发布记录…</p>}
    {!query.error && query.data?.items.length === 0 && <EmptyState title="暂无封装草稿" description="先上传参考图，再创建人物、产品或场景。" />}
    {action.error && <p role="alert" className="text-xs text-danger">{action.error.message}。状态未确认，请刷新核对后继续。</p>}
    {action.isSuccess && <p role="status" className="text-xs">已保存发布状态</p>}
    <div className="grid gap-3 sm:grid-cols-2">{query.data?.items.map(pack => <article key={pack.id} className="space-y-2 rounded-xl border border-line bg-panel p-4">
      <p>{pack.name || '未命名草稿'}</p>
      <p className="text-xs text-ink-mute">{ARCHIVE_LABELS[pack.kind]} · {pack.referenceIds.length} 张 · {pack.bindings?.map(binding => roleLabel(pack.kind, binding.role)).join('、') || '未指定用途'} · {pack.status === 'published' ? '已发布' : pack.status === 'disabled' ? '已停用' : '草稿'} · 版本 {pack.version ?? 0}</p>
      {!!pack.gaps?.length && <p className="text-xs text-danger">还差：{pack.gaps.join('、')}</p>}
      <div className="flex flex-wrap gap-2"><Button disabled={action.isPending} onClick={() => setEditor({pack})}>编辑草稿／新版本</Button><Can permission="template:pack:publish">
        <Button disabled={action.isPending || !!pack.gaps?.length} title={pack.gaps?.length ? '先补齐必填项再发布' : undefined} onClick={() => action.mutate({id: pack.id, action: 'publish', expectedRevision: pack.revision ?? 0})}>发布</Button>
        {(pack.version ?? 0) > 0 && <Button disabled={action.isPending} onClick={() => action.mutate({id: pack.id, action: 'disable', expectedRevision: pack.revision ?? 0})}>停用</Button>}
      </Can></div>
    </article>)}</div>
    {editor && <PackEditor pack={editor.pack} onClose={() => setEditor(null)} />}
  </section>;
}
